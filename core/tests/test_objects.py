"""The object seam's conformance probe: drive the sample's registered kinds through the five verbs.

The sample declares two kinds — `sample_widget` (full CRUD over its own `ext_store` rows, delete
admin-gated) and `sample_relic` (read-only, every mutation refused) — so these tests exercise the
whole surface through the real tool dispatch and read results back through the sample's own store:
create/update/get/list/delete round-trip, keyset paging under `OBJECT_LIST_PAGE`, the envelope and
name-grammar refusals, spec validation naming its field, handler-raised `VerbNotSupported` and
`AdminRequired`, and the boot gates (kind collision, spec-model gates) failing loud."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_connectors.objects import CONNECTION_OBJECT, CONNECTOR_GRANT_OBJECT
from ufo_ext_memory.objects import MEMORY_OBJECT
from ufo_ext_scheduled_tasks.tools import SCHEDULED_TASK_OBJECT
from ufo_ext_sites.objects import SITE_OBJECT
from ufo_ext_skill_create.manifest import SKILL_OBJECT
from ufo_ext_sources.pages import PAGE_OBJECT
from ufo_ext_sources.tools import SOURCE_OBJECT

import ufo.artifacts as artifacts
import ufo.conversations as conversations
from ufo.agent_scope import agent
from ufo.agents import (
    AGENT_CREATE_GATE,
    AGENT_EDIT_GATE,
    AGENT_KIND,
    AGENT_OBJECT,
    AGENT_PROMPT_REQUIRED,
    AGENT_UNDELETABLE,
    AgentObjects,
    AgentSpec,
)
from ufo.artifact_url import verify_artifact_url
from ufo.artifacts import (
    ARTIFACT_KIND,
    ARTIFACT_OBJECT,
    ArtifactObjects,
    artifact_object_names,
)
from ufo.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.conversations import CONVERSATION_KIND, CONVERSATION_OBJECT
from ufo.credential_kind import CredentialObjects
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, JsonValue, context_for
from ufo.ext.loader import load_manifests, turn_tools, validate_ext_tools
from ufo.ext.manifest import Manifest
from ufo.loop.transcript import Transcript
from ufo.members import MEMBER_OBJECT
from ufo.models.catalog import core_model_specs
from ufo.models.interface import Message, TextBlock, ToolUseBlock
from ufo.models.spec import ModelSpec
from ufo.object_name import (
    OBJECT_NAME_MAX_LENGTH,
    OBJECT_NAME_PATTERN,
    InvalidName,
)
from ufo.objects import (
    MATERIALIZE_MAX_BYTES,
    OBJECT_LIST_PAGE,
    AdminRequired,
    BoundKind,
    GeneratedObjectOwner,
    InvalidManifest,
    MemberListable,
    MemberOwnedObjects,
    MemberReadable,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectOwner,
    ObjectRow,
    ObjectVerbs,
    OwnedRow,
    SpecValidationFailed,
    UnknownKind,
    UnknownObject,
    VerbNotSupported,
    object_page,
    object_registry,
)
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    ExecResult,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.objects import AgentTargetVerb
from ufo.tools.context import SpawnResult, TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef
from ufo.transcript import Conversation, transcript_key
from ufo.workspace import ws

SANDBOX_UNTOUCHED = "object verbs run against stores and must not reach the sandbox"
OBJECT_NARRATION = "checking the workspace records"
ADMIN_CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
JOINER_CREATED_AT = datetime(2026, 7, 2, tzinfo=UTC)


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("object verbs must not spawn a subagent")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member(workspace_id: UUID, created_at: datetime) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                is_admin=created_at == ADMIN_CREATED_AT,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return member_id


# The ids these tests write. `claude-opus-42` is deliberately absent: it is the typo the
# write must refuse.
DEPLOY_MODELS = (
    "auto",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "m2",
    "m3",
)


def _tool_context(
    workspace_id: UUID,
    speaker_member_id: UUID | None = None,
    agent_model: str = "claude-opus-4-8",
    agent_id: UUID | None = None,
    model_specs: Mapping[str, ModelSpec] | None = None,
    auto_model: str = "auto",
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=Path())),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id or uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model=agent_model),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(None),
        artifact_token_secret="",
        models=DEPLOY_MODELS,
        model_specs=model_specs or {},
        auto_model=auto_model,
    )


def _object_tools() -> dict[str, ToolDef]:
    manifest = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert manifest is not None, "sample extension not discovered via entry points — run `uv sync`"
    tools, ext_by_tool = turn_tools(
        (manifest,),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )
    by_name = {tool.name: tool for tool in tools}
    assert not (by_name.keys() & ext_by_tool.keys()) & {
        "object_list",
        "object_get",
        "object_explain",
        "object_apply",
        "object_delete",
    }, "object verbs bind their kind contexts internally, never through ext_by_tool"
    return by_name


async def _text(tools: dict[str, ToolDef], tool_name: str, ctx: ToolContext, **args: object) -> str:
    tool = tools[tool_name]
    payload = {"user_description": OBJECT_NARRATION, **args}
    result: ToolResult = await tool.handler(ctx, tool.input_model.model_validate(payload))
    assert result.is_error is False
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def _agent_text(
    agent_id: UUID,
    tools: dict[str, ToolDef],
    tool_name: str,
    ctx: ToolContext,
    **args: object,
) -> str:
    with agent(agent_id):
        return await _text(tools, tool_name, ctx, **args)


def _widget_manifest(name: str, color: str = "teal", size: int = 1) -> str:
    return f"kind: {sample.WIDGET_KIND}\nname: {name}\nspec:\n  color: {color}\n  size: {size}\n"


async def test_widget_crud_round_trips_through_the_verbs(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)

        kinds = json.loads(await _text(tools, "object_list", ctx))["kinds"]
        assert {row["kind"] for row in kinds} >= {sample.WIDGET_KIND, sample.RELIC_KIND}

        empty = json.loads(
            await _text(
                tools,
                "object_list",
                ctx,
                kind=sample.WIDGET_KIND,
                filters={"color": "teal"},
            )
        )
        assert empty["objects"] == []
        list_tool = tools["object_list"]
        with pytest.raises(ValueError, match="unknown object list filters"):
            await list_tool.handler(
                ctx,
                list_tool.input_model.model_validate(
                    {
                        "user_description": OBJECT_NARRATION,
                        "kind": sample.WIDGET_KIND,
                        "filters": {"colour": "teal"},
                    }
                ),
            )
        with pytest.raises(ValueError, match="unknown object list order field"):
            await list_tool.handler(
                ctx,
                list_tool.input_model.model_validate(
                    {
                        "user_description": OBJECT_NARRATION,
                        "kind": sample.WIDGET_KIND,
                        "order_by": "weight",
                    }
                ),
            )

        created = json.loads(
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        )
        assert created == {"kind": sample.WIDGET_KIND, "name": "anvil", "result": "created"}

        listing = json.loads(await _text(tools, "object_list", ctx, kind=sample.WIDGET_KIND))
        assert [row["name"] for row in listing["objects"]] == ["anvil"]
        assert "next_cursor" not in listing

        fetched = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=sample.WIDGET_KIND, name="anvil")
        )
        assert fetched["spec"] == {"color": "teal", "size": 1}
        assert fetched["status"] is None
        assert fetched["links"] == []
        created_at = datetime.fromisoformat(fetched["created_at"])
        assert datetime.fromisoformat(fetched["updated_at"]) >= created_at

        updated = json.loads(
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil", color="red"))
        )
        assert updated["result"] == "updated"

        refetched = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=sample.WIDGET_KIND, name="anvil")
        )
        assert datetime.fromisoformat(refetched["created_at"]) == created_at
        assert datetime.fromisoformat(refetched["updated_at"]) >= created_at

        explained = json.loads(await _text(tools, "object_explain", ctx, kind=sample.WIDGET_KIND))
        assert "color" in explained["spec_schema"]["properties"]
        assert explained["guidance"] == sample.WIDGET_GUIDANCE
        assert explained["agent_target_verbs"] == []

        deleted = json.loads(
            await _text(tools, "object_delete", ctx, kind=sample.WIDGET_KIND, name="anvil")
        )
        assert deleted["deleted"] is True
        assert deleted["spec"]["color"] == "red"

        get_tool = tools["object_get"]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                ctx,
                get_tool.input_model.model_validate(
                    {
                        "user_description": OBJECT_NARRATION,
                        "kind": sample.WIDGET_KIND,
                        "name": "anvil",
                    }
                ),
            )


async def test_relic_reads_and_refuses_every_mutation(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        ctx = _tool_context(workspace_id)
        fetched = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=sample.RELIC_KIND, name=sample.RELIC_NAME)
        )
        assert fetched["status"] == {"origin": "excavated"}
        assert fetched["created_at"] is None
        assert fetched["updated_at"] is None

        apply_tool = tools["object_apply"]
        relic = f"kind: {sample.RELIC_KIND}\nname: {sample.RELIC_NAME}\nspec:\n  inscription: x\n"
        with pytest.raises(VerbNotSupported, match="read-only"):
            await apply_tool.handler(
                ctx,
                apply_tool.input_model.model_validate(
                    {"user_description": OBJECT_NARRATION, "manifest": relic}
                ),
            )

        delete_tool = tools["object_delete"]
        with pytest.raises(VerbNotSupported, match="read-only"):
            await delete_tool.handler(
                ctx,
                delete_tool.input_model.model_validate(
                    {
                        "user_description": OBJECT_NARRATION,
                        "kind": sample.RELIC_KIND,
                        "name": sample.RELIC_NAME,
                    }
                ),
            )


async def test_widget_delete_gates_on_the_owner(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        joiner = await _member(workspace_id, JOINER_CREATED_AT)
        owner_ctx = _tool_context(workspace_id, speaker_member_id=owner)
        await _text(tools, "object_apply", owner_ctx, manifest=_widget_manifest("guarded"))

        delete_tool = tools["object_delete"]
        args = delete_tool.input_model.model_validate(
            {"user_description": OBJECT_NARRATION, "kind": sample.WIDGET_KIND, "name": "guarded"}
        )
        with pytest.raises(AdminRequired):
            await delete_tool.handler(_tool_context(workspace_id), args)
        with pytest.raises(AdminRequired):
            await delete_tool.handler(_tool_context(workspace_id, speaker_member_id=joiner), args)
        deleted = json.loads(
            await _text(tools, "object_delete", owner_ctx, kind=sample.WIDGET_KIND, name="guarded")
        )
        assert deleted["deleted"] is True


async def test_apply_refusals_name_their_cause(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    apply_tool = tools["object_apply"]

    async def apply(manifest: str) -> None:
        await apply_tool.handler(
            _tool_context(workspace_id),
            apply_tool.input_model.model_validate(
                {"user_description": OBJECT_NARRATION, "manifest": manifest}
            ),
        )

    with ws(workspace_id):
        with pytest.raises(SpecValidationFailed, match="colour"):
            await apply(f"kind: {sample.WIDGET_KIND}\nname: anvil\nspec:\n  colour: teal\n")
        with pytest.raises(SpecValidationFailed, match="color"):
            await apply(f"kind: {sample.WIDGET_KIND}\nname: anvil\nspec: {{}}\n")
        with pytest.raises(UnknownKind, match=sample.WIDGET_KIND):
            await apply("kind: unregistered\nname: anvil\nspec: {}\n")
        with pytest.raises(InvalidName, match="Anvil"):
            await apply(f"kind: {sample.WIDGET_KIND}\nname: Anvil\nspec:\n  color: teal\n")
        with pytest.raises(InvalidManifest, match="exactly kind, name, spec"):
            await apply("kind: sample_widget\nname: anvil\nspec: {}\nextra: 1\n")
        with pytest.raises(InvalidManifest, match="mapping"):
            await apply("just a string")
        with pytest.raises(InvalidManifest, match="one YAML document"):
            await apply("---\nkind: a\nname: b\nspec: {}\n---\nkind: c\nname: d\nspec: {}\n")
        with pytest.raises(InvalidManifest, match="bytes"):
            await apply("kind: sample_widget\nname: anvil\nspec:\n  color: " + "a" * 66_000)


async def test_list_pages_with_cursor_and_query(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    total = OBJECT_LIST_PAGE + 5
    with ws(workspace_id):
        ctx = _tool_context(workspace_id)
        for index in range(total):
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest(f"w-{index:03d}"))

        first = json.loads(await _text(tools, "object_list", ctx, kind=sample.WIDGET_KIND))
        assert len(first["objects"]) == OBJECT_LIST_PAGE
        assert first["next_cursor"]

        second = json.loads(
            await _text(
                tools, "object_list", ctx, kind=sample.WIDGET_KIND, cursor=first["next_cursor"]
            )
        )
        assert [row["name"] for row in second["objects"]] == [
            f"w-{index:03d}" for index in range(OBJECT_LIST_PAGE, total)
        ]
        assert "next_cursor" not in second

        filtered = json.loads(
            await _text(tools, "object_list", ctx, kind=sample.WIDGET_KIND, query="w-003")
        )
        assert [row["name"] for row in filtered["objects"]] == ["w-003"]

        by_field = json.loads(
            await _text(
                tools,
                "object_list",
                ctx,
                kind=sample.WIDGET_KIND,
                filters={"color": "teal"},
                order_by="size",
                order="desc",
            )
        )
        assert len(by_field["objects"]) == OBJECT_LIST_PAGE
        assert by_field["objects"][0]["color"] == "teal"
        assert by_field["objects"][0]["size"] == 1


def test_object_page_cursor_survives_a_removed_boundary_row() -> None:
    rows = tuple(
        ObjectRow(name=f"w-{index:03d}", summary="", fields={"size": index % 3})
        for index in range(OBJECT_LIST_PAGE + 5)
    )
    query = ObjectListQuery(
        order_by="size",
        order="desc",
        supported_fields=frozenset({"size"}),
    )
    first = object_page(rows, query)
    assert first.next_cursor is not None
    remaining = tuple(row for row in rows if row.name != first.rows[-1].name)
    second = object_page(remaining, replace(query, cursor=first.next_cursor))

    ordered = sorted(
        rows,
        key=lambda row: (row.fields["size"], row.name),
        reverse=True,
    )
    assert [row.name for row in second.rows] == [row.name for row in ordered[OBJECT_LIST_PAGE:]]


def test_object_page_rejects_a_cursor_from_another_name_order() -> None:
    rows = tuple(
        ObjectRow(name=f"w-{index:03d}", summary="") for index in range(OBJECT_LIST_PAGE + 5)
    )
    first = object_page(rows, ObjectListQuery(order="asc"))
    assert first.next_cursor is not None
    with pytest.raises(ValueError, match="cursor does not match the requested order"):
        object_page(rows, ObjectListQuery(order="desc", cursor=first.next_cursor))


def test_object_page_rejects_a_malformed_cursor() -> None:
    rows = (ObjectRow(name="widget", summary=""),)
    with pytest.raises(ValueError, match="invalid object list cursor"):
        object_page(rows, ObjectListQuery(cursor="not-hex"))


def test_object_page_rejects_a_cursor_with_an_invalid_sort_rank() -> None:
    rows = (ObjectRow(name="widget", summary=""),)
    cursor = (
        json.dumps(
            {
                "order_by": "name",
                "order": "asc",
                "rank": 0,
                "value": "widget",
                "name": "widget",
            }
        )
        .encode()
        .hex()
    )
    with pytest.raises(ValueError, match="invalid object list cursor") as caught:
        object_page(rows, ObjectListQuery(cursor=cursor))
    assert "cursor value does not match its sort rank" in str(caught.value.__cause__)


def test_object_page_rejects_a_cursor_with_an_unknown_field() -> None:
    rows = (ObjectRow(name="widget", summary=""),)
    cursor = (
        json.dumps(
            {
                "order_by": "name",
                "order": "asc",
                "rank": 3,
                "value": "widget",
                "name": "widget",
                "unexpected": True,
            }
        )
        .encode()
        .hex()
    )
    with pytest.raises(ValueError, match="invalid object list cursor") as caught:
        object_page(rows, ObjectListQuery(cursor=cursor))
    assert "Extra inputs are not permitted" in str(caught.value.__cause__)


def test_object_page_rejects_reserved_row_fields() -> None:
    rows = (ObjectRow(name="widget", summary="", fields={"name": "shadow"}),)
    with pytest.raises(ValueError, match="collide with reserved fields"):
        object_page(rows, ObjectListQuery())


def test_object_page_rejects_undeclared_row_fields() -> None:
    rows = (ObjectRow(name="widget", summary="", fields={"color": "teal"}),)
    with pytest.raises(ValueError, match="rows carry undeclared fields"):
        object_page(rows, ObjectListQuery())


def test_member_owned_kinds_gate_through_the_shared_base() -> None:
    """A member-owned kind cannot hand-roll its own visibility/ownership gate — it subclasses the
    core base that owns it. The connector and source kinds are the reference members; a future
    member-owned kind that reimplements the gate instead of subclassing fails here."""
    assert isinstance(CONNECTION_OBJECT.store, MemberOwnedObjects)
    assert isinstance(CONNECTOR_GRANT_OBJECT.store, MemberOwnedObjects)
    assert isinstance(SOURCE_OBJECT.store, MemberOwnedObjects)
    assert isinstance(SCHEDULED_TASK_OBJECT.store, MemberOwnedObjects)


def test_the_portals_member_reads_are_an_opt_in_a_kind_declares_by_type() -> None:
    """The portal's two projections are implemented, not declared: a kind answering a signed-in
    member outside a turn is `MemberReadable`, one answering a whole page is `MemberListable`, and
    a kind absent from a projection is refused by name by the portal's routes instead of raising
    from inside it."""
    assert isinstance(SCHEDULED_TASK_OBJECT.store, MemberListable)
    assert isinstance(SITE_OBJECT.store, MemberListable)
    assert isinstance(SOURCE_OBJECT.store, MemberListable)
    assert isinstance(CONNECTION_OBJECT.store, MemberListable)
    assert isinstance(CONNECTOR_GRANT_OBJECT.store, MemberListable)
    assert isinstance(CredentialObjects(slots=()), MemberListable)
    assert isinstance(MEMBER_OBJECT.store, MemberListable)
    assert isinstance(ARTIFACT_OBJECT.store, MemberListable)
    assert isinstance(MEMORY_OBJECT.store, MemberListable)
    assert isinstance(SKILL_OBJECT.store, MemberListable)
    assert isinstance(CONVERSATION_OBJECT.store, MemberReadable)
    assert not isinstance(CONVERSATION_OBJECT.store, MemberListable)
    assert not isinstance(AGENT_OBJECT.store, MemberReadable)
    assert not isinstance(AGENT_OBJECT.store, MemberListable)
    assert not isinstance(PAGE_OBJECT.store, MemberReadable)
    assert not isinstance(PAGE_OBJECT.store, MemberListable)


def test_boot_fails_on_a_colliding_kind() -> None:
    widget = ObjectKind(
        name=sample.WIDGET_KIND,
        description="d",
        guidance="g",
        spec_model=sample.WidgetSpec,
        store=sample.WidgetStore(),
    )
    with pytest.raises(ValueError, match="collides"):
        object_registry(
            (
                BoundKind(kind=widget, extension="one", context=None),
                BoundKind(kind=widget, extension="two", context=None),
            )
        )
    with pytest.raises(ValueError, match="snake_case"):
        object_registry(
            (
                BoundKind(
                    kind=ObjectKind(
                        name="Bad-Kind",
                        description="d",
                        guidance="g",
                        spec_model=sample.WidgetSpec,
                        store=sample.WidgetStore(),
                    ),
                    extension="one",
                    context=None,
                ),
            )
        )


def test_boot_fails_on_an_unknown_agent_target_verb() -> None:
    kind = ObjectKind(
        name="widget",
        description="d",
        guidance="g",
        spec_model=sample.WidgetSpec,
        store=sample.WidgetStore(),
        agent_target_verbs=frozenset({"rename"}),
    )
    with pytest.raises(ValueError, match="unknown agent target verbs"):
        object_registry((BoundKind(kind=kind, extension="sample", context=None),))


def _target_tools(
    verbs: frozenset[AgentTargetVerb],
    context: ExtensionContext | None,
) -> dict[str, ToolDef]:
    kind = ObjectKind(
        name=sample.WIDGET_KIND,
        description="d",
        guidance="g",
        spec_model=sample.WidgetSpec,
        store=sample.WidgetStore(),
        agent_target_verbs=verbs,
    )
    registry = object_registry((BoundKind(kind=kind, extension="sample", context=context),))
    return {tool.name: tool for tool in ObjectVerbs(registry).tools()}


async def _target_setup() -> tuple[UUID, UUID, ToolContext]:
    workspace_id = await _workspace()
    member = await _member(workspace_id, ADMIN_CREATED_AT)
    main = await _agent_row(workspace_id, name="ufo", is_main=True)
    await _agent_row(workspace_id, name="research")
    return (
        workspace_id,
        main,
        _tool_context(workspace_id, speaker_member_id=member, agent_id=main),
    )


@pytest.mark.parametrize(
    ("verb", "seed", "color", "result", "refused_name", "refused_verb"),
    (
        ("create", False, "teal", "created", "anvil", "update"),
        ("update", True, "red", "updated", "missing", "create"),
        (None, False, "", "", "", ""),
    ),
    ids=("create-only", "update-only", "neither"),
)
async def test_cross_agent_apply_requires_the_actual_declared_verb(
    db: None,
    verb: AgentTargetVerb | None,
    seed: bool,
    color: str,
    result: str,
    refused_name: str,
    refused_verb: str,
) -> None:
    workspace_id, main, ctx = await _target_setup()
    verbs: frozenset[AgentTargetVerb] = frozenset() if verb is None else frozenset({verb, "get"})
    tools = _target_tools(
        verbs,
        None if verb is None else context_for(sample.NAME, frozenset()),
    )

    with ws(workspace_id), agent(main):
        if verb is None:
            with pytest.raises(ValueError, match="rejects an agent target"):
                await _text(
                    tools,
                    "object_apply",
                    ctx,
                    manifest=_widget_manifest("anvil"),
                    agent="research",
                )
            return
        if seed:
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        applied = json.loads(
            await _text(
                tools,
                "object_apply",
                ctx,
                manifest=_widget_manifest("anvil", color=color),
                agent="research",
            )
        )
        fetched = yaml.safe_load(
            await _text(
                tools,
                "object_get",
                ctx,
                kind=sample.WIDGET_KIND,
                name="anvil",
                agent="research",
            )
        )
        with pytest.raises(VerbNotSupported, match=f"cross-agent {refused_verb}"):
            await _text(
                tools,
                "object_apply",
                ctx,
                manifest=_widget_manifest(refused_name),
                agent="research",
            )

    assert applied["result"] == result
    assert fetched["spec"]["color"] == color


async def test_explain_publishes_the_agent_target_verbs_the_kind_enforces(db: None) -> None:
    """`object_explain` is where an agent learns whether a verb crosses to another agent, so the
    published list is the enforced one: a declared verb takes a target, an undeclared one is
    refused."""
    workspace_id, main, ctx = await _target_setup()
    tools = _target_tools(frozenset({"get", "update"}), context_for(sample.NAME, frozenset()))

    with ws(workspace_id), agent(main):
        explained = json.loads(await _text(tools, "object_explain", ctx, kind=sample.WIDGET_KIND))
        await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil"))
        targeted = json.loads(
            await _text(
                tools,
                "object_apply",
                ctx,
                manifest=_widget_manifest("anvil", color="red"),
                agent="research",
            )
        )
        with pytest.raises(ValueError, match="rejects an agent target"):
            await _text(
                tools,
                "object_delete",
                ctx,
                kind=sample.WIDGET_KIND,
                name="anvil",
                agent="research",
            )

    assert explained["agent_target_verbs"] == ["get", "update"]
    assert targeted == {
        "kind": sample.WIDGET_KIND,
        "name": "anvil",
        "result": "updated",
        "agent": "research",
    }


class _OpenSpec(BaseModel):
    color: str


class _UnrenderableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fn: Callable[[], int]


class _SealedInner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr


class _SecretSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inner: _SealedInner | None = None


def test_boot_fails_on_a_gate_violating_kind() -> None:
    def kind_of(spec_model: type[BaseModel], list_fields: frozenset[str] = frozenset()) -> Manifest:
        return Manifest(
            name="probe",
            version="0",
            objects=(
                ObjectKind(
                    name="probe_kind",
                    description="d",
                    guidance="g",
                    spec_model=spec_model,
                    store=sample.WidgetStore(),
                    list_fields=list_fields,
                ),
            ),
        )

    with pytest.raises(ValueError, match='extra="forbid"'):
        validate_ext_tools((kind_of(_OpenSpec),), None)
    with pytest.raises(ValueError, match="secret-bearing"):
        validate_ext_tools((kind_of(_SecretSpec),), None)
    with pytest.raises(ValueError, match="JSON-representable"):
        validate_ext_tools((kind_of(_UnrenderableSpec),), None)
    validate_ext_tools((kind_of(sample.WidgetSpec, frozenset({"weight"})),), None)


def test_a_declared_field_no_row_produces_reads_as_null() -> None:
    """`list_fields` is the kind's own vocabulary, so a field its rows never carry is admitted at
    boot and reads null in a listing — filterable and orderable, matching nothing."""
    rows = (ObjectRow(name="widget", summary="teal widget"),)
    query = ObjectListQuery(supported_fields=frozenset({"weight"}))
    assert [row.name for row in object_page(rows, query).rows] == ["widget"]
    assert object_page(rows, replace(query, filters={"weight": 3})).rows == ()
    assert [row.name for row in object_page(rows, replace(query, order_by="weight")).rows] == [
        "widget"
    ]


async def _agent_row(
    workspace_id: UUID,
    name: str = "assistant",
    prompt: str = "be brief",
    model: str = "claude-opus-4-8",
    internet_access_allowed: bool = True,
    reasoning: str = "high",
    *,
    is_main: bool = False,
) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt=prompt,
                model=model,
                is_main=is_main,
                visibility="workspace" if is_main else "private",
                internet_access_allowed=internet_access_allowed,
                reasoning=reasoning,
                created_at=datetime(2026, 7, 1, tzinfo=UTC),
                updated_at=datetime(2026, 7, 2, tzinfo=UTC),
            )
        )
    return agent_id


async def test_agent_kind_updates_model_admin_gated_and_returns_prompt(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        joiner = await _member(workspace_id, JOINER_CREATED_AT)
        agent_id = await _agent_row(workspace_id, is_main=True)
        owner_ctx = _tool_context(
            workspace_id,
            speaker_member_id=owner,
            agent_id=agent_id,
        )
        owner_ctx = replace(
            owner_ctx,
            turn=owner_ctx.turn.model_copy(update={"admission_source": "intent"}),
        )

        fetched = yaml.safe_load(
            await _text(tools, "object_get", owner_ctx, kind=AGENT_KIND, name="assistant")
        )
        assert fetched["spec"] == {
            "model": "claude-opus-4-8",
            "internet_access_allowed": True,
            "reasoning": "high",
            "sandbox_size": "small",
            "visibility": "workspace",
            "prompt": "be brief",
            "input_schema": None,
            "output_schema": None,
        }
        assert datetime.fromisoformat(fetched["created_at"]).replace(tzinfo=UTC) == datetime(
            2026, 7, 1, tzinfo=UTC
        )
        assert datetime.fromisoformat(fetched["updated_at"]).replace(tzinfo=UTC) == datetime(
            2026, 7, 2, tzinfo=UTC
        )

        manifest = yaml.safe_dump(
            {
                "kind": AGENT_KIND,
                "name": "assistant",
                "spec": {
                    "model": "claude-fable-5",
                    "internet_access_allowed": False,
                    "reasoning": "low",
                },
            }
        )
        applied = json.loads(await _text(tools, "object_apply", owner_ctx, manifest=manifest))
        assert applied["result"] == "updated"
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.reasoning,
                    ).where(tables.agent.c.workspace_id == workspace_id)
                )
            ).one()
        assert (row.prompt, row.model, row.internet_access_allowed, row.reasoning) == (
            "be brief",
            "claude-fable-5",
            False,
            "low",
        )

        listing = json.loads(await _text(tools, "object_list", owner_ctx, kind=AGENT_KIND))
        assert [entry["name"] for entry in listing["objects"]] == ["assistant"]
        joiner_fetched = yaml.safe_load(
            await _text(
                tools,
                "object_get",
                _tool_context(workspace_id, speaker_member_id=joiner),
                kind=AGENT_KIND,
                name="assistant",
            )
        )
        assert joiner_fetched["spec"]["internet_access_allowed"] is False

        apply_tool = tools["object_apply"]
        args = apply_tool.input_model.model_validate(
            {"user_description": OBJECT_NARRATION, "manifest": manifest}
        )
        with pytest.raises(AdminRequired):
            await apply_tool.handler(_tool_context(workspace_id, speaker_member_id=joiner), args)
        with pytest.raises(AdminRequired):
            await apply_tool.handler(_tool_context(workspace_id), args)

        prompt_write = apply_tool.input_model.model_validate(
            {
                "user_description": OBJECT_NARRATION,
                "manifest": yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "assistant",
                        "spec": {
                            "prompt": "injected",
                            "model": "claude-fable-5",
                            "internet_access_allowed": False,
                            "reasoning": "high",
                        },
                    }
                ),
            }
        )
        await apply_tool.handler(owner_ctx, prompt_write)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.agent.c.prompt, tables.agent.c.reasoning).where(
                        tables.agent.c.id == agent_id
                    )
                )
            ).one()
        assert row == ("injected", "high")


async def test_agent_kind_round_trips_sandbox_size(db: None) -> None:
    """The row births at `small` and an apply naming another size persists it; an apply that omits
    the field states the default, so a read-modify-write keeps whatever the agent holds only by
    carrying it — the spec is declarative, never a patch."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        agent_id = await _agent_row(workspace_id, is_main=True)
        owner_ctx = _tool_context(workspace_id, speaker_member_id=owner, agent_id=agent_id)
        owner_ctx = replace(
            owner_ctx,
            turn=owner_ctx.turn.model_copy(update={"admission_source": "intent"}),
        )

        manifest = yaml.safe_dump(
            {
                "kind": AGENT_KIND,
                "name": "assistant",
                "spec": {
                    "model": "claude-fable-5",
                    "internet_access_allowed": True,
                    "reasoning": "high",
                    "sandbox_size": "large",
                },
            }
        )
        applied = json.loads(await _text(tools, "object_apply", owner_ctx, manifest=manifest))
        assert applied["result"] == "updated"
        async with workspace_tx() as connection:
            stored = await connection.scalar(
                sa.select(tables.agent.c.sandbox_size).where(
                    tables.agent.c.workspace_id == workspace_id
                )
            )
        assert stored == "large"
        fetched = yaml.safe_load(
            await _text(tools, "object_get", owner_ctx, kind=AGENT_KIND, name="assistant")
        )
        assert fetched["spec"]["sandbox_size"] == "large"


async def test_agent_kind_visibility_widens_and_main_stays_workspace(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        main = await _agent_row(workspace_id, name="ufo", is_main=True)
        await _agent_row(workspace_id, name="research")
        ctx = _tool_context(workspace_id, speaker_member_id=owner, agent_id=main)
        ctx = replace(ctx, turn=ctx.turn.model_copy(update={"admission_source": "intent"}))

        fetched = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=AGENT_KIND, name="research")
        )
        assert fetched["spec"]["visibility"] == "private"
        widen = yaml.safe_dump(
            {
                "kind": AGENT_KIND,
                "name": "research",
                "spec": {
                    "model": "claude-opus-4-8",
                    "internet_access_allowed": True,
                    "reasoning": "high",
                    "visibility": "workspace",
                },
            }
        )
        applied = json.loads(await _text(tools, "object_apply", ctx, manifest=widen))
        assert applied["result"] == "updated"
        async with workspace_tx() as connection:
            stored = await connection.scalar(
                sa.select(tables.agent.c.visibility).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.name == "research",
                )
            )
        assert stored == "workspace"

        apply_tool = tools["object_apply"]
        narrow_main = apply_tool.input_model.model_validate(
            {
                "user_description": OBJECT_NARRATION,
                "manifest": yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "ufo",
                        "spec": {
                            "model": "claude-opus-4-8",
                            "internet_access_allowed": True,
                            "reasoning": "high",
                            "visibility": "private",
                        },
                    }
                ),
            }
        )
        with pytest.raises(ValueError, match="answers every member"):
            await apply_tool.handler(ctx, narrow_main)

        omitting = apply_tool.input_model.model_validate(
            {
                "user_description": OBJECT_NARRATION,
                "manifest": yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "ufo",
                        "spec": {
                            "model": "claude-opus-4-8",
                            "internet_access_allowed": True,
                            "reasoning": "high",
                        },
                    }
                ),
            }
        )
        await apply_tool.handler(ctx, omitting)
        async with workspace_tx() as connection:
            kept = await connection.scalar(
                sa.select(tables.agent.c.visibility).where(tables.agent.c.id == main)
            )
        assert kept == "workspace"

        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.id == main)
                .values(visibility="private", prompt="drifted")
            )
        await apply_tool.handler(ctx, omitting)
        async with workspace_tx() as connection:
            repaired = (
                await connection.execute(
                    sa.select(tables.agent.c.visibility, tables.agent.c.prompt).where(
                        tables.agent.c.id == main
                    )
                )
            ).one()
        assert repaired == ("workspace", "drifted")


async def test_a_child_agent_is_scoped_to_the_main_agent(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        main = await _agent_row(workspace_id, name="ufo", is_main=True)
        await _agent_row(workspace_id, name="research")
        ctx = _tool_context(workspace_id, speaker_member_id=owner, agent_id=main)

        child = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=AGENT_KIND, name="research")
        )
        parent = yaml.safe_load(await _text(tools, "object_get", ctx, kind=AGENT_KIND, name="ufo"))
    assert child["links"] == [
        {"relation": "scoped_to", "target": {"kind": AGENT_KIND, "name": "ufo"}}
    ]
    assert parent["links"] == [], "the main agent is the scope, so it links to none"


async def test_agent_creation_refuses_a_name_no_link_can_express(db: None) -> None:
    """`agent.name` is plain text but every agent-named link carries it as an `ObjectRef` name, so a
    name outside that grammar would raise out of a later read instead of at the write. Creation
    refuses it, and never coerces it into a conforming name."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        main = await _agent_row(workspace_id, name="ufo", is_main=True)
        ctx = _tool_context(workspace_id, speaker_member_id=owner, agent_id=main)
        with pytest.raises(InvalidName):
            await _text(
                tools,
                "object_apply",
                ctx,
                manifest=yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "candidate:8f14e45f",
                        "spec": {"prompt": "be helpful"},
                    }
                ),
            )
        async with workspace_tx() as connection:
            names = (
                await connection.execute(
                    sa.select(tables.agent.c.name).where(
                        tables.agent.c.workspace_id == workspace_id
                    )
                )
            ).scalars()
    assert set(names) == {"ufo"}


async def test_agent_kind_refuses_an_effort_outside_the_enum(db: None) -> None:
    """The spec is the member-facing gate on the effort: a level the enum does not name is refused
    before any write, and the stored value stands."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        agent_id = await _agent_row(workspace_id, is_main=True)
        ctx = _tool_context(workspace_id, speaker_member_id=owner, agent_id=agent_id)
        apply_tool = tools["object_apply"]
        args = apply_tool.input_model.model_validate(
            {
                "user_description": OBJECT_NARRATION,
                "manifest": yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "assistant",
                        "spec": {
                            "model": "claude-opus-4-8",
                            "internet_access_allowed": True,
                            "reasoning": "turbo",
                        },
                    }
                ),
            }
        )
        with pytest.raises(SpecValidationFailed, match="reasoning"):
            await apply_tool.handler(ctx, args)
        async with workspace_tx() as connection:
            stored = (
                await connection.execute(
                    sa.select(tables.agent.c.reasoning).where(tables.agent.c.id == agent_id)
                )
            ).scalar_one()
        assert stored == "high"


async def test_agent_kind_reports_the_model_an_auto_agent_actually_runs(db: None) -> None:
    """An agent deferring to the deploy stores the `auto` sentinel, which names a choice rather than
    a model. Everything a member reads for information — the listing line, status — reports the
    concrete model the turn resolved, so "what model am I on" is answerable. The spec keeps the
    sentinel, so a read-then-apply round trip cannot silently pin the agent to today's model."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        await _agent_row(workspace_id, model="auto", is_main=True)
        ctx = _tool_context(workspace_id, speaker_member_id=owner, agent_model="claude-opus-5")

        fetched = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=AGENT_KIND, name="assistant")
        )
        listing = json.loads(await _text(tools, "object_list", ctx, kind=AGENT_KIND))

    assert fetched["spec"]["model"] == "auto"
    assert fetched["status"]["model"] == "claude-opus-5"
    assert "claude-opus-5" in listing["objects"][0]["summary"]
    assert "auto" not in listing["objects"][0]["summary"]


async def test_agent_kind_creates_owned_by_any_speaking_member_and_refuses_delete(
    db: None,
) -> None:
    """Create is birth, not an edit: any speaking member applies a name no agent holds with a
    prompt and gets a fresh non-main row that copies nothing and is stamped theirs; without a
    prompt, without a speaker, or under a taken name the create refuses; delete stays refused."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        member = await _member(workspace_id, JOINER_CREATED_AT)
        agent_id = await _agent_row(workspace_id, is_main=True)
        ctx = _tool_context(workspace_id, speaker_member_id=member, agent_id=agent_id)
        apply_tool = tools["object_apply"]

        def create_input(name: str, spec: dict, *, create_only: bool = False) -> object:
            return apply_tool.input_model.model_validate(
                {
                    "user_description": OBJECT_NARRATION,
                    "manifest": yaml.safe_dump({"kind": AGENT_KIND, "name": name, "spec": spec}),
                    "create_only": create_only,
                }
            )

        full = {
            "model": "m2",
            "internet_access_allowed": False,
            "reasoning": "low",
            "prompt": "be second",
        }
        with pytest.raises(ValueError) as promptless:
            await apply_tool.handler(
                ctx,
                create_input(
                    "second-agent",
                    {"model": "m2", "internet_access_allowed": False, "reasoning": "low"},
                ),
            )
        assert str(promptless.value) == AGENT_PROMPT_REQUIRED
        speakerless_ctx = _tool_context(workspace_id, speaker_member_id=None, agent_id=agent_id)
        with pytest.raises(ValueError) as speakerless_refusal:
            await apply_tool.handler(speakerless_ctx, create_input("second-agent", full))
        assert str(speakerless_refusal.value) == AGENT_CREATE_GATE
        created = json.loads(
            await _text(
                tools,
                "object_apply",
                ctx,
                manifest=yaml.safe_dump({"kind": AGENT_KIND, "name": "second-agent", "spec": full}),
            )
        )
        assert created["result"] == "created"
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.agent).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == "second-agent",
                    )
                )
            ).one()
        assert row.prompt == "be second"
        assert row.model == "m2"
        assert row.internet_access_allowed is False
        assert row.reasoning == "low"
        assert row.is_main is False
        assert row.owner_member_id == member
        with pytest.raises(ValueError) as guarded_duplicate:
            await apply_tool.handler(
                ctx,
                create_input(
                    "second-agent",
                    {**full, "prompt": "replace the existing prompt"},
                    create_only=True,
                ),
            )
        assert str(guarded_duplicate.value) == "an agent named 'second-agent' already exists"
        with pytest.raises(ValueError) as raced_duplicate:
            await AgentObjects().apply(
                ctx,
                "second-agent",
                AgentSpec(
                    model="m2",
                    internet_access_allowed=False,
                    reasoning="low",
                    prompt="racer",
                ),
                None,
                expected_generation=None,
            )
        assert str(raced_duplicate.value) == "an agent named 'second-agent' already exists"
        assert (await AgentObjects().get(ctx, "second-agent")).spec.prompt == "be second"
        delete_tool = tools["object_delete"]
        with pytest.raises(VerbNotSupported) as delete_refusal:
            await delete_tool.handler(
                ctx,
                delete_tool.input_model.model_validate(
                    {"user_description": OBJECT_NARRATION, "kind": AGENT_KIND, "name": "assistant"}
                ),
            )
        assert str(delete_refusal.value) == AGENT_UNDELETABLE


async def test_an_owner_or_admin_edits_an_agent_and_anyone_else_is_refused(db: None) -> None:
    """One ownership rule for every write: the owner edits their agent — prompt and settings, from
    any agent's lane, no intent required — an admin edits any agent, and an ownerless row (the
    main agent, a provisioned agent) answers to admins alone."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        admin = await _member(workspace_id, ADMIN_CREATED_AT)
        member = await _member(workspace_id, JOINER_CREATED_AT)
        main = await _agent_row(workspace_id, name="ufo", is_main=True)
        child = await _agent_row(workspace_id, name="research")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(sandbox_size="large")
                .where(tables.agent.c.id == child)
            )
        apply_tool = tools["object_apply"]
        admin_ctx = _tool_context(workspace_id, speaker_member_id=admin, agent_id=main)
        member_ctx = _tool_context(workspace_id, speaker_member_id=member, agent_id=main)
        manifest = yaml.safe_dump(
            {
                "kind": AGENT_KIND,
                "name": "research",
                "spec": {
                    "model": "claude-opus-4-8",
                    "internet_access_allowed": True,
                    "reasoning": "high",
                    "prompt": "review the exact request",
                },
            }
        )
        result = json.loads(await _text(tools, "object_apply", admin_ctx, manifest=manifest))
        assert result["result"] == "updated"
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.agent.c.prompt, tables.agent.c.sandbox_size).where(
                        tables.agent.c.id == child
                    )
                )
            ).one()
        assert tuple(row) == ("review the exact request", "large")

        unauthorized = apply_tool.input_model.model_validate(
            {
                "manifest": manifest.replace("review the exact request", "unauthorized rewrite"),
                "user_description": OBJECT_NARRATION,
            }
        )
        with pytest.raises(AdminRequired) as ownerless_refusal:
            await apply_tool.handler(member_ctx, unauthorized)
        assert str(ownerless_refusal.value) == AGENT_EDIT_GATE

        owned = {
            "model": "m2",
            "internet_access_allowed": True,
            "reasoning": "low",
            "prompt": "triage tickets",
        }
        await _text(
            tools,
            "object_apply",
            member_ctx,
            manifest=yaml.safe_dump({"kind": AGENT_KIND, "name": "triage", "spec": owned}),
        )
        edited = json.loads(
            await _text(
                tools,
                "object_apply",
                member_ctx,
                manifest=yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "triage",
                        "spec": {**owned, "model": "m3", "prompt": "triage tickets faster"},
                    }
                ),
            )
        )
        assert edited["result"] == "updated"
        async with workspace_tx() as connection:
            triage = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.owner_member_id,
                    ).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == "triage",
                    )
                )
            ).one()
        assert tuple(triage) == ("triage tickets faster", "m3", member)

        stranger_ctx = _tool_context(
            workspace_id,
            speaker_member_id=await _member(workspace_id, JOINER_CREATED_AT),
            agent_id=main,
        )
        with pytest.raises(AdminRequired) as stranger_refusal:
            await apply_tool.handler(
                stranger_ctx,
                apply_tool.input_model.model_validate(
                    {
                        "manifest": yaml.safe_dump(
                            {
                                "kind": AGENT_KIND,
                                "name": "triage",
                                "spec": {**owned, "prompt": "stolen"},
                            }
                        ),
                        "user_description": OBJECT_NARRATION,
                    }
                ),
            )
        assert str(stranger_refusal.value) == AGENT_EDIT_GATE

        with pytest.raises(AdminRequired) as main_refusal:
            await apply_tool.handler(
                member_ctx,
                apply_tool.input_model.model_validate(
                    {
                        "manifest": yaml.safe_dump(
                            {
                                "kind": AGENT_KIND,
                                "name": "ufo",
                                "spec": {
                                    "model": "claude-opus-4-8",
                                    "internet_access_allowed": True,
                                    "reasoning": "high",
                                    "prompt": "rewrite the main agent",
                                },
                            }
                        ),
                        "user_description": OBJECT_NARRATION,
                    }
                ),
            )
        assert str(main_refusal.value) == AGENT_EDIT_GATE


ARTIFACT_TEST_SECRET = "artifact-test-secret"


async def _turn_row(
    workspace_id: UUID,
    agent_id: UUID | None = None,
    member_id: UUID | None = None,
    audience: Audience | None = None,
    surface_label: str | None = None,
) -> Turn:
    if agent_id is None:
        agent_id = await _agent_row(workspace_id, name=f"agent-{uuid4().hex[:8]}")
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=f"objects-{conversation_id.hex[:8]}",
                surface_label=surface_label,
                member_id=member_id,
                audience=str(conversation_audience(member_id) if audience is None else audience),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="hi",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Turn(
        id=turn_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
    )


async def _workspace_context(
    turn: Turn,
    tmp_path: Path,
    audience: Audience = SHARED_AUDIENCE,
    speaker_member_id: UUID | None = None,
) -> tuple[ToolContext, Path]:
    """A context whose sandbox is the real local carrier over a temp workspace and whose blob
    store is a real temp filesystem store — object materialization runs its true path."""
    workspace_dir = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=turn.conversation_id,
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace_dir),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    ctx = ToolContext(
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs")),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id or turn.speaker_member_id,
        audience=audience,
        artifact_token_secret=ARTIFACT_TEST_SECRET,
    )
    return ctx, workspace_dir


async def _shared_artifact_row(turn: Turn, blob_key: str, filename: str, size_bytes: int) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn.id,
                blob_key=blob_key,
                workspace_id=turn.workspace_id,
                filename=filename,
                subject=None,
                media_type="application/octet-stream",
                size_bytes=size_bytes,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _narrow_conversation(conversation_id: UUID, member_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.id == conversation_id)
            .values(
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                updated_at=sa.func.now(),
            )
        )


def test_artifact_object_names_prefix_the_conversation_and_slug_the_filename() -> None:
    conv_a, conv_b = uuid4(), uuid4()
    names = artifact_object_names(
        [(conv_a, "Q3 Report(final).PDF"), (conv_a, ".env"), (conv_a, "¡!")]
    )
    assert names[(conv_a, "Q3 Report(final).PDF")] == f"{conv_a.hex[:8]}-q3-report-final-pdf"
    assert names[(conv_a, ".env")] == f"{conv_a.hex[:8]}-env"
    assert names[(conv_a, "¡!")] == f"{conv_a.hex[:8]}-artifact"

    across = artifact_object_names([(conv_a, "report.txt"), (conv_b, "report.txt")])
    assert across[(conv_a, "report.txt")] == f"{conv_a.hex[:8]}-report-txt"
    assert across[(conv_b, "report.txt")] == f"{conv_b.hex[:8]}-report-txt"

    colliding = artifact_object_names([(conv_a, "a b.txt"), (conv_a, "a_b.txt")])
    assert len(set(colliding.values())) == 2
    for name in colliding.values():
        assert name.startswith(f"{conv_a.hex[:8]}-a-b-txt-")
        assert OBJECT_NAME_PATTERN.fullmatch(name)

    long_filename = f"{'a' * 100}.txt"
    long = artifact_object_names([(conv_a, long_filename)])[(conv_a, long_filename)]
    assert len(long) <= OBJECT_NAME_MAX_LENGTH
    assert OBJECT_NAME_PATTERN.fullmatch(long)


async def test_the_artifact_kind_filters_and_orders_on_its_declared_fields(
    db: None, tmp_path: Path
) -> None:
    """`conversation` and `shared_at` ride the listing rows beside `filename` and `subject`, so one
    session's files are reachable by filter and the newest share by order."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        agent_id = await _agent_row(workspace_id, name="assistant", is_main=True)
        first = await _turn_row(workspace_id, agent_id=agent_id)
        second = await _turn_row(workspace_id, agent_id=agent_id)
        first_ctx, _ = await _workspace_context(first, tmp_path / "one")
        second_ctx, _ = await _workspace_context(second, tmp_path / "two")
        await first_ctx.sandbox.bash("printf 'older' > alpha.txt")
        await second_ctx.sandbox.bash("printf 'newer' > beta.txt")
        await _text(tools, "share_file", first_ctx, files=[{"file_path": "alpha.txt"}])
        await _text(tools, "share_file", second_ctx, files=[{"file_path": "beta.txt"}])
        async with workspace_tx() as connection:
            for filename, day in (("alpha.txt", 3), ("beta.txt", 4)):
                await connection.execute(
                    sa.update(tables.shared_artifact)
                    .where(tables.shared_artifact.c.filename == filename)
                    .values(created_at=datetime(2026, 7, day, tzinfo=UTC))
                )
        alpha = f"{first.conversation_id.hex[:8]}-alpha-txt"
        beta = f"{second.conversation_id.hex[:8]}-beta-txt"

        listing = json.loads(
            await _agent_text(agent_id, tools, "object_list", first_ctx, kind=ARTIFACT_KIND)
        )
        by_conversation = json.loads(
            await _agent_text(
                agent_id,
                tools,
                "object_list",
                first_ctx,
                kind=ARTIFACT_KIND,
                filters={"conversation": str(second.conversation_id)},
            )
        )
        newest_first = json.loads(
            await _agent_text(
                agent_id,
                tools,
                "object_list",
                first_ctx,
                kind=ARTIFACT_KIND,
                order_by="shared_at",
                order="desc",
            )
        )
    rows = {row["name"]: row for row in listing["objects"]}
    assert rows[alpha]["conversation"] == str(first.conversation_id)
    assert rows[alpha]["filename"] == "alpha.txt"
    assert rows[alpha]["subject"] == ""
    assert datetime.fromisoformat(rows[alpha]["shared_at"]).replace(tzinfo=UTC) == datetime(
        2026, 7, 3, tzinfo=UTC
    )
    assert [row["name"] for row in by_conversation["objects"]] == [beta]
    assert [row["name"] for row in newest_first["objects"]] == [beta, alpha]


async def test_share_file_lands_an_artifact_object_and_get_copies_the_latest_back(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id)
        ctx, workspace_dir = await _workspace_context(turn, tmp_path)
        await ctx.sandbox.bash("printf 'quarterly numbers' > report.txt")

        name = f"{turn.conversation_id.hex[:8]}-report-txt"
        shared = json.loads(
            await _text(
                tools,
                "share_file",
                ctx,
                files=[{"file_path": "report.txt", "subject": "Q3 numbers"}],
            )
        )[0]
        assert shared["artifact"] == name

        listing = json.loads(
            await _agent_text(turn.agent_id, tools, "object_list", ctx, kind=ARTIFACT_KIND)
        )
        assert [row["name"] for row in listing["objects"]] == [name]
        assert "report.txt" in listing["objects"][0]["summary"]
        assert listing["objects"][0]["filename"] == "report.txt"
        assert listing["objects"][0]["subject"] == "Q3 numbers"
        by_filename = json.loads(
            await _agent_text(
                turn.agent_id,
                tools,
                "object_list",
                ctx,
                kind=ARTIFACT_KIND,
                filters={"filename": "report.txt"},
                order_by="filename",
            )
        )
        assert [row["name"] for row in by_filename["objects"]] == [name]
        by_subject = json.loads(
            await _agent_text(
                turn.agent_id,
                tools,
                "object_list",
                ctx,
                kind=ARTIFACT_KIND,
                filters={"subject": "Q3 numbers"},
                order_by="subject",
            )
        )
        assert [row["name"] for row in by_subject["objects"]] == [name]
        filtered = json.loads(
            await _agent_text(
                turn.agent_id,
                tools,
                "object_list",
                ctx,
                kind=ARTIFACT_KIND,
                query="no-such-share",
            )
        )
        assert filtered["objects"] == []
        folded = json.loads(
            await _agent_text(
                turn.agent_id,
                tools,
                "object_list",
                ctx,
                kind=ARTIFACT_KIND,
                query="q3 NUMBERS",
            )
        )
        assert [row["name"] for row in folded["objects"]] == [name]

        fetched = yaml.safe_load(
            await _agent_text(
                turn.agent_id, tools, "object_get", ctx, kind=ARTIFACT_KIND, name=name
            )
        )
        assert fetched["spec"] == {
            "filename": "report.txt",
            "media_type": "text/plain",
            "subject": "Q3 numbers",
        }
        status = fetched["status"]
        assert status["size_bytes"] == len(b"quarterly numbers")
        assert status["turn_id"] == str(turn.id)
        assert fetched["links"] == [
            {
                "relation": "created_in",
                "target": {"kind": "conversation", "name": str(turn.conversation_id)},
            }
        ]
        assert status["versions"] == 1
        assert status["workspace_path"] == f"artifacts/{name}/report.txt"
        assert (workspace_dir / "artifacts" / name / "report.txt").read_bytes() == (
            b"quarterly numbers"
        )
        split = urlsplit(status["download_url"])
        artifact_id, filename = split.path.removeprefix("/artifacts/").split("/")
        query = parse_qs(split.query)
        claims = verify_artifact_url(
            ARTIFACT_TEST_SECRET,
            artifact_id,
            filename,
            query["exp"][0],
            query["sig"][0],
            "",
            query["ws"][0],
            datetime.now(UTC),
        )
        assert claims.filename == "report.txt"

        await ctx.sandbox.bash("printf 'revised numbers' > report.txt")
        reshared = json.loads(
            await _text(tools, "share_file", ctx, files=[{"file_path": "report.txt"}])
        )[0]
        assert reshared["artifact"] == name
        listing = json.loads(
            await _agent_text(turn.agent_id, tools, "object_list", ctx, kind=ARTIFACT_KIND)
        )
        assert [row["name"] for row in listing["objects"]] == [name]
        refetched = yaml.safe_load(
            await _agent_text(
                turn.agent_id, tools, "object_get", ctx, kind=ARTIFACT_KIND, name=name
            )
        )
        assert refetched["status"]["versions"] == 2
        assert (workspace_dir / "artifacts" / name / "report.txt").read_bytes() == (
            b"revised numbers"
        )


async def test_artifact_kind_refuses_apply_and_delete_removes_every_version(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id)
        name = f"{turn.conversation_id.hex[:8]}-report-txt"
        ctx, _ = await _workspace_context(turn, tmp_path)
        await ctx.sandbox.bash("printf 'v1' > report.txt")
        await _text(tools, "share_file", ctx, files=[{"file_path": "report.txt"}])
        await ctx.sandbox.bash("printf 'v2' > report.txt")
        await _text(tools, "share_file", ctx, files=[{"file_path": "report.txt"}])
        async with workspace_tx() as connection:
            blob_keys = (
                (
                    await connection.execute(
                        sa.select(tables.shared_artifact.c.blob_key).where(
                            tables.shared_artifact.c.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(blob_keys) == 2
        for blob_key in blob_keys:
            assert await ctx.blob.exists(blob_key)

        apply_tool = tools["object_apply"]
        manifest = yaml.safe_dump(
            {
                "kind": ARTIFACT_KIND,
                "name": name,
                "spec": {"filename": "report.txt", "media_type": "text/plain"},
            }
        )
        with pytest.raises(VerbNotSupported, match="share_file"):
            with agent(turn.agent_id):
                await apply_tool.handler(
                    ctx,
                    apply_tool.input_model.model_validate(
                        {"user_description": OBJECT_NARRATION, "manifest": manifest}
                    ),
                )

        deleted = json.loads(
            await _agent_text(
                turn.agent_id, tools, "object_delete", ctx, kind=ARTIFACT_KIND, name=name
            )
        )
        assert deleted["spec"]["filename"] == "report.txt"
        listing = json.loads(
            await _agent_text(turn.agent_id, tools, "object_list", ctx, kind=ARTIFACT_KIND)
        )
        assert listing["objects"] == []
        for blob_key in blob_keys:
            assert not await ctx.blob.exists(blob_key)

        delete_tool = tools["object_delete"]
        with pytest.raises(UnknownObject):
            with agent(turn.agent_id):
                await delete_tool.handler(
                    ctx,
                    delete_tool.input_model.model_validate(
                        {"user_description": OBJECT_NARRATION, "kind": ARTIFACT_KIND, "name": name}
                    ),
                )


async def test_artifact_over_the_copy_bound_reports_no_workspace_path(db: None) -> None:
    """An oversize share renders spec and status without touching the sandbox or the blob store —
    the untouched carrier proves the copy is skipped, and with no deploy secret the status link is
    null too."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id)
        blob_key = f"artifacts/{uuid4()}/huge.bin"
        await _shared_artifact_row(turn, blob_key, "huge.bin", MATERIALIZE_MAX_BYTES + 1)
        ctx = _tool_context(workspace_id)

        fetched = yaml.safe_load(
            await _agent_text(
                turn.agent_id,
                tools,
                "object_get",
                ctx,
                kind=ARTIFACT_KIND,
                name=f"{turn.conversation_id.hex[:8]}-huge-bin",
            )
        )
        assert fetched["spec"]["filename"] == "huge.bin"
        assert fetched["status"]["workspace_path"] is None
        assert fetched["status"]["download_url"] is None


async def test_artifact_with_missing_bytes_fails_loud_on_get(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id)
        await _shared_artifact_row(turn, f"artifacts/{uuid4()}/gone.txt", "gone.txt", 5)
        ctx = replace(
            _tool_context(workspace_id),
            blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
        )

        get_tool = tools["object_get"]
        with pytest.raises(ValueError, match="no stored bytes"):
            with agent(turn.agent_id):
                await get_tool.handler(
                    ctx,
                    get_tool.input_model.model_validate(
                        {
                            "user_description": OBJECT_NARRATION,
                            "kind": ARTIFACT_KIND,
                            "name": f"{turn.conversation_id.hex[:8]}-gone-txt",
                        }
                    ),
                )


async def test_artifact_status_refuses_audience_narrowing_during_blob_read(
    db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        bob = await _member(workspace_id, JOINER_CREATED_AT)
        turn = await _turn_row(workspace_id, audience=SHARED_AUDIENCE)
        ctx, workspace_dir = await _workspace_context(turn, tmp_path)
        blob_key = f"artifacts/{uuid4()}/secret.txt"
        await ctx.blob.put(blob_key, b"shared until narrowed")
        await _shared_artifact_row(turn, blob_key, "secret.txt", len(b"shared until narrowed"))
        real_get = FilesystemBlobStore.get

        async def narrow_during_read(store: FilesystemBlobStore, key: str) -> bytes:
            data = await real_get(store, key)
            await _narrow_conversation(turn.conversation_id, bob)
            return data

        monkeypatch.setattr(FilesystemBlobStore, "get", narrow_during_read)
        with agent(turn.agent_id), pytest.raises(UnknownObject):
            await ArtifactObjects().status(
                ctx,
                f"{turn.conversation_id.hex[:8]}-secret-txt",
                expected_generation=None,
            )

    assert not (workspace_dir / "artifacts").exists()


async def test_artifact_delete_refuses_audience_narrowing_after_authorization(
    db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        bob = await _member(workspace_id, JOINER_CREATED_AT)
        turn = await _turn_row(workspace_id, audience=SHARED_AUDIENCE)
        ctx, _ = await _workspace_context(turn, tmp_path)
        blob_key = f"artifacts/{uuid4()}/secret.txt"
        await ctx.blob.put(blob_key, b"shared until narrowed")
        await _shared_artifact_row(turn, blob_key, "secret.txt", len(b"shared until narrowed"))
        real_find = ArtifactObjects._find

        async def narrow_after_find(
            store: ArtifactObjects,
            tool_ctx: ToolContext,
            name: str,
        ) -> tuple[sa.Row, ...] | None:
            shares = await real_find(store, tool_ctx, name)
            await _narrow_conversation(turn.conversation_id, bob)
            return shares

        monkeypatch.setattr(ArtifactObjects, "_find", narrow_after_find)
        with agent(turn.agent_id), pytest.raises(ValueError, match="changed while deleting"):
            await ArtifactObjects().delete(
                ctx,
                f"{turn.conversation_id.hex[:8]}-secret-txt",
                expected_generation=None,
            )
        async with workspace_tx() as connection:
            row_exists = (
                await connection.execute(
                    sa.select(
                        sa.exists(
                            sa.select(tables.shared_artifact.c.blob_key).where(
                                tables.shared_artifact.c.blob_key == blob_key
                            )
                        )
                    )
                )
            ).scalar_one()

    assert row_exists
    with ws(workspace_id):
        assert await ctx.blob.exists(blob_key)


ROW_LOCK_HELD = 'could not obtain lock on row in relation "conversation"'
POSTGRES_ONLY_LOCK = "row locks are a PostgreSQL mechanism; sqlite queues writers on one slot"


@dataclass(frozen=True)
class _HoldsTheConversationRow:
    """Parks the artifact delete inside its transaction the instant its authorization check has
    read the conversation row, so a competing writer meets whatever lock that check took while the
    delete is still open. The delete's check is the only single-table conversation select it
    runs — `_groups` reads through a join — so the park never fires on the wrong statement."""

    connection: AsyncConnection
    reached: asyncio.Event
    release: asyncio.Event

    async def execute(self, statement: sa.Executable) -> sa.CursorResult:
        result = await self.connection.execute(statement)
        if isinstance(statement, sa.Select) and statement.get_final_froms() == [
            tables.conversation
        ]:
            self.reached.set()
            await self.release.wait()
        return result


async def test_artifact_delete_holds_the_conversation_row_against_a_concurrent_narrowing(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two live PostgreSQL backends: the delete takes the conversation row as it authorizes and
    keeps it to commit, so a narrowing writer cannot move the audience out from under an
    already-authorized delete. The competitor asks for the row with NOWAIT, so the contention is
    read at the instant it happens instead of waited on."""
    if not database_url.startswith("postgresql"):
        pytest.skip(POSTGRES_ONLY_LOCK)
    workspace_id = await _workspace()
    with ws(workspace_id):
        bob = await _member(workspace_id, JOINER_CREATED_AT)
        turn = await _turn_row(workspace_id, audience=SHARED_AUDIENCE)
        ctx, _ = await _workspace_context(turn, tmp_path)
        blob_key = f"artifacts/{uuid4()}/secret.txt"
        await ctx.blob.put(blob_key, b"shared until narrowed")
        await _shared_artifact_row(turn, blob_key, "secret.txt", len(b"shared until narrowed"))
        reached, release = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def park_on_the_conversation_row() -> AsyncIterator[_HoldsTheConversationRow]:
            async with workspace_tx() as connection:
                yield _HoldsTheConversationRow(connection, reached, release)

        monkeypatch.setattr(artifacts, "workspace_tx", park_on_the_conversation_row)
        with agent(turn.agent_id):
            deleting = asyncio.create_task(
                ArtifactObjects().delete(
                    ctx, f"{turn.conversation_id.hex[:8]}-secret-txt", expected_generation=None
                )
            )
            authorized = asyncio.create_task(reached.wait())
            await asyncio.wait((deleting, authorized), return_when=asyncio.FIRST_COMPLETED)
            try:
                assert reached.is_set()
                with pytest.raises(sa.exc.DBAPIError, match=ROW_LOCK_HELD):
                    async with workspace_tx() as connection:
                        await connection.execute(
                            sa.select(tables.conversation.c.id)
                            .where(tables.conversation.c.id == turn.conversation_id)
                            .with_for_update(nowait=True)
                        )
                        await connection.execute(
                            sa.update(tables.conversation)
                            .where(tables.conversation.c.id == turn.conversation_id)
                            .values(
                                member_id=bob,
                                audience=str(conversation_audience(bob)),
                                updated_at=sa.func.now(),
                            )
                        )
            finally:
                release.set()
                authorized.cancel()
                await deleting
        async with workspace_tx() as connection:
            audience = (
                await connection.execute(
                    sa.select(tables.conversation.c.audience).where(
                        tables.conversation.c.id == turn.conversation_id
                    )
                )
            ).scalar_one()
            remaining = (
                (
                    await connection.execute(
                        sa.select(tables.shared_artifact.c.blob_key).where(
                            tables.shared_artifact.c.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )

    assert audience == str(SHARED_AUDIENCE)
    assert list(remaining) == []
    with ws(workspace_id):
        assert not await ctx.blob.exists(blob_key)


async def test_artifact_delete_refuses_when_a_version_is_taken_out_from_under_it(
    db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delete removes the exact key set it read, so a version another deleter took first
    leaves that set short. The count mismatch aborts the transaction before any bytes are
    destroyed: the version the winner did not take keeps its row and its blob."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id, audience=SHARED_AUDIENCE)
        ctx, _ = await _workspace_context(turn, tmp_path)
        first_key = f"artifacts/{uuid4()}/report.txt"
        second_key = f"artifacts/{uuid4()}/report.txt"
        for key, body in ((first_key, b"v1"), (second_key, b"v2")):
            await ctx.blob.put(key, body)
            await _shared_artifact_row(turn, key, "report.txt", len(body))
        real_find = ArtifactObjects._find

        async def take_one_version(
            store: ArtifactObjects,
            tool_ctx: ToolContext,
            name: str,
        ) -> tuple[sa.Row, ...] | None:
            shares = await real_find(store, tool_ctx, name)
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.delete(tables.shared_artifact).where(
                        tables.shared_artifact.c.blob_key == first_key
                    )
                )
            return shares

        monkeypatch.setattr(ArtifactObjects, "_find", take_one_version)
        with agent(turn.agent_id), pytest.raises(ValueError, match="lost a version while deleting"):
            await ArtifactObjects().delete(
                ctx, f"{turn.conversation_id.hex[:8]}-report-txt", expected_generation=None
            )
        async with workspace_tx() as connection:
            remaining = (
                (
                    await connection.execute(
                        sa.select(tables.shared_artifact.c.blob_key).where(
                            tables.shared_artifact.c.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )

    assert list(remaining) == [second_key]
    with ws(workspace_id):
        assert await ctx.blob.exists(first_key)
        assert await ctx.blob.exists(second_key)


async def test_artifact_slug_collisions_list_under_distinct_names(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id)
        await _shared_artifact_row(turn, f"artifacts/{uuid4()}/a b.txt", "a b.txt", 1)
        await _shared_artifact_row(turn, f"artifacts/{uuid4()}/a_b.txt", "a_b.txt", 1)
        ctx = _tool_context(workspace_id)

        listing = json.loads(
            await _agent_text(turn.agent_id, tools, "object_list", ctx, kind=ARTIFACT_KIND)
        )
        names = [row["name"] for row in listing["objects"]]
        expected = artifact_object_names(
            [(turn.conversation_id, "a b.txt"), (turn.conversation_id, "a_b.txt")]
        )
        assert names == sorted(expected.values())
        assert len(set(names)) == 2


async def test_same_filename_across_conversations_stays_distinct(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn_a = await _turn_row(workspace_id)
        turn_b = await _turn_row(workspace_id, agent_id=turn_a.agent_id)
        await _shared_artifact_row(turn_a, f"artifacts/{uuid4()}/report.txt", "report.txt", 1)
        await _shared_artifact_row(turn_b, f"artifacts/{uuid4()}/report.txt", "report.txt", 1)
        ctx = _tool_context(workspace_id)

        listing = json.loads(
            await _agent_text(turn_a.agent_id, tools, "object_list", ctx, kind=ARTIFACT_KIND)
        )
        names = sorted(row["name"] for row in listing["objects"])
        assert names == sorted(
            f"{turn.conversation_id.hex[:8]}-report-txt" for turn in (turn_a, turn_b)
        )


LAUNCH_EXCHANGE = Conversation(
    seq=2,
    messages=(
        Message(role="user", content="remind me about the launch"),
        Message(
            role="assistant",
            content=(
                ToolUseBlock(id="t1", name="memory_search", input={"queries": ["launch"]}),
                TextBlock(text="the launch is march 3"),
            ),
        ),
    ),
)


async def test_conversation_get_materializes_the_durable_transcript(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        past = await _turn_row(workspace_id)
        reader = await _turn_row(workspace_id, agent_id=past.agent_id)
        ctx, workspace_dir = await _workspace_context(reader, tmp_path)
        await Transcript(blob=ctx.blob, conversation_id=past.conversation_id).write(LAUNCH_EXCHANGE)

        fetched = yaml.safe_load(
            await _agent_text(
                past.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(past.conversation_id),
            )
        )

    body = b"user: remind me about the launch\nassistant: the launch is march 3"
    assert fetched["status"] == {
        "messages": 2,
        "size_bytes": len(body),
        "workspace_path": f"transcripts/{past.conversation_id}.txt",
    }
    assert (workspace_dir / "transcripts" / f"{past.conversation_id}.txt").read_bytes() == body


async def test_conversation_transcript_keeps_member_and_agent_gates(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        member = await _member(workspace_id, ADMIN_CREATED_AT)
        other = await _member(workspace_id, JOINER_CREATED_AT)
        private = await _turn_row(workspace_id, member_id=member)
        reader = await _turn_row(workspace_id, agent_id=private.agent_id, member_id=other)
        ctx, workspace_dir = await _workspace_context(
            reader, tmp_path, audience=conversation_audience(other)
        )
        correct_reader = await _turn_row(workspace_id, agent_id=private.agent_id, member_id=member)
        correct_ctx = replace(ctx, turn=correct_reader, audience=conversation_audience(member))
        other_agent_reader = await _turn_row(workspace_id, member_id=member)
        other_agent_ctx = replace(
            ctx,
            turn=other_agent_reader,
            audience=conversation_audience(member),
        )
        await Transcript(blob=ctx.blob, conversation_id=private.conversation_id).write(
            LAUNCH_EXCHANGE
        )

        get_tool = tools["object_get"]
        for agent_id, hidden in (
            (private.agent_id, ctx),
            (other_agent_reader.agent_id, other_agent_ctx),
        ):
            with agent(agent_id):
                with pytest.raises(UnknownObject):
                    await get_tool.handler(
                        hidden,
                        get_tool.input_model.model_validate(
                            {
                                "user_description": OBJECT_NARRATION,
                                "kind": CONVERSATION_KIND,
                                "name": str(private.conversation_id),
                            }
                        ),
                    )
        assert not (workspace_dir / "transcripts").exists()
        fetched = yaml.safe_load(
            await _agent_text(
                private.agent_id,
                tools,
                "object_get",
                correct_ctx,
                kind=CONVERSATION_KIND,
                name=str(private.conversation_id),
            )
        )

    path = f"transcripts/{private.conversation_id}.txt"
    assert fetched["status"]["workspace_path"] == path
    assert (workspace_dir / path).is_file()


async def test_conversation_status_refuses_audience_narrowing_during_transcript_read(
    db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        bob = await _member(workspace_id, JOINER_CREATED_AT)
        past = await _turn_row(workspace_id, audience=SHARED_AUDIENCE)
        reader = await _turn_row(
            workspace_id,
            agent_id=past.agent_id,
            audience=SHARED_AUDIENCE,
        )
        ctx, workspace_dir = await _workspace_context(reader, tmp_path)
        await Transcript(blob=ctx.blob, conversation_id=past.conversation_id).write(LAUNCH_EXCHANGE)
        real_exchange = conversations.ConversationObjects._exchange

        async def narrow_during_read(
            store: conversations.ConversationObjects,
            tool_ctx: ToolContext,
            conversation_id: UUID,
        ) -> tuple[str, ...]:
            exchange = await real_exchange(store, tool_ctx, conversation_id)
            await _narrow_conversation(conversation_id, bob)
            return exchange

        monkeypatch.setattr(conversations.ConversationObjects, "_exchange", narrow_during_read)
        with agent(past.agent_id), pytest.raises(UnknownObject):
            await conversations.ConversationObjects().status(
                ctx,
                str(past.conversation_id),
                expected_generation=None,
            )

    assert not (workspace_dir / "transcripts").exists()


async def test_main_targets_child_conversations_and_artifacts_with_the_requesters_audience(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    room = room_audience("slack", "CRESEARCH")
    with ws(workspace_id):
        alice = await _member(workspace_id, ADMIN_CREATED_AT)
        bob = await _member(workspace_id, JOINER_CREATED_AT)
        main_agent = await _agent_row(workspace_id, name="assistant", is_main=True)
        child_agent = await _agent_row(workspace_id, name="research")
        alice_private = await _turn_row(
            workspace_id,
            agent_id=child_agent,
            member_id=alice,
        )
        bob_private = await _turn_row(
            workspace_id,
            agent_id=child_agent,
            member_id=bob,
        )
        room_turn = await _turn_row(
            workspace_id,
            agent_id=child_agent,
            audience=room,
        )
        reader = await _turn_row(
            workspace_id,
            agent_id=main_agent,
            audience=room,
        )
        ctx, workspace_dir = await _workspace_context(
            reader,
            tmp_path,
            audience=room,
        )
        alice_ctx = replace(ctx, speaker_member_id=alice)
        bob_ctx = replace(ctx, speaker_member_id=bob)
        await Transcript(blob=ctx.blob, conversation_id=alice_private.conversation_id).write(
            LAUNCH_EXCHANGE
        )
        await Transcript(blob=ctx.blob, conversation_id=room_turn.conversation_id).write(
            LAUNCH_EXCHANGE
        )

        async def add_artifact(turn: Turn, filename: str, body: bytes) -> str:
            blob_key = f"artifacts/{turn.id}/{filename}"
            await ctx.blob.put(blob_key, body)
            await _shared_artifact_row(turn, blob_key, filename, len(body))
            return f"{turn.conversation_id.hex[:8]}-{filename.replace('.', '-')}"

        alice_artifact = await add_artifact(
            alice_private,
            "alice.txt",
            b"private research",
        )
        bob_artifact = await add_artifact(
            bob_private,
            "bob.txt",
            b"bob research",
        )
        room_artifact = await add_artifact(
            room_turn,
            "room.txt",
            b"room research",
        )

        with agent(main_agent):
            conversations_listing = json.loads(
                await _text(
                    tools,
                    "object_list",
                    alice_ctx,
                    kind=CONVERSATION_KIND,
                    agent="research",
                )
            )
            conversation = yaml.safe_load(
                await _text(
                    tools,
                    "object_get",
                    alice_ctx,
                    kind=CONVERSATION_KIND,
                    name=str(alice_private.conversation_id),
                    agent="research",
                )
            )
            artifacts = json.loads(
                await _text(
                    tools,
                    "object_list",
                    alice_ctx,
                    kind=ARTIFACT_KIND,
                    agent="research",
                )
            )
            artifact = yaml.safe_load(
                await _text(
                    tools,
                    "object_get",
                    alice_ctx,
                    kind=ARTIFACT_KIND,
                    name=alice_artifact,
                    agent="research",
                )
            )
            bob_conversations = json.loads(
                await _text(
                    tools,
                    "object_list",
                    bob_ctx,
                    kind=CONVERSATION_KIND,
                    agent="research",
                )
            )
            bob_artifacts = json.loads(
                await _text(
                    tools,
                    "object_list",
                    bob_ctx,
                    kind=ARTIFACT_KIND,
                    agent="research",
                )
            )
            with pytest.raises(UnknownObject):
                await _text(
                    tools,
                    "object_get",
                    bob_ctx,
                    kind=ARTIFACT_KIND,
                    name=alice_artifact,
                    agent="research",
                )
            with pytest.raises(ValueError, match="rejects an agent target"):
                await _text(
                    tools,
                    "object_apply",
                    alice_ctx,
                    manifest=yaml.safe_dump(
                        {
                            "kind": ARTIFACT_KIND,
                            "name": alice_artifact,
                            "spec": {},
                        }
                    ),
                    agent="research",
                )
            deleted_artifact = json.loads(
                await _text(
                    tools,
                    "object_delete",
                    alice_ctx,
                    kind=ARTIFACT_KIND,
                    name=alice_artifact,
                    agent="research",
                )
            )
            with pytest.raises(UnknownObject):
                await _text(
                    tools,
                    "object_get",
                    alice_ctx,
                    kind=ARTIFACT_KIND,
                    name=alice_artifact,
                    agent="research",
                )

    assert conversation["agent"] == "research"
    assert conversation["status"]["workspace_path"] == (
        f"transcripts/{alice_private.conversation_id}.txt"
    )
    assert {row["name"] for row in conversations_listing["objects"]} == {
        str(alice_private.conversation_id),
        str(room_turn.conversation_id),
    }
    assert artifacts["agent"] == "research"
    assert {row["name"] for row in artifacts["objects"]} == {
        alice_artifact,
        room_artifact,
    }
    assert artifact["agent"] == "research"
    assert artifact["links"] == [
        {
            "relation": "created_in",
            "target": {
                "kind": "conversation",
                "name": str(alice_private.conversation_id),
                "agent": "research",
            },
        }
    ]
    artifact_path = workspace_dir / "artifacts" / alice_artifact / "alice.txt"
    assert artifact_path.read_bytes() == b"private research"
    assert deleted_artifact == {
        "kind": ARTIFACT_KIND,
        "name": alice_artifact,
        "deleted": True,
        "spec": {
            "filename": "alice.txt",
            "subject": "",
            "media_type": "application/octet-stream",
        },
        "agent": "research",
    }
    assert {row["name"] for row in bob_conversations["objects"]} == {
        str(bob_private.conversation_id),
        str(room_turn.conversation_id),
    }
    assert {row["name"] for row in bob_artifacts["objects"]} == {
        bob_artifact,
        room_artifact,
    }


@pytest.mark.parametrize("kind", [CONVERSATION_KIND, ARTIFACT_KIND])
async def test_conversation_and_artifact_targets_require_main_live_member_authority(
    db: None, tmp_path: Path, kind: str
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        member = await _member(workspace_id, ADMIN_CREATED_AT)
        main_agent = await _agent_row(workspace_id, name="assistant", is_main=True)
        child_agent = await _agent_row(workspace_id, name="research")
        main_turn = await _turn_row(workspace_id, agent_id=main_agent)
        child_turn = await _turn_row(workspace_id, agent_id=child_agent)
        main_ctx, _ = await _workspace_context(main_turn, tmp_path)
        main_ctx = replace(main_ctx, speaker_member_id=member)
        child_ctx = replace(main_ctx, turn=child_turn)
        with agent(child_agent), pytest.raises(ValueError, match="only the workspace main agent"):
            await _text(
                tools,
                "object_list",
                child_ctx,
                kind=kind,
                agent="assistant",
            )
        with agent(main_agent):
            with pytest.raises(ValueError, match="typed subagent"):
                await _text(
                    tools,
                    "object_list",
                    replace(
                        main_ctx,
                        turn=main_ctx.turn.model_copy(update={"subagent_profile": "research"}),
                    ),
                    kind=kind,
                    agent="research",
                )
            with pytest.raises(ValueError, match="exact live member-requested"):
                await _text(
                    tools,
                    "object_list",
                    replace(main_ctx, speaker_member_id=None),
                    kind=kind,
                    agent="research",
                )
            with pytest.raises(ValueError, match="no agent named"):
                await _text(
                    tools,
                    "object_list",
                    main_ctx,
                    kind=kind,
                    agent="missing",
                )


async def test_message_requester_reads_their_private_conversation_from_a_shared_turn(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        member = await _member(workspace_id, ADMIN_CREATED_AT)
        private = await _turn_row(workspace_id, member_id=member)
        shared = await _turn_row(
            workspace_id,
            agent_id=private.agent_id,
            audience=SHARED_AUDIENCE,
        )
        ctx, workspace_dir = await _workspace_context(shared, tmp_path)
        ctx = replace(ctx, speaker_member_id=member)
        await Transcript(blob=ctx.blob, conversation_id=private.conversation_id).write(
            LAUNCH_EXCHANGE
        )

        listing = yaml.safe_load(
            await _agent_text(
                private.agent_id,
                tools,
                "object_list",
                ctx,
                kind=CONVERSATION_KIND,
            )
        )
        fetched = yaml.safe_load(
            await _agent_text(
                private.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(private.conversation_id),
            )
        )

    assert str(private.conversation_id) in {row["name"] for row in listing["objects"]}
    path = f"transcripts/{private.conversation_id}.txt"
    assert fetched["status"]["workspace_path"] == path
    assert (workspace_dir / path).is_file()


async def test_private_turn_can_open_a_shared_conversation_transcript(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        member = await _member(workspace_id, ADMIN_CREATED_AT)
        shared = await _turn_row(workspace_id)
        reader = await _turn_row(workspace_id, agent_id=shared.agent_id, member_id=member)
        ctx, workspace_dir = await _workspace_context(
            reader, tmp_path, audience=conversation_audience(member)
        )
        await Transcript(blob=ctx.blob, conversation_id=shared.conversation_id).write(
            LAUNCH_EXCHANGE
        )

        fetched = yaml.safe_load(
            await _agent_text(
                shared.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(shared.conversation_id),
            )
        )

    assert fetched["spec"]["audience"] == "shared"
    path = f"transcripts/{shared.conversation_id}.txt"
    assert fetched["status"]["workspace_path"] == path
    assert (workspace_dir / path).is_file()


async def test_room_conversations_open_shared_and_same_room_transcripts(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    room = room_audience("slack", "CPRIVATE")
    other_room = room_audience("slack", "COTHER")
    foreign = foreign_room_audience("slack", "CCONNECT")
    with ws(workspace_id):
        shared = await _turn_row(workspace_id)
        same = await _turn_row(workspace_id, agent_id=shared.agent_id, audience=room)
        other = await _turn_row(workspace_id, agent_id=shared.agent_id, audience=other_room)
        sealed = await _turn_row(workspace_id, agent_id=shared.agent_id, audience=foreign)
        ctx, workspace_dir = await _workspace_context(same, tmp_path, audience=room)
        await Transcript(blob=ctx.blob, conversation_id=shared.conversation_id).write(
            LAUNCH_EXCHANGE
        )
        await Transcript(blob=ctx.blob, conversation_id=same.conversation_id).write(LAUNCH_EXCHANGE)

        listing = yaml.safe_load(
            await _agent_text(
                shared.agent_id,
                tools,
                "object_list",
                ctx,
                kind=CONVERSATION_KIND,
            )
        )
        shared_get = yaml.safe_load(
            await _agent_text(
                shared.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(shared.conversation_id),
            )
        )
        same_get = yaml.safe_load(
            await _agent_text(
                shared.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(same.conversation_id),
            )
        )

    names = {row["name"] for row in listing["objects"]}
    assert names == {str(shared.conversation_id), str(same.conversation_id)}
    assert str(other.conversation_id) not in names
    assert str(sealed.conversation_id) not in names
    assert shared_get["status"]["workspace_path"] == f"transcripts/{shared.conversation_id}.txt"
    assert same_get["spec"]["audience"] == str(room)
    assert same_get["status"]["workspace_path"] == f"transcripts/{same.conversation_id}.txt"
    assert (workspace_dir / "transcripts" / f"{shared.conversation_id}.txt").is_file()


async def test_explicit_room_request_opens_room_and_requester_private_conversations(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    room = room_audience("slack", "CPRIVATE")
    with ws(workspace_id):
        alice = await _member(workspace_id, ADMIN_CREATED_AT)
        bob = await _member(workspace_id, JOINER_CREATED_AT)
        room_turn = await _turn_row(workspace_id, audience=room)
        mine = await _turn_row(
            workspace_id,
            agent_id=room_turn.agent_id,
            member_id=alice,
        )
        theirs = await _turn_row(
            workspace_id,
            agent_id=room_turn.agent_id,
            member_id=bob,
        )
        ctx, _ = await _workspace_context(room_turn, tmp_path, audience=room)
        ctx = replace(ctx, speaker_member_id=alice)
        for turn in (room_turn, mine, theirs):
            await Transcript(blob=ctx.blob, conversation_id=turn.conversation_id).write(
                LAUNCH_EXCHANGE
            )

        listing = yaml.safe_load(
            await _agent_text(
                room_turn.agent_id,
                tools,
                "object_list",
                ctx,
                kind=CONVERSATION_KIND,
            )
        )
        room_get = yaml.safe_load(
            await _agent_text(
                room_turn.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(room_turn.conversation_id),
            )
        )
        mine_get = yaml.safe_load(
            await _agent_text(
                room_turn.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(mine.conversation_id),
            )
        )
        with pytest.raises(UnknownObject):
            await _agent_text(
                room_turn.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(theirs.conversation_id),
            )

    assert {row["name"] for row in listing["objects"]} == {
        str(room_turn.conversation_id),
        str(mine.conversation_id),
    }
    assert room_get["status"]["messages"] == 2
    assert mine_get["status"]["messages"] == 2


async def test_conversation_surface_label_lists_filters_and_orders(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        general = await _turn_row(workspace_id, surface_label="#general")
        zebra = await _turn_row(workspace_id, agent_id=general.agent_id, surface_label="#zebra")
        unlabelled = await _turn_row(workspace_id, agent_id=general.agent_id)
        ctx, _ = await _workspace_context(unlabelled, tmp_path)

        listing = yaml.safe_load(
            await _agent_text(general.agent_id, tools, "object_list", ctx, kind=CONVERSATION_KIND)
        )
        filtered = yaml.safe_load(
            await _agent_text(
                general.agent_id,
                tools,
                "object_list",
                ctx,
                kind=CONVERSATION_KIND,
                filters={"surface_label": "#general"},
            )
        )
        ordered = yaml.safe_load(
            await _agent_text(
                general.agent_id,
                tools,
                "object_list",
                ctx,
                kind=CONVERSATION_KIND,
                order_by="surface_label",
            )
        )
        fetched = yaml.safe_load(
            await _agent_text(
                general.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(general.conversation_id),
            )
        )
        bare = yaml.safe_load(
            await _agent_text(
                general.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(unlabelled.conversation_id),
            )
        )

    rows = {row["name"]: row for row in listing["objects"]}
    assert rows[str(general.conversation_id)]["surface_label"] == "#general"
    assert rows[str(general.conversation_id)]["summary"].startswith("#general on cli, created ")
    assert "surface_label" not in rows[str(unlabelled.conversation_id)]
    assert rows[str(unlabelled.conversation_id)]["summary"].startswith("cli conversation, created ")
    assert [row["name"] for row in filtered["objects"]] == [str(general.conversation_id)]
    assert [row["name"] for row in ordered["objects"]] == [
        str(unlabelled.conversation_id),
        str(general.conversation_id),
        str(zebra.conversation_id),
    ]
    assert fetched["spec"]["surface_label"] == "#general"
    assert bare["spec"]["surface_label"] is None


async def test_foreign_channel_label_never_reaches_the_workspace(db: None, tmp_path: Path) -> None:
    """A Slack Connect channel's name can name another organization, so the label rides the row's
    own audience gate: a workspace-shared caller never lists the sealed conversation at all."""
    workspace_id = await _workspace()
    tools = _object_tools()
    foreign = foreign_room_audience("slack", "CCONNECT")
    with ws(workspace_id):
        shared = await _turn_row(workspace_id, surface_label="#internal")
        sealed = await _turn_row(
            workspace_id,
            agent_id=shared.agent_id,
            audience=foreign,
            surface_label="#acme-partner",
        )
        ctx, _ = await _workspace_context(shared, tmp_path, speaker_member_id=uuid4())

        listing = yaml.safe_load(
            await _agent_text(shared.agent_id, tools, "object_list", ctx, kind=CONVERSATION_KIND)
        )
        with pytest.raises(UnknownObject):
            await _agent_text(
                shared.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(sealed.conversation_id),
            )

    assert [row["name"] for row in listing["objects"]] == [str(shared.conversation_id)]
    assert "#acme-partner" not in yaml.safe_dump(listing)


async def test_foreign_conversation_cannot_see_shared_metadata(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    foreign = foreign_room_audience("slack", "CCONNECT")
    with ws(workspace_id):
        shared = await _turn_row(workspace_id)
        sealed = await _turn_row(workspace_id, agent_id=shared.agent_id, audience=foreign)
        ctx, _ = await _workspace_context(sealed, tmp_path, audience=foreign)
        speaking, _ = await _workspace_context(
            sealed, tmp_path, audience=foreign, speaker_member_id=uuid4()
        )

        listing = yaml.safe_load(
            await _agent_text(
                shared.agent_id,
                tools,
                "object_list",
                ctx,
                kind=CONVERSATION_KIND,
            )
        )
        speaking_listing = yaml.safe_load(
            await _agent_text(
                shared.agent_id,
                tools,
                "object_list",
                speaking,
                kind=CONVERSATION_KIND,
            )
        )

    assert [row["name"] for row in listing["objects"]] == [str(sealed.conversation_id)]
    assert [row["name"] for row in speaking_listing["objects"]] == [str(sealed.conversation_id)]


async def test_foreign_conversation_cannot_list_shared_artifacts(db: None, tmp_path: Path) -> None:
    """A member speaking in an externally-shared channel lists that channel's artifacts and their
    own, never the workspace's — the seal does not lift because someone is speaking."""
    workspace_id = await _workspace()
    tools = _object_tools()
    foreign = foreign_room_audience("slack", "CCONNECT")
    with ws(workspace_id):
        shared_turn = await _turn_row(workspace_id)
        sealed_turn = await _turn_row(workspace_id, agent_id=shared_turn.agent_id, audience=foreign)
        ctx, _ = await _workspace_context(shared_turn, tmp_path)
        blob_key = f"artifacts/{shared_turn.id}/internal.txt"
        await ctx.blob.put(blob_key, b"internal numbers")
        await _shared_artifact_row(shared_turn, blob_key, "internal.txt", 16)

        speaking, _ = await _workspace_context(
            sealed_turn, tmp_path, audience=foreign, speaker_member_id=uuid4()
        )
        listing = json.loads(
            await _agent_text(
                shared_turn.agent_id, tools, "object_list", speaking, kind=ARTIFACT_KIND
            )
        )

    assert listing["objects"] == []


async def test_conversation_transcript_uses_the_canonical_id_path(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        past = await _turn_row(workspace_id)
        reader = await _turn_row(workspace_id, agent_id=past.agent_id)
        ctx, workspace_dir = await _workspace_context(reader, tmp_path)
        await Transcript(blob=ctx.blob, conversation_id=past.conversation_id).write(LAUNCH_EXCHANGE)

        fetched = yaml.safe_load(
            await _agent_text(
                past.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=past.conversation_id.hex,
            )
        )

    path = f"transcripts/{past.conversation_id}.txt"
    assert fetched["status"]["workspace_path"] == path
    assert (workspace_dir / path).is_file()


async def test_conversation_without_a_transcript_is_empty_and_corruption_fails_loud(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        empty = await _turn_row(workspace_id)
        broken = await _turn_row(workspace_id, agent_id=empty.agent_id)
        reader = await _turn_row(workspace_id, agent_id=empty.agent_id)
        ctx, workspace_dir = await _workspace_context(reader, tmp_path)

        fetched = yaml.safe_load(
            await _agent_text(
                empty.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(empty.conversation_id),
            )
        )
        assert fetched["status"] == {"messages": 0, "size_bytes": 0, "workspace_path": None}
        assert not (workspace_dir / "transcripts").exists()

        await ctx.blob.put(transcript_key(broken.conversation_id), b"not a transcript")
        with pytest.raises(ValueError, match="unreadable transcript"):
            await _agent_text(
                empty.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(broken.conversation_id),
            )


async def test_oversize_conversation_reports_size_without_writing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    monkeypatch.setattr(conversations, "MATERIALIZE_MAX_BYTES", 8)
    with ws(workspace_id):
        past = await _turn_row(workspace_id)
        reader = await _turn_row(workspace_id, agent_id=past.agent_id)
        ctx, workspace_dir = await _workspace_context(reader, tmp_path)
        await Transcript(blob=ctx.blob, conversation_id=past.conversation_id).write(LAUNCH_EXCHANGE)

        fetched = yaml.safe_load(
            await _agent_text(
                past.agent_id,
                tools,
                "object_get",
                ctx,
                kind=CONVERSATION_KIND,
                name=str(past.conversation_id),
            )
        )

    assert fetched["status"]["messages"] == 2
    assert fetched["status"]["size_bytes"] > 8
    assert fetched["status"]["workspace_path"] is None
    assert not (workspace_dir / "transcripts").exists()


async def test_artifact_reads_and_mutation_resolution_stay_inside_the_agent(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        home = await _turn_row(workspace_id)
        other = await _turn_row(workspace_id)
        await _shared_artifact_row(home, f"artifacts/{uuid4()}/home.txt", "home.txt", 1)
        await _shared_artifact_row(other, f"artifacts/{uuid4()}/other.txt", "other.txt", 1)
        home_name = f"{home.conversation_id.hex[:8]}-home-txt"
        other_name = f"{other.conversation_id.hex[:8]}-other-txt"
        ctx = _tool_context(workspace_id)

        with agent(home.agent_id):
            listing = json.loads(await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND))
            assert [row["name"] for row in listing["objects"]] == [home_name]

            get_tool = tools["object_get"]
            with pytest.raises(UnknownObject):
                await get_tool.handler(
                    ctx,
                    get_tool.input_model.model_validate(
                        {
                            "user_description": OBJECT_NARRATION,
                            "kind": ARTIFACT_KIND,
                            "name": other_name,
                        }
                    ),
                )

            delete_tool = tools["object_delete"]
            with pytest.raises(UnknownObject):
                await delete_tool.handler(
                    ctx,
                    delete_tool.input_model.model_validate(
                        {
                            "user_description": OBJECT_NARRATION,
                            "kind": ARTIFACT_KIND,
                            "name": other_name,
                        }
                    ),
                )

        with agent(other.agent_id):
            listing = json.loads(await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND))
            assert [row["name"] for row in listing["objects"]] == [other_name]


class _BootSpec(BaseModel):
    pass


class _AdminOnlyStore(MemberOwnedObjects[_BootSpec, ObjectOwner]):
    kind_name = "admin-only-test"
    mutate_gate = "mutate refused"
    delete_gate = "delete refused"

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]:
        return (OwnedRow(name="boot", summary="s", owner=ObjectOwner(member_id=None, shared=True)),)

    async def _detail(
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> ObjectDetail[_BootSpec] | None:
        return ObjectDetail(spec=_BootSpec(), created_at=None, updated_at=None)

    async def _status(
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> dict[str, JsonValue] | None:
        return {}

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: _BootSpec,
        old: _BootSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        raise AssertionError("gate must refuse before _apply_owned")

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        raise AssertionError("gate must refuse before _delete_owned")


async def test_shared_admin_only_row_refuses_a_speakerless_turn() -> None:
    """A shared admin-only row (owner member_id None, shared) is visible to everyone but mutable
    only by a workspace admin. A speakerless turn has acting member None, which must never
    collide with the None owner into 'owned' — the gate refuses both its mutation and its
    deletion, and the domain hooks are never reached."""
    store = _AdminOnlyStore()
    ctx = _tool_context(uuid4())
    with pytest.raises(AdminRequired, match="delete refused"):
        await store.delete(ctx, "boot", expected_generation=None)
    with pytest.raises(AdminRequired, match="mutate refused"):
        await store.apply(ctx, "boot", _BootSpec(), None, expected_generation=None)


async def test_an_ungenerated_kind_reads_a_vanished_row_as_absent_not_a_lost_race() -> None:
    """A kind whose store returns no generation is unfenced: the expectation an active verb carries
    is empty, so a row absent by the time the gate looks reads as absent — an empty status and a
    not-found delete — never as a replacement the verb lost a race to."""
    store = _AdminOnlyStore()
    ctx = _tool_context(uuid4())
    assert await store.status(ctx, "vanished", expected_generation=None) is None
    with pytest.raises(UnknownObject, match="vanished"):
        await store.delete(ctx, "vanished", expected_generation=None)


@dataclass
class _RaceRows:
    """One member-owned row as successive gate reads find it. `reads` is what each `_owned_rows`
    call sees — an owner, or None once a concurrent turn removed the row — and the last entry stands
    once the list runs out, so a verb that reads twice sees the same final state. `applied` and
    `deleted` record the owner every domain mutation the gate let through received."""

    reads: list[ObjectOwner | None]
    applied: list[ObjectOwner | None] = field(default_factory=list)
    deleted: list[ObjectOwner] = field(default_factory=list)


@dataclass(frozen=True)
class _RaceStore(MemberOwnedObjects[_BootSpec, ObjectOwner]):
    """A member-owned kind whose row set changes under the verb, so the base gate's fence, its
    visibility recheck, and the mutations it lets through are all exercised on the real class."""

    kind_name: ClassVar[str] = "race-test"
    mutate_gate: ClassVar[str] = "mutate refused"
    delete_gate: ClassVar[str] = "delete refused"

    race: _RaceRows

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]:
        owner = self.race.reads.pop(0) if len(self.race.reads) > 1 else self.race.reads[0]
        if owner is None:
            return ()
        return (OwnedRow(name="boot", summary="s", owner=owner),)

    async def _detail(
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> ObjectDetail[_BootSpec] | None:
        return ObjectDetail(spec=_BootSpec(), created_at=None, updated_at=None)

    async def _status(
        self, ctx: ToolContext, name: str, owner: ObjectOwner
    ) -> dict[str, JsonValue] | None:
        return {"read": True}

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: _BootSpec,
        old: _BootSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        self.race.applied.append(owner)

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        self.race.deleted.append(owner)


async def test_an_unfenced_kind_edits_through_a_row_created_or_removed_while_editing(
    db: None,
) -> None:
    """A kind whose store returns no generation is last-write-wins, so the empty expectation its
    read produced matches whatever the name holds when the gate looks: an apply that raced another
    turn's create reaches the kind's mutation with the row that turn left, and one that raced a
    delete reaches it with none. Whether that row is absorbed or refused is the kind's own domain
    rule — the gate never terminates the turn with a lost race the docs promise it cannot lose."""
    workspace_id = await _workspace()
    member = await _member(workspace_id, JOINER_CREATED_AT)
    ctx = _tool_context(workspace_id, speaker_member_id=member)
    owner = ObjectOwner(member_id=member, shared=False)

    with ws(workspace_id):
        created = _RaceStore(race=_RaceRows(reads=[owner]))
        await created.apply(ctx, "boot", _BootSpec(), None, expected_generation=None)
        removed = _RaceStore(race=_RaceRows(reads=[None]))
        await removed.apply(ctx, "boot", _BootSpec(), _BootSpec(), expected_generation=None)

    assert created.race.applied == [owner]
    assert removed.race.applied == [None]


@pytest.mark.parametrize(
    ("found", "expected", "refused"),
    (
        ("current", "current", False),
        ("current", None, True),
        (None, "current", True),
        ("other", "current", True),
    ),
    ids=("unchanged", "created-while-active", "removed-while-active", "replaced-while-active"),
)
async def test_a_generated_kind_fences_every_verb_on_the_row_its_read_saw(
    db: None,
    found: str | None,
    expected: str | None,
    refused: bool,
) -> None:
    """A kind handing up `GeneratedObjectOwner` is fenced on that generation, so apply, status, and
    delete each refuse once the name holds a row its own read never saw — a replacement, a create
    that filled an absence, or a removal that emptied one — and reach the domain mutation only while
    the generation still matches."""
    workspace_id = await _workspace()
    member = await _member(workspace_id, JOINER_CREATED_AT)
    ctx = _tool_context(workspace_id, speaker_member_id=member)
    generations = {"current": uuid4(), "other": uuid4()}
    owner = (
        None
        if found is None
        else GeneratedObjectOwner(member_id=member, shared=False, generation=generations[found])
    )
    generation = None if expected is None else generations[expected]
    old = None if expected is None else _BootSpec()
    applying = _RaceStore(race=_RaceRows(reads=[owner]))
    reading = _RaceStore(race=_RaceRows(reads=[owner]))
    deleting = _RaceStore(race=_RaceRows(reads=[owner]))

    with ws(workspace_id):
        if refused:
            with pytest.raises(ValueError, match="changed while editing"):
                await applying.apply(ctx, "boot", _BootSpec(), old, expected_generation=generation)
            with pytest.raises(ValueError, match="changed while reading"):
                await reading.status(ctx, "boot", expected_generation=generation)
            with pytest.raises(ValueError, match="changed while deleting"):
                await deleting.delete(ctx, "boot", expected_generation=generation)
        else:
            await applying.apply(ctx, "boot", _BootSpec(), old, expected_generation=generation)
            status = await reading.status(ctx, "boot", expected_generation=generation)
            await deleting.delete(ctx, "boot", expected_generation=generation)
            assert status == {"read": True}

    assert applying.race.applied == ([] if refused else [owner])
    assert deleting.race.deleted == ([] if refused else [owner])


@pytest.mark.parametrize("after", ("removed", "hidden"))
async def test_a_status_read_rechecks_visibility_and_reports_a_removed_row_as_absent(
    db: None, after: str
) -> None:
    """The read the gate opens with is not the state the store's live read ran under, so it looks
    again: a row the caller may no longer see refuses instead of disclosing what was read, while a
    row simply gone reports an empty status — the unfenced kind's absent case, not a lost race."""
    workspace_id = await _workspace()
    member = await _member(workspace_id, JOINER_CREATED_AT)
    other = await _member(workspace_id, JOINER_CREATED_AT)
    ctx = _tool_context(workspace_id, speaker_member_id=member)
    owner = ObjectOwner(member_id=member, shared=True)
    landed = None if after == "removed" else ObjectOwner(member_id=other, shared=False)
    store = _RaceStore(race=_RaceRows(reads=[owner, landed]))

    with ws(workspace_id):
        if after == "removed":
            assert await store.status(ctx, "boot", expected_generation=None) is None
        else:
            with pytest.raises(UnknownObject, match="boot"):
                await store.status(ctx, "boot", expected_generation=None)


async def test_an_agent_write_refuses_a_model_the_deploy_does_not_serve(db: None) -> None:
    """A stored model the registry cannot answer is read at every later turn's setup, before any
    dispatch, so the agent fails on every surface and the repair turn fails the same way. The write
    is the last place a member can still be told, so it refuses there — on create and on update."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)
        good = AgentSpec(
            model="claude-opus-4-8",
            internet_access_allowed=False,
            reasoning="auto",
            prompt="be brief",
        )
        typo = good.model_copy(update={"model": "claude-opus-42"})
        with pytest.raises(ValueError, match="no model named"):
            await AgentObjects().apply(ctx, "typo-agent", typo, None, expected_generation=None)
        await AgentObjects().apply(ctx, "typo-agent", good, None, expected_generation=None)
        with pytest.raises(ValueError, match="no model named"):
            await AgentObjects().apply(ctx, "typo-agent", typo, good, expected_generation=None)
        async with workspace_tx() as connection:
            stored = (
                await connection.execute(
                    sa.select(tables.agent.c.model).where(tables.agent.c.name == "typo-agent")
                )
            ).scalar_one()
        assert stored == "claude-opus-4-8"


async def test_an_agent_write_refuses_off_reasoning_for_a_required_reasoning_model(
    db: None,
) -> None:
    workspace_id = await _workspace()
    fable = next(spec for spec in core_model_specs("", "") if spec.id == "claude-fable-5")
    with ws(workspace_id):
        owner = await _member(workspace_id, ADMIN_CREATED_AT)
        ctx = _tool_context(
            workspace_id,
            speaker_member_id=owner,
            model_specs={"claude-fable-5": fable},
            auto_model="claude-fable-5",
        )
        invalid = AgentSpec(
            model="claude-fable-5",
            internet_access_allowed=False,
            reasoning="off",
            prompt="be brief",
        )
        with pytest.raises(ValueError, match="requires reasoning"):
            await AgentObjects().apply(ctx, "fable-agent", invalid, None, expected_generation=None)
        valid = invalid.model_copy(update={"reasoning": "low"})
        await AgentObjects().apply(ctx, "fable-agent", valid, None, expected_generation=None)
        with pytest.raises(ValueError, match="requires reasoning"):
            await AgentObjects().apply(ctx, "fable-agent", invalid, valid, expected_generation=None)
        auto_invalid = invalid.model_copy(update={"model": "auto"})
        with pytest.raises(ValueError, match="requires reasoning"):
            await AgentObjects().apply(
                ctx, "auto-fable-agent", auto_invalid, None, expected_generation=None
            )
        await AgentObjects().apply(
            replace(ctx, auto_model="claude-opus-5"),
            "auto-opus-agent",
            auto_invalid,
            None,
            expected_generation=None,
        )
