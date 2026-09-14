from __future__ import annotations

import pytest

from app.lora import (
    SF_LADDER,
    airtime_s,
    low_data_rate_optimize,
    project_sf_ladder,
    symbol_time_s,
    time_on_air_s,
)


def test_symbol_time_grows_with_sf_and_shrinks_with_bandwidth():
    assert symbol_time_s(7, 125) == pytest.approx(2**7 / 125_000)
    assert symbol_time_s(8, 125) == pytest.approx(2 * symbol_time_s(7, 125))
    assert symbol_time_s(7, 250) == pytest.approx(symbol_time_s(7, 125) / 2)


def test_low_data_rate_optimize_kicks_in_at_sixteen_milliseconds():
    # LDRO es obligatorio a partir de 16 ms por símbolo: SF11/BW125 y SF12/BW250.
    assert low_data_rate_optimize(7, 125) is False
    assert low_data_rate_optimize(10, 125) is False
    assert low_data_rate_optimize(11, 125) is True
    assert low_data_rate_optimize(12, 250) is True


def test_time_on_air_matches_the_semtech_reference_values():
    # Dos puntos de referencia del calculador de Semtech, con 8 de preámbulo,
    # cabecera explícita y CRC. Si la fórmula se toca, esto lo delata.
    assert time_on_air_s(16, 7, 125, 5) == pytest.approx(0.051456, rel=1e-4)
    assert time_on_air_s(16, 12, 125, 5) == pytest.approx(1.318912, rel=1e-4)


def test_time_on_air_is_monotonic_in_sf():
    previous = 0.0
    for sf in SF_LADDER:
        current = time_on_air_s(32, sf, 62.5, 6)
        assert current > previous
        previous = current


def test_each_sf_step_roughly_doubles_the_air():
    for sf in range(7, 12):
        ratio = time_on_air_s(32, sf + 1, 62.5, 6) / time_on_air_s(32, sf, 62.5, 6)
        # El símbolo dobla, pero el número de símbolos baja algo al subir SF.
        assert 1.7 < ratio < 2.2


def test_time_on_air_grows_with_payload():
    assert time_on_air_s(64, 7, 125, 5) > time_on_air_s(16, 7, 125, 5)


def test_time_on_air_is_inversely_proportional_to_bandwidth():
    # A SF7 el LDRO no entra en ninguno de los dos anchos, así que la relación es
    # exactamente el doble de ancho, la mitad de aire.
    assert time_on_air_s(32, 7, 125, 5) == pytest.approx(2 * time_on_air_s(32, 7, 250, 5))


def test_a_tiny_payload_still_has_a_preamble():
    # Un payload vacío no ocupa cero: el preámbulo ya cuesta aire.
    assert time_on_air_s(0, 7, 62.5, 6) > 0


def test_airtime_of_a_mix_is_the_sum_of_each_size():
    # No se modela la media: se suma el aire de cada tamaño por su número.
    assert airtime_s({20: 1, 80: 1}, 7, 62.5, 6) == pytest.approx(
        time_on_air_s(20, 7, 62.5, 6) + time_on_air_s(80, 7, 62.5, 6)
    )


def test_the_mix_is_not_the_average():
    # Tráfico bimodal: un paquete de 20 B y otro de 80 B. Su media (50 B) no existe, y
    # el aire de la mezcla no es el aire de la media.
    assert airtime_s({20: 1, 80: 1}, 7, 62.5, 6) != pytest.approx(
        time_on_air_s(50, 7, 62.5, 6)
    )


def _project(**overrides):
    params = dict(
        bw_khz=62.5,
        cr=6,
        sizes={32: 100},
        active_seconds=3600.0,
        current_sf=7,
        snr_p50_db=0.0,
    )
    params.update(overrides)
    return project_sf_ladder(**params)


def test_projection_marks_the_current_sf_as_the_baseline():
    projections = _project()

    assert [item.sf for item in projections] == list(SF_LADDER)
    current = next(item for item in projections if item.sf == 7)
    assert current.factor_vs_current == pytest.approx(1.0)
    assert current.sensitivity_db == pytest.approx(0.0)


def test_projection_trades_air_for_sensitivity():
    projections = {item.sf: item for item in _project()}

    low = projections[7]
    high = projections[11]
    # Subir de SF cuesta aire…
    assert high.factor_vs_current > low.factor_vs_current
    assert high.airtime_pct > low.airtime_pct
    assert high.seconds_per_hour > low.seconds_per_hour
    # …y gana sensibilidad, que es el otro lado del balance.
    assert high.sensitivity_db > 0


def test_sf6_is_in_the_ladder_and_is_the_fastest():
    # SF6 es un preset válido, no un caso raro: el más rápido y el menos sensible.
    assert SF_LADDER[0] == 6
    projections = {item.sf: item for item in _project()}

    assert 6 in projections
    assert projections[6].airtime_pct < projections[7].airtime_pct
    assert projections[6].sensitivity_db < 0


def test_projection_reports_airtime_as_a_share_of_the_observed_time():
    # 100 transmisiones de 32 B medidas a lo largo de una hora.
    projections = {item.sf: item for item in _project()}

    air = 100 * time_on_air_s(32, 7, 62.5, 6)
    assert projections[7].airtime_pct == pytest.approx(100.0 * air / 3600.0)
    assert projections[7].seconds_per_hour == pytest.approx(air)


def test_projection_margin_is_measured_snr_minus_the_demod_threshold():
    projections = {item.sf: item for item in _project(snr_p50_db=5.0)}

    # SF7 demodula a partir de -7,5 dB: con 5 dB medidos, el margen es 12,5 dB.
    assert projections[7].margin_db == pytest.approx(12.5)
    # SF11 baja el umbral a -17,5 dB, así que el mismo enlace cierra con más holgura.
    assert projections[11].margin_db == pytest.approx(22.5)


def test_projection_without_measurements_has_no_margin():
    projections = _project(snr_p50_db=None)

    assert all(item.margin_db is None for item in projections)


def test_projection_of_nothing_is_empty_not_zero():
    # Sin mezcla o sin tiempo no hay nada que proyectar: ceros se leerían como «este
    # canal no ocupa aire», que es mentira.
    assert _project(sizes={}) == ()
    assert _project(active_seconds=0.0) == ()
