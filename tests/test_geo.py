from __future__ import annotations

import pytest

from app.geo import haversine_km


def test_distance_between_two_catalan_cities():
    # Barcelona -> Girona, unos 85-100 km según el punto exacto.
    distance = haversine_km(41.3874, 2.1686, 41.9794, 2.8214)

    assert 80 < distance < 100


def test_distance_to_itself_is_zero():
    assert haversine_km(41.0, 2.0, 41.0, 2.0) == pytest.approx(0.0)


def test_distance_is_symmetric():
    assert haversine_km(41.0, 2.0, 42.0, 3.0) == pytest.approx(
        haversine_km(42.0, 3.0, 41.0, 2.0)
    )
