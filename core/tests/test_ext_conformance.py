"""The extension seam's conformance probe: drive the installed sample through each public entry.

The sample is a real workspace member discovered via its `selfhost.extension` entry point, so this
file first proves discovery (`load_manifests` finds it) and then exercises every declared point —
tool, credential slot, job, route — through the narrowest public surface core uses, reading the
sample's own recorded rows back through `ScopedStore`. The negative cases ride along: an undeclared
slot is refused, and a second workspace can reach none of the first's rows. Breaking the sample
breaks this probe, and a Manifest field the sample stops registering breaks the conformance gate."""

from dataclasses import dataclass, fields
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
from selfhost.ext.context import (
    ScopedStore,
    TrajectoryCorpus,
    UndeclaredCredentialSlot,
    context_for,
)
from selfhost.ext.loader import load_manifests, turn_tools
from selfhost.ext.manifest import Manifest
from selfhost.governance import prompt_digest
from selfhost.jobs import JobRunner, bindings_from
from selfhost.loop.transcript import Transcript
from selfhost.models.interface import Message
from selfhost.onboarding import run_onboarding_steps
from selfhost.sandbox.proxy.rules import InjectionRule, MeterRule, derive_credential_rules
from selfhost.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.serve import _mount_ext_routes
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


async def test_sample_is_discovered_via_its_entry_point() -> None:
    manifest = _sample_manifest()
    assert {tool.name for tool in manifest.tools} == {sample.TOOL_NAME}
    assert {job.name for job in manifest.jobs} == {sample.JOB_NAME}
    assert {route.path for route in manifest.routes} == {sample.ROUTE_PATH}
    assert {slot.name for slot in manifest.credentials} == {sample.API_SLOT}
    assert {step.name for step in manifest.onboarding_steps} == {sample.ONBOARDING_NAME}


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
