"""
NDW road closures client.

Fetches the NDW planning feed (road works + events) once per day and caches
the filtered result in memory. The raw feed is 237 MB decompressed, so we:
  - Stream-parse with iterparse (never load the full tree into memory)
  - Parse at situation level so header metadata (project name, warning, URL)
    can be combined with the individual closure records inside that situation
  - Keep only carriagewayClosures active within the next 7 days
  - Store a small list of ClosureRecords — typically a few thousand for all NL

Endpoints that serve closure data call get_closures(), which returns the cache
(or triggers a lazy fetch if the cache is empty or older than CACHE_TTL_HOURS).
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
_fetch_lock = asyncio.Lock()   # prevents duplicate fetches if two requests arrive simultaneously


# ── Public API ────────────────────────────────────────────────────────────────

async def get_closures() -> list[ClosureRecord]:
    """Return cached closures, fetching fresh data if the cache is stale."""
    if _is_stale():
        async with _fetch_lock:
            if _is_stale():          # re-check after acquiring lock
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
    """Download and decompress the gzipped XML feed."""
    async with httpx.AsyncClient() as client:
        r = await client.get(NDW_PLANNING_URL, timeout=30.0)
        r.raise_for_status()
    return gzip.decompress(r.content)


def _parse(xml_bytes: bytes) -> list[ClosureRecord]:
    """
    Stream-parse the DATEX II v3 XML, working at situation level.

    Each <situation> groups all records for one works project. The first record
    is a "header" with the project name, a plain-Dutch warning, and sometimes a
    URL. The remaining records are the individual measures (closures, access
    rules, etc.). We parse header + closures together so the richer metadata is
    available on every ClosureRecord.

    We use iterparse so the full 237 MB tree is never held in memory — each
    <situation> element is processed and immediately discarded.
    """
    today   = date.today()
    cutoff  = today + timedelta(days=CLOSURE_MAX_DAYS_AHEAD)
    results = []

    context = ET.iterparse(io.BytesIO(xml_bytes), events=("end",))

    for _event, elem in context:
        if elem.tag.split("}")[-1] != "situation":
            continue

        records = elem.findall(f"{{{NDW_NS_SITUATION}}}situationRecord")
        if not records:
            elem.clear()
            continue

        # ── Extract header metadata from the first record ──────────────────
        # The first record is always the "umbrella" roadworks record that holds
        # the project name, warning text, and optional URL for the whole situation.
        header       = records[0]
        project_name = _comment_text(header, "internalNote")
        warning      = _comment_text(header, "warning")
        url          = _find_text(header, "urlLinkAddress")

        # ── Extract closure records from the rest ──────────────────────────
        for rec in records[1:]:
            closure = _extract_closure(rec, today, cutoff, project_name, warning, url)
            if closure:
                results.append(closure)

        elem.clear()   # free memory — done with this situation element

    return results


def _extract_closure(
    rec: ET.Element,
    today: date,
    cutoff: date,
    project_name: Optional[str],
    warning: Optional[str],
    url: Optional[str],
) -> Optional[ClosureRecord]:
    """
    Return a ClosureRecord if this record is a full carriagewayClosures that
    overlaps [today, cutoff]. Otherwise return None.
    """
    # ── Management type filter ─────────────────────────────────────────────
    mgmt_elem = _find(rec, "roadOrCarriagewayOrLaneManagementType")
    if mgmt_elem is None or mgmt_elem.text != NDW_CLOSURE_TYPE:
        return None

    # ── Date filter ────────────────────────────────────────────────────────
    start_elem = _find(rec, "overallStartTime")
    end_elem   = _find(rec, "overallEndTime")

    start_d = _parse_date(start_elem.text) if start_elem is not None else date.min
    end_d   = _parse_date(end_elem.text)   if end_elem   is not None else date.max

    if end_d < today or start_d > cutoff:
        return None

    # ── Road geometry ──────────────────────────────────────────────────────
    # The posList contains the full shape of the closed road section as a
    # sequence of "lat lon lat lon …" pairs. We keep all of them so the
    # frontend can draw an accurate highlight on the map.
    pos_elem = _find(rec, "posList")
    if pos_elem is None or not pos_elem.text:
        return None

    geometry = _parse_pos_list(pos_elem.text)
    if not geometry:
        return None

    lat, lon = geometry[0]   # marker placed at the start of the closure

    # ── Source (road manager) ──────────────────────────────────────────────
    source_elem = _find(rec, "value")
    source = source_elem.text.strip() if source_elem is not None else "unknown"

    # ── Description ────────────────────────────────────────────────────────
    # Collect any value text that looks like a human note (not the source name).
    all_values = [e.text.strip() for e in rec.iter() if e.tag.split("}")[-1] == "value" and e.text]
    desc_parts = [v for v in all_values if len(v) > 5 and v != source]
    description = "; ".join(dict.fromkeys(desc_parts))[:200] or None

    # ── Bicycle-specific flag ──────────────────────────────────────────────
    vehicle_type = _find_text(rec, "vehicleType")
    bicycle_specific = vehicle_type == "bicycle"

    return ClosureRecord(
        lat=lat,
        lon=lon,
        source=source,
        start=start_d.isoformat(),
        end=end_d.isoformat() if end_d != date.max else None,
        description=description,
        geometry=geometry,
        warning=warning,
        project_name=project_name,
        url=url,
        bicycle_specific=bicycle_specific,
    )


# ── XML helpers ───────────────────────────────────────────────────────────────

def _find(elem: ET.Element, local_name: str) -> Optional[ET.Element]:
    """Find first descendant by local tag name, ignoring namespace."""
    for child in elem.iter():
        if child.tag.split("}")[-1] == local_name:
            return child
    return None


def _find_text(elem: ET.Element, local_name: str) -> Optional[str]:
    """Find first descendant by local tag name and return its text, or None."""
    found = _find(elem, local_name)
    return found.text.strip() if found is not None and found.text else None


def _comment_text(elem: ET.Element, comment_type: str) -> Optional[str]:
    """
    Find a <generalPublicComment> with the given commentType and return its
    <value> text. The NDW feed stores the project name as commentType=internalNote
    and the plain-Dutch warning as commentType=warning.
    """
    for comment_block in elem.iter():
        if comment_block.tag.split("}")[-1] != "generalPublicComment":
            continue
        ct = _find_text(comment_block, "commentType")
        if ct == comment_type:
            return _find_text(comment_block, "value")
    return None


def _parse_pos_list(text: str) -> list:
    """
    Parse a DATEX II posList string ("lat lon lat lon …") into a list of
    [lat, lon] pairs. Skips malformed values silently.
    """
    parts = text.split()
    result = []
    for i in range(0, len(parts) - 1, 2):
        try:
            result.append([float(parts[i]), float(parts[i + 1])])
        except ValueError:
            continue
    return result


def _parse_date(text: str) -> date:
    """Parse ISO 8601 datetime string to a date. Falls back gracefully."""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return date.min
