"""SQLAlchemy ORM models for all six tables."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Question(Base):
    """A resolved geopolitical forecasting question from Metaculus."""

    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Metaculus question ID
    text: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_value: Mapped[float | None] = mapped_column(Float)  # 0.0 or 1.0
    community_probability: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    predictions: Mapped[list["Prediction"]] = relationship(back_populates="question")


class HistoricalEvent(Base):
    """A historical geopolitical event from the corpus."""

    __tablename__ = "historical_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    actors: Mapped[list | None] = mapped_column(JSON)
    event_type: Mapped[str | None] = mapped_column(String(64))  # conflict|election|diplomacy|economic|other
    outcome: Mapped[str | None] = mapped_column(Text)
    date: Mapped[str | None] = mapped_column(String(10))  # ISO date string: YYYY-MM-DD
    region: Mapped[str | None] = mapped_column(String(128))
    chroma_id: Mapped[str | None] = mapped_column(String)  # ID in ChromaDB
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunConfig(Base):
    """Hyperparameters for a single experiment run."""

    __tablename__ = "run_configs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    similarity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="embedding")
    embedding_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    metadata_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    metadata_filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="claude-sonnet-4-6")
    max_questions: Mapped[int | None] = mapped_column(Integer)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run_results: Mapped[list["RunResult"]] = relationship(back_populates="config")


class RunResult(Base):
    """Aggregate stats for a completed experiment run."""

    __tablename__ = "run_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    config_id: Mapped[str] = mapped_column(String, ForeignKey("run_configs.id"), nullable=False)
    n_predictions: Mapped[int | None] = mapped_column(Integer)
    mean_brier_score: Mapped[float | None] = mapped_column(Float)
    median_brier_score: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    config: Mapped["RunConfig"] = relationship(back_populates="run_results")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="run")


class Prediction(Base):
    """A single probability prediction for a question in a run."""

    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("run_results.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String, ForeignKey("questions.id"), nullable=False)
    probability_estimate: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    analogues_used: Mapped[list | None] = mapped_column(JSON)  # [{event_id, similarity_score, features_used}]
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["RunResult"] = relationship(back_populates="predictions")
    question: Mapped["Question"] = relationship(back_populates="predictions")
    score: Mapped["Score | None"] = relationship(back_populates="prediction", uselist=False)


class Score(Base):
    """Brier score for a single prediction."""

    __tablename__ = "scores"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    prediction_id: Mapped[str] = mapped_column(
        String, ForeignKey("predictions.id"), nullable=False, unique=True
    )
    brier_score: Mapped[float | None] = mapped_column(Float)
    resolved_value: Mapped[float | None] = mapped_column(Float)
    community_brier_score: Mapped[float | None] = mapped_column(Float)  # nullable — baseline comparison
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    prediction: Mapped["Prediction"] = relationship(back_populates="score")
