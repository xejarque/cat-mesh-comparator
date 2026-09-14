"""Fase 0: captura una muestra del broker y resume el contrato real.

Usa el mismo ``normalize_message`` que el colector, así que el informe refleja
exactamente lo que verá la ingesta, no una aproximación.

Las credenciales se pasan por entorno (CATMESH_MQTT_*), nunca por argumentos ni
quedan escritas en el repositorio.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import ssl
import sys
import time
from collections import Counter
from pathlib import Path

import aiomqtt
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from app.models import Packet, ParseError, Status  # noqa: E402
from app.mqtt.normalize import normalize_message, parse_topic  # noqa: E402

DEFAULT_DURATION_S = 300
DEFAULT_PREFIX = "meshcore"


def _tls_context() -> ssl.SSLContext:
    cafile = os.getenv("CATMESH_MQTT_CAFILE") or None
    return ssl.create_default_context(cafile=cafile)


def _client() -> aiomqtt.Client:
    kwargs: dict[str, object] = {
        "hostname": os.environ["CATMESH_MQTT_HOST"],
        "port": int(os.getenv("CATMESH_MQTT_PORT", "1883")),
    }
    if os.getenv("CATMESH_MQTT_TLS", "false").lower() in {"1", "true", "yes"}:
        kwargs["tls_context"] = _tls_context()
    if os.getenv("CATMESH_MQTT_USERNAME"):
        kwargs["username"] = os.environ["CATMESH_MQTT_USERNAME"]
        kwargs["password"] = os.getenv("CATMESH_MQTT_PASSWORD", "")
    return aiomqtt.Client(**kwargs)  # type: ignore[arg-type]


async def capture(duration_s: int, prefix: str, out_path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    topic = f"{prefix}/#"
    deadline = time.monotonic() + duration_s

    async with _client() as client:
        await client.subscribe(topic)
        print(f"Suscrito a {topic}. Capturando {duration_s}s...", flush=True)

        with out_path.open("w", encoding="utf-8") as handle:
            iterator = client.messages.__aiter__()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    message = await asyncio.wait_for(
                        iterator.__anext__(), timeout=remaining
                    )
                except TimeoutError:
                    break
                except StopAsyncIteration:
                    break

                raw = message.payload
                text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                handle.write(f"{message.topic}\t{text}\n")
                counts[str(message.topic)] += 1

    return counts


def report(out_path: Path, prefix: str) -> None:
    leaves: Counter[str] = Counter()
    normalised: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    status_observers: set[str] = set()
    packet_observers: set[str] = set()
    radios: Counter[str] = Counter()
    presets: Counter[tuple[object, ...]] = Counter()
    packet_hash_count = 0
    snr_present = 0
    samples: dict[str, str] = {}

    total = 0
    with out_path.open(encoding="utf-8") as handle:
        for line in handle:
            if "\t" not in line:
                continue
            topic, payload = line.rstrip("\n").split("\t", 1)
            total += 1
            info = parse_topic(topic, prefix)
            leaves[info.leaf if info else "?"] += 1

            result = normalize_message(topic, payload, prefix)
            if result is None:
                normalised["ignorado"] += 1
                continue
            if isinstance(result, ParseError):
                errors[result.reason] += 1
                samples.setdefault(f"error:{result.reason}", f"{topic}\t{payload[:160]}")
                continue

            normalised[type(result).__name__] += 1
            if topic not in samples:
                samples[topic] = f"{topic}\t{payload[:300]}"

            if isinstance(result, Status):
                status_observers.add(result.observer_pubkey)
                radios[result.radio_raw] += 1
                if result.preset is not None:
                    presets[
                        (result.freq_mhz, result.bw_khz, result.sf, result.cr)
                    ] += 1
            elif isinstance(result, Packet):
                packet_observers.add(result.observer_pubkey)
                if result.packet_hash:
                    packet_hash_count += 1
                if result.snr_x4 is not None:
                    snr_present += 1

    print(f"\n=== Informe de la muestra ({out_path}) ===")
    print(f"Mensajes capturados: {total}")

    print("\n1) Mensajes por tipo de topic:")
    for leaf, count in leaves.most_common():
        print(f"   {leaf:<12} {count}")

    print("\n2) Resultado del parseo (lo que verá el colector):")
    for name, count in normalised.most_common():
        print(f"   {name:<12} {count}")
    for reason, count in errors.most_common():
        print(f"   ERROR {reason:<20} {count}")

    if errors:
        print("\n   Ejemplos de payload no reconocido:")
        for key, value in samples.items():
            if key.startswith("error:"):
                print(f"   [{key}] {value}")

    print("\n3) Observers:")
    print(f"   publican /status : {len(status_observers)}")
    print(f"   publican /packets: {len(packet_observers)}")
    sin_status = packet_observers - status_observers
    print(f"   SIN status (no atribuibles): {len(sin_status)} {sorted(sin_status)[:5]}")

    print("\n4) Presets observados (freq, bw, sf, cr):")
    if presets:
        for preset, count in presets.most_common():
            print(f"   {preset}  x{count}")
    else:
        print("   ninguno")

    print("\n5) Otros formatos de 'radio' vistos:")
    for radio, count in radios.most_common(10):
        print(f"   {radio!r}  x{count}")

    print("\n6) Campos de los paquetes:")
    print(f"   con 'hash': {packet_hash_count}")
    print(f"   con 'SNR' : {snr_present}")

    print("\n7) Una línea de ejemplo por topic:")
    for topic, sample in sorted(samples.items())[:6]:
        print(f"   {sample}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Captura una muestra del broker MQTT")
    parser.add_argument("--seconds", type=int, default=None)
    parser.add_argument("--prefix", default=os.getenv("CATMESH_TOPIC_PREFIX", DEFAULT_PREFIX))
    parser.add_argument("--out", type=Path, default=Path("sample.jsonl"))
    args = parser.parse_args()

    if not os.getenv("CATMESH_MQTT_HOST"):
        parser.error("define CATMESH_MQTT_HOST (y CATMESH_MQTT_USERNAME/PASSWORD)")

    duration = args.seconds or int(os.getenv("CATMESH_SAMPLE_SECONDS", DEFAULT_DURATION_S))
    try:
        counts = await capture(duration, args.prefix, args.out)
    except aiomqtt.MqttError as exc:
        print(f"\nNo se pudo conectar: {exc}", file=sys.stderr)
        return 2

    if not counts:
        print("\nNo llegó ningún mensaje. Revisa prefijo, credenciales o puerto.", file=sys.stderr)
        return 1

    report(args.out, args.prefix)
    return 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(asyncio.run(main()))
