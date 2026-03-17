"""Shared PredictionResult dataclass used by ML predictors."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PredictionResult:
    probability: float  # 0.0–1.0
    rationale: str
    tokens_used: int
    latency_ms: int
    features: dict | None = None  # feature vector logged for ML analysis
