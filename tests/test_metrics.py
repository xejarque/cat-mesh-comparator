from __future__ import annotations

import pytest

from app.aggregate import ObserverMinute, PresetMinute
from app.metrics import cleanliness_scores, summarize
from app.models import PresetKey

CLEAN = PresetKey(869.431, 62.5, 11, 5)
DIRTY = PresetKey(869.619, 62.5, 11, 5)
EMPTY = PresetKey(869.600, 62.5, 11, 5)


def _preset_minute(
    preset: PresetKey,
    minute_ts: int,
    pkts: int,
    snr_p50_x4: float,
    ge0: float,
    rssi: float,
    observers: int = 2,
) -> PresetMinute:
    return PresetMinute(
        minute_ts=minute_ts,
        preset=preset,
        pkts=pkts,
        uniq_hashes=pkts,
        snr_avg_x4=snr_p50_x4,
        snr_p50_x4=snr_p50_x4,
        snr_ge0_pct=ge0,
        rssi_avg=rssi,
        observers=observers,
    )


def _observer_minute(
    pubkey: str,
    minute_ts: int,
    preset: PresetKey,
    noise: float,
    util: float,
    err: float,
) -> ObserverMinute:
    return ObserverMinute(
        observer_pubkey=pubkey,
        minute_ts=minute_ts,
        preset=preset,
        noise_floor=noise,
        chan_util_pct=util,
        err_per_h=err,
        pkts_rx=10,
    )


def _population() -> list:
    return summarize(
        [
            _preset_minute(CLEAN, 60000, 100, 40, 90.0, -70.0),
            _preset_minute(DIRTY, 60000, 100, -20, 40.0, -90.0),
        ],
        [
            _observer_minute("OBS1", 60000, CLEAN, -118.0, 3.0, 1.0),
            _observer_minute("OBS2", 60000, DIRTY, -100.0, 25.0, 40.0),
        ],
        [],
    )


def test_summarize_computes_rates_and_converts_snr():
    summaries = summarize(
        [_preset_minute(CLEAN, 60000, 100, 40, 90.0, -70.0)],
        [_observer_minute("OBS1", 60000, CLEAN, -118.0, 3.0, 1.0)],
        [],
    )

    assert len(summaries) == 1
    row = summaries[0]
    assert row.pkts == 100
    assert row.pkts_per_h == pytest.approx(6000.0)
    assert row.snr_median_db == pytest.approx(10.0)
    assert row.noise_floor_dbm == pytest.approx(-118.0)
    assert row.observers_configured == 1
    assert row.has_data is True


def test_summarize_lists_presets_without_data():
    summaries = summarize([], [], [], all_presets=[EMPTY])

    assert len(summaries) == 1
    row = summaries[0]
    assert row.preset == EMPTY
    assert row.pkts == 0
    assert row.noise_floor_dbm is None
    assert row.has_data is False


def test_snr_median_is_weighted_by_volume():
    summaries = summarize(
        [
            _preset_minute(CLEAN, 60000, 900, 40, 100.0, -70.0),
            _preset_minute(CLEAN, 60060, 100, -40, 0.0, -90.0),
        ],
        [],
        [],
    )

    assert summaries[0].snr_median_db == pytest.approx(10.0)


def test_cleanliness_scores_rank_the_clean_preset_first():
    scores = cleanliness_scores(_population())

    assert scores[CLEAN] == pytest.approx(100.0)
    assert scores[DIRTY] == pytest.approx(0.0)


def test_cleanliness_needs_two_presets_with_data():
    summaries = summarize([_preset_minute(CLEAN, 60000, 100, 40, 90.0, -70.0)], [], [])

    assert cleanliness_scores(summaries) == {}


def test_pdr_is_attached_by_physical_channel():
    from app.aggregate import PairMinute

    summaries = summarize(
        [_preset_minute(CLEAN, 60000, 100, 40, 90.0, -70.0)],
        [],
        [
            # El par se mide por canal (freq, bw, sf), y CLEAN es 869.431/62.5/SF11.
            PairMinute(
                minute_ts=60000,
                freq_mhz=869.431,
                bw_khz=62.5,
                sf=11,
                obs_a="OBS1",
                obs_b="OBS2",
                heard_a=10,
                heard_b=10,
                both=9,
                pdr_pct=90.0,
            )
        ],
    )

    assert summaries[0].pdr_pct == pytest.approx(90.0)
    assert _population()[1].pdr_pct is None
