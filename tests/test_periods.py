from __future__ import annotations

import pytest

from app.db import connect, init_db
from app.models import PresetKey
from app.periods import (
    channel_regimes,
    compare_periods,
    find_control,
    period_snapshots,
)
from app.presets import channel_id
from tests.helpers import epoch, fill_observer_minute

SLOT_4 = PresetKey(869.618, 62.5, 7, 6)
SLOT_1 = PresetKey(869.432, 62.5, 7, 6)

# Un régimen es un **canal físico**, así que su identificador no lleva la CR.
CHANNEL_4 = channel_id(869.618, 62.5, 7)
CHANNEL_1 = channel_id(869.432, 62.5, 7)


@pytest.fixture()
def conn(tmp_path):
    connection = connect(tmp_path / "periods.db")
    init_db(connection)
    yield connection
    connection.close()


def _clean_pair_of_regimes(conn, observer="OBS1", minutes=60):
    """Un receptor limpio en el slot 4 y luego en el slot 1."""
    fill_observer_minute(conn, observer, SLOT_4, "2026-09-01T00:00:00Z", minutes)
    fill_observer_minute(conn, observer, SLOT_1, "2026-09-01T02:00:00Z", minutes)
    return channel_regimes(conn, min_minutes=30)


def _metric(comparison, name):
    return next(item for item in comparison.metrics if item.metric == name)


def test_clean_regimes_are_detected_in_order(conn):
    regimes = _clean_pair_of_regimes(conn)

    assert [regime.channel_id for regime in regimes] == [CHANNEL_4, CHANNEL_1]
    assert regimes[0].minutes == 60
    assert regimes[0].dominant_share == 1.0
    assert regimes[0].channel_label == "869.618 MHz · BW62.5 · SF7"
    assert regimes[0].clean is True


def test_short_regimes_are_discarded(conn):
    fill_observer_minute(conn, "OBS1", SLOT_4, "2026-09-01T00:00:00Z", 20)

    assert channel_regimes(conn, min_minutes=30) == []


def test_a_split_network_is_not_a_clean_regime(conn):
    # Cuatro receptores, dos en cada canal: el dominante tiene el 50 %, por debajo del
    # umbral. Esto es una migración a medias, no un régimen.
    for index in range(2):
        fill_observer_minute(conn, f"A{index}", SLOT_4, "2026-09-01T00:00:00Z", 60)
        fill_observer_minute(conn, f"B{index}", SLOT_1, "2026-09-01T00:00:00Z", 60)

    assert channel_regimes(conn, min_minutes=30) == []


def test_a_majority_regime_is_kept_but_flagged_as_unclean(conn):
    # Tres de cuatro en el slot 4: pasa el umbral del 60 % pero no llega al 80 %.
    for index in range(3):
        fill_observer_minute(conn, f"A{index}", SLOT_4, "2026-09-01T00:00:00Z", 60)
    fill_observer_minute(conn, "B0", SLOT_1, "2026-09-01T00:00:00Z", 60)

    regimes = channel_regimes(conn, min_minutes=30)

    assert len(regimes) == 1
    assert regimes[0].channel_id == CHANNEL_4
    assert regimes[0].clean is False


def test_a_long_gap_breaks_the_regime(conn):
    fill_observer_minute(conn, "OBS1", SLOT_4, "2026-09-01T00:00:00Z", 40)
    fill_observer_minute(conn, "OBS1", SLOT_4, "2026-09-01T03:00:00Z", 40)

    regimes = channel_regimes(conn, min_minutes=30)

    assert len(regimes) == 2


def test_find_control_picks_another_regime_on_the_same_channel(conn):
    fill_observer_minute(conn, "OBS1", SLOT_4, "2026-09-01T00:00:00Z", 60)
    fill_observer_minute(conn, "OBS1", SLOT_1, "2026-09-01T02:00:00Z", 60)
    fill_observer_minute(conn, "OBS1", SLOT_4, "2026-09-01T04:00:00Z", 60)
    regimes = channel_regimes(conn, min_minutes=30)
    first, second, third = regimes

    control = find_control(regimes, first, second)

    assert control is not None
    assert control.starts_at == third.starts_at


def test_find_control_returns_none_when_the_channel_was_never_used_again(conn):
    regimes = _clean_pair_of_regimes(conn)

    assert find_control(regimes, regimes[0], regimes[1]) is None


def test_compare_periods_uses_only_the_common_subjects(conn):
    # OBS1 y OBS2 en los dos períodos; OBS3 solo en el segundo.
    for observer in ("OBS1", "OBS2"):
        fill_observer_minute(conn, observer, SLOT_4, "2026-09-01T00:00:00Z", 60, noise=-100.0)
        fill_observer_minute(conn, observer, SLOT_1, "2026-09-01T02:00:00Z", 60, noise=-110.0)
    fill_observer_minute(conn, "OBS3", SLOT_1, "2026-09-01T02:00:00Z", 60, noise=-108.0)

    regimes = channel_regimes(conn, min_minutes=30)
    comparison = compare_periods(conn, regimes[0], regimes[1])

    assert comparison.comparable_subjects == 2
    assert [excluded.reason for excluded in comparison.excluded] == [
        "no estaba en el primer período"
    ]
    # Dos sujetos × cuatro métricas.
    assert len(comparison.deltas) == 8
    ruido = [d for d in comparison.deltas if d.metric == "Ruido de fondo"]
    assert len(ruido) == 2
    assert all(d.delta == pytest.approx(-10.0) for d in ruido)


def test_medians_are_computed_over_the_same_subjects_when_there_is_a_control(conn):
    # Tercer período en el mismo canal que A, con el mismo ruido que A: el control
    # debe salir 0.
    for observer in ("OBS1", "OBS2"):
        fill_observer_minute(conn, observer, SLOT_4, "2026-09-01T00:00:00Z", 60, noise=-100.0)
        fill_observer_minute(conn, observer, SLOT_1, "2026-09-01T02:00:00Z", 60, noise=-110.0)
        fill_observer_minute(conn, observer, SLOT_4, "2026-09-01T04:00:00Z", 60, noise=-100.0)

    regimes = channel_regimes(conn, min_minutes=30)
    first, second, third = regimes
    comparison = compare_periods(conn, first, second, control=third)

    assert comparison.has_control is True
    noise = _metric(comparison, "Ruido de fondo")
    assert noise.subjects == 2
    assert noise.median_delta == pytest.approx(10.0)
    assert noise.median_control_delta == pytest.approx(0.0)
    assert noise.signal is True


def test_metrics_are_summarised_separately_not_blended(conn):
    # El ruido cambia 10 dB y las demás métricas no cambian. Si se mezclaran todas en
    # una sola mediana, ese 10 se diluiría entre ceros y desaparecería: cada métrica
    # va por su lado.
    for observer in ("OBS1", "OBS2"):
        fill_observer_minute(conn, observer, SLOT_4, "2026-09-01T00:00:00Z", 60, noise=-100.0)
        fill_observer_minute(conn, observer, SLOT_1, "2026-09-01T02:00:00Z", 60, noise=-110.0)

    regimes = channel_regimes(conn, min_minutes=30)
    comparison = compare_periods(conn, regimes[0], regimes[1])

    assert _metric(comparison, "Ruido de fondo").median_delta == pytest.approx(10.0)
    # Las que no cambian salen a cero, en vez de arrastrar la del ruido.
    assert _metric(comparison, "Recepciones").median_delta == pytest.approx(0.0)


def test_without_control_no_conclusion_can_be_drawn(conn):
    regimes = _clean_pair_of_regimes(conn)
    comparison = compare_periods(conn, regimes[0], regimes[1])

    assert comparison.has_control is False
    noise = _metric(comparison, "Ruido de fondo")
    assert noise.median_control_delta is None
    # Sin control no se puede afirmar nada, y decir lo contrario sería vender una
    # conclusión que los datos no sostienen.
    assert noise.signal is None


def test_subject_requires_being_in_both_periods(conn):
    fill_observer_minute(conn, "OBS1", SLOT_4, "2026-09-01T00:00:00Z", 60)
    fill_observer_minute(conn, "OBS2", SLOT_1, "2026-09-01T02:00:00Z", 60)

    regimes = channel_regimes(conn, min_minutes=30)
    comparison = compare_periods(conn, regimes[0], regimes[1])

    assert comparison.comparable_subjects == 0
    assert comparison.deltas == ()
    assert sorted(excluded.reason for excluded in comparison.excluded) == [
        "no aparece en el segundo período",
        "no estaba en el primer período",
    ]


def test_snapshots_cover_the_whole_period(conn):
    fill_observer_minute(conn, "OBS1", SLOT_4, "2026-09-01T00:00:00Z", 30)

    snapshots = period_snapshots(
        conn,
        epoch("2026-09-01T00:00:00Z"),
        epoch("2026-09-01T00:29:00Z") + 60,
    )

    assert len(snapshots) == 1
    snapshot = next(iter(snapshots.values()))
    assert snapshot.kind == "observer"
    assert snapshot.minutes == 30
    assert snapshot.values["Ruido de fondo"] == pytest.approx(-100.0)
