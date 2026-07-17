"""The object seam's conformance probe: drive the sample's registered kinds through the five verbs.

The sample declares two kinds — `sample_widget` (full CRUD over its own `ext_store` rows, delete
owner-gated) and `sample_relic` (read-only, every mutation refused) — so these tests exercise the
whole surface through the real tool dispatch and read results back through the sample's own store:
create/update/get/list/delete round-trip, keyset paging under `OBJECT_LIST_PAGE`, the envelope and
name-grammar refusals, spec validation naming its field, handler-raised `VerbNotSupported` and
`OwnerRequired`, and the boot gates (kind collision, spec-model gates) failing loud."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, SecretStr

from ufo.agents import AGENT_KIND
from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.loader import load_manifests, turn_tools, validate_ext_tools
from ufo.ext.manifest import Manifest
from ufo.governance import Governance, prompt_digest
from ufo.objects import (
    OBJECT_LIST_PAGE,
    BoundKind,
    InvalidManifest,
    InvalidName,
    ObjectKind,
    OwnerRequired,
    SpecValidationFailed,
    UnknownKind,
    UnknownObject,
    VerbNotSupported,
    object_registry,
)
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
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

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
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
        assert first["next_cursor"] == first["objects"][-1]["name"]

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
    def kind_of(spec_model: type[BaseModel]) -> Manifest:
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
                ),
            ),
        )

    with pytest.raises(ValueError, match='extra="forbid"'):
        validate_ext_tools((kind_of(_OpenSpec),), None)
    with pytest.raises(ValueError, match="secret-bearing"):
        validate_ext_tools((kind_of(_SecretSpec),), None)
    with pytest.raises(ValueError, match="JSON-representable"):
        validate_ext_tools((kind_of(_UnrenderableSpec),), None)


async def _agent_row(
    workspace_id: UUID,
    name: str = "assistant",
    prompt: str = "be brief",
    model: str = "claude-opus-4-8",
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
        assert fetched["spec"] == {"model": "claude-opus-4-8"}
        assert fetched["status"]["prompt"] == "be brief"
        assert fetched["status"]["prompt_digest"] == prompt_digest("be brief")

        manifest = yaml.safe_dump(
            {"kind": AGENT_KIND, "name": "assistant", "spec": {"model": "claude-fable-5"}}
        )
        applied = json.loads(await _text(tools, "object_apply", owner_ctx, manifest=manifest))
        assert applied["result"] == "updated"
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.agent.c.prompt, tables.agent.c.model).where(
                        tables.agent.c.workspace_id == workspace_id
                    )
                )
            ).one()
        assert (row.prompt, row.model) == ("be brief", "claude-fable-5")

        listing = json.loads(await _text(tools, "object_list", owner_ctx, kind=AGENT_KIND))
        assert [entry["name"] for entry in listing["objects"]] == ["assistant"]

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
                        "spec": {"prompt": "injected", "model": "claude-fable-5"},
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
                        "spec": {"model": "m"},
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
                {"kind": AGENT_KIND, "name": "assistant", "spec": {"model": "claude-fable-5"}}
            ),
        )
        await Governance(workspace_id=workspace_id, extension="probe").approve_proposal(
            ref.proposal_id, owner
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
