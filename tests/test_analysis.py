"""Tests for src/analysis/wind_analysis.py"""

import math
import pytest
from src.models import Coordinate, WindSample, SegmentWind
from src.analysis.wind_analysis import (
    analyse_route_wind,
    _headwind_component,
    _interpolate_uv,
    _cumulative_distances,
)


# --- _headwind_component -------------------------------------------------

def test_pure_headwind():
    # Wind from north (0°), travelling north (bearing 0°) → full headwind
    u = 0.0   # no eastward component
    v = 5.0   # 5 m/s northward (coming from north)
    assert _headwind_component(u, v, travel_bearing_deg=0) == pytest.approx(5.0)


def test_pure_tailwind():
    # Wind from south (180°), travelling north (bearing 0°) → tailwind
    direction_rad = math.radians(180)
    u = 5.0 * math.sin(direction_rad)
    v = 5.0 * math.cos(direction_rad)
    assert _headwind_component(u, v, travel_bearing_deg=0) == pytest.approx(-5.0)


def test_pure_crosswind():
    # Wind from east (90°), travelling north (bearing 0°) → zero headwind
    direction_rad = math.radians(90)
    u = 5.0 * math.sin(direction_rad)
    v = 5.0 * math.cos(direction_rad)
    assert _headwind_component(u, v, travel_bearing_deg=0) == pytest.approx(0.0, abs=1e-9)


# --- _interpolate_uv -----------------------------------------------------

def test_interpolate_at_start():
    u, v = _interpolate_uv(0.0, [0.0, 1.0], [1.0, 3.0], [2.0, 4.0])
    assert u == pytest.approx(1.0)
    assert v == pytest.approx(2.0)


def test_interpolate_at_end():
    u, v = _interpolate_uv(1.0, [0.0, 1.0], [1.0, 3.0], [2.0, 4.0])
    assert u == pytest.approx(3.0)
    assert v == pytest.approx(4.0)


def test_interpolate_midpoint():
    u, v = _interpolate_uv(0.5, [0.0, 1.0], [0.0, 4.0], [0.0, 8.0])
    assert u == pytest.approx(2.0)
    assert v == pytest.approx(4.0)


# --- analyse_route_wind --------------------------------------------------

def _make_sample(lat, lon, speed, direction):
    return WindSample(lat=lat, lon=lon, speed_ms=speed, direction_deg=direction)


def test_returns_one_segment_per_waypoint_pair():
    waypoints = [
        Coordinate(lat=52.0, lon=5.0),
        Coordinate(lat=52.1, lon=5.0),
        Coordinate(lat=52.2, lon=5.0),
    ]
    samples = [_make_sample(52.1, 5.0, 3.0, 0.0)]
    result  = analyse_route_wind(waypoints, samples)
    assert len(result) == 2


def test_returns_empty_for_single_waypoint():
    result = analyse_route_wind([Coordinate(lat=52.0, lon=5.0)], [])
    assert result == []


def test_northbound_route_with_north_wind_gives_headwind():
    # Route going north, wind from north → headwind on all segments
    waypoints = [Coordinate(lat=52.0 + i * 0.1, lon=5.0) for i in range(5)]
    samples   = [_make_sample(52.2, 5.0, speed=5.0, direction=0.0)]  # from north
    result    = analyse_route_wind(waypoints, samples)
    assert all(s.headwind_ms > 0 for s in result)


def test_northbound_route_with_south_wind_gives_tailwind():
    # Route going north, wind from south → tailwind on all segments
    waypoints = [Coordinate(lat=52.0 + i * 0.1, lon=5.0) for i in range(5)]
    samples   = [_make_sample(52.2, 5.0, speed=5.0, direction=180.0)]  # from south
    result    = analyse_route_wind(waypoints, samples)
    assert all(s.headwind_ms < 0 for s in result)


def test_all_segments_are_segmentwind_instances():
    waypoints = [Coordinate(lat=52.0, lon=5.0), Coordinate(lat=52.1, lon=5.0)]
    samples   = [_make_sample(52.05, 5.0, 3.0, 90.0)]
    result    = analyse_route_wind(waypoints, samples)
    assert all(isinstance(s, SegmentWind) for s in result)
