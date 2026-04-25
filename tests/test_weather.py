"""Tests for src/weather/wind_client.py"""

import pytest
from datetime import datetime
from src.models import WindSample
from src.weather.wind_client import interpolate_wind_at_time


def _make_forecast(hour: int, speed: float, direction: float) -> dict:
    """
    Build a minimal Open-Meteo-shaped 24-h forecast dict.
    Only one hour has non-zero data; the rest are zeros.
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


def test_returns_wind_sample():
    data   = _make_forecast(hour=10, speed=5.2, direction=270.0)
    result = interpolate_wind_at_time(data, at=datetime(2024, 6, 1, 10))
    assert isinstance(result, WindSample)


def test_reads_correct_hour():
    data   = _make_forecast(hour=14, speed=7.3, direction=180.0)
    result = interpolate_wind_at_time(data, at=datetime(2024, 6, 1, 14))
    assert result.speed_ms      == pytest.approx(7.3)
    assert result.direction_deg == pytest.approx(180.0)


def test_zero_speed_at_other_hours():
    # Hour 10 has data, asking for hour 8 — all other hours are 0.0
    data   = _make_forecast(hour=10, speed=5.0, direction=90.0)
    result = interpolate_wind_at_time(data, at=datetime(2024, 6, 1, 8))
    assert result.speed_ms == pytest.approx(0.0)


def test_falls_back_gracefully_for_out_of_range_time():
    # Asking for a time outside the 24-h window falls back to hour 0
    # rather than raising — the browser always sends a 48-h window so
    # this only happens with malformed input.
    data   = _make_forecast(hour=0, speed=3.0, direction=45.0)
    result = interpolate_wind_at_time(data, at=datetime(2024, 6, 2, 0))
    assert isinstance(result, WindSample)   # no raise
