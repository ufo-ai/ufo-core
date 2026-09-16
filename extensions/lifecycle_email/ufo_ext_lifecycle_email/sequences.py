"""The sequences this extension ships in the tree, and the two value types they are written in.

A sequence measures its steps from an instant: the event a member enrolls on. Its words are a module
here rather than a row because they are product copy — a diff reviews them, and a change to one
ships with the deploy that changed it.

`compose` answering None is the exit: the sequence looked again and found the reason for the
message gone, so the enrollment ends having sent nothing. That is the only exit condition there is,
and a sequence that wants one writes it into its own `compose`."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from ufo.sdk.context import ExtensionContext
from ufo.sdk.grants import connection_summaries
from ufo.sdk.seats import Seats, has_spoken, invited_member, workspace_domain

MEMBER_INVITED = "member_invited"
NOTHING_CONNECTED = "nothing_connected"

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

CONNECT_SOMETHING = "connect_something"
CONNECT_SOMETHING_KIND = "connect_something_reminder"
CONNECT_NUDGE_AFTER = timedelta(days=5)
"""How long after an invitation a member who has connected nothing is counted as not having. Long
enough that it names a member who did not get there rather than one who has not got there yet."""

CONNECT_NUDGE_UNTIL = timedelta(days=14)
"""How long the question stays worth asking. Past this the invitation is stale, and a workspace
whose members all connected something leaves the candidate set instead of being read forever."""

SUBJECT = "{inviter} added you to {workspace}"
BODY = (
    "You have a seat on {workspace} and have not signed in yet.\n\n"
    "Everything happens in chat. Ask the agent for what you need, and connect the accounts it "
    "should work in."
)
ACTION_LABEL = "Sign in"
UNNAMED_WORKSPACE = "a ufo workspace"

CONNECT_SUBJECT = "Connect an account to {workspace}"
CONNECT_BODY = (
    "The agent works in the accounts you connect, and you have connected none.\n\n"
    "Open the workspace and ask it to connect one. Email, calendar, Slack, a code host: it "
    "reads and writes what you give it, and nothing else."
)
CONNECT_ACTION_LABEL = "Connect an account"


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


async def who_has_connected(members: Iterable[UUID]) -> frozenset[UUID]:
    """Which of these members can reach an account, from one live read of core's connections.

    A connection the whole workspace shares counts for every member: the agent reaches it on their
    behalf, so telling them they connected nothing would be wrong.

    Live wherever it is asked, never a record that a member once connected something. A member who
    connected an account and removed it has connected nothing, and one who connected an account
    since the event is no longer who the message is about — so the sweep that writes the event and
    the step that comes due days later ask the same question and take the same answer."""
    summaries = await connection_summaries()
    if any(summary.shared for summary in summaries):
        return frozenset(members)
    owners = {
        summary.owner_member_id for summary in summaries if summary.owner_member_id is not None
    }
    return frozenset(member for member in members if member in owners)


async def connect_something(ctx: ExtensionContext, member_id: UUID) -> Composed | None:
    """What a member who has connected nothing reads. It ends unsent once their seat is gone, once
    they have connected something, and on a deploy with no portal to open.

    The absence is asked again here rather than trusted from the event that enrolled them. The
    event and the send are two jobs on two ticks, and the gap holds a deploy roll, a flag turned
    off and on again, and a queue the workspace absorbed — so a member who connected an account
    inside it would otherwise be told they have connected none."""
    url = ctx.home_url()
    if url is None:
        return None
    async with ctx.transaction() as connection:
        seated = next(
            (
                entry
                for entry in (await Seats(ctx.workspace_id).snapshot(connection)).members
                if entry.id == member_id and entry.seated
            ),
            None,
        )
        workspace = await workspace_domain(connection, ctx.workspace_id)
    if seated is None or member_id in await who_has_connected((member_id,)):
        return None
    return Composed(
        address=seated.email,
        kind=CONNECT_SOMETHING_KIND,
        subject=CONNECT_SUBJECT.format(workspace=workspace or UNNAMED_WORKSPACE),
        body=CONNECT_BODY,
        action_label=CONNECT_ACTION_LABEL,
        action_url=url,
    )


SEQUENCES: tuple[Sequence, ...] = (
    Sequence(
        name=INVITED_TEAMMATE,
        event=MEMBER_INVITED,
        steps=(Step(after=INVITED_TEAMMATE_AFTER, compose=invited_teammate),),
    ),
    Sequence(
        name=CONNECT_SOMETHING,
        event=NOTHING_CONNECTED,
        steps=(Step(after=timedelta(0), compose=connect_something),),
    ),
)
