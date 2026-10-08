"""The debug surface end to end through the shared-fleet mount under the installed operator rule:
the built-in rule's own reach and the sample rule's fleet reach, the cookie bind and the
`ufoctl debugger` handoff that opens it, the fleet index, the configured links, and the read
routes."""

import asyncio
import re
import socket
import threading
import tomllib
import webbrowser
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import get_args
from uuid import UUID, uuid4

import httpx
import lz4.frame
import pytest
import sqlalchemy as sa
import uvicorn
from click.testing import CliRunner
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_debugger import surface as debugger_surface
from ufo_ext_debugger.manifest import manifest as debugger_manifest
from ufo_ext_memory.manifest import manifest as memory_manifest
from ufo_ext_sample.manifest import manifest as sample_manifest
from ufo_ext_sample.operator import OPERATOR_DOMAIN, OPERATOR_RULE, OPERATOR_SIGN_IN
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.cli import DEFAULT_CONFIG, debugger
from ufo.config import DEFAULT_OPERATOR_RULE, Config, DebuggerConfig
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.models.interface import Message, TextBlock, ToolUseBlock
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.runtime.engine import DispatchResult, StreamResult
from ufo.runtime.ext.operator import OperatorSetup, install_operator, select_operator_rule
from ufo.runtime.hub import InProcessHub
from ufo.runtime.turns.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    RecoveryRecord,
    RolloverWindow,
    compaction_key,
    encode,
    rollover_key,
    transcript_key,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import SUBAGENT_SURFACE, TerminalFrame
from ufo.sdk.hub import (
    Absorbed,
    Activity,
    ArtifactsChanged,
    CostTick,
    Created,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SourceRef,
    Sources,
    SubagentActivity,
    Terminal,
    TextDelta,
)
from ufo.serve import _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite", "postgresql"], indirect=True),
]

SECRET = "debug-token-secret"
ADMIN = "admin@acme.com"
MEMBER = "member@acme.com"
OPERATOR_ADMIN = f"admin@{OPERATOR_DOMAIN}"
HOUR = timedelta(hours=1)
BASE_TIME = datetime(2026, 7, 1, tzinfo=UTC)
BUILT_PAGE = "<!doctype html><title>debug</title>"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
O11Y = "https://o11y.example.test"
CHAT = "https://chat.example.test/client/{installation[1]}/{address[0]}"
LINKS = DebuggerConfig(
    turn_urls={
        "Trace": O11Y
        + "/apm/traces?query=trace_id%3A{trace_id}&start={start_ms}&end={end_ms}&paused=true",
        "Logs": O11Y + "/logs?query=trace_id%3A{trace_id}&from_ts={start_ms}&to_ts={end_ms}",
        "Model retries": O11Y
        + "/logs?query=trace_id%3A{trace_id}+model.*&from_ts={start_ms}&to_ts={end_ms}",
        "Model calls": O11Y
        + "/llm/traces?query=%40session_id%3A%22{conversation_id}%22&start={start_ms}&end={end_ms}",
    },
    conversation_urls={
        "chat": (CHAT + "/thread/{address[0]}-{address[1]}", CHAT),
        "desk": ("https://desk.example.test/{installation}/{address}",),
        "ufo": ("https://ufo.example.test/c/{address}",),
    },
)


class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the debug surface admits nothing")

    async def list_workflow_steps_async(self, workflow_id: str) -> list[dict[str, object]]:
        return [
            {
                "function_name": "TurnEngine._stream_once",
                "started_at_epoch_ms": 1_777_215_600_123,
                "completed_at_epoch_ms": 1_777_215_606_577,
                "output": StreamResult(
                    tool_calls=(ToolUseBlock(id="call-1", name="bash", input={"command": "pwd"}),)
                ),
            },
            {
                "function_name": "TurnEngine._dispatch_step",
                "started_at_epoch_ms": 1_777_215_606_600,
                "completed_at_epoch_ms": 1_777_215_726_705,
                "output": DispatchResult(tool_use_id="call-1", text="/workspace", is_error=False),
            },
        ]


@dataclass(frozen=True)
class _Seeded:
    workspace_id: UUID
    agent_id: UUID
    admin_id: UUID | None


@pytest.fixture
def rule() -> str:
    return DEFAULT_OPERATOR_RULE


@pytest.fixture
def links() -> DebuggerConfig:
    return DebuggerConfig()


@pytest.fixture
def operator(rule: str, links: DebuggerConfig) -> Iterator[None]:
    install_operator(
        OperatorSetup(rule=select_operator_rule(rule, (sample_manifest(),)), links=links)
    )
    yield
    install_operator(None)


@pytest.fixture
def mounted(
    db: None, operator: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FastAPI, WorkspaceBlobStore]:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (debugger_manifest(), memory_manifest()),
        None,
        blob,
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        _StubDbos(),
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
        deploy_sessions=None,
    )
    return app, blob


@pytest.fixture
async def debug(
    mounted: tuple[FastAPI, WorkspaceBlobStore],
) -> AsyncIterator[tuple[AsyncClient, WorkspaceBlobStore]]:
    app, blob = mounted
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://fleet") as client:
        yield client, blob


async def _seed_workspace(emails: tuple[str, ...] = (ADMIN, MEMBER)) -> _Seeded:
    workspace_id, agent_id = uuid4(), uuid4()
    member_ids = [uuid4() for _ in emails]
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            if emails:
                await connection.execute(
                    sa.insert(tables.member).values(
                        [
                            {
                                "id": member_id,
                                "workspace_id": workspace_id,
                                "email": email,
                                "is_admin": seniority == 0,
                                "created_at": BASE_TIME + timedelta(minutes=seniority),
                                "updated_at": BASE_TIME + timedelta(minutes=seniority),
                            }
                            for seniority, (member_id, email) in enumerate(
                                zip(member_ids, emails, strict=True)
                            )
                        ]
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
    return _Seeded(
        workspace_id=workspace_id, agent_id=agent_id, admin_id=member_ids[0] if emails else None
    )


async def _seed_conversation(
    seeded: _Seeded,
    *,
    queue_key: str = "C042:1721.5",
    surface: str = "ufo",
    member_id: UUID | None = None,
    title: str | None = None,
    created_at: datetime | None = None,
) -> UUID:
    conversation_id = uuid4()
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=seeded.workspace_id,
                    agent_id=seeded.agent_id,
                    surface=surface,
                    queue_key=queue_key,
                    title=title,
                    member_id=member_id,
                    created_at=created_at or sa.func.now(),
                    updated_at=created_at or sa.func.now(),
                )
            )
    return conversation_id


async def _seed_turn(
    seeded: _Seeded,
    conversation_id: UUID,
    seq: int,
    *,
    status: str = "done",
    parent_turn_id: UUID | None = None,
    at: datetime | None = None,
    traceparent: str | None = None,
) -> UUID:
    turn_id = uuid4()
    terminal = (
        TerminalFrame(
            status=status, text="answer", tokens=9, cost_micro_usd=77, model="claude-opus-4-8"
        ).model_dump(mode="json")
        if status in ("done", "failed", "cancelled")
        else None
    )
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=seeded.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=seeded.agent_id,
                    seq=seq,
                    status=status,
                    inbound=f"ask {seq}",
                    parent_turn_id=parent_turn_id,
                    subagent_profile="research" if parent_turn_id is not None else None,
                    terminal=terminal,
                    traceparent=traceparent,
                    created_at=at or sa.func.now(),
                    updated_at=at or sa.func.now(),
                )
            )
    return turn_id


async def _seed_ledger(seeded: _Seeded, turn_id: UUID, model: str) -> None:
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.ledger).values(
                    id=uuid4(),
                    workspace_id=seeded.workspace_id,
                    turn_id=turn_id,
                    dimension="tokens",
                    amount=120,
                    input_tokens=120,
                    prompt_tokens=120,
                    priced_micro_usd=340,
                    model=model,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


async def _seed_installation(seeded: _Seeded, surface: str, installation_id: str) -> None:
    with ws(seeded.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.surface_installation).values(
                    routes_ingress=True,
                    workspace_id=seeded.workspace_id,
                    surface=surface,
                    installation_id=installation_id,
                    agent_id=seeded.agent_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


def _token(seeded: _Seeded, email: str = ADMIN, secret: str = SECRET, ttl: timedelta = HOUR) -> str:
    return mint_token(secret, str(seeded.workspace_id), email, ttl)


def _auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_identify_admits_a_seated_admin_of_the_claimed_workspace_alone(debug) -> None:
    client, _ = debug
    seeded = await _seed_workspace()

    granted = await client.get("/surface/debug/api/workspace", headers=_auth(_token(seeded)))
    assert granted.status_code == 200
    assert granted.json() == {
        "workspace_id": str(seeded.workspace_id),
        "reach": "own",
        "installations": {},
    }
    for denied in (
        _token(seeded, MEMBER),
        _token(seeded, "admin@elsewhere.com"),
        _token(seeded, secret="wrong-secret"),
        _token(seeded, ttl=-HOUR),
    ):
        response = await client.get("/surface/debug/api/workspace", headers=_auth(denied))
        assert response.status_code == 401
    assert (await client.get("/surface/debug/api/workspace")).status_code == 401


async def test_an_own_session_reads_its_workspace_and_no_other(debug) -> None:
    client, _ = debug
    own = await _seed_workspace()
    other = await _seed_workspace(("person0@beta.io",))
    foreign_conversation = await _seed_conversation(other)
    foreign_turn = await _seed_turn(other, foreign_conversation, 1)
    headers = _auth(_token(own))

    for named in (str(other.workspace_id), "beta.io", "acme.com"):
        refused = await client.get(
            "/surface/debug/api/conversations", params={"ws": named}, headers=headers
        )
        assert refused.status_code == 403
    home = await client.get(
        "/surface/debug/api/conversations", params={"ws": str(own.workspace_id)}, headers=headers
    )
    assert home.json() == []
    assert (await client.get("/surface/debug/api/fleet", headers=headers)).status_code == 403
    for path in (
        f"api/turns/{foreign_turn}",
        f"api/turns/{foreign_turn}/steps",
        f"api/turns/{foreign_turn}/stream",
        f"api/conversations/{foreign_conversation}/transcript",
    ):
        assert (await client.get(f"/surface/debug/{path}", headers=headers)).status_code == 404
    turns = await client.get(
        f"/surface/debug/api/conversations/{foreign_conversation}/turns", headers=headers
    )
    assert turns.json() == []


async def test_a_posted_token_opens_a_session_behind_a_stale_cookie(debug) -> None:
    client, _ = debug
    seeded = await _seed_workspace()
    fresh = _token(seeded)

    for stale in (
        _token(seeded, secret="a-secret-this-deploy-does-not-hold"),
        _token(seeded, ttl=-HOUR),
        mint_token(SECRET, str(uuid4()), ADMIN, HOUR),
        _token(seeded, MEMBER),
    ):
        client.cookies.clear()
        client.cookies.set("ufo_debug", stale)
        opened = await client.post("/surface/debug", data={"token": fresh}, follow_redirects=False)
        assert opened.status_code == 303
        assert opened.headers["location"] == "https://fleet/surface/debug"
        assert opened.headers["set-cookie"].startswith(f"ufo_debug={fresh}")
        assert (await client.get("/surface/debug/api/workspace")).status_code == 200


async def test_the_first_credential_the_rule_grants_is_admitted(debug) -> None:
    client, _ = debug
    own = await _seed_workspace()
    other = await _seed_workspace()
    client.cookies.set("ufo_debug", _token(own))

    for header, admitted in ((_token(own, MEMBER), own), (_token(other), other)):
        read = await client.get("/surface/debug/api/workspace", headers=_auth(header))
        assert read.json() == {
            "workspace_id": str(admitted.workspace_id),
            "reach": "own",
            "installations": {},
        }
    refused = await client.post(
        "/surface/debug", data={"token": _token(own, MEMBER)}, follow_redirects=False
    )
    assert refused.status_code == 401
    assert "set-cookie" not in refused.headers
    posted = _token(other)
    rebound = await client.post("/surface/debug", data={"token": posted}, follow_redirects=False)
    assert rebound.status_code == 303
    assert rebound.headers["set-cookie"].startswith(f"ufo_debug={posted}")


async def test_query_tokens_are_never_accepted(debug) -> None:
    client, _ = debug
    seeded = await _seed_workspace()
    token = _token(seeded)

    read = await client.get("/surface/debug/api/workspace", params={"token": token})
    assert read.status_code == 401
    page = await client.get("/surface/debug", params={"token": token}, follow_redirects=False)
    assert page.status_code == 401
    assert "set-cookie" not in page.headers
    assert (await client.post("/surface/debug", follow_redirects=False)).status_code == 401


@pytest.mark.parametrize("rule", [OPERATOR_RULE])
async def test_a_page_get_without_a_credential_goes_to_the_rules_sign_in(debug) -> None:
    client, _ = debug

    page = await client.get("/surface/debug", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == OPERATOR_SIGN_IN
    assert (await client.get("/surface/debug/api/workspace")).status_code == 401


@pytest.mark.parametrize(
    ("rule", "sign_in"), [(DEFAULT_OPERATOR_RULE, None), (OPERATOR_RULE, OPERATOR_SIGN_IN)]
)
async def test_an_api_read_without_a_granted_credential_names_the_rules_sign_in(
    debug, sign_in: str | None
) -> None:
    client, _ = debug
    expired = _auth(_token(await _seed_workspace(), ttl=-HOUR))

    for headers in ({}, expired):
        refused = await client.get("/surface/debug/api/workspace", headers=headers)
        assert (refused.status_code, refused.json()) == (401, {"sign_in": sign_in})


@pytest.mark.parametrize("rule", [OPERATOR_RULE])
async def test_a_fleet_grant_lands_on_the_fleet_index(debug) -> None:
    client, _ = debug
    operator = await _seed_workspace((OPERATOR_ADMIN,))
    acme = await _seed_workspace()
    beta = await _seed_workspace(("person0@beta.io",))
    idle = await _seed_workspace(())
    older, older_json = datetime(2026, 8, 20, 9, 30, tzinfo=UTC), "2026-08-20T09:30:00Z"
    newer, newer_json = datetime(2026, 8, 21, 17, 5, tzinfo=UTC), "2026-08-21T17:05:00Z"
    acme_conversation = await _seed_conversation(acme, queue_key="C001:1.0", title="quarter close")
    parent = await _seed_turn(acme, acme_conversation, 1, at=older)
    await _seed_turn(acme, acme_conversation, 2, at=older)
    fanned = await _seed_conversation(acme, queue_key=str(parent), surface=SUBAGENT_SURFACE)
    await _seed_turn(acme, fanned, 1, at=newer, parent_turn_id=parent)
    beta_conversation = await _seed_conversation(beta, queue_key="C002:2.0", surface="web")
    await _seed_turn(beta, beta_conversation, 1, at=newer)
    headers = _auth(_token(operator, OPERATOR_ADMIN))

    meta = await client.get("/surface/debug/api/workspace", headers=headers)
    body = (await client.get("/surface/debug/api/fleet", headers=headers)).json()

    assert meta.json() == {
        "workspace_id": str(operator.workspace_id),
        "reach": "fleet",
        "installations": {},
    }
    listed = [entry["workspace_id"] for entry in body["workspaces"]]
    assert listed[:2] == [str(beta.workspace_id), str(acme.workspace_id)]
    assert set(listed[2:]) == {str(operator.workspace_id), str(idle.workspace_id)}
    assert body["workspaces"][:2] == [
        {
            "workspace_id": str(beta.workspace_id),
            "domain": "beta.io",
            "members": 1,
            "conversations": 1,
            "last_turn_at": newer_json,
        },
        {
            "workspace_id": str(acme.workspace_id),
            "domain": "acme.com",
            "members": 2,
            "conversations": 1,
            "last_turn_at": older_json,
        },
    ]
    assert body["threads"] == [
        {
            "workspace_id": str(beta.workspace_id),
            "domain": "beta.io",
            "conversation_id": str(beta_conversation),
            "surface": "web",
            "queue_key": "C002:2.0",
            "title": None,
            "turn_count": 1,
            "last_turn_at": newer_json,
        },
        {
            "workspace_id": str(acme.workspace_id),
            "domain": "acme.com",
            "conversation_id": str(acme_conversation),
            "surface": "ufo",
            "queue_key": "C001:1.0",
            "title": "quarter close",
            "turn_count": 2,
            "last_turn_at": older_json,
        },
    ]


@pytest.mark.parametrize("rule", [OPERATOR_RULE])
async def test_a_fleet_grant_reaches_any_workspace_by_id_or_domain(debug) -> None:
    client, _ = debug
    operator = await _seed_workspace((OPERATOR_ADMIN,))
    acme = await _seed_workspace()
    conversation_id = await _seed_conversation(acme)
    await _seed_turn(acme, conversation_id, 1)
    token = _token(operator, OPERATOR_ADMIN)

    for named in (str(acme.workspace_id), "acme.com"):
        listed = await client.get(
            "/surface/debug/api/conversations", params={"ws": named}, headers=_auth(token)
        )
        assert [entry["id"] for entry in listed.json()] == [str(conversation_id)]
    unknown = await client.get(
        "/surface/debug/api/conversations", params={"ws": "nobody.test"}, headers=_auth(token)
    )
    assert unknown.status_code == 401
    bound = await client.post(
        "/surface/debug", params={"ws": "acme.com"}, data={"token": token}, follow_redirects=False
    )
    assert bound.status_code == 303
    assert bound.headers["set-cookie"].startswith(f"ufo_debug={token}")
    own_admin = _token(acme)
    refused = await client.get(
        "/surface/debug/api/conversations",
        params={"ws": str(operator.workspace_id)},
        headers=_auth(own_admin),
    )
    assert refused.status_code == 403


async def test_app_page_serves_the_built_app_and_fails_loud_unbuilt(debug, monkeypatch) -> None:
    client, _ = debug
    headers = _auth(_token(await _seed_workspace()))
    monkeypatch.setattr(debugger_surface, "APP_HTML", BUILT_PAGE)
    page = await client.get("/surface/debug", headers=headers)
    assert page.status_code == 200
    assert page.text == BUILT_PAGE
    monkeypatch.setattr(debugger_surface, "APP_HTML", None)
    unbuilt = await client.get("/surface/debug", headers=headers)
    assert unbuilt.status_code == 500
    assert unbuilt.text == debugger_surface.APP_MISSING


@dataclass(frozen=True)
class _Served:
    base: str
    server: uvicorn.Server
    serving: asyncio.Task[None]


@pytest.fixture
async def served(
    mounted: tuple[FastAPI, WorkspaceBlobStore], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_Served]:
    app, _ = mounted
    listener = socket.create_server(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    config = tomllib.loads(DEFAULT_CONFIG) | {"connect": {"public_base_url": base}}
    monkeypatch.setattr("ufo.cli.load_config", lambda: Config.model_validate(config))
    monkeypatch.setenv("UFOCTL_DIR", str(tmp_path / "ufoctl"))
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off", ws="none"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        yield _Served(base=base, server=server, serving=serving)
    finally:
        server.should_exit = True
        await serving


def _write_cli_token(tmp_path, token: str) -> None:
    (tmp_path / "ufoctl").mkdir()
    (tmp_path / "ufoctl" / "token").write_text(token)


def _no_browser(url: str) -> bool:
    raise AssertionError(f"opened {url}")


async def test_ufoctl_debugger_relays_the_unbuilt_page_before_opening_a_browser(
    served, tmp_path, monkeypatch
) -> None:
    _write_cli_token(tmp_path, _token(await _seed_workspace()))
    monkeypatch.setattr(debugger_surface, "APP_HTML", None)
    monkeypatch.setattr(webbrowser, "open", _no_browser)

    result = await asyncio.to_thread(CliRunner().invoke, debugger)

    assert result.exit_code == 1
    assert (
        f"{served.base}/surface/debug answered 500: {debugger_surface.APP_MISSING}" in result.output
    )


async def test_the_session_ufoctl_debugger_opens_serves_the_memory_explorer(
    served, tmp_path, monkeypatch
) -> None:
    _write_cli_token(tmp_path, _token(await _seed_workspace()))
    monkeypatch.setattr(debugger_surface, "APP_HTML", BUILT_PAGE)
    posted: list[httpx.Response] = []
    followers: list[threading.Thread] = []

    def browser(url: str) -> bool:
        def follow() -> None:
            page = httpx.get(url).text
            action = re.search(r'action="([^"]+)"', page)
            token = re.search(r'name="token" value="([^"]+)"', page)
            assert action is not None and token is not None
            posted.append(
                httpx.post(
                    unescape(action.group(1)),
                    data={"token": unescape(token.group(1))},
                    follow_redirects=False,
                )
            )

        followers.append(threading.Thread(target=follow))
        followers[-1].start()
        return True

    monkeypatch.setattr(webbrowser, "open", browser)

    result = await asyncio.to_thread(CliRunner().invoke, debugger)
    await asyncio.to_thread(followers[0].join)
    cookie = posted[0].cookies["ufo_debug"]
    async with AsyncClient(base_url=served.base, cookies={"ufo_debug": cookie}) as session:
        memories = await session.get("/surface/memory/api/memories")
        workspace = await session.get("/surface/debug/api/workspace")

    assert result.exit_code == 0, result.output
    assert posted[0].status_code == 303
    assert memories.status_code == 200
    assert memories.json() == []
    assert workspace.status_code == 200


async def test_conversations_list_every_surface_newest_first_with_what_opened_them(debug) -> None:
    client, _ = debug
    seeded = await _seed_workspace()
    older = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)
    newer = datetime(2026, 8, 21, 17, 5, tzinfo=UTC)
    settled = await _seed_conversation(seeded, queue_key="C001:1.0", member_id=seeded.admin_id)
    await _seed_turn(seeded, settled, 1, at=older)
    await _seed_turn(seeded, settled, 2, at=older)
    running_conversation = await _seed_conversation(seeded, queue_key="C002:2.0")
    running = await _seed_turn(seeded, running_conversation, 1, status="running", at=newer)
    await _seed_ledger(seeded, running, "claude-sonnet-5")
    fanned = await _seed_conversation(
        seeded, queue_key=str(running), surface=SUBAGENT_SURFACE, created_at=older
    )
    await _seed_turn(
        seeded,
        fanned,
        1,
        status="queued",
        parent_turn_id=running,
        at=datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
    )
    idle = await _seed_conversation(
        seeded, queue_key="C003:3.0", created_at=datetime(2026, 8, 1, tzinfo=UTC)
    )

    listed = await client.get("/surface/debug/api/conversations", headers=_auth(_token(seeded)))

    assert listed.status_code == 200
    rows = listed.json()
    assert [row["id"] for row in rows] == [
        str(running_conversation),
        str(settled),
        str(fanned),
        str(idle),
    ]
    by_key = {row["queue_key"]: row for row in rows}
    assert by_key["C001:1.0"] == {
        "id": str(settled),
        "surface": "ufo",
        "queue_key": "C001:1.0",
        "member_email": ADMIN,
        "created_at": by_key["C001:1.0"]["created_at"],
        "turn_count": 2,
        "last_turn_at": "2026-08-20T09:30:00Z",
        "opening_message": "ask 1",
        "model": "claude-opus-4-8",
        "link": None,
    }
    assert by_key["C002:2.0"]["model"] == "claude-sonnet-5"
    assert by_key[str(running)]["surface"] == SUBAGENT_SURFACE
    assert by_key[str(running)]["model"] is None
    assert by_key["C003:3.0"]["turn_count"] == 0
    assert by_key["C003:3.0"]["opening_message"] is None


@pytest.mark.parametrize("links", [LINKS])
async def test_links_fill_the_configured_templates(debug) -> None:
    client, _ = debug
    seeded = await _seed_workspace()
    await _seed_installation(seeded, "chat", "team:T042")
    thread = await _seed_conversation(seeded, queue_key="C042:1721.5", surface="chat")
    direct = await _seed_conversation(seeded, queue_key="D042", surface="chat")
    desk = await _seed_conversation(seeded, queue_key="D1", surface="desk")
    terminal = await _seed_conversation(seeded, queue_key="s/9", surface="ufo")
    unlinked = await _seed_conversation(seeded, queue_key="x", surface="web")
    created = datetime(2026, 8, 21, 17, 5, tzinfo=UTC)
    traced = await _seed_turn(
        seeded, thread, 1, at=created, traceparent=f"00-{TRACE_ID}-00f067aa0ba902b7-01"
    )
    untraced = await _seed_turn(seeded, desk, 1, at=created)
    headers = _auth(_token(seeded))

    meta = await client.get("/surface/debug/api/workspace", headers=headers)
    listed = await client.get("/surface/debug/api/conversations", headers=headers)
    detail = await client.get(f"/surface/debug/api/turns/{traced}", headers=headers)
    bare = await client.get(f"/surface/debug/api/turns/{untraced}", headers=headers)

    assert meta.json()["installations"] == {"chat": "team:T042"}
    assert {row["id"]: row["link"] for row in listed.json()} == {
        str(thread): "https://chat.example.test/client/T042/C042/thread/C042-1721.5",
        str(direct): "https://chat.example.test/client/T042/D042",
        str(desk): None,
        str(terminal): "https://ufo.example.test/c/s%2F9",
        str(unlinked): None,
    }
    padded = timedelta(minutes=5)
    window = (
        int((created - padded).timestamp() * 1000),
        int((created + padded).timestamp() * 1000),
    )
    trace = f"trace_id%3A{TRACE_ID}"
    assert detail.json()["links"] == [
        {
            "label": "Trace",
            "url": f"{O11Y}/apm/traces?query={trace}&start={window[0]}&end={window[1]}&paused=true",
        },
        {
            "label": "Logs",
            "url": f"{O11Y}/logs?query={trace}&from_ts={window[0]}&to_ts={window[1]}",
        },
        {
            "label": "Model retries",
            "url": f"{O11Y}/logs?query={trace}+model.*&from_ts={window[0]}&to_ts={window[1]}",
        },
        {
            "label": "Model calls",
            "url": f"{O11Y}/llm/traces?query=%40session_id%3A%22{thread}%22"
            f"&start={window[0]}&end={window[1]}",
        },
    ]
    assert bare.json()["links"] == [
        {
            "label": "Model calls",
            "url": f"{O11Y}/llm/traces?query=%40session_id%3A%22{desk}%22"
            f"&start={window[0]}&end={window[1]}",
        }
    ]


async def test_turn_detail_carries_its_ledger_its_subagents_and_its_steps(debug) -> None:
    client, _ = debug
    seeded = await _seed_workspace()
    conversation = await _seed_conversation(seeded)
    parent = await _seed_turn(seeded, conversation, 1, status="running")
    await _seed_ledger(seeded, parent, "claude-sonnet-5")
    fanned = await _seed_conversation(seeded, queue_key=str(parent), surface=SUBAGENT_SURFACE)
    child = await _seed_turn(seeded, fanned, 1, parent_turn_id=parent)
    headers = _auth(_token(seeded))

    detail = (await client.get(f"/surface/debug/api/turns/{parent}", headers=headers)).json()
    steps = (await client.get(f"/surface/debug/api/turns/{parent}/steps", headers=headers)).json()

    assert detail["turn"]["id"] == str(parent)
    assert detail["model"] == "claude-sonnet-5"
    assert detail["links"] == []
    assert [(entry["dimension"], entry["amount"]) for entry in detail["ledger"]] == [
        ("tokens", 120)
    ]
    assert [turn["id"] for turn in detail["children"]] == [str(child)]
    assert detail["children"][0]["subagent_profile"] == "research"
    assert [
        (step["kind"], step["name"], step["started_at"], step["duration_ms"]) for step in steps
    ] == [
        ("model", "model round", "2026-04-26T15:00:00.123000Z", 6454),
        ("tool", "bash", "2026-04-26T15:00:06.600000Z", 120105),
    ]
    assert steps[1]["completed_at"] == "2026-04-26T15:02:06.705000Z"
    assert steps[1]["messages"][0]["content"][0]["content"] == "/workspace"


async def test_transcript_reads_the_stored_window(debug) -> None:
    client, blob = debug
    seeded = await _seed_workspace()
    conversation_id = await _seed_conversation(seeded)
    stored = Conversation(
        seq=1,
        messages=(
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="t1",
                        name="bash",
                        input={"command": "ls", "user_description": "Listing files"},
                    ),
                ),
            ),
        ),
    )
    with ws(seeded.workspace_id):
        await blob.put(transcript_key(conversation_id), encode(stored))

    response = await client.get(
        f"/surface/debug/api/conversations/{conversation_id}/transcript",
        headers=_auth(_token(seeded)),
    )

    assert response.status_code == 200
    call = response.json()["messages"][0]["content"][0]
    assert call["input"] == {"command": "ls", "user_description": "Listing files"}


async def test_rollovers_list_and_read_back_both_boundary_records(debug) -> None:
    client, blob = debug
    seeded = await _seed_workspace()
    conversation_id = await _seed_conversation(seeded)
    before = (Message(role="user", content=(TextBlock(text="the long window"),)),)
    after = (Message(role="user", content=(TextBlock(text="the fresh window"),)),)
    with ws(seeded.workspace_id):
        for half, window in (("before", before), ("after", after)):
            await blob.put(
                compaction_key(conversation_id, 1, half),
                lz4.frame.compress(CompactionWindow(messages=window).model_dump_json().encode()),
            )
            await blob.put(
                rollover_key(conversation_id, 2, half),
                lz4.frame.compress(RolloverWindow(messages=window).model_dump_json().encode()),
            )
        await blob.put(
            compaction_key(conversation_id, 1, "summary"),
            lz4.frame.compress(
                CompactionSummary(
                    intent="close the quarter", current_work="reconciling", next_step="report"
                )
                .model_dump_json()
                .encode()
            ),
        )
        await blob.put(
            rollover_key(conversation_id, 2, "recovery"),
            lz4.frame.compress(RecoveryRecord(handoff="carry on").model_dump_json().encode()),
        )
    headers = _auth(_token(seeded))
    base = f"/surface/debug/api/conversations/{conversation_id}/rollovers"

    indices = await client.get(base, headers=headers)
    compaction = (await client.get(f"{base}/1", headers=headers)).json()
    rollover = (await client.get(f"{base}/2", headers=headers)).json()

    assert indices.json() == [1, 2]
    assert "intent: close the quarter" in compaction["recovery"]["handoff"]
    assert compaction["before"][0]["content"][0]["text"] == "the long window"
    assert rollover["recovery"]["handoff"] == "carry on"
    assert rollover["after"][0]["content"][0]["text"] == "the fresh window"
    assert (await client.get(f"{base}/3", headers=headers)).status_code == 404
    assert (await client.get(f"{base}/x", headers=headers)).status_code == 404


def test_debugger_stream_names_every_live_frame_kind() -> None:
    frames: dict[type, LiveFrame] = {
        TextDelta: TextDelta(text="t"),
        Terminal: Terminal(frame=TerminalFrame(status="done", text="t")),
        Parked: Parked(message="m"),
        CostTick: CostTick(cost_micro_usd=1, tokens=2),
        Activity: Activity(text="Checking the workspace."),
        ArtifactsChanged: ArtifactsChanged(),
        Created: Created(refs=()),
        Absorbed: Absorbed(arrivals=()),
        Resumed: Resumed(attempt="attempt-one"),
        Reply: Reply(id=uuid4(), text="sent"),
        SubagentActivity: SubagentActivity(
            turn_id=uuid4(),
            parent_turn_id=uuid4(),
            conversation_id=uuid4(),
            profile="general_purpose",
        ),
        Sources: Sources(items=(SourceRef(kind="web", title="t", url="https://ex.test"),)),
    }
    assert set(frames) == set(get_args(LiveFrame))
    for frame in frames.values():
        event = debugger_surface._sse("7", frame)
        assert event.startswith(b"id: 7\nevent: ")
    assert b"event: created\n" in debugger_surface._sse("8", Created(refs=()))
