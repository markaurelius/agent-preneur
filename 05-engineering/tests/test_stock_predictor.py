"""Tests for StockMLPredictor and confidence_label.

Uses a real tiny LightGBM model (trained in the fixture) rather than mocks,
so the prediction path is exercised end-to-end.
"""
import math
import os
import tempfile

import numpy as np
import pytest

from src.synthesis.stock_features import (
    STOCK_FEATURE_NAMES,
    extract_stock_features,
    features_to_vector,
)
from src.synthesis.stock_predictor import StockMLPredictor, confidence_label

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_SNAPSHOT = {
    "ticker": "AAPL",
    "current_price": 150.0,
    "pe_ratio": 25.0,
    "pe_vs_sector": 0.9,
    "revenue_growth_ttm": 12.5,
    "gross_margin": 45.0,
    "momentum_12_1": 8.3,
    "earnings_revision": "up",
    "roe": 18.0,
    "debt_to_equity": 0.5,
    "short_percent_float": 0.02,
    "price_52w_high": 180.0,
    "price_52w_low": 120.0,
    "macro_regime": {"market_trend": "bull", "rate_env": "rising"},
}


def _make_tiny_model(tmp_path: str) -> str:
    """Train a minimal LightGBM pipeline on synthetic data and save it.

    Returns the path to the saved joblib file.
    """
    import joblib
    import lightgbm as lgb
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(42)
    n = 60
    X = rng.standard_normal((n, len(STOCK_FEATURE_NAMES)))
    y = (X[:, 0] > 0).astype(float)  # simple linearly separable labels

    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                CalibratedClassifierCV(
                    lgb.LGBMClassifier(
                        n_estimators=10, num_leaves=4, verbose=-1, random_state=0
                    ),
                    cv=3,
                    method="isotonic",
                ),
            ),
        ]
    )
    pipeline.fit(X, y)

    model_path = os.path.join(tmp_path, "test_lgbm.pkl")
    joblib.dump(pipeline, model_path)
    return model_path


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def model_path(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("model"))
    return _make_tiny_model(tmp)


# ---------------------------------------------------------------------------
# confidence_label
# ---------------------------------------------------------------------------


class TestConfidenceLabel:
    def test_high_confidence_bullish(self):
        assert confidence_label(0.75) == "high"

    def test_high_confidence_bearish(self):
        assert confidence_label(0.25) == "high"

    def test_exactly_on_high_threshold(self):
        assert confidence_label(0.70) == "high"
        assert confidence_label(0.30) == "high"

    def test_medium_confidence_bullish(self):
        assert confidence_label(0.62) == "medium"

    def test_medium_confidence_bearish(self):
        assert confidence_label(0.38) == "medium"

    def test_exactly_on_medium_threshold(self):
        assert confidence_label(0.60) == "medium"
        assert confidence_label(0.40) == "medium"

    def test_low_confidence_near_50(self):
        assert confidence_label(0.50) == "low"
        assert confidence_label(0.55) == "low"
        assert confidence_label(0.45) == "low"

    def test_boundary_below_medium(self):
        # 0.59 → distance 0.09 < 0.10 → low
        assert confidence_label(0.59) == "low"

    def test_extreme_values(self):
        assert confidence_label(0.01) == "high"
        assert confidence_label(0.99) == "high"


# ---------------------------------------------------------------------------
# StockMLPredictor — loading and basic prediction
# ---------------------------------------------------------------------------


class TestStockMLPredictor:
    def test_loads_without_error(self, model_path):
        predictor = StockMLPredictor(model_path)
        assert predictor is not None

    def test_predict_returns_prediction_result(self, model_path):
        from src.synthesis.predictor import PredictionResult

        predictor = StockMLPredictor(model_path)
        result = predictor.predict(_FULL_SNAPSHOT)
        assert isinstance(result, PredictionResult)

    def test_probability_in_valid_range(self, model_path):
        predictor = StockMLPredictor(model_path)
        result = predictor.predict(_FULL_SNAPSHOT)
        assert 0.01 <= result.probability <= 0.99

    def test_zero_tokens_used(self, model_path):
        predictor = StockMLPredictor(model_path)
        result = predictor.predict(_FULL_SNAPSHOT)
        assert result.tokens_used == 0

    def test_latency_ms_non_negative(self, model_path):
        predictor = StockMLPredictor(model_path)
        result = predictor.predict(_FULL_SNAPSHOT)
        assert result.latency_ms >= 0

    def test_rationale_contains_probability(self, model_path):
        predictor = StockMLPredictor(model_path)
        result = predictor.predict(_FULL_SNAPSHOT)
        # Rationale should include the probability as a percentage
        assert "%" in result.rationale

    def test_features_stored_on_result(self, model_path):
        predictor = StockMLPredictor(model_path)
        result = predictor.predict(_FULL_SNAPSHOT)
        assert result.features is not None
        assert "pe_ratio" in result.features

    def test_empty_snapshot_still_predicts(self, model_path):
        """Model must handle missing data without crashing."""
        predictor = StockMLPredictor(model_path)
        result = predictor.predict({})
        assert 0.01 <= result.probability <= 0.99

    def test_probability_not_nan(self, model_path):
        predictor = StockMLPredictor(model_path)
        result = predictor.predict(_FULL_SNAPSHOT)
        assert not math.isnan(result.probability)

    def test_high_pe_vs_low_pe_differ(self, model_path):
        """Different input snapshots should produce different probabilities."""
        predictor = StockMLPredictor(model_path)
        high_pe_snap = {**_FULL_SNAPSHOT, "pe_ratio": 95.0, "pe_vs_sector": 3.4}
        low_pe_snap = {**_FULL_SNAPSHOT, "pe_ratio": 8.0, "pe_vs_sector": 0.3}
        prob_high = predictor.predict(high_pe_snap).probability
        prob_low = predictor.predict(low_pe_snap).probability
        # They should differ (model is not constant)
        assert prob_high != prob_low
