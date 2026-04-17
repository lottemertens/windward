"""
NDW road closures client.

Fetches the NDW planning feed (road works + events) once per day and caches
the filtered result in memory. The raw feed is 237 MB decompressed, so we:
  - Stream-parse with iterparse (never load the full tree into memory)
  - Parse at situation level — one ClosureRecord per situation, combining all
    carriagewayClosures records within it into a single geometry. This prevents
    the same works project from appearing as multiple stop signs on the map.
  - Keep only situations with at least one closure active within CLOSURE_MAX_DAYS_AHEAD

Endpoints that serve closure data call get_closures(), which returns the cache
(or triggers a lazy fetch if the cache is empty or older than CLOSURE_CACHE_TTL_HOURS).
"""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from src.config import (
    NDW_PLANNING_URL,
    NDW_NS_SITUATION,
    NDW_CLOSURE_TYPE,
    CLOSURE_CACHE_TTL_HOURS,
    CLOSURE_MAX_DAYS_AHEAD,
)
from src.models import ClosureRecord

logger = logging.getLogger(__name__)


# ── In-memory cache ───────────────────────────────────────────────────────────
# _Cache is private to this module — it is an implementation detail, not a
# shared domain type, so it lives here rather than in src/models.py.

@dataclass
class _Cache:
    records:    list[ClosureRecord] = field(default_factory=list)
    fetched_at: Optional[datetime]  = None

_cache      = _Cache()
_fetch_lock = asyncio.Lock()


# ── Public API ────────────────────────────────────────────────────────────────

async def get_closures() -> list[ClosureRecord]:
    """Return cached closures, fetching fresh data if the cache is stale."""
    if _is_stale():
        async with _fetch_lock:
            if _is_stale():
                await _refresh()
    return _cache.records


async def force_refresh() -> int:
    """Fetch fresh data unconditionally. Returns the number of records cached."""
    async with _fetch_lock:
        await _refresh()
    return len(_cache.records)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _is_stale() -> bool:
    if _cache.fetched_at is None:
        return True
    age = datetime.now(timezone.utc) - _cache.fetched_at
    return age > timedelta(hours=CLOSURE_CACHE_TTL_HOURS)


# ── Fetch + parse ─────────────────────────────────────────────────────────────

async def _refresh() -> None:
    logger.info("Fetching NDW planning feed…")
    raw = await _download()
    records = await asyncio.to_thread(_parse, raw)
    _cache.records    = records
    _cache.fetched_at = datetime.now(timezone.utc)
    logger.info("NDW cache refreshed: %d closures", len(records))


async def _download() -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.get(NDW_PLANNING_URL, timeout=30.0)
        r.raise_for_status()
    return gzip.decompress(r.content)


def _parse(xml_bytes: bytes) -> list[ClosureRecord]:
    """
    Stream-parse the DATEX II v3 XML at situation level.

    Each <situation> groups all records for one works project. We:
      1. Extract header metadata (project name, warning, URL) from record 0
      2. Collect every carriagewayClosures record that falls within the date window
      3. Combine their geometries into one list
      4. Emit a single ClosureRecord per situation

    This means a project with two affected road sections (e.g. both directions of
    a road) produces exactly one marker, not two.
    """
    today   = date.today()
    cutoff  = today + timedelta(days=CLOSURE_MAX_DAYS_AHEAD)
    results = []
    sit_counter = 0   # stable situation ID within this parse run

    context = ET.iterparse(io.BytesIO(xml_bytes), events=("end",))

    for _event, elem in context:
        if elem.tag.split("}")[-1] != "situation":
            continue

        records = elem.findall(f"{{{NDW_NS_SITUATION}}}situationRecord")
        if not records:
            elem.clear()
            continue

        # ── Header: first record holds project-level metadata ──────────────
        header       = records[0]
        project_name = _comment_text(header, "internalNote")
        warning      = _comment_text(header, "warning")
        url          = _find_text(header, "urlLinkAddress")

        # ── Collect all closure records that overlap [today, cutoff] ───────
        combined_geometry = []
        earliest_start    = date.max
        latest_end        = date.min
        source            = "unknown"
        description_parts = []
        bicycle_specific  = False

        for rec in records[1:]:
            mgmt_elem = _find(rec, "roadOrCarriagewayOrLaneManagementType")
            if mgmt_elem is None or mgmt_elem.text != NDW_CLOSURE_TYPE:
                continue

            start_elem = _find(rec, "overallStartTime")
            end_elem   = _find(rec, "overallEndTime")
            start_d = _parse_date(start_elem.text) if start_elem is not None else date.min
            end_d   = _parse_date(end_elem.text)   if end_elem   is not None else date.max

            if end_d < today or start_d > cutoff:
                continue

            # Accumulate the overall date range across all records
            earliest_start = min(earliest_start, start_d)
            latest_end     = max(latest_end,     end_d)

            # Source — use the first non-unknown value we find
            if source == "unknown":
                src_elem = _find(rec, "value")
                if src_elem is not None and src_elem.text:
                    source = src_elem.text.strip()

            # Geometry — combine all posList coordinates
            pos_elem = _find(rec, "posList")
            if pos_elem is not None and pos_elem.text:
                combined_geometry.extend(_parse_pos_list(pos_elem.text))

            # Description — collect any note-like values
            all_values = [e.text.strip() for e in rec.iter()
                          if e.tag.split("}")[-1] == "value" and e.text]
            description_parts.extend(
                v for v in all_values if len(v) > 5 and v != source
            )

            # Bicycle-specific — True if ANY record targets cyclists
            if _find_text(rec, "vehicleType") == "bicycle":
                bicycle_specific = True

        if not combined_geometry:
            elem.clear()
            continue  # no usable closures in this situation

        # ── Build the single ClosureRecord for this situation ─────────────
        sit_counter += 1

        # Marker at centroid of all geometry points
        lat = sum(p[0] for p in combined_geometry) / len(combined_geometry)
        lon = sum(p[1] for p in combined_geometry) / len(combined_geometry)

        description = "; ".join(dict.fromkeys(description_parts))[:200] or None

        results.append(ClosureRecord(
            situation_id=str(sit_counter),
            lat=lat,
            lon=lon,
            source=source,
            start=earliest_start.isoformat() if earliest_start != date.max else "?",
            end=latest_end.isoformat() if latest_end != date.min else None,
            description=description,
            geometry=combined_geometry,
            warning=warning,
            project_name=project_name,
            url=url,
            bicycle_specific=bicycle_specific,
        ))

        elem.clear()

    return results


# ── XML helpers ───────────────────────────────────────────────────────────────

def _find(elem: ET.Element, local_name: str) -> Optional[ET.Element]:
    for child in elem.iter():
        if child.tag.split("}")[-1] == local_name:
            return child
    return None


def _find_text(elem: ET.Element, local_name: str) -> Optional[str]:
    found = _find(elem, local_name)
    return found.text.strip() if found is not None and found.text else None


def _comment_text(elem: ET.Element, comment_type: str) -> Optional[str]:
    """Return the <value> text from a <generalPublicComment> with the given commentType."""
    for block in elem.iter():
        if block.tag.split("}")[-1] != "generalPublicComment":
            continue
        if _find_text(block, "commentType") == comment_type:
            return _find_text(block, "value")
    return None


def _parse_pos_list(text: str) -> list:
    """Parse a DATEX II posList string into a list of [lat, lon] pairs."""
    parts = text.split()
    result = []
    for i in range(0, len(parts) - 1, 2):
        try:
            result.append([float(parts[i]), float(parts[i + 1])])
        except ValueError:
            continue
    return result


def _parse_date(text: str) -> date:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return date.min
