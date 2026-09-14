"""Modelo de tiempo en el aire de LoRa y su proyección por spreading factor.

Este módulo es **puro**: no toca la base ni los agregados, y por tanto **calcula, no
mide**. El tiempo en el aire de una transmisión no se puede observar desde el broker
—solo se publica la configuración del receptor, no la del emisor—, así que todo lo que
sale de aquí es un modelo y así se etiqueta en la web.

La fórmula es la de Semtech (AN1200.13, §4.1.1.6). El caso de «cambiar de SF» mantiene
el ancho de banda y modela la escalera SF6…SF12: cada paso de SF dobla aproximadamente
el aire y baja unos 2,5 dB el umbral de demodulación.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import ceil

# La escalera de LoRa va de SF6 a SF12. SF6 es una opción legítima —la más rápida y la
# menos sensible—, no un caso raro: por eso entra en la comparación.
SF_LADDER = (6, 7, 8, 9, 10, 11, 12)

# SNR mínimo para demodular, por SF (dB). Es la referencia contra la que se lee el SNR
# medido: subir de SF baja el umbral, así que el enlace cierra con más holgura.
SF_DEMOD_SNR_DB = {
    6: -5.0,
    7: -7.5,
    8: -10.0,
    9: -12.5,
    10: -15.0,
    11: -17.5,
    12: -20.0,
}

# A partir de 16 ms por símbolo el chip necesita la optimización de baja tasa (LDRO).
LDRO_THRESHOLD_S = 0.016


def symbol_time_s(sf: int, bw_khz: float) -> float:
    return (2**sf) / (bw_khz * 1000.0)


def low_data_rate_optimize(sf: int, bw_khz: float) -> bool:
    return symbol_time_s(sf, bw_khz) >= LDRO_THRESHOLD_S


def cr_index(cr: int) -> int:
    """La CR se guarda como 5..8 (CR 4/5..4/8); la fórmula quiere 1..4."""
    return cr - 4


def time_on_air_s(
    payload_bytes: int,
    sf: int,
    bw_khz: float,
    cr: int,
    *,
    preamble_symbols: int = 8,
    crc: bool = True,
    explicit_header: bool = True,
) -> float:
    symbol = symbol_time_s(sf, bw_khz)
    preamble = (preamble_symbols + 4.25) * symbol

    de = 1 if low_data_rate_optimize(sf, bw_khz) else 0
    ih = 0 if explicit_header else 1
    crc_flag = 1 if crc else 0

    numerator = 8 * payload_bytes - 4 * sf + 28 + 16 * crc_flag - 20 * ih
    denominator = 4 * (sf - 2 * de)
    # Con payloads muy cortos el numerador sale negativo; el suelo son 8 símbolos.
    coded = ceil(numerator / denominator) * (cr_index(cr) + 4)
    payload_symbols = 8 + max(coded, 0)

    return preamble + payload_symbols * symbol


# Mezcla de tamaños observada: bytes en el aire -> número de transmisiones de ese
# tamaño. Se modela sobre ella y **no** sobre un tamaño medio, porque el tráfico es
# bimodal (control pequeño y adverts grandes) y su media no corresponde a ningún
# paquete real.
SizeMix = Mapping[int, int]


def airtime_s(sizes: SizeMix, sf: int, bw_khz: float, cr: int) -> float:
    """Segundos de aire que ocupa la mezcla entera a un SF dado.

    Suma el tiempo de aire **de cada tamaño**, no el de su media: el redondeo a
    símbolos de la fórmula hace que la media no sea exacta.
    """
    return sum(
        count * time_on_air_s(size, sf, bw_khz, cr)
        for size, count in sizes.items()
        if size > 0 and count > 0
    )


@dataclass(frozen=True, slots=True)
class AirtimeProjection:
    """Qué costaría el tráfico observado si la red emitiera con otro SF."""

    sf: int
    # Tiempo de aire de **una** transmisión, promediado sobre la mezcla real. Es el
    # número que hace tangible el coste: «un paquete tarda esto».
    mean_toa_ms: float
    # Aire que ocupa la mezcla sobre el tiempo observado, en % del canal.
    airtime_pct: float
    seconds_per_hour: float
    factor_vs_current: float
    # Mejora de sensibilidad frente al SF del canal (0 en él, positiva al subir).
    sensitivity_db: float | None
    # SNR medido menos el umbral del SF. Modelo de primer orden, no una medición.
    margin_db: float | None


def project_sf_ladder(
    *,
    bw_khz: float,
    cr: int,
    sizes: SizeMix,
    active_seconds: float,
    current_sf: int,
    snr_p50_db: float | None = None,
) -> tuple[AirtimeProjection, ...]:
    """Proyecta el aire a cada SF de la escalera, sobre la mezcla real de tamaños.

    ``active_seconds`` es el tiempo que cubren los datos (minutos medidos × 60), para
    poder expresar el aire en porcentaje del canal. Sin mezcla o sin tiempo no hay nada
    que proyectar: devuelve vacío en vez de ceros, que se leerían como «este canal no
    ocupa aire».
    """
    if not sizes or active_seconds <= 0:
        return ()

    current_air = airtime_s(sizes, current_sf, bw_khz, cr)
    transmissions = sum(count for count in sizes.values() if count > 0)
    if current_air <= 0 or transmissions <= 0:
        return ()

    ladder = tuple(sorted({*SF_LADDER, current_sf}))
    current_threshold = SF_DEMOD_SNR_DB.get(current_sf)

    return tuple(
        AirtimeProjection(
            sf=sf,
            mean_toa_ms=(
                1000.0 * airtime_s(sizes, sf, bw_khz, cr) / transmissions
            ),
            airtime_pct=100.0 * airtime_s(sizes, sf, bw_khz, cr) / active_seconds,
            seconds_per_hour=3600.0 * airtime_s(sizes, sf, bw_khz, cr) / active_seconds,
            factor_vs_current=airtime_s(sizes, sf, bw_khz, cr) / current_air,
            sensitivity_db=(
                None
                if current_threshold is None
                else current_threshold - SF_DEMOD_SNR_DB[sf]
            ),
            margin_db=(
                None if snr_p50_db is None else snr_p50_db - SF_DEMOD_SNR_DB[sf]
            ),
        )
        for sf in ladder
    )
