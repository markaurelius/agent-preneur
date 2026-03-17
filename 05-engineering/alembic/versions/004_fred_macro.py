"""fred_macro — FRED macro indicator snapshots keyed by year

Revision ID: 004
Revises: 003
Create Date: 2026-03-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fred_macro",
        sa.Column("year", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("yield_curve_slope", sa.Float(), nullable=True),
        sa.Column("fed_funds_rate", sa.Float(), nullable=True),
        sa.Column("hy_spread", sa.Float(), nullable=True),
        sa.Column("vix", sa.Float(), nullable=True),
        sa.Column("cpi_yoy", sa.Float(), nullable=True),
        sa.Column("market_trend", sa.String(16), nullable=True),
        sa.Column("rate_env", sa.String(16), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("fred_macro")
