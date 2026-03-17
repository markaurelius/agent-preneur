"""ML-based probability predictors.

AnaloguAggregator  — zero-training, no API calls; similarity-weighted outcome average.
MLPredictor        — loads a trained sklearn pipeline from disk.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.db.models import Question
    from src.retrieval.retriever import Analogue

from src.synthesis.feature_extractor import extract_features, features_to_vector
from src.synthesis.prediction_result import PredictionResult


class AnaloguAggregator:
    """Zero-training predictor: similarity-weighted average of analogue outcome_binary.

    Prediction priority:
    1. Weighted mean of labeled analogue outcomes  (if any analogues have outcome_binary)
    2. Metaculus community probability             (if available)
    3. Fixed domain prior (0.3)                   (geopolitics skews toward NO)
    """

    PRIOR = 0.3

    def predict(self, question: "Question", analogues: list["Analogue"]) -> PredictionResult:
        start = time.monotonic()
        features = extract_features(question, analogues)

        n_labeled_frac = features["n_labeled_frac"]
        weighted_mean = features["weighted_outcome_mean"]
        community_prob = question.community_probability

        if n_labeled_frac > 0:
            probability = weighted_mean
            rationale = (
                f"Analogue aggregator: {n_labeled_frac:.0%} of {len(analogues)} analogues labeled. "
                f"Similarity-weighted outcome mean = {weighted_mean:.3f}."
            )
        elif community_prob is not None:
            probability = community_prob
            rationale = f"No labeled analogues — using Metaculus community prior: {community_prob:.3f}."
        else:
            probability = self.PRIOR
            rationale = f"No labeled analogues or community prior — using fixed prior: {self.PRIOR}."

        probability = max(0.01, min(0.99, probability))
        elapsed_ms = int((time.monotonic() - start) * 1000)

        return PredictionResult(
            probability=probability,
            rationale=rationale,
            tokens_used=0,
            latency_ms=elapsed_ms,
            features=features,
        )


class MLPredictor:
    """Trained sklearn pipeline predictor.

    Expects a pipeline saved by ``scripts/train.py`` via ``joblib.dump``.
    The pipeline must expose ``predict_proba`` (e.g. CalibratedClassifierCV).
    """

    def __init__(self, model_path: str | Path) -> None:
        import joblib

        self._model_path = Path(model_path)
        if not self._model_path.exists():
            raise FileNotFoundError(
                f"ML model not found at {self._model_path}. "
                "Run `python scripts/train.py` first."
            )
        self._model = joblib.load(self._model_path)

    def predict(self, question: "Question", analogues: list["Analogue"]) -> PredictionResult:
        start = time.monotonic()
        features = extract_features(question, analogues)
        x = features_to_vector(features)

        # predict_proba returns [[P(0), P(1)]]
        prob_array = self._model.predict_proba([x])[0]
        probability = max(0.01, min(0.99, float(prob_array[1])))

        elapsed_ms = int((time.monotonic() - start) * 1000)

        return PredictionResult(
            probability=probability,
            rationale=f"ML model ({self._model_path.stem}): p={probability:.3f}",
            tokens_used=0,
            latency_ms=elapsed_ms,
            features=features,
        )
