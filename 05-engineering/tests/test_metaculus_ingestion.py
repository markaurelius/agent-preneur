"""Unit tests for Metaculus ingestion (HuggingFace dataset source)."""

from unittest.mock import MagicMock, patch

from src.ingestion.metaculus import _parse_date, _stable_id, upsert_questions


# ── _stable_id ────────────────────────────────────────────────────────────────

def test_stable_id_extracts_numeric_id_from_url():
    row = {"url": "https://www.metaculus.com/questions/1234/some-slug/"}
    assert _stable_id(row) == "metaculus-1234"


def test_stable_id_handles_url_without_trailing_slash():
    row = {"url": "https://www.metaculus.com/questions/5678/slug"}
    assert _stable_id(row) == "metaculus-5678"


def test_stable_id_fallback_to_hash_when_no_url():
    row = {"question": "Will X happen?"}
    sid = _stable_id(row)
    assert sid.startswith("metaculus-")
    assert len(sid) > len("metaculus-")


def test_stable_id_same_row_same_id():
    row = {"question": "Will X happen?"}
    assert _stable_id(row) == _stable_id(row)


def test_stable_id_different_questions_different_ids():
    assert _stable_id({"question": "A?"}) != _stable_id({"question": "B?"})


# ── _parse_date ───────────────────────────────────────────────────────────────

def test_parse_date_iso_format():
    dt = _parse_date("2023-06-01")
    assert dt is not None
    assert dt.year == 2023 and dt.month == 6 and dt.day == 1


def test_parse_date_none_returns_none():
    assert _parse_date(None) is None
    assert _parse_date("") is None


# ── fetch_resolved_questions ──────────────────────────────────────────────────

def _make_hf_row(i: int, resolution: int = 1) -> dict:
    return {
        "question": f"Will thing {i} happen?",
        "resolution": resolution,
        "date_resolve_at": "2023-06-01",
        "date_begin": "2023-01-01",
        "date_close": "2023-05-31",
        "url": f"https://www.metaculus.com/questions/{i}/slug/",
        "nr_forecasters": 50,
        "is_resolved": True,
    }


def test_fetch_filters_out_unresolved():
    from src.ingestion.metaculus import fetch_resolved_questions

    mock_ds = [_make_hf_row(1, resolution=1), _make_hf_row(2, resolution=0),
               {"question": "?", "resolution": None, "url": "", "date_resolve_at": ""}]

    with patch("datasets.load_dataset", return_value=mock_ds):
        results = fetch_resolved_questions()

    assert len(results) == 2
    assert all(r["resolution_value"] in (0.0, 1.0) for r in results)


def test_fetch_maps_fields_correctly():
    from src.ingestion.metaculus import fetch_resolved_questions

    with patch("datasets.load_dataset", return_value=[_make_hf_row(42, 1)]):
        results = fetch_resolved_questions()

    r = results[0]
    assert r["id"] == "metaculus-42"
    assert r["text"] == "Will thing 42 happen?"
    assert r["resolution_value"] == 1.0
    assert r["community_probability"] is None
    assert r["tags"] == []


def test_fetch_returns_empty_when_no_resolved_rows():
    from src.ingestion.metaculus import fetch_resolved_questions

    unresolved = [{"question": "?", "resolution": None, "url": "", "date_resolve_at": ""}]
    with patch("datasets.load_dataset", return_value=unresolved):
        results = fetch_resolved_questions()
    assert results == []


# ── upsert_questions ──────────────────────────────────────────────────────────

def _make_question(id_: str, resolution: float = 1.0) -> dict:
    return {
        "id": id_,
        "text": f"Question {id_}",
        "resolution_value": resolution,
        "resolution_date": None,
        "community_probability": None,
        "tags": [],
    }


def test_upsert_inserts_new_questions():
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = []

    count = upsert_questions([_make_question("metaculus-1"), _make_question("metaculus-2")], session)

    assert count == 2
    assert session.add.call_count == 2


def test_upsert_skips_existing_questions():
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [("metaculus-1",)]

    count = upsert_questions([_make_question("metaculus-1"), _make_question("metaculus-2")], session)

    assert count == 1
    assert session.add.call_count == 1


def test_upsert_empty_list_returns_zero():
    session = MagicMock()
    assert upsert_questions([], session) == 0
    session.add.assert_not_called()
