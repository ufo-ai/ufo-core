"""The core-registered `agent` object kind: the workspace's agent as a workspace object.

The spec holds the prompt, model, reasoning effort, sandbox size, and public-internet policy.
`object_apply` is their one member write path. In chat, only the main agent can change a prompt;
an admin prepared intent can change any field. Create is admin-gated on the main agent's lane and
never copies grants, credentials, sources, or derived data. Delete raises; a non-admin mutation
raises `AdminRequired`."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.models.interface import AUTO_MODEL
from ufo.objects import (
    AdminRequired,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    UnknownObject,
    VerbNotSupported,
    object_page,
)
from ufo.schema import tables
from ufo.schema.records import (
    DEFAULT_SANDBOX_SIZE,
    INTENT_ADMISSION,
    ReasoningEffort,
    SandboxSize,
)
from ufo.skills.runtime import RuntimeSkill
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

AGENT_KIND = "agent"
AGENT_UNDELETABLE = "agents cannot be deleted through objects"
AGENT_EDIT_GATE = "editing an agent requires a workspace admin"
AGENT_CREATE_GATE = "creating an agent requires a workspace admin, on the main agent"
AGENT_PROMPT_REQUIRED = "creating an agent requires a prompt"
AGENT_TURN_EDIT_GATE = (
    "an agent turn may change only a prompt, and only the workspace main agent may do it"
)


def _effective_model(ctx: ToolContext, stored: str) -> str:
    """The model this agent actually runs, for anything a member reads. The stored value may be the
    `auto` sentinel, which names the deploy's choice rather than a model; the turn already resolved
    it onto `ctx.agent`, so that is the concrete id to report. The spec keeps the stored value, so a
    read-then-apply round trip cannot silently pin an `auto` agent to today's model."""
    return ctx.agent.model if stored == AUTO_MODEL else stored


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(
        description=(
            "The model id the agent runs on, or 'auto' to follow the deploy's configured model — "
            "status reports the concrete model an 'auto' agent resolves to."
        )
    )
    internet_access_allowed: bool = Field(
        description=(
            "Whether this agent may use the deploy's sandbox public-internet capability. False "
            "still permits exact model, credential, connector, and transfer-host egress."
        )
    )
    reasoning: ReasoningEffort = Field(
        description=(
            "Reasoning effort for the agent's turns: a fixed level ('low', 'medium', 'high'), "
            "'off', or 'auto' — an Anthropic model sets its own depth per request; other "
            "providers run auto and off at their default."
        ),
    )
    sandbox_size: SandboxSize = Field(
        default=DEFAULT_SANDBOX_SIZE,
        description=(
            "The cpu/memory tier a new conversation's sandbox is provisioned at, on a deploy "
            "whose sandbox backend offers sizes; a single-shape backend stores and ignores it. "
            "An existing conversation keeps the size its sandbox was created at."
        ),
    )
    prompt: str | None = Field(
        default=None,
        description=(
            "The complete system prompt. Omit it on an update to keep the current prompt. A "
            "change takes effect on the next turn."
        ),
    )


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
    instructions: str = ""


async def pending_setup() -> tuple[tuple[UUID, str, AgentSetup], ...]:
    """Every shipped agent in this workspace a member has not finished wiring, with the grants it
    is still missing. The agent that needs a grant is the one that must ask for it: every grant
    binds to the agent whose conversation it is made in, so this is read to tell that agent what is
    outstanding.

    A connector is met by a grant to any connection of that provider — `connect_account` in the
    agent's own conversation writes one, and `GrantStore.attach` binds a connection that already
    exists, so every declared need has a member-reachable way to settle. An agent no extension
    shipped declares nothing and is never listed."""
    async with workspace_tx() as connection:
        shipped = (
            await connection.execute(
                sa.select(tables.agent.c.id, tables.agent.c.name, tables.agent.c.setup).where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.setup.is_not(None),
                )
            )
        ).all()
        if not shipped:
            return ()
        held = {row.id: set[str]() for row in shipped}
        for granted in await connection.execute(
            sa.select(tables.connector_grant.c.agent_id, tables.connection.c.provider)
            .join(
                tables.connection,
                tables.connector_grant.c.connection_id == tables.connection.c.id,
            )
            .where(tables.connector_grant.c.agent_id.in_(held))
        ):
            held[granted.agent_id].add(granted.provider)
    pending = []
    for row in sorted(shipped, key=lambda row: row.name):
        declared = AgentSetup.model_validate(row.setup)
        providers = held[row.id]
        missing = AgentSetup(
            connectors=tuple(name for name in declared.connectors if name not in providers),
            instructions=declared.instructions,
        )
        if missing.connectors:
            pending.append((row.id, row.name, missing))
    return tuple(pending)


SETUP_SKILL_NAME = "agent-setup"
SETUP_SKILL_DESCRIPTION = (
    "Finish setting up an agent this workspace installed: grant it the accounts it works from. "
    "Load when a member asks to set one up, or asks why one is not working."
)
SETUP_HEADER = (
    "You are installed but not set up. Ask the member for what is missing, then obtain it in this "
    "conversation: `connect_account` for an account. Every grant binds to the agent whose "
    "conversation it is made in, so all of it happens here, with you."
)


async def setup_skill(agent_id: UUID) -> RuntimeSkill | None:
    """The loadable skill telling an agent what it still needs, or None when it needs nothing.

    It reaches the agent itself because that is the only conversation where the grants can land:
    `connect_account` writes to the turn's own agent, and the `source` kind takes no agent target.
    How a member on a surface bound only to the main agent reaches this agent at all is RFC 0033's
    question, not this one's.

    It is a skill rather than a prompt section because it is a task, not a capability: the index
    carries one line, and the instructions reach the model only on the turn a member actually asks.

    Derived from the grants on every turn, so it erases itself as they land rather than needing a
    flag that a later revoke would leave stale."""
    mine = next((entry for entry in await pending_setup() if entry[0] == agent_id), None)
    if mine is None:
        return None
    _, _, missing = mine
    wants = ", ".join(f"a {provider} account" for provider in missing.connectors)
    body = f"{SETUP_HEADER}\n\nYou still need {wants}. {missing.instructions}"
    return RuntimeSkill(
        name=SETUP_SKILL_NAME, description=SETUP_SKILL_DESCRIPTION, instructions=body.rstrip()
    )


@dataclass(frozen=True)
class AgentObjects:
    """Admin-gated handlers over the complete `agent` row."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.name,
                        tables.agent.c.model,
                        tables.agent.c.is_main,
                        tables.agent.c.internet_access_allowed,
                    )
                    .where(tables.agent.c.workspace_id == ws_current().workspace_id)
                    .order_by(tables.agent.c.name)
                )
            ).all()
        return object_page(
            rows=tuple(
                ObjectRow(
                    name=row.name,
                    summary=(
                        f"{'main agent' if row.is_main else 'agent'}, "
                        f"on {_effective_model(ctx, row.model)}, public "
                        f"internet {'allowed' if row.internet_access_allowed else 'blocked'}"
                    ),
                )
                for row in rows
            ),
            query=query,
        )

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None:
        row = await self._row(name)
        if row is None:
            return None
        spec = AgentSpec(
            model=row.model,
            internet_access_allowed=row.internet_access_allowed,
            reasoning=row.reasoning,
            sandbox_size=row.sandbox_size,
            prompt=row.prompt,
        )
        return ObjectDetail(
            spec=spec,
            created_at=row.created_at,
            updated_at=row.updated_at,
            links=(
                ()
                if row.is_main
                else (
                    ObjectLink(
                        relation="scoped_to",
                        target=ObjectRef(kind=AGENT_KIND, name=row.main_agent),
                    ),
                )
            ),
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        row = await self._row(name)
        if row is None:
            return None
        return {
            "main": row.is_main,
            "model": _effective_model(ctx, row.model),
            "provisioned_by": row.provisioned_by,
            "provisioned_name": row.provisioned_name,
            "provisioned_version": row.provisioned_version,
        }

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: AgentSpec,
        old: AgentSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        if old is None:
            await self._create(ctx, name, spec)
            return
        row = await self._row(name)
        if row is None:
            raise UnknownObject(f"no agent object named {name!r}")
        if not await ctx.speaker_is_admin():
            raise AdminRequired(AGENT_EDIT_GATE)
        next_prompt = row.prompt if spec.prompt is None else spec.prompt
        prompt_changed = next_prompt != row.prompt
        next_sandbox_size = (
            spec.sandbox_size if "sandbox_size" in spec.model_fields_set else row.sandbox_size
        )
        settings_changed = (
            spec.model,
            spec.internet_access_allowed,
            spec.reasoning,
            next_sandbox_size,
        ) != (
            row.model,
            row.internet_access_allowed,
            row.reasoning,
            row.sandbox_size,
        )
        if ctx.turn.admission_source != INTENT_ADMISSION and (
            not await ctx.agent_is_main() or settings_changed
        ):
            raise AdminRequired(AGENT_TURN_EDIT_GATE)
        if not next_prompt.strip():
            raise ValueError("an agent prompt cannot be empty")
        if not prompt_changed and not settings_changed:
            return
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(
                    prompt=next_prompt,
                    model=spec.model,
                    internet_access_allowed=spec.internet_access_allowed,
                    reasoning=spec.reasoning,
                    sandbox_size=next_sandbox_size,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.id == row.id,
                )
            )

    async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None:
        """Insert the agent row — never main, never a copy of anything but the submitted
        configuration. The (workspace, name) unique constraint arbitrates a concurrent create of
        the same name; the loser reads back as a name refusal, not a second row."""
        if not await ctx.speaker_is_admin() or not await ctx.agent_is_main():
            raise AdminRequired(AGENT_CREATE_GATE)
        if spec.prompt is None or not spec.prompt.strip():
            raise ValueError(AGENT_PROMPT_REQUIRED)
        async with workspace_tx() as connection:
            try:
                await connection.execute(
                    sa.insert(tables.agent).values(
                        id=uuid4(),
                        workspace_id=ws_current().workspace_id,
                        name=name,
                        prompt=spec.prompt,
                        model=spec.model,
                        is_main=False,
                        internet_access_allowed=spec.internet_access_allowed,
                        reasoning=spec.reasoning,
                        sandbox_size=spec.sandbox_size,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            except IntegrityError as error:
                raise ValueError(f"an agent named {name!r} already exists") from error

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(AGENT_UNDELETABLE)

    async def _row(self, name: str) -> sa.Row | None:
        main = tables.agent.alias("main_agent")
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        tables.agent.c.prompt,
                        tables.agent.c.id,
                        tables.agent.c.model,
                        tables.agent.c.is_main,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.reasoning,
                        tables.agent.c.sandbox_size,
                        tables.agent.c.tools,
                        tables.agent.c.provisioned_by,
                        tables.agent.c.provisioned_name,
                        tables.agent.c.provisioned_version,
                        tables.agent.c.created_at,
                        tables.agent.c.updated_at,
                        sa.select(main.c.name)
                        .where(
                            main.c.workspace_id == ws_current().workspace_id,
                            main.c.is_main.is_(True),
                        )
                        .scalar_subquery()
                        .label("main_agent"),
                    ).where(
                        tables.agent.c.workspace_id == ws_current().workspace_id,
                        tables.agent.c.name == name,
                    )
                )
            ).one_or_none()


AGENT_OBJECT = ObjectKind(
    name=AGENT_KIND,
    description=(
        "A workspace agent: its prompt, model, reasoning effort, and public-internet policy, "
        "readable by all members, updatable and creatable by a workspace admin. It cannot be "
        "deleted through objects."
    ),
    guidance=(
        "A workspace agent as an object. In chat, the main agent may apply the current spec with "
        "only prompt changed — admin only, taking effect on the next turn. Omit prompt to keep it "
        "unchanged. The admin portal may also change model, internet_access_allowed, reasoning, "
        "and sandbox_size. Blocking public internet leaves "
        "exact model, credential, connector, and transfer hosts available. Reasoning 'auto' lets "
        "an Anthropic model set its own thinking depth per request and falls to the provider "
        "default elsewhere; a fixed level pins it. Sandbox size ('small', 'medium', 'large') "
        "picks the cpu/memory tier a new conversation's sandbox is provisioned at, where the "
        "deploy's sandbox backend offers sizes; existing conversations keep the sandbox they "
        "have. Applying a name no agent holds "
        "creates one — admin only, from the main agent, and the spec then requires `prompt`; a new "
        "agent starts empty, inheriting no grants, credentials, sources, or memory. A child's "
        "`scoped_to` link names the main agent it runs under. Delete is refused. Confirm before "
        "changing settings."
    ),
    spec_model=AgentSpec,
    store=AgentObjects(),
)
