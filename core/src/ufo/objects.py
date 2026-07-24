"""Workspace objects: registered kinds and the five CRUD verbs (RFC 0017).

An object is a workspace-scoped durable row addressed `kind` + `name`, authored as one YAML
document (`kind`/`name`/`spec`) validated against the kind's declared spec model. An extension
registers kinds through the `objects` Manifest point; each kind's `store` handlers run over the
extension's own tables, so the handler IS the create/update/delete trigger — core validates the
envelope, the name grammar, and the spec, then calls it. Refusal lives in the handler, not a
declaration: a kind that doesn't do a mutation raises `VerbNotSupported` with the domain reason,
and a kind that gates on role checks `ctx.speaker_is_owner()` itself, raising `OwnerRequired`.

`object_registry` is the boot gate: kind-name collisions and spec models that admit unknown keys,
non-JSON types, or secret-bearing fields fail loud before serving — specs are stored, rendered
into transcripts, and echoed by `object_get`, so secrets are excluded by construction.
`ObjectVerbs` is the dispatch workflow: five ToolDefs over one deploy's registry, threading each
call to the owning kind's ExtensionContext."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from typing import ClassVar, Literal, Protocol, get_args
from uuid import UUID

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic.errors import PydanticInvalidForJsonSchema

from ufo.ext.context import ExtensionContext, JsonValue
from ufo.tools.context import TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef

OBJECT_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
OBJECT_NAME_MAX_LENGTH = 64
KIND_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
OBJECT_MANIFEST_MAX_BYTES = 65_536
OBJECT_LIST_PAGE = 50
ENVELOPE_KEYS = frozenset({"kind", "name", "spec"})

type _SortRank = Literal[0, 1, 2, 3]


class UnknownKind(ValueError):
    """The named kind is not registered; the message lists the kinds that are."""


class UnknownObject(ValueError):
    """A get or delete addressed a name that does not exist in its kind."""


class InvalidName(ValueError):
    """The object name violates the one grammar every kind shares."""


class InvalidManifest(ValueError):
    """The apply payload is not one YAML document of exactly `kind`, `name`, and `spec`."""


class SpecValidationFailed(ValueError):
    """The spec failed the kind's declared model; the message names each field and reason."""


class VerbNotSupported(ValueError):
    """Handler-raised: the kind does not do this mutation; the message names the path that does."""


class OwnerRequired(ValueError):
    """Handler-raised: the mutation is gated on the workspace owner and the speaker is not one."""


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
    caller; None means the listing is complete."""

    rows: tuple[ObjectRow, ...]
    next_cursor: str | None = None


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
            case (0, "") | (1, int()) | (2, int() | float()) | (3, str()):
                return self
            case _:
                raise ValueError("cursor value does not match its sort rank")


def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage:
    """Apply the shared search/filter/order/page semantics to one kind's lightweight rows."""
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
                for value in row.fields.values()
                if isinstance(value, str)
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
        case _:
            raise ValueError(
                f"object list field {field_name!r} contains a non-scalar value and cannot order"
            )


class ObjectStore[SpecT: BaseModel](Protocol):
    """A kind's handlers over its own storage, typed by the kind's own spec model. `apply`
    receives the validated spec and the currently applied one (None on create); `status` is
    kind-specific live state rendered beside the spec on get — read by no code, so a loose
    mapping is the honest type. Handlers raise `VerbNotSupported` / `OwnerRequired` / domain
    `ValueError`s; each renders as the tool error."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage: ...

    async def get(self, ctx: ToolContext, name: str) -> SpecT | None: ...

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None: ...

    async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None: ...

    async def delete(self, ctx: ToolContext, name: str) -> None: ...


@dataclass(frozen=True)
class ObjectOwner:
    """Who a member-owned row belongs to and whether the workspace shares it. `member_id` None
    means owner-only: visible to and mutable by the workspace owner alone."""

    member_id: UUID | None
    shared: bool


@dataclass(frozen=True)
class OwnedRow:
    """One row a member-owned kind hands the gate: its name, one-line summary, and owner."""

    name: str
    summary: str
    owner: ObjectOwner


@dataclass(frozen=True)
class MemberOwnedObjects[SpecT: BaseModel]:
    """Base for a member-owned object kind: the per-member visibility and ownership gate lives here
    once, so a kind cannot ship without it. A subclass supplies only data (`_owned_rows`, `_spec`,
    `_status`) and domain mutation (`_apply_owned`, `_delete_owned`); the gate hides a row invisible
    to the acting member (absent from `list`, not-found from `get`/`status`, `UnknownObject` from
    `apply`/`delete`) and refuses `OwnerRequired` when a visible row is not the actor's to change.
    A row is visible when it is shared, owned by the acting member, or the speaker is the workspace
    owner; a row whose owner `member_id` is None is owner-only. A kind whose mutation or deletion is
    a grant/disclosure act sets `mutate_requires_speaker`/`delete_requires_speaker` so the gate also
    refuses it on a speakerless (scheduled/subagent) turn — an act that discloses or revokes access
    needs a live member, never a background turn acting on someone's behalf. The class vars name the
    kind and the two gate messages the refusals carry."""

    kind_name: ClassVar[str]
    mutate_gate: ClassVar[str]
    delete_gate: ClassVar[str]
    mutate_requires_speaker: ClassVar[bool] = False
    delete_requires_speaker: ClassVar[bool] = False

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        is_owner = await ctx.speaker_is_owner()
        acting = ctx.acting_member_id
        rows = tuple(
            ObjectRow(name=row.name, summary=row.summary)
            for row in await self._owned_rows(ctx)
            if self._visible(row.owner, acting, is_owner)
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> SpecT | None:
        owner = await self._owner(ctx, name)
        if owner is None or not self._visible(
            owner, ctx.acting_member_id, await ctx.speaker_is_owner()
        ):
            return None
        return await self._spec(ctx, name)

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        owner = await self._owner(ctx, name)
        if owner is None or not self._visible(
            owner, ctx.acting_member_id, await ctx.speaker_is_owner()
        ):
            return None
        return await self._status(ctx, name)

    async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None:
        owner = await self._owner(ctx, name)
        if owner is not None:
            is_owner = await ctx.speaker_is_owner()
            if not self._visible(owner, ctx.acting_member_id, is_owner):
                raise UnknownObject(f"no {self.kind_name} object named {name!r}")
            if not self._owned(owner, ctx.acting_member_id) and not is_owner:
                raise OwnerRequired(self.mutate_gate)
            if self.mutate_requires_speaker and ctx.speaker_member_id is None:
                raise OwnerRequired(self.mutate_gate)
        await self._apply_owned(ctx, name, spec, old, owner)

    async def delete(self, ctx: ToolContext, name: str) -> None:
        owner = await self._owner(ctx, name)
        if owner is None:
            raise UnknownObject(f"no {self.kind_name} object named {name!r}")
        is_owner = await ctx.speaker_is_owner()
        if not self._visible(owner, ctx.acting_member_id, is_owner):
            raise UnknownObject(f"no {self.kind_name} object named {name!r}")
        if not self._owned(owner, ctx.acting_member_id) and not is_owner:
            raise OwnerRequired(self.delete_gate)
        if self.delete_requires_speaker and ctx.speaker_member_id is None:
            raise OwnerRequired(self.delete_gate)
        await self._delete_owned(ctx, name, owner)

    def _owned(self, owner: ObjectOwner, acting: UUID | None) -> bool:
        """Whether the acting member is the row's member-owner. An owner-only row (`member_id`
        None) is owned by no member — only the workspace owner (`is_owner`) may touch it — so this
        is never true for it, even on a turn whose acting member is also None."""
        return owner.member_id is not None and owner.member_id == acting

    def _visible(self, owner: ObjectOwner, acting: UUID | None, is_owner: bool) -> bool:
        return owner.shared or self._owned(owner, acting) or is_owner

    async def _owner(self, ctx: ToolContext, name: str) -> ObjectOwner | None:
        return next((row.owner for row in await self._owned_rows(ctx) if row.name == name), None)

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]:
        raise NotImplementedError

    async def _spec(self, ctx: ToolContext, name: str) -> SpecT | None:
        raise NotImplementedError

    async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        raise NotImplementedError

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: SpecT,
        old: SpecT | None,
        owner: ObjectOwner | None,
    ) -> None:
        raise NotImplementedError

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class ObjectKind[SpecT: BaseModel]:
    """One registered kind: the YAML `kind:` name, a one-line description that also says in prose
    what mutations the kind accepts and by whom, the guidance `object_explain` returns verbatim —
    a kind that replaces bespoke tools carries their tuned descriptions ~verbatim there, so no
    instruction is lost with the tool — the model every authored spec validates against, and the
    store whose handlers do the work. `list_fields` declares every lightweight field its rows
    produce, so filter and order validation is independent of whether any rows currently exist."""

    name: str
    description: str
    guidance: str
    spec_model: type[SpecT]
    store: ObjectStore[SpecT]
    list_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BoundKind:
    """A kind bound to its owner for dispatch: the declaring extension's name (None for a
    core-registered kind) and the workspace-scoped context its store handlers run under."""

    kind: ObjectKind
    extension: str | None
    context: ExtensionContext | None


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
        _validate_spec_model(owner, kind)
        registry[kind.name] = entry
    return registry


def _validate_spec_model(owner: str, kind: ObjectKind) -> None:
    label = f"{owner!r} object kind {kind.name!r}"
    unknown_list_fields = kind.list_fields.difference(kind.spec_model.model_fields)
    if unknown_list_fields:
        raise ValueError(f"{label}: list fields are not spec fields: {sorted(unknown_list_fields)}")
    for model in _reachable_models(kind.spec_model):
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
        kind.spec_model.model_json_schema()
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
    query: str = ""
    filters: dict[str, JsonValue] = Field(default_factory=dict)
    order_by: str = "name"
    order: Literal["asc", "desc"] = "asc"
    cursor: str = ""


class ObjectGetInput(BaseModel):
    kind: str
    name: str


class ObjectExplainInput(BaseModel):
    kind: str


class ObjectApplyInput(BaseModel):
    manifest: str


class ObjectDeleteInput(BaseModel):
    kind: str
    name: str


@dataclass(frozen=True)
class ObjectVerbs:
    """The five CRUD verbs over one deploy's kind registry. Each dispatch resolves the kind,
    re-binds the ToolContext to the owning extension's context, and calls the kind's store; every
    failure raises with its cause and renders as the tool error the model recovers from."""

    registry: Mapping[str, BoundKind]

    def tools(self) -> tuple[ToolDef, ...]:
        return (
            ToolDef(
                name="object_list",
                description=(
                    "List workspace objects. With no arguments, lists every registered kind with "
                    "its description. With a kind, lists that kind's instances one line each — "
                    "`query` searches names, summaries, and string fields; `filters` exactly "
                    "matches first-class fields, and `order_by` with `order` sorts by a field. A "
                    "returned `next_cursor` passed back as `cursor` fetches the next page. Use "
                    "object_get for one instance's full spec."
                ),
                input_model=ObjectListInput,
                handler=self._list,
                parallel_safe=True,
            ),
            ToolDef(
                name="object_get",
                description=(
                    "Read one workspace object by kind and name: its applied spec and, beside "
                    "it, the kind's live status (next fire time, last sync, fill state)."
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
            ),
            ToolDef(
                name="object_apply",
                description=(
                    "Create or update a workspace object from one YAML manifest with exactly "
                    "three top-level keys: `kind`, `name`, and `spec`. An existing name is an "
                    "update, a new one a create; the spec is validated against the kind's "
                    "schema (see object_explain) before anything runs. Kinds that don't accept "
                    "a mutation refuse with the path that does."
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
                    "re-applied from it."
                ),
                input_model=ObjectDeleteInput,
                handler=self._delete,
                side_effecting=True,
            ),
        )

    async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult:
        if not args.kind:
            kinds = [
                {"kind": name, "description": entry.kind.description}
                for name, entry in sorted(self.registry.items())
            ]
            return _json_result({"kinds": kinds})
        bound = self._resolve(args.kind)
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
                {"name": row.name, "summary": row.summary, **row.fields} for row in page.rows
            ]
        }
        if page.next_cursor is not None:
            listing["next_cursor"] = page.next_cursor
        return _json_result(listing)

    async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult:
        bound = self._resolve(args.kind)
        bound_ctx = self._bound_ctx(ctx, bound)
        spec = await bound.kind.store.get(bound_ctx, args.name)
        if spec is None:
            raise UnknownObject(f"no {args.kind} object named {args.name!r}")
        rendered: dict[str, object] = {
            "kind": args.kind,
            "name": args.name,
            "spec": spec.model_dump(mode="json"),
        }
        status = await bound.kind.store.status(bound_ctx, args.name)
        if status is not None:
            rendered["status"] = status
        return ToolResult(content=(TextContent(text=yaml.safe_dump(rendered, sort_keys=False)),))

    async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult:
        bound = self._resolve(args.kind)
        return _json_result(
            {
                "kind": bound.kind.name,
                "description": bound.kind.description,
                "guidance": bound.kind.guidance,
                "name_rule": (
                    f"{OBJECT_NAME_PATTERN.pattern}, at most {OBJECT_NAME_MAX_LENGTH} chars"
                ),
                "spec_schema": bound.kind.spec_model.model_json_schema(),
            }
        )

    async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult:
        kind_name, name, spec_mapping = _parse_envelope(args.manifest)
        bound = self._resolve(kind_name)
        _validate_name(name)
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
        old = await bound.kind.store.get(bound_ctx, name)
        await bound.kind.store.apply(bound_ctx, name, spec, old)
        return _json_result(
            {"kind": kind_name, "name": name, "result": "updated" if old else "created"}
        )

    async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult:
        bound = self._resolve(args.kind)
        bound_ctx = self._bound_ctx(ctx, bound)
        old = await bound.kind.store.get(bound_ctx, args.name)
        if old is None:
            raise UnknownObject(f"no {args.kind} object named {args.name!r}")
        await bound.kind.store.delete(bound_ctx, args.name)
        return _json_result(
            {
                "kind": args.kind,
                "name": args.name,
                "deleted": True,
                "spec": old.model_dump(mode="json"),
            }
        )

    def _resolve(self, kind: str) -> BoundKind:
        found = self.registry.get(kind)
        if found is None:
            registered = ", ".join(sorted(self.registry)) or "none"
            raise UnknownKind(f"no object kind {kind!r}; registered kinds: {registered}")
        return found

    def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext:
        return replace(ctx, ext=bound.context)


def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]:
    if len(manifest.encode()) > OBJECT_MANIFEST_MAX_BYTES:
        raise InvalidManifest(f"manifest exceeds {OBJECT_MANIFEST_MAX_BYTES} bytes")
    try:
        document = yaml.safe_load(manifest)
    except yaml.YAMLError as error:
        raise InvalidManifest(f"manifest is not one YAML document: {error}") from error
    if not isinstance(document, dict):
        raise InvalidManifest("manifest must be a YAML mapping of kind, name, and spec")
    if set(document) != ENVELOPE_KEYS:
        raise InvalidManifest(
            f"manifest keys must be exactly kind, name, spec; got {sorted(document)}"
        )
    kind, name, spec = document["kind"], document["name"], document["spec"]
    if not isinstance(kind, str) or not isinstance(name, str):
        raise InvalidManifest("kind and name must be strings")
    if not isinstance(spec, dict):
        raise InvalidManifest("spec must be a mapping")
    return kind, name, spec


def _validate_name(name: str) -> None:
    if len(name) > OBJECT_NAME_MAX_LENGTH or not OBJECT_NAME_PATTERN.fullmatch(name):
        raise InvalidName(
            f"object name {name!r} must match {OBJECT_NAME_PATTERN.pattern} "
            f"(at most {OBJECT_NAME_MAX_LENGTH} chars)"
        )


def _json_result(payload: Mapping[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))
