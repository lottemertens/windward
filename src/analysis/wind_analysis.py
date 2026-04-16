"""
Wind analysis: headwind and tailwind calculations.

Responsibility: given a route (list of Coordinates) and wind samples,
compute the headwind component for every segment. No API calls — pure maths.

Key concepts
------------
- Wind direction is where wind comes FROM (meteorological convention).
- We decompose wind into (u, v) components (eastward, northward) before
  interpolating, because you cannot interpolate angles directly:
  e.g. interpolating between 350° and 10° naively gives 180°, not 0°.
- Headwind component = dot product of wind vector and travel unit vector.
  Positive → headwind (against you), negative → tailwind (with you).
"""

import math
from src.models import Coordinate, WindSample, SegmentWind
from src.geo import haversine_km, bearing, route_distance_km
from src.config import ARROW_SPACING_KM, MIN_ARROWS, MAX_ARROWS


def analyse_route_wind(
    waypoints: list[Coordinate],
    wind_samples: list[WindSample],
) -> list[SegmentWind]:
    """
    Return a SegmentWind for every consecutive pair of waypoints.

    Steps:
    1. Convert wind samples to (u, v) vector components.
    2. Map each sample to a position (km from start) along the route.
    3. For each waypoint, linearly interpolate (u, v) from surrounding samples.
    4. For each segment, compute bearing and dot-product with wind vector.
    """
    if len(waypoints) < 2 or not wind_samples:
        return []

    # 1. Cumulative distance (km) from the start for every waypoint.
    #    cumulative[i] = distance from waypoints[0] to waypoints[i].
    cumulative = _cumulative_distances(waypoints)
    total_km   = cumulative[-1]

    # 2. Build interpolation table from wind samples
    sample_positions, sample_us, sample_vs = _build_uv_table(wind_samples, waypoints, cumulative, total_km)

    # 3. Build a SegmentWind for each consecutive waypoint pair.
    segments: list[SegmentWind] = []

    for i in range(len(waypoints) - 1):
        start = waypoints[i]
        end   = waypoints[i + 1]

        # Use the midpoint of the segment to look up wind
        mid_t = ((cumulative[i] + cumulative[i + 1]) / 2) / total_km if total_km > 0 else 0.0
        u, v  = _interpolate_uv(mid_t, sample_positions, sample_us, sample_vs)

        seg_bearing = bearing(start, end)
        headwind    = _headwind_component(u, v, seg_bearing)

        segments.append(SegmentWind(start=start, end=end, headwind_ms=headwind))

    return segments


def generate_display_arrows(
    waypoints: list[Coordinate],
    wind_samples: list[WindSample],
) -> list[WindSample]:
    """
    Generate evenly-spaced wind arrows for display, independent of sample count.

    Wind samples are fetched sparsely (every ~5km) for API efficiency.
    Display arrows are placed every ARROW_SPACING_KM for visual clarity.
    Values at display positions are interpolated from the existing samples —
    no extra API calls needed.

    Returns a list of WindSample where speed_ms and direction_deg are
    reconstructed from the interpolated (u, v) components.
    """
    if len(waypoints) < 2 or not wind_samples:
        return []

    cumulative = _cumulative_distances(waypoints)
    total_km   = cumulative[-1]

    # Build the same (u, v) interpolation table used in analyse_route_wind
    sample_positions, sample_us, sample_vs = _build_uv_table(wind_samples, waypoints, cumulative, total_km)

    # Decide how many arrows to place
    n = int(total_km / ARROW_SPACING_KM)
    n = max(MIN_ARROWS, min(MAX_ARROWS, n))

    arrows: list[WindSample] = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0

        # Find the waypoint at this normalised position
        target_km  = t * total_km
        wp_idx     = min(range(len(cumulative)), key=lambda j: abs(cumulative[j] - target_km))
        wp         = waypoints[wp_idx]

        u, v       = _interpolate_uv(t, sample_positions, sample_us, sample_vs)
        speed      = math.sqrt(u ** 2 + v ** 2)
        # atan2(u, v) gives bearing from north — same convention as direction_deg
        direction  = (math.degrees(math.atan2(u, v)) + 360) % 360

        arrows.append(WindSample(lat=wp.lat, lon=wp.lon, speed_ms=speed, direction_deg=direction))

    return arrows


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_uv_table(
    wind_samples: list[WindSample],
    waypoints: list[Coordinate],
    cumulative: list[float],
    total_km: float,
) -> tuple[list[float], list[float], list[float]]:
    """
    Convert wind samples to sorted (position, u, v) lists for interpolation.
    Position is normalised 0..1 along the route.
    """
    positions, us, vs = [], [], []

    for sample in wind_samples:
        closest_idx   = _nearest_waypoint_index(sample, waypoints)
        t             = cumulative[closest_idx] / total_km if total_km > 0 else 0.0
        direction_rad = math.radians(sample.direction_deg)
        positions.append(t)
        us.append(sample.speed_ms * math.sin(direction_rad))
        vs.append(sample.speed_ms * math.cos(direction_rad))

    order     = sorted(range(len(positions)), key=lambda i: positions[i])
    return [positions[i] for i in order], [us[i] for i in order], [vs[i] for i in order]


def _cumulative_distances(waypoints: list[Coordinate]) -> list[float]:
    """Return cumulative Haversine distance (km) from waypoints[0] to each point."""
    distances = [0.0]
    for i in range(1, len(waypoints)):
        distances.append(distances[-1] + haversine_km(waypoints[i - 1], waypoints[i]))
    return distances


def _nearest_waypoint_index(sample: WindSample, waypoints: list[Coordinate]) -> int:
    """Return the index of the waypoint closest to the given wind sample."""
    sample_coord = Coordinate(lat=sample.lat, lon=sample.lon)
    return min(range(len(waypoints)), key=lambda i: haversine_km(waypoints[i], sample_coord))


def _interpolate_uv(
    t: float,
    positions: list[float],
    us: list[float],
    vs: list[float],
) -> tuple[float, float]:
    """
    Linearly interpolate wind components (u, v) at normalised position t.

    t is clamped to [0, 1]. Outside the sample range we use the nearest
    endpoint (no extrapolation).
    """
    if t <= positions[0]:
        return us[0], vs[0]
    if t >= positions[-1]:
        return us[-1], vs[-1]

    for i in range(len(positions) - 1):
        if positions[i] <= t <= positions[i + 1]:
            span = positions[i + 1] - positions[i]
            alpha = (t - positions[i]) / span if span > 0 else 0.0
            u = us[i] + alpha * (us[i + 1] - us[i])
            v = vs[i] + alpha * (vs[i + 1] - vs[i])
            return u, v

    return us[-1], vs[-1]


def _headwind_component(u: float, v: float, travel_bearing_deg: float) -> float:
    """
    Dot product of wind vector (u, v) with the travel unit vector.

    u = eastward wind component (m/s)
    v = northward wind component (m/s)
    travel_bearing_deg = compass bearing of travel (0=N, 90=E, ...)

    Returns positive for headwind, negative for tailwind.

    Why this works: the dot product of two vectors gives the component of
    one projected onto the other. If the wind vector points in the same
    direction as travel, the projection is positive (headwind). If it
    points the opposite way, the projection is negative (tailwind).
    """
    rad = math.radians(travel_bearing_deg)
    travel_u = math.sin(rad)   # eastward component of travel direction
    travel_v = math.cos(rad)   # northward component of travel direction
    return u * travel_u + v * travel_v
