"""Brier scoring for geopolitical analogue predictions."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from src.db.models import Prediction, Question


@dataclass
class ScoreResult:
    brier_score: float               # (probability - resolution)²
    resolved_value: float            # ground truth: 0.0 or 1.0
    community_brier_score: float | None  # (community_probability - resolution)²; None if unavailable


def score_prediction(prediction: Prediction, question: Question) -> ScoreResult:
    """Compute Brier score for a single prediction against its resolved question.

    Args:
        prediction: A Prediction ORM instance with probability_estimate.
        question: A Question ORM instance with resolution_value (and optionally community_probability).

    Returns:
        ScoreResult with brier_score, resolved_value, and community_brier_score.

    Raises:
        ValueError: If probability_estimate or resolution_value is None.
    """
    if prediction.probability_estimate is None:
        raise ValueError(
            f"prediction.probability_estimate is None for prediction id={prediction.id!r}; "
            "cannot compute Brier score"
        )
    if question.resolution_value is None:
        raise ValueError(
            f"question.resolution_value is None for question id={question.id!r}; "
            "cannot compute Brier score"
        )

    brier_score = (prediction.probability_estimate - question.resolution_value) ** 2
    assert 0.0 <= brier_score <= 1.0, (
        f"brier_score={brier_score} is outside [0.0, 1.0]; "
        f"probability_estimate={prediction.probability_estimate}, "
        f"resolution_value={question.resolution_value}"
    )

    if question.community_probability is not None:
        community_brier_score: float | None = (
            question.community_probability - question.resolution_value
        ) ** 2
        assert 0.0 <= community_brier_score <= 1.0, (
            f"community_brier_score={community_brier_score} is outside [0.0, 1.0]"
        )
    else:
        community_brier_score = None

    return ScoreResult(
        brier_score=brier_score,
        resolved_value=float(question.resolution_value),
        community_brier_score=community_brier_score,
    )


def compute_run_stats(scores: list[ScoreResult]) -> dict:
    """Return aggregate stats over a list of ScoreResult objects.

    Args:
        scores: List of ScoreResult instances from a single run.

    Returns:
        Dict with keys 'mean_brier', 'median_brier', and 'n'.
        If the list is empty, 'mean_brier' and 'median_brier' are None and 'n' is 0.
    """
    n = len(scores)
    if n == 0:
        return {"mean_brier": None, "median_brier": None, "n": 0}

    brier_scores = [s.brier_score for s in scores]
    return {
        "mean_brier": statistics.mean(brier_scores),
        "median_brier": statistics.median(brier_scores),
        "n": n,
    }
