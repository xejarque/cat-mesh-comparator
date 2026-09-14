from __future__ import annotations

import logging

from app.models import DecodedInfo

logger = logging.getLogger(__name__)

try:  # pragma: no cover - depende del entorno
    from meshcoredecoder import MeshCoreDecoder

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    MeshCoreDecoder = None  # type: ignore[assignment]
    _AVAILABLE = False


def decoder_available() -> bool:
    return _AVAILABLE


def decode_packet(raw_hex: str | None) -> DecodedInfo | None:
    """Enriquecimiento opcional de un paquete a partir de su ``raw``.

    Contrato: **nunca lanza**. La decodificación es un extra; un fallo aquí no puede
    tumbar la ingesta ni impedir que el paquete se guarde. Devuelve ``None`` si no
    hay ``raw``, si el decodificador no está instalado o si el paquete no se entiende.
    """
    if not _AVAILABLE or not raw_hex:
        return None

    try:
        packet = MeshCoreDecoder.decode(raw_hex)
    except Exception as exc:  # noqa: BLE001 - deliberado: el decode no es crítico
        logger.debug("Paquete no decodificable (%s): %.40s", exc, raw_hex)
        return None

    return DecodedInfo(
        path_length=_optional_int(getattr(packet, "path_length", None)),
        payload_type=_enum_name(getattr(packet, "payload_type", None)),
        is_valid=_optional_bool(getattr(packet, "is_valid", None)),
        **_advert_details(packet),
    )


def _advert_details(packet: object) -> dict[str, object]:
    """Extrae emisor y posición de un advert, si el paquete lo es.

    La posición que se aprende es la del **emisor**. Un receptor oye adverts de otros,
    así que la posición de un receptor solo se conoce si él también emite y alguien lo
    oye. ``hasLocation`` es falso en los nodos sin GPS.
    """
    payload = getattr(packet, "payload", None)
    decoded = payload.get("decoded") if isinstance(payload, dict) else None
    # `app_data` llega como dict (con claves en snake_case), no como objeto: hay que
    # acceder con `_field`, no con `getattr`.
    app = _field(decoded, "app_data")
    if app is None:
        return {}

    location = _field(app, "location")
    lat = _field(location, "latitude")
    lon = _field(location, "longitude")
    pubkey = _field(decoded, "public_key")

    return {
        "sender_pubkey": _clean(pubkey),
        "sender_name": _clean(_field(app, "name")),
        "lat": _optional_float(lat),
        "lon": _optional_float(lon),
    }


def _field(container: object, name: str) -> object:
    if isinstance(container, dict):
        return container.get(name)
    return getattr(container, name, None)


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Un 0.0 exacto es el marcador de "sin posición" en varios firmwares.
    return number if number else None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    return None if value is None else bool(value)


def _enum_name(value: object) -> str | None:
    if value is None:
        return None
    # PayloadType.Advert -> "Advert"
    return getattr(value, "name", None) or str(value)
