from __future__ import annotations

import pytest

from app.db import connect, init_db, insert_packets, insert_status
from app.models import PresetKey
from app.rollup import (
    SECONDS_PER_DAY,
    SECONDS_PER_HOUR,
    consolidate,
    refresh_data_quality,
    rollup_window,
    rollup_with_own_connection,
    run_rollup,
)
from tests.helpers import attribute, epoch, mk_packet, mk_status

SLOT1 = PresetKey(869.431, 62.5, 7, 6)
SLOT4 = PresetKey(869.618, 62.5, 7, 6)


@pytest.fixture()
def conn(tmp_path):
    connection = connect(tmp_path / "rollup.db")
    init_db(connection)
    yield connection
    connection.close()


def test_rollup_window_writes_the_three_tables(conn):
    insert_status(conn, mk_status("2026-09-14T10:00:00Z", "869.431,62.5,7,6"))
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), SLOT1),
            attribute(mk_packet("2026-09-14T10:00:20Z", "OBS2", packet_hash="aa"), SLOT1),
            attribute(mk_packet("2026-09-14T10:00:30Z", "OBS2", packet_hash="bb"), SLOT1),
        ],
    )

    (
        preset_minutes,
        channel_minutes,
        observer_minutes,
        pair_minutes,
        uncertain,
        unattributed,
    ) = rollup_window(conn, 0)

    assert (preset_minutes, channel_minutes, pair_minutes, uncertain, unattributed) == (
        1,
        1,
        1,
        0,
        0,
    )
    # AA11BB22 (por su status) + OBS1 + OBS2 (por sus paquetes)
    assert observer_minutes == 3

    preset = conn.execute(
        "SELECT pkts, uniq_hashes, observers FROM preset_minute"
    ).fetchone()
    assert preset["pkts"] == 3
    assert preset["uniq_hashes"] == 2
    assert preset["observers"] == 2

    channel = conn.execute(
        "SELECT channel_id, freq_mhz, bw_khz, sf, pkts, observers, crs "
        "FROM channel_minute"
    ).fetchone()
    assert channel["channel_id"] == "869.4310|62.500|7"
    assert (channel["freq_mhz"], channel["bw_khz"], channel["sf"]) == (869.431, 62.5, 7)
    assert channel["pkts"] == 3
    assert channel["observers"] == 2
    assert channel["crs"] == "6"

    pair = conn.execute(
        "SELECT channel_id, obs_a, obs_b, heard_a, heard_b, both, pdr_pct FROM pair_minute"
    ).fetchone()
    assert pair["channel_id"] == "869.4310|62.500|7"
    assert (pair["obs_a"], pair["obs_b"]) == ("OBS1", "OBS2")
    assert (pair["heard_a"], pair["heard_b"], pair["both"]) == (1, 2, 1)
    assert pair["pdr_pct"] == pytest.approx(50.0)


def test_rollup_window_is_idempotent(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1),
            attribute(mk_packet("2026-09-14T10:00:20Z", packet_hash="bb"), SLOT1),
        ],
    )

    rollup_window(conn, 0)
    rollup_window(conn, 0)
    rollup_window(conn, 0)

    rows = conn.execute("SELECT pkts FROM preset_minute").fetchall()
    assert len(rows) == 1
    # Recalcular no debe duplicar el recuento: es INSERT OR REPLACE.
    assert rows[0]["pkts"] == 2


def test_rollup_window_separates_minutes(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1),
            attribute(mk_packet("2026-09-14T10:01:10Z", packet_hash="bb"), SLOT1),
        ],
    )

    rollup_window(conn, 0)

    rows = conn.execute(
        "SELECT minute_ts, pkts FROM preset_minute ORDER BY minute_ts"
    ).fetchall()
    assert [row["minute_ts"] for row in rows] == [
        epoch("2026-09-14T10:00:00Z"),
        epoch("2026-09-14T10:01:00Z"),
    ]
    assert all(row["pkts"] == 1 for row in rows)


def test_uncertain_and_unattributed_stay_out_of_preset_minutes(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1),
            attribute(
                mk_packet("2026-09-14T10:00:20Z", packet_hash="bb"),
                SLOT1,
                uncertain=True,
            ),
            attribute(mk_packet("2026-09-14T10:00:30Z", packet_hash="cc"), None),
        ],
    )

    _, _, _, _, uncertain, unattributed = rollup_window(conn, 0)

    assert uncertain == 1
    assert unattributed == 1
    row = conn.execute("SELECT pkts FROM preset_minute").fetchone()
    assert row["pkts"] == 1


def test_consolidate_groups_minutes_into_hours(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1),
            attribute(mk_packet("2026-09-14T10:30:10Z", packet_hash="bb"), SLOT1),
            attribute(mk_packet("2026-09-14T11:05:10Z", packet_hash="cc"), SLOT1),
        ],
    )
    rollup_window(conn, 0)

    assert consolidate(conn, SECONDS_PER_HOUR, 0) == 2

    rows = conn.execute(
        "SELECT hour_ts, pkts, uniq_hashes FROM preset_hour ORDER BY hour_ts"
    ).fetchall()
    assert [row["hour_ts"] for row in rows] == [
        epoch("2026-09-14T10:00:00Z"),
        epoch("2026-09-14T11:00:00Z"),
    ]
    assert [row["pkts"] for row in rows] == [2, 1]
    assert [row["uniq_hashes"] for row in rows] == [2, 1]


def test_consolidate_groups_hours_into_days(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1),
            attribute(mk_packet("2026-09-14T22:00:10Z", packet_hash="bb"), SLOT1),
            attribute(mk_packet("2026-09-15T01:00:10Z", packet_hash="cc"), SLOT1),
        ],
    )
    rollup_window(conn, 0)

    assert consolidate(conn, SECONDS_PER_DAY, 0) == 2

    rows = conn.execute(
        "SELECT day_ts, pkts FROM preset_day ORDER BY day_ts"
    ).fetchall()
    assert [row["day_ts"] for row in rows] == [
        epoch("2026-09-14T00:00:00Z"),
        epoch("2026-09-15T00:00:00Z"),
    ]
    assert [row["pkts"] for row in rows] == [2, 1]


def test_consolidate_rejects_unknown_bucket_size(conn):
    with pytest.raises(ValueError):
        consolidate(conn, 60, 0)


def test_data_quality_separates_each_cause(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1),
            attribute(
                mk_packet("2026-09-14T10:00:20Z", packet_hash="bb"),
                SLOT1,
                uncertain=True,
            ),
            attribute(mk_packet("2026-09-14T10:00:30Z", packet_hash="cc"), None),
            # El mismo hash bajo dos presets distintos: una de las dos está mal.
            attribute(mk_packet("2026-09-14T10:00:40Z", packet_hash="dd"), SLOT1),
            attribute(mk_packet("2026-09-14T10:00:50Z", packet_hash="dd"), SLOT4),
        ],
    )

    row = refresh_data_quality(conn, epoch("2026-09-14T00:00:00Z"))

    assert row.unattributed_packets == 1
    assert row.uncertain_packets == 1
    assert row.hash_conflicts == 1
    # AA11BB22 publica paquetes pero no tiene ningún status con radio.
    assert row.observers_without_status == 1


def test_data_quality_is_persisted_per_day(conn):
    refresh_data_quality(conn, epoch("2026-09-14T00:00:00Z"))

    rows = conn.execute("SELECT day_ts, unattributed_packets FROM data_quality").fetchall()
    assert len(rows) == 1
    assert rows[0]["day_ts"] == epoch("2026-09-14T00:00:00Z")
    assert rows[0]["unattributed_packets"] == 0


def test_data_quality_does_not_leak_across_days(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T23:59:50Z", packet_hash="aa"), None),
            attribute(mk_packet("2026-09-15T00:00:10Z", packet_hash="bb"), SLOT1),
        ],
    )

    first = refresh_data_quality(conn, epoch("2026-09-14T00:00:00Z"))
    second = refresh_data_quality(conn, epoch("2026-09-15T00:00:00Z"))

    assert first.unattributed_packets == 1
    assert second.unattributed_packets == 0


def test_different_coding_rate_is_not_a_hash_conflict(conn):
    # Hallazgo del broker real: la CR viaja en la cabecera LoRa, así que dos
    # receptores con CR distinto sobre la misma frecuencia y SF decodifican la misma
    # transmisión. Antes esto contaba como conflicto y daba 14 falsos positivos.
    insert_packets(
        conn,
        [
            attribute(
                mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"),
                PresetKey(869.618, 62.5, 7, 6),
            ),
            attribute(
                mk_packet("2026-09-14T10:00:11Z", "OBS2", packet_hash="aa"),
                PresetKey(869.618, 62.5, 7, 8),
            ),
        ],
    )

    row = refresh_data_quality(conn, epoch("2026-09-14T00:00:00Z"))

    assert row.hash_conflicts == 0


def test_different_frequency_is_a_hash_conflict(conn):
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), SLOT1),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS2", packet_hash="aa"), SLOT4),
        ],
    )

    row = refresh_data_quality(conn, epoch("2026-09-14T00:00:00Z"))

    assert row.hash_conflicts == 1


def test_run_rollup_default_window_only_looks_back(conn):
    insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1)],
    )

    # La ventana por defecto son 30 minutos, así que a las 12:00 ya no lo alcanza.
    report = run_rollup(conn, now=epoch("2026-09-14T12:00:00Z"), retention_days=0)

    assert report.preset_minutes == 0


def test_run_rollup_with_since_zero_recomputes_the_whole_history(conn):
    insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1)],
    )

    report = run_rollup(
        conn, now=epoch("2026-09-14T12:00:00Z"), since_ts=0, retention_days=0
    )

    assert report.preset_minutes == 1
    assert report.hours == 1


def test_rollup_opens_its_own_connection_for_the_worker_thread(tmp_path):
    # El rollup corre en un hilo aparte, y una conexión SQLite está atada a su hilo:
    # compartir la del colector fallaba con ProgrammingError.
    from datetime import datetime, timezone
    import time

    moment = datetime.fromtimestamp(time.time() - 60, timezone.utc)
    path = tmp_path / "threaded.db"
    setup = connect(path)
    init_db(setup)
    insert_packets(
        setup,
        [
            attribute(
                mk_packet(moment.strftime("%Y-%m-%dT%H:%M:%SZ"), packet_hash="aa"),
                SLOT1,
            )
        ],
    )
    setup.close()

    report = rollup_with_own_connection(path, retention_days=0)

    assert report.preset_minutes == 1


def test_run_rollup_on_empty_database_is_harmless(conn):
    report = run_rollup(conn, now=epoch("2026-09-14T10:00:00Z"), retention_days=30)

    assert report.preset_minutes == 0
    assert report.pair_minutes == 0
    assert report.pruned_packets == 0
    # Sin paquetes no hay ningún día que refrescar: no se rellena el calendario.
    assert report.days_with_quality == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM data_quality").fetchone()["n"] == 0


def test_full_rollup_only_touches_days_with_data(conn):
    insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT1)],
    )

    report = run_rollup(
        conn, now=epoch("2026-09-14T12:00:00Z"), since_ts=0, retention_days=0
    )

    assert report.days_with_quality == 1
    rows = conn.execute("SELECT day_ts FROM data_quality").fetchall()
    assert [row["day_ts"] for row in rows] == [epoch("2026-09-14T00:00:00Z")]
