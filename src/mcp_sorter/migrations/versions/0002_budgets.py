"""Persist conservative reservations before external model requests."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("microusd", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Use a verified backup to restore an earlier state schema")
