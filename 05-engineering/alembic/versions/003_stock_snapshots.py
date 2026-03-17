"""stock_snapshots — cache historical (ticker, year) snapshots

Revision ID: 003
Revises: 002
Create Date: 2026-03-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_snapshots",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("label", sa.Float(), nullable=True),
        sa.Column("stock_return", sa.Float(), nullable=True),
        sa.Column("spy_return", sa.Float(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stock_snapshots_ticker_year", "stock_snapshots", ["ticker", "year"])


def downgrade() -> None:
    op.drop_index("ix_stock_snapshots_ticker_year", "stock_snapshots")
    op.drop_table("stock_snapshots")
