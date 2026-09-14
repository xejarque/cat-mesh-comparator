"""Comprueba que el colector sigue escribiendo. Devuelve un código de salida.

Se usa como ``HEALTHCHECK`` del contenedor del colector, y también a mano:

    python scripts/check_collector.py --db /data/catmesh.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.health import DEFAULT_STALE_SECONDS, collector_health  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Comprueba que el colector escribe")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=int(os.getenv("CATMESH_STALE_SECONDS", str(DEFAULT_STALE_SECONDS))),
    )
    args = parser.parse_args()

    db_path = args.db or Path(os.getenv("CATMESH_DB", "data/catmesh.db"))
    if not db_path.exists():
        print(f"no existe la base de datos: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        health = collector_health(conn, int(time.time()), args.stale_seconds)
    finally:
        conn.close()

    if health.stale:
        minutes = (health.age_seconds or 0) // 60
        print(
            f"colector parado: {minutes} min sin escribir "
            f"(umbral {args.stale_seconds // 60} min)",
            file=sys.stderr,
        )
        return 1

    print("colector vivo" if health.started else "colector arrancando (sin paquetes aún)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
