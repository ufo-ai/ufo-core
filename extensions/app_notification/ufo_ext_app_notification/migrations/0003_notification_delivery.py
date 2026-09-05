"""notification delivery

The delivery stamps, and the `deliver` grant carried onto the `notification` agent rows a workspace
already holds. A provisioning pass writes setup and purpose alone, so the allowlist this release
declares would otherwise reach new workspaces only; no member can write an allowlist, so every live
shipped row takes it. The prompt moves where the row still says what the previous release said, and
the recorded version moves with both."""

import sqlalchemy as sa
from alembic import op
from ufo_ext_app_notification.manifest import (
    NAME,
    NOTIFICATION_AGENT,
    NOTIFICATION_AGENT_PROMPT,
    VERSION,
)
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME

revision: str = "notification_0003"
down_revision: str | None = "notification_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

PREVIOUS_VERSION = "0.2.0"
PREVIOUS_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. A turn that opens with a `<notifications>` block is a batch "
    "raised for one member since you last read their inbox: pass on what changes what they do "
    "today — revenue moving, production down, a customer or investor waiting on them — and reply "
    "with that decision, in one message in your own words saying what happened and what it means "
    "for them. Drop the rest without comment: routine syncs, green runs, receipts, newsletters. A "
    "member who hears from you about everything stops reading you. "
    "When a member asks what has been raised for them, list the kind and answer from it; when "
    "they say one is handled or unwanted, delete it. Your homepage lists the same kind; when a "
    "member asks you to change the page, load the skill `app-notification-home` and follow it."
)
PREVIOUS_TOOLS = (
    "object_list",
    "object_get",
    "object_explain",
    "object_delete",
    "load_skill",
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "action:site:deploy_website",
    "action:agent:set_homepage",
)

agent = sa.table(
    "agent",
    sa.column("prompt", sa.Text),
    sa.column("tools", sa.JSON),
    sa.column("provisioned_by", sa.Text),
    sa.column("provisioned_name", sa.Text),
    sa.column("provisioned_version", sa.Text),
    sa.column("archived_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

LIVE_SHIPPED = sa.and_(
    agent.c.provisioned_by == NAME,
    agent.c.provisioned_name == NOTIFICATION_AGENT_NAME,
    agent.c.archived_at.is_(None),
)


def upgrade() -> None:
    with op.batch_alter_table("notification") as batch:
        batch.add_column(sa.Column("delivered_turn_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("delivered_surface", sa.Text(), nullable=True))
    op.create_index(
        "notification_delivered_turn", "notification", ["workspace_id", "delivered_turn_id"]
    )
    op.execute(
        agent.update()
        .where(LIVE_SHIPPED, agent.c.prompt == PREVIOUS_PROMPT)
        .values(prompt=NOTIFICATION_AGENT_PROMPT)
    )
    op.execute(
        agent.update()
        .where(LIVE_SHIPPED)
        .values(
            tools=list(NOTIFICATION_AGENT.tools or ()),
            provisioned_version=VERSION,
            updated_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    op.execute(
        agent.update()
        .where(LIVE_SHIPPED, agent.c.prompt == NOTIFICATION_AGENT_PROMPT)
        .values(prompt=PREVIOUS_PROMPT)
    )
    op.execute(
        agent.update()
        .where(LIVE_SHIPPED)
        .values(
            tools=list(PREVIOUS_TOOLS),
            provisioned_version=PREVIOUS_VERSION,
            updated_at=sa.func.now(),
        )
    )
    op.drop_index("notification_delivered_turn", "notification")
    with op.batch_alter_table("notification") as batch:
        batch.drop_column("delivered_surface")
        batch.drop_column("delivered_turn_id")
