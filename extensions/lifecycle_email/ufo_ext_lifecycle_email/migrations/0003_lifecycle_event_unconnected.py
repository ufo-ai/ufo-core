"""lifecycle_event_unconnected"""

from alembic import op

revision: str = "lifecycle_email_0003"
down_revision: str | None = "lifecycle_email_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "lifecycle_event_unconnected",
        "lifecycle_event",
        ["name", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("lifecycle_event_unconnected", table_name="lifecycle_event")
