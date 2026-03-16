"""Tests for src/scoring/scorer.py."""

from __future__ import annotations

import pytest

from src.scoring.scorer import ScoreResult, compute_run_stats, score_prediction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePrediction:
    """Minimal stand-in for the Prediction ORM model."""

    def __init__(self, probability_estimate, id="pred-1", run_id="run-1", question_id="q-1"):
        self.id = id
        self.run_id = run_id
        self.question_id = question_id
        self.probability_estimate = probability_estimate


class _FakeQuestion:
    """Minimal stand-in for the Question ORM model."""

    def __init__(self, resolution_value, community_probability=None, id="q-1"):
        self.id = id
        self.resolution_value = resolution_value
        self.community_probability = community_probability


# ---------------------------------------------------------------------------
# score_prediction — basic correctness
# ---------------------------------------------------------------------------

def test_perfect_prediction_resolves_true():
    """prob=1.0, resolved=1.0 → brier_score=0.0"""
    pred = _FakePrediction(probability_estimate=1.0)
    q = _FakeQuestion(resolution_value=1.0)
    result = score_prediction(pred, q)
    assert result.brier_score == 0.0
    assert result.resolved_value == 1.0


def test_perfect_wrong_prediction():
    """prob=1.0, resolved=0.0 → brier_score=1.0"""
    pred = _FakePrediction(probability_estimate=1.0)
    q = _FakeQuestion(resolution_value=0.0)
    result = score_prediction(pred, q)
    assert result.brier_score == 1.0
    assert result.resolved_value == 0.0


def test_random_guess():
    """prob=0.5, resolved=1.0 → brier_score=0.25"""
    pred = _FakePrediction(probability_estimate=0.5)
    q = _FakeQuestion(resolution_value=1.0)
    result = score_prediction(pred, q)
    assert result.brier_score == pytest.approx(0.25)
    assert result.resolved_value == 1.0


# ---------------------------------------------------------------------------
# score_prediction — community baseline
# ---------------------------------------------------------------------------

def test_community_brier_computed_when_available():
    """community_brier_score is computed when community_probability is present."""
    pred = _FakePrediction(probability_estimate=0.7)
    q = _FakeQuestion(resolution_value=1.0, community_probability=0.6)
    result = score_prediction(pred, q)
    assert result.community_brier_score is not None
    assert result.community_brier_score == pytest.approx((0.6 - 1.0) ** 2)


def test_community_brier_none_when_unavailable():
    """community_brier_score is None when community_probability is None."""
    pred = _FakePrediction(probability_estimate=0.7)
    q = _FakeQuestion(resolution_value=1.0, community_probability=None)
    result = score_prediction(pred, q)
    assert result.community_brier_score is None


# ---------------------------------------------------------------------------
# score_prediction — error handling
# ---------------------------------------------------------------------------

def test_none_probability_raises_value_error():
    """None probability_estimate must raise ValueError."""
    pred = _FakePrediction(probability_estimate=None)
    q = _FakeQuestion(resolution_value=1.0)
    with pytest.raises(ValueError, match="probability_estimate"):
        score_prediction(pred, q)


def test_none_resolution_value_raises_value_error():
    """None resolution_value must raise ValueError."""
    pred = _FakePrediction(probability_estimate=0.5)
    q = _FakeQuestion(resolution_value=None)
    with pytest.raises(ValueError, match="resolution_value"):
        score_prediction(pred, q)


# ---------------------------------------------------------------------------
# ScoreResult dataclass sanity
# ---------------------------------------------------------------------------

def test_score_result_fields():
    """ScoreResult stores all three fields correctly."""
    result = ScoreResult(brier_score=0.25, resolved_value=1.0, community_brier_score=0.16)
    assert result.brier_score == 0.25
    assert result.resolved_value == 1.0
    assert result.community_brier_score == 0.16


# ---------------------------------------------------------------------------
# compute_run_stats
# ---------------------------------------------------------------------------

def test_compute_run_stats_correct_mean_and_median():
    """Mean and median are computed correctly over a list of scores."""
    scores = [
        ScoreResult(brier_score=0.0, resolved_value=1.0, community_brier_score=None),
        ScoreResult(brier_score=0.25, resolved_value=1.0, community_brier_score=None),
        ScoreResult(brier_score=1.0, resolved_value=0.0, community_brier_score=None),
    ]
    stats = compute_run_stats(scores)
    assert stats["n"] == 3
    assert stats["mean_brier"] == pytest.approx((0.0 + 0.25 + 1.0) / 3)
    assert stats["median_brier"] == pytest.approx(0.25)


def test_compute_run_stats_single_element():
    """Single-element list: mean == median == that element."""
    scores = [ScoreResult(brier_score=0.36, resolved_value=0.0, community_brier_score=None)]
    stats = compute_run_stats(scores)
    assert stats["n"] == 1
    assert stats["mean_brier"] == pytest.approx(0.36)
    assert stats["median_brier"] == pytest.approx(0.36)


def test_compute_run_stats_empty_list():
    """Empty list returns n=0 and None for mean/median."""
    stats = compute_run_stats([])
    assert stats["n"] == 0
    assert stats["mean_brier"] is None
    assert stats["median_brier"] is None
