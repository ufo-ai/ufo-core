"""The route ABI: a route handler receives a Request and returns a Response, built with one of the
concrete response classes re-exported here — so an extension serving routes never reaches past
`ufo.sdk` for its HTTP types. A live surface serves a page as an `HTMLResponse` and its hub
tail as a `StreamingResponse`; `UploadFile` is the inbound type a multipart form's file parts
arrive as; `FormParserError` is what a malformed form body raises out of `Request.form()`
(starlette converts only its own `MultiPartException`), so a route that parses a form catches it
and answers the client's 400."""

from typing import Literal

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


def set_session_cookie(
    response: Response, name: str, token: str, *, samesite: Literal["lax", "strict", "none"]
) -> None:
    """The one sanctioned way to bind a session cookie. It takes no `domain`, so every cookie set
    through it is host-only — it can never widen to a parent domain, and so never crosses a
    subdomain, environment, or preview host, whatever the deploy's hostnames are. That covers the
    cookies ufo sets and only those: a proxy relaying another server's `Set-Cookie` is handing on a
    header this function never saw, and confining it is that proxy's own job (`ingress_serve`
    strips `Domain` from every cookie a hosted site sends). `HttpOnly` and `Secure` are
    non-negotiable; only `SameSite` varies by surface — `Secure` costs a plain-http origin nothing
    where ufo serves one, because the config gate restricts that to `localhost`, which browsers
    treat as trustworthy. A repo gate forbids raw `Response.set_cookie` outside this module, so the
    host-only guarantee cannot be bypassed."""
    response.set_cookie(name, token, httponly=True, secure=True, samesite=samesite)
