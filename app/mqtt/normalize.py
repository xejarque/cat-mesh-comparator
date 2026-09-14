from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from app.models import Packet, ParseError, Status, TopicInfo

# Topics que existen en el ecosistema pero que no alimentan el comparador.
IGNORED_LEAVES = frozenset({"raw", "neighbors", "decoded", "debug"})
HANDLED_LEAVES = frozenset({"packets", "status"})

# El ancho de banda de LoRa solo puede tomar estos valores. Algunos firmware
# publican 62 en vez de 62.5 (redondeo), lo que partiría el preset en dos.
LORA_BANDWIDTHS_KHZ = (7.8, 10.4, 15.6, 20.8, 31.25, 41.67, 62.5, 125.0, 250.0, 500.0)
BANDWIDTH_TOLERANCE = 0.05

_NON_NUMERIC = re.compile(r"[^0-9.+-]+")


def parse_topic(topic: str, prefix: str = "meshcore") -> TopicInfo | None:
    parts = topic.strip("/").split("/")
    if len(parts) != 4:
        return None
    head, iata, pubkey, leaf = parts
    if head != prefix.strip("/") or not pubkey:
        return None
    return TopicInfo(iata=iata.upper(), pubkey=pubkey.upper(), leaf=leaf)


def parse_ts(value: Any) -> int | None:
    """Acepta epoch (número o cadena) y varios ISO 8601. Naive se asume UTC."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if math.isfinite(float(value)) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def parse_radio(raw: Any) -> tuple[float | None, float | None, int | None, int | None]:
    """Parsea el campo ``radio`` de ``/status`` → (freq MHz, bw kHz, sf, cr).

    Formatos reales vistos en el broker de Cataluña:
      ``"869.618,62.5,7,6"``            (lo habitual)
      ``"869.6179809,62.5,7,6"``        (float32 del mismo valor)
      ``"SX1262 869.618/62/7/6"``       (prefijo de chip y barras)
      ``""``                            (status offline, sin datos de radio)
    """
    if not isinstance(raw, str):
        return (None, None, None, None)

    numbers = [
        value
        for token in _NON_NUMERIC.split(raw.strip())
        if (value := _as_float(token)) is not None
    ]
    if len(numbers) < 4:
        return (None, None, None, None)

    start = _radio_offset(numbers)
    if start is None:
        return (None, None, None, None)

    freq, bw, sf, cr = numbers[start : start + 4]
    return (_canonical_freq(freq), _snap_bandwidth(bw), _as_int(sf), _as_int(cr))


def _radio_offset(numbers: list[float]) -> int | None:
    """Localiza dónde empieza la tétrada, ignorando el número del chip (SX1262)."""
    for low, high in ((100.0, 1_000.0), (100_000.0, 1_000_000.0)):
        for index, value in enumerate(numbers):
            if low <= value <= high and len(numbers) - index >= 4:
                return index
    return None


def _canonical_freq(value: float) -> float:
    # Muchos firmware guardan la frecuencia como float32, así que 869.618 llega
    # como 869.6179809. Redondear a 3 decimales (1 kHz) los agrupa en un preset.
    return round(value / 1000.0, 3) if value > 1000 else round(value, 3)


def _snap_bandwidth(value: float) -> float:
    if value >= 1000:  # venía en Hz
        value = value / 1000.0
    nearest = min(LORA_BANDWIDTHS_KHZ, key=lambda known: abs(known - value))
    if abs(nearest - value) / nearest <= BANDWIDTH_TOLERANCE:
        return nearest
    return round(value, 3)


def normalize_message(
    topic: str, payload: Any, prefix: str = "meshcore"
) -> Packet | Status | ParseError | None:
    """Normaliza un mensaje MQTT. ``None`` = topic ignorado."""
    info = parse_topic(topic, prefix)
    if info is None:
        return ParseError("topic_unparseable", topic)
    if info.leaf in IGNORED_LEAVES:
        return None
    if info.leaf not in HANDLED_LEAVES:
        return ParseError("topic_unhandled", info.leaf)

    text = _decode(payload)
    if text is None:
        return ParseError("payload_not_utf8")

    stripped = text.strip()
    if not stripped:
        return ParseError("payload_empty")

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # Contrato alternativo: hay bridges que publican bytes crudos en vez de JSON.
        # Fase 0 decide si el broker real hace esto.
        return ParseError("payload_not_json", stripped[:64])

    if not isinstance(data, dict):
        return ParseError("payload_not_object")

    return _packet(info, data) if info.leaf == "packets" else _status(info, data)


def _packet(info: TopicInfo, data: dict[str, Any]) -> Packet | ParseError:
    ts = parse_ts(data.get("timestamp"))
    if ts is None:
        return ParseError("missing_timestamp")

    snr_db = _as_float(data.get("SNR"))
    rssi = _as_int(data.get("RSSI"))
    raw_hex = _as_str(data.get("raw"))
    packet_hash = _as_str(data.get("hash"))

    if snr_db is None and rssi is None and raw_hex is None and packet_hash is None:
        # Tramas vacías que emiten algunos nodos (SNR/RSSI/hash/raw en blanco).
        # Contarlas inflaría la tasa de paquetes por preset.
        return ParseError("packet_without_data")

    return Packet(
        ts=ts,
        observer_pubkey=info.pubkey,
        iata=info.iata,
        # Se guarda x4 como el firmware: evita perder precisión de 0.25 dB.
        snr_x4=None if snr_db is None else int(round(snr_db * 4)),
        rssi=rssi,
        packet_type=_as_int(data.get("packet_type")),
        route=_as_str(data.get("route")),
        payload_len=_as_int(data.get("payload_len")),
        raw_hex=raw_hex,
        origin=_as_str(data.get("origin")),
        packet_hash=None if packet_hash is None else packet_hash.upper(),
    )


def _status(info: TopicInfo, data: dict[str, Any]) -> Status | ParseError:
    ts = parse_ts(data.get("timestamp"))
    if ts is None:
        return ParseError("missing_timestamp")

    radio_raw = _as_str(data.get("radio")) or ""
    freq, bw, sf, cr = parse_radio(radio_raw)
    stats = data.get("stats")
    stats = stats if isinstance(stats, dict) else {}

    return Status(
        ts=ts,
        observer_pubkey=info.pubkey,
        iata=info.iata,
        radio_raw=radio_raw,
        freq_mhz=freq,
        bw_khz=bw,
        sf=sf,
        cr=cr,
        noise_floor=_as_int(stats.get("noise_floor")),
        tx_air_secs=_as_int(stats.get("tx_air_secs")),
        rx_air_secs=_as_int(stats.get("rx_air_secs")),
        recv_errors=_as_int(stats.get("recv_errors")),
        uptime_secs=_as_int(stats.get("uptime_secs")),
        battery_mv=_as_int(stats.get("battery_mv")),
        queue_len=_as_int(stats.get("queue_len")),
        model=_as_str(data.get("model")),
        fw=_as_str(data.get("firmware_version")),
        online=(_as_str(data.get("status")) or "").lower() != "offline",
        name=_as_str(data.get("origin")),
    )


def _decode(payload: Any) -> str | None:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (bytes, bytearray, memoryview)):
        try:
            return bytes(payload).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(round(number))
