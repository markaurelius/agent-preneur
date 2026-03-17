"""ml_columns — add outcome_binary, predictions.features, run_configs.predictor_type

Revision ID: 002
Revises: 001
Create Date: 2026-03-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Binary outcome label on historical events (populated by label_outcomes.py)
    op.add_column(
        "historical_events",
        sa.Column("outcome_binary", sa.Float(), nullable=True),
    )
    # Feature vector logged per prediction (for ML training / analysis)
    op.add_column(
        "predictions",
        sa.Column("features", sa.JSON(), nullable=True),
    )
    # Which predictor generated this run's predictions
    op.add_column(
        "run_configs",
        sa.Column("predictor_type", sa.String(length=32), nullable=False, server_default="claude"),
    )


def downgrade() -> None:
    op.drop_column("run_configs", "predictor_type")
    op.drop_column("predictions", "features")
    op.drop_column("historical_events", "outcome_binary")
