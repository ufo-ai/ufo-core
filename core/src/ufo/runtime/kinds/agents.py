"""The core-registered `agent` object kind: the workspace's agent as a workspace object.

The spec holds the prompt, model, reasoning effort, sandbox size, public-internet policy,
workspace-skill use, portal visibility, icon, and the optional I/O contract a spawn of the agent
validates against.
`object_apply` is their one member write path. Any speaking member creates agents and owns the
ones they created; an owner or a workspace admin edits, and an ownerless row — the main agent or a
provisioned agent — answers to admins alone. Create never copies grants, credentials, sources, or
derived data. A mutation by anyone else raises `AdminRequired`.

Delete archives: the row releases the name it held and stays gettable under its durable
`~archived-<id>` name, keeping the app's conversations, spend and grants as the record of what it
did. The archived row's `restore_application` action brings the same row back. The main agent
answers every member, so it is not archivable."""

from dataclasses import dataclass, replace
from typing import ClassVar
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core.core_schema import ValidationInfo
from sqlalchemy.exc import IntegrityError

from ufo.db import workspace_tx
from ufo.harness.models.interface import AUTO_MODEL
from ufo.runtime.ext.context import JsonValue
from ufo.runtime.object_name import ObjectRef, validate_object_name
from ufo.runtime.objects import (
    MemberOwnedObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectOwner,
    ObjectPage,
    OwnedRow,
    UnknownObject,
    VerbNotSupported,
)
from ufo.runtime.tools.context import SpeakerRequired, TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ActionPresentation, ObjectBinding, ToolDef
from ufo.runtime.turns.contracts import check_declared_schema
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import (
    AGENT_ICONS,
    DEFAULT_AGENT_VISIBILITY,
    DEFAULT_SANDBOX_SIZE,
    AgentVisibility,
    ReasoningEffort,
    SandboxSize,
    TablerIcon,
    auto_agent_icon,
)

AGENT_KIND = "agent"
RESTORE_APPLICATION_TOOL = "restore_application"
MAIN_AGENT_UNARCHIVABLE = "the main agent answers every member; it cannot be archived"
AGENT_ARCHIVE_GATE = "archiving an agent requires its owner or a workspace admin"
AGENT_RESTORE_GATE = "restoring an agent requires its owner or a workspace admin"
AGENT_EDIT_GATE = "editing an agent requires its owner or a workspace admin"
AGENT_CREATE_GATE = "creating an agent requires a speaking member"
AGENT_PROMPT_REQUIRED = "creating an agent requires a prompt"
MAIN_AGENT_STAYS_WORKSPACE = "the main agent answers every member; its visibility cannot change"
ARCHIVED_AGENT_NAME_PREFIX = "~archived-"
AGENT_ALREADY_ARCHIVED = "the app is already archived"


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
    use_workspace_skills: bool = Field(
        default=True,
        title="Use workspace skills",
        description=(
            "Whether this agent's turns load the workspace skill set — the member-authored "
            "skills the portal's workspace page manages. Off, no member-authored skill reaches "
            "this agent's turns; the deploy's own skills load either way."
        ),
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
    icon: TablerIcon | None = Field(
        default=None,
        description=(
            "The agent's icon in the member portal, one of the marks the portal draws: "
            f"{', '.join(AGENT_ICONS)}. Any other name applies but the portal does not offer it. "
            "Omit it on an update to keep the current icon; a new agent without one takes an icon "
            "from its name."
        ),
    )
    prompt: str | None = Field(
        default=None,
        description=(
            "The complete system prompt. Omit it on an update to keep the current prompt. A "
            "change takes effect on the next turn."
        ),
    )
    purpose: str | None = Field(
        default=None,
        description=(
            "One sentence saying what this agent is for, in the member's own terms — what it "
            "does for them, not how it works. The portal reads it wherever a member meets the "
            "agent before opening it. Omit it on an update to keep the current one."
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


def _agent_summary(ctx: ToolContext, row: sa.Row) -> str:
    model = _effective_model(ctx, row.model)
    if row.archived_at is not None:
        return f"archived {row.archived_at:%d %b %Y}, was on {model}"
    internet = "allowed" if row.internet_access_allowed else "blocked"
    return f"{'main agent' if row.is_main else 'agent'}, on {model}, public internet {internet}"


@dataclass(frozen=True)
class AgentObjects(MemberOwnedObjects[AgentSpec, ObjectOwner]):
    """The complete agent row behind the shared member-ownership gate. An agent's own row reads as
    shared on its own turns, speaker or not: the row is the turn's identity, so an action bound to
    it (a homepage bind) runs on the scheduled and seed turns that have no member speaking."""

    kind_name: ClassVar[str] = AGENT_KIND
    mutate_gate: ClassVar[str] = AGENT_EDIT_GATE
    delete_gate: ClassVar[str] = AGENT_ARCHIVE_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    def _admin_can_apply(self, old: AgentSpec, spec: AgentSpec) -> bool:
        return True

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        """The workspace's live agents, and its archived ones for a caller that asks for them by
        filter. An archived row is named by the durable `~archived-<id>` name its get answers to,
        with the name it held in `archived_name` and the stable `id` a restore addresses."""
        if "archived" not in query.filters:
            query = replace(query, filters={**query.filters, "archived": False})
        return await super().list(ctx, query)

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]:
        selection = sa.select(
            tables.agent.c.id,
            tables.agent.c.name,
            tables.agent.c.archived_name,
            tables.agent.c.model,
            tables.agent.c.is_main,
            tables.agent.c.internet_access_allowed,
            tables.agent.c.archived_at,
            tables.agent.c.owner_member_id,
            tables.agent.c.visibility,
        ).where(tables.agent.c.workspace_id == ws_current().workspace_id)
        async with workspace_tx() as connection:
            rows = (await connection.execute(selection.order_by(tables.agent.c.name))).all()
        return tuple(
            OwnedRow(
                name=row.name,
                summary=_agent_summary(ctx, row),
                owner=ObjectOwner(
                    member_id=row.owner_member_id,
                    shared=row.visibility == "workspace" or row.id == ctx.turn.agent_id,
                ),
                fields={
                    "id": str(row.id),
                    "archived": row.archived_at is not None,
                    "archived_at": (
                        None if row.archived_at is None else row.archived_at.isoformat()
                    ),
                    "archived_name": row.archived_name,
                },
            )
            for row in rows
        )

    async def _detail(
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> ObjectDetail[AgentSpec] | None:
        row = await self._row(name)
        if row is None:
            return None
        spec = AgentSpec(
            model=row.model,
            internet_access_allowed=row.internet_access_allowed,
            use_workspace_skills=row.use_workspace_skills,
            reasoning=row.reasoning,
            sandbox_size=row.sandbox_size,
            visibility=row.visibility,
            icon=row.icon,
            prompt=row.prompt,
            purpose=row.purpose,
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

    async def _status(
        self,
        ctx: ToolContext,
        name: str,
        owner: ObjectOwner,
    ) -> dict[str, JsonValue] | None:
        row = await self._row(name)
        if row is None:
            return None
        return {
            "main": row.is_main,
            "model": _effective_model(ctx, row.model),
            "archived": row.archived_at is not None,
            "archived_at": None if row.archived_at is None else row.archived_at.isoformat(),
            "archived_name": row.archived_name,
            "owner_member_id": None if row.owner_member_id is None else str(row.owner_member_id),
            "provisioned_by": row.provisioned_by,
            "provisioned_name": row.provisioned_name,
            "provisioned_version": row.provisioned_version,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: AgentSpec,
        old: AgentSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        if old is None:
            await self._create(ctx, name, spec)
            return
        await self._mutate(ctx, name, spec)

    async def _mutate(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None:
        row = await self._row(name)
        if row is None:
            raise UnknownObject(f"no agent object named {name!r}")
        _known_model(ctx, spec.model, spec.reasoning)
        next_prompt = row.prompt if spec.prompt is None else spec.prompt
        prompt_changed = next_prompt != row.prompt
        next_purpose = row.purpose if spec.purpose is None else spec.purpose
        next_icon = row.icon if spec.icon is None else spec.icon
        next_sandbox_size = (
            spec.sandbox_size if "sandbox_size" in spec.model_fields_set else row.sandbox_size
        )
        next_workspace_skills = (
            spec.use_workspace_skills
            if "use_workspace_skills" in spec.model_fields_set
            else row.use_workspace_skills
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
            next_workspace_skills,
            spec.reasoning,
            next_sandbox_size,
            next_visibility,
            next_icon,
            next_purpose,
            next_input_schema,
            next_output_schema,
        ) != (
            row.model,
            row.internet_access_allowed,
            row.use_workspace_skills,
            row.reasoning,
            row.sandbox_size,
            row.visibility,
            row.icon,
            row.purpose,
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
                    use_workspace_skills=next_workspace_skills,
                    reasoning=spec.reasoning,
                    sandbox_size=next_sandbox_size,
                    visibility=next_visibility,
                    icon=next_icon,
                    purpose=next_purpose,
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
            raise SpeakerRequired(AGENT_CREATE_GATE)
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
                        purpose=spec.purpose,
                        model=spec.model,
                        is_main=False,
                        internet_access_allowed=spec.internet_access_allowed,
                        use_workspace_skills=spec.use_workspace_skills,
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
        row = await self._row(name)
        if row is not None and row.is_main:
            raise VerbNotSupported(MAIN_AGENT_UNARCHIVABLE)
        await super().delete(ctx, name, expected_generation=expected_generation)

    async def _delete_owned(
        self,
        ctx: ToolContext,
        name: str,
        owner: ObjectOwner,
    ) -> None:
        """Archive the app: it admits no further turn, leaves the portal, and releases its name.
        What it did stays — its conversations, spend and grants are the record, and a restore
        reaches all of it. A turn already running finishes; nothing new founds one."""
        row = await self._row(name)
        if row is None:
            raise UnknownObject(f"no agent object named {name!r}")
        if row.is_main:
            raise VerbNotSupported(MAIN_AGENT_UNARCHIVABLE)
        if row.archived_at is not None:
            raise VerbNotSupported(AGENT_ALREADY_ARCHIVED)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(
                    name=f"{ARCHIVED_AGENT_NAME_PREFIX}{row.id}",
                    archived_name=row.name,
                    archived_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.agent.c.workspace_id == ws_current().workspace_id,
                    tables.agent.c.id == row.id,
                    tables.agent.c.archived_at.is_(None),
                )
            )

    async def _row(self, name: str) -> sa.Row | None:
        main = tables.agent.alias("main_agent")
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        tables.agent.c.prompt,
                        tables.agent.c.purpose,
                        tables.agent.c.id,
                        tables.agent.c.name,
                        tables.agent.c.model,
                        tables.agent.c.is_main,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.use_workspace_skills,
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
                        tables.agent.c.archived_at,
                        tables.agent.c.archived_name,
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


class RestoreApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_name: str = Field(
        description=(
            "The name the app takes as it comes back. Pass the name it held, or another name "
            "when a live app holds that one."
        )
    )


@dataclass(frozen=True)
class RestoreApplication:
    async def restore(self, ctx: ToolContext, args: RestoreApplicationInput) -> ToolResult:
        if ctx.target is None or ctx.target.name is None:
            raise RuntimeError("restore_application dispatched without its archived agent target")
        archived_name = ctx.target.name
        if ctx.speaker_member_id is None:
            raise SpeakerRequired(AGENT_RESTORE_GATE)
        validate_object_name(args.new_name)
        try:
            archived_id = UUID(archived_name.removeprefix(ARCHIVED_AGENT_NAME_PREFIX))
        except ValueError as error:
            raise UnknownObject(f"no archived app named {archived_name!r}") from error
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.owner_member_id,
                        tables.agent.c.name,
                        tables.agent.c.archived_at,
                    ).where(
                        tables.agent.c.workspace_id == ws_current().workspace_id,
                        tables.agent.c.id == archived_id,
                    )
                )
            ).one_or_none()
        if row is None or (row.archived_at is None and row.name != args.new_name):
            raise UnknownObject(f"no archived app named {archived_name!r}")
        owned = row.owner_member_id is not None and ctx.speaker_member_id == row.owner_member_id
        if not owned and not await ctx.speaker_is_admin():
            raise UnknownObject(f"no archived app named {archived_name!r}")
        result = ToolResult(
            content=(TextContent(text=f"{args.new_name} is live again. Its next turn runs it."),)
        )
        if row.archived_at is None:
            return result
        async with workspace_tx() as connection:
            try:
                restored = (
                    await connection.execute(
                        sa.update(tables.agent)
                        .values(
                            name=args.new_name,
                            archived_name=None,
                            archived_at=None,
                            updated_at=sa.func.now(),
                        )
                        .where(
                            tables.agent.c.workspace_id == ws_current().workspace_id,
                            tables.agent.c.id == archived_id,
                            tables.agent.c.archived_at.is_not(None),
                        )
                        .returning(tables.agent.c.id)
                    )
                ).scalar_one_or_none()
            except IntegrityError as error:
                raise ValueError(f"an agent named {args.new_name!r} already exists") from error
        if restored is None:
            raise UnknownObject(f"no archived app named {archived_name!r}")
        return result


RESTORE_APPLICATION_TOOL_DEF = ToolDef(
    name=RESTORE_APPLICATION_TOOL,
    description=(
        "Bring this archived app back, under the name it held or another one. Its conversations, "
        "scheduled tasks, connected accounts and grants come back with it. Only the app's owner "
        "or a workspace admin may restore it."
    ),
    input_model=RestoreApplicationInput,
    handler=RestoreApplication().restore,
    side_effecting=True,
    parallel_safe=True,
    bound=ObjectBinding(kind=AGENT_KIND, binding="instance"),
    presentation=ActionPresentation(label="Restore"),
)


AGENT_OBJECT = ObjectKind(
    name=AGENT_KIND,
    description=(
        "A workspace agent: its prompt, model, settings, and the I/O contract a spawn of it "
        "validates against. Any member may create one; its owner or an admin may change it."
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
        "have. use_workspace_skills loads the workspace's saved skills on this agent's turns; "
        "off, only the deploy's own skills load. Visibility 'workspace' answers every member in "
        "the portal, 'private' answers its owner, workspace admins, and members granted web "
        "access in chat; the agent's homepage follows it, and the main agent stays 'workspace'. "
        "The icon the portal shows is one of the "
        "marks it draws, the set the icon field names, so name the one that draws the job; a new "
        "agent takes one from its name, and omitting it on an update keeps the current icon. "
        "input_schema and output_schema (raw JSON Schema, top-level type 'object') fix the "
        "contract a spawn of this agent validates against; unset means {task} in and {result} "
        "out. Applying a name no agent holds "
        "creates one — the spec then requires `prompt`; a new "
        "agent starts empty, inheriting no grants, credentials, sources, or memory. A child's "
        "`scoped_to` link names the main agent it runs under. Delete archives the app: it admits "
        "no further turn, leaves the member portal, and releases its name, while "
        "its "
        "conversations, scheduled tasks, connected accounts and grants stay on the row. A turn "
        "already running finishes. The main agent is not archivable. List the archived apps with "
        'the filter {"archived": true}; get one by its durable name, and its '
        f"{RESTORE_APPLICATION_TOOL} action brings it back under an available name. "
        "object_get with an empty ref reads this turn's own agent, with the actions bound to it. "
        "Confirm before changing settings, and before archiving."
    ),
    spec_model=AgentSpec,
    store=AgentObjects(),
    list_fields=frozenset({"id", "archived", "archived_at", "archived_name"}),
)
