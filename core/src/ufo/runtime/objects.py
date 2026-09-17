"""Workspace objects: registered kinds and the five CRUD verbs (RFC 0017).

An object is a workspace-scoped durable row addressed `kind` + `name`, authored as one YAML
document (`kind`/`name`/`spec`) validated against the kind's declared spec model. An extension
registers kinds through the `objects` Manifest point; each kind's `store` handlers run over the
extension's own tables, so the handler IS the create/update/delete trigger — core validates the
envelope, the name grammar, and the spec, then calls it. Refusal lives in the handler, not a
declaration: a kind that doesn't do a mutation raises `VerbNotSupported` with the domain reason,
and a kind that gates on role checks `ctx.speaker_is_admin()` itself, raising `AdminRequired`.

`object_registry` is the boot gate: kind-name collisions and spec models that admit unknown keys,
non-JSON types, or secret-bearing fields fail loud before serving — specs are stored, rendered
into transcripts, and echoed by `object_get`, so secrets are excluded by construction.
`ObjectVerbs` is the dispatch workflow: five ToolDefs over one deploy's registry, threading each
call to the owning kind's ExtensionContext."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import ClassVar, Literal, Protocol, get_args, runtime_checkable
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

import sqlalchemy as sa
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.errors import PydanticInvalidForJsonSchema

from ufo.db import workspace_tx
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.object_name import (
    KIND_NAME_PATTERN,
    OBJECT_NAME_MAX_LENGTH,
    OBJECT_NAME_PATTERN,
    ObjectRef,
    validate_object_name,
)
from ufo.runtime.object_scope import (
    ObjectActionRequestTarget,
    ObjectActionTarget,
    ObjectAgent,
    object_agent,
)
from ufo.runtime.object_views import action_view
from ufo.runtime.seats import member_is_admin
from ufo.runtime.tools.context import SpeakerRequired, TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import (
    OBJECT_ACTION_TOOL,
    REQUESTED_BY,
    ToolDef,
    validate_tool_declaration,
)
from ufo.runtime.turns.audience import SHARED_AUDIENCE, Audience, readable_audiences
from ufo.schema import tables

OBJECT_MANIFEST_MAX_BYTES = 65_536
MATERIALIZE_MAX_BYTES = 33_554_432
OBJECT_LIST_PAGE = 50
ORDER_BY_DEFAULT = "name"
ORDER_DEFAULT: Literal["asc", "desc"] = "asc"
ENVELOPE_KEYS = frozenset({"kind", "name", "spec"})
ENVELOPE_GENERATION_KEY = "generation"
ACTION_RESERVED_INPUT_FIELDS = (
    REQUESTED_BY,
    "kind",
    "action",
    "name",
    "agent",
    "generation",
    "input",
)
AGENT_TARGET_DESCRIPTION = (
    "Stable agent name for an agent-scoped kind. Omit for this agent. Only the workspace main "
    "agent may target another agent, on an exact member-requested call."
)

type Relation = Literal[
    "created_from",
    "synced_by",
    "created_in",
    "reports_to",
    "watches",
    "superseded_by",
    "access_to",
    "scoped_to",
]
type AgentTargetVerb = Literal["list", "get", "create", "update", "delete"]

type _SortRank = Literal[0, 1, 2, 3, 4]

AGENT_TARGET_VERBS = frozenset({"list", "get", "create", "update", "delete"})


class ObjectLink(BaseModel):
    """One typed outgoing link on an object: a relation from the closed vocabulary and the target's
    ref. Stored on the owning row and rendered forward-only — the reverse direction is a structured
    query over the forward column, never a stored edge. A link never grants visibility: the target
    stays gated by its own kind's read. `scoped_to` names the row's one owning agent, and a parent
    edge takes it only where no narrower relation already names that scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: Relation
    target: ObjectRef


@dataclass(frozen=True)
class ObjectDetail[SpecT: BaseModel]:
    """One object as its store reads it: the applied spec, the owning row's timestamps (None for a
    kind whose instances are declarations, not rows), its typed outgoing links, and whether the
    caller may receive the spec. A non-null opaque generation opts the verb into replacement
    fencing."""

    spec: SpecT
    created_at: datetime | None
    updated_at: datetime | None
    links: tuple[ObjectLink, ...] = ()
    spec_visible: bool = True
    generation: UUID | None = None


class UnknownKind(ValueError):
    """The named kind is not registered; the message lists the kinds that are."""


class UnknownObject(ValueError):
    """A get or delete addressed a name that does not exist in its kind."""


class InvalidManifest(ValueError):
    """The apply payload is not one YAML document of exactly `kind`, `name`, and `spec`, plus an
    optional `generation` echoing what an object_get returned."""


class SpecValidationFailed(ValueError):
    """The spec failed the kind's declared model; the message names each field and reason."""


class VerbNotSupported(ValueError):
    """Handler-raised: the kind does not do this mutation; the message names the path that does."""


class AdminRequired(ValueError):
    """Handler-raised: the mutation requires a speaking workspace admin."""


@dataclass(frozen=True)
class ObjectRow:
    """One instance in a listing: its name, one-line summary, and lightweight fields a caller may
    filter or order on — never a full spec."""

    name: str
    summary: str
    fields: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class ObjectListQuery:
    """The one listing contract every kind implements. `filters` are exact field matches;
    `order_by` names `name`, `summary`, or a row field; `cursor` is a returned continuation
    token."""

    query: str = ""
    filters: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)
    order_by: str = "name"
    order: Literal["asc", "desc"] = "asc"
    cursor: str = ""
    supported_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ObjectPage:
    """One page of a kind's instances. `next_cursor` is an opaque continuation token for the
    caller; None means the listing is complete.

    `cut` is for a kind whose rows are bounded before they are paged: the continuation runs out
    while the workspace still holds more, so a screen that draws the whole page has still not
    drawn everything. A listing that cannot page says so rather than truncating in silence."""

    rows: tuple[ObjectRow, ...]
    next_cursor: str | None = None
    cut: bool = False


class _ObjectCursor(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    order_by: str
    order: Literal["asc", "desc"]
    rank: _SortRank
    value: str | int | float
    name: str

    @model_validator(mode="after")
    def validate_rank(self) -> "_ObjectCursor":
        match self.rank, self.value:
            case (0, "") | (1, int()) | (2, int() | float()) | (3, str()) | (4, str()):
                return self
            case _:
                raise ValueError("cursor value does not match its sort rank")


def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage:
    """Apply the shared search/filter/order/page semantics to one kind's lightweight rows.

    Free-text search scans every string field except `url` and `*_url`: a link is an address, not
    text, and a signed one carries its expiry and signature, so a numeric search would otherwise
    match every row that mints a link."""
    reserved = {"name", "summary"}
    collisions = reserved.intersection(field for row in rows for field in row.fields)
    if collisions:
        raise ValueError(f"object list fields collide with reserved fields: {sorted(collisions)}")
    undeclared = {
        field for row in rows for field in row.fields if field not in query.supported_fields
    }
    if undeclared:
        raise ValueError(f"object list rows carry undeclared fields: {sorted(undeclared)}")
    available = reserved.union(query.supported_fields)
    requested = set(query.filters)
    if not requested.issubset(available):
        raise ValueError(
            f"unknown object list filters {sorted(requested - available)}; "
            f"available fields: {sorted(available)}"
        )
    if query.order_by not in available:
        raise ValueError(
            f"unknown object list order field {query.order_by!r}; "
            f"available fields: {sorted(available)}"
        )

    def value(row: ObjectRow, name: str) -> JsonValue:
        if name == "name":
            return row.name
        if name == "summary":
            return row.summary
        return row.fields.get(name)

    matched = [
        row
        for row in rows
        if (
            query.query.casefold() in row.name.casefold()
            or query.query.casefold() in row.summary.casefold()
            or any(
                query.query.casefold() in value.casefold()
                for name, value in row.fields.items()
                if isinstance(value, str) and name != "url" and not name.endswith("_url")
            )
        )
        and all(value(row, name) == expected for name, expected in query.filters.items())
    ]

    ordered = sorted(
        matched,
        key=lambda row: (_sortable(value(row, query.order_by), query.order_by), row.name),
        reverse=query.order == "desc",
    )
    if query.cursor:
        try:
            cursor = _ObjectCursor.model_validate_json(bytes.fromhex(query.cursor))
        except (ValueError, ValidationError) as error:
            raise ValueError("invalid object list cursor") from error
        if (cursor.order_by, cursor.order) != (query.order_by, query.order):
            raise ValueError("object list cursor does not match the requested order")
        boundary = ((cursor.rank, cursor.value), cursor.name)
        ordered = [
            row
            for row in ordered
            if (
                (_sortable(value(row, query.order_by), query.order_by), row.name) > boundary
                if query.order == "asc"
                else (_sortable(value(row, query.order_by), query.order_by), row.name) < boundary
            )
        ]
    page, rest = ordered[:OBJECT_LIST_PAGE], ordered[OBJECT_LIST_PAGE:]
    if not rest:
        return ObjectPage(rows=tuple(page))
    rank, boundary_value = _sortable(value(page[-1], query.order_by), query.order_by)
    cursor = _ObjectCursor(
        order_by=query.order_by,
        order=query.order,
        rank=rank,
        value=boundary_value,
        name=page[-1].name,
    )
    return ObjectPage(rows=tuple(page), next_cursor=cursor.model_dump_json().encode().hex())


def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]:
    match value:
        case None:
            return (0, "")
        case bool():
            return (1, int(value))
        case int() | float():
            return (2, value)
        case str():
            return (3, value)
        # A run of names — a conversation's speakers — orders by the run itself, so a field a row
        # legitimately carries as a list is an order the page answers rather than a 400 it raises.
        case list() if all(isinstance(entry, str) for entry in value):
            return (4, "\n".join(entry for entry in value if isinstance(entry, str)))
        case _:
            raise ValueError(
                f"object list field {field_name!r} contains a non-scalar value and cannot order"
            )


class ObjectStore[SpecT: BaseModel](Protocol):
    """A kind's handlers over its own storage, typed by the kind's own spec model. `get` reads
    everything the owning row carries — spec, timestamps, links; `apply` receives the validated
    spec and the currently applied one (None on create); `status` is kind-specific live state
    rendered beside the spec on get — read by no code, so a loose mapping is the honest type.
    Handlers raise `VerbNotSupported` / `AdminRequired` / domain `ValueError`s; each renders as
    the tool error. Each active verb carries the generation its own read observed: a store
    returning a non-null `ObjectDetail.generation` is fenced, and status/apply/delete refuse once
    the row's generation no longer matches — before disclosure or mutation and after a disclosure
    read. A kind returning no generation stays last-write-wins, so a row created or removed between
    the read and the write is the verb's ordinary absent-or-present case, never a lost race."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage: ...

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None: ...

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None: ...

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: SpecT,
        old: SpecT | None,
        *,
        expected_generation: UUID | None,
    ) -> None: ...

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None: ...


@dataclass(frozen=True)
class ObjectOwner:
    """Who a member-owned row belongs to and who reads it: the audience of the conversation it
    belongs to, read live. `member_id` None means no member owns it; `audience` None means no
    audience reaches it, so its owner and a workspace admin read it alone."""

    member_id: UUID | None
    audience: Audience | None


@dataclass(frozen=True)
class GeneratedObjectOwner(ObjectOwner):
    """An owner whose row may be replaced under the same object name."""

    generation: UUID


def readable(
    owner: ObjectOwner, member_id: UUID | None, *, admin: bool, disclosed: bool = False
) -> bool:
    """Whether one reader sees a row: its audience, its owner, or a workspace admin. Content asks
    the same question with `admin=False` and `disclosed` — whether the reader holds a recorded
    `read_private_transcript` acknowledgement on the row's conversation, an admin's one way into
    private content. A reader nobody can name reads only what the workspace shares."""
    if admin or disclosed:
        return True
    if member_id is None:
        return owner.audience == SHARED_AUDIENCE
    return owner.member_id == member_id or owner.audience in readable_audiences(member_id)


async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]:
    """The workspace addresses behind a set of owner member ids, resolved in one query — the map a
    kind's listing reads to put `owner_email` on every row it emits. A row with no owner reads the
    map with the same key it holds, and the map answers nothing."""
    wanted = {member_id for member_id in owners if member_id is not None}
    if not wanted:
        return {}
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.member.c.id, tables.member.c.email).where(
                    tables.member.c.id.in_(wanted)
                )
            )
        ).all()
    return {row.id: row.email for row in rows}


@dataclass(frozen=True)
class OwnedRow[OwnerT: ObjectOwner]:
    """One row a member-owned kind hands the gate: its name, one-line summary, owner, and the
    lightweight fields its kind declared for filtering and ordering."""

    name: str
    summary: str
    owner: OwnerT
    fields: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class MemberOwnedObjects[SpecT: BaseModel, OwnerT: ObjectOwner]:
    """Base for a member-owned object kind: the per-member visibility and ownership gate lives here
    once, so a kind cannot ship without it. A subclass supplies only data (`_owned_rows`, `_detail`,
    `_status`) and domain mutation (`_apply_owned`, `_delete_owned`); the gate hides a row invisible
    to the acting member (absent from `list`, not-found from `get`/`status`, `UnknownObject` from
    `apply`/`delete`) and refuses `AdminRequired` when a visible row is not the actor's to change.
    A row is visible to its conversation's audience, its owner, or the speaker when they are a
    workspace admin. A row with no owner and no audience is admin-only. A kind whose mutation or
    deletion is a grant/disclosure act sets `mutate_requires_speaker`/`delete_requires_speaker` so
    the gate also refuses it on a speakerless (scheduled/subagent) turn — an act that discloses or
    revokes access needs a live member, never a background turn acting on someone's behalf. That
    refusal (`SpeakerRequired`) is answered before ownership is: with nobody bound there is no one
    to be the owner, and the engine turns the typed refusal into the retry the model can make where
    a `requested_by` ref would bind someone. The class vars name the kind and the two gate messages
    the refusals carry.

    A kind handing up `GeneratedObjectOwner` is fenced on that generation: every active verb refuses
    once the name holds a different row than its read saw, and `status` re-checks after the read so
    live state read under one row is never rendered beside another's spec. A kind handing up a plain
    `ObjectOwner` is unfenced and stays last-write-wins — a row created or removed between the read
    and the write is that verb's ordinary absent-or-present case. Both re-check visibility after a
    disclosure read: a row the caller may no longer see refuses rather than disclose what was
    read."""

    kind_name: ClassVar[str]
    mutate_gate: ClassVar[str]
    delete_gate: ClassVar[str]
    mutate_requires_speaker: ClassVar[bool] = False
    delete_requires_speaker: ClassVar[bool] = False

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        is_admin = await ctx.speaker_is_admin()
        acting = ctx.speaker_member_id
        rows = tuple(
            ObjectRow(name=row.name, summary=row.summary, fields=row.fields)
            for row in await self._owned_rows(ctx)
            if self._visible(row.owner, acting, is_admin) and self._listed(row, query)
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None:
        owner = await self._owner(ctx, name)
        if owner is None or not self._visible(
            owner, ctx.speaker_member_id, await ctx.speaker_is_admin()
        ):
            return None
        detail = await self._detail(ctx, name, owner)
        match owner, detail:
            case GeneratedObjectOwner(generation=generation), ObjectDetail():
                return replace(detail, generation=generation)
            case _:
                return detail

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        owner = await self._owner(ctx, name)
        self._require_current_generation(name, owner, expected_generation, "reading")
        if owner is None:
            return None
        is_admin = await ctx.speaker_is_admin()
        if not self._visible(owner, ctx.speaker_member_id, is_admin):
            raise UnknownObject(f"no {self.kind_name} object named {name!r}")
        status = await self._status(ctx, name, owner)
        current = await self._owner(ctx, name)
        self._require_current_generation(name, current, expected_generation, "reading")
        if current is None:
            return None
        if not self._visible(current, ctx.speaker_member_id, is_admin):
            raise UnknownObject(f"no {self.kind_name} object named {name!r}")
        return status

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: SpecT,
        old: SpecT | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        owner = await self._owner(ctx, name)
        if owner is None:
            self._require_current_generation(name, owner, expected_generation, "editing")
            await self._apply_owned(ctx, name, spec, old, owner)
            return
        is_admin = await ctx.speaker_is_admin()
        if not self._visible(owner, ctx.speaker_member_id, is_admin):
            raise UnknownObject(f"no {self.kind_name} object named {name!r}")
        self._require_current_generation(name, owner, expected_generation, "editing")
        if self.mutate_requires_speaker:
            ctx.require_speaker(self.mutate_gate)
        if not self._owned(owner, ctx.speaker_member_id) and (
            not is_admin
            or old is None
            or (owner.member_id is not None and not self._admin_can_apply(old, spec))
        ):
            raise AdminRequired(self.mutate_gate)
        await self._apply_owned(ctx, name, spec, old, owner)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        owner = await self._owner(ctx, name)
        self._require_current_generation(name, owner, expected_generation, "deleting")
        if owner is None:
            raise UnknownObject(f"no {self.kind_name} object named {name!r}")
        is_admin = await ctx.speaker_is_admin()
        if not self._visible(owner, ctx.speaker_member_id, is_admin):
            raise UnknownObject(f"no {self.kind_name} object named {name!r}")
        if self.delete_requires_speaker:
            ctx.require_speaker(self.delete_gate)
        if not self._owned(owner, ctx.speaker_member_id) and not is_admin:
            raise AdminRequired(self.delete_gate)
        await self._delete_owned(ctx, name, owner)

    def _owned(self, owner: OwnerT, acting: UUID | None) -> bool:
        """Whether the acting member is the row's member-owner. An admin-only row (`member_id`
        None) is owned by no member — only a workspace admin may touch it — so this
        is never true for it, even on a turn whose acting member is also None."""
        return owner.member_id is not None and owner.member_id == acting

    def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool:
        return readable(owner, acting, admin=is_admin)

    def _listed(self, row: OwnedRow[OwnerT], query: ObjectListQuery) -> bool:
        """Whether a row the gate admits also stands in a listing. A kind holding rows that are
        addressable by name but are not the workspace's to browse says which here; every other kind
        lists everything its gate admits.

        Core holds it because the query is core's: a kind builds its rows in `_owned_rows` and
        `_member_rows`, neither of which is handed what the read asked for, so an extension has
        nowhere of its own to weigh a row against a named filter."""
        return True

    def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool:
        return False

    def _require_current_generation(
        self,
        name: str,
        owner: OwnerT | None,
        expected_generation: UUID | None,
        action: str,
    ) -> None:
        """Refuse a verb whose read observed a different row than the one now under the name. An
        ungenerated owner carries no generation, so it matches the empty expectation an unfenced
        kind's read produces and the verb proceeds — the fence exists only for kinds that generate.
        """
        match owner:
            case GeneratedObjectOwner(generation=generation):
                stale = generation != expected_generation
            case _:
                stale = expected_generation is not None
        if stale:
            raise ValueError(f"{self.kind_name} {name!r} changed while {action}")

    async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None:
        return next((row.owner for row in await self._owned_rows(ctx) if row.name == name), None)

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]:
        raise NotImplementedError

    async def _detail(
        self, ctx: ToolContext, name: str, owner: OwnerT
    ) -> ObjectDetail[SpecT] | None:
        raise NotImplementedError

    async def _status(
        self, ctx: ToolContext, name: str, owner: OwnerT
    ) -> dict[str, JsonValue] | None:
        raise NotImplementedError

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: SpecT,
        old: SpecT | None,
        owner: OwnerT | None,
    ) -> None:
        raise NotImplementedError

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class MemberObject[SpecT: BaseModel]:
    """One object as a signed-in member reads it outside a turn: the listing row its kind's own
    index carries — name, summary, and the scalars the kind declared in `list_fields`, which are
    the live state a detail page renders beside the spec — and the detail its store reads."""

    row: ObjectRow
    detail: ObjectDetail[SpecT]


@runtime_checkable
class MemberReadable(Protocol):
    """A kind one signed-in member reads outside a turn, through the kind's own visibility gate —
    the portal's detail projection. Implementing this IS the opt-in: a kind that cannot answer a
    member without a turn is simply absent from the portal's object routes, which refuse by name
    rather than raising inside the kind. The caller binds the workspace and the agent namespace
    before calling, so an agent-scoped kind reads behind the same wall every portal route
    answers on."""

    async def member_detail(
        self,
        ext: "ExtensionContext | None",
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject | None: ...


@runtime_checkable
class MemberListable(MemberReadable, Protocol):
    """A member-readable kind that also answers a whole page — the portal's index projection,
    searched, filtered, and ordered on the kind's own `list_fields`."""

    async def member_page(
        self,
        ext: "ExtensionContext | None",
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage: ...


@dataclass(frozen=True)
class ConversationObjectGrant:
    name: str
    generation: UUID
    content_visible: bool


@runtime_checkable
class ConversationMemberListable(Protocol):
    async def member_conversation_rows(
        self,
        ext: "ExtensionContext | None",
        conversation_id: UUID,
        *,
        member_id: UUID,
        admin: bool,
        limit: int,
    ) -> tuple[ConversationObjectGrant, ...]: ...


@dataclass(frozen=True)
class MemberReadableObjects[SpecT: BaseModel, OwnerT: ObjectOwner](
    MemberOwnedObjects[SpecT, OwnerT]
):
    """A member-owned kind the portal reads and lists. The visibility gate is `list`'s and `get`'s,
    applied to rows the kind produces from its extension context and the acting member alone
    (`_member_rows`, `_member_object`) — which is where the turn-side `_owned_rows` and `_detail`
    delegate, so the two paths cannot diverge and a kind eliding private content elides it on
    both."""

    async def member_page(
        self,
        ext: "ExtensionContext | None",
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        rows = tuple(
            ObjectRow(name=row.name, summary=row.summary, fields=row.fields)
            for row in await self._member_rows(ext, member_id=member_id)
            if self._visible(row.owner, member_id, admin) and self._listed(row, query)
        )
        return object_page(rows, query)

    async def member_detail(
        self,
        ext: "ExtensionContext | None",
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[SpecT] | None:
        rows = await self._member_rows(ext, member_id=member_id)
        found = next((row for row in rows if row.name == name), None)
        if found is None or not self._visible(found.owner, member_id, admin):
            return None
        detail = await self._member_object(ext, name, found.owner, member_id=member_id)
        if detail is None:
            return None
        return MemberObject(
            row=ObjectRow(name=found.name, summary=found.summary, fields=found.fields),
            detail=detail,
        )

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]:
        return await self._member_rows(ctx.ext, member_id=ctx.speaker_member_id)

    async def _detail(
        self, ctx: ToolContext, name: str, owner: OwnerT
    ) -> ObjectDetail[SpecT] | None:
        return await self._member_object(ctx.ext, name, owner, member_id=ctx.speaker_member_id)

    async def _member_rows(
        self, ext: "ExtensionContext | None", *, member_id: UUID | None
    ) -> tuple[OwnedRow[OwnerT], ...]:
        raise NotImplementedError

    async def _member_object(
        self,
        ext: "ExtensionContext | None",
        name: str,
        owner: OwnerT,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[SpecT] | None:
        raise NotImplementedError


@dataclass(frozen=True)
class ObjectKind[SpecT: BaseModel]:
    """One registered kind: the YAML `kind:` name, a one-line description, the guidance
    `object_explain` returns verbatim — a kind that replaces bespoke tools carries their tuned
    descriptions ~verbatim there, so no instruction is lost with the tool — the model every
    authored spec validates against, and the store whose handlers do the work. The description is
    an index line the system prompt lists for every registered kind, so it follows the same rubric
    a skill's frontmatter description does: one or two sentences within 200 characters and 30
    words, plain words, saying what one row is and who may write it. Everything else is guidance —
    field lists, naming rules, which verbs refuse, pause and expiry semantics, admin exceptions,
    and visibility rules — because guidance is read by the turn that calls `object_explain`, while
    the description is read by every turn. `list_fields` is the kind's own filter and order
    vocabulary — whatever a row carries, spec field or not, so a read-only kind exposes a
    filterable column without widening the spec its apply refuses — and declaring it makes filter
    and order validation independent of whether any rows currently exist. A row carrying a field
    the kind never declared is refused; a declared field its rows never produce reads as null, so
    each kind's own listing proof is what holds declaration and rows in step."""

    name: str
    description: str
    guidance: str
    spec_model: type[SpecT]
    store: ObjectStore[SpecT]
    list_fields: frozenset[str] = frozenset()
    agent_target_verbs: frozenset[AgentTargetVerb] = frozenset()


@dataclass(frozen=True)
class BoundKind:
    """A kind bound to its owner for dispatch: the declaring extension's name (None for a
    core-registered kind) and the workspace-scoped context its store handlers run under."""

    kind: ObjectKind
    extension: str | None
    context: ExtensionContext | None


@dataclass(frozen=True)
class BoundAction:
    """A bound `ToolDef` and its contributor: the declaring extension's name (None for a
    core-registered action) and the workspace-scoped context its handler runs under. The
    contributor may differ from the target kind's owner — visibility is the kind owner's store,
    the handler runs with the contributor's own capabilities, and neither receives the other's
    context."""

    action: ToolDef
    extension: str | None
    context: ExtensionContext | None


def action_registry(
    bound: tuple[BoundAction, ...], kinds: Mapping[str, BoundKind]
) -> dict[str, dict[str, BoundAction]]:
    """Validate and index a deploy's object actions, keyed kind → short name — the boot gate,
    run only after every manifest is collected so extension order cannot fail a valid
    cross-extension attachment. Fails loud on a name outside the kind grammar, a target kind no
    manifest registers, a short name two contributors claim on one kind (across both bindings),
    an input model that admits unknown keys, non-JSON values, or secrets, an input model that
    smuggles a target or authority field, and a presentation or final-act declaration the shared
    gate refuses."""
    registry: dict[str, dict[str, BoundAction]] = {}
    for entry in bound:
        action, owner = entry.action, entry.extension or "core"
        binding = action.bound
        if binding is None:
            raise ValueError(f"{owner!r} registers {action.name!r} as an action with no binding")
        label = f"{owner!r} action {binding.kind}:{action.name}"
        if not KIND_NAME_PATTERN.fullmatch(action.name):
            raise ValueError(f"{label}: action names are snake_case ({KIND_NAME_PATTERN.pattern})")
        if not KIND_NAME_PATTERN.fullmatch(binding.kind):
            raise ValueError(f"{label}: kind names are snake_case ({KIND_NAME_PATTERN.pattern})")
        if binding.kind not in kinds:
            registered = ", ".join(sorted(kinds)) or "none"
            raise ValueError(
                f"{label} targets a kind no extension registers; registered kinds: {registered}"
            )
        held = registry.setdefault(binding.kind, {}).get(action.name)
        if held is not None:
            other = held.extension or "core"
            raise ValueError(
                f"action {binding.kind}:{action.name} from {owner!r} collides with {other!r}'s"
            )
        reserved = [
            field
            for field in ACTION_RESERVED_INPUT_FIELDS
            if field in action.input_model.model_fields
        ]
        if reserved:
            raise ValueError(
                f"{label}: input reserves {', '.join(repr(field) for field in reserved)} — the "
                "wire envelope owns the target and authority fields, never the action input"
            )
        validate_tool_declaration(action, label)
        _validate_spec_model(label, action.input_model)
        registry[binding.kind][action.name] = entry
    return registry


def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]:
    """Validate and index a deploy's kinds — the boot gate. Fails loud on a kind-name collision
    (one global namespace across core and every extension) and on a spec model that admits unknown
    keys, carries a field JSON cannot represent, or holds a secret-bearing field."""
    registry: dict[str, BoundKind] = {}
    for entry in bound:
        kind, owner = entry.kind, entry.extension or "core"
        if not KIND_NAME_PATTERN.fullmatch(kind.name):
            raise ValueError(
                f"{owner!r} registers object kind {kind.name!r}: kind names are snake_case "
                f"({KIND_NAME_PATTERN.pattern})"
            )
        if kind.name in registry:
            other = registry[kind.name].extension or "core"
            raise ValueError(f"object kind {kind.name!r} from {owner!r} collides with {other!r}'s")
        unknown_target_verbs = kind.agent_target_verbs.difference(AGENT_TARGET_VERBS)
        if unknown_target_verbs:
            raise ValueError(
                f"{owner!r} object kind {kind.name!r}: unknown agent target verbs "
                f"{sorted(unknown_target_verbs)}"
            )
        _validate_spec_model(f"{owner!r} object kind {kind.name!r}", kind.spec_model)
        registry[kind.name] = entry
    return registry


def _validate_spec_model(label: str, spec_model: type[BaseModel]) -> None:
    for model in _reachable_models(spec_model):
        if model.model_config.get("extra") != "forbid":
            raise ValueError(f'{label}: spec model {model.__name__} must set extra="forbid"')
        for field_name, field in model.model_fields.items():
            if any(
                isinstance(annotation, type) and issubclass(annotation, (SecretStr, SecretBytes))
                for annotation in _annotation_types(field.annotation)
            ):
                raise ValueError(
                    f"{label}: spec field {model.__name__}.{field_name} is secret-bearing — "
                    f"specs are rendered into transcripts and echoed by object_get"
                )
    try:
        spec_model.model_json_schema()
    except PydanticInvalidForJsonSchema as error:
        raise ValueError(
            f"{label}: spec model is not JSON-representable — specs are stored, rendered, "
            f"and echoed as JSON: {error}"
        ) from error


def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]:
    seen: dict[type[BaseModel], None] = {}
    frontier = [model]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen[current] = None
        for field in current.model_fields.values():
            frontier.extend(
                annotation
                for annotation in _annotation_types(field.annotation)
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
            )
    return tuple(seen)


def _annotation_types(annotation: object) -> tuple[object, ...]:
    args = get_args(annotation)
    if not args:
        return (annotation,)
    nested: list[object] = []
    for arg in args:
        nested.extend(_annotation_types(arg))
    return tuple(nested)


class ObjectListInput(BaseModel):
    kind: str = ""
    agent: str = Field(default="", description=AGENT_TARGET_DESCRIPTION)
    query: str = ""
    filters: dict[str, JsonValue] = Field(default_factory=dict)
    order_by: str = ORDER_BY_DEFAULT
    order: Literal["asc", "desc"] = ORDER_DEFAULT
    cursor: str = ""


class ObjectGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(
        description=(
            "Canonical <kind>/<name> object ref. Empty reads this turn's agent and returns its "
            "concrete ref."
        )
    )
    agent: str = Field(default="", description=AGENT_TARGET_DESCRIPTION)

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        if value:
            ObjectRef.parse(value)
        return value


class ObjectExplainInput(BaseModel):
    kind: str


class ObjectApplyInput(BaseModel):
    manifest: str
    create_only: bool = Field(
        default=False,
        description="Refuse if an object with this kind and name already exists.",
    )
    agent: str = Field(
        default="",
        description=(
            "Stable agent name for a kind declaring cross-agent create or update. Omit for this "
            "agent. Only the workspace main agent may target another agent, on an exact "
            "member-requested call."
        ),
    )


class ObjectDeleteInput(BaseModel):
    kind: str
    name: str
    agent: str = Field(default="", description=AGENT_TARGET_DESCRIPTION)


class ObjectActionInput(BaseModel):
    kind: str
    action: str = Field(
        description="The action's short name as an object read returned it beside this kind."
    )
    name: str = Field(
        default="",
        description="The target instance for an instance action; a collection action takes none.",
    )
    agent: str = Field(default="", description=AGENT_TARGET_DESCRIPTION)
    generation: UUID | None = Field(
        default=None,
        description=(
            "The generation an object_get returned. It is handed to the action beside the live "
            "generation, not checked ahead of it: an action that must not act on a row changed "
            "after your read fences its own write on the two."
        ),
    )
    input: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="The action's own input, validated against its published input_schema.",
    )


class InstanceActionDiscovery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, JsonValue]
    target_name: str | None = None


@dataclass(frozen=True)
class ObjectVerbs:
    """The five CRUD verbs and the `object_action` dispatcher over one deploy's kind and action
    registries. Each CRUD dispatch resolves the kind, re-binds the ToolContext to the owning
    extension's context, and calls the kind's store; every failure raises with its cause and
    renders as the tool error the model recovers from. The `object_action` def exists for schema
    exposure only — the engine resolves the wire name into the bound action before segmentation,
    so its handler is never a dispatch path and raises if reached. Discovery returns each kind's
    actions at the envelope, filtered to the canonical ids the calling turn holds
    (`ctx.granted_actions`); the views are structural — role and state never remove one, and
    invocation repeats every gate."""

    registry: Mapping[str, BoundKind]
    actions: Mapping[str, Mapping[str, BoundAction]] = dataclass_field(default_factory=dict)

    def tools(self) -> tuple[ToolDef, ...]:
        return (
            ToolDef(
                name="object_list",
                description=(
                    "List workspace objects. With no arguments, lists every registered kind with "
                    "its description. With a kind, lists that kind's instances one line each — "
                    "`query` searches names, summaries, and string fields; `filters` exactly "
                    "matches first-class fields, and `order_by` with `order` sorts by a field. A "
                    "returned `next_cursor` passed back as `cursor` fetches the next page. Pass "
                    "each row's `ref` unchanged to object_get for its full spec. An "
                    "agent-targetable kind accepts a stable `agent` name from the main agent for "
                    "the exact requesting member."
                ),
                input_model=ObjectListInput,
                handler=self._list,
                parallel_safe=True,
            ),
            ToolDef(
                name="object_get",
                description=(
                    "Read one workspace object by its canonical kind/name ref: its applied spec, "
                    "the kind's "
                    "live status (next fire time, last sync, fill state), its typed links to "
                    "related objects (each carrying a canonical target ref), and the row's "
                    "created_at/updated_at — recency is the first arbitration signal when "
                    "retrieved facts conflict. An agent-targetable kind accepts a stable `agent` "
                    "name from the main agent for the exact requesting member. Empty `ref` reads "
                    "this turn's agent and returns its concrete ref."
                ),
                input_model=ObjectGetInput,
                handler=self._get,
                parallel_safe=True,
            ),
            ToolDef(
                name="object_explain",
                description=(
                    "Explain an object kind before authoring one: its spec's JSON schema with "
                    "per-field documentation, what mutations it accepts, and the name rule."
                ),
                input_model=ObjectExplainInput,
                handler=self._explain,
                parallel_safe=True,
                binds_member_authority=False,
            ),
            ToolDef(
                name="object_apply",
                description=(
                    "Create or update a workspace object from one YAML manifest with top-level "
                    "keys `kind`, `name`, and `spec`, plus `generation` when updating an object "
                    "whose object_get returned one — echo that value, and the apply is refused "
                    "if the object changed after your read. An existing name is an "
                    "update, a new one a create; the spec is validated against the kind's "
                    "schema (see object_explain) before anything runs. Kinds that don't accept "
                    "a mutation refuse with the path that does. The main agent may pass a stable "
                    "`agent` name when the kind declares cross-agent create or update. Object "
                    "explanation reports the declared target verbs."
                ),
                input_model=ObjectApplyInput,
                handler=self._apply,
                side_effecting=True,
            ),
            ToolDef(
                name="object_delete",
                description=(
                    "Delete a workspace object by kind and name. The result echoes the deleted "
                    "spec, so on a kind that accepts create an accidental delete can be "
                    "re-applied from it. An agent-targetable kind accepts a stable `agent` name "
                    "from the main agent for the exact requesting member."
                ),
                input_model=ObjectDeleteInput,
                handler=self._delete,
                side_effecting=True,
            ),
            ToolDef(
                name=OBJECT_ACTION_TOOL,
                description=(
                    "Invoke an action on a workspace object kind or instance, when CRUD does not "
                    "express the operation. Discover each kind's actions and their input schemas "
                    "where their subject lives: object_list returns a kind's collection actions, "
                    "object_get an instance's actions with the call pre-bound, and object_explain "
                    "both sets. Call with the returned template — `kind` and `action`, `name` for "
                    "an instance action, and the action's own `input`."
                ),
                input_model=ObjectActionInput,
                handler=self._object_action,
            ),
        )

    async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult:
        if not args.kind:
            if args.agent:
                raise ValueError("an agent target requires an agent-scoped object kind")
            narrowed = tuple(
                name
                for name, given in (
                    ("query", bool(args.query)),
                    ("filters", bool(args.filters)),
                    ("cursor", bool(args.cursor)),
                    ("order_by", args.order_by != ORDER_BY_DEFAULT),
                    ("order", args.order != ORDER_DEFAULT),
                )
                if given
            )
            if narrowed:
                raise ValueError(
                    f"{', '.join(narrowed)} narrows one kind's instances, and no kind was named; "
                    "call object_list with no arguments for the kind catalog, then again with the "
                    "kind whose instances you want"
                )
            kinds: list[dict[str, JsonValue]] = []
            for name, entry in sorted(self.registry.items()):
                row: dict[str, JsonValue] = {"kind": name, "description": entry.kind.description}
                if self._granted_actions(ctx, name):
                    row["has_actions"] = True
                kinds.append(row)
            return _json_result({"kinds": kinds})
        bound = self._resolve(args.kind)
        target = await self._target(ctx, bound, args.agent, frozenset({"list"}))
        with object_agent(target):
            page = await bound.kind.store.list(
                self._bound_ctx(ctx, bound),
                ObjectListQuery(
                    query=args.query,
                    filters=args.filters,
                    order_by=args.order_by,
                    order=args.order,
                    cursor=args.cursor,
                    supported_fields=bound.kind.list_fields,
                ),
            )
        listing: dict[str, JsonValue] = {
            "objects": [
                {
                    "ref": str(ObjectRef(kind=args.kind, name=row.name)),
                    "name": row.name,
                    "summary": row.summary,
                    **row.fields,
                }
                for row in page.rows
            ]
        }
        actions = self._action_views(ctx, args.kind, "collection")
        if actions:
            listing["actions"] = actions
        instance_actions = self._instance_action_discoveries(
            ctx,
            args.kind,
            agent=None if target is None else target.name,
        )
        if instance_actions:
            listing["instance_actions"] = instance_actions
        if target is not None:
            listing["agent"] = target.name
        if page.next_cursor is not None:
            listing["next_cursor"] = page.next_cursor
        return _json_result(listing)

    async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult:
        if args.ref:
            ref = ObjectRef.parse(args.ref)
        else:
            if args.agent:
                raise ValueError("empty ref cannot target another agent")
            async with workspace_tx() as connection:
                current_name = (
                    await connection.execute(
                        sa.select(tables.agent.c.name).where(
                            tables.agent.c.workspace_id == ctx.turn.workspace_id,
                            tables.agent.c.id == ctx.turn.agent_id,
                        )
                    )
                ).scalar_one_or_none()
            if current_name is None:
                raise UnknownObject("this turn's agent does not exist")
            ref = ObjectRef(kind="agent", name=current_name)
        bound = self._resolve(ref.kind)
        bound_ctx = self._bound_ctx(ctx, bound)
        target = await self._target(ctx, bound, args.agent, frozenset({"get"}))
        with object_agent(target):
            detail = await bound.kind.store.get(bound_ctx, ref.name)
            if detail is None:
                raise UnknownObject(f"no {ref.kind} object named {ref.name!r}")
            status = await bound.kind.store.status(
                bound_ctx,
                ref.name,
                expected_generation=detail.generation,
            )
        links: list[dict[str, str]] = []
        for link in detail.links:
            link_agent = link.target.agent
            if (
                target is not None
                and (linked := self.registry.get(link.target.kind)) is not None
                and "get" in linked.kind.agent_target_verbs
                and link_agent is None
            ):
                link_agent = target.name
            rendered_link = {"relation": link.relation, "target": str(link.target)}
            if link_agent is not None:
                rendered_link["agent"] = link_agent
            links.append(rendered_link)
        rendered: dict[str, object] = {
            "ref": str(ref),
            "spec": detail.spec.model_dump(mode="json") if detail.spec_visible else None,
            "status": status,
            "actions": self._action_views(
                ctx,
                ref.kind,
                "instance",
                name=ref.name,
                agent=None if target is None else target.name,
                generation=detail.generation,
            ),
            "links": links,
            "created_at": None if detail.created_at is None else detail.created_at.isoformat(),
            "updated_at": None if detail.updated_at is None else detail.updated_at.isoformat(),
        }
        if detail.generation is not None:
            rendered["generation"] = str(detail.generation)
        if target is not None:
            rendered["agent"] = target.name
        return ToolResult(content=(TextContent(text=yaml.safe_dump(rendered, sort_keys=False)),))

    async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult:
        bound = self._resolve(args.kind)
        return _json_result(
            {
                "kind": bound.kind.name,
                "description": bound.kind.description,
                "guidance": bound.kind.guidance,
                "agent_target_verbs": sorted(bound.kind.agent_target_verbs),
                "name_rule": (
                    f"{OBJECT_NAME_PATTERN.pattern}, at most {OBJECT_NAME_MAX_LENGTH} chars"
                ),
                "spec_schema": bound.kind.spec_model.model_json_schema(),
                "collection_actions": self._action_views(ctx, args.kind, "collection"),
                "instance_actions": self._action_views(ctx, args.kind, "instance"),
            }
        )

    async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult:
        kind_name, name, spec_mapping, stated_generation = _parse_envelope(args.manifest)
        bound = self._resolve(kind_name)
        target = await self._target(
            ctx,
            bound,
            args.agent,
            frozenset({"create", "update"}),
        )
        validate_object_name(name)
        try:
            spec = bound.kind.spec_model.model_validate(spec_mapping)
        except ValidationError as error:
            raise SpecValidationFailed(
                "spec failed validation: "
                + "; ".join(
                    f"{'.'.join(str(part) for part in item['loc']) or 'spec'}: {item['msg']}"
                    for item in error.errors()
                )
            ) from error
        bound_ctx = self._bound_ctx(ctx, bound)
        with object_agent(target):
            existing = await bound.kind.store.get(bound_ctx, name)
            if args.create_only and existing is not None:
                article = "an" if kind_name[0].lower() in "aeiou" else "a"
                raise ValueError(f"{article} {kind_name} named {name!r} already exists")
            operation: Literal["create", "update"] = "create" if existing is None else "update"
            if target is not None and operation not in bound.kind.agent_target_verbs:
                raise VerbNotSupported(
                    f"{kind_name!r} objects do not support cross-agent {operation}"
                )
            change_id = await _journal_object_change(
                ctx,
                kind_name,
                name,
                operation,
                None if existing is None else existing.spec,
                spec,
                ctx.turn.agent_id if target is None else target.id,
            )
            try:
                await bound.kind.store.apply(
                    bound_ctx,
                    name,
                    spec,
                    None if existing is None else existing.spec,
                    expected_generation=(
                        stated_generation
                        if stated_generation is not None
                        else None
                        if existing is None
                        else existing.generation
                    ),
                )
            except Exception:
                if change_id is not None:
                    await _withdraw_object_change(ctx, change_id)
                raise
        result = {
            "kind": kind_name,
            "name": name,
            "result": "updated" if existing else "created",
        }
        if target is not None:
            result["agent"] = target.name
        return _json_result(result)

    async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult:
        bound = self._resolve(args.kind)
        bound_ctx = self._bound_ctx(ctx, bound)
        target = await self._target(ctx, bound, args.agent, frozenset({"delete"}))
        with object_agent(target):
            old = await bound.kind.store.get(bound_ctx, args.name)
            if old is None:
                raise UnknownObject(f"no {args.kind} object named {args.name!r}")
            change_id = await _journal_object_change(
                ctx,
                args.kind,
                args.name,
                "delete",
                old.spec,
                None,
                ctx.turn.agent_id if target is None else target.id,
            )
            try:
                await bound.kind.store.delete(
                    bound_ctx,
                    args.name,
                    expected_generation=old.generation,
                )
            except Exception:
                if change_id is not None:
                    await _withdraw_object_change(ctx, change_id)
                raise
        result = {
            "kind": args.kind,
            "name": args.name,
            "deleted": True,
            "spec": old.spec.model_dump(mode="json") if old.spec_visible else None,
        }
        if target is not None:
            result["agent"] = target.name
        return _json_result(result)

    async def _object_action(self, ctx: ToolContext, args: ObjectActionInput) -> ToolResult:
        raise RuntimeError("object_action dispatches through the engine")

    def _granted_actions(self, ctx: ToolContext, kind: str) -> dict[str, BoundAction]:
        held = self.actions.get(kind)
        if not held:
            return {}
        return {
            name: bound
            for name, bound in held.items()
            if bound.action.canonical_id in ctx.granted_actions
        }

    def _action_views(
        self,
        ctx: ToolContext,
        kind: str,
        binding: str,
        *,
        name: str | None = None,
        agent: str | None = None,
        generation: UUID | None = None,
    ) -> list[JsonValue]:
        """The granted actions of one binding on `kind`, lexical by short name, each with its
        pre-bound invocation template — the instance fields ride only what the enclosing read
        established. An agent-targeted read publishes only the actions that declare
        `agent_targetable`: dispatch refuses an agent target on any other action, and the same
        template without the agent would resolve a different agent's object than the read
        returned."""
        views: list[JsonValue] = []
        for _short_name, bound in sorted(self._granted_actions(ctx, kind).items()):
            declared = bound.action.bound
            if declared is None or declared.binding != binding:
                continue
            if declared.name is not None and declared.name != name:
                continue
            if agent is not None and not bound.action.agent_targetable:
                continue
            views.append(
                action_view(kind, bound, name=name, agent=agent, generation=generation).model_dump(
                    mode="json", exclude_none=True
                )
            )
        return views

    def _instance_action_discoveries(
        self,
        ctx: ToolContext,
        kind: str,
        *,
        agent: str | None,
    ) -> list[JsonValue]:
        views: list[JsonValue] = []
        for _short_name, bound in sorted(self._granted_actions(ctx, kind).items()):
            declared = bound.action.bound
            if declared is None or declared.binding != "instance":
                continue
            if agent is not None and not bound.action.agent_targetable:
                continue
            views.append(
                InstanceActionDiscovery(
                    name=bound.action.name,
                    description=bound.action.description,
                    input_schema=bound.action.input_model.model_json_schema(),
                    target_name=declared.name,
                ).model_dump(mode="json", exclude_none=True)
            )
        return views

    def action_request_target(
        self, action: ToolDef, wire: ObjectActionInput
    ) -> ObjectActionRequestTarget:
        """Validate and project an object action's wire target without resolving member state."""
        declared = action.bound
        if declared is None:
            raise ValueError(f"{action.name!r} is not an object action")
        if wire.agent and not action.agent_targetable:
            raise ValueError(f"action {action.canonical_id} takes no agent target")
        if declared.binding == "collection":
            return ObjectActionRequestTarget(
                kind=declared.kind,
                name=None,
                agent=wire.agent or None,
                expected_generation=None,
            )
        if declared.name is not None and wire.name != declared.name:
            raise ValueError(
                f"action {action.canonical_id} acts on {declared.kind}/{declared.name}, not "
                f"{declared.kind}/{wire.name}"
            )
        return ObjectActionRequestTarget(
            kind=declared.kind,
            name=wire.name,
            agent=wire.agent or None,
            expected_generation=wire.generation,
        )

    async def action_target(
        self, ctx: ToolContext, action: ToolDef, wire: ObjectActionInput
    ) -> ObjectActionTarget:
        """Resolve what one dispatched action acts on, before its handler runs: the cross-agent
        gate when the action declares `agent_targetable` and the call names an agent, and — for
        an instance action — the kind owner's own get and status under the kind owner's context
        and agent scope, so visibility and existence are the kind's answer, never the
        contributor's. The read never refuses on the wire's generation: both the live and the
        supplied generation ride the target, and a handler that must fence does so against its
        own write, after the idempotent dedup a resumed dispatch relies on."""
        request = self.action_request_target(action, wire)
        agent_target: ObjectAgent | None = None
        if request.agent:
            agent_target = await self._agent_gate(ctx, request.agent)
        if request.name is None:
            return ObjectActionTarget(
                kind=request.kind,
                name=None,
                agent=agent_target,
                generation=None,
                expected_generation=None,
            )
        kind = self._resolve(request.kind)
        kind_ctx = self._bound_ctx(ctx, kind)
        with object_agent(agent_target):
            detail = await kind.kind.store.get(kind_ctx, request.name)
            if detail is None:
                raise UnknownObject(f"no {request.kind} object named {request.name!r}")
            await kind.kind.store.status(
                kind_ctx, request.name, expected_generation=detail.generation
            )
        return ObjectActionTarget(
            kind=request.kind,
            name=request.name,
            agent=agent_target,
            generation=detail.generation,
            expected_generation=request.expected_generation,
        )

    def _resolve(self, kind: str) -> BoundKind:
        found = self.registry.get(kind)
        if found is None:
            registered = ", ".join(sorted(self.registry)) or "none"
            raise UnknownKind(f"no object kind {kind!r}; registered kinds: {registered}")
        return found

    def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext:
        return replace(ctx, ext=bound.context)

    async def _target(
        self,
        ctx: ToolContext,
        bound: BoundKind,
        name: str,
        verbs: frozenset[AgentTargetVerb],
    ) -> ObjectAgent | None:
        if not name:
            return None
        if not verbs.intersection(bound.kind.agent_target_verbs):
            raise ValueError(
                f"object kind {bound.kind.name!r} rejects an agent target for this verb"
            )
        return await self._agent_gate(ctx, name)

    async def _agent_gate(self, ctx: ToolContext, name: str) -> ObjectAgent | None:
        member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
        async with workspace_tx() as connection:
            current = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        member_name.label("name"),
                        tables.agent.c.is_main,
                    ).where(
                        tables.agent.c.workspace_id == ctx.turn.workspace_id,
                        tables.agent.c.id == ctx.turn.agent_id,
                    )
                )
            ).one()
            if name == current.name:
                return None
            if not current.is_main:
                raise ValueError("only the workspace main agent may target another agent")
            if ctx.turn.subagent_profile is not None:
                raise ValueError("a typed subagent may not target another agent")
            if ctx.speaker_member_id is None:
                raise SpeakerRequired(
                    "targeting another agent requires an exact live member-requested call"
                )
            is_admin = await member_is_admin(
                connection, ctx.turn.workspace_id, ctx.speaker_member_id
            )
            target = (
                await connection.execute(
                    sa.select(tables.agent.c.id, tables.agent.c.name).where(
                        tables.agent.c.workspace_id == ctx.turn.workspace_id,
                        tables.agent.c.name == name,
                        tables.agent.c.archived_at.is_(None),
                        *(
                            ()
                            if is_admin
                            else (
                                sa.or_(
                                    tables.agent.c.visibility == "workspace",
                                    tables.agent.c.owner_member_id == ctx.speaker_member_id,
                                ),
                            )
                        ),
                    )
                )
            ).one_or_none()
        if target is None:
            raise ValueError(f"no agent named {name!r} in this workspace")
        return ObjectAgent(id=target.id, name=target.name)


async def _journal_object_change(
    ctx: ToolContext,
    kind: str,
    name: str,
    verb: Literal["create", "update", "delete"],
    before: BaseModel | None,
    after: BaseModel | None,
    agent_id: UUID,
) -> UUID | None:
    """Journal one create/update/delete ahead of the mutation it describes, so a chat-turn write, a
    prepared-intent write, and a direct portal write are recorded the same way and a journal failure
    aborts before anything commits — a reported failure is then always a write that did not happen.
    The row id derives from the dispatch's idempotency key, so a crash-recovery re-run of the same
    dispatch finds its own row and inserts nothing: the row written before the first mutation keeps
    the true verb and before-spec, which a re-run reading the already-mutated store could not
    recompute. Returns the row id when this call inserted it and None when the row pre-existed: on
    a store refusal the caller withdraws only a row this attempt wrote, so a re-run refused on the
    stale generation fence keeps the record of the first attempt's committed mutation, while a
    cancellation between journal and commit leaves the row for the re-run to heal. `caller` names
    the acting member when one spoke, else the turn; `agent_id` is the agent whose namespace the
    object landed in. Spec bodies are stored inline for the prototype; promoting them to blob refs
    is deferred (RFC 0039)."""
    change_id = (
        uuid4() if ctx.idempotency_key is None else uuid5(NAMESPACE_OID, ctx.idempotency_key)
    )
    caller = (
        f"member:{ctx.speaker_member_id}"
        if ctx.speaker_member_id is not None
        else f"turn:{ctx.turn.id}"
    )
    async with workspace_tx() as connection:
        held = (
            await connection.execute(
                sa.select(tables.object_change.c.id).where(tables.object_change.c.id == change_id)
            )
        ).one_or_none()
        if held is not None:
            return None
        await connection.execute(
            sa.insert(tables.object_change).values(
                id=change_id,
                workspace_id=ctx.turn.workspace_id,
                kind=kind,
                name=name,
                verb=verb,
                caller=caller,
                agent_id=agent_id,
                spec_before=(
                    None if before is None else json.dumps(before.model_dump(mode="json"))
                ),
                spec_after=(None if after is None else json.dumps(after.model_dump(mode="json"))),
                created_at=sa.func.now(),
            )
        )
    return change_id


async def _withdraw_object_change(ctx: ToolContext, change_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.object_change).where(
                tables.object_change.c.id == change_id,
                tables.object_change.c.workspace_id == ctx.turn.workspace_id,
            )
        )


def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object], UUID | None]:
    if len(manifest.encode()) > OBJECT_MANIFEST_MAX_BYTES:
        raise InvalidManifest(f"manifest exceeds {OBJECT_MANIFEST_MAX_BYTES} bytes")
    try:
        document = yaml.safe_load(manifest)
    except yaml.YAMLError as error:
        raise InvalidManifest(f"manifest is not one YAML document: {error}") from error
    if not isinstance(document, dict):
        raise InvalidManifest("manifest must be a YAML mapping of kind, name, and spec")
    if set(document) - {ENVELOPE_GENERATION_KEY} != ENVELOPE_KEYS:
        raise InvalidManifest(
            f"manifest keys must be exactly kind, name, spec, and an optional generation; "
            f"got {sorted(document)}"
        )
    kind, name, spec = document["kind"], document["name"], document["spec"]
    if not isinstance(kind, str) or not isinstance(name, str):
        raise InvalidManifest("kind and name must be strings")
    if not isinstance(spec, dict):
        raise InvalidManifest("spec must be a mapping")
    stated = document.get(ENVELOPE_GENERATION_KEY)
    if stated is None:
        return kind, name, spec, None
    try:
        generation = UUID(str(stated))
    except ValueError as error:
        raise InvalidManifest("generation must be the value an object_get returned") from error
    return kind, name, spec, generation


def _json_result(payload: Mapping[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))
