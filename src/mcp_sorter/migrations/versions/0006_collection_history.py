"""Retain immutable collection revisions and a current selection pointer."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collections", sa.Column("current_revision", sa.Integer, nullable=False, server_default="1")
    )
    op.add_column(
        "collections", sa.Column("deleted", sa.Boolean, nullable=False, server_default="0")
    )
    op.create_table(
        "collection_revisions",
        sa.Column("collection_id", sa.String, sa.ForeignKey("collections.id"), primary_key=True),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("payload", sa.String, nullable=False),
        sa.Column("deleted", sa.Boolean, nullable=False),
        sa.Column("changed_at", sa.String, nullable=False),
    )
    op.execute(
        "INSERT INTO collection_revisions "
        "SELECT id,1,payload,0,json_extract(payload,'$.created_at') FROM collections"
    )
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER collection_history_no_{action.lower()} BEFORE {action} "
            "ON collection_revisions BEGIN "
            "SELECT RAISE(ABORT,'Collection history is immutable'); END"
        )


def downgrade() -> None:
    raise RuntimeError("Use a verified backup to restore an earlier state schema")
