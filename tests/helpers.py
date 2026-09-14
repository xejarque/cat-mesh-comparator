"""Constructores de datos de prueba, con las formas reales del contrato MQTT.

Los payloads se serializan a JSON a propósito: así los tests recorren la misma
ruta que el colector (bytes/str → decode → parse), no un atajo.
"""

from __future__ import annotations

import json

from app.models import AttributedPacket, Attribution, Packet, PresetKey, Status
from app.mqtt.normalize import normalize_message, parse_ts

IATA = "BAR"
DEFAULT_PUBKEY = "AA11BB22"
DEFAULT_RADIO = "869.525,62.5,11,5"

# Paquete real capturado del broker de Cataluña (BCN). Decodifica como Advert
# con un salto de camino, así que sirve para probar el enriquecimiento.
RAW_ADVERT = (
    "114169995DDA35673215CA6EEE2913E407FED53E0652FDFAC123D1A60F7E6BE955"
    "2220287BECA76A6804A3C720734142E08032E0652C94EACACCA205346355D79DB2"
    "13F1D73E9135333F4E6AA449395AAC872753635EEE4AA69F7F2A76052B3293A85"
    "18F5427180291DAB178026FB12200426F745F4D6F6E74676174"
)


def epoch(iso: str) -> int:
    value = parse_ts(iso)
    assert value is not None, iso
    return value


def packet_message(
    ts_iso: str,
    pubkey: str = DEFAULT_PUBKEY,
    *,
    packet_hash: str | None = None,
    snr: str | None = "10.0",
    rssi: str | None = "-70",
    packet_type: str | None = "2",
    raw: str | None = "f59301",
) -> tuple[str, str]:
    payload: dict[str, object] = {
        "origin": "Obs",
        "origin_id": pubkey.lower(),
        "timestamp": ts_iso,
        "type": "PACKET",
        "direction": "rx",
        "time": "00:00:00",
        "date": "1/1/2026",
        "len": "45",
        "route": "F",
        "payload_len": "32",
    }
    if raw is not None:
        payload["raw"] = raw
    if packet_type is not None:
        payload["packet_type"] = packet_type
    if snr is not None:
        payload["SNR"] = snr
    if rssi is not None:
        payload["RSSI"] = rssi
    if packet_hash is not None:
        payload["hash"] = packet_hash

    return f"meshcore/{IATA}/{pubkey}/packets", json.dumps(payload)


def status_message(
    ts_iso: str,
    radio: str = DEFAULT_RADIO,
    pubkey: str = DEFAULT_PUBKEY,
    **stats_over: object,
) -> tuple[str, str]:
    stats: dict[str, object] = {
        "battery_mv": 4000,
        "uptime_secs": 1000,
        "queue_len": 0,
        "noise_floor": -118,
        "tx_air_secs": 0,
        "rx_air_secs": 0,
        "recv_errors": 0,
    }
    stats.update(stats_over)

    payload = {
        "status": "online",
        "timestamp": ts_iso,
        "origin": "Obs",
        "origin_id": pubkey.lower(),
        "radio": radio,
        "model": "T1000-E",
        "firmware_version": "1.17.0",
        "stats": stats,
    }
    return f"meshcore/{IATA}/{pubkey}/status", json.dumps(payload)


def mk_packet(ts_iso: str, pubkey: str = DEFAULT_PUBKEY, **kwargs: object) -> Packet:
    topic, payload = packet_message(ts_iso, pubkey, **kwargs)  # type: ignore[arg-type]
    result = normalize_message(topic, payload)
    assert isinstance(result, Packet), result
    return result


def mk_status(
    ts_iso: str,
    radio: str = DEFAULT_RADIO,
    pubkey: str = DEFAULT_PUBKEY,
    **stats_over: object,
) -> Status:
    topic, payload = status_message(ts_iso, radio, pubkey, **stats_over)
    result = normalize_message(topic, payload)
    assert isinstance(result, Status), result
    return result


def attribute(
    packet: Packet, preset: PresetKey | None, uncertain: bool = False
) -> AttributedPacket:
    return AttributedPacket(
        packet=packet, attribution=Attribution(preset=preset, uncertain=uncertain)
    )


def fill_observer_minute(
    conn,
    pubkey: str,
    preset: PresetKey,
    start_iso: str,
    minutes: int,
    *,
    noise: float = -100.0,
    pkts_rx: int = 2,
    snr_x4: float | None = None,
) -> None:
    """Escribe ``observer_minute`` directamente.

    La detección de regímenes lee de ahí, así que montar los datos minuto a minuto
    por la vía larga (status → rollup → agregado) haría los tests de períodos
    larguísimos y frágiles.
    """
    from app.db import ensure_observer, ensure_preset

    base = epoch(start_iso)
    preset_id = ensure_preset(conn, preset)
    ensure_observer(conn, pubkey, iata="BAR", ts=base)
    conn.executemany(
        """INSERT OR REPLACE INTO observer_minute
           (pubkey, minute_ts, preset_id, noise_floor, chan_util_pct, err_per_h,
            pkts_rx, snr_p50_x4)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (pubkey, base + index * 60, preset_id, noise, 1.0, 0.5, pkts_rx, snr_x4)
            for index in range(minutes)
        ],
    )


def fill_channel_minute(
    conn,
    preset: PresetKey,
    start_iso: str,
    minutes: int,
    *,
    pkts: int = 10,
    uniq_hashes: int = 5,
    snr_p50_x4: float = 40.0,
    observers: int = 2,
) -> None:
    """Escribe ``channel_minute`` directamente, para los tests del comparador."""
    from app.db import ensure_preset
    from app.presets import channel_id

    base = epoch(start_iso)
    ensure_preset(conn, preset)
    conn.executemany(
        """INSERT OR REPLACE INTO channel_minute
           (minute_ts, channel_id, freq_mhz, bw_khz, sf, pkts, uniq_hashes,
            snr_avg_x4, snr_p50_x4, snr_ge0_pct, rssi_avg, observers, crs)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                base + index * 60,
                channel_id(preset.freq_mhz, preset.bw_khz, preset.sf),
                preset.freq_mhz,
                preset.bw_khz,
                preset.sf,
                pkts,
                uniq_hashes,
                snr_p50_x4,
                snr_p50_x4,
                100.0,
                -70.0,
                observers,
                str(preset.cr),
            )
            for index in range(minutes)
        ],
    )
