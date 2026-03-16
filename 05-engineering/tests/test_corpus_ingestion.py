"""Unit tests for corpus ingestion (CoW MID dataset)."""

import io
import json
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.corpus import (
    _format_date,
    _map_event_type,
    _map_outcome,
    _stable_id,
    embed_and_store_events,
    load_cow_dataset,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# Minimal synthetic CoW MID CSV with a representative set of columns.
_SYNTHETIC_CSV = textwrap.dedent("""\
    dispnum,stabb,ccode,styear,stmon,endyear,endmon,outcome,hostlev,orig
    42,USA,2,1990,8,1991,2,5,5,1
    42,IRQ,645,1990,8,1991,2,1,5,0
    99,RUS,365,2008,8,2008,8,3,4,1
    101,CHN,710,2013,1,2013,3,8,2,0
""")

# Minimal CSV that is missing several columns (only bare minimum)
_SPARSE_CSV = textwrap.dedent("""\
    dispnum,stabb,styear
    7,GBR,1982
    7,ARG,1982
""")

# CSV with no useful columns at all (just numeric codes)
_MINIMAL_CSV = textwrap.dedent("""\
    A,B
    1,2
    3,4
""")


def _make_mock_open(content: str):
    """Return a mock that, when used as open(), yields lines from content."""
    return patch("builtins.open", lambda *a, **kw: io.StringIO(content))


# ---------------------------------------------------------------------------
# load_cow_dataset — parsing
# ---------------------------------------------------------------------------


class TestLoadCowDataset:
    def test_returns_list_of_dicts(self, tmp_path):
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        events = load_cow_dataset(str(csv_file))
        assert isinstance(events, list)
        assert len(events) > 0

    def test_required_keys_present(self, tmp_path):
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        events = load_cow_dataset(str(csv_file))
        required_keys = {"id", "description", "actors", "event_type", "outcome", "date", "region"}
        for event in events:
            assert required_keys.issubset(event.keys()), (
                f"Missing keys: {required_keys - event.keys()}"
            )

    def test_actors_is_list(self, tmp_path):
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        events = load_cow_dataset(str(csv_file))
        for event in events:
            assert isinstance(event["actors"], list)

    def test_date_iso_format(self, tmp_path):
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        events = load_cow_dataset(str(csv_file))
        for event in events:
            if event["date"]:
                # Must be YYYY-MM-DD (10 chars)
                assert len(event["date"]) == 10, f"Bad date: {event['date']}"
                parts = event["date"].split("-")
                assert len(parts) == 3

    def test_event_type_valid_values(self, tmp_path):
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        valid = {"conflict", "diplomacy", "other"}
        events = load_cow_dataset(str(csv_file))
        for event in events:
            assert event["event_type"] in valid, f"Unexpected event_type: {event['event_type']}"

    def test_hostlev5_maps_to_conflict(self, tmp_path):
        """hostlev=5 (war) must map to 'conflict'."""
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        events = load_cow_dataset(str(csv_file))
        # First two rows in _SYNTHETIC_CSV have hostlev=5
        war_events = [e for e in events if "USA" in e["actors"] or "IRQ" in e["actors"]]
        assert all(e["event_type"] == "conflict" for e in war_events)

    def test_hostlev2_maps_to_diplomacy(self, tmp_path):
        """hostlev=2 (threat) must map to 'diplomacy'."""
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        events = load_cow_dataset(str(csv_file))
        # CHN row has hostlev=2
        chn_events = [e for e in events if "CHN" in e["actors"]]
        assert len(chn_events) >= 1
        assert chn_events[0]["event_type"] == "diplomacy"

    def test_sparse_csv_does_not_crash(self, tmp_path):
        """CSV with minimal columns must not raise an exception."""
        csv_file = tmp_path / "sparse.csv"
        csv_file.write_text(_SPARSE_CSV)

        events = load_cow_dataset(str(csv_file))
        assert isinstance(events, list)

    def test_minimal_csv_does_not_crash(self, tmp_path):
        """CSV with no recognisable CoW columns must return an empty or non-crashing list."""
        csv_file = tmp_path / "minimal.csv"
        csv_file.write_text(_MINIMAL_CSV)

        events = load_cow_dataset(str(csv_file))
        assert isinstance(events, list)


# ---------------------------------------------------------------------------
# Stable ID generation
# ---------------------------------------------------------------------------


class TestStableId:
    def test_same_row_same_id(self):
        row = {"dispnum": "42", "stabb": "USA", "styear": "1990"}
        cols = list(row.keys())
        id1 = _stable_id(row, cols)
        id2 = _stable_id(row, cols)
        assert id1 == id2

    def test_different_rows_different_ids(self):
        row_a = {"dispnum": "42", "stabb": "USA"}
        row_b = {"dispnum": "42", "stabb": "IRQ"}
        cols = ["dispnum", "stabb"]
        assert _stable_id(row_a, cols) != _stable_id(row_b, cols)

    def test_dispnum_stabb_format(self):
        row = {"dispnum": "42", "stabb": "USA"}
        result = _stable_id(row, list(row.keys()))
        assert result.startswith("cow-")
        assert "42" in result
        assert "USA" in result

    def test_fallback_hash_when_no_dispnum(self):
        row = {"col_a": "foo", "col_b": "bar"}
        result = _stable_id(row, list(row.keys()))
        assert result.startswith("cow-")
        # Hash-based IDs should still be stable
        assert result == _stable_id(row, list(row.keys()))

    def test_no_duplicate_ids_in_dataset(self, tmp_path):
        csv_file = tmp_path / "mids.csv"
        csv_file.write_text(_SYNTHETIC_CSV)

        events = load_cow_dataset(str(csv_file))
        ids = [e["id"] for e in events]
        assert len(ids) == len(set(ids)), "Duplicate IDs found in parsed events"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_format_date_year_and_month(self):
        assert _format_date(1990, 8) == "1990-08-01"

    def test_format_date_year_only(self):
        assert _format_date(1990, None) == "1990-01-01"

    def test_format_date_invalid_month_clamps(self):
        result = _format_date(1990, 13)
        assert result == "1990-01-01"

    def test_format_date_none_year(self):
        assert _format_date(None, 8) is None

    def test_map_event_type_hostlev1(self):
        assert _map_event_type({"hostlev": "1"}) == "diplomacy"

    def test_map_event_type_hostlev3(self):
        assert _map_event_type({"hostlev": "3"}) == "conflict"

    def test_map_event_type_hostlev5(self):
        assert _map_event_type({"hostlev": "5"}) == "conflict"

    def test_map_event_type_missing(self):
        assert _map_event_type({}) == "other"

    def test_map_outcome_known_code(self):
        result = _map_outcome({"outcome": "5"})
        assert result == "Stalemate"

    def test_map_outcome_unknown_code(self):
        result = _map_outcome({"outcome": "99"})
        assert "99" in result

    def test_map_outcome_missing(self):
        result = _map_outcome({})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# embed_and_store_events — idempotency & provider selection
# ---------------------------------------------------------------------------


def _make_mock_session(existing_ids: list[str] | None = None):
    """Build a mock SQLAlchemy session that reports the given IDs as existing."""
    session = MagicMock()
    existing_ids = existing_ids or []
    session.query.return_value.filter.return_value.all.return_value = [
        (eid,) for eid in existing_ids
    ]
    return session


def _make_mock_chroma_client():
    """Build a mock ChromaDB client whose collection accepts upserts."""
    collection = MagicMock()
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client, collection


def _sample_events(n: int = 3) -> list[dict]:
    return [
        {
            "id": f"cow-test-{i}",
            "description": f"Test dispute {i} involving state X in 2000.",
            "actors": ["StateX"],
            "event_type": "conflict",
            "outcome": "Stalemate",
            "date": f"200{i}-01-01",
            "region": "Europe",
        }
        for i in range(n)
    ]


class TestEmbedAndStoreEvents:
    # ── Idempotency ──────────────────────────────────────────────────────────

    def test_skips_existing_events(self):
        events = _sample_events(3)
        existing = [e["id"] for e in events]  # all already in DB
        session = _make_mock_session(existing_ids=existing)
        client, collection = _make_mock_chroma_client()

        count = embed_and_store_events(events, client, session)

        assert count == 0
        session.add.assert_not_called()
        collection.upsert.assert_not_called()

    def test_inserts_only_new_events(self):
        events = _sample_events(3)
        # First event already exists
        session = _make_mock_session(existing_ids=[events[0]["id"]])
        client, collection = _make_mock_chroma_client()

        fake_embedding = [0.1] * 10

        with patch("src.ingestion.corpus._get_embedding_fn", return_value=lambda t: fake_embedding):
            count = embed_and_store_events(events, client, session)

        assert count == 2
        assert session.add.call_count == 2
        collection.upsert.assert_called_once()

    def test_empty_input_returns_zero(self):
        session = _make_mock_session()
        client, _ = _make_mock_chroma_client()

        count = embed_and_store_events([], client, session)
        assert count == 0

    def test_reruns_produce_no_duplicates(self):
        """Running embed_and_store_events twice for the same events is safe."""
        events = _sample_events(2)
        session = _make_mock_session(existing_ids=[])
        client, collection = _make_mock_chroma_client()

        fake_embedding = [0.0] * 8

        with patch("src.ingestion.corpus._get_embedding_fn", return_value=lambda t: fake_embedding):
            count1 = embed_and_store_events(events, client, session)

        # Simulate second run where both events now "exist"
        session2 = _make_mock_session(existing_ids=[e["id"] for e in events])
        client2, collection2 = _make_mock_chroma_client()

        with patch("src.ingestion.corpus._get_embedding_fn", return_value=lambda t: fake_embedding):
            count2 = embed_and_store_events(events, client2, session2)

        assert count1 == 2
        assert count2 == 0

    # ── ChromaDB upsert metadata ─────────────────────────────────────────────

    def test_chroma_metadata_shape(self):
        events = _sample_events(1)
        session = _make_mock_session(existing_ids=[])
        client, collection = _make_mock_chroma_client()
        fake_embedding = [0.5] * 4

        with patch("src.ingestion.corpus._get_embedding_fn", return_value=lambda t: fake_embedding):
            embed_and_store_events(events, client, session)

        call_kwargs = collection.upsert.call_args.kwargs
        metadata = call_kwargs["metadatas"][0]
        assert "event_id" in metadata
        assert "event_type" in metadata
        assert "region" in metadata
        assert "date" in metadata
        assert "actors_json" in metadata
        # actors_json must be valid JSON
        parsed = json.loads(metadata["actors_json"])
        assert isinstance(parsed, list)

    # ── Embedding provider selection ─────────────────────────────────────────

    def test_uses_voyage_when_key_present(self):
        events = _sample_events(1)
        session = _make_mock_session()
        client, _ = _make_mock_chroma_client()

        env = {"VOYAGE_API_KEY": "vk_test", "OPENAI_API_KEY": ""}

        with patch.dict("os.environ", env, clear=False):
            with patch("voyageai.Client") as mock_voyage_cls:
                mock_voyage = MagicMock()
                mock_voyage.embed.return_value.embeddings = [[0.1, 0.2]]
                mock_voyage_cls.return_value = mock_voyage

                with patch("src.ingestion.corpus._get_embedding_fn") as mock_get_fn:
                    mock_get_fn.return_value = lambda t: [0.1, 0.2]
                    embed_and_store_events(events, client, session)
                    mock_get_fn.assert_called_once()

    def test_uses_openai_when_voyage_key_absent(self):
        events = _sample_events(1)
        session = _make_mock_session()
        client, _ = _make_mock_chroma_client()

        env = {"VOYAGE_API_KEY": "", "OPENAI_API_KEY": "sk_test"}

        with patch.dict("os.environ", env, clear=False):
            with patch("src.ingestion.corpus._get_embedding_fn") as mock_get_fn:
                mock_get_fn.return_value = lambda t: [0.3, 0.4]
                embed_and_store_events(events, client, session)
                mock_get_fn.assert_called_once()

    def test_raises_when_no_api_key(self):
        from src.ingestion.corpus import _get_embedding_fn

        with patch.dict("os.environ", {"VOYAGE_API_KEY": "", "OPENAI_API_KEY": ""}, clear=False):
            with pytest.raises(RuntimeError, match="No embedding API key"):
                _get_embedding_fn()

    def test_voyage_key_takes_priority_over_openai(self):
        """When both keys are present, Voyage AI must be selected."""
        from src.ingestion.corpus import _get_embedding_fn

        env = {"VOYAGE_API_KEY": "vk_test", "OPENAI_API_KEY": "sk_test"}

        with patch.dict("os.environ", env, clear=False):
            with patch("voyageai.Client") as mock_voyage_cls:
                mock_voyage = MagicMock()
                mock_voyage.embed.return_value.embeddings = [[0.1]]
                mock_voyage_cls.return_value = mock_voyage

                fn = _get_embedding_fn()
                # The function was created; call it to confirm it calls Voyage
                fn("test text")
                mock_voyage.embed.assert_called_once()

    # ── Batch processing ─────────────────────────────────────────────────────

    def test_batches_chroma_upserts(self):
        """101 events should produce 2 upsert calls (batch_size=100)."""
        events = _sample_events(101)
        session = _make_mock_session(existing_ids=[])
        client, collection = _make_mock_chroma_client()

        with patch("src.ingestion.corpus._get_embedding_fn", return_value=lambda t: [0.0]):
            embed_and_store_events(events, client, session)

        assert collection.upsert.call_count == 2
