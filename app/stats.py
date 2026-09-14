"""Ayudantes estadísticos compartidos.

Estaban duplicados en ``rollup`` y ``metrics``; con tres módulos usándolos, tres
copias era una invitación a que divergieran.
"""

from __future__ import annotations


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def weighted_mean(values: list[float | None], weights: list[int]) -> float | None:
    """Combina valores ponderando por volumen.

    Se usa para agregar los p50 por minuto en bloques mayores (hora, día): es una
    **aproximación**, no un percentil exacto. Para el valor exacto hay que ir a los
    paquetes crudos.
    """
    paired = [
        (value, weight)
        for value, weight in zip(values, weights)
        if value is not None and weight > 0
    ]
    if not paired:
        return None
    total = sum(weight for _, weight in paired)
    return sum(value * weight for value, weight in paired) / total


def weighted_median(values: list[float | None], weights: list[int]) -> float | None:
    paired = sorted(
        (value, weight)
        for value, weight in zip(values, weights)
        if value is not None and weight > 0
    )
    if not paired:
        return None
    total = sum(weight for _, weight in paired)
    accumulated = 0
    for value, weight in paired:
        accumulated += weight
        if accumulated >= total / 2:
            return value
    return paired[-1][0]
