"""snapshot_date — add snapshot_date column to stock_snapshots for quarterly granularity

Revision ID: 006
Revises: 005
Create Date: 2026-03-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_snapshots", sa.Column("snapshot_date", sa.String(10), nullable=True))
    op.execute("UPDATE stock_snapshots SET snapshot_date = CAST(year AS TEXT) || '-01-01'")


def downgrade() -> None:
    op.drop_column("stock_snapshots", "snapshot_date")
