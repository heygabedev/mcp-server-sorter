"""Record job attempts and retain links to explicit retries and request traces."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN parent_job_id TEXT REFERENCES jobs(id)")
    op.add_column("jobs", sa.Column("request_trace_id", sa.String))
    op.add_column("jobs", sa.Column("request_span_id", sa.String))
    op.create_table(
        "job_attempts",
        sa.Column("job_id", sa.String, sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("attempt", sa.Integer, primary_key=True),
        sa.Column("owner", sa.String, nullable=False),
        sa.Column("started_at", sa.Float, nullable=False),
        sa.Column("finished_at", sa.Float),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("error_code", sa.String),
        sa.Column("trace_id", sa.String),
        sa.CheckConstraint("attempt BETWEEN 1 AND 3"),
        sa.CheckConstraint(
            "outcome IN ('running','completed','failed','lease-expired','interrupted')"
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Use a verified backup to restore an earlier state schema")
