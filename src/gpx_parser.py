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

    def _collect(tag_name: str) -> list[Coordinate]:
        pts = []
        for element in root.iter():
            if _strip_ns(element.tag) == tag_name:
                lat = element.get('lat')
                lon = element.get('lon')
                if lat is None or lon is None:
                    continue
                try:
                    pts.append(Coordinate(lat=float(lat), lon=float(lon)))
                except ValueError:
                    continue
        return pts

    # Prefer track points (GPS recording) over route points (planned route) over
    # standalone waypoints (POIs). Komoot and similar apps sometimes export all
    # three in the same file; mixing them causes spurious line jumps on the map.
    for tag in ('trkpt', 'rtept', 'wpt'):
        waypoints = _collect(tag)
        if waypoints:
            break
    else:
        waypoints = []

    if not waypoints:
        raise ValueError(
            "No waypoints found in this file. "
            "Make sure it is a valid GPX file exported from Strava, Komoot, or Garmin."
        )

    return waypoints
