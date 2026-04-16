# KomootLayer

A wind-aware cycling route planner. Plan a route on the map and see instantly where you'll face headwind or tailwind — colour-coded per segment, with wind arrows and a breakdown summary.

![Route with wind overlay showing green tailwind and red headwind segments]

---

## Features

- **Plan a route** by clicking start, end, and optional via points on the map
- **Smart via point insertion** — new points are added into the nearest segment, not always at the end
- **Drag waypoints** to reposition them; the route recalculates automatically
- **Upload a GPX** file from Strava, Komoot, or Garmin and overlay wind on your existing route
- **Wind colour scale** — green = tailwind, yellow = crosswind, red = headwind (±5 m/s)
- **Wind arrows** on the map showing direction and strength
- **Wind summary** showing % tailwind / crosswind / headwind
- **Route info** — total distance, surface breakdown (asphalt, gravel, …), ORS warnings (ferries etc.)
- **Download GPX** of the calculated route
- **Historical wind** — works for past dates using the Open-Meteo archive API
- **Mobile-friendly** layout

---

## Setup

**Requirements:** Python 3.9+, a free [OpenRouteService API key](https://openrouteservice.org/)

```bash
# 1. Clone the repo
git clone https://github.com/your-username/KomootLayer.git
cd KomootLayer

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your ORS API key
echo "ORS_API_KEY=your_key_here" > .env
```

---

## Running

```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

Interactive API docs are available at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## Usage

### Plan tab
1. Click the map to place a **start point** (green)
2. Click again to place an **end point** (red) — the route calculates automatically
3. Click anywhere to add **via points** (blue) — inserted into the nearest segment
4. **Drag** any marker to reposition it
5. Click a via marker to **remove** it, or use **Undo** to reverse the last action
6. Change the departure time and hit **Calculate route + wind** to recalculate with different wind

### Upload tab
1. Choose a `.gpx` file exported from Strava, Komoot, Garmin, or similar
2. Click **Load route** to display it on the map
3. Click **Calculate wind** to overlay wind conditions for your chosen departure time

### Export
After any route is calculated, **Download GPX** appears in the summary card. The exported file contains the full route geometry and can be loaded into any cycling device or app.

---

## External APIs

| API | Purpose | Auth |
|-----|---------|------|
| [OpenRouteService](https://openrouteservice.org/) | Cycling route geometry, surface types, warnings | API key in `.env` |
| [Open-Meteo](https://open-meteo.com/) | Wind speed and direction (forecast + historical archive) | None required |

Wind data uses the ECMWF model at ~9 km grid resolution. Forecasts are available up to 16 days ahead; past dates automatically switch to the archive endpoint.

---

## Project structure

```
KomootLayer/
├── src/
│   ├── routing/        # OpenRouteService client
│   ├── weather/        # Open-Meteo client
│   └── analysis/       # Headwind/tailwind calculations (pure Python, no HTTP)
├── app/
│   ├── main.py         # FastAPI — four API endpoints + static file serving
│   └── static/         # Frontend: Leaflet map, plain HTML/CSS/JS, no build step
├── tests/              # pytest — mirrors src/ structure
└── data/               # Sample API responses for offline development
```

---

## Running tests

```bash
source venv/bin/activate
pytest                          # all tests
pytest tests/test_analysis.py -v   # single file
pytest tests/test_analysis.py::test_name -v   # single test
```
