from __future__ import annotations

from dataclasses import dataclass

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

# Nombre para los presets que **no** son un slot de ningún plan de canales.
#
# El nombre de un slot lo determina su plan, no esta lista: si no, cualquier
# combinación nueva de SF/CR sobre una frecuencia del plan se quedaría sin nombre, que
# es justo lo que pasaba con SF10. Aquí van solo los que no son slots.
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

    El nombre de un slot sale de su **plan de canales**: cualquier combinación de SF y
    CR sobre una de sus frecuencias es ese slot, aunque nadie la haya catalogado antes.
    Es lo que hace que un preset recién descubierto no aparezca distinto de los demás, y
    lo que permite estrenar un plan nuevo —tres slots con guarda de banda, por ejemplo—
    sin tocar esta función.
    """
    index = slot_index(preset.freq_mhz, preset.bw_khz)
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


@dataclass(frozen=True, slots=True)
class ChannelPlan:
    """Un plan de canales: un ancho de banda y sus frecuencias, en orden.

    El número de un slot es su posición en ``centres_mhz``, empezando en 1, así que un
    plan de otro tamaño —los tres slots con guarda de banda que se quieren probar— se
    soporta añadiendo una entrada a :data:`CHANNEL_PLANS`, no tocando el nombrado.
    """

    bw_khz: float
    centres_mhz: tuple[float, ...]


# Planes conocidos. Los cuatro slots estrechos de h1.4, en el orden en que los numera
# la comunidad: el color de la web sale de aquí, nunca del índice de la fila.
CHANNEL_PLANS: tuple[ChannelPlan, ...] = (
    ChannelPlan(62.5, (869.432, 869.493, 869.556, 869.618)),
)

# El nominal del plan y la frecuencia medida no cuadran al último dígito (869.431
# contra 869.432), así que la comparación lleva tolerancia.
PLAN_TOLERANCE_MHZ = 0.01


def slot_index(freq_mhz: float | None, bw_khz: float | None = None) -> int | None:
    """``1..N`` si la frecuencia es un slot de algún plan; ``None`` si no.

    Sin ``bw_khz`` se busca en todos los planes: es lo que necesita el color de una fila
    agrupada solo por frecuencia. Con él, solo en los planes de ese ancho.
    """
    if freq_mhz is None:
        return None
    for plan in CHANNEL_PLANS:
        if bw_khz is not None and abs(plan.bw_khz - bw_khz) > PLAN_TOLERANCE_MHZ:
            continue
        for index, centre in enumerate(plan.centres_mhz, start=1):
            if abs(freq_mhz - centre) <= PLAN_TOLERANCE_MHZ:
                return index
    return None
