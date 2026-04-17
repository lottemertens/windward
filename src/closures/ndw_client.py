"""
NDW road closures client.

Fetches the NDW planning feed (road works + events) once per day and caches
the filtered result in memory. The raw feed is 237 MB decompressed, so we:
  - Stream-parse with iterparse (never load the full tree into memory)
  - Keep only carriagewayClosures active within the next 7 days
  - Store a small list of dicts — typically a few hundred records for all NL

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
    CLOSURE_CACHE_TTL_HOURS,
    CLOSURE_MAX_DAYS_AHEAD,
)

logger = logging.getLogger(__name__)

# ── Namespaces in the DATEX II v3 planning feed ───────────────────────────────

NS_SIT = "http://datex2.eu/schema/3/situation"
NS_MC  = "http://datex2.eu/schema/3/messageContainer"

# ── In-memory cache ───────────────────────────────────────────────────────────

@dataclass
class _Cache:
    records:    list[dict] = field(default_factory=list)
    fetched_at: datetime | None = None

_cache = _Cache()
_fetch_lock = asyncio.Lock()   # prevents duplicate fetches if two requests arrive simultaneously


# ── Public API ────────────────────────────────────────────────────────────────

async def get_closures() -> list[dict]:
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
    _cache.records = records
    _cache.fetched_at = datetime.now(timezone.utc)
    logger.info("NDW cache refreshed: %d closures", len(records))


async def _download() -> bytes:
    """Download and decompress the gzipped XML feed."""
    async with httpx.AsyncClient() as client:
        r = await client.get(NDW_PLANNING_URL, timeout=30.0)
        r.raise_for_status()
    return gzip.decompress(r.content)


def _parse(xml_bytes: bytes) -> list[dict]:
    """
    Stream-parse the DATEX II v3 XML and return only carriagewayClosures that
    are active within the next CLOSURE_MAX_DAYS_AHEAD days.

    We use iterparse so the full 237 MB tree is never held in memory — each
    <situation> element is processed and immediately discarded.
    """
    today     = date.today()
    cutoff    = today + timedelta(days=CLOSURE_MAX_DAYS_AHEAD)
    results   = []

    context = ET.iterparse(io.BytesIO(xml_bytes), events=("end",))

    for event, elem in context:
        local = elem.tag.split("}")[-1]

        if local != "situation":
            continue

        # ── Walk each situationRecord inside this situation ────────────────
        for rec in elem.findall(f"{{{NS_SIT}}}situationRecord"):
            closure = _extract_closure(rec, today, cutoff)
            if closure:
                results.append(closure)

        # Free memory — we're done with this situation element
        elem.clear()

    return results


def _extract_closure(rec: ET.Element, today: date, cutoff: date) -> dict | None:
    """
    Return a closure dict if this record is:
      - a carriagewayClosures (full road closed, not just lane restriction)
      - active today or starting within the next CLOSURE_MAX_DAYS_AHEAD days
    Otherwise return None.
    """
    # ── Management type filter ─────────────────────────────────────────────
    mgmt_elem = _find(rec, "roadOrCarriagewayOrLaneManagementType")
    if mgmt_elem is None or mgmt_elem.text != "carriagewayClosures":
        return None

    # ── Date filter ────────────────────────────────────────────────────────
    start_elem = _find(rec, "overallStartTime")
    end_elem   = _find(rec, "overallEndTime")

    start_d = _parse_date(start_elem.text) if start_elem is not None else date.min
    end_d   = _parse_date(end_elem.text)   if end_elem   is not None else date.max

    # Keep if the closure overlaps [today, cutoff]
    if end_d < today or start_d > cutoff:
        return None

    # ── Coordinates ────────────────────────────────────────────────────────
    pos_elem = _find(rec, "posList")
    if pos_elem is None or not pos_elem.text:
        return None
    coords = pos_elem.text.split()
    if len(coords) < 2:
        return None
    try:
        lat, lon = float(coords[0]), float(coords[1])
    except ValueError:
        return None

    # ── Source (road manager) ──────────────────────────────────────────────
    source_elem = _find(rec, "value")
    source = source_elem.text.strip() if source_elem is not None else "unknown"

    # ── Human-readable description ─────────────────────────────────────────
    # The feed stores descriptions as multiple <value> elements under
    # <generalPublicComment><comment><values>. We grab any text that looks
    # like a description (more than 5 chars, not the source name).
    all_values = [e.text.strip() for e in rec.iter() if e.tag.split("}")[-1] == "value" and e.text]
    desc_parts = [v for v in all_values if len(v) > 5 and v != source]
    description = "; ".join(dict.fromkeys(desc_parts))[:200]  # deduplicate, cap length

    return {
        "lat":         lat,
        "lon":         lon,
        "source":      source,
        "start":       start_d.isoformat(),
        "end":         end_d.isoformat() if end_d != date.max else None,
        "description": description or None,
    }


# ── XML helpers ───────────────────────────────────────────────────────────────

def _find(elem: ET.Element, local_name: str) -> ET.Element | None:
    """Find first descendant by local tag name, ignoring namespace."""
    for child in elem.iter():
        if child.tag.split("}")[-1] == local_name:
            return child
    return None


def _parse_date(text: str) -> date:
    """Parse ISO 8601 datetime string to a date. Falls back gracefully."""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return date.min
