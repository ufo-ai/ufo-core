"""What a shipped agent offers a member to connect, install, or arm.

It lives apart from `ufo.runtime.kinds.agents` because the portal and the Manifest declaration read
it, and `ufo.runtime.kinds.agents` reaches the extension context, which the portal surface is itself
part of.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field, model_validator

from ufo.db import workspace_tx
from ufo.runtime.access.credentials import deploy_env
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

SCHEDULE_KIND = "scheduled_task"


class SetupCadence(BaseModel):
    """One cadence a shipped app offers for the schedule it needs, in the member's own clock.

    The hour is local, never UTC, because the member picking it is the one who has to recognise the
    time — and a cron field stores UTC, so the surface that knows the member's offset is the one
    that converts. `weekdays` is cron's own numbering (0 is Sunday); empty means every day.

    A null hour is every hour. It names no wall-clock time, so there is nothing to convert and no
    day to shift — which is also why it takes no weekday set: a local weekday spans two UTC days,
    and an hourly cadence has no anchor hour to decide which. An app whose subject arrives through
    the day wants this one, because a once-a-day pass leaves whatever arrived after it until
    tomorrow.

    It carries no label. What a cadence is called follows from the hour and the days — and the
    surface is where the member's clock and their locale's day names are, so the surface says it.
    An app writing its own would be writing the one string that has to agree with the cron it
    converts to, in a place that cannot see the offset."""

    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weekdays: tuple[int, ...] = ()

    @model_validator(mode="after")
    def _hourly_spans_every_day(self) -> "SetupCadence":
        if self.hour is None and self.weekdays:
            raise ValueError(
                f"an hourly cadence names weekdays {self.weekdays} — it has no anchor hour, so a "
                "local weekday cannot be resolved to UTC days"
            )
        if any(day not in range(7) for day in self.weekdays):
            raise ValueError(f"a cadence weekday is 0-6, Sunday first: {self.weekdays}")
        return self


class SetupSchedule(BaseModel):
    """The scheduled task an app offers to arm itself with: the object's name, the prompt it runs
    on each fire, and the cadences it offers.

    The app authors the prompt because the prompt IS the app's job — asking a member to write it is
    asking them to write the app they were given. What the member knows is how often, so that is
    the whole of what they are asked. The name is fixed and stable, so a member who picks a second
    cadence moves the one schedule instead of arming a second."""

    name: str
    prompt: str
    cadences: tuple[SetupCadence, ...] = Field(min_length=1)


class SetupCredential(BaseModel):
    """A stored credential a shipped agent needs, named the way a member would name it.

    `slots` is a set any one of which satisfies the need, because a credential often has more than
    one way in: a GitHub App installation and a fine-grained token reach the same API, and an app
    naming only the first would report itself unready for a workspace that chose the second.

    It sits beside `connectors` rather than inside them because the two are settled by different
    people in different places. A connector is the member's own grant, made in this app's
    conversation. A credential is filled once for the whole workspace, and for an app installation
    by an admin."""

    label: str
    slots: tuple[str, ...] = Field(min_length=1)
    provider: str | None = None
    """The connector whose workspace-wide install fills this credential, where one does.

    Named rather than inferred, because the surface offering the act has to know which act it is and
    a label is prose. A credential no install answers states itself and offers nothing, which is
    honest — there is nothing that member can press — but one that an admin can settle in a single
    press should say so rather than leaving them to find the verb in chat."""

    required: bool = False
    """Whether the app is unusable until this is filled.

    An install is settled once for the whole workspace, and by an admin — a member who is not one
    reads a row they cannot press. So it is optional by default: the app answers the member it has
    in front of it, thinner, rather than refusing until somebody else acts. An app that genuinely
    cannot read anything without the install says so here."""


class AgentSetup(BaseModel):
    """What a shipped agent still needs from a member, and what to do about it. An agent arrives
    with no edges at all: `connectors` names the providers it calls, and `instructions` is what to
    do to obtain them. Stored on the row at creation like the prompt and the model, so the read
    needs no manifest and answers the same after the extension is gone.

    A grant is consent over an account a person owns, so an extension declares this and never
    creates it. A connector names a kind of authority, never an instance: any connection of that
    provider answers it, so which account is the member's choice.

    Source feeds are deliberately absent. A need must be settleable by the agent it is declared
    for, and `object_apply source` writes no grant when the workspace already holds that binding —
    it returns on the existing one — while no verb attaches an existing source to a second agent
    the way `GrantStore.attach` does for a connection. Declaring a source need would state a
    requirement a member cannot always satisfy, so it waits for that primitive."""

    connectors: tuple[str, ...] = ()
    credentials: tuple[SetupCredential, ...] = ()
    """Workspace credentials the agent cannot work without. Declared so the screen can carry the
    row while one is missing: an app that reported itself wired and then failed every run against
    an uninstalled App is the worst of both."""

    standing: tuple[str, ...] = ()
    """The object kinds this agent needs one of before it does anything on its own — a
    `scheduled_task` for an agent a clock wakes, a `source_trigger` for one a feed wakes. An
    account is half of what a shipped agent arrives without: connected and unarmed, it holds the
    authority to work and no occasion to, and a setup list that stopped at the account would call
    that done.

    A kind, never a named object: any standing order of that kind answers it, however it was made.
    It is settleable by the agent it is declared for — the agent applies the kind itself from the
    conversation the member asks in — which is the same bar the connectors meet and the reason
    source feeds are still absent."""

    schedule: SetupSchedule | None = None
    """What to arm the agent's `scheduled_task` with. Declared beside the kind rather than instead
    of it: the kind is what the agent needs, and this is the app's offer of how to meet it without
    the member composing one. It narrows how, never whether — an app that offers cadences is still
    armed by a schedule the member composed in chat instead."""

    instructions: str = ""

    @model_validator(mode="after")
    def _a_clock_need_carries_its_offer(self) -> "AgentSetup":
        """An app that says a clock must wake it offers the cadences that would. A surface stating
        a need it carries no act for is a line the member cannot settle from where they read it —
        they would have to go and compose a cron in chat for an app that already knows exactly what
        it wants to run and only needs to be told how often."""
        if SCHEDULE_KIND in self.standing and self.schedule is None:
            raise ValueError(
                f"a setup declaring a {SCHEDULE_KIND!r} need offers no cadences — an app that a "
                "clock wakes states what it would run and how often it could, or the member is "
                "asked to compose the app's own job for it"
            )
        if self.schedule is not None and SCHEDULE_KIND not in self.standing:
            raise ValueError(
                f"a setup offering cadences declares no {SCHEDULE_KIND!r} need — the offer is "
                "carried by the row stating the need, so an offer with no row is a cadence "
                "nothing on the screen can be armed with"
            )
        return self


class SetupConnector(BaseModel):
    """One offered provider against an account this agent can actually work from: a connection
    granted to it that the reading member may use — the workspace's own, or theirs.

    A grant made privately is usable by the member who made it and by nobody else, so a read that
    counted every grant would tell the second member their app was connected and then refuse every
    call it made. The account itself stays behind `list_agent_connections`; this is the one bit."""

    provider: str
    granted: bool
    required: bool
    """Always false. A connector is an offer: the app works from the accounts the member grants
    and does not require accounts the workspace does not hold."""


class SetupCredentialState(BaseModel):
    """One declared credential, whether any slot that answers it is filled, and the connector whose
    workspace install would fill it."""

    label: str
    filled: bool
    provider: str | None = None
    required: bool


class SetupStanding(BaseModel):
    """One declared standing-order kind and whether this agent holds one."""

    kind: str
    armed: bool
    required: bool
    """Always false. A standing order is an occasion to run unasked; an app holding none still
    answers the member who asks it, so the screen offers the row without demanding it."""

    schedule: str | None = None
    """The cron the order this agent holds fires on, where it is a scheduled task. The screen reads
    it back into the cadence it offered, so a settled step shows the answer the member gave rather
    than only the fact that they gave one."""


class SetupState(BaseModel):
    """What one agent still needs, read by a surface rather than by a turn. The three lists are the
    whole declaration — settled and outstanding together — because a member reading a setup screen
    is asking what the app runs on, not only what is missing, and a list that emptied as the work
    landed would leave the finished app saying nothing about itself.

    Each row carries its own settled bit and the act that settles it, which is what the screen
    draws. Nothing rolls them into one word: a member acts on the row that is outstanding, never on
    a summary of all of them."""

    connectors: tuple[SetupConnector, ...] = ()
    credentials: tuple[SetupCredentialState, ...] = ()
    standing: tuple[SetupStanding, ...] = ()
    schedule: SetupSchedule | None = None
    """The app's own cadence offer, echoed so the surface stating the need carries the act that
    settles it. Stated whether or not the schedule is already armed, for the same reason the
    settled connectors are: the band says what the app runs on, not only what is missing."""

    instructions: str = ""


class ArmedOrder(BaseModel):
    """A standing order this agent holds, if it holds one. `schedule` is the cron a scheduled task
    fires on, so a screen that offered the cadences can say which of them was taken rather than
    only that something was."""

    held: bool
    schedule: str | None = None


Armed = Callable[[str, str | None], Awaitable[ArmedOrder]]
"""Whether this agent holds a standing order of one kind: the one that carries `name`, or any at
all when the caller names none. It is the caller's because the kinds belong to extensions and their
rows live in extension tables: core owns the declaration and the grants, and the surface that has
the object registry answers the rest.

The name goes down rather than a set of names coming back, because one kind holds every feature's
order and an agent's listing of a kind is paged. A caller handed the name reads that one row; a
caller handed a page has to walk to the end of the listing to know an absent name is absent, and one
that reads the first page alone calls a real order missing as soon as the rows before it fill a
page."""


async def _armed(kind: str, wanted: AgentSetup, armed: Armed) -> ArmedOrder:
    """Whether the app's own order of this kind exists.

    An app that offers a schedule names the task that schedule arms, and that name is what is asked
    for: one kind holds every feature's order, so asking the kind alone would read a member turning
    on a second feature as this one being armed.

    A kind the app names no order for is asked for by presence, which is the same question for a
    kind that can only answer it once — a `source_trigger` is named for the pair it is, and one
    conversation over one source holds exactly one."""
    if kind == SCHEDULE_KIND and wanted.schedule is not None:
        return await armed(kind, wanted.schedule.name)
    return await armed(kind, None)


async def setup_state(agent_id: UUID, member_id: UUID, *, armed: Armed) -> SetupState:
    """One agent's declared setup against what this workspace has done about it.

    Derived from the row, the grants, the credentials and the standing orders on every read, so it
    answers the same after a revoke or a deleted schedule as it did before the first one — the
    offer is never a flag to clear.

    Declaration and installs are workspace shape; the accounts are read for `member_id`, because
    that is the only reading of a grant that predicts what the app will do for them. Whose account
    it is stays out: the answer is that they have one, never which."""
    async with workspace_tx() as connection:
        declared = (
            await connection.execute(
                sa.select(tables.agent.c.setup).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.id == agent_id,
                )
            )
        ).one_or_none()
        if declared is None or declared.setup is None:
            return SetupState()
        wanted = AgentSetup.model_validate(declared.setup)
        if not (wanted.connectors or wanted.credentials or wanted.standing):
            return SetupState(instructions=wanted.instructions)
        granted = set(
            (
                await connection.execute(
                    sa.select(tables.connection.c.provider)
                    .join(
                        tables.connector_grant,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                    .where(
                        tables.connector_grant.c.agent_id == agent_id,
                        sa.or_(
                            tables.connection.c.shared,
                            tables.connection.c.owner_member_id == member_id,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        wanted_slots = {slot for credential in wanted.credentials for slot in credential.slots}
        stored_slots = (
            set()
            if not wanted_slots
            else set(
                (
                    await connection.execute(
                        sa.select(tables.credential.c.slot).where(
                            tables.credential.c.workspace_id == ws_current().workspace_id,
                            tables.credential.c.slot.in_(wanted_slots),
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
    connectors = tuple(
        SetupConnector(provider=provider, granted=provider in granted, required=False)
        for provider in wanted.connectors
    )
    # A slot the deploy supplies from its own environment is filled: `WorkspaceScope.credential`
    # falls back to it when the workspace stored none, so the agent obtains the secret and works.
    # Asking the table alone answers "did this workspace bring its own key", which is a different
    # question — and it reported a working app unready for ever, with a row stating a need the
    # member had no act for.
    filled_slots = stored_slots | {slot for slot in wanted_slots if deploy_env(slot.upper())}
    credentials = tuple(
        SetupCredentialState(
            label=credential.label,
            filled=bool(set(credential.slots) & filled_slots),
            provider=credential.provider,
            required=credential.required,
        )
        for credential in wanted.credentials
    )
    standing = tuple(
        [
            SetupStanding(
                kind=kind,
                armed=(order := await _armed(kind, wanted, armed)).held,
                required=False,
                schedule=order.schedule,
            )
            for kind in wanted.standing
        ]
    )
    return SetupState(
        connectors=connectors,
        credentials=credentials,
        standing=standing,
        schedule=wanted.schedule,
        instructions=wanted.instructions,
    )
