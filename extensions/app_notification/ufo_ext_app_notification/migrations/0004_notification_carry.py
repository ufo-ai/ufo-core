"""notification carry

The rows the roll window created. A deploy runs the migrate Job before the fleet rolls, and the
outgoing pods serve until the new ones are ready — so a workspace whose first turn landed on an
outgoing pod in that interval had its `notification` agent created by the previous release, after
the carry that release's replacement ran. Prod holds one such row: created eight minutes after the
migrate Job, at the version, prompt and allowlist of the release that shipped the queue alone.

The row is found by what it says, never by its version: a provisioning pass moves
`provisioned_version` whenever it writes setup or purpose, so a row can name the current release
while holding an earlier prompt. The prompt moves where it still reads as either earlier release
wrote it — a member's own wording stands — and the allowlist and version move on every live shipped
row, which no member can write and which is idempotent to write again. There is nothing to restore
on the way down: the rows it moves are rows the earlier carries meant to move."""

import sqlalchemy as sa
from alembic import op
from ufo_ext_app_notification.manifest import (
    NAME,
    NOTIFICATION_AGENT,
    NOTIFICATION_AGENT_PROMPT,
    VERSION,
)
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME

revision: str = "notification_0004"
down_revision: str | None = "notification_0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

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
EARLIER_PROMPTS = (RELEASED_PROMPT, TRIAGE_PROMPT)

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
    pass
