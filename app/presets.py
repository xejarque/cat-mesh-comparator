from __future__ import annotations

from app.models import PresetKey

# Presets que se crean al arrancar aunque nadie los use todavía, para que la página de
# campaña pueda enseñar los huecos.
SEED_PRESETS: tuple[PresetKey, ...] = (
    PresetKey(869.432, 62.5, 7, 6),
    PresetKey(869.493, 62.5, 7, 6),
    PresetKey(869.556, 62.5, 7, 6),
    PresetKey(869.618, 62.5, 7, 6),
    PresetKey(869.450, 62.5, 7, 6),
    PresetKey(869.600, 62.5, 7, 6),
    PresetKey(869.525, 250.0, 11, 5),
)

# Nombre para los presets que **no** son uno de los cuatro slots.
#
# El nombre de un slot lo determina su frecuencia, no esta lista: si no, cualquier
# combinación nueva de SF/CR sobre 869.618 se quedaría sin nombre, que es justo lo que
# pasaba con SF10. Aquí van solo los que no son slots.
PRESET_ALIASES: dict[PresetKey, str] = {
    PresetKey(869.450, 62.5, 7, 6): "Propuesto · guarda inferior",
    PresetKey(869.600, 62.5, 7, 6): "Propuesto · guarda superior",
    PresetKey(869.525, 250.0, 11, 5): "LongFast",
}


def preset_parameters(preset: PresetKey) -> str:
    """La tupla técnica, siempre completa y siempre en el mismo orden."""
    return (
        f"{preset.freq_mhz:g} MHz · BW{preset.bw_khz:g} · SF{preset.sf} · CR4/{preset.cr}"
    )


def preset_name(preset: PresetKey) -> str | None:
    """Nombre del preset, si tiene uno.

    Los cuatro slots de h1.4 se nombran por su **frecuencia**: cualquier combinación
    de SF y CR sobre 869.618 es el slot 4, aunque nadie la haya catalogado antes. Es
    lo que hace que un preset recién descubierto no aparezca distinto de los demás.
    """
    if abs(preset.bw_khz - 62.5) < 0.01:
        index = slot_index(preset.freq_mhz)
        if index is not None:
            return f"Slot {index}"
    return PRESET_ALIASES.get(preset)


def preset_label(preset: PresetKey) -> str:
    """Nombre del preset, si lo tiene, seguido **siempre** de sus parámetros.

    La etiqueta tiene que describirse sola: sin frecuencia y sin SF/CR no se puede
    comparar nada leyendo. En 869.618 conviven cuatro configuraciones distintas, así
    que el nombre por sí solo no basta para distinguirlas.
    """
    name = preset_name(preset)
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
