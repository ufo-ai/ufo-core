import asyncio
import hashlib
import json
import socket
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from ufo_testsupport.invoker import RecordedTurn, RecordingInvoker
from uvicorn._types import ASGIApplication, ASGIReceiveCallable, ASGISendCallable, Scope
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Origin, Subprotocol

from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.db import dispose_db, workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.sandbox import ingress_serve
from ufo.harness.sandbox.ingress_host import serve_port, shipped_anchor, site_label
from ufo.harness.sandbox.ingress_serve import (
    CACHE_DIRECTIVE_HEADERS,
    CONTENT_SECURITY_POLICY,
    DOCUMENT_DESTINATIONS,
    EDGE_REPLACED_STATUSES,
    FETCH_DESTINATION_HEADER,
    FOREIGN_ORIGIN,
    FRAME_ANCESTORS_DIRECTIVE,
    HEARTBEAT_MEDIA_TYPE,
    HEARTBEAT_PING_PATH,
    HEARTBEAT_PING_SECONDS,
    HEARTBEAT_RENEWAL_SECONDS,
    HEARTBEAT_SCRIPT,
    HEARTBEAT_SCRIPT_PATH,
    HEARTBEAT_TAG,
    INGRESS_SESSION_COOKIE,
    INGRESS_SESSION_TTL_SECONDS,
    LINK_NOT_VALID,
    NO_FRAME_ANCESTOR,
    NO_SITE_HERE,
    NOT_FOUND,
    SESSION_ENDED_PAGE,
    SHIPPED_CACHE,
    SHIPPED_VERSION_PARAM,
    SITE_GONE,
    SITE_HAS_NO_SOCKET,
    SITE_NOT_ANSWERING,
    SITE_WAITING_POLICY,
    SITE_WAITING_RELOAD_SECONDS,
    STORED_SITE_CACHE,
    UNCACHEABLE,
    WEBSOCKET_MAX_MESSAGE_BYTES,
    WRONG_SITE,
    IngressServe,
    ingress_base_host,
    ingress_frame_ancestor,
    upstream_client,
)
from ufo.harness.sandbox.ingress_token import (
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    FramerClaim,
    IngressClaims,
    IngressTokenKind,
    ShippedClaim,
    mint_ingress_token,
    verify_ingress_token,
)
from ufo.harness.sandbox.session import (
    Carrier,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
)
from ufo.harness.sandbox.site_report import (
    REPORT_BUCKET_SECONDS,
    SITE_NOT_ANSWERING_FIRE,
    SiteReporter,
    SiteReports,
)
from ufo.runtime.ext.manifest import CarrierSpec
from ufo.schema import tables
from ufo.schema.records import TurnRuntimeConfig

BACKEND = "stub"
BASE_HOST = "sites.example.test"
APP_ORIGIN = "https://app.example.test"
"""Where the frame that reads a site lives — the origin of `[connect] public_base_url`, which is a
different host from `BASE_HOST` on every deploy."""
SECRET = "s3cret"
VIEWER_DEFAULT_HEADERS = ("accept", "accept-encoding", "user-agent")
"""What httpx sends of its own accord, so a test can tell a viewer's header from a fabricated one.
httpx fabricates a `connection` too; it is left out because the client sets it per request, below
the header list, so it cannot be deleted off the viewer the way these can — not because it is
harmless. It is filtered when the *viewer* sends it, and reached the origin when the client
fabricated it."""
PLANT_COOKIES_PATH = "/plant-cookies"
CACHEABLE_PATH = "/cacheable"
PLANTED_COOKIES = (
    f"tracker=9; Domain={BASE_HOST}; Path=/; Secure",
    f"ufo_session=attacker; Domain={BASE_HOST}",
    f"{INGRESS_SESSION_COOKIE}=forged",
)
SMUGGLE_COOKIES_PATH = "/smuggle-cookies"
SMUGGLED_COOKIES = (
    f"={INGRESS_SESSION_COOKIE}=FORGED; Path=/",
    " =ufo_session=FORGED",
    "=bare",
)
REFUSE_FRAMING_PATH = "/refuse-framing"
REFUSED_POLICY = "default-src 'self'; frame-ancestors 'none'; img-src *"
REFUSED_POLICY_KEPT = "default-src 'self'; img-src *"
REPORT_ONLY_POLICY = "frame-ancestors 'none'"
FRAMING_ONLY_PATH = "/framing-only"
FRAMING_ONLY_POLICY = "  frame-ancestors 'self' ;  "
UPSTREAM_STATUS_PATH = "/upstream-status/"
HTML_PAGE_PATH = "/page.html"
HTML_PAGE_BYTES = b"<!doctype html><html><body><p>live</p></body></html>"
"""A dialed site's own document, the one kind of response the heartbeat tag is appended to."""
CARRIER_ERROR_BODY = '{"sandboxId":"sbx-1","message":"the sandbox is running but port is not open"}'
"""What a carrier's edge answers when the addressed port is not open: its own error, naming its own
sandbox, under its own status — never anything the site wrote."""
pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)


class _OriginHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path.startswith(HTML_PAGE_PATH):
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(HTML_PAGE_BYTES)))
            self.end_headers()
            self.wfile.write(HTML_PAGE_BYTES)
            return
        if self.path.startswith(UPSTREAM_STATUS_PATH):
            self.send_response(int(self.path.removeprefix(UPSTREAM_STATUS_PATH)))
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(CARRIER_ERROR_BODY)))
            self.end_headers()
            self.wfile.write(CARRIER_ERROR_BODY.encode())
            return
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "probe": self.headers.get("x-dial-probe", ""),
                "probe_count": len(self.headers.get_all("x-dial-probe") or []),
                "cookies": self.headers.get_all("cookie") or [],
                "header_names": sorted({name.lower() for name in self.headers}),
                "framing": sorted(
                    name for name in ("content-length", "transfer-encoding") if name in self.headers
                ),
                "body": body.decode(),
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.send_header("set-cookie", "a=1")
        self.send_header("set-cookie", "b=2")
        if self.path.startswith(PLANT_COOKIES_PATH):
            for planted in PLANTED_COOKIES:
                self.send_header("set-cookie", planted)
        if self.path.startswith(SMUGGLE_COOKIES_PATH):
            for smuggled in SMUGGLED_COOKIES:
                self.send_header("set-cookie", smuggled)
        if self.path.startswith(REFUSE_FRAMING_PATH):
            self.send_header("x-frame-options", "SAMEORIGIN")
            self.send_header("content-security-policy", REFUSED_POLICY)
            self.send_header("content-security-policy-report-only", REPORT_ONLY_POLICY)
        if self.path.startswith(FRAMING_ONLY_PATH):
            self.send_header("content-security-policy", FRAMING_ONLY_POLICY)
        if self.path.startswith(CACHEABLE_PATH):
            self.send_header("cache-control", "public, max-age=31536000, immutable")
            self.send_header("cdn-cache-control", "max-age=31536000")
            self.send_header("cloudflare-cdn-cache-control", "max-age=31536000")
            self.send_header("surrogate-control", "max-age=31536000")
            self.send_header("expires", "Thu, 31 Dec 2037 23:59:59 GMT")
            self.send_header("pragma", "cache")
        self.send_header("alt-svc", 'h3=":443"; ma=2592000')
        self.send_header("via", "1.1 google")
        self.end_headers()
        self.wfile.write(payload)

    def _describe(self) -> None:
        """A HEAD of the page: the headers its GET carries and no body, which is what the ingress
        must leave the origin's own `content-length` on — there is nothing to append a tag to."""
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(HTML_PAGE_BYTES)))
        self.end_headers()

    do_GET = do_POST = _respond
    do_HEAD = _describe

    def log_message(self, *args: object) -> None: ...


@pytest.fixture
def origin_port() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OriginHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


@dataclass(frozen=True)
class _TlsCarrier:
    """A carrier whose target terminates TLS, which is what e2b's per-port hosts do. Only the scheme
    the ingress derives is under test here, so nothing needs to answer on the far side."""

    port: int

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        return DialTarget(host=f"127.0.0.1:{self.port}", tls=True)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("the ingress never creates a sandbox")


@dataclass(frozen=True)
class _StubCarrier:
    port: int

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        assert handle.container_id == "sbx-1"
        return DialTarget(
            host=f"127.0.0.1:{self.port}", tls=False, headers={"x-dial-probe": "dialed"}
        )

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("the ingress never creates a sandbox")

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError("the ingress never execs in a sandbox")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        raise AssertionError("the ingress never writes to a sandbox")

    def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        raise AssertionError("the ingress never reads from a sandbox")


async def _seed_conversation(handle: str | None) -> tuple[UUID, UUID]:
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
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
                prompt="p",
                model="claude-opus-5-5",
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
                member_id=None,
                sandbox_handle=handle,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id


async def _seed_hosted_site(
    workspace_id: UUID, conversation_id: UUID, port: int, source_manifest: str | None = None
) -> UUID:
    hosted_site = sa.table(
        "hosted_site",
        sa.column("workspace_id", sa.Uuid()),
        sa.column("conversation_id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("port", sa.Integer()),
        sa.column("visibility", sa.Text()),
        sa.column("creator_member_id", sa.Uuid()),
        sa.column("generation", sa.Uuid()),
        sa.column("source_manifest", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    creator_member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(hosted_site).values(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                name=f"site-{conversation_id.hex[:8]}",
                port=port,
                visibility="workspace",
                creator_member_id=creator_member_id,
                generation=uuid4(),
                source_manifest=source_manifest,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return creator_member_id


async def _seed_shipped_app(workspace_id: UUID, extension: str, name: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=uuid4(),
                workspace_id=workspace_id,
                name=name,
                prompt="p",
                model="claude-opus-5-5",
                provisioned_by=extension,
                provisioned_name=name,
                provisioned_version="1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


def _token(
    workspace_id: UUID,
    conversation_id: UUID,
    port: int = 8000,
    ttl: int = 900,
    kind: IngressTokenKind = INGRESS_VIEW_KIND,
    framer: FramerClaim | None = None,
) -> str:
    return mint_ingress_token(
        IngressClaims(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            port=port,
            expires_at=int(datetime.now(UTC).timestamp()) + ttl,
            framer=framer,
        ),
        kind,
    )


def _origin(conversation_id: UUID, port: int = 8000) -> str:
    """The site's own address."""
    return f"https://{site_label(conversation_id, port)}.{BASE_HOST}"


async def _open(
    client: httpx.AsyncClient,
    workspace_id: UUID,
    conversation_id: UUID,
    port: int = 8000,
    framer: FramerClaim | None = None,
) -> str:
    """Arrive at the site the way the frame's iframe does, and answer with the session the ingress
    bound — the client's own jar holds it too, host-only, so every later request carries it."""
    token = _token(workspace_id, conversation_id, port, framer=framer)
    got = await client.get(f"{_origin(conversation_id, port)}{INGRESS_VIEW_PATH}/{token}")
    assert got.status_code == 303, got.text
    return got.cookies[INGRESS_SESSION_COOKIE]


UNREAD_BLOBS = FilesystemBlobStore(root=Path("blob-root-never-read"))
"""The store for every ingress these tests dial a sandbox through: the dial path reads no blob, so
the root needs to exist for no test — a read through it is itself the failure."""


def _server(
    carrier: Carrier,
    upstream: httpx.AsyncClient,
    frame_ancestor: str = APP_ORIGIN,
    resume: Mapping[str, tuple[Carrier, CarrierSpec]] | None = None,
    blob: FilesystemBlobStore = UNREAD_BLOBS,
    site_scheme: str = "https",
    reporter: SiteReporter | None = None,
    apps_dev_server: DialTarget | None = None,
) -> IngressServe:
    return IngressServe(
        backend=BACKEND,
        base_host=BASE_HOST,
        carrier=carrier,
        client=upstream,
        blob=blob,
        frame_ancestor=frame_ancestor,
        reporter=reporter
        if reporter is not None
        else SiteReporter(client=upstream, serve_base_url=None),
        site_scheme=site_scheme,
        site_port_suffix="",
        resume_carriers=resume if resume is not None else {},
        apps_dev_server=apps_dev_server,
    )


@pytest.fixture
async def ingress(
    origin_port: int, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    async with upstream_client() as upstream:
        server = _server(_StubCarrier(origin_port), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            client.upstream = upstream  # type: ignore[attr-defined]
            yield client


async def test_proxies_method_path_query_body_and_dial_headers(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.post(f"{_origin(conversation_id)}/api/save?x=1", content=b"hello")
    assert got.status_code == 200
    echoed = got.json()
    assert echoed == {
        "method": "POST",
        "path": "/api/save?x=1",
        "probe": "dialed",
        "probe_count": 1,
        "cookies": [],
        "header_names": sorted({*VIEWER_DEFAULT_HEADERS, "content-length", "host", "x-dial-probe"}),
        "framing": ["content-length"],
        "body": "hello",
    }


async def test_the_origin_sees_no_header_the_viewer_did_not_send(db, ingress) -> None:
    """The forwarded list is the whole list."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    for fabricated in VIEWER_DEFAULT_HEADERS:
        del ingress.headers[fabricated]
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert got.json()["header_names"] == ["host", "x-dial-probe"]


async def test_a_bodyless_get_carries_no_body_framing(db, ingress) -> None:
    """A GET has no body, so it must reach the origin with neither `content-length` nor
    `transfer-encoding`."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert got.json()["framing"] == []


async def test_percent_encoded_path_characters_reach_the_origin_intact(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/a%3Fb.html?x=1")
    assert got.status_code == 200
    assert got.json()["path"] == "/a%3Fb.html?x=1"


async def test_a_root_absolute_asset_path_reaches_the_origin(db, ingress) -> None:
    """What the per-site origin buys: a built site's `/assets/app.js` is the site's own root, so it
    proxies as itself with no prefix to escape."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/assets/app.js")
    assert got.status_code == 200
    assert got.json()["path"] == "/assets/app.js"


async def test_the_view_token_binds_a_session_and_redirects_to_the_site_root(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}/{token}")
    assert got.status_code == 303
    assert got.headers["location"] == "/"
    cookie = got.headers["set-cookie"]
    assert cookie.startswith(f"{INGRESS_SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "Secure" in cookie and "domain" not in cookie.lower()
    assert "samesite=lax" in cookie.lower()


async def test_a_plain_http_deploy_binds_a_session_the_browser_will_keep(
    db, origin_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    async with upstream_client() as upstream:
        server = _server(_StubCarrier(origin_port), upstream, site_scheme="http")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            got = await client.get(
                f"http://{site_label(conversation_id, 8000)}.{BASE_HOST}{INGRESS_VIEW_PATH}/{token}"
            )
    assert got.status_code == 303
    cookie = got.headers["set-cookie"]
    assert cookie.startswith(f"{INGRESS_SESSION_COOKIE}=")
    assert "Secure" not in cookie
    assert "HttpOnly" in cookie and "domain" not in cookie.lower()


async def test_the_view_token_lands_the_viewer_on_the_path_the_frame_named(db, ingress) -> None:
    """A site's own paths are reachable only through the frame, so entering one is the frame's to
    ask for: whatever follows the token is where the bound session lands."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}/{token}/send")
    assert got.status_code == 303
    assert got.headers["location"] == "/send"
    assert got.headers["set-cookie"].startswith(f"{INGRESS_SESSION_COOKIE}=")


async def test_an_entry_path_cannot_send_the_viewer_off_this_origin(db, ingress) -> None:
    """`//evil.test` in a `Location` is a protocol-relative URL, not a path — the one entry the
    quoting cannot neutralise, since `/` has to stay a path separator."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    origin = _origin(conversation_id)
    escaped = await ingress.get(f"{origin}{INGRESS_VIEW_PATH}/{token}//evil.test")
    assert escaped.status_code == 403
    assert escaped.text == LINK_NOT_VALID
    quoted = await ingress.get(f"{origin}{INGRESS_VIEW_PATH}/{token}/a%3Fb")
    assert quoted.status_code == 303
    assert quoted.headers["location"] == "/a%3Fb"
    injected = await ingress.get(f"{origin}{INGRESS_VIEW_PATH}/{token}/a%0d%0aX-Evil:%20yes")
    assert injected.status_code == 404
    assert "x-evil" not in injected.headers


async def test_a_request_without_a_session_is_403_with_no_dead_end(db, ingress) -> None:
    """The label is an address, not an authorization: knowing a site's origin gets a viewer
    nothing until the frame has traded a view token for that origin's session."""
    _workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 403
    assert got.text == SESSION_ENDED_PAGE
    assert got.headers["content-type"].startswith("text/html")
    assert "fresh link" not in got.text


async def test_a_session_cookie_cannot_mint_its_own_successor(db, ingress) -> None:
    """The renewal chain, measured and closed: the cookie the ingress binds is a session token,
    the view path takes only a view token, so replaying the cookie at `/~t/` mints nothing."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    session = await _open(ingress, workspace_id, conversation_id)
    replayed = await ingress.get(f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}/{session}")
    assert replayed.status_code == 403
    assert "set-cookie" not in replayed.headers


async def test_a_view_token_is_not_a_session(db, ingress) -> None:
    """The other half of the same seam: a view token pasted straight into the cookie jar serves
    nothing, so the handshake is the only way onto a site and the redirect cannot be skipped."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    view = _token(workspace_id, conversation_id)
    got = await ingress.get(
        f"{_origin(conversation_id)}/index.html",
        headers={"cookie": f"{INGRESS_SESSION_COOKIE}={view}"},
    )
    assert got.status_code == 403


async def test_the_session_runs_its_own_ttl_from_the_moment_it_is_minted(db, ingress) -> None:
    """A view token with seconds left still opens a full session — the visit is not cut short by
    how long the link had been sitting in the frame — and that session's expiry is fixed at mint."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    expiring = _token(workspace_id, conversation_id, ttl=5)
    got = await ingress.get(f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}/{expiring}")
    assert got.status_code == 303
    now = datetime.now(UTC)
    claims = verify_ingress_token(got.cookies[INGRESS_SESSION_COOKIE], now, INGRESS_SESSION_KIND)
    assert claims.expires_at - int(now.timestamp()) == pytest.approx(
        INGRESS_SESSION_TTL_SECONDS, abs=2
    )


async def test_the_site_never_receives_our_session_cookie(db, ingress) -> None:
    """The site is agent-authored code running in the sandbox."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    session = await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(
        f"{_origin(conversation_id)}/",
        headers={"cookie": f"theme=dark; {INGRESS_SESSION_COOKIE}={session}; cart=7"},
    )
    assert got.status_code == 200
    assert got.json()["cookies"] == ["theme=dark; cart=7"]
    only_ours = await ingress.get(
        f"{_origin(conversation_id)}/",
        headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"},
    )
    assert only_ours.status_code == 200
    assert only_ours.json()["cookies"] == []


async def test_the_ingress_keeps_no_cookie_jar_of_its_own(db, ingress) -> None:
    """The upstream client is cookie-blind."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    session = await _open(ingress, workspace_id, conversation_id)
    first = await ingress.get(
        f"{_origin(conversation_id)}/",
        headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"},
    )
    assert first.headers.get_list("set-cookie") == ["a=1", "b=2"]
    again = await ingress.get(
        f"{_origin(conversation_id)}/",
        headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"},
    )
    assert again.json()["cookies"] == []
    assert len(ingress.upstream.cookies.jar) == 0


async def test_a_view_token_never_opens_another_site(db, ingress) -> None:
    """A token minted for one `(conversation, port)` is refused at every other site's origin, so a
    frame cannot be pointed at a conversation it was not issued for."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    _other_ws, other_conversation = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    at_another_conversation = await ingress.get(
        f"{_origin(other_conversation)}{INGRESS_VIEW_PATH}/{token}"
    )
    assert at_another_conversation.status_code == 403
    assert at_another_conversation.text == WRONG_SITE
    at_another_port = await ingress.get(
        f"{_origin(conversation_id, 3000)}{INGRESS_VIEW_PATH}/{token}"
    )
    assert at_another_port.status_code == 403


async def test_a_session_never_opens_another_site(db, ingress) -> None:
    """Two labels are two origins."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    _other_ws, other_conversation = await _seed_conversation("stub:sbx-1")
    session = await _open(ingress, workspace_id, conversation_id)
    replayed = await ingress.get(
        f"{_origin(other_conversation)}/index.html",
        headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"},
    )
    assert replayed.status_code == 403
    assert (await ingress.get(f"{_origin(other_conversation)}/index.html")).status_code == 403


async def test_bad_sessions_and_view_tokens_are_403(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    origin = _origin(conversation_id)
    assert (
        await ingress.get(f"{origin}{INGRESS_VIEW_PATH}/{_token(workspace_id, conversation_id)}x")
    ).status_code == 403
    stale_view = _token(workspace_id, conversation_id, ttl=-1)
    stale = await ingress.get(f"{origin}{INGRESS_VIEW_PATH}/{stale_view}")
    assert stale.status_code == 403
    assert stale.text == LINK_NOT_VALID
    session = _token(workspace_id, conversation_id, kind=INGRESS_SESSION_KIND)
    stale_session = _token(workspace_id, conversation_id, ttl=-1, kind=INGRESS_SESSION_KIND)
    for value in (f"{session}x", stale_session, "not-a-token"):
        refused = await ingress.get(
            f"{origin}/index.html", headers={"cookie": f"{INGRESS_SESSION_COOKIE}={value}"}
        )
        assert refused.status_code == 403
        assert refused.text == SESSION_ENDED_PAGE


def test_the_ingress_refuses_to_boot_without_a_base(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one piece `run()` reads off the knob, and its fail-loud."""
    assert ingress_base_host("https://sites.example.test") == "sites.example.test"
    assert ingress_base_host("https://sites.example.test:8443") == "sites.example.test"
    for unusable in (None, ""):
        with pytest.raises(RuntimeError, match="ingress_public_url"):
            ingress_base_host(unusable)


async def test_a_host_naming_no_site_is_404(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    label = site_label(conversation_id, 8000)
    for host in (f"{label}.other.example.test", BASE_HOST, f"forged.{BASE_HOST}"):
        nowhere = await ingress.get(f"https://{host}/index.html")
        assert (nowhere.status_code, nowhere.text) == (404, NO_SITE_HERE)
        at_view = await ingress.get(f"https://{host}{INGRESS_VIEW_PATH}/{token}")
        assert (at_view.status_code, at_view.text) == (404, NO_SITE_HERE)


async def test_missing_or_foreign_sandbox_handle_is_503(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation(None)
    await _open(ingress, workspace_id, conversation_id)
    assert (await ingress.get(f"{_origin(conversation_id)}/")).status_code == 503
    other_ws, other_conv = await _seed_conversation("docker:other")
    await _open(ingress, other_ws, other_conv)
    assert (await ingress.get(f"{_origin(other_conv)}/")).status_code == 503


async def test_a_resume_backends_handle_dials_through_its_own_carrier(
    db, origin_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    async with upstream_client() as upstream:
        resumed = _StubCarrier(origin_port)
        server = _server(
            _StubCarrier(origin_port),
            upstream,
            resume={"old": (resumed, CarrierSpec(name="old", factory=lambda: resumed))},
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            workspace_id, conversation_id = await _seed_conversation("old:sbx-1")
            await _open(client, workspace_id, conversation_id)
            got = await client.get(f"{_origin(conversation_id)}/")
            assert got.status_code == 200


async def test_cross_workspace_session_is_503(db, ingress) -> None:
    workspace_a, _conversation_a = await _seed_conversation("stub:sbx-1")
    _workspace_b, conversation_b = await _seed_conversation("stub:sbx-1")
    session = await _open(ingress, workspace_a, conversation_b)
    got = await ingress.get(
        f"{_origin(conversation_b)}/", headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"}
    )
    assert got.status_code == 503


async def test_inbound_headers_cannot_override_dial_headers(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/x", headers={"X-Dial-Probe": "stolen"})
    assert got.status_code == 200
    echoed = got.json()
    assert echoed["probe"] == "dialed"
    assert echoed["probe_count"] == 1


async def test_repeated_inbound_headers_reach_the_origin_intact(db, ingress) -> None:
    """The response path preserves repeats and so must the request path: a browser sending two
    `Cookie` lines, or a chained `X-Forwarded-For`, must arrive as it was sent."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    session = await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(
        f"{_origin(conversation_id)}/x",
        headers=[
            ("cookie", f"{INGRESS_SESSION_COOKIE}={session}; a=1"),
            ("cookie", "b=2"),
            ("X-Dial-Probe", "stolen"),
        ],
    )
    assert got.status_code == 200
    echoed = got.json()
    assert echoed["cookies"] == ["a=1", "b=2"]
    assert echoed["probe"] == "dialed"
    assert echoed["probe_count"] == 1


async def test_duplicate_set_cookie_headers_are_preserved_not_joined(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/")
    assert got.headers.get_list("set-cookie") == ["a=1", "b=2"]


async def test_a_site_cannot_widen_a_cookie_past_its_own_origin(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{PLANT_COOKIES_PATH}")
    assert got.status_code == 200
    relayed = got.headers.get_list("set-cookie")
    assert relayed == ["a=1", "b=2", "tracker=9; Path=/; Secure"]
    assert not any("domain" in cookie.lower() for cookie in relayed)
    assert not any(cookie.lower().startswith("ufo_") for cookie in relayed)
    assert INGRESS_SESSION_COOKIE not in got.cookies
    assert "ufo_session" not in got.cookies


async def test_a_nameless_cookie_cannot_smuggle_a_reserved_name(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{SMUGGLE_COOKIES_PATH}")
    assert got.status_code == 200
    assert got.headers.get_list("set-cookie") == ["a=1", "b=2"]


def _framers(response: httpx.Response) -> list[str]:
    """Every `frame-ancestors` the response relays, across all of its policies."""
    return [
        directive.strip()[len(FRAME_ANCESTORS_DIRECTIVE) :].strip()
        for policy in response.headers.get_list(CONTENT_SECURITY_POLICY)
        for directive in policy.split(";")
        if directive.strip().split()[:1] == [FRAME_ANCESTORS_DIRECTIVE]
    ]


async def test_a_site_that_says_nothing_about_framing_is_still_only_framed_by_the_app(
    db, ingress
) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert _framers(got) == [APP_ORIGIN]


async def test_framing_names_only_the_requested_workspace_site(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    sibling_conversation_id = uuid4()
    await _seed_hosted_site(workspace_id, sibling_conversation_id, port=3000)
    unrequested_conversation_id = uuid4()
    await _seed_hosted_site(workspace_id, unrequested_conversation_id, port=4000)
    foreign_workspace_id, foreign_conversation_id = await _seed_conversation(None)
    await _seed_hosted_site(foreign_workspace_id, foreign_conversation_id, port=3000)
    await _open(
        ingress,
        workspace_id,
        conversation_id,
        framer=FramerClaim(conversation_id=sibling_conversation_id, port=3000),
    )
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert _framers(got) == [f"{APP_ORIGIN} {_origin(sibling_conversation_id, 3000)}"]
    assert _origin(unrequested_conversation_id, 4000) not in _framers(got)[0]
    assert _origin(foreign_conversation_id, 3000) not in _framers(got)[0]


async def test_framing_refuses_a_requested_site_from_another_workspace(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    foreign_workspace_id, foreign_conversation_id = await _seed_conversation(None)
    await _seed_hosted_site(foreign_workspace_id, foreign_conversation_id, port=3000)
    token = _token(
        workspace_id,
        conversation_id,
        framer=FramerClaim(conversation_id=foreign_conversation_id, port=3000),
    )

    got = await ingress.get(f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}/{token}")

    assert (got.status_code, got.text) == (403, LINK_NOT_VALID)


async def test_framing_names_the_requested_workspace_shipped_app(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    foreign_workspace_id, _foreign_conversation_id = await _seed_conversation(None)
    await _seed_shipped_app(workspace_id, "app_wiki", "wiki")
    await _seed_shipped_app(foreign_workspace_id, "app_wiki", "wiki")
    anchor = shipped_anchor(workspace_id, "wiki")
    await _open(
        ingress,
        workspace_id,
        conversation_id,
        framer=FramerClaim(conversation_id=anchor, port=serve_port(anchor)),
    )

    got = await ingress.get(f"{_origin(conversation_id)}/index.html")

    foreign_anchor = shipped_anchor(foreign_workspace_id, "wiki")
    assert _framers(got) == [f"{APP_ORIGIN} {_origin(anchor, serve_port(anchor))}"]
    assert _origin(foreign_anchor, serve_port(foreign_anchor)) not in _framers(got)[0]


async def test_a_deploy_with_no_app_origin_lets_nothing_frame_a_site(
    db, origin_port, monkeypatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _seed_hosted_site(workspace_id, uuid4(), port=3000)
    async with upstream_client() as upstream:
        server = _server(_StubCarrier(origin_port), upstream, frame_ancestor=NO_FRAME_ANCESTOR)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            got = await client.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert _framers(got) == [NO_FRAME_ANCESTOR]


async def test_a_sites_own_framing_directive_is_replaced_not_added_to(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{REFUSE_FRAMING_PATH}")
    assert got.status_code == 200
    assert _framers(got) == [APP_ORIGIN]


def test_the_frame_ancestor_is_an_origin_and_nothing_frames_a_site_without_one(
    database_url: str,
) -> None:
    """An origin, not the whole configured URL: a path in `frame-ancestors` is matched by the
    browser and would name a source no page has."""
    assert ingress_frame_ancestor("https://app.example/chat?c=1") == "https://app.example"
    assert ingress_frame_ancestor("https://app.example:8443/") == "https://app.example:8443"
    assert ingress_frame_ancestor("http://localhost:8710/") == "http://localhost:*"
    assert ingress_frame_ancestor("http://ufo-3.localhost:18280") == "http://ufo-3.localhost:*"
    assert ingress_frame_ancestor("http://10.0.0.5:8710") == "http://10.0.0.5:8710"
    assert ingress_frame_ancestor(None) == NO_FRAME_ANCESTOR
    assert ingress_frame_ancestor("") == NO_FRAME_ANCESTOR


async def test_a_site_cannot_refuse_to_be_framed(db, ingress) -> None:
    """A site is read inside the frame at the app origin, and that frame is the only page which
    embeds one — so who may frame a site is core's answer, not the site's."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{REFUSE_FRAMING_PATH}")
    assert got.status_code == 200
    assert "x-frame-options" not in got.headers
    assert REFUSED_POLICY_KEPT in got.headers.get_list(CONTENT_SECURITY_POLICY)
    assert got.headers["content-security-policy-report-only"] == REPORT_ONLY_POLICY


async def test_a_policy_of_nothing_but_framing_is_dropped_whole(db, ingress) -> None:
    """Removing the only directive leaves an empty policy, and an empty `Content-Security-Policy`
    is not a permissive one — a browser reads it as a policy that allows nothing."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{FRAMING_ONLY_PATH}")
    assert got.status_code == 200
    assert got.headers.get_list(CONTENT_SECURITY_POLICY) == [
        f"{FRAME_ANCESTORS_DIRECTIVE} {APP_ORIGIN}"
    ]


async def test_a_tls_target_is_dialed_over_https(db, origin_port, monkeypatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(_TlsCarrier(origin_port), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            answered = await client.get(f"{_origin(conversation_id)}/index.html")
    assert answered.status_code == 503
    assert answered.text == SITE_NOT_ANSWERING


async def test_the_bare_view_path_takes_no_token_from_the_query(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    for url in (
        f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}?view_path={token}",
        f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}?view_path=/{token}",
        f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}",
    ):
        refused = await ingress.get(url, follow_redirects=False)
        assert refused.status_code == 403, url
        assert "set-cookie" not in refused.headers, url


async def test_a_site_path_merely_starting_with_the_view_prefix_is_served(db, ingress) -> None:
    """The view claim is a path segment, not a three-character prefix."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    for path in ("~theme.css", "~t-assets/app.js", "~tok"):
        served = await ingress.get(f"{_origin(conversation_id)}/{path}")
        assert served.status_code == 200, path
        assert served.json()["path"] == f"/{path}"


async def test_the_view_path_answers_only_get_and_head(db, ingress) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    url = f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}/{token}"
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        refused = await ingress.request(method, url)
        assert refused.status_code == 405, method
        assert refused.headers["allow"] == "GET, HEAD"
        assert token not in refused.text
    assert (await ingress.get(url)).status_code == 303
    heading = await ingress.head(url)
    assert heading.status_code == 303
    assert heading.headers["location"] == "/"


async def test_no_path_under_the_view_prefix_reaches_the_sandbox(db, ingress) -> None:
    """The view path claims everything under itself, not one segment of it."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    origin = _origin(conversation_id)
    entered = await ingress.get(f"{origin}{INGRESS_VIEW_PATH}/{token}/a/b")
    assert entered.status_code == 303
    assert entered.headers["location"] == "/a/b"
    served = await ingress.get(f"{origin}/a/b")
    assert served.json()["path"] == "/a/b"
    assert token not in served.json()["path"]
    for suffix in ("/", ""):
        got = await ingress.get(f"{origin}{INGRESS_VIEW_PATH}{suffix}", follow_redirects=False)
        assert got.status_code == 403, suffix
        assert got.text == LINK_NOT_VALID
        assert "set-cookie" not in got.headers


async def test_the_origins_date_and_server_headers_are_not_relayed(db, ingress) -> None:
    """The ASGI server writes `date` and `server` on every response it sends, so relaying the
    origin's own puts two of each on the wire and RFC 9110 forbids a second `date`."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert "date" not in got.headers
    assert "server" not in got.headers


async def test_the_origins_edge_headers_are_not_relayed(db, ingress) -> None:
    """A sandbox host answers through the provider's own edge, which adds these."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert "alt-svc" not in got.headers
    assert "via" not in got.headers


async def test_no_shared_cache_may_store_a_proxied_response(db, ingress) -> None:
    """Authorization is a cookie checked per request, so a cache that stores a site's bytes and
    answers a later request from them answers it without the check."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{CACHEABLE_PATH}/assets/app.js")
    assert got.status_code == 200
    assert got.headers.get_list("cache-control") == [UNCACHEABLE]
    for dropped in CACHE_DIRECTIVE_HEADERS - {"cache-control"}:
        assert dropped not in got.headers, dropped


@dataclass(frozen=True)
class _UnreachableCarrier:
    """Every verb the ingress must never exercise raises; `dial` raises `SandboxUnreachable`, the
    carrier's real signal that a stored handle no longer names a live sandbox."""

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise SandboxUnreachable("sandbox is gone")

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("the ingress never creates a sandbox")

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        raise AssertionError("the ingress never execs in a sandbox")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        raise AssertionError("the ingress never writes to a sandbox")

    def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]:
        raise AssertionError("the ingress never reads from a sandbox")


@dataclass(frozen=True)
class _DeadPortCarrier(_StubCarrier):
    """Dials a port nothing listens on, the shape of a site whose server died while its sandbox
    lived: the dial succeeds because the sandbox is there, and the connection is refused."""

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        return DialTarget(host="127.0.0.1:1", tls=False)


async def test_an_unreachable_origin_is_the_site_not_answering(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(_DeadPortCarrier(port=0), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            got = await client.get(f"{_origin(conversation_id)}/")
    assert got.status_code == 503
    assert got.text == SITE_NOT_ANSWERING


@pytest.mark.parametrize("status", sorted(EDGE_REPLACED_STATUSES))
async def test_an_upstream_status_the_edge_replaces_is_answered_here(db, ingress, status) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)

    got = await ingress.get(f"{_origin(conversation_id)}{UPSTREAM_STATUS_PATH}{status}")

    assert (got.status_code, got.text) == (503, SITE_NOT_ANSWERING)
    assert _framers(got) == [APP_ORIGIN]
    assert got.headers["cache-control"] == UNCACHEABLE


@dataclass(frozen=True)
class _Reported:
    """One ingress wired to one real `SiteReports`, and the turns its reports founded there."""

    client: httpx.AsyncClient
    turns: list[RecordedTurn]


@asynccontextmanager
async def _reporting(carrier: Carrier) -> AsyncIterator[_Reported]:
    invoker = RecordingInvoker()
    serve = FastAPI()
    serve.include_router(SiteReports(invoker_for=lambda _workspace_id: invoker).router())
    async with upstream_client() as upstream:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=serve)) as to_serve:
            server = _server(
                carrier,
                upstream,
                reporter=SiteReporter(client=to_serve, serve_base_url=APP_ORIGIN),
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
                yield _Reported(client=client, turns=invoker.turns)


async def _read(
    client: httpx.AsyncClient, conversation_id: UUID, path: str, destination: str
) -> httpx.Response:
    return await client.get(
        f"{_origin(conversation_id)}{path}", headers={FETCH_DESTINATION_HEADER: destination}
    )


@pytest.mark.parametrize("destination", sorted(DOCUMENT_DESTINATIONS))
async def test_a_page_read_from_a_site_that_stopped_waits_and_reloads(
    db, monkeypatch: pytest.MonkeyPatch, destination
) -> None:
    """What a member actually meets."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(_DeadPortCarrier(port=0), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            got = await _read(client, conversation_id, "/", destination)

    assert got.status_code == 503
    assert got.headers["content-type"].startswith("text/html")
    assert SITE_NOT_ANSWERING in got.text
    assert f'http-equiv="refresh" content="{SITE_WAITING_RELOAD_SECONDS}"' in got.text
    assert _framers(got) == [APP_ORIGIN]
    assert got.headers[CONTENT_SECURITY_POLICY].startswith(SITE_WAITING_POLICY)
    assert got.headers["cache-control"] == UNCACHEABLE


async def test_a_sub_resource_of_a_site_that_stopped_is_answered_as_text(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page names its own scripts, styles and images, and each of them meets the same stopped
    server."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(_DeadPortCarrier(port=0), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            got = await _read(client, conversation_id, "/app.js", "script")

    assert (got.status_code, got.text) == (503, SITE_NOT_ANSWERING)
    assert _framers(got) == [APP_ORIGIN]


async def test_a_page_read_from_a_site_that_stopped_tells_the_conversation_that_owns_it(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole hop, end to end: a member opens a site whose server has stopped, and the
    conversation that built it is told inside the same request that answers the member."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _seed_hosted_site(workspace_id, conversation_id, 8000)
    async with _reporting(_DeadPortCarrier(port=0)) as reported:
        await _open(reported.client, workspace_id, conversation_id)
        got = await _read(reported.client, conversation_id, "/", "iframe")

    assert got.status_code == 503
    assert [turn.conversation_id for turn in reported.turns] == [conversation_id]
    assert reported.turns[0].message == SITE_NOT_ANSWERING_FIRE.format(port=8000)
    assert reported.turns[0].runtime_config == TurnRuntimeConfig(internet_access=False)


async def test_reloads_of_the_waiting_page_carry_one_idempotency_key(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The waiting page reloads until the site answers, so a site that stays down reports itself
    again every time."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _seed_hosted_site(workspace_id, conversation_id, 8000)
    async with _reporting(_DeadPortCarrier(port=0)) as reported:
        await _open(reported.client, workspace_id, conversation_id)
        for _ in range(3):
            assert (await _read(reported.client, conversation_id, "/", "document")).status_code

    bucket = int(datetime.now(UTC).timestamp()) // REPORT_BUCKET_SECONDS
    assert len(reported.turns) == 3
    assert {turn.idempotency_key for turn in reported.turns} == {
        f"site-down:{conversation_id.hex}:8000:{bucket}"
    }


async def test_a_sub_resource_and_a_served_page_report_nothing(
    db, monkeypatch: pytest.MonkeyPatch, origin_port: int
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _reporting(_DeadPortCarrier(port=0)) as reported:
        await _open(reported.client, workspace_id, conversation_id)
        assert (await _read(reported.client, conversation_id, "/app.js", "script")).status_code
    async with _reporting(_StubCarrier(origin_port)) as answering:
        await _open(answering.client, workspace_id, conversation_id)
        assert (await _read(answering.client, conversation_id, "/", "iframe")).status_code == 200

    assert reported.turns == []
    assert answering.turns == []


async def test_dial_failure_is_503(db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(_UnreachableCarrier(), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            got = await client.get(f"{_origin(conversation_id)}/")
    assert got.status_code == 503
    assert got.text == SITE_GONE


VITE_SUBPROTOCOL = Subprotocol("vite-hmr")
SOCKET_CLOSE_PATH = "/site-closes"
SOCKET_ABORT_PATH = "/site-aborts"
SOCKET_FLOOD_PATH = "/site-floods"
SOCKET_LARGE_PATH = "/site-sends-large"
SOCKET_PUSH_PATH = "/site-pushes"
SOCKET_SINK_PATH = "/site-reads-only"
LARGE_FRAME_BYTES = 2 * 1024 * 1024
"""Over the WebSocket library's own 1 MiB default and under ours, so a frame this size proves the
bound is the one this module sets. An over-bound frame alone would not: the default would reject it
too, and the test would pass with the bound removed."""
SITE_CLOSE_CODE = 1001
SITE_CLOSE_REASON = "site is going away"
SERVER_START_TICKS = 100
HANDSHAKE_BYTES = 4096
ABORT_ROUNDS = 3
"""Enough resets to hit the window the failure needs — it opens only when the transport dies with no
loop yield in between, so one round can miss it."""
PUSH_INTERVAL_SECONDS = 0.01
"""What a pushing site does between messages. Sending in a tight loop with no yield is a flood
rather than a push: it starved the relay enough that one reset round passed the deadline on CI's
slower shard while finishing in seconds locally. The window under test is the viewer's own reset,
which pacing leaves untouched."""
RELAY_DEADLINE_SECONDS = 30.0
"""What the two sockets in series must beat. Losing the cancellation leaves the relay never
returning, so the client waits on a close that never comes — a wedge that a bare assertion cannot
see. A wedge never finishes, so a generous bound costs nothing but time on a real failure —
ten times the slowest local run, and well below the runner's own kill."""
OPEN_TIMEOUT_HEADROOM_SECONDS = 5.0
"""What the refusal must beat, and the only thing that distinguishes the timeout being set from it
being absent: the hung server never answers and never hangs up, so with `open_timeout` deleted the
WebSocket client's own 10-second default still ends the wait and the refusal is still a 503 — just
ten seconds later. Twenty-five times the shortened timeout the test sets, so the bound is a
measurement rather than a race."""
TICK_SECONDS = 0.02
SERVER_STOP_GRACE_SECONDS = 5
SERVER_STOP_WAIT_SECONDS = 20.0
STOP_FLOOR_SECONDS = 3.0
STOP_DEADLINE_SECONDS = 8.0
REGISTERED_WAIT_SECONDS = 5.0


@dataclass
class _SocketOrigin:
    """A real WebSocket server standing in for the site's own, so the relay is asserted against a
    real handshake, real subprotocol negotiation, and real frames rather than a fake of them."""

    port: int = 0
    handshakes: list["_Handshake"] = field(default_factory=list)
    received: list[int] = field(default_factory=list)
    """The size of every message the site actually read, acknowledged back as a short count.

    The viewer-to-site half of the frame bound is only visible from here. Echoing the payload
    measures the bound on the *return* trip instead, where the upstream client's own cap refuses it
    whatever the server was configured to accept — so the test passed with the server's bound
    deleted. The count is small enough that no cap touches it, and awaiting it makes the assertion
    positive rather than a check on an empty list."""

    async def handle(self, connection: ServerConnection) -> None:
        request = connection.request
        assert request is not None
        self.handshakes.append(
            _Handshake(
                path=request.path,
                probe=request.headers.get("x-dial-probe") or "",
                cookies=tuple(request.headers.get_all("cookie")),
                header_names=tuple(name.lower() for name, _ in request.headers.raw_items()),
                subprotocol=connection.subprotocol or "",
            )
        )
        if request.path.startswith(SOCKET_CLOSE_PATH):
            await connection.close(code=SITE_CLOSE_CODE, reason=SITE_CLOSE_REASON)
            return
        if request.path.startswith(SOCKET_ABORT_PATH):
            connection.transport.abort()
            return
        if request.path.startswith(SOCKET_FLOOD_PATH):
            await connection.send("x" * (WEBSOCKET_MAX_MESSAGE_BYTES + 1))
            return
        if request.path.startswith(SOCKET_LARGE_PATH):
            await connection.send("x" * LARGE_FRAME_BYTES)
            return
        if request.path.startswith(SOCKET_PUSH_PATH):
            with suppress(Exception):
                while True:
                    await connection.send("push")
                    await asyncio.sleep(PUSH_INTERVAL_SECONDS)
            return
        if request.path.startswith(SOCKET_SINK_PATH):
            with suppress(Exception):
                async for message in connection:
                    self.received.append(len(message))
                    await connection.send(str(len(message)))
            return
        async for message in connection:
            if isinstance(message, str):
                await connection.send(f"echo:{message}")
            else:
                await connection.send(b"echo:" + message)


@dataclass(frozen=True)
class _Handshake:
    path: str
    probe: str
    cookies: tuple[str, ...]
    header_names: tuple[str, ...]
    subprotocol: str


def _select_subprotocol(
    connection: ServerConnection, subprotocols: Sequence[Subprotocol]
) -> Subprotocol | None:
    """What a dev server does: take the subprotocol when it is offered, and serve the connection
    without one when it is not."""
    return VITE_SUBPROTOCOL if VITE_SUBPROTOCOL in subprotocols else None


@pytest.fixture
async def socket_origin() -> AsyncIterator[_SocketOrigin]:
    origin = _SocketOrigin()
    async with serve(
        origin.handle,
        "127.0.0.1",
        0,
        select_subprotocol=_select_subprotocol,
        max_size=None,
        compression=None,
    ) as server:
        origin.port = server.sockets[0].getsockname()[1]
        yield origin


@asynccontextmanager
async def _serving(
    app: ASGIApplication | Callable[..., Any] | str, **config: object
) -> AsyncIterator[uvicorn.Server]:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_config=None,
            lifespan="off",
            timeout_graceful_shutdown=SERVER_STOP_GRACE_SECONDS,
            **config,
        )
    )
    serving = asyncio.create_task(server.serve())
    for _ in range(SERVER_START_TICKS):
        if server.started:
            break
        await asyncio.sleep(TICK_SECONDS)
    assert server.started, "the ingress never bound a port"
    try:
        yield server
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, SERVER_STOP_WAIT_SECONDS)


def _bound_port(server: uvicorn.Server) -> int:
    return int(server.servers[0].sockets[0].getsockname()[1])


@pytest.fixture
async def socket_ingress(
    socket_origin: _SocketOrigin, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[int]:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    async with upstream_client() as upstream:
        app = _server(_StubCarrier(socket_origin.port), upstream).app()
        async with _serving(
            app,
            ws_max_size=WEBSOCKET_MAX_MESSAGE_BYTES,
            ws_per_message_deflate=False,
        ) as server:
            yield _bound_port(server)


async def test_a_connection_still_open_at_stop_does_not_hold_the_ingress(
    database_url: str,
) -> None:
    entered = asyncio.Event()

    async def never_answers(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        entered.set()
        await asyncio.Event().wait()

    loop = asyncio.get_running_loop()
    held = socket.socket()
    try:
        async with _serving(never_answers) as server:
            held.connect(("127.0.0.1", _bound_port(server)))
            held.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            await asyncio.wait_for(entered.wait(), REGISTERED_WAIT_SECONDS)
            started = loop.time()
        waited = loop.time() - started
    finally:
        held.close()
    assert waited >= SERVER_STOP_GRACE_SECONDS, waited
    assert STOP_FLOOR_SECONDS <= waited < STOP_DEADLINE_SECONDS, waited


def _socket(
    ingress_port: int,
    conversation_id: UUID,
    path: str,
    session: str | None = None,
    subprotocols: list[Subprotocol] | None = None,
    site_port: int = 8000,
    origin: str | None = None,
) -> connect:
    label = site_label(conversation_id, site_port)
    headers = {} if session is None else {"cookie": f"{INGRESS_SESSION_COOKIE}={session}"}
    return connect(
        f"ws://{label}.{BASE_HOST}{path}",
        additional_headers=headers,
        subprotocols=subprotocols,
        origin=Origin(origin or f"https://{label}.{BASE_HOST}"),
        max_size=None,
        host="127.0.0.1",
        port=ingress_port,
    )


def _session(workspace_id: UUID, conversation_id: UUID, port: int = 8000) -> str:
    return _token(workspace_id, conversation_id, port=port, kind=INGRESS_SESSION_KIND)


async def test_a_site_socket_relays_both_frame_types_and_the_negotiated_subprotocol(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """The whole point of the relay, in the shape a dev server's live reload uses it: a
    subprotocol the site selects, a text frame carrying JSON, and a binary frame."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _socket(
        socket_ingress,
        conversation_id,
        "/hmr?token=abc",
        session=_session(workspace_id, conversation_id),
        subprotocols=[VITE_SUBPROTOCOL],
    ) as viewer:
        assert viewer.subprotocol == VITE_SUBPROTOCOL
        await viewer.send('{"type":"update"}')
        assert await viewer.recv() == 'echo:{"type":"update"}'
        await viewer.send(b"\x00\x01\x02")
        assert await viewer.recv() == b"echo:\x00\x01\x02"
    handshake = socket_origin.handshakes[0]
    assert handshake.path == "/hmr?token=abc"
    assert handshake.subprotocol == VITE_SUBPROTOCOL
    assert handshake.probe == "dialed"


async def test_the_site_never_sees_the_session_cookie_or_the_viewers_handshake_headers(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _socket(
        socket_ingress,
        conversation_id,
        "/hmr",
        session=_session(workspace_id, conversation_id),
        subprotocols=[VITE_SUBPROTOCOL],
    ) as viewer:
        await viewer.send("ping")
        assert await viewer.recv() == "echo:ping"
    handshake = socket_origin.handshakes[0]
    assert handshake.cookies == ()
    for once in ("sec-websocket-key", "sec-websocket-version", "sec-websocket-protocol"):
        assert handshake.header_names.count(once) == 1, handshake.header_names


async def test_a_socket_at_the_view_path_never_hands_the_token_to_the_site(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """The leak the HTTP routes were split to close, reached over the other protocol."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    view_token = _token(workspace_id, conversation_id)
    session = _session(workspace_id, conversation_id)
    for path in (
        f"{INGRESS_VIEW_PATH}/{view_token}",
        f"{INGRESS_VIEW_PATH}?view_path={view_token}",
    ):
        with pytest.raises(InvalidStatus) as refused:
            async with _socket(socket_ingress, conversation_id, path, session=session):
                pass
        assert refused.value.response.status_code == 403
        assert refused.value.response.body == LINK_NOT_VALID.encode(), path
    assert socket_origin.handshakes == []


async def test_a_socket_without_a_session_is_refused_as_the_proxy_refuses_it(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """One gate, one answer."""
    _, conversation_id = await _seed_conversation("stub:sbx-1")
    with pytest.raises(InvalidStatus) as refused:
        async with _socket(socket_ingress, conversation_id, "/hmr"):
            pass
    assert refused.value.response.status_code == 403
    assert refused.value.response.body == SESSION_ENDED_PAGE.encode()
    assert socket_origin.handshakes == []


async def test_a_socket_bearing_another_sites_session_is_refused(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """A session is minted for one `(conversation, port)` and authorizes that origin alone, so one
    site's own script cannot reach another site's server with the cookie it holds."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    other = _session(workspace_id, conversation_id, port=9999)
    with pytest.raises(InvalidStatus) as refused:
        async with _socket(socket_ingress, conversation_id, "/hmr", session=other):
            pass
    assert refused.value.response.status_code == 403
    assert refused.value.response.body == WRONG_SITE.encode()
    assert socket_origin.handshakes == []


async def test_a_socket_to_a_host_naming_no_site_is_refused(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    session = _session(workspace_id, conversation_id)
    with pytest.raises(InvalidStatus) as refused:
        async with connect(
            f"ws://not-a-label.{BASE_HOST}/hmr",
            additional_headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"},
            origin=Origin(f"https://not-a-label.{BASE_HOST}"),
            host="127.0.0.1",
            port=socket_ingress,
        ):
            pass
    assert refused.value.response.status_code == 404
    assert refused.value.response.body == NO_SITE_HERE.encode()
    assert socket_origin.handshakes == []


async def test_a_socket_to_a_site_whose_sandbox_is_gone_is_refused(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """A conversation row holding no `sandbox_handle` resolves to no sandbox, and there is
    nothing to dial."""
    workspace_id, conversation_id = await _seed_conversation(None)
    with pytest.raises(InvalidStatus) as refused:
        async with _socket(
            socket_ingress,
            conversation_id,
            "/hmr",
            session=_session(workspace_id, conversation_id),
        ):
            pass
    assert refused.value.response.status_code == 503
    assert refused.value.response.body == SITE_GONE.encode()
    assert socket_origin.handshakes == []


async def test_the_sites_own_close_code_reaches_the_viewer(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """A site's client reads the close code to decide whether to retry, so the site's own code and
    reason are relayed rather than replaced by a generic one."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _socket(
        socket_ingress,
        conversation_id,
        SOCKET_CLOSE_PATH,
        session=_session(workspace_id, conversation_id),
    ) as viewer:
        with pytest.raises(ConnectionClosed):
            await viewer.recv()
    assert viewer.close_code == SITE_CLOSE_CODE
    assert viewer.close_reason == SITE_CLOSE_REASON


async def test_a_socket_opened_by_another_site_is_refused(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    session = _session(workspace_id, conversation_id)
    _, neighbour = await _seed_conversation("stub:sbx-1")
    foreign = f"https://{site_label(neighbour, 8000)}.{BASE_HOST}"
    for origin in (foreign, f"https://{BASE_HOST}", "null"):
        with pytest.raises(InvalidStatus) as refused:
            async with _socket(
                socket_ingress, conversation_id, "/hmr", session=session, origin=origin
            ):
                pass
        assert refused.value.response.status_code == 403
        assert refused.value.response.body == FOREIGN_ORIGIN.encode(), origin
    with pytest.raises(InvalidStatus) as bare:
        async with connect(
            f"ws://{site_label(conversation_id, 8000)}.{BASE_HOST}/hmr",
            additional_headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"},
            host="127.0.0.1",
            port=socket_ingress,
        ):
            pass
    assert bare.value.response.status_code == 403
    assert bare.value.response.body == FOREIGN_ORIGIN.encode()
    assert socket_origin.handshakes == []


async def test_the_viewer_leaving_first_ends_the_relay(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """The direction that did not finish is cancelled by the one that did."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await asyncio.wait_for(
        _two_sockets_in_series(socket_ingress, workspace_id, conversation_id),
        RELAY_DEADLINE_SECONDS,
    )
    assert len(socket_origin.handshakes) == 2


async def _two_sockets_in_series(
    ingress_port: int, workspace_id: UUID, conversation_id: UUID
) -> None:
    async with _socket(
        ingress_port,
        conversation_id,
        "/hmr",
        session=_session(workspace_id, conversation_id),
    ) as viewer:
        await viewer.send("ping")
        assert await viewer.recv() == "echo:ping"
    async with _socket(
        ingress_port,
        conversation_id,
        "/hmr",
        session=_session(workspace_id, conversation_id),
    ) as second:
        await second.send("again")
        assert await second.recv() == "echo:again"


async def test_a_subprotocol_the_site_declines_is_not_echoed_to_the_viewer(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """The viewer is told what the site chose. Echoing the offer instead would have a client believe
    a protocol is in force that the site never agreed to speak."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _socket(
        socket_ingress,
        conversation_id,
        "/hmr",
        session=_session(workspace_id, conversation_id),
        subprotocols=[Subprotocol("some-other-protocol")],
    ) as viewer:
        assert viewer.subprotocol is None
        await viewer.send("ping")
        assert await viewer.recv() == "echo:ping"
    assert socket_origin.handshakes[0].subprotocol == ""


async def test_the_handshake_carries_one_of_every_negotiated_field(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _socket(
        socket_ingress,
        conversation_id,
        "/hmr",
        session=_session(workspace_id, conversation_id),
        subprotocols=[VITE_SUBPROTOCOL],
    ) as viewer:
        await viewer.send("ping")
        assert await viewer.recv() == "echo:ping"
    assert socket_origin.handshakes[0].header_names.count("sec-websocket-extensions") == 0


async def test_a_frame_over_the_bound_ends_the_socket_in_both_directions(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _socket(
        socket_ingress,
        conversation_id,
        SOCKET_LARGE_PATH,
        session=_session(workspace_id, conversation_id),
    ) as large:
        assert len(await large.recv()) == LARGE_FRAME_BYTES
    async with _socket(
        socket_ingress,
        conversation_id,
        SOCKET_FLOOD_PATH,
        session=_session(workspace_id, conversation_id),
    ) as viewer:
        with pytest.raises(ConnectionClosed):
            await viewer.recv()
    async with _socket(
        socket_ingress,
        conversation_id,
        SOCKET_SINK_PATH,
        session=_session(workspace_id, conversation_id),
    ) as sender:
        await sender.send("y" * LARGE_FRAME_BYTES)
        assert await sender.recv() == str(LARGE_FRAME_BYTES)
        with pytest.raises(ConnectionClosed):
            await sender.send("y" * (WEBSOCKET_MAX_MESSAGE_BYTES + 1))
            await sender.recv()
    assert socket_origin.received == [LARGE_FRAME_BYTES], socket_origin.received
    assert len(socket_origin.handshakes) == 3


async def test_a_site_that_vanishes_without_a_close_frame_ends_as_an_unexpected_condition(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """1006 stands for a close this end only observed and RFC 6455 forbids sending it, so relaying
    the site's code verbatim would put an illegal code on the wire. Substituted, not repeated."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with _socket(
        socket_ingress,
        conversation_id,
        SOCKET_ABORT_PATH,
        session=_session(workspace_id, conversation_id),
    ) as viewer:
        with pytest.raises(ConnectionClosed):
            await viewer.recv()
    assert viewer.close_code not in (1005, 1006, 1015)
    assert viewer.close_code == 1011


async def test_a_socket_to_a_site_that_does_not_answer_is_refused_before_it_is_accepted(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        app = _server(_StubCarrier(_unused_port()), upstream).app()
        async with _serving(app) as server:
            with pytest.raises(InvalidStatus) as refused:
                async with _socket(
                    _bound_port(server),
                    conversation_id,
                    "/hmr",
                    session=_session(workspace_id, conversation_id),
                ):
                    pass
    assert refused.value.response.status_code == 503
    assert refused.value.response.body == SITE_NOT_ANSWERING.encode()


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_a_site_that_accepts_and_never_answers_the_handshake_times_out(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead port refuses at once; a hung one would hold the viewer's handshake open forever,
    and a site is agent-authored code that can hang."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    monkeypatch.setattr(ingress_serve, "WEBSOCKET_OPEN_TIMEOUT_SECONDS", 0.2)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")

    async def accept_and_hang(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(HANDSHAKE_BYTES)
        await asyncio.Event().wait()

    hung = await asyncio.start_server(accept_and_hang, "127.0.0.1", 0)
    try:
        async with upstream_client() as upstream:
            app = _server(_StubCarrier(hung.sockets[0].getsockname()[1]), upstream).app()
            async with _serving(app) as server:
                started = asyncio.get_running_loop().time()
                with pytest.raises(InvalidStatus) as refused:
                    async with _socket(
                        _bound_port(server),
                        conversation_id,
                        "/hmr",
                        session=_session(workspace_id, conversation_id),
                    ):
                        pass
                waited = asyncio.get_running_loop().time() - started
    finally:
        hung.close()
    assert waited < OPEN_TIMEOUT_HEADROOM_SECONDS, waited
    assert refused.value.response.status_code == 503
    assert refused.value.response.body == SITE_NOT_ANSWERING.encode()


async def test_a_viewer_that_vanishes_mid_push_leaves_the_ingress_serving(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """The relay's failure branch, reached the way it is reached in practice: a viewer whose
    transport resets while the site is mid-push, with no loop yield in between."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")

    async def abort_mid_push() -> None:
        async with _socket(
            socket_ingress,
            conversation_id,
            SOCKET_PUSH_PATH,
            session=_session(workspace_id, conversation_id),
        ) as viewer:
            await viewer.recv()
            viewer.transport.abort()

    for _ in range(ABORT_ROUNDS):
        await asyncio.wait_for(abort_mid_push(), RELAY_DEADLINE_SECONDS)
    async with _socket(
        socket_ingress,
        conversation_id,
        "/hmr",
        session=_session(workspace_id, conversation_id),
    ) as after:
        await after.send("still here")
        assert await after.recv() == "echo:still here"
    assert len(socket_origin.handshakes) == ABORT_ROUNDS + 1


def test_the_ingress_refuses_to_bind_when_its_database_is_unreachable(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UFO_OWNER_DSN", "postgresql://ufo:ufo@127.0.0.1:1/ufo")
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "ingress-boot-test-secret")
    monkeypatch.setattr(
        ingress_serve,
        "load_config",
        lambda: Config(
            database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
            blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
            sandbox=SandboxConfig(
                backend="local", ingress_port=0, ingress_public_url="https://ingress.test"
            ),
        ),
    )

    def never(*_: object, **__: object) -> None:
        raise AssertionError("bound the ingress with an unreachable database")

    monkeypatch.setattr(ingress_serve.uvicorn, "run", never)
    try:
        with pytest.raises(sa.exc.OperationalError):
            ingress_serve.run()
    finally:
        asyncio.run(dispose_db())


STORED_SITE_NAME = "app-home"
INDEX_BYTES = b"<!doctype html><h1>stored</h1>"
APP_JS_BYTES = b"console.log('stored')"
DOCS_BYTES = b"<p>docs</p>"
STORED_FILES = {
    "index.html": (INDEX_BYTES, "text/html"),
    "assets/app.js": (APP_JS_BYTES, "text/javascript"),
    "docs/index.html": (DOCS_BYTES, "text/html"),
}


def _stored_etag(data: bytes) -> str:
    return f'"{hashlib.sha256(data).hexdigest()}"'


async def _seed_stored_site(
    blobs: FilesystemBlobStore,
    workspace_id: UUID,
    conversation_id: UUID,
    files: dict[str, tuple[bytes, str]],
    port: int = 8000,
) -> None:
    """Register a stored site the way a deploy leaves one: file bytes under a tokened root in the
    workspace's own blob prefix, and the manifest naming them on the row."""
    root = f"sites/{conversation_id}/{STORED_SITE_NAME}/{uuid4().hex}/"
    named: dict[str, dict[str, object]] = {}
    for path, (data, media_type) in files.items():
        await blobs.put(f"workspaces/{workspace_id}/{root}{path}", data)
        named[path] = {
            "size": len(data),
            "media_type": media_type,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    await _seed_hosted_site(
        workspace_id,
        conversation_id,
        port,
        source_manifest=json.dumps({"root": root, "files": named}),
    )


@dataclass(frozen=True)
class _NeverDialCarrier(_StubCarrier):
    """A stored site never dials: the carrier being reached at all is the failure under test."""

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        raise AssertionError("a stored site dialed the sandbox")


@pytest.fixture
async def stored_ingress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, FilesystemBlobStore]]:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    async with upstream_client() as upstream:
        server = _server(_NeverDialCarrier(port=0), upstream, blob=blobs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            yield client, blobs


async def test_a_stored_site_is_served_from_the_store_and_never_dials(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    got = await client.get(f"{_origin(conversation_id)}/")
    assert got.status_code == 200
    assert got.content == INDEX_BYTES
    assert got.headers["content-type"].startswith("text/html")
    assert got.headers["content-length"] == str(len(INDEX_BYTES))
    assert got.headers["etag"] == _stored_etag(INDEX_BYTES)
    assert got.headers["cache-control"] == STORED_SITE_CACHE
    assert got.headers["x-content-type-options"] == "nosniff"
    assert _framers(got) == [APP_ORIGIN]


async def test_a_stored_sites_nested_asset_and_directory_index_are_served(
    db, stored_ingress
) -> None:
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    asset = await client.get(f"{_origin(conversation_id)}/assets/app.js")
    assert (asset.status_code, asset.content) == (200, APP_JS_BYTES)
    assert asset.headers["content-type"].startswith("text/javascript")
    for entry in ("/docs", "/docs/"):
        indexed = await client.get(f"{_origin(conversation_id)}{entry}")
        assert (indexed.status_code, indexed.content) == (200, DOCS_BYTES), entry


async def test_a_path_a_stored_site_does_not_name_is_404(db, stored_ingress) -> None:
    """The manifest is the site's whole filesystem, so a miss is a miss whatever its shape — a
    dotted escape is a string no manifest key equals, with no root anywhere for it to walk."""
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    missing = await client.get(f"{_origin(conversation_id)}/nope.js")
    assert (missing.status_code, missing.text) == (404, NOT_FOUND)
    escape = await client.get(f"{_origin(conversation_id)}/%2e%2e/index.html")
    assert escape.status_code == 404


async def test_a_stored_site_revalidates_by_digest(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    etag = _stored_etag(INDEX_BYTES)
    unchanged = await client.get(f"{_origin(conversation_id)}/", headers={"if-none-match": etag})
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == etag
    assert unchanged.headers["cache-control"] == STORED_SITE_CACHE
    listed = await client.get(
        f"{_origin(conversation_id)}/", headers={"if-none-match": f'"stale", {etag}'}
    )
    assert listed.status_code == 304
    moved = await client.get(f"{_origin(conversation_id)}/", headers={"if-none-match": '"stale"'})
    assert (moved.status_code, moved.content) == (200, INDEX_BYTES)


async def test_a_stored_site_answers_head_with_the_description_alone(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    head = await client.head(f"{_origin(conversation_id)}/assets/app.js")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(APP_JS_BYTES))
    assert head.headers["etag"] == _stored_etag(APP_JS_BYTES)


async def test_a_stored_site_takes_no_method_but_get_and_head(db, stored_ingress) -> None:
    """A directory of files speaks GET and HEAD and nothing else — there is no server behind this
    to forward a POST to, so the refusal is the ingress's own."""
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        refused = await client.request(method, f"{_origin(conversation_id)}/")
        assert refused.status_code == 405, method
        assert refused.headers["allow"] == "GET, HEAD"


async def test_a_stored_site_still_gates_on_the_session(db, stored_ingress) -> None:
    """Stored bytes answer to the same door as dialed ones: no session, no read — the store is
    never touched for an unauthorized viewer."""
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    got = await client.get(f"{_origin(conversation_id)}/")
    assert (got.status_code, got.text) == (403, SESSION_ENDED_PAGE)


async def test_a_manifest_naming_a_vanished_blob_is_404(db, stored_ingress) -> None:
    """The window where a redeploy retired the old root between the row read and the blob read:
    answered as a miss for the refresh to heal, never a 200 that dies mid-stream."""
    client, _blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    manifest = {
        "root": f"sites/{conversation_id}/{STORED_SITE_NAME}/{uuid4().hex}/",
        "files": {"index.html": {"size": 5, "media_type": "text/html", "sha256": "0" * 64}},
    }
    await _seed_hosted_site(
        workspace_id, conversation_id, port=8000, source_manifest=json.dumps(manifest)
    )
    await _open(client, workspace_id, conversation_id)
    got = await client.get(f"{_origin(conversation_id)}/")
    assert (got.status_code, got.text) == (404, NOT_FOUND)


async def test_a_hosted_row_without_a_manifest_still_dials(db, ingress) -> None:
    """The dial path is what a manifest-less row keeps, byte for byte: proxied bytes, `no-store`,
    and the sandbox actually reached."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _seed_hosted_site(workspace_id, conversation_id, port=8000)
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert got.json()["path"] == "/index.html"
    assert got.headers.get_list("cache-control") == [UNCACHEABLE]


async def test_a_socket_to_a_stored_site_is_refused(
    db, socket_ingress: int, socket_origin: _SocketOrigin
) -> None:
    """A stored site has no server, so it speaks no socket protocol — refused on the handshake
    with its own sentence, and nothing upstream ever sees one."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    manifest = {"root": f"sites/{conversation_id}/{STORED_SITE_NAME}/{uuid4().hex}/", "files": {}}
    await _seed_hosted_site(
        workspace_id, conversation_id, port=8000, source_manifest=json.dumps(manifest)
    )
    with pytest.raises(InvalidStatus) as refused:
        async with _socket(
            socket_ingress,
            conversation_id,
            "/hmr",
            session=_session(workspace_id, conversation_id),
        ):
            pass
    assert refused.value.response.status_code == 501
    assert refused.value.response.body == SITE_HAS_NO_SOCKET.encode()
    assert refused.value.response.headers["cache-control"] == UNCACHEABLE
    assert socket_origin.handshakes == []


JAVASCRIPT_MEDIA_TYPES = frozenset({"text/javascript", "application/javascript"})
"""What `mimetypes.guess_type` returns for a `.js` name — `text/javascript` off Python's own table,
`application/javascript` where a host's `/etc/mime.types` overrides it. Both run a module script."""
SHIPPED_SLUG = "wiki"
SHIPPED_DIGEST = "9f3a1c2b4d5e6f70"
SHIPPED_ETAG = f'"{SHIPPED_DIGEST}"'
SHIPPED_INDEX = b'<!doctype html><script type="module" src="/assets/app.7c2b.js"></script>'
SHIPPED_APP = b"mountApp()"
SHIPPED_KIT = b"export function mountApp() {}"
SHIPPED_FILES = {
    f"{SHIPPED_SLUG}/index.html": (SHIPPED_INDEX, "text/html"),
    "assets/app.7c2b.js": (SHIPPED_APP, "text/javascript"),
    "assets/kit.4a9e.js": (SHIPPED_KIT, "text/javascript"),
}


async def _seed_shipped_bundle(
    blobs: FilesystemBlobStore,
    digest: str,
    files: dict[str, tuple[bytes, str]],
) -> None:
    for path, (data, _media_type) in files.items():
        await blobs.put(f"apps/{digest}/{path}", data)


async def _open_shipped(
    client: httpx.AsyncClient,
    workspace_id: UUID,
    anchor: UUID,
    slug: str,
    digest: str,
    port: int = 8000,
) -> None:
    """Arrive at a shipped page the way its frame does: a view token carrying the shipped reference
    beside the synthetic anchor, traded for the session cookie the client's jar then carries."""
    token = mint_ingress_token(
        IngressClaims(
            workspace_id=workspace_id,
            conversation_id=anchor,
            port=port,
            expires_at=int(datetime.now(UTC).timestamp()) + 900,
            shipped=ShippedClaim(slug=slug, digest=digest),
        ),
        INGRESS_VIEW_KIND,
    )
    got = await client.get(f"{_origin(anchor, port)}{INGRESS_VIEW_PATH}/{token}")
    assert got.status_code == 303, got.text
    assert got.headers["location"] == f"/?{SHIPPED_VERSION_PARAM}={digest}"


async def test_a_shipped_bundle_serves_row_less_from_the_fleet_store(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id = uuid4()
    anchor = uuid5(NAMESPACE_URL, f"{workspace_id}:{SHIPPED_SLUG}")
    await _seed_shipped_bundle(blobs, SHIPPED_DIGEST, SHIPPED_FILES)
    await _open_shipped(client, workspace_id, anchor, SHIPPED_SLUG, SHIPPED_DIGEST)

    index = await client.get(f"{_origin(anchor)}/?{SHIPPED_VERSION_PARAM}={SHIPPED_DIGEST}")
    assert index.status_code == 200
    assert index.content == SHIPPED_INDEX
    assert index.headers["content-type"].startswith("text/html")
    assert index.headers["content-length"] == str(len(SHIPPED_INDEX))
    assert index.headers["etag"] == SHIPPED_ETAG
    assert index.headers["cache-control"] == SHIPPED_CACHE
    assert index.headers["x-content-type-options"] == "nosniff"
    assert index.headers[CONTENT_SECURITY_POLICY].startswith(FRAME_ANCESTORS_DIRECTIVE)

    unversioned = await client.get(f"{_origin(anchor)}/")
    assert unversioned.headers["cache-control"] == STORED_SITE_CACHE

    app = await client.get(f"{_origin(anchor)}/assets/app.7c2b.js")
    assert (app.status_code, app.content) == (200, SHIPPED_APP)
    assert app.headers["content-type"].split(";")[0] in JAVASCRIPT_MEDIA_TYPES
    assert app.headers["cache-control"] == SHIPPED_CACHE

    kit = await client.get(f"{_origin(anchor)}/assets/kit.4a9e.js")
    assert (kit.status_code, kit.content) == (200, SHIPPED_KIT)
    assert kit.headers["content-type"].split(";")[0] in JAVASCRIPT_MEDIA_TYPES
    assert kit.headers["cache-control"] == SHIPPED_CACHE


async def test_a_shipped_bundle_unknown_path_is_404(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id = uuid4()
    anchor = uuid5(NAMESPACE_URL, f"{workspace_id}:{SHIPPED_SLUG}")
    await _seed_shipped_bundle(blobs, SHIPPED_DIGEST, SHIPPED_FILES)
    await _open_shipped(client, workspace_id, anchor, SHIPPED_SLUG, SHIPPED_DIGEST)
    missing = await client.get(f"{_origin(anchor)}/nope.js")
    assert (missing.status_code, missing.text) == (404, NOT_FOUND)


async def test_a_shipped_bundle_revalidates_by_digest(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id = uuid4()
    anchor = uuid5(NAMESPACE_URL, f"{workspace_id}:{SHIPPED_SLUG}")
    await _seed_shipped_bundle(blobs, SHIPPED_DIGEST, SHIPPED_FILES)
    await _open_shipped(client, workspace_id, anchor, SHIPPED_SLUG, SHIPPED_DIGEST)
    fresh = await client.get(
        f"{_origin(anchor)}/?{SHIPPED_VERSION_PARAM}={SHIPPED_DIGEST}",
        headers={"if-none-match": SHIPPED_ETAG},
    )
    assert fresh.status_code == 304
    assert fresh.content == b""


async def test_a_shipped_bundle_manifest_is_fixed_by_its_digest(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id = uuid4()
    anchor = uuid5(NAMESPACE_URL, f"{workspace_id}:{SHIPPED_SLUG}")
    await _seed_shipped_bundle(blobs, SHIPPED_DIGEST, SHIPPED_FILES)
    await _open_shipped(client, workspace_id, anchor, SHIPPED_SLUG, SHIPPED_DIGEST)
    assert (
        await client.get(f"{_origin(anchor)}/?{SHIPPED_VERSION_PARAM}={SHIPPED_DIGEST}")
    ).status_code == 200

    late = b"not part of the content-addressed tree"
    await blobs.put(f"apps/{SHIPPED_DIGEST}/assets/late.1234.js", late)
    got = await client.get(f"{_origin(anchor)}/assets/late.1234.js")
    assert (got.status_code, got.text) == (404, NOT_FOUND)


async def test_a_shipped_bundle_whose_digest_is_gone_is_404(db, stored_ingress) -> None:
    """A digest naming no published bundle — the window a redeploy retired it in — answers 404 for
    the refresh to heal, never an error mid-serve."""
    client, _ = stored_ingress
    workspace_id = uuid4()
    anchor = uuid5(NAMESPACE_URL, f"{workspace_id}:{SHIPPED_SLUG}")
    await _open_shipped(client, workspace_id, anchor, SHIPPED_SLUG, "deadbeefdeadbeef")
    got = await client.get(f"{_origin(anchor)}/")
    assert got.status_code == 404


@dataclass(frozen=True)
class _CountingCarrier(_StubCarrier):
    """The stub carrier, counting the dials the heartbeat makes."""

    dials: list[int] = field(default_factory=list)

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        self.dials.append(port)
        return await super().dial(handle, port)


@dataclass(frozen=True)
class _GoneCarrier(_CountingCarrier):
    """A carrier whose sandbox is gone: every dial raises the one error `dial` promises."""

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        self.dials.append(port)
        raise SandboxUnreachable("sandbox is gone")


@pytest.fixture
async def heartbeat_ingress(
    origin_port: int, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, IngressServe, _CountingCarrier]]:
    """The dial path with the carrier counted and the server itself in hand — the throttle's memory
    lives on the instance, so a test that ages it needs the object the client is talking to."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    carrier = _CountingCarrier(port=origin_port)
    async with upstream_client() as upstream:
        server = _server(carrier, upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            yield client, server, carrier


async def test_a_dialed_html_page_carries_the_heartbeat_tag_once(db, heartbeat_ingress) -> None:
    """The document a live site answers with, plus one script element after it."""
    client, _server_object, _carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(client, workspace_id, conversation_id)
    got = await client.get(f"{_origin(conversation_id)}{HTML_PAGE_PATH}")
    assert got.status_code == 200
    assert got.content == HTML_PAGE_BYTES + HEARTBEAT_TAG
    assert got.content.count(HEARTBEAT_TAG) == 1
    assert "content-length" not in got.headers


async def test_a_dialed_json_response_carries_no_heartbeat_tag(db, heartbeat_ingress) -> None:
    """A site's data call is bytes a caller parses, so a script element appended to it is a
    broken response."""
    client, _server_object, _carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(client, workspace_id, conversation_id)
    got = await client.get(f"{_origin(conversation_id)}/api/state")
    assert got.status_code == 200
    assert HEARTBEAT_TAG not in got.content
    assert got.json()["path"] == "/api/state"
    assert got.headers["content-length"] == str(len(got.content))


async def test_a_head_of_an_html_page_carries_no_tag(db, heartbeat_ingress) -> None:
    """A HEAD has no body to append to, so its length still describes the document the origin would
    have sent."""
    client, _server_object, _carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(client, workspace_id, conversation_id)
    got = await client.head(f"{_origin(conversation_id)}{HTML_PAGE_PATH}")
    assert got.status_code == 200
    assert got.content == b""
    assert got.headers["content-length"] == str(len(HTML_PAGE_BYTES))


async def test_the_heartbeat_script_is_served_by_the_ingress_and_gates_on_visibility(
    db, heartbeat_ingress
) -> None:
    """The tag's target is this process's own script at the site's origin, never a path forwarded
    to the site: the bytes are the module's own, typed as script, and stored by nothing."""
    client, _server_object, carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(client, workspace_id, conversation_id)
    got = await client.get(f"{_origin(conversation_id)}{HEARTBEAT_SCRIPT_PATH}")
    assert got.status_code == 200
    assert got.text == HEARTBEAT_SCRIPT
    assert got.headers["content-type"].startswith(HEARTBEAT_MEDIA_TYPE)
    assert got.headers["cache-control"] == UNCACHEABLE
    assert "document.visibilityState==='visible'" in got.text
    assert "visibilitychange" in got.text
    assert f"setInterval(tick,{HEARTBEAT_PING_SECONDS * 1000})" in got.text
    assert HEARTBEAT_PING_PATH in got.text
    assert carrier.dials == []


async def test_a_ping_renews_the_lease_through_the_carrier(db, heartbeat_ingress) -> None:
    """What the whole endpoint is for: a page open in front of a member renews the box its site
    runs on, through the same session the page was loaded with and the same dial the proxy makes."""
    client, _server_object, carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(client, workspace_id, conversation_id)
    got = await client.post(f"{_origin(conversation_id)}{HEARTBEAT_PING_PATH}")
    assert got.status_code == 204
    assert got.content == b""
    assert carrier.dials == [8000]


async def test_a_ping_without_a_session_renews_nothing(db, heartbeat_ingress) -> None:
    """Knowing the path renews nothing. The ping runs the proxy's own gate, so a request carrying no
    session is refused exactly as a page request is."""
    client, _server_object, carrier = heartbeat_ingress
    _workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    got = await client.post(f"{_origin(conversation_id)}{HEARTBEAT_PING_PATH}")
    assert (got.status_code, got.text) == (403, SESSION_ENDED_PAGE)
    assert carrier.dials == []


async def test_a_ping_carrying_an_expired_session_renews_nothing(db, heartbeat_ingress) -> None:
    client, _server_object, carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    expired = _token(workspace_id, conversation_id, ttl=-1, kind=INGRESS_SESSION_KIND)
    got = await client.post(
        f"{_origin(conversation_id)}{HEARTBEAT_PING_PATH}",
        headers={"cookie": f"{INGRESS_SESSION_COOKIE}={expired}"},
    )
    assert (got.status_code, got.text) == (403, SESSION_ENDED_PAGE)
    assert carrier.dials == []


async def test_a_ping_at_a_site_the_session_does_not_open_renews_nothing(
    db, heartbeat_ingress
) -> None:
    """One site's session pings its own site or nothing: the claims must name the very origin the
    ping arrived at, so a member holding one site's cookie cannot keep a neighbour's box awake."""
    client, _server_object, carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    _other_workspace, other_conversation = await _seed_conversation("stub:sbx-1")
    session = _token(workspace_id, conversation_id, kind=INGRESS_SESSION_KIND)
    got = await client.post(
        f"{_origin(other_conversation)}{HEARTBEAT_PING_PATH}",
        headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"},
    )
    assert (got.status_code, got.text) == (403, WRONG_SITE)
    assert carrier.dials == []


async def test_a_ping_takes_no_method_but_post(db, heartbeat_ingress) -> None:
    """The path is the ingress's on every method, so a GET of it answers 405 rather than being
    forwarded to the site as a path of its own."""
    client, _server_object, carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(client, workspace_id, conversation_id)
    got = await client.get(f"{_origin(conversation_id)}{HEARTBEAT_PING_PATH}")
    assert got.status_code == 405
    assert got.headers["allow"] == "POST"
    assert carrier.dials == []


async def test_a_burst_of_pings_renews_once_and_again_after_the_window(
    db, heartbeat_ingress
) -> None:
    """Every tab on a site pings on its own clock, so the throttle is what turns a room full of
    them into one carrier call per window."""
    client, server_object, carrier = heartbeat_ingress
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(client, workspace_id, conversation_id)
    url = f"{_origin(conversation_id)}{HEARTBEAT_PING_PATH}"
    for _ping in range(3):
        assert (await client.post(url)).status_code == 204
    assert carrier.dials == [8000]

    key = (conversation_id, 8000)
    server_object.renewals[key] -= HEARTBEAT_RENEWAL_SECONDS + 1
    assert (await client.post(url)).status_code == 204
    assert carrier.dials == [8000, 8000]


async def test_a_ping_whose_dial_fails_answers_204_and_is_retried(
    db, origin_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A site whose sandbox is gone is not this endpoint's news to break: the member's own page
    load reports the site, and a ping that could not renew answers as quietly as one that did."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    carrier = _GoneCarrier(port=origin_port)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(carrier, upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            session = _token(workspace_id, conversation_id, kind=INGRESS_SESSION_KIND)
            held = {"cookie": f"{INGRESS_SESSION_COOKIE}={session}"}
            url = f"{_origin(conversation_id)}{HEARTBEAT_PING_PATH}"
            first = await client.post(url, headers=held)
            second = await client.post(url, headers=held)
    assert (first.status_code, second.status_code) == (204, 204)
    assert carrier.dials == [8000, 8000]
    assert server.renewals == {}


async def test_a_ping_for_a_stored_site_renews_nothing(db, stored_ingress) -> None:
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    got = await client.post(f"{_origin(conversation_id)}{HEARTBEAT_PING_PATH}")
    assert got.status_code == 204
    assert got.content == b""


async def test_a_stored_page_carries_no_heartbeat_tag(db, stored_ingress) -> None:
    """Nothing to renew, nothing to inject: a stored site's document is served byte for byte from
    the store, under the length and digest its deploy measured."""
    client, blobs = stored_ingress
    workspace_id, conversation_id = await _seed_conversation(None)
    await _seed_stored_site(blobs, workspace_id, conversation_id, STORED_FILES)
    await _open(client, workspace_id, conversation_id)
    got = await client.get(f"{_origin(conversation_id)}/")
    assert got.content == INDEX_BYTES
    assert HEARTBEAT_TAG not in got.content


def _dev_target(port: int) -> DialTarget:
    return DialTarget(host=f"127.0.0.1:{port}", tls=False)


def test_the_apps_dev_server_dial_is_the_knobs_authority_and_scheme(database_url: str) -> None:
    assert ingress_serve.apps_dev_target(None) is None
    assert ingress_serve.apps_dev_target("http://web:5174") == DialTarget(
        host="web:5174", tls=False
    )
    assert ingress_serve.apps_dev_target("https://apps.example.com") == DialTarget(
        host="apps.example.com", tls=True
    )


async def test_a_shipped_page_is_relayed_to_the_apps_dev_server_a_deploy_names(
    db, origin_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id = uuid4()
    anchor = shipped_anchor(workspace_id, SHIPPED_SLUG)
    async with upstream_client() as upstream:
        server = _server(
            _NeverDialCarrier(port=0), upstream, apps_dev_server=_dev_target(origin_port)
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open_shipped(client, workspace_id, anchor, SHIPPED_SLUG, SHIPPED_DIGEST)
            page = await client.get(f"{_origin(anchor)}/?{SHIPPED_VERSION_PARAM}={SHIPPED_DIGEST}")
            assert page.status_code == 200
            assert (
                page.json()["path"] == f"/{SHIPPED_SLUG}/?{SHIPPED_VERSION_PARAM}={SHIPPED_DIGEST}"
            )
            assert page.json()["probe"] == ""
            assert page.headers["cache-control"] == UNCACHEABLE
            assert page.headers[CONTENT_SECURITY_POLICY].startswith(FRAME_ANCESTORS_DIRECTIVE)
            module = await client.get(f"{_origin(anchor)}/@vite/client")
            assert module.status_code == 200
            assert module.json()["path"] == "/@vite/client"


async def test_a_shipped_pages_socket_reaches_the_apps_dev_server(
    db, socket_origin: _SocketOrigin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shipped page speaks no socket protocol until a dev server holds it, whose reload channel
    the frame opens at the page's own origin."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id = uuid4()
    anchor = shipped_anchor(workspace_id, SHIPPED_SLUG)
    async with upstream_client() as upstream:
        app = _server(
            _NeverDialCarrier(port=0), upstream, apps_dev_server=_dev_target(socket_origin.port)
        ).app()
        async with _serving(
            app, ws_max_size=WEBSOCKET_MAX_MESSAGE_BYTES, ws_per_message_deflate=False
        ) as server:
            session = mint_ingress_token(
                IngressClaims(
                    workspace_id=workspace_id,
                    conversation_id=anchor,
                    port=8000,
                    expires_at=int(datetime.now(UTC).timestamp()) + 900,
                    shipped=ShippedClaim(slug=SHIPPED_SLUG, digest=SHIPPED_DIGEST),
                ),
                INGRESS_SESSION_KIND,
            )
            async with _socket(
                _bound_port(server),
                anchor,
                "/",
                session=session,
                subprotocols=[VITE_SUBPROTOCOL],
            ) as viewer:
                assert viewer.subprotocol == VITE_SUBPROTOCOL
                await viewer.send('{"type":"ping"}')
                assert await viewer.recv() == 'echo:{"type":"ping"}'
    assert [handshake.path for handshake in socket_origin.handshakes] == ["/"]
    assert socket_origin.handshakes[0].subprotocol == VITE_SUBPROTOCOL


async def test_a_shipped_page_ping_renews_nothing(db, stored_ingress) -> None:
    """A shipped app page's origin is a synthetic anchor with no conversation and no sandbox behind
    it, so its ping short-circuits before any row is read."""
    client, blobs = stored_ingress
    workspace_id = uuid4()
    anchor = uuid5(NAMESPACE_URL, f"{workspace_id}:{SHIPPED_SLUG}")
    await _seed_shipped_bundle(blobs, SHIPPED_DIGEST, SHIPPED_FILES)
    await _open_shipped(client, workspace_id, anchor, SHIPPED_SLUG, SHIPPED_DIGEST)
    got = await client.post(f"{_origin(anchor)}{HEARTBEAT_PING_PATH}")
    assert got.status_code == 204
