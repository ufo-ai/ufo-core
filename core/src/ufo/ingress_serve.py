"""Composition root for the sandbox ingress — the egress proxy's inbound twin: one process for
every workspace, serving each sandbox port at its own signed origin, owner DSN with explicit
workspace filters."""

import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from ufo.config import load_config
from ufo.db import init_db, workspace_tx
from ufo.ext.loader import load_manifests
from ufo.o11y import init_o11y, log, log_error, warn
from ufo.proxy_serve import OTLP_ENDPOINT_ENV, owner_dsn
from ufo.sandbox.ingress_host import SiteLabelError, parse_site_label
from ufo.sandbox.ingress_token import (
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    IngressTokenError,
    ingress_secret,
    mint_ingress_token,
    verify_ingress_token,
)
from ufo.sandbox.select import select_carrier
from ufo.sandbox.session import Carrier, SandboxHandle, SandboxUnreachable, sandbox_handle_id
from ufo.schema import tables
from ufo.sdk.http import set_session_cookie
from ufo.workspace import ws

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
ORIGIN_RESPONSE_DROPPED_HEADERS = (
    HOP_BY_HOP_HEADERS | CACHE_DIRECTIVE_HEADERS | frozenset({"date", "server", "alt-svc", "via"})
)
"""What never comes back off the origin's response: the hop-by-hop set; the `date` and `server` the
ASGI server writes on every response it sends, since relaying the origin's own would put two of each
on the wire and RFC 9110 forbids a second `date`; `alt-svc`, which advertises another origin's
alternative services against this hostname, so a viewer would try that protocol here for the
lifetime the header names; `via`, which tells the viewer our own hop chain; and every
`CACHE_DIRECTIVE_HEADERS` field, replaced by `UNCACHEABLE` rather than relayed."""
UNCACHEABLE = "private, no-store"
"""The cache directive every proxied response carries, whatever the origin said.

Authorization here is a cookie checked per request, so any cache that stores a site's bytes and
answers a later request from them answers it without the check. The sites hostname is proxied at the
edge, and a CDN's default rules commonly cache by file extension — `/assets/*.js` is exactly what a
built site serves — so a hit would never reach this process and never see the 403. A site is
agent-authored code that typically sets no cache header at all, so this cannot be left to the
origin, and an origin that asks for `public, max-age=…` must not be able to override it: the
directive is set, and the origin's own is dropped."""
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
SESSION_ENDED = "This site needs a fresh link. Open it again in chat."
SITE_GONE = "This site is no longer hosted. Ask the agent that built it to put it back up."
SITE_NOT_ANSWERING = "This site is not answering."


@dataclass(frozen=True)
class IngressServe:
    """Read which site a request addresses off its own Host, authorize the viewer against that
    origin's session cookie, resolve the conversation's live sandbox, dial the addressed port
    through the carrier, and stream the exchange both ways — never buffering a body whole.

    A site owning its origin is what makes the label load-bearing rather than cosmetic: the
    conversation and port come from the hostname, so the site's `/`-rooted assets and redirects
    address it unchanged, and the browser keeps its cookies and storage away from every other
    site."""

    backend: str
    base_host: str
    carrier: Carrier
    client: httpx.AsyncClient

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
        # The bare path routes to its own handler, never to `_open` with a defaulted argument: a
        # route carrying no `{view_path}` placeholder makes FastAPI bind that parameter from the
        # query string, so `/~t?view_path=<token>` would have been read as a view link.
        application.add_api_route(INGRESS_VIEW_PATH, self._no_view_token, methods=PROXY_METHODS)
        application.add_api_route(
            f"{INGRESS_VIEW_PATH}/{{view_path:path}}", self._open, methods=PROXY_METHODS
        )
        application.add_api_route("/{path:path}", self._proxy, methods=PROXY_METHODS)
        return application

    async def _no_view_token(self, request: Request) -> Response:
        """The view path with nothing under it carries no token, so it opens nothing. Its own
        handler rather than `_open` with a default, because a route without the placeholder would
        bind `view_path` off the query string and read `/~t?view_path=<token>` as a link."""
        if request.method not in VIEW_METHODS:
            return Response(status_code=405, headers={"allow": ", ".join(VIEW_METHODS)})
        return Response(LINK_NOT_VALID, status_code=403, media_type="text/plain")

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
        registrable domains would need, and is not what this is."""
        if request.method not in VIEW_METHODS:
            return Response(status_code=405, headers={"allow": ", ".join(VIEW_METHODS)})
        view_token = view_path
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
        session = mint_ingress_token(
            replace(claims, expires_at=int(now.timestamp()) + INGRESS_SESSION_TTL_SECONDS),
            INGRESS_SESSION_KIND,
        )
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, INGRESS_SESSION_COOKIE, session, samesite="lax")
        return response

    def _site(self, request: Request) -> tuple[UUID, int] | None:
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

    async def _proxy(self, request: Request, path: str) -> Response:
        site = self._site(request)
        if site is None:
            return Response(NO_SITE_HERE, status_code=404, media_type="text/plain")
        try:
            claims = verify_ingress_token(
                request.cookies.get(INGRESS_SESSION_COOKIE, ""),
                datetime.now(UTC),
                INGRESS_SESSION_KIND,
            )
        except IngressTokenError:
            return Response(SESSION_ENDED, status_code=403, media_type="text/plain")
        if (claims.conversation_id, claims.port) != site:
            return Response(WRONG_SITE, status_code=403, media_type="text/plain")
        with ws(claims.workspace_id):
            stored = await self._stored_handle(claims.workspace_id, claims.conversation_id)
            container_id = None if stored is None else sandbox_handle_id(self.backend, stored)
            if not container_id:
                warn(
                    "ingress.no_container",
                    conversation_id=str(claims.conversation_id),
                    stored_handle=stored,
                    backend=self.backend,
                )
                return Response(SITE_GONE, status_code=503, media_type="text/plain")
            handle = SandboxHandle(
                conversation_id=claims.conversation_id, container_id=container_id
            )
            try:
                target = await self.carrier.dial(handle, claims.port)
            except SandboxUnreachable as error:
                warn(
                    "ingress.dial_failed",
                    conversation_id=str(claims.conversation_id),
                    error=repr(error),
                )
                return Response(SITE_GONE, status_code=503, media_type="text/plain")
            scheme = "https" if target.tls else "http"
            query = request.scope["query_string"].decode()
            url = f"{scheme}://{target.host}/{quote(path, safe=PATH_SAFE_CHARACTERS)}"
            if query:
                url = f"{url}?{query}"
            framed = any(header in request.headers for header in BODY_FRAMING_HEADERS)
            upstream_request = httpx.Request(
                request.method,
                url,
                headers=self._upstream_headers(request, target.headers),
                content=request.stream() if framed else None,
            )
            try:
                upstream = await self.client.send(upstream_request, stream=True)
            except httpx.HTTPError as error:
                log_error(
                    "ingress.upstream_failed",
                    conversation_id=str(claims.conversation_id),
                    error=repr(error),
                )
                return Response(SITE_NOT_ANSWERING, status_code=502, media_type="text/plain")
            try:
                response = StreamingResponse(
                    self._body(upstream),
                    status_code=upstream.status_code,
                    background=BackgroundTask(upstream.aclose),
                )
                response.headers["cache-control"] = UNCACHEABLE
                for name, value in upstream.headers.multi_items():
                    if name.lower() in ORIGIN_RESPONSE_DROPPED_HEADERS:
                        continue
                    if name.lower() == "set-cookie":
                        confined = self._confined_cookie(value)
                        if confined is not None:
                            response.headers.append(name, confined)
                        continue
                    response.headers.append(name, value)
            except Exception:
                await upstream.aclose()
                raise
        return response

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
        self, request: Request, dial_headers: Mapping[str, str]
    ) -> list[tuple[str, str]]:
        """Every header the origin sees, and the only ones it sees. The viewer's own, minus the
        hop-by-hop set, `host`, and any name the dial supplies, with the dial's own appended so its
        value is the one on the wire. Repeats arrive as repeats.

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

    async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]:
        """The origin's bytes, closing the upstream response whichever way the stream ends. A
        failure mid-stream — the origin dies, the read times out — escapes the response's task group
        before Starlette reaches its background task, so the pooled connection is released here or
        never; `aclose` is idempotent, so the background close still covers a client that
        disconnects before the last chunk."""
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()


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
    ingress_secret()
    server = IngressServe(
        backend=config.sandbox.backend,
        base_host=ingress_base_host(config.sandbox.ingress_public_url),
        carrier=select_carrier(config, manifests)[0],
        client=upstream_client(),
    )
    log("ingress.starting", port=config.sandbox.ingress_port)
    uvicorn.run(server.app(), host=INGRESS_BIND_HOST, port=config.sandbox.ingress_port)
