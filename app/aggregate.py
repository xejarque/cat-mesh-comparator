"""Rollups por minuto a partir de paquetes ya atribuidos y de los status.

Los paquetes con ``preset_uncertain`` **no** entran en los agregados del comparador:
un dato ambiguo no debe sesgar una comparación en silencio. Se cuentan aparte para
poder mostrarlos en la página de calidad.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import fmean, median

from app.models import AttributedPacket, PresetKey, Status
from app.stats import median as _median

SECONDS_PER_MINUTE = 60


def minute_of(ts: int) -> int:
    return ts - (ts % SECONDS_PER_MINUTE)


@dataclass(frozen=True, slots=True)
class PresetMinute:
    minute_ts: int
    preset: PresetKey
    pkts: int
    uniq_hashes: int
    snr_avg_x4: float | None
    snr_p50_x4: float | None
    snr_ge0_pct: float | None
    rssi_avg: float | None
    observers: int


@dataclass(frozen=True, slots=True)
class ObserverMinute:
    observer_pubkey: str
    minute_ts: int
    preset: PresetKey | None
    noise_floor: float | None
    chan_util_pct: float | None
    err_per_h: float | None
    pkts_rx: int
    # Señal vista **por este receptor**. Sin esto, agregar por canal queda ponderado
    # por paquetes y un receptor charlatán domina la media.
    snr_p50_x4: float | None = None
    rssi_avg: float | None = None


@dataclass(frozen=True, slots=True)
class ChannelMinute:
    """Agregado por **canal físico** ``(freq, bw, sf)``, con la CR fuera de la clave.

    Es la unidad correcta para comparar: una transmisión ocupa un solo canal físico,
    mientras que varios receptores con CR distinta sobre ese canal la oyen a la vez.
    Agrupar por preset contaba esas recepciones dos veces.
    """

    minute_ts: int
    freq_mhz: float
    bw_khz: float
    sf: int
    pkts: int
    uniq_hashes: int
    snr_avg_x4: float | None
    snr_p50_x4: float | None
    snr_ge0_pct: float | None
    rssi_avg: float | None
    observers: int
    crs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PairMinute:
    minute_ts: int
    freq_mhz: float
    bw_khz: float
    sf: int
    obs_a: str
    obs_b: str
    heard_a: int
    heard_b: int
    both: int
    pdr_pct: float


@dataclass(frozen=True, slots=True)
class AggregateResult:
    presets: list[PresetMinute]
    channels: list[ChannelMinute]
    observers: list[ObserverMinute]
    pairs: list[PairMinute]
    uncertain_packets: int
    unattributed_packets: int


def aggregate(
    statuses: Iterable[Status], packets: Iterable[AttributedPacket]
) -> AggregateResult:
    statuses = list(statuses)
    packets = list(packets)

    trustworthy = [row for row in packets if row.preset is not None and not row.uncertain]

    return AggregateResult(
        presets=_preset_minutes(trustworthy),
        channels=_channel_minutes(trustworthy),
        observers=_observer_minutes(statuses, trustworthy),
        pairs=_pair_minutes(trustworthy),
        uncertain_packets=sum(1 for row in packets if row.uncertain),
        unattributed_packets=sum(1 for row in packets if row.preset is None),
    )


def _packet_stats(
    items: list[AttributedPacket],
) -> tuple[int, int, float | None, float | None, float | None, float | None, int]:
    snr_values = [row.packet.snr_x4 for row in items if row.packet.snr_x4 is not None]
    rssi_values = [row.packet.rssi for row in items if row.packet.rssi is not None]
    hashes = {row.packet.packet_hash for row in items if row.packet.packet_hash}
    observers = {row.packet.observer_pubkey for row in items}
    return (
        len(items),
        len(hashes),
        fmean(snr_values) if snr_values else None,
        median(snr_values) if snr_values else None,
        (
            100.0 * sum(1 for value in snr_values if value >= 0) / len(snr_values)
            if snr_values
            else None
        ),
        fmean(rssi_values) if rssi_values else None,
        len(observers),
    )


def _preset_minutes(packets: list[AttributedPacket]) -> list[PresetMinute]:
    grouped: dict[tuple[int, PresetKey], list[AttributedPacket]] = defaultdict(list)
    for row in packets:
        grouped[(minute_of(row.packet.ts), row.preset)].append(row)

    minutes: list[PresetMinute] = []
    for (minute_ts, preset), items in sorted(grouped.items()):
        pkts, uniq, snr_avg, snr_p50, ge0, rssi, observers = _packet_stats(items)
        minutes.append(
            PresetMinute(
                minute_ts=minute_ts,
                preset=preset,
                pkts=pkts,
                uniq_hashes=uniq,
                snr_avg_x4=snr_avg,
                snr_p50_x4=snr_p50,
                snr_ge0_pct=ge0,
                rssi_avg=rssi,
                observers=observers,
            )
        )
    return minutes


def _channel_minutes(packets: list[AttributedPacket]) -> list[ChannelMinute]:
    grouped: dict[tuple[int, float, float, int], list[AttributedPacket]] = defaultdict(
        list
    )
    for row in packets:
        preset = row.preset
        grouped[
            (minute_of(row.packet.ts), preset.freq_mhz, preset.bw_khz, preset.sf)
        ].append(row)

    minutes: list[ChannelMinute] = []
    for (minute_ts, freq, bw, sf), items in sorted(grouped.items()):
        pkts, uniq, snr_avg, snr_p50, ge0, rssi, observers = _packet_stats(items)
        minutes.append(
            ChannelMinute(
                minute_ts=minute_ts,
                freq_mhz=freq,
                bw_khz=bw,
                sf=sf,
                pkts=pkts,
                uniq_hashes=uniq,
                snr_avg_x4=snr_avg,
                snr_p50_x4=snr_p50,
                snr_ge0_pct=ge0,
                rssi_avg=rssi,
                observers=observers,
                # Se guarda con qué CR escuchaban los receptores: así la dimensión CR
                # queda visible sin fingir que parte el tráfico.
                crs=tuple(sorted({row.preset.cr for row in items})),
            )
        )
    return minutes


def _observer_minutes(
    statuses: list[Status], packets: list[AttributedPacket]
) -> list[ObserverMinute]:
    packets_rx: dict[tuple[str, int], int] = defaultdict(int)
    signal: dict[tuple[str, int], list[int]] = defaultdict(list)
    strength: dict[tuple[str, int], list[int]] = defaultdict(list)
    for row in packets:
        key = (row.packet.observer_pubkey, minute_of(row.packet.ts))
        packets_rx[key] += 1
        if row.packet.snr_x4 is not None:
            signal[key].append(row.packet.snr_x4)
        if row.packet.rssi is not None:
            strength[key].append(row.packet.rssi)

    deltas: dict[tuple[str, int], list[tuple[float | None, float | None]]] = defaultdict(list)
    by_observer: dict[str, list[Status]] = defaultdict(list)
    for status in statuses:
        by_observer[status.observer_pubkey].append(status)

    for pubkey, items in by_observer.items():
        items.sort(key=lambda status: status.ts)
        for previous, current in zip(items, items[1:]):
            elapsed = current.ts - previous.ts
            if elapsed <= 0:
                continue
            utilisation = _channel_utilisation(previous, current, elapsed)
            errors = _errors_per_hour(previous, current, elapsed)
            if utilisation is not None or errors is not None:
                deltas[(pubkey, minute_of(current.ts))].append((utilisation, errors))

    grouped: dict[tuple[str, int], list[Status]] = defaultdict(list)
    for status in statuses:
        grouped[(status.observer_pubkey, minute_of(status.ts))].append(status)

    keys = set(grouped) | set(deltas) | set(packets_rx)
    effective = _effective_presets(by_observer, keys)

    minutes: list[ObserverMinute] = []
    for pubkey, minute_ts in sorted(keys):
        items = sorted(grouped.get((pubkey, minute_ts), []), key=lambda s: s.ts)
        noise = [s.noise_floor for s in items if s.noise_floor is not None]
        picked = deltas.get((pubkey, minute_ts), [])
        utils = [value for value, _ in picked if value is not None]
        errs = [value for _, value in picked if value is not None]
        minutes.append(
            ObserverMinute(
                observer_pubkey=pubkey,
                minute_ts=minute_ts,
                preset=effective.get((pubkey, minute_ts)),
                noise_floor=fmean(noise) if noise else None,
                chan_util_pct=fmean(utils) if utils else None,
                err_per_h=fmean(errs) if errs else None,
                pkts_rx=packets_rx.get((pubkey, minute_ts), 0),
                snr_p50_x4=_median(signal.get((pubkey, minute_ts), [])),
                rssi_avg=_mean(strength.get((pubkey, minute_ts), [])),
            )
        )
    return minutes


def _effective_presets(
    by_observer: dict[str, list[Status]], keys: set[tuple[str, int]]
) -> dict[tuple[str, int], PresetKey | None]:
    """En qué canal estaba cada receptor **en cada minuto**.

    La configuración de un receptor **persiste** hasta que un status diga lo
    contrario. Antes solo se anotaba el preset en los minutos que contenían un status,
    y como los status llegan cada ~300 s, cuatro de cada cinco minutos quedaban a
    NULL: el 46 % de las filas. Eso dejaba ciego al análisis, porque detectar en qué
    canal estaba la red y agregar por receptor dependen de este dato.
    """
    wanted: dict[str, list[int]] = defaultdict(list)
    for pubkey, minute_ts in keys:
        wanted[pubkey].append(minute_ts)

    effective: dict[tuple[str, int], PresetKey | None] = {}
    for pubkey, minute_list in wanted.items():
        statuses = sorted(by_observer.get(pubkey, []), key=lambda status: status.ts)
        index = 0
        current: PresetKey | None = None
        for minute_ts in sorted(minute_list):
            # Los status del propio minuto también cuentan.
            while (
                index < len(statuses)
                and statuses[index].ts < minute_ts + SECONDS_PER_MINUTE
            ):
                if statuses[index].preset is not None:
                    current = statuses[index].preset
                index += 1
            effective[(pubkey, minute_ts)] = current
    return effective


def _mean(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _channel_utilisation(previous: Status, current: Status, elapsed: int) -> float | None:
    if None in (
        previous.tx_air_secs,
        current.tx_air_secs,
        previous.rx_air_secs,
        current.rx_air_secs,
    ):
        return None
    delta_tx = current.tx_air_secs - previous.tx_air_secs
    delta_rx = current.rx_air_secs - previous.rx_air_secs
    if delta_tx < 0 or delta_rx < 0:
        # Contadores reiniciados (reboot del nodo): el delta no es fiable.
        return None
    return 100.0 * (delta_tx + delta_rx) / elapsed


def _errors_per_hour(previous: Status, current: Status, elapsed: int) -> float | None:
    if previous.recv_errors is None or current.recv_errors is None:
        return None
    delta = current.recv_errors - previous.recv_errors
    if delta < 0:
        return None
    return 3600.0 * delta / elapsed


def _pair_minutes(packets: list[AttributedPacket]) -> list[PairMinute]:
    # Agrupado por canal físico y minuto: dos observers que solo difieren en CR sí
    # pueden compararse, porque oyen lo mismo.
    buckets: dict[tuple[float, float, int, int], dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in packets:
        if row.packet.packet_hash is None:
            continue
        preset = row.preset
        key = (
            preset.freq_mhz,
            preset.bw_khz,
            preset.sf,
            minute_of(row.packet.ts),
        )
        buckets[key][row.packet.observer_pubkey].add(row.packet.packet_hash)

    pairs: list[PairMinute] = []
    for (freq, bw, sf, minute_ts), per_observer in buckets.items():
        observers = sorted(per_observer)
        if len(observers) < 2:
            continue
        for index, obs_a in enumerate(observers):
            for obs_b in observers[index + 1 :]:
                set_a = per_observer[obs_a]
                set_b = per_observer[obs_b]
                union = set_a | set_b
                if not union:
                    continue
                both = len(set_a & set_b)
                pairs.append(
                    PairMinute(
                        minute_ts=minute_ts,
                        freq_mhz=freq,
                        bw_khz=bw,
                        sf=sf,
                        obs_a=obs_a,
                        obs_b=obs_b,
                        heard_a=len(set_a),
                        heard_b=len(set_b),
                        both=both,
                        pdr_pct=100.0 * both / len(union),
                    )
                )
    return sorted(
        pairs,
        key=lambda row: (
            row.minute_ts,
            row.freq_mhz,
            row.bw_khz,
            row.sf,
            row.obs_a,
            row.obs_b,
        ),
    )
