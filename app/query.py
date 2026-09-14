"""Consultas de lectura sobre los agregados, para la web.

Toda la agregación vive aquí y no en las plantillas: así lo que se ve en la web está
cubierto por tests y no puede divergir de lo que hay en la base.

El **tall dimensional** (agrupar por frecuencia, ancho o SF) sale de ``channel_minute``
y no de ``preset_minute``, y eso no es un detalle: sumar por preset contaba dos veces
cada paquete oído por receptores de CR distinta. Ver ``docs/format-findings.md`` §7.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from app.aggregate import ObserverMinute, PairMinute, PresetMinute
from app.geo import haversine_km
from app.metrics import PresetSummary, summarize
from app.models import PresetKey
from app.presets import channel_label, parse_channel_id, preset_label, slot_index
from app.stats import mean, median, weighted_mean, weighted_median

DIMENSIONS = ("freq", "bw", "sf")
DEFAULT_GROUP_BY = DIMENSIONS
# Etiqueta corta de cada dimensión, para los encabezados de tabla.
DIMENSION_LABELS = {"freq": "Frecuencia", "bw": "Ancho", "sf": "SF"}
_COLUMN_FOR = {"freq": "freq_mhz", "bw": "bw_khz", "sf": "sf"}


@dataclass(frozen=True, slots=True)
class ReceiverStats:
    """La misma métrica, pero agregada **por receptor** en vez de por paquete.

    Cada receptor aporta un punto, oiga mucho o poco. Así un nodo con malas lecturas
    mueve una posición, no la media entera de la tabla. Con él llega el rango, para
    que ese nodo se **vea** en lugar de quedar escondido dentro de un promedio.
    """

    receivers: int
    snr_median_db: float | None
    snr_low_db: float | None
    snr_high_db: float | None
    noise_median_dbm: float | None
    noise_low_dbm: float | None
    noise_high_dbm: float | None
    util_median_pct: float | None
    err_median: float | None
    pkts_median: float | None


@dataclass(frozen=True, slots=True)
class ChannelSummary:
    key: tuple[float, ...]
    label: str
    # Slot 1..N cuando el agrupamiento identifica una frecuencia; si no, ``None``.
    slot: int | None
    pkts: int
    transmissions: int
    snr_median_db: float | None
    snr_ge0_pct: float | None
    rssi_avg: float | None
    observers: int
    noise_floor_dbm: float | None
    chan_util_pct: float | None
    err_per_h: float | None
    pdr_median: float | None
    crs: tuple[int, ...]
    minutes: int
    by_receiver: ReceiverStats | None = None

    @property
    def has_data(self) -> bool:
        return self.pkts > 0 or self.noise_floor_dbm is not None


def validate_group_by(group_by: tuple[str, ...]) -> None:
    if not group_by:
        raise ValueError("hay que agrupar por al menos una dimensión")
    unknown = set(group_by) - set(DIMENSIONS)
    if unknown:
        raise ValueError(f"dimensiones no soportadas: {sorted(unknown)}")


def channel_summaries(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None = None,
    group_by: tuple[str, ...] = DEFAULT_GROUP_BY,
    per_receiver: bool = False,
    only_common: bool = False,
) -> list[ChannelSummary]:
    validate_group_by(group_by)

    grouped: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for row in _channel_rows(conn, since_ts, until_ts):
        grouped[_group_key(row, group_by)].append(row)

    rf_by_group = _rf_by_group(conn, since_ts, until_ts, group_by)
    pdr_by_group = _pdr_by_group(conn, since_ts, until_ts, group_by)
    receiver_by_group = (
        _receiver_stats(conn, since_ts, until_ts, group_by, only_common=only_common)
        if per_receiver
        else {}
    )

    summaries: list[ChannelSummary] = []
    for key, items in grouped.items():
        weights = [row["pkts"] for row in items]
        rf = rf_by_group.get(key, {})
        summaries.append(
            ChannelSummary(
                key=key,
                label=channel_label(key, group_by),
                slot=(
                    slot_index(key[group_by.index("freq")])
                    if "freq" in group_by
                    else None
                ),
                pkts=sum(weights),
                transmissions=sum(row["uniq_hashes"] for row in items),
                snr_median_db=_to_db(
                    weighted_median([row["snr_p50_x4"] for row in items], weights)
                ),
                snr_ge0_pct=weighted_mean(
                    [row["snr_ge0_pct"] for row in items], weights
                ),
                rssi_avg=weighted_mean([row["rssi_avg"] for row in items], weights),
                observers=max(row["observers"] for row in items),
                noise_floor_dbm=rf.get("noise"),
                chan_util_pct=rf.get("util"),
                err_per_h=rf.get("err"),
                pdr_median=pdr_by_group.get(key),
                crs=tuple(sorted({cr for row in items for cr in _parse_crs(row["crs"])})),
                minutes=len(items),
                by_receiver=receiver_by_group.get(key),
            )
        )
    return sorted(summaries, key=lambda row: -row.pkts)


def _receiver_stats(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None,
    group_by: tuple[str, ...],
    only_common: bool = False,
) -> dict[tuple, ReceiverStats]:
    """Un punto por receptor y grupo, y luego mediana y rango entre ellos.

    Es la respuesta a «un receptor que lee mal contamina la media»: aquí aporta una
    posición, no todo su volumen de paquetes.

    Con ``only_common`` se queda solo con los receptores presentes en **todos** los
    grupos, que convierte la comparación en apareada. Si la intersección es vacía, el
    resultado también lo es, y quien llama debe decirlo en vez de enseñar una tabla.
    """
    bounds, params = _bounds("om.minute_ts", since_ts, until_ts)
    rows = conn.execute(
        f"""SELECT om.pubkey, p.freq_mhz, p.bw_khz, p.sf,
                   AVG(om.snr_p50_x4) AS snr, AVG(om.noise_floor) AS noise,
                   AVG(om.chan_util_pct) AS util, AVG(om.err_per_h) AS err,
                   SUM(om.pkts_rx) AS pkts
            FROM observer_minute om
            JOIN presets p ON p.preset_id = om.preset_id
            WHERE {bounds}
            GROUP BY om.pubkey, p.freq_mhz, p.bw_khz, p.sf""",
        params,
    ).fetchall()

    if only_common:
        common = _common_observer_keys(conn, since_ts, until_ts, group_by)
        rows = [row for row in rows if row["pubkey"] in common]

    grouped: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[_group_key(row, group_by)].append(row)

    stats: dict[tuple, ReceiverStats] = {}
    for key, items in grouped.items():
        snr = [_to_db(row["snr"]) for row in items if row["snr"] is not None]
        noise = [row["noise"] for row in items if row["noise"] is not None]
        util = [row["util"] for row in items if row["util"] is not None]
        err = [row["err"] for row in items if row["err"] is not None]
        pkts = [row["pkts"] for row in items if row["pkts"] is not None]
        stats[key] = ReceiverStats(
            receivers=len(items),
            snr_median_db=median(snr),
            snr_low_db=min(snr) if snr else None,
            snr_high_db=max(snr) if snr else None,
            noise_median_dbm=median(noise),
            noise_low_dbm=min(noise) if noise else None,
            noise_high_dbm=max(noise) if noise else None,
            util_median_pct=median(util),
            err_median=median(err),
            pkts_median=median(pkts),
        )
    return stats


def _observer_keys_by_group(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None,
    group_by: tuple[str, ...],
) -> dict[tuple, set[str]]:
    bounds, params = _bounds("om.minute_ts", since_ts, until_ts)
    rows = conn.execute(
        f"""SELECT DISTINCT om.pubkey, p.freq_mhz, p.bw_khz, p.sf
            FROM observer_minute om
            JOIN presets p ON p.preset_id = om.preset_id
            WHERE {bounds}""",
        params,
    ).fetchall()

    grouped: dict[tuple, set[str]] = defaultdict(set)
    for row in rows:
        grouped[_group_key(row, group_by)].add(row["pubkey"])
    return grouped


def _common_observer_keys(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None,
    group_by: tuple[str, ...],
) -> set[str]:
    grouped = _observer_keys_by_group(conn, since_ts, until_ts, group_by)
    if not grouped:
        return set()
    return set.intersection(*grouped.values())


def common_observers(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None,
    group_by: tuple[str, ...],
) -> tuple[int, int]:
    """``(receptores en todos los grupos, receptores en total)``.

    El primero es cuántos se pueden comparar de verdad; el segundo, cuántos hay. Si el
    primero es cero, no existe ninguna comparación apareada y hay que decirlo.
    """
    grouped = _observer_keys_by_group(conn, since_ts, until_ts, group_by)
    if not grouped:
        return (0, 0)
    return (
        len(set.intersection(*grouped.values())),
        len(set.union(*grouped.values())),
    )


def channel_series(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None = None,
    group_by: tuple[str, ...] = DEFAULT_GROUP_BY,
) -> dict[str, list[tuple[int, int]]]:
    """Serie temporal de recepciones por grupo: ``{etiqueta: [(minute_ts, pkts)]}``."""
    validate_group_by(group_by)

    series: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for row in _channel_rows(conn, since_ts, until_ts):
        key = _group_key(row, group_by)
        series[channel_label(key, group_by)][row["minute_ts"]] += row["pkts"]

    return {
        label: sorted(points.items()) for label, points in sorted(series.items())
    }


def preset_summaries(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None = None
) -> list[PresetSummary]:
    """Vista por configuración exacta de receptor (incluye la CR)."""
    bounds, params = _bounds("minute_ts", since_ts, until_ts)

    preset_minutes = [
        PresetMinute(
            minute_ts=row["minute_ts"],
            preset=_preset(row),
            pkts=row["pkts"],
            uniq_hashes=row["uniq_hashes"],
            snr_avg_x4=row["snr_avg_x4"],
            snr_p50_x4=row["snr_p50_x4"],
            snr_ge0_pct=row["snr_ge0_pct"],
            rssi_avg=row["rssi_avg"],
            observers=row["observers"],
        )
        for row in conn.execute(
            f"""SELECT pm.*, p.freq_mhz, p.bw_khz, p.sf, p.cr
                FROM preset_minute pm JOIN presets p ON p.preset_id = pm.preset_id
                WHERE {bounds}""",
            params,
        ).fetchall()
    ]

    observer_minutes = [
        ObserverMinute(
            observer_pubkey=row["pubkey"],
            minute_ts=row["minute_ts"],
            preset=None if row["freq_mhz"] is None else _preset(row),
            noise_floor=row["noise_floor"],
            chan_util_pct=row["chan_util_pct"],
            err_per_h=row["err_per_h"],
            pkts_rx=row["pkts_rx"],
        )
        for row in conn.execute(
            f"""SELECT om.*, p.freq_mhz, p.bw_khz, p.sf, p.cr
                FROM observer_minute om
                LEFT JOIN presets p ON p.preset_id = om.preset_id
                WHERE {_bounds("om.minute_ts", since_ts, until_ts)[0]}""",
            params,
        ).fetchall()
    ]

    pair_minutes = []
    for row in conn.execute(
        f"SELECT * FROM pair_minute WHERE {bounds}", params
    ).fetchall():
        freq, bw, sf = parse_channel_id(row["channel_id"])
        pair_minutes.append(
            PairMinute(
                minute_ts=row["minute_ts"],
                freq_mhz=freq,
                bw_khz=bw,
                sf=sf,
                obs_a=row["obs_a"],
                obs_b=row["obs_b"],
                heard_a=row["heard_a"],
                heard_b=row["heard_b"],
                both=row["both"],
                pdr_pct=row["pdr_pct"],
            )
        )

    return summarize(preset_minutes, observer_minutes, pair_minutes)


@dataclass(frozen=True, slots=True)
class ObserverRow:
    pubkey: str
    short: str
    name: str | None
    iata: str | None
    model: str | None
    fw: str | None
    preset_label: str | None
    cr: int | None
    freq_mhz: float | None
    sf: int | None
    last_seen: int
    noise_floor: float | None
    chan_util_pct: float | None
    err_per_h: float | None
    pkts_rx: int


def observer_rows(conn: sqlite3.Connection) -> list[ObserverRow]:
    """Último estado conocido de cada observer, con su configuración."""
    rows = conn.execute(
        """SELECT o.pubkey, o.name, o.iata, o.model, o.fw, o.last_seen,
                  s.noise_floor, s.chan_util_pct, s.err_per_h, s.pkts_rx,
                  p.freq_mhz, p.bw_khz, p.sf, p.cr
           FROM observers o
           LEFT JOIN (
               SELECT pubkey, MAX(minute_ts) AS minute_ts FROM observer_minute
               GROUP BY pubkey) latest
               ON latest.pubkey = o.pubkey
           LEFT JOIN observer_minute s
               ON s.pubkey = latest.pubkey AND s.minute_ts = latest.minute_ts
           LEFT JOIN presets p ON p.preset_id = s.preset_id
           ORDER BY o.last_seen DESC"""
    ).fetchall()

    observers: list[ObserverRow] = []
    for row in rows:
        preset = None
        if None not in (row["freq_mhz"], row["bw_khz"], row["sf"], row["cr"]):
            preset = _preset(row)
        observers.append(
            ObserverRow(
                pubkey=row["pubkey"],
                short=row["pubkey"][:8],
                name=row["name"],
                iata=row["iata"],
                model=row["model"],
                fw=row["fw"],
                preset_label=preset_label(preset) if preset else None,
                cr=row["cr"],
                freq_mhz=row["freq_mhz"],
                sf=row["sf"],
                last_seen=row["last_seen"],
                noise_floor=row["noise_floor"],
                chan_util_pct=row["chan_util_pct"],
                err_per_h=row["err_per_h"],
                pkts_rx=row["pkts_rx"] or 0,
            )
        )
    return observers


@dataclass(frozen=True, slots=True)
class LivePacket:
    """Un paquete recién oído, sin agregar. Para el feed en vivo."""

    id: int
    ts: int
    observer: str
    iata: str | None
    freq_mhz: float | None
    sf: int | None
    cr: int | None
    snr_db: float | None
    rssi: int | None
    payload_type: str | None
    path_length: int | None
    packet_hash: str | None

    @property
    def channel(self) -> str:
        if self.freq_mhz is None:
            return "sin atribuir"
        parts = [f"{self.freq_mhz:.3f} MHz"]
        if self.sf is not None:
            parts.append(f"SF{self.sf}")
        if self.cr is not None:
            parts.append(f"CR{self.cr}")
        return " · ".join(parts)


def recent_packets(
    conn: sqlite3.Connection, after_id: int = 0, limit: int = 50
) -> list[LivePacket]:
    """Paquetes crudos para el feed en vivo.

    Sin ``after_id`` devuelve los últimos ``limit``; con él, solo los posteriores.
    Siempre en orden ascendente de ``id``, para poder añadir por abajo sin recolocar.
    """
    limit = max(1, min(limit, 200))
    columns = """r.id, r.ts, r.snr_x4, r.rssi, r.packet_type, r.packet_hash,
                 r.path_length, r.payload_type,
                 o.name, o.iata, p.freq_mhz, p.sf, p.cr, r.observer_pubkey"""
    source = """FROM raw_packets r
                JOIN observers o ON o.pubkey = r.observer_pubkey
                LEFT JOIN presets p ON p.preset_id = r.preset_id"""

    if after_id:
        rows = conn.execute(
            f"SELECT {columns} {source} WHERE r.id > ? ORDER BY r.id LIMIT ?",
            (after_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT * FROM (
                    SELECT {columns} {source} ORDER BY r.id DESC LIMIT ?
                ) ORDER BY id""",
            (limit,),
        ).fetchall()

    return [
        LivePacket(
            id=row["id"],
            ts=row["ts"],
            observer=row["name"] or row["observer_pubkey"][:8],
            iata=row["iata"],
            freq_mhz=row["freq_mhz"],
            sf=row["sf"],
            cr=row["cr"],
            snr_db=None if row["snr_x4"] is None else row["snr_x4"] / 4.0,
            rssi=row["rssi"],
            payload_type=row["payload_type"],
            path_length=row["path_length"],
            packet_hash=row["packet_hash"],
        )
        for row in rows
    ]


def latest_packet_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM raw_packets").fetchone()
    return int(row["id"])


@dataclass(frozen=True, slots=True)
class Metric:
    label: str
    value: float | None
    unit: str  # "dbm" | "pct" | "num" | "raw"


@dataclass(frozen=True, slots=True)
class PairedMeasurement:
    channel_label: str
    freq_mhz: float
    bw_khz: float
    sf: int
    first_ts: int
    last_ts: int
    metrics: tuple[Metric, ...]


@dataclass(frozen=True, slots=True)
class PairedSubject:
    """Un sujeto medido en varios canales: lo único que permite comparar de verdad.

    Un observador (o una pareja, o una zona) que ha estado en dos canales aporta una
    comparación **apareada**: misma antena, misma ubicación, mismo hardware. Comparar
    canales medidos por receptores distintos mezcla geografía y equipo, y entonces no
    se sabe qué causó la diferencia.
    """

    subject: str
    subtitle: str | None
    measurements: tuple[PairedMeasurement, ...]
    overlap_seconds: int
    # Distancia entre los dos receptores, cuando se conoce. Un PDR sin distancia no se
    # puede interpretar: 79 % a 1 km es malo, a 40 km es excelente.
    distance_km: float | None = None

    @property
    def comparable(self) -> bool:
        # Sin solapamiento temporal, la diferencia entre canales puede ser propagación
        # y no canal: la comparación no vale.
        return len(self.measurements) >= 2 and self.overlap_seconds > 0

    @property
    def only_sf_changes(self) -> bool:
        """Misma frecuencia y ancho, distinto SF.

        Es el caso más traicionero: parece una comparación de canales, pero un
        receptor SF7 **no puede oír** una transmisión SF8. Cada configuración ve a un
        conjunto distinto de emisores, así que las métricas describen poblaciones
        distintas, no el mismo enlace en dos condiciones.
        """
        return (
            len({(item.freq_mhz, item.bw_khz) for item in self.measurements}) == 1
            and len({item.sf for item in self.measurements}) > 1
        )


def _shared_overlap(measurements: list[PairedMeasurement]) -> int:
    first = max(item.first_ts for item in measurements)
    last = min(item.last_ts for item in measurements)
    return max(0, last - first)


def _subjects(
    grouped: dict[str, list[PairedMeasurement]],
    displays: dict[str, str],
    subtitles: dict[str, str | None],
    distances: dict[str, float] | None = None,
) -> list[PairedSubject]:
    """Agrupa por una clave estable, no por el nombre: dos receptores pueden llamarse
    igual y sus medidas no se deben mezclar."""
    subjects = []
    for key, measurements in grouped.items():
        merged = _merge_same_channel(measurements)
        if len(merged) < 2:
            continue
        subjects.append(
            PairedSubject(
                subject=displays.get(key, key),
                subtitle=subtitles.get(key),
                measurements=tuple(merged),
                overlap_seconds=_shared_overlap(merged),
                distance_km=(distances or {}).get(key),
            )
        )
    return sorted(subjects, key=lambda item: (not item.comparable, item.subject))


def node_positions(conn: sqlite3.Connection) -> dict[str, tuple[float, float]]:
    """Posición conocida de cada nodo, según sus propios adverts."""
    return {
        row["pubkey"]: (row["lat"], row["lon"])
        for row in conn.execute(
            "SELECT pubkey, lat, lon FROM node_positions"
        ).fetchall()
    }


def pair_distance_km(
    conn: sqlite3.Connection, obs_a: str, obs_b: str
) -> float | None:
    positions = node_positions(conn)
    first = positions.get(obs_a)
    second = positions.get(obs_b)
    if not first or not second:
        return None
    return haversine_km(first[0], first[1], second[0], second[1])


def _merge_same_channel(measurements: list[PairedMeasurement]) -> list[PairedMeasurement]:
    grouped: dict[str, list[PairedMeasurement]] = defaultdict(list)
    for item in measurements:
        grouped[item.channel_label].append(item)

    merged = []
    for label, items in grouped.items():
        merged.append(
            PairedMeasurement(
                channel_label=label,
                freq_mhz=items[0].freq_mhz,
                bw_khz=items[0].bw_khz,
                sf=items[0].sf,
                first_ts=min(item.first_ts for item in items),
                last_ts=max(item.last_ts for item in items),
                metrics=items[0].metrics,
            )
        )
    return sorted(merged, key=lambda item: item.channel_label)


def _measurement(
    freq: float, bw: float, sf: int, first_ts: int, last_ts: int, metrics: tuple[Metric, ...]
) -> PairedMeasurement:
    return PairedMeasurement(
        channel_label=channel_label((freq, bw, sf)),
        freq_mhz=freq,
        bw_khz=bw,
        sf=sf,
        first_ts=first_ts,
        last_ts=last_ts,
        metrics=metrics,
    )


def paired_observers(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None = None
) -> list[PairedSubject]:
    """Mismo receptor en varios canales. La comparación más limpia que existe."""
    bounds, params = _bounds("om.minute_ts", since_ts, until_ts)
    rows = conn.execute(
        f"""SELECT om.pubkey, o.name, o.iata, om.preset_id,
                   p.freq_mhz, p.bw_khz, p.sf,
                   COUNT(*) AS minutes,
                   MIN(om.minute_ts) AS first_ts, MAX(om.minute_ts) AS last_ts,
                   AVG(om.noise_floor) AS noise, AVG(om.chan_util_pct) AS util,
                   AVG(om.err_per_h) AS err, SUM(om.pkts_rx) AS pkts_rx
            FROM observer_minute om
            JOIN observers o ON o.pubkey = om.pubkey
            JOIN presets p ON p.preset_id = om.preset_id
            WHERE {bounds}
            GROUP BY om.pubkey, p.freq_mhz, p.bw_khz, p.sf""",
        params,
    ).fetchall()

    grouped: dict[str, list[PairedMeasurement]] = defaultdict(list)
    displays: dict[str, str] = {}
    subtitles: dict[str, str | None] = {}
    for row in rows:
        displays[row["pubkey"]] = row["name"] or row["pubkey"][:8]
        subtitles[row["pubkey"]] = row["iata"]
        grouped[row["pubkey"]].append(
            _measurement(
                row["freq_mhz"],
                row["bw_khz"],
                row["sf"],
                row["first_ts"],
                row["last_ts"],
                (
                    Metric("Ruido de fondo", row["noise"], "dbm"),
                    Metric("Ocupación", row["util"], "pct"),
                    Metric("Errores/h", row["err"], "raw"),
                    Metric("Recepciones", row["pkts_rx"], "num"),
                    Metric("Minutos medidos", row["minutes"], "num"),
                ),
            )
        )
    return _subjects(grouped, displays, subtitles)


def paired_pairs(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None = None
) -> list[PairedSubject]:
    """Misma pareja de receptores en varios canales: el PDR sí es comparable."""
    bounds, params = _bounds("minute_ts", since_ts, until_ts)
    rows = conn.execute(
        f"""SELECT channel_id, obs_a, obs_b,
                   COUNT(*) AS observations, AVG(pdr_pct) AS pdr,
                   MIN(minute_ts) AS first_ts, MAX(minute_ts) AS last_ts
            FROM pair_minute WHERE {bounds}
            GROUP BY obs_a, obs_b, channel_id""",
        params,
    ).fetchall()

    names = _observer_names(conn)
    positions = node_positions(conn)
    grouped: dict[str, list[PairedMeasurement]] = defaultdict(list)
    displays: dict[str, str] = {}
    distances: dict[str, float] = {}
    for row in rows:
        label_a = names.get(row["obs_a"], row["obs_a"][:8])
        label_b = names.get(row["obs_b"], row["obs_b"][:8])
        key = f"{row['obs_a']}|{row['obs_b']}"
        displays[key] = f"{label_a} ↔ {label_b}"
        first, second = positions.get(row["obs_a"]), positions.get(row["obs_b"])
        if first and second:
            distances[key] = haversine_km(first[0], first[1], second[0], second[1])
        freq, bw, sf = parse_channel_id(row["channel_id"])
        grouped[key].append(
            _measurement(
                freq,
                bw,
                sf,
                row["first_ts"],
                row["last_ts"],
                (
                    Metric("PDR", row["pdr"], "pct"),
                    Metric("Observaciones", row["observations"], "num"),
                ),
            )
        )
    return _subjects(grouped, displays, {}, distances)


def paired_regions(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None = None
) -> list[PairedSubject]:
    """Misma comarca en varios canales.

    Es la comparación más débil de las tres: comparte geografía, pero **no** los
    receptores, así que siguen cambiando el equipo y la antena. Se ofrece solo para
    cuando no hay nada mejor, y la web lo dice.
    """
    bounds, params = _bounds("om.minute_ts", since_ts, until_ts)
    rows = conn.execute(
        f"""SELECT o.iata, p.freq_mhz, p.bw_khz, p.sf,
                   COUNT(DISTINCT om.pubkey) AS observers,
                   MIN(om.minute_ts) AS first_ts, MAX(om.minute_ts) AS last_ts,
                   AVG(om.noise_floor) AS noise, SUM(om.pkts_rx) AS pkts_rx
            FROM observer_minute om
            JOIN observers o ON o.pubkey = om.pubkey
            JOIN presets p ON p.preset_id = om.preset_id
            WHERE {bounds} AND o.iata IS NOT NULL
            GROUP BY o.iata, p.freq_mhz, p.bw_khz, p.sf""",
        params,
    ).fetchall()

    grouped: dict[str, list[PairedMeasurement]] = defaultdict(list)
    displays: dict[str, str] = {}
    for row in rows:
        displays[row["iata"]] = row["iata"]
        grouped[row["iata"]].append(
            _measurement(
                row["freq_mhz"],
                row["bw_khz"],
                row["sf"],
                row["first_ts"],
                row["last_ts"],
                (
                    Metric("Ruido de fondo", row["noise"], "dbm"),
                    Metric("Observadores", row["observers"], "num"),
                    Metric("Recepciones", row["pkts_rx"], "num"),
                ),
            )
        )
    return _subjects(grouped, displays, {})


def _observer_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["pubkey"]: row["name"] or row["pubkey"][:8]
        for row in conn.execute("SELECT pubkey, name FROM observers").fetchall()
    }


def channel_catalog(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Canales físicos vistos y quién los escucha. Para la página de campaña."""
    rows = conn.execute(
        """SELECT p.freq_mhz, p.bw_khz, p.sf, p.cr, COUNT(DISTINCT o.pubkey) AS observers,
                  MAX(o.last_seen) AS last_seen
           FROM presets p
           LEFT JOIN observer_status s ON s.preset_id = p.preset_id
           LEFT JOIN observers o ON o.pubkey = s.pubkey
           GROUP BY p.freq_mhz, p.bw_khz, p.sf, p.cr
           ORDER BY observers DESC, p.freq_mhz"""
    ).fetchall()
    return [dict(row) for row in rows]


def quality_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM data_quality ORDER BY day_ts DESC LIMIT 30"
        ).fetchall()
    ]


def window_bounds(preset: str, now: int) -> tuple[int, int | None]:
    """Traduce el selector de ventana de la UI a un rango de tiempo."""
    hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}
    if preset not in hours:
        raise ValueError(f"ventana no soportada: {preset}")
    return now - hours[preset] * 3600, None


def _channel_rows(
    conn: sqlite3.Connection, since_ts: int, until_ts: int | None
) -> list[sqlite3.Row]:
    bounds, params = _bounds("minute_ts", since_ts, until_ts)
    return conn.execute(
        f"SELECT * FROM channel_minute WHERE {bounds}", params
    ).fetchall()


def _rf_by_group(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None,
    group_by: tuple[str, ...],
) -> dict[tuple, dict[str, float | None]]:
    """Ruido, ocupación y errores vienen del observer, no del paquete.

    Cuidado al agrupar sin la frecuencia o sin el ancho: el piso de ruido depende de
    ambos, así que agrupar solo por SF mezcla canales distintos. La web lo advierte.
    """
    bounds, params = _bounds("om.minute_ts", since_ts, until_ts)
    rows = conn.execute(
        f"""SELECT om.noise_floor, om.chan_util_pct, om.err_per_h,
                   p.freq_mhz, p.bw_khz, p.sf
            FROM observer_minute om
            JOIN presets p ON p.preset_id = om.preset_id
            WHERE {bounds}""",
        params,
    ).fetchall()

    grouped: dict[tuple, dict[str, list[float]]] = defaultdict(
        lambda: {"noise": [], "util": [], "err": []}
    )
    for row in rows:
        key = _group_key(row, group_by)
        for field, column in (
            ("noise", "noise_floor"),
            ("util", "chan_util_pct"),
            ("err", "err_per_h"),
        ):
            if row[column] is not None:
                grouped[key][field].append(row[column])

    return {
        key: {field: mean(values) for field, values in fields.items()}
        for key, fields in grouped.items()
    }


def _pdr_by_group(
    conn: sqlite3.Connection,
    since_ts: int,
    until_ts: int | None,
    group_by: tuple[str, ...],
) -> dict[tuple, float | None]:
    bounds, params = _bounds("minute_ts", since_ts, until_ts)
    grouped: dict[tuple, list[float]] = defaultdict(list)
    for row in conn.execute(
        f"SELECT channel_id, pdr_pct FROM pair_minute WHERE {bounds}", params
    ).fetchall():
        freq, bw, sf = parse_channel_id(row["channel_id"])
        key = _group_key({"freq_mhz": freq, "bw_khz": bw, "sf": sf}, group_by)
        grouped[key].append(row["pdr_pct"])
    return {key: median(values) for key, values in grouped.items()}


def _group_key(row: object, group_by: tuple[str, ...]) -> tuple:
    return tuple(row[_COLUMN_FOR[name]] for name in group_by)


def _preset(row: sqlite3.Row) -> PresetKey:
    return PresetKey(row["freq_mhz"], row["bw_khz"], row["sf"], row["cr"])


def _to_db(value: float | None) -> float | None:
    return None if value is None else value / 4.0


def _parse_crs(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split(",") if part]


def _bounds(
    column: str, since_ts: int, until_ts: int | None
) -> tuple[str, list[object]]:
    clause = f"{column} >= ?"
    params: list[object] = [since_ts]
    if until_ts is not None:
        clause += f" AND {column} < ?"
        params.append(until_ts)
    return clause, params
