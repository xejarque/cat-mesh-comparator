from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db, insert_packets, insert_status
from app.i18n import CATALAN, LANGUAGES, normalize, translate
from app.models import PresetKey
from app.rollup import rollup_window
from app.web.main import app
from tests.helpers import attribute, mk_packet, mk_status

PAGES = (
    "/",
    "/vivo",
    "/comparador",
    "/comparar",
    "/observadores",
    "/campana",
    "/calidad",
    "/metodologia",
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
# Tolerante a propósito: caza el caso común (literal en una línea, comillas simples o
# dobles). Un literal partido o con comillas dentro se le escapa, y por eso el test de
# cobertura es una red, no una garantía.
LITERAL = re.compile(r"""_\(\s*(['"])(.*?)\1""")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "i18n.db"
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


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_in_catalan(client, path):
    response = client.get(f"{path}?lang=ca")

    assert response.status_code == 200
    assert '<html lang="ca">' in response.text
    assert "Meshcore Preset Analyzer" in response.text
    assert "Observatorio MeshCore 868" not in response.text


def test_castilian_is_the_default(client):
    response = client.get("/")

    assert '<html lang="es">' in response.text
    # Un texto catalán que no debe aparecer sin pedirlo.
    assert "Quin canal està més net" not in response.text


def test_catalan_translates_the_content(client):
    response = client.get("/?lang=ca")

    assert "Quin canal està més net" in response.text
    assert "Canals amb dades (24 h)" in response.text


def test_the_header_offers_the_language_switch(client):
    response = client.get("/")

    assert ">ES<" in response.text
    assert ">CA<" in response.text
    assert "lang=ca" in response.text


def test_the_choice_is_remembered_in_a_cookie(client):
    response = client.get("/?lang=ca")
    assert "lang=ca" in response.headers["set-cookie"]

    # Sin el parámetro, la cookie manda: la elección sobrevive a la navegación.
    follow_up = client.get("/")
    assert '<html lang="ca">' in follow_up.text


def test_an_unknown_language_falls_back_to_castilian(client):
    assert '<html lang="es">' in client.get("/?lang=xx").text


def test_the_switch_keeps_repeated_query_params(client):
    # /comparador repite `by`: el enlace del selector no debe colapsar los filtros.
    response = client.get("/comparador?by=freq&by=bw&lang=ca")

    assert response.status_code == 200
    assert "by=freq&amp;by=bw&amp;lang=ca" in response.text


def test_translate_known_missing_and_unknown_languages():
    assert str(translate("Resumen", "ca")) == "Resum"
    assert str(translate("Resumen", "es")) == "Resumen"
    assert str(translate("cadena que no existe", "ca")) == "cadena que no existe"


def test_translate_substitutes_values():
    assert str(translate("hace {n} min", "ca", n=5)) == "fa 5 min"
    assert str(translate("hace {n} min", "es", n=5)) == "hace 5 min"


def test_translate_keeps_inline_html_unescaped():
    # La prosa lleva <strong>/<code>: el traductor devuelve Markup, así que Jinja no
    # la escapa y las etiquetas llegan enteras al navegador.
    assert "<code>" in str(translate("<code>raw</code>", "ca"))


def test_every_language_is_a_real_catalog():
    assert set(LANGUAGES) == {"es", "ca"}
    assert all(value.strip() for value in CATALAN.values())
    normalized = [normalize(key) for key in CATALAN]
    assert len(normalized) == len(set(normalized))


@pytest.mark.parametrize(
    "label",
    [
        "Ruido de fondo",
        "Ocupación",
        "Errores/h",
        "Recepciones",
        "Minutos medidos",
        "PDR",
        "Observaciones",
        "Observadores",
        "sin atribuir",
        "no aparece en el segundo período",
        "no estaba en el primer período",
        "hace segundos",
        "hace {n} min",
        "hace {n} h",
        "hace {n} d",
    ],
)
def test_labels_generated_in_python_are_in_the_catalog(label):
    # Estos no viven en las plantillas (salen de query.py / periods.py / los filtros),
    # así que el escaneo de literales no los ve y hay que fijarlos aquí.
    assert label in CATALAN


def test_every_literal_in_the_templates_has_a_translation():
    catalog = {normalize(key) for key in CATALAN}
    files = sorted((APP_DIR / "templates").glob("*.html")) + [
        APP_DIR / "web" / "main.py"
    ]

    missing = [
        f"{path.name}: {normalize(match.group(2))}"
        for path in files
        for match in LITERAL.finditer(path.read_text(encoding="utf-8"))
        if normalize(match.group(2)) not in catalog
    ]

    assert not missing, missing
