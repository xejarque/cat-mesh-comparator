from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.db import connect, init_db, insert_status
from app.mqtt.collector import (
    MAX_BUFFER,
    Buffers,
    TimelineCache,
    _prime_timeline,
    flush,
    handle_message,
    should_flush,
)
from tests.helpers import RAW_ADVERT, mk_status, packet_message, status_message

SLOT1_ID = "869.4310|62.500|11|5"


def _recent_iso(offset_seconds: int = 0) -> str:
    from datetime import datetime, timezone

    moment = datetime.fromtimestamp(time.time() - offset_seconds, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_tls=False,
        mqtt_username=None,
        mqtt_password=None,
        topic_prefix="meshcore",
        iata_filter=None,
        db_path=tmp_path / "collector.db",
        status_max_gap_s=600,
        raw_retention_days=30,
    )


@pytest.fixture()
def conn(settings):
    connection = connect(settings.db_path)
    init_db(connection)
    yield connection
    connection.close()


def test_packet_is_attributed_to_the_observers_preset(conn, settings):
    buffers = Buffers()
    cache = TimelineCache()

    handle_message(
        *status_message("2026-09-14T10:00:00Z", "869.431,62.5,11,5"),
        settings.topic_prefix,
        buffers,
    )
    handle_message(
        *packet_message("2026-09-14T10:00:10Z", packet_hash="aa"),
        settings.topic_prefix,
        buffers,
    )

    assert flush(conn, buffers, cache, settings) == 1
    row = conn.execute("SELECT preset_id, preset_uncertain FROM raw_packets").fetchone()
    assert row["preset_id"] == SLOT1_ID
    assert row["preset_uncertain"] == 0


def test_packet_without_status_is_stored_unattributed(conn, settings):
    buffers = Buffers()
    cache = TimelineCache()

    handle_message(
        *packet_message("2026-09-14T10:00:10Z"),
        settings.topic_prefix,
        buffers,
    )

    assert flush(conn, buffers, cache, settings) == 1
    row = conn.execute("SELECT preset_id FROM raw_packets").fetchone()
    assert row["preset_id"] is None


def test_status_is_persisted_alongside_packets(conn, settings):
    buffers = Buffers()
    cache = TimelineCache()

    handle_message(
        *status_message("2026-09-14T10:00:00Z", noise_floor=-121),
        settings.topic_prefix,
        buffers,
    )
    flush(conn, buffers, cache, settings)

    row = conn.execute("SELECT noise_floor FROM observer_status").fetchone()
    assert row["noise_floor"] == -121


def test_unrecognised_payloads_are_counted_not_silently_dropped(settings):
    buffers = Buffers()

    handle_message("meshcore/BAR/AA/packets", "no-es-json", settings.topic_prefix, buffers)
    handle_message("meshcore/BAR/AA/packets", b"\xff\xfe", settings.topic_prefix, buffers)

    assert buffers.errors["payload_not_json"] == 1
    assert buffers.errors["payload_not_utf8"] == 1
    assert buffers.pending() == 0


def test_ignored_topics_do_not_count_as_errors(settings):
    buffers = Buffers()

    handle_message("meshcore/BAR/AA/raw", "f59301", settings.topic_prefix, buffers)

    assert sum(buffers.errors.values()) == 0
    assert buffers.pending() == 0


def test_flush_on_empty_buffer_is_a_noop(conn, settings):
    buffers = Buffers()

    assert flush(conn, buffers, TimelineCache(), settings) == 0


def test_buffer_is_drained_after_flush(conn, settings):
    buffers = Buffers()
    cache = TimelineCache()

    handle_message(*packet_message("2026-09-14T10:00:10Z"), settings.topic_prefix, buffers)
    handle_message(*packet_message("2026-09-14T10:00:11Z"), settings.topic_prefix, buffers)

    assert buffers.pending() == 2
    flush(conn, buffers, cache, settings)
    assert buffers.pending() == 0

    total = conn.execute("SELECT COUNT(*) AS n FROM raw_packets").fetchone()["n"]
    assert total == 2


def test_timeline_cache_rebuilds_only_what_changed(settings):
    from tests.helpers import mk_status

    cache = TimelineCache()
    cache.add(mk_status("2026-09-14T10:00:00Z", "869.431,62.5,11,5", pubkey="OBS1"))
    cache.add(mk_status("2026-09-14T10:00:00Z", "869.619,62.5,11,5", pubkey="OBS2"))

    timelines = cache.timelines()
    assert set(timelines) == {"OBS1", "OBS2"}
    assert cache._dirty == set()

    cache.add(mk_status("2026-09-14T10:05:00Z", "869.619,62.5,11,5", pubkey="OBS2"))
    refreshed = cache.timelines()
    assert len(refreshed["OBS2"].timestamps) == 2
    assert len(refreshed["OBS1"].timestamps) == 1


def test_flush_trigger_uses_time_not_just_message_arrival():
    # Con tráfico continuo el timeout de lectura no salta nunca, así que la regla
    # temporal es la que acota lo que se pierde si el proceso muere de golpe.
    assert should_flush(pending=0, seconds_since_flush=99.0, interval_s=5.0) is False
    assert should_flush(pending=1, seconds_since_flush=4.9, interval_s=5.0) is False
    assert should_flush(pending=1, seconds_since_flush=5.0, interval_s=5.0) is True
    assert should_flush(pending=MAX_BUFFER, seconds_since_flush=0.0, interval_s=5.0) is True


def test_interval_zero_means_write_through():
    # Escritura directa: un commit por mensaje, sin esperar a nada.
    assert should_flush(pending=1, seconds_since_flush=0.0, interval_s=0) is True
    assert should_flush(pending=99, seconds_since_flush=0.0, interval_s=0) is True
    assert should_flush(pending=0, seconds_since_flush=0.0, interval_s=0) is False


def test_without_priming_a_restart_loses_the_attribution(conn, settings):
    # El status está en la BD de una ejecución anterior, pero la caché arranca vacía
    # y el paquete llega antes de recibir ningún /status en vivo.
    insert_status(conn, mk_status(_recent_iso(120), "869.431,62.5,7,6"))
    buffers = Buffers()
    handle_message(*packet_message(_recent_iso(60)), settings.topic_prefix, buffers)

    flush(conn, buffers, TimelineCache(), settings)

    row = conn.execute("SELECT preset_id FROM raw_packets").fetchone()
    assert row["preset_id"] is None


def test_priming_the_timeline_recovers_attribution_after_restart(conn, settings):
    insert_status(conn, mk_status(_recent_iso(120), "869.431,62.5,7,6"))
    buffers = Buffers()
    handle_message(*packet_message(_recent_iso(60)), settings.topic_prefix, buffers)

    cache = TimelineCache()
    assert _prime_timeline(conn, cache) == 1
    assert flush(conn, buffers, cache, settings) == 1

    row = conn.execute("SELECT preset_id FROM raw_packets").fetchone()
    assert row["preset_id"] == "869.4310|62.500|7|6"


def test_decoded_enrichment_is_persisted(conn, settings):
    buffers = Buffers()
    cache = TimelineCache()

    handle_message(*status_message("2026-09-14T10:00:00Z"), settings.topic_prefix, buffers)
    handle_message(
        *packet_message("2026-09-14T10:00:10Z", raw=RAW_ADVERT),
        settings.topic_prefix,
        buffers,
    )

    assert flush(conn, buffers, cache, settings) == 1
    row = conn.execute(
        "SELECT path_length, payload_type, is_valid FROM raw_packets"
    ).fetchone()
    assert row["payload_type"] == "Advert"
    assert row["path_length"] == 1
    assert row["is_valid"] == 1
    assert buffers.decode_failures == 0


def test_decoding_can_be_switched_off(conn, settings):
    from dataclasses import replace

    disabled = replace(settings, decode_packets=False)
    buffers = Buffers()

    handle_message(
        *packet_message("2026-09-14T10:00:10Z", raw=RAW_ADVERT),
        disabled.topic_prefix,
        buffers,
    )
    flush(conn, buffers, TimelineCache(), disabled)

    row = conn.execute(
        "SELECT payload_type, path_length FROM raw_packets"
    ).fetchone()
    assert row["payload_type"] is None
    assert row["path_length"] is None


def test_undecodable_raw_is_counted_but_the_packet_is_still_stored(conn, settings):
    buffers = Buffers()

    handle_message(
        *packet_message("2026-09-14T10:00:10Z", packet_hash="aa", raw="ZZZZZZZZ"),
        settings.topic_prefix,
        buffers,
    )

    assert flush(conn, buffers, TimelineCache(), settings) == 1
    assert buffers.decode_failures == 1
    row = conn.execute("SELECT payload_type, packet_hash FROM raw_packets").fetchone()
    assert row["payload_type"] is None
    assert row["packet_hash"] == "AA"
