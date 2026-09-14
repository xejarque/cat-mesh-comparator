"""Resolución del preset de cada paquete.

El paquete no dice en qué preset entró: el preset es una propiedad del receptor.
Se resuelve uniendo el pubkey del topic con la línea de tiempo de ``/status`` de
ese observer. La clave es hacerlo **temporalmente correcto**: usar el último
status conocido en el momento del paquete, nunca el status retenido actual.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.models import AttributedPacket, Attribution, Packet, PresetKey, Status


@dataclass(frozen=True, slots=True)
class Timeline:
    timestamps: list[int]
    presets: list[PresetKey | None]


def build_timelines(statuses: Iterable[Status]) -> dict[str, Timeline]:
    grouped: dict[str, list[Status]] = defaultdict(list)
    for status in statuses:
        if status.preset is None:
            # Un status sin ``radio`` (p. ej. el aviso de offline) no dice nada del
            # preset: si lo metiéramos en la línea de tiempo, borraría el preset que
            # ya conocíamos y dejaría sin atribuir todo el tráfico posterior.
            continue
        grouped[status.observer_pubkey].append(status)

    timelines: dict[str, Timeline] = {}
    for pubkey, items in grouped.items():
        ordered = sorted(items, key=lambda status: status.ts)
        deduped: list[Status] = []
        for status in ordered:
            if deduped and deduped[-1].ts == status.ts:
                deduped[-1] = status
            else:
                deduped.append(status)
        timelines[pubkey] = Timeline(
            timestamps=[status.ts for status in deduped],
            presets=[status.preset for status in deduped],
        )
    return timelines


def resolve(timeline: Timeline | None, ts: int, max_gap_s: int) -> Attribution:
    if timeline is None or not timeline.timestamps:
        # Observer que nunca publica /status: no hay forma de saber su preset.
        return Attribution(None, False)

    index = bisect_right(timeline.timestamps, ts) - 1
    if index < 0:
        # Anterior al primer status: configuración desconocida.
        return Attribution(None, False)

    preset = timeline.presets[index]
    uncertain = (ts - timeline.timestamps[index]) > max_gap_s

    if not uncertain:
        next_index = index + 1
        if next_index < len(timeline.timestamps):
            # Si el siguiente status anuncia otro preset, el cambio ocurrió en algún
            # punto entre ambos: este paquete cae en la ventana ambigua.
            if timeline.presets[next_index] != preset:
                uncertain = True

    return Attribution(preset, uncertain)


def attribute_packets(
    packets: Iterable[Packet],
    timelines: dict[str, Timeline],
    max_gap_s: int,
) -> list[AttributedPacket]:
    return [
        AttributedPacket(
            packet=packet,
            attribution=resolve(
                timelines.get(packet.observer_pubkey), packet.ts, max_gap_s
            ),
        )
        for packet in packets
    ]
