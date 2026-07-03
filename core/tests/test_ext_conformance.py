"""The extension seam's conformance probe: drive the installed sample through each public entry.

The sample is a real workspace member discovered via its `selfhost.extension` entry point, so this
file first proves discovery (`load_manifests` finds it) and then exercises every declared point —
tool, credential slot, job, route — through the narrowest public surface core uses, reading the
sample's own recorded rows back through `ScopedStore`. The negative cases ride along: an undeclared
slot is refused, and a second workspace can reach none of the first's rows. Breaking the sample
breaks this probe, and a Manifest field the sample stops registering breaks the conformance gate."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import selfhost_ext_sample as sample
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from selfhost.blob import FilesystemBlobStore
from selfhost.credentials import CredentialSlotUnset, CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ScopedStore, UndeclaredCredentialSlot, context_for
from selfhost.ext.loader import load_manifests, turn_tools
from selfhost.ext.manifest import Manifest
from selfhost.jobs import JobRunner, bindings_from
from selfhost.sandbox.proxy.rules import InjectionRule, derive_credential_rules
from selfhost.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.serve import _mount_ext_routes
from selfhost.tools.context import SpawnResult, ToolContext

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


class _UntouchedCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def route(self, handle: SandboxHandle, port: int) -> str:
        raise AssertionError(SANDBOX_UNTOUCHED)

    async def destroy(self, handle: SandboxHandle) -> None:
        raise AssertionError(SANDBOX_UNTOUCHED)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("the sample tool must not spawn a subagent")


async def test_sample_is_discovered_via_its_entry_point() -> None:
    manifest = _sample_manifest()
    assert {tool.name for tool in manifest.tools} == {sample.TOOL_NAME}
    assert {job.name for job in manifest.jobs} == {sample.JOB_NAME}
    assert {route.path for route in manifest.routes} == {sample.ROUTE_PATH}
    assert {slot.name for slot in manifest.credentials} == {sample.API_SLOT}


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
        ext=ext_by_tool[tool.name],
    )
    args = tool.input_model.model_validate({"message": "conformance-echo"})
    result = await tool.handler(context, args)
    assert result.is_error is False
    scoped = ScopedStore(workspace_id=workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.TOOL_KEY) == {"message": "conformance-echo"}


async def test_credential_slot_derives_its_injection_rule(db: None) -> None:
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


async def test_undeclared_credential_slot_is_refused(db: None) -> None:
    workspace_id = await _workspace()
    manifest = _sample_manifest()
    declared = frozenset(slot.name for slot in manifest.credentials)
    context = context_for(workspace_id, manifest.name, declared, _credential_store())
    assert sample.UNDECLARED_SLOT not in declared
    with pytest.raises(UndeclaredCredentialSlot, match=sample.UNDECLARED_SLOT):
        await context.credentials.get(sample.UNDECLARED_SLOT)


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
