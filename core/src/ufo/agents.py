"""The core-registered `agent` object kind: the workspace's agent as a workspace object.

Each agent field has exactly one write path, so this kind and the governance proposal path can
never conflict. Spec holds the model, reasoning effort, and public-internet policy, applied
directly and admin-gated over the `agent` table; `prompt` belongs to `Governance`'s proposal CAS
and appears here read-only in status beside its digest (the `from_digest` a proposal presents, so
`object_get agent` is the read half of the proposal flow) — except at birth: create takes the
initial prompt, the one write that is not an edit, and every later prompt change goes through the
proposal path. Create is admin-gated on the main agent's lane and never copies grants,
credentials, sources, or derived data — a new agent starts empty. Delete raises; a non-admin
mutation raises `AdminRequired`."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.governance import prompt_digest
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
from ufo.schema.records import ReasoningEffort
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

AGENT_KIND = "agent"
AGENT_UNDELETABLE = "agents cannot be deleted through objects"
AGENT_EDIT_GATE = (
    "editing an agent requires a workspace admin; editing another agent also requires "
    "the main agent"
)
AGENT_CREATE_GATE = "creating an agent requires a workspace admin, on the main agent"
AGENT_PROMPT_REQUIRED = "creating an agent requires a prompt"
AGENT_PROMPT_IS_PROPOSED = (
    "an existing agent's prompt changes through the governed proposal path, never object_apply"
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
            "status reports the concrete model an 'auto' agent resolves to. The system prompt is "
            "not part of this spec — prompt changes go through the governed proposal path; read "
            "the current prompt and its digest from status."
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
    prompt: str | None = Field(
        default=None,
        description=(
            "The agent's system prompt — accepted only when creating an agent. An existing "
            "agent's prompt changes through the governed proposal path; read it from status."
        ),
    )


@dataclass(frozen=True)
class AgentObjects:
    """Handlers over the `agent` table: apply rewrites the model, reasoning effort, and internet
    policy in place and creates a missing agent, admin-gated; past birth the prompt is
    proposal-owned and rendered read-only in status."""

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
        return (
            None
            if row is None
            else ObjectDetail(
                spec=AgentSpec(
                    model=row.model,
                    internet_access_allowed=row.internet_access_allowed,
                    reasoning=row.reasoning,
                ),
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
            "prompt": row.prompt,
            "prompt_digest": prompt_digest(row.prompt),
            "model": _effective_model(ctx, row.model),
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
        if spec.prompt is not None:
            raise VerbNotSupported(AGENT_PROMPT_IS_PROPOSED)
        row = await self._row(name)
        if row is None:
            raise UnknownObject(f"no agent object named {name!r}")
        if not await ctx.speaker_is_admin() or (
            row.id != ctx.turn.agent_id and not await ctx.agent_is_main()
        ):
            raise AdminRequired(AGENT_EDIT_GATE)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(
                    model=spec.model,
                    internet_access_allowed=spec.internet_access_allowed,
                    reasoning=spec.reasoning,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
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
        """The named agent plus the workspace main agent's name, so a child agent's owning-scope
        link costs no second round trip."""
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
        "A workspace agent: its model, reasoning effort, and public-internet policy, readable by "
        "all members, updatable and creatable by a workspace admin. It cannot be deleted through "
        "objects."
    ),
    guidance=(
        "A workspace agent as an object. Apply {model, internet_access_allowed, reasoning} to "
        "change its model, public-internet access, or reasoning effort — admin only, taking "
        "effect on the next turn. Blocking public internet leaves exact model, credential, "
        "connector, and transfer hosts available. Reasoning 'auto' lets an Anthropic model set "
        "its own thinking depth per request and falls to the provider default elsewhere; a "
        "fixed level pins it. Applying a name no agent holds "
        "creates one — admin only, from the main agent, and the spec then requires `prompt`, the "
        "one write that is not an edit; a new agent starts empty, inheriting no grants, "
        "credentials, sources, or memory. An existing agent's prompt is read-only here: changes "
        "use the governed proposal path, and status carries its current value and digest. The "
        "main agent may manage other agents; a child agent may only manage itself, and its "
        "`scoped_to` link names the main agent it runs under. Delete is "
        "refused. Confirm before changing settings."
    ),
    spec_model=AgentSpec,
    store=AgentObjects(),
)
