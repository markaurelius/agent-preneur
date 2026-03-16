"""Ingest recent SEC 8-K filings from EDGAR full-text search API.

The SEC's EDGAR (Electronic Data Gathering, Analysis, and Retrieval) system
publishes every public company's regulatory filings. 8-K forms are "current
reports" that companies must file within four business days of a material event.

This module fetches the last N days of 8-K filings, filters to the most
significant item codes (material agreements, acquisitions, bankruptcies,
earnings, officer changes, etc.), and returns event dicts suitable for
embed_and_store_events().

The corpus is intentionally RECENT — these are live regulatory disclosures that
Claude was not trained on, so retrieval adds genuine signal for financial
forecasting questions.

Data source: https://efts.sec.gov/LATEST/search-index  (free, no auth required)
Rate limit: SEC fair use policy requests ≤10 requests/second; we sleep 0.1s.
User-Agent: required by SEC — must include contact info.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

# EDGAR full-text search endpoint
_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"

# Required by SEC EDGAR — must identify the application and provide contact info
_USER_AGENT = "forecasting-engine research@example.com"

# Maximum filings to collect per run — keeps corpus manageable
_MAX_FILINGS = 500

# Page size for EDGAR search results (max supported is 40 per SEC docs)
_PAGE_SIZE = 40

# 8-K item codes we care about — these represent the most consequential
# corporate events for financial and geopolitical forecasting.
# Format: "Item X.YY" as it appears in EDGAR metadata.
_SIGNIFICANT_ITEMS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.04": "Triggering Events That Accelerate or Increase a Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "5.02": "Departure of Directors or Certain Officers; Election of Directors",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}

# Map item codes to a concise event_type label for the corpus schema
_ITEM_TO_EVENT_TYPE = {
    "1.01": "corporate",
    "1.02": "corporate",
    "1.03": "bankruptcy",
    "2.01": "merger",
    "2.02": "earnings",
    "2.04": "corporate",
    "2.05": "corporate",
    "2.06": "corporate",
    "3.01": "corporate",
    "4.01": "corporate",
    "5.02": "corporate",
    "7.01": "corporate",
    "8.01": "corporate",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get(url: str, params: dict, max_retries: int = 4) -> dict | None:
    """GET a URL with retries and exponential backoff on 429."""
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt * 5  # 5s, 10s, 20s, 40s
                logger.warning("EDGAR rate limited (429) — waiting %ds (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                logger.debug("EDGAR 404 for params %s", params)
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("EDGAR HTTP error: %s", exc)
            return None
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning("EDGAR request error (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                time.sleep(wait)
    logger.warning("EDGAR: giving up after %d attempts", max_retries)
    return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _normalize_accession(accession: str) -> str:
    """Return a URL/ID-safe accession number (hyphens stripped)."""
    return accession.replace("-", "").replace("/", "").replace("\\", "")


def _extract_items(source: dict) -> list[str]:
    """Extract 8-K item codes from a filing _source dict.

    EDGAR search results sometimes include an 'items' field as a list of
    strings like ["Item 2.02", "Item 7.01"], or may embed them in
    'display_names' or other fields. We parse whatever is available.
    """
    items: list[str] = []

    # Prefer the explicit 'items' field if present
    raw_items = source.get("items", [])
    if isinstance(raw_items, list):
        for item in raw_items:
            # Normalise "Item 2.02" → "2.02"
            cleaned = str(item).replace("Item ", "").replace("item ", "").strip()
            items.append(cleaned)
    elif isinstance(raw_items, str) and raw_items:
        cleaned = raw_items.replace("Item ", "").replace("item ", "").strip()
        items.append(cleaned)

    return items


def _is_significant(items: list[str]) -> bool:
    """Return True if any of the item codes are in our significant-items list."""
    if not items:
        # No item metadata — include the filing anyway (better to over-include)
        return True
    return any(item in _SIGNIFICANT_ITEMS for item in items)


def _item_description(items: list[str]) -> str:
    """Build a human-readable description of the filed items."""
    descriptions = []
    for item in items:
        if item in _SIGNIFICANT_ITEMS:
            descriptions.append(f"Item {item}: {_SIGNIFICANT_ITEMS[item]}")
    if not descriptions:
        return "various disclosures"
    return "; ".join(descriptions)


def _item_event_type(items: list[str]) -> str:
    """Derive the most specific event_type from the item codes."""
    # Priority order: more specific types win
    priority = ["bankruptcy", "merger", "earnings", "corporate"]
    type_set = {_ITEM_TO_EVENT_TYPE.get(item, "corporate") for item in items}
    for t in priority:
        if t in type_set:
            return t
    return "corporate"


def _coerce_str(val) -> str:
    """Coerce a value that may be a list or string to a plain string."""
    if isinstance(val, list):
        return val[0].strip() if val else ""
    return (val or "").strip()


def _build_narrative(source: dict, items: list[str]) -> str:
    """Build a supplemental narrative from available EDGAR metadata fields."""
    parts = []

    biz_location = _coerce_str(source.get("biz_locations") or source.get("biz_location"))
    if biz_location:
        parts.append(f"Business location: {biz_location}.")

    period = _coerce_str(source.get("period_ending") or source.get("period_of_report"))
    if period:
        parts.append(f"Period of report: {period}.")

    file_num = _coerce_str(source.get("file_num"))
    if file_num:
        parts.append(f"SEC file number: {file_num}.")

    if not items:
        parts.append("No specific item codes available in search index.")

    return " ".join(parts)


def _source_to_event(source: dict) -> dict | None:
    """Convert an EDGAR search hit _source into our standard event schema.

    Returns None if the filing lacks the minimum required metadata.
    """
    # Extract entity name from display_names e.g. "loanDepot, Inc.  (LDI)  (CIK 0001831631)"
    display_names = source.get("display_names", [])
    if isinstance(display_names, list) and display_names:
        entity_name = display_names[0].split("(")[0].strip()
    else:
        entity_name = _coerce_str(display_names).split("(")[0].strip()

    file_date = _coerce_str(source.get("file_date"))
    # EDGAR uses 'adsh' for accession number
    accession = _coerce_str(source.get("adsh") or source.get("accession_no") or source.get("file_num", ""))

    if not entity_name or not file_date:
        logger.debug("Skipping filing with missing entity_name or file_date: %s", source)
        return None

    items = _extract_items(source)

    if not _is_significant(items):
        return None

    item_desc = _item_description(items)
    event_type = _item_event_type(items)
    narrative = _build_narrative(source, items)

    # Normalise accession number for use as an ID
    acc_norm = _normalize_accession(accession) if accession else ""
    # Fallback ID if accession is missing: hash entity + date
    if acc_norm:
        event_id = f"edgar-{acc_norm}"
    else:
        import hashlib
        key = f"{entity_name}-{file_date}"
        event_id = "edgar-" + hashlib.sha1(key.encode()).hexdigest()[:16]

    description = (
        f"On {file_date}, {entity_name} filed an 8-K ({item_desc})."
    )
    if narrative:
        description = description + " " + narrative

    # Extract ticker symbols from display_names e.g. "loanDepot, Inc.  (LDI)  (CIK 0001831631)"
    actors = [entity_name]
    dn_list = display_names if isinstance(display_names, list) else [display_names]
    for dn in dn_list:
        dn_str = str(dn)
        # Find short uppercase tokens in parens — these are tickers (not CIK numbers)
        import re
        for match in re.finditer(r"\(([A-Z]{1,5})\)", dn_str):
            ticker = match.group(1)
            if ticker not in actors:
                actors.append(ticker)

    return {
        "id": event_id,
        "description": description,
        "actors": actors,
        "event_type": event_type,
        "outcome": "",  # unknown at filing time
        "date": file_date,
        "region": "North America",  # EDGAR filers are overwhelmingly US companies
        "num_mentions": 1,
    }


# ---------------------------------------------------------------------------
# Fetching logic
# ---------------------------------------------------------------------------


def _fetch_page(start_date: str, end_date: str, from_offset: int) -> tuple[list[dict], int]:
    """Fetch one page of EDGAR search results.

    Returns (list_of_hits, total_hits) where each hit is a raw _source dict.
    """
    params = {
        "q": '""',
        "forms": "8-K",
        "dateRange": "custom",
        "startdt": start_date,
        "enddt": end_date,
        "from": from_offset,
        "size": _PAGE_SIZE,
    }

    data = _get(_EDGAR_SEARCH, params)
    if not data:
        return [], 0

    hits_block = data.get("hits", {})
    total = 0
    total_obj = hits_block.get("total", {})
    if isinstance(total_obj, dict):
        total = total_obj.get("value", 0)
    elif isinstance(total_obj, int):
        total = total_obj

    hits = hits_block.get("hits", [])
    sources = []
    for hit in hits:
        src = hit.get("_source", {})
        if src:
            sources.append(src)

    return sources, total


def _fetch_window(start_date: str, end_date: str, max_filings: int) -> list[dict]:
    """Fetch all 8-K filings in a date window, up to max_filings."""
    events: list[dict] = []
    seen_ids: set[str] = set()
    offset = 0

    logger.info("Fetching EDGAR 8-Ks: %s → %s", start_date, end_date)

    while len(events) < max_filings:
        sources, total = _fetch_page(start_date, end_date, offset)

        if offset == 0:
            logger.info("EDGAR: %d total 8-K filings in window %s–%s", total, start_date, end_date)

        if not sources:
            break

        for src in sources:
            event = _source_to_event(src)
            if event is None:
                continue
            if event["id"] in seen_ids:
                continue
            seen_ids.add(event["id"])
            events.append(event)
            if len(events) >= max_filings:
                break

        offset += len(sources)
        if offset >= total or offset >= max_filings:
            break

        time.sleep(0.1)  # SEC fair use: ≤10 req/s

    logger.debug("Window %s–%s yielded %d significant events", start_date, end_date, len(events))
    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_edgar_corpus(days: int = 90) -> list[dict]:
    """Fetch last N days of SEC 8-K filings and return as event dicts.

    Parameters
    ----------
    days:
        How many calendar days back to fetch (default 90).

    Returns
    -------
    list[dict]
        Events in the standard corpus schema, ready for embed_and_store_events().
        Each dict has: id, description, actors, event_type, outcome, date,
        region, num_mentions.
    """
    today = date.today()
    start = today - timedelta(days=days)

    start_str = start.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    # For large windows, chunk into 30-day slices to avoid timeouts and
    # ensure we don't blow past the EDGAR API's result window limits.
    # EDGAR search returns at most ~10,000 hits; chunking keeps each slice
    # well within that.
    chunk_days = 30
    all_events: list[dict] = []
    seen_ids: set[str] = set()

    current = start
    while current < today and len(all_events) < _MAX_FILINGS:
        chunk_end = min(current + timedelta(days=chunk_days - 1), today)
        remaining = _MAX_FILINGS - len(all_events)

        chunk_events = _fetch_window(
            current.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d"),
            max_filings=remaining,
        )

        for event in chunk_events:
            if event["id"] not in seen_ids:
                seen_ids.add(event["id"])
                all_events.append(event)

        current = chunk_end + timedelta(days=1)
        time.sleep(0.1)

    # Sort by date descending so most recent filings appear first
    all_events.sort(key=lambda e: e["date"], reverse=True)

    logger.info(
        "EDGAR corpus: %d significant 8-K events from last %d days",
        len(all_events),
        days,
    )
    return all_events
