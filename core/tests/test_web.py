import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from selfhost_ext_index_default import DefaultIndex
from selfhost_ext_memory.store import recall_subjects

from selfhost.accounting import CORE_PRICING, record_egress_request, record_turn_usage
from selfhost.artifact_token import (
    ARTIFACT_KEY_PREFIX,
    ArtifactTokenError,
    mint_artifact_token,
    verify_artifact_token,
)
from selfhost.blob import FilesystemBlobStore
from selfhost.browser.backend import BuaBackend
from selfhost.browser.cdp_provider import BrowserCdpProviderChain
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
from selfhost.subjects import SHARED_SUBJECT, member_subject
from selfhost.surfaces.admission import Admission
from selfhost.surfaces.web import SESSION_COOKIE, WebSurface, _sse
from selfhost.surfaces.web import router as web_router

SECRET = "artifact-signing-secret"
STREAM_TIMEOUT_SECONDS = 30


def test_sse_tags_tool_and_skill_activity_frames() -> None:
    tool = _sse("7", ToolCall(tool="bash", preview='{"command":"ls"}'))
    assert tool.startswith(b"id: 7\nevent: tool\ndata: ")
    assert json.loads(tool.split(b"data: ", 1)[1]) == {
        "tool": "bash",
        "preview": '{"command":"ls"}',
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


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workflow_id: str) -> None:
        self.enqueued.append(workflow_id)


def _future() -> int:
    return int(datetime.now(UTC).timestamp()) + 3600


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
            hub=hub,
            carrier=StandInCarrier(),
            browser=BuaBackend(provider=BrowserCdpProviderChain(hosted=None, local=None)),
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
) -> AsyncIterator[AsyncClient]:
    config, hub, blob = dbos_runtime
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    app = FastAPI()
    app.state.web = WebSurface(
        admission=Admission(dbos=dbos_client), hub=hub, blob=blob, artifact_token_secret=SECRET
    )
    app.include_router(web_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://web") as client:
        yield client
    dbos_client.destroy()


@pytest.fixture
async def artifact_client(
    tmp_path,
) -> AsyncIterator[tuple[AsyncClient, FilesystemBlobStore]]:
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    app.state.web = WebSurface(
        admission=Admission(dbos=StubDbos()),
        hub=InProcessHub(),
        blob=blob,
        artifact_token_secret=SECRET,
    )
    app.include_router(web_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://web") as client:
        yield client, blob


async def _consume(client: AsyncClient, token: str, turn_id: str) -> tuple[str, dict[str, object]]:
    deltas: list[str] = []
    event: str | None = None
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream(
            "GET", f"/web/turns/{turn_id}/stream", headers={"cookie": f"{SESSION_COOKIE}={token}"}
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


def test_verify_artifact_token_accepts_valid_and_rejects_everything_else() -> None:
    now = datetime.now(UTC)
    key = "artifacts/abc123"
    token = mint_artifact_token(SECRET, key, "report.txt", int(now.timestamp()) + 100)
    claims = verify_artifact_token(token, SECRET, now)
    assert claims.blob_key == key
    assert claims.filename == "report.txt"
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(token, "wrong-secret", now)
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(token + "x", SECRET, now)
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(
            mint_artifact_token(SECRET, key, "", int(now.timestamp()) - 1), SECRET, now
        )
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(
            mint_artifact_token(
                SECRET, "conversations/c/messages.json.lz4", "", int(now.timestamp()) + 100
            ),
            SECRET,
            now,
        )
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(
            mint_artifact_token(
                SECRET,
                "artifacts/../conversations/c/messages.json.lz4",
                "",
                int(now.timestamp()) + 100,
            ),
            SECRET,
            now,
        )
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token("not-a-token", SECRET, now)
    with pytest.raises(ArtifactTokenError):
        verify_artifact_token(token, "", now)


async def test_artifact_download_serves_bytes_for_a_valid_token(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"artifacts/{uuid4()}"
    await blob.put(key, b"the shared bytes")
    token = mint_artifact_token(SECRET, key, "report.txt", _future())
    response = await client.get("/web/artifacts/download", params={"token": token})
    assert response.status_code == 200
    assert response.content == b"the shared bytes"
    assert "report.txt" in response.headers["content-disposition"]


async def test_artifact_download_escapes_special_filenames(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """A `"` would break the quoted `filename=` and a unicode name is not header-safe; both must
    ride out as a well-formed Content-Disposition the download still serves."""
    client, blob = artifact_client
    for filename in ('a"quote.txt', "résumé pièce.txt"):
        key = f"artifacts/{uuid4()}"
        await blob.put(key, b"the bytes")
        token = mint_artifact_token(SECRET, key, filename, _future())
        response = await client.get("/web/artifacts/download", params={"token": token})
        assert response.status_code == 200
        assert response.content == b"the bytes"
        parsed = Message()
        parsed["content-disposition"] = response.headers["content-disposition"]
        assert parsed.get_content_disposition() == "attachment"
        assert parsed.get_filename() == filename


async def test_artifact_download_rejects_missing_tampered_expired_and_out_of_namespace(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"artifacts/{uuid4()}"
    await blob.put(key, b"x")
    missing = await client.get("/web/artifacts/download")
    tampered = await client.get(
        "/web/artifacts/download",
        params={"token": mint_artifact_token(SECRET, key, "", _future()) + "z"},
    )
    expired = await client.get(
        "/web/artifacts/download",
        params={
            "token": mint_artifact_token(SECRET, key, "", int(datetime.now(UTC).timestamp()) - 10)
        },
    )
    outside = await client.get(
        "/web/artifacts/download",
        params={"token": mint_artifact_token(SECRET, "conversations/c/x", "", _future())},
    )
    assert missing.status_code == 401
    assert tampered.status_code == 403
    assert expired.status_code == 403
    assert outside.status_code == 403


async def test_download_endpoint_serves_a_minted_artifact(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """The download endpoint is the consumer of a share_file token: bytes under an artifacts key
    plus a valid token serve. `share_file` producing that token+blob is proven end-to-end against a
    real container in test_file_tools; here the token is minted directly to keep this non-Docker."""
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    await blob.put(key, b"produced report bytes")
    now = datetime.now(UTC)
    token = mint_artifact_token(SECRET, key, "report.txt", int(now.timestamp()) + 100)
    response = await client.get("/web/artifacts/download", params={"token": token})
    assert response.status_code == 200
    assert response.content == b"produced report bytes"
    assert "report.txt" in response.headers["content-disposition"]


async def test_web_turn_round_trip_admits_streams_and_links_identity(web: AsyncClient) -> None:
    workspace_id = await _seed_workspace()
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    admitted = await web.post(
        "/web/chat", content=b"hello", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]
    streamed, terminal = await _consume(web, token, turn_id)
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
    assert linked.member_id == member_id
    assert conversation.surface == "web"
    assert conversation.member_id == member_id


async def test_unknown_session_token_is_rejected(web: AsyncClient) -> None:
    await _seed_workspace()
    stranger = secrets.token_hex(16)
    denied = await web.post(
        "/web/chat", content=b"hi", headers={"cookie": f"{SESSION_COOKIE}={stranger}"}
    )
    assert denied.status_code == 401
    missing = await web.post("/web/chat", content=b"hi")
    assert missing.status_code == 401


async def test_two_web_members_get_isolated_subjects_and_cannot_cross(web: AsyncClient) -> None:
    workspace_id = await _seed_workspace()
    member_a, token_a = await _seed_member(workspace_id, "a@example.com")
    member_b, token_b = await _seed_member(workspace_id, "b@example.com")
    turn_a = (
        await web.post(
            "/web/chat", content=b"hi", headers={"cookie": f"{SESSION_COOKIE}={token_a}"}
        )
    ).json()["turn_id"]
    turn_b = (
        await web.post(
            "/web/chat", content=b"hi", headers={"cookie": f"{SESSION_COOKIE}={token_b}"}
        )
    ).json()["turn_id"]
    await _consume(web, token_a, turn_a)
    await _consume(web, token_b, turn_b)
    async with workspace_tx() as connection:
        owners = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.surface == "web"
                )
            )
        ).scalars().all()
    assert set(owners) == {member_a, member_b}
    assert recall_subjects(member_a) & recall_subjects(member_b) == frozenset({SHARED_SUBJECT})
    assert member_subject(member_a) not in recall_subjects(member_b)
    crossed = await web.get(
        f"/web/turns/{turn_a}/stream", headers={"cookie": f"{SESSION_COOKIE}={token_b}"}
    )
    assert crossed.status_code == 403


async def test_web_spend_view_matches_ledger_sums(web: AsyncClient) -> None:
    workspace_id = await _seed_workspace()
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
    page = await web.get("/web/spend", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert page.status_code == 200
    body = page.text
    assert "owner@example.com" in body
    assert "assistant" in body
    assert "egress" in body
    assert "$0.055000" in body


def test_chat_page_is_self_contained_and_binds_a_session_cookie() -> None:
    from selfhost.surfaces.web import CHAT_PAGE

    assert "<!doctype html>" in CHAT_PAGE
    assert "EventSource" in CHAT_PAGE
    assert "http://" not in CHAT_PAGE
    assert "https://" not in CHAT_PAGE
    assert "//cdn" not in CHAT_PAGE
