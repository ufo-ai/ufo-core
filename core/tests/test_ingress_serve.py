import json
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa

from ufo.bearer import UFO_TOKEN_SECRET_ENV
from ufo.db import workspace_tx
from ufo.ingress_serve import (
    CACHE_DIRECTIVE_HEADERS,
    INGRESS_SESSION_COOKIE,
    INGRESS_SESSION_TTL_SECONDS,
    LINK_NOT_VALID,
    NO_SITE_HERE,
    SESSION_ENDED,
    SITE_GONE,
    SITE_NOT_ANSWERING,
    UNCACHEABLE,
    WRONG_SITE,
    IngressServe,
    ingress_base_host,
    upstream_client,
)
from ufo.sandbox.ingress_host import site_label
from ufo.sandbox.ingress_token import (
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    IngressClaims,
    IngressTokenKind,
    mint_ingress_token,
    verify_ingress_token,
)
from ufo.sandbox.session import (
    Carrier,
    DialTarget,
    ExecResult,
    SandboxHandle,
    SandboxSpec,
    SandboxUnreachable,
)
from ufo.schema import tables

BACKEND = "stub"
BASE_HOST = "sites.example.test"
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


class _OriginHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
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

    do_GET = do_POST = _respond

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
    """Stands in for the carrier so the tests assert the ingress's own behavior — origin resolution,
    session gating, handle resolution, header and body forwarding — against a real HTTP origin.
    Every verb the ingress must never exercise raises."""

    port: int

    async def dial(self, handle: SandboxHandle, port: int) -> DialTarget:
        assert handle.container_id == "sbx-1"
        return DialTarget(
            host=f"127.0.0.1:{self.port}", tls=False, headers={"x-dial-probe": "dialed"}
        )

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("the ingress never creates a sandbox")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
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
                model="claude-opus-4-8",
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


def _token(
    workspace_id: UUID,
    conversation_id: UUID,
    port: int = 8000,
    ttl: int = 900,
    kind: IngressTokenKind = INGRESS_VIEW_KIND,
) -> str:
    return mint_ingress_token(
        IngressClaims(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            port=port,
            expires_at=int(datetime.now(UTC).timestamp()) + ttl,
        ),
        kind,
    )


def _origin(conversation_id: UUID, port: int = 8000) -> str:
    """The site's own address. Built from the codec rather than copied from `ingress_url`'s format
    string — the two agreeing is `test_ingress_url_addresses_the_site_the_ingress_resolves`'s job,
    and duplicating the format here would only hide a disagreement from both."""
    return f"https://{site_label(conversation_id, port)}.{BASE_HOST}"


async def _open(
    client: httpx.AsyncClient, workspace_id: UUID, conversation_id: UUID, port: int = 8000
) -> str:
    """Arrive at the site the way the frame's iframe does, and answer with the session the ingress
    bound — the client's own jar holds it too, host-only, so every later request carries it."""
    token = _token(workspace_id, conversation_id, port)
    got = await client.get(f"{_origin(conversation_id, port)}{INGRESS_VIEW_PATH}/{token}")
    assert got.status_code == 303, got.text
    return got.cookies[INGRESS_SESSION_COOKIE]


def _server(carrier: Carrier, upstream: httpx.AsyncClient) -> IngressServe:
    """Annotated as the `Carrier` it stands in for, with no suppression: a stub that drifts from the
    protocol it fakes stops standing in for the dependency, and mypy is what catches the drift."""
    return IngressServe(backend=BACKEND, base_host=BASE_HOST, carrier=carrier, client=upstream)


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
    """The forwarded list is the whole list. A client's `build_request` merges its own defaults
    under it, putting an `accept`, a `user-agent`, a `connection` and — the one with teeth — an
    `accept-encoding: gzip, deflate` on the wire the viewer never sent; `_body` relays raw bytes,
    so an origin honouring that fabricated negotiation would return gzip to a viewer who never asked
    for it. A bare `httpx.Request` sends what it is given, plus the `host` HTTP requires. The
    viewer here is stripped down to prove it: drop what httpx would otherwise send and the origin
    sees nothing in their place."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    for fabricated in VIEWER_DEFAULT_HEADERS:
        del ingress.headers[fabricated]
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert got.json()["header_names"] == ["host", "x-dial-probe"]


async def test_a_bodyless_get_carries_no_body_framing(db, ingress) -> None:
    """A GET has no body, so it must reach the origin with neither `content-length` nor
    `transfer-encoding`. Attaching the request stream unconditionally frames every asset GET as
    chunked; an HTTP/1.1 keep-alive origin that never reads a GET body is then left with the
    terminating chunk in its buffer and desyncs the connection for the next request on it."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert got.json()["framing"] == []


async def test_percent_encoded_path_characters_reach_the_origin_intact(db, ingress) -> None:
    """`%3F` in a path names a literal `?` in a filename, not the start of a query — the decoded
    path is re-encoded rather than re-parsed as a URL, so it arrives as a path character and the
    request's own query stays the only query. The query comes off the ASGI scope for the same
    reason: `request.url` rebuilds a URL string from the *decoded* path, so a `?` in a filename
    would otherwise swallow the real query when that string is parsed back."""
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
    """The frame hands over its short-lived token once, in the URL; the ingress answers with a
    host-only `Secure`/`HttpOnly` cookie and sends the browser to `/`, so the token never reappears
    in a later request, a referer, or the site's own logs."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    token = _token(workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{INGRESS_VIEW_PATH}/{token}")
    assert got.status_code == 303
    assert got.headers["location"] == "/"
    cookie = got.headers["set-cookie"]
    assert cookie.startswith(f"{INGRESS_SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "Secure" in cookie and "domain" not in cookie.lower()
    assert "samesite=lax" in cookie.lower()


async def test_a_request_without_a_session_is_403_and_says_how_to_get_back_in(db, ingress) -> None:
    """The label is an address, not an authorization: knowing a site's origin gets a viewer nothing
    until the frame has traded a view token for that origin's session. A bookmark opened after the
    session ran out lands here too, so the body has to name the way back rather than leave a bare
    status code on the screen."""
    _workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 403
    assert got.text == SESSION_ENDED
    assert "chat" in got.text


async def test_a_session_cookie_cannot_mint_its_own_successor(db, ingress) -> None:
    """The renewal chain, measured and closed: the cookie the ingress binds is a session token, the
    view path takes only a view token, so replaying the cookie at `/~t/` mints nothing. Without the
    two kinds a single leaked link renewed itself an hour at a time, forever, and the only way to
    revoke it was rotating the deploy secret — which renames every site."""
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
    how long the link had been sitting in the frame — and that session's expiry is fixed at mint.
    Nothing extends it: the only path that mints a session refuses the session it would extend."""
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
    """The site is agent-authored code running in the sandbox. Handed `ufo_site`, it could read an
    hour of access to itself out of its own request log and keep it — so the crumb is cut out of
    every `cookie` line on the way upstream while the site's own crumbs pass through untouched, and
    a line holding nothing else is dropped rather than sent empty."""
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
    """The upstream client is cookie-blind. A client jar would fill from every origin it ever
    dialed and replay it: one process serves every workspace, and the local and docker carriers dial
    each sandbox as `127.0.0.1:<port>`, so the next site to answer on that authority would be handed
    the last one's cookies — and a viewer who sent no cookie would still see one arrive. This origin
    sets `a=1` and `b=2` on every response; nothing they set comes back to them, or to anyone."""
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
    # The other half: `Client.send` extracts `Set-Cookie` whatever built the request, so an
    # unpoliced jar would hold this origin's `a=1`/`b=2` and grow by every cookie every site ever
    # sets. Nothing is stored, so nothing accumulates for the life of the process.
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
    """Two labels are two origins. The browser already keeps a host-only cookie off every other
    host; the ingress refuses one replayed there anyway, so a shared or copied cookie buys nothing
    beyond the site it was bound for."""
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
        assert refused.text == SESSION_ENDED


def test_the_ingress_refuses_to_boot_without_a_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one piece `run()` reads off the knob, and its fail-loud. Unset, the ingress would resolve
    no request's site and answer 404 to every viewer, so it dies before its readiness probe reports
    green rather than serving nothing."""
    assert ingress_base_host("https://sites.example.test") == "sites.example.test"
    assert ingress_base_host("https://sites.example.test:8443") == "sites.example.test"
    for unusable in (None, ""):
        with pytest.raises(RuntimeError, match="ingress_public_url"):
            ingress_base_host(unusable)


async def test_a_host_naming_no_site_is_404(db, ingress) -> None:
    """A hostname outside the wildcard base, or a label under it this deploy's secret never signed,
    addresses nothing — answered before any token is read, and never confused with a site whose
    sandbox is down."""
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


async def test_cross_workspace_session_is_503(db, ingress) -> None:
    """A session's `workspace_id` and `conversation_id` must name the same row — the query's
    explicit `workspace_id` filter gates the read, not just the globally-unique conversation id, so
    a session claiming one workspace's id alongside another workspace's live conversation resolves
    to nothing rather than that other workspace's sandbox. The label cannot police it: it addresses
    the conversation, and the workspace rides the signed claims."""
    workspace_a, _conversation_a = await _seed_conversation("stub:sbx-1")
    _workspace_b, conversation_b = await _seed_conversation("stub:sbx-1")
    session = await _open(ingress, workspace_a, conversation_b)
    got = await ingress.get(
        f"{_origin(conversation_b)}/", headers={"cookie": f"{INGRESS_SESSION_COOKIE}={session}"}
    )
    assert got.status_code == 503


async def test_inbound_headers_cannot_override_dial_headers(db, ingress) -> None:
    """A mixed-case inbound `X-Dial-Probe` must not survive alongside the carrier's own
    (lowercase) dial header — the merge normalizes case so the dial's value always wins, on the
    wire as one header, never two."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/x", headers={"X-Dial-Probe": "stolen"})
    assert got.status_code == 200
    echoed = got.json()
    assert echoed["probe"] == "dialed"
    assert echoed["probe_count"] == 1


async def test_repeated_inbound_headers_reach_the_origin_intact(db, ingress) -> None:
    """The response path preserves repeats and so must the request path: a browser sending two
    `Cookie` lines, or a chained `X-Forwarded-For`, must arrive as it was sent. A dict keyed by
    header name silently kept only the last of each. Cutting our own crumb out of the first line
    must not disturb that — the site's cookies arrive on the two lines they were sent on."""
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
    """A site is agent-authored code, and `Set-Cookie: …; Domain=<parent>` was its one way to write
    outside its origin: the parent is not a public suffix, so a browser accepts the cookie and then
    sends it to the app host — session fixation reached from inside a member's own site. The
    attribute is stripped, so what the browser stores is host-only to this label; a cookie in ufo's
    own namespace is dropped whole, so a site can neither forge this origin's session nor plant one
    the app host would read. The site's ordinary cookies still arrive, still as repeats."""
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
    """`=ufo_site=FORGED` has an empty name where the guard reads one, and the reserved name once a
    browser has stored it: an empty-name cookie serializes into the `Cookie` header as its bare
    value, so the pair the guard refused arrives on the next request anyway — where this ingress
    reads `ufo_site` itself. Measured passing the prefix check before this; a nameless cookie is now
    dropped whole."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}{SMUGGLE_COOKIES_PATH}")
    assert got.status_code == 200
    assert got.headers.get_list("set-cookie") == ["a=1", "b=2"]


async def test_a_tls_target_is_dialed_over_https(db, origin_port, monkeypatch) -> None:
    """`DialTarget.tls` picks the scheme so no caller guesses it — but every other stub here dials
    plain loopback, so the `https` arm never ran and forcing the scheme to `http` left the suite
    green. This carrier reports TLS for an origin that speaks plain HTTP, so the dial can only
    succeed if the scheme came from `tls`: https to that port fails and the viewer gets the
    unreachable answer, where `http` would have served the page."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(_TlsCarrier(origin_port), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            answered = await client.get(f"{_origin(conversation_id)}/index.html")
    assert answered.status_code == 502
    assert answered.text == SITE_NOT_ANSWERING


async def test_the_bare_view_path_takes_no_token_from_the_query(db, ingress) -> None:
    """A route carrying no `{view_path}` placeholder makes FastAPI bind that parameter from the
    query string, so `/~t?view_path=<token>` would have been read as a view link and answered with a
    session. The bare path has its own handler, which takes nothing from the query and opens
    nothing."""
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
    """The view claim is a path segment, not a three-character prefix. Registering
    `f"{INGRESS_VIEW_PATH}{{view_path:path}}"` compiles to `^/~t(?P<view_path>.*)$`, which matches
    `/~theme.css` — so a site asset whose name happens to begin with those characters was refused as
    a malformed view token instead of being proxied. The boundary is what keeps the claim to the
    view path and what lives under it."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    for path in ("~theme.css", "~t-assets/app.js", "~tok"):
        served = await ingress.get(f"{_origin(conversation_id)}/{path}")
        assert served.status_code == 200, path
        assert served.json()["path"] == f"/{path}"


async def test_the_view_path_answers_only_get_and_head(db, ingress) -> None:
    """A route registered GET-only matches the path but not the method, and Starlette prefers a
    later route matching both — so the catch-all took `POST /~t/{token}` and forwarded the token to
    the sandbox as its request path, handing agent-authored code a credential good for fresh
    sessions until it expires. The path is claimed for every method the proxy serves and answers the
    two RFC 9110 pairs together: a server answering GET answers HEAD, so `Allow` names both and both
    get the redirect rather than a 405 contradicting its own header."""
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
    """The view path claims everything under itself, not one segment of it. A single-segment capture
    left every deeper path to the catch-all, which forwards its path to the origin — so
    `GET /~t/{token}/anything` handed agent-authored code a token good for fresh sessions until it
    expires. A deeper path is no longer proxied and is not a valid view link either, so it stops at
    the ingress; the bare view path, which carries no token, stops there too. What it does not claim
    is a *name* that merely starts the same way — see
    `test_a_site_path_merely_starting_with_the_view_prefix_is_served`."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    token = _token(workspace_id, conversation_id)
    origin = _origin(conversation_id)
    for suffix in (f"/{token}/extra", f"/{token}/a/b", f"/{token}/../assets/app.js", "/", ""):
        got = await ingress.get(f"{origin}{INGRESS_VIEW_PATH}{suffix}", follow_redirects=False)
        assert got.status_code == 403, suffix
        assert got.text == LINK_NOT_VALID
        assert "set-cookie" not in got.headers


async def test_the_origins_date_and_server_headers_are_not_relayed(db, ingress) -> None:
    """The ASGI server writes `date` and `server` on every response it sends, so relaying the
    origin's own puts two of each on the wire and RFC 9110 forbids a second `date`. The origin here
    sends both, as any real one does — `send_response` sets them — and neither is passed on."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert "date" not in got.headers
    assert "server" not in got.headers


async def test_the_origins_edge_headers_are_not_relayed(db, ingress) -> None:
    """A sandbox host answers through the provider's own edge, which adds these. `alt-svc`
    advertises that edge's alternative services against the ingress hostname, so a viewer told
    `h3=":443"` tries QUIC here for the month the header names; `via` hands the viewer our hop
    chain. Both are the origin's to say about itself, neither is true of this host, so neither is
    relayed."""
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    await _open(ingress, workspace_id, conversation_id)
    got = await ingress.get(f"{_origin(conversation_id)}/index.html")
    assert got.status_code == 200
    assert "alt-svc" not in got.headers
    assert "via" not in got.headers


async def test_no_shared_cache_may_store_a_proxied_response(db, ingress) -> None:
    """Authorization is a cookie checked per request, so a cache that stores a site's bytes and
    answers a later request from them answers it without the check. The sites hostname is proxied at
    the edge and a CDN's default rules commonly cache by extension — `/assets/*.js` is what a built
    site serves — so a hit would never reach this process and never see the 403. The directive is
    set here rather than asked of the origin, which is agent-authored code that usually sets none,
    and an origin asking for `public, max-age=…` cannot override it: its own are dropped."""
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
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
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


async def test_an_unreachable_origin_is_502(db, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dialable sandbox whose server is not listening is the site not answering, not the site
    being gone — a different status and a different sentence from the 503 branches."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, SECRET)
    workspace_id, conversation_id = await _seed_conversation("stub:sbx-1")
    async with upstream_client() as upstream:
        server = _server(_DeadPortCarrier(port=0), upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app())) as client:
            await _open(client, workspace_id, conversation_id)
            got = await client.get(f"{_origin(conversation_id)}/")
    assert got.status_code == 502
    assert got.text == SITE_NOT_ANSWERING


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
