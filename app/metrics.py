"""Resumen por preset y ranking de limpieza.

Se calcula solo con los rollups (no necesita los paquetes crudos), así que las
páginas de rangos largos no escanean ``raw_packets``. Los percentiles se aproximan
ponderando los p50 por minuto; para el valor exacto hay que ir al crudo.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.aggregate import ObserverMinute, PairMinute, PresetMinute
from app.models import PresetKey
from app.presets import preset_label
from app.stats import mean as _mean
from app.stats import median as _median
from app.stats import weighted_mean as _weighted_mean
from app.stats import weighted_median as _weighted_median_raw

# Pesos del índice compuesto. El ruido pesa más porque es lo que de verdad
# distingue un slot limpio de uno invadido. Se etiqueta como heurístico en la web.
CLEANLINESS_WEIGHTS = {
    "noise_floor": 0.35,
    "snr": 0.25,
    "errors": 0.25,
    "occupancy": 0.15,
}


@dataclass(frozen=True, slots=True)
class PresetSummary:
    preset: PresetKey
    label: str
    pkts: int
    pkts_per_h: float
    uniq_hashes: int
    uniq_hashes_per_h: float
    observers_peak: int
    observers_configured: int
    snr_median_db: float | None
    snr_ge0_pct: float | None
    rssi_avg: float | None
    noise_floor_dbm: float | None
    chan_util_pct: float | None
    err_per_h: float | None
    pdr_pct: float | None
    minutes: int

    @property
    def has_data(self) -> bool:
        return self.pkts > 0 or self.noise_floor_dbm is not None


def summarize(
    preset_minutes: Iterable[PresetMinute],
    observer_minutes: Iterable[ObserverMinute],
    pair_minutes: Iterable[PairMinute],
    all_presets: Iterable[PresetKey] = (),
) -> list[PresetSummary]:
    by_preset: dict[PresetKey, list[PresetMinute]] = defaultdict(list)
    for row in preset_minutes:
        by_preset[row.preset].append(row)

    configured: dict[PresetKey, set[str]] = defaultdict(set)
    noise_by_preset: dict[PresetKey, list[float]] = defaultdict(list)
    util_by_preset: dict[PresetKey, list[float]] = defaultdict(list)
    err_by_preset: dict[PresetKey, list[float]] = defaultdict(list)
    for row in observer_minutes:
        if row.preset is None:
            continue
        configured[row.preset].add(row.observer_pubkey)
        if row.noise_floor is not None:
            noise_by_preset[row.preset].append(row.noise_floor)
        if row.chan_util_pct is not None:
            util_by_preset[row.preset].append(row.chan_util_pct)
        if row.err_per_h is not None:
            err_by_preset[row.preset].append(row.err_per_h)

    # El PDR se mide por canal físico (freq, bw, sf), no por preset: dos observers que
    # solo difieren en CR oyen lo mismo y por tanto sí se pueden comparar.
    pdr_by_channel: dict[tuple[float, float, int], list[float]] = defaultdict(list)
    for row in pair_minutes:
        pdr_by_channel[(row.freq_mhz, row.bw_khz, row.sf)].append(row.pdr_pct)

    summaries: list[PresetSummary] = []
    for preset in sorted(set(by_preset) | set(configured) | set(all_presets)):
        rows = by_preset.get(preset, [])
        weights = [row.pkts for row in rows]
        pkts = sum(weights)
        minutes = len(rows)

        summaries.append(
            PresetSummary(
                preset=preset,
                label=preset_label(preset),
                pkts=pkts,
                pkts_per_h=_rate(pkts, minutes),
                uniq_hashes=sum(row.uniq_hashes for row in rows),
                uniq_hashes_per_h=_rate(sum(row.uniq_hashes for row in rows), minutes),
                observers_peak=max((row.observers for row in rows), default=0),
                observers_configured=len(configured.get(preset, set())),
                snr_median_db=_snr_median_db(
                    [row.snr_p50_x4 for row in rows], weights
                ),
                snr_ge0_pct=_weighted_mean([row.snr_ge0_pct for row in rows], weights),
                rssi_avg=_weighted_mean([row.rssi_avg for row in rows], weights),
                noise_floor_dbm=_mean(noise_by_preset.get(preset, [])),
                chan_util_pct=_mean(util_by_preset.get(preset, [])),
                err_per_h=_mean(err_by_preset.get(preset, [])),
                pdr_pct=_median(
                    pdr_by_channel.get(
                        (preset.freq_mhz, preset.bw_khz, preset.sf), []
                    )
                ),
                minutes=minutes,
            )
        )
    return summaries


def cleanliness_scores(
    summaries: Iterable[PresetSummary],
) -> dict[PresetKey, float]:
    """Índice 0-100 por preset. Solo compara entre presets con datos."""
    ranked = [row for row in summaries if row.has_data]
    if len(ranked) < 2:
        return {}

    rules = (
        ("noise_floor_dbm", False, CLEANLINESS_WEIGHTS["noise_floor"]),
        ("snr_median_db", True, CLEANLINESS_WEIGHTS["snr"]),
        ("err_per_h", False, CLEANLINESS_WEIGHTS["errors"]),
        ("chan_util_pct", False, CLEANLINESS_WEIGHTS["occupancy"]),
    )

    scores: dict[PresetKey, float] = {}
    for candidate in ranked:
        total = 0.0
        used_weight = 0.0
        for attribute, higher_is_better, weight in rules:
            values = [
                getattr(row, attribute)
                for row in ranked
                if getattr(row, attribute) is not None
            ]
            value = getattr(candidate, attribute)
            if value is None or len(values) < 2:
                continue
            best = max(values) if higher_is_better else min(values)
            worst = min(values) if higher_is_better else max(values)
            if best == worst:
                continue
            total += weight * (value - worst) / (best - worst)
            used_weight += weight
        if used_weight:
            scores[candidate.preset] = round(100.0 * total / used_weight, 1)
    return scores


def _rate(count: int, minutes: int) -> float:
    if minutes <= 0:
        return 0.0
    return count * 60.0 / minutes


def _snr_median_db(values: list[float | None], weights: list[int]) -> float | None:
    """La p50 se guarda escalada x4 como el firmware; aquí se pasa a dB."""
    raw = _weighted_median_raw(values, weights)
    return None if raw is None else raw / 4.0
