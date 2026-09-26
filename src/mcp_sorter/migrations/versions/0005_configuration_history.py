"""Record the configuration selected with each catalog activation."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activations", sa.Column("configuration", sa.String))


def downgrade() -> None:
    raise RuntimeError("Use a verified backup to restore an earlier state schema")
