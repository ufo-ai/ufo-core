import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from selfhost_ext_index_default import DefaultIndex
from selfhost_ext_memory.store import recall_subjects
from selfhost_ext_web.manifest import manifest as web_manifest
from selfhost_ext_web.surface import CHAT_PAGE, SESSION_COOKIE, _sse

from selfhost.accounting import CORE_PRICING, record_egress_request, record_turn_usage
from selfhost.blob import FilesystemBlobStore
from selfhost.browser import SandboxCdpProvider
from selfhost.config import Config
from selfhost.db import workspace_tx
from selfhost.ext.loader import skill_registry
from selfhost.ext.manifest import ModelProviderSpec
from selfhost.hub import InProcessHub, SkillLoad, ToolCall
from selfhost.loop import queue as loop_queue
from selfhost.loop.subagents import SubagentRegistry
from selfhost.models.interface import ModelEvent, ModelRequest, TextDelta
from selfhost.models.registry import ModelRegistry
from selfhost.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import TerminalFrame, Usage
from selfhost.serve import _mount_surfaces
from selfhost.subjects import SHARED_SUBJECT, member_subject

SECRET = "artifact-signing-secret"
STREAM_TIMEOUT_SECONDS = 30


def test_sse_tags_tool_and_skill_activity_frames() -> None:
    tool = _sse("7", ToolCall(tool="bash", preview='{"command":"ls"}'))
    assert tool.startswith(b"id: 7\nevent: tool\ndata: ")
    assert json.loads(tool.split(b"data: ", 1)[1]) == {
        "tool": "bash",
        "preview": '{"command":"ls"}',
        "description": "",
    }
    skill = _sse("", SkillLoad(skill="demo"))
    assert skill.startswith(b"event: skill\ndata: ")
    assert json.loads(skill.split(b"data: ", 1)[1]) == {"skill": "demo"}


@dataclass(frozen=True)
class StandInModel:
    """Echoes the round count back so a web turn runs the full queue path without a provider."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="echo:")
        yield TextDelta(text=str(len(request.messages)))
        yield Usage(input_tokens=7, output_tokens=3)


STANDIN_REGISTRY = ModelRegistry(
    providers=(
        ModelProviderSpec(
            name="standin",
            matches=lambda model: True,
            client=lambda model: StandInModel(),
        ),
    ),
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


@dataclass(frozen=True)
class StandInCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


@dataclass(frozen=True)
class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


async def _seed_workspace() -> UUID:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _seed_member(workspace_id: UUID, email: str) -> tuple[UUID, str]:
    member_id = uuid4()
    token = secrets.token_hex(16)
    async with workspace_tx() as connection:
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
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface="cli",
                external_id=hashlib.sha256(token.encode()).hexdigest(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id, token


@pytest.fixture(scope="session")
def dbos_runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, InProcessHub, FilesystemBlobStore]]:
    config = dbos_launched
    hub = InProcessHub()
    blob = FilesystemBlobStore(root=config.blob.root)
    proxy = ProxyEndpoint(port=0, ca_cert="test-ca")
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            workspace_fs=None,
            hub=hub,
            carrier=StandInCarrier(),
            cdp_provider=SandboxCdpProvider(endpoint=None),
            search_provider=None,
            proxy=proxy,
            dbos=dbos_client,
            subagents=SubagentRegistry(()),
            manifests=(),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(embed=StubEmbed(), transaction=workspace_tx),
            embed=StubEmbed(),
            artifact_token_secret=SECRET,
        )
    )
    yield config, hub, blob
    dbos_client.destroy()
    loop_queue.reset_runtime()


@pytest.fixture
async def web(
    db: None,
    dbos_runtime: tuple[Config, InProcessHub, FilesystemBlobStore],
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    config, hub, blob = dbos_runtime
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id = await _seed_workspace()
    app = FastAPI()
    _mount_surfaces(app, (web_manifest(),), workspace_id, None, blob, hub, dbos_client, "", None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://web") as client:
        yield client, workspace_id
    dbos_client.destroy()


async def _consume(client: AsyncClient, token: str, turn_id: str) -> tuple[str, dict[str, object]]:
    deltas: list[str] = []
    event: str | None = None
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream(
            "GET",
            f"/surface/web/turns/{turn_id}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        ) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    if event == "terminal":
                        return "".join(deltas), data
                    if event == "cost":
                        continue
                    deltas.append(data["text"])
                elif not line:
                    event = None
    raise AssertionError("stream ended without a terminal frame")


async def test_web_turn_round_trip_admits_streams_and_links_identity(
    web: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    admitted = await client.post(
        "/surface/web/chat", content=b"hello", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]
    streamed, terminal = await _consume(client, token, turn_id)
    assert streamed == "echo:1"
    assert terminal["status"] == "done"
    digest = hashlib.sha256(token.encode()).hexdigest()
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == "web",
                    tables.surface_identity.c.external_id == digest,
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.surface, tables.conversation.c.member_id)
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.id == UUID(turn_id))
            )
        ).one()
        writeback = (
            await connection.execute(
                sa.select(tables.writeback.c.turn_id).where(
                    tables.writeback.c.turn_id == UUID(turn_id)
                )
            )
        ).one_or_none()
    assert linked.member_id == member_id
    assert conversation.surface == "web"
    assert conversation.member_id == member_id
    assert writeback is None


async def test_unknown_session_token_is_rejected(web: tuple[AsyncClient, UUID]) -> None:
    client, _workspace_id = web
    stranger = secrets.token_hex(16)
    denied = await client.post(
        "/surface/web/chat", content=b"hi", headers={"cookie": f"{SESSION_COOKIE}={stranger}"}
    )
    assert denied.status_code == 401
    missing = await client.post("/surface/web/chat", content=b"hi")
    assert missing.status_code == 401


async def test_two_web_members_get_isolated_subjects_and_cannot_cross(
    web: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = web
    member_a, token_a = await _seed_member(workspace_id, "a@example.com")
    member_b, token_b = await _seed_member(workspace_id, "b@example.com")
    turn_a = (
        await client.post(
            "/surface/web/chat", content=b"hi", headers={"cookie": f"{SESSION_COOKIE}={token_a}"}
        )
    ).json()["turn_id"]
    turn_b = (
        await client.post(
            "/surface/web/chat", content=b"hi", headers={"cookie": f"{SESSION_COOKIE}={token_b}"}
        )
    ).json()["turn_id"]
    await _consume(client, token_a, turn_a)
    await _consume(client, token_b, turn_b)
    async with workspace_tx() as connection:
        owners = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id).where(
                        tables.conversation.c.surface == "web"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(owners) == {member_a, member_b}
    assert recall_subjects(member_a) & recall_subjects(member_b) == frozenset({SHARED_SUBJECT})
    assert member_subject(member_a) not in recall_subjects(member_b)
    crossed = await client.get(
        f"/surface/web/turns/{turn_a}/stream", headers={"cookie": f"{SESSION_COOKIE}={token_b}"}
    )
    assert crossed.status_code == 403


async def test_web_spend_view_matches_ledger_sums(web: tuple[AsyncClient, UUID]) -> None:
    client, workspace_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    async with workspace_tx() as connection:
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
        conversation_id, turn_id = uuid4(), uuid4()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="web",
                queue_key=uuid4().hex,
                member_id=member_id,
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
                inbound="x",
                terminal=TerminalFrame(status="done").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1000, output_tokens=2000),
        )
        await record_egress_request(connection, workspace_id, turn_id)
    page = await client.get("/surface/web/spend", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert page.status_code == 200
    body = page.text
    assert "owner@example.com" in body
    assert "assistant" in body
    assert "egress" in body
    assert "$0.055000" in body


def test_chat_page_is_self_contained_and_binds_a_session_cookie() -> None:
    assert "<!doctype html>" in CHAT_PAGE
    assert "EventSource" in CHAT_PAGE
    assert "http://" not in CHAT_PAGE
    assert "https://" not in CHAT_PAGE
    assert "//cdn" not in CHAT_PAGE
