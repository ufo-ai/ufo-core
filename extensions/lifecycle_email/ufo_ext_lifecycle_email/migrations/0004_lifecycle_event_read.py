"""lifecycle_event_read"""

import sqlalchemy as sa
from alembic import op

revision: str = "lifecycle_email_0004"
down_revision: str | None = "lifecycle_email_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """The image this replaces enrolled an event as it logged it, so every row already here has
    been offered to the sequences that measure from it. Marking them read is what stops the
    reconciling pass offering the whole log a second time on its first tick.

    The stamp reaches only the rows the table holds while this runs. The outgoing image serves
    until the new pods are ready, and every event its sweeps log in that window carries a null
    stamp and an enrolment of its own already — so the refusal moves onto the event.
    `lifecycle_enrollment_event` is unique over the event and the sequence in *every* state, where
    `lifecycle_enrollment_live` covers the live rows alone: a one-step sequence due at once fires
    and ends inside one pass, so the live index would not see the enrolment the outgoing image made
    and the member would be sent the same message twice."""
    op.add_column(
        "lifecycle_event",
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text("update lifecycle_event set matched_at = created_at"))
    op.create_index(
        "lifecycle_event_unread",
        "lifecycle_event",
        ["workspace_id", "occurred_at"],
        sqlite_where=sa.text("matched_at is null"),
        postgresql_where=sa.text("matched_at is null"),
    )
    op.create_index(
        "lifecycle_enrollment_event",
        "lifecycle_enrollment",
        ["workspace_id", "event_id", "sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("lifecycle_enrollment_event", "lifecycle_enrollment")
    op.drop_index("lifecycle_event_unread", "lifecycle_event")
    op.drop_column("lifecycle_event", "matched_at")
