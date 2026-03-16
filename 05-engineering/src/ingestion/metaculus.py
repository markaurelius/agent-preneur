"""Load and store resolved binary questions from the nikhilchandak/metaculus-binary HuggingFace dataset.

Dataset: https://huggingface.co/datasets/nikhilchandak/metaculus-binary
Fields used: question, resolution (0/1), date_resolve_at, date_begin, url, nr_forecasters
community_probability is intentionally skipped (not available in the dataset).
"""

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.db.models import Question

logger = logging.getLogger(__name__)

HF_DATASET = "nikhilchandak/metaculus-binary"

# High-signal keywords — any single match is sufficient
_GEO_HIGH_SIGNAL = [
    "war", "warfare", "invasion", "troops", "ceasefire", "missile", "airstrike",
    "nuclear", "sanctions", "coup", "civil war", "insurgency", "armed forces",
    "geopolit", "foreign policy", "state department", "pentagon",
    "nato", "united nations", "g7", "g20", "european union",
    "treaty", "annexation", "sovereignty",
    "kremlin", "white house",
    "ukraine", "taiwan", "north korea",
]

# Lower-signal keywords — require at least two matches to pass
_GEO_LOW_SIGNAL = [
    "military", "conflict", "weapon", "regime", "referendum",
    "prime minister", "parliament", "authoritarian",
    "diplomatic", "diplomat", "embassy", "bilateral",
    "territory", "border", "independence",
    "alliance", "defense", "offensive", "blockade",
    "presidential election", "general election", "parliamentary election",
    "russia", "china", "israel", "iran",
    "beijing", "washington",
]


def _is_geopolitics(text: str) -> bool:
    """Return True if the question text is geopolitics-relevant.

    Passes if:
    - Any single high-signal keyword matches, OR
    - Two or more low-signal keywords match (reduces false positives from
      broad terms like "russia" or "military" appearing in non-geo contexts).
    """
    lower = text.lower()
    if any(kw in lower for kw in _GEO_HIGH_SIGNAL):
        return True
    low_hits = sum(1 for kw in _GEO_LOW_SIGNAL if kw in lower)
    return low_hits >= 2


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(str(raw)[:19], fmt[:len(str(raw)[:19])])
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _stable_id(row: dict) -> str:
    """Generate a stable question ID from the Metaculus URL or row content."""
    url = row.get("url") or ""
    if url:
        # Extract numeric ID from URL like https://www.metaculus.com/questions/1234/...
        parts = [p for p in url.rstrip("/").split("/") if p.isdigit()]
        if parts:
            return f"metaculus-{parts[-1]}"
    # Fallback: hash of question text
    text = str(row.get("question", ""))
    return "metaculus-" + hashlib.sha1(text.encode()).hexdigest()[:12]


def fetch_resolved_questions(
    dataset_name: str = HF_DATASET,
    split: str = "train",
    geopolitics_only: bool = True,
) -> list[dict]:
    """Load resolved binary questions from the HuggingFace dataset.

    Parameters
    ----------
    geopolitics_only:
        When True (default), only questions whose text matches at least one
        geopolitics keyword are returned.  Set to False to get all questions.

    Returns a list of normalised question dicts ready for upsert_questions().
    No authentication required.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise RuntimeError(
            "The 'datasets' package is required. It should be installed — "
            "check pyproject.toml dependencies."
        )

    logger.info("Loading dataset %s (split=%s) from HuggingFace ...", dataset_name, split)
    ds = load_dataset(dataset_name, split=split)
    logger.info("Loaded %d rows", len(ds))

    results = []
    skipped_resolution = 0
    skipped_geopolitics = 0
    for row in ds:
        row = dict(row)
        resolution = row.get("resolution")
        if resolution not in (0, 1, 0.0, 1.0):
            skipped_resolution += 1
            continue  # skip unresolved or ambiguous

        text = row.get("question") or ""
        if geopolitics_only and not _is_geopolitics(text):
            skipped_geopolitics += 1
            continue

        results.append({
            "id": _stable_id(row),
            "text": text,
            "resolution_value": float(resolution),
            "resolution_date": _parse_date(row.get("date_resolve_at")),
            "community_probability": None,  # not available in this dataset
            "tags": [],  # not available in this dataset
        })

    logger.info(
        "Prepared %d geopolitics questions (%d skipped non-geopolitics, %d skipped unresolved)",
        len(results),
        skipped_geopolitics,
        skipped_resolution,
    )
    return results


def upsert_questions(questions: list[dict], session: Session) -> int:
    """Insert questions into the DB, skipping any that already exist by ID.

    Returns the count of newly inserted questions.
    """
    if not questions:
        return 0

    existing_ids: set[str] = set(
        row[0]
        for row in session.query(Question.id).filter(
            Question.id.in_([q["id"] for q in questions])
        ).all()
    )

    inserted = 0
    for q in questions:
        if q["id"] in existing_ids:
            continue
        session.add(
            Question(
                id=q["id"],
                text=q["text"],
                resolution_date=q["resolution_date"],
                resolution_value=q["resolution_value"],
                community_probability=q["community_probability"],
                tags=q["tags"],
            )
        )
        inserted += 1

    logger.info("Inserted %d new questions (%d skipped as duplicates)", inserted, len(existing_ids))
    return inserted
