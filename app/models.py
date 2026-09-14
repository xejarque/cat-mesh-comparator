from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TopicInfo:
    iata: str
    pubkey: str
    leaf: str


@dataclass(frozen=True, slots=True, order=True)
class PresetKey:
    """Un preset es la tupla completa: comparar solo por frecuencia no significa nada."""

    freq_mhz: float
    bw_khz: float
    sf: int
    cr: int

    @property
    def db_id(self) -> str:
        """Identificador canónico y estable, para claves y URLs."""
        return f"{self.freq_mhz:.4f}|{self.bw_khz:.3f}|{self.sf}|{self.cr}"


@dataclass(frozen=True, slots=True)
class Packet:
    ts: int
    observer_pubkey: str
    iata: str
    snr_x4: int | None
    rssi: int | None
    packet_type: int | None
    route: str | None
    payload_len: int | None
    raw_hex: str | None
    origin: str | None
    packet_hash: str | None


@dataclass(frozen=True, slots=True)
class Status:
    ts: int
    observer_pubkey: str
    iata: str
    radio_raw: str
    freq_mhz: float | None
    bw_khz: float | None
    sf: int | None
    cr: int | None
    noise_floor: int | None
    tx_air_secs: int | None
    rx_air_secs: int | None
    recv_errors: int | None
    uptime_secs: int | None
    battery_mv: int | None
    queue_len: int | None
    model: str | None
    fw: str | None
    online: bool
    # Nombre humano del observer (`origin`). Sin esto, la web solo puede enseñar
    # el prefijo del pubkey.
    name: str | None = None

    @property
    def preset(self) -> PresetKey | None:
        if None in (self.freq_mhz, self.bw_khz, self.sf, self.cr):
            return None
        return PresetKey(self.freq_mhz, self.bw_khz, self.sf, self.cr)


@dataclass(frozen=True, slots=True)
class ParseError:
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Attribution:
    """Resultado de resolver a qué preset pertenece un paquete."""

    preset: PresetKey | None
    uncertain: bool


@dataclass(frozen=True, slots=True)
class DecodedInfo:
    """Enriquecimiento opcional obtenido al decodificar el ``raw`` del paquete.

    Todo puede ser ``None``: el decodificador es un extra y nunca es obligatorio.

    La posición viene de los adverts y es la del **emisor** del paquete, no la del
    receptor que lo oyó.
    """

    path_length: int | None
    payload_type: str | None
    is_valid: bool | None
    sender_pubkey: str | None = None
    sender_name: str | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True, slots=True)
class AttributedPacket:
    packet: Packet
    attribution: Attribution
    decoded: DecodedInfo | None = None

    @property
    def preset(self) -> PresetKey | None:
        return self.attribution.preset

    @property
    def uncertain(self) -> bool:
        return self.attribution.uncertain
