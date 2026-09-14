"""Colector MQTT: suscribe, normaliza, atribuye preset y persiste.

Diseñado para poder probarse sin red: ``handle_message`` y ``flush`` son funciones
puras sobre un buffer, y solo ``run_collector`` toca la red.

Reintenta con backoff exponencial: el broker comunitario puede caerse o expulsarnos,
y perder la ventana de ingesta corrompería la métrica de PDR.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import ssl
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace

import aiomqtt

from app.attribution import Timeline, attribute_packets, build_timelines
from app.config import Settings, topics_for
from app.db import insert_packets, insert_status, statuses_between, transaction
from app.decode import decode_packet, decoder_available
from app.models import AttributedPacket, Packet, ParseError, Status
from app.mqtt.normalize import normalize_message
from app.rollup import run_rollup_loop

logger = logging.getLogger(__name__)

MAX_BUFFER = 500
HEARTBEAT_INTERVAL_S = 60.0
MAX_BACKOFF_S = 60.0
# El bucle despierta al menos una vez por segundo para poder atender la parada y
# comprobar si toca vaciar, aunque no llegue ningún mensaje.
READ_TIMEOUT_S = 1.0
# Ventana de histórico con la que se siembra la línea de tiempo al arrancar.
PRIME_WINDOW_S = 7 * 24 * 3600


@dataclass
class Buffers:
    statuses: list[Status] = field(default_factory=list)
    packets: list[Packet] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)
    decode_failures: int = 0

    def pending(self) -> int:
        return len(self.statuses) + len(self.packets)


class TimelineCache:
    """Mantiene la línea de tiempo observer→preset, reconstruyendo solo lo que cambia."""

    def __init__(self) -> None:
        self._statuses: dict[str, list[Status]] = defaultdict(list)
        self._timelines: dict[str, Timeline] = {}
        self._dirty: set[str] = set()

    def add(self, status: Status) -> None:
        self._statuses[status.observer_pubkey].append(status)
        self._dirty.add(status.observer_pubkey)

    def timelines(self) -> dict[str, Timeline]:
        if self._dirty:
            refreshed: dict[str, Timeline] = {}
            for pubkey in self._dirty:
                refreshed.update(build_timelines(self._statuses[pubkey]))
            self._timelines.update(refreshed)
            self._dirty.clear()
        return self._timelines


def handle_message(topic: str, payload: object, prefix: str, buffers: Buffers) -> None:
    """Normaliza un mensaje y lo encola. Los no reconocidos se cuentan, no se tiran."""
    result = normalize_message(topic, payload, prefix)

    if result is None:
        return
    if isinstance(result, ParseError):
        first_time = buffers.errors[result.reason] == 0
        buffers.errors[result.reason] += 1
        if first_time:
            logger.warning(
                "Mensaje no reconocido en %s: %s %s",
                topic,
                result.reason,
                result.detail or "",
            )
        return

    if isinstance(result, Status):
        buffers.statuses.append(result)
    else:
        buffers.packets.append(result)


def flush(
    conn: sqlite3.Connection, buffers: Buffers, cache: TimelineCache, settings: Settings
) -> int:
    """Persiste el buffer. Devuelve cuántos paquetes se escribieron."""
    if not buffers.pending():
        return 0

    statuses = buffers.statuses
    packets = buffers.packets
    buffers.statuses = []
    buffers.packets = []

    for status in statuses:
        cache.add(status)

    rows = attribute_packets(packets, cache.timelines(), settings.status_max_gap_s)
    rows = _attach_decoded(rows, settings, buffers)

    with transaction(conn):
        for status in statuses:
            insert_status(conn, status)
        written = insert_packets(conn, rows)

    return written


def _attach_decoded(
    rows: list[AttributedPacket], settings: Settings, buffers: Buffers
) -> list[AttributedPacket]:
    """Añade el enriquecimiento del decodificador. Nunca descarta un paquete."""
    if not settings.decode_packets or not decoder_available():
        return rows

    enriched: list[AttributedPacket] = []
    for row in rows:
        info = decode_packet(row.packet.raw_hex)
        if info is not None:
            row = replace(row, decoded=info)
        elif row.packet.raw_hex:
            # Tenía raw y no se entendió: se cuenta, pero se guarda igual.
            buffers.decode_failures += 1
        enriched.append(row)
    return enriched


def _prime_timeline(conn: sqlite3.Connection, cache: TimelineCache) -> int:
    """Siembra la línea de tiempo con el histórico, para no perder la atribución de
    los paquetes que llegan antes del primer /status tras un reinicio."""
    since = int(time.time()) - PRIME_WINDOW_S
    historical = statuses_between(conn, since)
    for status in historical:
        cache.add(status)
    return len(historical)


def should_flush(pending: int, seconds_since_flush: float, interval_s: float) -> bool:
    """Cuándo vaciar el buffer.

    Con ``interval_s <= 0`` se escribe directamente: un commit por mensaje, sin
    buffer. A cambio, un commit por paquete a alto volumen sí cuesta.

    Nota: la frescura del *soroll* y de la *ocupació* no la decide esto, sino la
    fuente, que publica ``/status`` cada 300 s. Bajar el intervalo no las refresca.
    """
    if pending <= 0:
        return False
    if interval_s <= 0:
        return True
    return pending >= MAX_BUFFER or seconds_since_flush >= interval_s


def _client(settings: Settings) -> aiomqtt.Client:
    kwargs: dict[str, object] = {
        "hostname": settings.mqtt_host,
        "port": settings.mqtt_port,
    }
    if settings.mqtt_tls:
        kwargs["tls_context"] = ssl.create_default_context()
    if settings.mqtt_username:
        kwargs["username"] = settings.mqtt_username
        kwargs["password"] = settings.mqtt_password
    return aiomqtt.Client(**kwargs)  # type: ignore[arg-type]


async def run_collector(
    settings: Settings,
    conn: sqlite3.Connection,
    stop_event: asyncio.Event | None = None,
) -> None:
    buffers = Buffers()
    cache = TimelineCache()

    primed = _prime_timeline(conn, cache)
    if primed:
        logger.info(
            "Línea de tiempo sembrada con %d status del histórico", primed
        )

    if settings.decode_packets and not decoder_available():
        logger.warning(
            "CATMESH_DECODE_PACKETS está activo pero falta meshcoredecoder: "
            "se ingiere sin enriquecer (path_length/payload_type/is_valid serán NULL)"
        )

    # El rollup corre en el mismo proceso para que un solo systemd lo cubra todo.
    # Va en un hilo aparte con su propia conexión: no puede compartir la del colector.
    rollup_task = asyncio.create_task(
        run_rollup_loop(
            settings.db_path,
            settings.rollup_interval_s,
            settings.raw_retention_days,
        )
    )
    try:
        await _collect_forever(settings, conn, buffers, cache, stop_event)
    finally:
        rollup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await rollup_task


async def _collect_forever(
    settings: Settings,
    conn: sqlite3.Connection,
    buffers: Buffers,
    cache: TimelineCache,
    stop_event: asyncio.Event | None,
) -> None:
    packets_topic, status_topic = topics_for(settings)
    backoff = 1.0

    while stop_event is None or not stop_event.is_set():
        try:
            async with _client(settings) as client:
                logger.info(
                    "MQTT conectado a %s:%s", settings.mqtt_host, settings.mqtt_port
                )
                await client.subscribe(packets_topic)
                await client.subscribe(status_topic)
                logger.info("Suscrito a %s y %s", packets_topic, status_topic)
                backoff = 1.0
                await _consume(client, settings, conn, buffers, cache, stop_event)
        except aiomqtt.MqttError as exc:
            logger.warning("MQTT caído (%s); reintento en %.0f s", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
        finally:
            written = flush(conn, buffers, cache, settings)
            if written:
                logger.info("Volcados %d paquetes al perder la conexión", written)


async def _consume(
    client: aiomqtt.Client,
    settings: Settings,
    conn: sqlite3.Connection,
    buffers: Buffers,
    cache: TimelineCache,
    stop_event: asyncio.Event | None,
) -> None:
    iterator = client.messages.__aiter__()
    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()
    last_flush = loop.time()
    total_packets = 0

    while stop_event is None or not stop_event.is_set():
        try:
            message = await asyncio.wait_for(
                iterator.__anext__(), timeout=READ_TIMEOUT_S
            )
        except TimeoutError:
            if should_flush(
                buffers.pending(), loop.time() - last_flush, settings.flush_interval_s
            ):
                total_packets += flush(conn, buffers, cache, settings)
                last_flush = loop.time()
            continue
        except StopAsyncIteration:
            return

        handle_message(
            str(message.topic), message.payload, settings.topic_prefix, buffers
        )

        now = loop.time()
        if should_flush(buffers.pending(), now - last_flush, settings.flush_interval_s):
            total_packets += flush(conn, buffers, cache, settings)
            last_flush = now

        if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            total_packets += flush(conn, buffers, cache, settings)
            last_flush = now
            logger.info(
                "Latido: %d paquetes escritos, %d fallos de decode, no reconocidos: %s",
                total_packets,
                buffers.decode_failures,
                dict(buffers.errors) or "ninguno",
            )
            last_heartbeat = now


async def _main() -> None:
    from app.config import load_settings
    from app.db import connect, init_db

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = load_settings()
    conn = connect(settings.db_path)
    init_db(conn)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    logger.info("Base de datos en %s", settings.db_path)
    await run_collector(settings, conn, stop_event)
    conn.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
