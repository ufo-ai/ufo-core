"""Composition root for the sandbox ingress — the egress proxy's inbound twin: one process for
every workspace, serving each site at its own signed origin — a stored site's bytes straight from
the blob store, a dialed site's off its live sandbox port — owner DSN with explicit workspace
filters."""

import asyncio
import json
import mimetypes
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.requests import HTTPConnection
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol

from ufo.blob import (
    BlobNotFound,
    FilesystemBlobStore,
    FleetBlobStore,
    S3BlobStore,
    WorkspaceBlobStore,
    blob_store_for,
)
from ufo.config import load_config
from ufo.db import init_db, verify_db_reachable, workspace_tx
from ufo.harness.o11y import init_o11y, log, log_error, warn
from ufo.harness.sandbox.ingress_host import (
    SiteLabelError,
    parse_site_label,
    serve_port,
    shipped_anchor,
    shipped_app_slug,
    site_label,
)
from ufo.harness.sandbox.ingress_token import (
    INGRESS_SESSION_ENDED_MESSAGE,
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    IngressClaims,
    IngressTokenError,
    ShippedClaim,
    ingress_secret,
    mint_ingress_token,
    verify_ingress_token,
)
from ufo.harness.sandbox.select import select_carriers
from ufo.harness.sandbox.session import (
    Carrier,
    DialTarget,
    SandboxHandle,
    SandboxUnreachable,
    sandbox_handle_backend,
    sandbox_handle_id,
)
from ufo.harness.sandbox.site_report import SiteReporter
from ufo.host.ext.loader import load_manifests
from ufo.proxy_serve import OTLP_ENDPOINT_ENV, owner_dsn
from ufo.runtime.ext.manifest import CarrierSpec
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.http import cookie_secure, plain_local, same_origin_handshake, set_session_cookie

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
BODY_FRAMING_HEADERS = frozenset({"content-length", "transfer-encoding"})
CACHE_DIRECTIVE_HEADERS = frozenset(
    {
        "cache-control",
        "cdn-cache-control",
        "cloudflare-cdn-cache-control",
        "surrogate-control",
        "expires",
        "pragma",
    }
)
"""Every header an origin can cache with, dropped as a set rather than one by one. `cache-control`
is the one a browser reads; the CDN-targeted fields outrank it *at the edge*, which is the cache
that matters here — the sites wildcard is proxied, so an origin sending `cdn-cache-control: max-age`
would have the edge store an access-controlled site's bytes however `cache-control` was set."""
CONTENT_SECURITY_POLICY = "content-security-policy"
FRAME_ANCESTORS_DIRECTIVE = "frame-ancestors"
NO_FRAME_ANCESTOR = "'none'"
ORIGIN_RESPONSE_DROPPED_HEADERS = (
    HOP_BY_HOP_HEADERS
    | CACHE_DIRECTIVE_HEADERS
    | frozenset({"date", "server", "alt-svc", "via", "x-frame-options"})
)
"""What never comes back off the origin's response: the hop-by-hop set; the `date` and `server` the
ASGI server writes on every response it sends, since relaying the origin's own would put two of each
on the wire and RFC 9110 forbids a second `date`; `alt-svc`, which advertises another origin's
alternative services against this hostname, so a viewer would try that protocol here for the
lifetime the header names; `via`, which tells the viewer our own hop chain; every
`CACHE_DIRECTIVE_HEADERS` field, replaced by `UNCACHEABLE` rather than relayed; and
`x-frame-options`, the origin's veto over being framed.

A hosted site is read inside the frame, at the app origin, and that frame is the only page which
embeds one — so whether a site may be framed is core's answer and not the site's. The site is
agent-authored code, and an agent hardening its own server with a header it has every ordinary
reason to send would otherwise break the link its own deliverable is handed out as, leaving a
browser-generated refusal no reply can explain. `frame-ancestors` says the same thing inside
`CONTENT_SECURITY_POLICY` and is cut from it by `_unframed_policy`."""
UNCACHEABLE = "private, no-store"
"""The cache directive every proxied response carries, whatever the origin said.

Authorization here is a cookie checked per request, so any cache that stores a site's bytes and
answers a later request from them answers it without the check. The sites hostname is proxied at the
edge, and a CDN's default rules commonly cache by file extension — `/assets/*.js` is exactly what a
built site serves — so a hit would never reach this process and never see the 403. A site is
agent-authored code that typically sets no cache header at all, so this cannot be left to the
origin, and an origin that asks for `public, max-age=…` must not be able to override it: the
directive is set, and the origin's own is dropped."""
STORED_SITE_CACHE = "private, no-cache"
"""What a stored site's bytes carry instead of `UNCACHEABLE`. `private` keeps every shared cache
refused for `UNCACHEABLE`'s own reason — authorization is a cookie checked per request, and a
shared hit would answer without the check. `no-cache` rather than `no-store` because these bytes
have what a dialed site's never do: a digest. The viewer's own browser may hold a copy it must
revalidate on every use, the `etag` answer runs the same session gate as a full response — so a
held copy never outlives its authorization — and the digest moves on every redeploy, so the same
URL never serves retired bytes."""
SHIPPED_CACHE = "public, max-age=31536000, immutable"
"""What a shipped app's versioned document and Vite-hashed assets carry. The bytes are deploy-wide
code rather than workspace data, and a changed byte changes either the document's digest query or
the asset's `/assets/` name, so browsers and shared caches keep them without serving stale code."""
SHIPPED_VERSION_PARAM = "ufo-app"
STORED_SITE_METHODS = ("GET", "HEAD")
STORED_SITE_INDEX = "index.html"
WEBSOCKET_HANDSHAKE_HEADERS = frozenset(
    {
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
    }
)
"""The handshake's own fields, never forwarded. The upstream connection performs its own handshake,
so the client library writes `sec-websocket-key` and `sec-websocket-version` itself and negotiates
`sec-websocket-extensions` on its own terms; relaying the viewer's would describe a handshake that
never happened. `sec-websocket-protocol` travels as `subprotocols` instead, so forwarding the header
too would offer every subprotocol twice."""
WEBSOCKET_MAX_MESSAGE_BYTES = 8 * 1024 * 1024
"""The largest frame relayed in either direction. A site is agent-authored code and the viewer is
whoever opened it, so neither end's framing is ours to trust: without a bound, one message sizes
this process's memory. Generous next to a dev server's reload notices, small next to the pod.

The bound is only as good as the failure it produces, so permessage-deflate is off on both halves —
`compression=None` on the upstream connection, `ws_per_message_deflate=False` on the ASGI server,
whose own default is `True`.

Measured on the upstream half at this exact value: with deflate negotiated, a message one byte over
the bound arrives *clipped to exactly the bound* rather than raising, so a site's own protocol frame
would be silently short — JSON cut mid-object instead of a socket that ended. Uncompressed it raises
at every size tried, which is the failure the relay can act on. The viewer's half is turned off for
the same reason without the same evidence: an over-bound frame from a viewer is refused there with
deflate either on or off, so this is the two halves failing alike rather than a fix for an observed
clipping."""
WEBSOCKET_OPEN_TIMEOUT_SECONDS = 10.0
WEBSOCKET_CLOSE_UPSTREAM_GONE = 1011
WEBSOCKET_CLOSE_NORMAL = 1000
WEBSOCKET_UNSENDABLE_CLOSE_CODES = frozenset({1005, 1006, 1015})
"""Codes RFC 6455 reserves for a local observation and forbids on the wire — no status received, an
abnormal close with no frame at all, a failed TLS handshake. The site's close code is relayed so the
site's own client sees why it ended, and these three are what the relay must substitute for rather
than repeat."""
PATH_SAFE_CHARACTERS = "/:@!$&'()*+,;="
PROXY_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
VIEW_METHODS = ("GET", "HEAD")
"""What the view path answers, and what its `Allow` names — RFC 9110 obliges a server answering GET
to answer HEAD, so a `curl -I` of a site link, a link checker, or an unfurl probe gets the redirect
rather than a 405 whose own `Allow` contradicted it."""
UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0)
UPSTREAM_MAX_CONNECTIONS = 200
UPSTREAM_MAX_KEEPALIVE_CONNECTIONS = 50
INGRESS_BIND_HOST = "0.0.0.0"
RESERVED_COOKIE_PREFIX = "ufo_"
"""The namespace no site may set a cookie in. A relayed `Set-Cookie` under it would let a site
shadow this origin's own session, or plant one the app host reads — and the prefix, rather than a
list of names, is what a core module can hold: the app's cookie is named in an extension core must
not import, and a name added later needs no second edit here."""
INGRESS_SESSION_COOKIE = f"{RESERVED_COOKIE_PREFIX}site"
INGRESS_SESSION_TTL_SECONDS = 3600
NO_SITE_HERE = "No site is served at this address."
WRONG_SITE = "This link opens a different site."
LINK_NOT_VALID = "This link is expired or not valid. Open the site again to get a new one."
SESSION_ENDED_PAGE = (
    "<script>if(parent!==self){const tell=()=>parent.postMessage("
    f"{{ufo:'{INGRESS_SESSION_ENDED_MESSAGE}'}},'*');"
    "tell();setTimeout(tell,250);setTimeout(tell,1000)}</script>"
)
SITE_GONE = "This site is no longer hosted. Ask the agent that built it to put it back up."
SITE_NOT_ANSWERING = "This site is not answering."
SITE_WAITING_RELOAD_SECONDS = 30
FETCH_DESTINATION_HEADER = "sec-fetch-dest"
DOCUMENT_DESTINATIONS = frozenset({"document", "iframe", "frame"})
"""The `sec-fetch-dest` values a page is read at, and the only requests answered with one.

A site's own script, stylesheet or image asking for a page would render nothing and would report a
site the member never opened, so a sub-resource keeps the one sentence as text; one page load must
not report itself once per asset it names. A request carrying no Fetch Metadata is no browser
reading a page either. `embed` and `object` are left out: neither renders a hosted site anywhere in
the product."""
SITE_WAITING_PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{SITE_WAITING_RELOAD_SECONDS}">
<title>{SITE_NOT_ANSWERING.rstrip(".")}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-content: center;
    padding: 2rem; gap: 0.5rem; text-align: center;
    font: 14px/1.5 system-ui, -apple-system, sans-serif;
  }}
  p {{ margin: 0; }}
  .waiting {{ opacity: 0.6; }}
</style>
</head>
<body>
<p>{SITE_NOT_ANSWERING}</p>
<p class="waiting">The agent that built it has been asked to bring it back up.
This page reloads every {SITE_WAITING_RELOAD_SECONDS} seconds.</p>
</body>
</html>
"""
"""What a member reads in the frame while the site is down, and the whole of the recovery loop: the
meta refresh reloads this origin on a fixed interval, so the first reload after the server comes
back serves the site itself. Declarative rather than scripted, so it needs no script in a frame and
runs under a policy that admits none."""
SITE_WAITING_POLICY = "default-src 'none'; style-src 'unsafe-inline'"
"""What the waiting page may reach: nothing but the style in its own head. It is this process's own
document served at the site's origin, so it carries a policy of its own rather than the latitude a
site is served under."""
HEARTBEAT_PING_PATH = "/__ufo_heartbeat"
HEARTBEAT_SCRIPT_PATH = "/__ufo_heartbeat.js"
"""The two paths the viewer's heartbeat owns, reserved out of every site's own path space. Both are
claimed on every proxied method for the reason the view path is: whatever these routes do not claim
the catch-all forwards to the sandbox, and a site answering at either name would be asked to renew
its own lease."""
HEARTBEAT_PING_METHODS = ("POST",)
HEARTBEAT_SCRIPT_METHODS = ("GET", "HEAD")
HEARTBEAT_PING_SECONDS = 60
"""How often an open page pings while it is the tab in front. Short next to the lease a ping renews,
so a lease outlives several missed pings, and long enough that a page left open for an afternoon
costs one request a minute."""
HEARTBEAT_RENEWAL_SECONDS = 450
"""The floor between two carrier renewals for one site, whatever a site's viewers ping. Half the
span a dial leases on the e2b carrier (`DIAL_LEASE_SECONDS`, 900 seconds), which is the number this
cannot import from an extension: at half the span every renewal lands with the previous lease still
running, and a room full of tabs on one site costs the carrier one call rather than one per tab."""
HEARTBEAT_SCRIPT = f"""(function(){{
if(window.__ufoHeartbeat)return;
window.__ufoHeartbeat=true;
var ping=function(keepalive){{
  fetch('{HEARTBEAT_PING_PATH}',{{method:'POST',credentials:'same-origin',keepalive:keepalive}})
    .catch(function(){{}});
}};
var tick=function(){{if(document.visibilityState==='visible')ping(false);}};
tick();
setInterval(tick,{HEARTBEAT_PING_SECONDS * 1000});
document.addEventListener('visibilitychange',function(){{
  if(document.visibilityState!=='visible')ping(true);
}});
}})();
"""
"""What a dialed site's pages run: a ping now, a ping every interval, and one last ping the moment
the page stops being visible.

`document.visibilityState` is the whole gate, because the sandbox is paid for by the minute and a
backgrounded tab is nobody watching: a page left open in a window behind other windows renews
nothing, and the box pauses as it did before this existed. The ping on the way to hidden is what
makes the last renewal the last one — the server's own clock then runs out on its own rather than
being carried by a tab the member walked away from.

The window flag is the guard against a second copy: the tag is appended once per document by
`_body`, and a page that arrives with it twice — a document assembled from two proxied responses —
still holds one interval.

Carried as a file rather than inlined into the page: a site is agent-authored code and a site that
sets `script-src 'self'` is being ordinary, which admits this and forbids an inline script."""
HEARTBEAT_TAG = f'<script src="{HEARTBEAT_SCRIPT_PATH}" defer></script>'.encode()
HEARTBEAT_MEDIA_TYPE = "text/javascript"
IDENTITY_ENCODINGS = frozenset({"", "identity"})
FOREIGN_ORIGIN = "This connection did not come from the site it addresses."
NOT_FOUND = "Not found."
SITE_HAS_NO_SOCKET = "This site is static and speaks no socket protocol."
EDGE_REPLACED_STATUSES = frozenset({502, 504})
"""The statuses this origin cannot answer with, because they never reach the viewer.

The sites wildcard is proxied, and the edge discards an origin's 502 or 504 and answers its own
error page instead — under `x-frame-options: SAMEORIGIN`, which the frame that reads a site is not.
A site whose server has died would then be refused by the browser rather than told "This site is
not answering", leaving a refusal no reply can explain, exactly as a site sending that header
itself does. Measured against the live edge: a 503 carrying this module's own body arrives
verbatim, a 502 arrives as the edge's page.

An upstream answering inside this set is answered for, which is also the only reading a dialed
sandbox admits: the carrier's edge answers a port nothing listens on with its own error naming its
own sandbox, so those bytes are never the site's response to relay.

The mask is unconditional — every request, and every deploy, whether an edge stands in front or
not. A site proxying onward and answering its own 504, or a `fetch` for an API path under it, reads
back this module's sentence rather than the origin's own bytes, and a bare `ufoctl ingress` with no
edge discards bytes that would have arrived. That is the price of one answer: a status that depended
on what was deployed in front of this process would make a site's failure mean two different things,
and the one it means behind the proxy is the one members meet."""


@dataclass(frozen=True)
class SiteRefusal:
    """Why a connection reaches no site: an HTTP status and response body. The proxy answers with
    it and the socket denies its handshake with it, so one gate produces both refusals — the same
    status and body either way — and neither protocol can admit what the other turns away."""

    status: int
    message: str
    media_type: str = "text/plain"


HOSTED_SITE = sa.table(
    "hosted_site",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("port", sa.Integer()),
    sa.column("source_manifest", sa.Text()),
)
"""The sites extension's registry, read by name the way extension tables are read elsewhere in
core, always under an explicit workspace filter because this process runs on the owner DSN.
`source_manifest` is the serving mode: a row carrying one is a stored site whose bytes come from
the blob store, a row without one — and a label with no row at all — is dialed."""


@dataclass(frozen=True)
class StoredFile:
    """One file of a stored site: the workspace-relative blob key its bytes live under, the size
    and digest its deploy measured, and the media type its responses are typed by."""

    key: str
    size_bytes: int
    media_type: str
    sha256: str


@dataclass(frozen=True)
class IngressServe:
    """Read which site a request addresses off its own Host, authorize the viewer against that
    origin's session cookie, and serve the site the way its row says it is served: a stored site's
    bytes stream from the blob store with no sandbox in the path, a dialed site's port is resolved
    through the carrier and the exchange streamed both ways — never buffering a body whole.

    A site owning its origin is what makes the label load-bearing rather than cosmetic: the
    conversation and port come from the hostname, so the site's `/`-rooted assets and redirects
    address it unchanged, and the browser keeps its cookies and storage away from every other
    site."""

    backend: str
    base_host: str
    carrier: Carrier
    client: httpx.AsyncClient
    blob: FilesystemBlobStore | S3BlobStore
    """The deploy's blob backend, wrapped per request into the addressed workspace's own store —
    a stored site's bytes are read from here and its sandbox is never dialed."""
    frame_ancestor: str
    """The one deploy-wide source expression a hosted site may be framed by — the app origin, where
    the portal's homepage iframe and the ordinary site frame live — or `'none'` when no app base is
    configured. Framing is core's invariant rather than an accident of what each agent's server
    emitted: `_open` binds the session `SameSite=Lax`, so a cross-site framer already gets no cookie
    and lands on 403, but every site label shares one registrable domain — so without this, site A
    frames site B and the viewer's session cookie for B rides along.

    A view opened inside a sibling site carries that one site's signed address. `_open` proves the
    sibling belongs to the same workspace before binding it into the session, and responses name
    its exact origin beside the app origin. The policy is therefore fixed-size while a site in
    another workspace still cannot wrap this one around the viewer's session.

    Carried as our own header rather than appended to the origin's: a site commonly sends no policy
    at all, which is the case this exists for, and several policies combine restrictively — so one
    header of ours binds whatever the site said, and says it exactly once however many the site
    sent."""
    site_scheme: str
    site_port_suffix: str
    """The scheme and rendered `:port` (or empty) of `[sandbox] ingress_public_url` — with
    `base_host`, what `_frame_ancestors` renders a sibling site's origin from."""
    reporter: SiteReporter
    """The hop that tells a site's own conversation the site is down. This process runs no turn
    engine, so a dead site is reported to serve rather than acted on here, and only for a request
    reading a page — the member's frame is what makes it worth an agent's turn."""
    apps_dev_server: DialTarget | None = None
    """Where a shipped app page is relayed from in place of the fleet store — `[sandbox]
    apps_dev_server`, a dev server holding the pages from source with each at `/<slug>/`. The local
    stack's edit loop; a deploy leaves it unset and serves the published bundle."""
    shipped_manifests: dict[ShippedClaim, dict[str, StoredFile]] = field(default_factory=dict)
    renewals: dict[tuple[UUID, int], float] = field(default_factory=dict)
    """When each dialed site last had its lease renewed by a viewer's ping, keyed by conversation
    and port — what `_renew_lease` throttles on."""
    resume_carriers: Mapping[str, tuple[Carrier, CarrierSpec]] = field(default_factory=dict)
    """Backends kept live only for the stored handles bearing their scheme (`[sandbox]
    resume_backends`): a site whose conversation still runs on a prior provider dials through that
    provider's carrier."""

    def app(self) -> FastAPI:
        """The view path and everything under it is the ingress's, on every method the proxy serves
        — `PROXY_METHODS` rather than the GET and HEAD it answers, because anything these routes
        do not claim the catch-all does, and the catch-all forwards its path to the sandbox: `POST
        /~t/{token}` (a route matching the path but not the method loses to a later route matching
        both) and `GET /~t/{token}/anything` (one segment captured, the rest unmatched) each handed
        the token to agent-authored code in a request path, a credential good for fresh sessions
        until it expires.

        Claimed as two routes on the segment boundary, never as a prefix: one `:path` capture
        appended straight to `INGRESS_VIEW_PATH` compiles to `^/~t(?P<view_path>.*)$`, which claims
        every site path merely *starting* with those three characters, so `/~theme.css` answered 403
        instead of being served. The boundary is what keeps the claim to the view path without
        taking a name that only resembles it."""
        application = FastAPI(openapi_url=None)
        # The bare path routes to its own handler: a route carrying no `{view_path}` placeholder
        # makes FastAPI bind that parameter from the query string.
        application.add_api_route(INGRESS_VIEW_PATH, self._no_view_token, methods=PROXY_METHODS)
        application.add_api_route(
            f"{INGRESS_VIEW_PATH}/{{view_path:path}}", self._open, methods=PROXY_METHODS
        )
        application.add_api_route(HEARTBEAT_PING_PATH, self._ping, methods=PROXY_METHODS)
        application.add_api_route(
            HEARTBEAT_SCRIPT_PATH, self._heartbeat_script, methods=PROXY_METHODS
        )
        application.add_api_route("/{path:path}", self._proxy, methods=PROXY_METHODS)
        # A WebSocket handshake matches no HTTP route, so `/~t/{token}` over WebSocket would
        # otherwise reach the catch-all and be forwarded as a request path.
        application.add_api_websocket_route(INGRESS_VIEW_PATH, self._no_socket_view)
        application.add_api_websocket_route(
            f"{INGRESS_VIEW_PATH}/{{view_path:path}}", self._no_socket_view
        )
        application.add_api_websocket_route("/{path:path}", self._socket)
        return application

    async def _no_view_token(self, request: Request) -> Response:
        """The view path with nothing under it carries no token, so it opens nothing. Its own
        handler rather than `_open` with a default, because a route without the placeholder would
        bind `view_path` off the query string and read `/~t?view_path=<token>` as a link."""
        if request.method not in VIEW_METHODS:
            return Response(status_code=405, headers={"allow": ", ".join(VIEW_METHODS)})
        return Response(LINK_NOT_VALID, status_code=403, media_type="text/plain")

    async def _ping(self, request: Request) -> Response:
        """One viewer saying the page is still open in front of them, and the one reason this
        process ever renews a lease nobody's request is renewing: a dialed site's box pauses after
        its idle span, and a loaded page that asks for nothing more would let it pause under a
        member reading it. The gate is `_authorized`, exactly as the proxy's, so a ping carries the
        session the page was loaded with and nothing else — no second credential, and no way to
        renew a site this cookie does not already open.

        A stored site answers the same 204 having renewed nothing: its bytes come off the blob
        store and there is no sandbox behind it to keep awake. A dial that fails renews nothing
        either and is not reported here — the member's own page load is the request that tells the
        conversation its site is down."""
        if request.method not in HEARTBEAT_PING_METHODS:
            return Response(status_code=405, headers={"allow": ", ".join(HEARTBEAT_PING_METHODS)})
        authorized = self._authorized(request)
        if isinstance(authorized, SiteRefusal):
            return Response(
                authorized.message,
                status_code=authorized.status,
                media_type=authorized.media_type,
            )
        if authorized.shipped is not None or await self._stored_manifest(authorized) is not None:
            return Response(status_code=204)
        await self._renew_lease(authorized)
        return Response(status_code=204)

    async def _heartbeat_script(self, request: Request) -> Response:
        """The heartbeat itself, served at the site's own origin so a site's own
        `script-src 'self'` admits it. It is this process's code rather than the site's, so it is
        served here whatever the addressed site is and whoever asks: it names one path of this
        origin and reads nothing."""
        if request.method not in HEARTBEAT_SCRIPT_METHODS:
            return Response(status_code=405, headers={"allow": ", ".join(HEARTBEAT_SCRIPT_METHODS)})
        return Response(
            b"" if request.method == "HEAD" else HEARTBEAT_SCRIPT,
            media_type=HEARTBEAT_MEDIA_TYPE,
            headers={"cache-control": UNCACHEABLE, "x-content-type-options": "nosniff"},
        )

    async def _renew_lease(self, claims: IngressClaims) -> None:
        """Renew the addressed sandbox's lease through the carrier, at most once every
        `HEARTBEAT_RENEWAL_SECONDS` per site. A dial is the renewal: every inbound carrier call
        leases the box forward, and dial is the cheapest one the ingress already makes, so this
        asks for an address it throws away.

        `renewals` is this process's memory of when each site last had one, held in memory because
        the ingress runs as one process per deploy — a second replica would renew a site twice per
        window, which costs one extra carrier call and keeps the same box awake. The time is
        recorded before the dial so a burst of tabs collapses into one call rather than all of them
        racing past an unwritten entry, and dropped again when the dial refused, so a site that
        comes back is renewed by the next ping instead of waiting out the window."""
        key = (claims.conversation_id, claims.port)
        now = datetime.now(UTC).timestamp()
        renewed = self.renewals.get(key)
        if renewed is not None and now - renewed < HEARTBEAT_RENEWAL_SECONDS:
            return
        self.renewals[key] = now
        if isinstance(await self._dial_site(claims), SiteRefusal):
            self.renewals.pop(key, None)

    async def _open(self, request: Request, view_path: str) -> Response:
        """Trade the frame's view token for this origin's session. Only a *view* token opens this
        path, so a session cookie replayed here mints no successor and the chain of sessions ends at
        the one the frame's link started; the token must also claim the very site whose URL it
        arrived at, so one minted for a member's site never opens another's. The cookie it binds is
        host-only, carries the same claims under the session kind, and runs its own TTL from this
        moment.

        `lax` because arrival is a navigation in from the app host — a different origin, but one
        registrable domain, so the cookie counts as same-site and is sent both on the way in and on
        every request the embedded site makes afterwards. `none` is what a frame across two
        registrable domains would need, and is not what this is.

        Whatever follows the token is where the bound session lands, so a frame can open a site at
        one of its own paths rather than only at its root — the site's paths are reachable no other
        way from outside. The boundary is the first `/` after the token, which is unambiguous
        because `sign_token` emits `base64url.base64url` and neither half can hold one. The entry
        path is not signed: it grants nothing the session does not, since the catch-all proxies
        every path once the cookie is bound."""
        if request.method not in VIEW_METHODS:
            return Response(status_code=405, headers={"allow": ", ".join(VIEW_METHODS)})
        view_token, _, entry_path = view_path.partition("/")
        site = self._site(request)
        if site is None:
            return Response(NO_SITE_HERE, status_code=404, media_type="text/plain")
        now = datetime.now(UTC)
        try:
            claims = verify_ingress_token(view_token, now, INGRESS_VIEW_KIND)
        except IngressTokenError:
            return Response(LINK_NOT_VALID, status_code=403, media_type="text/plain")
        if (claims.conversation_id, claims.port) != site:
            return Response(WRONG_SITE, status_code=403, media_type="text/plain")
        if claims.framer is not None and not await self._framer_belongs(
            claims.workspace_id, claims.framer.conversation_id, claims.framer.port
        ):
            return Response(LINK_NOT_VALID, status_code=403, media_type="text/plain")
        if entry_path.startswith("/"):
            return Response(LINK_NOT_VALID, status_code=403, media_type="text/plain")
        session = mint_ingress_token(
            replace(claims, expires_at=int(now.timestamp()) + INGRESS_SESSION_TTL_SECONDS),
            INGRESS_SESSION_KIND,
        )
        target = f"/{quote(entry_path, safe=PATH_SAFE_CHARACTERS)}"
        if claims.shipped is not None:
            target = f"{target}?{SHIPPED_VERSION_PARAM}={claims.shipped.digest}"
        response = RedirectResponse(target, status_code=303)
        set_session_cookie(
            response,
            INGRESS_SESSION_COOKIE,
            session,
            samesite="lax",
            secure=cookie_secure(self.site_scheme),
        )
        return response

    def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None:
        """The conversation and port this request's own origin addresses, or None when its Host
        names no site of this deploy's — a hostname outside the wildcard base, or a label this
        deploy's secret never signed."""
        host = request.url.hostname or ""
        suffix = f".{self.base_host}"
        if not host.endswith(suffix):
            return None
        try:
            return parse_site_label(host.removesuffix(suffix))
        except SiteLabelError:
            return None

    def _authorized(self, connection: HTTPConnection) -> IngressClaims | SiteRefusal:
        """The gate in front of a site, shared by both protocols and both serving modes: which
        site this connection's Host addresses, and whether its session cookie authorizes that very
        site. A socket that authorized differently from the proxy would be a second door into the
        same bytes, and a stored site that authorized differently from a dialed one would be the
        same door wedged open — so the answer is computed once, here, and what serves an
        authorized viewer is decided after. It takes an `HTTPConnection` rather than a `Request`
        because a WebSocket handshake carries the same Host, cookies, and claims, and is gated by
        this same code rather than by a copy of it."""
        site = self._site(connection)
        if site is None:
            return SiteRefusal(404, NO_SITE_HERE)
        try:
            claims = verify_ingress_token(
                connection.cookies.get(INGRESS_SESSION_COOKIE, ""),
                datetime.now(UTC),
                INGRESS_SESSION_KIND,
            )
        except IngressTokenError:
            return SiteRefusal(403, SESSION_ENDED_PAGE, "text/html")
        if (claims.conversation_id, claims.port) != site:
            return SiteRefusal(403, WRONG_SITE)
        return claims

    async def _stored_manifest(self, claims: IngressClaims) -> dict[str, StoredFile] | None:
        """The stored site the claims address, as its manifest's files keyed by site path — or
        None where the row is absent or carries no manifest, which is the dial path. Read per
        request rather than cached: the portal remounts a homepage frame the moment a redeploy
        bumps its row, so a cached manifest would keep answering the retired bytes at the same URL
        for its whole TTL. The row read costs what the dial path's own conversation read costs. A
        manifest that does not parse is this deploy's own write gone wrong and raises rather than
        serving something else.

        A `shipped` claim redirects the read to a deploy-wide app bundle in the fleet store: no
        conversation row exists for a shipped page, so the files are enumerated from the fleet tree
        under `apps/<digest>/` instead of the `hosted_site` row. The bytes are the same for every
        workspace — the session cookie still scoped the workspace claim, and the digest names
        immutable content, so serving them row-less leaks nothing a workspace owns."""
        if claims.shipped is not None:
            return await self._shipped_manifest(claims.shipped)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(HOSTED_SITE.c.source_manifest).where(
                        HOSTED_SITE.c.workspace_id == claims.workspace_id,
                        HOSTED_SITE.c.conversation_id == claims.conversation_id,
                        HOSTED_SITE.c.port == claims.port,
                    )
                )
            ).one_or_none()
        if row is None or row.source_manifest is None:
            return None
        manifest = json.loads(row.source_manifest)
        root = manifest["root"]
        return {
            path: StoredFile(
                key=f"{root}{path}",
                size_bytes=entry["size"],
                media_type=entry["media_type"],
                sha256=entry["sha256"],
            )
            for path, entry in manifest["files"].items()
        }

    async def _shipped_manifest(self, shipped: ShippedClaim) -> dict[str, StoredFile] | None:
        """The built app pages a shipped claim names, from the fleet store: every file of the
        deploy-wide apps tree under `apps/<digest>/`, keyed by the request path that reaches it. A
        file inside the claim's own slug subdir answers a root-relative request — so
        `apps/<digest>/<slug>/index.html` is `/` — while a file outside it answers at its own path,
        so `apps/<digest>/assets/<chunk>.js` is `/assets/<chunk>.js` and the one set of hashed
        chunks the pages share serves every app. None when the digest names no published tree
        — a race against a redeploy that retired it — answering 404 for the refresh to heal.

        The etag is the digest itself: the tree is content-addressed, so a byte change anywhere is a
        new digest carried in a new token, and a held copy of any file revalidates against it.
        Enumerated by one bounded `list`, not a published manifest — the digest supplies the etag a
        bare listing could not, and the sizes and media types are all the listing and the filenames
        already carry. The keys stay fleet keys, read in `_serve_stored` from the fleet store."""
        held = self.shipped_manifests.get(shipped)
        if held is not None:
            return held
        prefix = f"apps/{shipped.digest}/"
        entries = await FleetBlobStore(backend=self.blob).list(prefix)
        if not entries:
            return None
        slug_prefix = f"{shipped.slug}/"
        manifest = {
            entry.key.removeprefix(prefix).removeprefix(slug_prefix): StoredFile(
                key=entry.key,
                size_bytes=entry.size_bytes,
                media_type=mimetypes.guess_type(entry.key)[0] or "application/octet-stream",
                sha256=shipped.digest,
            )
            for entry in entries
        }
        self.shipped_manifests[shipped] = manifest
        return manifest

    async def _dial_site(self, claims: IngressClaims) -> DialTarget | SiteRefusal:
        """The live target the addressed port is reachable at, for a site whose bytes are served
        by the conversation's own sandbox."""
        with ws(claims.workspace_id):
            stored = await self._stored_handle(claims.workspace_id, claims.conversation_id)
            carrier, backend = self.carrier, self.backend
            if stored is not None:
                resumed = self.resume_carriers.get(sandbox_handle_backend(stored))
                if resumed is not None:
                    carrier, backend = resumed[0], resumed[1].name
            container_id = None if stored is None else sandbox_handle_id(backend, stored)
            if not container_id:
                warn(
                    "ingress.no_container",
                    conversation_id=str(claims.conversation_id),
                    stored_handle=stored,
                    backend=backend,
                )
                return SiteRefusal(503, SITE_GONE)
            handle = SandboxHandle(
                conversation_id=claims.conversation_id, container_id=container_id
            )
            try:
                return await carrier.dial(handle, claims.port)
            except SandboxUnreachable as error:
                warn(
                    "ingress.dial_failed",
                    conversation_id=str(claims.conversation_id),
                    error=repr(error),
                )
                return SiteRefusal(503, SITE_GONE)

    async def _proxy(self, request: Request, path: str) -> Response:
        authorized = self._authorized(request)
        if isinstance(authorized, SiteRefusal):
            return Response(
                authorized.message,
                status_code=authorized.status,
                media_type=authorized.media_type,
            )
        if authorized.shipped is not None and self.apps_dev_server is not None:
            return await self._relay_request(
                request, authorized, self.apps_dev_server, path or f"{authorized.shipped.slug}/"
            )
        files = await self._stored_manifest(authorized)
        if files is not None:
            return await self._serve_stored(request, authorized, files, path)
        if authorized.shipped is not None:
            return Response(NOT_FOUND, status_code=404, media_type="text/plain")
        dialed = await self._dial_site(authorized)
        if isinstance(dialed, SiteRefusal):
            return Response(dialed.message, status_code=dialed.status, media_type=dialed.media_type)
        return await self._relay_request(request, authorized, dialed, path)

    async def _relay_request(
        self, request: Request, authorized: IngressClaims, dialed: DialTarget, path: str
    ) -> Response:
        scheme = "https" if dialed.tls else "http"
        url = self._upstream_url(scheme, dialed.host, path, request.scope["query_string"])
        framed = any(header in request.headers for header in BODY_FRAMING_HEADERS)
        upstream_request = httpx.Request(
            request.method,
            url,
            headers=self._upstream_headers(request, dialed.headers),
            content=request.stream() if framed else None,
        )
        with ws(authorized.workspace_id):
            try:
                upstream = await self.client.send(upstream_request, stream=True)
            except httpx.HTTPError as error:
                log_error(
                    "ingress.upstream_failed",
                    conversation_id=str(authorized.conversation_id),
                    error=repr(error),
                )
                return self._not_answering(request, authorized)
            if upstream.status_code in EDGE_REPLACED_STATUSES:
                await upstream.aclose()
                warn(
                    "ingress.upstream_not_answering",
                    conversation_id=str(authorized.conversation_id),
                    http_status=upstream.status_code,
                )
                return self._not_answering(request, authorized)
            tagged = self._heartbeat_tagged(request, upstream)
            try:
                response = StreamingResponse(
                    self._body(upstream, HEARTBEAT_TAG if tagged else b""),
                    status_code=upstream.status_code,
                    background=BackgroundTask(upstream.aclose),
                )
                response.headers["cache-control"] = UNCACHEABLE
                ancestors = self._frame_ancestors(authorized)
                response.headers[CONTENT_SECURITY_POLICY] = (
                    f"{FRAME_ANCESTORS_DIRECTIVE} {ancestors}"
                )
                for name, value in upstream.headers.multi_items():
                    lowered = name.lower()
                    if lowered in ORIGIN_RESPONSE_DROPPED_HEADERS:
                        continue
                    if tagged and lowered in BODY_FRAMING_HEADERS:
                        continue
                    if lowered == CONTENT_SECURITY_POLICY:
                        policy = self._unframed_policy(value)
                        if policy:
                            response.headers.append(name, policy)
                        continue
                    if lowered == "set-cookie":
                        confined = self._confined_cookie(value)
                        if confined is not None:
                            response.headers.append(name, confined)
                        continue
                    response.headers.append(name, value)
            except Exception:
                await upstream.aclose()
                raise
        return response

    def _heartbeat_tagged(self, request: Request, upstream: httpx.Response) -> bool:
        """Whether this dialed response carries the heartbeat tag: an HTML document, in bytes this
        process can add to. The tag goes into the pages of a live site rather than into the waiting
        page a stopped one answers with, because keeping a healthy site awake under a reader is the
        whole job — the waiting page already reloads, and its reload renews on its own.

        A coded body is left alone: appending plain bytes to a gzip stream is a truncated document,
        and the origin's encoding is the viewer's own negotiation relayed untouched. A HEAD carries
        no body to append to. Nothing else about the response is read, so a site's own bytes are
        never parsed or buffered to find a place for the tag."""
        if request.method == "HEAD":
            return False
        if upstream.headers.get("content-encoding", "").strip().lower() not in IDENTITY_ENCODINGS:
            return False
        media_type = upstream.headers.get("content-type", "").partition(";")[0].strip().lower()
        return media_type == "text/html"

    def _not_answering(self, request: HTTPConnection, claims: IngressClaims) -> Response:
        """The site did not answer, said by this origin under a status the edge delivers. It stands
        in the served page's place, so it is framed by the same origins the page would have been:
        the member reads it inside the frame they opened, where the upstream's own answer would
        have reached them as the edge's error page or as the carrier's JSON.

        A request reading a page is answered with the waiting page, and reports the site to the
        conversation that owns it — as a background task, so the frame paints on this process's own
        work and never waits on a hop to serve. Every other request is answered with the sentence
        as text and reports nothing."""
        ancestors = f"{FRAME_ANCESTORS_DIRECTIVE} {self._frame_ancestors(claims)}"
        if request.headers.get(FETCH_DESTINATION_HEADER, "") not in DOCUMENT_DESTINATIONS:
            return Response(
                SITE_NOT_ANSWERING,
                status_code=503,
                media_type="text/plain",
                headers={"cache-control": UNCACHEABLE, CONTENT_SECURITY_POLICY: ancestors},
            )
        return Response(
            SITE_WAITING_PAGE,
            status_code=503,
            media_type="text/html",
            headers={
                "cache-control": UNCACHEABLE,
                CONTENT_SECURITY_POLICY: f"{SITE_WAITING_POLICY}; {ancestors}",
            },
            background=BackgroundTask(self.reporter.report, claims),
        )

    async def _serve_stored(
        self, request: Request, claims: IngressClaims, files: dict[str, StoredFile], path: str
    ) -> Response:
        """One file of a stored site, straight from the blob store — no sandbox dial, so the site
        answers whatever became of the sandbox that deployed it. The deploy promoted the site's
        directory into the store and wrote the manifest onto the row, so the manifest is the
        site's whole filesystem: a request path either names one of its files — or the index under
        it — or nothing. The lookup is a dict membership, never a path resolved against a root, so
        a traversal has nothing to traverse: `../anything` is a string no manifest key equals, and
        it misses. GET and HEAD are the whole protocol a directory of files speaks; anything else
        is refused rather than forwarded, because there is nothing behind this to forward to.

        A stored workspace site's 304 is exactly as authorized as its 200 because the session gate
        has already run, so its private browser copy revalidates through that gate. A deploy-wide
        app's versioned document and hashed assets are public immutable code; only its data calls
        cross the portal's authenticated bridge. A blob the manifest names but the store no longer
        holds is the window where a redeploy retired the root between the row read and this read,
        and it answers 404 for the refresh to heal rather than erroring mid-stream."""
        if request.method not in STORED_SITE_METHODS:
            return Response(status_code=405, headers={"allow": ", ".join(STORED_SITE_METHODS)})
        trimmed = path.strip("/")
        candidates = (
            (STORED_SITE_INDEX,) if not trimmed else (trimmed, f"{trimmed}/{STORED_SITE_INDEX}")
        )
        located = next(((name, files[name]) for name in candidates if name in files), None)
        if located is None:
            return Response(NOT_FOUND, status_code=404, media_type="text/plain")
        name, stored = located
        ancestors = self._frame_ancestors(claims)
        etag = f'"{stored.sha256}"'
        headers = {
            "cache-control": (
                SHIPPED_CACHE
                if claims.shipped is not None
                and (
                    name.startswith("assets/")
                    or request.query_params.get(SHIPPED_VERSION_PARAM) == claims.shipped.digest
                )
                else STORED_SITE_CACHE
            ),
            "etag": etag,
            "x-content-type-options": "nosniff",
            CONTENT_SECURITY_POLICY: f"{FRAME_ANCESTORS_DIRECTIVE} {ancestors}",
        }
        held = request.headers.get("if-none-match", "")
        if etag in {mark.strip() for mark in held.split(",")}:
            return Response(status_code=304, headers=headers)
        headers["content-length"] = str(stored.size_bytes)
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type=stored.media_type)
        if claims.shipped is not None:
            chunks = FleetBlobStore(backend=self.blob).get_stream(stored.key)
        else:
            with ws(claims.workspace_id):
                chunks = WorkspaceBlobStore(backend=self.blob).get_stream(stored.key)
        try:
            first = await anext(chunks)
        except StopAsyncIteration:
            return Response(b"", status_code=200, headers=headers, media_type=stored.media_type)
        except BlobNotFound:
            return Response(NOT_FOUND, status_code=404, media_type="text/plain")
        return StreamingResponse(
            self._stored_body(first, chunks),
            status_code=200,
            headers=headers,
            media_type=stored.media_type,
        )

    async def _stored_body(self, first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """The stored bytes, with the first chunk already read: pulling it before the response is
        built is what turns a vanished blob into a clean 404 instead of a stream that dies after
        the 200 went out."""
        yield first
        async for chunk in rest:
            yield chunk

    async def _framer_belongs(self, workspace_id: UUID, conversation_id: UUID, port: int) -> bool:
        async with workspace_tx() as connection:
            site = (
                await connection.execute(
                    sa.select(HOSTED_SITE.c.conversation_id)
                    .where(
                        HOSTED_SITE.c.workspace_id == workspace_id,
                        HOSTED_SITE.c.conversation_id == conversation_id,
                        HOSTED_SITE.c.port == port,
                    )
                    .limit(1)
                )
            ).one_or_none()
            if site is not None:
                return True
            provisions = (
                (
                    await connection.execute(
                        sa.select(tables.agent.c.provisioned_by)
                        .distinct()
                        .where(
                            tables.agent.c.workspace_id == workspace_id,
                            tables.agent.c.archived_at.is_(None),
                            tables.agent.c.provisioned_by.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return any(
            (anchor := shipped_anchor(workspace_id, slug)) == conversation_id
            and serve_port(anchor) == port
            for provision in provisions
            if (slug := shipped_app_slug(provision)) is not None
        )

    def _frame_ancestors(self, claims: IngressClaims) -> str:
        if self.frame_ancestor == NO_FRAME_ANCESTOR or claims.framer is None:
            return self.frame_ancestor
        framer = claims.framer
        origin = (
            f"{self.site_scheme}://{site_label(framer.conversation_id, framer.port)}"
            f".{self.base_host}{self.site_port_suffix}"
        )
        return f"{self.frame_ancestor} {origin}"

    def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str:
        url = f"{scheme}://{host}/{quote(path, safe=PATH_SAFE_CHARACTERS)}"
        query = query_string.decode()
        return f"{url}?{query}" if query else url

    async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.sandbox_handle).where(
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.workspace_id == workspace_id,
                    )
                )
            ).one_or_none()
        return None if row is None else row.sandbox_handle

    def _upstream_headers(
        self, request: HTTPConnection, dial_headers: Mapping[str, str]
    ) -> list[tuple[str, str]]:
        """Every header the origin sees, and the only ones it sees. The viewer's own, minus the
        hop-by-hop set, `host`, the handshake's own fields, and any name the dial supplies, with the
        dial's own appended so its value is the one on the wire. Repeats arrive as repeats.

        This is the whole list because the request is built as a bare `httpx.Request`: a client's
        `build_request` merges its own defaults under it, which would put an `accept`, a
        `user-agent`, a `connection`, and — the one with teeth — an `accept-encoding: gzip, deflate`
        on the wire the viewer never sent. `_body` relays raw bytes, so an origin honouring that
        fabricated negotiation would return gzip to a viewer who never asked for it.

        `ufo_site` is cut out of every `cookie` line, and the line dropped when it held nothing
        else. The site is agent-authored code running in the sandbox: handing it our session cookie
        would let it read an hour of access to itself out of its own request log and keep it. The
        site's own cookies pass through untouched — they are the site's."""
        dialed = {name.lower() for name in dial_headers}
        forwarded = []
        for name, value in request.headers.items():
            lowered = name.lower()
            if lowered in HOP_BY_HOP_HEADERS or lowered == "host" or lowered in dialed:
                continue
            if lowered in WEBSOCKET_HANDSHAKE_HEADERS:
                continue
            if lowered == "cookie":
                value = "; ".join(
                    crumb
                    for crumb in (part.strip() for part in value.split(";"))
                    if crumb and crumb.partition("=")[0].strip() != INGRESS_SESSION_COOKIE
                )
                if not value:
                    continue
            forwarded.append((lowered, value))
        return forwarded + [(name.lower(), value) for name, value in dial_headers.items()]

    def _unframed_policy(self, policy: str) -> str:
        """One relayed `Content-Security-Policy` minus the origin's say over who may frame it, or
        empty to drop the header. The directive is cut out rather than the header dropped whole,
        because the rest of the policy is the site's own defence against the scripts it loads and
        solving framing must not take that with it. An empty policy is not a permissive one — a
        browser reads it as allowing nothing — so nothing is relayed when `frame-ancestors` was all
        the policy said. Repeats are rewritten one by one: several policies combine restrictively,
        so a `frame-ancestors` left in any one of them still refuses the frame."""
        return "; ".join(
            directive
            for directive in (part.strip() for part in policy.split(";"))
            if directive and directive.split()[0].lower() != FRAME_ANCESTORS_DIRECTIVE
        )

    def _confined_cookie(self, header: str) -> str | None:
        """One relayed `Set-Cookie`, confined to the origin that sent it, or None to drop it.

        A site is agent-authored code, and a cookie is the one thing it can write that outlives its
        own origin. `Domain=` is the escape: a site answering `Set-Cookie: s=x; Domain=<parent>`
        gets that cookie accepted for every host under the parent — the app host included — which is
        session fixation reached from inside a member's own site. The attribute is dropped rather
        than the cookie, since the cookie itself is the site's to set; without it the browser scopes
        it host-only to this label, which is what a per-site origin is for. A name in ufo's own
        namespace is dropped whole: a site may not set this origin's session, nor one the app host
        would read.

        A nameless cookie goes with them, because `=ufo_site=FORGED` reads as an empty name here and
        as the reserved name once a browser has stored it and sent it back: an empty-name cookie
        serializes into the `Cookie` header as its bare value, so the pair we refused to let a site
        set arrives on the next request anyway. Everything else is relayed verbatim, repeats
        included — a site's cookies are its own business inside its own origin."""
        crumb, *attributes = header.split(";")
        name, separator, _ = crumb.partition("=")
        name = name.strip()
        if not separator or not name or name.lower().startswith(RESERVED_COOKIE_PREFIX):
            return None
        kept = [
            attribute
            for attribute in attributes
            if attribute.strip().partition("=")[0].strip().lower() != "domain"
        ]
        return ";".join([crumb, *kept])

    async def _body(self, upstream: httpx.Response, appended: bytes) -> AsyncIterator[bytes]:
        """The origin's bytes, closing the upstream response whichever way the stream ends. A
        failure mid-stream — the origin dies, the read times out — escapes the response's task group
        before Starlette reaches its background task, so the pooled connection is released here or
        never; `aclose` is idempotent, so the background close still covers a client that
        disconnects before the last chunk.

        `appended` is the heartbeat tag, written once after the document's last chunk: a script
        element after `</html>` is parsed and run, while one put in front of the doctype would drop
        the page into quirks mode, and appending is the only place that needs neither a buffer nor a
        parse of a site's own markup. It follows the origin's bytes only when they all arrived, so a
        stream that died carries no tag of ours past the failure."""
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
        if appended:
            yield appended

    async def _no_socket_view(self, websocket: WebSocket) -> None:
        """The view path trades a token over HTTP and speaks no other protocol. Claimed for the
        reason the proxy claims it: unclaimed, `/~t/{token}` falls to the catch-all, which forwards
        its path to the sandbox — handing agent-authored code a token good for fresh sessions. One
        parameter-less handler serves both routes, since FastAPI binds only what a handler
        declares."""
        await self._refuse(websocket, SiteRefusal(403, LINK_NOT_VALID))

    async def _socket(self, websocket: WebSocket, path: str) -> None:
        """Relay one WebSocket to the site's own server, so a site whose protocol is not
        request/response works through the frame — a dev server's live reload, and an app that
        pushes.

        The gate is the proxy's — same Host, same session cookie, same dial — plus the origin check
        the proxy has no use for: every site is a label under one `base_host`, so the browser counts
        them same-site and attaches the addressed site's host-only `ufo_site` cookie to a socket
        opened from any other label, which would hand site B a bidirectional channel into site A's
        own server for the hour that session lasts. Nothing is accepted until the site's own server
        has agreed to the connection, so a viewer never holds an open socket to a site that refused
        one, and the subprotocol the viewer is told is the one the site chose, not an echo of what
        was asked."""
        if not same_origin_handshake(websocket):
            return await self._refuse(websocket, SiteRefusal(403, FOREIGN_ORIGIN))
        authorized = self._authorized(websocket)
        if isinstance(authorized, SiteRefusal):
            return await self._refuse(websocket, authorized)
        if authorized.shipped is not None and self.apps_dev_server is not None:
            dialed: DialTarget | SiteRefusal = self.apps_dev_server
        elif await self._stored_manifest(authorized) is not None or authorized.shipped is not None:
            return await self._refuse(websocket, SiteRefusal(501, SITE_HAS_NO_SOCKET))
        else:
            dialed = await self._dial_site(authorized)
        if isinstance(dialed, SiteRefusal):
            return await self._refuse(websocket, dialed)
        scheme = "wss" if dialed.tls else "ws"
        url = self._upstream_url(scheme, dialed.host, path, websocket.scope["query_string"])
        offered = [Subprotocol(name) for name in websocket.scope.get("subprotocols") or []]
        with ws(authorized.workspace_id):
            try:
                upstream = await connect(
                    url,
                    additional_headers=self._upstream_headers(websocket, dialed.headers),
                    subprotocols=offered or None,
                    max_size=WEBSOCKET_MAX_MESSAGE_BYTES,
                    compression=None,
                    open_timeout=WEBSOCKET_OPEN_TIMEOUT_SECONDS,
                )
            except (OSError, WebSocketException, TimeoutError) as error:
                log_error(
                    "ingress.socket_refused",
                    conversation_id=str(authorized.conversation_id),
                    error=repr(error),
                )
                return await self._refuse(websocket, SiteRefusal(503, SITE_NOT_ANSWERING))
            async with upstream:
                await websocket.accept(subprotocol=upstream.subprotocol)
                try:
                    await self._relay(websocket, upstream)
                except Exception as error:
                    # Any failure of the relay, not an enumeration of its classes: three spellings
                    # of the viewer is gone have escaped a named tuple so far.
                    log_error(
                        "ingress.socket_failed",
                        conversation_id=str(authorized.conversation_id),
                        error=repr(error),
                    )
                    await self._end(websocket, WEBSOCKET_CLOSE_UPSTREAM_GONE, SITE_NOT_ANSWERING)

    async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None:
        """Refuse the handshake with the very response the proxy would have sent, through the
        Websocket Denial Response extension. One gate, one answer: an unauthorized viewer reads the
        same status and response body whichever protocol it arrived on, where a bare policy close
        would have supplied neither — down to the cache directive, since `SITE_HAS_NO_SOCKET` is a
        501 and RFC 9110 lists that status cacheable by default."""
        await websocket.send_denial_response(
            Response(
                refusal.message,
                status_code=refusal.status,
                media_type=refusal.media_type,
                headers={"cache-control": UNCACHEABLE},
            )
        )

    async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None:
        """Both directions at once, ending as soon as either does. A socket has no request to
        enclose it, so the end of one direction is the only signal the other is finished: the viewer
        navigating away leaves the site's own reader with nothing to read, and a site that stops
        leaves the viewer waiting on a socket that will never speak again. Whichever finishes first
        cancels its twin, and its own exception — not the cancellation — is what surfaces."""
        directions = (
            asyncio.create_task(self._viewer_to_site(viewer, upstream)),
            asyncio.create_task(self._site_to_viewer(upstream, viewer)),
        )
        done, pending = await asyncio.wait(directions, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        failures = [task.exception() for task in done]
        for failure in failures:
            if failure is not None:
                raise failure

    async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None:
        """Text relayed as text and bytes as bytes: the frame type is part of the protocol a site
        speaks, and a dev server reading JSON off a text frame gets a binary one it cannot parse if
        this collapses them."""
        while True:
            message = await viewer.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            await upstream.send(message["bytes"] if text is None else text)

    async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None:
        """The site's own close code reaches the viewer, since a site's client reads it to decide
        whether to retry — except for the three codes RFC 6455 forbids sending, which stand for a
        close this end only observed and are reported as an unexpected condition instead."""
        with suppress(WebSocketException):
            async for message in upstream:
                if isinstance(message, str):
                    await viewer.send_text(message)
                else:
                    await viewer.send_bytes(message)
        code = upstream.close_code or WEBSOCKET_CLOSE_NORMAL
        if code in WEBSOCKET_UNSENDABLE_CLOSE_CODES:
            code = WEBSOCKET_CLOSE_UPSTREAM_GONE
        await self._end(viewer, code, upstream.close_reason or "")

    async def _end(self, viewer: WebSocket, code: int, reason: str) -> None:
        """The terminal close, which must not raise on top of whatever brought us here.

        A viewer that already went away leaves nothing to close, and the stack reports that at least
        three ways: `RuntimeError` once Starlette's state machine holds the disconnect,
        `WebSocketDisconnect` out of the send itself, and an `AttributeError` from inside the ASGI
        server's own protocol when the transport resets with no loop yield in between. Naming the
        classes is what kept missing one, and the next miss is a socket that ends with no close
        frame at all. The connection is over however this raises, and that is what is committed."""
        with suppress(Exception):
            await viewer.close(code=code, reason=reason)


def ingress_base_host(configured: str | None) -> str:
    """The hostname every site is a label under, from `[sandbox] ingress_public_url`. Unset, the
    ingress has nothing to resolve a request's site against and would answer 404 to every viewer, so
    it refuses to boot instead — before the readiness probe reports green. The knob's shape is held
    at config load; this is the one piece the ingress reads off it."""
    host = urlsplit(configured or "").hostname
    if not host:
        raise RuntimeError(
            "sandbox.ingress_public_url must be set to the wildcard base every site is a subdomain "
            "of (e.g. https://example.com) — the ingress resolves each request's site from it"
        )
    return host


def apps_dev_target(configured: str | None) -> DialTarget | None:
    """The dial for `[sandbox] apps_dev_server`, or None where a deploy serves the published bundle.
    The knob's shape is held at config load; this reads the authority and the scheme off it."""
    if configured is None:
        return None
    base = urlsplit(configured)
    return DialTarget(host=base.netloc, tls=base.scheme == "https")


def ingress_frame_ancestor(configured: str | None) -> str:
    """The deploy-wide source expression a hosted site may be framed by: the origin of
    `[connect] public_base_url` — scheme, host and port, never its path — which is where the frame
    that reads a hosted site lives. A signed session may name one workspace-scoped sibling site
    beside it. `'none'` when unset, since then no frame exists and nothing may embed a site: unset
    is not a reason to allow what a set base would forbid.

    A plain-http `localhost` base names every port on it: the host never leaves the machine, so
    the portal served from source on another port (the zero-services dev server) is the same
    developer's page, and the exact port would refuse the frame it opens with a browser-generated
    error no reply can explain. An https base keeps its one port."""
    base = urlsplit(configured or "")
    if not (base.scheme and base.hostname):
        return NO_FRAME_ANCESTOR
    if plain_local(configured):
        return f"{base.scheme}://{base.hostname}:*"
    port = f":{base.port}" if base.port else ""
    return f"{base.scheme}://{base.hostname}{port}"


def upstream_client() -> httpx.AsyncClient:
    """The one client every site's origin is dialed through, bounded and cookie-blind.

    A client keeps a cookie jar, fills it from every response it sees, and replays it on later
    requests to the same host. One process serves every workspace and the local and docker carriers
    dial each sandbox as `127.0.0.1:<port>`, so a jar here would hand one site's cookies to the next
    site to land on that authority — and would fabricate a `cookie` header on requests the viewer
    sent none. The policy allows no domain, so nothing is ever stored and nothing is ever sent: the
    viewer's own cookies, minus ours, are the only ones an origin reads.

    Two mechanisms, two halves. Nothing is ever *sent* because `_proxy` builds a bare
    `httpx.Request` rather than going through `build_request`, which is where a client would merge
    its jar onto an outgoing request. Nothing is ever *stored* because of the policy below —
    `Client.send` extracts `Set-Cookie` from every response it sees whatever built the request, so
    without it this process's jar would grow by every cookie every site ever set, for the life of
    the pod."""
    return httpx.AsyncClient(
        timeout=UPSTREAM_TIMEOUT,
        limits=httpx.Limits(
            max_connections=UPSTREAM_MAX_CONNECTIONS,
            max_keepalive_connections=UPSTREAM_MAX_KEEPALIVE_CONNECTIONS,
        ),
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    )


def run() -> None:
    config = load_config()
    init_o11y(os.environ.get(OTLP_ENDPOINT_ENV) or config.o11y.otlp_endpoint)
    manifests = load_manifests(config.pack.name)
    init_db(owner_dsn(config))
    asyncio.run(verify_db_reachable())
    ingress_secret()
    ingress_base = urlsplit(config.sandbox.ingress_public_url or "")
    selected = select_carriers(config, manifests)
    client = upstream_client()
    server = IngressServe(
        backend=config.sandbox.backend,
        base_host=ingress_base_host(config.sandbox.ingress_public_url),
        carrier=selected.carrier,
        resume_carriers=selected.resume,
        client=client,
        blob=blob_store_for(config.blob),
        frame_ancestor=ingress_frame_ancestor(config.connect.public_base_url),
        reporter=SiteReporter(client=client, serve_base_url=config.connect.public_base_url),
        site_scheme=ingress_base.scheme,
        site_port_suffix=f":{ingress_base.port}" if ingress_base.port else "",
        apps_dev_server=apps_dev_target(config.sandbox.apps_dev_server),
    )
    log("ingress.starting", port=config.sandbox.ingress_port)
    uvicorn.run(
        server.app(),
        host=INGRESS_BIND_HOST,
        port=config.sandbox.ingress_port,
        ws_max_size=WEBSOCKET_MAX_MESSAGE_BYTES,
        ws_per_message_deflate=False,
    )
