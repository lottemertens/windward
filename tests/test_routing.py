"""Tests for src/routing/ors_client.py"""

from src.routing.ors_client import _parse_route_response, _parse_surfaces, OrsRouteResult
from src.models import Coordinate


SAMPLE_ORS_RESPONSE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [4.9, 52.3, 5.0],
                    [4.95, 52.35, 6.0],
                    [5.0, 52.4, 4.0],
                ],
            },
            "properties": {
                "extras": {
                    "surface": {
                        "summary": [
                            {"value": 3, "distance": 8200.0, "amount": 78.5},
                            {"value": 10, "distance": 2240.0, "amount": 21.5},
                        ]
                    }
                },
                "warnings": [
                    {"code": 4, "message": "Route may use a ferry."}
                ],
            },
        }
    ],
}


def test_parse_route_response_returns_correct_number_of_points():
    result = _parse_route_response(SAMPLE_ORS_RESPONSE)
    assert len(result.waypoints) == 3


def test_parse_route_response_flips_lon_lat_to_lat_lon():
    result = _parse_route_response(SAMPLE_ORS_RESPONSE)
    first  = result.waypoints[0]
    assert first.lat == 52.3
    assert first.lon == 4.9


def test_parse_route_response_returns_coordinate_objects():
    result = _parse_route_response(SAMPLE_ORS_RESPONSE)
    assert all(isinstance(c, Coordinate) for c in result.waypoints)


def test_parse_route_response_extracts_surfaces():
    result = _parse_route_response(SAMPLE_ORS_RESPONSE)
    assert len(result.surfaces) == 2
    assert result.surfaces[0].name == "Asphalt"
    assert result.surfaces[0].percentage == 78.5


def test_parse_route_response_extracts_warnings():
    result = _parse_route_response(SAMPLE_ORS_RESPONSE)
    assert len(result.warnings) == 1
    assert "ferry" in result.warnings[0].lower()


def test_parse_surfaces_sorts_by_coverage():
    result = _parse_route_response(SAMPLE_ORS_RESPONSE)
    percentages = [s.percentage for s in result.surfaces]
    assert percentages == sorted(percentages, reverse=True)


def test_parse_surfaces_converts_distance_to_km():
    result = _parse_route_response(SAMPLE_ORS_RESPONSE)
    assert result.surfaces[0].distance_km == 8.2


def test_parse_surfaces_ignores_below_one_percent():
    data = {"summary": [{"value": 3, "distance": 100.0, "amount": 0.5}]}
    assert _parse_surfaces(data) == []
