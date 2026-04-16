"""Tests for src/geo.py"""

import pytest
from src.models import Coordinate
from src.geo import haversine_km, route_distance_km, bearing


def test_haversine_known_distance():
    # Amsterdam to Utrecht is roughly 40km in a straight line
    amsterdam = Coordinate(lat=52.37, lon=4.89)
    utrecht   = Coordinate(lat=52.09, lon=5.12)
    distance  = haversine_km(amsterdam, utrecht)
    assert 30 < distance < 45


def test_haversine_same_point_is_zero():
    point = Coordinate(lat=52.37, lon=4.89)
    assert haversine_km(point, point) == 0.0


def test_haversine_is_symmetric():
    a = Coordinate(lat=52.37, lon=4.89)
    b = Coordinate(lat=52.09, lon=5.12)
    assert haversine_km(a, b) == pytest.approx(haversine_km(b, a))


def test_route_distance_sums_segments():
    a = Coordinate(lat=52.37, lon=4.89)
    b = Coordinate(lat=52.09, lon=5.12)
    c = Coordinate(lat=51.90, lon=4.48)  # Rotterdam area
    total = route_distance_km([a, b, c])
    assert total == pytest.approx(haversine_km(a, b) + haversine_km(b, c))


def test_route_distance_single_point_is_zero():
    assert route_distance_km([Coordinate(lat=52.37, lon=4.89)]) == 0.0


def test_route_distance_empty_is_zero():
    assert route_distance_km([]) == 0.0


# --- bearing -------------------------------------------------------------

def test_bearing_due_north():
    a = Coordinate(lat=52.0, lon=5.0)
    b = Coordinate(lat=53.0, lon=5.0)
    assert bearing(a, b) == pytest.approx(0.0, abs=0.5)


def test_bearing_due_east():
    a = Coordinate(lat=52.0, lon=5.0)
    b = Coordinate(lat=52.0, lon=6.0)
    assert bearing(a, b) == pytest.approx(90.0, abs=1.0)


def test_bearing_due_south():
    a = Coordinate(lat=53.0, lon=5.0)
    b = Coordinate(lat=52.0, lon=5.0)
    assert bearing(a, b) == pytest.approx(180.0, abs=0.5)
