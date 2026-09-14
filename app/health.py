"""Salud del colector.

Un colector muerto no hace ruido: simplemente deja de llegar dato. Para un proyecto
que vale precisamente por el historial acumulado, ese silencio es el fallo más caro.
Vive en su propio módulo porque lo usan dos sitios con públicos distintos: el
``HEALTHCHECK`` de Docker (que devuelve un código de salida) y la portada de la web
(que lo enseña a una persona).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

DEFAULT_STALE_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class CollectorHealth:
    last_packet_ts: int | None
    age_seconds: int | None
    stale: bool

    @property
    def started(self) -> bool:
        return self.last_packet_ts is not None


def last_packet_ts(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(ts) AS ts FROM raw_packets").fetchone()
    return None if row is None or row["ts"] is None else int(row["ts"])


def is_stale(
    last_ts: int | None, now: int, stale_seconds: int = DEFAULT_STALE_SECONDS
) -> bool:
    """¿Lleva el colector demasiado tiempo sin escribir?

    Sin ningún paquete todavía no se considera parado: puede estar arrancando.
    """
    if last_ts is None:
        return False
    return (now - last_ts) > stale_seconds


def collector_health(
    conn: sqlite3.Connection,
    now: int,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> CollectorHealth:
    last_ts = last_packet_ts(conn)
    return CollectorHealth(
        last_packet_ts=last_ts,
        age_seconds=None if last_ts is None else now - last_ts,
        stale=is_stale(last_ts, now, stale_seconds),
    )
