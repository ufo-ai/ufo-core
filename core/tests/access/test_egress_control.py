import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ufo.db import workspace_tx
from ufo.harness.auth.token_signing import sign_token
from ufo.harness.document_renderer import DOCUMENT_INPUT_MAX_BYTES
from ufo.harness.sandbox.session import RunToken, RunTokenCodec
from ufo.runtime.access.connectors import CliCredential, GitWire, GrantUnusable
from ufo.runtime.access.egress_control import (
    PROXY_PUBLIC_KEY_ENV,
    SESSION_STAMP_HEADER,
    SESSION_STAMP_MAX_AGE_SECONDS,
    EgressControl,
    SessionStamp,
    StampInvalid,
    load_proxy_public_key,
    verify_stamp,
)
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import CONNECTION_SECRET_PREFIX, RUN_HEADER, PolicyScope
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.billing.accounting import TURN_LABEL
from ufo.runtime.tools.bridge import ToolBridgePrincipal, ToolBridgeRequest, ToolBridgeSuccess
from ufo.runtime.workspace import ws
from ufo.schema import tables

CACHE_TOKEN = "egress-cache-secret"
RUN_TOKENS = RunTokenCodec(b"egress-control-test-token-secret")
FORGED_TOKENS = RunTokenCodec(secret=b"another-deploy-secret")
KEY = Ed25519PrivateKey.generate()
PREVIEW_ADDRESS = ("127.0.0.1", 8930)
PREVIEW_TOKEN = "preview-token"
PREVIEW_BUNDLE = b"rendered-bundle"
PREVIEW_REFUSED = b"refuse-this"
PROVIDER = "sampleprov"
HOST = "api.sample.test"
ACCOUNT = "acct-9f3c"
CLI_HEADER = "authorization"
BRIDGE_REQUEST = {
    "request_id": "00000000-0000-4000-8000-000000000001",
    "action": "get_schema",
    "tool_name": "object_list",
    "arguments": {},
}
RFC_8032_PUBLIC_KEY = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
GOLDEN_STAMP = (
    "eyJpc3N1ZWRfYXQiOjE3MDAwMDAwMDAsImxhYmVscyI6eyJhIjoiMSIsImIiOiIyIn0sInNlc3Npb25faWQiOiIyMjIy"
    "MjIyMi0yMjIyLTIyMjItMjIyMi0yMjIyMjIyMjIyMjIiLCJ3b3Jrc3BhY2VfaWQiOiIxMTExMTExMS0xMTExLTExMTEt"
    "MTExMS0xMTExMTExMTExMTEifQ._Jmu8T-nnsHQMOiAb8ykYC9wakbtWjbveZaptLHTbmtMrinCbSGkyA_vp9DjNv53DH"
    "TSEkWMdfzX45jKpQqeDw"
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _stamp(
    workspace_id: UUID,
    labels: dict[str, str],
    *,
    key: Ed25519PrivateKey = KEY,
    issued_at: datetime | None = None,
    **extra: object,
) -> str:
    payload = {
        "issued_at": int((issued_at or datetime.now(UTC)).timestamp()),
        "labels": labels,
        "session_id": str(uuid4()),
        "workspace_id": str(workspace_id),
        **extra,
    }
    return _signed(json.dumps(payload, separators=(",", ":")).encode(), key)


def _signed(payload: bytes, key: Ed25519PrivateKey = KEY) -> str:
    body = _b64url(payload)
    return f"{body}.{_b64url(key.sign(body.encode()))}"


def _basic(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode()


def _cache_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {CACHE_TOKEN}"}


@dataclass(frozen=True)
class _Tokens:
    fault: Exception | None = None
    asked: list[tuple[UUID, str]] = field(default_factory=list)

    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        self.asked.append((workspace_id, account_id))
        if self.fault is not None:
            raise self.fault
        return f"token-{account_id}"


GIT = GitWire(host="github.com", basic_user="x-access-token", helper="!gh auth git-credential")


@dataclass
class _Bridge:
    received: list[tuple[ToolBridgePrincipal, ToolBridgeRequest]] = field(default_factory=list)

    async def request(
        self, run: ToolBridgePrincipal, request: ToolBridgeRequest
    ) -> ToolBridgeSuccess:
        self.received.append((run, request))
        return ToolBridgeSuccess(result={"name": request.tool_name})


@dataclass
class _PreviewService:
    seen: list[tuple[str, str, bytes]] = field(default_factory=list)

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/render", self._render, methods=["POST"])])

    async def _render(self, request: Request) -> Response:
        body = await request.body()
        self.seen.append((request.headers["authorization"], request.headers["content-type"], body))
        if body == PREVIEW_REFUSED:
            return Response(b'{"error":"unreadable"}', 422, media_type="application/json")
        return Response(PREVIEW_BUNDLE, media_type="application/zip")


def _control(
    resolver: PerAgentRules,
    bridge: object | None = None,
    preview: _PreviewService | None = None,
) -> EgressControl:
    return EgressControl(
        cache_control_token=CACHE_TOKEN,
        resolver=resolver,
        run_tokens=RUN_TOKENS,
        bridge=bridge,
        stamp_key=KEY.public_key(),
        preview=None if preview is None else (PREVIEW_ADDRESS, PREVIEW_TOKEN),
        http=(
            None
            if preview is None
            else httpx.AsyncClient(transport=ASGITransport(app=preview.app()))
        ),
    )


def _client(control: EgressControl) -> AsyncClient:
    app = FastAPI()
    app.include_router(control.router())
    app.include_router(control.git_credential_router())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://serve")


@dataclass(frozen=True)
class _Seeded:
    workspace_id: UUID
    turn_id: UUID
    agent_id: UUID
    member_id: UUID
    conversation_id: UUID

    def headers(
        self,
        run: RunToken | None = None,
        *,
        key: Ed25519PrivateKey = KEY,
        issued_at: datetime | None = None,
        extra: dict[str, object] | None = None,
    ) -> dict[str, str]:
        labels = {TURN_LABEL: str(self.turn_id), "conversation": str(self.conversation_id)}
        stamp = _stamp(self.workspace_id, labels, key=key, issued_at=issued_at, **(extra or {}))
        return {
            SESSION_STAMP_HEADER: stamp,
            RUN_HEADER: RUN_TOKENS.encode(run or RunToken(self.workspace_id, self.turn_id)),
        }


async def _seed_turn(
    connection: AsyncConnection,
    status: str = "running",
    detached_until: datetime | None = None,
) -> _Seeded:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    await connection.execute(
        sa.insert(tables.member).values(
            id=member_id,
            workspace_id=workspace_id,
            email="a@b.c",
            seated_at=sa.func.now(),
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
            internet_access_allowed=True,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="cli",
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
            status=status,
            terminal=None if status in ("running", "parked") else {"status": status},
            detached_until=detached_until,
            inbound="hi",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return _Seeded(workspace_id, turn_id, agent_id, member_id, conversation_id)


async def _seeded(status: str = "running", detached_until: datetime | None = None) -> _Seeded:
    async with workspace_tx() as connection:
        return await _seed_turn(connection, status, detached_until)


async def _bridged(seeded: _Seeded, headers: dict[str, str]) -> tuple[httpx.Response, _Bridge]:
    bridge = _Bridge()
    async with _client(_control(PerAgentRules(), bridge=bridge)) as client:
        response = await client.post(
            "/internal/egress/tool-bridge/request", headers=headers, json=BRIDGE_REQUEST
        )
    return response, bridge


def _refused(response: httpx.Response, bridge: _Bridge) -> bool:
    return (response.status_code, response.json(), bridge.received) == (
        403,
        {"detail": "forbidden"},
        [],
    )


async def test_a_stamped_route_with_core_s_run_token_reaches_the_bridge_as_the_member_it_acts_for(
    db: None,
) -> None:
    seeded = await _seeded()
    run = RunToken(seeded.workspace_id, seeded.turn_id, acts_for=seeded.member_id)

    response, bridge = await _bridged(seeded, seeded.headers(run))

    assert response.json() == {"ok": True, "result": {"name": "object_list"}}
    assert bridge.received == [
        (
            ToolBridgePrincipal(run.workspace_id, run.turn_id, seeded.member_id),
            ToolBridgeRequest.model_validate(BRIDGE_REQUEST),
        )
    ]


async def test_a_verified_request_with_a_malformed_body_answers_422(db: None) -> None:
    seeded = await _seeded()
    bridge = _Bridge()
    async with _client(_control(PerAgentRules(), bridge=bridge)) as client:
        malformed = await client.post(
            "/internal/egress/tool-bridge/request", headers=seeded.headers(), json={}
        )
        unparsed = await client.post(
            "/internal/egress/tool-bridge/request", headers=seeded.headers(), content=b"{"
        )
    assert (malformed.status_code, unparsed.status_code, bridge.received) == (422, 422, [])


async def test_the_bridge_refuses_a_missing_or_malformed_stamp(db: None) -> None:
    seeded = await _seeded()
    run = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    for stamp in (None, "", "no-separator", "a.b", f"{seeded.headers()[SESSION_STAMP_HEADER]}x"):
        headers = (
            {RUN_HEADER: run} if stamp is None else {RUN_HEADER: run, SESSION_STAMP_HEADER: stamp}
        )
        assert _refused(*await _bridged(seeded, headers)), stamp


async def test_the_bridge_refuses_a_stale_stamp(db: None) -> None:
    seeded = await _seeded()
    now = datetime.now(UTC)
    skew = timedelta(seconds=SESSION_STAMP_MAX_AGE_SECONDS + 5)
    for issued_at in (now - skew, now + skew):
        assert _refused(*await _bridged(seeded, seeded.headers(issued_at=issued_at)))


async def test_the_bridge_refuses_a_stamp_by_another_key(db: None) -> None:
    seeded = await _seeded()
    headers = seeded.headers(key=Ed25519PrivateKey.generate())
    assert _refused(*await _bridged(seeded, headers))


async def test_the_bridge_refuses_a_stamp_of_another_shape(db: None) -> None:
    seeded = await _seeded()
    run = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    session_token = _signed(f"ufo-session/{seeded.workspace_id}/{uuid4()}".encode())
    for stamp in (seeded.headers(extra={"v": 1})[SESSION_STAMP_HEADER], session_token):
        headers = {RUN_HEADER: run, SESSION_STAMP_HEADER: stamp}
        assert _refused(*await _bridged(seeded, headers))


async def test_the_bridge_refuses_a_stamp_naming_another_workspace(db: None) -> None:
    seeded = await _seeded()
    other = await _seeded()
    headers = {
        **seeded.headers(),
        SESSION_STAMP_HEADER: other.headers()[SESSION_STAMP_HEADER],
    }
    assert _refused(*await _bridged(seeded, headers))


async def test_the_bridge_refuses_a_missing_or_foreign_run_token(db: None) -> None:
    seeded = await _seeded()
    stamp = seeded.headers()[SESSION_STAMP_HEADER]
    run = RunToken(seeded.workspace_id, seeded.turn_id)
    for token in (
        None,
        "",
        FORGED_TOKENS.encode(run),
        _basic(RUN_TOKENS.encode(run)),
        sign_token(
            RUN_TOKENS.secret, f"ufo-probe/{seeded.workspace_id}/{seeded.turn_id}/-".encode()
        ),
    ):
        headers = (
            {SESSION_STAMP_HEADER: stamp}
            if token is None
            else {
                SESSION_STAMP_HEADER: stamp,
                RUN_HEADER: token,
            }
        )
        assert _refused(*await _bridged(seeded, headers)), token


async def test_the_bridge_refuses_a_turn_label_that_is_not_the_run_token_s(db: None) -> None:
    seeded = await _seeded()
    sibling = await _seeded()
    run = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    for labels in ({TURN_LABEL: str(uuid4())}, {}, {"conversation": str(seeded.conversation_id)}):
        headers = {SESSION_STAMP_HEADER: _stamp(seeded.workspace_id, labels), RUN_HEADER: run}
        assert _refused(*await _bridged(seeded, headers))
    sibling_run = RUN_TOKENS.encode(RunToken(sibling.workspace_id, sibling.turn_id))
    assert _refused(*await _bridged(seeded, {**seeded.headers(), RUN_HEADER: sibling_run}))


@pytest.mark.parametrize(
    ("status", "detached"),
    [("parked", False), ("done", False), ("done", True)],
    ids=["parked", "done", "detached-only"],
)
async def test_the_bridge_refuses_a_turn_that_is_not_running(
    db: None, status: str, detached: bool
) -> None:
    seeded = await _seeded(status, datetime.now(UTC) + timedelta(minutes=10) if detached else None)
    assert _refused(*await _bridged(seeded, seeded.headers()))


async def test_the_bridge_route_refuses_when_no_bridge_is_wired(db: None) -> None:
    seeded = await _seeded()
    async with _client(_control(PerAgentRules())) as client:
        response = await client.post(
            "/internal/egress/tool-bridge/request", headers=seeded.headers(), json=BRIDGE_REQUEST
        )
    assert (response.status_code, response.json()) == (403, {"detail": "forbidden"})


async def _rendered(
    seeded: _Seeded, preview: _PreviewService, content: bytes | AsyncIterator[bytes]
) -> httpx.Response:
    async with _client(_control(PerAgentRules(), preview=preview)) as client:
        return await client.post(
            "/internal/egress/preview/render",
            headers={**seeded.headers(), "content-type": "multipart/form-data; boundary=b"},
            content=content,
        )


async def test_the_preview_relay_forwards_the_body_with_the_real_bearer_and_streams_the_answer_back(
    db: None,
) -> None:
    seeded = await _seeded()
    preview = _PreviewService()
    body = b"--b\r\ncontent-disposition: form-data; name=file\r\n\r\nhello\r\n--b--\r\n"

    rendered = await _rendered(seeded, preview, body)
    refused = await _rendered(seeded, preview, PREVIEW_REFUSED)

    assert (rendered.status_code, rendered.content) == (200, PREVIEW_BUNDLE)
    assert rendered.headers["content-type"] == "application/zip"
    assert (refused.status_code, refused.json()) == (422, {"error": "unreadable"})
    assert preview.seen == [
        (f"Bearer {PREVIEW_TOKEN}", "multipart/form-data; boundary=b", body),
        (f"Bearer {PREVIEW_TOKEN}", "multipart/form-data; boundary=b", PREVIEW_REFUSED),
    ]


async def test_the_preview_relay_refuses_an_oversized_body(db: None) -> None:
    seeded = await _seeded()
    preview = _PreviewService()

    async def chunked() -> AsyncIterator[bytes]:
        yield b"unsized"

    async with _client(_control(PerAgentRules(), preview=preview)) as client:
        request = client.build_request(
            "POST",
            "/internal/egress/preview/render",
            headers={**seeded.headers(), "content-type": "multipart/form-data; boundary=b"},
            content=b"x",
        )
        request.headers["content-length"] = str(DOCUMENT_INPUT_MAX_BYTES + 1)
        oversized = await client.send(request)
    unsized = await _rendered(seeded, preview, chunked())

    assert oversized.status_code == 413
    assert unsized.status_code == 413
    assert preview.seen == []


async def test_the_preview_relay_answers_502_when_the_preview_service_is_unreachable(
    db: None,
) -> None:
    seeded = await _seeded()

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    control = EgressControl(
        cache_control_token=CACHE_TOKEN,
        resolver=PerAgentRules(),
        run_tokens=RUN_TOKENS,
        stamp_key=KEY.public_key(),
        preview=(PREVIEW_ADDRESS, PREVIEW_TOKEN),
        http=httpx.AsyncClient(transport=httpx.MockTransport(unreachable)),
    )
    async with _client(control) as client:
        response = await client.post(
            "/internal/egress/preview/render",
            headers={**seeded.headers(), "content-type": "text/plain"},
            content=b"x",
        )
    assert (response.status_code, response.json()) == (
        502,
        {"detail": "The preview service did not answer."},
    )


async def test_the_preview_relay_refuses_an_unstamped_request(db: None) -> None:
    seeded = await _seeded()
    preview = _PreviewService()
    async with _client(_control(PerAgentRules(), preview=preview)) as client:
        response = await client.post(
            "/internal/egress/preview/render",
            headers={RUN_HEADER: seeded.headers()[RUN_HEADER], "content-type": "text/plain"},
            content=b"x",
        )
    assert (response.status_code, preview.seen) == (403, [])


async def test_the_preview_route_is_absent_with_no_preview_service(db: None) -> None:
    seeded = await _seeded()
    async with _client(_control(PerAgentRules(), bridge=_Bridge())) as client:
        response = await client.post(
            "/internal/egress/preview/render", headers=seeded.headers(), content=b"x"
        )
    assert response.status_code == 404


def test_the_stamp_verifies_the_proxy_service_s_golden_signature() -> None:
    issued = datetime.fromtimestamp(1_700_000_000, UTC)
    key = load_proxy_public_key(RFC_8032_PUBLIC_KEY)

    assert verify_stamp(key, GOLDEN_STAMP, issued) == SessionStamp(
        issued_at=1_700_000_000,
        labels={"a": "1", "b": "2"},
        session_id=UUID(int=0x2222_2222_2222_2222_2222_2222_2222_2222),
        workspace_id=UUID(int=0x1111_1111_1111_1111_1111_1111_1111_1111),
    )
    with pytest.raises(StampInvalid):
        verify_stamp(
            key, GOLDEN_STAMP, issued + timedelta(seconds=SESSION_STAMP_MAX_AGE_SECONDS + 1)
        )


def test_the_proxy_public_key_is_the_raw_key_in_standard_base64() -> None:
    raw = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    loaded = load_proxy_public_key(base64.b64encode(raw).decode())
    assert loaded.public_bytes(Encoding.Raw, PublicFormat.Raw) == raw
    pem = KEY.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    for raw in ("", "not base64!", base64.b64encode(b"short").decode(), pem):
        with pytest.raises(RuntimeError, match=PROXY_PUBLIC_KEY_ENV):
            load_proxy_public_key(raw)


async def _seed_git_cli(
    shared: bool = True, fault: Exception | None = None
) -> tuple[_Seeded, PerAgentRules, _Tokens]:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=shared,
        )
    tokens = _Tokens(fault=fault)
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, secret=tokens, git=GIT)}
    return seeded, PerAgentRules(grants=GrantStore(), clis=clis), tokens


async def test_git_credential_answers_the_granted_accounts_token_for_the_wired_host(
    db: None,
) -> None:
    seeded, resolver, tokens = await _seed_git_cli()
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"proxy_auth": _basic(token), "host": GIT.host},
        )
    assert response.status_code == 200
    assert response.json() == {
        "username": "x-access-token",
        "token": f"token-{ACCOUNT}",
        "principal": f"w{seeded.workspace_id}-{ACCOUNT}",
    }
    assert tokens.asked == [(seeded.workspace_id, ACCOUNT)]


async def test_git_credential_is_public_without_a_principal(db: None) -> None:
    _, resolver, tokens = await _seed_git_cli()
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential", headers=_cache_auth(), json={"host": GIT.host}
        )
    assert response.status_code == 200
    assert response.json() == {"principal": "public"}
    assert tokens.asked == []


async def test_git_credential_is_public_for_a_host_no_cli_clones_through(db: None) -> None:
    seeded, resolver, tokens = await _seed_git_cli()
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"proxy_auth": _basic(token), "host": "gitlab.com"},
        )
    assert response.status_code == 200
    assert response.json() == {"credential": None, "principal": "public"}
    assert tokens.asked == []


async def test_git_credential_is_public_for_a_private_grant_the_principal_cannot_use(
    db: None,
) -> None:
    seeded, resolver, tokens = await _seed_git_cli(shared=False)
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"proxy_auth": _basic(token), "host": GIT.host},
        )
    assert response.json() == {"credential": None, "principal": "public"}
    assert tokens.asked == []


@pytest.mark.parametrize(
    "fault",
    [
        GrantUnusable("reconnect the account", awaits_grant=True),
        RuntimeError("broker 503"),
    ],
    ids=["unhealthy-account", "unreachable-broker"],
)
async def test_git_credential_is_public_when_the_broker_will_not_answer(
    db: None, fault: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """Reading the account's token is a call to the broker, so it is uncertain the way every
    broker call is."""
    seeded, resolver, tokens = await _seed_git_cli(fault=fault)
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    with caplog.at_level(logging.WARNING, logger="ufo"):
        async with _client(_control(resolver)) as client:
            response = await client.post(
                "/internal/git-credential",
                headers=_cache_auth(),
                json={"proxy_auth": _basic(token), "host": GIT.host},
            )

    assert response.status_code == 200
    assert response.json() == {"credential": None, "principal": "public"}
    assert tokens.asked == [(seeded.workspace_id, ACCOUNT)]
    withheld = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "egress.git_credential_failed"
    ]
    assert [(entry["provider"], entry["account_id"]) for entry in withheld] == [(PROVIDER, ACCOUNT)]
    assert withheld[0]["error_class"] == type(fault).__name__


async def test_git_credential_is_gated_by_the_cache_token_alone(db: None) -> None:
    seeded = await _seeded()
    body = {"host": GIT.host}
    async with _client(_control(PerAgentRules(), bridge=_Bridge())) as client:
        none = await client.post("/internal/git-credential", json=body)
        stamped = await client.post("/internal/git-credential", headers=seeded.headers(), json=body)
        cross = await client.post(
            "/internal/egress/tool-bridge/request", headers=_cache_auth(), json=BRIDGE_REQUEST
        )
    assert none.status_code == 401
    assert stamped.status_code == 401
    assert cross.status_code == 403


async def _bound(resolver: PerAgentRules, seeded: _Seeded, member_id: UUID | None) -> set[str]:
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        policy = await resolver.session_policy(
            PolicyScope(seeded.workspace_id, member_id, True, False, None)
        )
    return {bind.secret for bind in policy.bind}


async def test_session_binds_and_git_credentials_prefer_the_members_private_account(
    db: None,
) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        private = await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=False,
        )
        shared = await GrantStore().record(
            provider=PROVIDER,
            account_id="acct-other",
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=True,
        )
    tokens = _Tokens()
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, secret=tokens, git=GIT)}
    resolver = PerAgentRules(grants=GrantStore(), clis=clis)
    acting = RunToken(seeded.workspace_id, seeded.turn_id, acts_for=seeded.member_id)
    nobody = RunToken(seeded.workspace_id, seeded.turn_id)

    acting_credential = await resolver.git_credential(acting, GIT.host)
    nobody_credential = await resolver.git_credential(nobody, GIT.host)

    assert await _bound(resolver, seeded, seeded.member_id) == {
        f"{CONNECTION_SECRET_PREFIX}{private}"
    }
    assert acting_credential == (GIT, f"token-{ACCOUNT}", ACCOUNT)
    assert await _bound(resolver, seeded, None) == {f"{CONNECTION_SECRET_PREFIX}{shared}"}
    assert nobody_credential == (GIT, "token-acct-other", "acct-other")


async def test_a_run_acting_for_a_member_loses_a_disconnected_grant(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    store = GrantStore()
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        connection_id = await store.record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=False,
        )
    run = RunToken(seeded.workspace_id, seeded.turn_id, acts_for=seeded.member_id)
    tokens = _Tokens()
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, secret=tokens, git=GIT)}
    resolver = PerAgentRules(grants=store, clis=clis)

    granted = await _bound(resolver, seeded, seeded.member_id)
    granted_credential = await resolver.git_credential(run, GIT.host)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        assert await store.disconnect(connection_id, actor_member_id=seeded.member_id) is True
    removed = await _bound(resolver, seeded, seeded.member_id)
    removed_credential = await resolver.git_credential(run, GIT.host)

    assert granted == {f"{CONNECTION_SECRET_PREFIX}{connection_id}"}
    assert granted_credential == (GIT, f"token-{ACCOUNT}", ACCOUNT)
    assert removed == set()
    assert removed_credential is None
