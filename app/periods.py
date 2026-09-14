"""Comparación entre períodos: la red entera en un canal contra la red en otro.

Esto es un modo distinto del que vive en ``query.py``, y las reglas son distintas a
propósito:

* En el modo **simultáneo** se exige que las medidas se solapen en el tiempo, porque
  comparar dos momentos distintos arrastraría la propagación.
* Aquí el no solapamiento **es el diseño**: se trata de comparar una semana con otra.
  Lo que sustituye a esa regla es el **control**: un segundo período en el mismo canal
  que el primero, que mide cuánta diferencia es simple ruido temporal.

Sin control, la comparación es sugerente pero no concluyente, y el resultado lo dice.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from app.presets import channel_id, channel_label
from app.stats import median

SECONDS_PER_MINUTE = 60
# Un hueco mayor que esto rompe el régimen: no sabemos qué pasó en medio.
GAP_TOLERANCE_S = 600

METRICS_OBSERVER: tuple[tuple[str, str], ...] = (
    ("Ruido de fondo", "dbm"),
    ("Ocupación", "pct"),
    ("Errores/h", "raw"),
    ("Recepciones", "num"),
)
METRICS_PAIR: tuple[tuple[str, str], ...] = (
    ("PDR", "pct"),
    ("Observaciones", "num"),
)


@dataclass(frozen=True, slots=True)
class ChannelRegime:
    """Un tramo de tiempo con un canal dominante estable."""

    channel_id: str
    channel_label: str
    freq_mhz: float
    bw_khz: float
    sf: int
    starts_at: int
    ends_at: int
    minutes: int
    observers: int
    dominant_share: float

    @property
    def clean(self) -> bool:
        """Fracción de receptores en el canal dominante.

        Un régimen con el 60 % está en transición: la mitad de la red ya se movió.
        """
        return self.dominant_share >= 0.8


@dataclass(frozen=True, slots=True)
class PeriodMeasurement:
    label: str
    starts_at: int
    ends_at: int
    minutes: int
    value: float | None


@dataclass(frozen=True, slots=True)
class SubjectDelta:
    subject: str
    subtitle: str | None
    kind: str
    metric: str
    unit: str
    a: PeriodMeasurement
    b: PeriodMeasurement
    control: PeriodMeasurement | None = None

    @property
    def delta(self) -> float | None:
        return _difference(self.b.value, self.a.value)

    @property
    def control_delta(self) -> float | None:
        if self.control is None:
            return None
        return _difference(self.control.value, self.a.value)

    @property
    def exceeds_control(self) -> bool | None:
        """¿La diferencia entre canales supera la que hay entre dos períodos del mismo?

        Es la única pregunta que convierte esto en una conclusión. ``None`` cuando no
        hay control y por tanto no se puede saber.
        """
        delta = self.delta
        control = self.control_delta
        if delta is None or control is None:
            return None
        return abs(delta) > abs(control)


@dataclass(frozen=True, slots=True)
class ExcludedSubject:
    subject: str
    reason: str


@dataclass(frozen=True, slots=True)
class MetricComparison:
    """Una métrica concreta, resumida sobre todos los sujetos comunes.

    Va **por métrica** y no en un solo número global: promediar dBm con porcentajes y
    con contadores daría una cifra que no significa nada.
    """

    metric: str
    unit: str
    subjects: int
    median_delta: float | None
    median_control_delta: float | None

    @property
    def signal(self) -> bool | None:
        """¿La diferencia entre canales supera el ruido temporal?

        ``None`` sin control: no se puede afirmar nada, y decir lo contrario sería
        vender una conclusión que los datos no sostienen.
        """
        if self.median_delta is None or self.median_control_delta is None:
            return None
        return self.median_delta > self.median_control_delta


@dataclass(frozen=True, slots=True)
class PeriodComparison:
    a_label: str
    b_label: str
    control_label: str | None
    deltas: tuple[SubjectDelta, ...]
    metrics: tuple[MetricComparison, ...]
    excluded: tuple[ExcludedSubject, ...]
    comparable_subjects: int

    @property
    def has_control(self) -> bool:
        return self.control_label is not None


def channel_regimes(
    conn: sqlite3.Connection,
    since_ts: int = 0,
    until_ts: int | None = None,
    min_minutes: int = 180,
    min_share: float = 0.6,
) -> list[ChannelRegime]:
    """Segmenta el tiempo en tramos con un canal dominante.

    Se mira en qué canal estaba cada receptor **configurado** (``observer_minute``), no
    quién oía algo (``channel_minute``): confundirlos sesgaría hacia el canal con más
    tráfico.
    """
    bounds, params = _bounds(since_ts, until_ts)
    rows = conn.execute(
        f"""SELECT om.minute_ts, p.freq_mhz, p.bw_khz, p.sf, COUNT(*) AS receivers
            FROM observer_minute om
            JOIN presets p ON p.preset_id = om.preset_id
            WHERE {bounds}
            GROUP BY om.minute_ts, p.freq_mhz, p.bw_khz, p.sf
            ORDER BY om.minute_ts""",
        params,
    ).fetchall()

    per_minute: dict[int, list[tuple[tuple[float, float, int], int]]] = defaultdict(list)
    for row in rows:
        per_minute[row["minute_ts"]].append(
            ((row["freq_mhz"], row["bw_khz"], row["sf"]), row["receivers"])
        )

    regimes: list[ChannelRegime] = []
    current: list[tuple[int, tuple[float, float, int], float, int]] = []

    for minute_ts in sorted(per_minute):
        entries = per_minute[minute_ts]
        total = sum(count for _, count in entries)
        channel, count = max(entries, key=lambda entry: entry[1])
        share = count / total if total else 0.0

        contiguous = current and minute_ts - current[-1][0] <= GAP_TOLERANCE_S
        same_channel = contiguous and current[-1][1] == channel

        if not same_channel or share < min_share:
            _flush_regime(regimes, current, min_minutes)
            current = []

        if share >= min_share:
            current.append((minute_ts, channel, share, count))

    _flush_regime(regimes, current, min_minutes)
    return regimes


def _flush_regime(
    regimes: list[ChannelRegime],
    current: list[tuple[int, tuple[float, float, int], float, int]],
    min_minutes: int,
) -> None:
    if len(current) < min_minutes:
        return
    freq, bw, sf = current[0][1]
    regimes.append(
        ChannelRegime(
            channel_id=channel_id(freq, bw, sf),
            channel_label=channel_label((freq, bw, sf), ("freq", "bw", "sf")),
            freq_mhz=freq,
            bw_khz=bw,
            sf=sf,
            starts_at=current[0][0],
            ends_at=current[-1][0],
            minutes=len(current),
            observers=max(item[3] for item in current),
            # La fracción más baja del tramo: si en algún minuto estuvo al 62 %, el
            # régimen no fue limpio aunque el promedio sea alto.
            dominant_share=min(item[2] for item in current),
        )
    )


@dataclass(frozen=True, slots=True)
class SubjectSnapshot:
    key: str
    display: str
    subtitle: str | None
    kind: str
    minutes: int
    values: dict[str, float | None]
    units: dict[str, str]


def period_snapshots(
    conn: sqlite3.Connection, since_ts: int, until_ts: int
) -> dict[str, SubjectSnapshot]:
    """Qué se midió de cada sujeto dentro de un período."""
    snapshots = {**_observer_snapshots(conn, since_ts, until_ts)}
    snapshots.update(_pair_snapshots(conn, since_ts, until_ts))
    return snapshots


def _observer_snapshots(
    conn: sqlite3.Connection, since_ts: int, until_ts: int
) -> dict[str, SubjectSnapshot]:
    rows = conn.execute(
        """SELECT om.pubkey, o.name, o.iata,
                  COUNT(*) AS minutes, AVG(om.noise_floor) AS noise,
                  AVG(om.chan_util_pct) AS util, AVG(om.err_per_h) AS err,
                  SUM(om.pkts_rx) AS pkts_rx
           FROM observer_minute om
           JOIN observers o ON o.pubkey = om.pubkey
           WHERE om.minute_ts >= ? AND om.minute_ts < ?
           GROUP BY om.pubkey""",
        (since_ts, until_ts),
    ).fetchall()

    return {
        f"obs:{row['pubkey']}": SubjectSnapshot(
            key=f"obs:{row['pubkey']}",
            display=row["name"] or row["pubkey"][:8],
            subtitle=row["iata"],
            kind="observer",
            minutes=row["minutes"],
            values={
                "Ruido de fondo": row["noise"],
                "Ocupación": row["util"],
                "Errores/h": row["err"],
                "Recepciones": row["pkts_rx"],
            },
            units=dict(METRICS_OBSERVER),
        )
        for row in rows
    }


def _pair_snapshots(
    conn: sqlite3.Connection, since_ts: int, until_ts: int
) -> dict[str, SubjectSnapshot]:
    rows = conn.execute(
        """SELECT obs_a, obs_b, COUNT(*) AS observations, AVG(pdr_pct) AS pdr
           FROM pair_minute
           WHERE minute_ts >= ? AND minute_ts < ?
           GROUP BY obs_a, obs_b""",
        (since_ts, until_ts),
    ).fetchall()

    names = {
        row["pubkey"]: row["name"] or row["pubkey"][:8]
        for row in conn.execute("SELECT pubkey, name FROM observers").fetchall()
    }
    return {
        f"pair:{row['obs_a']}|{row['obs_b']}": SubjectSnapshot(
            key=f"pair:{row['obs_a']}|{row['obs_b']}",
            display=(
                f"{names.get(row['obs_a'], row['obs_a'][:8])} ↔ "
                f"{names.get(row['obs_b'], row['obs_b'][:8])}"
            ),
            subtitle=None,
            kind="pair",
            minutes=row["observations"],
            values={"PDR": row["pdr"], "Observaciones": row["observations"]},
            units=dict(METRICS_PAIR),
        )
        for row in rows
    }


def _disjoint(left: ChannelRegime, right: ChannelRegime) -> bool:
    return left.ends_at < right.starts_at or left.starts_at > right.ends_at


def find_control(
    regimes: list[ChannelRegime],
    period_a: ChannelRegime,
    period_b: ChannelRegime,
) -> ChannelRegime | None:
    """Otro período del **mismo canal** que A, sin solaparse con A ni con B.

    Es lo que permite estimar cuánta diferencia es simple paso del tiempo.
    """
    candidates = [
        regime
        for regime in regimes
        if regime.channel_id == period_a.channel_id
        and regime.starts_at != period_a.starts_at
        and _disjoint(regime, period_a)
        and _disjoint(regime, period_b)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda regime: regime.minutes)


def compare_periods(
    conn: sqlite3.Connection,
    period_a: ChannelRegime,
    period_b: ChannelRegime,
    control: ChannelRegime | None = None,
) -> PeriodComparison:
    """Compara dos períodos usando **solo los sujetos presentes en ambos**.

    Los que solo están en uno llegaron o se fueron: meterlos mezclaría el efecto canal
    con el efecto de quién estaba escuchando.
    """
    snapshots_a = period_snapshots(conn, period_a.starts_at, period_a.ends_at + SECONDS_PER_MINUTE)
    snapshots_b = period_snapshots(conn, period_b.starts_at, period_b.ends_at + SECONDS_PER_MINUTE)
    snapshots_c = (
        period_snapshots(
            conn, control.starts_at, control.ends_at + SECONDS_PER_MINUTE
        )
        if control
        else {}
    )

    deltas: list[SubjectDelta] = []
    excluded: list[ExcludedSubject] = []

    for key, snapshot_a in sorted(snapshots_a.items()):
        snapshot_b = snapshots_b.get(key)
        if snapshot_b is None:
            excluded.append(
                ExcludedSubject(snapshot_a.display, "no aparece en el segundo período")
            )
            continue

        snapshot_c = snapshots_c.get(key)
        for metric, unit in snapshot_a.units.items():
            deltas.append(
                SubjectDelta(
                    subject=snapshot_a.display,
                    subtitle=snapshot_a.subtitle,
                    kind=snapshot_a.kind,
                    metric=metric,
                    unit=unit,
                    a=PeriodMeasurement(
                        "A", period_a.starts_at, period_a.ends_at,
                        snapshot_a.minutes, snapshot_a.values.get(metric),
                    ),
                    b=PeriodMeasurement(
                        "B", period_b.starts_at, period_b.ends_at,
                        snapshot_b.minutes, snapshot_b.values.get(metric),
                    ),
                    control=(
                        PeriodMeasurement(
                            "control", control.starts_at, control.ends_at,
                            snapshot_c.minutes, snapshot_c.values.get(metric),
                        )
                        if control and snapshot_c
                        else None
                    ),
                )
            )

    comparable = {
        key for key in snapshots_a if key in snapshots_b
    }
    for key in sorted(snapshots_b):
        if key not in snapshots_a:
            excluded.append(
                ExcludedSubject(snapshots_b[key].display, "no estaba en el primer período")
            )

    # Cada métrica resume los MISMOS sujetos en la comparación principal y en el
    # control. Si no, las dos medianas no serían comparables entre sí.
    return PeriodComparison(
        a_label=period_a.channel_label,
        b_label=period_b.channel_label,
        control_label=control.channel_label if control else None,
        deltas=tuple(deltas),
        metrics=_metric_comparisons(deltas, has_control=control is not None),
        excluded=tuple(excluded),
        comparable_subjects=len(comparable),
    )


def _metric_comparisons(
    deltas: list[SubjectDelta], has_control: bool
) -> tuple[MetricComparison, ...]:
    grouped: dict[str, list[SubjectDelta]] = defaultdict(list)
    for delta in deltas:
        grouped[delta.metric].append(delta)

    comparisons = []
    for metric, items in grouped.items():
        usable = [
            delta
            for delta in items
            if delta.delta is not None
            and (not has_control or delta.control_delta is not None)
        ]
        comparisons.append(
            MetricComparison(
                metric=metric,
                unit=items[0].unit,
                subjects=len(usable),
                median_delta=median([abs(delta.delta) for delta in usable]),
                median_control_delta=(
                    median([abs(delta.control_delta) for delta in usable])
                    if has_control
                    else None
                ),
            )
        )
    return tuple(sorted(comparisons, key=lambda item: item.metric))


def _difference(later: float | None, earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    return later - earlier


def _bounds(since_ts: int, until_ts: int | None) -> tuple[str, list[object]]:
    clause = "om.minute_ts >= ?"
    params: list[object] = [since_ts]
    if until_ts is not None:
        clause += " AND om.minute_ts < ?"
        params.append(until_ts)
    return clause, params
