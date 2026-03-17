# DEPRECATED — Correlates of War / geopolitics corpus is not the active focus.
# This file is retained for reference only. Do not use in new work.
"""Ingest and embed historical geopolitical events from the CoW MID dataset."""

import csv
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import HistoricalEvent

logger = logging.getLogger(__name__)

_CHROMA_COLLECTION = "historical_events"
_BATCH_SIZE = 100

# ---------------------------------------------------------------------------
# Hostility-level → event_type mapping (CoW MID codebook)
# hostlev: 1=No militarized action, 2=Threat, 3=Display, 4=Use of force, 5=War
# ---------------------------------------------------------------------------
_HOSTLEV_TO_EVENT_TYPE = {
    "1": "diplomacy",
    "2": "diplomacy",
    "3": "conflict",
    "4": "conflict",
    "5": "conflict",
}

# CoW outcome codes (MID codebook v4/5)
_OUTCOME_CODES = {
    "1": "Victory for side A",
    "2": "Victory for side B",
    "3": "Yield by side A",
    "4": "Yield by side B",
    "5": "Stalemate",
    "6": "Compromise",
    "7": "Released",
    "8": "Unclear",
    "-9": "Missing",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(val: str | None) -> int | None:
    """Convert a string to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _format_date(year: int | None, month: int | None) -> str | None:
    """Return ISO date string YYYY-MM-DD, falling back gracefully."""
    if year is None:
        return None
    month = month if (month and 1 <= month <= 12) else 1
    try:
        return datetime(year, month, 1).strftime("%Y-%m-%d")
    except ValueError:
        return f"{year:04d}-01-01"


def _stable_id(row: dict, columns: list[str]) -> str:
    """Generate a stable, deterministic ID from a subset of row columns.

    Primary strategy: cow-{dispnum}-{stabb}
    Fallback: SHA-1 hash of key field values so we never return an empty id.
    """
    dispnum = row.get("dispnum") or row.get("dispnum3") or row.get("dispno")
    stabb = row.get("stabb") or row.get("stateabb") or row.get("ccode")

    if dispnum and stabb:
        return f"cow-{dispnum}-{stabb}".replace(" ", "_")

    # Fallback: hash the entire row to guarantee uniqueness and stability
    key_values = "|".join(str(row.get(c, "")) for c in sorted(columns))
    digest = hashlib.sha1(key_values.encode()).hexdigest()[:16]
    return f"cow-{digest}"


def _map_event_type(row: dict) -> str:
    """Map CoW hostility level to our event_type vocabulary."""
    hostlev = str(row.get("hostlev") or row.get("hihost") or "").strip()
    return _HOSTLEV_TO_EVENT_TYPE.get(hostlev, "other")


def _map_outcome(row: dict) -> str:
    """Return a human-readable outcome string."""
    outcome_code = str(row.get("outcome") or "").strip()
    if outcome_code in _OUTCOME_CODES:
        return _OUTCOME_CODES[outcome_code]
    if outcome_code:
        return f"Outcome code {outcome_code}"
    return "Unknown"


def _parse_actors(row: dict) -> list[str]:
    """Extract actor abbreviations from the row."""
    actors = []
    for key in ("stabb", "stateabb", "ccode"):
        val = row.get(key)
        if val and val not in actors:
            actors.append(str(val).strip())
    return actors if actors else ["Unknown"]


def _synthesize_description(row: dict) -> str:
    """Build a narrative description from available CoW columns."""
    stabb = row.get("stabb") or row.get("stateabb") or "Unknown state"
    dispnum = row.get("dispnum") or row.get("dispnum3") or row.get("dispno") or "?"

    styear = _safe_int(row.get("styear") or row.get("year"))
    endyear = _safe_int(row.get("endyear"))

    hostlev_raw = str(row.get("hostlev") or row.get("hihost") or "").strip()
    event_type = _HOSTLEV_TO_EVENT_TYPE.get(hostlev_raw, "militarized dispute")

    period = str(styear) if styear else "unknown year"
    if endyear and endyear != styear:
        period = f"{styear}–{endyear}"

    outcome = _map_outcome(row)

    parts = [
        f"Dispute {dispnum}: {event_type} involving {stabb} in {period}.",
        f"Outcome: {outcome}.",
    ]

    # Add any extra columns that carry useful content
    for col in ("fatality", "fatalper", "action", "revstate", "revtype1", "revtype2"):
        val = row.get(col)
        if val is not None and str(val).strip() not in ("", "-9", "-8"):
            parts.append(f"{col.capitalize()}: {val}.")

    return " ".join(parts)


def _infer_region(row: dict) -> str:
    """Infer region from CoW state abbreviation or ccode.

    CoW country codes roughly map to regions by numeric range:
    2-165:  Western Hemisphere
    200-395: Europe
    400-626: Africa
    630-698: Middle East
    700-990: Asia/Oceania
    """
    ccode_raw = row.get("ccode")
    ccode = _safe_int(str(ccode_raw).strip() if ccode_raw else None)

    if ccode is not None:
        if 2 <= ccode <= 165:
            return "Western Hemisphere"
        if 200 <= ccode <= 395:
            return "Europe"
        if 400 <= ccode <= 626:
            return "Africa"
        if 630 <= ccode <= 698:
            return "Middle East"
        if 700 <= ccode <= 990:
            return "Asia-Pacific"

    return "Unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_cow_dataset(path: str) -> list[dict]:
    """Parse the CoW MID dataset CSV and return a list of event dicts.

    Handles missing columns gracefully; the exact column names are
    auto-detected at parse time and mapped defensively to our schema.

    Parameters
    ----------
    path:
        Filesystem path to the CSV file (can be any CoW MID participant-level
        or dispute-level file).

    Returns
    -------
    list[dict]
        Each dict has keys: id, description, actors, event_type, outcome,
        date, region.  All values are strings or lists; none are None —
        missing data is represented by sentinel strings ("Unknown", "").
    """
    events: list[dict] = []
    seen_ids: set[str] = set()

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            logger.warning("CSV file %s has no header row — returning empty list", path)
            return events

        columns = [c.strip().lower() for c in reader.fieldnames]
        # Rebuild DictReader with normalised column names
        fh.seek(0)
        reader2 = csv.DictReader(fh)
        reader2.fieldnames = columns  # type: ignore[assignment]
        next(reader2)  # skip the original header row

        for raw_row in reader2:
            # Normalise keys to lowercase and strip whitespace from values
            row = {k.strip().lower(): (v.strip() if v else "") for k, v in raw_row.items()}

            event_id = _stable_id(row, columns)
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)

            styear = _safe_int(row.get("styear") or row.get("year"))
            stmon = _safe_int(row.get("stmon") or row.get("month"))
            date = _format_date(styear, stmon) or ""

            events.append(
                {
                    "id": event_id,
                    "description": _synthesize_description(row),
                    "actors": _parse_actors(row),
                    "event_type": _map_event_type(row),
                    "outcome": _map_outcome(row),
                    "date": date,
                    "region": _infer_region(row),
                }
            )

    logger.info("Loaded %d events from %s", len(events), path)
    return events


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _get_embedding_fn():
    """Return a callable(texts: list[str]) -> list[list[float]] using batch embedding.

    Priority:
    1. Voyage AI (voyage-3) if VOYAGE_API_KEY is set
    2. OpenAI (text-embedding-3-small) if OPENAI_API_KEY is set
    3. Raise RuntimeError

    Both providers support batching — the entire batch is sent in one API call.
    """
    voyage_key = os.environ.get("VOYAGE_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if voyage_key:
        import voyageai  # type: ignore[import]

        client = voyageai.Client(api_key=voyage_key)

        def embed_voyage(texts: list[str]) -> list[list[float]]:
            result = client.embed(texts, model="voyage-3", input_type="document")
            return result.embeddings

        logger.debug("Using Voyage AI for embeddings (voyage-3, batch)")
        return embed_voyage

    if openai_key:
        from openai import OpenAI  # type: ignore[import]

        client = OpenAI(api_key=openai_key)

        def embed_openai(texts: list[str]) -> list[list[float]]:
            response = client.embeddings.create(
                input=texts,
                model="text-embedding-3-small",
            )
            return [item.embedding for item in response.data]

        logger.debug("Using OpenAI for embeddings (text-embedding-3-small, batch)")
        return embed_openai

    raise RuntimeError(
        "No embedding API key found. Set VOYAGE_API_KEY or OPENAI_API_KEY in the environment."
    )


def embed_and_store_events(
    events: list[dict],
    chroma_client,
    session: Session,
    collection: str = _CHROMA_COLLECTION,
) -> int:
    """Embed events and persist them to ChromaDB and SQLite.

    This function is idempotent: events whose ``id`` already exists in SQLite
    are skipped without error.

    Parameters
    ----------
    events:
        Output of :func:`load_cow_dataset`.
    chroma_client:
        A ``chromadb.Client`` or ``chromadb.PersistentClient`` instance.
    session:
        An active SQLAlchemy ``Session`` (caller manages commit/rollback).

    Returns
    -------
    int
        The number of newly inserted events (skipped ones are not counted).
    """
    if not events:
        return 0

    # Determine which IDs already exist in SQLite
    candidate_ids = [e["id"] for e in events]
    existing_ids: set[str] = set(
        row[0]
        for row in session.query(HistoricalEvent.id)
        .filter(HistoricalEvent.id.in_(candidate_ids))
        .all()
    )

    new_events = [e for e in events if e["id"] not in existing_ids]
    if not new_events:
        logger.info("All %d events already exist — nothing to ingest", len(events))
        return 0

    embed_fn = _get_embedding_fn()
    collection = chroma_client.get_or_create_collection(
        collection,
        metadata={"hnsw:space": "cosine"},
    )

    inserted = 0
    # Process in batches to avoid memory issues with ChromaDB upserts
    for batch_start in range(0, len(new_events), _BATCH_SIZE):
        batch = new_events[batch_start : batch_start + _BATCH_SIZE]

        # Embed the entire batch in one API call
        descriptions = [e["description"] for e in batch]
        embeddings = embed_fn(descriptions)  # one call, N results

        chroma_ids: list[str] = []
        chroma_embeddings: list[list[float]] = []
        chroma_documents: list[str] = []
        chroma_metadatas: list[dict] = []
        orm_objects: list[HistoricalEvent] = []

        for event, embedding in zip(batch, embeddings):
            chroma_id = event["id"]

            chroma_ids.append(chroma_id)
            chroma_embeddings.append(embedding)
            chroma_documents.append(event["description"])
            chroma_metadatas.append(
                {
                    "event_id": event["id"],
                    "event_type": event["event_type"],
                    "region": event["region"],
                    "date": event["date"],
                    "actors_json": json.dumps(event["actors"]),
                }
            )

            orm_objects.append(
                HistoricalEvent(
                    id=event["id"],
                    description=event["description"],
                    actors=event["actors"],
                    event_type=event["event_type"],
                    outcome=event["outcome"],
                    date=event["date"] or None,
                    region=event["region"],
                    chroma_id=chroma_id,
                    created_at=datetime.now(timezone.utc),
                )
            )

        # Batch upsert to ChromaDB
        collection.upsert(
            ids=chroma_ids,
            embeddings=chroma_embeddings,
            documents=chroma_documents,
            metadatas=chroma_metadatas,
        )

        # Persist to SQLite
        for obj in orm_objects:
            session.add(obj)

        inserted += len(batch)
        logger.info(
            "Ingested batch %d–%d (%d total so far)",
            batch_start + 1,
            batch_start + len(batch),
            inserted,
        )

    logger.info(
        "Done. %d new events ingested, %d skipped (already existed).",
        inserted,
        len(existing_ids),
    )
    return inserted
