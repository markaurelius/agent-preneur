"""edgar_fundamentals — SEC EDGAR XBRL quarterly financial data

Revision ID: 005
Revises: 004
Create Date: 2026-03-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "edgar_fundamentals",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(4), nullable=False),
        sa.Column("period_end", sa.String(10), nullable=True),
        sa.Column("filed_date", sa.String(10), nullable=False),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("net_income", sa.Float(), nullable=True),
        sa.Column("gross_profit", sa.Float(), nullable=True),
        sa.Column("operating_income", sa.Float(), nullable=True),
        sa.Column("total_assets", sa.Float(), nullable=True),
        sa.Column("long_term_debt", sa.Float(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_edgar_ticker_year", "edgar_fundamentals", ["ticker", "fiscal_year"])


def downgrade() -> None:
    op.drop_index("ix_edgar_ticker_year", table_name="edgar_fundamentals")
    op.drop_table("edgar_fundamentals")
