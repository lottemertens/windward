// ── Map setup ─────────────────────────────────────────────────────────
const map = L.map('map').setView([52.3, 5.3], 8);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);


// ── DOM references ────────────────────────────────────────────────────
const datetimeInput  = document.getElementById('datetime-input');
const windCompass    = document.getElementById('wind-compass');
const windLabel      = document.getElementById('wind-label');
const windDateNote   = document.getElementById('wind-date-note');
const instructions   = document.getElementById('instructions');
const calculateBtn   = document.getElementById('calculate-btn');
const undoBtn        = document.getElementById('undo-btn');
const resetBtn       = document.getElementById('reset-btn');
const statusDiv      = document.getElementById('status');
const summaryCard    = document.getElementById('summary-card');
const loadingOverlay = document.getElementById('loading-overlay');
const routeInfo      = document.getElementById('route-info');

// Upload tab
const tabPlan    = document.getElementById('tab-plan');
const tabUpload  = document.getElementById('tab-upload');
const panelPlan  = document.getElementById('panel-plan');
const panelUpload = document.getElementById('panel-upload');
const gpxInput   = document.getElementById('gpx-input');
const fileNameEl = document.getElementById('file-name');
const uploadBtn  = document.getElementById('upload-btn');
const windBtn    = document.getElementById('wind-btn');
const exportBtn  = document.getElementById('export-btn');
const btnRoad           = document.getElementById('btn-road');
const btnRegular        = document.getElementById('btn-regular');
const addressInput      = document.getElementById('address-input');
const addressResults    = document.getElementById('address-results');
const rerouteBar        = document.getElementById('reroute-bar');
const acceptRerouteBtn  = document.getElementById('accept-reroute-btn');
const discardRerouteBtn = document.getElementById('discard-reroute-btn');
const avoidAllBtn       = document.getElementById('avoid-all-btn');


// ── App state ─────────────────────────────────────────────────────────
// planPoints holds all clicked points in order: [start, ...via, end]
// Each entry: { lat, lng, marker, addedAt }
// addedAt is a monotonic counter used by undo to find the most recently added point.
let selectedProfile   = 'cycling-road';   // matches the default in RouteRequest
let planPoints        = [];
let pointCounter      = 0;
let routeLayers       = [];
let arrowLayers       = [];
let uploadedWaypoints = null;   // set when a GPX is loaded
let lastRouteSegments = [];     // stored after each calculation, used for GPX export

// ── Reroute avoidance state ────────────────────────────────────────────
// avoidedClosures: array of closure objects the user has chosen to avoid.
// previewLayers:   the proposed reroute polylines shown during preview.
// previewSegments: the segments of the proposed reroute (needed for closure filtering).
// previewArrows:   wind_arrows for the proposed reroute (drawn on Accept).
// allClosuresCache: session-level cache of the full /api/closures response.
//   Populated on page load (background pre-warm) and reused on every route.
//   No need to persist across page reloads — the server refreshes daily.
let avoidedClosures  = [];
let previewLayers    = [];
let previewSegments  = [];
let previewArrows    = [];
let allClosuresCache = null;

// Default datetime to current time rounded to the nearest hour
const now = new Date();
now.setMinutes(0, 0, 0);
datetimeInput.value = now.toISOString().slice(0, 16);


// ── Constants ─────────────────────────────────────────────────────────
const HEADWIND_SCALE         = 5.0;   // m/s — mirrors HEADWIND_SCALE_MS in config.py
const CLOSURE_BUFFER_M       = 10;    // metres from route line to include a road closure
const GHOST_ROUTE_OPACITY    = 0.40;  // opacity of the old route during reroute preview
const GHOST_ARROW_OPACITY    = 0.25;  // arrows are less important so fade them more

function windColour(headwindMs) {
  const t = Math.max(-1, Math.min(1, headwindMs / HEADWIND_SCALE));
  if (t <= 0) return `rgb(${Math.round(255 * (t + 1))}, 200, 0)`;
  return `rgb(255, ${Math.round(200 * (1 - t))}, 0)`;
}


// ── Wind overview ─────────────────────────────────────────────────────
async function fetchWindOverview() {
  const datetime = datetimeInput.value + ':00';
  windLabel.innerHTML = 'Loading…';
  windDateNote.textContent = '';

  if (new Date(datetimeInput.value) < new Date()) {
    windDateNote.textContent = 'Showing historical wind data.';
  }

  try {
    const res  = await fetch(`/api/wind?datetime_iso=${encodeURIComponent(datetime)}`);
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail); }
    renderWindWidget(await res.json());
  } catch (err) {
    windCompass.innerHTML = '';
    windLabel.textContent = err.message;
  }
}

function renderWindWidget(data) {
  const arrowTo = (data.direction_deg + 180) % 360;
  windCompass.innerHTML = `
    <svg width="48" height="48" viewBox="0 0 48 48">
      <polygon points="24,4 38,42 24,32 10,42"
               fill="white" stroke="#334155" stroke-width="2.5" stroke-linejoin="round"
               transform="rotate(${arrowTo}, 24, 24)"/>
    </svg>`;
  windLabel.innerHTML = `
    <strong>${data.speed_ms} m/s</strong>
    from ${degreesToCompass(data.direction_deg)}<br>
    <span style="font-size:0.75rem;color:#94a3b8">${data.location}</span>`;
}

function degreesToCompass(deg) {
  const d = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
  return d[Math.round(deg / 22.5) % 16];
}

fetchWindOverview();
datetimeInput.addEventListener('change', fetchWindOverview);

// Warm the closure cache silently on page load. The NDW feed takes ~25 s to
// fetch and parse on the first request; doing it now means closures appear
// immediately after the first route calculation instead of after a long wait.
// We also store the result in allClosuresCache so subsequent calls (e.g. after
// accepting a reroute) don't re-fetch and lose the data.
fetch('/api/closures')
  .then(r => r.ok ? r.json() : Promise.reject())
  .then(data => { allClosuresCache = data; })
  .catch(() => {});


// ── Tab switching ─────────────────────────────────────────────────────
tabPlan.addEventListener('click', () => {
  tabPlan.classList.add('active');
  tabUpload.classList.remove('active');
  panelPlan.classList.remove('hidden');
  panelUpload.classList.add('hidden');
});

tabUpload.addEventListener('click', () => {
  tabUpload.classList.add('active');
  tabPlan.classList.remove('active');
  panelUpload.classList.remove('hidden');
  panelPlan.classList.add('hidden');
});


// ── Address search ────────────────────────────────────────────────────
let searchTimer = null;

addressInput.addEventListener('input', () => {
  clearTimeout(searchTimer);
  const q = addressInput.value.trim();
  if (q.length < 3) { hideResults(); return; }
  // Wait 350 ms after the user stops typing before sending a request.
  searchTimer = setTimeout(() => fetchAddresses(q), 350);
});

// Hide the dropdown when the user clicks anywhere else on the page.
document.addEventListener('click', (e) => {
  if (!e.target.closest('.address-search')) hideResults();
});

async function fetchAddresses(q) {
  try {
    const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
    if (!res.ok) return;
    const results = await res.json();
    showResults(results);
  } catch { /* network error — silently ignore */ }
}

function showResults(results) {
  addressResults.innerHTML = '';
  if (!results.length) { hideResults(); return; }

  results.forEach(r => {
    const li = document.createElement('li');
    li.textContent = r.name;
    li.title       = r.full;   // full address on hover
    li.addEventListener('click', () => {
      selectAddress(r.lat, r.lon, r.name);
      addressInput.value = '';
      hideResults();
    });
    addressResults.appendChild(li);
  });

  addressResults.classList.remove('hidden');
}

function hideResults() {
  addressResults.classList.add('hidden');
  addressResults.innerHTML = '';
}

/**
 * Add a waypoint from an address search result — same logic as a map click
 * but the coordinates come from the geocoder instead of the mouse position.
 */
function selectAddress(lat, lng, label) {
  if (planPoints.length === 0) {
    const marker = makePlanMarker(lat, lng, 'start');
    planPoints.push({ lat, lng, marker, addedAt: pointCounter++ });
    refreshMarkerLabels();
    map.setView([lat, lng], 14);
    updateInstructions();
    updateUndoBtn();

  } else if (planPoints.length === 1) {
    const marker = makePlanMarker(lat, lng, 'end');
    planPoints.push({ lat, lng, marker, addedAt: pointCounter++ });
    refreshMarkerLabels();
    calculateBtn.disabled = false;
    map.setView([lat, lng], 12);
    updateInstructions();
    updateUndoBtn();
    calculateOrsRoute();

  } else {
    const insertIdx = nearestSegmentIndex({ lat, lng }) + 1;
    const marker = makePlanMarker(lat, lng, 'via');
    planPoints.splice(insertIdx, 0, { lat, lng, marker, addedAt: pointCounter++ });
    refreshMarkerLabels();
    updateUndoBtn();
    calculateOrsRoute();
  }
}


// ── Bike type toggle ──────────────────────────────────────────────────
btnRoad.addEventListener('click', () => {
  selectedProfile = 'cycling-road';
  btnRoad.classList.add('active');
  btnRegular.classList.remove('active');
  if (planPoints.length >= 2) calculateOrsRoute();
});

btnRegular.addEventListener('click', () => {
  selectedProfile = 'cycling-regular';
  btnRegular.classList.add('active');
  btnRoad.classList.remove('active');
  if (planPoints.length >= 2) calculateOrsRoute();
});


// ── Plan tab: map click → add waypoint ───────────────────────────────
map.on('click', (e) => {
  if (panelPlan.classList.contains('hidden')) return; // upload tab is active, ignore map clicks
  const { lat, lng } = e.latlng;

  if (planPoints.length === 0) {
    // First click: start
    const marker = makePlanMarker(lat, lng, 'start');
    planPoints.push({ lat, lng, marker, addedAt: pointCounter++ });
    refreshMarkerLabels();
    updateInstructions();
    updateUndoBtn();

  } else if (planPoints.length === 1) {
    // Second click: end — add and auto-calculate
    const marker = makePlanMarker(lat, lng, 'end');
    planPoints.push({ lat, lng, marker, addedAt: pointCounter++ });
    refreshMarkerLabels();
    calculateBtn.disabled = false;
    updateInstructions();
    updateUndoBtn();
    calculateOrsRoute();

  } else {
    // Third+ click: via point — insert into the nearest segment
    const insertIdx = nearestSegmentIndex({ lat, lng }) + 1;
    const marker = makePlanMarker(lat, lng, 'via');
    planPoints.splice(insertIdx, 0, { lat, lng, marker, addedAt: pointCounter++ });
    refreshMarkerLabels();
    updateUndoBtn();
    calculateOrsRoute();
  }
});

// ── Nearest-segment geometry ──────────────────────────────────────────

/**
 * Squared distance from point p to the line segment a→b, in lat/lng space
 * corrected for longitude compression at the current latitude.
 * Returns a value only meaningful for comparison, not as a real distance.
 */
function distToSegmentSq(p, a, b) {
  const cosLat = Math.cos(p.lat * Math.PI / 180);
  const dx = b.lat - a.lat;
  const dy = (b.lng - a.lng) * cosLat;
  const lenSq = dx * dx + dy * dy;
  let t = lenSq > 0
    ? ((p.lat - a.lat) * dx + (p.lng - a.lng) * cosLat * dy) / lenSq
    : 0;
  t = Math.max(0, Math.min(1, t));
  const dLat = p.lat - (a.lat + t * (b.lat - a.lat));
  const dLng = (p.lng - (a.lng + t * (b.lng - a.lng))) * cosLat;
  return dLat * dLat + dLng * dLng;
}

/**
 * Return the index i of the planPoints segment (i → i+1) that is closest
 * to the clicked point. A new via point should be inserted at i+1.
 */
function nearestSegmentIndex(p) {
  let bestIdx  = planPoints.length - 2;  // fallback: just before end
  let bestDist = Infinity;
  for (let i = 0; i < planPoints.length - 1; i++) {
    const d = distToSegmentSq(p, planPoints[i], planPoints[i + 1]);
    if (d < bestDist) { bestDist = d; bestIdx = i; }
  }
  return bestIdx;
}

/** Build a divIcon for a plan waypoint with label S / 1 / 2 / E. */
function makePlanIcon(type, label) {
  return L.divIcon({
    className:   '',   // suppress Leaflet's default white box
    html:        `<div class="plan-marker plan-marker-${type}">${label}</div>`,
    iconSize:    [32, 32],
    iconAnchor:  [16, 16],
    popupAnchor: [0, -18],
  });
}

const POPUP_LABELS = { start: 'Start', end: 'End', via: 'Via point' };

/**
 * Re-label every marker so numbers and colours stay correct after adds/removes.
 * Also refreshes popup content so "Start"/"End" labels update if points shift.
 */
function refreshMarkerLabels() {
  const n = planPoints.length;
  planPoints.forEach((point, idx) => {
    let label, type;
    if (idx === 0)          { label = 'S'; type = 'start'; }
    else if (idx === n - 1) { label = 'E'; type = 'end'; }
    else                    { label = String(idx); type = 'via'; }
    point.marker.setIcon(makePlanIcon(type, label));
    point.marker.setPopupContent(markerPopupHtml(POPUP_LABELS[type]));
  });
}

function markerPopupHtml(label) {
  return `<div class="marker-popup">
    <strong>${label}</strong>
    <button class="popup-remove-btn">Remove</button>
  </div>`;
}

/**
 * Create a waypoint marker using L.marker + divIcon so it lands in Leaflet's
 * markerPane (z-index 600) — naturally above route polylines (overlayPane, 400).
 * All markers (start, via, end) have a Remove button in their popup.
 */
function makePlanMarker(lat, lng, type) {
  const marker = L.marker([lat, lng], {
    icon:          makePlanIcon(type, ''),   // label set immediately by refreshMarkerLabels()
    draggable:     true,
    zIndexOffset:  1000,   // always above wind arrow markers
  }).addTo(map);

  // Update coordinates and recalculate when the marker is dragged to a new position.
  marker.on('dragend', () => {
    const { lat: newLat, lng: newLng } = marker.getLatLng();
    const point = planPoints.find(p => p.marker === marker);
    if (point) {
      point.lat = newLat;
      point.lng = newLng;
      if (planPoints.length >= 2) calculateOrsRoute();
    }
  });

  marker.bindPopup(markerPopupHtml(POPUP_LABELS[type]));
  marker.on('popupopen', (e) => {
    e.popup.getElement().querySelector('.popup-remove-btn').onclick = () => {
      marker.closePopup();
      const point = planPoints.find(p => p.marker === marker);
      if (point) removePlanPoint(point);
    };
  });

  return marker;
}

/**
 * Remove a plan point from the map and array, then recalculate or reset UI.
 */
function removePlanPoint(point) {
  map.removeLayer(point.marker);
  planPoints.splice(planPoints.indexOf(point), 1);
  refreshMarkerLabels();

  if (planPoints.length >= 2) {
    clearRoute();
    calculateOrsRoute();
  } else {
    clearRoute();
    calculateBtn.disabled = true;
    summaryCard.classList.add('hidden');
    routeInfo.classList.add('hidden');
    updateInstructions();
  }
  updateUndoBtn();
}

/**
 * Undo the last-added waypoint by finding the highest addedAt counter.
 * This is correct regardless of where in the array the point was inserted.
 */
function undoLastPoint() {
  if (planPoints.length === 0) return;
  const toRemove = planPoints.reduce((latest, p) =>
    p.addedAt > latest.addedAt ? p : latest
  );
  removePlanPoint(toRemove);
}

function updateInstructions() {
  if (planPoints.length === 0) {
    instructions.innerHTML = 'Search an address or click the map to set a <strong>start point</strong>.';
  } else if (planPoints.length === 1) {
    instructions.innerHTML = 'Search an address or click the map to set an <strong>end point</strong>.';
  } else {
    instructions.textContent = 'Click to add a via point. Drag any marker to reposition it.';
  }
}

function updateUndoBtn() {
  undoBtn.disabled = planPoints.length === 0;
}

// Fetch a route from the API. avoidGeometries is an optional array of
// [[lat,lon],...] closure geometries to pass as avoid_polygons to ORS.
async function _fetchRoute(avoidGeometries = []) {
  const res = await fetch('/api/route', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      waypoints:        planPoints.map(p => ({ lat: p.lat, lon: p.lng })),
      datetime_iso:     datetimeInput.value + ':00',
      profile:          selectedProfile,
      avoid_geometries: avoidGeometries,
    }),
  });
  if (!res.ok) { const e = await res.json(); throw new Error(e.detail); }
  return res.json();
}

async function calculateOrsRoute() {
  setLoading(true);
  discardPreview();   // cancel any pending preview before a full recalculate

  try {
    const data = await _fetchRoute(avoidedClosures.map(c => c.geometry));

    // Only clear the old route once we know the new one succeeded —
    // this way a failed API call leaves the existing route visible.
    clearRoute();
    drawRoute(data.segments);
    drawWindArrows(data.wind_arrows);
    showSummary(data.segments);
    showRouteInfo(data.route_info);
    exportBtn.classList.remove('hidden');

  } catch (err) {
    statusDiv.textContent = `⚠ ${err.message}`;
  } finally {
    setLoading(false);
  }
}

// ── Reroute preview ────────────────────────────────────────────────────

async function avoidClosure(closure) {
  if (planPoints.length < 2) return;
  if (avoidedClosures.some(c => c.situation_id === closure.situation_id)) return;

  avoidedClosures.push(closure);
  setLoading(true);

  try {
    const data = await _fetchRoute(avoidedClosures.map(c => c.geometry));

    // Fade the current route so the user can compare
    routeLayers.forEach(l => l.setStyle({ opacity: GHOST_ROUTE_OPACITY, weight: 5 }));
    arrowLayers.forEach(l => l.setOpacity(GHOST_ARROW_OPACITY));

    // Draw proposed reroute on top
    previewSegments = data.segments;
    previewArrows   = data.wind_arrows;
    previewLayers = _drawSegmentPolylines(data.segments, 0.9);

    rerouteBar.classList.remove('hidden');

  } catch (err) {
    avoidedClosures.pop();   // roll back since the request failed
    statusDiv.textContent = `⚠ Could not reroute: ${err.message}`;
  } finally {
    setLoading(false);
  }
}

function acceptPreview() {
  if (!previewLayers.length) return;

  // Remove ghost route and arrows, make preview the new main route
  routeLayers.forEach(l => map.removeLayer(l));
  arrowLayers.forEach(l => map.removeLayer(l));
  routeLayers       = previewLayers;
  arrowLayers       = [];
  previewLayers     = [];
  lastRouteSegments = previewSegments;
  previewSegments   = [];

  // Draw the wind arrows that were fetched for the preview route
  drawWindArrows(previewArrows);
  previewArrows = [];

  rerouteBar.classList.add('hidden');

  // Refilter closures using the cached data — no re-fetch needed
  loadAndFilterClosures(lastRouteSegments);
  showSummary(lastRouteSegments);
}

function discardPreview() {
  if (!previewLayers.length) return;

  // Remove the preview route
  previewLayers.forEach(l => map.removeLayer(l));
  previewLayers   = [];
  previewSegments = [];
  previewArrows   = [];

  // Remove the closure we just added and restore route opacity
  avoidedClosures.pop();
  routeLayers.forEach(l => l.setStyle({ opacity: 0.9, weight: 5 }));
  arrowLayers.forEach(l => l.setOpacity(1));

  rerouteBar.classList.add('hidden');
}

async function avoidAllClosures() {
  if (!closureLayers.length) return;
  // Collect all closures currently shown on the route
  // closureLayers stores the markers; we stored the closure data on each marker
  const newToAvoid = currentRouteClosures.filter(
    c => !avoidedClosures.some(a => a.situation_id === c.situation_id)
  );
  if (!newToAvoid.length) return;
  newToAvoid.forEach(c => avoidedClosures.push(c));

  setLoading(true);
  try {
    const data = await _fetchRoute(avoidedClosures.map(c => c.geometry));
    routeLayers.forEach(l => l.setStyle({ opacity: GHOST_ROUTE_OPACITY, weight: 5 }));
    arrowLayers.forEach(l => l.setOpacity(GHOST_ARROW_OPACITY));
    previewSegments = data.segments;
    previewArrows   = data.wind_arrows;
    previewLayers = _drawSegmentPolylines(data.segments, 0.9);
    rerouteBar.classList.add('hidden'); // will re-show below
    rerouteBar.classList.remove('hidden');
  } catch (err) {
    newToAvoid.forEach(() => avoidedClosures.pop());
    statusDiv.textContent = `⚠ Could not reroute: ${err.message}`;
  } finally {
    setLoading(false);
  }
}

function stopAvoiding(closureId) {
  const idx = avoidedClosures.findIndex(c => c.situation_id === closureId);
  if (idx === -1) return;
  avoidedClosures.splice(idx, 1);
  // Trigger a full recalculate without this avoid (no preview — just recalculate)
  calculateOrsRoute();
}

acceptRerouteBtn.addEventListener('click',  acceptPreview);
discardRerouteBtn.addEventListener('click', discardPreview);
avoidAllBtn.addEventListener('click', avoidAllClosures);

// Manual recalculate button (useful after changing departure time)
calculateBtn.addEventListener('click', () => { avoidedClosures = []; calculateOrsRoute(); });
undoBtn.addEventListener('click', undoLastPoint);


// ── Upload tab: GPX → route, then separate wind calculation ──────────
gpxInput.addEventListener('change', () => {
  if (gpxInput.files.length > 0) {
    fileNameEl.textContent = gpxInput.files[0].name;
    uploadBtn.disabled = false;
  }
});

uploadBtn.addEventListener('click', async () => {
  if (!gpxInput.files.length) return;
  setLoading(true);
  clearRoute();

  const formData = new FormData();
  formData.append('file', gpxInput.files[0]);

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail); }
    const data = await res.json();

    uploadedWaypoints = data.waypoints;
    drawUploadedRoute(data.waypoints);
    showRouteInfo(data.route_info);

    // Show the "Calculate wind" button now that we have a route
    windBtn.classList.remove('hidden');
    windBtn.disabled = false;

  } catch (err) {
    statusDiv.textContent = `⚠ ${err.message}`;
  } finally {
    setLoading(false);
  }
});

windBtn.addEventListener('click', async () => {
  if (!uploadedWaypoints) return;
  setLoading(true);
  clearArrows();
  summaryCard.classList.add('hidden');

  try {
    const res = await fetch('/api/wind-overlay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        waypoints:    uploadedWaypoints,
        datetime_iso: datetimeInput.value + ':00',
      }),
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail); }
    const data = await res.json();

    drawRoute(data.segments);
    drawWindArrows(data.wind_arrows);
    showSummary(data.segments);
    exportBtn.classList.remove('hidden');

  } catch (err) {
    statusDiv.textContent = `⚠ ${err.message}`;
  } finally {
    setLoading(false);
  }
});


// ── Reset ─────────────────────────────────────────────────────────────
resetBtn.addEventListener('click', () => {
  planPoints.forEach(p => map.removeLayer(p.marker));
  planPoints = [];
  pointCounter = 0;
  clearRoute();
  uploadedWaypoints = null;
  calculateBtn.disabled = true;
  undoBtn.disabled = true;
  windBtn.classList.add('hidden');
  summaryCard.classList.add('hidden');
  routeInfo.classList.add('hidden');
  updateInstructions();
  statusDiv.textContent = '';
  gpxInput.value = '';
  fileNameEl.textContent = 'Choose .gpx file…';
  uploadBtn.disabled = true;
});


// ── Export GPX ───────────────────────────────────────────────────────
exportBtn.addEventListener('click', exportGpx);

function exportGpx() {
  if (!lastRouteSegments.length) return;

  // Reconstruct the ordered point list from the segments.
  // Each segment shares its end with the next segment's start, so we take
  // the start of every segment plus the end of the last one.
  const points = [
    lastRouteSegments[0].start,
    ...lastRouteSegments.map(s => s.end),
  ];

  const trkpts = points
    .map(p => `      <trkpt lat="${p.lat.toFixed(6)}" lon="${p.lon.toFixed(6)}"></trkpt>`)
    .join('\n');

  const datetime = datetimeInput.value || new Date().toISOString().slice(0, 16);
  const gpx = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="KomootLayer" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata>
    <name>KomootLayer route</name>
    <time>${datetime}:00</time>
  </metadata>
  <trk>
    <name>KomootLayer route</name>
    <trkseg>
${trkpts}
    </trkseg>
  </trk>
</gpx>`;

  const blob = new Blob([gpx], { type: 'application/gpx+xml' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'komootlayer-route.gpx';
  a.click();
  URL.revokeObjectURL(url);
}


// ── Drawing helpers ───────────────────────────────────────────────────

// Draw segment polylines and return the layer array. Used by both the main
// route and the reroute preview so the logic lives in one place.
function _drawSegmentPolylines(segments, opacity = 0.9) {
  return segments.map(seg =>
    L.polyline(
      [[seg.start.lat, seg.start.lon], [seg.end.lat, seg.end.lon]],
      { color: windColour(seg.headwind_ms), weight: 5, opacity }
    ).addTo(map)
  );
}

function drawRoute(segments) {
  lastRouteSegments = segments;   // stored for GPX export
  routeLayers = _drawSegmentPolylines(segments);
  if (segments.length) {
    const pts = segments.flatMap(s => [[s.start.lat, s.start.lon], [s.end.lat, s.end.lon]]);
    map.fitBounds(pts, { padding: [40, 40] });
  }
  loadAndFilterClosures(segments);
}

function drawUploadedRoute(waypoints) {
  // Draw a plain grey route before wind is calculated
  const latlngs = waypoints.map(w => [w.lat, w.lon]);
  routeLayers.push(L.polyline(latlngs, { color: '#888', weight: 4, opacity: 0.7 }).addTo(map));
  map.fitBounds(latlngs, { padding: [40, 40] });
}

function drawWindArrows(arrows) {
  arrows.forEach(arrow => {
    const arrowTo = (arrow.direction_deg + 180) % 360;
    const opacity = Math.min(1, 0.4 + arrow.speed_ms / HEADWIND_SCALE * 0.6);
    const icon = L.divIcon({
      className: 'wind-arrow-icon',
      html: `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28"
                  style="opacity:${opacity.toFixed(2)}">
               <polygon points="14,2 22,24 14,18 6,24"
                        fill="white" stroke="#333" stroke-width="2" stroke-linejoin="round"
                        transform="rotate(${arrowTo}, 14, 14)"/>
             </svg>`,
      iconSize: [28, 28], iconAnchor: [14, 14],
    });
    arrowLayers.push(
      L.marker([arrow.lat, arrow.lon], { icon })
        .bindPopup(`Wind: ${arrow.speed_ms} m/s from ${arrow.direction_deg}°`)
        .addTo(map)
    );
  });
}

function clearRoute() {
  previewLayers.forEach(l => map.removeLayer(l)); previewLayers = [];
  routeLayers.forEach(l => map.removeLayer(l));   routeLayers   = [];
  lastRouteSegments = [];
  previewSegments   = [];
  rerouteBar.classList.add('hidden');
  exportBtn.classList.add('hidden');
  avoidAllBtn.classList.add('hidden');
  clearArrows();
  clearClosures();
}

function clearArrows() {
  arrowLayers.forEach(l => map.removeLayer(l)); arrowLayers = [];
}


// ── Summary & route info ──────────────────────────────────────────────
function showSummary(segments) {
  const total  = segments.length;
  const tailN  = segments.filter(s => s.headwind_ms < -0.5).length;
  const headN  = segments.filter(s => s.headwind_ms >  0.5).length;
  const crossN = total - tailN - headN;

  const tp = pct(tailN, total), hp = pct(headN, total), cp = pct(crossN, total);
  document.getElementById('pct-tail').textContent  = tp;
  document.getElementById('pct-cross').textContent = cp;
  document.getElementById('pct-head').textContent  = hp;
  document.getElementById('summary-tailwind').style.width  = tp + '%';
  document.getElementById('summary-cross').style.width     = cp + '%';
  document.getElementById('summary-headwind').style.width  = hp + '%';
  summaryCard.classList.remove('hidden');
}

function showRouteInfo(info) {
  document.getElementById('val-distance').textContent = info.distance_km;
  routeInfo.classList.remove('hidden');

  const surfEl   = document.getElementById('val-surfaces');
  const surfItem = document.getElementById('info-surfaces');
  if (info.surfaces && info.surfaces.length > 0) {
    surfEl.textContent = info.surfaces
      .map(s => `${s.name} ${s.percentage}%`)
      .join(' · ');
    surfItem.classList.remove('hidden');
  } else {
    surfItem.classList.add('hidden');
  }

  const warnEl   = document.getElementById('val-warnings');
  const warnItem = document.getElementById('info-warnings');
  if (info.warnings && info.warnings.length > 0) {
    warnEl.textContent = info.warnings.join(' · ');
    warnItem.classList.remove('hidden');
  } else {
    warnItem.classList.add('hidden');
  }
}

function setLoading(on) {
  loadingOverlay.classList.toggle('hidden', !on);
  calculateBtn.disabled = on;
  undoBtn.disabled      = on || planPoints.length === 0;
  uploadBtn.disabled    = on;
  windBtn.disabled      = on;
  statusDiv.textContent = '';
}

function pct(count, total) {
  return total > 0 ? Math.round(100 * count / total) : 0;
}


// ── Road closures ─────────────────────────────────────────────────────────────
// Closures are only shown after a route is calculated, filtered to those within
// CLOSURE_BUFFER_M metres of the route. All 8 000+ NL closures are in the
// server cache; filtering happens here in JS (~5 ms) so no extra API call is
// needed per route change.

let closureLayers        = [];
let currentRouteClosures = [];   // closures currently shown on the route (for "Avoid all")

// ── Geometry helper ───────────────────────────────────────────────────────────
// Returns the distance in metres from point p to the line segment a→b.
// Uses a flat-earth approximation with cosLat correction, accurate enough
// for the short segments we're dealing with.
function distPointToSegmentM(p, a, b) {
  const cosLat = Math.cos(p.lat * Math.PI / 180);
  const px = (p.lon - a.lon) * cosLat;
  const py =  p.lat - a.lat;
  const bx = (b.lon - a.lon) * cosLat;
  const by =  b.lat - a.lat;
  const len2 = bx * bx + by * by;

  let t = len2 > 0 ? Math.max(0, Math.min(1, (px * bx + py * by) / len2)) : 0;

  const dx = px - t * bx;
  const dy = py - t * by;
  return Math.sqrt(dx * dx + dy * dy) * 111_319;   // degrees → metres
}

function isClosureOnRoute(closure, segments) {
  return segments.some(seg =>
    distPointToSegmentM(
      { lat: closure.lat, lon: closure.lon },
      seg.start,
      seg.end,
    ) <= CLOSURE_BUFFER_M
  );
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function makeClosureIcon() {
  return L.divIcon({
    className: '',
    html: `<div class="closure-marker" title="Road closure">✕</div>`,
    iconSize:    [22, 22],
    iconAnchor:  [11, 11],
    popupAnchor: [0, -12],
  });
}

function formatClosureDate(iso) {
  if (!iso) return 'onbekend';
  return new Date(iso).toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', year: 'numeric' });
}

function closurePopupHtml(c) {
  const isAvoided = avoidedClosures.some(a => a.situation_id === c.situation_id);
  const bikeTag   = c.bicycle_specific
    ? `<span class="closure-tag closure-tag-bike">🚲 fietsers</span>` : '';
  const warning   = c.warning
    ? `<p class="closure-warning">${c.warning}</p>` : '';
  const name      = c.project_name
    ? `<p class="closure-project">${c.project_name}</p>` : '';
  const urlLink   = c.url
    ? `<p class="closure-url"><a href="${c.url}" target="_blank" rel="noopener">Meer info ↗</a></p>` : '';
  const actionBtn = isAvoided
    ? `<button class="closure-action-btn closure-stop-btn" data-id="${c.situation_id}">Stop avoiding</button>`
    : `<button class="closure-action-btn closure-avoid-btn" data-id="${c.situation_id}">⛔ Avoid this</button>`;

  return `
    <div class="closure-popup">
      <strong>Weg afgesloten ${bikeTag}</strong>
      ${warning}
      <p class="closure-source">${c.source}</p>
      ${name}
      ${urlLink}
      <p class="closure-dates">📅 ${formatClosureDate(c.start)} – ${formatClosureDate(c.end)}</p>
      ${actionBtn}
    </div>`;
}

let closureHighlight = null;   // the currently drawn road geometry highlight

function clearClosureHighlight() {
  if (closureHighlight) { map.removeLayer(closureHighlight); closureHighlight = null; }
}

function clearClosures() {
  clearClosureHighlight();
  closureLayers.forEach(l => map.removeLayer(l));
  closureLayers = [];
}

function renderClosures(closures) {
  clearClosures();
  currentRouteClosures = closures;
  avoidAllBtn.classList.toggle('hidden', closures.length === 0);

  closures.forEach(c => {
    const marker = L.marker([c.lat, c.lon], {
      icon:         makeClosureIcon(),
      zIndexOffset: 500,   // above route polylines, below waypoint markers (1000)
    });

    // Regenerate popup content on open so avoid/stop button reflects current state
    marker.bindPopup('', { maxWidth: 280 });
    marker.on('popupopen', (e) => {
      const html = closurePopupHtml(c);
      e.popup.setContent(html);

      // Wire up the action button inside the popup
      const btn = e.popup.getElement().querySelector('.closure-action-btn');
      if (btn) {
        btn.onclick = () => {
          marker.closePopup();
          if (btn.classList.contains('closure-avoid-btn')) {
            avoidClosure(c);
          } else {
            stopAvoiding(c.situation_id);
          }
        };
      }

      // Draw road geometry highlight
      if (c.geometry && c.geometry.length > 1) {
        clearClosureHighlight();
        closureHighlight = L.polyline(c.geometry, {
          color: '#dc2626', weight: 7, opacity: 0.85, dashArray: '10, 6',
        }).addTo(map);
      }
    });
    marker.on('popupclose', clearClosureHighlight);

    marker.addTo(map);
    closureLayers.push(marker);
  });
}

async function loadAndFilterClosures(segments) {
  if (!segments.length) return;
  try {
    // Use the session cache if available — avoids a re-fetch after accepting a
    // reroute and prevents closures from flickering off then (maybe) back on.
    if (!allClosuresCache) {
      const res = await fetch('/api/closures');
      if (!res.ok) return;
      allClosuresCache = await res.json();
    }
    const onRoute = allClosuresCache.filter(c => isClosureOnRoute(c, segments));
    renderClosures(onRoute);
  } catch (_) {
    // Network error — closures are best-effort
  }
}
