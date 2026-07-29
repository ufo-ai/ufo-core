import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import recall_subjects
from ufo_ext_web.audience import AUDIENCE_PREFIX, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.surface import PORTAL_HTML, SESSION_COOKIE, _sse
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running

from ufo.accounting import record_egress_request, record_turn_usage
from ufo.bearer import mint_token
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.grants import ConnectFlow, GrantStore, OAuthAccount, install_connect_flow
from ufo.hub import InProcessHub, SkillLoad, ToolCall
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import ConnectRequest, TerminalFrame, Usage
from ufo.sdk.audience import conversation_audience
from ufo.serve import _mount_shared_surfaces
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.surfaces import hub_tail
from ufo.workspace import ws

SECRET = "artifact-signing-secret"
TOKEN_SECRET = "web-token-secret"
STREAM_TIMEOUT_SECONDS = 30
STREAM_GATE = StreamGate()


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
    specs={
        spec.id: replace(spec, client=lambda spec, key: StandInModel(), key_slot="", key_env="")
        for spec in CORE_MODEL_SPECS
    },
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


@dataclass(frozen=True)
class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


@dataclass(frozen=True)
class ConnectProvider:
    provider: str = "github"
    host: str = "api.github.test"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://oauth.example.test/authorize?state={state}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id="github-account")


async def _seed_workspace() -> tuple[UUID, UUID]:
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
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _seed_member(workspace_id: UUID, email: str, *, admin: bool = False) -> tuple[UUID, str]:
    """Seed a member and mint the signed bearer the gateway or `ufoctl init` would — the value the
    `ufo_session` cookie carries; the web surface resolves the workspace and the member email from
    it. An admin reaches every agent; anyone else reaches only what the web audience grants."""
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    token = mint_token(TOKEN_SECRET, str(workspace_id), email, timedelta(hours=1))
    return member_id, token


async def _grant_web_access(workspace_id: UUID, agent_id: UUID, email: str) -> None:
    with ws(workspace_id):
        await web_extension().store.put(
            f"{AUDIENCE_PREFIX}{agent_id}/{email}", {"granted_by": str(uuid4())}
        )


@pytest.fixture(scope="session")
def dbos_runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox]]:
    config = dbos_launched
    hub = GatingHub(InProcessHub(), STREAM_GATE)
    blob = FilesystemBlobStore(root=config.blob.root)
    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=config.blob.root.parent / "workspaces",
    )
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            sandboxes=sandboxes,
            hub=hub,
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(b"web-test-run-token-secret"),
            dbos=dbos_client,
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(transaction=workspace_tx),
            embed=StubEmbed(),
            artifact_token_secret=SECRET,
        )
    )
    yield config, hub, blob, sandboxes
    dbos_client.destroy()
    loop_queue.reset_runtime()


@pytest.fixture
async def web(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID, UUID]]:
    config, hub, blob, sandboxes = dbos_runtime
    STREAM_GATE.reset()
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setattr(
        hub_tail, "turn_status_frame", release_when_running(STREAM_GATE, hub_tail.turn_status_frame)
    )
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id, agent_id = await _seed_workspace()
    app = FastAPI()
    _mount_shared_surfaces(
        app, (web_manifest(),), None, blob, sandboxes, hub, dbos_client, "", None
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        yield client, workspace_id, agent_id
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
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"hello",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]
    streamed, terminal = await _consume(client, token, turn_id)
    assert streamed == "echo:1"
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == "web",
                    tables.surface_identity.c.external_id == "owner@example.com",
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(
                    tables.conversation.c.surface,
                    tables.conversation.c.member_id,
                    tables.conversation.c.agent_id,
                    tables.conversation.c.queue_key,
                )
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
    assert conversation.agent_id == agent_id
    assert conversation.queue_key == f"{agent_id}/owner@example.com"
    assert writeback is None
    transcript = await client.get(
        f"/surface/web/agents/{agent_id}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert transcript.status_code == 200
    assert transcript.json()["messages"] == [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "echo:1"},
    ]


async def test_unknown_session_token_is_rejected(web: tuple[AsyncClient, UUID, UUID]) -> None:
    client, _workspace_id, agent_id = web
    stranger = secrets.token_hex(16)
    denied = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"hi",
        headers={"cookie": f"{SESSION_COOKIE}={stranger}"},
    )
    assert denied.status_code == 401
    missing = await client.post(f"/surface/web/agents/{agent_id}/chat", content=b"hi")
    assert missing.status_code == 401


async def test_agents_outside_the_web_audience_are_not_found(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The strict audience contract: a member without a grant sees no agent — the index is empty
    and chat, transcript, and a guessed identifier all fail closed as not-found, so changing a URL
    proves nothing exists (#624 acceptance)."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "outsider@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    index = await client.get("/surface/web/api/agents", headers=cookie)
    assert index.status_code == 200
    assert index.json() == {
        "member": {"email": "outsider@example.com", "admin": False},
        "agents": [],
    }
    denied = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=b"hi", headers=cookie
    )
    assert denied.status_code == 404
    transcript = await client.get(f"/surface/web/agents/{agent_id}/transcript", headers=cookie)
    assert transcript.status_code == 404
    guessed = await client.post(
        f"/surface/web/agents/{uuid4()}/chat", content=b"hi", headers=cookie
    )
    assert guessed.status_code == 404
    async with workspace_tx() as connection:
        conversations = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.conversation))
        ).scalar_one()
    assert conversations == 0


async def test_agents_index_filters_by_grant_and_widens_for_admins(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-sonnet-5",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, second_agent, "member@example.com")
    admin_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert admin_view.json()["member"] == {"email": "admin@example.com", "admin": True}
    assert [(a["name"], a["main"]) for a in admin_view.json()["agents"]] == [
        ("assistant", True),
        ("ops", False),
    ]
    member_view = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert member_view.json()["member"] == {"email": "member@example.com", "admin": False}
    assert [a["id"] for a in member_view.json()["agents"]] == [str(second_agent)]
    denied = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"hi",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert denied.status_code == 404


async def test_revoking_web_access_ends_streaming_too(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A revocation closes every portal route, including the tail of a turn admitted while the
    grant was live — the member owns the turn, but its agent left their audience, so the stream
    is not-found like chat and transcript."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, agent_id, "member@example.com")
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=f"{agent_id}/member@example.com",
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
                inbound="hello",
                speaker_member_id=member_id,
                terminal=TerminalFrame(status="done", text="hi").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    granted = await client.get(f"/surface/web/turns/{turn_id}/stream", headers=cookie)
    assert granted.status_code == 200
    with ws(workspace_id):
        await web_extension().store.delete(f"{AUDIENCE_PREFIX}{agent_id}/member@example.com")
    revoked = await client.get(f"/surface/web/turns/{turn_id}/stream", headers=cookie)
    assert revoked.status_code == 404


async def test_a_stale_cookie_does_not_block_a_fresh_token_post(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The fallback chain keys on the cookie failing to resolve, not being absent: a member whose
    cookie outlived its bearer recovers by posting a fresh token — the session reopens instead of
    401ing behind the stale cookie, and a GET behind that stale cookie serves the portal page with
    its token form rather than a bare rejection."""
    client, workspace_id, _agent_id = web
    stale = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=-1))
    fresh = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    page = await client.get("/surface/web", headers={"cookie": f"{SESSION_COOKIE}={stale}"})
    assert page.status_code == 200
    assert "token-form" in page.text
    opened = await client.post(
        "/surface/web",
        data={"token": fresh},
        headers={"cookie": f"{SESSION_COOKIE}={stale}"},
    )
    assert opened.status_code == 303
    assert opened.headers["set-cookie"].startswith(f"ufo_session={fresh}")


async def test_one_member_holds_a_conversation_per_agent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Two isolated agent areas (#624 acceptance): the same member's chats with two agents land in
    two conversations, each permanently bound to its agent."""
    client, workspace_id, agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    STREAM_GATE.arm()
    first = await client.post(f"/surface/web/agents/{agent_id}/chat", content=b"hi", headers=cookie)
    await _consume(client, token, first.json()["turn_id"])
    STREAM_GATE.arm()
    second = await client.post(
        f"/surface/web/agents/{second_agent}/chat", content=b"hi", headers=cookie
    )
    await _consume(client, token, second.json()["turn_id"])
    async with workspace_tx() as connection:
        bound = (
            (
                await connection.execute(
                    sa.select(tables.conversation.c.agent_id).where(
                        tables.conversation.c.surface == "web"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(bound) == {agent_id, second_agent}


async def test_web_stream_privately_opens_the_speakers_connect_handoff(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    await _grant_web_access(workspace_id, agent_id, "owner@example.com")
    flow = ConnectFlow(
        providers={"github": ConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
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
                inbound="connect github",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=TerminalFrame(
                    status="done",
                    text="Use the connection control.",
                    connect_request=ConnectRequest(
                        provider="github", requester_member_id=member_id
                    ),
                ).model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    try:
        response = await client.get(
            f"/surface/web/turns/{turn_id}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    finally:
        install_connect_flow(None)
    assert response.status_code == 200
    lines = response.text.splitlines()
    connect_data = json.loads(lines[lines.index("event: connect") + 1].removeprefix("data: "))
    assert connect_data["url"].startswith("https://oauth.example.test/authorize")
    assert lines.index("event: connect") < lines.index("event: terminal")
    async with workspace_tx() as connection:
        memoized_url = (
            await connection.execute(
                sa.select(tables.turn.c.connect_authorization_url).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).scalar_one()
    assert memoized_url == connect_data["url"]


async def test_two_web_members_get_isolated_subjects_and_cannot_cross(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_a, token_a = await _seed_member(workspace_id, "a@example.com")
    member_b, token_b = await _seed_member(workspace_id, "b@example.com")
    await _grant_web_access(workspace_id, agent_id, "a@example.com")
    await _grant_web_access(workspace_id, agent_id, "b@example.com")
    turn_a = (
        await client.post(
            f"/surface/web/agents/{agent_id}/chat",
            content=b"hi",
            headers={"cookie": f"{SESSION_COOKIE}={token_a}"},
        )
    ).json()["turn_id"]
    turn_b = (
        await client.post(
            f"/surface/web/agents/{agent_id}/chat",
            content=b"hi",
            headers={"cookie": f"{SESSION_COOKIE}={token_b}"},
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
    assert recall_subjects(conversation_audience(member_a)) & recall_subjects(
        conversation_audience(member_b)
    ) == frozenset({SHARED_SUBJECT})
    assert member_subject(member_a) not in recall_subjects(conversation_audience(member_b))
    crossed = await client.get(
        f"/surface/web/turns/{turn_a}/stream", headers={"cookie": f"{SESSION_COOKIE}={token_b}"}
    )
    assert crossed.status_code == 403


async def _seed_priced_turn(workspace_id: UUID, agent_id: UUID, member_id: UUID) -> None:
    """One finished turn with priced usage and an egress request, so the rollup has real sums to
    report against a known member and agent."""
    async with workspace_tx() as connection:
        conversation_id, turn_id = uuid4(), uuid4()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
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


async def test_web_spend_view_matches_ledger_sums(web: tuple[AsyncClient, UUID, UUID]) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    await _seed_priced_turn(workspace_id, agent_id, member_id)
    page = await client.get("/surface/web/spend", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert page.status_code == 200
    body = page.text
    assert "owner@example.com" in body
    assert "assistant" in body
    assert "egress" in body
    assert "$0.055000" in body


async def test_a_non_admin_is_not_found_on_the_spend_view(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The rollup is the workspace's financial state — every agent by name and every member's burn —
    so it answers an admin only. A member granted an agent reaches that agent's chat and still
    cannot read the workspace's spend, and the page is not-found rather than refused so it never
    confirms what it holds."""
    client, workspace_id, agent_id = web
    admin_id, _admin_token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    await _seed_priced_turn(workspace_id, agent_id, admin_id)
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, agent_id, "member@example.com")
    reached = await client.get(
        f"/surface/web/agents/{agent_id}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert reached.status_code == 200
    page = await client.get("/surface/web/spend", headers={"cookie": f"{SESSION_COOKIE}={token}"})
    assert page.status_code == 404
    assert "owner@example.com" not in page.text
    assert "assistant" not in page.text


def test_portal_page_is_self_contained() -> None:
    assert "<!doctype html>" in PORTAL_HTML
    assert "EventSource" in PORTAL_HTML
    assert "http://" not in PORTAL_HTML
    assert "https://" not in PORTAL_HTML
    assert "//cdn" not in PORTAL_HTML


async def test_portal_serves_without_a_session_and_posted_token_opens_one(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The bearer never rides a URL: the page serves unauthenticated (its token form posts back
    here), and the one POST that opens a session lands the form token as the host-only session
    cookie — HttpOnly, Secure, and `lax`, because arrival is a cross-site navigation from the
    gateway's signed-in card — then redirects into the portal."""
    client, workspace_id, _agent_id = web
    page = await client.get("/surface/web")
    assert page.status_code == 200
    assert "token-form" in page.text
    token = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    opened = await client.post("/surface/web", data={"token": token})
    assert opened.status_code == 303
    cookie = opened.headers["set-cookie"]
    assert cookie.startswith(f"ufo_session={token}")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    client.cookies.clear()
    tokenless = await client.post("/surface/web", data={})
    assert tokenless.status_code == 401
    assert "Domain" not in cookie


@pytest.mark.parametrize("pasted", ["tok\rnl", "tok\x00x", "tok日", "tok x"])
async def test_a_token_that_cannot_ride_a_cookie_answers_400(
    web: tuple[AsyncClient, UUID, UUID], pasted: str
) -> None:
    """A pasted value outside the bearer alphabet is refused before a Set-Cookie header is built —
    control characters and non-latin-1 raise inside the cookie writer, so without the shape check
    this exact request was a 500. The live cookie is what routes the request to the handler (a
    form-only garbage token dies at identify with 401)."""
    client, workspace_id, _agent_id = web
    session = mint_token(TOKEN_SECRET, str(workspace_id), "owner@example.com", timedelta(hours=1))
    refused = await client.post(
        "/surface/web",
        data={"token": pasted},
        headers={"cookie": f"{SESSION_COOKIE}={session}"},
    )
    assert refused.status_code == 400
    assert "set-cookie" not in refused.headers


async def test_an_unverified_bearer_authenticates_nobody(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A POST behind a cookie that still resolves lands its form token unread, so the session cookie
    can carry a string nothing verified. That is inert: every request after it verifies the cookie
    again, and one that verifies against nothing reaches no member."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    landed = await client.post(
        "/surface/web",
        data={"token": "not-a-signed-bearer"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert landed.status_code == 303
    assert "ufo_session=not-a-signed-bearer" in landed.headers["set-cookie"]
    refused = await client.get(
        "/surface/web/api/agents",
        headers={"cookie": f"{SESSION_COOKIE}=not-a-signed-bearer"},
    )
    assert refused.status_code == 401
