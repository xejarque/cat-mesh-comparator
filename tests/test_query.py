from __future__ import annotations

import pytest

from app.db import connect, init_db, insert_packets, insert_status
from app.models import PresetKey
from app.query import (
    channel_catalog,
    channel_costs,
    channel_regions,
    channel_series,
    channel_summaries,
    common_observers,
    latest_packet_id,
    observer_rows,
    paired_observers,
    paired_pairs,
    preset_summaries,
    quality_rows,
    recent_packets,
    slot_index,
    validate_group_by,
    window_bounds,
)
from app.rollup import rollup_window
from app.presets import SEED_PRESETS
from tests.helpers import (
    attribute,
    epoch,
    fill_channel_minute,
    fill_observer_minute,
    mk_packet,
    mk_status,
)

CR6 = PresetKey(869.618, 62.5, 7, 6)
CR8 = PresetKey(869.618, 62.5, 7, 8)
SLOT_1 = PresetKey(869.432, 62.5, 7, 6)


@pytest.fixture()
def conn(tmp_path):
    connection = connect(tmp_path / "query.db")
    init_db(connection)
    yield connection
    connection.close()


def _seed(conn):
    """Dos receptores, misma frecuencia y SF, CR distinta: oyen lo mismo."""
    insert_status(
        conn,
        mk_status("2026-09-14T10:00:00Z", "869.618,62.5,7,6", "OBS1", noise_floor=-110),
    )
    insert_status(
        conn,
        mk_status("2026-09-14T10:00:00Z", "869.618,62.5,7,8", "OBS2", noise_floor=-104),
    )
    insert_packets(
        conn,
        [
            attribute(mk_packet("2026-09-14T10:00:10Z", "OBS1", packet_hash="aa", snr="10.0"), CR6),
            attribute(mk_packet("2026-09-14T10:00:11Z", "OBS2", packet_hash="aa", snr="8.0"), CR8),
            attribute(mk_packet("2026-09-14T10:00:12Z", "OBS1", packet_hash="bb", snr="6.0"), CR6),
        ],
    )
    rollup_window(conn, 0)


def test_channel_summaries_grouped_by_width_and_sf(conn):
    _seed(conn)

    summaries = channel_summaries(conn, since_ts=0)

    assert len(summaries) == 1
    row = summaries[0]
    assert row.label == "869.618 MHz · BW62.5 · SF7"
    assert row.pkts == 3
    # Dos receptores con CR distinta oyen `aa`: es UNA transmisión, no dos.
    assert row.transmissions == 2
    assert row.observers == 2
    assert row.crs == (6, 8)
    assert row.noise_floor_dbm == pytest.approx(-107.0)
    # OBS1 oyó {aa, bb} y OBS2 {aa}: intersección 1 de 2.
    assert row.pdr_median == pytest.approx(50.0)


def test_grouping_by_sf_alone_collapses_the_channel(conn):
    _seed(conn)

    summaries = channel_summaries(conn, since_ts=0, group_by=("sf",))

    assert len(summaries) == 1
    assert summaries[0].label == "SF7"
    assert summaries[0].transmissions == 2


def test_grouping_by_frequency_alone(conn):
    _seed(conn)

    summaries = channel_summaries(conn, since_ts=0, group_by=("freq",))

    assert summaries[0].label == "869.618 MHz"
    assert summaries[0].transmissions == 2


def test_summaries_are_ordered_by_volume(conn):
    _seed(conn)
    insert_packets(
        conn,
        [
            attribute(
                mk_packet("2026-09-14T11:00:10Z", "OBS1", packet_hash="cc"), 
                PresetKey(869.431, 62.5, 7, 6),
            )
        ],
    )
    rollup_window(conn, 0)

    summaries = channel_summaries(conn, since_ts=0)

    assert [row.label for row in summaries] == [
        "869.618 MHz · BW62.5 · SF7",
        "869.431 MHz · BW62.5 · SF7",
    ]


def test_slot_is_only_set_when_the_grouping_identifies_a_frequency(conn):
    _seed(conn)

    # 869.618 es el slot 4 de h1.4, y el color de la web sale de aquí — nunca del
    # índice de la fila, que cambiaría al reordenar.
    por_canal = channel_summaries(conn, since_ts=0)
    assert por_canal[0].slot == 4

    por_sf = channel_summaries(conn, since_ts=0, group_by=("sf",))
    assert por_sf[0].slot is None


def test_slot_index_matches_only_the_frequencies_of_a_plan():
    assert slot_index(869.432) == 1
    assert slot_index(869.618) == 4
    # El slot es esa frecuencia **con el ancho del plan**; en 250 kHz es otra cosa.
    assert slot_index(869.618, 250.0) is None
    # 869.525 es el canal público de Meshtastic: no es uno de los cuatro slots.
    assert slot_index(869.525) is None
    assert slot_index(None) is None


def _alternating(conn, pubkey, radios):
    """Deja al observer alternando entre canales, minuto a minuto."""
    for index, radio in enumerate(radios):
        insert_status(
            conn,
            mk_status(f"2026-09-14T10:{index:02d}:00Z", radio, pubkey),
        )
    rollup_window(conn, 0)


def test_paired_observers_need_more_than_one_channel(conn):
    insert_status(conn, mk_status("2026-09-14T10:00:00Z", "869.618,62.5,7,6", "OBS1"))
    rollup_window(conn, 0)

    assert paired_observers(conn, since_ts=0) == []


def test_paired_observers_with_overlapping_channels_are_comparable(conn):
    _alternating(
        conn,
        "OBS1",
        [
            "869.618,62.5,7,6",
            "869.432,62.5,7,6",
            "869.618,62.5,7,6",
            "869.432,62.5,7,6",
        ],
    )

    subjects = paired_observers(conn, since_ts=0)

    assert len(subjects) == 1
    assert subjects[0].comparable is True
    assert [m.channel_label for m in subjects[0].measurements] == [
        "869.432 MHz · BW62.5 · SF7",
        "869.618 MHz · BW62.5 · SF7",
    ]


def test_paired_observers_without_overlap_are_flagged(conn):
    _alternating(
        conn,
        "OBS1",
        ["869.618,62.5,7,6", "869.618,62.5,7,6", "869.432,62.5,7,6"],
    )

    subjects = paired_observers(conn, since_ts=0)

    # Los dos canales existen, pero no coinciden en el tiempo: la diferencia podría
    # ser propagación y no canal, así que no vale compararlos.
    assert len(subjects) == 1
    assert subjects[0].comparable is False
    assert subjects[0].overlap_seconds == 0


def test_two_observers_sharing_a_name_are_not_merged(conn):
    # Se agrupa por pubkey, no por nombre: dos receptores pueden llamarse igual.
    for pubkey in ("OBSA", "OBSB"):
        _alternating(conn, pubkey, ["869.618,62.5,7,6", "869.432,62.5,7,6"])

    subjects = paired_observers(conn, since_ts=0)

    assert len(subjects) == 2
    assert {s.subject for s in subjects} == {"Obs"}


def test_paired_pairs_compares_the_same_link_across_channels(conn):
    for minute, preset in (
        ("10:00", PresetKey(869.618, 62.5, 7, 6)),
        ("10:01", PresetKey(869.432, 62.5, 7, 6)),
        ("10:02", PresetKey(869.618, 62.5, 7, 6)),
        ("10:03", PresetKey(869.432, 62.5, 7, 6)),
    ):
        insert_packets(
            conn,
            [
                attribute(
                    mk_packet(f"2026-09-14T{minute}:10Z", "OBS1", packet_hash="aa"),
                    preset,
                ),
                attribute(
                    mk_packet(f"2026-09-14T{minute}:11Z", "OBS2", packet_hash="aa"),
                    preset,
                ),
            ],
        )
    rollup_window(conn, 0)

    subjects = paired_pairs(conn, since_ts=0)

    assert len(subjects) == 1
    assert subjects[0].comparable is True
    assert len(subjects[0].measurements) == 2
    assert all(
        measurement.metrics[0].value == pytest.approx(100.0)
        for measurement in subjects[0].measurements
    )


def test_paired_subject_flags_when_only_the_sf_changes(conn):
    # Caso real del broker: VLC tiene receptores en 869.618 con SF7, SF8 y SF10.
    # Parece una comparación de canales, pero cada SF oye a emisores distintos.
    _alternating(
        conn,
        "OBS1",
        ["869.618,62.5,7,6", "869.618,62.5,8,8", "869.618,62.5,7,6", "869.618,62.5,8,8"],
    )

    subjects = paired_observers(conn, since_ts=0)

    assert len(subjects) == 1
    assert subjects[0].comparable is True
    assert subjects[0].only_sf_changes is True


def test_paired_subject_does_not_flag_a_real_frequency_change(conn):
    _alternating(conn, "OBS1", ["869.618,62.5,7,6", "869.432,62.5,7,6"])

    subjects = paired_observers(conn, since_ts=0)

    assert subjects[0].only_sf_changes is False


def test_channel_regions_compare_regions_on_the_same_channel(conn):
    # Mismo canal, dos comarcas: es la comparación que sí tiene sentido.
    fill_observer_minute(
        conn, "OBS_BCN", SLOT_1, "2026-09-14T10:00:00Z", 10, noise=-104.0, iata="BCN"
    )
    fill_observer_minute(
        conn, "OBS_GRO", SLOT_1, "2026-09-14T10:00:00Z", 10, noise=-97.0, iata="GRO"
    )

    channels = channel_regions(conn, since_ts=0)

    assert len(channels) == 1
    group = channels[0]
    assert group.channel_label == "869.432 MHz · BW62.5 · SF7"
    assert group.comparable is True
    # A igualdad de receptores y minutos, la referencia es la primera por nombre.
    assert group.reference == "BCN"

    # Lo que convierte la tabla en una comparación: la diferencia contra la referencia.
    reference = next(item for item in group.regions if item.is_reference)
    other = next(item for item in group.regions if not item.is_reference)
    assert reference.noise_delta_db == pytest.approx(0.0)
    assert other.noise_delta_db == pytest.approx(7.0)


def test_channel_regions_prefers_the_region_with_more_receivers_as_reference(conn):
    fill_observer_minute(
        conn, "OBS_BCN", SLOT_1, "2026-09-14T10:00:00Z", 10, iata="BCN"
    )
    for index in range(3):
        fill_observer_minute(
            conn,
            f"OBS_GRO{index}",
            SLOT_1,
            "2026-09-14T10:00:00Z",
            10,
            iata="GRO",
        )

    group = channel_regions(conn, since_ts=0)[0]

    assert group.reference == "GRO"
    assert group.regions[0].observers == 3


def test_channel_regions_needs_two_regions_to_compare(conn):
    fill_observer_minute(
        conn, "OBS_BCN", SLOT_1, "2026-09-14T10:00:00Z", 10, iata="BCN"
    )

    group = channel_regions(conn, since_ts=0)[0]

    assert group.comparable is False
    assert len(group.regions) == 1


def test_channel_regions_without_temporal_overlap_are_not_comparable(conn):
    fill_observer_minute(
        conn, "OBS_BCN", SLOT_1, "2026-09-14T10:00:00Z", 10, iata="BCN"
    )
    fill_observer_minute(
        conn, "OBS_GRO", SLOT_1, "2026-09-14T12:00:00Z", 10, iata="GRO"
    )

    group = channel_regions(conn, since_ts=0)[0]

    assert group.overlap_seconds == 0
    assert group.comparable is False


def test_per_receiver_aggregation_is_not_dominated_by_volume(conn):
    # Un receptor que oye muchísimo y con mala señal, y otro que oye poco y bien.
    # Cada uno necesita su status: el canal de un receptor lo dice su configuración.
    insert_status(conn, mk_status("2026-09-14T10:00:00Z", "869.432,62.5,7,6", "LOUD"))
    insert_status(conn, mk_status("2026-09-14T10:00:00Z", "869.432,62.5,7,6", "QUIET"))

    insert_packets(
        conn,
        [
            attribute(
                mk_packet(
                    f"2026-09-14T10:00:{index:02d}Z", "LOUD",
                    packet_hash=f"L{index}", snr="0.0",
                ),
                SLOT_1,
            )
            for index in range(40)
        ]
        + [
            attribute(
                mk_packet(
                    f"2026-09-14T10:00:{index + 45:02d}Z", "QUIET",
                    packet_hash=f"Q{index}", snr="20.0",
                ),
                SLOT_1,
            )
            for index in range(2)
        ],
    )
    rollup_window(conn, 0)

    por_recepcion = channel_summaries(conn, since_ts=0)
    por_receptor = channel_summaries(conn, since_ts=0, per_receiver=True)

    # Ponderando por paquetes, el charlatán arrastra la mediana hacia 0 dB.
    assert por_recepcion[0].snr_median_db == pytest.approx(0.0)
    # Contando cada receptor una vez, manda la mayoría de receptores, no de paquetes.
    stats = por_receptor[0].by_receiver
    assert stats.receivers == 2
    assert stats.snr_median_db == pytest.approx(10.0)
    # Y el rango deja ver al que lee mal, en vez de esconderlo en la media.
    assert stats.snr_low_db == pytest.approx(0.0)
    assert stats.snr_high_db == pytest.approx(20.0)


def test_common_observers_counts_the_intersection(conn):
    fill_channel_minute(conn, SLOT_1, "2026-09-14T10:00:00Z", 5)
    fill_observer_minute(conn, "SHARED", SLOT_1, "2026-09-14T10:00:00Z", 5)
    fill_observer_minute(conn, "ONLY_A", SLOT_1, "2026-09-14T10:00:00Z", 5)

    common, total = common_observers(conn, since_ts=0, until_ts=None, group_by=("freq",))

    assert (common, total) == (2, 2)


def test_only_common_drops_observers_that_are_not_in_every_group(conn):
    # Dos canales. SHARED está en los dos, pero **en franjas distintas**: un receptor
    # no puede estar en dos canales a la vez, y la clave primaria lo impide.
    fill_channel_minute(conn, CR6, "2026-09-14T10:00:00Z", 5)
    fill_channel_minute(conn, SLOT_1, "2026-09-14T11:00:00Z", 5)
    fill_observer_minute(conn, "SHARED", CR6, "2026-09-14T10:00:00Z", 5, snr_x4=40.0)
    fill_observer_minute(conn, "SHARED", SLOT_1, "2026-09-14T11:00:00Z", 5, snr_x4=40.0)
    fill_observer_minute(conn, "ONLY_A", CR6, "2026-09-14T10:00:00Z", 5, snr_x4=0.0)
    fill_observer_minute(conn, "ONLY_B", SLOT_1, "2026-09-14T11:00:00Z", 5, snr_x4=0.0)

    todos = channel_summaries(conn, since_ts=0, per_receiver=True)
    comunes = channel_summaries(conn, since_ts=0, per_receiver=True, only_common=True)

    assert len(todos) == 2
    assert all(row.by_receiver.receivers == 2 for row in todos)
    # Quedándose solo con SHARED, la comparación pasa a ser entre los mismos nodos.
    assert all(row.by_receiver.receivers == 1 for row in comunes)
    assert all(row.by_receiver.snr_median_db == pytest.approx(10.0) for row in comunes)


def test_recent_packets_returns_the_last_ones_in_ascending_order(conn):
    _seed(conn)

    rows = recent_packets(conn, limit=10)

    assert len(rows) == 3
    assert [row.id for row in rows] == sorted(row.id for row in rows)
    assert rows[-1].channel == "869.618 MHz · SF7 · CR6"
    assert rows[-1].snr_db == pytest.approx(6.0)


def test_recent_packets_cursor_returns_only_newer(conn):
    _seed(conn)
    newest = recent_packets(conn, limit=1)[0].id

    assert recent_packets(conn, after_id=newest) == []
    assert [row.id for row in recent_packets(conn, after_id=newest - 1)] == [newest]


def test_recent_packets_limit_is_clamped(conn):
    _seed(conn)

    assert len(recent_packets(conn, limit=1)) == 1
    assert len(recent_packets(conn, limit=9999)) == 3


def test_latest_packet_id_starts_at_zero(conn):
    assert latest_packet_id(conn) == 0

    _seed(conn)
    assert latest_packet_id(conn) == recent_packets(conn, limit=1)[0].id


def test_recent_packets_marks_unattributed_ones(conn):
    insert_packets(
        conn,
        [attribute(mk_packet("2026-09-14T10:00:10Z", packet_hash="zz"), None)],
    )

    assert recent_packets(conn, limit=1)[0].channel == "sin atribuir"


def test_channel_series_returns_points_per_group(conn):
    _seed(conn)

    series = channel_series(conn, since_ts=0)

    assert list(series) == ["869.618 MHz · BW62.5 · SF7"]
    points = series["869.618 MHz · BW62.5 · SF7"]
    assert points == [(epoch("2026-09-14T10:00:00Z"), 3)]


def test_channel_costs_count_on_air_bytes_once_per_transmission(conn):
    _seed(conn)

    costs = channel_costs(conn, since_ts=0)

    assert len(costs) == 1
    cost = costs[0]
    assert cost.channel_label == "869.618 MHz · BW62.5 · SF7"
    # `aa` la oyen dos receptores pero es UNA transmisión. El raw de prueba son 3 B.
    assert cost.transmissions == 2
    assert cost.sizes == ((3, 2),)
    assert cost.payload_bytes_total == 6
    assert cost.median_size_bytes == 3
    # Una sola fila de un minuto: la tasa se proyecta a la hora.
    assert cost.transmissions_per_h == pytest.approx(120.0)
    assert cost.snr_p50_db == pytest.approx(8.0)
    # El SF del canal es el eje: su fila es la referencia 1,00×.
    assert cost.can_model is True
    baseline = next(item for item in cost.projections if item.sf == 7)
    assert baseline.factor_vs_current == pytest.approx(1.0)
    assert baseline.margin_db == pytest.approx(8.0 - (-7.5))


def test_channel_costs_model_the_real_size_mix(conn):
    # Tráfico bimodal: 22 B de control y 124 B de advert. Se modela la mezcla, no su
    # media, porque la media cae en un valle y no describe ningún paquete real.
    fill_channel_minute(
        conn,
        SLOT_1,
        "2026-09-14T10:00:00Z",
        6,
        uniq_hashes=10,
        sizes={22: 7, 124: 3},
        snr_p50_x4=40.0,
    )

    cost = channel_costs(conn, since_ts=0)[0]

    assert cost.sizes == ((22, 42), (124, 18))
    assert cost.payload_bytes_total == 6 * (22 * 7 + 124 * 3)
    assert cost.median_size_bytes == 22
    assert cost.top_sizes[0] == (22, 42)
    # La media queda entre los dos picos: no es ninguno de los tamaños que hay.
    media = cost.payload_bytes_total / cost.transmissions
    assert 22 < media < 124


def test_channel_costs_without_a_mix_cannot_model_the_air(conn):
    # Sin mezcla de tamaños no hay aire que proyectar: mejor decirlo que inventar ceros.
    fill_channel_minute(conn, SLOT_1, "2026-09-14T10:00:00Z", 3)

    costs = channel_costs(conn, since_ts=0)

    assert len(costs) == 1
    assert costs[0].can_model is False
    assert costs[0].projections == ()


def test_channel_costs_group_by_physical_channel(conn):
    fill_channel_minute(
        conn, SLOT_1, "2026-09-14T10:00:00Z", 6, uniq_hashes=10, sizes={32: 10}
    )
    fill_channel_minute(
        conn, CR6, "2026-09-14T11:00:00Z", 6, uniq_hashes=10, sizes={32: 10}
    )

    costs = channel_costs(conn, since_ts=0)

    assert len(costs) == 2
    assert all(cost.transmissions_per_h == pytest.approx(600.0) for cost in costs)
    assert all(cost.payload_bytes_total == 6 * 32 * 10 for cost in costs)


def test_validate_group_by_rejects_unknown_dimensions():
    with pytest.raises(ValueError):
        validate_group_by(("freq", "cr"))
    with pytest.raises(ValueError):
        validate_group_by(())


def test_preset_summaries_keep_the_cr_apart(conn):
    _seed(conn)

    summaries = preset_summaries(conn, since_ts=0)

    assert len(summaries) == 2
    assert {row.preset.cr for row in summaries} == {6, 8}
    # Por preset, el PDR se busca por canal: las dos filas lo comparten.
    assert all(row.pdr_pct == pytest.approx(50.0) for row in summaries)


def test_observer_rows_carry_the_configuration(conn):
    _seed(conn)

    rows = observer_rows(conn)

    by_short = {row.short: row for row in rows}
    assert by_short["OBS1"].cr == 6
    assert by_short["OBS1"].sf == 7
    assert by_short["OBS1"].noise_floor == pytest.approx(-110.0)
    assert by_short["OBS2"].cr == 8


def test_channel_catalog_lists_channels_and_watchers(conn):
    _seed(conn)

    catalog = channel_catalog(conn)

    # Salen TODOS los canales conocidos, también los que no tiene nadie: ver los
    # huecos es justo el objetivo de la página de campaña. Y además los que se hayan
    # descubierto desde los datos, que no están sembrados.
    assert len(catalog) >= len(SEED_PRESETS)
    con_observers = [entry for entry in catalog if entry["observers"]]
    assert len(con_observers) == 2
    assert {entry["cr"] for entry in con_observers} == {6, 8}
    # Los que tienen observers van primero.
    assert catalog[0]["observers"] == 1


def test_quality_rows_and_window_bounds(conn):
    _seed(conn)

    assert quality_rows(conn) == []

    now = epoch("2026-09-14T12:00:00Z")
    since, until = window_bounds("6h", now)
    assert since == epoch("2026-09-14T06:00:00Z")
    assert until is None

    with pytest.raises(ValueError):
        window_bounds("3y", now)
