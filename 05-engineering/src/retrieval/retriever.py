"""Retrieval engine: fetch the N most structurally similar historical events for a question."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.config.schema import RunConfig
from src.db.models import HistoricalEvent, Question

logger = logging.getLogger(__name__)

_CHROMA_COLLECTION = "historical_events"  # default; overridden by config.corpus_collection


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class Analogue:
    event: HistoricalEvent
    similarity_score: float  # 0.0–1.0, higher = more similar
    features_used: dict = field(default_factory=dict)  # which features drove the score


# ---------------------------------------------------------------------------
# Embedding helper — standalone, no import from corpus.py
# ---------------------------------------------------------------------------


def _embed_text(text: str) -> list[float]:
    """Embed *text* using Voyage AI (if VOYAGE_API_KEY is set) or OpenAI as fallback.

    Raises RuntimeError if neither key is available.
    """
    voyage_key = os.environ.get("VOYAGE_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if voyage_key:
        import voyageai  # type: ignore[import]

        client = voyageai.Client(api_key=voyage_key)
        result = client.embed([text], model="voyage-3", input_type="query")
        return result.embeddings[0]

    if openai_key:
        from openai import OpenAI  # type: ignore[import]

        client = OpenAI(api_key=openai_key)
        response = client.embeddings.create(input=text, model="text-embedding-3-small")
        return response.data[0].embedding

    raise RuntimeError(
        "No embedding API key found. Set VOYAGE_API_KEY or OPENAI_API_KEY in the environment."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_collection(chroma_client, collection_name: str = _CHROMA_COLLECTION):
    """Return the named ChromaDB collection, raising RuntimeError if empty/missing."""
    try:
        collection = chroma_client.get_collection(collection_name)
    except Exception as exc:
        raise RuntimeError(
            f"ChromaDB collection '{collection_name}' does not exist. "
            "Run corpus ingestion first to populate it."
        ) from exc

    count = collection.count()
    if count == 0:
        raise RuntimeError(
            f"ChromaDB collection '{collection_name}' exists but is empty. "
            "Run corpus ingestion first to populate it."
        )
    return collection


def _question_year(question: Question) -> int | None:
    """Extract the year from a Question's resolution_date."""
    if question.resolution_date is None:
        return None
    return question.resolution_date.year


def _event_year(event: HistoricalEvent) -> int | None:
    """Extract the year from a HistoricalEvent's date string (YYYY-MM-DD)."""
    if not event.date:
        return None
    try:
        return int(event.date[:4])
    except (ValueError, TypeError):
        return None


def _date_proximity_score(event: HistoricalEvent, question: Question) -> float:
    """Score = 1 / (1 + |event_year - question_year|), or 0.5 if years unknown."""
    q_year = _question_year(question)
    e_year = _event_year(event)
    if q_year is None or e_year is None:
        return 0.5  # neutral score when dates are unavailable
    return 1.0 / (1.0 + abs(e_year - q_year))


def _fetch_events_by_ids(session: Session, ids: list[str]) -> dict[str, HistoricalEvent]:
    """Fetch HistoricalEvent rows in a single batch query; return {id: event}."""
    if not ids:
        return {}
    rows = session.query(HistoricalEvent).filter(HistoricalEvent.id.in_(ids)).all()
    return {row.id: row for row in rows}


def _apply_metadata_filters(events: list[HistoricalEvent], filters: dict) -> list[HistoricalEvent]:
    """Filter a list of events by the metadata_filters dict (exact match per key)."""
    if not filters:
        return events

    result = []
    for event in events:
        match = True
        for key, value in filters.items():
            event_val = getattr(event, key, None)
            if event_val != value:
                match = False
                break
        if match:
            result.append(event)
    return result


def _fetch_filtered_events(session: Session, filters: dict) -> list[HistoricalEvent]:
    """Fetch HistoricalEvent rows from SQLite, applying metadata_filters as WHERE clauses."""
    query = session.query(HistoricalEvent)
    for key, value in filters.items():
        if hasattr(HistoricalEvent, key):
            query = query.filter(getattr(HistoricalEvent, key) == value)
        else:
            logger.warning("Unknown metadata filter key '%s' — skipping", key)
    return query.all()


# ---------------------------------------------------------------------------
# Retrieval modes
# ---------------------------------------------------------------------------


def _retrieve_embedding(
    question: Question,
    config: RunConfig,
    chroma_client,
    session: Session,
    n_results: int | None = None,
) -> list[Analogue]:
    """Embedding-only retrieval."""
    collection = _get_collection(chroma_client, config.corpus_collection)

    k = n_results if n_results is not None else config.top_k
    # ChromaDB can return at most collection.count() results
    k = min(k, collection.count())

    query_embedding = _embed_text(question.text)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        include=["distances", "metadatas"],
    )

    # ChromaDB returns L2 distances by default; convert to cosine similarity.
    # When the collection was built with cosine space the distance IS already
    # 1 - cosine_sim, so similarity = 1 - distance.  For L2 we normalise
    # differently; we use a safe approximation: treat the raw distance as
    # cosine distance when it is in [0, 2].
    ids = results["ids"][0]  # list of chroma IDs
    distances = results["distances"][0]

    # chroma_id is the same as HistoricalEvent.id (set during ingestion)
    events_by_id = _fetch_events_by_ids(session, ids)

    analogues: list[Analogue] = []
    for chroma_id, dist in zip(ids, distances):
        event = events_by_id.get(chroma_id)
        if event is None:
            logger.warning("ChromaDB returned ID '%s' not found in SQLite — skipping", chroma_id)
            continue
        # Cosine similarity: distance in chromadb with cosine space = 1 - sim
        # Clamp to [0, 1]
        sim = max(0.0, min(1.0, 1.0 - dist))
        analogues.append(
            Analogue(
                event=event,
                similarity_score=sim,
                features_used={"embedding": sim},
            )
        )

    # Apply metadata filters after fetching
    filtered = _apply_metadata_filters([a.event for a in analogues], config.metadata_filters)
    filtered_ids = {e.id for e in filtered}
    analogues = [a for a in analogues if a.event.id in filtered_ids]

    return analogues


def _retrieve_metadata(
    question: Question,
    config: RunConfig,
    session: Session,
) -> list[Analogue]:
    """Metadata-only retrieval (no embedding)."""
    events = _fetch_filtered_events(session, config.metadata_filters)

    scored: list[tuple[HistoricalEvent, float]] = []
    for event in events:
        score = _date_proximity_score(event, question)
        scored.append((event, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[: config.top_k]

    return [
        Analogue(
            event=event,
            similarity_score=score,
            features_used={"date_proximity": score},
        )
        for event, score in top
    ]


def _retrieve_hybrid(
    question: Question,
    config: RunConfig,
    chroma_client,
    session: Session,
) -> list[Analogue]:
    """Hybrid retrieval: embedding + metadata re-ranking."""
    # Cast a wide net with embedding retrieval
    wide_k = config.top_k * 3
    embedding_analogues = _retrieve_embedding(
        question, config, chroma_client, session, n_results=wide_k
    )

    # Re-score each candidate with combined weights
    rescored: list[Analogue] = []
    for analogue in embedding_analogues:
        emb_score = analogue.similarity_score
        meta_score = _date_proximity_score(analogue.event, question)
        final_score = config.embedding_weight * emb_score + config.metadata_weight * meta_score
        rescored.append(
            Analogue(
                event=analogue.event,
                similarity_score=final_score,
                features_used={
                    "embedding": emb_score,
                    "metadata": meta_score,
                    "weighted": final_score,
                },
            )
        )

    rescored.sort(key=lambda a: a.similarity_score, reverse=True)
    return rescored[: config.top_k]


# ---------------------------------------------------------------------------
# Post-retrieval filtering
# ---------------------------------------------------------------------------

_UNKNOWN_OUTCOMES = {"unknown", "missing", "outcome code -9", "unclear"}


def _filter_meaningful(analogues: list[Analogue], top_k: int) -> list[Analogue]:
    """Prefer analogues with known outcomes; fall back to unknown ones if needed.

    Tries to fill top_k slots with events that have a meaningful outcome string.
    If there aren't enough, pads with the remaining candidates rather than
    returning an empty list.
    """
    known = [
        a for a in analogues
        if a.event.outcome and a.event.outcome.lower() not in _UNKNOWN_OUTCOMES
    ]
    if len(known) >= top_k:
        return known[:top_k]

    # Pad with unknown-outcome events to reach top_k
    unknown = [a for a in analogues if a not in known]
    return (known + unknown)[:top_k]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def retrieve_analogues(
    question: Question,
    config: RunConfig,
    chroma_client,
    session: Session,
) -> list[Analogue]:
    """Retrieve the top-K most structurally similar historical events for *question*.

    Parameters
    ----------
    question:
        The geopolitical forecasting question to find analogues for.
    config:
        RunConfig controlling top_k, similarity_type, weights, and filters.
    chroma_client:
        An initialised ``chromadb.ClientAPI`` instance.
    session:
        An active SQLAlchemy ``Session``.

    Returns
    -------
    list[Analogue]
        Up to ``config.top_k`` analogues, sorted by similarity score descending.
        May be shorter if fewer matching events exist.
    """
    mode = config.similarity_type

    if mode == "embedding":
        candidates = _retrieve_embedding(
            question, config, chroma_client, session, n_results=config.top_k * 4
        )
        candidates.sort(key=lambda a: a.similarity_score, reverse=True)
        return _filter_meaningful(candidates, config.top_k)

    if mode == "metadata":
        return _filter_meaningful(_retrieve_metadata(question, config, session), config.top_k)

    if mode == "hybrid":
        return _filter_meaningful(_retrieve_hybrid(question, config, chroma_client, session), config.top_k)

    raise ValueError(f"Unknown similarity_type '{mode}'. Must be embedding, hybrid, or metadata.")
