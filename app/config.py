from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

TOPIC_PREFIX_DEFAULT = "meshcore"


@dataclass(frozen=True, slots=True)
class Settings:
    mqtt_host: str
    mqtt_port: int
    mqtt_tls: bool
    mqtt_username: str | None
    mqtt_password: str | None
    topic_prefix: str
    iata_filter: str | None
    db_path: Path
    status_max_gap_s: int
    raw_retention_days: int
    decode_packets: bool = True
    # 0 = escritura directa (un commit por mensaje, sin buffer).
    flush_interval_s: float = 5.0
    rollup_interval_s: float = 300.0
    # A partir de aquí se considera que el colector se ha parado.
    stale_seconds: int = 1800


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or BASE_DIR / ".env")

    db_path = Path(os.getenv("CATMESH_DB", str(BASE_DIR / "data" / "catmesh.db")))

    return Settings(
        mqtt_host=os.getenv("CATMESH_MQTT_HOST", "localhost"),
        mqtt_port=int(os.getenv("CATMESH_MQTT_PORT", "1883")),
        mqtt_tls=_flag("CATMESH_MQTT_TLS", False),
        mqtt_username=os.getenv("CATMESH_MQTT_USERNAME") or None,
        mqtt_password=os.getenv("CATMESH_MQTT_PASSWORD") or None,
        topic_prefix=os.getenv("CATMESH_TOPIC_PREFIX", TOPIC_PREFIX_DEFAULT),
        iata_filter=os.getenv("CATMESH_IATA") or None,
        db_path=db_path,
        # Cadencia de /status por defecto 300 s; a partir de 2x la marca ya es dudoso.
        status_max_gap_s=int(os.getenv("CATMESH_STATUS_MAX_GAP_S", "600")),
        raw_retention_days=int(os.getenv("CATMESH_RAW_RETENTION_DAYS", "30")),
        decode_packets=_flag("CATMESH_DECODE_PACKETS", True),
        flush_interval_s=float(os.getenv("CATMESH_FLUSH_INTERVAL_S", "5.0")),
        rollup_interval_s=float(os.getenv("CATMESH_ROLLUP_INTERVAL_S", "300.0")),
        stale_seconds=int(os.getenv("CATMESH_STALE_SECONDS", "1800")),
    )


def topics_for(settings: Settings) -> tuple[str, str]:
    prefix = settings.topic_prefix.strip("/")
    iata = settings.iata_filter or "+"
    return (
        f"{prefix}/{iata}/+/packets",
        f"{prefix}/{iata}/+/status",
    )
