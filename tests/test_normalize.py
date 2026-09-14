from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Packet, ParseError, PresetKey, Status
from app.mqtt.normalize import normalize_message, parse_radio, parse_ts, parse_topic
from tests.helpers import mk_packet, mk_status


def test_parse_topic_uppercases_its_parts():
    info = parse_topic("meshcore/bar/aa11bb22/packets")
    assert info is not None
    assert (info.iata, info.pubkey, info.leaf) == ("BAR", "AA11BB22", "packets")


@pytest.mark.parametrize(
    "topic",
    ["", "meshcore/BAR/packets", "otro/BAR/AA11/packets", "meshcore/BAR/AA11/packets/extra"],
)
def test_parse_topic_rejects_malformed_topics(topic):
    assert parse_topic(topic) is None


def test_parse_ts_accepts_iso_variants_and_epoch():
    expected = int(datetime(2026, 9, 14, 18, 30, tzinfo=timezone.utc).timestamp())
    assert parse_ts("2026-09-14T18:30:00.000000") == expected
    assert parse_ts("2026-09-14T18:30:00Z") == expected
    assert parse_ts("2026-09-14T20:30:00+02:00") == expected
    assert parse_ts(1757874600) == 1757874600
    assert parse_ts("1757874600") == 1757874600


@pytest.mark.parametrize("value", [None, "", "no-es-fecha", {}, True])
def test_parse_ts_rejects_garbage(value):
    assert parse_ts(value) is None


def test_parse_radio_reads_mhz():
    assert parse_radio("869.525,62.5,11,5") == (869.525, 62.5, 11, 5)


def test_parse_radio_normalises_khz_and_hz():
    assert parse_radio("869525,62500,11,5") == (869.525, 62.5, 11, 5)


@pytest.mark.parametrize("value", [None, "", "869.525,62.5", "a,b,c,d"])
def test_parse_radio_handles_bad_input(value):
    assert parse_radio(value) == (None, None, None, None)


# Los tres casos siguientes salen del broker real de Cataluña, no de la spec.


def test_parse_radio_reads_chip_prefix_and_slashes():
    assert parse_radio("SX1262 869.618/62/7/6") == (869.618, 62.5, 7, 6)


def test_parse_radio_canonicalises_float32_frequency():
    # 869.618 guardado como float32 llega como 869.6179809: debe agruparse igual.
    assert parse_radio("869.6179809,62.5,7,6") == parse_radio("869.618,62.5,7,6")


def test_parse_radio_snaps_bandwidth_to_the_lora_standard_set():
    assert parse_radio("869.618,62,7,6")[1] == 62.5


def test_packet_coerces_stringified_numbers():
    packet = mk_packet(
        "2026-09-14T18:30:00.000000",
        packet_hash="a1b2c3d4",
        snr="12.5",
        rssi="-65",
        packet_type="4",
    )
    assert isinstance(packet, Packet)
    assert packet.snr_x4 == 50
    assert packet.rssi == -65
    assert packet.packet_type == 4
    assert packet.payload_len == 32
    assert packet.packet_hash == "A1B2C3D4"
    assert packet.observer_pubkey == "AA11BB22"
    assert packet.iata == "BAR"


def test_packet_without_snr_keeps_none():
    packet = mk_packet("2026-09-14T18:30:00.000000", snr=None, rssi=None)
    assert packet.snr_x4 is None
    assert packet.rssi is None


def test_status_parses_radio_and_stats():
    status = mk_status("2026-09-14T18:30:00.000000", noise_floor=-118, recv_errors=7)
    assert isinstance(status, Status)
    assert status.preset == PresetKey(869.525, 62.5, 11, 5)
    assert status.noise_floor == -118
    assert status.recv_errors == 7
    assert status.online is True


def test_status_without_radio_has_no_preset():
    status = mk_status("2026-09-14T18:30:00.000000", radio="")
    assert status.preset is None


def test_empty_packet_frame_is_rejected():
    # Algunos nodos emiten tramas con SNR/RSSI/hash/raw en blanco.
    payload = (
        '{"timestamp": "2026-09-14T18:30:00Z", "type": "PACKET",'
        ' "SNR": "", "RSSI": "", "hash": "", "raw": "", "len": "0"}'
    )
    result = normalize_message("meshcore/BAR/AA/packets", payload)
    assert isinstance(result, ParseError)
    assert result.reason == "packet_without_data"


def test_non_json_payload_is_reported_and_not_crashed():
    result = normalize_message("meshcore/BAR/AA/packets", b"\x01\x02\x03not-json")
    assert isinstance(result, ParseError)
    assert result.reason == "payload_not_json"


def test_non_utf8_payload_is_reported():
    result = normalize_message("meshcore/BAR/AA/packets", b"\xff\xfe")
    assert isinstance(result, ParseError)
    assert result.reason == "payload_not_utf8"


def test_ignored_leaf_returns_none():
    assert normalize_message("meshcore/BAR/AA/raw", "{}") is None
    assert normalize_message("meshcore/BAR/AA/neighbors", "{}") is None


def test_missing_timestamp_is_reported():
    result = normalize_message("meshcore/BAR/AA/packets", '{"type": "PACKET"}')
    assert isinstance(result, ParseError)
    assert result.reason == "missing_timestamp"


def test_json_array_payload_is_rejected():
    result = normalize_message("meshcore/BAR/AA/packets", "[1, 2, 3]")
    assert isinstance(result, ParseError)
    assert result.reason == "payload_not_object"
