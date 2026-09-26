"""Create the mutable application state; catalogs live in immutable snapshot files."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "state",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.String, nullable=False),
    )
    op.create_table(
        "activations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("snapshot", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.execute("INSERT INTO state VALUES ('schema_version', '1')")


def downgrade() -> None:
    raise RuntimeError("Use a verified backup to restore an earlier state schema")
