from __future__ import annotations

from app.attribution import attribute_packets, build_timelines, resolve
from app.models import PresetKey
from tests.helpers import epoch, mk_packet, mk_status

SLOT_A = PresetKey(869.525, 62.5, 11, 5)
SLOT_B = PresetKey(869.619, 62.5, 11, 5)
RADIO_A = "869.525,62.5,11,5"
RADIO_B = "869.619,62.5,11,5"

MAX_GAP = 600


def test_build_timelines_sorts_per_observer():
    timelines = build_timelines(
        [
            mk_status("2026-09-14T10:05:00Z", RADIO_B, pubkey="OBS2"),
            mk_status("2026-09-14T10:00:00Z", RADIO_A, pubkey="OBS2"),
        ]
    )
    timeline = timelines["OBS2"]
    assert timeline.timestamps == [
        epoch("2026-09-14T10:00:00Z"),
        epoch("2026-09-14T10:05:00Z"),
    ]
    assert timeline.presets == [SLOT_A, SLOT_B]


def test_build_timelines_keeps_last_status_on_timestamp_tie():
    timelines = build_timelines(
        [
            mk_status("2026-09-14T10:00:00Z", RADIO_A),
            mk_status("2026-09-14T10:00:00Z", RADIO_B),
        ]
    )
    assert timelines["AA11BB22"].presets == [SLOT_B]


def test_resolve_before_first_status_is_unknown_not_uncertain():
    timelines = build_timelines([mk_status("2026-09-14T10:00:00Z", RADIO_A)])
    result = resolve(timelines["AA11BB22"], epoch("2026-09-14T09:00:00Z"), MAX_GAP)
    assert result.preset is None
    assert result.uncertain is False


def test_resolve_uses_the_status_in_effect():
    timelines = build_timelines(
        [
            mk_status("2026-09-14T10:00:00Z", RADIO_A),
            mk_status("2026-09-14T10:05:00Z", RADIO_A),
        ]
    )
    result = resolve(timelines["AA11BB22"], epoch("2026-09-14T10:03:00Z"), MAX_GAP)
    assert result.preset == SLOT_A
    assert result.uncertain is False


def test_preset_change_window_is_flagged_uncertain():
    timelines = build_timelines(
        [
            mk_status("2026-09-14T10:00:00Z", RADIO_A),
            mk_status("2026-09-14T10:05:00Z", RADIO_B),
        ]
    )
    result = resolve(timelines["AA11BB22"], epoch("2026-09-14T10:03:00Z"), MAX_GAP)
    # El cambio ocurrió en algún punto entre ambos status, así que el dato es ambiguo.
    assert result.preset == SLOT_A
    assert result.uncertain is True


def test_stale_status_is_flagged_uncertain():
    timelines = build_timelines([mk_status("2026-09-14T10:00:00Z", RADIO_A)])
    result = resolve(timelines["AA11BB22"], epoch("2026-09-14T11:00:00Z"), MAX_GAP)
    assert result.preset == SLOT_A
    assert result.uncertain is True


def test_observer_without_status_stays_unattributed():
    timelines = build_timelines([])
    result = resolve(timelines.get("NADIE"), 1_700_000_000, MAX_GAP)
    assert result.preset is None
    assert result.uncertain is False


def test_offline_status_does_not_clear_the_known_preset():
    # Caso real: el observer publica un status "offline" sin `radio`. Si eso
    # entrara en la línea de tiempo, borraría el preset y dejaría sin atribuir
    # todo el tráfico posterior.
    timelines = build_timelines(
        [
            mk_status("2026-09-14T10:00:00Z", RADIO_A),
            mk_status("2026-09-14T10:02:00Z", radio=""),  # aviso de offline
        ]
    )

    result = resolve(timelines["AA11BB22"], epoch("2026-09-14T10:03:00Z"), MAX_GAP)
    assert result.preset == SLOT_A
    assert result.uncertain is False


def test_observer_with_only_offline_statuses_has_no_timeline():
    timelines = build_timelines([mk_status("2026-09-14T10:00:00Z", radio="")])

    assert timelines == {}
    assert resolve(timelines.get("AA11BB22"), 1_700_000_000, MAX_GAP).preset is None


def test_attribute_packets_end_to_end():
    timelines = build_timelines([mk_status("2026-09-14T10:00:00Z", RADIO_A)])
    rows = attribute_packets(
        [
            mk_packet("2026-09-14T10:01:00Z"),
            mk_packet("2026-09-14T09:00:00Z"),
        ],
        timelines,
        MAX_GAP,
    )
    assert rows[0].preset == SLOT_A
    assert rows[0].uncertain is False
    assert rows[1].preset is None
