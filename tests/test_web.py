from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db, insert_packets, insert_status
from app.models import PresetKey
from app.rollup import rollup_window
from app.web.main import app
from tests.helpers import attribute, mk_packet, mk_status


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """La web lee de una base propia, así que se le apunta a una de pruebas."""
    db_path = tmp_path / "web.db"
    conn = connect(db_path)
    init_db(conn)
    insert_status(conn, mk_status("2026-09-14T10:00:00Z", "869.618,62.5,7,6", "OBS1"))
    insert_packets(
        conn,
        [
            attribute(
                mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"),
                PresetKey(869.618, 62.5, 7, 6),
            )
        ],
    )
    rollup_window(conn, 0)
    conn.close()

    monkeypatch.setenv("CATMESH_DB", str(db_path))
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/vivo",
        "/comparador",
        "/comparar",
        "/observadores",
        "/campana",
        "/calidad",
        "/metodologia",
    ],
)
def test_pages_render(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_comparar_says_what_is_missing_when_there_is_nothing(client):
    # Con un solo canal no hay comparación posible, y la página debe explicarlo en
    # vez de enseñar tablas vacías.
    response = client.get("/comparar")

    assert "Hoy no hay nada que comparar" in response.text
    assert "uno que vaya alternando" in response.text


def test_comparar_unknown_window_falls_back(client):
    assert client.get("/comparar?window=1h").status_code == 200


def test_comparar_offers_both_modes(client):
    response = client.get("/comparar")

    assert "Simultáneo" in response.text
    assert "Por períodos" in response.text


def test_period_mode_without_history_explains_itself(client):
    # Con una sola hora de datos y todo en el mismo canal no hay dos períodos, y la
    # página debe decirlo en vez de enseñar tablas vacías.
    response = client.get("/comparar?mode=periodos")

    assert response.status_code == 200
    assert "Solo hay un período" in response.text or "No se ha detectado ningún período" in response.text


def test_unknown_mode_falls_back_to_simultaneous(client):
    response = client.get("/comparar?mode=inventado")

    assert response.status_code == 200
    assert "comparación válida" in response.text or "Hoy no hay nada que comparar" in response.text


def test_campana_includes_the_campaign_protocol(client):
    response = client.get("/campana")

    assert "A-B-A, no solo A-B" in response.text
    assert "no se puede concluir nada" in response.text


def test_api_packets_returns_the_newest_without_a_cursor(client):
    body = client.get("/api/packets").json()

    assert len(body["packets"]) == 1
    packet = body["packets"][0]
    assert packet["channel"] == "869.618 MHz · SF7 · CR6"
    assert packet["observer"] == "Obs"
    assert body["last_id"] == packet["id"]


def test_api_packets_with_a_cursor_returns_nothing_new(client):
    newest = client.get("/api/packets").json()["last_id"]

    body = client.get(f"/api/packets?after={newest}").json()

    assert body["packets"] == []
    # Sin novedades, el cursor no retrocede.
    assert body["last_id"] == newest


def test_api_packets_cursor_only_returns_what_came_after(client):
    newest = client.get("/api/packets").json()["last_id"]

    body = client.get(f"/api/packets?after={newest - 1}").json()

    assert [packet["id"] for packet in body["packets"]] == [newest]


def test_comparador_accepts_dimensional_grouping(client):
    response = client.get("/comparador?window=24h&by=sf")

    assert response.status_code == 200
    assert "SF7" in response.text


def test_comparador_warns_when_grouping_mixes_channels(client):
    # Sin frecuencia, el ruido de fondo promedia canales distintos: hay que avisar.
    response = client.get("/comparador?by=sf")

    assert "promedio de canales distintos" in response.text


def test_comparador_does_not_warn_on_the_full_channel(client):
    response = client.get("/comparador?by=freq,bw,sf")

    assert "promedio de canales distintos" not in response.text


def test_comparador_per_receiver_mode_shows_the_range(client):
    response = client.get("/comparador?mode=receptores")

    assert response.status_code == 200
    # La columna que justifica el modo: sin rango, un receptor malo queda escondido.
    assert "Receptores" in response.text
    assert "(rango)" in response.text


def test_comparador_common_filter_reports_the_intersection(client):
    response = client.get("/comparador?mode=receptores&common=1")

    assert response.status_code == 200
    assert "Comparar solo los comunes" in response.text
    assert "receptores que aparecen" in response.text


def test_comparador_common_filter_is_ignored_without_per_receiver(client):
    # El filtro solo tiene sentido sobre datos por receptor; con el modo de
    # recepciones no debe aparecer ni cambiar nada.
    response = client.get("/comparador?common=1")

    assert response.status_code == 200
    assert "Comparar solo los comunes" not in response.text


def test_unknown_comparador_mode_falls_back(client):
    response = client.get("/comparador?mode=inventado")

    assert response.status_code == 200
    assert "Todas las recepciones" in response.text


def test_unknown_window_falls_back_instead_of_failing(client):
    response = client.get("/comparador?window=99y")

    assert response.status_code == 200


def test_index_shows_the_empty_state_without_data(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    conn = connect(db_path)
    init_db(conn)
    conn.close()

    monkeypatch.setenv("CATMESH_DB", str(db_path))
    with TestClient(app) as empty_client:
        response = empty_client.get("/")

    assert response.status_code == 200
    assert "Todavía no hay datos agregados" in response.text
