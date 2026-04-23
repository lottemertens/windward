"""
Project-wide configuration constants.

All magic numbers and tuning parameters live here — not scattered across
modules. To adjust behaviour, change a value here and it takes effect
everywhere that uses it.
"""

# --- OpenRouteService (ORS) -----------------------------------------------

ORS_BASE_URL             = "https://api.openrouteservice.org/v2/directions"
ORS_GEOCODE_URL          = "https://api.openrouteservice.org/geocode/search"
# cycling-regular is used for all routes. cycling-road was considered but it
# deprioritises dedicated cycling infrastructure (highway=cycleway) in favour of
# named streets, which produces worse results in the Netherlands where cycling
# paths are extensive and well-mapped.
CYCLING_PROFILE          = "cycling-regular"
# "recommended" weighs cycling suitability and infrastructure rather than
# minimising travel time ("fastest" is deprecated for cycling profiles in ORS).
ORS_ROUTE_PREFERENCE     = "recommended"
ORS_AVOID_FEATURES       = ["steps", "ferries"]

# --- Open-Meteo ------------------------------------------------------------

OPEN_METEO_URL         = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"  # for past dates
OPEN_METEO_FORECAST_DAYS = 16   # max days ahead the forecast API supports
OPEN_METEO_MAX_RETRIES   = 3    # retry attempts on 429 Too Many Requests
OPEN_METEO_RETRY_DELAY_S = 2.0  # initial back-off delay in seconds (doubles each attempt)

# --- Default location for wind overview -----------------------------------
# Shown in the sidebar before a route is planned.

DEFAULT_LOCATION_LAT  = 52.37
DEFAULT_LOCATION_LON  = 4.89
DEFAULT_LOCATION_NAME = "Amsterdam"

# --- Riding speed and time-dependent wind ---------------------------------
# Used when the user provides their expected speed. The system estimates
# arrival time at each sample point and interpolates the wind forecast for
# that specific moment in time instead of using a single departure-time
# snapshot. Optional — if no speed is given, static wind is used.
#
# SPEED_HEADWIND_FACTOR: m/s of speed lost per m/s of headwind.
#   0.147 empirically calibrated from Garmin ride data (was 0.5).
# MIN_SPEED_KMH: floor speed so the model never gives absurd values.

DEFAULT_SPEED_KMH         = 20
SPEED_HEADWIND_FACTOR     = 0.147
MIN_SPEED_KMH             = 8

# --- Best departure time chart --------------------------------------------
# Hours checked when scoring departure times. Covers a full cycling day
# from early morning to early evening.

DEPARTURE_SCORE_HOUR_START = 6    # inclusive (06:00)
DEPARTURE_SCORE_HOUR_END   = 21   # exclusive  (last bar = 20:00)

# --- Wind arrow display ---------------------------------------------------
# Arrows are shown every ARROW_SPACING_KM along the route, independent of
# how many wind samples were fetched. More frequent than samples is fine
# because values are interpolated from the existing samples — no extra API calls.

ARROW_SPACING_KM = 1.5   # one arrow per 1.5 km
MIN_ARROWS       = 4     # always show at least this many
MAX_ARROWS       = 20    # cap for very long routes

# --- Wind colour scale ----------------------------------------------------
# headwind_ms values are clamped to [-HEADWIND_SCALE_MS, +HEADWIND_SCALE_MS]
# before mapping to the green→yellow→red colour spectrum.
# At 5 m/s (~18 km/h) directly against you, cycling effort increases noticeably.

HEADWIND_SCALE_MS = 5.0

# --- Wind sampling along a route ------------------------------------------
# Open-Meteo's grid resolution is ~9 km. Sampling more densely than the
# grid spacing returns the same cell repeatedly, so 5 km is the sweet spot:
# fine enough to capture wind variation, not so fine that it's redundant.

SAMPLE_SPACING_KM       = 5    # target distance between wind samples
MIN_SAMPLES             = 3    # always at least start, middle, end
MAX_SAMPLES             = 25   # cap to avoid excessive API calls on very long routes
MAX_CONCURRENT_REQUESTS = 5    # cap parallel Open-Meteo calls to avoid 429 rate limiting

# --- NDW road closures --------------------------------------------------------
# The planning feed is 237 MB decompressed. We fetch it once a day and keep
# only the records we care about (carriagewayClosures within the next 7 days).
#
# NDW_NS_* are the XML namespace URIs used in the DATEX II v3 feed.
# NDW_CLOSURE_TYPE is the management-type value that means "road fully closed"
# (as opposed to laneClosures, which is a partial restriction we skip).

NDW_PLANNING_URL    = "https://opendata.ndw.nu/planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz"
NDW_NS_SITUATION    = "http://datex2.eu/schema/3/situation"
NDW_CLOSURE_TYPE    = "carriagewayClosures"

CLOSURE_CACHE_TTL_HOURS  = 23     # refresh the cache if it is older than this
CLOSURE_MAX_DAYS_AHEAD   = 7      # only show closures starting within this many days
CLOSURE_AVOID_BUFFER_DEG = 0.0003 # ~33 m padding around a closure bounding box for ORS avoid_polygons
