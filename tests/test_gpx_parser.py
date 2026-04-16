"""Tests for src/gpx_parser.py"""

import pytest
from src.gpx_parser import parse_gpx

# Minimal GPX 1.1 track file (the format Strava and Garmin use)
GPX_TRACK = b"""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <trk>
    <trkseg>
      <trkpt lat="52.3" lon="4.9"><ele>2.0</ele></trkpt>
      <trkpt lat="52.35" lon="4.95"><ele>3.0</ele></trkpt>
      <trkpt lat="52.4" lon="5.0"><ele>1.5</ele></trkpt>
    </trkseg>
  </trk>
</gpx>"""

# Minimal GPX route file (the format Komoot uses)
GPX_ROUTE = b"""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <rte>
    <rtept lat="52.1" lon="4.5"/>
    <rtept lat="52.2" lon="4.6"/>
  </rte>
</gpx>"""

GPX_NO_NAMESPACE = b"""<?xml version="1.0"?>
<gpx version="1.0">
  <trk><trkseg>
    <trkpt lat="52.0" lon="5.0"/>
  </trkseg></trk>
</gpx>"""


def test_parse_track_returns_correct_count():
    result = parse_gpx(GPX_TRACK)
    assert len(result) == 3


def test_parse_track_reads_lat_lon():
    result = parse_gpx(GPX_TRACK)
    assert result[0].lat == 52.3
    assert result[0].lon == 4.9


def test_parse_route_points():
    result = parse_gpx(GPX_ROUTE)
    assert len(result) == 2
    assert result[0].lat == 52.1


def test_parse_no_namespace():
    result = parse_gpx(GPX_NO_NAMESPACE)
    assert len(result) == 1


def test_parse_invalid_xml_raises():
    with pytest.raises(ValueError, match="Could not parse"):
        parse_gpx(b"not xml at all")


def test_parse_empty_gpx_raises():
    empty = b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"></gpx>'
    with pytest.raises(ValueError, match="No waypoints found"):
        parse_gpx(empty)
