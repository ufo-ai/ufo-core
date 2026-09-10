"""The route ABI: a route handler receives a Request and returns a Response, built with one of the
concrete response classes re-exported here — so an extension serving routes never reaches past
`ufo.sdk` for its HTTP types. A live surface serves a page as an `HTMLResponse` and its hub
tail as a `StreamingResponse`; `UploadFile` is the inbound type a multipart form's file parts
arrive as; `FormParserError` is what a malformed form body raises out of `Request.form()`
(starlette converts only its own `MultiPartException`), so a route that parses a form catches it
and answers the client's 400. A surface serving a socket takes the connection as a `WebSocket` and
reads `WebSocketDisconnect` as the viewer having gone."""

from typing import Literal
from urllib.parse import urlsplit

from python_multipart.exceptions import FormParserError as FormParserError
from starlette.datastructures import FormData as FormData
from starlette.datastructures import UploadFile as UploadFile
from starlette.requests import Request as Request
from starlette.responses import HTMLResponse as HTMLResponse
from starlette.responses import JSONResponse as JSONResponse
from starlette.responses import PlainTextResponse as PlainTextResponse
from starlette.responses import RedirectResponse as RedirectResponse
from starlette.responses import Response as Response
from starlette.responses import StreamingResponse as StreamingResponse
from starlette.websockets import WebSocket as WebSocket
from starlette.websockets import WebSocketDisconnect as WebSocketDisconnect


def cookie_secure(published_scheme: str) -> bool:
    """Whether a surface publishing under this scheme may mark its session cookie `Secure`.

    The scheme the surface publishes decides it, never the request's: a deploy terminates TLS at
    its ingress, so every request reaches the surface as plain http while the origin the browser
    holds is https — reading the request would give the attribute up on exactly the deploys that
    need it. Anything but plain http keeps it, so a base nobody stated stays `Secure`: the
    attribute is what makes a session cookie safe to carry, and only a surface that says it
    publishes plain http gives it up."""
    return published_scheme != "http"


def plain_local(published_base: str | None) -> bool:
    """Whether a published base is plain http on a host that never leaves the machine — `localhost`
    or a subdomain of it, which browsers resolve to the loopback. The one base a deploy may publish
    without TLS, and the mark of a dev deploy; a base nobody stated is not local."""
    base = urlsplit(published_base or "")
    return base.scheme == "http" and (
        base.hostname == "localhost" or (base.hostname or "").endswith(".localhost")
    )


def set_session_cookie(
    response: Response,
    name: str,
    token: str,
    *,
    samesite: Literal["lax", "strict", "none"],
    secure: bool,
) -> None:
    """The one sanctioned way to bind a session cookie. It takes no `domain`, so every cookie set
    through it is host-only — it can never widen to a parent domain, and so never crosses a
    subdomain, environment, or preview host, whatever the deploy's hostnames are. That covers the
    cookies ufo sets and only those: a proxy relaying another server's `Set-Cookie` is handing on a
    header this function never saw, and confining it is that proxy's own job (`ingress_serve`
    strips `Domain` from every cookie a hosted site sends). `HttpOnly` is non-negotiable; `SameSite`
    varies by surface, and `Secure` follows the scheme the surface publishes — a browser stores no
    `Secure` cookie sent by a plain-http origin, so a deploy serving one would bind a cookie that is
    dropped on arrival and refuse every request after it. `cookie_secure` is what every caller reads
    it from. A repo gate forbids raw `Response.set_cookie` outside this module, so the host-only
    guarantee cannot be bypassed."""
    response.set_cookie(name, token, httponly=True, secure=secure, samesite=samesite)
