"""The core-registered `agent` object kind: the workspace's agent as a workspace object.

Each agent field has exactly one write path, so this kind and the governance proposal path can
never conflict. Spec holds the model and public-internet policy, applied directly and admin-gated
over the `agent` table; `prompt` belongs to `Governance`'s proposal CAS and appears here read-only
in status beside its digest (the `from_digest` a proposal presents, so `object_get agent` is the
read half of the proposal flow). The kind is update-only: changes take effect on the next turn.
Create and delete raise; a non-admin mutation raises `AdminRequired`."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.governance import prompt_digest
from ufo.models.interface import AUTO_MODEL
from ufo.objects import (
    AdminRequired,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.schema import tables
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

AGENT_KIND = "agent"
AGENT_CREATE = "agents cannot be created through object_apply"
AGENT_UNDELETABLE = "agents cannot be deleted through objects"
AGENT_EDIT_GATE = (
    "editing an agent requires a workspace admin; editing another agent also requires "
    "the main agent"
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


@dataclass(frozen=True)
class AgentObjects:
    """Update-only handlers over the `agent` table: apply rewrites the model and internet policy
    in place, admin-gated; the prompt is proposal-owned and rendered read-only in status."""

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
                ),
                created_at=row.created_at,
                updated_at=row.updated_at,
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
            raise VerbNotSupported(AGENT_CREATE)
        row = await self._row(name)
        if row is None:
            raise VerbNotSupported(AGENT_CREATE)
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
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
                )
            )

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(AGENT_UNDELETABLE)

    async def _row(self, name: str) -> sa.Row | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        tables.agent.c.prompt,
                        tables.agent.c.id,
                        tables.agent.c.model,
                        tables.agent.c.is_main,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.created_at,
                        tables.agent.c.updated_at,
                    ).where(
                        tables.agent.c.workspace_id == ws_current().workspace_id,
                        tables.agent.c.name == name,
                    )
                )
            ).one_or_none()


AGENT_OBJECT = ObjectKind(
    name=AGENT_KIND,
    description=(
        "A workspace agent: its model and public-internet policy, readable by all members and "
        "updatable by a workspace admin. It cannot be created or deleted through objects."
    ),
    guidance=(
        "A workspace agent as an object. Apply {model, internet_access_allowed} to change "
        "its model or public-internet access — admin only, taking effect on the next "
        "turn. Blocking public internet leaves exact model, credential, connector, and transfer "
        "hosts available. The system prompt is read-only here: prompt changes use the governed "
        "proposal path, and status carries its current value and digest. The main agent may manage "
        "other agents; a child agent may only manage itself. Create and delete are refused. "
        "Confirm before changing either setting."
    ),
    spec_model=AgentSpec,
    store=AgentObjects(),
)
