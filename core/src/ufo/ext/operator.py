"""The operator web session shared by operator-only surfaces (the session debugger, the memory
explorer): verify the gateway bearer the request carries — the Authorization header, the session
cookie, or the one POST that opens a session, never a query parameter, so the long-lived credential
stays out of URLs, access logs, and browser history — and resolve it to the workspace the request is
scoped to under the operator-domain gate. One cookie serves every operator surface, so an operator
authenticates once and browses all of them.

It lives in core, re-exported through `ufo.sdk.operator`, because two sibling extensions (the
debugger and the memory explorer) share it and an extension imports only `ufo.sdk` — never another
extension — so this session can live in neither. Verification stays the caller's: `verified_claims`
takes the token and resolves `UFO_TOKEN_SECRET` itself, so this module never holds the signing
key."""

import re
from uuid import NAMESPACE_DNS, UUID, uuid5

from ufo.bearer import LOGIN_PATH, verified_claims
from ufo.ext.surface import OPERATOR_EMAIL_DOMAIN, SurfaceAuth, SurfaceContext
from ufo.sdk.http import JSONResponse, RedirectResponse, Request, Response, set_session_cookie
from ufo.seats import email_domain

OPERATOR_COOKIE = "ufo_debug"
TOKEN_FIELD = "token"
OPERATOR_PAGE_PATH = re.compile(r"/surface/[^/]+/?")


async def operator_claims(request: Request) -> tuple[str, str] | None:
    """The `(workspace, email)` the request's bearer proves: the Authorization header, then the
    session cookie, then — for the one POST that opens a session — the form body. Never a query
    parameter. Each fallback keys on the previous credential failing to RESOLVE, not merely being
    absent, so an operator whose cookie outlived its bearer's expiry recovers by posting a fresh
    token instead of being locked behind a stale cookie they can neither read nor delete."""
    scheme, _, header_token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        claims = _candidate_claims(header_token)
        if claims is not None:
            return claims
    claims = _candidate_claims(request.cookies.get(OPERATOR_COOKIE, ""))
    if claims is not None:
        return claims
    if request.method == "POST":
        posted = (await request.form()).get(TOKEN_FIELD, "")
        if isinstance(posted, str):
            return _candidate_claims(posted)
    return None


def _candidate_claims(candidate: str) -> tuple[str, str] | None:
    token = candidate.strip()
    return verified_claims(token) if token else None


async def resolve_operator_workspace(
    request: Request, _auth: SurfaceAuth
) -> UUID | Response | None:
    """The workspace this operator request is scoped to, or None to reject. The domain gate is the
    whole authorization: the verified bearer's email domain must equal the operator's domain before
    `?ws=` may re-scope the request to any workspace in the fleet — a raw workspace UUID, or a
    customer domain the shared fleet addresses as `uuid5(NAMESPACE_DNS, domain)`. Without `?ws=` the
    bearer's own workspace claim is the scope.

    A GET of the surface page carrying no credential that resolves redirects to the deploy's one
    sign-in page, so a link into an operator surface — the `debug` footer of a Slack turn, a
    bookmark — leads to the card that mints a bearer instead of dead-ending on `unauthorized`. The
    surface's own API routes still reject, so a fetch fails loudly rather than reading a page."""
    claims = await operator_claims(request)
    if claims is None:
        if request.method == "GET" and OPERATOR_PAGE_PATH.fullmatch(request.url.path):
            return RedirectResponse(LOGIN_PATH, status_code=303)
        return None
    claimed_workspace, email = claims
    if email_domain(email) != OPERATOR_EMAIL_DOMAIN:
        return None
    target = request.query_params.get("ws", "").strip()
    if not target:
        try:
            return UUID(claimed_workspace)
        except ValueError:
            return None
    try:
        return UUID(target)
    except ValueError:
        return uuid5(NAMESPACE_DNS, target.lower())


async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response:
    """Open a session: land the POSTed bearer as the httponly session cookie and redirect into the
    page. The token crosses only in the form body — never a URL — so access logs and browser
    history hold no credential; the identify resolver has already verified this exact form token
    before the handler runs. The cookie is `lax`, not `strict`, because arrival IS a cross-site
    navigation (the apex login page posts here, a Slack footer links here) and the redirected GET
    must already carry it."""
    posted = (await request.form()).get(TOKEN_FIELD, "")
    if not isinstance(posted, str) or not posted.strip():
        return JSONResponse({"error": "token form field is required"}, status_code=400)
    response = RedirectResponse(str(request.url), status_code=303)
    set_session_cookie(response, OPERATOR_COOKIE, posted.strip(), samesite="lax")
    return response
