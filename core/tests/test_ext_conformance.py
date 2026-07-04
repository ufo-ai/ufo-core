"""The extension seam's conformance probe: drive the installed sample through each public entry.

The sample is a real workspace member discovered via its `selfhost.extension` entry point, so this
file first proves discovery (`load_manifests` finds it) and then exercises every declared point —
tool, credential slot, job, route — through the narrowest public surface core uses, reading the
sample's own recorded rows back through `ScopedStore`. The negative cases ride along: an undeclared
slot is refused, and a second workspace can reach none of the first's rows. Breaking the sample
breaks this probe, and a Manifest field the sample stops registering breaks the conformance gate."""

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import selfhost_ext_sample as sample
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from selfhost.blob import FilesystemBlobStore
from selfhost.browser.backend import BuaBackend
from selfhost.config import (
    BlobConfig,
    BrowserConfig,
    Config,
    DatabaseConfig,
    HubConfig,
    SandboxConfig,
)
from selfhost.credentials import CredentialSlotUnset, CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import (
    ExtensionContext,
    MemoryAccess,
    ScopedStore,
    TrajectoryCorpus,
    UndeclaredCredentialSlot,
    context_for,
)
from selfhost.ext.loader import (
    index_backend,
    load_manifests,
    skill_registry,
    turn_subagents,
    turn_tools,
)
from selfhost.ext.manifest import CarrierSpec, Manifest
from selfhost.ext.surface import WRITEBACK_DELIVERED, WRITEBACK_PENDING, workspace_key
from selfhost.governance import prompt_digest
from selfhost.grants import GrantStore
from selfhost.hub import InProcessHub
from selfhost.jobs import JobRunner, bindings_from
from selfhost.loop.prompts.render import render_system_prompt
from selfhost.loop.subagents import SubagentRegistry, subagent_system_prompt
from selfhost.loop.transcript import Transcript
from selfhost.memory.chunk import Chunk, TextChunker
from selfhost.memory.embed import EMBED_DIM
from selfhost.memory.index import PgvectorIndex, SqliteFtsIndex, index_backend_for
from selfhost.memory.indexer import PageIndexer
from selfhost.memory.service import OWNER_KIND_MEMORY_ITEM, SHARED_SUBJECT, MemoryService
from selfhost.memory.sources import SyncDriver
from selfhost.models.interface import Message, ModelRequest, TextDelta
from selfhost.models.registry import model_registry
from selfhost.onboarding import run_onboarding_steps
from selfhost.sandbox.local import LocalCarrier
from selfhost.sandbox.proxy.rules import InjectionRule, MeterRule, derive_credential_rules
from selfhost.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn, Usage
from selfhost.serve import (
    _mount_ext_routes,
    _mount_surfaces,
    _select_browser,
    _select_carrier,
    _select_hub,
)
from selfhost.skills.runtime import mount_skill
from selfhost.tools.context import SpawnResult, ToolContext
from selfhost.transcript import Conversation, transcript_key

SANDBOX_UNTOUCHED = "the sample tool records through its store and must not reach the sandbox"


@dataclass(frozen=True)
class StubMemory:
    """Stand-in memory service for the conformance context: the sample tool records through its own
    store and never touches memory, so recall/commit are inert here."""

    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


def _sample_manifest() -> Manifest:
    found = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert found is not None, "sample extension not discovered via entry points — run `uv sync`"
    return found


def _credential_store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _grantable(workspace_id: UUID) -> tuple[UUID, UUID, UUID]:
    """A member, an agent, and their conversation — the foreign-key rows a recorded grant
    references, so the connector tool can authenticate the turn-agent's grant."""
    member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id, agent_id, conversation_id


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
    raise AssertionError("the sample tool must not spawn a subagent")


def _tool_context(workspace_id: UUID, ext: ExtensionContext, tmp_path: Path) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=StubMemory(),
        member_id=None,
        artifact_token_secret="",
        ext=ext,
    )


async def test_sample_is_discovered_via_its_entry_point() -> None:
    manifest = _sample_manifest()
    assert {tool.name for tool in manifest.tools} == {sample.TOOL_NAME, sample.NOTE_TOOL_NAME}
    assert {job.name for job in manifest.jobs} == {sample.JOB_NAME}
    assert {route.path for route in manifest.routes} == {sample.ROUTE_PATH}
    assert {slot.name for slot in manifest.credentials} == {sample.API_SLOT}
    assert {step.name for step in manifest.onboarding_steps} == {sample.ONBOARDING_NAME}
    assert {connector.oauth.provider for connector in manifest.connectors} == {
        sample.CONNECTOR_PROVIDER
    }
    assert {
        tool.name for connector in manifest.connectors for tool in connector.tools
    } == {sample.CONNECTOR_EXECUTE_TOOL_NAME}
    assert {section.name for section in manifest.prompt_sections} == {sample.SECTION_NAME}
    assert {profile.name for profile in manifest.subagents} == {sample.SUBAGENT_NAME}
    assert {surface.name for surface in manifest.surfaces} == {sample.SURFACE_NAME}
    assert {source.backend for source in manifest.sources} == {sample.SOURCE_BACKEND}
    assert {spec.name for spec in manifest.indexes} == {sample.INDEX_BACKEND}
    assert {spec.backend for spec in manifest.hubs} == {sample.HUB_BACKEND}
    assert {spec.path.name for spec in manifest.skills} == {sample.SKILL_NAME}
    assert {spec.backend for spec in manifest.browsers} == {sample.BROWSER_BACKEND}
    assert {carrier.name for carrier in manifest.carriers} == {sample.CARRIER_NAME}


def test_core_selects_a_manifest_contributed_hub() -> None:
    """The `hubs` seam end to end: core's boot-time selection knows only the in-process default, so
    resolving the sample's backend name proves the Manifest `hubs` point flowed into selection.
    Selecting a name no manifest registers, and two manifests claiming one name, both fail loud."""
    manifest = _sample_manifest()

    def _config(backend: str) -> Config:
        return Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
            blob=BlobConfig(backend="filesystem", root=Path()),
            hub=HubConfig(backend=backend),
        )

    assert isinstance(_select_hub(_config("in_process"), ()), InProcessHub)
    assert isinstance(_select_hub(_config(sample.HUB_BACKEND), (manifest,)), InProcessHub)
    with pytest.raises(RuntimeError, match="no extension registers it"):
        _select_hub(_config(sample.HUB_BACKEND), ())
    with pytest.raises(RuntimeError, match="two extensions register hub backend"):
        _select_hub(_config(sample.HUB_BACKEND), (manifest, manifest))


def test_core_selects_a_manifest_contributed_browser() -> None:
    """The `browsers` seam end to end: core's boot-time selection knows only the built-in `bua`
    default, so resolving the sample's backend name proves the Manifest `browsers` point flowed into
    selection. Selecting a name no manifest registers, and two manifests claiming one name, both
    fail loud; a named backend with no credential key set fails loud."""
    manifest = _sample_manifest()
    workspace_id = uuid4()
    store = _credential_store()

    def _config(backend: str) -> Config:
        return Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///dev.db"),
            blob=BlobConfig(backend="filesystem", root=Path()),
            browser=BrowserConfig(backend=backend),
        )

    assert isinstance(_select_browser(_config("bua"), (), workspace_id, None), BuaBackend)
    selected = _select_browser(_config(sample.BROWSER_BACKEND), (manifest,), workspace_id, store)
    assert isinstance(selected, sample.SampleBrowserBackend)
    with pytest.raises(RuntimeError, match="no extension registers it"):
        _select_browser(_config(sample.BROWSER_BACKEND), (), workspace_id, store)
    with pytest.raises(RuntimeError, match="two extensions register browser backend"):
        _select_browser(_config(sample.BROWSER_BACKEND), (manifest, manifest), workspace_id, store)
    with pytest.raises(RuntimeError, match="needs a credential key"):
        _select_browser(_config(sample.BROWSER_BACKEND), (manifest,), workspace_id, None)


async def test_sample_browser_backend_yields_a_drivable_surface() -> None:
    """The consumer half of the `browsers` seam through the probe: the registered backend yields a
    per-turn surface driven through the same protocol the browser tools call, and it answers — a
    real object exercised end to end, not a mock call-log."""
    surface = sample.SampleBrowserBackend().surface(None, None)
    reply = await surface.navigate({"url": "https://x.test"})
    assert reply == {
        "action": "navigate",
        "backend": sample.BROWSER_BACKEND,
        "args": {"url": "https://x.test"},
    }
    await surface.aclose()


def _carrier_config(backend: str) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///carrier.db"),
        blob=BlobConfig(backend="filesystem", root=Path("blobs")),
        sandbox=SandboxConfig(backend=backend),
    )


def test_config_backend_defaults_to_the_built_in_local_carrier() -> None:
    assert isinstance(_select_carrier(_carrier_config("local"), ()), LocalCarrier)


def test_config_backend_selects_a_manifest_contributed_carrier() -> None:
    """The carriers seam end to end: the sample registers a carrier through its Manifest, and with
    `[sandbox] backend` naming it `serve` builds exactly that carrier — a deploy swaps the sandbox
    backend to an extension's without core naming it."""
    manifest = _sample_manifest()
    carrier = _select_carrier(_carrier_config(sample.CARRIER_NAME), (manifest,))
    assert isinstance(carrier, sample.SampleCarrier)


def test_an_unregistered_backend_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="not a registered carrier"):
        _select_carrier(_carrier_config("nope"), ())


def test_a_carrier_colliding_with_a_built_in_fails_loud() -> None:
    collide = Manifest(
        name="collide",
        version="0",
        carriers=(CarrierSpec(name="local", factory=sample.SampleCarrier),),
    )
    with pytest.raises(RuntimeError, match="two carriers register backend"):
        _select_carrier(_carrier_config("local"), (collide,))


def test_pack_prompt_section_reaches_the_rendered_system_prompt() -> None:
    """The contribution seam end to end: the section the sample declares renders into the shell's
    `{{sections}}` slot beside the agent's own prompt, and the render carries a digest — the same
    tuple the loop builds from `manifest.prompt_sections` and hands the engine each turn."""
    manifest = _sample_manifest()
    sections = tuple((section.name, section.body) for section in manifest.prompt_sections)
    rendered = render_system_prompt("You are the workspace assistant.", sections)
    assert sample.SECTION_BODY in rendered.content
    assert "You are the workspace assistant." in rendered.content
    assert rendered.digest.startswith("sha256:")


async def test_sample_skill_parses_indexes_and_mounts_with_its_script() -> None:
    """The skills seam end to end through the probe: the skill the sample contributes parses into
    the registry the loader aggregates, renders into the `{{skill_index}}` the main prompt carries,
    and mounts into the sandbox under `.skills/<name>/` with its bundled script — every step the
    real `load_skill` path runs, minus the live-sandbox execution deferred to the Docker proofs."""
    manifest = _sample_manifest()
    registry = skill_registry((manifest,))

    assert sample.SKILL_NAME in dict(registry.index())
    prompt = render_system_prompt("You are the assistant.", (), skills=registry.index())
    assert sample.SKILL_NAME in prompt.content

    written: dict[str, bytes] = {}

    class _Recorder:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    for skill in registry.tree(sample.SKILL_NAME):
        await mount_skill(_Recorder(), skill)

    root = f"/workspace/.skills/{sample.SKILL_NAME}"
    assert f"name: {sample.SKILL_NAME}" in written[f"{root}/SKILL.md"].decode()
    assert sample.SKILL_SCRIPT_MARKER in written[f"{root}/{sample.SKILL_SCRIPT}"].decode()


def test_sample_subagent_profile_flows_through_the_loader_into_the_registry() -> None:
    manifest = _sample_manifest()
    registry = SubagentRegistry(turn_subagents((manifest,)))
    profile = registry.get(sample.SUBAGENT_NAME)
    assert profile.tool_names == (sample.TOOL_NAME,)
    assert "JSON" in subagent_system_prompt(profile)


async def test_sample_model_provider_is_selected_priced_and_streams(tmp_path: Path) -> None:
    """The models Manifest point end to end through the probe: the registry selects the sample's
    contributed backend for its model id, that client streams a real ModelEvent, and the registry
    prices the id against the contributed rate — core's direct providers still claim bare ids."""
    manifest = _sample_manifest()
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (manifest,))
    client = registry.client_for(sample.SAMPLE_MODEL)
    assert isinstance(client, sample.SampleModelClient)
    request = ModelRequest(
        model=sample.SAMPLE_MODEL,
        system="",
        messages=(Message(role="user", content="hi"),),
        max_tokens=16,
    )
    events = [event async for event in client.complete(request)]
    assert TextDelta(text=sample.SAMPLE_MODEL_REPLY) in events
    priced = registry.pricing.micro_usd(
        sample.SAMPLE_MODEL, Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    )
    assert priced == 6_000_000


async def test_tool_dispatches_with_its_scoped_context(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    tools, ext_by_tool = turn_tools((manifest,), workspace_id, _credential_store())
    tool = next(tool for tool in tools if tool.name == sample.TOOL_NAME)
    context = ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=StubMemory(),
        member_id=None,
        artifact_token_secret="",
        ext=ext_by_tool[tool.name],
    )
    args = tool.input_model.model_validate({"message": "conformance-echo"})
    result = await tool.handler(context, args)
    assert result.is_error is False
    scoped = ScopedStore(workspace_id=workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.TOOL_KEY) == {"message": "conformance-echo"}


async def test_extension_owns_a_table_through_its_own_migration(db: None, tmp_path: Path) -> None:
    """The migration seam end to end: the sample's own migration created `sample_ext_note`, so its
    note tool writes and reads that table through the extension's workspace-scoped transaction. Two
    workspaces keep separate rows — the extension's table is scoped exactly as core's are, so an
    extension owns a real table and reaches only its own workspace's rows."""
    manifest = _sample_manifest()
    first, second = await _workspace(), await _workspace()
    tools, ext_by_tool = turn_tools((manifest,), first, _credential_store())
    note = next(tool for tool in tools if tool.name == sample.NOTE_TOOL_NAME)

    write = await note.handler(
        _tool_context(first, ext_by_tool[note.name], tmp_path),
        note.input_model.model_validate({"text": "first note"}),
    )
    assert write.is_error is False
    assert write.content[0].text == "first note"

    _, other_ext = turn_tools((manifest,), second, _credential_store())
    await note.handler(
        _tool_context(second, other_ext[note.name], tmp_path),
        note.input_model.model_validate({"text": "second note"}),
    )

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sample.NOTE_TABLE.c.workspace_id, sample.NOTE_TABLE.c.note).order_by(
                    sample.NOTE_TABLE.c.note
                )
            )
        ).all()
    assert [(row.workspace_id, row.note) for row in rows] == [
        (first, "first note"),
        (second, "second note"),
    ]


async def test_connector_execute_tool_resolves_the_bound_account_without_the_sandbox(
    db: None, tmp_path: Path
) -> None:
    """The server-side-execution seam: the sample's execute connector tool resolves the turn-agent's
    bound connected-account id through `connector_account` and records it, never touching the
    sandbox (its carrier raises on any reach). The test reads the account back through the store."""
    workspace_id = await _workspace()
    member_id, agent_id, conversation_id = await _grantable(workspace_id)
    grants = GrantStore()
    await grants.record(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider=sample.CONNECTOR_PROVIDER,
        account_id=sample.CONNECTOR_ACCOUNT,
        host=sample.CONNECTOR_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    manifest = _sample_manifest()
    tools, ext_by_tool = turn_tools((manifest,), workspace_id, _credential_store())
    tool = next(tool for tool in tools if tool.name == sample.CONNECTOR_EXECUTE_TOOL_NAME)
    context = ToolContext(
        sandbox=SandboxSession(
            carrier=_UntouchedCarrier(),
            handle=SandboxHandle(conversation_id=conversation_id, container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=StubMemory(),
        member_id=member_id,
        artifact_token_secret="",
        grants=grants,
        ext=ext_by_tool[tool.name],
    )
    args = tool.input_model.model_validate({"tool_name": "sample_list"})
    result = await tool.handler(context, args)
    assert result.is_error is False
    assert result.content[0].text == sample.CONNECTOR_ACCOUNT
    scoped = ScopedStore(workspace_id=workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.CONNECTOR_EXECUTE_KEY) == {
        "account": sample.CONNECTOR_ACCOUNT,
        "tool_name": "sample_list",
    }


async def test_credential_slot_derives_its_injection_and_meter_rules(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    store = _credential_store()
    await store.put(workspace_id, sample.API_SLOT, "sk-real-sample")
    rules = await derive_credential_rules((manifest,), workspace_id, store)
    injection = next(rule for rule in rules if isinstance(rule, InjectionRule))
    assert injection == InjectionRule(
        host=sample.INJECTION_HOST,
        header=sample.INJECTION_HEADER,
        sentinel=sample.INJECTION_SENTINEL,
        real="sk-real-sample",
    )
    meter = next(rule for rule in rules if isinstance(rule, MeterRule))
    assert meter == MeterRule(host=sample.INJECTION_HOST, dimension=sample.INJECTION_DIMENSION)


async def test_undeclared_credential_slot_is_refused(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    declared = frozenset(slot.name for slot in manifest.credentials)
    context = context_for(workspace_id, manifest.name, declared, _credential_store())
    assert sample.UNDECLARED_SLOT not in declared
    with pytest.raises(UndeclaredCredentialSlot, match=sample.UNDECLARED_SLOT):
        await context.credentials.get(sample.UNDECLARED_SLOT)


async def test_context_confines_the_credential_and_memory_handles(db: None) -> None:
    """The credential and memory handles a context carries expose only their gated methods: no raw
    CredentialStore field to read an undeclared slot, no raw MemoryService to recall or commit under
    another member's subject. The same confinement holds whether the context is built for a
    job/route (`context_for`) or a tool (`turn_tools`)."""
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    declared = frozenset(slot.name for slot in manifest.credentials)
    _, ext_by_tool = turn_tools((manifest,), workspace_id, _credential_store(), StubMemory())
    for context in (
        context_for(workspace_id, manifest.name, declared, _credential_store(), StubMemory()),
        ext_by_tool[sample.TOOL_NAME],
    ):
        assert {name for name in dir(context.credentials) if not name.startswith("_")} == {
            "get",
            "workspace_id",
            "declared",
        }
        with pytest.raises(UndeclaredCredentialSlot, match=sample.UNDECLARED_SLOT):
            await context.credentials.get(sample.UNDECLARED_SLOT)
        assert isinstance(context.memory, MemoryAccess)
        assert {name for name in dir(context.memory) if not name.startswith("_")} == {
            "recall",
            "commit",
        }


def test_a_route_without_a_credential_key_fails_loud() -> None:
    manifest = _sample_manifest()
    with pytest.raises(RuntimeError, match="serves routes but no credential key"):
        _mount_ext_routes(FastAPI(), (manifest,), uuid4(), None, StubMemory())


async def test_job_fires_through_its_scoped_context(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    runner = JobRunner(
        workspace_id=workspace_id,
        credential_store=_credential_store(),
        bindings=bindings_from((manifest,), ()),
    )
    await runner.fire(f"{manifest.name}:{sample.JOB_NAME}")
    scoped = ScopedStore(workspace_id=workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.JOB_KEY) == {"ran": True}


async def test_onboarding_step_runs_through_its_scoped_context(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    await run_onboarding_steps((manifest,), workspace_id, _credential_store())
    scoped = ScopedStore(workspace_id=workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.ONBOARDING_KEY) == {"onboarded": True}


async def test_route_reaches_its_scoped_context(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    app = FastAPI()
    _mount_ext_routes(app, (manifest,), workspace_id, _credential_store(), StubMemory())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://serve") as client:
        response = await client.post(f"/ext/{sample.NAME}/{sample.ROUTE_PATH}", content="ping")
    assert response.status_code == 200
    assert response.text == "ping"
    scoped = ScopedStore(workspace_id=workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.ROUTE_KEY) == {"body": "ping"}


async def test_a_second_workspace_reaches_none_of_the_firsts_rows(db: None) -> None:
    first = await _workspace()
    second = await _workspace()
    manifest = _sample_manifest()
    await JobRunner(
        workspace_id=first,
        credential_store=_credential_store(),
        bindings=bindings_from((manifest,), ()),
    ).fire(f"{manifest.name}:{sample.JOB_NAME}")
    store = _credential_store()
    await store.put(first, sample.API_SLOT, "sk-first")

    assert await ScopedStore(workspace_id=second, extension=sample.NAME).get(sample.JOB_KEY) is None
    with pytest.raises(CredentialSlotUnset):
        await store.get(second, sample.API_SLOT)

    assert await ScopedStore(workspace_id=first, extension=sample.NAME).get(sample.JOB_KEY) == {
        "ran": True
    }


SEED_PROMPT = "You are helpful."


async def _seed_trajectory(
    workspace_id: UUID, blob: FilesystemBlobStore, *, corrupt: bool = False
) -> UUID:
    """A real agent, conversation, terminal turn, and durable transcript — the corpus the trajectory
    read enumerates and the target the governed proposal is pinned against. `corrupt` lands
    undecodable bytes at the transcript key instead, to prove one bad transcript is skipped."""
    agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=agent_id.hex,
                prompt=SEED_PROMPT,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=str(conversation_id),
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
                status="done",
                inbound="hi",
                terminal={"status": "done", "text": "hello", "model": "claude-opus-4-8"},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    if corrupt:
        await blob.put(transcript_key(conversation_id), b"\x00 not a transcript")
    else:
        await Transcript(blob=blob, conversation_id=conversation_id).write(
            Conversation(
                seq=2,
                messages=(
                    Message(role="user", content="hi"),
                    Message(role="assistant", content="hello"),
                ),
            )
        )
    return agent_id


async def test_job_reads_trajectories_and_opens_a_governed_proposal(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    blob = FilesystemBlobStore(root=tmp_path)
    agent_id = await _seed_trajectory(workspace_id, blob)
    await JobRunner(
        workspace_id=workspace_id,
        credential_store=_credential_store(),
        bindings=bindings_from((manifest,), ()),
        blob=blob,
    ).fire(f"{manifest.name}:{sample.JOB_NAME}")

    scoped = ScopedStore(workspace_id=workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.TRAJECTORY_KEY) == {"count": 1}
    assert await scoped.get(sample.PROPOSAL_KEY) is not None

    async with workspace_tx() as connection:
        proposal = (
            await connection.execute(
                sa.select(
                    tables.proposal.c.extension,
                    tables.proposal.c.agent_id,
                    tables.proposal.c.status,
                    tables.proposal.c.from_digest,
                    tables.proposal.c.body,
                ).where(tables.proposal.c.workspace_id == workspace_id)
            )
        ).one()
        prompt = (
            await connection.execute(
                sa.select(tables.agent.c.prompt).where(tables.agent.c.id == agent_id)
            )
        ).scalar_one()
    assert proposal.extension == sample.NAME
    assert proposal.agent_id == agent_id
    assert proposal.status == "pending"
    assert proposal.from_digest == prompt_digest(SEED_PROMPT)
    assert proposal.body == {"prompt": SEED_PROMPT + sample.PROPOSAL_SUFFIX}
    assert prompt == SEED_PROMPT


async def test_job_context_confines_blob_to_a_workspace_scoped_trajectory_read(
    db: None, tmp_path: Path
) -> None:
    """The context a job receives carries no raw blob handle — only a workspace-scoped, read-only
    TrajectoryCorpus. It cannot get or put an arbitrary key (another workspace's transcript, an
    artifact), and its trajectory read returns only this workspace's conversations."""
    first = await _workspace()
    second = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    first_agent = await _seed_trajectory(first, blob)
    await _seed_trajectory(second, blob)
    await blob.put("artifacts/leak/report.txt", b"private")

    context = context_for(first, sample.NAME, frozenset(), _credential_store(), None, blob)

    assert "blob" not in {field.name for field in fields(context)}
    assert isinstance(context.corpus, TrajectoryCorpus)
    assert {name for name in dir(context.corpus) if not name.startswith("_")} == {
        "trajectories",
        "workspace_id",
    }

    trajectories = await context.trajectories()
    assert len(trajectories) == 1
    assert trajectories[0].agent_id == first_agent


async def test_a_corrupt_transcript_is_skipped_not_aborting_the_corpus(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    good_agent = await _seed_trajectory(workspace_id, blob)
    await _seed_trajectory(workspace_id, blob, corrupt=True)

    context = context_for(workspace_id, sample.NAME, frozenset(), _credential_store(), None, blob)
    trajectories = await context.trajectories()
    assert len(trajectories) == 1
    assert trajectories[0].agent_id == good_agent


@dataclass
class _StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


async def _surface_workspace() -> tuple[UUID, UUID, str]:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    email = "member@x.test"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id, email


async def test_sample_surface_admits_links_streams_and_delivers(db: None, tmp_path: Path) -> None:
    """The surface seam end to end through the probe: an inbound event admits a turn, links a
    surface identity, and streams an inbound file into the workspace; then the writeback poller
    delivers the terminal turn and streams a shared file back out — every step read through the
    durable rows and blobs core wrote, never a mock."""
    workspace_id, member_id, email = await _surface_workspace()
    manifest = _sample_manifest()
    blob = FilesystemBlobStore(root=tmp_path)
    dbos = _StubDbos()
    app = FastAPI()
    _mount_surfaces(
        app, (manifest,), workspace_id, _credential_store(), blob, dbos, "", None
    )
    body = json.dumps(
        {"external_id": "ext-1", "email": email, "message": "hello", "inbound_text": "note!"}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        response = await client.post(f"/surface/{sample.SURFACE_NAME}", content=body)
    assert response.status_code == 200
    turn_id = UUID(response.json()["turn_id"])
    conversation_id = UUID(response.json()["conversation_id"])

    assert dbos.enqueued == [str(turn_id)]
    async with workspace_tx() as connection:
        turn = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.inbound).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == sample.SURFACE_NAME,
                    tables.surface_identity.c.external_id == "ext-1",
                )
            )
        ).one()
        writeback_status = (
            await connection.execute(
                sa.select(tables.writeback.c.status).where(tables.writeback.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert turn.status == "queued"
    assert "note!" in turn.inbound or turn.inbound == "hello"
    assert linked.member_id == member_id
    assert writeback_status == WRITEBACK_PENDING
    inbound = await blob.get(workspace_key(conversation_id, sample.SURFACE_INBOX_REL))
    assert inbound == b"note!"

    await blob.put("artifacts/z/out.txt", b"shared-bytes")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(status="done", terminal={"status": "done", "text": "done!"})
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key="artifacts/z/out.txt",
                workspace_id=workspace_id,
                filename="out.txt",
                subject=None,
                media_type="text/plain",
                size_bytes=12,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await app.state.writeback_poller.drain()
    async with workspace_tx() as connection:
        delivered = (
            await connection.execute(
                sa.select(tables.writeback.c.status, tables.writeback.c.reply_ref).where(
                    tables.writeback.c.turn_id == turn_id
                )
            )
        ).one()
    assert delivered.status == WRITEBACK_DELIVERED
    assert delivered.reply_ref == sample.SURFACE_POST_REF
    round_tripped = await blob.get(f"{sample.SURFACE_DELIVERED_PREFIX}/{turn_id}/out.txt")
    assert round_tripped == b"shared-bytes"


def _vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class _StubEmbed:
    """Deterministic stand-in EmbedClient the page indexer embeds through and search queries
    through; the test asserts the recalled SourceMatch, never this stand-in."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


async def test_sample_source_syncs_a_page_recallable_through_memory(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The source seam end to end through the sample: onboarding registers a source row via the SDK,
    the core sync driver drives the sample's registered SourceBackend and lands one page (embedding
    deferred), the page index job derives its chunk, and the page is recalled through
    `search_sources` — proving register_source + the `sources` Manifest point + the runner + memory,
    all through public surfaces."""
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))
    await run_onboarding_steps((manifest,), workspace_id, _credential_store())

    embed = _StubEmbed(_vec((3, 1.0)))
    index = index_backend_for(database_url, embed)
    blob = FilesystemBlobStore(root=tmp_path)
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(
        backends={source.backend: source.source for source in manifest.sources},
        blob=blob,
        postgres=postgres,
    )
    page_indexer = PageIndexer(
        index=index, embed=embed, chunker=TextChunker(), blob=blob, postgres=postgres
    )
    service = MemoryService(index=index, embed=embed)

    await driver.run()
    async with workspace_tx() as connection:
        page = (
            await connection.execute(
                sa.select(tables.page.c.embedding_digest, tables.page.c.subject).where(
                    tables.page.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert page.subject == SHARED_SUBJECT
    assert page.embedding_digest is None

    await page_indexer.run()
    matches = await service.search_sources(
        "migrating orbital widget fleet", frozenset({SHARED_SUBJECT}), 5
    )
    assert matches and "orbital widget" in matches[0].text


async def test_core_selects_a_manifest_index_backend_by_name(db: None, database_url: str) -> None:
    """The `indexes` seam end to end through the probe: with `memory.index_backend` naming the
    sample's backend, core builds the manifest-contributed IndexBackend (not the dialect default)
    and it is driven through the protocol — upsert then retrieve. Unset falls back to the dialect
    default; an unknown name and a missing credential key each fail loud."""
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    embed = _StubEmbed(_vec((0, 1.0)))
    store = _credential_store()

    selected = index_backend(
        (manifest,), sample.INDEX_BACKEND, database_url, embed, workspace_id, store
    )
    assert isinstance(selected, sample.SampleIndex)
    await selected.upsert(
        (Chunk("d1", OWNER_KIND_MEMORY_ITEM, "m1", SHARED_SUBJECT, 0, "orbital", _vec((0, 1.0))),)
    )
    hits = await selected.lexical("orbital", frozenset({SHARED_SUBJECT}), OWNER_KIND_MEMORY_ITEM, 5)
    assert [hit.chunk_digest for hit in hits] == ["d1"]

    default = index_backend((manifest,), None, database_url, embed, workspace_id, store)
    assert isinstance(default, (PgvectorIndex, SqliteFtsIndex))

    with pytest.raises(RuntimeError, match="no extension registers"):
        index_backend((manifest,), "nonesuch", database_url, embed, workspace_id, store)
    with pytest.raises(RuntimeError, match="needs a credential key"):
        index_backend((manifest,), sample.INDEX_BACKEND, database_url, embed, workspace_id, None)
