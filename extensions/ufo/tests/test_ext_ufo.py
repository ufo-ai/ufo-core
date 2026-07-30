import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_ufo.manifest import manifest as ufo_manifest
from ufo_ext_ufo.surface import (
    HOLD_SECONDS,
    PROMPT,
    directive,
    directives_for,
    resolve_workspace,
    stream_directives,
)
from ufo_testsupport.surfaces import EMPTY_SKILL_REGISTRY, no_user_skills

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.credentials import (
    CredentialRequestState,
    CredentialStore,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.grants import ConnectFlow, GrantStore, OAuthAccount, install_connect_flow
from ufo.hub import CostTick, InProcessHub, Parked, SkillLoad, Terminal, ToolCall
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import CredentialPrompt, CredentialRequest, TerminalFrame, Usage
from ufo.sdk.bearer import verify_token, workspace_claim
from ufo.sdk.surfaces import ConnectRequest, SurfaceAuth
from ufo.serve import _mount_shared_surfaces

SECRET = "ufo-token-secret"
STREAM_TIMEOUT_SECONDS = 30


def _mint(secret: str, workspace_id: UUID, email: str, exp: int) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"ws": str(workspace_id), "email": email, "exp": exp}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _future() -> int:
    return int(datetime.now(tz=UTC).timestamp()) + 3600


def _lines(body: bytes) -> list[list[str]]:
    return [line.split("\t") for line in body.decode().splitlines()]


def test_directive_escapes_tabs_newlines_and_backslashes() -> None:
    assert directive("say", "hello") == b"say\thello\n"
    assert directive("txt", "a\tb\nc\\d\r") == b"txt\ta\\tb\\nc\\\\d\n"
    assert directive("ask", PROMPT) == b"ask\t>\n"
    assert directive("poll", "1") == b"poll\t1\n"


def test_frame_map_covers_every_live_frame() -> None:
    assert directives_for(TextDelta(text="hi"), streamed=False) == (b"txt\thi\n",)
    assert directives_for(TextDelta(text=""), streamed=False) == ()
    assert directives_for(ToolCall(tool="bash", preview="ls", description=""), False) == (
        b"note\trunning bash: ls\n",
    )
    assert directives_for(ToolCall(tool="bash", preview="", description="listing"), False) == (
        b"note\trunning bash: listing\n",
    )
    assert directives_for(SkillLoad(skill="demo"), False) == (b"note\tloading skill: demo\n",)
    assert directives_for(CostTick(cost_micro_usd=55_000, tokens=3000), False) == (
        b"status\t3000 tok - $0.055000\n",
    )


def test_terminal_frame_maps_by_status_and_streamed() -> None:
    done = Terminal(frame=TerminalFrame(status="done", text="line one\nline two"))
    assert directives_for(done, streamed=False) == (
        b"say\tline one\n",
        b"say\tline two\n",
        b"ask\t>\n",
    )
    assert directives_for(done, streamed=True) == (b"ask\t>\n",)
    failed = Terminal(frame=TerminalFrame(status="failed", error_class="ModelError"))
    assert directives_for(failed, streamed=True) == (b"say\tModelError\n", b"ask\t>\n")
    cancelled = Terminal(frame=TerminalFrame(status="cancelled"))
    assert directives_for(cancelled, streamed=True) == (b"say\tcancelled\n", b"exit\t0\n")
    assert directives_for(Parked(message="over cap"), False) == (b"say\tover cap\n", b"ask\t>\n")


def _request() -> CredentialRequest:
    return CredentialRequest(
        reason="Connecting Slack needs two values.",
        prompts=(
            CredentialPrompt(slot="slack_bot_token", prompt="Bot User OAuth Token"),
            CredentialPrompt(slot="slack_signing_secret", prompt="Signing Secret"),
        ),
        sealed="sealed-opaque",
    )


def test_pending_credential_prompts_render_individually() -> None:
    """A done turn prompts exactly the still-unanswered slots — all, one, or none — so a stored
    sibling never re-prompts while a missing one keeps asking."""
    request = _request()
    done = Terminal(frame=TerminalFrame(status="done", text="t", credential_request=request))
    assert directives_for(done, streamed=True, collect=request.prompts) == (
        b"secret\tsealed-opaque\tslack_bot_token\tBot User OAuth Token\n",
        b"secret\tsealed-opaque\tslack_signing_secret\tSigning Secret\n",
        b"ask\t>\n",
    )
    assert directives_for(done, streamed=True, collect=request.prompts[1:]) == (
        b"secret\tsealed-opaque\tslack_signing_secret\tSigning Secret\n",
        b"ask\t>\n",
    )
    assert directives_for(done, streamed=True) == (b"ask\t>\n",)


async def test_stream_gates_each_secret_prompt_on_the_pending_check() -> None:
    def frames() -> AsyncIterator[tuple[str, Terminal]]:
        async def gen() -> AsyncIterator[tuple[str, Terminal]]:
            frame = TerminalFrame(status="done", text="t", credential_request=_request())
            yield ("c1", Terminal(frame=frame))

        return gen()

    async def fulfilled(sealed: str, slot: str) -> bool:
        assert sealed == "sealed-opaque"
        return False

    async def token_only(sealed: str, slot: str) -> bool:
        return slot == "slack_bot_token"

    gated = [line async for line in stream_directives(frames(), 5.0, fulfilled)]
    assert not any(line.startswith(b"secret\t") for line in gated)
    partial = [line async for line in stream_directives(frames(), 5.0, token_only)]
    secrets = [line for line in partial if line.startswith(b"secret\t")]
    assert secrets == [b"secret\tsealed-opaque\tslack_bot_token\tBot User OAuth Token\n"]


async def test_stream_privately_renders_a_connect_handoff() -> None:
    async def frames() -> AsyncIterator[tuple[str, Terminal]]:
        yield (
            "c1",
            Terminal(
                frame=TerminalFrame(
                    status="done",
                    text="Use the connection control.",
                    connect_request=ConnectRequest(provider="github", requester_member_id=uuid4()),
                )
            ),
        )

    async def connect() -> str:
        return "https://oauth.example.test/authorize"

    lines = [line async for line in stream_directives(frames(), 5.0, connect=connect)]
    assert lines == [
        b"say\tUse the connection control.\n",
        b"say\tComplete the connection: https://oauth.example.test/authorize\n",
        b"ask\t>\n",
    ]


def test_valid_token_verifies_to_its_lowered_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "Owner@Example.com", _future())
    assert verify_token(token, workspace_id) == "owner@example.com"


def test_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future() - 7200)
    assert verify_token(token, workspace_id) is None


def test_token_for_another_workspace_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    token = _mint(SECRET, uuid4(), "owner@example.com", _future())
    assert verify_token(token, uuid4()) is None


def test_tampered_signature_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    payload, _, signature = token.partition(".")
    forged = f"{payload}.{signature[:-1]}{'0' if signature[-1] != '0' else '1'}"
    assert verify_token(forged, workspace_id) is None
    assert verify_token("not-a-token", workspace_id) is None
    assert (
        verify_token(
            _mint("other-secret", workspace_id, "owner@example.com", _future()), workspace_id
        )
        is None
    )


def test_workspace_claim_returns_the_signed_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared fleet resolves scope from the signed claim itself — no pinned workspace to match
    against. A valid token yields its `ws` uuid; forged, expired, or non-uuid yields None."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    assert workspace_claim(token) == workspace_id
    assert workspace_claim(_mint(SECRET, workspace_id, "o@x.com", _future() - 7200)) is None
    assert workspace_claim(_mint("other-secret", workspace_id, "o@x.com", _future())) is None
    assert workspace_claim("not-a-token") is None


def _get_request(headers: dict[str, str]) -> StarletteRequest:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return StarletteRequest({"type": "http", "method": "POST", "headers": raw})


async def test_resolve_workspace_reads_the_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """SurfaceSpec.identify: the workspace a request's bearer claims, or None to reject — the shared
    fleet binds it before the handler runs. A missing or non-bearer authorization yields None."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    workspace_id = uuid4()
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    auth = SurfaceAuth(_credentials=None, _declared=frozenset(), _surface="ufo")
    assert (
        await resolve_workspace(_get_request({"authorization": f"Bearer {token}"}), auth)
        == workspace_id
    )
    assert await resolve_workspace(_get_request({}), auth) is None
    assert await resolve_workspace(_get_request({"authorization": token}), auth) is None


@dataclass(frozen=True)
class _Never:
    """A tail that yields one non-terminal frame then blocks forever — the turn that outruns the
    hold, so the stream must end on `poll`."""

    async def __anext__(self) -> tuple[str, TextDelta]:
        if not getattr(self, "_sent", False):
            object.__setattr__(self, "_sent", True)
            return "1", TextDelta(text="working")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def __aiter__(self) -> "_Never":
        return self

    async def aclose(self) -> None: ...


async def _feed(frames: list[tuple[str, object]]) -> AsyncIterator[tuple[str, object]]:
    for item in frames:
        yield item


async def test_hold_expires_and_the_stream_ends_with_poll() -> None:
    assert HOLD_SECONDS == 85.0
    out = b"".join([chunk async for chunk in stream_directives(_Never(), hold_seconds=0.05)])
    lines = _lines(out)
    assert lines[0] == ["txt", "working"]
    assert lines[-1] == ["poll", "1"]


async def test_terminal_frame_closes_the_stream_without_polling() -> None:
    frames = _feed(
        [("1", TextDelta(text="echo:1")), ("2", Terminal(frame=TerminalFrame(status="done")))]
    )
    out = b"".join([chunk async for chunk in stream_directives(frames, hold_seconds=HOLD_SECONDS)])
    lines = _lines(out)
    assert lines == [["txt", "echo:1"], ["ask", ">"]]


@dataclass(frozen=True)
class StandInModel:
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
class StubConnectProvider:
    provider: str = "github"
    host: str = "api.github.test"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://oauth.example.test/authorize?state={state}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id="github-account")


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
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _seed_member(workspace_id: UUID, email: str) -> UUID:
    member_id = uuid4()
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
    return member_id


@pytest.fixture(scope="session")
def runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox]]:
    config = dbos_launched
    hub = InProcessHub()
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
            run_tokens=RunTokenCodec(b"ufo-test-run-token-secret"),
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
async def ufo(
    db: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id = await _seed_workspace()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ufo") as client:
        yield client, workspace_id
    dbos_client.destroy()


@pytest.fixture
async def shared_ufo(
    db: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """The ufo surface on the shared fleet: mounted with no boot-pinned workspace, so every request
    scopes itself from its bearer through `_mount_shared_surfaces`. One app, every workspace."""
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        yield client
    dbos_client.destroy()


async def _sole_turn(workspace_id: UUID) -> tuple[UUID, str]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.status).where(
                    tables.turn.c.workspace_id == workspace_id
                )
            )
        ).one()
    return row.id, row.status


async def test_shared_fleet_scopes_each_turn_to_its_token_workspace(
    shared_ufo: AsyncClient,
) -> None:
    """No boot-pinned workspace: `_mount_shared_surfaces` resolves each request's workspace from its
    signed `{ws,email}` bearer, binds it, and admits the turn under exactly that workspace — two
    workspaces through one mounted app, each scoped by its token, and the live turn streams to
    done."""
    ws_a, ws_b = await _seed_workspace(), await _seed_workspace()
    await _seed_member(ws_a, "a@example.com")
    await _seed_member(ws_b, "b@example.com")
    token_a = _mint(SECRET, ws_a, "a@example.com", _future())
    token_b = _mint(SECRET, ws_b, "b@example.com", _future())
    lines_a = await _post(shared_ufo, "main", token_a, b"hi a")
    lines_b = await _post(shared_ufo, "main", token_b, b"hi b")
    for lines in (lines_a, lines_b):
        answer = "".join(f for verb, *rest in lines if verb in ("txt", "say") for f in rest)
        assert "echo:1" in answer
        assert lines[-1] == ["ask", ">"]
    turn_a, status_a = await _sole_turn(ws_a)
    turn_b, status_b = await _sole_turn(ws_b)
    assert (status_a, status_b) == ("done", "done")
    assert turn_a != turn_b


async def test_shared_fleet_rejects_a_forged_or_missing_bearer(shared_ufo: AsyncClient) -> None:
    """A token this fleet's secret did not sign, and no token at all, are both 401 before any turn
    is admitted — the signed workspace claim is the only authority the shared fleet trusts."""
    ws = await _seed_workspace()
    forged = _mint("wrong-secret", ws, "a@example.com", _future())
    denied = await shared_ufo.post(
        "/surface/ufo/main", content=b"hi", headers={"authorization": f"Bearer {forged}"}
    )
    missing = await shared_ufo.post("/surface/ufo/main", content=b"hi")
    assert denied.status_code == 401
    assert missing.status_code == 401
    assert await _turn_count(ws) == 0


async def _post(client: AsyncClient, channel: str, token: str, body: bytes) -> list[list[str]]:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        response = await client.post(
            f"/surface/ufo/{channel}",
            content=body,
            headers={"authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return _lines(response.content)


async def _turn_count(workspace_id: UUID) -> int:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()


async def test_message_admits_a_turn_streams_it_and_links_the_member(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    lines = await _post(client, "main", token, b"hello")
    answer = "".join(field for verb, *rest in lines if verb in ("txt", "say") for field in rest)
    assert "echo:1" in answer
    assert lines[-1][0] in ("ask", "exit")
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == "ufo",
                    tables.surface_identity.c.external_id == "owner@example.com",
                )
            )
        ).one()
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id, tables.conversation.c.queue_key).where(
                    tables.conversation.c.surface == "ufo"
                )
            )
        ).one()
    assert linked.member_id == member_id
    assert conversation.member_id == member_id
    assert conversation.queue_key == "owner@example.com:main"


async def test_admitted_turn_carries_the_member_and_the_terminal_as_its_source(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"hello")
    async with workspace_tx() as connection:
        context = (
            await connection.execute(
                sa.select(tables.turn.c.context).where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert context == {
        "sender": "owner@example.com",
        "timezone": None,
        "source": "ufo cli (owner@example.com)",
    }


async def test_empty_body_polls_without_admitting_a_turn(ufo: tuple[AsyncClient, UUID]) -> None:
    client, workspace_id = ufo
    await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    await _post(client, "main", token, b"hello")
    assert await _turn_count(workspace_id) == 1
    polled = await _post(client, "main", token, b"")
    assert await _turn_count(workspace_id) == 1
    answer = "".join(field for verb, *rest in polled if verb in ("txt", "say") for field in rest)
    assert "echo:1" in answer
    assert polled[-1][0] in ("ask", "exit")


async def test_empty_body_privately_opens_the_latest_connect_handoff(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    member_id = await _seed_member(workspace_id, "owner@example.com")
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    assert await _post(client, "main", token, b"") == [["ask", ">"]]
    flow = ConnectFlow(
        providers={"github": StubConnectProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri="https://ufo.example.test/v1/connect/callback",
    )
    install_connect_flow(flow)
    turn_id = uuid4()
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.surface == "ufo",
                )
            )
        ).scalar_one()
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
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
        lines = await _post(client, "main", token, b"")
    finally:
        install_connect_flow(None)
    assert lines[0] == ["say", "Use the connection control."]
    assert lines[1][0] == "say"
    assert lines[1][1].startswith("Complete the connection: https://oauth.example.test/authorize")
    assert lines[2] == ["ask", ">"]
    async with workspace_tx() as connection:
        memoized_url = (
            await connection.execute(
                sa.select(tables.turn.c.connect_authorization_url).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).scalar_one()
    assert memoized_url in lines[1][1]


async def test_unknown_bearer_is_rejected(ufo: tuple[AsyncClient, UUID]) -> None:
    client, workspace_id = ufo
    forged = _mint("wrong-secret", workspace_id, "owner@example.com", _future())
    denied = await client.post(
        "/surface/ufo/main", content=b"hi", headers={"authorization": f"Bearer {forged}"}
    )
    assert denied.status_code == 401
    missing = await client.post("/surface/ufo/main", content=b"hi")
    assert missing.status_code == 401


async def test_email_matching_no_member_gets_an_unlinked_conversation(
    ufo: tuple[AsyncClient, UUID],
) -> None:
    client, workspace_id = ufo
    token = _mint(SECRET, workspace_id, "stranger@example.com", _future())
    polled = await _post(client, "main", token, b"")
    assert polled[-1] == ["ask", ">"]
    assert await _turn_count(workspace_id) == 0
    async with workspace_tx() as connection:
        conversation = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.surface == "ufo"
                )
            )
        ).one()
        identity = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == "ufo"
                )
            )
        ).one_or_none()
    assert conversation.member_id is None
    assert identity is None


async def test_secret_fulfillment_lands_in_the_store_never_the_transcript(
    db: None,
    runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other end of the `secret` directive: the shell POSTs each privately-entered value with
    the sealed request, the surface verifies the seal and writes the encrypted slot, and no turn is
    admitted — the secret never becomes a message. Only the member the request was sealed for may
    fulfill it, only for slots it named, under the size bound."""
    config, hub, blob, sandboxes = runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", SECRET)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    workspace_id = await _seed_workspace()
    owner = await _seed_member(workspace_id, "owner@example.com")
    await _seed_member(workspace_id, "late@example.com")
    sealed = seal_credential_request(
        store.fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=owner,
            slots=("slack_bot_token", "slack_signing_secret"),
        ),
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        store,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
    )
    token = _mint(SECRET, workspace_id, "owner@example.com", _future())
    foreign = _mint(SECRET, workspace_id, "late@example.com", _future())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ufo") as client:

            async def send(bearer: str, slot: str, value: bytes):
                return await client.post(
                    "/surface/ufo/main",
                    content=value,
                    headers={
                        "authorization": f"Bearer {bearer}",
                        "x-ufo-secret": sealed,
                        "x-ufo-slot": slot,
                    },
                )

            last_first = await send(token, "slack_signing_secret", b"shhh")
            assert last_first.status_code == 200
            assert last_first.content == b"say\tstored slack_signing_secret\n"
            first = await send(token, "slack_bot_token", b"xoxb-real")
            assert first.status_code == 200
            assert first.content == b"say\tstored slack_bot_token\n"
            assert await store.get(workspace_id, "slack_bot_token") == "xoxb-real"
            assert await store.get(workspace_id, "slack_signing_secret") == "shhh"
            assert await _turn_count(workspace_id) == 0
            denied = await send(foreign, "slack_bot_token", b"xoxb-evil")
            assert denied.status_code == 403
            assert await store.get(workspace_id, "slack_bot_token") == "xoxb-real"
            offslot = await send(token, "unrelated_slot", b"v")
            assert offslot.status_code == 403
            oversized = await send(token, "slack_bot_token", b"x" * 5000)
            assert oversized.status_code == 413
            garbage = await send(token, "slack_bot_token", b"")
            assert garbage.status_code == 400
    finally:
        dbos_client.destroy()
