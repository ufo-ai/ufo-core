import asyncio
import json
import secrets
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_connectors.manifest import manifest as connectors_manifest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import recall_subjects
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.store import HostedSites
from ufo_ext_skill_create.manifest import manifest as skill_create_manifest
from ufo_ext_skill_create.store import user_skill
from ufo_ext_sources.manifest import manifest as sources_manifest
from ufo_ext_web import surface as web_surface
from ufo_ext_web.audience import AUDIENCE_PREFIX, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.panels import _outcome
from ufo_ext_web.surface import PORTAL_HTML, SESSION_COOKIE, _sse
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running
from ufo_testsupport.surfaces import EMPTY_SKILL_REGISTRY, no_user_skills

from ufo.accounting import record_egress_request, record_turn_usage
from ufo.bearer import mint_token
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.credentials import (
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.loader import member_object_registry, skill_registry, turn_runtime_skills
from ufo.grants import (
    ConnectFlow,
    GrantStore,
    OAuthAccount,
    account_object_name,
    install_connect_flow,
)
from ufo.hub import InProcessHub, SkillLoad, ToolCall
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.loop.transcript import Transcript
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import (
    AskQuestion,
    AskUserInput,
    ConnectRequest,
    CredentialPrompt,
    CredentialRequest,
    QuestionOption,
    TerminalFrame,
    Usage,
)
from ufo.sdk.audience import conversation_audience
from ufo.sdk.manifest import CredentialSlot, Manifest
from ufo.sdk.seats import Seats
from ufo.serve import _mount_shared_surfaces
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.surfaces import hub_tail
from ufo.workspace import ws

SECRET = "artifact-signing-secret"
SLOTTED = Manifest(
    name="stub",
    version="0",
    credentials=(
        CredentialSlot(name="acme_api_key", description="ACME API key"),
        CredentialSlot(
            name="acme_install_seal",
            description="ACME install binding",
            member_filled=False,
        ),
    ),
)
TOKEN_SECRET = "web-token-secret"
STREAM_TIMEOUT_SECONDS = 30
STREAM_GATE = StreamGate()
CREDENTIAL_FERNET = Fernet(Fernet.generate_key())


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
    it. An admin reaches every agent; anyone else reaches the main agent plus the non-main agents
    the web audience grants."""
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
            manifests=(
                web_manifest(),
                connectors_manifest(),
                skill_create_manifest(),
                sources_manifest(),
                SLOTTED,
            ),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=CredentialStore(fernet=CREDENTIAL_FERNET),
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
        app,
        (web_manifest(), SLOTTED),
        CredentialStore(fernet=CREDENTIAL_FERNET),
        blob,
        sandboxes,
        hub,
        dbos_client,
        SECRET,
        "https://web",
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=lambda: turn_runtime_skills(
            (skill_create_manifest(),),
            CredentialStore(fernet=CREDENTIAL_FERNET),
            DefaultIndex(transaction=workspace_tx),
            StubEmbed(),
        ),
        objects=member_object_registry((web_manifest(), SLOTTED, sites_manifest())),
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
        context = (
            await connection.execute(
                sa.select(tables.turn.c.context).where(tables.turn.c.id == UUID(turn_id))
            )
        ).scalar_one()
    assert linked.member_id == member_id
    assert conversation.surface == "web"
    assert conversation.member_id == member_id
    assert conversation.agent_id == agent_id
    assert conversation.queue_key == f"{agent_id}/owner@example.com"
    assert writeback is None
    assert context == {
        "sender": "owner@example.com",
        "timezone": None,
        "source": "ufo web (owner@example.com)",
    }
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


async def test_ungranted_member_reaches_the_main_agent_and_nothing_else(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every member reaches the workspace's main agent — the portal answers the way every other
    surface routes an unbound member — while a non-main agent without a grant fails closed as
    not-found on every route, including a guessed identifier (#624 acceptance)."""
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
    _member_id, token = await _seed_member(workspace_id, "outsider@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    index = await client.get("/surface/web/api/agents", headers=cookie)
    assert index.status_code == 200
    assert index.json() == {
        "member": {"email": "outsider@example.com", "admin": False},
        "agents": [
            {"id": str(agent_id), "name": "assistant", "main": True, "model": "claude-opus-4-8"}
        ],
    }
    reachable = await client.get(f"/surface/web/agents/{agent_id}/transcript", headers=cookie)
    assert reachable.status_code == 200
    assert reachable.json() == {"messages": []}
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=b"hi", headers=cookie
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    for path in (f"agents/{second_agent}/chat", f"agents/{uuid4()}/chat"):
        denied = await client.post(f"/surface/web/{path}", content=b"hi", headers=cookie)
        assert denied.status_code == 404
    transcript = await client.get(f"/surface/web/agents/{second_agent}/transcript", headers=cookie)
    assert transcript.status_code == 404
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
    assert bound == [agent_id]


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
    assert [a["id"] for a in member_view.json()["agents"]] == [
        str(agent_id),
        str(second_agent),
    ]
    reachable = await client.get(
        f"/surface/web/agents/{agent_id}/transcript",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert reachable.status_code == 200


async def _seed_connection(
    workspace_id: UUID, agent_id: UUID, owner_member_id: UUID, provider: str, *, shared: bool
) -> None:
    conversation_id, connection_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=conversation_id.hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=provider,
                account_id=f"{provider}-account",
                host="api.example.test",
                owner_member_id=owner_member_id,
                conversation_id=conversation_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_id,
                connection_id=connection_id,
                conversation_id=conversation_id,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def test_connections_panel_holds_the_member_gate_and_the_wall(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """#624 acceptance, read-side: inside one agent, member M's private connector never appears in
    member N's panel while agent-shared ones appear to both — a shared edge naming its owner only
    to an admin or the owner; another agent's grants are absent; an out-of-audience agent is
    not-found; a workspace admin sees every edge."""
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
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    await _seed_connection(workspace_id, agent_id, member_m, "github", shared=False)
    await _seed_connection(workspace_id, agent_id, member_n, "slack", shared=True)
    await _seed_connection(workspace_id, second_agent, member_m, "asana", shared=True)
    path = f"/surface/web/agents/{agent_id}/connections"
    m_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert [
        (c["provider"], c["shared"], c["owner_email"]) for c in m_view.json()["connections"]
    ] == [
        ("github", False, "m@example.com"),
        ("slack", True, None),
    ]
    n_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    assert [(c["provider"], c["owner_email"]) for c in n_view.json()["connections"]] == [
        ("slack", "n@example.com")
    ]
    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    assert [(c["provider"], c["owner_email"]) for c in admin_view.json()["connections"]] == [
        ("github", "m@example.com"),
        ("slack", "n@example.com"),
    ]
    other = await client.get(
        f"/surface/web/agents/{second_agent}/connections",
        headers={"cookie": f"{SESSION_COOKIE}={token_admin}"},
    )
    assert [c["provider"] for c in other.json()["connections"]] == ["asana"]
    walled = await client.get(
        f"/surface/web/agents/{second_agent}/connections",
        headers={"cookie": f"{SESSION_COOKIE}={token_m}"},
    )
    assert walled.status_code == 404


async def test_credentials_view_reports_slots_and_never_values(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace credentials view: member-fillable declared slots with their fill state — the
    slots are the deploy's, shared across every agent, and a value never renders. An
    unauthenticated read is refused before a byte of it."""
    client, workspace_id, _agent_id = web
    _member, token = await _seed_member(workspace_id, "m@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="acme_api_key",
                ciphertext=b"super-sealed-value",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    anonymous = await client.get("/surface/web/workspace/credentials")
    assert anonymous.status_code == 401
    assert "acme_api_key" not in anonymous.text
    listed = await client.get(
        "/surface/web/workspace/credentials",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert listed.status_code == 200
    assert listed.json()["slots"] == [
        {
            "slot": "acme_api_key",
            "name": "acme-api-key",
            "extension": "stub",
            "description": "ACME API key",
            "filled": True,
        }
    ]
    assert "sealed" not in listed.text
    assert "acme_install_seal" not in listed.text


async def test_sources_panel_gates_on_subject(web: tuple[AsyncClient, UUID, UUID]) -> None:
    """The workspace sources view: a member sees shared sources plus their own registrations, a
    shared source names its owner only to an admin or the owner (a source with no owner member
    names nobody), and an unauthenticated read is refused."""
    client, workspace_id, _agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    async with workspace_tx() as connection:
        for backend, subject, owner in (
            ("folder", "shared", None),
            ("github", f"member:{member_m}", member_m),
            ("notion", "shared", member_n),
        ):
            await connection.execute(
                sa.insert(tables.source).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    backend=backend,
                    config={},
                    subject=subject,
                    owner_member_id=owner,
                    next_sync_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    path = "/surface/web/workspace/sources"
    m_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert [(s["backend"], s["shared"], s["owner_email"]) for s in m_view.json()["sources"]] == [
        ("folder", True, None),
        ("github", False, "m@example.com"),
        ("notion", True, None),
    ]
    n_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})
    assert [(s["backend"], s["owner_email"]) for s in n_view.json()["sources"]] == [
        ("folder", None),
        ("notion", "n@example.com"),
    ]
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    admin_view = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    assert [(s["backend"], s["owner_email"]) for s in admin_view.json()["sources"]] == [
        ("folder", None),
        ("github", "m@example.com"),
        ("notion", "n@example.com"),
    ]
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_artifacts_view_lists_own_files_with_links_and_admins_see_all(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace artifacts view: a member reads their own conversations' shared files newest
    first, each carrying the signed TTL download link and the media type the page previews an
    image by; another member's files never list; an admin reads the workspace's."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    minted = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
    shared = {
        "m@example.com": (
            (member_m, "artifacts/a/report.pdf", "report.pdf", "application/pdf"),
            (member_m, "artifacts/a/chart.png", "chart.png", "image/png"),
        ),
        "n@example.com": ((member_n, "artifacts/b/notes.txt", "notes.txt", "text/plain"),),
    }
    minute = 0
    for email, files in shared.items():
        _conversation, turn_id = await _seed_web_turn(
            workspace_id, agent_id, files[0][0], email, TerminalFrame(status="done", text="ok")
        )
        for _member_id, blob_key, filename, media_type in files:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.shared_artifact).values(
                        turn_id=turn_id,
                        blob_key=blob_key,
                        workspace_id=workspace_id,
                        filename=filename,
                        subject="the file",
                        media_type=media_type,
                        size_bytes=3,
                        created_at=minted + timedelta(minutes=minute),
                        updated_at=sa.func.now(),
                    )
                )
            minute += 1
    path = "/surface/web/workspace/artifacts"
    m_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})).json()
    assert [entry["filename"] for entry in m_view["artifacts"]] == ["chart.png", "report.pdf"]
    assert [entry["media_type"] for entry in m_view["artifacts"]] == [
        "image/png",
        "application/pdf",
    ]
    assert m_view["artifacts"][0]["url"].startswith("https://web/")
    assert "token=" in m_view["artifacts"][0]["url"]
    n_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})).json()
    assert [entry["filename"] for entry in n_view["artifacts"]] == ["notes.txt"]
    admin_view = (
        await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()
    assert [entry["filename"] for entry in admin_view["artifacts"]] == [
        "notes.txt",
        "chart.png",
        "report.pdf",
    ]
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_workspace_usage_answers_a_member_their_own_and_an_admin_the_rollup(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A member's own burn is theirs to read, so the workspace usage view answers every member —
    unlike the admin-only rollup page. A non-admin's payload carries only their own sums and their
    own member-scoped caps, naming no other member and no agent; an admin additionally receives the
    rollup they already read on the spend page."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin_id, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    await _seed_priced_turn(workspace_id, agent_id, member_m)
    await _seed_priced_turn(workspace_id, agent_id, member_n)
    async with workspace_tx() as connection:
        for subject, limit in ((member_m, 5_000_000), (member_n, 9_000_000)):
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope="member",
                    subject_id=subject,
                    window_seconds=3_600,
                    limit_micro_usd=limit,
                    on_breach="park",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    path = "/surface/web/workspace/usage"
    mine = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})
    assert mine.status_code == 200
    payload = mine.json()
    assert payload["workspace"] is None
    assert payload["window_seconds"] == 86_400
    assert {line["dimension"] for line in payload["by_dimension"]} == {"egress", "tokens"}
    assert payload["total_micro_usd"] == 55_000
    assert [cap["limit_micro_usd"] for cap in payload["caps"]] == [5_000_000]
    body = mine.text
    assert "n@example.com" not in body
    assert "assistant" not in body
    theirs = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})).json()
    assert [cap["limit_micro_usd"] for cap in theirs["caps"]] == [9_000_000]
    rolled = await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    workspace = rolled.json()["workspace"]
    assert rolled.json()["total_micro_usd"] == 0
    assert workspace["total_micro_usd"] == 110_000
    assert {entry["label"] for entry in workspace["by_member"]} == {
        "m@example.com",
        "n@example.com",
    }
    assert [entry["label"] for entry in workspace["by_agent"]] == ["assistant"]
    assert {line["dimension"] for line in workspace["by_dimension"]} == {"egress", "tokens"}
    windowed = (
        await client.get(
            f"{path}?window_seconds=3600", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
    ).json()
    assert windowed["window_seconds"] == 3_600
    for bad in ("abc", "-5", "0", str(web_surface.MAX_USAGE_WINDOW_SECONDS + 1)):
        refused = await client.get(
            f"{path}?window_seconds={bad}", headers={"cookie": f"{SESSION_COOKIE}={token_m}"}
        )
        assert refused.status_code == 400
    anonymous = await client.get(path)
    assert anonymous.status_code == 401


async def test_sites_view_answers_through_the_kinds_own_gate(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The workspace sites view lists through the site kind's visibility gate: a shared site
    answers every member, a private one only its creator and an admin — the same rows chat's
    `object_list` answers, never a second ACL."""
    client, workspace_id, agent_id = web
    member_m, token_m = await _seed_member(workspace_id, "m@example.com")
    _member_n, token_n = await _seed_member(workspace_id, "n@example.com")
    _admin, token_admin = await _seed_member(workspace_id, "boss@example.com", admin=True)
    conversation_id, _turn = await _seed_web_turn(
        workspace_id, agent_id, member_m, "m@example.com", TerminalFrame(status="done", text="ok")
    )
    with ws(workspace_id):
        sites = HostedSites(workspace_id, workspace_tx)
        await sites.register(
            conversation_id,
            "landing",
            3000,
            member_m,
            member_m,
            "workspace",
            conversation_audience(member_m),
        )
        await sites.register(
            conversation_id,
            "draft",
            3001,
            member_m,
            member_m,
            "private",
            conversation_audience(member_m),
        )
    path = "/surface/web/workspace/sites"
    m_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_m}"})).json()
    assert m_view["available"] is True
    assert sorted(site["name"].split("-")[0] for site in m_view["sites"]) == ["draft", "landing"]
    n_view = (await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_n}"})).json()
    assert [site["name"].split("-")[0] for site in n_view["sites"]] == ["landing"]
    admin_view = (
        await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token_admin}"})
    ).json()
    assert sorted(site["name"].split("-")[0] for site in admin_view["sites"]) == [
        "draft",
        "landing",
    ]


async def test_revoking_web_access_ends_streaming_too(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A revocation closes every portal route, including the tail of a turn admitted while the
    grant was live — the member owns the turn, but its agent left their audience, so the stream
    is not-found like chat and transcript."""
    client, workspace_id, _agent_id = web
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
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
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
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


async def test_admin_view_reads_the_workspace_shape(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The administration read end to end: agents carry their policy, surface installations, and
    the exact web-audience grants written through the extension's store — the main agent carries
    none, and its `main` flag is what the portal renders as "every member"; members and seat
    state are `Seats.snapshot`'s answer; every spend cap arrives with its subject named for the
    reader; and the deploy reports its installed extensions and public-internet ceiling from the
    mounted manifest set."""
    client, workspace_id, _agent_id = web
    second_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-sonnet-5",
                internet_access_allowed=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                workspace_id=workspace_id,
                surface="slack",
                installation_id="team:T42",
                agent_id=second_agent,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, _member_token = await _seed_member(workspace_id, "member@example.com")
    await _grant_web_access(workspace_id, second_agent, "member@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(seat_limit=5, included_seats=2)
            .where(tables.workspace.c.id == workspace_id)
        )
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now())
            .where(tables.member.c.id == member_id)
        )
        for scope, subject, window in (
            ("workspace", None, 86_400),
            ("agent", second_agent, 3_600),
            ("member", member_id, 86_400),
        ):
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope=scope,
                    subject_id=subject,
                    window_seconds=window,
                    limit_micro_usd=5_000_000,
                    on_breach="park",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    view = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert view.status_code == 200
    payload = view.json()
    by_name = {agent["name"]: agent for agent in payload["agents"]}
    assert by_name["assistant"]["main"] and by_name["assistant"]["internet_access_allowed"]
    assert by_name["assistant"]["installations"] == []
    assert by_name["assistant"]["web_audience"] == []
    assert not by_name["ops"]["internet_access_allowed"]
    assert by_name["ops"]["installations"] == ["slack"]
    assert by_name["ops"]["web_audience"] == ["member@example.com"]
    assert {(m["email"], m["admin"], m["seated"]) for m in payload["members"]} == {
        ("admin@example.com", True, False),
        ("member@example.com", False, True),
    }
    assert payload["seats"] == {"limit": 5, "included": 2}
    assert [
        (
            cap["scope"],
            cap["subject"],
            cap["window_seconds"],
            cap["limit_micro_usd"],
            cap["on_breach"],
        )
        for cap in payload["caps"]
    ] == [
        ("agent", "ops", 3_600, 5_000_000, "park"),
        ("member", "member@example.com", 86_400, 5_000_000, "park"),
        ("workspace", None, 86_400, 5_000_000, "park"),
    ]
    assert payload["deploy"]["sandbox_internet"] is False
    assert [
        (entry["name"], entry["version"], entry["sandbox_internet"])
        for entry in payload["deploy"]["extensions"]
    ] == [("stub", "0", False), ("web", "0.1.0", False)]


async def test_admin_view_reports_ungated_seats(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The default deploy keeps both seat bounds NULL: auto-seating seats every member at
    creation, so the wire carries null bounds beside seated members — the page drops its seat
    column on this shape because the bounds, not per-member state, are what gate anything."""
    client, workspace_id, _agent_id = web
    admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, _member_token = await _seed_member(workspace_id, "member@example.com")
    async with workspace_tx() as connection:
        await Seats(workspace_id).auto_seat(connection, admin_id)
        await Seats(workspace_id).auto_seat(connection, member_id)
    view = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={admin_token}"}
    )
    assert view.status_code == 200
    payload = view.json()
    assert payload["seats"] == {"limit": None, "included": None}
    assert {(m["email"], m["seated"]) for m in payload["members"]} == {
        ("admin@example.com", True),
        ("member@example.com", True),
    }


async def test_a_non_admin_is_not_found_on_the_admin_view(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, _agent_id = web
    await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    denied = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert denied.status_code == 404
    assert "admin@example.com" not in denied.text


async def test_a_non_admin_is_not_found_on_the_spend_view(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The rollup is the workspace's financial state — every agent by name and every member's
    burn — so it answers an admin only. A member who reaches the main agent's chat still cannot
    read the workspace's spend, and the page is not-found rather than refused so it never
    confirms what it holds."""
    client, workspace_id, agent_id = web
    admin_id, _admin_token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    await _seed_priced_turn(workspace_id, agent_id, admin_id)
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
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
async def test_a_token_outside_the_bearer_alphabet_answers_400(
    web: tuple[AsyncClient, UUID, UUID], pasted: str
) -> None:
    """A pasted value outside the bearer alphabet is refused before a Set-Cookie header is built —
    nothing outside it can be a bearer, and the control-character and non-latin-1 cases would
    raise inside the cookie writer (a 500 without the shape check; a space would merely land
    quoted). The live cookie is what routes the request to the handler (a form-only garbage token
    dies at identify with 401)."""
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


async def _seed_web_turn(
    workspace_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    terminal: TerminalFrame,
) -> tuple[UUID, UUID]:
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=f"{agent_id}/{email}",
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
                inbound="ask",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id, turn_id


async def _collect_events(
    client: AsyncClient, token: str, turn_id: UUID
) -> list[tuple[str, dict[str, object]]]:
    collected: list[tuple[str, dict[str, object]]] = []
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream(
            "GET",
            f"/surface/web/turns/{turn_id}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        ) as stream:
            assert stream.status_code == 200
            event = "message"
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    collected.append((event, json.loads(line.split(":", 1)[1].strip())))
                    if event == "terminal":
                        return collected
                elif not line:
                    event = "message"
    raise AssertionError("stream ended without a terminal frame")


QUESTION = AskUserInput(
    title="Pick a deploy window",
    questions=(
        AskQuestion(
            question="When should the deploy run?",
            options=(
                QuestionOption(label="Now"),
                QuestionOption(label="Tonight", description="after 22:00 UTC"),
            ),
        ),
        AskQuestion(
            question="Page the on-call?",
            options=(
                QuestionOption(label="Yes"),
                QuestionOption(label="No"),
            ),
        ),
    ),
)


async def test_question_affordance_admits_the_first_answer_only(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A turn that ended by asking renders its options on reload, and an answer click admits the
    answer as the conversation's next turn under a per-question idempotency key — a double click
    or a second tab joins the turn the first answer won, and the response names the landed body so
    only the winning click renders as the answer."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    _conversation_id, asked_turn = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="one question", question=QUESTION),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    loaded = await client.get(f"/surface/web/agents/{agent_id}/transcript", headers=cookie)
    assert loaded.json()["question"]["turn_id"] == str(asked_turn)
    assert loaded.json()["question"]["title"] == "Pick a deploy window"
    answer_headers = {
        **cookie,
        "x-ufo-answer-turn": str(asked_turn),
        "x-ufo-answer-question": "0",
    }
    STREAM_GATE.arm()
    first = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content="Now · When should the deploy run?".encode(),
        headers=answer_headers,
    )
    assert first.status_code == 200
    assert first.json()["body"] == "Now · When should the deploy run?"
    await _consume(client, token, first.json()["turn_id"])
    second = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content="Tonight · When should the deploy run?".encode(),
        headers=answer_headers,
    )
    assert second.status_code == 200
    assert second.json()["turn_id"] == first.json()["turn_id"]
    assert second.json()["body"] == "Now · When should the deploy run?"
    STREAM_GATE.arm()
    sibling = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content="Yes · Page the on-call?".encode(),
        headers={**answer_headers, "x-ufo-answer-question": "1"},
    )
    assert sibling.status_code == 200
    assert sibling.json()["turn_id"] != first.json()["turn_id"]
    assert sibling.json()["body"] == "Yes · Page the on-call?"
    await _consume(client, token, sibling.json()["turn_id"])
    async with workspace_tx() as connection:
        answers = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.idempotency_key)
                .where(
                    tables.turn.c.idempotency_key.is_not(None),
                    tables.turn.c.workspace_id == workspace_id,
                )
                .order_by(tables.turn.c.seq)
            )
        ).all()
    assert [row.inbound for row in answers] == [
        "Now · When should the deploy run?",
        "Yes · Page the on-call?",
    ]
    assert answers[0].idempotency_key.endswith(":answer:0")
    assert answers[1].idempotency_key.endswith(":answer:1")


async def test_credential_prompts_stream_pending_and_fulfill_privately(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The web leg of the private credential handoff: the stream names only the prompts still
    awaiting values, a posted value lands through the sealed fulfillment without admitting a turn
    or touching a transcript, and the seal's member gate refuses anyone but the requester."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    sealed = seal_credential_request(
        CREDENTIAL_FERNET,
        CredentialRequestState(
            workspace_id=workspace_id, member_id=member_id, slots=("api_key", "signing_key")
        ),
    )
    request = CredentialRequest(
        reason="the acme connector needs its keys",
        prompts=(
            CredentialPrompt(slot="api_key", prompt="Acme API key"),
            CredentialPrompt(slot="signing_key", prompt="Acme signing key"),
        ),
        sealed=sealed,
    )
    _conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="keys please", credential_request=request),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    events = dict(await _collect_events(client, token, turn_id))
    assert [p["slot"] for p in events["credentials"]["prompts"]] == ["api_key", "signing_key"]
    _member_b, token_b = await _seed_member(workspace_id, "b@example.com")
    hijack = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "stolen"},
        headers={"cookie": f"{SESSION_COOKIE}={token_b}"},
    )
    assert hijack.status_code == 403
    stored = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "s3cr3t"},
        headers=cookie,
    )
    assert stored.status_code == 200
    assert stored.json() == {"stored": "api_key"}
    assert await CredentialStore(fernet=CREDENTIAL_FERNET).get(workspace_id, "api_key") == "s3cr3t"
    events = dict(await _collect_events(client, token, turn_id))
    assert [p["slot"] for p in events["credentials"]["prompts"]] == ["signing_key"]
    loaded = await client.get(f"/surface/web/agents/{agent_id}/transcript", headers=cookie)
    assert [p["slot"] for p in loaded.json()["credentials"]["prompts"]] == ["signing_key"]
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert turns == 1


async def test_shared_files_stream_and_reload_as_download_links(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    _conversation_id, turn_id = await _seed_web_turn(
        workspace_id,
        agent_id,
        member_id,
        "owner@example.com",
        TerminalFrame(status="done", text="here is the report"),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key="artifacts/x/report.pdf",
                workspace_id=workspace_id,
                filename="report.pdf",
                subject="the report",
                media_type="application/pdf",
                size_bytes=3,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    events = dict(await _collect_events(client, token, turn_id))
    (file,) = events["files"]["files"]
    assert file["filename"] == "report.pdf"
    assert file["size_bytes"] == 3
    assert file["url"].startswith("https://web/artifacts/download?token=")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    loaded = await client.get(f"/surface/web/agents/{agent_id}/transcript", headers=cookie)
    assert loaded.json()["files"][0]["url"].startswith("https://web/artifacts/download?token=")
    assert loaded.json()["files"][0]["size_bytes"] == 3


async def test_composer_files_land_in_the_workspace_before_the_turn(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """A multipart composer submit streams each attached file into the conversation's
    `web-inbox/` and admits one message naming the saved paths — colliding names get distinct
    files, and the turn's sandbox mounts them because the write precedes admission."""
    client, workspace_id, agent_id = web
    _config, _hub, _blob, sandboxes = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        data={"message": "read these"},
        files=[
            ("file", ("notes.txt", b"hello", "text/plain")),
            ("file", ("notes.txt", b"again", "text/plain")),
        ],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.conversation_id).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).one()
    assert row.inbound.startswith("read these")
    assert "web-inbox/notes.txt" in row.inbound
    assert "web-inbox/notes-1.txt" in row.inbound
    inbox = sandboxes.workspace_root / str(row.conversation_id) / "web-inbox"
    assert (inbox / "notes.txt").read_bytes() == b"hello"
    assert (inbox / "notes-1.txt").read_bytes() == b"again"


async def test_an_oversize_request_is_refused_at_the_door(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every refusal lands before the multipart parse runs, and the body the parser would choke on
    is what proves it: each request below carries a boundary its body never uses, so a parse that
    ran would surface as a 400 instead of the door's own status. A chunked body is refused whatever
    it declares — the server frames by the chunks, so a Content-Length beside them bounds
    nothing."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    unparseable = {**cookie, "content-type": "multipart/form-data; boundary=never-used"}
    monkeypatch.setattr(web_surface, "MAX_REQUEST_BYTES", 4)
    oversize = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers=unparseable,
    )
    assert oversize.status_code == 413

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"not a multipart body at all"

    chunked = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=_streamed(),
        headers=unparseable,
    )
    assert chunked.status_code == 411
    lying = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers={**unparseable, "transfer-encoding": "chunked"},
    )
    assert lying.status_code == 411
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_plain_body_is_bounded_by_what_it_consumes(
    web: tuple[AsyncClient, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary composer submit and every answer click take the plain-body path, whose bound is
    the bytes actually read — so an oversize body is refused on both framings, the honest length
    and the chunked one that declares none."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    monkeypatch.setattr(web_surface, "MAX_INBOUND_BYTES", 16)
    declared = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=b"x" * 64, headers=cookie
    )
    assert declared.status_code == 413

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"x" * 64

    chunked = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=_streamed(), headers=cookie
    )
    assert chunked.status_code == 413
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_malformed_multipart_body_is_the_clients_400(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A multipart body its own boundary never appears in is the client's malformed request —
    refused like every other malformed shape, never a fault — and admits nothing."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers={
            "cookie": f"{SESSION_COOKIE}={token}",
            "content-type": "multipart/form-data; boundary=never-used",
        },
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_body_that_is_not_utf8_is_refused_not_rewritten(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A plain body that does not decode as UTF-8 is refused whole — the member's bytes are never
    silently substituted into the admitted turn."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"caf\xe9 in latin-1",
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_multipart_message_part_lands_as_the_parsers_decode(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The multipart twin of the strict plain-path refusal, pinning where the guarantee ends: a
    `message` part arrives already decoded by starlette's form parser (UTF-8, falling back to
    latin-1), so the same bytes the plain path refuses admit here as that parser's reading —
    stated in `_parse_inbound`'s docstring and pinned so the paths' divergence is the parser's
    decode, never a silent drop."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    body = (
        b"--frame\r\n"
        b'Content-Disposition: form-data; name="message"\r\n\r\n'
        b"caf\xe9 in latin-1\r\n"
        b"--frame--\r\n"
    )
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=body,
        headers={
            "cookie": f"{SESSION_COOKIE}={token}",
            "content-type": "multipart/form-data; boundary=frame",
        },
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).scalar_one()
    assert inbound == b"caf\xe9 in latin-1".decode("latin-1")


async def test_a_urlencoded_chat_body_is_unsupported(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The composer posts plain text or multipart, never urlencoded — the odd shape is refused,
    not parsed."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        data={"message": "hi"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 415
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_the_token_form_reads_are_framed_at_both_doors(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Both places a token form is parsed — the identify fallback (no resolving cookie) and
    `open_session` behind one — refuse a chunked body with 411 and an over-limit declared length
    with 413 before any parse runs. Order is what the last two legs discriminate: each carries a
    boundary its body never uses, so a door that fired after the parse would answer the parse's
    own 400 instead of 411 — and `open_session`'s malformed multipart is the parse refusal
    itself, proving `_form`'s arm at this site."""
    client, workspace_id, _agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    urlencoded = {"content-type": "application/x-www-form-urlencoded"}
    cookie = {"cookie": f"{SESSION_COOKIE}={token}", **urlencoded}

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"token=x"

    oversized = b"x" * (web_surface.MAX_FORM_BYTES + 1)
    anonymous_chunked = await client.post("/surface/web", content=_streamed(), headers=urlencoded)
    assert anonymous_chunked.status_code == 411
    anonymous_oversize = await client.post("/surface/web", content=oversized, headers=urlencoded)
    assert anonymous_oversize.status_code == 413
    session_chunked = await client.post("/surface/web", content=_streamed(), headers=cookie)
    assert session_chunked.status_code == 411
    session_oversize = await client.post("/surface/web", content=oversized, headers=cookie)
    assert session_oversize.status_code == 413
    unparseable = {
        "cookie": f"{SESSION_COOKIE}={token}",
        "content-type": "multipart/form-data; boundary=never-used",
    }

    async def _streamed_junk() -> AsyncIterator[bytes]:
        yield b"not a multipart body at all"

    door_before_parse = await client.post(
        "/surface/web", content=_streamed_junk(), headers=unparseable
    )
    assert door_before_parse.status_code == 411
    parse_refusal = await client.post(
        "/surface/web", content=b"not a multipart body at all", headers=unparseable
    )
    assert parse_refusal.status_code == 400


async def test_a_multibyte_message_at_the_char_bound_admits(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The byte door exists to bound the read, not to shrink the message bound: a message of
    exactly `MAX_INBOUND_CHARS` characters admits even when every character is four bytes — the
    widest UTF-8 makes — pinning the full 4-byte relationship at the real constants, no
    stand-ins."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    text = "\U0001d11e" * web_surface.MAX_INBOUND_CHARS
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=text.encode(),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).scalar_one()
    assert inbound == text


async def test_a_client_chosen_filename_is_never_a_path(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The content-disposition leaf is untrusted: path components drop, everything outside the
    safe charset collapses, and an absurd length caps — the note names exactly what landed."""
    client, workspace_id, agent_id = web
    _config, _hub, _blob, sandboxes = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    STREAM_GATE.arm()
    admitted = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        data={"message": "renamed"},
        files=[
            ("file", ("../../we ird&name!!.txt", b"safe", "text/plain")),
            ("file", ("x" * 300 + ".txt", b"capped", "text/plain")),
        ],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert admitted.status_code == 200
    await _consume(client, token, admitted.json()["turn_id"])
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.inbound, tables.turn.c.conversation_id).where(
                    tables.turn.c.id == UUID(admitted.json()["turn_id"])
                )
            )
        ).one()
    assert "web-inbox/we-ird-name--.txt" in row.inbound
    assert row.inbound.endswith("web-inbox/" + "x" * 80 + "]")
    inbox = sandboxes.workspace_root / str(row.conversation_id) / "web-inbox"
    assert (inbox / "we-ird-name--.txt").read_bytes() == b"safe"
    assert (inbox / ("x" * 80)).read_bytes() == b"capped"


async def test_a_files_note_cannot_blow_the_inbound_bound(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound applies to the admitted body — text plus the attached-files note — and a refusal
    lands nothing in the member's existing conversation: no new turn, no workspace file."""
    client, workspace_id, agent_id = web
    _config, _hub, _blob, sandboxes = dbos_runtime
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    STREAM_GATE.arm()
    opened = await client.post(
        f"/surface/web/agents/{agent_id}/chat", content=b"hi", headers=cookie
    )
    await _consume(client, token, opened.json()["turn_id"])
    monkeypatch.setattr(web_surface, "MAX_INBOUND_CHARS", 64)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        data={"message": "short"},
        files=[("file", ("long-name-that-pads-the-note.txt", b"x", "text/plain"))],
        headers=cookie,
    )
    assert refused.status_code == 413
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
        conversation_id = (
            await connection.execute(sa.select(tables.conversation.c.id))
        ).scalar_one()
    assert turns == 1
    stray = (
        sandboxes.workspace_root
        / str(conversation_id)
        / "web-inbox"
        / "long-name-that-pads-the-note.txt"
    )
    assert not stray.exists()


async def test_malformed_answer_headers_are_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    bad_turn = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"Now",
        headers={**cookie, "x-ufo-answer-turn": "not-a-uuid"},
    )
    assert bad_turn.status_code == 400
    bad_index = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"Now",
        headers={**cookie, "x-ufo-answer-turn": str(uuid4()), "x-ufo-answer-question": "one"},
    )
    assert bad_index.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_identify_never_parses_a_multipart_body(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The identify token fallback reads only a urlencoded body — a multipart request never
    carries the session token, so an unauthenticated multipart POST is refused without its parse
    (and its disk spool) ever running, and a token smuggled as a multipart field authenticates
    nothing."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    unparseable = {"content-type": "multipart/form-data; boundary=never-used"}
    anonymous = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=b"not a multipart body at all",
        headers=unparseable,
    )
    assert anonymous.status_code == 401
    smuggled = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        content=f"token={token}".encode(),
        headers=unparseable,
    )
    assert smuggled.status_code == 401
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_message_part_that_is_not_text_is_refused(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A `message` part carrying a filename parses as a file, not text — refused loud instead of
    silently dropping the member's words from the admitted turn."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/chat",
        files=[
            ("message", ("message.txt", b"typed words", "text/plain")),
            ("file", ("notes.txt", b"hello", "text/plain")),
        ],
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_credential_fulfillment_refusals(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Every declared refusal produces: no session 401s, a chunked body 411s (a chunked malformed
    multipart too — the door fires before any parse could answer its 400), an over-limit declared
    length 413s, a malformed multipart body 400s (`_form`'s arm at this site), an empty value
    400s, a non-text field 400s, an over-cap value 413s, a slot the seal never named 403s, and a
    garbage seal 403s — none of them stores a byte, and the four bodies the portal splices into
    its member sentence are pinned to the lowercase unpunctuated wire register."""
    client, workspace_id, _agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com", admin=True)
    sealed = seal_credential_request(
        CREDENTIAL_FERNET,
        CredentialRequestState(workspace_id=workspace_id, member_id=member_id, slots=("api_key",)),
    )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    sessionless = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "value", "token": token},
    )
    assert sessionless.status_code == 401

    async def _streamed() -> AsyncIterator[bytes]:
        yield b"sealed=x"

    chunked = await client.post(
        "/surface/web/credentials",
        content=_streamed(),
        headers={**cookie, "content-type": "application/x-www-form-urlencoded"},
    )
    assert chunked.status_code == 411

    async def _streamed_junk() -> AsyncIterator[bytes]:
        yield b"not a multipart body at all"

    unparseable = {**cookie, "content-type": "multipart/form-data; boundary=never-used"}
    door_before_parse = await client.post(
        "/surface/web/credentials", content=_streamed_junk(), headers=unparseable
    )
    assert door_before_parse.status_code == 411
    malformed = await client.post(
        "/surface/web/credentials", content=b"not a multipart body at all", headers=unparseable
    )
    assert malformed.status_code == 400
    assert malformed.text == "malformed form body"
    over_limit = await client.post(
        "/surface/web/credentials",
        content=b"x" * (web_surface.MAX_FORM_BYTES + 1),
        headers={**cookie, "content-type": "application/x-www-form-urlencoded"},
    )
    assert over_limit.status_code == 413
    assert over_limit.text == "request too large"
    not_text = await client.post(
        "/surface/web/credentials",
        data={"slot": "api_key", "value": "value"},
        files=[("sealed", ("sealed.bin", sealed.encode(), "application/octet-stream"))],
        headers=cookie,
    )
    assert not_text.status_code == 400
    empty = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "  "},
        headers=cookie,
    )
    assert empty.status_code == 400
    oversize = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "api_key", "value": "x" * 4_097},
        headers=cookie,
    )
    assert oversize.status_code == 413
    assert oversize.text == "value too large"
    off_seal = await client.post(
        "/surface/web/credentials",
        data={"sealed": sealed, "slot": "unnamed", "value": "value"},
        headers=cookie,
    )
    assert off_seal.status_code == 403
    assert off_seal.text.startswith("not stored: ")
    garbage = await client.post(
        "/surface/web/credentials",
        data={"sealed": "garbage", "slot": "api_key", "value": "value"},
        headers=cookie,
    )
    assert garbage.status_code == 403
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.credential))
        ).scalar_one()
    assert stored == 0


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


INTENT_BODY = {
    "verb": "apply",
    "kind": "agent",
    "name": "assistant",
    "spec": {"model": "claude-sonnet-5", "internet_access_allowed": False},
}


async def _agent_row(agent_id: UUID) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.agent.c.model,
                    tables.agent.c.internet_access_allowed,
                ).where(tables.agent.c.id == agent_id)
            )
        ).one()


async def test_an_intent_applies_exactly_and_the_turn_is_the_audit_record(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel contract end to end: a form-shaped POST admits a turn that dispatches the object
    verb verbatim — no model round — so the submitted values land exactly, the terminal frame
    returns synchronously, and the turn row plus its transcript are the audit record."""
    client, workspace_id, agent_id = web
    _config, _hub, blob, _sandboxes = dbos_runtime
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 200
    outcome = submitted.json()
    assert outcome["applied"] is True
    row = await _agent_row(agent_id)
    assert row.model == "claude-sonnet-5"
    assert row.internet_access_allowed is False
    async with workspace_tx() as connection:
        turn = (
            await connection.execute(
                sa.select(
                    tables.turn.c.admission_source,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.inbound,
                    tables.turn.c.status,
                    tables.turn.c.conversation_id,
                    tables.conversation.c.agent_id,
                    tables.conversation.c.queue_key,
                )
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.id == UUID(outcome["turn_id"]))
            )
        ).one()
    assert turn.admission_source == "intent"
    assert turn.speaker_member_id == admin_id
    assert turn.status == "done"
    assert turn.agent_id == agent_id
    assert turn.queue_key == f"intent/{agent_id}/admin@example.com"
    assert outcome["message"] == "Saved."
    assert "claude-sonnet-5" in turn.inbound
    recorded = await Transcript(blob=blob, conversation_id=turn.conversation_id).read()
    assert recorded is not None
    assert len(recorded.messages) == 2
    assert recorded.messages[-1].role == "assistant"


SKILL_MD = "---\nname: release-notes\ndescription: How release notes read.\n---\nWrite tersely.\n"


async def test_a_skill_intent_creates_replaces_and_deletes(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The skill kind rides the intent lane end to end for any member: apply creates a skill the
    skills projection lists as member-authored, a second apply on the same name replaces it,
    delete tears it out whole — the projection forgets it and the store holds no rows — and a
    walled agent's lane stays not-found for the same body."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}

    async def listed() -> dict[str, dict[str, str]]:
        answer = await client.get(f"/surface/web/agents/{agent_id}/skills", headers=cookie)
        assert answer.status_code == 200
        return {skill["name"]: skill for skill in answer.json()["skills"]}

    created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "release-notes",
            "spec": {"files": {"SKILL.md": SKILL_MD}},
        },
        headers=cookie,
    )
    assert created.status_code == 200
    assert created.json()["applied"] is True, created.json()
    saved = (await listed())["release-notes"]
    assert saved["description"] == "How release notes read."
    assert saved["origin"] == "member"
    replaced = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "release-notes",
            "spec": {
                "files": {
                    "SKILL.md": SKILL_MD.replace("How release notes read.", "Terse and dated.")
                }
            },
        },
        headers=cookie,
    )
    assert replaced.json()["applied"] is True
    assert (await listed())["release-notes"]["description"] == "Terse and dated."
    deleted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "skill", "name": "release-notes"},
        headers=cookie,
    )
    assert deleted.json()["applied"] is True
    assert "release-notes" not in await listed()
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(user_skill))
        ).scalar_one()
    assert rows == 0
    walled = await client.post(
        f"/surface/web/agents/{uuid4()}/intents",
        json={"verb": "delete", "kind": "skill", "name": "release-notes"},
        headers=cookie,
    )
    assert walled.status_code == 404


async def test_a_refused_skill_intent_surfaces_the_kinds_error_and_writes_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A SKILL.md whose frontmatter name does not match the object name is the kind's own
    refusal: the outcome carries it, no skill lands, and no store row exists."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "skill",
            "name": "other-name",
            "spec": {"files": {"SKILL.md": SKILL_MD}},
        },
        headers=cookie,
    )
    assert refused.status_code == 200
    outcome = refused.json()
    assert outcome["applied"] is False
    assert outcome["message"]
    skills = await client.get(f"/surface/web/agents/{agent_id}/skills", headers=cookie)
    assert skills.json()["skills"] == []
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(user_skill))
        ).scalar_one()
    assert rows == 0


async def test_an_agent_delete_intent_surfaces_the_kinds_refusal(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The widened verb Literal admits `delete` for the agent kind too, and the kind itself is
    the gate: agents are undeletable through objects, so the outcome is the refusal and the row
    survives — the same answer chat gives."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "agent", "name": "assistant"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    row = await _agent_row(agent_id)
    assert row.model == "claude-opus-4-8"


async def test_a_refused_intent_surfaces_the_refusal_and_applies_nothing(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The kind's own admin gate answers the panel: the refusal text returns, the turn commits
    failed, and the submitted values never land — no partial application."""
    client, workspace_id, agent_id = web
    await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, token = await _seed_member(workspace_id, "member@example.com")
    before = await _agent_row(agent_id)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 200
    outcome = submitted.json()
    assert outcome["applied"] is False
    assert "admin" in outcome["message"]
    assert await _agent_row(agent_id) == before
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == UUID(outcome["turn_id"]))
            )
        ).scalar_one()
    assert status == "failed"


async def test_an_out_of_audience_agent_takes_no_intent(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A walled agent's intent lane is not-found like every portal route, writing nothing — while
    the main agent, which every member reaches, admits the turn and answers with the object
    verb's own refusal: the panel mutates exactly what chat would."""
    client, workspace_id, agent_id = web
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _outsider, token = await _seed_member(workspace_id, "outsider@example.com")
    denied = await client.post(
        f"/surface/web/agents/{walled_agent}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert denied.status_code == 404
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    outcome = refused.json()
    assert outcome["applied"] is False
    assert "admin" in outcome["message"]


async def test_a_source_intent_reaches_the_source_kind(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel's own envelope, reconstructed from the projection the panel reads, reaching the
    real `source` kind: Resync pulls the binding's next sync to now through the kind's registrar
    gate, and Remove takes the rows out. The envelope is built from `workspace/sources` exactly as
    `renderSources` builds it, so the projection's binding fields and the kind's `SourceSpec`
    cannot drift apart without this failing — and a source apply carries no model, so the
    agent-spec registry precheck must not swallow it."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="asana",
                config={"account": "acct-7", "stream": "workspaces"},
                subject=SHARED_SUBJECT,
                owner_member_id=admin_id,
                next_sync_at=datetime.now(UTC) + timedelta(hours=6),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    listed = await client.get("/surface/web/workspace/sources", headers=cookie)
    [projected] = listed.json()["sources"]
    spec = {
        "provider": projected["backend"],
        "streams": [projected["stream"]],
        "account_id": projected["account_id"],
        "base_url": projected["base_url"],
        "shared": projected["shared"],
    }

    resynced = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "source",
            "name": projected["name"],
            "spec": {**spec, "resync": True},
        },
        headers=cookie,
    )

    assert resynced.status_code == 200
    outcome = resynced.json()
    assert outcome["applied"] is True, outcome["message"]
    assert "No model named" not in outcome["message"]
    async with workspace_tx() as connection:
        due = (
            await connection.execute(
                sa.select(tables.source.c.next_sync_at).where(tables.source.c.id == source_id)
            )
        ).scalar_one()
    assert due <= datetime.now(UTC).replace(tzinfo=due.tzinfo)  # the kind pulled it forward

    removed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "source", "name": projected["name"]},
        headers=cookie,
    )

    assert removed.status_code == 200
    assert removed.json()["applied"] is True, removed.json()["message"]
    async with workspace_tx() as connection:
        live = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.source)
                .where(tables.source.c.id == source_id, tables.source.c.removed_at.is_(None))
            )
        ).scalar_one()
    assert live == 0
    async with workspace_tx() as connection:
        inbound = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).order_by(tables.turn.c.created_at)
                )
            )
            .scalars()
            .all()
        )
    assert [json.loads(entry)["tool"] for entry in inbound] == ["object_apply", "object_delete"]
    assert projected["name"] in json.loads(inbound[1])["input"]["name"]


async def test_grant_intents_flip_and_revoke_under_the_owner_gate(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The connections panel's two mutations ride the intent lane against the `connector_grant`
    kind, named by the grant's stable object name: a non-owner member's flip surfaces the kind's
    own refusal and changes nothing, the owner's flip lands exactly, and the owner's delete
    revokes the grant row — each outcome synchronous and audited as a turn."""
    client, workspace_id, agent_id = web
    owner_id, owner_token = await _seed_member(workspace_id, "owner@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    await _seed_connection(workspace_id, agent_id, owner_id, "github", shared=True)
    name = account_object_name("github", "github-account")
    flip = {
        "verb": "apply",
        "kind": "connector_grant",
        "name": name,
        "spec": {"provider": "github", "account_id": "github-account", "shared": False},
    }
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=flip,
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    assert "owner" in refused.json()["message"]
    async with workspace_tx() as connection:
        still_shared = (
            await connection.execute(sa.select(tables.connector_grant.c.shared))
        ).scalar_one()
    assert still_shared is True
    flipped = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=flip,
        headers={"cookie": f"{SESSION_COOKIE}={owner_token}"},
    )
    assert flipped.status_code == 200
    assert flipped.json()["applied"] is True
    async with workspace_tx() as connection:
        shared_now = (
            await connection.execute(sa.select(tables.connector_grant.c.shared))
        ).scalar_one()
    assert shared_now is False
    revoked = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "connector_grant", "name": name},
        headers={"cookie": f"{SESSION_COOKIE}={owner_token}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["applied"] is True
    async with workspace_tx() as connection:
        grants_left = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connector_grant))
        ).scalar_one()
    assert grants_left == 0


async def test_a_connect_intent_leaves_the_private_handoff_on_the_turn(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The panel's connect rides the same private OAuth handoff as chat's `connect_account`: the
    intent's terminal carries the connect request (never a URL), the member's stream mints their
    private authorization URL from it, and an unknown provider is the tool's own refusal."""
    client, workspace_id, agent_id = web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    flow = ConnectFlow(
        providers={"github": ConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    try:
        submitted = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={"verb": "connect", "kind": "connection", "name": "github"},
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert submitted.status_code == 200
        outcome = submitted.json()
        assert outcome["applied"] is True
        async with workspace_tx() as connection:
            terminal = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(
                        tables.turn.c.id == UUID(outcome["turn_id"])
                    )
                )
            ).scalar_one()
        frame = TerminalFrame.model_validate(terminal)
        assert frame.connect_request is not None
        assert frame.connect_request.provider == "github"
        assert frame.connect_request.requester_member_id == member_id
        assert "oauth.example.test" not in json.dumps(terminal)
        streamed = await client.get(
            f"/surface/web/turns/{outcome['turn_id']}/stream",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        lines = streamed.text.splitlines()
        connect_data = json.loads(lines[lines.index("event: connect") + 1].removeprefix("data: "))
        assert connect_data["url"].startswith("https://oauth.example.test/authorize")
        unknown = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={"verb": "connect", "kind": "connection", "name": "nonesuch"},
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert unknown.status_code == 200
        assert unknown.json()["applied"] is False
    finally:
        install_connect_flow(None)


async def test_connect_pairs_with_the_connection_kind_exactly(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """`connect` is the one verb no kind gates, so the model pins its pair both ways: `connect`
    with any other kind, and any other verb with the `connection` kind, are malformed intents —
    400 before any turn exists."""
    client, workspace_id, agent_id = web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    for body in (
        {"verb": "connect", "kind": "connector_grant", "name": "github"},
        {"verb": "connect", "kind": "agent", "name": "assistant"},
        {"verb": "apply", "kind": "connection", "name": "github"},
        {"verb": "delete", "kind": "connection", "name": "github"},
    ):
        refused = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json=body,
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_an_intent_naming_another_kind_is_refused_at_validation(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """ApplyIntent.kind's Literal is the whole gate keeping this route from becoming a general
    object_apply endpoint: the panels mutate agents and members today, and an intent naming any
    other kind must die at validation, before a turn exists."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    for kind in ("connection", "scheduled_task", "credential"):
        refused = await client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={"verb": "apply", "kind": kind, "name": "x", "spec": {"admin": True}},
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
        assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_credential_set_intent_mints_a_prompt_and_the_seal_stores_the_value(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Set/replace end to end with no pending chat request: the panel's `request` intent dispatches
    `request_credentials` verbatim, the turn's terminal frame carries the server-minted seal and
    its prompts, and the value crosses only in the sealed fulfillment — the intent outcome, the
    audit turn, and every response body stay secret-free. A deploy-written slot is not a slot the
    panel can name."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    minted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "request", "kind": "credential", "name": "acme-api-key"},
        headers=cookie,
    )
    assert minted.status_code == 200
    outcome = minted.json()
    assert outcome["applied"] is True
    request = outcome["credentials"]
    assert [prompt["slot"] for prompt in request["prompts"]] == ["acme_api_key"]
    assert [prompt["prompt"] for prompt in request["prompts"]] == ["ACME API key"]
    assert request["sealed"]
    stored = await client.post(
        "/surface/web/credentials",
        data={"sealed": request["sealed"], "slot": "acme_api_key", "value": "s3cr3t-value"},
        headers=cookie,
    )
    assert stored.status_code == 200
    assert "s3cr3t-value" not in stored.text
    assert (
        await CredentialStore(fernet=CREDENTIAL_FERNET).get(workspace_id, "acme_api_key")
        == "s3cr3t-value"
    )
    async with workspace_tx() as connection:
        audit = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.inbound, tables.turn.c.terminal)
            )
        ).one()
    assert audit.status == "done"
    assert "s3cr3t-value" not in audit.inbound
    assert "s3cr3t-value" not in str(audit.terminal)
    machinery = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "request", "kind": "credential", "name": "acme-install-seal"},
        headers=cookie,
    )
    refused = machinery.json()
    assert refused["applied"] is False
    assert "No credential slot named" in refused["message"]


async def test_a_credential_clear_intent_empties_the_slot_and_gates_on_admin(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Clear rides the intent lane to the credential kind's delete verb: an admin's clear empties
    the stored value and the slot lists as empty again; a non-admin gets the kind's own admin
    refusal — for clear and for minting a prompt alike — and the value stands."""
    client, workspace_id, agent_id = web
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    store = CredentialStore(fernet=CREDENTIAL_FERNET)
    await store.put(workspace_id, "acme_api_key", "live-value")
    member_cookie = {"cookie": f"{SESSION_COOKIE}={member_token}"}
    held = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "credential", "name": "acme-api-key"},
        headers=member_cookie,
    )
    refusal = held.json()
    assert refusal["applied"] is False
    assert "admin" in refusal["message"]
    assert await store.get(workspace_id, "acme_api_key") == "live-value"
    unminted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "request", "kind": "credential", "name": "acme-api-key"},
        headers=member_cookie,
    )
    assert unminted.json()["applied"] is False
    assert "admin" in unminted.json()["message"]
    cleared = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "delete", "kind": "credential", "name": "acme-api-key"},
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert cleared.json()["applied"] is True
    with pytest.raises(CredentialSlotUnset):
        await store.get(workspace_id, "acme_api_key")
    listed = await client.get(
        "/surface/web/workspace/credentials",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    slots = {entry["slot"]: entry for entry in listed.json()["slots"]}
    assert slots["acme_api_key"]["filled"] is False
    assert slots["acme_api_key"]["name"] == "acme-api-key"


async def test_a_credential_intent_never_carries_a_spec(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The kind pairing is the gate: a credential slot's value is set through its private prompt,
    so an apply naming the kind — the shape that would carry a secret in an intent body — is
    malformed before any turn exists."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    crossed = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "credential",
            "name": "acme-api-key",
            "spec": {"value": "s3cr3t"},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert crossed.status_code == 400
    assert "s3cr3t" not in crossed.text
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_a_malformed_intent_answers_400_before_any_turn(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "rename", "kind": "agent", "name": "assistant"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 400
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0


async def test_overview_projects_spec_schema_ceiling_and_admin_audience(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The settings panel's read: the agent row beside its prompt digest and bound surfaces, the
    deploy internet capability as the ceiling, the writable spec's own schema, and — admins only —
    the web audience this extension grants (empty for the main agent, which no grant ever holds).
    The read answers the agent's whole web audience — every member on the main agent — while
    the audience list itself stays the admin's."""
    client, workspace_id, agent_id = web
    _admin_id, admin_token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
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
    await _grant_web_access(workspace_id, second_agent, "member@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                workspace_id=workspace_id,
                surface="slack",
                installation_id="T123",
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    seen = await client.get(
        f"/surface/web/agents/{agent_id}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert seen.status_code == 200
    data = seen.json()
    assert data["agent"]["name"] == "assistant"
    assert data["agent"]["surfaces"] == ["slack"]
    assert data["agent"]["prompt"] == "be brief"
    assert len(data["agent"]["prompt_digest"]) > 8
    assert data["agent"]["updated_at"].endswith("+00:00")
    assert data["deploy"]["sandbox_internet"] is False
    assert data["models"] == ["auto", "claude-opus-4-8", "claude-sonnet-5"]
    assert data["spec"] == {"model": "claude-opus-4-8", "internet_access_allowed": True}
    assert set(data["spec_schema"]["properties"]) == {"model", "internet_access_allowed"}
    assert data["audience"] == []
    granted_view = await client.get(
        f"/surface/web/agents/{second_agent}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert granted_view.json()["audience"] == ["member@example.com"]
    member_view = await client.get(
        f"/surface/web/agents/{second_agent}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert member_view.status_code == 200
    assert member_view.json()["audience"] is None
    ungranted_main = await client.get(
        f"/surface/web/agents/{agent_id}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert ungranted_main.status_code == 200
    assert ungranted_main.json()["agent"]["name"] == "assistant"
    assert ungranted_main.json()["audience"] is None
    stranger = await client.get(
        f"/surface/web/agents/{uuid4()}/overview",
        headers={"cookie": f"{SESSION_COOKIE}={admin_token}"},
    )
    assert stranger.status_code == 404


async def test_concurrent_intents_serialize_on_the_members_intent_conversation(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Intent admission never folds into a live turn: two racing submits land as two whole turns
    on the member's one intent conversation with the agent, and the per-conversation partition
    runs them in order — both answer with their own turn's outcome."""
    client, workspace_id, agent_id = web
    await _seed_member(workspace_id, "admin@example.com", admin=True)
    token = mint_token(TOKEN_SECRET, str(workspace_id), "admin@example.com", timedelta(hours=1))
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    first, second = await asyncio.gather(
        client.post(f"/surface/web/agents/{agent_id}/intents", json=INTENT_BODY, headers=cookie),
        client.post(
            f"/surface/web/agents/{agent_id}/intents",
            json={
                "verb": "apply",
                "kind": "agent",
                "name": "assistant",
                "spec": {"model": "auto", "internet_access_allowed": True},
            },
            headers=cookie,
        ),
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["applied"] is True and second.json()["applied"] is True
    assert first.json()["turn_id"] != second.json()["turn_id"]
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(tables.turn.c.seq, tables.conversation.c.queue_key)
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.conversation.c.queue_key.like("intent/%"))
                .order_by(tables.turn.c.seq)
            )
        ).all()
    assert [turn.seq for turn in turns] == [1, 2]
    assert {turn.queue_key for turn in turns} == {f"intent/{agent_id}/admin@example.com"}


async def test_a_capped_members_intent_parks_and_the_panel_reads_the_reason(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Spend enforcement runs at admission for intents: a breached park cap holds the turn and the
    submit answers with the park message instead of applying."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    await _seed_priced_turn(workspace_id, agent_id, admin_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=uuid4(),
                workspace_id=workspace_id,
                scope="workspace",
                subject_id=None,
                window_seconds=3600,
                limit_micro_usd=1,
                on_breach="park",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    before = await _agent_row(agent_id)
    submitted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=INTENT_BODY,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert submitted.status_code == 200
    outcome = submitted.json()
    assert outcome["applied"] is False
    assert outcome["message"]
    assert await _agent_row(agent_id) == before
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == UUID(outcome["turn_id"]))
            )
        ).scalar_one()
    assert status == "parked"


async def test_a_model_outside_the_registry_refuses_before_any_turn(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A stored unknown model wedges the agent's every later turn at setup, so the submit refuses
    it before a turn exists."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "agent",
            "name": "assistant",
            "spec": {"model": "claude-sonnet-5-typo", "internet_access_allowed": False},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 200
    assert refused.json() == {
        "applied": False,
        "message": "No model named 'claude-sonnet-5-typo'.",
    }
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
    assert turns == 0
    assert (await _agent_row(agent_id)).model == "claude-opus-4-8"


async def test_an_oversized_intent_answers_413(web: tuple[AsyncClient, UUID, UUID]) -> None:
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    body = dict(INTENT_BODY, spec={"model": "x" * 20_000, "internet_access_allowed": False})
    refused = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=body,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert refused.status_code == 413


async def test_overview_reports_the_deploy_internet_ceiling_when_granted(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The True direction of the deploy ceiling: a manifest declaring sandbox_internet makes the
    overview report the capability the agent setting narrows."""
    config, hub, blob, sandboxes = dbos_runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id, agent_id = await _seed_workspace()
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (web_manifest(), Manifest(name="net", version="0.0.1", sandbox_internet=True)),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        seen = await client.get(
            f"/surface/web/agents/{agent_id}/overview",
            headers={"cookie": f"{SESSION_COOKIE}={token}"},
        )
    dbos_client.destroy()
    assert seen.status_code == 200
    assert seen.json()["deploy"]["sandbox_internet"] is True


def test_portal_page_smoke_walks_every_view() -> None:
    """A reference-level walk of the page's script under a stub DOM (`portal_smoke.mjs`) — boot
    (signed in and the 401 token-card branch), select, overview, the settings save, the mid-save
    agent switch, the failed post-save re-read, the memory tab's search bar (its `form.search`
    class and both style rules scoped to it), the connections panel (owner-gated flip and revoke
    envelopes, the connect intent and its private consent link off the stream), the workspace
    sources view (per-binding grouping, the summed errors and earliest next-sync cells, the
    resync/share/remove envelopes on the main agent's lane, Share suppressed once shared, and the
    empty state), admin, select-after-admin, the question render
    across every `buttonable` condition, the answered chain, the credentials prompts and their
    refusal arms, an agent switch mid-answer, and a later turn superseding an older turn's
    question and files — so a deleted declaration, a dangling element reference, or a
    silently reverted render rule fails here instead of rendering a blank portal (the class of
    bug `node --check` cannot see)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    tests_dir = Path(__file__).parent
    page = tests_dir.parent / "ufo_ext_web" / "static" / "portal.html"
    result = subprocess.run(
        [node, str(tests_dir / "portal_smoke.mjs"), str(page)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_outcome_strips_the_error_class_and_names_a_bare_status() -> None:
    """The three outcome transforms members read: a dispatch refusal loses its exception-class
    prefix but keeps the kind's reason, a frame with neither message nor text reads as
    `Not applied (<status>).` instead of one bare word, and success is the fixed word, never the
    tool's JSON."""
    turn_id = uuid4()
    refused = json.loads(
        _outcome(
            TerminalFrame(
                status="failed",
                error_class="IntentRefused",
                error_message="AdminRequired: editing an agent requires a workspace admin",
            ),
            turn_id,
        ).body
    )
    assert refused == {
        "applied": False,
        "message": "editing an agent requires a workspace admin",
        "turn_id": str(turn_id),
    }
    bare = json.loads(_outcome(TerminalFrame(status="cancelled"), turn_id).body)
    assert bare["message"] == "Not applied (cancelled)."
    saved = json.loads(
        _outcome(TerminalFrame(status="done", text='{"result": "updated"}'), turn_id).body
    )
    assert saved == {"applied": True, "message": "Saved.", "turn_id": str(turn_id)}


async def test_an_admin_creates_an_agent_through_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The administration view's create rides the same lane as every panel mutation: an admin's
    create intent on the main agent's lane lands a fresh non-main row with exactly the submitted
    configuration; without a prompt, under a taken name, or from a non-admin the kind's refusal
    returns — and nothing is created."""
    client, workspace_id, agent_id = web
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    envelope = {
        "verb": "apply",
        "kind": "agent",
        "name": "research",
        "spec": {
            "model": "claude-sonnet-5",
            "internet_access_allowed": False,
            "prompt": "be curious",
        },
    }
    created = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert created.status_code == 200
    assert created.json()["applied"] is True
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.agent).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.name == "research",
                )
            )
        ).one()
    assert (row.prompt, row.model, row.internet_access_allowed, row.is_main) == (
        "be curious",
        "claude-sonnet-5",
        False,
        False,
    )
    listed = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    assert "research" in [agent["name"] for agent in listed.json()["agents"]]
    duplicate = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope,
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert duplicate.json()["applied"] is False
    assert "proposal path" in duplicate.json()["message"]
    promptless = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={
            "verb": "apply",
            "kind": "agent",
            "name": "second",
            "spec": {"model": "claude-sonnet-5", "internet_access_allowed": True},
        },
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert promptless.json()["applied"] is False
    assert "requires a prompt" in promptless.json()["message"]
    outsider = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={**envelope, "name": "third"},
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert outsider.json()["applied"] is False
    assert "admin" in outsider.json()["message"]
    async with workspace_tx() as connection:
        names = (
            (
                await connection.execute(
                    sa.select(tables.agent.c.name).where(
                        tables.agent.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(names) == ["assistant", "research"]


async def test_member_seat_and_role_ride_the_intent_lane(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The members table's controls are member-kind applies on the main agent's lane: role and
    seat changes land exactly, and the kind's own guards answer — the last admin cannot be
    demoted, the last seated admin cannot be unseated, and a non-admin mutates nobody."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .values(seated_at=sa.func.now())
            .where(tables.member.c.id.in_((admin_id, member_id)))
        )

    def envelope(member: UUID, *, admin: bool, seated: bool) -> dict:
        return {
            "verb": "apply",
            "kind": "member",
            "name": str(member),
            "spec": {"admin": admin, "seated": seated},
        }

    promoted = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(member_id, admin=True, seated=True),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert promoted.json()["applied"] is True
    unseated = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(member_id, admin=True, seated=False),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert unseated.json()["applied"] is True
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.member.c.is_admin, tables.member.c.seated_at).where(
                    tables.member.c.id == member_id
                )
            )
        ).one()
    assert row.is_admin is True
    assert row.seated_at is None
    demote_last_seated = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(admin_id, admin=False, seated=True),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert demote_last_seated.json()["applied"] is False
    assert "seated admin" in demote_last_seated.json()["message"]
    unseat_last = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(admin_id, admin=True, seated=False),
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert unseat_last.json()["applied"] is False
    assert "last seated" in unseat_last.json()["message"]
    outsider = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json=envelope(admin_id, admin=False, seated=True),
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert outsider.json()["applied"] is False
    assert "admin" in outsider.json()["message"]


async def test_audience_intents_write_the_grant_store(
    web: tuple[AsyncClient, UUID, UUID],
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore, ConversationSandbox],
) -> None:
    """The administration view's audience controls ride the target agent's own intent lane and
    land in the same store the chat verbs write: a grant makes the agent appear in the member's
    portal, a revoke removes it, and a non-admin changes nothing."""
    client, workspace_id, _agent_id = web
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
    _admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    _member_id, member_token = await _seed_member(workspace_id, "member@example.com")
    granted = await client.post(
        f"/surface/web/agents/{second_agent}/intents",
        json={"verb": "grant_web_access", "email": "member@example.com"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert granted.status_code == 200
    assert granted.json()["applied"] is True, granted.json()
    listed = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert "ops" in [agent["name"] for agent in listed.json()["agents"]]
    revoked = await client.post(
        f"/surface/web/agents/{second_agent}/intents",
        json={"verb": "revoke_web_access", "email": "member@example.com"},
        headers={"cookie": f"{SESSION_COOKIE}={token}"},
    )
    assert revoked.json()["applied"] is True
    relisted = await client.get(
        "/surface/web/api/agents", headers={"cookie": f"{SESSION_COOKIE}={member_token}"}
    )
    assert "ops" not in [agent["name"] for agent in relisted.json()["agents"]]
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="other@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    outsider = await client.post(
        f"/surface/web/agents/{second_agent}/intents",
        json={"verb": "grant_web_access", "email": "other@example.com"},
        headers={"cookie": f"{SESSION_COOKIE}={member_token}"},
    )
    assert outsider.status_code == 404
    with ws(workspace_id):
        assert await web_extension().store.list(AUDIENCE_PREFIX) == ()


async def test_admin_payload_names_ids_and_models_for_the_mutation_forms(
    web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The administration view's forms need targets: every agent row carries its id (the intent
    lane is per-agent), every member row carries the stable member id the member kind applies to,
    and the create form's model choice is the deploy's own list."""
    client, workspace_id, agent_id = web
    admin_id, token = await _seed_member(workspace_id, "admin@example.com", admin=True)
    view = await client.get(
        "/surface/web/api/admin", headers={"cookie": f"{SESSION_COOKIE}={token}"}
    )
    payload = view.json()
    assert [agent["id"] for agent in payload["agents"]] == [str(agent_id)]
    assert [entry["id"] for entry in payload["members"]] == [str(admin_id)]
    assert payload["models"] == ["auto", "claude-opus-4-8", "claude-sonnet-5"]
