"""Ingest recent global events from the GDELT 1.0 daily export.

GDELT (Global Database of Events, Language, and Tone) publishes a structured
record of every newsworthy event worldwide, extracted from global news media.

This module fetches the last N days of GDELT data, filters to significant
events (high media coverage, meaningful conflict/cooperation scale), and
returns event dicts suitable for embed_and_store_events().

The corpus is intentionally RECENT — the point is to give Claude information
it was NOT trained on, so retrieval genuinely adds signal.

Data source: http://data.gdeltproject.org/events/  (free, no auth required)
Format: GDELT 1.0 daily export, tab-separated, 58 columns
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import time
import zipfile
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

GDELT_BASE = "http://data.gdeltproject.org/events"

# GDELT 1.0 column indices (0-based)
_COL = {
    "event_id":      0,
    "day":           1,   # YYYYMMDD
    "actor1_name":   5,
    "actor1_cc":     7,   # country code
    "actor2_name":   15,
    "actor2_cc":     17,
    "event_code":    26,  # full CAMEO code
    "event_root":    28,  # root CAMEO code (first 2 digits)
    "quad_class":    29,  # 1=VerbalCoop 2=MatCoop 3=VerbalConflict 4=MatConflict
    "goldstein":     30,  # -10 (conflict) to +10 (cooperation)
    "num_mentions":  31,
    "num_sources":   32,
    "avg_tone":      34,
    "geo_fullname":  51,
    "geo_cc":        53,
    "source_url":    57,
}

# CAMEO root code → human-readable description
_CAMEO_ROOT = {
    "01": "made a public statement about",
    "02": "appealed to",
    "03": "expressed intent to cooperate with",
    "04": "consulted with",
    "05": "engaged in diplomatic cooperation with",
    "06": "engaged in material cooperation with",
    "07": "provided aid to",
    "08": "yielded to",
    "09": "investigated",
    "10": "demanded action from",
    "11": "expressed disapproval of",
    "12": "rejected overtures from",
    "13": "threatened",
    "14": "protested",
    "15": "exhibited military force posture toward",
    "16": "reduced relations with",
    "17": "coerced",
    "18": "assaulted",
    "19": "engaged in armed conflict with",
    "20": "used unconventional mass violence against",
}

# Only ingest events with these root codes (conflict + cooperation + diplomacy)
_RELEVANT_ROOT_CODES = {
    "04", "05", "06", "07",        # cooperation / diplomacy
    "10", "11", "12", "13",        # demands / disapproval / threats
    "14", "15", "16", "17",        # protest / force / reduce relations / coerce
    "18", "19", "20",              # violence / armed conflict
}


def _safe(row: list[str], col: int, default: str = "") -> str:
    try:
        return row[col].strip()
    except IndexError:
        return default


def _stable_id(event_id: str, day: str) -> str:
    key = f"gdelt-{day}-{event_id}"
    return "gdelt-" + hashlib.sha1(key.encode()).hexdigest()[:14]


def _parse_row(row: list[str]) -> dict | None:
    """Parse a single GDELT row into our event schema. Returns None to skip."""
    root = _safe(row, _COL["event_root"])
    if root not in _RELEVANT_ROOT_CODES:
        return None

    try:
        num_mentions = int(_safe(row, _COL["num_mentions"], "0"))
    except ValueError:
        num_mentions = 0

    if num_mentions < 5:
        return None  # low-coverage events are noise

    try:
        goldstein = float(_safe(row, _COL["goldstein"], "0"))
    except ValueError:
        goldstein = 0.0

    day = _safe(row, _COL["day"])
    event_id = _safe(row, _COL["event_id"])
    actor1 = _safe(row, _COL["actor1_name"]) or _safe(row, _COL["actor1_cc"]) or "Unknown actor"
    actor2 = _safe(row, _COL["actor2_name"]) or _safe(row, _COL["actor2_cc"]) or "Unknown actor"
    location = _safe(row, _COL["geo_fullname"]) or _safe(row, _COL["geo_cc"]) or "unknown location"
    action = _CAMEO_ROOT.get(root, f"acted (CAMEO {root}) toward")
    num_sources = _safe(row, _COL["num_sources"], "0")
    avg_tone = _safe(row, _COL["avg_tone"], "0")
    source_url = _safe(row, _COL["source_url"])

    try:
        tone_val = float(avg_tone)
        tone_str = f"tone {tone_val:+.1f}"
    except ValueError:
        tone_str = ""

    # Format date
    date_str = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day

    description = (
        f"On {date_str}, {actor1} {action} {actor2}"
        f"{(' in ' + location) if location != 'unknown location' else ''}. "
        f"Covered by {num_mentions} articles across {num_sources} sources"
        f"{(', ' + tone_str) if tone_str else ''}. "
        f"Goldstein scale: {goldstein:+.1f} "
        f"({'conflict' if goldstein < 0 else 'cooperation'})."
    )

    if source_url:
        description += f" Source: {source_url}"

    # Map to region
    geo_cc = _safe(row, _COL["geo_cc"])
    region = _cc_to_region(geo_cc)

    # Map to event_type
    quad = _safe(row, _COL["quad_class"], "0")
    event_type = "conflict" if quad in ("3", "4") else "diplomacy"

    return {
        "id": _stable_id(event_id, day),
        "description": description,
        "actors": list(filter(None, [
            _safe(row, _COL["actor1_cc"]) or _safe(row, _COL["actor1_name"]),
            _safe(row, _COL["actor2_cc"]) or _safe(row, _COL["actor2_name"]),
        ])),
        "event_type": event_type,
        "outcome": "",  # GDELT events are ongoing; outcome not yet known
        "date": date_str,
        "region": region,
        "num_mentions": num_mentions,
    }


def _cc_to_region(cc: str) -> str:
    """Map ISO country code to broad region."""
    europe = {"UK", "FR", "DE", "IT", "ES", "PL", "NL", "BE", "SE", "NO", "FI", "RU", "UA", "TR"}
    middle_east = {"IL", "IR", "IQ", "SY", "SA", "YE", "LB", "JO", "EG", "AE", "QA"}
    asia = {"CN", "JP", "KR", "KP", "IN", "PK", "AF", "TW", "VN", "TH", "ID", "MY"}
    africa = {"ZA", "NG", "ET", "KE", "SD", "SO", "LY", "ML", "CF", "CD"}
    americas = {"US", "CA", "MX", "BR", "AR", "CO", "VE", "CU", "BO", "CL"}

    cc = cc.upper()
    if cc in europe: return "Europe"
    if cc in middle_east: return "Middle East"
    if cc in asia: return "Asia-Pacific"
    if cc in africa: return "Africa"
    if cc in americas: return "Western Hemisphere"
    return "Unknown"


def _fetch_day(day: date) -> list[dict]:
    """Download and parse one day's GDELT export. Returns list of event dicts."""
    date_str = day.strftime("%Y%m%d")
    url = f"{GDELT_BASE}/{date_str}.export.CSV.zip"

    for attempt in range(3):
        try:
            resp = httpx.get(url, timeout=60, follow_redirects=True)
            if resp.status_code == 404:
                logger.debug("No GDELT file for %s (404)", date_str)
                return []
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                logger.warning("Rate limited fetching GDELT %s — waiting %ds", date_str, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            logger.warning("Failed to fetch GDELT for %s: %s", date_str, exc)
            return []
    else:
        logger.warning("Giving up on GDELT %s after 3 attempts (rate limited)", date_str)
        return []

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")
                rows = list(reader)
    except Exception as exc:
        logger.warning("Failed to parse GDELT zip for %s: %s", date_str, exc)
        return []

    events = []
    for row in rows:
        parsed = _parse_row(row)
        if parsed:
            events.append(parsed)

    logger.debug("Day %s: %d raw rows → %d significant events", date_str, len(rows), len(events))
    return events


def _top_n_per_day(events: list[dict], n: int) -> list[dict]:
    """Keep only the top-N events per day by num_mentions."""
    by_day: dict[str, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)

    result = []
    for day_events in by_day.values():
        day_events.sort(key=lambda x: x.get("num_mentions", 0), reverse=True)
        result.extend(day_events[:n])
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_gdelt_corpus(days: int = 90, top_per_day: int = 200) -> list[dict]:
    """Fetch the last `days` days of GDELT data and return significant events.

    Parameters
    ----------
    days:
        How many days back to fetch (default 90).
    top_per_day:
        Max events to keep per day, ranked by number of news mentions (default 200).
        Keeps corpus size manageable while preserving the most newsworthy events.

    Returns
    -------
    list[dict]
        Events in the standard corpus schema, ready for embed_and_store_events().
    """
    today = date.today()
    all_events: list[dict] = []

    for i in range(days, 0, -1):
        day = today - timedelta(days=i)
        day_events = _fetch_day(day)
        all_events.extend(day_events)
        logger.info("Fetched %s: %d events (total so far: %d)", day, len(day_events), len(all_events))
        time.sleep(1)  # be polite to the GDELT server

    # Keep only the most-covered events per day
    filtered = _top_n_per_day(all_events, top_per_day)

    # Deduplicate by id
    seen: set[str] = set()
    deduped = []
    for e in filtered:
        if e["id"] not in seen:
            seen.add(e["id"])
            deduped.append(e)

    logger.info(
        "GDELT corpus: %d events from last %d days (top %d/day)",
        len(deduped), days, top_per_day,
    )
    return deduped
