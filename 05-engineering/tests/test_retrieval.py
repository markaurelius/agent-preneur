"""Unit tests for the retrieval engine (no real API calls or DB connections)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config.schema import RunConfig
from src.retrieval.retriever import Analogue, retrieve_analogues


# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


class _FakeQuestion:
    """Lightweight stand-in for Question ORM model (avoids SA instrumentation issues)."""

    def __init__(
        self,
        text: str = "Will there be armed conflict between State A and State B in 2020?",
        resolution_date: datetime | None = None,
    ) -> None:
        self.id = "q-test-1"
        self.text = text
        self.resolution_date = resolution_date or datetime(2020, 6, 1, tzinfo=timezone.utc)
        self.resolution_value = None
        self.community_probability = None
        self.tags = []


class _FakeEvent:
    """Lightweight stand-in for HistoricalEvent ORM model."""

    def __init__(
        self,
        event_id: str = "ev-1",
        description: str = "A historical conflict.",
        event_type: str = "conflict",
        region: str = "Europe",
        date: str = "2000-01-01",
        chroma_id: str | None = None,
    ) -> None:
        self.id = event_id
        self.description = description
        self.actors = ["StateX"]
        self.event_type = event_type
        self.outcome = "Stalemate"
        self.date = date
        self.region = region
        self.chroma_id = chroma_id or event_id


def _make_question(
    text: str = "Will there be armed conflict between State A and State B in 2020?",
    resolution_date: datetime | None = None,
) -> _FakeQuestion:
    return _FakeQuestion(text=text, resolution_date=resolution_date)


def _make_event(
    event_id: str = "ev-1",
    description: str = "A historical conflict.",
    event_type: str = "conflict",
    region: str = "Europe",
    date: str = "2000-01-01",
    chroma_id: str | None = None,
) -> _FakeEvent:
    return _FakeEvent(
        event_id=event_id,
        description=description,
        event_type=event_type,
        region=region,
        date=date,
        chroma_id=chroma_id,
    )


def _make_config(**kwargs) -> RunConfig:
    """Create a RunConfig with sensible defaults, overridable via kwargs."""
    defaults = dict(
        name="test-run",
        top_k=3,
        similarity_type="embedding",
        embedding_weight=0.7,
        metadata_weight=0.3,
        metadata_filters={},
    )
    defaults.update(kwargs)
    return RunConfig(**defaults)


def _make_chroma_client(
    event_ids: list[str],
    distances: list[float],
    collection_count: int | None = None,
) -> MagicMock:
    """Build a mock ChromaDB client that returns the given ids/distances."""
    collection = MagicMock()
    collection.count.return_value = collection_count if collection_count is not None else len(event_ids)
    collection.query.return_value = {
        "ids": [event_ids],
        "distances": [distances],
        "metadatas": [[{}] * len(event_ids)],
    }
    client = MagicMock()
    client.get_collection.return_value = collection
    return client


def _make_session(events: list[HistoricalEvent]) -> MagicMock:
    """Build a mock SQLAlchemy session returning the given events on batch query."""
    session = MagicMock()
    # Support chained .query().filter().all() pattern used in _fetch_events_by_ids
    # and .query().filter(attr == val)...all() used in _fetch_filtered_events
    query_mock = MagicMock()
    query_mock.filter.return_value = query_mock
    query_mock.all.return_value = events
    session.query.return_value = query_mock
    return session


# ---------------------------------------------------------------------------
# Embedding mode
# ---------------------------------------------------------------------------


class TestEmbeddingMode:
    def test_returns_top_k_analogues(self):
        events = [_make_event(event_id=f"ev-{i}", date=f"200{i}-01-01") for i in range(5)]
        distances = [0.1 * (i + 1) for i in range(5)]  # increasing distance → decreasing sim
        chroma_ids = [e.id for e in events]

        client = _make_chroma_client(chroma_ids, distances)
        session = _make_session(events)
        config = _make_config(top_k=3, similarity_type="embedding")
        question = _make_question()

        with patch("src.retrieval.retriever._embed_text", return_value=[0.1, 0.2, 0.3]):
            result = retrieve_analogues(question, config, client, session)

        assert len(result) == 3

    def test_sorted_by_similarity_descending(self):
        events = [_make_event(event_id=f"ev-{i}") for i in range(4)]
        # distances 0.4, 0.1, 0.3, 0.2  → similarities 0.6, 0.9, 0.7, 0.8
        distances = [0.4, 0.1, 0.3, 0.2]
        chroma_ids = [e.id for e in events]

        client = _make_chroma_client(chroma_ids, distances)
        session = _make_session(events)
        config = _make_config(top_k=4, similarity_type="embedding")
        question = _make_question()

        with patch("src.retrieval.retriever._embed_text", return_value=[0.5]):
            result = retrieve_analogues(question, config, client, session)

        scores = [a.similarity_score for a in result]
        assert scores == sorted(scores, reverse=True)

    def test_features_used_has_embedding_key(self):
        events = [_make_event(event_id="ev-1")]
        client = _make_chroma_client(["ev-1"], [0.2])
        session = _make_session(events)
        config = _make_config(top_k=1, similarity_type="embedding")

        with patch("src.retrieval.retriever._embed_text", return_value=[0.1]):
            result = retrieve_analogues(_make_question(), config, client, session)

        assert len(result) == 1
        assert "embedding" in result[0].features_used

    def test_similarity_score_is_one_minus_distance(self):
        events = [_make_event(event_id="ev-1")]
        client = _make_chroma_client(["ev-1"], [0.25])
        session = _make_session(events)
        config = _make_config(top_k=1, similarity_type="embedding")

        with patch("src.retrieval.retriever._embed_text", return_value=[0.1]):
            result = retrieve_analogues(_make_question(), config, client, session)

        assert abs(result[0].similarity_score - 0.75) < 1e-6


# ---------------------------------------------------------------------------
# Metadata mode
# ---------------------------------------------------------------------------


class TestMetadataMode:
    def test_returns_top_k_by_date_proximity(self):
        question = _make_question(resolution_date=datetime(2010, 1, 1, tzinfo=timezone.utc))
        # Events at 2010, 1990, 2005, 2015, 1960
        dates = ["2010-01-01", "1990-01-01", "2005-01-01", "2015-01-01", "1960-01-01"]
        events = [_make_event(event_id=f"ev-{i}", date=d) for i, d in enumerate(dates)]

        config = _make_config(top_k=3, similarity_type="metadata")
        session = _make_session(events)

        result = retrieve_analogues(question, config, MagicMock(), session)

        assert len(result) == 3
        # Closest years: 2010 (0 away), 2005 (5 away), 2015 (5 away)
        top_ids = {a.event.id for a in result}
        assert "ev-0" in top_ids  # 2010 should be in top 3

    def test_sorted_by_score_descending(self):
        question = _make_question(resolution_date=datetime(2010, 1, 1, tzinfo=timezone.utc))
        events = [
            _make_event(event_id="ev-far", date="1960-01-01"),
            _make_event(event_id="ev-close", date="2009-01-01"),
            _make_event(event_id="ev-exact", date="2010-01-01"),
        ]
        config = _make_config(top_k=3, similarity_type="metadata")
        session = _make_session(events)

        result = retrieve_analogues(question, config, MagicMock(), session)

        scores = [a.similarity_score for a in result]
        assert scores == sorted(scores, reverse=True)
        assert result[0].event.id == "ev-exact"

    def test_features_used_has_date_proximity_key(self):
        events = [_make_event()]
        config = _make_config(top_k=1, similarity_type="metadata")
        session = _make_session(events)

        result = retrieve_analogues(_make_question(), config, MagicMock(), session)

        assert len(result) == 1
        assert "date_proximity" in result[0].features_used

    def test_filters_by_event_type(self):
        events = [
            _make_event(event_id="ev-conflict", event_type="conflict"),
            _make_event(event_id="ev-diplomacy", event_type="diplomacy"),
            _make_event(event_id="ev-conflict2", event_type="conflict"),
        ]
        # The session mock must honour filters; simulate by returning only matching events
        config = _make_config(top_k=5, similarity_type="metadata", metadata_filters={"event_type": "conflict"})

        # Build a session where .filter().all() returns only conflict events
        conflict_events = [e for e in events if e.event_type == "conflict"]
        session = _make_session(conflict_events)

        result = retrieve_analogues(_make_question(), config, MagicMock(), session)

        assert all(a.event.event_type == "conflict" for a in result)

    def test_filters_by_region(self):
        europe_events = [_make_event(event_id=f"eu-{i}", region="Europe") for i in range(2)]
        config = _make_config(top_k=5, similarity_type="metadata", metadata_filters={"region": "Europe"})
        session = _make_session(europe_events)

        result = retrieve_analogues(_make_question(), config, MagicMock(), session)

        assert all(a.event.region == "Europe" for a in result)


# ---------------------------------------------------------------------------
# Hybrid mode
# ---------------------------------------------------------------------------


class TestHybridMode:
    def _make_hybrid_config(self, top_k: int = 3, metadata_filters: dict | None = None) -> RunConfig:
        return _make_config(
            top_k=top_k,
            similarity_type="hybrid",
            embedding_weight=0.7,
            metadata_weight=0.3,
            metadata_filters=metadata_filters or {},
        )

    def test_returns_top_k_analogues(self):
        n = 9  # wide_k = top_k * 3
        events = [_make_event(event_id=f"ev-{i}", date=f"200{i % 10}-01-01") for i in range(n)]
        distances = [0.1 * (i + 1) for i in range(n)]

        client = _make_chroma_client([e.id for e in events], distances, collection_count=n)
        session = _make_session(events)
        config = self._make_hybrid_config(top_k=3)

        with patch("src.retrieval.retriever._embed_text", return_value=[0.1]):
            result = retrieve_analogues(_make_question(), config, client, session)

        assert len(result) == 3

    def test_features_used_has_all_three_keys(self):
        events = [_make_event(event_id=f"ev-{i}") for i in range(3)]
        client = _make_chroma_client([e.id for e in events], [0.1, 0.2, 0.3], collection_count=3)
        session = _make_session(events)
        config = self._make_hybrid_config(top_k=3)

        with patch("src.retrieval.retriever._embed_text", return_value=[0.5]):
            result = retrieve_analogues(_make_question(), config, client, session)

        for analogue in result:
            assert "embedding" in analogue.features_used
            assert "metadata" in analogue.features_used
            assert "weighted" in analogue.features_used

    def test_weighted_score_formula(self):
        """Verify weighted = 0.7 * emb_score + 0.3 * meta_score."""
        events = [_make_event(event_id="ev-1", date="2020-01-01")]
        # distance=0.0 → embedding_score=1.0
        client = _make_chroma_client(["ev-1"], [0.0], collection_count=1)
        session = _make_session(events)
        config = self._make_hybrid_config(top_k=1)
        question = _make_question(resolution_date=datetime(2020, 1, 1, tzinfo=timezone.utc))

        with patch("src.retrieval.retriever._embed_text", return_value=[0.1]):
            result = retrieve_analogues(question, config, client, session)

        assert len(result) == 1
        a = result[0]
        emb = a.features_used["embedding"]
        meta = a.features_used["metadata"]
        expected = 0.7 * emb + 0.3 * meta
        assert abs(a.features_used["weighted"] - expected) < 1e-9

    def test_reranks_relative_to_metadata(self):
        """An event with high embedding but distant date should be ranked below
        one with similar embedding but close date."""
        question = _make_question(resolution_date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        ev_far = _make_event(event_id="ev-far", date="1950-01-01")    # far in time
        ev_close = _make_event(event_id="ev-close", date="2019-01-01")  # close in time

        # ev_far has better embedding score (lower distance)
        client = _make_chroma_client(
            ["ev-far", "ev-close"],
            [0.1, 0.3],  # ev-far: sim=0.9, ev-close: sim=0.7
            collection_count=2,
        )
        session = _make_session([ev_far, ev_close])
        config = self._make_hybrid_config(top_k=2)

        with patch("src.retrieval.retriever._embed_text", return_value=[0.1]):
            result = retrieve_analogues(question, config, client, session)

        # ev-close should rank higher due to date proximity boosting its hybrid score
        assert result[0].event.id == "ev-close"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_chroma_raises_runtime_error(self):
        collection = MagicMock()
        collection.count.return_value = 0
        client = MagicMock()
        client.get_collection.return_value = collection

        config = _make_config(top_k=3, similarity_type="embedding")
        question = _make_question()
        session = _make_session([])

        with pytest.raises(RuntimeError, match="empty"):
            retrieve_analogues(question, config, client, session)

    def test_missing_chroma_collection_raises_runtime_error(self):
        client = MagicMock()
        client.get_collection.side_effect = Exception("collection not found")

        config = _make_config(top_k=3, similarity_type="embedding")
        session = _make_session([])

        with pytest.raises(RuntimeError, match="does not exist"):
            retrieve_analogues(_make_question(), config, client, session)

    def test_fewer_than_top_k_events_returns_all_available(self):
        """If only 2 events exist but top_k=5, return 2."""
        events = [_make_event(event_id=f"ev-{i}") for i in range(2)]
        client = _make_chroma_client(
            [e.id for e in events],
            [0.1, 0.2],
            collection_count=2,
        )
        session = _make_session(events)
        config = _make_config(top_k=5, similarity_type="embedding")

        with patch("src.retrieval.retriever._embed_text", return_value=[0.1]):
            result = retrieve_analogues(_make_question(), config, client, session)

        assert len(result) == 2

    def test_fewer_than_top_k_in_metadata_mode_returns_all(self):
        events = [_make_event(event_id="only-one")]
        session = _make_session(events)
        config = _make_config(top_k=10, similarity_type="metadata")

        result = retrieve_analogues(_make_question(), config, MagicMock(), session)

        assert len(result) == 1

    def test_metadata_filters_applied_in_embedding_mode(self):
        """Events that don't match metadata_filters are excluded even in embedding mode."""
        eu_event = _make_event(event_id="eu-1", region="Europe")
        asia_event = _make_event(event_id="asia-1", region="Asia-Pacific")

        # ChromaDB returns both; only Europe should survive filter
        client = _make_chroma_client(["eu-1", "asia-1"], [0.1, 0.2], collection_count=2)
        # Session returns both for batch fetch
        session = _make_session([eu_event, asia_event])
        config = _make_config(
            top_k=5,
            similarity_type="embedding",
            metadata_filters={"region": "Europe"},
        )

        with patch("src.retrieval.retriever._embed_text", return_value=[0.5]):
            result = retrieve_analogues(_make_question(), config, client, session)

        assert len(result) == 1
        assert result[0].event.id == "eu-1"

    def test_analogue_dataclass_fields(self):
        ev = _make_event()
        analogue = Analogue(event=ev, similarity_score=0.85, features_used={"embedding": 0.85})
        assert analogue.event is ev
        assert analogue.similarity_score == 0.85
        assert analogue.features_used == {"embedding": 0.85}

    def test_analogue_default_features_used(self):
        ev = _make_event()
        analogue = Analogue(event=ev, similarity_score=0.5)
        assert isinstance(analogue.features_used, dict)
        assert analogue.features_used == {}
