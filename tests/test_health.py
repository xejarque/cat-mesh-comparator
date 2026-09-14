from __future__ import annotations

from app.db import connect, init_db, insert_packets
from app.health import collector_health, is_stale, last_packet_ts
from app.models import PresetKey
from tests.helpers import attribute, epoch, mk_packet

SLOT = PresetKey(869.618, 62.5, 7, 6)


def test_is_stale_compares_against_the_threshold():
    now = 1_000_000

    assert is_stale(now - 100, now, 1800) is False
    assert is_stale(now - 1801, now, 1800) is True


def test_no_packets_yet_is_not_stale():
    # Un colector recién arrancado no ha escrito nada: eso no es estar parado.
    assert is_stale(None, 1_000_000, 1800) is False


def test_health_without_packets_is_not_started(tmp_path):
    conn = connect(tmp_path / "h.db")
    init_db(conn)

    health = collector_health(conn, now=1_700_000_000)

    assert health.started is False
    assert health.age_seconds is None
    assert health.stale is False
    conn.close()


def test_health_reports_the_age(tmp_path):
    conn = connect(tmp_path / "h.db")
    init_db(conn)
    insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), SLOT)],
    )
    last = epoch("2026-09-14T10:00:10Z")

    health = collector_health(conn, now=last + 120, stale_seconds=1800)
    assert health.age_seconds == 120
    assert health.stale is False

    stale = collector_health(conn, now=last + 3600, stale_seconds=1800)
    assert stale.stale is True
    conn.close()


def test_last_packet_ts_reads_the_newest(tmp_path):
    conn = connect(tmp_path / "h.db")
    init_db(conn)
    assert last_packet_ts(conn) is None

    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), SLOT),
            attribute(mk_packet("2026-09-14T10:05:10Z", "OBS1", packet_hash="bb"), SLOT),
        ],
    )

    assert last_packet_ts(conn) == epoch("2026-09-14T10:05:10Z")
    conn.close()
