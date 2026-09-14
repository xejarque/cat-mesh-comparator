from __future__ import annotations

import pytest

from app.db import connect, ensure_preset, init_db, insert_packets, insert_status
from app.db import SCHEMA_VERSION, prune_raw_packets
from app.models import PresetKey
from app.presets import SEED_PRESETS, preset_label
from tests.helpers import attribute, epoch, mk_packet, mk_status

SLOT1 = PresetKey(869.431, 62.5, 11, 5)


@pytest.fixture()
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    init_db(connection)
    yield connection
    connection.close()


def test_init_db_seeds_the_known_presets(conn):
    freqs = {
        row["freq_mhz"]
        for row in conn.execute("SELECT freq_mhz FROM presets").fetchall()
    }
    assert {869.432, 869.493, 869.556, 869.618, 869.525} <= freqs

    slot4 = conn.execute(
        "SELECT label FROM presets WHERE freq_mhz = 869.618 AND sf = 7 AND cr = 6"
    ).fetchone()
    assert slot4["label"] == "Slot 4 · 869.618 MHz · BW62.5 · SF7 · CR4/6"


def test_preset_label_always_carries_the_parameters():
    """La etiqueta tiene que describirse sola.

    Regresión: los alias se escribían a mano y unos llevaban SF/CR y otros no, así
    que dos filas de la misma tabla se leían con criterios distintos y no se podían
    comparar. En 869.618 conviven tres configuraciones, y el nombre no las distingue.
    """
    for preset in SEED_PRESETS:
        assert preset_label(preset) == (
            f"{SEED_PRESETS[preset]} · {preset.freq_mhz:g} MHz"
            f" · BW{preset.bw_khz:g} · SF{preset.sf} · CR4/{preset.cr}"
        )


def test_a_new_preset_without_alias_is_labelled_with_its_parameters(conn):
    preset = PresetKey(869.700, 125.0, 9, 8)
    assert preset_label(preset) == "869.7 MHz · BW125 · SF9 · CR4/8"


def test_stale_labels_are_refreshed_on_startup(tmp_path):
    """Las etiquetas viejas se ponen al día al arrancar, no se quedan para siempre."""
    path = tmp_path / "stale.db"
    connection = connect(path)
    init_db(connection)
    connection.execute("UPDATE presets SET label = 'texto viejo'")
    connection.commit()
    connection.close()

    connection = connect(path)
    init_db(connection)
    labels = {row["label"] for row in connection.execute("SELECT label FROM presets")}
    connection.close()
    assert "texto viejo" not in labels


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    for _ in range(2):
        connection = connect(path)
        init_db(connection)
        connection.close()

    connection = connect(path)
    count = connection.execute("SELECT COUNT(*) AS n FROM presets").fetchone()["n"]
    connection.close()
    assert count == len(SEED_PRESETS)


def test_unknown_preset_is_discovered_from_data(conn):
    preset_id = ensure_preset(conn, PresetKey(869.700, 125.0, 9, 8))

    row = conn.execute(
        "SELECT label FROM presets WHERE preset_id = ?", (preset_id,)
    ).fetchone()
    assert "869.7" in row["label"]


def test_status_and_packet_roundtrip(conn):
    insert_status(conn, mk_status("2026-09-14T10:00:00Z", noise_floor=-118))
    written = insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1)],
    )

    assert written == 1
    stored = conn.execute(
        "SELECT preset_id, packet_hash, snr_x4, preset_uncertain FROM raw_packets"
    ).fetchone()
    assert stored["preset_id"] == "869.4310|62.500|11|5"
    assert stored["packet_hash"] == "AA"
    assert stored["snr_x4"] == 40
    assert stored["preset_uncertain"] == 0


def test_packets_from_an_observer_without_status_store_a_null_preset(conn):
    insert_packets(conn, [attribute(mk_packet("2026-09-14T10:00:10Z"), None)])

    row = conn.execute("SELECT preset_id FROM raw_packets").fetchone()
    assert row["preset_id"] is None


def test_uncertain_flag_is_persisted(conn):
    insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z"), SLOT1, uncertain=True)],
    )

    row = conn.execute("SELECT preset_uncertain FROM raw_packets").fetchone()
    assert row["preset_uncertain"] == 1


def test_duplicate_status_is_ignored(conn):
    first = mk_status("2026-09-14T10:00:00Z", noise_floor=-118)
    second = mk_status("2026-09-14T10:00:00Z", noise_floor=-90)
    insert_status(conn, first)
    insert_status(conn, second)

    rows = conn.execute("SELECT noise_floor FROM observer_status").fetchall()
    assert len(rows) == 1
    assert rows[0]["noise_floor"] == -118


def test_observer_window_is_monotonic_even_out_of_order(conn):
    insert_status(conn, mk_status("2026-09-14T10:00:00Z"))
    insert_status(conn, mk_status("2026-09-14T09:00:00Z"))

    row = conn.execute("SELECT first_seen, last_seen FROM observers").fetchone()
    assert row["first_seen"] == epoch("2026-09-14T09:00:00Z")
    assert row["last_seen"] == epoch("2026-09-14T10:00:00Z")


def test_init_db_adds_the_new_columns_to_an_existing_v1_database(tmp_path):
    import sqlite3

    # Una base v1 real trae todo el esquema v1 y solo le faltan las columnas nuevas.
    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE raw_packets (
            id               INTEGER PRIMARY KEY,
            ts               INTEGER NOT NULL,
            observer_pubkey  TEXT    NOT NULL,
            preset_id        TEXT,
            preset_uncertain INTEGER NOT NULL DEFAULT 0,
            packet_hash      TEXT,
            snr_x4           INTEGER,
            rssi             INTEGER,
            packet_type      INTEGER,
            route            TEXT,
            payload_len      INTEGER,
            raw_hex          TEXT
        );
        CREATE INDEX idx_raw_hash ON raw_packets (packet_hash);
        """
    )
    legacy.close()

    connection = connect(path)
    init_db(connection)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(raw_packets)")
    }
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    connection.close()

    assert {"path_length", "payload_type", "is_valid"} <= columns
    assert version == SCHEMA_VERSION


def test_migration_to_v3_rebuilds_pair_minute_by_channel(tmp_path):
    import sqlite3

    # En la v2 pair_minute estaba indexado por preset; en la v3, por canal físico.
    path = tmp_path / "v2.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE pair_minute (
            minute_ts INTEGER NOT NULL,
            preset_id TEXT    NOT NULL,
            obs_a     TEXT    NOT NULL,
            obs_b     TEXT    NOT NULL,
            heard_a   INTEGER NOT NULL,
            heard_b   INTEGER NOT NULL,
            both      INTEGER NOT NULL,
            pdr_pct   REAL    NOT NULL,
            PRIMARY KEY (minute_ts, preset_id, obs_a, obs_b)
        );
        PRAGMA user_version = 2;
        """
    )
    legacy.close()

    connection = connect(path)
    init_db(connection)
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(pair_minute)")
    }
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    connection.close()

    assert "channel_id" in columns
    assert "preset_id" not in columns
    assert version == SCHEMA_VERSION


def test_statuses_between_rebuilds_the_preset_and_filters_by_window(conn):
    from app.db import statuses_between

    insert_status(conn, mk_status("2026-09-14T10:00:00Z", "869.431,62.5,7,6"))
    insert_status(conn, mk_status("2026-09-14T09:00:00Z", "869.618,62.5,7,6"))

    todos = statuses_between(conn, since_ts=0)
    assert len(todos) == 2
    assert {s.radio_raw for s in todos} == {"869.431,62.5,7,6", "869.618,62.5,7,6"}
    assert {s.preset for s in todos} == {
        PresetKey(869.431, 62.5, 7, 6),
        PresetKey(869.618, 62.5, 7, 6),
    }

    recent = statuses_between(conn, since_ts=epoch("2026-09-14T09:30:00Z"))
    assert [s.ts for s in recent] == [epoch("2026-09-14T10:00:00Z")]

    bounded = statuses_between(
        conn,
        since_ts=epoch("2026-09-14T09:30:00Z"),
        until_ts=epoch("2026-09-14T10:30:00Z"),
    )
    assert [s.ts for s in bounded] == [epoch("2026-09-14T10:00:00Z")]


def test_statuses_between_keeps_offline_rows_without_preset(conn):
    from app.db import statuses_between

    insert_status(conn, mk_status("2026-09-14T10:00:00Z", radio=""))

    rows = statuses_between(conn, since_ts=0)
    assert len(rows) == 1
    assert rows[0].preset is None


def test_observer_name_is_taken_from_the_payload(conn):
    # El broker publica el nombre humano en `origin`. Sin guardarlo, la web solo
    # puede enseñar el prefijo del pubkey.
    insert_status(conn, mk_status("2026-09-14T10:00:00Z", pubkey="OBS1"))

    row = conn.execute("SELECT name FROM observers WHERE pubkey = 'OBS1'").fetchone()
    assert row["name"] == "Obs"


def test_packet_also_can_fill_in_the_observer_name(conn):
    insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z", "OBS9", packet_hash="aa"), None)],
    )

    row = conn.execute("SELECT name FROM observers WHERE pubkey = 'OBS9'").fetchone()
    assert row["name"] == "Obs"


def test_statuses_between_keeps_the_observer_name(conn):
    from app.db import statuses_between

    insert_status(conn, mk_status("2026-09-14T10:00:00Z", pubkey="OBS1"))

    rows = statuses_between(conn, since_ts=0)
    assert [row.name for row in rows] == ["Obs"]


def test_newer_position_wins(conn):
    from app.db import upsert_node_position

    upsert_node_position(conn, "N1", 41.0, 2.0, seen_ts=1000)
    upsert_node_position(conn, "N1", 42.0, 3.0, seen_ts=2000)

    row = conn.execute(
        "SELECT lat, lon, seen_ts FROM node_positions WHERE pubkey = 'N1'"
    ).fetchone()
    assert (row["lat"], row["lon"]) == (42.0, 3.0)
    assert row["seen_ts"] == 2000


def test_an_old_advert_arriving_late_does_not_overwrite(conn):
    # Los adverts llegan cuando alguien los oye, así que uno viejo puede aparecer
    # después. No debe pisar una posición más reciente.
    from app.db import upsert_node_position

    upsert_node_position(conn, "N1", 41.0, 2.0, seen_ts=2000)
    upsert_node_position(conn, "N1", 42.0, 3.0, seen_ts=1000)

    assert (
        conn.execute("SELECT lat FROM node_positions WHERE pubkey = 'N1'").fetchone()[
            "lat"
        ]
        == 41.0
    )


def test_prune_removes_only_old_packets(conn):
    import time
    from datetime import datetime, timezone

    def iso(offset_seconds: int) -> str:
        moment = datetime.fromtimestamp(time.time() - offset_seconds, timezone.utc)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    insert_packets(
        conn,
        [
            attribute(mk_packet(iso(40 * 86_400)), SLOT1),
            attribute(mk_packet(iso(3_600)), SLOT1),
        ],
    )

    assert prune_raw_packets(conn, retention_days=30) == 1
    remaining = conn.execute("SELECT COUNT(*) AS n FROM raw_packets").fetchone()["n"]
    assert remaining == 1
