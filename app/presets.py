from __future__ import annotations

from app.models import PresetKey

# Nombres de los presets conocidos. Los presets se descubren solos desde los datos
# (cualquier tupla nueva en un /status crea su fila); esto solo pone **alias** a los
# que ya hemos identificado.
#
# Aquí va solo el nombre, nunca los parámetros: los añade `preset_label` desde la
# tupla. Antes se escribían a mano y unos llevaban SF/CR y otros no, así que dos
# filas de la misma tabla se leían con criterios distintos y no se podían comparar.
#
# Los cuatro slots de h1.4 van con los parámetros que usa de verdad la red de
# Cataluña — BW 62.5 kHz, SF7, CR 4/6 — observados en el broker el 2026-09-14.
# No son los SF11/CR5 que se ven en otras redes europeas.
SEED_PRESETS: dict[PresetKey, str] = {
    PresetKey(869.432, 62.5, 7, 6): "Slot 1",
    PresetKey(869.493, 62.5, 7, 6): "Slot 2",
    PresetKey(869.556, 62.5, 7, 6): "Slot 3",
    PresetKey(869.618, 62.5, 7, 6): "Slot 4",
    PresetKey(869.618, 62.5, 8, 8): "Slot 4",
    PresetKey(869.618, 62.5, 7, 8): "Slot 4",
    PresetKey(869.450, 62.5, 7, 6): "Propuesto · guarda inferior",
    PresetKey(869.600, 62.5, 7, 6): "Propuesto · guarda superior",
    PresetKey(869.525, 250.0, 11, 5): "LongFast",
}


def preset_parameters(preset: PresetKey) -> str:
    """La tupla técnica, siempre completa y siempre en el mismo orden."""
    return (
        f"{preset.freq_mhz:g} MHz · BW{preset.bw_khz:g} · SF{preset.sf} · CR4/{preset.cr}"
    )


def preset_label(preset: PresetKey) -> str:
    """Nombre del preset, si lo tiene, seguido **siempre** de sus parámetros.

    La etiqueta tiene que describirse sola: sin frecuencia y sin SF/CR no se puede
    comparar nada leyendo. En 869.618 conviven tres configuraciones distintas, así
    que el nombre por sí solo no basta para distinguirlas.
    """
    name = SEED_PRESETS.get(preset)
    parameters = preset_parameters(preset)
    return f"{name} · {parameters}" if name else parameters


def preset_slug(preset: PresetKey) -> str:
    return f"{preset.freq_mhz:g}_{preset.bw_khz:g}_{preset.sf}_{preset.cr}"


def channel_id(freq_mhz: float, bw_khz: float, sf: int) -> str:
    """Identificador del **canal físico**.

    El canal físico es ``(freq, bw, sf)``: la CR queda fuera a propósito. Viaja en la
    cabecera LoRa, así que dos receptores con CR distinta sobre la misma frecuencia y
    SF decodifican la misma transmisión. Medido en el broker real: 8 receptores con
    2 CR distintas oyendo el mismo hash, dentro de un solo canal físico, y 0 hashes
    repartidos entre dos canales.
    """
    return f"{freq_mhz:.4f}|{bw_khz:.3f}|{sf}"


def parse_channel_id(value: str) -> tuple[float, float, int]:
    """Inversa de :func:`channel_id`. Falla fuerte si el formato no cuadra."""
    freq, bw, sf = value.split("|")
    return (float(freq), float(bw), int(sf))


# Orden canónico de las dimensiones al nombrar un canal.
CHANNEL_DIMENSIONS = ("freq", "bw", "sf")


def channel_label(
    key: tuple, group_by: tuple[str, ...] = CHANNEL_DIMENSIONS
) -> str:
    """Etiqueta legible de un grupo de dimensiones.

    Con las tres dimensiones da ``"869.618 MHz · BW62.5 · SF7"``; con un subconjunto,
    solo las que se hayan pedido.
    """
    parts = []
    for name, value in zip(group_by, key):
        if name == "freq":
            parts.append(f"{value:g} MHz")
        elif name == "bw":
            parts.append(f"BW{value:g}")
        else:
            parts.append(f"SF{value}")
    return " · ".join(parts)


def is_narrow_slot(preset: PresetKey) -> bool:
    """Los cuatro slots estrechos de h1.4 con BW 62.5 kHz."""
    return abs(preset.bw_khz - 62.5) < 0.01 and 869.4 <= preset.freq_mhz <= 869.65


# Centros nominales de los cuatro slots estrechos, en el orden en que los numera
# la comunidad. El color de la web sale de aquí, nunca del índice de la fila.
NARROW_SLOTS_MHZ = (869.432, 869.493, 869.556, 869.618)


def slot_index(freq_mhz: float | None, tolerance: float = 0.01) -> int | None:
    """1..4 si la frecuencia es uno de los slots estrechos; ``None`` si no."""
    if freq_mhz is None:
        return None
    for index, centre in enumerate(NARROW_SLOTS_MHZ, start=1):
        if abs(freq_mhz - centre) <= tolerance:
            return index
    return None
