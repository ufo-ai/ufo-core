"""The core-registered `agent` object kind: the workspace's agent as a workspace object.

Each agent field has exactly one write path, so this kind and the governance proposal path can
never conflict. Spec is `model` alone — the knob nothing could update before — applied directly,
owner-gated, over the `agent` table; `prompt` belongs to `Governance`'s proposal CAS and appears
here read-only in status beside its digest (the `from_digest` a proposal presents, so
`object_get agent` is the read half of the proposal flow). One agent exists per workspace,
created at `ufoctl init`, so the kind is update-only: the next turn runs under the new model (the
loop reads agent config fresh from the row when it claims each turn). Create and delete raise
with that reason; a non-owner mutation raises `OwnerRequired`."""

from dataclasses import dataclass

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.governance import prompt_digest
from ufo.objects import (
    BoundKind,
    ObjectKind,
    ObjectPage,
    ObjectRow,
    OwnerRequired,
    VerbNotSupported,
)
from ufo.schema import tables
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

AGENT_KIND = "agent"
SINGLE_AGENT = "one agent per workspace today — it is created at workspace init, never applied"
AGENT_UNDELETABLE = "one agent per workspace today — the agent cannot be deleted"
AGENT_EDIT_GATE = "only the workspace owner can edit the agent"


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(
        description=(
            "The model id the agent runs on. The system prompt is not part of this spec — "
            "prompt changes go through the governed proposal path; read the current prompt "
            "and its digest from status."
        )
    )


@dataclass(frozen=True)
class AgentObjects:
    """Update-only handlers over the `agent` table: apply rewrites the model in place,
    owner-gated; the prompt is proposal-owned and rendered read-only in status; the row's name is
    the object's name."""

    async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.agent.c.name, tables.agent.c.model)
                    .where(tables.agent.c.workspace_id == ws_current().workspace_id)
                    .order_by(tables.agent.c.name)
                )
            ).all()
        matched = [row for row in rows if query in row.name or query in row.model]
        remaining = [row for row in matched if row.name > cursor] if cursor else matched
        return ObjectPage(
            rows=tuple(
                ObjectRow(name=row.name, summary=f"the workspace agent, on {row.model}")
                for row in remaining
            )
        )

    async def get(self, ctx: ToolContext, name: str) -> AgentSpec | None:
        row = await self._row(name)
        return None if row is None else AgentSpec(model=row.model)

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        row = await self._row(name)
        if row is None:
            return None
        return {
            "prompt": row.prompt,
            "prompt_digest": prompt_digest(row.prompt),
            "updated_at": row.updated_at.isoformat(),
        }

    async def apply(
        self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None
    ) -> None:
        if old is None:
            raise VerbNotSupported(SINGLE_AGENT)
        if not await ctx.speaker_is_owner():
            raise OwnerRequired(AGENT_EDIT_GATE)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(model=spec.model, updated_at=sa.func.now())
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.name == name,
                )
            )

    async def delete(self, ctx: ToolContext, name: str) -> None:
        raise VerbNotSupported(AGENT_UNDELETABLE)

    async def _row(self, name: str) -> sa.Row | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        tables.agent.c.prompt,
                        tables.agent.c.model,
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
        "The workspace's agent: its model, updatable by the workspace owner. The system prompt "
        "is proposal-owned and read-only here; the agent cannot be created or deleted."
    ),
    guidance=(
        "The workspace's main agent as an object. Apply {model} to switch what it runs on — "
        "workspace owner only, and the update takes effect on the next turn, never mid-turn. "
        "The system prompt is not writable here: prompt changes go through the governed "
        "proposal path, and status shows the current prompt with the digest a proposal is "
        "pinned against. Create and delete are refused: one agent per workspace today. Confirm "
        "with the member before switching models."
    ),
    spec_model=AgentSpec,
    store=AgentObjects(),
)

CORE_OBJECT_KINDS: tuple[BoundKind, ...] = (
    BoundKind(kind=AGENT_OBJECT, extension=None, context=None),
)
