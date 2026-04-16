"""
GPX file parser.

GPX is the standard export format for Strava, Komoot, and Garmin.
It is XML with waypoints stored as <trkpt> (track) or <rtept> (route) elements.

We use Python's built-in xml.etree.ElementTree — no extra dependencies needed.
"""

import xml.etree.ElementTree as ET
from src.models import Coordinate


# GPX files can use two different XML namespace declarations.
# We strip the namespace from tag names so we don't have to care which version.
def _strip_ns(tag: str) -> str:
    """Remove XML namespace prefix from a tag: '{http://...}trkpt' → 'trkpt'"""
    return tag.split('}')[-1] if '}' in tag else tag


def parse_gpx(content: bytes) -> list[Coordinate]:
    """
    Parse a GPX file and return its waypoints as a list of Coordinates.

    Supports:
    - Track files (<trk><trkseg><trkpt>) — typical Strava / Garmin activity exports
    - Route files (<rte><rtept>)         — typical Komoot planned route exports

    Raises ValueError if no waypoints are found or the file is not valid GPX.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"Could not parse file as XML: {e}")

    waypoints: list[Coordinate] = []

    # Walk every element in the tree, match by local tag name (ignoring namespace)
    for element in root.iter():
        tag = _strip_ns(element.tag)
        if tag in ('trkpt', 'rtept', 'wpt'):
            lat = element.get('lat')
            lon = element.get('lon')
            if lat is None or lon is None:
                continue
            try:
                waypoints.append(Coordinate(lat=float(lat), lon=float(lon)))
            except ValueError:
                continue  # skip malformed points

    if not waypoints:
        raise ValueError(
            "No waypoints found in this file. "
            "Make sure it is a valid GPX file exported from Strava, Komoot, or Garmin."
        )

    return waypoints
