"""Persist job leases and bounded operational events."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("idempotency_key", sa.String, unique=True, nullable=False),
        sa.Column("payload", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("owner", sa.String),
        sa.Column("lease_until", sa.Float),
        sa.Column("available_at", sa.Float, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("result", sa.String),
        sa.Column("error_code", sa.String),
    )
    op.create_index("jobs_due", "jobs", ["status", "available_at"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("at", sa.Float, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("code", sa.String, nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Use a verified backup to restore an earlier state schema")
