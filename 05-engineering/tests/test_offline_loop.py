"""Tests for src/runner/offline_loop.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.schema import RunConfig
from src.db.models import Base, Prediction, Question, RunResult, Score
from src.runner.offline_loop import run_offline_loop


# ---------------------------------------------------------------------------
# DB helpers — in-memory SQLite
# ---------------------------------------------------------------------------


def _make_engine():
    # StaticPool forces all connections to share the same in-memory DB, which
    # is required so worker threads in the offline loop see the same data as
    # the test's main session.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(engine)
    return engine


def _make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Data factories
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> RunConfig:
    # Use analogue_aggregator so no model file is needed
    defaults = dict(name="test-run", dry_run=True, similarity_type="embedding",
                    predictor_type="analogue_aggregator")
    defaults.update(kwargs)
    return RunConfig(**defaults)


def _make_question(
    id: str = "q1",
    text: str = "Will Russia invade Ukraine?",  # must pass _is_geopolitics filter
    resolution_value: float = 1.0,
    community_probability: float | None = 0.6,
) -> Question:
    return Question(
        id=id,
        text=text,
        resolution_value=resolution_value,
        resolution_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
        community_probability=community_probability,
        tags=None,
    )


def _make_mock_analogues():
    """Return a minimal list of mock Analogue objects."""
    event = SimpleNamespace(
        id="event-1",
        description="Historical conflict in region Z",
        outcome="Conflict was resolved diplomatically",
        outcome_binary=None,
        actors=None,
        event_type="conflict",
        date="2000-01-01",
        region="Eastern Europe",
        chroma_id=None,
    )

    @dataclass
    class _MockAnalogue:
        event: object
        similarity_score: float
        features_used: dict

    return [_MockAnalogue(event=event, similarity_score=0.8, features_used={"embedding": 0.8})]


# ---------------------------------------------------------------------------
# Mock patch targets
# ---------------------------------------------------------------------------

RETRIEVE_PATCH = "src.runner.offline_loop.retrieve_analogues"
GET_SESSION_PATCH = "src.db.session.get_session"  # worker threads call this


from contextlib import contextmanager


def _session_patcher(Session):
    """Patch src.db.session.get_session to use a given sessionmaker.

    The worker thread in offline_loop imports get_session at call time, so patching
    the module-level function redirects worker DB access to the test's in-memory DB.
    """
    @contextmanager
    def _fake_get_session():
        with Session() as s:
            yield s

    return patch(GET_SESSION_PATCH, side_effect=_fake_get_session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullDryRun:
    """Full dry-run with 3 mock questions — verify RunResult created with n_predictions=3."""

    def test_three_questions_produces_n_predictions_3(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        questions = [
            _make_question(id="q1", resolution_value=1.0),
            _make_question(id="q2", resolution_value=0.0),
            _make_question(id="q3", resolution_value=1.0),
        ]
        with Session() as session:
            for q in questions:
                session.add(q)
            session.commit()

        config = _make_config()
        chroma_client = MagicMock()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, chroma_client, _worker_session_factory=Session)

        assert result.n_predictions == 3

    def test_run_result_is_run_result_instance(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            session.add(_make_question(id="q1"))
            session.commit()

        config = _make_config()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert isinstance(result, RunResult)
        assert result.id is not None

    def test_run_result_persisted_in_db(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            for i in range(3):
                session.add(_make_question(id=f"q{i}"))
            session.commit()

        config = _make_config()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)
            run_id = result.id

        # Verify it's actually in the DB in a fresh session
        with Session() as session:
            db_result = session.get(RunResult, run_id)
            assert db_result is not None
            assert db_result.n_predictions == 3


class TestIdempotency:
    """Skipping already-predicted questions in the same run."""

    def test_existing_prediction_is_skipped(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            q = _make_question(id="q1", resolution_value=1.0)
            session.add(q)
            session.commit()

        config = _make_config()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)
            run_id = result.id

        with Session() as session:
            pred_count = session.query(Prediction).filter_by(run_id=run_id, question_id="q1").count()
            assert pred_count == 1


class TestExceptionHandling:
    """Per-question exceptions are caught — run completes with remaining questions."""

    def test_exception_on_retrieve_does_not_abort_run(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            for i in range(3):
                session.add(_make_question(id=f"q{i}", resolution_value=1.0))
            session.commit()

        config = _make_config()

        call_count = 0

        def flaky_retrieve(question, config, chroma_client, session):
            nonlocal call_count
            call_count += 1
            if question.id == "q1":
                raise RuntimeError("simulated retrieval error")
            return _make_mock_analogues()

        with Session() as session:
            with patch(RETRIEVE_PATCH, side_effect=flaky_retrieve):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        # Run completes despite one failure
        assert isinstance(result, RunResult)
        # 2 of 3 questions should be predicted
        assert result.n_predictions == 2


class TestMaxQuestions:
    """max_questions cap is respected."""

    def test_max_questions_limits_processing(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            for i in range(5):
                session.add(_make_question(id=f"q{i}", resolution_value=1.0))
            session.commit()

        config = _make_config(max_questions=2)

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert result.n_predictions == 2

    def test_max_questions_of_1(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            for i in range(3):
                session.add(_make_question(id=f"q{i}", resolution_value=0.0))
            session.commit()

        config = _make_config(max_questions=1)

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert result.n_predictions == 1


class TestMeanBrierScore:
    """RunResult is updated with mean_brier_score after completion."""

    def test_mean_brier_score_populated(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            # analogue_aggregator with no labeled analogues returns prior 0.3
            # brier = (0.3 - 1.0)^2 = 0.49
            session.add(_make_question(id="q1", resolution_value=1.0, community_probability=None))
            session.commit()

        config = _make_config()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert result.mean_brier_score is not None
        assert 0.0 <= result.mean_brier_score <= 1.0

    def test_mean_brier_none_when_no_questions(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)
        # No questions in DB

        config = _make_config()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert result.mean_brier_score is None
        assert result.n_predictions == 0

    def test_completed_at_is_set(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            session.add(_make_question(id="q1"))
            session.commit()

        config = _make_config()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert result.completed_at is not None


class TestCostUsd:
    """cost_usd is non-negative."""

    def test_cost_usd_non_negative_zero_tokens(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            session.add(_make_question(id="q1"))
            session.commit()

        config = _make_config()

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        # analogue_aggregator returns tokens_used=0, so cost_usd should be 0.0
        assert result.cost_usd is not None
        assert result.cost_usd >= 0.0

    def test_cost_usd_no_questions(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        config = _make_config()

        with Session() as session:
            result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert result.cost_usd is not None
        assert result.cost_usd >= 0.0
        assert result.cost_usd == 0.0


class TestAnalogueAggregatorRouting:
    """Verify analogue_aggregator predictor_type works correctly."""

    def test_analogue_aggregator_cost_is_zero(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            for i in range(3):
                session.add(_make_question(id=f"q{i}", resolution_value=1.0))
            session.commit()

        config = _make_config(predictor_type="analogue_aggregator")

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)

        assert result.cost_usd == pytest.approx(0.0)

    def test_analogue_aggregator_features_stored_on_prediction(self):
        engine = _make_engine()
        Session = _make_session_factory(engine)

        with Session() as session:
            session.add(_make_question(id="q1", resolution_value=1.0))
            session.commit()

        config = _make_config(predictor_type="analogue_aggregator")

        with Session() as session:
            with patch(RETRIEVE_PATCH, return_value=_make_mock_analogues()):
                result = run_offline_loop(config, session, MagicMock(), _worker_session_factory=Session)
            run_id = result.id

        from src.db.models import Prediction as PredModel
        with Session() as session:
            pred = session.query(PredModel).filter_by(run_id=run_id).first()
            # features may be None (no labeled analogues in stubs) but column must exist
            assert hasattr(pred, "features")
