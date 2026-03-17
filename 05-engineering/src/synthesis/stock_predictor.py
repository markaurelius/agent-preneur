"""LightGBM-based stock outperformance predictor.

Uses a pre-trained sklearn-compatible pipeline (saved via joblib) to predict
the probability that a stock outperforms the S&P 500 over 12 months, given a
fundamental snapshot dict.
"""
from __future__ import annotations

import logging
import time

import joblib
import numpy as np
import pandas as pd

from src.synthesis.prediction_result import PredictionResult
from src.synthesis.stock_features import (
    STOCK_FEATURE_NAMES,
    extract_stock_features,
    features_to_vector,
)

logger = logging.getLogger(__name__)


def confidence_label(probability: float) -> str:
    """Return 'high', 'medium', or 'low' based on distance from 0.5.

    High   : |prob - 0.5| >= 0.20  (prob ≥ 0.70 or prob ≤ 0.30)
    Medium : |prob - 0.5| >= 0.10  (prob in [0.40, 0.60] range boundary)
    Low    : otherwise
    """
    distance = abs(probability - 0.5)
    if distance >= 0.20:
        return "high"
    if distance >= 0.10:
        return "medium"
    return "low"


class StockMLPredictor:
    """Load a saved LightGBM pipeline and predict from fundamental snapshots."""

    def __init__(self, model_path: str) -> None:
        self._pipeline = joblib.load(model_path)
        logger.info("Loaded stock ML model from %s", model_path)

    def predict(self, snapshot: dict) -> PredictionResult:
        """Return a calibrated probability from a fundamental snapshot dict.

        Zero Claude API calls, zero ChromaDB queries.
        """
        start = time.monotonic()
        features = extract_stock_features(snapshot)
        vec = np.array([features_to_vector(features)])
        prob_raw = float(
            self._pipeline.predict_proba(
                pd.DataFrame(vec, columns=STOCK_FEATURE_NAMES)
            )[0, 1]
        )
        probability = max(0.01, min(0.99, prob_raw))
        elapsed_ms = int((time.monotonic() - start) * 1000)

        conf = confidence_label(probability)
        direction = "bullish" if probability >= 0.5 else "bearish"
        rationale = (
            f"LightGBM {direction} ({probability:.1%}) — confidence: {conf}  |  "
            f"pe_vs_sector={features['pe_vs_sector']:.2f}, "
            f"momentum={features['momentum_12_1']:+.1f}%, "
            f"revenue_growth={features['revenue_growth_ttm']:+.1f}%, "
            f"gross_margin={features['gross_margin']:.1f}%"
        )

        return PredictionResult(
            probability=probability,
            rationale=rationale,
            tokens_used=0,
            latency_ms=elapsed_ms,
            features=features,
        )
