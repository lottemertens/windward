"""
Project-wide configuration constants.

All magic numbers and tuning parameters live here — not scattered across
modules. To adjust behaviour, change a value here and it takes effect
everywhere that uses it.
"""

# --- OpenRouteService (ORS) -----------------------------------------------

ORS_BASE_URL      = "https://api.openrouteservice.org/v2/directions"
CYCLING_PROFILE   = "cycling-regular"

# --- Open-Meteo ------------------------------------------------------------

OPEN_METEO_URL         = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"  # for past dates
OPEN_METEO_FORECAST_DAYS = 16   # max days ahead the forecast API supports

# --- Default location for wind overview -----------------------------------
# Shown in the sidebar before a route is planned.

DEFAULT_LOCATION_LAT  = 52.37
DEFAULT_LOCATION_LON  = 4.89
DEFAULT_LOCATION_NAME = "Amsterdam"

# --- Wind arrow display ---------------------------------------------------
# Arrows are shown every ARROW_SPACING_KM along the route, independent of
# how many wind samples were fetched. More frequent than samples is fine
# because values are interpolated from the existing samples — no extra API calls.

ARROW_SPACING_KM = 0.5   # one arrow per 0.5 km
MIN_ARROWS       = 4     # always show at least this many
MAX_ARROWS       = 30    # cap for very long routes

# --- Wind colour scale ----------------------------------------------------
# headwind_ms values are clamped to [-HEADWIND_SCALE_MS, +HEADWIND_SCALE_MS]
# before mapping to the green→yellow→red colour spectrum.
# At 5 m/s (~18 km/h) directly against you, cycling effort increases noticeably.

HEADWIND_SCALE_MS = 5.0

# --- Wind sampling along a route ------------------------------------------
# Open-Meteo's grid resolution is ~9 km. Sampling more densely than the
# grid spacing returns the same cell repeatedly, so 5 km is the sweet spot:
# fine enough to capture wind variation, not so fine that it's redundant.

SAMPLE_SPACING_KM = 5    # target distance between wind samples
MIN_SAMPLES       = 3    # always at least start, middle, end
MAX_SAMPLES       = 25   # cap to avoid excessive API calls on very long routes
