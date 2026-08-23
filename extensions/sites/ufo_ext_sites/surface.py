"""The hosted-site frame: the page a site's permanent link opens, and the creator's visibility
control.

The link is an address, not an authorization — a surface token carrying `{ws, conversation, name}`,
so the shared fleet resolves the workspace from the URL alone before a row is read, and nothing in
it expires. Every visit passes the gate again: the token is verified, the site resolved, the viewer
authenticated from the `ufo_session` cookie the web surface binds, and the site's `visibility`
decides. A public site skips authentication; a workspace site admits any authenticated member; a
private one admits its creator and workspace admins. A site bound as an agent's homepage answers
on the agent instead: a workspace-visible agent's homepage admits any authenticated member, a
private agent's its owner and admins — and it renders bare, no header, because the portal frames
it as the agent's own page. Anything else is a 404 with the same body as an unknown token,
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
bound to the viewer's own session, so a cross-site form cannot flip a site the creator owns.

The head carries a share card, so a link pasted into a chat unfurls as a picture rather than one
bare line. The site's own name reaches that head only when the site is public: the crawler that
reads these tags carries no session and passes no gate, so a name in them is published to whoever
holds the link. Every other level gets a generic title naming neither the site nor its workspace.

The picture obeys that same rule, harder. A public site's card is the site's own front page beside
the brandmark, composed at deploy time (`share_card.py`) and served by the one anonymous route here:
`share/site/<site token>/<digest>.jpg`. That route re-reads the row and serves the card only while
the site is still public and not homepage-bound, because a level is mutable and a URL in a head tag
cannot be recalled. It carries no session, sets no cookie, mints no grant, and answers
`public, max-age=600` rather than `immutable`, so a site that stops being public stops being
previewed within minutes. The edge worker (`infra/modules/edge`) answers that one address off a
stored copy for exactly the lifetime this route names, so an unfurl storm reads the row once per
window rather than once per request and the same minutes bound the drain. Every other site keeps the
brand's generic card, naming nothing."""

import hashlib
import html
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from ufo.sdk.bearer import LOGOUT_PATH, verify_token
from ufo.sdk.http import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Request,
    Response,
)
from ufo.sdk.seats import Seats
from ufo.sdk.surface_token import mint_surface_token, verify_surface_token
from ufo.sdk.surfaces import SurfaceAuth, SurfaceContext, SurfaceRoute, SurfaceSpec
from ufo_ext_sites.share_card import CARD_EXTENSION, CARD_HEIGHT, CARD_MEDIA_TYPE, CARD_WIDTH
from ufo_ext_sites.store import HostedSite, HostedSites, Visibility, visibility_level

SURFACE_SITES = "sites"
SESSION_COOKIE = "ufo_session"
FRAME_PATH = "/surface/sites"
WORKSPACE_CLAIM = "ws"
CONVERSATION_CLAIM = "conversation"
NAME_CLAIM = "name"
SLUG_CLAIM = "slug"
DIGEST_CLAIM = "digest"
AGENT_CLAIM = "agent"
CSRF_CLAIM = "csrf"

APP_PORT_FLOOR = 20000
APP_PORT_SPAN = 20000
SHIPPED_ANCHOR_LABEL = "ufo-shipped-app"
TOKEN_PARAM = "site_token"
PATH_PARAM = "site_path"
CARD_HASH_PARAM = "card_hash"
VISIBILITY_FIELD = "visibility"
CSRF_FIELD = "csrf"
HOSTING_UNCONFIGURED = (
    "this deployment sets no [connect] public_base_url, so a site has no link to be opened at"
)
NOT_FOUND_BODY = "no such site"
UNCONFIGURED_BODY = "Site hosting is not configured on this deployment."
CSRF_REJECTED_BODY = "the visibility form did not match this session; reload the page and retry"
HOMEPAGE_FOLLOWS_AGENT_BODY = "this site is an agent's homepage; its visibility follows the agent's"
# The sign-out door, not the sign-in one: this page is also what a browser holding a live session
# for another workspace is answered, and the sign-in door forwards such a browser to its own portal
# rather than drawing the form. Clearing first reaches the form either way.
NOT_SIGNED_IN_PAGE = (
    "<main><p>This site is not public, and this browser is not signed in to the workspace that "
    f'hosts it.</p><p><a href="{LOGOUT_PATH}">Sign in</a>, then open this link again.</p></main>'
)
IFRAME_SANDBOX = (
    "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads "
    "allow-pointer-lock"
)
SHARE_CARD_URL = "https://ufo.ai/share/og-site.jpg"
SHARE_CARD_WIDTH = str(CARD_WIDTH)
SHARE_CARD_HEIGHT = str(CARD_HEIGHT)
SHARE_CARD_ALT = (
    "Made with — the words in white above the UFO wordmark, three ember dots beside it, "
    "on a black field."
)
SITE_CARD_SEGMENT = "share/site"
SITE_CARD_PATH = f"{FRAME_PATH}/{SITE_CARD_SEGMENT}"
SITE_CARD_CACHE = "public, max-age=600"
"""Cacheable, and never `immutable`: a site that stops being public must stop being previewed within
minutes, and the digest in the URL already covers the redeploy case.

The edge in front of this route stores its copy under this directive and writes none of its own, so
this is the one place the drain window is set — for the colo and the browser alike."""
SITE_CARD_HEADERS = {"cache-control": SITE_CARD_CACHE, "x-content-type-options": "nosniff"}
SITE_CARD_ALT = "The front page of {name}, drawn beside the UFO brandmark."
GENERIC_SHARE_TITLE = "A site on UFO"
SHARE_DESCRIPTION = "A site made with UFO."
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


def serve_port(conversation_id: UUID) -> int:
    """The port a conversation's site serves on, derived from the conversation so it is stable
    across redeploys (the site's origin hangs off `(conversation, port)`). Per-conversation rather
    than one fixed port because the local carrier's sandboxes share the host's port namespace — on
    one fixed port every deploy killed the previous conversation's server and every dial reached
    whoever deployed last. Container carriers are indifferent: any port works inside a namespace.

    It lives here, beside `site_label` and `ingress_url`, because the port is half a site's origin:
    the deploy tools and the shipped-frame render both derive it, and the origin math has one home
    rather than a copy in each."""
    return APP_PORT_FLOOR + conversation_id.int % APP_PORT_SPAN


def shipped_anchor(workspace_id: UUID, slug: str) -> UUID:
    """The synthetic conversation a shipped app page's origin hangs off — computed, never a row, so
    the page serves without one. One per `(workspace, app)`, so each workspace's shipped page owns
    its own origin and its cookies and storage stay scoped to it, and a fork reuses the same anchor
    as its `hosted_site.conversation_id` so the browser origin is identical before and after."""
    return uuid5(NAMESPACE_URL, f"{SHIPPED_ANCHOR_LABEL}:{workspace_id}:{slug}")


@dataclass(frozen=True)
class ShippedAddress:
    """What a shipped-frame token names: the workspace to bind, the app agent to gate on, the app
    slug whose deploy-wide bundle serves, and the bundle digest that bundle is."""

    workspace_id: UUID
    agent_id: UUID
    slug: str
    digest: str


def shipped_site_url(
    public_base_url: str | None, workspace_id: UUID, agent_id: UUID, slug: str, digest: str
) -> str | None:
    """The hosted link for a workspace's shipped app page: the frame route on the deploy's public
    base, carrying the shipped address whole. The token carries the agent so the frame gates the
    visit on the agent's visibility, and the slug and digest so it resolves the deploy-wide bytes —
    no `hosted_site` row is read for it. None when the deploy configures no `[connect]
    public_base_url` — the homepage read then answers as if no page exists, the same absence a
    deploy that hosts nothing shows."""
    if not public_base_url:
        return None
    token = mint_surface_token(
        SURFACE_SITES,
        {
            WORKSPACE_CLAIM: str(workspace_id),
            AGENT_CLAIM: str(agent_id),
            SLUG_CLAIM: slug,
            DIGEST_CLAIM: digest,
        },
    )
    return f"{public_base_url.rstrip('/')}{FRAME_PATH}/{token}"


def shipped_address(token: str) -> ShippedAddress | None:
    """The shipped address a token proves, or None when its signature, surface, or claims do not
    hold — a normal site token (no `agent`/`slug`/`digest`) reads as None here, and a shipped one
    reads as None in `site_address`, so the two token shapes never collide."""
    claims = verify_surface_token(SURFACE_SITES, token)
    if claims is None:
        return None
    try:
        return ShippedAddress(
            workspace_id=UUID(claims[WORKSPACE_CLAIM]),
            agent_id=UUID(claims[AGENT_CLAIM]),
            slug=claims[SLUG_CLAIM],
            digest=claims[DIGEST_CLAIM],
        )
    except (KeyError, ValueError):
        return None


def site_card_url(public_base_url: str | None, token: str, digest: str) -> str | None:
    """The public address of one site's share card: the anonymous route on the deploy's public base,
    carrying the site's own token and the digest of the card's bytes.

    The digest is what makes the address change on a redeploy, so a crawler that cached the old card
    is asking for a URL nothing answers rather than being handed stale pixels. A deploy with no
    `[connect] public_base_url` has no address to name and names none."""
    if not public_base_url:
        return None
    return f"{public_base_url.rstrip('/')}{SITE_CARD_PATH}/{token}/{digest}.{CARD_EXTENSION}"


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
    public viewer carries no cookie to scope by. A shipped app page's token names its workspace the
    same way, with no row behind it. An unverifiable token answers with the frame's own 404 rather
    than a 401, so a forged address and an unauthorized one are indistinguishable."""
    token = request.path_params.get(TOKEN_PARAM, "")
    address = site_address(token)
    if address is not None:
        return address.workspace_id
    shipped = shipped_address(token)
    return _not_found() if shipped is None else shipped.workspace_id


async def frame(ctx: SurfaceContext, request: Request) -> Response:
    """Render one site: verify, resolve, authenticate, gate, embed.

    Whatever trails the token is the site path to open at, so a member can be handed a link to one
    page of a site rather than only its front door — the site's own paths live at the embedded
    origin and are reachable from outside no other way. A deep link proves no more than the bare
    one: both pass the same gate — the agent's visibility for a homepage, the row's own for any
    other site. A shipped app page's token carries no row; it renders through `_shipped_frame`,
    gated the same way a homepage is."""
    shipped = shipped_address(request.path_params[TOKEN_PARAM])
    if shipped is not None:
        return await _shipped_frame(ctx, request, shipped)
    site = await _resolve(ctx, request)
    if site is None:
        return _not_found()
    frame_path = f"{FRAME_PATH}/{request.path_params[TOKEN_PARAM]}"
    base = ctx.public_base_url
    # A link unfurler is unauthenticated, so whatever the head says is public by definition: the
    # site's own name and its own picture go in the card only when the site itself is public. A
    # homepage follows its agent, whose levels stop at `workspace`, so it is never named or drawn.
    published = site.homepage_agent_id is None and site.visibility == "public"
    share = _share_tags(
        site.name if published else None,
        f"{base.rstrip('/')}{frame_path}" if base else None,
        site_card_url(base, request.path_params[TOKEN_PARAM], site.share_card_hash)
        if published and site.share_card_hash is not None
        else None,
    )
    viewer = await _viewer(ctx, request)
    if site.homepage_agent_id is not None:
        if viewer is None:
            return HTMLResponse(_page("Not public", _STYLE, NOT_SIGNED_IN_PAGE, share))
        agent = next((a for a in await ctx.list_agents() if a.id == site.homepage_agent_id), None)
        if agent is None:
            return _not_found()
        if (
            agent.visibility != "workspace"
            and viewer != agent.owner_member_id
            and not await _viewer_is_admin(ctx, viewer)
        ):
            return _not_found()
    else:
        if site.visibility != "public" and viewer is None:
            return HTMLResponse(_page("Not public", _STYLE, NOT_SIGNED_IN_PAGE, share))
        if (
            site.visibility == "private"
            and viewer != site.creator_member_id
            and not await _viewer_is_admin(ctx, viewer)
        ):
            return _not_found()
    embedded = ctx.ingress_url(
        site.conversation_id, site.port, f"/{request.path_params.get(PATH_PARAM, '')}"
    )
    csrf = (
        mint_surface_token(SURFACE_SITES, {CSRF_CLAIM: _session_digest(request)})
        if site.homepage_agent_id is None
        and viewer is not None
        and viewer == site.creator_member_id
        else ""
    )
    return HTMLResponse(
        _frame_page(
            site, embedded, frame_path, csrf, share, bare=site.homepage_agent_id is not None
        )
    )


async def _shipped_frame(
    ctx: SurfaceContext, request: Request, shipped: ShippedAddress
) -> Response:
    """Render a workspace's shipped app page: no `hosted_site` row, gated on the app agent's
    visibility exactly as a bound homepage is, and embedded from the deploy-wide bundle in the fleet
    store rather than any workspace's own source. The origin is the synthetic per-workspace anchor,
    so the page keeps its own cookies and storage and a later fork lands on the same origin. It
    renders `bare` — no header — because the portal frames it as the agent's own page.

    A shipped page follows its agent and has no public level of its own, so an unauthenticated
    viewer is always sent to sign in, and the head names the brand's generic card, not the page."""
    share = _share_tags(None, None, None)
    viewer = await _viewer(ctx, request)
    if viewer is None:
        return HTMLResponse(_page("Not public", _STYLE, NOT_SIGNED_IN_PAGE, share))
    agent = next((a for a in await ctx.list_agents() if a.id == shipped.agent_id), None)
    if agent is None:
        return _not_found()
    if (
        agent.visibility != "workspace"
        and viewer != agent.owner_member_id
        and not await _viewer_is_admin(ctx, viewer)
    ):
        return _not_found()
    anchor = shipped_anchor(shipped.workspace_id, shipped.slug)
    embedded = ctx.ingress_url(
        anchor,
        serve_port(anchor),
        f"/{request.path_params.get(PATH_PARAM, '')}",
        shipped_slug=shipped.slug,
        shipped_digest=shipped.digest,
    )
    return HTMLResponse(_shipped_frame_page(agent.name, embedded, share))


def _shipped_frame_page(title: str, embedded: str | None, share: str) -> str:
    """The shipped page inside the portal's pane: bare, transparent, the fleet bundle at its own
    origin freshly addressed every render. A deploy with no ingress configured has no origin to
    embed and says so instead of framing nothing — the same fallback the row-backed frame draws."""
    site_view = (
        f'<iframe src="{html.escape(embedded, quote=True)}" '
        f'title="{html.escape(title, quote=True)}" referrerpolicy=no-referrer '
        f'sandbox="{IFRAME_SANDBOX}" allow="fullscreen"></iframe>'
        if embedded is not None
        else f"<main><p>{UNCONFIGURED_BODY}</p></main>"
    )
    return _page(html.escape(title), _STYLE + _FRAME_STYLE + _BARE_STYLE, site_view, share)


async def share_card(ctx: SurfaceContext, request: Request) -> Response:
    """Serve one public site's share card to whoever asks, and nothing else.

    This is the one anonymous route in the module, because a link unfurler carries no session and
    follows one absolute URL. So it holds four rules of its own:

    **Public only.** The site row is read and its `visibility` decides, here, on this request — not
    at the deploy that drew the card. A level is mutable, and a site that stops being public must
    stop being previewed, so a card written while the site was public answers 404 the moment it is
    not. A homepage-bound site follows its agent and has no level of its own to publish, so it is
    refused outright. Every refusal is the frame's own 404 body, so the route is no oracle for which
    sites exist.

    **No session, no grant.** The URL is public by construction — it goes in a head tag that anyone
    may read — so it must never be a credential. Nothing here reads a cookie, sets one, mints a view
    token, or redirects to a signed artifact link.

    **Cacheable but drainable.** `max-age=600` and not `immutable`: a card must fall out of caches
    within minutes of a site being narrowed, and the digest in the URL already keeps a redeploy from
    being served from a stale cache. This is the origin behind the edge worker, which keeps its copy
    under this same directive: the rate reaching here is one read per window per colo, and the
    window a narrowed site drains in is the one named here.

    **Bytes only.** The key served is the one the row carries. The URL's digest is compared against
    the row's and never used to address anything, so no path in the artifact namespace is reachable
    through here and there is nothing to list."""
    site = await _resolve(ctx, request)
    if (
        site is None
        or site.homepage_agent_id is not None
        or site.visibility != "public"
        or site.share_card_blob_key is None
        or site.share_card_hash != request.path_params[CARD_HASH_PARAM]
    ):
        return _not_found()
    return Response(
        await ctx.blob.get(site.share_card_blob_key),
        media_type=CARD_MEDIA_TYPE,
        headers=SITE_CARD_HEADERS,
    )


async def set_visibility(ctx: SurfaceContext, request: Request) -> Response:
    """Move a site between visibility levels for its creator alone. A viewer who is not the creator
    gets the same 404 an unknown site does; a request whose CSRF token is not this session's is
    refused outright, since the creator is proven and the form is not. A homepage-bound site has no
    level of its own to move — its viewers follow the agent — so the post is refused whole."""
    site = await _resolve(ctx, request)
    if site is None:
        return _not_found()
    viewer = await _viewer(ctx, request)
    if viewer is None or viewer != site.creator_member_id:
        return _not_found()
    if site.homepage_agent_id is not None:
        return PlainTextResponse(HOMEPAGE_FOLLOWS_AGENT_BODY, status_code=409)
    form = await request.form()
    if not _csrf_holds(request, str(form.get(CSRF_FIELD, ""))):
        return PlainTextResponse(CSRF_REJECTED_BODY, status_code=403)
    try:
        level = visibility_level(str(form.get(VISIBILITY_FIELD, "")))
    except ValueError as error:
        return PlainTextResponse(str(error), status_code=400)
    await _sites(ctx).set_visibility(site.conversation_id, site.name, level)
    return RedirectResponse(f"{FRAME_PATH}/{request.path_params[TOKEN_PARAM]}", status_code=303)


async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None:
    address = site_address(request.path_params.get(TOKEN_PARAM, ""))
    if address is None:
        return None
    return await _sites(ctx).read(address.conversation_id, address.name)


def _sites(ctx: SurfaceContext) -> HostedSites:
    return HostedSites(ctx.workspace_id, ctx.transaction)


async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool:
    if viewer is None:
        return False
    async with ctx.transaction() as connection:
        snapshot = await Seats(ctx.workspace_id).snapshot(connection)
    return any(entry.admin and entry.id == viewer for entry in snapshot.members)


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


def _page(title: str, style: str, body: str, share: str) -> str:
    return (
        "<!doctype html><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{title}</title>{share}<style>{style}</style>{body}"
    )


def _share_tags(name: str | None, canonical: str | None, card: str | None) -> str:
    """What a link unfurler draws for this site: a card, and a title that names the site only when
    `name` is given — the caller decides both from the site's visibility, never from the viewer's
    session, because the crawler reading these tags carries none and republishes what it reads.

    `card` is this site's own composed card, which only a public site has one of and only a public
    site may be shown. Without one the tags name the brand's generic card, the gateway's own asset
    on the apex: a site's own bytes are `private, no-store` behind a session, so the page itself can
    never be an `og:image`, and a site with no card yet — one deployed before cards existed, or one
    whose render failed — unfurls exactly as it did before. A deploy with no `[connect]
    public_base_url` has no canonical link to name, and omits `og:url` rather than guessing one."""
    url = ""
    if canonical:
        url = f'<meta property=og:url content="{html.escape(canonical, quote=True)}">'
    title = html.escape(name or GENERIC_SHARE_TITLE, quote=True)
    image = html.escape(card or SHARE_CARD_URL, quote=True)
    alt = html.escape(
        SHARE_CARD_ALT if card is None else SITE_CARD_ALT.format(name=name), quote=True
    )
    return (
        "<meta property=og:type content=website>"
        "<meta property=og:site_name content=UFO>"
        f"{url}"
        f'<meta property=og:title content="{title}">'
        f'<meta property=og:description content="{SHARE_DESCRIPTION}">'
        f'<meta property=og:image content="{image}">'
        f"<meta property=og:image:width content={SHARE_CARD_WIDTH}>"
        f"<meta property=og:image:height content={SHARE_CARD_HEIGHT}>"
        f"<meta property=og:image:type content={CARD_MEDIA_TYPE}>"
        f'<meta property=og:image:alt content="{alt}">'
        "<meta name=twitter:card content=summary_large_image>"
        f'<meta name=twitter:title content="{title}">'
        f'<meta name=twitter:description content="{SHARE_DESCRIPTION}">'
        f'<meta name=twitter:image content="{image}">'
    )


def _frame_page(
    site: HostedSite,
    embedded: str | None,
    frame_path: str,
    csrf: str,
    share: str,
    *,
    bare: bool = False,
) -> str:
    """The site inside the app's own chrome: its name, the creator's selector or a viewer's badge,
    and the site itself at its own origin, freshly addressed every render. A homepage renders
    `bare` — no header at all — because the portal frames it as the agent's own page and a
    visibility control would name a level the row no longer answers to. `referrerpolicy` keeps
    the frame's address — which is the site token — out of every request the embedded site makes,
    and the `sandbox` list withholds `allow-top-navigation`: the framed bytes are model-authored,
    so a page built from an injected brief must not be able to navigate the member off the app
    origin onto something wearing its face. The flags kept are the ones the site is promised —
    scripts, its own origin's storage, forms, popups, modals, downloads, pointer lock — with
    fullscreen granted through `allow`. A deploy with no ingress configured has no origin to embed,
    so the frame says that instead of framing nothing.

    `frame_path` is the token's own address rather than the address this request arrived at: the
    selector posts to `<frame_path>/visibility`, and a deep link's path would otherwise trail into
    that action and name a route no method serves. `share` is the head's card for a link unfurler,
    already gated on visibility by the caller — the `<title>` a viewer reads is the site's name
    whatever its level, because that viewer passed the gate; a crawler passed nothing."""
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
    if bare:
        return _page(html.escape(site.name), _STYLE + _FRAME_STYLE + _BARE_STYLE, site_view, share)
    return _page(
        html.escape(site.name),
        _STYLE + _FRAME_STYLE,
        f"<header><span class=name>{html.escape(site.name)}</span>{control}</header>{site_view}",
        share,
    )


def _selector(current: Visibility, frame_path: str, csrf: str) -> str:
    options = "".join(
        f"<option value={level}{' selected' if level == current else ''}>{label}</option>"
        for level, label in VISIBILITY_LABELS.items()
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
_BARE_STYLE = "body{background:transparent}"
"""A bare frame paints nothing of its own: it stands inside the portal's pane, whose themed
background must show through until the embedded site draws — `Canvas` here is the white flash a
member sees on every homepage load."""

SITES_SURFACE = SurfaceSpec(
    name=SURFACE_SITES,
    routes=(
        # The card's route is declared before the deep-link route, because a deep link matches any
        # path under a token and would otherwise swallow this one as a site path.
        SurfaceRoute(
            method="GET",
            path=f"{SITE_CARD_SEGMENT}/{{{TOKEN_PARAM}}}/{{{CARD_HASH_PARAM}}}.{CARD_EXTENSION}",
            handler=share_card,
        ),
        SurfaceRoute(method="GET", path=f"{{{TOKEN_PARAM}}}", handler=frame),
        SurfaceRoute(method="POST", path=f"{{{TOKEN_PARAM}}}/visibility", handler=set_visibility),
        SurfaceRoute(method="GET", path=f"{{{TOKEN_PARAM}}}/{{{PATH_PARAM}:path}}", handler=frame),
    ),
    identify=resolve_workspace,
)
