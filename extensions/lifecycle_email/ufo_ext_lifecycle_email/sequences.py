"""The sequences this extension ships in the tree, and the two value types they are written in.

A sequence measures its steps from an instant: the event a member enrolls on. Its words are a module
here rather than a row because they are product copy — a diff reviews them, and a change to one
ships with the deploy that changed it.

`compose` answering None is the exit: the sequence looked again and found the reason for the
message gone, so the enrollment ends having sent nothing. That is the only exit condition there is,
and a sequence that wants one writes it into its own `compose`."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from ufo.sdk.context import ExtensionContext
from ufo.sdk.seats import has_spoken, invited_member, workspace_domain

MEMBER_INVITED = "member_invited"

INVITED_TEAMMATE = "invited_teammate"
INVITED_TEAMMATE_KIND = "invited_teammate_reminder"
INVITED_TEAMMATE_AFTER = timedelta(days=2)
INVITATION_WINDOW = INVITED_TEAMMATE_AFTER
"""How far back the enrolling sweep looks for an invitation, which bounds both the workspaces it
runs in and the members it reads.

It can never exceed the earliest step measured from an invitation, and so it is that step. An
enrollment is written at `invited_at + after`, so reading further back writes rows already past
due, and the runner claims every one of them on the next tick — turning the flag on would mail
everyone invited between the two figures at once, which is the backlog the manifest promises not
to send.

It is the product rule too: a sweep that fell behind by longer than this has missed those members
rather than owing them a late message."""

SUBJECT = "{inviter} added you to {workspace}"
BODY = (
    "You have a seat on {workspace} and have not signed in yet.\n\n"
    "Everything happens in chat. Ask the agent for what you need, and connect the accounts it "
    "should work in."
)
ACTION_LABEL = "Sign in"
UNNAMED_WORKSPACE = "a ufo workspace"


@dataclass(frozen=True)
class Composed:
    """One message, addressed and written. `kind` is what control records the send under and what
    the attempt row is keyed by, so an operator reading either ledger can tell one step of one
    sequence from another.

    Words, never markup: control draws every message this deploy sends in the one frame."""

    address: str
    kind: str
    subject: str
    body: str
    action_label: str | None = None
    action_url: str | None = None


@dataclass(frozen=True)
class Step:
    """One message in a sequence, due `after` the instant the enrollment measures from. `compose`
    answers None where the reason for the message is gone, which ends the enrollment."""

    after: timedelta
    compose: Callable[[ExtensionContext, UUID], Awaitable[Composed | None]]


@dataclass(frozen=True)
class Sequence:
    """A member enrolls on `event` and receives `steps` in order, each measured from that event's
    instant rather than from the step before it: a delay is what the member experiences, and
    chaining from the previous send would carry every late send forward into the next."""

    name: str
    event: str
    steps: tuple[Step, ...]


async def invited_teammate(ctx: ExtensionContext, member_id: UUID) -> Composed | None:
    """The reminder an added teammate who never showed up reads. It ends unsent once they have
    spoken, once their seat is gone, and on a deploy with no portal to sign in to."""
    url = ctx.home_url()
    if url is None:
        return None
    async with ctx.transaction() as connection:
        invited = await invited_member(connection, ctx.workspace_id, member_id)
        if invited is None or not invited.seated or invited.inviter is None:
            return None
        if await has_spoken(connection, ctx.workspace_id, member_id):
            return None
        workspace = await workspace_domain(connection, ctx.workspace_id)
    return Composed(
        address=invited.email,
        kind=INVITED_TEAMMATE_KIND,
        subject=SUBJECT.format(inviter=invited.inviter, workspace=workspace or UNNAMED_WORKSPACE),
        body=BODY.format(workspace=workspace or UNNAMED_WORKSPACE),
        action_label=ACTION_LABEL,
        action_url=url,
    )


SEQUENCES: tuple[Sequence, ...] = (
    Sequence(
        name=INVITED_TEAMMATE,
        event=MEMBER_INVITED,
        steps=(Step(after=INVITED_TEAMMATE_AFTER, compose=invited_teammate),),
    ),
)
