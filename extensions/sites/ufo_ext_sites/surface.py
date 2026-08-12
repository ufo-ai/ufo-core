"""The hosted-site frame: the page a site's permanent link opens, and the creator's visibility
control.

The link is an address, not an authorization — a surface token carrying `{ws, conversation, name}`,
so the shared fleet resolves the workspace from the URL alone before a row is read, and nothing in
it expires. Every visit passes the gate again: the token is verified, the site resolved, the viewer
authenticated from the `ufo_session` cookie the web surface binds, and the site's `visibility`
decides. A public site skips authentication; a workspace site admits any authenticated member; a
private one admits its creator alone. Anything else is a 404 with the same body as an unknown token,
so the frame is no oracle for which sites exist. An unauthenticated viewer of a non-public site is
told exactly that and sent to `/login`: the shared host routes that prefix to the onboarding
gateway rather than to this fleet, so it is same-origin with the frame, and the walk it starts ends
on a card whose one button posts the member's bearer into the portal, which binds `ufo_session`.
Signing in is therefore the whole recovery — a member of the workspace needs nothing widened to see
a site their workspace already may. The portal itself is not the link: reached cold it can only ask
for a token the viewer does not have.

The bytes are not served here. The frame renders an `<iframe>` at the site's own ingress origin, and
site traffic goes there — the serve loop only ever hands out this one page. That address is minted
per render and never stored: it carries a view token the ingress trades for the origin's own session
cookie. That token is a bearer credential until its TTL passes, not a single use, and the origin
session it buys consults no `hosted_site` row — so narrowing a site's visibility or deleting it
stops new viewers, not one already inside. Because the two origins differ, the embedded site's
scripts reach neither the selector, the session cookie, nor any app route, and the `sandbox` list
keeps them from navigating the member's tab away; the visibility `POST` still carries a CSRF token
bound to the viewer's own session, so a cross-site form cannot flip a site the creator owns."""

import hashlib
import html
from dataclasses import dataclass
from uuid import UUID

from ufo.sdk.bearer import verify_token
from ufo.sdk.http import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Request,
    Response,
)
from ufo.sdk.surface_token import mint_surface_token, verify_surface_token
from ufo.sdk.surfaces import SurfaceAuth, SurfaceContext, SurfaceRoute, SurfaceSpec
from ufo_ext_sites.store import HostedSite, HostedSites, Visibility, visibility_level

SURFACE_SITES = "sites"
SESSION_COOKIE = "ufo_session"
FRAME_PATH = "/surface/sites"
WORKSPACE_CLAIM = "ws"
CONVERSATION_CLAIM = "conversation"
NAME_CLAIM = "name"
CSRF_CLAIM = "csrf"
TOKEN_PARAM = "site_token"
PATH_PARAM = "site_path"
VISIBILITY_FIELD = "visibility"
CSRF_FIELD = "csrf"
HOSTING_UNCONFIGURED = (
    "this deployment sets no [connect] public_base_url, so a site has no link to be opened at"
)
NOT_FOUND_BODY = "no such site"
UNCONFIGURED_BODY = "Site hosting is not configured on this deployment."
CSRF_REJECTED_BODY = "the visibility form did not match this session; reload the page and retry"
PUBLIC_VISIBILITY_UNAVAILABLE = "public sharing is not available for new sites"
LOGIN_PATH = "/login"
NOT_SIGNED_IN_PAGE = (
    "<main><p>This site is not public, and this browser is not signed in to the workspace that "
    f'hosts it.</p><p><a href="{LOGIN_PATH}">Sign in</a>, then open this link again.</p></main>'
)
IFRAME_SANDBOX = (
    "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads "
    "allow-pointer-lock"
)
VISIBILITY_LABELS: dict[Visibility, str] = {
    "private": "Only me",
    "workspace": "Workspace members",
    "public": "Anyone with the link",
}
VISIBILITY_BADGES: dict[Visibility, str] = {
    "private": "Private",
    "workspace": "Visible to workspace members",
    "public": "Visible to anyone with the link",
}


class SiteHostingUnconfigured(RuntimeError):
    """The deploy configures no public base URL, so no site link exists to mint or report."""


@dataclass(frozen=True)
class SiteAddress:
    """What a site token names: the workspace to bind, and the conversation-scoped site to read."""

    workspace_id: UUID
    conversation_id: UUID
    name: str


def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str:
    """The permanent token addressing one site, mintable wherever a site is registered."""
    return mint_surface_token(
        SURFACE_SITES,
        {
            WORKSPACE_CLAIM: str(workspace_id),
            CONVERSATION_CLAIM: str(conversation_id),
            NAME_CLAIM: name,
        },
    )


def site_url(
    public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str
) -> str:
    """The site's hosted link: the frame route on the deploy's public base, carrying the address.
    The one producer of that link, and it refuses to be approximate — a deploy with no
    `[connect] public_base_url` can host nothing, and the deliverable every tool description and
    prompt promises must not come back absent or null."""
    if not public_base_url:
        raise SiteHostingUnconfigured(HOSTING_UNCONFIGURED)
    token = site_token(workspace_id, conversation_id, name)
    return f"{public_base_url.rstrip('/')}{FRAME_PATH}/{token}"


def site_address(token: str) -> SiteAddress | None:
    """The address a token proves, or None when its signature, surface, or claims do not hold."""
    claims = verify_surface_token(SURFACE_SITES, token)
    if claims is None:
        return None
    try:
        workspace_id = UUID(claims[WORKSPACE_CLAIM])
        conversation_id = UUID(claims[CONVERSATION_CLAIM])
        name = claims[NAME_CLAIM]
    except (KeyError, ValueError):
        return None
    return SiteAddress(workspace_id=workspace_id, conversation_id=conversation_id, name=name)


async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None:
    """The workspace the requested site's token names — resolved before any row is read, because a
    public viewer carries no cookie to scope by. An unverifiable token answers with the frame's own
    404 rather than a 401, so a forged address and an unauthorized one are indistinguishable."""
    address = site_address(request.path_params.get(TOKEN_PARAM, ""))
    return _not_found() if address is None else address.workspace_id


async def frame(ctx: SurfaceContext, request: Request) -> Response:
    """Render one site: verify, resolve, authenticate, gate, embed.

    Whatever trails the token is the site path to open at, so a member can be handed a link to one
    page of a site rather than only its front door — the site's own paths live at the embedded
    origin and are reachable from outside no other way. The gate is the site's either way: a deep
    link proves no more than the bare one, and both pass through the same visibility check."""
    site = await _resolve(ctx, request)
    if site is None:
        return _not_found()
    viewer = await _viewer(ctx, request)
    if site.visibility != "public" and viewer is None:
        return HTMLResponse(_page("Not public", _STYLE, NOT_SIGNED_IN_PAGE))
    if site.visibility == "private" and viewer != site.creator_member_id:
        return _not_found()
    embedded = ctx.ingress_url(
        site.conversation_id, site.port, f"/{request.path_params.get(PATH_PARAM, '')}"
    )
    csrf = (
        mint_surface_token(SURFACE_SITES, {CSRF_CLAIM: _session_digest(request)})
        if viewer is not None and viewer == site.creator_member_id
        else ""
    )
    frame_path = f"{FRAME_PATH}/{request.path_params[TOKEN_PARAM]}"
    return HTMLResponse(_frame_page(site, embedded, frame_path, csrf))


async def set_visibility(ctx: SurfaceContext, request: Request) -> Response:
    """Move a site between visibility levels for its creator alone. A viewer who is not the creator
    gets the same 404 an unknown site does; a request whose CSRF token is not this session's is
    refused outright, since the creator is proven and the form is not."""
    site = await _resolve(ctx, request)
    if site is None:
        return _not_found()
    viewer = await _viewer(ctx, request)
    if viewer is None or viewer != site.creator_member_id:
        return _not_found()
    form = await request.form()
    if not _csrf_holds(request, str(form.get(CSRF_FIELD, ""))):
        return PlainTextResponse(CSRF_REJECTED_BODY, status_code=403)
    try:
        level = visibility_level(str(form.get(VISIBILITY_FIELD, "")))
    except ValueError as error:
        return PlainTextResponse(str(error), status_code=400)
    if level == "public" and site.visibility != "public":
        return PlainTextResponse(PUBLIC_VISIBILITY_UNAVAILABLE, status_code=400)
    await _sites(ctx).set_visibility(site.conversation_id, site.name, level)
    return RedirectResponse(f"{FRAME_PATH}/{request.path_params[TOKEN_PARAM]}", status_code=303)


async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None:
    address = site_address(request.path_params.get(TOKEN_PARAM, ""))
    if address is None:
        return None
    return await _sites(ctx).read(address.conversation_id, address.name)


def _sites(ctx: SurfaceContext) -> HostedSites:
    return HostedSites(ctx.workspace_id, ctx.transaction)


async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None:
    """The member this request's session cookie authenticates, or None when it carries none or its
    bearer names no member of this workspace. The email is the sites `surface_identity`, linked to
    the member who already owns it the first time they open a frame."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    email = verify_token(token, ctx.workspace_id)
    if email is None:
        return None
    return await ctx.linked_member(email) or await ctx.link_member(email, email)


def _csrf_holds(request: Request, submitted: str) -> bool:
    claims = verify_surface_token(SURFACE_SITES, submitted)
    return claims is not None and claims.get(CSRF_CLAIM) == _session_digest(request)


def _session_digest(request: Request) -> str:
    """What a CSRF token is signed over: this session's cookie. Another origin's page can neither
    read the HttpOnly cookie nor sign over the deploy secret, so it cannot produce a token this
    request's own session matches."""
    return hashlib.sha256(request.cookies.get(SESSION_COOKIE, "").encode()).hexdigest()


def _not_found() -> Response:
    return PlainTextResponse(NOT_FOUND_BODY, status_code=404)


def _page(title: str, style: str, body: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{title}</title><style>{style}</style>{body}"
    )


def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str) -> str:
    """The site inside the app's own chrome: its name, the creator's selector or a viewer's badge,
    and the site itself at its own origin, freshly addressed every render. `referrerpolicy` keeps
    the frame's address — which is the site token — out of every request the embedded site makes,
    and the `sandbox` list withholds `allow-top-navigation`: the framed bytes are model-authored,
    so a page built from an injected brief must not be able to navigate the member off the app
    origin onto something wearing its face. The flags kept are the ones the site is promised —
    scripts, its own origin's storage, forms, popups, modals, downloads, pointer lock — with
    fullscreen granted through `allow`. A deploy with no ingress configured has no origin to embed,
    so the frame says that instead of framing nothing.

    `frame_path` is the token's own address rather than the address this request arrived at: the
    selector posts to `<frame_path>/visibility`, and a deep link's path would otherwise trail into
    that action and name a route no method serves."""
    control = (
        _selector(site.visibility, frame_path, csrf)
        if csrf
        else f"<span class=badge>{VISIBILITY_BADGES[site.visibility]}</span>"
    )
    site_view = (
        f'<iframe src="{html.escape(embedded, quote=True)}" '
        f'title="{html.escape(site.name, quote=True)}" referrerpolicy=no-referrer '
        f'sandbox="{IFRAME_SANDBOX}" allow="fullscreen"></iframe>'
        if embedded is not None
        else f"<main><p>{UNCONFIGURED_BODY}</p></main>"
    )
    return _page(
        html.escape(site.name),
        _STYLE + _FRAME_STYLE,
        f"<header><span class=name>{html.escape(site.name)}</span>{control}</header>{site_view}",
    )


def _selector(current: Visibility, frame_path: str, csrf: str) -> str:
    levels: tuple[Visibility, ...] = ("private", "workspace") + (
        ("public",) if current == "public" else ()
    )
    options = "".join(
        f"<option value={level}{' selected' if level == current else ''}>"
        f"{VISIBILITY_LABELS[level]}</option>"
        for level in levels
    )
    return (
        f'<form method=post action="{html.escape(frame_path, quote=True)}/visibility">'
        f'<input type=hidden name={CSRF_FIELD} value="{html.escape(csrf, quote=True)}">'
        f"<select name={VISIBILITY_FIELD}>{options}</select>"
        "<button type=submit>Save</button></form>"
    )


_STYLE = (
    ":root{color-scheme:light dark}"
    "*{box-sizing:border-box}"
    "body{margin:0;font:15px/1.5 system-ui,sans-serif;display:flex;flex-direction:column;"
    "height:100vh;background:Canvas;color:CanvasText}"
    "main{padding:24px;max-width:60ch}"
    "a{color:LinkText}"
)
_FRAME_STYLE = (
    "header{display:flex;align-items:center;gap:12px;padding:8px 12px;"
    "border-bottom:1px solid color-mix(in srgb, CanvasText 15%, transparent)}"
    ".name{font-weight:600}"
    ".badge,form{margin-left:auto;font-size:13px;opacity:.75}"
    "form{display:flex;gap:6px;opacity:1}"
    "select,button{font:inherit;font-size:13px;padding:2px 6px}"
    "iframe{flex:1;width:100%;border:0}"
)

SITES_SURFACE = SurfaceSpec(
    name=SURFACE_SITES,
    routes=(
        SurfaceRoute(method="GET", path=f"{{{TOKEN_PARAM}}}", handler=frame),
        SurfaceRoute(method="POST", path=f"{{{TOKEN_PARAM}}}/visibility", handler=set_visibility),
        SurfaceRoute(method="GET", path=f"{{{TOKEN_PARAM}}}/{{{PATH_PARAM}:path}}", handler=frame),
    ),
    identify=resolve_workspace,
)
