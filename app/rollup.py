"""Consolidación de ``raw_packets`` en los agregados que alimentan el comparador.

Tres decisiones que definen este módulo:

* **No vuelve a atribuir.** Lee la atribución ya resuelta y guardada en la ingesta, así
  que el agregado no puede discrepar de lo que hay en ``raw_packets``.
* **Es idempotente.** Recalcula una ventana y hace ``INSERT OR REPLACE``: se puede
  reejecutar sin duplicar, y los datos que llegan tarde entran en la siguiente pasada.
* **Es tolerante a huecos.** Un minuto sin datos simplemente no genera fila; no se
  rellena con ceros, porque un cero en SNR o en ocupación sería mentira.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.aggregate import aggregate
from app.db import (
    DataQuality,
    PresetBucket,
    connect,
    init_db,
    packets_between,
    prune_raw_packets,
    replace_channel_minutes,
    replace_data_quality,
    replace_observer_minutes,
    replace_pair_minutes,
    replace_preset_buckets,
    replace_preset_minutes,
    statuses_between,
    transaction,
)
from app.models import PresetKey
from app.stats import weighted_mean as _weighted_mean

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_MINUTES = 30
SECONDS_PER_HOUR = 3_600
SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, slots=True)
class RollupReport:
    preset_minutes: int
    channel_minutes: int
    observer_minutes: int
    pair_minutes: int
    hours: int
    days: int
    days_with_quality: int
    pruned_packets: int
    uncertain_packets: int
    unattributed_packets: int


def _floor(ts: int, size: int) -> int:
    return ts - (ts % size)


def rollup_window(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None = None
) -> tuple[int, int, int, int, int, int]:
    """Recalcula los agregados por minuto de la ventana. Devuelve los contadores."""
    statuses = statuses_between(conn, since_ts, until_ts)
    packets = packets_between(conn, since_ts, until_ts)
    result = aggregate(statuses, packets)

    with transaction(conn):
        preset_minutes = replace_preset_minutes(conn, result.presets)
        channel_minutes = replace_channel_minutes(conn, result.channels)
        observer_minutes = replace_observer_minutes(conn, result.observers)
        pair_minutes = replace_pair_minutes(conn, result.pairs)

    return (
        preset_minutes,
        channel_minutes,
        observer_minutes,
        pair_minutes,
        result.uncertain_packets,
        result.unattributed_packets,
    )


def consolidate(conn: sqlite3.Connection, bucket_seconds: int, since_ts: int) -> int:
    """Consolida ``preset_minute`` en ``preset_hour`` o ``preset_day``."""
    if bucket_seconds == SECONDS_PER_HOUR:
        table = "preset_hour"
    elif bucket_seconds == SECONDS_PER_DAY:
        table = "preset_day"
    else:
        raise ValueError(f"tamaño de bloque no soportado: {bucket_seconds}")

    buckets = _consolidate_from_minutes(conn, bucket_seconds, since_ts)
    with transaction(conn):
        return replace_preset_buckets(conn, table, buckets)


def _consolidate_from_minutes(
    conn: sqlite3.Connection, bucket_seconds: int, since_ts: int
) -> list[PresetBucket]:
    rows = conn.execute(
        """SELECT pm.minute_ts, pm.pkts, pm.uniq_hashes, pm.snr_p50_x4,
                  pm.snr_ge0_pct, pm.rssi_avg, pm.observers,
                  p.freq_mhz, p.bw_khz, p.sf, p.cr
           FROM preset_minute pm
           JOIN presets p ON p.preset_id = pm.preset_id
           WHERE pm.minute_ts >= ?
           ORDER BY pm.minute_ts""",
        (since_ts,),
    ).fetchall()

    grouped: dict[tuple[int, PresetKey], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        preset = PresetKey(row["freq_mhz"], row["bw_khz"], row["sf"], row["cr"])
        grouped[(_floor(row["minute_ts"], bucket_seconds), preset)].append(row)

    buckets: list[PresetBucket] = []
    for (bucket_ts, preset), items in sorted(grouped.items()):
        weights = [item["pkts"] for item in items]
        buckets.append(
            PresetBucket(
                bucket_ts=bucket_ts,
                preset=preset,
                pkts=sum(weights),
                uniq_hashes=sum(item["uniq_hashes"] for item in items),
                snr_p50_x4=_weighted_mean(
                    [item["snr_p50_x4"] for item in items], weights
                ),
                snr_ge0_pct=_weighted_mean(
                    [item["snr_ge0_pct"] for item in items], weights
                ),
                rssi_avg=_weighted_mean([item["rssi_avg"] for item in items], weights),
                observers=max(item["observers"] for item in items),
            )
        )
    return buckets


def _days_with_data(
    conn: sqlite3.Connection, since_ts: int, until_ts: int
) -> list[int]:
    """Solo los días que tienen paquetes.

    Recorrer el calendario entero desde el epoch genera miles de filas de ceros en
    ``data_quality``: con ``--full`` llegó a crear 20.000 filas vacías.
    """
    rows = conn.execute(
        """SELECT DISTINCT ts - (ts % ?) AS day_ts
           FROM raw_packets
           WHERE ts >= ? AND ts < ?
           ORDER BY day_ts""",
        (SECONDS_PER_DAY, since_ts, until_ts),
    ).fetchall()
    return [row["day_ts"] for row in rows]


def refresh_data_quality(conn: sqlite3.Connection, day_ts: int) -> DataQuality:
    end = day_ts + SECONDS_PER_DAY

    observers_without_status = conn.execute(
        """SELECT COUNT(DISTINCT r.observer_pubkey) AS n
           FROM raw_packets r
           WHERE r.ts >= ? AND r.ts < ?
             AND NOT EXISTS (
                 SELECT 1 FROM observer_status s
                 WHERE s.pubkey = r.observer_pubkey AND s.preset_id IS NOT NULL)""",
        (day_ts, end),
    ).fetchone()["n"]

    counts = conn.execute(
        """SELECT COALESCE(SUM(preset_uncertain), 0) AS uncertain,
                  COALESCE(SUM(preset_id IS NULL), 0) AS unattributed
           FROM raw_packets WHERE ts >= ? AND ts < ?""",
        (day_ts, end),
    ).fetchone()

    # Mismo hash en dos canales FÍSICOS distintos = una de las dos atribuciones está
    # mal: una transmisión no se puede oír en dos frecuencias.
    #
    # Ojo con el CR: NO entra en la comparación. La tasa de codificación viaja en la
    # cabecera LoRa, así que dos receptores con CR distinto sobre la misma frecuencia
    # y SF decodifican la misma transmisión. Contarlo daba 14 falsos positivos en la
    # primera medición. El canal físico es (freq, bw, sf).
    hash_conflicts = conn.execute(
        """SELECT COUNT(*) AS n FROM (
               SELECT r.packet_hash
               FROM raw_packets r
               JOIN presets p ON p.preset_id = r.preset_id
               WHERE r.ts >= ? AND r.ts < ?
                 AND r.packet_hash IS NOT NULL AND r.preset_id IS NOT NULL
               GROUP BY r.packet_hash
               HAVING COUNT(DISTINCT p.freq_mhz || '|' || p.bw_khz || '|' || p.sf) > 1)""",
        (day_ts, end),
    ).fetchone()["n"]

    row = DataQuality(
        observers_without_status=observers_without_status,
        uncertain_packets=counts["uncertain"],
        unattributed_packets=counts["unattributed"],
        hash_conflicts=hash_conflicts,
    )
    with transaction(conn):
        replace_data_quality(conn, day_ts, row)
    return row


def run_rollup(
    conn: sqlite3.Connection,
    *,
    now: int | None = None,
    since_ts: int | None = None,
    backfill_minutes: int = DEFAULT_BACKFILL_MINUTES,
    retention_days: int = 30,
) -> RollupReport:
    """Recalcula, consolida y poda.

    Por defecto mira solo hacia atrás (``backfill_minutes``), porque los datos solo
    crecen: recalcular la cola basta para capturar lo que llegó tarde. Con
    ``since_ts=0`` se recalcula todo el histórico.
    """
    moment = int(time.time()) if now is None else now
    since = (
        _floor(since_ts, 60)
        if since_ts is not None
        else _floor(moment - backfill_minutes * 60, 60)
    )

    (
        preset_minutes,
        channel_minutes,
        observer_minutes,
        pair_minutes,
        uncertain,
        unattributed,
    ) = rollup_window(conn, since)

    hours = consolidate(conn, SECONDS_PER_HOUR, _floor(since, SECONDS_PER_HOUR))
    days = consolidate(conn, SECONDS_PER_DAY, _floor(since, SECONDS_PER_DAY))

    days_with_quality = 0
    for day_ts in _days_with_data(conn, since, moment + 1):
        refresh_data_quality(conn, day_ts)
        days_with_quality += 1

    # Poda después de agregar: si se podara antes, se perderían datos sin consolidar.
    pruned = prune_raw_packets(conn, retention_days) if retention_days > 0 else 0

    return RollupReport(
        preset_minutes=preset_minutes,
        channel_minutes=channel_minutes,
        observer_minutes=observer_minutes,
        pair_minutes=pair_minutes,
        hours=hours,
        days=days,
        days_with_quality=days_with_quality,
        pruned_packets=pruned,
        uncertain_packets=uncertain,
        unattributed_packets=unattributed,
    )


def rollup_with_own_connection(
    db_path: Path, retention_days: int = 30
) -> RollupReport:
    """Ejecuta el rollup con su propia conexión.

    Una conexión SQLite está atada al hilo que la creó, y el rollup corre en un hilo
    aparte (``asyncio.to_thread``) para no bloquear el bucle de red. Compartir la
    conexión del colector falla con ``ProgrammingError``.
    """
    conn = connect(db_path)
    try:
        return run_rollup(conn, retention_days=retention_days)
    finally:
        conn.close()


async def run_rollup_loop(
    db_path: Path, interval_s: float = 300.0, retention_days: int = 30
) -> None:
    """Tarea periódica para el proceso del colector."""
    while True:
        try:
            report = await asyncio.to_thread(
                rollup_with_own_connection, db_path, retention_days
            )
            logger.info(
                "Rollup: %d min de preset, %d de canal, %d de observer, %d pares, "
                "%d sin atribuir, %d podados",
                report.preset_minutes,
                report.channel_minutes,
                report.observer_minutes,
                report.pair_minutes,
                report.unattributed_packets,
                report.pruned_packets,
            )
        except Exception:  # noqa: BLE001 - un fallo del rollup no puede matar la ingesta
            logger.exception("El rollup falló; se reintentará en el próximo ciclo")
        await asyncio.sleep(interval_s)


def _main() -> None:
    from app.config import load_settings

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    parser = argparse.ArgumentParser(description="Consolida los agregados del observatorio")
    parser.add_argument(
        "--full",
        action="store_true",
        help="recalcula todo el histórico, no solo la cola reciente",
    )
    parser.add_argument("--minutes", type=int, default=DEFAULT_BACKFILL_MINUTES)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    db_path = args.db or settings.db_path
    conn = connect(db_path)
    init_db(conn)

    report = run_rollup(
        conn,
        since_ts=0 if args.full else None,
        backfill_minutes=args.minutes,
        retention_days=settings.raw_retention_days,
    )
    logger.info(
        "Hecho sobre %s: %d min (%d de canal), %d horas, %d días, %d pares, "
        "%d sin atribuir, %d podados",
        db_path,
        report.preset_minutes,
        report.channel_minutes,
        report.hours,
        report.days,
        report.pair_minutes,
        report.unattributed_packets,
        report.pruned_packets,
    )
    conn.close()


if __name__ == "__main__":
    _main()
