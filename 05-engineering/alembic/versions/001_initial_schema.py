"""initial_schema

Revision ID: 001
Revises:
Create Date: 2026-03-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("resolution_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_value", sa.Float(), nullable=True),
        sa.Column("community_probability", sa.Float(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "historical_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("actors", sa.JSON(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("date", sa.String(length=10), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("chroma_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "run_configs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("similarity_type", sa.String(length=32), nullable=False),
        sa.Column("embedding_weight", sa.Float(), nullable=False),
        sa.Column("metadata_weight", sa.Float(), nullable=False),
        sa.Column("metadata_filters", sa.JSON(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("max_questions", sa.Integer(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "run_results",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("config_id", sa.String(), nullable=False),
        sa.Column("n_predictions", sa.Integer(), nullable=True),
        sa.Column("mean_brier_score", sa.Float(), nullable=True),
        sa.Column("median_brier_score", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["config_id"], ["run_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "predictions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("question_id", sa.String(), nullable=False),
        sa.Column("probability_estimate", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("analogues_used", sa.JSON(), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["run_results.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scores",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("prediction_id", sa.String(), nullable=False),
        sa.Column("brier_score", sa.Float(), nullable=True),
        sa.Column("resolved_value", sa.Float(), nullable=True),
        sa.Column("community_brier_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["prediction_id"], ["predictions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prediction_id"),
    )


def downgrade() -> None:
    op.drop_table("scores")
    op.drop_table("predictions")
    op.drop_table("run_results")
    op.drop_table("run_configs")
    op.drop_table("historical_events")
    op.drop_table("questions")
