from __future__ import annotations

import pytest

from app.aggregate import aggregate, minute_of
from app.models import PresetKey
from tests.helpers import attribute, epoch, mk_packet, mk_status

SLOT_A = PresetKey(869.525, 62.5, 11, 5)
SLOT_B = PresetKey(869.619, 62.5, 11, 5)


def test_preset_minutes_aggregate_snr_hashes_and_rssi():
    result = aggregate(
        [],
        [
            attribute(
                mk_packet("2026-09-14T10:00:10Z", packet_hash="aa", snr="10.0", rssi="-70"),
                SLOT_A,
            ),
            attribute(
                mk_packet("2026-09-14T10:00:20Z", packet_hash="aa", snr="-6.0", rssi="-90"),
                SLOT_A,
            ),
            attribute(
                mk_packet("2026-09-14T10:00:30Z", packet_hash="bb", snr="2.0", rssi="-80"),
                SLOT_A,
            ),
        ],
    )

    assert len(result.presets) == 1
    row = result.presets[0]
    assert row.preset == SLOT_A
    assert row.pkts == 3
    assert row.uniq_hashes == 2
    assert row.snr_avg_x4 == pytest.approx((40 - 24 + 8) / 3)
    assert row.snr_p50_x4 == 8
    assert row.snr_ge0_pct == pytest.approx(200 / 3)
    assert row.rssi_avg == pytest.approx(-80.0)
    assert row.observers == 1


def test_uncertain_and_unattributed_packets_stay_out_of_the_aggregates():
    result = aggregate(
        [],
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="aa"), SLOT_A),
            attribute(
                mk_packet("2026-09-14T10:00:20Z", packet_hash="bb"),
                SLOT_A,
                uncertain=True,
            ),
            attribute(mk_packet("2026-09-14T10:00:30Z", packet_hash="cc"), None),
        ],
    )

    assert result.presets[0].pkts == 1
    assert result.uncertain_packets == 1
    assert result.unattributed_packets == 1


def test_observer_minutes_derive_utilisation_and_error_rate():
    result = aggregate(
        [
            mk_status(
                "2026-09-14T10:00:00Z",
                noise_floor=-100,
                tx_air_secs=0,
                rx_air_secs=0,
                recv_errors=0,
            ),
            mk_status(
                "2026-09-14T10:00:30Z",
                noise_floor=-110,
                tx_air_secs=6,
                rx_air_secs=24,
                recv_errors=3,
            ),
        ],
        [],
    )

    row = {item.minute_ts: item for item in result.observers}[
        minute_of(epoch("2026-09-14T10:00:30Z"))
    ]
    assert row.chan_util_pct == pytest.approx(100.0)
    assert row.err_per_h == pytest.approx(360.0)
    assert row.noise_floor == pytest.approx(-105.0)
    assert row.preset == SLOT_A


def test_counter_reset_produces_no_bogus_deltas():
    result = aggregate(
        [
            mk_status(
                "2026-09-14T10:00:00Z",
                tx_air_secs=100,
                rx_air_secs=500,
                recv_errors=90,
            ),
            mk_status(
                "2026-09-14T10:00:30Z",
                tx_air_secs=2,
                rx_air_secs=8,
                recv_errors=1,
            ),
        ],
        [],
    )

    row = {item.minute_ts: item for item in result.observers}[
        minute_of(epoch("2026-09-14T10:00:30Z"))
    ]
    assert row.chan_util_pct is None
    assert row.err_per_h is None


def test_pair_minutes_compute_pdr_within_one_preset():
    result = aggregate(
        [],
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), SLOT_A),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS1", packet_hash="bb"), SLOT_A),
            attribute(mk_packet("2026-09-14T10:00:12Z", "OBS2", packet_hash="bb"), SLOT_A),
            attribute(mk_packet("2026-09-14T10:00:13Z", "OBS2", packet_hash="cc"), SLOT_A),
        ],
    )

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert (pair.obs_a, pair.obs_b) == ("OBS1", "OBS2")
    assert (pair.heard_a, pair.heard_b, pair.both) == (2, 2, 1)
    assert pair.pdr_pct == pytest.approx(100 / 3)


def test_channel_aggregate_does_not_double_count_co_channel_cr_variants():
    # Hallazgo real del broker: 8 receptores con 2 CR distintas oyendo el mismo hash
    # en un solo canal físico. Sumando por preset, esa transmisión se contaba dos
    # veces; por canal físico se cuenta una.
    cr6 = PresetKey(869.618, 62.5, 7, 6)
    cr8 = PresetKey(869.618, 62.5, 7, 8)

    result = aggregate(
        [],
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), cr6),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS2", packet_hash="aa"), cr8),
        ],
    )

    # Por preset son dos filas, una por configuración de receptor...
    assert len(result.presets) == 2
    assert {row.preset.cr for row in result.presets} == {6, 8}
    assert sum(row.uniq_hashes for row in result.presets) == 2  # contaría doble

    # ...pero por canal físico es UNA transmisión oída dos veces.
    assert len(result.channels) == 1
    channel = result.channels[0]
    assert (channel.freq_mhz, channel.bw_khz, channel.sf) == (869.618, 62.5, 7)
    assert channel.pkts == 2
    assert channel.uniq_hashes == 1
    assert channel.observers == 2
    assert channel.crs == (6, 8)


def test_channel_bytes_count_each_transmission_once():
    # `aa` lo oyen dos receptores, pero es UNA transmisión. Trama de 40 B en el aire.
    trama = "aa" * 40
    result = aggregate(
        [],
        [
            attribute(
                mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa", raw=trama),
                SLOT_A,
            ),
            attribute(
                mk_packet("2026-09-14T10:00:11Z", "OBS2", packet_hash="aa", raw=trama),
                SLOT_A,
            ),
            attribute(
                mk_packet("2026-09-14T10:00:12Z", "OBS1", packet_hash="bb", raw=trama),
                SLOT_A,
            ),
        ],
    )

    channel = result.channels[0]
    assert channel.uniq_hashes == 2
    assert channel.payload_bytes == 80
    assert channel.payload_sizes == ((40, 2),)


def test_bytes_are_the_frame_on_air_not_the_application_payload():
    # El `payload_len` de aplicación son 32 B; la trama en el aire, 60. Se usa la
    # segunda, que es la que entra en la fórmula de tiempo de aire.
    result = aggregate(
        [],
        [
            attribute(
                mk_packet(
                    "2026-09-14T10:00:10Z", "OBS1", packet_hash="aa", raw="aa" * 60
                ),
                SLOT_A,
            ),
        ],
    )

    assert result.channels[0].payload_sizes == ((60, 1),)


def test_channel_keeps_the_size_mix_not_just_a_total():
    # Tráfico bimodal: control pequeño por un lado, adverts grandes por otro. El panel
    # modela esta mezcla, no su media, porque la media no es ningún paquete real.
    result = aggregate(
        [],
        [
            attribute(
                mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="s1", raw="aa" * 22),
                SLOT_A,
            ),
            attribute(
                mk_packet("2026-09-14T10:00:11Z", "OBS1", packet_hash="s2", raw="aa" * 22),
                SLOT_A,
            ),
            attribute(
                mk_packet("2026-09-14T10:00:12Z", "OBS1", packet_hash="b1", raw="bb" * 124),
                SLOT_A,
            ),
        ],
    )

    channel = result.channels[0]
    assert channel.payload_sizes == ((22, 2), (124, 1))
    assert channel.payload_bytes == 22 * 2 + 124


def test_packets_without_a_hash_count_their_bytes_once_each():
    # Sin hash no hay forma de saber que son la misma transmisión, así que se cuentan
    # igual que las transmisiones distintas: una por paquete.
    trama = "aa" * 40
    result = aggregate(
        [],
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", raw=trama), SLOT_A),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS1", raw=trama), SLOT_A),
        ],
    )

    channel = result.channels[0]
    assert channel.uniq_hashes == 0
    assert channel.payload_sizes == ((40, 2),)


def test_packets_without_a_frame_fall_back_to_the_application_length():
    from app.models import AttributedPacket, Attribution, Packet

    packet = Packet(
        ts=epoch("2026-09-14T10:00:10Z"),
        observer_pubkey="OBS1",
        iata="BAR",
        snr_x4=None,
        rssi=None,
        packet_type=None,
        route=None,
        payload_len=17,
        raw_hex=None,
        origin=None,
        packet_hash="aa",
    )
    result = aggregate(
        [],
        [
            AttributedPacket(
                packet=packet,
                attribution=Attribution(preset=SLOT_A, uncertain=False),
            )
        ],
    )

    assert result.channels[0].payload_sizes == ((17, 1),)


def test_packets_without_a_length_do_not_add_bytes():
    from app.models import AttributedPacket, Attribution, Packet

    packet = Packet(
        ts=epoch("2026-09-14T10:00:10Z"),
        observer_pubkey="OBS1",
        iata="BAR",
        snr_x4=None,
        rssi=None,
        packet_type=None,
        route=None,
        payload_len=None,
        raw_hex=None,
        origin=None,
        packet_hash="aa",
    )
    result = aggregate(
        [],
        [
            AttributedPacket(
                packet=packet,
                attribution=Attribution(preset=SLOT_A, uncertain=False),
            )
        ],
    )

    assert result.channels[0].payload_bytes == 0
    assert result.channels[0].payload_sizes == ()


def test_pdr_pairs_observers_differing_only_in_coding_rate():
    # Antes no se emparejaban porque el emparejamiento era por preset exacto, y son
    # justo los que más sentido tiene comparar: oyen exactamente lo mismo.
    cr6 = PresetKey(869.618, 62.5, 7, 6)
    cr8 = PresetKey(869.618, 62.5, 7, 8)

    result = aggregate(
        [],
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), cr6),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS2", packet_hash="aa"), cr8),
        ],
    )

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.pdr_pct == pytest.approx(100.0)
    assert (pair.freq_mhz, pair.bw_khz, pair.sf) == (869.618, 62.5, 7)


def test_the_channel_persists_between_statuses():
    # Un status cada dos minutos: el canal debe valer para todos los minutos
    # intermedios, no solo para aquellos donde llegó el status. Antes el 46 % de las
    # filas quedaban sin canal y el análisis se quedaba ciego.
    statuses = [
        mk_status("2026-09-14T10:00:00Z", "869.431,62.5,7,6", "OBS1"),
        mk_status("2026-09-14T10:02:00Z", "869.431,62.5,7,6", "OBS1"),
    ]
    packets = [
        attribute(
            mk_packet(f"2026-09-14T10:{index:02d}:10Z", "OBS1", packet_hash=f"H{index}"),
            PresetKey(869.431, 62.5, 7, 6),
        )
        for index in (0, 1, 2, 3)
    ]

    result = aggregate(statuses, packets)

    canales = {row.minute_ts: row.preset for row in result.observers}
    assert len(canales) == 4
    assert all(preset == PresetKey(869.431, 62.5, 7, 6) for preset in canales.values())


def test_no_pair_across_different_channels():
    result = aggregate(
        [],
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), SLOT_A),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS2", packet_hash="aa"), SLOT_B),
        ],
    )

    assert result.pairs == []
    assert len(result.presets) == 2
    assert len(result.channels) == 2


def test_pdr_needs_at_least_two_observers():
    result = aggregate(
        [],
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa"), SLOT_A),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS1", packet_hash="bb"), SLOT_A),
        ],
    )

    assert result.pairs == []
