"""Keep portable selections independently of the active catalog."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("payload", sa.String, nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Use a verified backup to restore an earlier state schema")
