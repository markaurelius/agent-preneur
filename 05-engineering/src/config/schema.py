"""Run configuration schema and YAML loader."""

from __future__ import annotations

from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator


class RunConfig(BaseModel):
    name: str
    domain: Literal["geopolitics", "finance"] = "finance"
    corpus_collection: str = "historical_events"
    top_k: int = Field(default=5, ge=1, le=50)
    similarity_type: Literal["embedding", "hybrid", "metadata"] = "embedding"
    embedding_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    metadata_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    metadata_filters: dict = Field(default_factory=dict)
    predictor_type: Literal["analogue_aggregator", "ml"] = "ml"
    model_path: Optional[str] = None  # required when predictor_type == "ml"
    prompt_version: str = "v1"
    model: str = "claude-sonnet-4-6"
    max_questions: int | None = Field(default=None, ge=1)
    min_resolution_year: int | None = Field(default=None, ge=2000, le=2100)
    workers: int = Field(default=1, ge=1, le=20)
    dry_run: bool = False

    @model_validator(mode="after")
    def weights_sum_to_one_in_hybrid(self) -> RunConfig:
        if self.similarity_type == "hybrid":
            total = self.embedding_weight + self.metadata_weight
            if abs(total - 1.0) > 1e-6:
                raise ValueError(
                    f"embedding_weight + metadata_weight must equal 1.0 in hybrid mode, got {total}"
                )
        return self

    model_config = {"frozen": True}  # immutable after construction


def load_config(path: str) -> RunConfig:
    """Load a RunConfig from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return RunConfig(**data)
