from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.aggregate import ChannelMinute, ObserverMinute, PairMinute, PresetMinute
from app.models import (
    AttributedPacket,
    Attribution,
    DecodedInfo,
    Packet,
    PresetKey,
    Status,
)
from app.presets import SEED_PRESETS, channel_id, preset_label


@dataclass(frozen=True, slots=True)
class PresetBucket:
    """Fila consolidada por hora o por día (misma forma para ambas tablas)."""

    bucket_ts: int
    preset: PresetKey
    pkts: int
    uniq_hashes: int
    snr_p50_x4: float | None
    snr_ge0_pct: float | None
    rssi_avg: float | None
    observers: int


@dataclass(frozen=True, slots=True)
class DataQuality:
    observers_without_status: int
    uncertain_packets: int
    unattributed_packets: int
    hash_conflicts: int


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
SCHEMA_VERSION = 5
SECONDS_PER_DAY = 86_400

# Tablas derivadas cuya clave cambió en la v3. Se reconstruyen desde raw_packets,
# así que rehacerlas no pierde información.
DERIVED_TABLES_V3 = ("pair_minute",)

# Columnas añadidas después de la v1. `schema.sql` ya las trae para bases nuevas;
# esto las añade a las que se crearon antes.
ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("raw_packets", "path_length", "INTEGER"),
    ("raw_packets", "payload_type", "TEXT"),
    ("raw_packets", "is_valid", "INTEGER"),
    ("observer_minute", "snr_p50_x4", "REAL"),
    ("observer_minute", "rssi_avg", "REAL"),
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection) -> None:
    _migrate(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    for table, column, declaration in ADDITIVE_COLUMNS:
        _ensure_column(conn, table, column, declaration)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    with transaction(conn):
        for preset in SEED_PRESETS:
            ensure_preset(conn, preset)


def _migrate(conn: sqlite3.Connection) -> None:
    """Cambios de esquema que ``CREATE TABLE IF NOT EXISTS`` no puede aplicar."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 3:
        # pair_minute pasó a estar indexado por canal físico en vez de por preset.
        for table in DERIVED_TABLES_V3:
            conn.execute(f"DROP TABLE IF EXISTS {table}")


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, declaration: str
) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def ensure_preset(conn: sqlite3.Connection, preset: PresetKey) -> str:
    conn.execute(
        """INSERT OR IGNORE INTO presets (preset_id, freq_mhz, bw_khz, sf, cr, label)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            preset.db_id,
            preset.freq_mhz,
            preset.bw_khz,
            preset.sf,
            preset.cr,
            preset_label(preset),
        ),
    )
    row = conn.execute(
        """SELECT preset_id FROM presets
           WHERE freq_mhz = ? AND bw_khz = ? AND sf = ? AND cr = ?""",
        (preset.freq_mhz, preset.bw_khz, preset.sf, preset.cr),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"no se pudo registrar el preset {preset.db_id}")
    return row["preset_id"]


def ensure_observer(
    conn: sqlite3.Connection,
    pubkey: str,
    *,
    iata: str | None = None,
    name: str | None = None,
    model: str | None = None,
    fw: str | None = None,
    ts: int | None = None,
) -> None:
    moment = int(time.time()) if ts is None else ts
    conn.execute(
        """INSERT INTO observers (pubkey, name, iata, model, fw, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (pubkey) DO UPDATE SET
               first_seen = MIN(observers.first_seen, excluded.first_seen),
               last_seen  = MAX(observers.last_seen,  excluded.last_seen),
               name  = COALESCE(excluded.name,  observers.name),
               iata  = COALESCE(excluded.iata,  observers.iata),
               model = COALESCE(excluded.model, observers.model),
               fw    = COALESCE(excluded.fw,    observers.fw)""",
        (pubkey, name, iata, model, fw, moment, moment),
    )


def insert_status(conn: sqlite3.Connection, status: Status) -> None:
    ensure_observer(
        conn,
        status.observer_pubkey,
        iata=status.iata,
        name=status.name,
        model=status.model,
        fw=status.fw,
        ts=status.ts,
    )
    preset = status.preset
    preset_id = ensure_preset(conn, preset) if preset is not None else None
    conn.execute(
        """INSERT OR IGNORE INTO observer_status
           (pubkey, ts, radio_raw, preset_id, noise_floor, tx_air_secs, rx_air_secs,
            recv_errors, uptime_secs, battery_mv, queue_len, online)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            status.observer_pubkey,
            status.ts,
            status.radio_raw,
            preset_id,
            status.noise_floor,
            status.tx_air_secs,
            status.rx_air_secs,
            status.recv_errors,
            status.uptime_secs,
            status.battery_mv,
            status.queue_len,
            int(status.online),
        ),
    )


def upsert_node_position(
    conn: sqlite3.Connection,
    pubkey: str,
    lat: float,
    lon: float,
    *,
    name: str | None = None,
    seen_ts: int | None = None,
) -> None:
    """Guarda la posición que un nodo declaró en su advert.

    El ``WHERE`` del upsert es importante: un advert viejo que llega tarde no debe
    pisar una posición más reciente.
    """
    moment = int(time.time()) if seen_ts is None else seen_ts
    conn.execute(
        """INSERT INTO node_positions (pubkey, name, lat, lon, seen_ts)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (pubkey) DO UPDATE SET
               lat     = excluded.lat,
               lon     = excluded.lon,
               seen_ts = excluded.seen_ts,
               name    = COALESCE(excluded.name, node_positions.name)
           WHERE excluded.seen_ts >= node_positions.seen_ts""",
        (pubkey, name, lat, lon, moment),
    )


def insert_packets(conn: sqlite3.Connection, rows: Iterable[AttributedPacket]) -> int:
    payload: list[tuple[object, ...]] = []
    known_observers: set[str] = set()

    for row in rows:
        packet = row.packet
        if packet.observer_pubkey not in known_observers:
            ensure_observer(
                conn,
                packet.observer_pubkey,
                iata=packet.iata,
                name=packet.origin,
                ts=packet.ts,
            )
            known_observers.add(packet.observer_pubkey)
        preset_id = ensure_preset(conn, row.preset) if row.preset is not None else None
        decoded = row.decoded
        if decoded is not None and decoded.lat and decoded.lon and decoded.sender_pubkey:
            # La posición es del EMISOR del advert, no del receptor que lo oyó.
            upsert_node_position(
                conn,
                decoded.sender_pubkey,
                decoded.lat,
                decoded.lon,
                name=decoded.sender_name,
                seen_ts=packet.ts,
            )
        payload.append(
            (
                packet.ts,
                packet.observer_pubkey,
                preset_id,
                int(row.uncertain),
                packet.packet_hash,
                packet.snr_x4,
                packet.rssi,
                packet.packet_type,
                packet.route,
                packet.payload_len,
                packet.raw_hex,
                None if decoded is None else decoded.path_length,
                None if decoded is None else decoded.payload_type,
                None
                if decoded is None or decoded.is_valid is None
                else int(decoded.is_valid),
            )
        )

    if not payload:
        return 0

    conn.executemany(
        """INSERT INTO raw_packets
           (ts, observer_pubkey, preset_id, preset_uncertain, packet_hash, snr_x4,
            rssi, packet_type, route, payload_len, raw_hex,
            path_length, payload_type, is_valid)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    return len(payload)


def prune_raw_packets(conn: sqlite3.Connection, retention_days: int) -> int:
    cutoff = int(time.time()) - retention_days * SECONDS_PER_DAY
    cursor = conn.execute("DELETE FROM raw_packets WHERE ts < ?", (cutoff,))
    return cursor.rowcount


def replace_preset_minutes(
    conn: sqlite3.Connection, rows: Iterable[PresetMinute]
) -> int:
    payload = [
        (
            row.minute_ts,
            row.preset.db_id,
            row.pkts,
            row.uniq_hashes,
            row.snr_avg_x4,
            row.snr_p50_x4,
            row.snr_ge0_pct,
            row.rssi_avg,
            row.observers,
        )
        for row in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO preset_minute
           (minute_ts, preset_id, pkts, uniq_hashes, snr_avg_x4, snr_p50_x4,
            snr_ge0_pct, rssi_avg, observers)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    return len(payload)


def replace_observer_minutes(
    conn: sqlite3.Connection, rows: Iterable[ObserverMinute]
) -> int:
    payload = [
        (
            row.observer_pubkey,
            row.minute_ts,
            None if row.preset is None else row.preset.db_id,
            row.noise_floor,
            row.chan_util_pct,
            row.err_per_h,
            row.pkts_rx,
            row.snr_p50_x4,
            row.rssi_avg,
        )
        for row in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO observer_minute
           (pubkey, minute_ts, preset_id, noise_floor, chan_util_pct, err_per_h,
            pkts_rx, snr_p50_x4, rssi_avg)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    return len(payload)


def replace_channel_minutes(
    conn: sqlite3.Connection, rows: Iterable[ChannelMinute]
) -> int:
    payload = [
        (
            row.minute_ts,
            channel_id(row.freq_mhz, row.bw_khz, row.sf),
            row.freq_mhz,
            row.bw_khz,
            row.sf,
            row.pkts,
            row.uniq_hashes,
            row.snr_avg_x4,
            row.snr_p50_x4,
            row.snr_ge0_pct,
            row.rssi_avg,
            row.observers,
            ",".join(str(cr) for cr in row.crs) or None,
        )
        for row in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO channel_minute
           (minute_ts, channel_id, freq_mhz, bw_khz, sf, pkts, uniq_hashes,
            snr_avg_x4, snr_p50_x4, snr_ge0_pct, rssi_avg, observers, crs)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    return len(payload)


def replace_pair_minutes(conn: sqlite3.Connection, rows: Iterable[PairMinute]) -> int:
    payload = [
        (
            row.minute_ts,
            channel_id(row.freq_mhz, row.bw_khz, row.sf),
            row.obs_a,
            row.obs_b,
            row.heard_a,
            row.heard_b,
            row.both,
            row.pdr_pct,
        )
        for row in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO pair_minute
           (minute_ts, channel_id, obs_a, obs_b, heard_a, heard_b, both, pdr_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    return len(payload)


def replace_preset_buckets(
    conn: sqlite3.Connection, table: str, rows: Iterable[PresetBucket]
) -> int:
    if table not in {"preset_hour", "preset_day"}:
        raise ValueError(f"tabla de consolidación no soportada: {table}")
    payload = [
        (
            row.bucket_ts,
            row.preset.db_id,
            row.pkts,
            row.uniq_hashes,
            row.snr_p50_x4,
            row.snr_ge0_pct,
            row.rssi_avg,
            row.observers,
        )
        for row in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        f"""INSERT OR REPLACE INTO {table}
            ({"hour_ts" if table == "preset_hour" else "day_ts"}, preset_id, pkts,
             uniq_hashes, snr_p50_x4, snr_ge0_pct, rssi_avg, observers)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    return len(payload)


def replace_data_quality(
    conn: sqlite3.Connection, day_ts: int, row: DataQuality
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO data_quality
           (day_ts, observers_without_status, uncertain_packets,
            unattributed_packets, hash_conflicts)
           VALUES (?, ?, ?, ?, ?)""",
        (
            day_ts,
            row.observers_without_status,
            row.uncertain_packets,
            row.unattributed_packets,
            row.hash_conflicts,
        ),
    )


def statuses_between(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None = None
) -> list[Status]:
    """Reconstruye los status de un intervalo.

    Se usa para dos cosas: sembrar la línea de tiempo al arrancar (sin esto, un
    reinicio deja sin atribuir los paquetes que lleguen antes del primer ``/status``
    del observer) y alimentar los rollups.
    """
    bounds = "s.ts >= ?"
    params: list[object] = [since_ts]
    if until_ts is not None:
        bounds += " AND s.ts < ?"
        params.append(until_ts)

    rows = conn.execute(
        f"""SELECT s.ts, s.pubkey, s.radio_raw, s.noise_floor, s.tx_air_secs,
                   s.rx_air_secs, s.recv_errors, s.uptime_secs, s.battery_mv,
                   s.queue_len, s.online, o.iata, o.model, o.fw, o.name,
                   p.freq_mhz, p.bw_khz, p.sf, p.cr
            FROM observer_status s
            JOIN observers o ON o.pubkey = s.pubkey
            LEFT JOIN presets p ON p.preset_id = s.preset_id
            WHERE {bounds}
            ORDER BY s.pubkey, s.ts""",
        params,
    ).fetchall()

    return [
        Status(
            ts=row["ts"],
            observer_pubkey=row["pubkey"],
            iata=row["iata"] or "",
            radio_raw=row["radio_raw"] or "",
            freq_mhz=row["freq_mhz"],
            bw_khz=row["bw_khz"],
            sf=row["sf"],
            cr=row["cr"],
            noise_floor=row["noise_floor"],
            tx_air_secs=row["tx_air_secs"],
            rx_air_secs=row["rx_air_secs"],
            recv_errors=row["recv_errors"],
            uptime_secs=row["uptime_secs"],
            battery_mv=row["battery_mv"],
            queue_len=row["queue_len"],
            model=row["model"],
            fw=row["fw"],
            online=bool(row["online"]),
            name=row["name"],
        )
        for row in rows
    ]


def packets_between(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None = None
) -> list[AttributedPacket]:
    """Reconstruye los paquetes con la atribución **ya resuelta** en la ingesta.

    Los rollups no vuelven a atribuir: leen la decisión que se tomó y se guardó, para
    que el agregado no pueda discrepar de lo que hay en ``raw_packets``.
    """
    bounds = "r.ts >= ?"
    params: list[object] = [since_ts]
    if until_ts is not None:
        bounds += " AND r.ts < ?"
        params.append(until_ts)

    rows = conn.execute(
        f"""SELECT r.ts, r.observer_pubkey, r.packet_hash, r.snr_x4, r.rssi,
                   r.packet_type, r.route, r.payload_len, r.raw_hex,
                   r.preset_uncertain, r.path_length, r.payload_type, r.is_valid,
                   o.iata, o.name, p.freq_mhz, p.bw_khz, p.sf, p.cr
            FROM raw_packets r
            JOIN observers o ON o.pubkey = r.observer_pubkey
            LEFT JOIN presets p ON p.preset_id = r.preset_id
            WHERE {bounds}
            ORDER BY r.ts""",
        params,
    ).fetchall()

    packets: list[AttributedPacket] = []
    for row in rows:
        preset = None
        if None not in (row["freq_mhz"], row["bw_khz"], row["sf"], row["cr"]):
            preset = PresetKey(
                row["freq_mhz"], row["bw_khz"], row["sf"], row["cr"]
            )
        decoded = None
        if row["payload_type"] is not None or row["path_length"] is not None:
            decoded = DecodedInfo(
                path_length=row["path_length"],
                payload_type=row["payload_type"],
                is_valid=None if row["is_valid"] is None else bool(row["is_valid"]),
            )
        packets.append(
            AttributedPacket(
                packet=Packet(
                    ts=row["ts"],
                    observer_pubkey=row["observer_pubkey"],
                    iata=row["iata"] or "",
                    snr_x4=row["snr_x4"],
                    rssi=row["rssi"],
                    packet_type=row["packet_type"],
                    route=row["route"],
                    payload_len=row["payload_len"],
                    raw_hex=row["raw_hex"],
                    origin=row["name"],
                    packet_hash=row["packet_hash"],
                ),
                attribution=Attribution(
                    preset=preset, uncertain=bool(row["preset_uncertain"])
                ),
                decoded=decoded,
            )
        )
    return packets
