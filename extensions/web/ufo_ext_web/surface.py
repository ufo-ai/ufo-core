"""The web portal on the core surface seam, in its live mode: the authenticated shell around the
member's agents — the page and the one POST that opens its session, per-agent chat with
cookie-authenticated turn admission (each member holds any number of conversations per agent,
opened by the first message and listed for the rail) and an SSE tail of each turn's live frames,
read projections (the agent index, conversation transcripts, settings, skills, per-agent usage,
and connections) beside the workspace-level views every member holds — sources, credential slots,
memory (latest first, searched across every reachable agent), shared artifacts, and usage (their
own window, plus the workspace rollup for an admin) — the two generic object reads every kind's
index and detail page is built on (`objects/{kind}` and `objects/{kind}/{name}`, each answering
through the kind's own gate in the named agent's namespace), the administration view for a
workspace admin, and prepared intents, the panels' one mutation path.

The `ufo_session` cookie carries the signed HMAC member bearer the gateway or `ufoctl init` mints
(the `ufo.sdk.bearer` codec over `{ws, email, exp}`), landed by the one POST that opens a session
— the bearer never rides a URL. The shared fleet scopes each request to the workspace the bearer
claims (`resolve_workspace`), and the handler re-verifies it for its email — that email is the web
`surface_identity`, resolved to (or created as) a member the first time they speak, and the axis
the web audience (`ufo_ext_web.audience`) grants on. Admission is the shared durable queue every
surface admits onto; each conversation binds permanently to the agent the member selected. A web
conversation delivers by tailing the hub over SSE; a portal comment in a Slack conversation uses
that conversation's durable delivery, and one in a terminal conversation reaches its held stream.
Everything web-specific lives here, reaching core only through the privileged `SurfaceContext` —
the SDK surface a CI gate pins."""

import asyncio
import base64
import json
import os
import re
from binascii import Error as Base64Error
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, JsonValue, ValidationError
from ufo_ext_sites.surface import homepage_embed_url, shipped_homepage_url

from ufo.sdk.accounting import MemberSpendReport, SpendReport
from ufo.sdk.audience import SHARED_AUDIENCE, audience_subjects, conversation_audience
from ufo.sdk.balance import read_headroom
from ufo.sdk.bearer import LOGIN_PATH, SESSION_COOKIE, verify_token, workspace_claim
from ufo.sdk.callback_page import callback_page
from ufo.sdk.context import ExtensionContext, ScopedStore, SourceReader
from ufo.sdk.http import (
    FormData,
    FormParserError,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Request,
    Response,
    StreamingResponse,
    UploadFile,
    set_session_cookie,
)
from ufo.sdk.hub import (
    Absorbed,
    CostTick,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SkillLoad,
    SubagentActivity,
    Terminal,
    TextDelta,
    ToolCall,
    tool_activity,
)
from ufo.sdk.listings import ListingCursor, MalformedCursor
from ufo.sdk.manifest import (
    CONVERSATION_ARTIFACTS_MAX,
    CONVERSATION_AUTOMATIONS_MAX,
    CONVERSATION_SITES_MAX,
    IMAGE_PREVIEW_MAX_BYTES,
    ArtifactsSlotPayload,
    AutomationsSlotPayload,
    ConversationArtifact,
    ConversationSlotContext,
    ConversationSlotItem,
    ConversationSlotPayload,
    ConversationSlotProvider,
    ImagePreview,
    ImagePreviewGrant,
    InvalidImagePreview,
    SitesSlotPayload,
    WorkspaceChanges,
    raster_image_media_type,
    validated_image_preview,
)
from ufo.sdk.memory import MemoryMatch
from ufo.sdk.models import Message, ModelRequest, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.sdk.o11y import log
from ufo.sdk.objects import AGENT_KIND, ObjectListQuery, ObjectRef
from ufo.sdk.sandbox import shipped_app_slug
from ufo.sdk.seats import Seats
from ufo.sdk.surfaces import (
    MEMBER_ADMISSION,
    AgentSummary,
    BlobStore,
    ConnectRequestInvalid,
    CredentialRequest,
    CredentialRequestInvalid,
    KeyedAdmission,
    ListedConversation,
    PortalKind,
    SharedArtifact,
    SurfaceAuth,
    SurfaceContext,
    SurfaceRoute,
    TerminalFrame,
    ToolIntent,
    Turn,
    TurnContext,
    inbox_name,
    member_message_text,
)
from ufo.sdk.tools import REQUESTED_BY
from ufo_ext_web.audience import WebAudience, granted_emails, web_audience, web_extension
from ufo_ext_web.community import COMMUNITY, CommunityUnavailable
from ufo_ext_web.panels import (
    FIRST_RUN_PROVIDERS,
    SPOKEN_ROOM_PREFIXES,
    UNLOCKS_BY_NAME,
    ApplyIntent,
    agent_settings,
    submit_intent,
)
from ufo_ext_web.starters import (
    MEMORY_LIMIT,
    MEMORY_TEXT_CHARS,
    Slate,
    StarterCache,
)

SURFACE_WEB = "web"
SOURCE = "ufo web"
TOKEN_FIELD = "token"
MAX_INBOUND_CHARS = 200_000
MAX_INBOUND_BYTES = 4 * MAX_INBOUND_CHARS
MAX_REQUEST_BYTES = 25 * 1024 * 1024
MAX_FORM_BYTES = 64 * 1024
MAX_SECRET_BYTES = 4_096
UPLOAD_CHUNK_BYTES = 65_536
WEB_INBOX_DIR = "web-inbox"
FILES_NOTE = "[Attached files, saved in the workspace: {paths}]"
FILES_NOTE_RE = re.compile(r"\[Attached files, saved in the workspace: (?P<paths>[^]\n]+)\]\Z")
ATTACHMENT_MEDIA_TYPES = {".pdf": "application/pdf"}
ATTACHMENT_FALLBACK_MEDIA_TYPE = "application/octet-stream"
ATTACHMENT_PREVIEW_HEADERS = {
    "x-content-type-options": "nosniff",
    "cache-control": "private, no-store",
}
ANSWER_TURN_HEADER = "x-ufo-answer-turn"
ANSWER_QUESTION_HEADER = "x-ufo-answer-question"
TIMEZONE_HEADER = "x-ufo-timezone"
STOP_TURN_HEADER = "x-ufo-stop-turn"
SESSION_FAULT_HEADER = "x-ufo-session-fault"
REFUSAL_HEADER = "x-ufo-refusal"
NO_MEMBER_FAULT = "no-member"
MAX_MEMORY_QUERY_CHARS = 500
MAX_SEARCH_CHARS = 200
MEMORY_RECENT_LIMIT = 100
MEMORY_RESULT_LIMIT = 100
SCHEDULED_TASK_KIND = "scheduled_task"
SITE_KIND = "site"
OBJECT_FANOUT_LIMIT = 50
CONVERSATION_LIST_LIMIT = 100
COMMENT_SURFACES = frozenset({"slack", "ufo"})
SUBAGENT_ACTIVITY_LIMIT = 40
SUBAGENT_EVENT_LIMIT = 100
HISTORY_PAGE_MESSAGE_LIMIT = 100
HISTORY_PAGE_BYTE_LIMIT = 64 * 1024
CHAT_STORE_PREFIX = "chat/"
TITLE_JOB_NAME = "chat_titles"
TITLE_JOB_SCHEDULE = "*/15 * * * * *"
TITLE_BATCH = 5
HOMEPAGE_SEED_PREFIX = "homepage-seed/"
SEED_JOB_NAME = "seed_homepages"
SEED_JOB_SCHEDULE = "0 */5 * * * *"
HOMEPAGE_TOOLS = ("deploy_website", "set_homepage")
SEED_PROMPT = (
    "Build your homepage: the page members open on the agents screen. State what you are for, "
    "what you watch, recent work, and what you need from members. Build a small static site in "
    "the workspace, run deploy_website, then run set_homepage with the site name from the deploy "
    "result. Update the homepage when what you report changes."
)
TITLE_EXCERPT_CHARS = 1000
TITLE_MAX_TOKENS = 100
TITLE_SYSTEM_PROMPT = (
    "Write a title for the conversation excerpt: a plain phrase of at most eight words naming "
    "what the conversation is about. No quotes, no ending punctuation, no restated instructions. "
    "Answer with the title alone."
)
NEW_CONVERSATION = "new"
MAX_CHAT_TITLE_CHARS = 60
SPEND_WINDOW_DEFAULT_SECONDS = 86_400
MAX_USAGE_WINDOW_SECONDS = 31_536_000
USAGE_RANGES = {"7d": 604_800, "30d": 2_592_000, "90d": 7_776_000, "all": None}
PORTAL_PATH = "/surface/web"
CHAT_TARGET_PARAM = "c"
PORTAL_BUILD = "make build"
STATIC_DIR = Path(__file__).parent / "static"
PORTAL_FILE = STATIC_DIR / "index.html"
PORTAL_HTML = PORTAL_FILE.read_text() if PORTAL_FILE.is_file() else None
STATIC_PREFIX = f"{PORTAL_PATH}/static/"
ASSET_MEDIA_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".otf": "font/otf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".woff2": "font/woff2",
}


def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]:
    """The built assets this surface serves, by request name. Only a file whose suffix carries a
    declared media type is served, so a build that starts emitting source maps publishes nothing
    until someone declares them here. Nothing raises — the page names the assets it needs, and the
    origin gate fails when one of those is unserved, which is the layer that can tell a missing
    asset from a file the build merely left behind."""
    return {
        f"{directory.name}/{path.name}": (path.read_bytes(), ASSET_MEDIA_TYPES[path.suffix])
        for path in sorted(directory.glob("*"))
        if path.is_file() and path.suffix in ASSET_MEDIA_TYPES
    }


RUM_ENV = {
    "applicationId": "UFO_WEB_RUM_APPLICATION_ID",
    "clientToken": "UFO_WEB_RUM_CLIENT_TOKEN",
    "site": "UFO_WEB_RUM_SITE",
    "env": "UFO_WEB_RUM_ENV",
    "version": "UFO_WEB_RUM_VERSION",
}
RUM_BLOCK = re.compile(r'(<script[^>]*id="rum"[^>]*>)null(</script>)')


def rum_config(environ: Mapping[str, str]) -> dict[str, str] | None:
    """What this deploy tells the page about Datadog RUM, or None where it records nothing.

    One image serves every deploy, so the application, its client token, and the environment the
    sessions are tagged with cannot be built into the bundle — they are read here and written into
    the shell. A deploy that sets none of them records nothing; one that sets some of them is
    refused, since a recording that reaches the wrong application, or none, is found by nobody."""
    held = {field: environ.get(name, "").strip() for field, name in RUM_ENV.items()}
    if not any(held.values()):
        return None
    unset = sorted(RUM_ENV[field] for field, value in held.items() if not value)
    if unset:
        raise RuntimeError(f"the portal records sessions but {', '.join(unset)} is unset")
    return held


def portal_shell(html: str, config: Mapping[str, str] | None) -> str:
    """The shell with this deploy's RUM configuration written into the block the page declares for
    it. `<` is escaped so no value can close the script element early. The block is the page's own
    declaration, so a build that lost it is refused rather than served: the page would go on asking
    for a configuration nothing writes, and record nothing, silently."""
    written = "null" if config is None else json.dumps(config).replace("<", "\\u003c")
    shell, filled = RUM_BLOCK.subn(lambda block: block[1] + written + block[2], html)
    if filled != 1:
        raise RuntimeError(f"the portal page declares no rum block — run `{PORTAL_BUILD}`")
    return shell


STATIC_ASSETS = load_assets(STATIC_DIR / "assets")
STATIC_ETAGS = {
    name: f'"{sha256(body).hexdigest()[:32]}"' for name, (body, _) in STATIC_ASSETS.items()
}
CONTEXT_TAG = re.compile(r"\A\s*<context>.*?</context>\s*", re.S)
INJECTED_CONTEXT = re.compile(r"\s*<injected_context>.*?</injected_context>\s*\Z", re.S)
MESSAGE_REF = re.compile(r"\A\s*<context>\s*message_ref:\s*(?P<ref>[^\n]+)", re.S)
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9._-]+")


async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None:
    """The `SurfaceSpec.identify` the shared fleet calls to scope a request before its handler runs:
    the workspace the bearer claims, or None to reject. The bearer rides the `ufo_session` cookie,
    or — for the one POST that opens a session — the form body (`open_session` binds it into the
    cookie for the requests that follow); never a query parameter, so it stays out of URLs, access
    logs, and browser history. Each fallback keys on the previous credential failing to RESOLVE,
    not merely being absent, so a member whose cookie outlived its bearer's expiry recovers by
    posting a fresh token instead of being locked behind the stale cookie. An unresolved GET of
    the portal page redirects to the deploy's one sign-in page, so the portal offers no second way
    in and nothing of the shell is served to a stranger. Only a urlencoded body is read for the
    token — the type gateway sign-in posts — so an unauthenticated multipart request is
    rejected without its parse ever running. A conversation the arrival names (`?c=<uuid>`) rides
    on to the sign-in page, so the target survives signing in; it is re-parsed as a UUID, so only
    a conversation id ever reaches that redirect. The handler re-verifies the same bearer for the
    member email — workspace here, identity there."""
    cookie = request.cookies.get(SESSION_COOKIE, "")
    workspace = workspace_claim(cookie) if cookie else None
    if (
        workspace is None
        and request.method == "POST"
        and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
    ):
        refused = _framed_length(request, MAX_FORM_BYTES)
        if refused is not None:
            return refused
        form = await _form(request)
        if isinstance(form, Response):
            return form
        posted = form.get(TOKEN_FIELD, "")
        if isinstance(posted, str) and posted.strip():
            workspace = workspace_claim(posted.strip())
    if (
        workspace is None
        and request.method == "GET"
        and request.url.path.rstrip("/") == PORTAL_PATH
    ):
        target = _chat_target(request)
        login = f"{LOGIN_PATH}?{CHAT_TARGET_PARAM}={target}" if target else LOGIN_PATH
        return RedirectResponse(login, status_code=303)
    return workspace


def _chat_target(request: Request) -> UUID | None:
    try:
        return UUID(request.query_params.get(CHAT_TARGET_PARAM, ""))
    except ValueError:
        return None


def _static_response(request: Request) -> Response | None:
    """The stylesheet or module a portal page path names, or None when the path names no declared
    asset. The name indexes a table built at import, so a traversal sequence resolves to no entry
    rather than to a file. A matching `if-none-match` answers 304: the page's assets revalidate on
    every load and transfer only when their content hash changes."""
    name = request.url.path.removeprefix(STATIC_PREFIX)
    asset = STATIC_ASSETS.get(name)
    if asset is None:
        return None
    body, media_type = asset
    return _asset_response(request, body, media_type, STATIC_ETAGS[name])


def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response:
    headers = {"etag": etag, "cache-control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=media_type, headers=headers)


STATIC_STORE_PREFIX = "static/web/"
STORED_ASSET_NAME = re.compile(r"assets/[A-Za-z0-9._-]+")
STORED_ASSETS_MAX = 64

APPS_DIR = Path(__file__).parent / "apps"
APPS_STORE_PREFIX = "apps/"
APPS_SHARED_DIRS = frozenset({"assets"})

_ASSET_PUBLISH: asyncio.Task[None] | None = None
_STORED_ASSETS: OrderedDict[str, tuple[bytes, str, str]] = OrderedDict()


@dataclass(frozen=True)
class AppsBundle:
    """The built app pages as the one tree every unforked workspace is served: every file's bytes
    by tree-relative path, the content digest naming the fleet prefix they publish under, and the
    slugs the tree holds a page for."""

    files: Mapping[str, bytes]
    digest: str
    slugs: frozenset[str]


def load_apps(directory: Path) -> AppsBundle | None:
    """The built app pages, or None where the frontend build left nothing.

    Read whole, once, at import. The digest is `sha256` over the sorted `(path, bytes)` of every
    file, first 16 hex: content derived, so it is identical on every pod, names the fleet key
    prefix `apps/<digest>/`, and folds to the `deploy_generation` the homepage read reports.
    Hashing the tree per portal page load would stall the loop on the same megabytes every time.
    Every file is carried whatever its suffix. The tree is the served bytes — five documents and
    the hashed `assets/` they name — and the ingress that serves it at a frame origin's root holds
    the one media-type table over them, so nothing here decides what a page may name (`.html`, the
    suffix every page's own entry carries, is not in this module's table at all). A slug is a
    top-level directory other than the shared `assets/`.

    The set is what the build emits, which names no dot-prefixed path segment. A file browser
    dropping `.DS_Store` into the output would otherwise join the tree, change the digest, and cost
    a whole republish under a fresh prefix plus a `deploy_generation` every open app page remounts
    on — a deploy's worth of churn from a byte no page names."""
    if not directory.is_dir():
        return None
    files = {
        relative.as_posix(): (directory / relative).read_bytes()
        for relative in sorted(
            path.relative_to(directory) for path in directory.rglob("*") if path.is_file()
        )
        if not any(part.startswith(".") for part in relative.parts)
    }
    if not files:
        return None
    digest = sha256(
        b"".join(f"{path}\x00".encode() + body for path, body in sorted(files.items()))
    ).hexdigest()[:16]
    top = {path.split("/", 1)[0] for path in files if "/" in path}
    return AppsBundle(files=files, digest=digest, slugs=frozenset(top - APPS_SHARED_DIRS))


APPS = load_apps(APPS_DIR)


def apps() -> AppsBundle:
    """The built app pages, or a fault naming the build that writes them. Every reader of the tree
    is answering a request for a page, so an unbuilt deploy has to say which command it skipped
    rather than serve an app the answer that it has no page."""
    if APPS is None:
        raise RuntimeError(f"the app pages are not built — run `{PORTAL_BUILD}`")
    return APPS


async def _publish_assets(blob: BlobStore, apps: AppsBundle) -> None:
    for name, (body, _media_type) in STATIC_ASSETS.items():
        key = STATIC_STORE_PREFIX + name
        if not await blob.exists(key):
            await blob.put(key, body)
    prefix = f"{APPS_STORE_PREFIX}{apps.digest}/"
    published = {entry.key for entry in await blob.list(prefix)}
    for path, body in apps.files.items():
        key = prefix + path
        if key not in published:
            await blob.put(key, body)


def _assets_published(blob: BlobStore, apps: AppsBundle) -> "asyncio.Task[None]":
    """This process's one publish of its built assets into the shared store (RFC 0031): every pod
    writes its own set before it serves its first page, so a hash a page names is in the store
    before any pod is asked for it — the causal order that makes a mixed-version roll harmless.
    Keys carry Vite's content hash, so a key present is a key already correct and is skipped. A
    failed publish fails the page that awaited it and is replaced here, so the next page retries
    rather than serving a reference nothing can answer.

    The apps bundle rides the same publish, under one prefix and so learned by one listing: the
    files it already holds are skipped (content addressed → present is correct) and a tree left
    half-written by an interrupted publish is completed, so a shipped page's bytes are all in the
    store under `apps/<digest>/` before the homepage read hands out a link naming that digest.

    That skip covers a retry, not a redeploy. `static/web/` keys carry a per-file hash, so an
    unchanged chunk keeps its key across builds and is written once ever; `apps/<digest>/` is one
    digest over the whole tree, so a byte changed anywhere moves the prefix and every file under it
    is a first write. A deploy that touches one app page writes the whole tree again — 160 files,
    6,888,632 bytes (6.6 MiB: 4.4 MiB of hashed `.js`, 1.4 MiB of mark sprites, 465 KiB of fonts,
    339 KiB of images, 154 KiB of stylesheet) — and the prefixes before it stay. They have to: a
    rolling deploy's outgoing pods serve theirs until they are gone, and an app page a member has
    open names the digest it was minted against. 6.6 MiB of fleet store per deploy, kept, is the
    price of a tree addressed by its content."""
    global _ASSET_PUBLISH
    task = _ASSET_PUBLISH
    if task is None or (task.done() and task.exception() is not None):
        task = asyncio.create_task(_publish_assets(blob, apps))
        _ASSET_PUBLISH = task
    return task


async def _stored_asset(blob: BlobStore, request: Request) -> Response:
    """An asset of another build, served from the shared store: the answer for a page minted by a
    pod on a different build than this one (RFC 0031). Only a name shaped like this surface's own
    publishes is looked up — one leaf under `assets/` with a declared suffix — and a store hit is
    held in a bounded process dictionary of immutable entries, then served exactly as a local
    asset. A store miss stays the 404 it always was: an asset that never existed."""
    name = request.url.path.removeprefix(STATIC_PREFIX)
    if STORED_ASSET_NAME.fullmatch(name) is None:
        return Response("no such asset", status_code=404)
    media_type = ASSET_MEDIA_TYPES.get(Path(name).suffix)
    if media_type is None:
        return Response("no such asset", status_code=404)
    held = _STORED_ASSETS.get(name)
    if held is None:
        key = STATIC_STORE_PREFIX + name
        if not await blob.exists(key):
            return Response("no such asset", status_code=404)
        body = await blob.get(key)
        held = (body, media_type, f'"{sha256(body).hexdigest()[:32]}"')
        _STORED_ASSETS[name] = held
        while len(_STORED_ASSETS) > STORED_ASSETS_MAX:
            _STORED_ASSETS.popitem(last=False)
    return _asset_response(request, *held)


async def portal_page(ctx: SurfaceContext, request: Request) -> Response:
    """Serve the portal shell to a request whose session resolved. A session that expires while the
    page is open leaves the shell asking `api/agents`, which answers 401; the page sends the member
    to the same sign-in page an unresolved arrival is redirected to. A deploy that skipped the
    frontend build fails here, naming the command, rather than at import — an unbuilt tree still
    loads the extension, so every route that holds no built asset keeps working and the fault reads
    as what it is.

    The shell is `no-store`: it is the one document naming which build to load, and every asset it
    names carries a content hash. Cached, it would go on naming a build that is no longer there —
    the member reloads, the deploy they were told about is missing, and nothing in the page says
    why. Its assets revalidate and transfer only on a hash change, so the shell costs one request
    and no page is ever stale while looking current."""
    if PORTAL_HTML is None or APPS is None:
        raise RuntimeError(f"portal app is not built — run `{PORTAL_BUILD}`")
    await _assets_published(ctx.fleet_blob, APPS)
    shell = portal_shell(PORTAL_HTML, rum_config(os.environ))
    return HTMLResponse(shell, headers={"cache-control": "no-store"})


async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response:
    """The member and email a request's session cookie authenticates, or the 401 that names which
    of the two refusals happened: no bearer to read — absent, forged, or expired — which signing in
    again fixes, or a live bearer whose email holds no member row in this workspace, which it does
    not. The second carries `SESSION_FAULT_HEADER`, so the page states the cause instead of
    advising a sign-in that cannot change it. The email is the web `surface_identity`, linked to
    (or created as) a member on first contact."""
    token = request.cookies.get(SESSION_COOKIE, "")
    email = verify_token(token, ctx.workspace_id) if token else None
    if email is None:
        return Response("missing or unknown session cookie", status_code=401)
    member_id = await ctx.linked_member(email) or await ctx.link_member(email, email)
    if member_id is None:
        return Response(
            "no member with this email in this workspace",
            status_code=401,
            headers={SESSION_FAULT_HEADER: NO_MEMBER_FAULT},
        )
    return member_id, email


async def static_asset(ctx: SurfaceContext, request: Request) -> Response:
    """Serve a portal stylesheet or module to a request whose session resolved. Only the shell
    names these, and the shell serves to a session, so an unresolved request is a 401 rather than
    a transfer. The assets carry no workspace data. A name this build does not hold is answered
    from the shared store, where every pod published its own build before serving pages — so a
    page from one build resolves on a pod running another."""
    return _static_response(request) or await _stored_asset(ctx.fleet_blob, request)


async def open_session(ctx: SurfaceContext, request: Request) -> Response:
    """Open a session: land the POSTed bearer as the session cookie and redirect into the portal.
    The token crosses only in the form body — never a URL. The identify resolver has verified this
    form token whenever it is what scoped the request; behind a cookie that still resolves the form
    goes unread and the new bearer lands unverified, which changes nothing, because the cookie is
    verified again on every request that follows (`resolve_workspace`, then `_authenticate`) and one
    that verifies against nothing authenticates nobody. The shape check is transport, not
    authentication: nothing outside the bearer alphabet can BE a bearer, and the worst of it
    (control characters, non-latin-1) would raise inside the cookie writer, so it answers 400
    before a header is built. The cookie is `lax`, not `strict`, because arrival IS a cross-site
    navigation (gateway sign-in posts here) and the redirected GET must already
    carry it."""
    refused = _framed_length(request, MAX_FORM_BYTES)
    if refused is not None:
        return refused
    form = await _form(request)
    if isinstance(form, Response):
        return form
    posted = form.get(TOKEN_FIELD, "")
    if not isinstance(posted, str) or not posted.strip():
        return JSONResponse({"error": "token form field is required"}, status_code=400)
    if not TOKEN_SHAPE.fullmatch(posted.strip()):
        return JSONResponse({"error": "malformed token"}, status_code=400)
    response = RedirectResponse(str(request.url), status_code=303)
    set_session_cookie(
        response,
        SESSION_COOKIE,
        posted.strip(),
        samesite="lax",
        secure=ctx.cookie_secure,
    )
    return response


def _agent_param(request: Request) -> UUID | None:
    try:
        return UUID(request.path_params["agent_id"])
    except ValueError:
        return None


def _chat_row_key(conversation_id: UUID) -> str:
    return f"{CHAT_STORE_PREFIX}{conversation_id}"


TITLE_DANGLERS = frozenset(
    "a an and are as at be but by for if in is it its my of on or our so that the their then "
    "this to we what when with you your".split()
)


def _chat_title(text: str, paths: tuple[str, ...]) -> str:
    """A conversation's rail label, cut from its first message at a word boundary — or from the
    attached filenames when the message is files alone. A cut title sheds trailing punctuation
    and dangling connectives ("…what files you see, then" ends at "see"); the rail's own overflow
    ellipsis marks any further cut, so the stored title carries none."""
    collapsed = " ".join(text.split())
    if not collapsed:
        collapsed = ", ".join(path.removeprefix(f"{WEB_INBOX_DIR}/") for path in paths)
    if len(collapsed) <= MAX_CHAT_TITLE_CHARS:
        return collapsed
    cut = collapsed[:MAX_CHAT_TITLE_CHARS]
    words = cut.split(" ")[:-1] or [cut]
    while words:
        trimmed = words[-1].rstrip(".,;:!?—-")
        if (trimmed and trimmed.lower() not in TITLE_DANGLERS) or len(words) == 1:
            words[-1] = trimmed or words[-1]
            break
        words.pop()
    return " ".join(words)


class ChatRecord(BaseModel):
    """One conversation this surface opened, as its store row persists it: the (agent, member)
    binding the chat gate checks. Validated at construction — a row that fails to parse is a fault,
    never a silent "no chat"."""

    agent_id: UUID
    email: str


def _title_excerpt(messages: tuple[Message, ...]) -> str:
    """The opening exchange the title is written from — the first user and first assistant texts,
    each bounded, joined. The member's side is their own words out of the fence, so a thread a
    channel surface opened is named over what the member said rather than over the ambient digest
    and markup wrapped around it. Empty where no assistant message stands: a transcript whose turns
    all failed holds nothing a title can be written from, and the conversation keeps the name its
    opening words gave it."""
    if all(message.role != "assistant" for message in messages):
        return ""
    parts = []
    for role in ("user", "assistant"):
        spoken = next((message for message in messages if message.role == role), None)
        if spoken is None:
            continue
        text = _rendered_text(spoken)
        said = member_message_text(text).strip() if role == "user" else text
        if said:
            parts.append(said[:TITLE_EXCERPT_CHARS])
    return "\n\n".join(parts)


async def summarize_chat_titles(ctx: ExtensionContext) -> None:
    """Name each conversation a member spoke in from its opening exchange — the batch job behind
    the rail's summary titles, naming the conversation over the opening words the turn that opened
    it named it with. Every surface's conversations, not the portal's alone: a Slack thread and a
    CLI session are read here the same way a portal chat is, so one rail row is named like the row
    beside it.

    Core's candidate read is the whole state machine: it answers the conversations a member spoke in
    and an agent answered that no summary has named yet, newest first, and the write that names one
    records the summary as run. Every candidate a tick takes is recorded — an exchange the model
    names nothing usable for, and one whose transcript states no answer at all, keep the name their
    opening words gave them and are recorded all the same. So one conversation costs one summary,
    and a backlog drains rather than standing behind a batch of conversations nothing can name.

    The transcripts read are the batch's own, so a conversation reaches its title however long the
    history in front of it is. One that has not landed yet is read again on the next tick: the run
    that ends a turn writes the transcript just after it commits the turn, so a candidate taken
    inside that moment is a conversation whose exchange is coming, not one nothing can name."""
    awaiting = await ctx.conversations_awaiting_title(TITLE_BATCH)
    if not awaiting:
        return
    if ctx.corpus is None or ctx.model is None:
        raise RuntimeError("chat titles need trajectory and model access; serve wires both")
    trajectories = {t.conversation_id: t for t in await ctx.corpus.conversations(awaiting)}
    for conversation_id in awaiting:
        trajectory = trajectories.get(conversation_id)
        if trajectory is None:
            continue
        excerpt = _title_excerpt(trajectory.messages)
        summary = (
            ""
            if not excerpt
            else _chat_title(
                await ctx.model.complete(
                    ModelRequest(
                        model=ctx.model.model,
                        system=TITLE_SYSTEM_PROMPT,
                        messages=(Message(role="user", content=excerpt),),
                        max_tokens=TITLE_MAX_TOKENS,
                        conversation_cache_ttl="5m",
                        reasoning="off",
                    )
                ),
                (),
            )
        )
        await ctx.summarized_conversation_title(conversation_id, summary)


async def seed_homepages(ctx: ExtensionContext, bucket: str | None = None) -> None:
    """One homepage-build turn per agent, ever — the batch job behind the Home tab's first fill.
    The marker alone decides, so the sweep cannot fire on rows it caused and the fleet's existing
    agents seed through the same sweep. The marker is written only for a turn admission accepted:
    a refusal — a breached cap, an unseated on-behalf member — leaves the agent unmarked, and the
    day-bucketed idempotency key retries it tomorrow. The turn rides on behalf of the agent's
    owner — the earliest-seated admin for an ownerless row — because a deploy needs an acting
    member, and it runs in that member's own room, the shape every on-behalf invocation takes, so
    the authority it carries stays inside a room its member already reads. The homepage answers
    the agent's audience from birth: the frame gates a bound site on the agent's visibility, so a
    workspace-visible agent's homepage (main is born one) reaches every member at once, a private
    agent's reaches its owner and admins, and flipping the agent object's visibility is what
    widens the page — no site row is rewritten.
    A shipped app agent (one an `app_*` extension provisioned) is marked settled without a turn:
    its homepage is the deploy-wide bundle served row-less, so it needs no build. Marking it rather
    than skipping it is what lets the candidate query settle — an unmarked agent it never builds
    would keep the workspace due forever.
    An agent whose allowlist withholds the site tools is marked
    settled rather than handed a turn it cannot finish — chat is its recovery if the allowlist
    grows — an archived app is marked for the same reason, since it admits no turn at all, and an
    ownerless agent in a workspace with no seated admin waits, unmarked, for one.
    The candidates gate on due work: a workspace whose agents are all marked never fires this
    handler, so the settled fleet costs nothing. `bucket` is the day the idempotency key names — a
    refused admission is a durable turn its key would answer forever, so a refusal costs at most
    one bucket's attempt while a crash between admitting and marking still dedupes to the turn
    already admitted."""
    bucket = bucket or datetime.now(UTC).date().isoformat()
    agents = await ctx.workspace_agents()
    if not agents:
        return
    marked = {key for key, _ in await ctx.store.list(HOMEPAGE_SEED_PREFIX)}
    admin_resolved = False
    admin: UUID | None = None
    for agent in agents:
        key = f"{HOMEPAGE_SEED_PREFIX}{agent.id}"
        if key in marked:
            continue
        if agent.archived:
            await ctx.store.put(key, "archived")
            continue
        if shipped_app_slug(agent.provisioned_by) is not None:
            await ctx.store.put(key, "shipped")
            continue
        if agent.tools is not None and not set(HOMEPAGE_TOOLS) <= set(agent.tools):
            await ctx.store.put(key, "withheld-tools")
            continue
        acting = agent.owner_member_id
        if acting is None:
            if not admin_resolved:
                admin = await ctx.earliest_seated_admin()
                admin_resolved = True
            acting = admin
        if acting is None:
            continue
        conversation_id = await ctx.open_conversation(
            agent.id, f"homepage/{agent.id}/{acting}", member_id=acting
        )
        turn_id = await ctx.invoke(
            conversation_id,
            agent.id,
            SEED_PROMPT,
            f"homepage-seed:{agent.id}:{bucket}",
            on_behalf_of_member_id=acting,
            as_scheduled=True,
        )
        if turn_id is None:
            raise RuntimeError(f"homepage seed for agent {agent.id} answered no turn")
        outcomes = await ctx.turn_outcomes((turn_id,))
        outcome = outcomes.get(turn_id)
        if outcome is not None and outcome.status == "cancelled":
            continue
        await ctx.store.put(key, str(acting))


async def _open_conversation(
    ctx: SurfaceContext,
    store: ScopedStore,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    queue_key: str,
    text: str,
    paths: tuple[str, ...],
) -> tuple[UUID, str]:
    """Open a conversation under `queue_key`, its chat row written first, keyed by the id the
    conversation is then created with — a crash between the two leaves an inert row, never a
    conversation the rail must carry rowless. A lost creation race on the queue key lands on the
    surviving conversation, whose winner wrote its row and named it.

    What the conversation is called is core's, so the opening turn names it and the rail, the index
    and this reply all read the one string. The chat row is the (agent, member) binding this
    surface gates its own chat on, and holds nothing a listing states."""
    title = _chat_title(text, paths)
    minted = uuid4()
    await store.put(
        _chat_row_key(minted),
        ChatRecord(agent_id=agent_id, email=email).model_dump(mode="json"),
    )
    conversation_id = await ctx.conversation_for(
        queue_key, conversation_audience(member_id), agent_id=agent_id, conversation_id=minted
    )
    if conversation_id != minted:
        await store.delete(_chat_row_key(minted))
        if await _own_web_chat(store, agent_id, email, conversation_id) is None:
            raise RuntimeError(f"conversation {conversation_id} has no chat row")
        return conversation_id, await _named(ctx, agent_id, member_id, conversation_id)
    await ctx.retitle_conversation(conversation_id, title)
    return conversation_id, title


async def _named(
    ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID
) -> str:
    """What one conversation is called, off the read every listing takes it from."""
    listed = await ctx.list_agent_conversations(
        agent_id, member_id, admin=False, limit=1, conversation_id=conversation_id
    )
    return listed[0].title if listed else ""


async def _own_web_chat(
    store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID
) -> ChatRecord | None:
    """The requested conversation's chat record, when it is this member's own chat with this
    agent. Anything else — another member's, another agent's, a room's, an unknown id — is None,
    and every caller answers not-found."""
    value = await store.get(_chat_row_key(conversation_id))
    if value is None:
        return None
    record = ChatRecord.model_validate(value)
    if record.agent_id != agent_id or record.email != email:
        return None
    return record


async def _member_chat(
    ctx: SurfaceContext,
    store: ScopedStore,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    conversation_id: UUID,
    *,
    agent_visible: bool,
) -> ListedConversation | None:
    """One conversation this member may continue through the portal. A member-private extension
    grants only itself; Slack and terminal comments require the agent's ordinary web reach.

    A room the portal opened for this member is theirs to read, and theirs to speak in where the app
    answers there. It carries no chat row — that row is the (agent, member) binding a chat is
    founded with, and a room is opened by a press instead — so the durable audience is what says
    whose it is: `conversation_for` names exactly this member, and nothing else can widen it
    afterwards. Without it a member who pressed Build app, or armed a schedule, was handed a link to
    the room their own press opened and met a 404 on it.

    The prepared-intent lane is not covered, by its key. An intent turn dispatches its one tool call
    and runs no model round, so it claims no arrivals — a message folded onto a live one is a
    message no round ever reads. It is read like any other room; it is the chat POST that is
    refused."""
    web = await _own_web_chat(store, agent_id, email, conversation_id)
    listed = await ctx.list_agent_conversations(
        agent_id, member_id, admin=False, limit=1, conversation_id=conversation_id
    )
    if not listed:
        return None
    conversation = listed[0]
    if web is not None:
        return conversation
    own = conversation.audience == str(conversation_audience(member_id))
    if own and conversation.summary.surface.startswith("extension:"):
        return conversation
    if (
        own
        and agent_visible
        and conversation.summary.surface == SURFACE_WEB
        and conversation.summary.queue_key.startswith(SPOKEN_ROOM_PREFIXES)
    ):
        return conversation
    if agent_visible and _commentable(conversation, member_id):
        return conversation
    return None


def _commentable(conversation: ListedConversation, member_id: UUID) -> bool:
    return conversation.summary.surface in COMMENT_SURFACES and conversation.audience in {
        str(SHARED_AUDIENCE),
        str(conversation_audience(member_id)),
    }


def _turn_context(email: str, request: Request, source: str) -> TurnContext:
    """The admitted turn's ambient context: the member as sender, the zone their browser reported
    so the turn's time reads as the member's own, and where they said it; a reported zone that is
    not a known IANA name is dropped with a log rather than failing the member's message."""
    zone = request.headers.get(TIMEZONE_HEADER, "").strip()
    if not zone:
        return TurnContext(sender=email, source=source)
    try:
        return TurnContext(sender=email, timezone=zone, source=source)
    except ValidationError:
        log("web.timezone_dropped", zone=zone)
        return TurnContext(sender=email, source=source)


def _chat_url(public_base_url: str | None, conversation_id: UUID) -> str | None:
    if not public_base_url:
        return None
    return f"{public_base_url.rstrip('/')}{PORTAL_PATH}#/c/{conversation_id}"


def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str:
    """Where a portal message was said, as the agent carries it into anything it creates: the
    portal URL that opens this conversation, plus who asked. The portal routes on the fragment
    (`#/c/<id>`), so the link lands on the conversation rather than the shell. A deploy whose
    public base is unset or empty has no address to give, and names the client and the member
    instead."""
    url = _chat_url(public_base_url, conversation_id)
    if url is None:
        return f"{SOURCE} ({email})"
    return f"{url} ({email})"


def _comment_notice(
    public_base_url: str | None,
    conversation: ListedConversation,
    member_id: UUID,
    email: str,
    text: str,
    paths: tuple[str, ...],
) -> str:
    if conversation.audience == str(conversation_audience(member_id)):
        author = "You"
    else:
        speaker = next((who for who in conversation.speakers if who.email == email), None)
        sender = None if speaker is None else speaker.sender
        author = email if sender is None else sender.removesuffix(f" ({email})")
    url = _chat_url(public_base_url, conversation.summary.id)
    verb = "commented" if url is None else f"[commented]({url})"
    message = text.strip()
    if paths:
        attached = ", ".join(PurePosixPath(path).name for path in paths)
        message = f"{message}\n\nAttached: {attached}" if message else f"Attached: {attached}"
    return f"{author} {verb}: {message}"


async def _audience_for(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, str, WebAudience] | Response:
    auth = await _authenticate(ctx, request)
    if isinstance(auth, Response):
        return auth
    member_id, email = auth
    return member_id, email, await web_audience(ctx, web_extension(), email)


async def agents_index(ctx: SurfaceContext, request: Request) -> Response:
    """The portal's first read: the signed-in member and the agents their web audience holds — every
    agent for a workspace admin, the main agent plus the granted non-main agents for everyone else.
    `agents` is the set a member may open and message. The create act draws nothing from this read:
    it is a conversation the `create-application` skill runs, and the screen offers it to every
    signed-in member, because the `agent` kind admits a create from any speaking member and stamps
    them the owner. `archived` contains the apps this member may restore."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    grants = None
    if audience.admin:
        grants = await granted_emails(web_extension().store)
    # The agent carries its homepage — an app's own page or the answer it has none — so a screen
    # opens the page from the boot read and never asks per agent as the member moves between them.
    await _assets_published(ctx.fleet_blob, apps())
    homepages = {
        agent.id: await _homepage_state(ctx, agent, member_id, audience.admin)
        for agent in audience.agents
    }
    archived = [
        app
        for app in await ctx.list_archived_agents()
        if audience.admin or app.owner_member_id == member_id
    ]
    return JSONResponse(
        {
            "member": {"email": email, "admin": audience.admin},
            "archived": [
                {
                    "id": str(app.id),
                    "name": app.name,
                    "icon": app.icon,
                    "archived_at": app.archived_at.isoformat(),
                }
                for app in archived
            ],
            "agents": [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "main": agent.main,
                    "model": agent.model,
                    "icon": agent.icon,
                    "purpose": agent.purpose,
                    "app": shipped_app_slug(agent.provisioned_by),
                    "homepage": homepages[agent.id],
                    **(
                        {"web_audience": list(grants.get(agent.id, ()))}
                        if grants is not None
                        else {}
                    ),
                }
                for agent in audience.agents
            ],
        }
    )


def _activity_label(frame: ToolCall | SkillLoad | None) -> str | None:
    match frame:
        case None:
            return None
        case SkillLoad():
            return f"Loading skill · {frame.skill}"
        case ToolCall():
            return frame.description or (
                f"{frame.tool} {frame.preview}" if frame.preview else frame.tool
            )


async def agents_status(ctx: SurfaceContext, request: Request) -> Response:
    """Each visible agent's live picture, polled beside the index `agents_index` serves: the
    liveest non-terminal turn it holds — with what a running one is doing right now, peeked off
    the hub's newest activity frame — when any turn of its last moved, whether its most recent
    terminal turn failed, and the soonest unpaused scheduled task on it — read through the task
    kind's own member gate, never its table.

    An agent is visible workspace-wide; its turns are not. The aggregate is fenced to the
    conversations this reader reads, so a row reports this member's picture of the agent and never
    another member's private turn.

    The turn aggregate is one read for every agent at once; the two per-agent reads are taken only
    where the row is drawn from them. A running turn's hub is peeked for the agents running one,
    and an agent's tasks are listed only where no turn state stands in front of them — so an agent
    at work costs the object store nothing, and a screen pays that read only for the agents
    standing still."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    statuses = await ctx.agent_turn_statuses(
        tuple(agent.id for agent in audience.agents), member_id
    )
    activity: dict[UUID, str] = {}
    for status in statuses:
        if status.running_turn_id is None:
            continue
        label = _activity_label(await ctx.latest_activity(status.running_turn_id))
        if label:
            activity[status.agent_id] = label
    next_runs: dict[UUID, str] = {}
    for status in statuses:
        if status.live is not None or status.last_failed:
            continue
        page = await ctx.list_member_objects(
            SCHEDULED_TASK_KIND,
            status.agent_id,
            member_id,
            admin=audience.admin,
            query=ObjectListQuery(filters={"paused": False}, order_by="next_run_at"),
        )
        if page is None:
            break
        if not page.rows:
            continue
        soonest = page.rows[0].fields["next_run_at"]
        if isinstance(soonest, str):
            next_runs[status.agent_id] = soonest
    return JSONResponse(
        {
            "statuses": [
                {
                    "agent_id": str(status.agent_id),
                    "turn": status.live,
                    "activity": activity.get(status.agent_id),
                    "next_run_at": next_runs.get(status.agent_id),
                    "last_active_at": _iso(status.last_active_at),
                    "last_failed": status.last_failed,
                }
                for status in statuses
            ]
        }
    )


def _framed_length(request: Request, limit: int) -> Response | None:
    """The refusal a whole-body parse must answer before it runs, or None when the request frames
    its body honestly. A chunked body carries no length a parse can be bounded by — RFC 7230 makes
    any accompanying Content-Length a lie, and the server frames by the chunks — so a request this
    route must parse whole (a form) is refused unless it declares a length under the limit and is
    not chunked."""
    if "chunked" in request.headers.get("transfer-encoding", "").lower():
        return Response("length required", status_code=411)
    declared = request.headers.get("content-length", "").strip()
    if not declared.isdigit():
        return Response("length required", status_code=411)
    if int(declared) > limit:
        return Response("request too large", status_code=413)
    return None


async def _form(request: Request) -> FormData | Response:
    """The request's parsed form, or the 400 a malformed body earns. python-multipart's parse
    errors escape `request.form()` — starlette converts only its own `MultiPartException` — and
    they are the client's malformed body, refused like every other malformed shape here."""
    try:
        return await request.form()
    except FormParserError:
        return Response("malformed form body", status_code=400)


async def _bounded_body(request: Request, limit: int) -> bytes | Response:
    """The request body under a hard byte cap: the read stops at the cap, so a mis-declared or
    chunked length cannot outgrow it — the bound is what was actually consumed, never a header."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return Response("request too large", status_code=413)
    return bytes(body)


async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...]] | Response:
    """The composer's message text and attached files. A plain body is read under a hard byte
    cap, so what bounds it is the bytes consumed rather than a declared length, and it must decode
    as UTF-8 — bytes that don't are refused, never rewritten. A multipart submit must declare a
    length and must not be chunked — the parse buffers each part whole (in memory up to
    starlette's spool threshold, a temp file past it), so it runs only under a length the server
    itself frames the body by; its `message` text arrives already decoded by that parser (UTF-8,
    falling back to latin-1), so the strict-UTF-8 refusal is the plain path's — the decoded text
    is admitted as received. A urlencoded body is not a shape the composer sends, so it is
    refused."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded"):
        return Response("unsupported body type", status_code=415)
    if not content_type.startswith("multipart/form-data"):
        body = await _bounded_body(request, MAX_INBOUND_BYTES)
        if isinstance(body, Response):
            return body
        try:
            return body.decode("utf-8"), ()
        except UnicodeDecodeError:
            return Response("malformed message text", status_code=400)
    refused = _framed_length(request, MAX_REQUEST_BYTES)
    if refused is not None:
        return refused
    form = await _form(request)
    if isinstance(form, Response):
        return form
    message = form.get("message", "")
    if not isinstance(message, str):
        return Response("malformed message part", status_code=400)
    text = message
    uploads = tuple(
        upload
        for upload in form.getlist("file")
        if isinstance(upload, UploadFile) and upload.filename
    )
    return text, uploads


def _inbox_paths(uploads: tuple[UploadFile, ...]) -> tuple[str, ...]:
    used: set[str] = set()
    return tuple(
        f"{WEB_INBOX_DIR}/{inbox_name(upload.filename or 'file', used)}" for upload in uploads
    )


async def _deliver_uploads(
    ctx: SurfaceContext,
    conversation_id: UUID,
    uploads: tuple[UploadFile, ...],
    paths: tuple[str, ...],
) -> None:
    """Stream each attached file into the conversation's `web-inbox/` before the turn runs, so the
    sandbox mounts them already present under the paths the admitted text names."""
    for upload, path in zip(uploads, paths, strict=True):
        await ctx.write_workspace_file(conversation_id, path, _upload_chunks(upload))


def _files_note(text: str, paths: tuple[str, ...]) -> str:
    """The admitted text naming the saved paths it carries."""
    note = FILES_NOTE.format(paths=", ".join(paths))
    return f"{text}\n\n{note}" if text.strip() else note


Attach = Callable[[str], str | None]
"""Where a projection gets the picture of one attached workspace path, or None for a file the
portal has no picture of."""


def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]:
    """The member's own words and the workspace paths the note at their foot names — the reader of
    `_files_note`, so a bubble draws what was attached as the files themselves rather than as the
    sentence naming where they landed. Words carrying no note are their own."""
    found = FILES_NOTE_RE.search(said)
    if found is None:
        return said, ()
    paths = tuple(path for path in (part.strip() for part in found["paths"].split(",")) if path)
    return said[: found.start()].rstrip(), paths


def _attachment_preview(
    public_base_url: str | None, agent_id: UUID, conversation_id: UUID, path: str
) -> str | None:
    """The link the chat draws one attached file's picture from, or None for a type the attachment
    route does not serve and for a deploy that names no public base. The link carries that base
    because an app page draws the chat framed on its own origin, where a picture named without one
    resolves against the site rather than the route serving it."""
    if raster_image_media_type(path) is None or not public_base_url:
        return None
    return (
        f"{public_base_url.rstrip('/')}{PORTAL_PATH}"
        f"/agents/{agent_id}/conversations/{conversation_id}/attachments/{quote(path)}"
    )


def _attachment_payload(path: str, preview_url: str | None) -> dict[str, object]:
    """One file the member attached, as the chat draws it. The bytes live in the conversation's
    workspace rather than the artifact store, so the payload names no download link — a member's own
    attachment is a file they already hold — and a type with no picture of its own draws as a card
    naming it. `media_type` is how the portal knows a PDF card wears a PDF badge."""
    media_type = raster_image_media_type(path) or ATTACHMENT_MEDIA_TYPES.get(
        PurePosixPath(path).suffix.lower(), ATTACHMENT_FALLBACK_MEDIA_TYPE
    )
    return {
        "filename": PurePosixPath(path).name,
        "url": None,
        "media_type": media_type,
        "preview_url": preview_url,
    }


def _member_bubble(said: str, attach: Attach | None) -> dict[str, object]:
    """One bubble of the member's own words, carrying what they attached to them as files."""
    words, paths = _member_attachments(said)
    bubble: dict[str, object] = {"role": "user", "text": words}
    if paths:
        bubble["files"] = [
            _attachment_payload(path, None if attach is None else attach(path)) for path in paths
        ]
    return bubble


async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
        yield chunk


def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str:
    """The key one answer admits under: the conversation it was said in, the turn that asked, and
    the question's place in that ask. Admission and the transcript projection both name an answer
    with this, so a message is recognized as the answer to one question by the key it landed under
    rather than by reading its words."""
    return f"{conversation_id}:{turn_id}:answer:{index}"


def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response:
    """The question an answer click names — validated before any conversation is opened, so a
    malformed answer leaves nothing behind — or None for an ordinary message. The answer admits
    under a per-question idempotency key, so a double click or a second tab joins the turn the
    first click won."""
    answer_turn = request.headers.get(ANSWER_TURN_HEADER, "").strip()
    if not answer_turn:
        return None
    raw_index = request.headers.get(ANSWER_QUESTION_HEADER, "0").strip()
    try:
        return UUID(answer_turn), int(raw_index)
    except ValueError:
        return Response("malformed answer headers", status_code=400)


def _stop_header(request: Request) -> UUID | None | Response:
    """The turn a stop press names — validated before a body is read or a conversation opened, so a
    malformed press leaves nothing behind — or None for an ordinary message."""
    named = request.headers.get(STOP_TURN_HEADER, "").strip()
    if not named:
        return None
    try:
        return UUID(named)
    except ValueError:
        return Response(f"{STOP_TURN_HEADER} must be a turn id", status_code=400)


async def chat(ctx: SurfaceContext, request: Request) -> Response:
    """Admit one member message. The `conversation` query parameter continues that conversation —
    gated to the member's own portal or private-extension chat, or a Slack/terminal conversation
    shared with them — and the `new` sentinel opens a fresh one: the chat POST is the chat
    transport, so opening a conversation rides the first message rather than a separate mutation,
    and the response names the conversation it landed in.

    A message sent while a turn is still running joins that turn instead of founding one, and the
    response says so by naming the `arrival_id` the turn's `absorbed` event will carry — the id the
    page holds its wait against. `opened_run` is the other half, and the one the page decides on:
    admission's own answer, taken under the conversation-row lock, to whether this delivery opened
    the run the named turn belongs to. False beside an `arrival_id` is the single outcome whose live
    frames a tail already carries, so the page leaves that tail alone rather than opening a second
    stream on one turn. Every other outcome is the page's to tail, the refusals included: a
    seat-refused or cap-refused message founds a turn of its own carrying its own terminal, and the
    stream replays it.

    An `x-ufo-stop-turn` header over an empty body is the member ending a turn of this conversation
    rather than saying anything into it: nothing is admitted, so the transcript never mentions the
    press, and the cancelled terminal the stop publishes is what the member's live tail ends on."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows_chat(agent_id):
        return Response("no such agent", status_code=404)
    stop = _stop_header(request)
    if isinstance(stop, Response):
        return stop
    parsed = await _parse_inbound(request)
    if isinstance(parsed, Response):
        return parsed
    text, uploads = parsed
    if stop is not None:
        if text or uploads:
            return Response("a stop admits no message", status_code=400)
    elif not text.strip() and not uploads:
        return Response("empty message", status_code=400)
    paths = _inbox_paths(uploads)
    inbound = _files_note(text, paths) if paths else text
    if len(inbound) > MAX_INBOUND_CHARS:
        return Response(f"message exceeds {MAX_INBOUND_CHARS} characters", status_code=413)
    answer = _answer_headers(request)
    if isinstance(answer, Response):
        return answer
    store = web_extension().store
    requested = request.query_params.get("conversation", "").strip()
    if not requested:
        return Response("conversation is required", status_code=400)
    comment = None
    if requested == NEW_CONVERSATION:
        if not audience.allows(agent_id):
            return Response("no such agent", status_code=404)
        if answer is not None:
            return Response("an answer names the conversation it was asked in", status_code=400)
        if stop is not None:
            return Response("a stop names the conversation its turn runs in", status_code=400)
        conversation_id, title = await _open_conversation(
            ctx, store, agent_id, member_id, email, f"{agent_id}/{email}/{uuid4().hex}", text, paths
        )
    else:
        try:
            conversation_id = UUID(requested)
        except ValueError:
            return Response("no such conversation", status_code=404)
        conversation = await _member_chat(
            ctx,
            store,
            agent_id,
            member_id,
            email,
            conversation_id,
            agent_visible=audience.allows(agent_id),
        )
        if conversation is None:
            return Response("no such conversation", status_code=404)
        title = conversation.title
        if _commentable(conversation, member_id):
            comment = _comment_notice(
                ctx.public_base_url, conversation, member_id, email, text, paths
            )
    if stop is not None:
        authorized = await _member_turn(ctx, request, named_turn=stop)
        if isinstance(authorized, Response):
            return authorized
        try:
            stopped = await ctx.stop_turn(conversation_id, stop)
        except ValueError:
            return Response("no such turn in this conversation", status_code=404)
        outcome: dict[str, bool | str] = {"stopped": stopped.ended}
        if stopped.founded_turn_id is not None:
            outcome["turn_id"] = str(stopped.founded_turn_id)
        return JSONResponse(outcome)
    key = None if answer is None else _answer_key(conversation_id, answer[0], answer[1])
    await _deliver_uploads(ctx, conversation_id, uploads, paths)
    admitted = await ctx.admit(
        conversation_id,
        inbound,
        context=_turn_context(
            email, request, _chat_source(ctx.public_base_url, conversation_id, email)
        ),
        idempotency_key=key,
        speaker_member_id=member_id,
        comment=comment,
    )
    payload: dict[str, str | bool | None] = {
        "turn_id": str(admitted.turn_id),
        "conversation_id": str(conversation_id),
        "title": title,
        "opened_run": admitted.opened_run,
    }
    if admitted.arrival_id is not None:
        payload["arrival_id"] = str(admitted.arrival_id)
    if key is not None:
        payload["body"] = await ctx.admitted_body(key)
    return JSONResponse(payload)


def _rendered_text(message: Message) -> str:
    match message.content:
        case str() as text:
            rendered = text
        case blocks:
            rendered = "\n".join(
                block.text for block in blocks if isinstance(block, TextBlock) and block.text
            )
    if message.role == "user":
        rendered = CONTEXT_TAG.sub("", rendered, count=1)
        rendered = INJECTED_CONTEXT.sub("", rendered, count=1)
    return rendered.strip()


def _tool_event(block: ToolUseBlock) -> dict[str, str]:
    visible = block.model_copy(
        update={"input": {key: value for key, value in block.input.items() if key != REQUESTED_BY}}
    )
    match tool_activity(visible):
        case SkillLoad(skill=skill):
            return {"kind": "skill", "name": skill, "preview": "", "description": ""}
        case ToolCall(tool=name, preview=preview, description=description):
            return {
                "kind": "tool",
                "name": name,
                "preview": preview,
                "description": description,
            }


class SubagentNode(TypedDict):
    """One spawned run as the conversation shows it: the display name its spawn gave it (empty
    when it gave none — the row states the target then), the qualified target (`agent:<name>` for
    an agent child, the bare profile otherwise), the conversation that holds the whole record,
    the work it did, what it answered, and the runs it spawned in turn. A profile run links to
    its own conversation page; an agent run has no such page — the portal derives that from the
    target's prefix, shows its work inline, and mints no link."""

    profile: str
    name: str
    conversation_id: str
    events: list[dict[str, str]]
    output: str
    subagents: list["SubagentNode"]


SubagentRuns = dict[str, list[SubagentNode]]


def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]:
    """A subagent's own work in the order it happened — the tools and skills it dispatched and the
    text it wrote between them. Only a round that called a tool is stored as blocks, and work is
    read from blocks, so the plain text a run ends on is not work here: the prose a stopped child is
    force-finished over, and the finish payload the transcript closes with, are both string content.
    Its answer is the terminal's."""
    active = {
        block.tool_use_id
        for message in messages
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.activity
    }
    events: list[dict[str, str]] = []
    for message in messages:
        if message.role != "assistant" or isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                events.append({"kind": "note", "text": block.text.strip()})
            elif isinstance(block, ToolUseBlock) and block.id in active:
                events.append(_tool_event(block))
    return events[:SUBAGENT_EVENT_LIMIT]


def _finish_payload(answer: str) -> dict[str, JsonValue] | None:
    """The typed object a run's finish call carried, or None when its answer is not one — a run
    that failed answers with whatever text it left, which is the error to read. The one place an
    answer is decoded; what a surface then shows of it is that surface's own decision."""
    if not answer:
        return None
    try:
        payload = json.loads(answer)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _payload_prose(value: JsonValue) -> str:
    """One value of a finish payload as a member reads it: prose as itself, a list as its entries
    an empty line apart, and an object as its fields one to a line, each named the only name it
    has. An empty list or object says so rather than vanishing — a review that found nothing
    answered, and a blank page would state that it never ran."""
    match value:
        case str():
            return value.strip()
        case bool():
            return "yes" if value else "no"
        case int() | float():
            return str(value)
        case list():
            rendered = [entry for item in value if (entry := _payload_prose(item))]
            return "\n\n".join(rendered) if rendered else "none"
        case dict():
            fields = [
                f"**{key.replace('_', ' ').capitalize()}**"
                + (f"\n{entry}" if "\n" in entry else f" — {entry}")
                for key, item in value.items()
                if item is not None and (entry := _payload_prose(item))
            ]
            return "\n".join(fields) if fields else "none"
    return ""


def _run_answer(answer: str) -> str:
    """A run's answer, whole, wherever it is read — the tree under the reply that spawned it and
    the run's own page state one answer, and how much of it fits on a screen is the fold's business
    and not this one's. Never the JSON its output schema carried it in: a payload whose single
    field is prose is that prose, since a label over the one thing a bubble holds says what the
    bubble already is, and anything else states its fields, so a run answering in findings rather
    than sentences is read rather than guessed at. A field holding an empty list says so; a payload
    holding no field at all says nothing, having nothing to say it about."""
    payload = _finish_payload(answer)
    if payload is None:
        return answer
    if not payload:
        return ""
    written = [value for value in payload.values() if isinstance(value, str) and value.strip()]
    if len(payload) == 1 and len(written) == 1:
        return written[0].strip()
    return _payload_prose(payload)


async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns:
    """The spawned turns as a tree under the turns that spawned them, each node carrying the run's
    own work. `turns` is the transitive descendant set, so a subagent that spawned its own nests
    again rather than being lost beside its parent. An agent child carries no profile, so it is
    named by its qualified target instead. The work is read from each run's conversation,
    bounded and concurrently, so a conversation that spawned hundreds still answers in one round
    trip; a run past the bound, and one still going, carries the conversation link that holds it."""
    spawned: list[Turn] = []
    nodes: dict[UUID, SubagentNode] = {}
    agent_names: dict[UUID, str] | None = None
    for turn in turns:
        if turn.parent_turn_id is None:
            continue
        profile = turn.subagent_profile
        if profile is None:
            if agent_names is None:
                agent_names = {agent.id: agent.name for agent in await ctx.list_agents()}
            profile = f"agent:{agent_names.get(turn.agent_id, '')}"
        spawned.append(turn)
        nodes[turn.id] = SubagentNode(
            profile=profile,
            name=turn.subagent_name or "",
            conversation_id=str(turn.conversation_id),
            events=[],
            output=_run_answer("" if turn.terminal is None else turn.terminal.text),
            subagents=[],
        )
    read = sorted(spawned, key=lambda turn: turn.created_at, reverse=True)[:SUBAGENT_ACTIVITY_LIMIT]
    recorded = await asyncio.gather(*(ctx.read_transcript(turn.conversation_id) for turn in read))
    for turn, work in zip(read, recorded, strict=True):
        if work is not None:
            nodes[turn.id]["events"] = _subagent_activity(work.messages)
    runs: SubagentRuns = {}
    for turn in spawned:
        parent = nodes.get(turn.parent_turn_id) if turn.parent_turn_id else None
        if parent is None:
            runs.setdefault(str(turn.parent_turn_id), []).append(nodes[turn.id])
        else:
            parent["subagents"].append(nodes[turn.id])
    return runs


def _rendered_messages(
    messages: tuple[Message, ...],
    subagents: SubagentRuns | None = None,
    turn_ids: frozenset[str] = frozenset(),
    agent_origin: frozenset[str] = frozenset(),
    speakers: Mapping[str, str] | None = None,
    questions: Mapping[str, dict[str, object]] | None = None,
    asked: Mapping[str, str] | None = None,
    files: Mapping[str, list[dict[str, object]]] | None = None,
    apps: Mapping[str, list[dict[str, object]]] | None = None,
    connects: Mapping[str, dict[str, object]] | None = None,
    attach: Attach | None = None,
    answers: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    """The transcript as the portal draws it. A user-role message is the member's own bubble, so
    one no member spoke never becomes one: a scheduled task's firing carries its cron envelope and a
    delivered subagent result carries the wire's element around the child's output, and both would
    otherwise read as words the member typed. The reply that answers it still renders — the member
    reads the agent coming back to them, which is what happened.

    A bubble states the member's own words, never the prompt the turn ran on: a channel surface
    fences those words between the ambient digest and their attachments, and
    `member_message_text` is what takes them back out. `speakers` names who spoke each turn — the
    bubble carries the label so a conversation more members than the viewer are in reads as who
    said what.

    `questions` names what a turn asked of the member, keyed by the turn that asked: the reply
    carries it, so the portal draws the question under the words that asked it rather than at the
    foot of the pane. A turn that asked and wrote nothing still renders its reply — the question
    needs the reply it belongs to.

    `asked` names the question a member's words answered, keyed like `speakers`: the bubble draws
    it over the words, and carries it for every speaker, the viewer's own included — an answer
    reads with what it answered.

    `answers` names the messages a question card already states, and those draw no bubble: the card
    on the reply that asked holds each answer under the question it answers, so a bubble of its own
    would say the same words a second time. The words themselves are untouched — the card states
    them, and the turn read them as the member sent them.

    `files` names what each turn shared, keyed like `questions`: the reply carries its own, so a
    file stands on the words that shared it and stays there when later turns run. A turn that
    shared and wrote nothing still renders its reply — the file needs the reply it belongs to.
    `apps` names the applications each turn created and rides the reply the same way, so the card
    that opens one stands under the words that made it. `connects` names the private connect act a
    turn left the member and rides it the same way again — the control stands under the words that
    asked for it, and stays there while later turns run, drawn as the account it made once the
    connect landed.

    A member's own bubble carries what they attached the same way, off the note admission wrote at
    the foot of their words: `attach` turns each saved path into the file the bubble draws, so the
    portal shows the picture rather than the sentence naming where it landed.

    A round that called a tool still narrated, and that narration is a step of the work: every
    assistant text but the turn's last becomes a `note` event in the place it was written — before
    the calls the round dispatched — and the last one is the answer the reply states. The notes are
    bounded like a child run's, so a turn of many rounds cannot grow the projection without
    limit. A reply another message closes states an answer only when its last text stands after the
    work: a round that wrote its text and then dispatched work was cut there — a drain, or a stop —
    so that text is a step of the work too and the reply states no words, which is the shape the
    live view settles into. The rule reaches a turn's own reply and no further: a run's transcript
    closes on the message it stored last, and that message is the answer its page states."""
    subagents = subagents or {}
    connects = connects or {}
    questions = questions or {}
    asked = asked or {}
    files = files or {}
    apps = apps or {}
    rendered: list[dict[str, object]] = []
    pending: list[dict[str, str]] = []
    answer = ""
    answer_at = 0
    notes = 0
    current_turn_id: str | None = None

    def note_answer() -> None:
        nonlocal answer, notes
        if answer and notes < SUBAGENT_EVENT_LIMIT:
            pending.insert(answer_at, {"kind": "note", "text": answer})
            notes += 1
        answer = ""

    def flush_reply(include_subagents: bool) -> None:
        nonlocal answer, answer_at, notes, pending
        closing = current_turn_id if include_subagents else None
        runs = [] if closing is None else subagents.get(closing, [])
        asked = None if closing is None else questions.get(closing)
        shared = [] if closing is None else files.get(closing, [])
        made = [] if closing is None else apps.get(closing, [])
        control = None if closing is None else connects.get(closing)
        if (
            not answer
            and not pending
            and not runs
            and asked is None
            and not shared
            and not made
            and control is None
        ):
            return
        reply: dict[str, object] = {"role": "assistant", "text": answer}
        if pending:
            reply["events"] = pending
        if runs:
            reply["subagents"] = runs
        if asked is not None:
            reply["question"] = asked
        if shared:
            reply["files"] = shared
        if made:
            reply["apps"] = made
        if control is not None:
            reply["connect"] = control
        rendered.append(reply)
        pending = []
        answer = ""
        answer_at = 0
        notes = 0

    active = {
        block.tool_use_id
        for message in messages
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.activity
    }
    for message in messages:
        text = _rendered_text(message)
        if message.role == "assistant":
            if text:
                note_answer()
                answer, answer_at = text, len(pending)
            if not isinstance(message.content, str):
                pending.extend(
                    _tool_event(block)
                    for block in message.content
                    if isinstance(block, ToolUseBlock) and block.id in active
                )
            continue
        if not text:
            continue
        if not isinstance(message.content, str) or CONTEXT_TAG.match(message.content) is None:
            continue
        match = MESSAGE_REF.match(message.content)
        turn_id = None if match is None else match.group("ref").strip()
        if answer_at < len(pending):
            note_answer()
        if turn_id in turn_ids and turn_id != current_turn_id:
            flush_reply(True)
            current_turn_id = turn_id
        else:
            flush_reply(False)
        if turn_id in agent_origin or turn_id in answers:
            continue
        bubble = _member_bubble(member_message_text(text), attach)
        label = None if speakers is None or turn_id is None else speakers.get(turn_id)
        if label is not None:
            bubble["speaker"] = label
        answered = None if turn_id is None else asked.get(turn_id)
        if answered is not None:
            bubble["asked"] = answered
        rendered.append(bubble)
    flush_reply(True)
    return rendered


@dataclass(frozen=True)
class _Asks:
    """What this conversation asked of the member and what they answered. `cards` is the question
    each asking turn's reply carries, keyed by that turn; `stated` names the messages those cards
    already state, which therefore draw no bubble of their own.

    One ask is open — the newest turn's, the only one the member can still answer, since a later
    turn supersedes what an earlier one asked. Every older ask the member answered draws as the
    record of what they chose: `closed`, so the portal states the answers and offers no control on a
    run that is over. An older ask they answered nothing of draws nothing — there is no record to
    make, and the question is a choice they no longer have."""

    cards: dict[str, dict[str, object]]
    stated: frozenset[str]


def _asks(
    conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]
) -> _Asks:
    """Every question the conversation's turns asked, with the answers that landed against each of
    them. An answer is recognized by the key it admitted under — `_answer_key` names the turn that
    asked and the question's place in that ask — so the card states each answer under the question
    it answers, and the answer's own message is named as one the card draws."""
    landed = {row.idempotency_key: row for row in admitted}
    newest = turns[-1].id if turns else None
    cards: dict[str, dict[str, object]] = {}
    stated: set[str] = set()
    for turn in turns:
        question = None if turn.terminal is None else turn.terminal.question
        if question is None:
            continue
        answered: dict[str, str] = {}
        refs: list[str] = []
        for index in range(len(question.questions)):
            row = landed.get(_answer_key(conversation_id, turn.id, index))
            if row is None:
                continue
            answered[str(index)] = member_message_text(row.inbound)
            refs.append(str(row.ref))
        if turn.id != newest and not answered:
            continue
        card: dict[str, object] = {"turn_id": str(turn.id), **question.model_dump(mode="json")}
        if turn.id != newest:
            card["closed"] = True
        if answered:
            card["answered"] = answered
        cards[str(turn.id)] = card
        stated.update(refs)
    return _Asks(cards, frozenset(stated))


@dataclass(frozen=True)
class _TranscriptAids:
    """Everything the transcript renderer needs beside the messages themselves — subagent runs,
    speaker and question attribution, what the member answered, shared files, the applications each
    turn created, where a member's own attachment is drawn from, and whether the conversation ran a
    profile — gathered once so the live window and an earlier page render one message
    identically."""

    subagents: SubagentRuns
    turn_ids: frozenset[str]
    agent_origin: frozenset[str]
    speakers: dict[str, str]
    asked: dict[str, str]
    asks: _Asks
    files: dict[str, list[dict[str, object]]]
    apps: dict[str, list[dict[str, object]]]
    connects: dict[str, dict[str, object]]
    attach: Attach
    run_conversation: bool

    def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]:
        rendered = _rendered_messages(
            messages,
            self.subagents,
            self.turn_ids,
            self.agent_origin,
            self.speakers,
            self.asks.cards,
            self.asked,
            self.files,
            self.apps,
            self.connects,
            self.attach,
            self.asks.stated,
        )
        if self.run_conversation:
            for reply in rendered:
                if reply["role"] == "assistant":
                    reply["text"] = _run_answer(str(reply["text"]))
        return rendered


async def _transcript_aids(
    ctx: SurfaceContext,
    agent_id: UUID,
    conversation_id: UUID,
    viewer: UUID,
    agent_origin: frozenset[str],
    speakers: dict[str, str],
    asked: dict[str, str],
    opens: frozenset[UUID],
) -> _TranscriptAids:
    turns, spawned, shared, admitted = await asyncio.gather(
        ctx.list_turns(conversation_id),
        ctx.conversation_subagent_turns(conversation_id),
        ctx.list_conversation_artifacts(conversation_id, limit=CONVERSATION_ARTIFACTS_MAX),
        ctx.keyed_admissions(conversation_id),
    )
    files: dict[str, list[dict[str, object]]] = {}
    for entry in reversed(shared):
        files.setdefault(str(entry.turn_id), []).append(_file_payload(ctx, entry.artifact))
    drawn = await _created_apps(
        ctx,
        {
            str(turn.id): turn.terminal.created
            for turn in turns
            if turn.terminal is not None and turn.terminal.created
        },
        opens,
    )
    return _TranscriptAids(
        subagents=await _subagent_nodes(ctx, spawned),
        turn_ids=frozenset(str(turn.id) for turn in turns),
        agent_origin=agent_origin,
        speakers=speakers
        | {
            str(turn.id): turn.context.sender
            for turn in turns
            if turn.context is not None
            and turn.context.sender is not None
            and turn.speaker_member_id != viewer
        },
        asked=asked
        | {
            str(turn.id): turn.context.question
            for turn in turns
            if turn.context is not None and turn.context.question is not None
        },
        asks=_asks(conversation_id, turns, admitted),
        files=files,
        apps=drawn,
        connects=await _connect_controls(ctx, conversation_id, turns, viewer),
        attach=partial(_attachment_preview, ctx.public_base_url, agent_id, conversation_id),
        run_conversation=any(turn.subagent_profile is not None for turn in turns),
    )


async def _connect_controls(
    ctx: SurfaceContext,
    conversation_id: UUID,
    turns: tuple[Turn, ...],
    viewer: UUID,
) -> dict[str, dict[str, object]]:
    """The private connect act each turn left this member, keyed by the turn that left it.

    A request whose connect landed is drawn as the account it made: the words above it asked the
    member to press something, and a control that vanished on landing would leave that instruction
    pointing at nothing, while one still offering to connect would ask for an act already done. What
    says it landed is the turn's own stamp, written by the callback that recorded the grant — a
    member may hold two accounts on one provider, and may reconnect from another conversation
    entirely, so the accounts they hold cannot answer which request was answered. A request still
    open is drawn as the act itself, naming the turn the press opens rather than a consent URL: the
    URL is minted where that press lands, so the control a member comes back to hours later is as
    fresh as their press.

    Another member's request is drawn for nobody but them: the grant lands on whoever presses, so a
    colleague reading the conversation is shown no act, as the stream shows them none. A deploy that
    holds no connect flow draws none either — there is nowhere for a press to go."""
    if not ctx.connect_available():
        return {}
    controls: dict[str, dict[str, object]] = {}
    held: dict[str, str] | None = None
    for turn in turns:
        terminal = turn.terminal
        request = None if terminal is None else terminal.connect_request
        if request is None or request.requester_member_id != viewer:
            continue
        if turn.connect_landed_at is None:
            controls[str(turn.id)] = _connect_control(ctx, request.provider, turn.id)
            continue
        if held is None:
            held = await ctx.held_accounts(viewer)
        controls[str(turn.id)] = {
            "provider": request.provider,
            "label": _provider_label(ctx, request.provider),
            "account": held.get(request.provider, ""),
        }
    return controls


async def _conversation_messages(
    ctx: SurfaceContext,
    agent_id: UUID,
    conversation_id: UUID,
    viewer: UUID,
    opens: frozenset[UUID],
) -> tuple[list[dict[str, object]], Turn | None, int]:
    """One conversation as every portal surface renders it — the live chat, the read-only
    transcript an agent's conversations open, and a subagent run's own page: the engine's
    `<context>` framing stripped, tool results elided, and each reply carrying the work it did,
    down to what the runs it spawned did in turn. A projection of the durable transcript, never a
    second store, and one projection, so no screen shows a conversation another screen would show
    differently.

    A turn writes the transcript when it ends, so a turn still running is absent from it: the
    conversation's newest turn rides back with the messages, and its prompt is appended as the
    message it is — the live chat attaches to that turn's frames, a read-only pane states what has
    landed. A prompt that is a machine envelope rather than words draws no bubble and is not
    appended — a scheduled firing carries its cron element, a delivered subagent result the element
    naming the child that answered — and one set decides it for the settled turns and the running
    one alike, so the live chat and a transcript read back never disagree about a message.

    A message admitted while that turn runs is in neither store until the turn ends, so the
    conversation's queue rows the transcript does not hold are appended after the turn's own
    prompt — a reload anywhere between admission and the turn's end shows the message rather than
    losing it. Only a row still waiting names the `arrival_id` a wait is drawn against, and only a
    member's own on a turn about to take it up, since that wait clears on that turn's `absorbed`
    event and on nothing else. The row's admission source is what says whose message it is — an
    internal row is an agent's prompt into the conversation, which draws its bubble as the prompt
    founding a turn does, because the reply answers it, and claims no wait of a member who sent
    nothing. And a wait names a turn working on the message: a settled turn works on none, and a
    run's turn claims only internally admitted rows, so an external row in a run's conversation is
    one no live turn takes.

    A run answers its parent by calling finish, and the payload that call carried is what the
    transcript closes with — so a conversation whose turns ran a profile states its replies as the
    answer it wrote, whole. Only such a conversation: the same words from a main agent are a reply
    it composed, and reading them as a payload would drop every field it meant to show.

    A bubble names its speaker as the display line the admitting surface reported — except the
    `viewer`'s own: their bubbles are the unlabelled default, so the label marks exactly the words
    somebody else said. The transcript refers to a message by the turn it founded or, for one
    folded into a running turn, by its queue row, so the speakers map is keyed by both — a folded
    message keeps its speaker after the turn writes it. A bubble whose words answered a question
    carries that question the same way, the viewer's own included.

    A question stands on the reply that asked it, because that is the reply it answers, and it
    carries the answers the member gave it — the record of an ask is the card the ask was made on,
    so those answers draw no bubble of their own. Only the newest committed turn's ask is still
    open: a later turn supersedes what an earlier one asked, so an older question is a choice the
    member no longer has and its card states what they chose and nothing else.

    A compacted conversation's live transcript starts at its newest summary, so this projection
    states the conversation's tail and the cursor standing directly above it — the newest
    compaction whose kept window the transcript opens with, served by
    `_history_messages` as the reader scrolls up, each page naming the one above it in turn."""
    recorded, agent_origin, spoken, compactions = await asyncio.gather(
        ctx.read_transcript(conversation_id),
        ctx.agent_origin_refs(conversation_id),
        ctx.arrival_speakers(conversation_id),
        ctx.list_compactions(conversation_id),
    )
    speakers = {
        str(arrival.id): arrival.sender
        for arrival in spoken
        if arrival.sender is not None and arrival.speaker_member_id != viewer
    }
    asked = {
        str(arrival.id): arrival.question for arrival in spoken if arrival.question is not None
    }
    latest = await ctx.latest_turn(conversation_id)
    detail = None if latest is None else await ctx.turn_detail(latest)
    attach = partial(_attachment_preview, ctx.public_base_url, agent_id, conversation_id)
    if recorded is None:
        rendered: list[dict[str, object]] = []
        earlier = 0
        # A conversation with no written transcript has no settled turn, so it asked nothing and
        # nothing it holds is an answer to a card.
        stated: frozenset[str] = frozenset()
    else:
        aids = await _transcript_aids(
            ctx, agent_id, conversation_id, viewer, agent_origin, speakers, asked, opens
        )
        rendered = aids.render(recorded.messages)
        earlier = await _verified_earlier(ctx, conversation_id, compactions, recorded.messages)
        stated = aids.asks.stated
    if detail is None:
        return rendered, None, earlier
    if (
        detail.turn.terminal is None
        and str(detail.turn.id) not in agent_origin
        and str(detail.turn.id) not in stated
    ):
        prompt = _member_bubble(member_message_text(detail.turn.inbound), attach)
        if (
            detail.turn.context is not None
            and detail.turn.context.sender is not None
            and detail.turn.speaker_member_id != viewer
        ):
            prompt["speaker"] = detail.turn.context.sender
        if detail.turn.context is not None and detail.turn.context.question is not None:
            prompt["asked"] = detail.turn.context.question
        rendered.append(prompt)
    draining = detail.turn.id if detail.turn.terminal is None else None
    for arrival in await ctx.queued_arrivals(conversation_id, draining):
        if str(arrival.id) in agent_origin or str(arrival.id) in stated:
            continue
        bubble = _member_bubble(member_message_text(arrival.inbound), attach)
        label = speakers.get(str(arrival.id))
        if label is not None:
            bubble["speaker"] = label
        answered = asked.get(str(arrival.id))
        if answered is not None:
            bubble["asked"] = answered
        if (
            arrival.waiting
            and arrival.admission_source == MEMBER_ADMISSION
            and detail.turn.terminal is None
            and detail.turn.subagent_profile is None
        ):
            bubble["arrival_id"] = str(arrival.id)
        rendered.append(bubble)
    return rendered, detail.turn, earlier


async def _verified_earlier(
    ctx: SurfaceContext,
    conversation_id: UUID,
    indices: tuple[int, ...],
    messages: tuple[Message, ...],
) -> int:
    """The newest compaction record `messages` actually opens with — its `after` window is their
    prefix — or 0 when none is. A record can exist without ever reaching the transcript: a turn
    that compacted and then ended non-done keeps the pre-compaction transcript
    (`TranscriptRepair.persist_inbound`), and the next compaction then summarizes from that fuller
    window, shadowing the orphaned record. Counting records would page those in as messages the
    window below already shows, so a page is advertised only when the chain to it holds."""
    for index in sorted(indices, reverse=True):
        after = await ctx.read_compaction_after(conversation_id, index)
        if after and messages[: len(after)] == after:
            return index
    return 0


def _history_cursor(index: int, end: int | None = None) -> str:
    position = f"{index}:{'' if end is None else end}".encode()
    return base64.urlsafe_b64encode(position).decode().rstrip("=")


def _history_position(cursor: str) -> tuple[int, int | None]:
    if len(cursor) > 128:
        raise ValueError("invalid history cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        position = base64.b64decode(
            (cursor + padding).encode("ascii"), altchars=b"-_", validate=True
        ).decode("ascii")
    except (Base64Error, UnicodeError, ValueError) as error:
        raise ValueError("invalid history cursor") from error
    index, separator, end = position.partition(":")
    if not separator or len(index) > 20 or not index.isdigit() or int(index) < 1:
        raise ValueError("invalid history cursor")
    if end and (len(end) > 20 or not end.isdigit() or int(end) < 1):
        raise ValueError("invalid history cursor")
    return int(index), int(end) if end else None


def _bounded_history_page(
    messages: list[dict[str, object]], index: int, end: int
) -> tuple[list[dict[str, object]], int]:
    floor = max(0, end - HISTORY_PAGE_MESSAGE_LIMIT)

    def fits(start: int) -> bool:
        payload = {
            "messages": messages[start:end],
            "earlier_cursor": _history_cursor(index, start or None),
        }
        return len(JSONResponse(payload).body) <= HISTORY_PAGE_BYTE_LIMIT

    if floor == end or fits(floor):
        return messages[floor:end], floor
    low = floor
    high = end - 1
    while low < high:
        middle = (low + high) // 2
        if fits(middle):
            high = middle
        else:
            low = middle + 1
    return messages[low:end], low


async def _history_messages(
    ctx: SurfaceContext,
    agent_id: UUID,
    conversation_id: UUID,
    viewer: UUID,
    cursor: str,
    opens: frozenset[UUID],
) -> tuple[list[dict[str, object]], str | None] | None:
    """One bounded earlier page of a compacted conversation and the cursor above it, or None when
    the cursor names no position.

    A compaction record's `before` is the whole window the compaction replaced, and its `after` is
    the summary plus the tail it kept verbatim — messages the window after it (the next record's
    `before`, or the live transcript) opens with. The page is therefore `before` less that kept
    tail: pages and the live projection concatenate without a message repeating or going missing,
    whichever page the reader has scrolled to. The page above is the newest older record this
    page's `before` opens with — the same verified chain the tail's cursor states — so the
    reader is never handed a page the one below already restates. Rendered with the same aids as
    the live window, so a message reads the same on whichever page it stands."""
    try:
        index, requested_end = _history_position(cursor)
    except ValueError:
        return None
    record = await ctx.read_compaction(conversation_id, index)
    if record is None:
        return None
    kept = max(len(record.after) - 1, 0)
    window = record.before[: len(record.before) - kept] if kept else record.before
    agent_origin, spoken = await asyncio.gather(
        ctx.agent_origin_refs(conversation_id),
        ctx.arrival_speakers(conversation_id),
    )
    speakers = {
        str(arrival.id): arrival.sender
        for arrival in spoken
        if arrival.sender is not None and arrival.speaker_member_id != viewer
    }
    asked = {
        str(arrival.id): arrival.question for arrival in spoken if arrival.question is not None
    }
    aids = await _transcript_aids(
        ctx, agent_id, conversation_id, viewer, agent_origin, speakers, asked, opens
    )
    rendered = aids.render(window)
    end = len(rendered) if requested_end is None else requested_end
    if requested_end is not None and (end < 1 or end > len(rendered)):
        return None
    page, start = _bounded_history_page(rendered, index, end)
    if start:
        return page, _history_cursor(index, start)
    above = await _verified_earlier(ctx, conversation_id, tuple(range(1, index)), record.before)
    return page, _history_cursor(above) if above else None


async def transcript(ctx: SurfaceContext, request: Request) -> Response:
    """One conversation of the member's with this agent, as the portal renders it on load. The
    `conversation` parameter names which one, gated to the member's own like the chat POST that
    writes it. A turn still running names itself, so the page attaches to its live frames instead
    of drawing an empty conversation, and a settled one carries what it still asks of the
    member."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows_chat(agent_id):
        return Response("no such agent", status_code=404)
    requested = request.query_params.get("conversation", "").strip()
    if not requested:
        return Response("conversation is required", status_code=400)
    try:
        conversation_id = UUID(requested)
    except ValueError:
        return Response("no such conversation", status_code=404)
    if (
        await _member_chat(
            ctx,
            web_extension().store,
            agent_id,
            member_id,
            email,
            conversation_id,
            agent_visible=audience.allows(agent_id),
        )
        is None
    ):
        return Response("no such conversation", status_code=404)
    rendered, turn, earlier = await _conversation_messages(
        ctx, agent_id, conversation_id, member_id, _opens(audience)
    )
    payload: dict[str, object] = {"messages": rendered}
    if earlier:
        payload["earlier_cursor"] = _history_cursor(earlier)
    if turn is not None:
        if turn.terminal is None:
            payload["turn"] = str(turn.id)
        else:
            payload.update(await _open_handoffs(ctx, turn.terminal))
    return JSONResponse(payload)


async def _open_handoffs(ctx: SurfaceContext, terminal: TerminalFrame) -> dict[str, object]:
    """What the conversation's newest committed turn still asks of the member, so a reload
    re-renders the affordance the live stream drew: credential prompts still awaiting values. A
    question, a shared file, an app's card, and the connect control are not among them — each rides
    the reply it belongs to, which is where the member answers one, reads another, opens the third,
    and presses the fourth."""
    handoffs: dict[str, object] = {}
    if terminal.credential_request is not None:
        prompts = await _pending_prompts(ctx, terminal.credential_request)
        if prompts is not None:
            handoffs["credentials"] = prompts
    return handoffs


def _connect_control(ctx: SurfaceContext, provider: str, turn_id: UUID) -> dict[str, object]:
    """The standing connect act, as every surface read draws it: what is being connected, and the
    turn whose request the press opens. It carries no consent URL — that is minted at the address
    the press lands on — so nothing the member is looking at can go stale while they read it."""
    return {"provider": provider, "label": _provider_label(ctx, provider), "turn": str(turn_id)}


def _provider_label(ctx: SurfaceContext, provider: str) -> str:
    """The words the portal already uses for this provider: the first-run catalog's label, so the
    chat control and the connect tiles name one thing one way, or the connect flow's declared name
    for a provider the catalog does not curate."""
    for tile in FIRST_RUN_PROVIDERS:
        if tile.name == provider:
            return tile.label
    return ctx.connect_label(provider)


async def chats_index(ctx: SurfaceContext, request: Request) -> Response:
    """The `#/c/<id>` permalink resolve: the one conversation `?conversation=` names, answered as
    `_resolve_chat` answers it. The rail's listing is the `conversation` kind's member listing —
    `GET objects/conversation` — so a read naming no conversation has nothing to answer here."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    store = web_extension().store
    requested = request.query_params.get("conversation", "").strip()
    if not requested:
        return Response("name a conversation to resolve", status_code=400)
    return await _resolve_chat(ctx, store, audience, member_id, email, requested)


async def _resolve_chat(
    ctx: SurfaceContext,
    store: ScopedStore,
    audience: WebAudience,
    member_id: UUID,
    email: str,
    requested: str,
) -> Response:
    """The conversation a `#/c/<id>` permalink names: a web chat returns its rail row; another
    surface returns its conversation projection. A member-private extension conversation is a chat;
    a readable Slack or terminal conversation in the member's own or workspace audience takes
    comments, and every other surface is read-only. The same audience gates as their ordinary views
    answer, down to the viewer's own admin flag — so a row the conversations panel offers an admin
    to disclose resolves here too, carrying `readable: false` rather than reading as a conversation
    that does not exist. A malformed or turnless id is absent."""
    try:
        named = UUID(requested)
    except ValueError:
        return JSONResponse({"chats": []})
    for agent in audience.chat_agents:
        own = await _member_chat(
            ctx,
            store,
            agent.id,
            member_id,
            email,
            named,
            agent_visible=audience.allows(agent.id),
        )
        if own is None:
            continue
        if _commentable(own, member_id):
            return JSONResponse(
                {
                    "chats": [],
                    "conversation": _conversation_row(
                        own,
                        member_id,
                        {"id": str(agent.id), "name": agent.name},
                    ),
                }
            )
        latest = await ctx.latest_turn(named)
        if latest is None:
            break
        detail = await ctx.turn_detail(latest)
        if detail is None:
            break
        return JSONResponse(
            {
                "chats": [
                    {
                        "conversation_id": str(named),
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "agent_model": agent.model,
                        "title": own.title,
                        "mine": True,
                        "speaker": None,
                        "surface": own.summary.surface,
                        "surface_label": own.surface_label,
                        "last_at": _iso(detail.turn.created_at),
                    }
                ]
            }
        )
    agent_id = await ctx.conversation_agent(named)
    target_agent = next((agent for agent in audience.agents if agent.id == agent_id), None)
    if target_agent is None:
        return JSONResponse({"chats": []})
    listed = await ctx.list_agent_conversations(
        target_agent.id,
        member_id,
        admin=audience.admin,
        limit=1,
        conversation_id=named,
    )
    if not listed:
        return JSONResponse({"chats": []})
    return JSONResponse(
        {
            "chats": [],
            "conversation": _conversation_row(
                listed[0],
                member_id,
                {"id": str(target_agent.id), "name": target_agent.name},
            ),
        }
    )


async def _panel_gate(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, str, WebAudience, UUID] | Response:
    """The shared entry of every per-agent panel read and the intent lane: the session's member,
    their email, and audience, plus the path's agent — 404 when the agent is outside the viewer's
    web audience, like every portal route."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows(agent_id):
        return Response("no such agent", status_code=404)
    return member_id, email, audience, agent_id


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _window_param(request: Request) -> int | None | Response:
    """The named usage range, `None` for all time, or a valid rolling `window_seconds`."""
    named = request.query_params.get("range")
    if named is not None:
        if named not in USAGE_RANGES:
            return Response("range must be 7d, 30d, 90d, or all", status_code=400)
        return USAGE_RANGES[named]
    raw = request.query_params.get("window_seconds", str(SPEND_WINDOW_DEFAULT_SECONDS))
    try:
        window = int(raw)
    except ValueError:
        return Response("window_seconds must be a whole number of seconds", status_code=400)
    if not 0 < window <= MAX_USAGE_WINDOW_SECONDS:
        return Response(
            f"window_seconds must be between 1 and {MAX_USAGE_WINDOW_SECONDS}", status_code=400
        )
    return window


def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]:
    details = report.usage
    return {
        "selected": {
            "tokens": details.selected.tokens,
            "token_micro_usd": details.selected.token_micro_usd,
            "total_micro_usd": details.selected.total_micro_usd,
        },
        "all_time": {
            "tokens": details.all_time.tokens,
            "token_micro_usd": details.all_time.token_micro_usd,
            "total_micro_usd": details.all_time.total_micro_usd,
        },
        "first_used_at": _iso(details.first_used_at),
        "previous_tokens": details.previous_tokens,
        "daily": [
            {
                "day": line.day,
                "tokens": line.tokens,
                "token_micro_usd": line.token_micro_usd,
                "total_micro_usd": line.total_micro_usd,
            }
            for line in details.daily
        ],
        "by_execution": [
            {
                "label": line.label,
                "tokens": line.tokens,
                "priced_micro_usd": line.priced_micro_usd,
            }
            for line in details.by_execution
        ],
        "by_model": [
            {
                "label": line.label,
                "tokens": line.tokens,
                "priced_micro_usd": line.priced_micro_usd,
            }
            for line in details.by_model
        ],
    }


async def skills(ctx: SurfaceContext, request: Request) -> Response:
    """The loadable skills the workspace page manages: the workspace's own member-authored ones and
    the deploy's shared set. An edit reads the skill's generation and file digests from the object
    detail (`objects/skill/<name>`); this listing carries the frontmatter's routing metadata so a
    regenerated SKILL.md keeps it."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, _email, _audience, agent_id = gated
    listed = await ctx.agent_skills(agent_id)
    return JSONResponse(
        {
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "origin": skill.origin,
                    "instructions": skill.instructions,
                    "depends": list(skill.depends),
                    "agents": list(skill.agents),
                }
                for skill in listed
            ]
        }
    )


COMMUNITY_QUERY_MIN_CHARS = 2


def _community_refusal(fault: Exception) -> Response:
    """A directory failure the member reads verbatim. `REFUSAL_HEADER` is what marks the body as
    member copy — without it the panel states the status code, since a bare body is the surface
    talking to itself."""
    return Response(str(fault), status_code=502, headers={REFUSAL_HEADER: "1"})


GITHUB_SEGMENT = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})\Z")
COMMUNITY_SKILL_NAME = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,63})\Z")


async def community_skills(ctx: SurfaceContext, request: Request) -> Response:
    """One page of the community skill directory — the leaderboard with no query, the search with
    one — scoped like every other panel read. The results are candidates for the agent the member
    is reading, filed only through the intent lane."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    query = request.query_params.get("q", "").strip()
    if query and len(query) < COMMUNITY_QUERY_MIN_CHARS:
        return Response(
            f"q must be at least {COMMUNITY_QUERY_MIN_CHARS} characters", status_code=400
        )
    try:
        found = await COMMUNITY.listing(query)
    except (CommunityUnavailable, httpx.HTTPError) as fault:
        return _community_refusal(fault)
    return JSONResponse({"skills": [skill.model_dump() for skill in found]})


async def community_skill(ctx: SurfaceContext, request: Request) -> Response:
    """One community skill's document, fetched for review — the member reads the description and
    instructions before the apply intent files the same document."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    owner = request.path_params["owner"]
    repo = request.path_params["repo"]
    name = request.path_params["skill"]
    if not (
        GITHUB_SEGMENT.match(owner)
        and GITHUB_SEGMENT.match(repo)
        and COMMUNITY_SKILL_NAME.match(name)
    ):
        return Response("no such skill", status_code=404)
    try:
        fetched = await COMMUNITY.fetch(f"{owner}/{repo}", name)
    except (CommunityUnavailable, httpx.HTTPError) as fault:
        return _community_refusal(fault)
    if fetched is None:
        return Response("no such skill", status_code=404)
    return JSONResponse(fetched.model_dump())


async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response:
    """The member's memory across every agent they reach. With no query, one keyset page of the
    live items under the viewer's own subject plus shared, newest first — a listing, not a recall,
    narrowable to an item class and walked by the page's own boundary cursors, so an item landing
    mid-read shifts no boundary. With a query, one search per reachable agent unioned and deduped
    by ref: the reader contract stays per-agent, so source-page fencing becomes "any agent the
    member reaches" — live reachability, the same authority chat's tools exercise agent by agent.
    The filter is the listing's alone: recall ranks by similarity and mixes in source pages, which
    carry no item class to narrow on.

    Either shape states how long a body the provider stores, because the correction form on this
    page collects one and has to stop the member at the bound the memory tool enforces rather than
    refuse what they already wrote."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    if not ctx.memory_available:
        return JSONResponse({"available": False, "matches": []})
    body_max_chars = ctx.memory_body_max_chars
    subjects = audience_subjects(conversation_audience(member_id))
    query = request.query_params.get("q", "").strip()
    if not query:
        offered = ctx.memory_kinds
        selected = request.query_params.get("kind", "").strip()
        if selected and selected not in offered:
            return Response("no such memory kind", status_code=400)
        raw_cursor = request.query_params.get("after", "").strip()
        cursor: ListingCursor | None = None
        if raw_cursor:
            try:
                cursor = ListingCursor.decode(raw_cursor)
            except MalformedCursor:
                return Response("malformed listing cursor", status_code=400)
        page = await ctx.recent_memory(
            subjects,
            MEMORY_RECENT_LIMIT,
            frozenset({selected}) if selected else None,
            cursor,
        )
        return JSONResponse(
            {
                "available": True,
                "matches": _memory_rows(page.rows),
                "kinds": list(offered),
                "kind": selected or None,
                "body_max_chars": body_max_chars,
                "older": None if page.older is None else page.older.encode(),
                "newer": None if page.newer is None else page.newer.encode(),
            }
        )
    legs = await asyncio.gather(
        *(
            ctx.search_memory(
                SourceReader(agent_id=agent.id, requesting_member_id=member_id, subjects=subjects),
                (query[:MAX_MEMORY_QUERY_CHARS],),
            )
            for agent in audience.agents
        )
    )
    deduped: dict[object, MemoryMatch] = {}
    for leg in legs:
        for match in leg:
            key = (
                (match.ref.kind, match.ref.name)
                if match.ref is not None
                else (match.kind, match.text)
            )
            if key not in deduped:
                deduped[key] = match
    found = tuple(deduped.values())[:MEMORY_RESULT_LIMIT]
    return JSONResponse(
        {
            "available": True,
            "matches": _memory_rows(found),
            "body_max_chars": body_max_chars,
        }
    )


def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]:
    return [
        {
            "kind": match.kind,
            "text": match.text,
            "ref": None if match.ref is None else f"{match.ref.kind}/{match.ref.name}",
            "created_at": _iso(match.created_at),
            "subject": match.subject,
        }
        for match in found
    ]


async def connections(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's connector accounts this member may see — their own private grants plus
    agent-shared ones, every edge for a workspace admin. The member gate is the query's, the wall
    is the agent id, and the read names a shared edge's owner only to an admin or the owner."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    listed = await ctx.list_agent_connections(agent_id, member_id, admin=audience.admin)
    return JSONResponse({"connections": [entry.model_dump(mode="json") for entry in listed]})


async def connection_pool(ctx: SurfaceContext, request: Request) -> Response:
    gated = await _audience_for(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience = gated
    listed = await ctx.list_connections(member_id, admin=audience.admin)
    visible = (
        entry.model_copy(update={"agents": tuple(a for a in entry.agents if audience.allows(a.id))})
        for entry in listed
    )
    return JSONResponse({"connections": [entry.model_dump(mode="json") for entry in visible]})


async def github_coverage(ctx: SurfaceContext, request: Request) -> Response:
    gated = await _audience_for(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience = gated
    coverage = await ctx.github_coverage(member_id, admin=audience.admin)
    return JSONResponse(coverage.model_dump(mode="json"))


async def conversations(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's conversations this member may see: their own plus the workspace-shared
    ones, every one of the agent's for an admin. Each says whether its content reads now and
    whether an admin may disclose it to themselves by acknowledging. Recorded disclosures are not
    here and no portal read lists them: that record is the operator's, read with
    `ufoctl transcript-reads`.

    `q` narrows the read itself rather than the page it returns, so a member searching for a
    conversation older than the bound finds it."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    listed = await ctx.list_agent_conversations(
        agent_id,
        member_id,
        admin=audience.admin,
        limit=CONVERSATION_LIST_LIMIT,
        search=_searched(request),
    )
    return JSONResponse(
        {"conversations": [_conversation_row(entry, member_id) for entry in listed]}
    )


def _searched(request: Request) -> str | None:
    """What the member typed into a listing's search box, or None where the box is empty. Bounded
    beside the query it reaches, because it is a member's string on a read this surface runs."""
    typed = request.query_params.get("q", "").strip()[:MAX_SEARCH_CHARS]
    return typed or None


def _conversation_row(
    entry: ListedConversation,
    member_id: UUID,
    agent: dict[str, str] | None = None,
) -> dict[str, object]:
    """One conversation as the panel lists it. `description` is what the conversation is called —
    the one string the rail row, this row and the record's own heading all read, so no screen names
    one conversation two ways. A row this viewer may not read carries neither a description nor a
    speaker: what a conversation is called is content, and core withholds it with the rest.

    `agent` names the owner of a row read across every agent, and is null for a read taken inside
    one agent's namespace, where the pane names it once instead of every row naming it again.

    `source` is where the conversation was opened, as the admitting surface reported it — the
    permalink of a Slack thread's first message, so a row leads back out to the thread as well as
    into the transcript, and the transcript itself states the one way back. Every surface defines
    its own, and a portal chat's names the portal, so the row states it and the screen decides which
    surface's is a link worth drawing."""
    return {
        "id": str(entry.summary.id),
        "agent": agent,
        "surface": entry.summary.surface,
        "surface_label": entry.surface_label,
        "audience": entry.audience,
        "member_email": entry.summary.member_email,
        "description": entry.title,
        "source": entry.source,
        "speakers": [who.sender or who.email for who in entry.speakers],
        "turn_count": entry.summary.turn_count,
        "created_at": _iso(entry.summary.created_at),
        "last_turn_at": _iso(entry.summary.last_turn_at),
        "readable": entry.readable,
        "disclosable": entry.disclosable,
        "commentable": _commentable(entry, member_id),
    }


async def _readable_conversation(
    ctx: SurfaceContext, request: Request, conversation_id: UUID | None = None
) -> tuple[UUID, UUID, "SlotViewer"] | Response:
    """The agent and conversation a content read is authorized for, or the 404 every unreadable
    case answers: an agent outside the audience, a malformed id, another agent's conversation, a
    room's, and another member's private one until an admin records a disclosure against it. One
    gate, so content reads cannot disagree."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    if conversation_id is None:
        try:
            conversation_id = UUID(request.path_params["conversation_id"])
        except ValueError:
            return Response("no such conversation", status_code=404)
    if not await ctx.readable_conversation(
        conversation_id, agent_id, member_id, admin=audience.admin
    ):
        return Response("no such conversation", status_code=404)
    return agent_id, conversation_id, SlotViewer(member_id, audience.admin, _opens(audience))


async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response:
    """One conversation read rather than continued — another member's the admin acknowledged, one
    another surface holds — as the same messages the chat draws. Each reply names the children it
    spawned, and a child carries this conversation's audience, so the card opens that run through
    this conversation and the one gate here authorizes both."""
    cursor = request.query_params.get("cursor")
    if cursor is not None:
        return await _conversation_history(ctx, request, cursor)
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    agent_id, conversation_id, viewer = authorized
    rendered, _turn, earlier = await _conversation_messages(
        ctx, agent_id, conversation_id, viewer.member_id, viewer.opens
    )
    payload: dict[str, object] = {"messages": rendered}
    if earlier:
        payload["earlier_cursor"] = _history_cursor(earlier)
    return JSONResponse(payload)


async def _member_chat_page(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, UUID, "SlotViewer"] | Response:
    """The member-chat transcript's own admission, answered for the page read it advertises: the
    agent at the chat reach and the conversation `_member_chat` serves — a member-private
    extension conversation grants chat with an agent no panel gate holds."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows_chat(agent_id):
        return Response("no such conversation", status_code=404)
    try:
        conversation_id = UUID(request.path_params["conversation_id"])
    except ValueError:
        return Response("no such conversation", status_code=404)
    chat = await _member_chat(
        ctx,
        web_extension().store,
        agent_id,
        member_id,
        email,
        conversation_id,
        agent_visible=audience.allows(agent_id),
    )
    if chat is None:
        return Response("no such conversation", status_code=404)
    return agent_id, conversation_id, SlotViewer(member_id, audience.admin, _opens(audience))


async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response:
    """One earlier page of a conversation whose transcript has compacted. The transcript's cursor
    names the bounded page above its tail and each page's cursor the one above it, so the pane
    follows the chain upward as the reader scrolls. Gated as exactly the union of the two reads
    that advertise a page — the conversation content read, or the member's own chat transcript —
    so a page answers precisely where a transcript that names it answers, and nowhere else."""
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        authorized = await _member_chat_page(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    agent_id, conversation_id, viewer = authorized
    page = await _history_messages(
        ctx, agent_id, conversation_id, viewer.member_id, cursor, viewer.opens
    )
    if page is None:
        return Response("no such page", status_code=404)
    rendered, above = page
    payload: dict[str, object] = {"messages": rendered}
    if above:
        payload["earlier_cursor"] = above
    return JSONResponse(payload)


def _inbox_attachment(path: str) -> bool:
    """Whether a path names one file the composer saved — a plain name directly under the inbox
    directory. Nothing else is addressable: the route serves what a member attached to their own
    message, never the rest of the conversation's workspace."""
    directory, separator, name = path.partition("/")
    return (
        directory == WEB_INBOX_DIR
        and bool(separator)
        and bool(name)
        and "/" not in name
        and name not in (".", "..")
    )


async def conversation_attachment(ctx: SurfaceContext, request: Request) -> Response:
    """One file the member attached to a message, served as the picture the bubble draws it as.

    Gated as exactly the union of the two reads that advertise the bubble — the conversation content
    read, or the member's own chat transcript — so a picture answers precisely where the message
    naming it answers. The bytes come from the conversation's live workspace, where the composer put
    them and the agent reads them, and they serve inline only after proving to be the raster type
    the filename declares at the size the workspace lists: the same validated-preview shape the
    signed artifact preview serves member bytes under. A type that is no raster — a PDF, an SVG,
    anything HTML-ish — is never served here, so nothing that could execute reaches the page; the
    bubble cards those by name. A conversation whose sandbox is asleep or whose file has moved
    answers 404, and the bubble falls back to that same card."""
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        authorized = await _member_chat_page(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    _agent_id, conversation_id, _viewer = authorized
    path = request.path_params["path"]
    media_type = raster_image_media_type(path)
    if media_type is None or not _inbox_attachment(path):
        return Response("no such attachment", status_code=404)
    listed = {
        entry.path: entry.size_bytes for entry in await ctx.list_workspace_files(conversation_id)
    }
    size_bytes = listed.get(path)
    if size_bytes is None:
        return Response("no such attachment", status_code=404)
    if size_bytes > IMAGE_PREVIEW_MAX_BYTES:
        return Response("attachment is too large to draw", status_code=415)
    stream = await ctx.read_workspace_file(conversation_id, path)
    if stream is None:
        return Response("no such attachment", status_code=404)
    try:
        drawn = await validated_image_preview(
            stream, ImagePreviewGrant(media_type=media_type, size_bytes=size_bytes)
        )
    except InvalidImagePreview as invalid:
        log("web.attachment_preview_refused", path=path, detail=str(invalid))
        return Response("attachment is not the picture its name claims", status_code=415)
    return Response(content=drawn, media_type=media_type, headers=ATTACHMENT_PREVIEW_HEADERS)


@dataclass(frozen=True)
class SlotViewer:
    member_id: UUID
    admin: bool
    opens: frozenset[UUID] = frozenset()
    """The applications this reader may open, which is what decides whether a card for one is
    drawn: a conversation they may read can still name an application they may not."""


@dataclass(frozen=True)
class SlotTarget:
    agent_id: UUID
    conversation_id: UUID
    viewer: SlotViewer
    root_conversation_id: UUID | None


async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response:
    root = request.query_params.get("root")
    if root is None:
        authorized = await _readable_conversation(ctx, request)
        root_id = None
    else:
        try:
            conversation_id = UUID(request.path_params["conversation_id"])
            root_id = UUID(root)
        except ValueError:
            return Response("no such conversation", status_code=404)
        authorized = await _readable_conversation(ctx, request, root_id)
        if not isinstance(authorized, Response):
            agent_id, _root_id, viewer = authorized
            spawned = await ctx.conversation_subagent_turns(root_id)
            if not any(
                turn.agent_id == agent_id and turn.conversation_id == conversation_id
                for turn in spawned
            ):
                return Response("no such conversation", status_code=404)
            authorized = agent_id, conversation_id, viewer
    if isinstance(authorized, Response):
        return authorized
    agent_id, conversation_id, viewer = authorized
    return SlotTarget(agent_id, conversation_id, viewer, root_id)


async def _slot_context(
    ctx: SurfaceContext,
    target: SlotTarget,
    ext: ExtensionContext,
) -> ConversationSlotContext | None:
    audience = await ctx.conversation_audience(target.conversation_id, target.agent_id)
    if audience is None:
        return None
    recorded = await ctx.read_transcript(target.conversation_id)
    return ConversationSlotContext(
        ext=replace(ext, audience=audience),
        conversation_id=target.conversation_id,
        agent_id=target.agent_id,
        audience=audience,
        messages=() if recorded is None else recorded.messages,
        public_base_url=ctx.public_base_url,
    )


async def _project_slot_context(
    ctx: SurfaceContext,
    slot_context: ConversationSlotContext,
    extension: str,
    content: type[BaseModel],
    root_conversation_id: UUID | None,
    viewer: SlotViewer,
) -> ConversationSlotContext:
    if extension == "web" and content is WorkspaceChanges:
        return replace(
            slot_context,
            projection=await ctx.conversation_changes(slot_context.conversation_id),
        )
    if extension == "web" and content is ArtifactsSlotPayload:
        listed_artifacts = await ctx.list_conversation_artifacts(
            slot_context.conversation_id, limit=CONVERSATION_ARTIFACTS_MAX + 1
        )
        truncated = len(listed_artifacts) > CONVERSATION_ARTIFACTS_MAX
        artifacts: list[ConversationArtifact] = []
        for artifact_entry in listed_artifacts[:CONVERSATION_ARTIFACTS_MAX]:
            try:
                artifact_url = ctx.artifact_link(artifact_entry.artifact)
                artifact_preview = None
                artifact_preview_url = ctx.artifact_preview_link(artifact_entry.artifact)
                # The link is minted only for bytes that are a raster of a declared type — the
                # file's own where it is a picture, its rendered first page where it is a document —
                # so the picture's type is read off the blob the link serves, never off the
                # member's filename, which for a document names the document.
                if artifact_preview_url is not None:
                    preview_path = urlsplit(artifact_preview_url).path
                    preview_media_type = raster_image_media_type(preview_path)
                    if preview_media_type is not None:
                        artifact_preview = ImagePreview(
                            media_type=preview_media_type, url=artifact_preview_url
                        )
                artifacts.append(
                    ConversationArtifact(
                        filename=artifact_entry.artifact.filename,
                        subject=artifact_entry.artifact.subject,
                        media_type=artifact_entry.artifact.media_type,
                        size_bytes=artifact_entry.artifact.size_bytes,
                        created_at=artifact_entry.created_at,
                        url=artifact_url,
                        preview=artifact_preview,
                    )
                )
            except ValidationError:
                truncated = True
        return replace(
            slot_context,
            projection=ArtifactsSlotPayload(artifacts=tuple(artifacts), truncated=truncated),
        )
    if extension == "sites" and content is SitesSlotPayload:
        rows = await ctx.list_conversation_member_objects(
            "site",
            slot_context.agent_id,
            slot_context.conversation_id,
            viewer.member_id,
            admin=viewer.admin,
            limit=CONVERSATION_SITES_MAX + 1,
        )
        return replace(
            slot_context,
            visible_items=tuple(
                ConversationSlotItem(row.name, row.generation, row.content_visible)
                for row in rows or ()
            ),
        )
    if extension == "scheduled_tasks" and content is AutomationsSlotPayload:
        rows = await ctx.list_conversation_member_objects(
            "scheduled_task",
            slot_context.agent_id,
            slot_context.conversation_id,
            viewer.member_id,
            admin=viewer.admin,
            limit=CONVERSATION_AUTOMATIONS_MAX + 1,
        )
        return replace(
            slot_context,
            visible_items=tuple(
                ConversationSlotItem(row.name, row.generation, row.content_visible)
                for row in rows or ()
            ),
        )
    return slot_context


def _authorized_slot_payload(
    payload: ConversationSlotPayload, context: ConversationSlotContext
) -> ConversationSlotPayload:
    if isinstance(payload, SitesSlotPayload):
        return payload.model_copy(
            update={
                "sites": tuple(
                    site
                    for site in payload.sites
                    if any(
                        item.name == site.authorization_name
                        and item.generation == site.authorization_generation
                        for item in context.visible_items
                    )
                )
            }
        )
    if isinstance(payload, AutomationsSlotPayload):
        visible = tuple(
            automation
            for automation in payload.automations
            if any(
                item.name == automation.name
                and item.generation == automation.authorization_generation
                for item in context.visible_items
            )
        )
        return payload.model_copy(
            update={
                "automations": tuple(
                    automation
                    if any(
                        item.name == automation.name
                        and item.generation == automation.authorization_generation
                        and item.content_visible
                        for item in context.visible_items
                    )
                    else automation.model_copy(
                        update={"description": None, "latest_response": None}
                    )
                    for automation in visible
                )
            }
        )
    return payload


async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response:
    """Available typed slots for one authorized conversation."""
    authorized = await _slot_target(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    slots: list[dict[str, object]] = []
    if not ctx.conversation_slots:
        return JSONResponse({"slots": slots})
    shared_context = await _slot_context(ctx, authorized, ctx.conversation_slots[0].ext)
    if shared_context is None:
        return Response("no such conversation", status_code=404)
    for bound in ctx.conversation_slots:
        try:
            slot_context = await _project_slot_context(
                ctx,
                replace(
                    shared_context,
                    ext=replace(bound.ext, audience=shared_context.audience),
                ),
                bound.extension,
                bound.provider.content,
                authorized.root_conversation_id,
                authorized.viewer,
            )
            count = await ctx.summarize_conversation_slot(bound, slot_context)
        except Exception as error:
            log(
                "web.conversation_slot.summary_failed",
                extension=bound.extension,
                slot=bound.provider.id,
                error=str(error),
            )
            continue
        if count is None:
            continue
        slots.append(
            {
                "id": bound.provider.id,
                "label": bound.provider.label,
                "icon": bound.provider.icon,
                "kind": bound.provider.content.model_fields["type"].default,
                "count": count,
            }
        )
    return JSONResponse({"slots": slots})


async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response:
    """One typed slot payload for an authorized conversation."""
    authorized = await _slot_target(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    slot_id = request.path_params["slot_id"]
    bound = next((entry for entry in ctx.conversation_slots if entry.provider.id == slot_id), None)
    if bound is None:
        return Response("no such conversation slot", status_code=404)
    slot_context = await _slot_context(ctx, authorized, bound.ext)
    if slot_context is None:
        return Response("no such conversation", status_code=404)
    slot_context = await _project_slot_context(
        ctx,
        slot_context,
        bound.extension,
        bound.provider.content,
        authorized.root_conversation_id,
        authorized.viewer,
    )
    payload = await ctx.read_conversation_slot(bound, slot_context)
    if type(payload) is not bound.provider.content:
        raise TypeError(
            f"conversation slot {slot_id!r} returned {type(payload).__name__}, "
            f"expected {bound.provider.content.__name__}"
        )
    payload = _authorized_slot_payload(payload, slot_context)
    return JSONResponse(payload.model_dump(mode="json"))


def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges:
    if not isinstance(ctx.projection, WorkspaceChanges):
        raise RuntimeError("changes slot needs the host workspace projection")
    return ctx.projection


async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges:
    return _changes_projection(ctx)


async def _summarize_changes(ctx: ConversationSlotContext) -> int | None:
    return len(_changes_projection(ctx).changes) or None


CHANGES_SLOT = ConversationSlotProvider(
    id="changes",
    label="Changes",
    icon="diff",
    content=WorkspaceChanges,
    summarize=_summarize_changes,
    read=_read_changes,
)


def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload:
    if not isinstance(ctx.projection, ArtifactsSlotPayload):
        raise RuntimeError("artifacts slot needs the host artifact projection")
    return ctx.projection


async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload:
    return _artifacts_projection(ctx)


async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None:
    count = len(_artifacts_projection(ctx).artifacts)
    return count or None


ARTIFACTS_SLOT = ConversationSlotProvider(
    id="artifacts",
    label="Artifacts",
    icon="artifact",
    content=ArtifactsSlotPayload,
    summarize=_summarize_artifacts,
    read=_read_artifacts,
)


async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response:
    """Member-fillable declared BYOK slots and their fill state — never a value, and never the
    `member_filled=False` seals the `credential` object kind still lists (deploy machinery, not a
    member's key). Workspace-scoped: the slots are the deploy's, shared across every agent."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    listed = await ctx.list_credential_slots()
    return JSONResponse({"slots": [entry.model_dump(mode="json") for entry in listed]})


async def workspace_team(ctx: SurfaceContext, request: Request) -> Response:
    """The workspace roster: who the members are, which of them administer the workspace, and who
    holds a seat — the same rows the `member` kind lists to a member asking the main agent, so the
    panel shows a non-admin exactly what chat would tell them. `can_add` reports whether this
    member may add another, read back from the verb's own authority (the same `member.is_admin` row
    its gate checks), never a second copy; the verb refuses regardless."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, _email, audience = resolved
    return JSONResponse(
        {
            "members": [
                {"email": entry.email, "admin": entry.admin, "seated": entry.seated}
                for entry in await ctx.list_members()
            ],
            "can_add": audience.admin,
        }
    )


async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response:
    """The live source bindings this member may see — their own registrations plus shared ones,
    all of them for a workspace admin. Workspace-scoped: the rows never carried an agent. A
    member-subject source's indexed pages stay gated to that member; the view shows the subject
    so that stays legible, and the read names a shared source's owner only to an admin or the
    owner."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    listed = await ctx.list_sources(member_id, admin=audience.admin)
    return JSONResponse({"sources": [entry.model_dump(mode="json") for entry in listed]})


async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response:
    """The surface installations bound to the agents this member's web audience holds, each with
    the agent its conversations land on — the edges the agents topology graph draws between
    surfaces and agents. An agent outside that audience is named nowhere here, as on every other
    portal route; the whole workspace's bindings are the administration read's."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, _email, audience = resolved
    installations = await ctx.list_installations()
    visible = (entry for entry in installations if audience.allows(entry.agent_id))
    return JSONResponse({"installations": [entry.model_dump(mode="json") for entry in visible]})


SLACK_SURFACE = "slack"
GITHUB_PROVIDER = "github"
CONNECT_STEP_NAMES = (SLACK_SURFACE, GITHUB_PROVIDER)
CONNECTOR_CATALOG_LIMIT = 50
CONNECTOR_CATALOG_QUERY_CHARS = 100
CONNECTOR_CATALOG_CURSOR_CHARS = 500


class ConnectStep(BaseModel):
    """One connector the first run offers to install where the member picked it, and whether the
    workspace already holds it."""

    name: str
    label: str
    installed: bool


class ConnectorCatalogTile(BaseModel):
    """One broker-catalog provider the connector page can offer."""

    name: str
    label: str


async def workspace_first_run(ctx: SurfaceContext, request: Request) -> Response:
    """The connector catalog, read by the first run's selector and the Connect page: the tools a
    team can say it uses, and the two of them the pages install themselves, beside whether the
    workspace holds them. Each step reads the leg its own Connect
    act writes: Slack's is the surface installation `slack_connect` binds, GitHub's the `git_push`
    credential leg `github/coverage` reports — what `connect_github` fills by installing the App,
    and what a stored token fills where no organization installed it — never the `api` leg, a broker
    connection row that act neither writes nor needs. Both rows are the whole workspace's rather
    than the reader's audience: the row is stated as a bare boolean, and a step narrowed by audience
    would tell a member to install what the workspace already has. Both steps carry the catalog's
    own label, so a tile and the step it reveals never name one connector two ways."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    surfaces = {entry.surface for entry in await ctx.list_installations()}
    coverage = await ctx.github_coverage(member_id, admin=audience.admin)
    held = {SLACK_SURFACE: SLACK_SURFACE in surfaces, GITHUB_PROVIDER: coverage.git_push}
    return JSONResponse(
        {
            "providers": [tile.model_dump(mode="json") for tile in FIRST_RUN_PROVIDERS],
            "connectors": [
                ConnectStep(name=tile.name, label=tile.label, installed=held[tile.name]).model_dump(
                    mode="json"
                )
                for tile in FIRST_RUN_PROVIDERS
                if tile.name in CONNECT_STEP_NAMES
            ],
        }
    )


async def connector_catalog(ctx: SurfaceContext, request: Request) -> Response:
    """The installed brokers' connectable providers for the connector page."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    query = request.query_params.get("q", "").strip()
    if len(query) > CONNECTOR_CATALOG_QUERY_CHARS:
        return Response(
            "The connector search is too long.",
            status_code=400,
            headers={REFUSAL_HEADER: "1"},
        )
    after = request.query_params.get("after", "").strip() or None
    if after is not None and len(after) > CONNECTOR_CATALOG_CURSOR_CHARS:
        return Response(
            "The connector page cursor is invalid.",
            status_code=400,
            headers={REFUSAL_HEADER: "1"},
        )
    page = await ctx.connector_catalog(query, CONNECTOR_CATALOG_LIMIT, after)
    return JSONResponse(
        {
            "providers": [
                ConnectorCatalogTile(name=row.provider, label=row.label).model_dump(mode="json")
                for row in page.entries
            ],
            "after": page.after,
        }
    )


STARTER_APP_SLOTS = 2
UNLOCK_MAX_MISSING = 2
PROVIDER_LABELS = {tile.name: tile.label for tile in FIRST_RUN_PROVIDERS}


class MissingTile(BaseModel):
    name: str
    label: str


class StarterRow(BaseModel):
    """One row the start screen presses. `ask` is what the member says by pressing it — the whole
    act, in their own voice. `mark` is the app icon an application row wears; a check-in founds no
    application, so it carries none and the page draws it a plain glyph. An `unlock` row is an
    application the member is an account or two short of, drawn in an application's slot because
    none was ready; it carries the accounts it still needs and the page draws their brand."""

    kind: Literal["app", "check_in", "unlock"]
    mark: str | None
    title: str
    body: str
    ask: str
    providers: tuple[MissingTile, ...] = ()


class UnlockRow(BaseModel):
    """The start screen's fourth row: an application this member is one or two accounts short of,
    stated as the accounts it would take. `providers` is what is still missing, in catalog order,
    and pressing the row says the same build ask an owned row says — the agent asks for the
    accounts it finds it does not hold, and its reply carries the connect control."""

    mark: str
    title: str
    ask: str
    providers: tuple[MissingTile, ...]


async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]:
    """The connectors this workspace already reaches, in the catalog's own vocabulary: the broker
    connections the reader's audience holds, the Slack the workspace installed, and the GitHub
    `github/coverage` reports. Read live, on every start screen, so an account connected a moment
    ago is never offered again."""
    connections = await ctx.list_connections(member_id, admin=admin)
    held = {view.provider for view in connections}
    if SLACK_SURFACE in {entry.surface for entry in await ctx.list_installations()}:
        held.add(SLACK_SURFACE)
    if (await ctx.github_coverage(member_id, admin=admin)).git_push:
        held.add(GITHUB_PROVIDER)
    return frozenset(held)


def _named_tiles(providers: "tuple[MissingTile, ...]") -> str:
    """The accounts a row still needs, said the way a person says them."""
    labels = [tile.label for tile in providers]
    return labels[0] if len(labels) < 2 else ", ".join(labels[:-1]) + " and " + labels[-1]


def fill_starters(
    slate: Slate, held: frozenset[str], taken: frozenset[str]
) -> tuple[tuple[StarterRow, ...], UnlockRow | None]:
    """Which ranked rows the start screen draws, decided against what the workspace holds right now.

    The ranking states relevance and nothing else; access is answered here. A row whose accounts are
    all held is an application the member can build today, and the first two fill the screen's
    application slots. A row short of one or two accounts is an unlock — short of more than two, the
    row is a project rather than an offer and is passed over. A row whose name an application
    already carries is dropped either way. The check-in closes the list, because it asks after work
    rather than founding any.

    Where fewer than two applications are ready, the remaining slots take unlocks instead of
    standing empty. A workspace that has connected nothing has no ready application by definition,
    and the rows it would otherwise fall back to need accounts just the same while saying nothing
    about which — so it reads as named work and its price rather than as generic filler."""
    apps: list[StarterRow] = []
    short: list[UnlockRow] = []
    for entry in slate.ranked:
        row = UNLOCKS_BY_NAME.get(entry.unlock)
        if row is None or row.name in taken or entry.title.strip().lower() in taken:
            continue
        missing = row.missing(held)
        if not missing:
            if len(apps) < STARTER_APP_SLOTS:
                apps.append(
                    StarterRow(
                        kind="app",
                        mark=row.mark,
                        title=entry.title,
                        body=entry.body,
                        ask=entry.ask,
                    )
                )
        elif len(missing) <= UNLOCK_MAX_MISSING:
            short.append(
                UnlockRow(
                    mark=row.mark,
                    title=entry.title,
                    ask=entry.ask,
                    providers=tuple(
                        MissingTile(name=name, label=PROVIDER_LABELS[name]) for name in missing
                    ),
                )
            )
    unlock = short[0] if short else None
    for spare in short[1:]:
        if len(apps) >= STARTER_APP_SLOTS:
            break
        apps.append(
            StarterRow(
                kind="unlock",
                mark=spare.mark,
                title=spare.title,
                body="Connect " + _named_tiles(spare.providers) + ".",
                ask=spare.ask,
                providers=spare.providers,
            )
        )
    if slate.check_in is not None:
        apps.append(
            StarterRow(
                kind="check_in",
                mark=None,
                title=slate.check_in.title,
                body=slate.check_in.body,
                ask=slate.check_in.ask,
            )
        )
    return tuple(apps), unlock


async def _solvent() -> bool:
    """Whether this workspace's balance still admits spend. Read as its own line rather than through
    `BalanceGate`, which a surface cannot reach and which answers about a turn — the question here
    is only whether generating a slate is spending money a refusing workspace does not have."""
    ext = web_extension()
    async with ext.transaction() as connection:
        headroom = await read_headroom(connection, ext.workspace_id)
    if headroom is None:
        return True
    return headroom.balance_micro_usd > headroom.reserve_micro_usd - headroom.grace_micro_usd


async def _recalled(ctx: SurfaceContext, member_id: UUID) -> tuple[str, ...]:
    """The memory this member's own audience reads — their subject and the workspace-shared one,
    never a colleague's — newest first and bounded next to the call that sends it."""
    if not ctx.memory_available:
        return ()
    subjects = audience_subjects(conversation_audience(member_id))
    page = await ctx.recent_memory(subjects, MEMORY_LIMIT)
    return tuple(match.text[:MEMORY_TEXT_CHARS] for match in page.rows)


async def workspace_starters(ctx: SurfaceContext, request: Request) -> Response:
    """What this member reads before they have asked anything: two applications they can build now,
    one check-in drawn from their own memory, and one unlock naming what an account would buy them.

    The ranking is made here, for the member who is asking, and cached for `STARTERS_TTL` under
    their own subject where nobody else reads it. Nothing generates on a clock: a slate nobody opens
    is never made.

    Which ranked row is an application and which is an unlock is not cached with it. That is decided
    on every read against the connectors the workspace holds right now, so connecting an account
    moves a row with no new ranking, and a stored slate never carries a claim about access that a
    connect made stale. A row whose name an application already carries is dropped for the same
    reason: the screen offers work to do, never work already done.

    An empty answer is ordinary — a workspace whose memory says nothing yet has nothing to rank —
    and the page draws its own rows for every slot this read does not fill."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    slate = await StarterCache(
        store=web_extension().store,
        member_id=member_id,
        agents=tuple(sorted(agent.name for agent in audience.agents)),
        recalled=await _recalled(ctx, member_id),
        model=ctx.model,
        solvent=await _solvent(),
    ).read()
    if slate is None:
        return JSONResponse({"starters": [], "unlock": None})
    held = await _held_providers(ctx, member_id, admin=audience.admin)
    taken = frozenset(agent.name.strip().lower() for agent in audience.agents)
    starters, unlock = fill_starters(slate, held, taken)
    return JSONResponse(
        {
            "starters": [row.model_dump(mode="json") for row in starters],
            "unlock": None if unlock is None else unlock.model_dump(mode="json"),
        }
    )


async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response:
    """The reader's own range and all-time usage and their member-scoped caps — a member's burn is
    theirs to read, so this answers every member. An admin additionally receives the workspace
    rollup (totals by dimension, member, and agent) in the same payload — the workspace's whole
    financial state, which lives here and nowhere else; a non-admin's payload names no other
    member and no agent."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    window = _window_param(request)
    if isinstance(window, Response):
        return window
    own = await ctx.member_spend(member_id, window)
    payload: dict[str, object] = {
        "window_seconds": own.window_seconds,
        "total_micro_usd": own.total_micro_usd,
        "by_dimension": [
            {
                "dimension": line.dimension,
                "amount": line.amount,
                "priced_micro_usd": line.priced_micro_usd,
            }
            for line in own.by_dimension
        ],
        "caps": [
            {
                "window_seconds": cap.window_seconds,
                "limit_micro_usd": cap.limit_micro_usd,
                "on_breach": cap.on_breach,
            }
            for cap in own.caps
        ],
        "usage": _usage_payload(own),
        "workspace": None,
    }
    if audience.admin:
        rollup = await ctx.spend_rollup(window)
        payload["workspace"] = {
            "total_micro_usd": rollup.total_micro_usd,
            "by_dimension": [
                {
                    "dimension": line.dimension,
                    "amount": line.amount,
                    "priced_micro_usd": line.priced_micro_usd,
                }
                for line in rollup.by_dimension
            ],
            "by_member": [
                {
                    "label": subject.label,
                    "tokens": subject.tokens,
                    "priced_micro_usd": subject.priced_micro_usd,
                }
                for subject in rollup.by_member
            ],
            "by_agent": [
                {
                    "id": None if subject.subject_id is None else str(subject.subject_id),
                    "label": subject.label,
                    "tokens": subject.tokens,
                    "priced_micro_usd": subject.priced_micro_usd,
                }
                for subject in rollup.by_agent
            ],
            "by_origin": [
                {
                    "label": origin.label,
                    "tokens": origin.tokens,
                    "priced_micro_usd": origin.priced_micro_usd,
                }
                for origin in rollup.by_origin
            ],
            "usage": _usage_payload(rollup),
        }
    return JSONResponse(payload)


CONNECT_ASK_AGAIN = "Ask the app to connect the account again."


async def connect_handoff(ctx: SurfaceContext, request: Request) -> Response:
    """The outbound leg of one turn's connect handoff: mint this member's own consent URL and send
    the window that opened here on to the provider.

    Minting happens on the press rather than when the reply was drawn. The state a consent URL
    carries is signed for minutes and the member reads at their own pace, so one minted for a chip
    nobody had pressed yet is spent before they reach it — and re-minting it on every read of the
    conversation would write to the turn to answer a read. The chip carries this address instead, so
    the press is what mints, and what the member lands on is always live.

    A request that can no longer be opened says so where the window is, which is the only place the
    member is looking."""
    reached = await _member_turn(ctx, request)
    if isinstance(reached, Response):
        return reached
    member_id, turn_id, _email = reached
    try:
        url = await ctx.connect_url(turn_id, member_id)
    except ConnectRequestInvalid:
        return callback_page(
            headline="This connection request is no longer available.",
            detail=CONNECT_ASK_AGAIN,
            status=404,
        )
    return RedirectResponse(url, status_code=303)


async def _member_turn(
    ctx: SurfaceContext,
    request: Request,
    *,
    named_turn: UUID | None = None,
    allow_commentable: bool = False,
) -> tuple[UUID, UUID, str] | Response:
    """One turn this member may reach, as the member, the turn, and the email their audience is
    resolved from, or the refusal to answer with. A mutation requires the member's own turn; a
    stream may also read a Slack or terminal conversation they may comment in. The agent must still
    be reachable by their web audience or that conversation."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    absent = Response("That turn is not available.", status_code=404, headers={REFUSAL_HEADER: "1"})
    if named_turn is None:
        try:
            turn_id = UUID(request.path_params["turn_id"])
        except ValueError:
            return absent
    else:
        turn_id = named_turn
    detail = await ctx.turn_detail(turn_id)
    if detail is None:
        return absent
    owner = detail.turn.speaker_member_id or await ctx.turn_owner(turn_id)
    agent_visible = audience.allows(detail.turn.agent_id)
    conversation = (
        await _member_chat(
            ctx,
            web_extension().store,
            detail.turn.agent_id,
            member_id,
            email,
            detail.turn.conversation_id,
            agent_visible=agent_visible,
        )
        if owner != member_id or not agent_visible
        else None
    )
    if owner != member_id and (
        not allow_commentable or conversation is None or not _commentable(conversation, member_id)
    ):
        if owner is None:
            return absent
        return Response(
            "That turn belongs to another member.",
            status_code=403,
            headers={REFUSAL_HEADER: "1"},
        )
    if not agent_visible and conversation is None:
        return absent
    return member_id, turn_id, email


async def stream(ctx: SurfaceContext, request: Request) -> Response:
    reached = await _member_turn(ctx, request, allow_commentable=True)
    if isinstance(reached, Response):
        return reached
    member_id, turn_id, email = reached
    since = request.headers.get("last-event-id", "")
    return StreamingResponse(
        _events(ctx, turn_id, member_id, since, email), media_type="text/event-stream"
    )


def _event(name: str, payload: dict[str, object]) -> bytes:
    return f"event: {name}\ndata: ".encode() + json.dumps(payload).encode() + b"\n\n"


async def _pending_prompts(
    ctx: SurfaceContext, request_: CredentialRequest
) -> dict[str, object] | None:
    """The credential prompts of a terminal request still awaiting values, as the page renders
    them — the same per-slot gate the terminal shell uses, so a fulfilled or expired prompt never
    re-renders on reconnect while an unanswered sibling keeps asking."""
    pending = [
        {"slot": prompt.slot, "prompt": prompt.prompt}
        for prompt in request_.prompts
        if await ctx.credential_prompt_pending(request_.sealed, prompt.slot)
    ]
    if not pending:
        return None
    return {"reason": request_.reason, "sealed": request_.sealed, "prompts": pending}


def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]:
    """One shared file as the chat draws it, carrying a `preview_url` when the file is itself a
    picture — the chat draws those inline in the reply. Both links carry their base: the
    chat is drawn by the portal and by an app page framed on its own origin, so a picture named
    without one resolves against whichever origin happens to draw it. `media_type` is how the chat
    knows which cards the artifacts sidebar can draw as a document."""
    return {
        "filename": artifact.filename,
        "subject": artifact.subject,
        "media_type": artifact.media_type,
        "size_bytes": artifact.size_bytes,
        "url": ctx.artifact_link(artifact),
        "preview_url": ctx.artifact_preview_link(artifact),
    }


def _opens(audience: WebAudience) -> frozenset[UUID]:
    return frozenset(agent.id for agent in audience.agents)


async def _created_apps(
    ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]
) -> dict[str, list[dict[str, object]]]:
    """The applications each turn created, keyed by that turn: the mark, the name, and the model
    the card draws, beside the id the portal opens the app at. A terminal frame names every kind
    the turn created and the surface draws the one it has a card for; the id an app is opened by is
    the workspace's to answer, so the names are resolved against it here and one the workspace no
    longer holds draws nothing. `opens` is the reader's own audience: an application defaults to
    private, and a conversation they may read can name one they may not, so a card is drawn only
    for an application the card would open for them."""
    wanted = {ref.name for refs in created.values() for ref in refs if ref.kind == AGENT_KIND}
    if not wanted:
        return {}
    known = {
        agent.name: agent
        for agent in await ctx.list_agents()
        if agent.name in wanted and agent.id in opens
    }
    drawn: dict[str, list[dict[str, object]]] = {
        turn_id: [
            {
                "id": str(known[ref.name].id),
                "name": known[ref.name].name,
                "model": known[ref.name].model,
                "icon": known[ref.name].icon,
            }
            for ref in refs
            if ref.kind == AGENT_KIND and ref.name in known
        ]
        for turn_id, refs in created.items()
    }
    return {turn_id: cards for turn_id, cards in drawn.items() if cards}


async def _events(
    ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str
) -> AsyncIterator[bytes]:
    """The turn as the live chat draws it. A created application is gated on the audience read at
    the terminal frame, not the one the stream opened on: the streamed turn is itself what creates
    the application, so the reader's audience holds it only once that turn has ended.

    A connect act is drawn on the same terms the transcript draws it on: for the member who asked
    and nobody else, since the grant lands on whoever presses, and only where the deploy holds the
    machinery a press would need."""
    async with ctx.tail(turn_id, since) as frames:
        async for cursor, frame in frames:
            if isinstance(frame, Terminal):
                detail = await ctx.turn_detail(turn_id)
                if detail is not None:
                    spawned = await ctx.conversation_subagent_turns(detail.turn.conversation_id)
                    lineage = {turn_id}
                    mine = []
                    for spawn in spawned:
                        if spawn.parent_turn_id in lineage:
                            lineage.add(spawn.id)
                            mine.append(spawn)
                    nodes = await _subagent_nodes(ctx, tuple(mine))
                    for run in nodes.get(str(turn_id), []):
                        yield _event("subagent", dict(run))
                request = frame.frame.connect_request
                if (
                    request is not None
                    and request.requester_member_id == member_id
                    and ctx.connect_available()
                ):
                    yield _event("connect", _connect_control(ctx, request.provider, turn_id))
                if frame.frame.credential_request is not None:
                    prompts = await _pending_prompts(ctx, frame.frame.credential_request)
                    if prompts is not None:
                        yield _event("credentials", prompts)
                files = [
                    _file_payload(ctx, artifact) for artifact in await ctx.shared_artifacts(turn_id)
                ]
                if files:
                    yield _event("files", {"files": files})
                if frame.frame.created:
                    audience = await web_audience(ctx, web_extension(), email)
                    apps = await _created_apps(
                        ctx, {str(turn_id): frame.frame.created}, _opens(audience)
                    )
                    if apps:
                        yield _event("apps", {"apps": apps[str(turn_id)]})
            yield _sse(cursor, frame)


async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response:
    """Land one privately-entered credential value — the web leg of the same handoff the terminal
    shell and Slack run. The value crosses only in the form body, becomes no message, and reaches
    no transcript; the privileged fulfillment verifies the seal (workspace, requesting member,
    named slot, freshness) before the encrypted store takes it."""
    auth = await _authenticate(ctx, request)
    if isinstance(auth, Response):
        return auth
    member_id, _email = auth
    refused = _framed_length(request, MAX_FORM_BYTES)
    if refused is not None:
        return refused
    form = await _form(request)
    if isinstance(form, Response):
        return form
    sealed = form.get("sealed", "")
    slot = form.get("slot", "")
    value = form.get("value", "")
    if not isinstance(sealed, str) or not isinstance(slot, str) or not isinstance(value, str):
        return Response("sealed, slot, and value are required", status_code=400)
    if not sealed or not slot or not value.strip():
        return Response("sealed, slot, and value are required", status_code=400)
    if len(value.encode()) > MAX_SECRET_BYTES:
        return Response("value too large", status_code=413)
    try:
        await ctx.fulfill_credential_request(sealed, slot, value.strip(), member_id)
    except CredentialRequestInvalid as error:
        return Response(f"not stored: {error}", status_code=403)
    return JSONResponse({"stored": slot})


async def admin_index(ctx: SurfaceContext, request: Request) -> Response:
    """The administration read: every agent with its policy, surface installations, and
    web-audience grants; members and seat state; every spend cap with its subject named; and the
    deploy's shape — installed extensions and the sandbox public-internet ceiling. Recorded
    disclosures of private transcripts are absent: that record is the operator's, read with
    `ufoctl transcript-reads`. It answers a workspace admin only and is not-found for everyone
    else. Reads only; every mutation stays a chat act — caps are the deploy operators' today (no
    object kind owns them), and the plan, invoices, and payment methods are managed in chat
    (`manage_billing`)."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, _email, audience = resolved
    if not audience.admin:
        return Response("no such page", status_code=404)
    extension = web_extension()
    installations = await ctx.list_installations()
    grants = await granted_emails(extension.store)
    async with extension.transaction() as connection:
        snapshot = await Seats(ctx.workspace_id).snapshot(connection)
    return JSONResponse(
        {
            "agents": [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "main": agent.main,
                    "model": agent.model,
                    "icon": agent.icon,
                    "internet_access_allowed": agent.internet_access_allowed,
                    "installations": [
                        entry.surface for entry in installations if entry.agent_id == agent.id
                    ],
                    "web_audience": list(grants.get(agent.id, ())),
                }
                for agent in audience.agents
            ],
            "members": [
                {
                    "id": str(entry.id),
                    "email": entry.email,
                    "admin": entry.admin,
                    "seated": entry.seated,
                }
                for entry in snapshot.members
            ],
            "caps": [entry.model_dump(mode="json") for entry in await ctx.spend_caps()],
            "deploy": {
                "sandbox_internet": ctx.deploy_sandbox_internet,
                "extensions": [entry.model_dump(mode="json") for entry in ctx.deploy_extensions],
            },
        }
    )


async def _object_gate(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, WebAudience, PortalKind] | Response:
    """The shared entry of both object pages: the session's member, the web audience walling every
    agent namespace a read may run in, and the kind's declared fields and spec schema. A kind this
    deploy does not register is not-found by name, never a 500 from inside it."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    kind = request.path_params["kind"]
    described = ctx.object_kind(kind)
    if described is None:
        return Response(f"no object kind named {kind!r}", status_code=404)
    return member_id, audience, described


def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response:
    """The one agent namespace a read runs in, named by `agent` and gated by the viewer's web
    audience like every per-agent panel."""
    try:
        agent_id = UUID(request.query_params.get("agent", ""))
    except ValueError:
        return Response("no such agent", status_code=404)
    for agent in audience.agents:
        if agent.id == agent_id:
            return agent
    return Response("no such agent", status_code=404)


def _kind_payload(kind: PortalKind) -> dict[str, object]:
    return {
        "kind": kind.kind,
        "fields": list(kind.list_fields),
        "spec_schema": kind.spec_schema,
        "applies": kind.kind in ApplyIntent.applying_kinds(),
        "deletes": kind.kind in ApplyIntent.deleting_kinds(),
    }


def _filter_value(raw: str) -> JsonValue:
    """One query-string filter value as the kind's rows carry it. A declared field holds whatever
    scalar its kind produces — `paused=true` is a boolean, `port=3000` a number — so each value is
    read as JSON and falls back to the string it already is."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _fanout_token(walking: dict[str, str]) -> str | None:
    """One opaque continuation for a fanned-out index — each still-walking agent's own kind cursor
    under its id — or None when every agent's walk is done."""
    if not walking:
        return None
    return json.dumps(walking, sort_keys=True).encode().hex()


def _fanout_walks(token: str) -> dict[UUID, str] | None:
    """The per-agent cursors a fan-out token carries, or None for a token this route never
    minted."""
    try:
        decoded = json.loads(bytes.fromhex(token).decode())
    except ValueError:
        return None
    if not isinstance(decoded, dict) or not decoded:
        return None
    walks: dict[UUID, str] = {}
    for agent, held in decoded.items():
        if not isinstance(held, str) or not held:
            return None
        try:
            walks[UUID(agent)] = held
        except ValueError:
            return None
    return walks


def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]:
    """Where one row falls in a fanned-out index. Each agent answers its own ordered page, so the
    merge re-ranks every row on the same field — absent, then flags, then numbers, then text, ties
    broken by name — rather than leaving the page standing in agent blocks."""
    name = str(row["name"])
    match row.get(order_by):
        case None:
            return (0, "", name)
        case bool() as flag:
            return (1, int(flag), name)
        case int() | float() as number:
            return (2, number, name)
        case value:
            return (3, str(value), name)


async def object_index(ctx: SurfaceContext, request: Request) -> Response:
    """One object kind's rows for the signed-in member — the portal's index projection, answering
    through the kind's own visibility gate and searched, filtered, and ordered on the fields the
    kind declared. `agent` names one agent's namespace; without it the read fans out over every
    agent the viewer's web audience holds, and every row names the agent that owns it either way,
    so a section listing one kind across the workspace addresses each edit to the right lane. `q`
    searches, `order_by`/`order` sort, `cursor` continues the walk, and every remaining
    query parameter is an exact filter; a field the kind never declared is the kind's own refusal,
    so the page offers only what the kind admits. A fanned-out read takes `OBJECT_FANOUT_LIMIT`
    rows from each agent, re-ranks the merge, and continues on a compound token — one kind cursor
    per agent still walking, so each agent's page resumes exactly where its own walk stopped and
    an agent whose rows ran out leaves the token."""
    gated = await _object_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, kind = gated
    reserved = {"agent", "q", "order_by", "order", "cursor"}
    order: Literal["asc", "desc"] = "asc"
    match request.query_params.get("order", "asc"):
        case "asc":
            order = "asc"
        case "desc":
            order = "desc"
        case _:
            return Response("order must be asc or desc", status_code=400)
    named = bool(request.query_params.get("agent", ""))
    cursor = request.query_params.get("cursor", "")
    agents = audience.agents
    continuations: dict[UUID, str] = {}
    if named:
        one = _object_agent(request, audience)
        if isinstance(one, Response):
            return one
        agents = (one,)
    elif cursor:
        walks = _fanout_walks(cursor)
        if walks is None:
            return Response("malformed fan-out cursor", status_code=400)
        continuations = walks
        agents = tuple(agent for agent in audience.agents if agent.id in walks)
    query = ObjectListQuery(
        query=request.query_params.get("q", ""),
        filters={
            name: _filter_value(value)
            for name, value in request.query_params.items()
            if name not in reserved
        },
        order_by=request.query_params.get("order_by", "name"),
        order=order,
        cursor=cursor,
    )
    rows: list[dict[str, object]] = []
    walk: str | None = None
    walking: dict[str, str] = {}
    for agent in agents:
        try:
            page = await ctx.list_member_objects(
                kind.kind,
                agent.id,
                member_id,
                admin=audience.admin,
                query=(query if named else replace(query, cursor=continuations.get(agent.id, ""))),
            )
        except ValueError as error:
            return Response(str(error), status_code=400)
        if page is None:
            return Response(f"{kind.kind} does not list in the portal", status_code=404)
        rows.extend(
            {
                "name": row.name,
                "summary": row.summary,
                **row.fields,
                "agent_id": str(agent.id),
                "agent_name": agent.name,
            }
            for row in page.rows[:OBJECT_FANOUT_LIMIT]
        )
        if named:
            walk = page.next_cursor
        elif page.next_cursor:
            walking[str(agent.id)] = page.next_cursor
    if not named:
        rows.sort(key=lambda row: _merged_rank(row, query.order_by), reverse=order == "desc")
        walk = _fanout_token(walking)
    return JSONResponse({**_kind_payload(kind), "objects": rows, "next_cursor": walk})


async def object_detail(ctx: SurfaceContext, request: Request) -> Response:
    """One object as the signed-in member reads it: the spec its kind applied, the declared fields
    that are its live state, its typed outgoing links — each naming an object and saying whether
    this member's read of that row answers, since a kind reading for members is not that row
    reading for this one — and the row's timestamps. `spec` is null where the kind elides content
    the member may not read. A row the member may not see is not-found, exactly as an absent
    one is."""
    gated = await _object_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, kind = gated
    agent = _object_agent(request, audience)
    if isinstance(agent, Response):
        return agent
    agent_id, admin = agent.id, audience.admin
    name = request.path_params["name"]
    found = await ctx.member_object(kind.kind, name, agent_id, member_id, admin=admin)
    if found is None:
        return Response(f"no {kind.kind} named {name!r}", status_code=404)
    detail = found.detail
    opened = [
        await ctx.member_object(
            link.target.kind, link.target.name, agent_id, member_id, admin=admin
        )
        is not None
        for link in detail.links
    ]
    return JSONResponse(
        {
            **_kind_payload(kind),
            "name": found.row.name,
            "summary": found.row.summary,
            "spec": detail.spec.model_dump(mode="json") if detail.spec_visible else None,
            "generation": None if detail.generation is None else str(detail.generation),
            "status": dict(found.row.fields),
            "links": [
                {
                    "relation": link.relation,
                    "kind": link.target.kind,
                    "name": link.target.name,
                    "opens": opens,
                }
                for link, opens in zip(detail.links, opened, strict=True)
            ],
            "created_at": _iso(detail.created_at),
            "updated_at": _iso(detail.updated_at),
        }
    )


def _sse(cursor: str, frame: LiveFrame) -> bytes:
    """One SSE event. A non-empty cursor is emitted as the event `id:`, which the browser echoes as
    `Last-Event-ID` on reconnect, so a dropped stream resumes from the last frame it rendered."""
    head = f"id: {cursor}\n".encode() if cursor else b""
    match frame:
        case Terminal():
            payload = frame.frame.model_dump_json().encode()
            return head + b"event: terminal\ndata: " + payload + b"\n\n"
        case Parked():
            return head + b"event: parked\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case CostTick():
            return head + b"event: cost\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case ToolCall():
            return head + b"event: tool\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case SkillLoad():
            return head + b"event: skill\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case SubagentActivity():
            return (
                head
                + b"event: subagent_activity\ndata: "
                + frame.model_dump_json().encode()
                + b"\n\n"
            )
        case Absorbed():
            return head + b"event: absorbed\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case Resumed():
            return head + b"event: resumed\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case Reply(is_comment=True):
            return head + b"event: comment\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case Reply():
            return head + b"event: reply\ndata: " + frame.model_dump_json().encode() + b"\n\n"
        case TextDelta():
            return head + b"data: " + frame.model_dump_json().encode() + b"\n\n"
        case _:
            raise ValueError(f"unmapped live frame {type(frame).__name__}")


async def intents(ctx: SurfaceContext, request: Request) -> Response:
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, email, _audience, agent_id = gated
    return await submit_intent(ctx, request, agent_id, member_id, email)


DIRECT_WRITE_TIMEOUT_SECONDS = 120
OBJECT_WRITE_MAX_BYTES = 65_536


def _write_agent(
    request: Request, audience: WebAudience, stated: object = None
) -> AgentSummary | Response:
    """The agent namespace a direct write runs in: the body's `agent` field when given — the shape
    the bridge client posts — else the `agent` query param, else the workspace main agent."""
    raw = (stated if isinstance(stated, str) else request.query_params.get("agent", "")).strip()
    if raw:
        try:
            agent_id = UUID(raw)
        except ValueError:
            return Response("no such agent", status_code=404)
        for agent in audience.agents:
            if agent.id == agent_id:
                return agent
        return Response("no such agent", status_code=404)
    main = next((agent for agent in audience.agents if agent.main), None)
    if main is None:
        return Response("no main agent", status_code=404)
    return main


def _direct_result(frame: TerminalFrame, name: str) -> Response:
    if frame.status == "done":
        return JSONResponse({"ok": True, "name": name, "detail": frame.text or ""})
    reason = frame.error_message or frame.text or f"not applied ({frame.status})"
    return JSONResponse({"ok": False, "name": name, "detail": reason})


async def object_write(ctx: SurfaceContext, request: Request) -> Response:
    """The bridge's write path: an app frame's object create/update/delete, run under the member's
    own session as a prepared-intent turn on the member's durable intent conversation. The turn
    dispatches object_apply/object_delete verbatim (no model round), the object verb path journals
    it, and the typed result or refusal returns synchronously. The delete route carries the object
    name in its path (`objects/{kind}/{name}/delete`); the create/update route names it in the
    body. Prototype: the write carries no per-request confirmation gate — a frame-initiated write
    acts under the viewer's session (RFC 0039 security debt #1/#2)."""
    delete = "name" in request.path_params
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    kind = request.path_params["kind"]
    if ctx.object_kind(kind) is None:
        return Response(f"no object kind named {kind!r}", status_code=404)
    if delete:
        agent = _write_agent(request, audience)
        if isinstance(agent, Response):
            return agent
        name = request.path_params["name"]
        intent = ToolIntent(tool="object_delete", input={"kind": kind, "name": name})
    else:
        try:
            body = await request.json()
        except ValueError:
            return Response("body must be JSON", status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("spec"), dict):
            return Response("body must be a JSON object carrying a spec mapping", status_code=400)
        agent = _write_agent(request, audience, body.get("agent"))
        if isinstance(agent, Response):
            return agent
        raw_name = body.get("name")
        if not isinstance(raw_name, str) or not raw_name:
            return Response("body must name the object", status_code=400)
        name = raw_name
        manifest = json.dumps({"kind": kind, "name": name, "spec": body["spec"]})
        if len(manifest.encode()) > OBJECT_WRITE_MAX_BYTES:
            return JSONResponse(
                {"ok": False, "name": None, "detail": "write too large"}, status_code=413
            )
        intent = ToolIntent(tool="object_apply", input={"manifest": manifest})
    conversation_id = await ctx.conversation_for(
        f"intent/{agent.id}/{email}", conversation_audience(member_id), agent_id=agent.id
    )
    admitted = await ctx.admit(
        conversation_id, intent.model_dump_json(), speaker_member_id=member_id, intent=intent
    )
    try:
        async with (
            ctx.tail(admitted.turn_id) as frames,
            asyncio.timeout(DIRECT_WRITE_TIMEOUT_SECONDS),
        ):
            async for _cursor, frame in frames:
                match frame:
                    case Terminal():
                        return _direct_result(frame.frame, name)
                    case Parked():
                        return JSONResponse({"ok": False, "name": name, "detail": frame.message})
    except TimeoutError:
        return JSONResponse(
            {"ok": False, "name": name, "detail": "still applying"}, status_code=504
        )
    raise RuntimeError("the write turn's tail ended without a terminal frame")


OBJECT_CHANGES_LIMIT = 200


async def object_changes(ctx: SurfaceContext, request: Request) -> Response:
    """The admin audit read of the object-change journal: the workspace's recent create/update/
    delete rows, each naming the verb, the caller (`member:<id>` or `turn:<id>`), the kind and
    name, and when. Admin-only — the journal is the operator's record, not a member surface (RFC
    0039). The stored spec bodies stay off this read: a kind's own read redacts what its owner
    withheld (a private task's prompt), and an audit row must not answer what the record refuses,
    so the audit states that a change happened and the kind stays the one door to its content."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, _email, audience = resolved
    if not audience.admin:
        return Response("admin only", status_code=404)
    changes = await ctx.recent_object_changes(OBJECT_CHANGES_LIMIT)
    return JSONResponse(
        {
            "changes": [
                {
                    "kind": change.kind,
                    "name": change.name,
                    "verb": change.verb,
                    "caller": change.caller,
                    "agent_id": str(change.agent_id),
                    "at": change.created_at.isoformat(),
                }
                for change in changes
            ]
        }
    )


async def settings(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's configuration read — its prompt, spec, bound surfaces, and the
    deploy's ceilings — answering the agent's whole web audience, so every member reads the main
    agent's settings; the web-audience grant list inside it stays the admin's."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    summary = next(agent for agent in audience.agents if agent.id == agent_id)
    archivable = not summary.main and (audience.admin or summary.owner_member_id == member_id)
    return await agent_settings(ctx, agent_id, admin=audience.admin, archivable=archivable)


async def agent_setup(ctx: SurfaceContext, request: Request) -> Response:
    """What the selected app needs before it works: the accounts its provision declared, which of
    them this workspace already holds, the workspace credentials it cannot run without, and whether
    it holds the standing order that gives it an occasion to run.

    It answers the agent's whole web audience, like the settings read beside it — what an app runs
    on is what the app is, and a member who cannot see it cannot tell a resting app from an unwired
    one. Which account answered a provider is not here; that is `agents/{id}/connections`, gated on
    the member."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, _audience, agent_id = gated
    state = await ctx.agent_setup(agent_id, member_id)
    return JSONResponse(
        {**state.model_dump(mode="json"), "own_page": await _has_own_page(ctx, agent_id, member_id)}
    )


async def _has_own_page(ctx: SurfaceContext, agent_id: UUID, member_id: UUID) -> bool:
    """Whether this workspace has built this app its own page, read from the row the homepage read
    answers `set` from.

    It asks whether the workspace built one, not whether a page exists to draw. A shipped app always
    has a page — the deploy carries one for every workspace — so "is a page available" is answered
    `yes` from the first moment and could gate nothing.

    What it gates is where a shipped app stands. Until the workspace builds, the app stands on its
    setup screen, which is what holds the accounts, the installs, the cadences, and the Build app
    press; from the first build it stands on the page it built, for good. The deploy's own page is
    the shape that build starts from rather than a screen a member browses first — an app the
    workspace has not wired has nothing real to draw on it.

    Read the same way the homepage read reads it, so the two can never disagree about whose page
    this is."""
    page = await ctx.list_member_objects(
        SITE_KIND,
        agent_id,
        member_id,
        admin=True,
        query=ObjectListQuery(filters={"homepage_agent": str(agent_id)}),
    )
    if page is None:
        return False
    return any("site_url" in row.fields for row in page.rows)


async def homepage(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's homepage: the frame link of the hosted site `set_homepage` bound.
    Its audience is the agent's — every member for a workspace-visible agent, the owner and
    admins for a private one — the same rule the frame gates each visit on, so the read never
    hands out a link that renders a refusal. The binding is read past the row gate (the row's
    own column is dormant while bound) and this handler applies the agent rule itself; a binding
    that no longer resolves and an agent that never bound one answer the same absent state.

    Two states: `set` once a page is bound or shipped, and `none` where none exists yet. A first
    build in flight is simply a page not there yet, never a state of its own."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    summary = next(a for a in audience.agents if a.id == agent_id)
    await _assets_published(ctx.fleet_blob, apps())
    return JSONResponse(await _homepage_state(ctx, summary, member_id, audience.admin))


async def _homepage_state(
    ctx: SurfaceContext,
    summary: AgentSummary,
    member_id: UUID,
    admin: bool,
) -> dict[str, JsonValue]:
    """One agent's homepage: `set` for a forked hosted_site row or the shipped bundle, `none` when
    it has no page — a first page still building is simply `none` until it registers, and a page
    being rebuilt keeps its prior version (the forked row's url and generation, or the bundle still
    serving) so it answers `set` throughout. The boot index carries it on the agent object, so a
    screen paints the page from what boot resolved; the granular route begins polling after its
    first interval, avoiding a duplicate initial read while still landing a deployment without a
    reload. The agent rule gates the read — a private agent's page answers `none` to anyone but its
    owner and an admin. A shipped page's `deploy_generation` is the digest folded to a JS-safe int,
    so the frame's identity moves onto the new bundle across the next answer."""
    if summary.visibility != "workspace" and member_id != summary.owner_member_id and not admin:
        return {"state": "none"}
    page = await ctx.list_member_objects(
        SITE_KIND,
        summary.id,
        member_id,
        admin=True,
        query=ObjectListQuery(filters={"homepage_agent": str(summary.id)}),
    )
    bound = (
        next((row for row in page.rows if "site_url" in row.fields), None)
        if page is not None
        else None
    )
    if bound is not None:
        site_url = bound.fields["site_url"]
        if not isinstance(site_url, str):
            raise TypeError("site_url must be a string")
        return {
            "state": "set",
            "url": homepage_embed_url(site_url),
            "deploy_generation": bound.fields.get("deploy_generation", 0),
        }
    slug = shipped_app_slug(summary.provisioned_by)
    bundle = apps()
    if slug is not None and slug in bundle.slugs:
        url = shipped_homepage_url(
            ctx.public_base_url,
            ctx.workspace_id,
            slug,
            bundle.digest,
        )
        if url is not None:
            return {
                "state": "set",
                "url": url,
                "deploy_generation": int(bundle.digest[:13], 16),
            }
    return {"state": "none"}


PREVIEW_KINDS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".csv": "csv",
    ".md": "md",
    ".svg": "svg",
    ".mp4": "mp4",
    ".mov": "mov",
    ".webm": "webm",
    ".mkv": "mkv",
}


async def preview(ctx: SurfaceContext, request: Request) -> Response:
    """Render one attached file to a preview PNG so the composer shows the member the document they
    are about to send. The bytes go to the preview service and the picture comes straight back —
    nothing is stored and no turn is admitted: this changes nothing, it only renders for display, so
    it is not a member action the chat transport must carry. A valid member session is the whole
    gate; the render is agent-agnostic, so the route is not scoped to one."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return Response("unsupported body type", status_code=415)
    refused = _framed_length(request, MAX_REQUEST_BYTES)
    if refused is not None:
        return refused
    form = await _form(request)
    if isinstance(form, Response):
        return form
    uploads = [
        upload
        for upload in form.getlist("file")
        if isinstance(upload, UploadFile) and upload.filename
    ]
    if not uploads:
        return Response("no file", status_code=400)
    upload = uploads[0]
    kind = PREVIEW_KINDS.get(PurePosixPath(upload.filename or "").suffix.lower())
    if kind is None:
        return Response("unpreviewable type", status_code=415)
    png = await ctx.render_preview(kind, await upload.read())
    if png is None:
        return Response("no preview", status_code=415)
    return Response(png, media_type="image/png")


ROUTES = (
    SurfaceRoute(method="GET", path="", handler=portal_page),
    SurfaceRoute(method="POST", path="", handler=open_session),
    SurfaceRoute(method="GET", path="static/{asset:path}", handler=static_asset),
    SurfaceRoute(method="GET", path="api/agents", handler=agents_index),
    SurfaceRoute(method="GET", path="api/agents/status", handler=agents_status),
    SurfaceRoute(method="GET", path="api/chats", handler=chats_index),
    SurfaceRoute(method="GET", path="api/admin", handler=admin_index),
    SurfaceRoute(method="POST", path="agents/{agent_id}/chat", handler=chat),
    SurfaceRoute(method="POST", path="preview", handler=preview),
    SurfaceRoute(method="GET", path="agents/{agent_id}/transcript", handler=transcript),
    SurfaceRoute(method="GET", path="agents/{agent_id}/settings", handler=settings),
    SurfaceRoute(method="GET", path="agents/{agent_id}/setup", handler=agent_setup),
    SurfaceRoute(method="GET", path="agents/{agent_id}/homepage", handler=homepage),
    SurfaceRoute(method="POST", path="agents/{agent_id}/intents", handler=intents),
    SurfaceRoute(method="GET", path="agents/{agent_id}/connections", handler=connections),
    SurfaceRoute(method="GET", path="connections", handler=connection_pool),
    SurfaceRoute(method="GET", path="connector-catalog", handler=connector_catalog),
    SurfaceRoute(method="GET", path="github/coverage", handler=github_coverage),
    SurfaceRoute(method="GET", path="agents/{agent_id}/skills", handler=skills),
    SurfaceRoute(method="GET", path="agents/{agent_id}/skills/community", handler=community_skills),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/skills/community/{owner}/{repo}/{skill}",
        handler=community_skill,
    ),
    SurfaceRoute(method="GET", path="agents/{agent_id}/conversations", handler=conversations),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/transcript",
        handler=conversation_transcript,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/attachments/{path:path}",
        handler=conversation_attachment,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/slots",
        handler=conversation_slots,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/slots/{slot_id}",
        handler=conversation_slot,
    ),
    SurfaceRoute(method="GET", path="workspace/team", handler=workspace_team),
    SurfaceRoute(method="GET", path="workspace/sources", handler=workspace_sources),
    SurfaceRoute(method="GET", path="workspace/surfaces", handler=workspace_surfaces),
    SurfaceRoute(method="GET", path="workspace/credentials", handler=workspace_credentials),
    SurfaceRoute(method="GET", path="workspace/memory", handler=workspace_memory),
    SurfaceRoute(method="GET", path="workspace/first-run", handler=workspace_first_run),
    SurfaceRoute(method="GET", path="workspace/starters", handler=workspace_starters),
    SurfaceRoute(method="GET", path="objects/{kind}", handler=object_index),
    SurfaceRoute(method="GET", path="objects/{kind}/{name}", handler=object_detail),
    SurfaceRoute(method="POST", path="objects/{kind}", handler=object_write),
    SurfaceRoute(method="POST", path="objects/{kind}/{name}/delete", handler=object_write),
    SurfaceRoute(method="GET", path="workspace/object-changes", handler=object_changes),
    SurfaceRoute(method="GET", path="workspace/usage", handler=workspace_usage),
    SurfaceRoute(method="GET", path="turns/{turn_id}/stream", handler=stream),
    SurfaceRoute(method="GET", path="turns/{turn_id}/connect", handler=connect_handoff),
    SurfaceRoute(method="POST", path="credentials", handler=fulfill_credential),
)
