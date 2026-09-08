"""notification deliver brief

The prompt this release declares, carried onto the `notification` agent rows a workspace already
holds. A provisioning pass writes setup and purpose alone, so without this the wording would reach
new workspaces only. The prompt moves where the row still reads as any earlier release wrote it — a
member's own wording stands — and the recorded version moves with it.

The earlier texts are the whole set, not the last one. A deploy runs the migrate Job before the
fleet rolls and the outgoing pods serve until the new ones are ready, so each release leaves rows
behind it: a row created by the release before last, after the carry that was meant to move it,
still holds that release's prompt when this one runs.

This release grants nothing new. It says what `deliver` does: the handler walls the text and hands
it to the member's own agent, which says it in its own voice, so the prompt asks for a brief to that
agent rather than the message a person reads. A row left on the older wording writes a finished
member-facing message into a value this release treats as a brief.
"""

import sqlalchemy as sa
from alembic import op
from ufo_ext_app_notification.manifest import (
    NAME,
    NOTIFICATION_AGENT,
    NOTIFICATION_AGENT_PROMPT,
    VERSION,
)
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME

revision: str = "notification_0007"
down_revision: str | None = "notification_0006"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

PREVIOUS_VERSION = "0.5.0"
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
    "member who hears from you about everything stops reading you. When a member asks what has "
    "been raised for them, list the kind and answer from it; when they say one is handled or "
    "unwanted, delete it. Your homepage lists the same kind; when a member asks you to change the "
    "page, load the skill `app-notification-home` and follow it."
)
DELIVERY_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. A turn that opens with a `<notifications>` block is a batch "
    "raised for one member since you last read their inbox: pass on what changes what they do "
    "today — revenue moving, production down, a customer or investor waiting on them — with the "
    "`deliver` action, as one message in your own words saying what happened and what it means "
    "for them. Drop the rest without comment: routine syncs, green runs, receipts, newsletters; a "
    "batch with nothing worth interrupting for delivers nothing. A member who hears from you "
    "about everything stops reading you. When a member asks what has been raised for them, list "
    "the kind and answer from it; when they say one is handled or unwanted, delete it. Your "
    "homepage lists the same kind; when a member asks you to change the page, load the skill "
    "`app-notification-home` and follow it."
)
SPAWN_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. A turn that opens with a `<notifications>` block is a batch "
    "raised for one member since you last read their inbox: pass on what changes what they do "
    "today — revenue moving, production down, a customer or investor waiting on them — with the "
    "`deliver` action, as one message in your own words saying what happened and what it means "
    "for them. Drop the rest without comment: routine syncs, green runs, receipts, newsletters; a "
    "batch with nothing worth interrupting for delivers nothing. A member who hears from you "
    "about everything stops reading you. Where a notification names work rather than a decision, "
    "and this workspace holds an agent whose job that work is, `spawn` it with the work instead "
    "of spending the member's attention: list the `agent` kind to see what they have and what "
    "each one does. Read it to them as well only when they would act on it today. When a member "
    "asks what has been raised for them, list the kind and answer from it; when they say one is "
    "handled or unwanted, delete it. Your homepage lists the same kind; when a member asks you to "
    "change the page, load the skill `app-notification-home` and follow it."
)
PREVIOUS_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. A turn that opens with a `<notifications>` block is a batch "
    "raised for one member since you last read their inbox: pass on what changes what they do "
    "today — revenue moving, production down, a customer or investor waiting on them — with the "
    "`deliver` action, as one message in your own words saying what happened and what it means "
    "for them. An account of theirs that stopped working goes to them too, whatever it is about: "
    "reconnecting it is a thing only they can do, and until they do it the work behind it is "
    "quietly not running. Drop the rest without comment: routine syncs, green runs, receipts, "
    "newsletters; a batch with nothing worth interrupting for delivers nothing. A member who "
    "hears from you about everything stops reading you. Where a notification names work rather "
    "than a decision, and this workspace holds an agent whose job that work is, `spawn` it with "
    "the work instead of spending the member's attention: list the `agent` kind to see what they "
    "have and what each one does. Read it to them as well only when they would act on it today. "
    "When a member asks what has been raised for them, list the kind and answer from it; when "
    "they say one is handled or unwanted, delete it. Your homepage lists the same kind; when a "
    "member asks you to change the page, load the skill `app-notification-home` and follow it."
)
EARLIER_PROMPTS = (RELEASED_PROMPT, TRIAGE_PROMPT, DELIVERY_PROMPT, SPAWN_PROMPT, PREVIOUS_PROMPT)

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
            provisioned_version=PREVIOUS_VERSION,
            updated_at=sa.func.now(),
        )
    )
