"""Geometría mínima para interpretar las medidas.

Existe por una razón concreta: **un PDR sin distancia no significa nada**. Un 79 %
entre dos receptores a un kilómetro es malo; a cuarenta kilómetros es excelente. Sin
la distancia, el número no se puede leer.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    """Distancia en kilómetros entre dos puntos (fórmula del semiverseno)."""
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)

    h = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))
