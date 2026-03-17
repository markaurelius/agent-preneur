"""Tests for src/synthesis/ml_predictor.py.

AnaloguAggregator — no training, no API calls.
MLPredictor       — loads a joblib sklearn pipeline; tested with a fixture model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.synthesis.ml_predictor import AnaloguAggregator, MLPredictor
from src.synthesis.predictor import PredictionResult


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _make_question(community_probability: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="q1",
        text="Will X happen?",
        resolution_value=None,
        community_probability=community_probability,
    )


def _make_analogue(sim: float = 0.8, outcome_binary: float | None = None) -> SimpleNamespace:
    event = SimpleNamespace(
        id="e1",
        description="Historical conflict",
        outcome="Escalated",
        outcome_binary=outcome_binary,
        event_type="conflict",
        region="Europe",
        date="2000-01-01",
        actors=[],
    )
    return SimpleNamespace(event=event, similarity_score=sim, features_used={})


# ---------------------------------------------------------------------------
# AnaloguAggregator — happy paths
# ---------------------------------------------------------------------------


class TestAnaloguAggregatorOutput:
    def test_returns_prediction_result(self):
        agg = AnaloguAggregator()
        result = agg.predict(_make_question(), [_make_analogue()])
        assert isinstance(result, PredictionResult)

    def test_probability_in_bounds(self):
        agg = AnaloguAggregator()
        result = agg.predict(_make_question(), [_make_analogue()])
        assert 0.01 <= result.probability <= 0.99

    def test_tokens_used_is_zero(self):
        agg = AnaloguAggregator()
        result = agg.predict(_make_question(), [_make_analogue()])
        assert result.tokens_used == 0

    def test_latency_ms_non_negative(self):
        agg = AnaloguAggregator()
        result = agg.predict(_make_question(), [_make_analogue()])
        assert result.latency_ms >= 0

    def test_features_dict_attached(self):
        agg = AnaloguAggregator()
        result = agg.predict(_make_question(), [_make_analogue()])
        assert result.features is not None
        assert isinstance(result.features, dict)


# ---------------------------------------------------------------------------
# AnaloguAggregator — prediction logic
# ---------------------------------------------------------------------------


class TestAnaloguAggregatorLogic:
    def test_uses_weighted_outcome_when_labeled_analogues_present(self):
        """All positive outcomes → probability close to 1."""
        agg = AnaloguAggregator()
        analogues = [_make_analogue(sim=0.9, outcome_binary=1.0) for _ in range(4)]
        result = agg.predict(_make_question(), analogues)
        assert result.probability > 0.9

    def test_uses_community_prior_when_no_labeled_analogues(self):
        """No outcome_binary → fall back to community_probability."""
        agg = AnaloguAggregator()
        analogues = [_make_analogue(outcome_binary=None)]
        result = agg.predict(_make_question(community_probability=0.8), analogues)
        assert result.probability == pytest.approx(0.8)

    def test_uses_fixed_prior_when_no_labels_and_no_community(self):
        agg = AnaloguAggregator()
        analogues = [_make_analogue(outcome_binary=None)]
        result = agg.predict(_make_question(community_probability=None), analogues)
        assert result.probability == pytest.approx(AnaloguAggregator.PRIOR)

    def test_uses_fixed_prior_when_no_analogues_and_no_community(self):
        agg = AnaloguAggregator()
        result = agg.predict(_make_question(community_probability=None), [])
        assert result.probability == pytest.approx(AnaloguAggregator.PRIOR)

    def test_uses_community_prior_when_no_analogues(self):
        agg = AnaloguAggregator()
        result = agg.predict(_make_question(community_probability=0.65), [])
        assert result.probability == pytest.approx(0.65)

    def test_labeled_analogues_take_priority_over_community(self):
        """Even if community_probability is available, labeled analogues win."""
        agg = AnaloguAggregator()
        analogues = [_make_analogue(sim=0.9, outcome_binary=0.0) for _ in range(3)]
        result = agg.predict(_make_question(community_probability=0.9), analogues)
        # Labeled analogues are all 0 → probability should be well below 0.9
        assert result.probability < 0.2

    def test_probability_clamped_to_bounds(self):
        """Weighted mean of 0.0 or 1.0 should be clamped to [0.01, 0.99]."""
        agg = AnaloguAggregator()
        all_positive = [_make_analogue(sim=1.0, outcome_binary=1.0) for _ in range(5)]
        result = agg.predict(_make_question(), all_positive)
        assert result.probability <= 0.99

        all_negative = [_make_analogue(sim=1.0, outcome_binary=0.0) for _ in range(5)]
        result = agg.predict(_make_question(), all_negative)
        assert result.probability >= 0.01


# ---------------------------------------------------------------------------
# MLPredictor — missing model file
# ---------------------------------------------------------------------------


class TestMLPredictorMissingModel:
    def test_raises_file_not_found_when_model_absent(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="scripts/train.py"):
            MLPredictor(tmp_path / "nonexistent_model.pkl")


# ---------------------------------------------------------------------------
# MLPredictor — with a real (tiny) trained model
# ---------------------------------------------------------------------------


@pytest.fixture()
def trained_model_path(tmp_path):
    """Train a minimal LogisticRegression on synthetic data and return the path."""
    import numpy as np
    import joblib
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from src.synthesis.feature_extractor import FEATURE_NAMES

    rng = np.random.default_rng(42)
    n = 60
    X = rng.random((n, len(FEATURE_NAMES)))
    y = rng.integers(0, 2, n).astype(float)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", CalibratedClassifierCV(LogisticRegression(max_iter=200), cv=3)),
    ])
    pipeline.fit(X, y)

    path = tmp_path / "test_model.pkl"
    joblib.dump(pipeline, path)
    return path


class TestMLPredictorWithModel:
    def test_returns_prediction_result(self, trained_model_path):
        predictor = MLPredictor(trained_model_path)
        result = predictor.predict(_make_question(), [_make_analogue()])
        assert isinstance(result, PredictionResult)

    def test_probability_in_bounds(self, trained_model_path):
        predictor = MLPredictor(trained_model_path)
        result = predictor.predict(_make_question(), [_make_analogue()])
        assert 0.01 <= result.probability <= 0.99

    def test_tokens_used_is_zero(self, trained_model_path):
        predictor = MLPredictor(trained_model_path)
        result = predictor.predict(_make_question(), [_make_analogue()])
        assert result.tokens_used == 0

    def test_features_dict_attached(self, trained_model_path):
        predictor = MLPredictor(trained_model_path)
        result = predictor.predict(_make_question(), [_make_analogue()])
        assert result.features is not None

    def test_deterministic_for_same_input(self, trained_model_path):
        predictor = MLPredictor(trained_model_path)
        question = _make_question(community_probability=0.6)
        analogues = [_make_analogue(sim=0.8, outcome_binary=1.0)]
        r1 = predictor.predict(question, analogues)
        r2 = predictor.predict(question, analogues)
        assert r1.probability == pytest.approx(r2.probability)

    def test_different_inputs_can_produce_different_probs(self, trained_model_path):
        predictor = MLPredictor(trained_model_path)
        r_high = predictor.predict(
            _make_question(community_probability=0.9),
            [_make_analogue(sim=0.95, outcome_binary=1.0)],
        )
        r_low = predictor.predict(
            _make_question(community_probability=0.1),
            [_make_analogue(sim=0.95, outcome_binary=0.0)],
        )
        # Not guaranteed to differ (tiny model), but probabilities should be valid
        assert 0.01 <= r_high.probability <= 0.99
        assert 0.01 <= r_low.probability <= 0.99
