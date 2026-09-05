"""notification triage

The drain's columns, and the triage sentences carried onto the `notification` agent rows a workspace
already holds. A provisioning pass writes setup and purpose alone, so the prompt this release
declares would otherwise reach new workspaces only; the row moves where it still says what the
release that created it said — a member's own wording stands — and the recorded version moves with
it, so it names the declaration the row carries."""

import sqlalchemy as sa
from alembic import op
from ufo_ext_app_notification.manifest import NAME, NOTIFICATION_AGENT_PROMPT, VERSION
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME

revision: str = "notification_0002"
down_revision: str | None = "notification_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260820015537"

OPEN = sa.text("triaged_turn_id is null")
RELEASED_VERSION = "0.1.0"
RELEASED_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. When a member asks what has been raised for them, list the kind "
    "and answer from it; when they say one is handled or unwanted, delete it. Your homepage lists "
    "the same kind; when a member asks you to change the page, load the skill "
    "`app-notification-home` and follow it."
)

agent = sa.table(
    "agent",
    sa.column("prompt", sa.Text),
    sa.column("provisioned_by", sa.Text),
    sa.column("provisioned_name", sa.Text),
    sa.column("provisioned_version", sa.Text),
    sa.column("archived_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _shipped_rows_saying(prompt: str) -> sa.ColumnElement[bool]:
    return sa.and_(
        agent.c.provisioned_by == NAME,
        agent.c.provisioned_name == NOTIFICATION_AGENT_NAME,
        agent.c.archived_at.is_(None),
        agent.c.prompt == prompt,
    )


def upgrade() -> None:
    with op.batch_alter_table("notification") as batch:
        batch.add_column(sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("triaged_turn_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "last_raised_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
    op.execute(sa.text("update notification set last_raised_at = updated_at"))
    op.create_index(
        "notification_untriaged",
        "notification",
        ["workspace_id", "to_agent_id", "member_id"],
        postgresql_where=OPEN,
        sqlite_where=OPEN,
    )
    op.execute(
        agent.update()
        .where(_shipped_rows_saying(RELEASED_PROMPT))
        .values(
            prompt=NOTIFICATION_AGENT_PROMPT, provisioned_version=VERSION, updated_at=sa.func.now()
        )
    )


def downgrade() -> None:
    op.execute(
        agent.update()
        .where(_shipped_rows_saying(NOTIFICATION_AGENT_PROMPT))
        .values(
            prompt=RELEASED_PROMPT, provisioned_version=RELEASED_VERSION, updated_at=sa.func.now()
        )
    )
    op.drop_index("notification_untriaged", "notification")
    with op.batch_alter_table("notification") as batch:
        batch.drop_column("last_raised_at")
        batch.drop_column("triaged_at")
        batch.drop_column("triaged_turn_id")
        batch.drop_column("claim_expires_at")
