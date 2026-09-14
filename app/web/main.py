from __future__ import annotations

import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_settings
from app.db import connect, init_db
from app.health import collector_health
from app.periods import ChannelRegime, channel_regimes, compare_periods, find_control
from app.query import (
    DIMENSION_LABELS,
    DIMENSIONS,
    channel_catalog,
    channel_costs,
    channel_regions,
    channel_series,
    channel_summaries,
    common_observers,
    latest_packet_id,
    observer_rows,
    paired_observers,
    paired_pairs,
    preset_summaries,
    quality_rows,
    recent_packets,
    window_bounds,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

NAV = (
    ("/", "Resumen"),
    ("/vivo", "En vivo"),
    ("/comparador", "Comparador"),
    ("/comparar", "Comparar"),
    ("/observadores", "Observadores"),
    ("/campana", "Campaña"),
    ("/calidad", "Calidad"),
    ("/metodologia", "Metodología"),
)

WINDOWS = ("1h", "6h", "24h", "7d")
# La comparación aparcada necesita que alguien haya cambiado de canal, así que
# ventanas cortas casi nunca sirven.
PAIRED_WINDOWS = ("24h", "7d", "30d")
# Un régimen de menos de una hora es ruido, no una campaña.
MIN_REGIME_MINUTES = 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    conn = connect(settings.db_path)
    init_db(conn)
    app.state.settings = settings
    app.state.conn = conn
    yield
    conn.close()


app = FastAPI(title="Observatorio de canales MeshCore 868", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _db(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def _parse_dims(raw: str) -> tuple[str, ...]:
    dims = tuple(part for part in raw.split(",") if part)
    return dims if dims else DIMENSIONS


def _context(request: Request, **extra: object) -> dict[str, object]:
    return {"nav": NAV, "dims_all": DIMENSIONS, "dim_labels": DIMENSION_LABELS, **extra}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    conn = _db(request)
    settings = request.app.state.settings
    now = int(time.time())
    since, _ = window_bounds("24h", now)

    summaries = channel_summaries(conn, since_ts=since)
    observers = observer_rows(conn)
    quality = quality_rows(conn)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_context(
            request,
            active="/",
            summaries=summaries,
            observers=observers,
            quality=quality[0] if quality else None,
            health=collector_health(conn, now, settings.stale_seconds),
            generated_at=now,
            window="24h",
        ),
    )


@app.get("/vivo", response_class=HTMLResponse)
async def vivo(request: Request):
    conn = _db(request)
    packets = recent_packets(conn, limit=40)
    return templates.TemplateResponse(
        request=request,
        name="vivo.html",
        context=_context(
            request,
            active="/vivo",
            packets=packets,
            last_id=latest_packet_id(conn),
            generated_at=int(time.time()),
        ),
    )


@app.get("/api/packets")
async def api_packets(request: Request, after: int = Query(0), limit: int = Query(50)):
    """Paquetes nuevos desde ``after``. El navegador lo consulta cada 2 s.

    No hay SSE a propósito: a 24 paquetes por minuto, preguntar cada dos segundos
    cuesta menos que mantener una conexión abierta por visitante.
    """
    conn = _db(request)
    packets = recent_packets(conn, after_id=after, limit=limit)
    return JSONResponse(
        {
            "packets": [
                {
                    "id": row.id,
                    "time": time.strftime("%H:%M:%S", time.localtime(row.ts)),
                    "observer": row.observer,
                    "iata": row.iata or "",
                    "channel": row.channel,
                    "snr_db": row.snr_db,
                    "rssi": row.rssi,
                    "payload_type": row.payload_type or "",
                    "path_length": row.path_length,
                    "hash": (row.packet_hash or "")[:8],
                }
                for row in packets
            ],
            "last_id": packets[-1].id if packets else after,
            "server_time": int(time.time()),
        }
    )


@app.get("/comparador", response_class=HTMLResponse)
async def comparador(
    request: Request,
    window: str = Query("24h"),
    by: str = Query("freq,bw,sf"),
    mode: str = Query("recepciones"),
    common: int = Query(0),
):
    if window not in WINDOWS:
        window = "24h"
    if mode not in {"recepciones", "receptores"}:
        mode = "recepciones"

    conn = _db(request)
    now = int(time.time())
    since, until = window_bounds(window, now)
    group_by = _parse_dims(by)

    per_receiver = mode == "receptores"
    only_common = bool(common) and per_receiver

    summaries = channel_summaries(
        conn, since, until, group_by, per_receiver=per_receiver, only_common=only_common
    )
    series = channel_series(conn, since, until, group_by)
    presets = preset_summaries(conn, since, until)
    common_count, total_count = common_observers(conn, since, until, group_by)

    # El piso de ruido depende de la frecuencia y del ancho: agrupar sin ellos mezcla
    # canales distintos y el promedio deja de significar algo.
    mixes_channels = "freq" not in group_by or "bw" not in group_by

    return templates.TemplateResponse(
        request=request,
        name="comparador.html",
        context=_context(
            request,
            active="/comparador",
            summaries=summaries,
            series=series,
            presets=presets,
            group_by=group_by,
            window=window,
            windows=WINDOWS,
            mode=mode,
            only_common=only_common,
            common_count=common_count,
            total_count=total_count,
            mixes_channels=mixes_channels,
            generated_at=now,
        ),
    )


@app.get("/comparar", response_class=HTMLResponse)
async def comparar(
    request: Request,
    mode: str = Query("simultaneo"),
    window: str = Query("7d"),
    a: int | None = Query(None),
    b: int | None = Query(None),
):
    """Dos modos de comparación, con reglas distintas a propósito.

    - **simultáneo**: el mismo sujeto en dos canales a la vez, exigiendo solapamiento
      temporal. Mide recepción con mucha potencia y pocos nodos.
    - **por períodos**: la red entera en un canal contra la red en otro. Mide el
      comportamiento de la malla, pero arrastra el efecto del tiempo, y por eso lo que
      lo hace defendible es el control.
    """
    if mode not in {"simultaneo", "periodos"}:
        mode = "simultaneo"

    conn = _db(request)
    now = int(time.time())

    if mode == "periodos":
        return _comparar_periodos(request, conn, now, a, b)

    if window not in PAIRED_WINDOWS:
        window = "7d"
    since, until = window_bounds(window, now)

    return templates.TemplateResponse(
        request=request,
        name="comparar.html",
        context=_context(
            request,
            active="/comparar",
            mode="simultaneo",
            window=window,
            windows=PAIRED_WINDOWS,
            observers=paired_observers(conn, since, until),
            pairs=paired_pairs(conn, since, until),
            regions=channel_regions(conn, since, until),
            costs=channel_costs(conn, since, until),
            generated_at=now,
        ),
    )


def _comparar_periodos(
    request: Request,
    conn: sqlite3.Connection,
    now: int,
    a: int | None,
    b: int | None,
):
    regimes = channel_regimes(conn, 0, min_minutes=MIN_REGIME_MINUTES)

    period_a = _regime_by_start(regimes, a) or (regimes[-2] if len(regimes) >= 2 else None)
    period_b = _regime_by_start(regimes, b) or (regimes[-1] if len(regimes) >= 2 else None)

    comparison = None
    control = None
    if period_a and period_b and period_a.starts_at != period_b.starts_at:
        control = find_control(regimes, period_a, period_b)
        comparison = compare_periods(conn, period_a, period_b, control)

    return templates.TemplateResponse(
        request=request,
        name="comparar_periodos.html",
        context=_context(
            request,
            active="/comparar",
            mode="periodos",
            regimes=regimes,
            period_a=period_a,
            period_b=period_b,
            comparison=comparison,
            min_regime_minutes=MIN_REGIME_MINUTES,
            generated_at=now,
        ),
    )


def _regime_by_start(regimes: list[ChannelRegime], starts_at: int | None):
    if starts_at is None:
        return None
    return next((r for r in regimes if r.starts_at == starts_at), None)


@app.get("/observadores", response_class=HTMLResponse)
async def observadores(request: Request):
    now = int(time.time())
    return templates.TemplateResponse(
        request=request,
        name="observadores.html",
        context=_context(
            request, active="/observadores", observers=observer_rows(_db(request)),
            generated_at=now,
        ),
    )


@app.get("/campana", response_class=HTMLResponse)
async def campana(request: Request):
    now = int(time.time())
    return templates.TemplateResponse(
        request=request,
        name="campana.html",
        context=_context(
            request, active="/campana", catalog=channel_catalog(_db(request)),
            generated_at=now,
        ),
    )


@app.get("/calidad", response_class=HTMLResponse)
async def calidad(request: Request):
    now = int(time.time())
    return templates.TemplateResponse(
        request=request,
        name="calidad.html",
        context=_context(
            request, active="/calidad", quality=quality_rows(_db(request)),
            generated_at=now,
        ),
    )


@app.get("/metodologia", response_class=HTMLResponse)
async def metodologia(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="metodologia.html",
        context=_context(request, active="/metodologia"),
    )


def _fmt(value: object, digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}".replace(",", " ")
    return str(value)


def _fmt_int(value: object) -> str:
    return "—" if value is None else f"{int(value):,}".replace(",", " ")


def _fmt_signed(value: object, digits: int, suffix: str) -> str:
    """Diferencia con el signo delante: comparar es ver el + y el −, no el valor."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:+,.{digits}f}{suffix}".replace(",", " ")
    return str(value)


def _fmt_ts(value: object) -> str:
    if not value:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))


def _fmt_ago(value: object) -> str:
    if not value:
        return "—"
    seconds = int(time.time()) - int(value)
    if seconds < 90:
        return "hace segundos"
    if seconds < 5400:
        return f"hace {seconds // 60} min"
    if seconds < 172800:
        return f"hace {seconds // 3600} h"
    return f"hace {seconds // 86400} d"


def _fmt_bytes(value: object) -> str:
    """Bytes con unidad legible: 32 B, 1,5 kB, 2,3 MB."""
    if value is None:
        return "—"
    amount = float(value)
    for suffix, scale in (("MB", 1024 * 1024), ("kB", 1024)):
        if abs(amount) >= scale:
            return f"{amount / scale:,.1f} {suffix}".replace(",", " ")
    return f"{amount:,.0f} B".replace(",", " ")


def _fmt_metric(value: object, unit: str) -> str:
    if unit == "dbm":
        return _fmt(value, 1, " dBm")
    if unit == "pct":
        return _fmt(value, 1, " %")
    if unit == "num":
        return _fmt_int(value)
    return _fmt(value, 1)


templates.env.filters.update(
    db=lambda v: _fmt(v, 1, " dBm"),
    db1=lambda v: _fmt(v, 1),
    # Tres decimales: los slots de h1.4 están separados por 62.5 kHz, así que
    # redondear a un decimal borra justo lo que se quiere distinguir.
    mhz=lambda v: _fmt(v, 3, " MHz"),
    khz=lambda v: _fmt(v, 1, " kHz"),
    snr=lambda v: _fmt(v, 1, " dB"),
    pct=lambda v: _fmt(v, 1, " %"),
    num=_fmt_int,
    bsize=_fmt_bytes,
    ratio=lambda v: _fmt(v, 2, "×"),
    ddb=lambda v: _fmt_signed(v, 1, " dB"),
    dpct=lambda v: _fmt_signed(v, 1, " %"),
    ts=_fmt_ts,
    ago=_fmt_ago,
    metric=_fmt_metric,
)
