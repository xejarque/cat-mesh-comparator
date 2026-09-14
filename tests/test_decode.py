from __future__ import annotations

import pytest

from app.decode import decode_packet, decoder_available
from app.models import DecodedInfo
from tests.helpers import RAW_ADVERT

needs_decoder = pytest.mark.skipif(
    not decoder_available(), reason="meshcoredecoder no está instalado"
)


@needs_decoder
def test_decodes_a_real_advert_from_the_broker():
    info = decode_packet(RAW_ADVERT)

    assert isinstance(info, DecodedInfo)
    assert info.payload_type == "Advert"
    assert info.path_length == 1
    assert info.is_valid is True


@needs_decoder
def test_decodes_sender_and_position_from_a_real_advert():
    # El advert trae la posición de quien lo EMITE, no la del receptor que lo oyó.
    info = decode_packet(RAW_ADVERT)

    assert info.sender_pubkey == (
        "5DDA35673215CA6EEE2913E407FED53E0652FDFAC123D1A60F7E6BE955222028"
    )
    assert info.sender_name == "Bot_Montgat"
    assert info.lat == pytest.approx(41.464282, abs=1e-5)
    assert info.lon == pytest.approx(2.273647, abs=1e-5)


@needs_decoder
def test_a_packet_that_is_not_an_advert_has_no_position():
    # Este decodifica como GroupText, así que no hay emisor ni posición que sacar.
    info = decode_packet(
        "114169995DDA35673215CA6EEE2913E407FED53E0652FDFAC123D1A60F7E6BE955"
    )

    assert info is None or info.lat is None


@pytest.mark.parametrize("value", [None, ""])
def test_missing_raw_returns_none(value):
    assert decode_packet(value) is None


@pytest.mark.parametrize(
    "value",
    ["no-es-hex", "00", "ZZZZZZZZ", "11" * 200, "1 2 3", "-"],
)
def test_decoding_never_raises(value):
    # El contrato es explícito: el decodificador es un extra y no puede tumbar
    # la ingesta. Un paquete ilegible se traduce en None, no en excepción.
    decode_packet(value)
