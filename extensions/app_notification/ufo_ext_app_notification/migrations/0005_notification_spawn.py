"""notification spawn

The `spawn` grant carried onto the `notification` agent rows a workspace already holds. A
provisioning pass writes setup and purpose alone, so the allowlist this release declares would
otherwise reach new workspaces only; no member can write an allowlist, so every live shipped row
takes it. The prompt moves where the row still reads as any earlier release wrote it — a member's
own wording stands — and the recorded version moves with both.

The earlier texts are the whole set, not the last one. A deploy runs the migrate Job before the
fleet rolls and the outgoing pods serve until the new ones are ready, so each release leaves rows
behind it: a row created by the release before last, after the carry that was meant to move it,
still holds that release's prompt when this one runs. Matching the last text alone would stamp such
a row at this version while it ran the older release's instructions.

The release adds no column. Work a notification names goes to the agent whose job it is through the
spawn every turn already has, so there is nothing new for the table to hold."""

import sqlalchemy as sa
from alembic import op
from ufo_ext_app_notification.manifest import (
    NAME,
    NOTIFICATION_AGENT,
    NOTIFICATION_AGENT_PROMPT,
    VERSION,
)
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME

revision: str = "notification_0005"
down_revision: str | None = "notification_0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

PREVIOUS_VERSION = "0.3.0"
PREVIOUS_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. A turn that opens with a `<notifications>` block is a batch "
    "raised for one member since you last read their inbox: pass on what changes what they do "
    "today — revenue moving, production down, a customer or investor waiting on them — with the "
    "`deliver` action, as one message in your own words saying what happened and what it means "
    "for them. Drop the rest without comment: routine syncs, green runs, receipts, newsletters; a "
    "batch with nothing worth interrupting for delivers nothing. A member who hears from you "
    "about everything stops reading you. "
    "When a member asks what has been raised for them, list the kind and answer from it; when "
    "they say one is handled or unwanted, delete it. Your homepage lists the same kind; when a "
    "member asks you to change the page, load the skill `app-notification-home` and follow it."
)
RELEASED_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. When a member asks what has been raised for them, list the kind "
    "and answer from it; when they say one is handled or unwanted, delete it. Your homepage lists "
    "the same kind; when a member asks you to change the page, load the skill "
    "`app-notification-home` and follow it."
)
TRIAGE_PROMPT = (
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
EARLIER_PROMPTS = (RELEASED_PROMPT, TRIAGE_PROMPT, PREVIOUS_PROMPT)
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
    "action:notification:deliver",
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
    op.execute(
        agent.update()
        .where(LIVE_SHIPPED, agent.c.prompt.in_(EARLIER_PROMPTS))
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
