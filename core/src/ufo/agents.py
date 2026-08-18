"""The core-registered `agent` object kind: the workspace's agent as a workspace object.

The spec holds the prompt, model, reasoning effort, sandbox size, public-internet policy, portal
visibility, icon, and the optional I/O contract a spawn of the agent validates against.
`object_apply` is their one member write path. Any speaking member creates agents and owns the
ones they created; an owner or a workspace admin edits, and an ownerless row — the main agent, a
provisioned agent — answers to admins alone. Create never copies grants, credentials, sources, or
derived data. Delete raises; a mutation by anyone else raises `AdminRequired`."""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core.core_schema import ValidationInfo
from sqlalchemy.exc import IntegrityError

from ufo.contracts import check_declared_schema
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
    DEFAULT_AGENT_VISIBILITY,
    DEFAULT_SANDBOX_SIZE,
    AgentIcon,
    AgentVisibility,
    ReasoningEffort,
    SandboxSize,
    auto_agent_icon,
)
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

AGENT_KIND = "agent"
AGENT_UNDELETABLE = "agents cannot be deleted through objects"
AGENT_EDIT_GATE = "editing an agent requires its owner or a workspace admin"
AGENT_CREATE_GATE = "creating an agent requires a speaking member"
AGENT_PROMPT_REQUIRED = "creating an agent requires a prompt"
MAIN_AGENT_STAYS_WORKSPACE = "the main agent answers every member; its visibility cannot change"


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
    visibility: AgentVisibility = Field(
        default=DEFAULT_AGENT_VISIBILITY,
        description=(
            "Who reaches this agent in the member portal and may open its homepage: 'workspace' "
            "(every member) or 'private' (its owner, workspace admins, and members granted web "
            "access in chat — grants open chat, not the homepage). The main agent is always "
            "'workspace'."
        ),
    )
    icon: AgentIcon | None = Field(
        default=None,
        description=(
            "The agent's icon in the member portal, one slug from a closed set. Omit it on an "
            "update to keep the current icon; a new agent without one takes an icon from its "
            "name."
        ),
    )
    prompt: str | None = Field(
        default=None,
        description=(
            "The complete system prompt. Omit it on an update to keep the current prompt. A "
            "change takes effect on the next turn."
        ),
    )
    input_schema: dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "Raw JSON Schema (top-level type 'object') for the payload a spawn of this agent "
            "takes. Unset means the default {task: string} contract."
        ),
    )
    output_schema: dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "Raw JSON Schema (top-level type 'object') for the final answer a spawn of this "
            "agent returns. Unset means the default {result: string} contract."
        ),
    )

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _declared_schema(
        cls, value: dict[str, JsonValue] | None, info: ValidationInfo
    ) -> dict[str, JsonValue] | None:
        if value is not None:
            check_declared_schema(value, info.field_name or "schema")
        return value


def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None:
    """Refuse a model id this deploy's registry cannot answer, where it is written.

    `ModelRegistry.spec` raises on an unknown id, and a turn reads the stored model at setup before
    it dispatches anything — so an id that reaches the row fails every later turn of that agent, on
    every surface, and the repair turn fails the same way. The write is the only place a member can
    still be told."""
    resolved = ctx.auto_model if model == AUTO_MODEL else model
    if ctx.models and model not in ctx.models:
        raise ValueError(f"no model named {model!r}")
    model_spec = ctx.model_specs.get(resolved)
    if (
        model_spec is not None
        and reasoning == "off"
        and model_spec.reasoning.default_on
        and not model_spec.reasoning.can_disable
    ):
        raise ValueError(f"model {resolved!r} requires reasoning when reasoning is 'off'")


@dataclass(frozen=True)
class AgentObjects:
    """Owner-gated handlers over the complete `agent` row: the owner or an admin writes, and an
    ownerless row (main, provisioned) answers to admins alone."""

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
            visibility=row.visibility,
            icon=row.icon,
            prompt=row.prompt,
            input_schema=row.input_schema,
            output_schema=row.output_schema,
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
            "owner_member_id": None if row.owner_member_id is None else str(row.owner_member_id),
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
        owned = row.owner_member_id is not None and ctx.speaker_member_id == row.owner_member_id
        if not owned and not await ctx.speaker_is_admin():
            raise AdminRequired(AGENT_EDIT_GATE)
        _known_model(ctx, spec.model, spec.reasoning)
        next_prompt = row.prompt if spec.prompt is None else spec.prompt
        prompt_changed = next_prompt != row.prompt
        next_icon = row.icon if spec.icon is None else spec.icon
        next_sandbox_size = (
            spec.sandbox_size if "sandbox_size" in spec.model_fields_set else row.sandbox_size
        )
        next_visibility = (
            spec.visibility if "visibility" in spec.model_fields_set else row.visibility
        )
        if row.is_main:
            if "visibility" in spec.model_fields_set and spec.visibility != "workspace":
                raise ValueError(MAIN_AGENT_STAYS_WORKSPACE)
            next_visibility = "workspace"
        next_input_schema = (
            spec.input_schema if "input_schema" in spec.model_fields_set else row.input_schema
        )
        next_output_schema = (
            spec.output_schema if "output_schema" in spec.model_fields_set else row.output_schema
        )
        settings_changed = (
            spec.model,
            spec.internet_access_allowed,
            spec.reasoning,
            next_sandbox_size,
            next_visibility,
            next_icon,
            next_input_schema,
            next_output_schema,
        ) != (
            row.model,
            row.internet_access_allowed,
            row.reasoning,
            row.sandbox_size,
            row.visibility,
            row.icon,
            row.input_schema,
            row.output_schema,
        )
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
                    visibility=next_visibility,
                    icon=next_icon,
                    input_schema=next_input_schema,
                    output_schema=next_output_schema,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.id == row.id,
                )
            )

    async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None:
        """Insert the agent row — never main, never a copy of anything but the submitted
        configuration, owned by the member who asked for it. The (workspace, name) unique
        constraint arbitrates a concurrent create of the same name; the loser reads back as a name
        refusal, not a second row."""
        if ctx.speaker_member_id is None:
            raise ValueError(AGENT_CREATE_GATE)
        if spec.prompt is None or not spec.prompt.strip():
            raise ValueError(AGENT_PROMPT_REQUIRED)
        _known_model(ctx, spec.model, spec.reasoning)
        async with workspace_tx() as connection:
            taken = (
                (
                    await connection.execute(
                        sa.select(tables.agent.c.icon).where(
                            tables.agent.c.workspace_id == ws_current().workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
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
                        visibility=spec.visibility,
                        icon=spec.icon if spec.icon is not None else auto_agent_icon(name, taken),
                        input_schema=spec.input_schema,
                        output_schema=spec.output_schema,
                        owner_member_id=ctx.speaker_member_id,
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
                        tables.agent.c.visibility,
                        tables.agent.c.icon,
                        tables.agent.c.tools,
                        tables.agent.c.input_schema,
                        tables.agent.c.output_schema,
                        tables.agent.c.owner_member_id,
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
        "A workspace agent: its prompt, model, reasoning effort, public-internet policy, portal "
        "visibility, icon, and the I/O contract a spawn of it validates against — readable by all "
        "members, creatable by any member, updatable by its owner or a workspace admin. It "
        "cannot be deleted through objects."
    ),
    guidance=(
        "A workspace agent as an object. Any member may create one and owns what they created; "
        "its owner or a workspace admin may apply changes, taking effect on the next turn. The "
        "main agent and provisioned agents have no owner, so only an admin edits them. Omit "
        "prompt on an update to keep it unchanged. Blocking public internet leaves "
        "exact model, credential, connector, and transfer hosts available. Reasoning 'auto' lets "
        "an Anthropic model set its own thinking depth per request and falls to the provider "
        "default elsewhere; a fixed level pins it. Sandbox size ('small', 'medium', 'large') "
        "picks the cpu/memory tier a new conversation's sandbox is provisioned at, where the "
        "deploy's sandbox backend offers sizes; existing conversations keep the sandbox they "
        "have. Visibility 'workspace' answers every member in the portal, 'private' answers its "
        "owner, workspace admins, and members granted web access in chat; the agent's homepage "
        "follows it, and the main agent stays 'workspace'. The icon the portal shows is one slug "
        "from a closed set; a new agent takes one from its name, and omitting it on an update "
        "keeps the current icon. "
        "input_schema and output_schema (raw JSON Schema, top-level type 'object') fix the "
        "contract a spawn of this agent validates against; unset means {task} in and {result} "
        "out. Applying a name no agent holds "
        "creates one — the spec then requires `prompt`; a new "
        "agent starts empty, inheriting no grants, credentials, sources, or memory. A child's "
        "`scoped_to` link names the main agent it runs under. Delete is refused. Confirm before "
        "changing settings."
    ),
    spec_model=AgentSpec,
    store=AgentObjects(),
)
