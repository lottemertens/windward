"""Tests for src/weather/wind_client.py"""

import pytest
from datetime import datetime
from src.weather.wind_client import _parse_wind_response, WindSample


def _make_response(hour: int, speed: float, direction: float) -> dict:
    """
    Build a minimal Open-Meteo-shaped response with 24 hourly entries.
    Only one hour has real data; the rest are zeros.
    """
    times      = [f"2024-06-01T{h:02d}:00" for h in range(24)]
    speeds     = [0.0] * 24
    directions = [0.0] * 24
    speeds[hour]     = speed
    directions[hour] = direction

    return {
        "latitude":  52.3,
        "longitude": 4.9,
        "hourly": {
            "time":              times,
            "windspeed_10m":     speeds,
            "winddirection_10m": directions,
        },
    }


def test_parse_returns_wind_sample():
    data   = _make_response(hour=10, speed=5.2, direction=270.0)
    result = _parse_wind_response(data, at=datetime(2024, 6, 1, 10))
    assert isinstance(result, WindSample)


def test_parse_reads_correct_hour():
    data   = _make_response(hour=14, speed=7.3, direction=180.0)
    result = _parse_wind_response(data, at=datetime(2024, 6, 1, 14))
    assert result.speed_ms     == 7.3
    assert result.direction_deg == 180.0


def test_parse_uses_different_hour_than_data():
    # Hour 10 has data, but we ask for hour 8 — should get zeros
    data   = _make_response(hour=10, speed=5.0, direction=90.0)
    result = _parse_wind_response(data, at=datetime(2024, 6, 1, 8))
    assert result.speed_ms == 0.0


def test_parse_raises_for_missing_time():
    # Ask for a time outside the returned range
    data = _make_response(hour=0, speed=1.0, direction=0.0)
    with pytest.raises(ValueError, match="No wind data for"):
        _parse_wind_response(data, at=datetime(2024, 6, 2, 0))  # wrong day
