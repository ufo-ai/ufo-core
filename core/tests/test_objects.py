"""The object seam's conformance probe: drive the sample's registered kinds through the five verbs.

The sample declares two kinds — `sample_widget` (full CRUD over its own `ext_store` rows, delete
owner-gated) and `sample_relic` (read-only, every mutation refused) — so these tests exercise the
whole surface through the real tool dispatch and read results back through the sample's own store:
create/update/get/list/delete round-trip, keyset paging under `OBJECT_LIST_PAGE`, the envelope and
name-grammar refusals, spec validation naming its field, handler-raised `VerbNotSupported` and
`OwnerRequired`, and the boot gates (kind collision, spec-model gates) failing loud."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, SecretStr
from ufo_ext_connectors.objects import CONNECTOR_OBJECT
from ufo_ext_scheduled_tasks.tools import SCHEDULED_TASK_OBJECT
from ufo_ext_sources.tools import SOURCE_OBJECT

from ufo.agents import AGENT_KIND
from ufo.artifact_token import verify_artifact_token
from ufo.artifacts import ARTIFACT_KIND, MATERIALIZE_MAX_BYTES, artifact_object_names
from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.ext.loader import load_manifests, turn_tools, validate_ext_tools
from ufo.ext.manifest import Manifest
from ufo.governance import Governance, prompt_digest
from ufo.objects import (
    OBJECT_LIST_PAGE,
    OBJECT_NAME_MAX_LENGTH,
    OBJECT_NAME_PATTERN,
    BoundKind,
    InvalidManifest,
    InvalidName,
    MemberOwnedObjects,
    ObjectKind,
    ObjectListQuery,
    ObjectOwner,
    ObjectRow,
    OwnedRow,
    OwnerRequired,
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
    MountSpec,
    ProxyEndpoint,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.schema import tables
from ufo.schema.records import Agent, AgentChange, Turn
from ufo.tools.context import SpawnResult, TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

SANDBOX_UNTOUCHED = "object verbs run against stores and must not reach the sandbox"
OWNER_CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
JOINER_CREATED_AT = datetime(2026, 7, 2, tzinfo=UTC)


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def destroy(self, handle: SandboxHandle) -> None:
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
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return member_id


def _tool_context(workspace_id: UUID, speaker_member_id: UUID | None = None) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=Path()),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience_member_id=None,
        artifact_token_secret="",
    )


def _object_tools() -> dict[str, ToolDef]:
    manifest = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert manifest is not None, "sample extension not discovered via entry points — run `uv sync`"
    tools, ext_by_tool = turn_tools(
        (manifest,), CredentialStore(fernet=Fernet(Fernet.generate_key()))
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
    result: ToolResult = await tool.handler(ctx, tool.input_model.model_validate(args))
    assert result.is_error is False
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def _widget_manifest(name: str, color: str = "teal", size: int = 1) -> str:
    return f"kind: {sample.WIDGET_KIND}\nname: {name}\nspec:\n  color: {color}\n  size: {size}\n"


async def test_widget_crud_round_trips_through_the_verbs(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, OWNER_CREATED_AT)
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
                    {"kind": sample.WIDGET_KIND, "filters": {"colour": "teal"}}
                ),
            )
        with pytest.raises(ValueError, match="unknown object list order field"):
            await list_tool.handler(
                ctx,
                list_tool.input_model.model_validate(
                    {"kind": sample.WIDGET_KIND, "order_by": "weight"}
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
        assert "status" not in fetched

        updated = json.loads(
            await _text(tools, "object_apply", ctx, manifest=_widget_manifest("anvil", color="red"))
        )
        assert updated["result"] == "updated"

        explained = json.loads(await _text(tools, "object_explain", ctx, kind=sample.WIDGET_KIND))
        assert "color" in explained["spec_schema"]["properties"]
        assert explained["guidance"] == sample.WIDGET_GUIDANCE

        deleted = json.loads(
            await _text(tools, "object_delete", ctx, kind=sample.WIDGET_KIND, name="anvil")
        )
        assert deleted["deleted"] is True
        assert deleted["spec"]["color"] == "red"

        get_tool = tools["object_get"]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                ctx,
                get_tool.input_model.model_validate({"kind": sample.WIDGET_KIND, "name": "anvil"}),
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

        apply_tool = tools["object_apply"]
        relic = f"kind: {sample.RELIC_KIND}\nname: {sample.RELIC_NAME}\nspec:\n  inscription: x\n"
        with pytest.raises(VerbNotSupported, match="read-only"):
            await apply_tool.handler(
                ctx, apply_tool.input_model.model_validate({"manifest": relic})
            )

        delete_tool = tools["object_delete"]
        with pytest.raises(VerbNotSupported, match="read-only"):
            await delete_tool.handler(
                ctx,
                delete_tool.input_model.model_validate(
                    {"kind": sample.RELIC_KIND, "name": sample.RELIC_NAME}
                ),
            )


async def test_widget_delete_gates_on_the_owner(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, OWNER_CREATED_AT)
        joiner = await _member(workspace_id, JOINER_CREATED_AT)
        owner_ctx = _tool_context(workspace_id, speaker_member_id=owner)
        await _text(tools, "object_apply", owner_ctx, manifest=_widget_manifest("guarded"))

        delete_tool = tools["object_delete"]
        args = delete_tool.input_model.model_validate(
            {"kind": sample.WIDGET_KIND, "name": "guarded"}
        )
        with pytest.raises(OwnerRequired):
            await delete_tool.handler(_tool_context(workspace_id), args)
        with pytest.raises(OwnerRequired):
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
            apply_tool.input_model.model_validate({"manifest": manifest}),
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
    assert isinstance(CONNECTOR_OBJECT.store, MemberOwnedObjects)
    assert isinstance(SOURCE_OBJECT.store, MemberOwnedObjects)
    assert isinstance(SCHEDULED_TASK_OBJECT.store, MemberOwnedObjects)


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
    with pytest.raises(ValueError, match="list fields are not spec fields"):
        validate_ext_tools((kind_of(sample.WidgetSpec, frozenset({"weight"})),), None)


async def _agent_row(
    workspace_id: UUID,
    name: str = "assistant",
    prompt: str = "be brief",
    model: str = "claude-opus-4-8",
    internet_access_allowed: bool = True,
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
                internet_access_allowed=internet_access_allowed,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def test_agent_kind_updates_model_owner_gated_and_shows_prompt_readonly(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, OWNER_CREATED_AT)
        joiner = await _member(workspace_id, JOINER_CREATED_AT)
        await _agent_row(workspace_id)
        owner_ctx = _tool_context(workspace_id, speaker_member_id=owner)

        fetched = yaml.safe_load(
            await _text(tools, "object_get", owner_ctx, kind=AGENT_KIND, name="assistant")
        )
        assert fetched["spec"] == {
            "model": "claude-opus-4-8",
            "internet_access_allowed": True,
        }
        assert fetched["status"]["prompt"] == "be brief"
        assert fetched["status"]["prompt_digest"] == prompt_digest("be brief")

        manifest = yaml.safe_dump(
            {
                "kind": AGENT_KIND,
                "name": "assistant",
                "spec": {
                    "model": "claude-fable-5",
                    "internet_access_allowed": False,
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
                    ).where(tables.agent.c.workspace_id == workspace_id)
                )
            ).one()
        assert (row.prompt, row.model, row.internet_access_allowed) == (
            "be brief",
            "claude-fable-5",
            False,
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
        args = apply_tool.input_model.model_validate({"manifest": manifest})
        with pytest.raises(OwnerRequired):
            await apply_tool.handler(_tool_context(workspace_id, speaker_member_id=joiner), args)
        with pytest.raises(OwnerRequired):
            await apply_tool.handler(_tool_context(workspace_id), args)

        prompt_write = apply_tool.input_model.model_validate(
            {
                "manifest": yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "assistant",
                        "spec": {
                            "prompt": "injected",
                            "model": "claude-fable-5",
                            "internet_access_allowed": False,
                        },
                    }
                )
            }
        )
        with pytest.raises(SpecValidationFailed, match="prompt"):
            await apply_tool.handler(owner_ctx, prompt_write)


async def test_agent_kind_refuses_create_and_delete(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, OWNER_CREATED_AT)
        await _agent_row(workspace_id)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)

        apply_tool = tools["object_apply"]
        create = apply_tool.input_model.model_validate(
            {
                "manifest": yaml.safe_dump(
                    {
                        "kind": AGENT_KIND,
                        "name": "second-agent",
                        "spec": {"model": "m", "internet_access_allowed": True},
                    }
                )
            }
        )
        with pytest.raises(VerbNotSupported, match="one agent per workspace"):
            await apply_tool.handler(ctx, create)

        delete_tool = tools["object_delete"]
        with pytest.raises(VerbNotSupported, match="cannot be deleted"):
            await delete_tool.handler(
                ctx,
                delete_tool.input_model.model_validate({"kind": AGENT_KIND, "name": "assistant"}),
            )


async def test_agent_apply_and_pending_proposal_write_disjoint_fields(db: None) -> None:
    """The disjoint-writers contract: the agent kind owns `model`, proposals own `prompt`, so an
    `object_apply` under a pending proposal cannot conflict with it — the approval still applies
    cleanly, and both writes land."""
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        owner = await _member(workspace_id, OWNER_CREATED_AT)
        agent_id = await _agent_row(workspace_id)
        ctx = _tool_context(workspace_id, speaker_member_id=owner)

        ref = await Governance(workspace_id=workspace_id, extension="probe").propose_change(
            AgentChange(
                agent_id=agent_id,
                new_prompt="proposed prompt",
                from_digest=prompt_digest("be brief"),
            )
        )
        await _text(
            tools,
            "object_apply",
            ctx,
            manifest=yaml.safe_dump(
                {
                    "kind": AGENT_KIND,
                    "name": "assistant",
                    "spec": {
                        "model": "claude-fable-5",
                        "internet_access_allowed": True,
                    },
                }
            ),
        )
        await Governance(workspace_id=workspace_id, extension="probe").approve_proposal(
            ref.proposal_id
        )
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.proposal.c.status).where(
                        tables.proposal.c.id == ref.proposal_id
                    )
                )
            ).scalar_one()
            row = (
                await connection.execute(
                    sa.select(tables.agent.c.prompt, tables.agent.c.model).where(
                        tables.agent.c.id == agent_id
                    )
                )
            ).one()
    assert status == "approved"
    assert (row.prompt, row.model) == ("proposed prompt", "claude-fable-5")


ARTIFACT_TEST_SECRET = "artifact-test-secret"


async def _turn_row(workspace_id: UUID) -> Turn:
    agent_id = await _agent_row(workspace_id, name=f"agent-{uuid4().hex[:8]}")
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=f"objects-{conversation_id.hex[:8]}",
                member_id=None,
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


async def _artifact_context(turn: Turn, tmp_path: Path) -> tuple[ToolContext, Path]:
    """A context whose sandbox is the real local carrier over a temp workspace and whose blob
    store is a real temp filesystem store — `share_file` and the artifact kind's workspace copy
    both run their true paths, no stand-ins."""
    workspace_dir = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=turn.conversation_id,
            image_ref="ufo-sandbox:latest",
            mount=MountSpec(kind="filesystem", host_path=str(workspace_dir)),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    ctx = ToolContext(
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience_member_id=None,
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


async def test_share_file_lands_an_artifact_object_and_get_copies_the_latest_back(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id)
        ctx, workspace_dir = await _artifact_context(turn, tmp_path)
        await ctx.sandbox.bash("printf 'quarterly numbers' > report.txt")

        name = f"{turn.conversation_id.hex[:8]}-report-txt"
        shared = json.loads(
            await _text(tools, "share_file", ctx, file_path="report.txt", subject="Q3 numbers")
        )
        assert shared["artifact"] == name

        listing = json.loads(await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND))
        assert [row["name"] for row in listing["objects"]] == [name]
        assert "report.txt" in listing["objects"][0]["summary"]
        assert listing["objects"][0]["filename"] == "report.txt"
        assert listing["objects"][0]["subject"] == "Q3 numbers"
        by_filename = json.loads(
            await _text(
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
            await _text(
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
            await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND, query="no-such-share")
        )
        assert filtered["objects"] == []
        folded = json.loads(
            await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND, query="q3 NUMBERS")
        )
        assert [row["name"] for row in folded["objects"]] == [name]

        fetched = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=ARTIFACT_KIND, name=name)
        )
        assert fetched["spec"] == {
            "filename": "report.txt",
            "media_type": "text/plain",
            "subject": "Q3 numbers",
        }
        status = fetched["status"]
        assert status["size_bytes"] == len(b"quarterly numbers")
        assert status["turn_id"] == str(turn.id)
        assert status["conversation_id"] == str(turn.conversation_id)
        assert status["versions"] == 1
        assert status["workspace_path"] == f"artifacts/{name}/report.txt"
        assert (workspace_dir / "artifacts" / name / "report.txt").read_bytes() == (
            b"quarterly numbers"
        )
        token = status["download_url"].split("token=", 1)[1]
        claims = verify_artifact_token(token, ARTIFACT_TEST_SECRET, datetime.now(UTC))
        assert claims.filename == "report.txt"

        await ctx.sandbox.bash("printf 'revised numbers' > report.txt")
        reshared = json.loads(await _text(tools, "share_file", ctx, file_path="report.txt"))
        assert reshared["artifact"] == name
        listing = json.loads(await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND))
        assert [row["name"] for row in listing["objects"]] == [name]
        refetched = yaml.safe_load(
            await _text(tools, "object_get", ctx, kind=ARTIFACT_KIND, name=name)
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
        ctx, _ = await _artifact_context(turn, tmp_path)
        await ctx.sandbox.bash("printf 'v1' > report.txt")
        await _text(tools, "share_file", ctx, file_path="report.txt")
        await ctx.sandbox.bash("printf 'v2' > report.txt")
        await _text(tools, "share_file", ctx, file_path="report.txt")
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
            await apply_tool.handler(
                ctx, apply_tool.input_model.model_validate({"manifest": manifest})
            )

        deleted = json.loads(
            await _text(tools, "object_delete", ctx, kind=ARTIFACT_KIND, name=name)
        )
        assert deleted["spec"]["filename"] == "report.txt"
        listing = json.loads(await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND))
        assert listing["objects"] == []
        for blob_key in blob_keys:
            assert not await ctx.blob.exists(blob_key)

        delete_tool = tools["object_delete"]
        with pytest.raises(UnknownObject):
            await delete_tool.handler(
                ctx,
                delete_tool.input_model.model_validate({"kind": ARTIFACT_KIND, "name": name}),
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
            await _text(
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
        ctx = replace(_tool_context(workspace_id), blob=FilesystemBlobStore(root=tmp_path))

        get_tool = tools["object_get"]
        with pytest.raises(ValueError, match="no stored bytes"):
            await get_tool.handler(
                ctx,
                get_tool.input_model.model_validate(
                    {
                        "kind": ARTIFACT_KIND,
                        "name": f"{turn.conversation_id.hex[:8]}-gone-txt",
                    }
                ),
            )


async def test_artifact_slug_collisions_list_under_distinct_names(db: None) -> None:
    workspace_id = await _workspace()
    tools = _object_tools()
    with ws(workspace_id):
        turn = await _turn_row(workspace_id)
        await _shared_artifact_row(turn, f"artifacts/{uuid4()}/a b.txt", "a b.txt", 1)
        await _shared_artifact_row(turn, f"artifacts/{uuid4()}/a_b.txt", "a_b.txt", 1)
        ctx = _tool_context(workspace_id)

        listing = json.loads(await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND))
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
        turn_b = await _turn_row(workspace_id)
        await _shared_artifact_row(turn_a, f"artifacts/{uuid4()}/report.txt", "report.txt", 1)
        await _shared_artifact_row(turn_b, f"artifacts/{uuid4()}/report.txt", "report.txt", 1)
        ctx = _tool_context(workspace_id)

        listing = json.loads(await _text(tools, "object_list", ctx, kind=ARTIFACT_KIND))
        names = sorted(row["name"] for row in listing["objects"])
        assert names == sorted(
            f"{turn.conversation_id.hex[:8]}-report-txt" for turn in (turn_a, turn_b)
        )


class _BootSpec(BaseModel):
    pass


class _OwnerOnlyStore(MemberOwnedObjects[_BootSpec]):
    kind_name = "owner-only-test"
    mutate_gate = "mutate refused"
    delete_gate = "delete refused"

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]:
        return (OwnedRow(name="boot", summary="s", owner=ObjectOwner(member_id=None, shared=True)),)

    async def _spec(self, ctx: ToolContext, name: str) -> _BootSpec | None:
        return _BootSpec()

    async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
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


async def test_shared_owner_only_row_refuses_a_non_owner_speakerless_turn() -> None:
    """A shared owner-only row (owner member_id None, shared) is visible to everyone but mutable
    only by the workspace owner. A speakerless turn has acting member None, which must never
    collide with the None owner into 'owned' — the gate refuses both its mutation and its
    deletion, and the domain hooks are never reached."""
    store = _OwnerOnlyStore()
    ctx = _tool_context(uuid4())
    with pytest.raises(OwnerRequired, match="delete refused"):
        await store.delete(ctx, "boot")
    with pytest.raises(OwnerRequired, match="mutate refused"):
        await store.apply(ctx, "boot", _BootSpec(), None)
