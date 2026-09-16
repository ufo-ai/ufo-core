"""The web portal on the core surface seam, in its live mode: the authenticated shell around the
member's agents — the page and the one POST that opens its session, per-agent chat with
cookie-authenticated turn admission (each member holds any number of conversations per agent,
opened by the first message and listed for the rail) and an SSE tail of each turn's live frames,
read projections (the agent index, conversation transcripts, settings, skills, per-agent usage,
and connections) beside the workspace-level views every member holds — sources, credential slots,
memory (latest first, searched across every reachable agent), shared artifacts, and usage (their
own window, plus the workspace rollup for an admin) — the two generic object reads every kind's
index and detail page is built on (`objects/{kind}` and `objects/{kind}/{name}`, each answering
through the kind's own gate in the named agent's namespace), and prepared intents, the panels'
one mutation path.

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
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from ufo_ext_imessage.cloud import imessage_offered
from ufo_ext_imessage.surface import SURFACE_IMESSAGE
from ufo_ext_imessage.tools import IMESSAGE_CONNECT_ACTION
from ufo_ext_sites.objects import SITE_KIND
from ufo_ext_sites.store import HostedSites
from ufo_ext_sites.surface import homepage_embed_url, shipped_homepage_url
from ufo_ext_slack.mentions import as_markdown
from ufo_ext_slack.surface import SURFACE_SLACK
from ufo_ext_slack.tools import SLACK_CONNECT_ACTION
from ufo_ext_ufo.surface import SURFACE_UFO

from ufo.sdk.accounting import MemberSpendReport, SpendReport
from ufo.sdk.audience import audience_subjects, conversation_audience
from ufo.sdk.balance import spend_admitted
from ufo.sdk.bearer import LOGIN_PATH, SESSION_COOKIE, verify_token, workspace_claim
from ufo.sdk.callback_page import callback_page
from ufo.sdk.context import (
    ExtensionContext,
    SourceReader,
    WorkspaceAgent,
)
from ufo.sdk.credentials import CredentialValueInvalid
from ufo.sdk.flags import flag_enabled
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
    WebSocket,
    set_session_cookie,
)
from ufo.sdk.hub import (
    ArtifactsChanged,
    LiveFrame,
    Parked,
    Terminal,
    TextDelta,
)
from ufo.sdk.listings import ListingCursor, MalformedCursor
from ufo.sdk.manifest import (
    CONVERSATION_ARTIFACTS_MAX,
    CONVERSATION_AUTOMATIONS_MAX,
    CONVERSATION_SITES_MAX,
    PREVIEW_KINDS,
    ArtifactsSlotPayload,
    AutomationsSlotPayload,
    ConversationArtifact,
    ConversationSlotContext,
    ConversationSlotItem,
    ConversationSlotPayload,
    ConversationSlotProvider,
    ImagePreview,
    SitesSlotPayload,
    WorkspaceChanges,
    raster_image_media_type,
)
from ufo.sdk.member_profiles import (
    MEMBER_PROFILE_KIND,
    PROFILE_PHOTO_MEDIA_TYPE,
    MemberProfile,
    profile_name,
)
from ufo.sdk.memory import MemoryMatch
from ufo.sdk.models import (
    ANTHROPIC_KEY_SLOT,
    AUTO_MODEL,
    OPENAI_KEY_SLOT,
    Message,
    ModelRequest,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    anthropic_client_id,
    openai_client_id,
)
from ufo.sdk.o11y import emit_metric, log
from ufo.sdk.objects import (
    AGENT_KIND,
    ARTIFACT_KIND,
    CONVERSATION_KIND,
    CREDENTIAL_KIND,
    MEMBER_KIND,
    SURFACE_KIND,
    ActionView,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
)
from ufo.sdk.record import ActivityEvent, SubagentRun, frame_event, frame_payload
from ufo.sdk.sandbox import shipped_app_slug
from ufo.sdk.surfaces import (
    AGENT_DETAIL_ELEMENT,
    MEMBER_ADMISSION,
    WORKSPACE_WRITE_MAX_BYTES,
    AgentSummary,
    BlobStore,
    ConnectRequestInvalid,
    CredentialRequest,
    CredentialRequestInvalid,
    KeyedAdmission,
    ListedConversation,
    PortalKind,
    SetupState,
    SharedArtifact,
    SpokenArrival,
    SurfaceAuth,
    SurfaceContext,
    SurfaceRoute,
    SurfaceSocket,
    TerminalFrame,
    ToolIntent,
    Turn,
    TurnContext,
    TurnRuntimeConfig,
    handshake_request,
    inbox_name,
    member_message_attachments,
    member_message_said,
    member_message_text,
)
from ufo.sdk.tools import ActionBinding
from ufo_ext_web.anthropic_login import (
    AUTHORIZE_PATH as ANTHROPIC_AUTHORIZE_PATH,
)
from ufo_ext_web.anthropic_login import (
    CODE_FIELD as ANTHROPIC_CODE_FIELD,
)
from ufo_ext_web.anthropic_login import (
    CODE_MISSING as ANTHROPIC_CODE_MISSING,
)
from ufo_ext_web.anthropic_login import (
    CODE_PATH as ANTHROPIC_CODE_PATH,
)
from ufo_ext_web.anthropic_login import (
    CODE_REFUSED as ANTHROPIC_CODE_REFUSED,
)
from ufo_ext_web.anthropic_login import (
    MAX_CODE_BYTES as ANTHROPIC_MAX_CODE_BYTES,
)
from ufo_ext_web.anthropic_login import (
    SIGN_IN_PATH as ANTHROPIC_SIGN_IN_PATH,
)
from ufo_ext_web.anthropic_login import (
    STATE_COOKIE as ANTHROPIC_STATE_COOKIE,
)
from ufo_ext_web.anthropic_login import (
    AnthropicCodeLogin,
)
from ufo_ext_web.anthropic_login import (
    verified_key as anthropic_verified_key,
)
from ufo_ext_web.audience import (
    WebAudience,
    granted_emails,
    web_audience,
    web_extension,
)
from ufo_ext_web.community import COMMUNITY, CommunityUnavailable
from ufo_ext_web.followups import (
    TAIL_MESSAGES,
    TAIL_TEXT_CHARS,
    FollowUpCache,
    Reading,
)
from ufo_ext_web.openai_login import (
    DEVICE_COOKIE,
    DEVICE_PATH,
    DEVICE_UNAVAILABLE,
    POLL_PATH,
    SIGN_IN_PATH,
    OpenAiDeviceLogin,
)
from ufo_ext_web.panels import (
    FIRST_RUN_PROVIDERS,
    MCP_SERVERS,
    PORTAL_LANE_PREFIX,
    STARTER_APP_EXTENSIONS,
    UNLOCKS_BY_NAME,
    ApplyIntent,
    AppUnlock,
    agent_settings,
    offered_models,
    submit_action,
    submit_intent,
)
from ufo_ext_web.shell import ShellRelay, shell_state
from ufo_ext_web.starters import (
    AUTOMATIONS_SLATE,
    MEMORY_LIMIT,
    MEMORY_TEXT_CHARS,
    Slate,
    StarterCache,
)

SOURCE = "ufo web"
TOKEN_FIELD = "token"
MAX_INBOUND_CHARS = 200_000
MAX_INBOUND_BYTES = 4 * MAX_INBOUND_CHARS
MAX_REQUEST_BYTES = 25 * 1024 * 1024
MAX_FORM_BYTES = 64 * 1024
MEMBERS_PREFIX = "members/"
PHOTO_VERSION_CHARS = 16
PHOTO_CACHE = "private, no-cache"
MAX_SECRET_BYTES = 4_096
UPLOAD_CHUNK_BYTES = 65_536
MAX_INBOUND_FILES = 10
"""How many files one send carries. A presigned attachment costs the body nothing, so the framing
cap bounds it no longer: this is what keeps one request from reading the store without end and
writing it all into one workspace."""
WEB_INBOX_DIR = "web-inbox"
FILES_NOTE = "[Attached files, saved in the workspace: {paths}]"
FILES_NOTE_RE = re.compile(r"\[Attached files, saved in the workspace: (?P<paths>[^]\n]+)\]\Z")
ANSWER_TURN_HEADER = "x-ufo-answer-turn"
ANSWER_QUESTION_HEADER = "x-ufo-answer-question"
TIMEZONE_HEADER = "x-ufo-timezone"
STOP_TURN_HEADER = "x-ufo-stop-turn"
MODEL_HEADER = "x-ufo-model"
CLICK_HEADER = "x-ufo-click"
CLICK_KIND_HEADER = "x-ufo-click-kind"
STARTER_CLICK = "starter"
THREAD_FOLLOWUP_CLICK = "thread-followup"
STARTER_CLICK_METRIC = "starter_click_total"
THREAD_FOLLOWUP_CLICK_METRIC = "thread_followup_click_total"
STARTER_KINDS = frozenset({"app", "check_in", "unlock"})
SESSION_FAULT_HEADER = "x-ufo-session-fault"
REFUSAL_HEADER = "x-ufo-refusal"
NO_MEMBER_FAULT = "no-member"
NO_SEAT_FAULT = "no-seat"
MAX_MEMORY_QUERY_CHARS = 500
MEMORY_RECENT_LIMIT = 100
MEMORY_RESULT_LIMIT = 100
MEMORY_KIND = "memory"
SETUP_READ_FANOUT = 8
OBJECT_READ_FANOUT = 8
OBJECT_FANOUT_LIMIT = 50
TASK_KINDS = ("scheduled_task", "source_trigger")
LAST_RUN_FIELD = "last_run_at"
TASK_LANE_SEPARATOR = "|"
COMMENT_SURFACES = frozenset({"slack", "ufo"})
SUBAGENT_ACTIVITY_LIMIT = 40
# The engine's envelope stands on every member message and names nothing; an extension's fold
# is the one a bubble reports.
HIDDEN_ELEMENTS = frozenset({AGENT_DETAIL_ELEMENT})
SUBAGENT_EVENT_LIMIT = 100
HISTORY_PAGE_MESSAGE_LIMIT = 100
HISTORY_PAGE_BYTE_LIMIT = 64 * 1024
TITLE_JOB_NAME = "chat_titles"
TITLE_JOB_SCHEDULE = "*/15 * * * * *"
TITLE_BATCH = 5
HOMEPAGE_SETTLED_PREFIX = "homepage-settled/"
"""Where the sweep records an agent it has finished with, and a key space of its own because the
marker changed meaning: the release before this one wrote `homepage-seed/<agent>` for a turn that
ran. That image serves until the new pods are ready and keeps writing its own key through the
rollout, so a new sweep reading the old space would read those writes as pages that are bound and
settle each of those agents for ever."""
HOMEPAGE_ATTEMPT_PREFIX = "homepage-attempt/"
SEED_JOB_NAME = "seed_homepages"
SEED_JOB_SCHEDULE = "0 */5 * * * *"
SEED_MAX_ATTEMPTS = 3
HOMEPAGE_TOOLS = (
    "spawn",
    "load_skill",
    "share_file",
    "action:site:deploy_website",
    "action:agent:set_homepage",
)
"""What a homepage build asks of the agent it fires on, and therefore what an allowlist must name
for the sweep to admit one.

An allowlist is the whole naming — an agent that declares these holds nothing else — so the set is
exactly the acts the build needs: reach the builder, load the skill that says how, show the member
the wireframe, host a page, and bind it. `deploy_website` is the gate itself, so the caller changes
nothing about what a page must pass; withholding it only removes callers. It is the one that reaches
a page bound by another conversation, which refuses a worker for having no speaker — the skill sends
that deploy back to this agent, and it needs the tool to take it."""
SEED_PROMPT = (
    "Build your homepage: the page members open for you on the Apps screen. It should state what "
    "you are for, what you watch, your recent work, and what you need from members. Spawn the "
    "application homepage builder to draw and build it, then bind what it hosted as your homepage."
)
BUILD_ASK = (
    "Build this workspace its own version of your page. Load your homepage skill and follow it."
)
"""What the portal's build press types into the composer the member sends it from. The words are
stated here and held against the frontend's own copy by a test: the routing eval measures this
exact string, and a phrasing that drifted on one side would route in the eval and not in the
portal."""
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
USAGE_RANGES = {
    "1d": 86_400,
    "7d": 604_800,
    "30d": 2_592_000,
    "90d": 7_776_000,
    "all": None,
}
PORTAL_PATH = "/surface/web"
ARRIVAL_PATHS = frozenset(
    {PORTAL_PATH, f"{PORTAL_PATH}/{SIGN_IN_PATH}", f"{PORTAL_PATH}/{ANTHROPIC_SIGN_IN_PATH}"}
)
CHAT_TARGET_PARAM = "c"
PORTAL_BUILD = "make build"
STATIC_DIR = Path(__file__).parent / "static"
PORTAL_FILE = STATIC_DIR / "index.html"
PORTAL_HTML = PORTAL_FILE.read_text() if PORTAL_FILE.is_file() else None
SIDEBAR_FILE = STATIC_DIR / "sidebar.html"
SIDEBAR_HTML = SIDEBAR_FILE.read_text() if SIDEBAR_FILE.is_file() else None
LANES_SHELL_FLAG = "enable-lanes-shell"
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


def asset_etags(table: Mapping[str, tuple[bytes, str]]) -> dict[str, str]:
    """The validator each entry of a served table answers with: its content hash, so bytes that
    changed transfer and bytes that did not answer 304. Every table a request can be served from
    derives its validators here, because a table and its validators built apart is a name served
    under another's hash — held for a year, and wrong for a year."""
    return {name: f'"{sha256(body).hexdigest()[:32]}"' for name, (body, _) in table.items()}


STATIC_ASSETS = load_assets(STATIC_DIR / "assets")
STATIC_ETAGS = asset_etags(STATIC_ASSETS)

DESIGN_SURFACE_ENV = "UFO_WEB_DESIGN_SURFACES"
DESIGN_SURFACE_SERVED = "true"
DESIGN_SURFACE_NAMES = ("blocks.html", "playground.html")
DESIGN_SURFACE_MEDIA_TYPE = "text/html; charset=utf-8"
DESIGN_DIR = STATIC_DIR / "design"


def design_surfaces_published(environ: Mapping[str, str]) -> bool:
    """Whether this deploy publishes the design surfaces.

    One image is promoted from testing into production, so which deploys carry these pages is the
    deploy's variable rather than the build's output: production sets nothing and goes on answering
    what it always answered. Only the exact affirmative opens them — unset, empty, and every other
    spelling withhold them — so a variable set wrong reads as a deploy that never asked rather than
    as one publishing its own documentation."""
    return environ.get(DESIGN_SURFACE_ENV, "").strip() == DESIGN_SURFACE_SERVED


def load_design_surfaces(directory: Path) -> dict[str, tuple[bytes, str]]:
    """The documentation pages for the component fork and the chat design system, by request name.

    Read once at import beside the built assets, because the tree is startup's to read and a page
    request runs on the loop. The set of names is closed and spelled out: these entries sit at the
    root of the build tree, where the portal shells sit too, so a suffix or a prefix rule over that
    directory would hand out `index.html` — the shell, addressed around its own handler. A deploy
    that skipped the frontend build holds neither page and serves the 404 it always did."""
    pages = {name: directory / name for name in DESIGN_SURFACE_NAMES}
    return {
        name: (path.read_bytes(), DESIGN_SURFACE_MEDIA_TYPE)
        for name, path in pages.items()
        if path.is_file()
    }


def load_design_assets(environ: Mapping[str, str]) -> dict[str, tuple[bytes, str]]:
    """The chunks the design pages name, by request name — nothing where the deploy withholds the
    pages.

    The gate is on the read, not only on the request, because this is 5.7 MB the portal never names
    and a pod that read it would hold it for the life of the process. The frontend builds it under
    `static/design/assets` rather than beside the portal's own chunks, so `STATIC_ASSETS` cannot see
    it and `publish_assets` cannot write it to the fleet store — that store answers for `assets/*`
    alone, and a design page is served from its own deploy's build or not at all. The keys keep the
    directory, so nothing under it can answer for a portal asset."""
    if not design_surfaces_published(environ):
        return {}
    return {
        f"{DESIGN_DIR.name}/{name}": asset
        for name, asset in load_assets(DESIGN_DIR / "assets").items()
    }


DESIGN_SURFACES = {**load_design_surfaces(STATIC_DIR), **load_design_assets(os.environ)}
DESIGN_SURFACE_ETAGS = asset_etags(DESIGN_SURFACES)
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
    posting a fresh token instead of being locked behind the stale cookie. An unresolved GET of an
    address a member arrives at cold — the portal page and the two provider doors, which are handed
    around in mail and in chat — redirects to the deploy's one sign-in page, so the portal offers no
    second way in and nothing of the shell is served to a stranger. Every other unresolved request
    stays the 401 it is, so a page's read or intent never answers a redirect its fetch would follow.
    Only a urlencoded body is read for the
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
        and request.url.path.rstrip("/") in ARRIVAL_PATHS
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
    return _asset_response(request, name, body, media_type, STATIC_ETAGS[name])


def _design_surface_response(request: Request, environ: Mapping[str, str]) -> Response | None:
    """The design surface a portal path names where this deploy publishes them, or None — which
    leaves the path the 404 it answers everywhere else.

    The table is the two pages and, under `design/assets/`, the chunks they name. A deploy that
    asked for nothing never read the chunks at all; the variable is read here as well because the
    pages are a kilobyte each and are read whatever it says, so this is what withholds them. The
    name then indexes that table, so a traversal
    sequence and a near miss alike resolve to no entry rather than to a file. A page carries no
    content hash and revalidates on every load — its name is fixed and its bytes change with the
    build behind it — while a chunk's hashed name is held for a year, as the portal's own is."""
    if not design_surfaces_published(environ):
        return None
    name = request.url.path.removeprefix(STATIC_PREFIX)
    surface = DESIGN_SURFACES.get(name)
    if surface is None:
        return None
    body, media_type = surface
    return _asset_response(request, name, body, media_type, DESIGN_SURFACE_ETAGS[name])


def _asset_response(
    request: Request, name: str, body: bytes, media_type: str, etag: str
) -> Response:
    """A content-hashed name addresses its own bytes, so the browser may hold it without asking
    again — the shell is `no-store` and names a fresh hash every deploy, so no cached asset outlives
    the page that names it. Anything else revalidates on every load."""
    cache = ASSET_IMMUTABLE if HASHED_ASSET_NAME.fullmatch(name) else ASSET_REVALIDATE
    headers = {"etag": etag, "cache-control": cache}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=media_type, headers=headers)


HASHED_ASSET_NAME = re.compile(r"(?:design/)?assets/.+-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+")
ASSET_IMMUTABLE = "public, max-age=31536000, immutable"
ASSET_REVALIDATE = "no-cache"
STATIC_STORE_PREFIX = "static/web/"
STORED_ASSET_NAME = re.compile(r"assets/[A-Za-z0-9._-]+")
STORED_ASSETS_MAX = 64

APPS_DIR = Path(__file__).parent / "apps"
APPS_STORE_PREFIX = "apps/"
APPS_SHARED_DIRS = frozenset({"assets"})
ASSET_PUBLISH_IN_FLIGHT = 24

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
    Every file is carried whatever its suffix. The tree is the served bytes — one document per
    app and the hashed `assets/` they name — and the ingress that serves it at a frame origin's
    root holds
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


async def publish_assets(blob: BlobStore, apps: AppsBundle) -> None:
    """Write this build's static assets and app pages into the shared store, skipping what is
    already there. Each half learns what it holds from one listing of its own prefix rather than a
    key at a time, and the writes that remain run together under `ASSET_PUBLISH_IN_FLIGHT`: the
    tree is 160 files and 6.6 MiB, so a round trip per file is the whole cost. The first failed
    write fails the publish, and the caller that awaited it. A listing that hit its cap leaves a
    key it did not see to be written again, which writes the bytes that key already holds: every
    name here is content addressed."""
    apps_prefix = f"{APPS_STORE_PREFIX}{apps.digest}/"
    stored, published = await asyncio.gather(blob.list(STATIC_STORE_PREFIX), blob.list(apps_prefix))
    held = {entry.key for entry in (*stored, *published)}
    missing = [
        (key, body)
        for key, body in (
            *((STATIC_STORE_PREFIX + name, body) for name, (body, _) in STATIC_ASSETS.items()),
            *((apps_prefix + path, body) for path, body in apps.files.items()),
        )
        if key not in held
    ]
    limit = asyncio.Semaphore(ASSET_PUBLISH_IN_FLIGHT)

    async def write(key: str, body: bytes) -> None:
        async with limit:
            await blob.put(key, body)

    await asyncio.gather(*(write(key, body) for key, body in missing))


def _assets_published(blob: BlobStore, apps: AppsBundle) -> "asyncio.Task[None]":
    """This process's one publish of its built assets into the shared store (RFC 0031): every pod
    writes its own set at boot, so a hash a page names is in the store before any pod is asked for
    it — the causal order that makes a mixed-version roll harmless.
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
        task = asyncio.create_task(publish_assets(blob, apps))
        _ASSET_PUBLISH = task
    return task


def start_asset_publish(blob: BlobStore) -> None:
    """Start this process's publish as the app loop opens (the surface's `boot`), so the pod is
    already writing before it takes a request. Every reader still awaits the task — the causal
    order RFC 0031 holds is unchanged — but no member's page is what starts it. A deploy that
    skipped the frontend build has no tree to publish and its pages fail naming the build."""
    if APPS is not None:
        _assets_published(blob, APPS)


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
    return _asset_response(request, name, *held)


async def portal_page(ctx: SurfaceContext, request: Request) -> Response:
    """Serve the portal shell to a request whose session resolved: the lanes shell where
    `enable-lanes-shell` answers true for the workspace, the sidebar shell — apps and chats in one
    sidebar — everywhere else, including every silence the flag read fails closed through.
    A session that expires while the
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
    if PORTAL_HTML is None or SIDEBAR_HTML is None or APPS is None:
        raise RuntimeError(f"portal app is not built — run `{PORTAL_BUILD}`")
    await _assets_published(ctx.fleet_blob, APPS)
    lanes = await flag_enabled(LANES_SHELL_FLAG, default=False)
    shell = portal_shell(PORTAL_HTML if lanes else SIDEBAR_HTML, rum_config(os.environ))
    return HTMLResponse(shell, headers={"cache-control": "no-store"})


ACCOUNTS_PATH = "workspace/accounts"
DISCONNECT_SUFFIX = "disconnect"
CONNECT_HASH = "#/workspace/credentials"
CONNECTED = "connected"
REFUSED = "refused"
PENDING = "pending"

CODING_ACCOUNTS = (
    ("openai", "ChatGPT", OPENAI_KEY_SLOT),
    ("anthropic", "Claude", ANTHROPIC_KEY_SLOT),
)


def _refused(message: str) -> Response:
    return JSONResponse({"status": REFUSED, "message": message})


async def workspace_accounts(ctx: SurfaceContext, request: Request) -> Response:
    """Which coding accounts this member has connected, one row per provider. The screen that offers
    to connect one and the screen that offers to replace it read the same row, so the first run and
    the settings panel can never disagree about what a member holds."""
    authenticated = await _authenticate(ctx, request)
    if isinstance(authenticated, Response):
        return authenticated
    member_id, _ = authenticated
    return JSONResponse(
        {
            "accounts": [
                {
                    "provider": provider,
                    "label": label,
                    "connected": await ctx.member_credential_stored(member_id, slot),
                }
                for provider, label, slot in CODING_ACCOUNTS
            ]
        }
    )


async def openai_device(ctx: SurfaceContext, request: Request) -> Response:
    """Open a device grant and hand back the code the member types at OpenAI. The device code is the
    handle that claims it, so it rides an HttpOnly cookie; only the short user code crosses."""
    authenticated = await _authenticate(ctx, request)
    if isinstance(authenticated, Response):
        return authenticated
    opened = await OpenAiDeviceLogin(client_id=openai_client_id()).request_code()
    if opened is None:
        return JSONResponse({"error": DEVICE_UNAVAILABLE})
    answer = JSONResponse(
        {
            "user_code": opened.user_code,
            "verification_uri": opened.verification_uri,
            "interval": opened.interval,
        }
    )
    set_session_cookie(
        answer,
        DEVICE_COOKIE,
        f"{opened.device_auth_id}.{opened.user_code}",
        samesite="lax",
        secure=ctx.cookie_secure,
    )
    return answer


async def openai_device_poll(ctx: SurfaceContext, request: Request) -> Response:
    """One poll of the device grant. Pending while the member is still at OpenAI; connected once the
    grant bought a key and it is stored; refused with the line they have to read."""
    authenticated = await _authenticate(ctx, request)
    if isinstance(authenticated, Response):
        return authenticated
    member_id, _ = authenticated
    device_auth_id, _, user_code = request.cookies.get(DEVICE_COOKIE, "").partition(".")
    if not device_auth_id or not user_code:
        return _refused(DEVICE_UNAVAILABLE)
    claimed = await OpenAiDeviceLogin(client_id=openai_client_id()).claim(device_auth_id, user_code)
    if claimed.status == "pending":
        return JSONResponse({"status": PENDING})
    if claimed.status == "refused":
        return _refused(claimed.refusal)
    await ctx.put_member_credential(member_id, OPENAI_KEY_SLOT, claimed.key)
    log("web.openai_signed_in", member=str(member_id), route="device")
    return JSONResponse({"status": CONNECTED})


async def anthropic_authorize(ctx: SurfaceContext, request: Request) -> Response:
    """Open an authorization the member finishes at Anthropic. Its page displays the code rather
    than returning it here, so they carry it back by hand and the verifier waits in a cookie."""
    authenticated = await _authenticate(ctx, request)
    if isinstance(authenticated, Response):
        return authenticated
    pending = AnthropicCodeLogin(client_id=anthropic_client_id()).authorize()
    answer = JSONResponse({"url": pending.url})
    set_session_cookie(
        answer, ANTHROPIC_STATE_COOKIE, pending.cookie, samesite="lax", secure=ctx.cookie_secure
    )
    return answer


async def anthropic_code(ctx: SurfaceContext, request: Request) -> Response:
    """Spend the code the member pasted back for an access token, stored under their own slot."""
    refused = _framed_length(request, ANTHROPIC_MAX_CODE_BYTES)
    if refused is not None:
        return refused
    authenticated = await _authenticate(ctx, request)
    if isinstance(authenticated, Response):
        return authenticated
    member_id, _ = authenticated
    form = await _form(request)
    if isinstance(form, Response):
        return form
    pasted = form.get(ANTHROPIC_CODE_FIELD, "")
    if not isinstance(pasted, str) or not pasted.strip():
        return _refused(ANTHROPIC_CODE_MISSING)
    grant = await AnthropicCodeLogin(client_id=anthropic_client_id()).claim(
        pasted, request.cookies.get(ANTHROPIC_STATE_COOKIE, "")
    )
    if grant is None or not await anthropic_verified_key(grant.access):
        return _refused(ANTHROPIC_CODE_REFUSED)
    await ctx.put_member_credential(member_id, ANTHROPIC_KEY_SLOT, grant.stored())
    log("web.anthropic_signed_in", member=str(member_id), route="code")
    return JSONResponse({"status": CONNECTED})


async def account_disconnect(ctx: SurfaceContext, request: Request) -> Response:
    """Drop the account this member connected, so they can replace one revoked or rotated. Only
    their own row goes: an admin's key and the workspace's own are not theirs to clear."""
    authenticated = await _authenticate(ctx, request)
    if isinstance(authenticated, Response):
        return authenticated
    member_id, _ = authenticated
    provider = request.path_params["provider"]
    slot = next((slot for name, _, slot in CODING_ACCOUNTS if name == provider), None)
    if slot is None:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    await ctx.clear_member_credential(member_id, slot)
    log("web.account_disconnected", member=str(member_id), route=provider)
    return JSONResponse({"status": "disconnected"})


async def connect_arrival(ctx: SurfaceContext, request: Request) -> Response:
    """A connect link in mail or chat: the flow lives in the portal, so the member lands there."""
    authenticated = await _authenticate(ctx, request)
    if isinstance(authenticated, Response):
        return authenticated
    return RedirectResponse(f"/surface/{ctx.surface}{CONNECT_HASH}", status_code=303)


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
    if not await ctx.member_has_access(member_id):
        return Response(
            "workspace access was removed",
            status_code=403,
            headers={SESSION_FAULT_HEADER: NO_SEAT_FAULT},
        )
    return member_id, email


async def static_asset(ctx: SurfaceContext, request: Request) -> Response:
    """Serve a portal stylesheet or module to a request whose session resolved. Only the shell
    names these, and the shell serves to a session, so an unresolved request is a 401 rather than
    a transfer. The assets carry no workspace data. A name this build does not hold is answered
    from the shared store, where every pod published its own build before serving pages — so a
    page from one build resolves on a pod running another.

    The two design surfaces answer here as well, on the deploys that opt into them: they and the
    chunks under `design/assets/` are read by the team through the same session, and the shared
    store publishes only `assets/*`, so a deploy serves them from its own build or not at all."""
    return (
        _static_response(request)
        or _design_surface_response(request, os.environ)
        or await _stored_asset(ctx.fleet_blob, request)
    )


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


class HomepageSeedAttempt(BaseModel):
    """What the sweep has already tried for one unbound agent."""

    model_config = ConfigDict(frozen=True)

    attempts: int = Field(default=0, ge=0)
    bucket: str = ""


@dataclass(frozen=True)
class HomepageSeed:
    """One homepage per agent — the batch job behind the Home tab's first fill.

    What settles an agent is the bound page, not the fact that a turn once ran for it: a build that
    refused, a spawn the provider dropped, a turn that ended without deploying all leave the agent
    with no homepage, and a marker written on the attempt would leave it with none forever. So the
    sweep asks the `site` kind whether this agent has a bound page and settles on the answer.

    An unbound agent fires once per day bucket, and the attempt count is what stops it: three
    buckets without a bound page and the agent is marked `unbuilt` and left alone, because a
    fourth identical attempt is not new information. Attempts are counted under their own prefix,
    which the candidate query does not count, so a workspace holding a half-tried agent stays due.

    The turn runs automatically in the agent owner's room, or the earliest-seated admin's room for
    an ownerless row, so its result lands where one member already reads it. The homepage answers
    the agent's audience from birth: the frame gates a bound site on the agent's visibility, so a
    workspace-visible agent's homepage (main is born one) reaches every member at once, a private
    agent's reaches its owner and admins, and flipping the agent object's visibility is what
    widens the page — no site row is rewritten.
    A shipped app agent (one an `app_*` extension provisioned) is marked settled without a turn:
    its homepage is the deploy-wide bundle served row-less, so it needs no build. Marking it rather
    than skipping it is what lets the candidate query settle — an unmarked agent it never builds
    would keep the workspace due forever.
    An agent whose allowlist withholds the deploy action is skipped and left unmarked: the
    negative is recomputed every pass, so an allowlist that later gains the action is seeded on the
    next sweep and a marker computed against a stale name can never outlive a deploy. An archived
    app is marked, since it admits no turn at all, and an ownerless agent in a workspace with no
    seated admin waits, unmarked, for one.
    The candidates gate on due work: a workspace whose agents are all marked never fires this
    handler, so a settled fleet costs nothing beyond one roster read per pass for a workspace that
    holds a withheld agent. `bucket` is the day the idempotency key names — a
    refused admission is a durable turn its key would answer forever, so a refusal costs at most
    one bucket's attempt while a crash between admitting and marking still dedupes to the turn
    already admitted."""

    ctx: ExtensionContext
    bucket: str
    sites: HostedSites

    async def sweep(self) -> None:
        agents = await self.ctx.workspace_agents()
        if not agents:
            return
        marked = {key for key, _ in await self.ctx.store.list(HOMEPAGE_SETTLED_PREFIX)}
        attempts = dict(await self.ctx.store.list(HOMEPAGE_ATTEMPT_PREFIX))
        for agent in agents:
            key = f"{HOMEPAGE_SETTLED_PREFIX}{agent.id}"
            if key in marked:
                continue
            settled = await self._settled(agent)
            if settled is not None:
                await self.ctx.store.put(key, settled)
                continue
            if agent.tools is not None and not set(HOMEPAGE_TOOLS) <= set(agent.tools):
                continue
            attempt_key = f"{HOMEPAGE_ATTEMPT_PREFIX}{agent.id}"
            attempt = HomepageSeedAttempt.model_validate(attempts.get(attempt_key) or {})
            if attempt.attempts >= SEED_MAX_ATTEMPTS:
                await self.ctx.store.put(key, "unbuilt")
                continue
            if attempt.bucket == self.bucket:
                continue
            await self._fire(agent, key, attempt_key, attempt)

    async def _settled(self, agent: WorkspaceAgent) -> str | None:
        if agent.archived:
            return "archived"
        if shipped_app_slug(agent.provisioned_by) is not None:
            return "shipped"
        if await self.sites.homepage(agent.id) is not None:
            return "bound"
        return None

    async def _recipient(self, agent: WorkspaceAgent) -> UUID | None:
        if agent.owner_member_id is not None:
            return agent.owner_member_id
        return await self.ctx.earliest_seated_admin()

    async def _fire(
        self, agent: WorkspaceAgent, key: str, attempt_key: str, attempt: HomepageSeedAttempt
    ) -> None:
        recipient = await self._recipient(agent)
        if recipient is None:
            return
        conversation_id = await self.ctx.open_conversation(
            agent.id, f"homepage/{agent.id}/{recipient}", member_id=recipient
        )
        turn_id = await self.ctx.invoke(
            conversation_id,
            agent.id,
            SEED_PROMPT,
            f"homepage-seed:{agent.id}:{self.bucket}",
            as_scheduled=True,
            runtime_config=TurnRuntimeConfig(internet_access=False),
        )
        if turn_id is None:
            raise RuntimeError(f"homepage seed for agent {agent.id} answered no turn")
        outcome = (await self.ctx.turn_outcomes((turn_id,))).get(turn_id)
        if outcome is not None and outcome.status == "cancelled":
            return
        if await self.sites.homepage(agent.id) is not None:
            await self.ctx.store.put(key, "bound")
            return
        await self.ctx.store.put(
            attempt_key,
            HomepageSeedAttempt(attempts=attempt.attempts + 1, bucket=self.bucket).model_dump(),
        )


async def seed_homepages(ctx: ExtensionContext, bucket: str | None = None) -> None:
    """One homepage per agent — the batch job behind the Home tab's first fill."""

    await HomepageSeed(
        ctx,
        bucket or datetime.now(UTC).date().isoformat(),
        HostedSites(ctx.store.workspace_id, ctx.transaction),
    ).sweep()


async def _open_conversation(
    ctx: SurfaceContext,
    agent_id: UUID,
    member_id: UUID,
    queue_key: str,
    text: str,
    paths: tuple[str, ...],
) -> tuple[UUID, str]:
    """Open a conversation under `queue_key`. A lost creation race on the queue key lands on the
    surviving conversation, whose winner named it, with the audience the winner left it: asking for
    the member's audience does not narrow a conversation already shared. The survivor must be what
    was asked for — this agent's, and one this member reads — or the key has been given to a
    conversation this surface did not open, which is a fault and never a chat.

    A portal chat is its member's at birth. Its member can share it with the workspace from the
    title's visibility control (`share_conversation`), which is a turn like any other act.

    What the conversation is called is core's, so the opening turn names it and the rail, the index
    and this reply all read the one string."""
    title = _chat_title(text, paths)
    minted = uuid4()
    audience = conversation_audience(member_id)
    conversation_id = await ctx.conversation_for(
        queue_key,
        audience,
        agent_id=agent_id,
        conversation_id=minted,
        preserve_existing_audience=True,
    )
    if conversation_id != minted:
        listed = await ctx.list_agent_conversations(
            agent_id, member_id, admin=False, limit=1, conversation_id=conversation_id
        )
        if not listed:
            raise RuntimeError(f"conversation {conversation_id} under {queue_key} is not a chat")
        return conversation_id, listed[0].title
    await ctx.retitle_conversation(conversation_id, title)
    return conversation_id, title


async def _spoken_conversation(
    ctx: SurfaceContext,
    audience: WebAudience,
    agent_id: UUID,
    member_id: UUID,
    conversation_id: UUID,
) -> ListedConversation | None:
    """The conversation this member may speak in under this agent, or None — another member's
    private one, a room, another agent's and an unknown id are one refusal, and every caller
    answers not-found."""
    if not audience.allows_chat(agent_id):
        return None
    listed = await ctx.list_agent_conversations(
        agent_id, member_id, admin=False, limit=1, conversation_id=conversation_id
    )
    if (
        listed
        and listed[0].speakable
        and _reaches(audience, agent_id, listed[0].audience, member_id)
    ):
        return listed[0]
    return None


def _reaches(audience: WebAudience, agent_id: UUID, held: str, member_id: UUID) -> bool:
    """The agent-reach half of a conversation being this member's to open or speak in, beside the
    audience half the row's `speakable` states: an agent in their web reach opens every
    conversation the row admits, and an agent reached only through a member-private extension
    conversation opens that conversation — their own, `held` being its audience — and no other."""
    return audience.allows(agent_id) or held == str(conversation_audience(member_id))


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
        author = email if sender is None else reported_speaker_name(sender, email)
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


APP_FLAGS = {
    "code": "enable-code-app",
    "issues": "enable-issues-app",
    "meetings": "enable-meetings-app",
    "metrics": "enable-metrics-app",
    "notification": "enable-notification-app",
    "wiki": "enable-wiki-app",
}
MAIN_AGENT_FLAG = "enable-assistant-app"
PORTAL_SURFACES = {
    "memory": "enable-memory-tab",
    "radar": "enable-radar-app",
    "community-skills": "enable-community-skills",
    "installed-skills": "enable-installed-skills",
    "app-store": "enable-app-store",
    "apps": "enable-apps-tab",
}
APP_STORE_FLAG = PORTAL_SURFACES["app-store"]
APPS_TAB_FLAG = PORTAL_SURFACES["apps"]
CLOSED_UNTIL_ANSWERED = frozenset([*APP_FLAGS.values(), APP_STORE_FLAG, APPS_TAB_FLAG])


def _visibility_flag(main: bool, provisioned_by: str | None) -> str | None:
    """The flag deciding whether the portal lists this agent, or None for one it always lists. One
    answer for a live row and an archived one: the deploy withholds an app, not a state of it."""
    if main:
        return MAIN_AGENT_FLAG
    return APP_FLAGS.get(shipped_app_slug(provisioned_by) or "")


async def _flag_reads(flags: Iterable[str | None]) -> dict[str, bool]:
    """Every flag the boot read consults, answered in one round: the portal's own screens and the
    visibility of each agent listed, live or archived.

    Each is read at the default its own feature ships in (`CLOSED_UNTIL_ANSWERED`). A flag
    withholding one of the portal's own screens reads open, so a deploy holding no flag service, one
    whose keys are unseeded, a key nobody has created and a Flagship outage all leave a member
    exactly what they had; a shipped app's flag, and the flag over a screen never offered before,
    read closed, so none of those four lists an app or the store, and a member sees one where
    somebody turned it on."""
    keys = list(
        dict.fromkeys([*PORTAL_SURFACES.values(), *(flag for flag in flags if flag is not None)])
    )
    answers = await asyncio.gather(
        *(flag_enabled(key, default=key not in CLOSED_UNTIL_ANSWERED) for key in keys)
    )
    return dict(zip(keys, answers, strict=True))


def _setup_configured(state: SetupState) -> bool:
    """Whether the app offers nothing or the member accepted one setup offer."""
    rows = (*state.connectors, *state.credentials, *state.standing)
    if not rows:
        return True
    return any(
        (
            *(connector.granted for connector in state.connectors),
            *(credential.filled for credential in state.credentials),
            *(order.armed for order in state.standing),
        )
    )


async def agents_index(ctx: SurfaceContext, request: Request) -> Response:
    """The portal's first read: the signed-in member and the agents their web audience holds — every
    agent for a workspace admin, the main agent plus the granted non-main agents for everyone else.
    `agents` is the set a member may open and message. An agent whose flag is off is marked `hidden`
    rather than dropped: the workspace holds the app either way, the portal draws it in no list, and
    a member holding its link still opens it. `surfaces` answers the same question for the portal's
    own screens: each flag is read at the default its own feature ships in, so a deploy whose flag
    service answers nothing draws the portal's own screens as it drew them before, and lists no
    shipped app, and `team` is answered by this reader's admin standing rather than by a flag —
    the roster is an admin's screen. A withheld screen keeps its address either way. `archived`
    contains the apps this member may restore, each carrying the slug and flag state a live row
    carries, so the store lists a shipped app the workspace removed as one to install and lists a
    withheld one nowhere.

    The create act draws nothing from this read:
    it is a conversation the `create-application` skill runs, and the screen offers it to every
    signed-in member, because the `agent` kind admits a create from any speaking member and stamps
    them the owner.

    `setup_due` says an app is installed and a required setup row is not settled. It rides this
    read rather than the status poll beside it: each answer costs a transaction, up to three
    selects, and an object-registry read per
    standing kind — a price a once-per-boot read pays and a four-second poll cannot. Only a
    provisioned row is asked, because the declaration is written where `provisioned_by` is: an
    agent a member built declares nothing and so owes nothing, and the asking runs
    `SETUP_READ_FANOUT` wide because that price is per app on the one request the member is
    waiting on: a workspace holding thirty apps would otherwise hold thirty transactions open at
    once on the pool the whole deploy shares, and eight keeps the read wide enough that its
    latency is the slowest answer rather than the sum of them.

    `stands_on_setup` says the app stands on its setup screen rather than on a page: it declared
    something a setup screen lists, and this workspace has never built it one. It rides this read
    because the portal draws its shell from this read — the setup screen draws no navigation, and a
    portal that learned the fact from a later per-app request would draw the sidebar and take it
    away again. The page row it needs is the row the homepage answer already reads, so the fact
    costs no query of its own.

    `models` is the model ids this deploy's registry serves, less a flagged model this workspace's
    flag withholds: the closed set the portal offers where a member chooses what an agent runs on,
    so no screen names a model a turn would refuse."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    grants = None
    if audience.admin:
        grants = await granted_emails(web_extension().store)
    await _assets_published(ctx.fleet_blob, apps())
    bound_pages = {agent.id: await _bound_page(ctx, agent, member_id) for agent in audience.agents}
    homepages = {
        agent.id: _homepage_state(ctx, agent, bound_pages[agent.id], audience.admin, member_id)
        for agent in audience.agents
    }
    provisioned = tuple(agent for agent in audience.agents if agent.provisioned_by is not None)
    fanout = asyncio.Semaphore(SETUP_READ_FANOUT)

    async def setup_of(agent: AgentSummary) -> SetupState:
        async with fanout:
            return await ctx.agent_setup(agent.id, member_id)

    setup_states = await asyncio.gather(*(setup_of(agent) for agent in provisioned))
    due = frozenset(
        agent.id
        for agent, state in zip(provisioned, setup_states, strict=True)
        if not (
            all(connector.granted for connector in state.connectors if connector.required)
            and all(credential.filled for credential in state.credentials if credential.required)
            and all(order.armed for order in state.standing if order.required)
        )
    )
    on_setup = frozenset(
        agent.id
        for agent, state in zip(provisioned, setup_states, strict=True)
        if (state.connectors or state.credentials or state.standing)
        and bound_pages[agent.id] is None
    )
    archived = [
        app
        for app in await ctx.list_archived_agents()
        if audience.admin or app.owner_member_id == member_id
    ]
    flags = await _flag_reads(
        [
            *(_visibility_flag(agent.main, agent.provisioned_by) for agent in audience.agents),
            *(_visibility_flag(False, app.provisioned_by) for app in archived),
        ]
    )

    def withheld(main: bool, provisioned_by: str | None) -> bool:
        key = _visibility_flag(main, provisioned_by)
        return key is not None and not flags[key]

    return JSONResponse(
        {
            "member": {
                "id": str(member_id),
                "email": email,
                "admin": audience.admin,
                "workspace_id": str(ctx.workspace_id),
                **_member_face(next(iter(await ctx.member_profiles(frozenset({member_id}))), None)),
            },
            "surfaces": {
                **{name: flags[key] for name, key in PORTAL_SURFACES.items()},
                "team": audience.admin,
                "email": ctx.sends_email,
            },
            "models": await offered_models(ctx.models),
            "archived": [
                {
                    "id": str(app.id),
                    "name": app.name,
                    "object": app.object_name,
                    "icon": app.icon,
                    "purpose": app.purpose,
                    "app": shipped_app_slug(app.provisioned_by),
                    "hidden": withheld(False, app.provisioned_by),
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
                    "mine": agent.owner_member_id == member_id,
                    "hidden": withheld(agent.main, agent.provisioned_by),
                    "homepage": homepages[agent.id],
                    "setup_due": agent.id in due,
                    "stands_on_setup": agent.id in on_setup,
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


async def agents_status(ctx: SurfaceContext, request: Request) -> Response:
    """Each visible agent's live picture, polled beside the index `agents_index` serves: the
    liveest non-terminal turn it holds — with what a running one is doing right now, peeked off
    the hub's newest activity frame — when any turn of its last moved, and whether its most
    recent terminal turn failed.

    An agent is visible workspace-wide; its turns are not. The aggregate is fenced to the
    conversations this reader reads, so a row reports this member's picture of the agent and never
    another member's private turn.

    Every tab holding the portal open polls this for as long as it is open, at four seconds while
    any agent in the workspace works, so it costs one turn aggregate for every agent at once and
    nothing per resting agent. The single per-agent read is the hub peek, taken only for the
    agents holding a running turn, because the frame it wants exists only while that turn does.

    The balance rides the same answer because the composer has to say, before a member types, that
    the workspace is out of credit — and the billing read that states it in full answers an admin
    only, so it cannot be what a member's composer asks. This is the same one-row headroom read the
    starters already take, on a poll every open tab already runs."""
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
        frame = await ctx.latest_activity(status.running_turn_id)
        if frame is not None:
            activity[status.agent_id] = frame.text
    return JSONResponse(
        {
            "statuses": [
                {
                    "agent_id": str(status.agent_id),
                    "turn": status.live,
                    "activity": activity.get(status.agent_id),
                    "last_active_at": _iso(status.last_active_at),
                    "last_failed": status.last_failed,
                }
                for status in statuses
            ],
            "out_of_credit": not await _solvent(ctx),
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


async def _parse_inbound(
    request: Request,
) -> tuple[str, tuple[UploadFile, ...], tuple[tuple[str, str], ...]] | Response:
    """The composer's message text, attached files, and the (key, signature) pairs a presigned
    upload already landed. A plain body is read under a hard byte cap, so what bounds it is the
    bytes consumed rather than a declared length, and it must decode as UTF-8 — bytes that don't are
    refused, never rewritten. A multipart submit must declare a length and must not be chunked — the
    parse buffers each part whole (in memory up to starlette's spool threshold, a temp file past
    it), so it runs only under a length the server itself frames the body by; its `message` text
    arrives already decoded by that parser (UTF-8, falling back to latin-1), so the strict-UTF-8
    refusal is the plain path's — the decoded text is admitted as received. A urlencoded body is not
    a shape the composer sends, so it is refused.

    A body carrying `uploaded_key` parts carries no file bytes for those attachments — the browser
    already PUT them to the blob store — so the framing bound applies to text and any inline
    fallback files only, never to what a key names. Each key rides with the signature this deploy
    struck for it; the send admits it only on that signature, verified against the caller's own
    workspace. One send carries at most `MAX_INBOUND_FILES` attachments of both kinds together — the
    bytes a key names cost the body nothing, so the count is what bounds what one request moves."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded"):
        return Response("unsupported body type", status_code=415)
    if not content_type.startswith("multipart/form-data"):
        body = await _bounded_body(request, MAX_INBOUND_BYTES)
        if isinstance(body, Response):
            return body
        try:
            return body.decode("utf-8"), (), ()
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
    keys, signatures = form.getlist("uploaded_key"), form.getlist("uploaded_sig")
    if len(keys) != len(signatures) or any(
        not isinstance(part, str) for part in (*keys, *signatures)
    ):
        return Response("malformed upload reference", status_code=400)
    presigned = tuple(
        (str(key), str(signature)) for key, signature in zip(keys, signatures, strict=True)
    )
    if len(uploads) + len(presigned) > MAX_INBOUND_FILES:
        return Response(f"a send carries at most {MAX_INBOUND_FILES} files", status_code=413)
    return text, uploads, presigned


def _inbox_paths(uploads: tuple[UploadFile, ...], keys: tuple[str, ...]) -> tuple[str, ...]:
    """Where each attachment lands in the conversation's workspace, inline files first. An uploaded
    key is named by the member's own file too, so both kinds arrive under the same safe leaf and a
    name taken twice in one send is numbered rather than overwritten."""
    used: set[str] = set()
    names = (
        *(upload.filename or "file" for upload in uploads),
        *(PurePosixPath(key).name for key in keys),
    )
    return tuple(f"{WEB_INBOX_DIR}/{inbox_name(name, used)}" for name in names)


async def _deliver_uploads(
    ctx: SurfaceContext,
    conversation_id: UUID,
    uploads: tuple[UploadFile, ...],
    presigned_keys: tuple[str, ...],
    rels: tuple[str, ...],
) -> tuple[str, ...]:
    """Land every attachment in the conversation's `web-inbox/` before the turn runs and answer the
    artifact key each sits under, in the order the message names them.

    A presigned attachment already sits under its artifact key — the browser PUT it there. An inline
    one reaches this process in the body (a dev deploy that signs no upload, or an upload that
    failed): it is streamed to the store here so it becomes an artifact like any other. Then the
    sandbox fetches each key into the workspace itself, so no attachment's bytes cross this process
    on the way in."""
    keys: list[str] = []
    for upload, rel in zip(uploads, rels[: len(uploads)], strict=True):
        keys.append(await ctx.store_inbound_file(PurePosixPath(rel).name, _upload_chunks(upload)))
    keys.extend(presigned_keys)
    for key, rel in zip(keys, rels, strict=True):
        await ctx.deliver_attachment(conversation_id, key, rel)
    return tuple(keys)


def _files_note(text: str, paths: tuple[str, ...]) -> str:
    """The admitted text naming the saved paths it carries."""
    note = FILES_NOTE.format(paths=", ".join(paths))
    return f"{text}\n\n{note}" if text.strip() else note


def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]:
    """The member's own words and the workspace paths the note at their foot names — the reader of
    `_files_note`, so a bubble draws what was attached as the files themselves rather than as the
    sentence naming where they landed. Words carrying no note are their own."""
    found = FILES_NOTE_RE.search(said)
    if found is None:
        return said, ()
    paths = tuple(path for path in (part.strip() for part in found["paths"].split(",")) if path)
    return said[: found.start()].rstrip(), paths


def _member_bubble(
    inbound: str, attached: Mapping[str, dict[str, object]], *, slack: bool = False
) -> dict[str, object]:
    """One bubble of the member's own words, taken out of the inbound the turn ran on and carrying
    the files its own note names. Admission wrote the note at the foot of the words and it
    addresses the model, so the words read clean; each file the note names is drawn from the turn
    artifact the member's send recorded, matched by name, or named as a plain card when no row
    carries it — a turn admitted before member attachments became artifacts, or one whose row a
    same-named file in the same turn already took.

    A channel surface names what it delivered in the attachments element it fenced beside the
    member's words rather than in a note under them, so a share from Slack draws its files off that
    element alone — the rows the share recorded, drawn the way the composer's own send is. A note
    inside fenced words is characters the member typed, and names no file.

    `slack` says the conversation stands on Slack, where a member's emphasis was markup Slack drew
    rather than characters they typed: a message the Slack surface fenced crosses as markdown and
    the bubble is marked so the portal draws it as the member saw it. A comment the same member
    typed into the portal of that conversation admits unfenced, so their `#` and `*` stay the
    characters they typed."""
    parts = member_message_said(inbound)
    if parts.fenced:
        words, paths = parts.said, member_message_attachments(inbound)
    else:
        words, paths = _member_attachments(parts.said)
    mrkdwn = slack and parts.fenced
    bubble: dict[str, object] = {"role": "user", "text": as_markdown(words) if mrkdwn else words}
    if mrkdwn:
        bubble["markdown"] = True
    if paths:
        bubble["files"] = [
            attached.get(PurePosixPath(path).name) or _note_card(path) for path in paths
        ]
    hidden = [element for element in parts.folded if element in HIDDEN_ELEMENTS]
    if hidden:
        bubble["hidden"] = hidden
    return bubble


def _note_card(path: str) -> dict[str, object]:
    """One file a member attached before the row that carries it existed, named from the note with
    no link and no picture: the bytes were the workspace copy, gone once the sandbox is."""
    name = PurePosixPath(path).name
    return {
        "filename": name,
        "url": None,
        "id": None,
        "media_type": raster_image_media_type(name) or "application/octet-stream",
        "role": "file",
        "preview_url": None,
    }


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


def _count_click(request: Request) -> Response | None:
    """Count the press this message came from: a starter row above the start screen's composer, or
    a suggestion the thread offered under a question. The press happens in the page and this POST is
    the one thing the fleet sees of it, so the header it rides is where the counter lives. A value
    the page does not mint is refused rather than folded, so no browser mints a series."""
    clicked = request.headers.get(CLICK_HEADER, "").strip()
    if not clicked:
        return None
    if clicked == THREAD_FOLLOWUP_CLICK:
        emit_metric(THREAD_FOLLOWUP_CLICK_METRIC)
        return None
    kind = request.headers.get(CLICK_KIND_HEADER, "").strip()
    if clicked != STARTER_CLICK or kind not in STARTER_KINDS:
        return Response(f"{CLICK_HEADER} names no press", status_code=400)
    emit_metric(STARTER_CLICK_METRIC, kind=kind)
    return None


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


def _picked_model(ctx: SurfaceContext, request: Request) -> TurnRuntimeConfig | None | Response:
    """The model the composer picked for this thread, as this turn's own pin — the agent's stored
    model is never written, so the pick lasts as long as the messages that carry it. Absent means
    the turn runs the agent's own model, which is also what the `auto` sentinel asks for: a turn
    pins a concrete id, and the sentinel resolves per turn where the row holds it."""
    picked = request.headers.get(MODEL_HEADER, "").strip()
    if not picked or picked == AUTO_MODEL:
        return None
    try:
        pinned = TurnRuntimeConfig(model=picked)
        ctx.validate_runtime_config(pinned)
    except ValueError as error:
        return Response(str(error), status_code=400, headers={REFUSAL_HEADER: "1"})
    return pinned


@dataclass(frozen=True)
class _ChatInbound:
    text: str
    uploads: tuple[UploadFile, ...]
    presigned_keys: tuple[str, ...]
    paths: tuple[str, ...]
    body: str
    stop: UUID | None
    answer: tuple[UUID, int] | None
    runtime_config: TurnRuntimeConfig | None


@dataclass(frozen=True)
class _ChatTarget:
    conversation_id: UUID
    title: str
    comment: str | None


async def _chat_inbound(ctx: SurfaceContext, request: Request) -> _ChatInbound | Response:
    stop = _stop_header(request)
    if isinstance(stop, Response):
        return stop
    parsed = await _parse_inbound(request)
    if isinstance(parsed, Response):
        return parsed
    text, uploads, presigned = parsed
    if stop is not None and (text or uploads or presigned):
        return Response("a stop admits no message", status_code=400)
    if stop is None and not text.strip() and not uploads and not presigned:
        return Response("empty message", status_code=400)
    keys: list[str] = []
    for key, signature in presigned:
        if not ctx.verify_upload_grant(key, signature):
            return Response("unknown upload key", status_code=403)
        if not await ctx.blob.exists(key):
            return Response("upload not found", status_code=404)
        keys.append(key)
    paths = _inbox_paths(uploads, tuple(keys))
    body = _files_note(text, paths) if paths else text
    if len(body) > MAX_INBOUND_CHARS:
        return Response(f"message exceeds {MAX_INBOUND_CHARS} characters", status_code=413)
    answer = _answer_headers(request)
    if isinstance(answer, Response):
        return answer
    pinned = _picked_model(ctx, request)
    if isinstance(pinned, Response):
        return pinned
    return _ChatInbound(text, uploads, tuple(keys), paths, body, stop, answer, pinned)


async def _new_chat_target(
    ctx: SurfaceContext,
    audience: WebAudience,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    inbound: _ChatInbound,
) -> _ChatTarget | Response:
    if not audience.allows(agent_id):
        return Response("no such agent", status_code=404)
    if inbound.answer is not None:
        return Response("an answer names the conversation it was asked in", status_code=400)
    if inbound.stop is not None:
        return Response("a stop names the conversation its turn runs in", status_code=400)
    conversation_id, title = await _open_conversation(
        ctx,
        agent_id,
        member_id,
        f"{agent_id}/{email}/{uuid4().hex}",
        inbound.text,
        inbound.paths,
    )
    return _ChatTarget(conversation_id, title, None)


async def _existing_chat_target(
    ctx: SurfaceContext,
    audience: WebAudience,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    conversation_id: UUID,
    inbound: _ChatInbound,
) -> _ChatTarget | Response:
    conversation = await _spoken_conversation(ctx, audience, agent_id, member_id, conversation_id)
    if conversation is None:
        return Response("no such conversation", status_code=404)
    comment = (
        _comment_notice(
            ctx.public_base_url, conversation, member_id, email, inbound.text, inbound.paths
        )
        if conversation.summary.surface in COMMENT_SURFACES
        else None
    )
    return _ChatTarget(conversation_id, conversation.title, comment)


async def _resolve_chat_target(
    ctx: SurfaceContext,
    request: Request,
    audience: WebAudience,
    agent_id: UUID,
    member_id: UUID,
    email: str,
    inbound: _ChatInbound,
) -> _ChatTarget | Response:
    requested = request.query_params.get("conversation", "").strip()
    if not requested:
        return Response("conversation is required", status_code=400)
    if requested == NEW_CONVERSATION:
        return await _new_chat_target(ctx, audience, agent_id, member_id, email, inbound)
    try:
        conversation_id = UUID(requested)
    except ValueError:
        return Response("no such conversation", status_code=404)
    return await _existing_chat_target(
        ctx, audience, agent_id, member_id, email, conversation_id, inbound
    )


async def _stop_chat(
    ctx: SurfaceContext, request: Request, conversation_id: UUID, turn_id: UUID
) -> Response:
    authorized = await _member_turn(ctx, request, named_turn=turn_id, admin_stops_fired=True)
    if isinstance(authorized, Response):
        return authorized
    try:
        stopped = await ctx.stop_turn(conversation_id, turn_id)
    except ValueError:
        return Response("no such turn in this conversation", status_code=404)
    outcome: dict[str, bool | str] = {"stopped": stopped.ended}
    if stopped.founded_turn_id is not None:
        outcome["turn_id"] = str(stopped.founded_turn_id)
    return JSONResponse(outcome)


async def _admit_chat(
    ctx: SurfaceContext,
    request: Request,
    target: _ChatTarget,
    inbound: _ChatInbound,
    member_id: UUID,
    context: TurnContext,
) -> Response:
    key = (
        None
        if inbound.answer is None
        else _answer_key(target.conversation_id, inbound.answer[0], inbound.answer[1])
    )
    blob_keys = await _deliver_uploads(
        ctx,
        target.conversation_id,
        inbound.uploads,
        inbound.presigned_keys,
        inbound.paths,
    )
    admitted = await ctx.admit(
        target.conversation_id,
        inbound.body,
        context=context,
        idempotency_key=key,
        speaker_member_id=member_id,
        comment=target.comment,
        runtime_config=inbound.runtime_config,
    )
    await ctx.attach_member_files(admitted.turn_id, blob_keys, member_id=member_id)
    payload: dict[str, str | bool | None] = {
        "turn_id": str(admitted.turn_id),
        "conversation_id": str(target.conversation_id),
        "title": target.title,
        "opened_run": admitted.opened_run,
    }
    if admitted.arrival_id is not None:
        payload["arrival_id"] = str(admitted.arrival_id)
    if key is not None:
        payload["body"] = await ctx.admitted_body(key)
    return JSONResponse(payload)


async def chat(ctx: SurfaceContext, request: Request) -> Response:
    """Admit one member message. The `conversation` query parameter continues that conversation —
    gated to one the member may speak in: their own, or one the workspace shares, whatever surface
    holds it — and the `new` sentinel opens a fresh one: the chat POST is the chat transport, so
    opening a conversation rides the first message rather than a separate mutation, and the
    response names the conversation it landed in.

    A message sent while a turn is still running joins that turn instead of founding one, and the
    response says so by naming the `arrival_id` the turn's `absorbed` event will carry — the id the
    page holds its wait against. `opened_run` is the other half, and the one the page decides on:
    admission's own answer, taken under the conversation-row lock, to whether this delivery opened
    the run the named turn belongs to. False beside an `arrival_id` is the single outcome whose live
    frames a tail already carries, so the page leaves that tail alone rather than opening a second
    stream on one turn. Every other outcome is the page's to tail, the refusals included: a
    seat-refused or cap-refused message founds a turn of its own carrying its own terminal, and the
    stream replays it.

    An `x-ufo-model` header is the model the composer picked for this thread, pinned on the turn
    this message founds and on nothing else — the agent's stored model stands.

    An `x-ufo-click` header names the press this message came from — a starter row, named by kind in
    `x-ufo-click-kind`, or a suggestion under a thread's question — and counts it.

    An `x-ufo-stop-turn` header over an empty body is the member ending a turn of this conversation
    rather than saying anything into it: nothing is admitted, so the transcript never mentions the
    press, and the cancelled terminal the stop publishes is what the member's live tail ends on."""
    gated = await _panel_gate(ctx, request, reach=WebAudience.allows_chat)
    if isinstance(gated, Response):
        return gated
    member_id, email, audience, agent_id = gated
    counted = _count_click(request)
    if counted is not None:
        return counted
    inbound = await _chat_inbound(ctx, request)
    if isinstance(inbound, Response):
        return inbound
    target = await _resolve_chat_target(ctx, request, audience, agent_id, member_id, email, inbound)
    if isinstance(target, Response):
        return target
    if inbound.stop is not None:
        return await _stop_chat(ctx, request, target.conversation_id, inbound.stop)
    context = _turn_context(
        email, request, _chat_source(ctx.public_base_url, target.conversation_id, email)
    )
    if inbound.answer is not None:
        answer_turn, answer_index = inbound.answer
        question = await ctx.answerable_question(
            target.conversation_id,
            answer_turn,
            answer_index,
            member_id,
        )
        if question is None:
            return Response("This question is not available to you.", status_code=403)
        if question.authorization_id is not None:
            if inbound.uploads or inbound.presigned_keys:
                return Response("An authorization answer cannot include files.", status_code=400)
            selected = tuple(
                option
                for option in question.questions[answer_index].options or ()
                if option.label == inbound.text.strip()
            )
            if len(selected) != 1 or selected[0].authorization_choice is None:
                return Response("This authorization choice is not available.", status_code=400)
            context = context.model_copy(
                update={
                    "authorization_id": question.authorization_id,
                    "authorization_choice": selected[0].authorization_choice,
                }
            )
    return await _admit_chat(ctx, request, target, inbound, member_id, context)


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


def _append_activity(events: list[dict[str, str]], text: str) -> None:
    events.append({"kind": "activity", "text": text})


def _stored_activity(block: ToolUseBlock, result: ToolResultBlock) -> str | None:
    if result.activity_text:
        return result.activity_text
    description = block.input.get("user_description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    if block.name == "load_skill":
        skill = block.input.get("name")
        if isinstance(skill, str) and skill:
            return f"Loading skill · {skill}"
    if "activity_text" in result.model_fields_set:
        return None
    return block.name


SubagentRuns = dict[str, list[SubagentRun]]


def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]:
    """A subagent's own work in the order it happened — the tools and skills it dispatched and the
    text it wrote between them. Only a round that called a tool is stored as blocks, and work is
    read from blocks, so the plain text a run ends on is not work here: the prose a stopped child is
    force-finished over, and the finish payload the transcript closes with, are both string content.
    Its answer is the terminal's."""
    activity = {
        block.tool_use_id: block
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
            elif isinstance(block, ToolUseBlock) and block.id in activity:
                text = _stored_activity(block, activity[block.id])
                if text is not None:
                    _append_activity(events, text)
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
    profiles: dict[UUID, str] = {}
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
        profiles[turn.id] = profile
    read = sorted(spawned, key=lambda turn: turn.created_at, reverse=True)[:SUBAGENT_ACTIVITY_LIMIT]
    recorded = await asyncio.gather(*(ctx.read_transcript(turn.conversation_id) for turn in read))
    work = {
        turn.id: _subagent_activity(held.messages)
        for turn, held in zip(read, recorded, strict=True)
        if held is not None
    }
    children: dict[UUID, list[Turn]] = {}
    for turn in spawned:
        if turn.parent_turn_id is not None:
            children.setdefault(turn.parent_turn_id, []).append(turn)

    def node(turn: Turn) -> SubagentRun:
        return SubagentRun(
            profile=profiles[turn.id],
            name=turn.subagent_name or "",
            conversation_id=turn.conversation_id,
            events=tuple(ActivityEvent.model_validate(event) for event in work.get(turn.id, [])),
            output=_run_answer("" if turn.terminal is None else turn.terminal.text),
            subagents=tuple(node(child) for child in children.get(turn.id, [])),
            running=turn.terminal is None,
        )

    return {
        str(parent): [node(turn) for turn in held]
        for parent, held in children.items()
        if parent not in profiles
    }


def _run_payload(run: SubagentRun) -> dict[str, object]:
    return run.model_dump(mode="json", exclude_none=True)


@dataclass(frozen=True)
class _ReplyState:
    pending: tuple[dict[str, str], ...] = ()
    answer: str = ""
    answer_at: int = 0
    notes: int = 0
    current_turn_id: str | None = None

    def note_answer(self) -> "_ReplyState":
        pending = list(self.pending)
        notes = self.notes
        if self.answer and notes < SUBAGENT_EVENT_LIMIT:
            pending.insert(self.answer_at, {"kind": "note", "text": self.answer})
            notes += 1
        return replace(self, pending=tuple(pending), answer="", notes=notes)


@dataclass(frozen=True)
class _TranscriptRenderer:
    subagents: SubagentRuns
    turn_ids: frozenset[str]
    agent_origin: frozenset[str]
    fired: Mapping[str, str | None]
    speakers: Mapping[str, dict[str, object]] | None
    questions: Mapping[str, dict[str, object]]
    asked: Mapping[str, str]
    files: Mapping[str, list[dict[str, object]]]
    apps: Mapping[str, list[dict[str, object]]]
    connects: Mapping[str, dict[str, object]]
    attached: Mapping[str, Mapping[str, dict[str, object]]]
    answers: frozenset[str]
    slack: bool
    spoken_at: Mapping[str, str]
    answered_at: Mapping[str, str]
    summaries: Mapping[str, dict[str, object]]

    def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]:
        activity = {
            block.tool_use_id: block
            for message in messages
            if not isinstance(message.content, str)
            for block in message.content
            if isinstance(block, ToolResultBlock) and block.activity
        }
        rendered: list[dict[str, object]] = []
        state = _ReplyState()
        for message in messages:
            if message.role == "assistant":
                state = self._assistant(message, activity, state)
            else:
                state = self._member(message, state, rendered)
        self._flush(state, rendered, include_subagents=True)
        return rendered

    def _assistant(
        self,
        message: Message,
        activity: Mapping[str, ToolResultBlock],
        state: _ReplyState,
    ) -> _ReplyState:
        text = _rendered_text(message)
        if text:
            state = state.note_answer()
            state = replace(state, answer=text, answer_at=len(state.pending))
        if isinstance(message.content, str):
            return state
        pending = list(state.pending)
        for block in message.content:
            if isinstance(block, ToolUseBlock) and block.id in activity:
                activity_text = _stored_activity(block, activity[block.id])
                if activity_text is not None:
                    _append_activity(pending, activity_text)
        return replace(state, pending=tuple(pending))

    def _member(
        self,
        message: Message,
        state: _ReplyState,
        rendered: list[dict[str, object]],
    ) -> _ReplyState:
        text = _rendered_text(message)
        if not text:
            return state
        if not isinstance(message.content, str) or CONTEXT_TAG.match(message.content) is None:
            return state
        match = MESSAGE_REF.match(message.content)
        turn_id = None if match is None else match.group("ref").strip()
        if state.answer_at < len(state.pending):
            state = state.note_answer()
        if turn_id in self.turn_ids and turn_id != state.current_turn_id:
            state = self._flush(state, rendered, include_subagents=True)
            state = replace(state, current_turn_id=turn_id)
        else:
            state = self._flush(state, rendered, include_subagents=False)
        if turn_id in self.agent_origin or turn_id in self.answers:
            return state
        bubble = _member_bubble(text, self.attached.get(turn_id or "", {}), slack=self.slack)
        if state.current_turn_id is not None:
            bubble["turn"] = state.current_turn_id
        label = None if self.speakers is None or turn_id is None else self.speakers.get(turn_id)
        if label is not None:
            bubble["speaker"] = label
        if turn_id is not None and turn_id in self.fired:
            bubble["fired"] = {"provider": self.fired[turn_id]}
        answered = None if turn_id is None else self.asked.get(turn_id)
        if answered is not None:
            bubble["asked"] = answered
        moment = None if turn_id is None else self.spoken_at.get(turn_id)
        if moment is not None:
            bubble["at"] = moment
        rendered.append(bubble)
        return state

    def _flush(
        self,
        state: _ReplyState,
        rendered: list[dict[str, object]],
        *,
        include_subagents: bool,
    ) -> _ReplyState:
        closing = state.current_turn_id if include_subagents else None
        runs = [] if closing is None else self.subagents.get(closing, [])
        question = None if closing is None else self.questions.get(closing)
        shared = [] if closing is None else self.files.get(closing, [])
        made = [] if closing is None else self.apps.get(closing, [])
        control = None if closing is None else self.connects.get(closing)
        summary = None if closing is None else self.summaries.get(closing)
        if (
            not state.answer
            and not state.pending
            and not runs
            and question is None
            and not shared
            and not made
            and control is None
        ):
            return state
        reply: dict[str, object] = {"role": "assistant", "text": state.answer}
        if state.current_turn_id is not None:
            reply["turn"] = state.current_turn_id
        if state.pending:
            reply["events"] = list(state.pending)
        if runs:
            reply["subagents"] = [_run_payload(run) for run in runs]
        if question is not None:
            reply["question"] = question
        if shared:
            reply["files"] = shared
        if made:
            reply["apps"] = made
        if control is not None:
            reply["connect"] = control
        moment = (
            None if state.current_turn_id is None else self.answered_at.get(state.current_turn_id)
        )
        if moment is not None:
            reply["at"] = moment
        if summary is not None:
            reply["summary"] = summary
        rendered.append(reply)
        return replace(state, pending=(), answer="", answer_at=0, notes=0)


def _rendered_messages(
    messages: tuple[Message, ...],
    subagents: SubagentRuns | None = None,
    turn_ids: frozenset[str] = frozenset(),
    agent_origin: frozenset[str] = frozenset(),
    speakers: Mapping[str, dict[str, object]] | None = None,
    questions: Mapping[str, dict[str, object]] | None = None,
    asked: Mapping[str, str] | None = None,
    files: Mapping[str, list[dict[str, object]]] | None = None,
    apps: Mapping[str, list[dict[str, object]]] | None = None,
    connects: Mapping[str, dict[str, object]] | None = None,
    attached: Mapping[str, Mapping[str, dict[str, object]]] | None = None,
    answers: frozenset[str] = frozenset(),
    slack: bool = False,
    spoken_at: Mapping[str, str] | None = None,
    answered_at: Mapping[str, str] | None = None,
    summaries: Mapping[str, dict[str, object]] | None = None,
    fired: Mapping[str, str | None] | None = None,
) -> list[dict[str, object]]:
    """The transcript as the portal draws it. A user-role message is the member's own bubble, so
    one no member spoke never becomes one: a scheduled task's firing carries its cron envelope and a
    delivered subagent result carries the wire's element around the child's output, and both would
    otherwise read as words the member typed. The reply that answers it still renders — the member
    reads the agent coming back to them, which is what happened.

    A bubble states the member's own words, never the prompt the turn ran on: a channel surface
    fences those words between the ambient digest and their attachments, and
    `member_message_text` is what takes them back out. `slack` says the conversation stands on
    Slack, whose own markup drew the member's emphasis, so each bubble Slack fenced crosses as
    markdown.

    `speakers` names who spoke each turn — the bubble carries the label so a conversation more
    members than the viewer are in reads as who said what. `fired` names the turns an object's
    fire admitted — a source trigger's wake — each with the connector provider behind it, and the
    bubble carries both, so its headline reads as sent by ufo under the provider's mark rather than
    typed by a member.

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

    A reply names the turn that wrote it, so a screen the member reached from one run's row finds
    that run's own words among the conversation's.

    `summaries` names what each turn spent and how long it took, keyed like `questions`: the reply
    carries it, so the model, the tokens, the cost and the duration stand under the words they paid
    for on a transcript read back, and not only for the member who watched the turn stream.

    `files` names what each turn shared, keyed like `questions`: the reply carries its own, so a
    file stands on the words that shared it and stays there when later turns run. A turn that
    shared and wrote nothing still renders its reply — the file needs the reply it belongs to.
    `apps` names the applications each turn created and rides the reply the same way, so the card
    that opens one stands under the words that made it. `connects` names the private connect act a
    turn left the member and rides it the same way again — the control stands under the words that
    asked for it, and stays there while later turns run, drawn as the account it made once the
    connect landed.

    `spoken_at` and `answered_at` name when each turn's words landed and when the turn that wrote
    them settled, keyed like `questions`: a bubble and a reply each state their own time, which
    nothing in the words themselves says.

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
    renderer = _TranscriptRenderer(
        subagents=subagents or {},
        turn_ids=turn_ids,
        agent_origin=agent_origin,
        fired=fired or {},
        speakers=speakers,
        questions=questions or {},
        asked=asked or {},
        files=files or {},
        apps=apps or {},
        connects=connects or {},
        attached=attached or {},
        answers=answers,
        slack=slack,
        spoken_at=spoken_at or {},
        answered_at=answered_at or {},
        summaries=summaries or {},
    )
    return renderer.render(messages)


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
    conversation_id: UUID,
    turns: tuple[Turn, ...],
    admitted: tuple[KeyedAdmission, ...],
    viewer: UUID,
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
        if question is None or question.target_member_id not in (None, viewer):
            continue
        answered: dict[str, str] = {}
        refs: list[str] = []
        for index in range(len(question.questions)):
            row = landed.get(_answer_key(conversation_id, turn.id, index))
            if row is None:
                continue
            answered[str(index)] = member_message_text(row.inbound)
            refs.append(str(row.ref))
        if turn.id != newest and not answered and question.target_member_id is None:
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
    turn created, where a member's own attachment is drawn from, when each turn landed and settled,
    what each turn spent, whether the conversation ran a profile, and whether it stands on Slack —
    gathered once so the live window and an earlier page render one message identically.

    `attached` answers under both ids a member message is named by: a turn's own id for the message
    that founded it, and the queue-row id the engine writes for one folded into a running turn.
    Both reach the rows of the turn the files were recorded against, and each message takes the
    files its own note names."""

    subagents: SubagentRuns
    turn_ids: frozenset[str]
    agent_origin: frozenset[str]
    fired: dict[str, str | None]
    speakers: dict[str, dict[str, object]]
    asked: dict[str, str]
    asks: _Asks
    files: dict[str, list[dict[str, object]]]
    apps: dict[str, list[dict[str, object]]]
    connects: dict[str, dict[str, object]]
    attached: dict[str, dict[str, dict[str, object]]]
    spoken_at: dict[str, str]
    answered_at: dict[str, str]
    summaries: dict[str, dict[str, object]]
    run_conversation: bool
    slack: bool

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
            self.attached,
            self.asks.stated,
            self.slack,
            self.spoken_at,
            self.answered_at,
            self.summaries,
            self.fired,
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
    speakers: dict[str, dict[str, object]],
    asked: dict[str, str],
    folded: dict[str, str],
    opens: frozenset[UUID],
    slack: bool,
) -> _TranscriptAids:
    turns, spawned, shared, admitted, agent_model = await asyncio.gather(
        ctx.list_turns(conversation_id),
        ctx.conversation_subagent_turns(conversation_id),
        ctx.list_conversation_artifacts(conversation_id, limit=CONVERSATION_ARTIFACTS_MAX),
        ctx.keyed_admissions(conversation_id),
        ctx.agent_model(agent_id),
    )
    files: dict[str, list[dict[str, object]]] = {}
    attached: dict[str, dict[str, dict[str, object]]] = {}
    for entry in reversed(shared):
        if entry.artifact.attached_by_member:
            attached.setdefault(str(entry.turn_id), {})[entry.artifact.filename] = _file_payload(
                ctx, entry.artifact
            )
        else:
            files.setdefault(str(entry.turn_id), []).append(_file_payload(ctx, entry.artifact))
    for arrival_id, turn_id in folded.items():
        if turn_id in attached:
            attached[arrival_id] = attached[turn_id]
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
        fired={str(turn.id): turn.fired_by.provider for turn in turns if turn.fired_by is not None},
        speakers=speakers | await _turn_faces(ctx, turns, viewer),
        asked=asked
        | {
            str(turn.id): turn.context.question
            for turn in turns
            if turn.context is not None and turn.context.question is not None
        },
        asks=_asks(conversation_id, turns, admitted, viewer),
        spoken_at={str(turn.id): turn.created_at.isoformat() for turn in turns},
        answered_at={
            str(turn.id): (turn.updated_at or turn.created_at).isoformat() for turn in turns
        },
        files=files,
        apps=drawn,
        connects=await _connect_controls(ctx, conversation_id, turns, viewer),
        attached=attached,
        summaries={
            str(turn.id): summary
            for turn in turns
            if (summary := _turn_summary(turn, agent_model)) is not None
        },
        run_conversation=any(turn.subagent_profile is not None for turn in turns),
        slack=slack,
    )


def _turn_summary(turn: Turn, agent_model: str | None) -> dict[str, object] | None:
    """What one settled turn cost and how long it took, drawn under the reply it produced. Read off
    the turn's own record rather than kept from the live stream, so the line survives a reload and
    reaches a member who never watched the turn run. A turn that did not complete states nothing:
    the stream draws its stop or its fault instead, and a spend under those words would read as the
    price of an answer there is none of.

    An agent on the auto sentinel names no model, exactly as the live stream draws it: the member
    picked the deploy's own choice, and stating the model it resolved to would read as a model they
    pinned."""
    terminal = turn.terminal
    if terminal is None or terminal.status != "done":
        return None
    summary: dict[str, object] = {
        "tokens": terminal.tokens,
        "cost_micro_usd": terminal.cost_micro_usd,
    }
    if terminal.model and agent_model != AUTO_MODEL:
        summary["model"] = terminal.model
    if turn.updated_at is not None:
        elapsed = (turn.updated_at - turn.created_at).total_seconds()
        summary["duration_ms"] = max(round(elapsed * 1_000), 0)
    return summary


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

    A conversation a member speaks in over Slack states the bubbles Slack admitted as markdown:
    Slack drew their emphasis as its own markup, so words the portal drew as the characters they
    were typed with would read as asterisks where the member saw bold. A comment typed into the
    portal of that same conversation wears no surface fence and stays the characters it was typed
    with.

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
    recorded, agent_origin, spoken, compactions, surface = await asyncio.gather(
        ctx.read_transcript(conversation_id),
        ctx.agent_origin_refs(conversation_id),
        ctx.arrival_speakers(conversation_id),
        ctx.list_rollovers(conversation_id),
        ctx.conversation_surface(conversation_id),
    )
    slack = surface == SURFACE_SLACK
    speakers = await _spoken_faces(ctx, spoken, viewer)
    asked = {
        str(arrival.id): arrival.question for arrival in spoken if arrival.question is not None
    }
    folded = {
        str(arrival.id): str(arrival.turn_id) for arrival in spoken if arrival.turn_id is not None
    }
    latest = await ctx.latest_turn(conversation_id)
    detail = None if latest is None else await ctx.turn_detail(latest)
    if recorded is None:
        rendered: list[dict[str, object]] = []
        earlier = 0
        stated: frozenset[str] = frozenset()
    else:
        aids = await _transcript_aids(
            ctx,
            agent_id,
            conversation_id,
            viewer,
            agent_origin,
            speakers,
            asked,
            folded,
            opens,
            slack,
        )
        rendered = aids.render(recorded.messages)
        earlier = await _verified_earlier(ctx, conversation_id, compactions, recorded.messages)
        stated = aids.asks.stated
    if detail is None:
        return rendered, None, earlier
    member_rows = {
        artifact.filename: _file_payload(ctx, artifact)
        for artifact in await ctx.shared_artifacts(detail.turn.id)
        if artifact.attached_by_member
    }
    if (
        detail.turn.terminal is None
        and str(detail.turn.id) not in agent_origin
        and str(detail.turn.id) not in stated
    ):
        prompt = _member_bubble(detail.turn.inbound, member_rows, slack=slack)
        prompt["turn"] = str(detail.turn.id)
        if detail.turn.fired_by is not None:
            prompt["fired"] = {"provider": detail.turn.fired_by.provider}
        if detail.turn.speaker_member_id != viewer:
            spoke = _speaker_face(
                next(
                    iter(
                        await ctx.member_profiles(frozenset({detail.turn.speaker_member_id}))
                        if detail.turn.speaker_member_id is not None
                        else ()
                    ),
                    None,
                ),
                None if detail.turn.context is None else detail.turn.context.sender,
            )
            if spoke is not None:
                prompt["speaker"] = spoke
        if detail.turn.context is not None and detail.turn.context.question is not None:
            prompt["asked"] = detail.turn.context.question
        prompt["at"] = detail.turn.created_at.isoformat()
        rendered.append(prompt)
    draining = detail.turn.id if detail.turn.terminal is None else None
    for arrival in await ctx.queued_arrivals(conversation_id, draining):
        if str(arrival.id) in agent_origin or str(arrival.id) in stated:
            continue
        bubble = _member_bubble(arrival.inbound, member_rows, slack=slack)
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
        after = await ctx.read_rollover_after(conversation_id, index)
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
    record = await ctx.read_rollover(conversation_id, index)
    if record is None:
        return None
    kept = max(len(record.after) - 1, 0)
    window = record.before[: len(record.before) - kept] if kept else record.before
    agent_origin, spoken, surface = await asyncio.gather(
        ctx.agent_origin_refs(conversation_id),
        ctx.arrival_speakers(conversation_id),
        ctx.conversation_surface(conversation_id),
    )
    speakers = await _spoken_faces(ctx, spoken, viewer)
    asked = {
        str(arrival.id): arrival.question for arrival in spoken if arrival.question is not None
    }
    folded = {
        str(arrival.id): str(arrival.turn_id) for arrival in spoken if arrival.turn_id is not None
    }
    aids = await _transcript_aids(
        ctx,
        agent_id,
        conversation_id,
        viewer,
        agent_origin,
        speakers,
        asked,
        folded,
        opens,
        surface == SURFACE_SLACK,
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


async def _open_handoffs(
    ctx: SurfaceContext, terminal: TerminalFrame, member_id: UUID
) -> dict[str, object]:
    """What the conversation's newest committed turn still asks of the member, so a reload
    re-renders the affordance the live stream drew: credential prompts still awaiting values. A
    question, a shared file, an app's card, and the connect control are not among them — each rides
    the reply it belongs to, which is where the member answers one, reads another, opens the third,
    and presses the fourth."""
    handoffs: dict[str, object] = {}
    if terminal.credential_request is not None:
        prompts = await _pending_prompts(ctx, terminal.credential_request, member_id)
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
    for a provider the catalog does not curate.

    A deploy holding no connect machinery still draws the accounts an app declares — the setup
    screen states what an app runs on whether or not this deploy can grant it — so an uncurated
    provider falls back to the slug it is named by rather than taking the screen down with it."""
    for tile in FIRST_RUN_PROVIDERS:
        if tile.name == provider:
            return tile.label
    return ctx.connect_label(provider) if ctx.connect_available() else provider


def _provider_summary(provider: str) -> str:
    """What an app does once this account answers for it, in the words the connect tiles already
    use. A provider the catalog does not curate says nothing rather than a sentence written here:
    the screen carries a line under the name only where there is one to carry."""
    for tile in FIRST_RUN_PROVIDERS:
        if tile.name == provider:
            return tile.summary
    return ""


async def chats_index(ctx: SurfaceContext, request: Request) -> Response:
    """The one conversation `?conversation=` names, as an app page reads it: the row the
    `conversation` kind lists, under the field names the kit's `Conversation` carries. A Chat app
    homepage resolves the conversation it opens here, and a deploy of that page rebuilds nothing,
    so the shape this answers is the one every deployed page reads. The portal itself resolves a
    permalink through `objects/conversation/<id>`, which answers the same row.

    An agent reached through a member-private conversation alone resolves that conversation and no
    other, and a row an admin may only disclose resolves with `readable` false rather than as a
    conversation that does not exist.

    `archived` and `deleted` are the conversation's own filing marks; `pinned` is this viewer's
    own, so two members reading one row read their own pin."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    requested = request.query_params.get("conversation", "").strip()
    if not requested:
        return Response("name a conversation to resolve", status_code=400)
    absent = Response("no such conversation", status_code=404)
    try:
        conversation_id = UUID(requested)
    except ValueError:
        return absent
    agent_id = await ctx.conversation_agent(conversation_id)
    agent = next((held for held in audience.chat_agents if held.id == agent_id), None)
    if agent is None:
        return absent
    listed = await ctx.list_agent_conversations(
        agent.id, member_id, admin=audience.admin, limit=1, conversation_id=conversation_id
    )
    if not listed or not _reaches(audience, agent.id, listed[0].audience, member_id):
        return absent
    entry = listed[0]
    return JSONResponse(
        {
            "conversation": {
                "id": str(entry.summary.id),
                "agent": {"id": str(agent.id), "name": agent.name},
                "surface": entry.summary.surface,
                "surface_label": entry.surface_label,
                "audience": entry.audience,
                "member_email": entry.summary.member_email,
                "mine": entry.mine,
                "description": entry.title,
                "source": entry.source,
                "speakers": [who.sender or who.email for who in entry.speakers],
                "turn_count": entry.summary.turn_count,
                "created_at": _iso(entry.summary.created_at),
                "last_turn_at": _iso(entry.summary.last_turn_at),
                "archived": entry.archived,
                "deleted": entry.deleted,
                "pinned": entry.pinned,
                "readable": entry.readable,
                "disclosable": entry.disclosable,
                "speakable": entry.speakable,
            }
        }
    )


async def _panel_gate(
    ctx: SurfaceContext,
    request: Request,
    *,
    reach: Callable[[WebAudience, UUID], bool] = WebAudience.allows,
) -> tuple[UUID, str, WebAudience, UUID] | Response:
    """The shared entry of every per-agent route: the session's member, their email, and audience,
    plus the path's agent — 404 when the agent is outside the reach the route admits. A panel read
    and the intent lane take web reach (`WebAudience.allows`); conversation content and the chat
    lane take chat reach (`WebAudience.allows_chat`), which an agent held through a member-private
    conversation alone also has — `_reaches` then says which of its conversations that opens."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not reach(audience, agent_id):
        return Response("no such agent", status_code=404)
    return member_id, email, audience, agent_id


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _window_param(request: Request) -> int | None | Response:
    """The named usage range, `None` for all time, or a valid rolling `window_seconds`."""
    named = request.query_params.get("range")
    if named is not None:
        if named not in USAGE_RANGES:
            return Response("range must be one of " + ", ".join(USAGE_RANGES), status_code=400)
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

    Either shape carries the acts the memory collection presents, whose own schemas bound what a
    correction may run to; a deploy without memory answers the same fields, empty."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    if not ctx.memory_available:
        return JSONResponse({"available": False, "kinds": [], "matches": [], "actions": []})
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
                "older": None if page.older is None else page.older.encode(),
                "newer": None if page.newer is None else page.newer.encode(),
                "actions": _action_payloads(ctx.object_actions(MEMORY_KIND, "collection")),
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
            "actions": _action_payloads(ctx.object_actions(MEMORY_KIND, "collection")),
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
    """Whether this member reaches GitHub on each of its two legs, read over the connections they
    may see — the same set the connections and streams beside it list, so the card cannot state a
    coverage the screen under it does not show."""
    gated = await _audience_for(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, _audience = gated
    coverage = await ctx.github_coverage(member_id)
    return JSONResponse(coverage.model_dump(mode="json"))


async def _readable_conversation(
    ctx: SurfaceContext, request: Request, conversation_id: UUID | None = None
) -> tuple[UUID, UUID, "SlotViewer"] | Response:
    """The agent and conversation a content read is authorized for, or the 404 every unreadable
    case answers: an agent outside the member's chat reach, a malformed id, a conversation
    `_reaches` refuses, and under web reach one `readable_conversation` refuses — another agent's,
    a room's, and another member's private one until an admin records a disclosure against it. One
    gate, so content reads cannot disagree."""
    gated = await _panel_gate(ctx, request, reach=WebAudience.allows_chat)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    if conversation_id is None:
        try:
            conversation_id = UUID(request.path_params["conversation_id"])
        except ValueError:
            return Response("no such conversation", status_code=404)
    if audience.allows(agent_id):
        readable = await ctx.readable_conversation(
            conversation_id, agent_id, member_id, admin=audience.admin
        )
    else:
        listed = await ctx.list_agent_conversations(
            agent_id, member_id, admin=False, limit=1, conversation_id=conversation_id
        )
        readable = (
            bool(listed)
            and listed[0].speakable
            and _reaches(audience, agent_id, listed[0].audience, member_id)
        )
    if not readable:
        return Response("no such conversation", status_code=404)
    return agent_id, conversation_id, SlotViewer(member_id, audience.admin, _opens(audience))


async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response:
    """One conversation as the portal renders it on load — the member's own, one the workspace
    shares, another member's an admin acknowledged, one another surface holds — as the same
    messages the chat draws. Each reply names the children it spawned, and a child carries this
    conversation's audience, so the card opens that run through this conversation and the one gate
    here authorizes both. A turn still running names itself and the moment it was admitted, so the
    page attaches to its live frames and counts that turn's clock from the turn's own start; a
    settled one carries what it still asks of the member.

    Serving the messages moves this member's read cursor on the conversation: the act the cursor
    records is the member reading them, and the rail draws the row unread until it moves."""
    cursor = request.query_params.get("cursor")
    if cursor is not None:
        return await _conversation_history(ctx, request, cursor)
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    agent_id, conversation_id, viewer = authorized
    rendered, turn, earlier = await _conversation_messages(
        ctx, agent_id, conversation_id, viewer.member_id, viewer.opens
    )
    await ctx.mark_conversation_read(conversation_id, viewer.member_id)
    payload: dict[str, object] = {"messages": rendered}
    if earlier:
        payload["earlier_cursor"] = _history_cursor(earlier)
    if turn is not None:
        if turn.terminal is None:
            payload["turn"] = str(turn.id)
            payload["turn_started_at"] = _iso(turn.created_at)
        else:
            payload.update(await _open_handoffs(ctx, turn.terminal, viewer.member_id))
    return JSONResponse(payload)


FOLLOW_UPS_DRAWN = 4
ALWAYS_OFFERED = frozenset({"ask", "keep", "watch"})
SAID_ROLES = frozenset({"user", "assistant"})


def _follow_ups(reading: Reading, kinds: frozenset[str] = frozenset()) -> Response:
    """One answer shape on every path, so a page never has to read a missing field as a state."""
    drawn = tuple(row for row in reading.offers if row.kind in kinds)
    return JSONResponse(
        {
            "offers": [row.model_dump(mode="json") for row in drawn[:FOLLOW_UPS_DRAWN]],
            "ranking": reading.ranking,
        }
    )


def _thread_tail(rendered: list[dict[str, object]]) -> tuple[tuple[str, str], ...]:
    """The end of the thread as the ranking reads it — what the member and the agent said, oldest
    first, bounded next to the call that sends it. A file card and a fold of detail carry no words a
    row could follow from.

    A message past the bound keeps its last characters rather than its first. What a row follows
    from is where the words were going — the conclusion a long reply reaches, the question it ends
    on — and a head-first cut takes exactly that and leaves the preamble."""
    said = [
        (str(row["role"]), str(row["text"])[-TAIL_TEXT_CHARS:])
        for row in rendered
        if row.get("role") in SAID_ROLES and isinstance(row.get("text"), str) and row["text"]
    ]
    return tuple(said[-TAIL_MESSAGES:])


async def conversation_follow_ups(ctx: SurfaceContext, request: Request) -> Response:
    """The rows the chat draws under a settled thread: up to four next turns, each a few words the
    member reads over the prompt pressing it sends.

    Written where they are read, for the turn they answer, by the read that finds none for it — a
    thread nobody has open is never ranked, and the turn's end fires nothing. A thread whose newest
    turn is still running answers none: the work the rows would follow from is not finished, and
    the member is watching it rather than choosing what comes next. The transcript is projected only
    by the read that ranks: every open tab asks this on its every mount, and the rows it is usually
    answered from were written for the turn it already holds.

    Which kinds of row this workspace can take is answered here rather than kept with them: `share`
    is a row only while Slack is installed, so a workspace that removed it stops offering the send
    on the next read with no new ranking.

    Admitted by the gate every other content read enters through, never a narrower one of its own:
    an agent a member reaches only through a member-private conversation is outside `allows` and
    inside `allows_chat`, so a second gate spelled here refused a thread the same screen draws and
    the member speaks in — and a first read that fails is never asked again.

    An empty answer is ordinary — a thread that supports no honest row draws nothing — and the page
    asks again when the next turn settles. `ranking` separates that from the other empty answer:
    another read holds the claim and is writing this turn's rows now, which is a reason to ask again
    in a moment rather than to wait out the page's own long interval."""
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    agent_id, conversation_id, viewer = authorized
    latest = await ctx.latest_turn(conversation_id)
    detail = None if latest is None else await ctx.turn_detail(latest)
    if detail is None or detail.turn.terminal is None:
        return _follow_ups(Reading())

    async def thread() -> tuple[tuple[str, str], ...]:
        rendered, _turn, _earlier = await _conversation_messages(
            ctx, agent_id, conversation_id, viewer.member_id, viewer.opens
        )
        return _thread_tail(rendered)

    installed = {entry.surface for entry in await ctx.list_installations()}
    kinds = ALWAYS_OFFERED | ({"share"} if SURFACE_SLACK in installed else frozenset())
    reading = await FollowUpCache(
        store=web_extension().store,
        conversation_id=conversation_id,
        turn_id=detail.turn.id,
        thread=thread,
        kinds=kinds,
        model=ctx.model,
        solvent=await _solvent(ctx),
    ).read()
    return _follow_ups(reading, kinds)


async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response:
    """One earlier page of a conversation whose transcript has compacted. The transcript's cursor
    names the bounded page above its tail and each page's cursor the one above it, so the pane
    follows the chain upward as the reader scrolls. Gated as the transcript is, so a page answers
    exactly where the transcript that names it answers."""
    authorized = await _readable_conversation(ctx, request)
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
            slot_context.conversation_id, limit=CONVERSATION_ARTIFACTS_MAX + 1, role="file"
        )
        truncated = len(listed_artifacts) > CONVERSATION_ARTIFACTS_MAX
        artifacts: list[ConversationArtifact] = []
        for artifact_entry in listed_artifacts[:CONVERSATION_ARTIFACTS_MAX]:
            try:
                artifact_url = ctx.artifact_link(artifact_entry.artifact)
                artifact_preview = None
                artifact_preview_url = ctx.artifact_preview_link(artifact_entry.artifact)
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


async def conversation_shell(ctx: SurfaceContext, request: Request) -> Response:
    """Whether this conversation has a terminal, and whether its sandbox is up. Gated as the slot
    reads are, so a terminal is offered exactly where the conversation's own content is readable —
    a subagent's conversation included, named through its root the same way."""
    authorized = await _slot_target(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    return JSONResponse(await shell_state(ctx, authorized.conversation_id))


async def conversation_shell_socket(ctx: SurfaceContext, websocket: WebSocket) -> None:
    """The member's terminal on this conversation's sandbox. The handshake carries the portal's own
    session cookie, so it is admitted through the gate the sibling GET answers — the same member,
    the same audience, the same 404 for a conversation they may not read, refused as a response
    before the socket is ever accepted."""
    authorized = await _slot_target(ctx, handshake_request(websocket))
    if isinstance(authorized, Response):
        await websocket.send_denial_response(authorized)
        return
    await ShellRelay(ctx, websocket, authorized.conversation_id).run()


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
    """Declared BYOK slots and their fill state — never a value. Workspace-scoped: the slots are the
    deploy's, shared across every agent."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    listed = await ctx.list_credential_slots()
    return JSONResponse(
        {
            "slots": [entry.model_dump(mode="json") for entry in listed],
            "actions": _action_payloads(ctx.object_actions(CREDENTIAL_KIND, "collection")),
        }
    )


async def workspace_email(ctx: SurfaceContext, request: Request) -> Response:
    """What this member may stop receiving, and whether they have.

    A preference is keyed by the address and held for the whole fleet, so this reads the reader's
    own and nobody else's — the same rule the tool behind the acts applies. A deploy with no send
    seam answers no topics and no acts, because it sends nothing to silence.

    Nothing here is workspace state, and the screen says so: what a workspace is doing with a
    member's money or access is not theirs to silence, and that line is the page's, not a row's."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, email, _audience = resolved
    held = await ctx.email_preferences(email)
    if held is None:
        return JSONResponse({"address": email, "topics": [], "actions": []})
    return JSONResponse(
        {
            "address": email,
            "topics": [
                {"topic": topic, "receiving": not silenced} for topic, silenced in held.items()
            ],
            "actions": _action_payloads(ctx.object_actions(MEMBER_KIND, "collection")),
        }
    )


async def workspace_team(ctx: SurfaceContext, request: Request) -> Response:
    """The workspace roster: who the members are, what each is called, which of them administer the
    workspace, and whose access is live — the same rows the `member` kind lists to a member asking
    the main agent, so the panel shows a non-admin exactly what chat would tell them. Each row names
    the stable member id the panel's acts apply to. `can_manage` reports whether this member may add
    another and change a role or an access state, read back from the verbs' own authority (the same
    `member.is_admin` row their gates check), never a second copy; the verbs refuse regardless."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, _email, audience = resolved
    drawn = {profile.id: profile for profile in await ctx.member_profiles()}
    return JSONResponse(
        {
            "members": [
                {
                    "id": str(entry.id),
                    "email": entry.email,
                    "admin": entry.admin,
                    "seated": entry.seated,
                    **_member_face(drawn.get(entry.id)),
                }
                for entry in await ctx.list_members()
            ],
            "can_manage": audience.admin,
            "actions": _action_payloads(ctx.object_actions(MEMBER_KIND, "collection")),
        }
    )


def _member_face(profile: MemberProfile | None) -> dict[str, object]:
    """What a screen draws one member as: the name to print, and the address of their picture when
    they have one. The address carries the digest, so a replaced picture is a new URL and no
    browser holds the old one; a member with no picture carries no address and the page draws their
    initials."""
    if profile is None:
        return {"name": None, "photo_url": None}
    return {
        "name": profile_name(profile),
        "photo_url": member_photo_path(profile.id, profile.photo_digest),
    }


REPORTED_SENDER = re.compile(r"\A(?P<name>.+) \((?P<email>[^()]+@[^()]+)\)\Z")


def reported_speaker_name(sender: str, email: str | None = None) -> str:
    """The display half of the line a surface reported a speaker under. Slack reports
    `Rae Whitlock (rae@example.com)`; a surface that reports a bare name reports itself, and one
    that reports a bare address is shortened to its local part, because a header reading
    `rae@example.com` over somebody's words names them the way a machine would.

    Read here rather than in the browser so a bubble and a notice name a speaker the same way
    without either parsing a composite string."""
    if email is not None:
        return sender.removesuffix(f" ({email})")
    found = REPORTED_SENDER.match(sender)
    if found is not None:
        return found.group("name")
    local, at, _ = sender.partition("@")
    return local if at and local else sender


async def _spoken_faces(
    ctx: SurfaceContext, spoken: Iterable[SpokenArrival], viewer: UUID
) -> dict[str, dict[str, object]]:
    """Who spoke each of a conversation's member-admitted rows, as the bubble draws them. The
    viewer's own words are unlabelled — the label marks exactly what somebody else said — so their
    rows are absent here, and one profile read covers every colleague the conversation names rather
    than one per bubble."""
    others = tuple(arrival for arrival in spoken if arrival.speaker_member_id != viewer)
    drawn = {
        profile.id: profile
        for profile in await ctx.member_profiles(
            frozenset(
                arrival.speaker_member_id
                for arrival in others
                if arrival.speaker_member_id is not None
            )
        )
    }
    faced = (
        (
            str(arrival.id),
            _speaker_face(
                None if arrival.speaker_member_id is None else drawn.get(arrival.speaker_member_id),
                arrival.sender,
            ),
        )
        for arrival in others
    )
    return {arrival_id: face for arrival_id, face in faced if face is not None}


async def _turn_faces(
    ctx: SurfaceContext, turns: Iterable[Turn], viewer: UUID
) -> dict[str, dict[str, object]]:
    """Who spoke each turn a transcript renders, as the bubble draws them. A turn names the message
    that founded it; `_spoken_faces` covers the messages folded into a running one, and the two maps
    are keyed apart, so a bubble finds its speaker under whichever id the transcript referred to it
    by."""
    spoke = tuple(
        turn
        for turn in turns
        if turn.context is not None
        and turn.context.sender is not None
        and turn.speaker_member_id != viewer
    )
    drawn = {
        profile.id: profile
        for profile in await ctx.member_profiles(
            frozenset(
                turn.speaker_member_id for turn in spoke if turn.speaker_member_id is not None
            )
        )
    }
    faced = (
        (
            str(turn.id),
            _speaker_face(
                None if turn.speaker_member_id is None else drawn.get(turn.speaker_member_id),
                turn.context.sender,
            ),
        )
        for turn in spoke
        if turn.context is not None
    )
    return {turn_id: face for turn_id, face in faced if face is not None}


def _speaker_face(profile: MemberProfile | None, sender: str | None) -> dict[str, object] | None:
    """Who a bubble says spoke, and the picture beside the name.

    The name a member chose answers first, then the name the surface reported on this very message,
    then the local part of their address. The middle step is what keeps a Slack channel reading as
    it always has: Slack knows a member as `Sam Frost` before this workspace holds a profile for
    them, and dropping to `sam` because no profile exists yet would lose a name the transcript
    already had. The picture is the member's own or none — no picture is derived per message.

    A guest another organization shares a channel with holds no member row, so the reported line is
    all there is and no picture stands for them."""
    reported = None if sender is None else reported_speaker_name(sender)
    if profile is not None:
        return {
            "name": profile.name or reported or profile_name(profile),
            "photo_url": member_photo_path(profile.id, profile.photo_digest),
            "email": profile.email,
        }
    if reported is None:
        return None
    return {"name": reported, "photo_url": None, "email": None}


def member_photo_path(member_id: UUID, digest: str | None) -> str | None:
    if digest is None:
        return None
    return f"{MEMBERS_PREFIX}{member_id}/photo?v={digest[:PHOTO_VERSION_CHARS]}"


async def member_photo(ctx: SurfaceContext, request: Request) -> Response:
    """One member's picture, served from the portal's own origin. Every workspace member reads who
    their colleagues are, so a signed-in session reads any of them; nothing outside a session reads
    any. The bytes were fetched or uploaded once and normalized then — the browser never reaches
    Slack or gravatar, which is why the portal's own `img-src` still names no picture host.

    The digest is the ETag, and the address carries it, so a held picture revalidates once and a
    replaced one is a different URL entirely."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    try:
        member_id = UUID(request.path_params["member_id"])
    except ValueError:
        return Response("no such member", status_code=404)
    profile = next(iter(await ctx.member_profiles(frozenset({member_id}))), None)
    if profile is None or profile.photo_digest is None:
        return Response("no photo", status_code=404)
    etag = f'"{profile.photo_digest}"'
    headers = {"etag": etag, "cache-control": PHOTO_CACHE}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    body = await ctx.member_photo(member_id)
    if body is None:
        return Response("no photo", status_code=404)
    return Response(body, media_type=PROFILE_PHOTO_MEDIA_TYPE, headers=headers)


async def workspace_profile(ctx: SurfaceContext, request: Request) -> Response:
    """The signed-in member's own profile screen: what they are called, their picture, and the
    address and role they cannot change here. `name` is what they chose and `drawn_name` what the
    product draws them as, which differ while a derived name is standing in — the screen offers the
    chosen one for editing and prints the drawn one, and says nothing about where it came from."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    profile = next(iter(await ctx.member_profiles(frozenset({member_id}))), None)
    if profile is None:
        return Response("no such member", status_code=404)
    return JSONResponse(
        {
            "id": str(member_id),
            "email": email,
            "admin": audience.admin,
            "name": profile.name,
            "drawn_name": profile_name(profile),
            "photo_url": member_photo_path(profile.id, profile.photo_digest),
            "actions": _action_payloads(ctx.object_actions(MEMBER_PROFILE_KIND, "collection")),
        }
    )


async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response:
    """Every stream of every connection this member may see, each naming the connection it hangs
    off so the connectors screen draws it under that account. Workspace-scoped: a stream never
    carried an agent, and the connection it hangs off is what decides who sees it."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, _audience = resolved
    listed = await ctx.list_sources(member_id)
    return JSONResponse({"sources": [entry.model_dump(mode="json") for entry in listed]})


class SurfaceRow(BaseModel):
    """One chat surface as the reading member stands with it: whether this deploy offers it, whether
    the reader reaches it, and for the terminal the line that installs the client."""

    name: str
    label: str
    offered: bool
    connected: bool
    install_command: str | None


def _connect_declared(ctx: SurfaceContext, surface: str, action: str) -> bool:
    """Whether this deploy declares the act that connects one surface. A pack that omits the
    surface's extension registers no such action, and dispatch would refuse the press, so the row
    states the surface as unofferable instead of drawing a Connect it cannot complete."""
    return any(
        view.name == action for view in ctx.object_actions(SURFACE_KIND, "instance", name=surface)
    )


async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response:
    """Two reads of the workspace's surfaces. `installations`: the surface installations bound to
    the agents this member's web audience holds, each with the agent its conversations land on —
    the edges the agents topology graph draws; an agent outside that audience is named nowhere here.
    `surfaces`: the three chat surfaces in fixed order as the reader stands with them — Slack is the
    workspace's installation, iMessage the reader's own proved address (an unproved reservation
    reaches nothing), the terminal the identity its first authenticated request links, with its
    install line built from the deploy's public base. A row is offered only where the act that
    connects it would dispatch: the deploy declares that surface's connect action, and iMessage
    additionally holds a provider."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    installations = await ctx.list_installations()
    visible = (entry for entry in installations if audience.allows(entry.agent_id))
    reached = await ctx.member_surfaces(member_id)
    install_command = (
        None
        if ctx.public_base_url is None
        else f"curl -fsSL {ctx.public_base_url.rstrip('/')}/ufo | sh"
    )
    surfaces = (
        SurfaceRow(
            name=SURFACE_SLACK,
            label="Slack",
            offered=_connect_declared(ctx, SURFACE_SLACK, SLACK_CONNECT_ACTION),
            connected=SURFACE_SLACK in {entry.surface for entry in installations},
            install_command=None,
        ),
        SurfaceRow(
            name=SURFACE_IMESSAGE,
            label="iMessage",
            offered=_connect_declared(ctx, SURFACE_IMESSAGE, IMESSAGE_CONNECT_ACTION)
            and imessage_offered(ctx.public_base_url),
            connected=SURFACE_IMESSAGE in reached,
            install_command=None,
        ),
        SurfaceRow(
            name=SURFACE_UFO,
            label="Terminal",
            offered=True,
            connected=SURFACE_UFO in reached,
            install_command=install_command,
        ),
    )
    return JSONResponse(
        {
            "installations": [entry.model_dump(mode="json") for entry in visible],
            "surfaces": [row.model_dump(mode="json") for row in surfaces],
        }
    )


CONNECT_STEP_NAMES = (SURFACE_SLACK,)

"""The kind the enrichment extension keeps a member's company and role on, and the act that fills
it from a confirmed website. A deploy without that kind offers the first run no website step."""
PROFILE_KIND = "enrichment_profile"
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
    team can say it uses, and the one of them the pages install themselves, beside whether the
    workspace holds it. The read also states whether the first run offers iMessage — its extension
    is in the deploy and this environment's flag is open — so every member sees that branch after
    the team step only where both hold, and whether the reading member holds a provider key of their
    own under either slot, which is the reader's answer rather than the workspace's: a key is what
    the member's own turns run on, so the step that offers the door reads the member.
    `workspace_domain` is the sign-up policy's verdict on the founding address: the company domain a
    work address founded the workspace under, and null where the policy made none — a personal-mail
    workspace, or one founded by `ufoctl init` — which the website step reads so a gmail.com founder
    is offered no website. The Slack step
    reads the leg its own Connect act writes: the surface installation `slack_connect` binds. That
    row is the whole workspace's rather than the reader's audience: it is stated as a bare boolean,
    and a step narrowed by audience would tell a member to install what the workspace already has.
    The step carries the catalog's own label, so a tile and the step it reveals never name one
    connector two ways."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, _audience = resolved
    installations, model_key_held, workspace_domain = await asyncio.gather(
        ctx.list_installations(),
        ctx.member_holds_own_model_key(member_id),
        ctx.founding_domain(),
    )
    surfaces = {entry.surface for entry in installations}
    held = {SURFACE_SLACK: SURFACE_SLACK in surfaces}
    return JSONResponse(
        {
            "providers": [tile.model_dump(mode="json") for tile in FIRST_RUN_PROVIDERS],
            "mcp_servers": [tile.model_dump(mode="json") for tile in MCP_SERVERS],
            "model_key_held": model_key_held,
            "workspace_domain": workspace_domain,
            "connectors": [
                ConnectStep(name=tile.name, label=tile.label, installed=held[tile.name]).model_dump(
                    mode="json"
                )
                for tile in FIRST_RUN_PROVIDERS
                if tile.name in CONNECT_STEP_NAMES
            ],
            "actions": {
                MEMBER_KIND: _action_payloads(ctx.object_actions(MEMBER_KIND, "collection")),
                MEMORY_KIND: _action_payloads(ctx.object_actions(MEMORY_KIND, "collection")),
                PROFILE_KIND: _action_payloads(ctx.object_actions(PROFILE_KIND, "collection"))
                if ctx.object_kind(PROFILE_KIND) is not None
                else [],
            },
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
DEFAULT_APP_SETUP_ASK = "Set up this app."


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
    line: str
    ask: str
    agent_id: UUID | None = None
    providers: tuple[MissingTile, ...] = ()


class UnlockRow(BaseModel):
    """The start screen's fourth row: an application this member is one or two accounts short of,
    stated as the work it does and the accounts it would take. `providers` is what is still
    missing, in catalog order, and pressing the row says the same build ask an owned row says — the
    agent asks for the accounts it finds it does not hold, and its reply carries the connect
    control. This is the one row that states a price, so it is the one row drawn as a line and a
    note; the rows above it say their sentence and nothing else."""

    mark: str
    line: str
    ask: str
    agent_id: UUID | None = None
    providers: tuple[MissingTile, ...]


@dataclass(frozen=True)
class StarterApp:
    id: UUID
    extension: str
    configured: bool


async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]:
    """The connectors this workspace already reaches, in the catalog's own vocabulary: the broker
    connections the reader's audience holds and the Slack the workspace installed. Read live, on
    every start screen, so an account connected a moment ago is never offered again."""
    connections = await ctx.list_connections(member_id, admin=admin)
    held = {view.provider for view in connections}
    if SURFACE_SLACK in {entry.surface for entry in await ctx.list_installations()}:
        held.add(SURFACE_SLACK)
    return frozenset(held)


def fill_starters(
    slate: Slate,
    held: frozenset[str],
    taken: frozenset[str],
    installed: tuple[StarterApp, ...] = (),
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
    and the rows it would otherwise fall back to need accounts just the same — so it reads as named
    work rather than as generic filler. A spare standing in an application's slot says its work and
    nothing about its accounts: the row closing the list is the one that states a price, and the
    ranking instructions already hold that an account is asked for once the work is agreed.

    A catalog row that names an installed app is different. It is offered only while the member has
    accepted none of that app's setup offers and the workspace already holds every account the row
    names. The account makes the installed app useful now; the row opens that app's own chat so its
    setup gains no duplicate. Two catalog rows for one app produce one offer, in rank order."""
    apps: list[StarterRow] = []
    short: list[UnlockRow] = []
    offered_apps: set[UUID] = set()
    for entry in slate.ranked:
        row = UNLOCKS_BY_NAME.get(entry.unlock)
        if row is None:
            continue
        missing = row.missing(held)
        match row:
            case AppUnlock(extension=extension):
                target = next((app for app in installed if app.extension == extension), None)
                if target is None or target.configured or missing or target.id in offered_apps:
                    continue
                offered_apps.add(target.id)
            case _:
                target = None
                if row.name in taken or entry.title.strip().lower() in taken:
                    continue
        if not missing:
            if len(apps) < STARTER_APP_SLOTS:
                apps.append(
                    StarterRow(
                        kind="app",
                        mark=row.mark,
                        line=entry.line,
                        ask=entry.ask if target is None else DEFAULT_APP_SETUP_ASK,
                        agent_id=None if target is None else target.id,
                    )
                )
        elif len(missing) <= UNLOCK_MAX_MISSING:
            short.append(
                UnlockRow(
                    mark=row.mark,
                    line=entry.line,
                    ask=entry.ask,
                    agent_id=None,
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
                line=spare.line,
                ask=spare.ask,
                providers=spare.providers,
            )
        )
    if slate.check_in is not None:
        apps.append(
            StarterRow(
                kind="check_in",
                mark=None,
                line=slate.check_in.line,
                ask=slate.check_in.ask,
            )
        )
    return tuple(apps), unlock


async def _solvent(ctx: SurfaceContext) -> bool:
    """Whether the balance would let this workspace start work — the gate's own question, asked for
    the workspace rather than for a turn.

    The bare balance line is not that question: a workspace under it that holds its own key in a
    model slot still runs every turn that model serves, so a line read off the reserve alone tells
    a member their work has stopped while it is running. The slots the exemption is tested against
    are the deploy's, so they ride the surface context rather than being rebuilt here."""
    ext = web_extension()
    async with ext.transaction() as connection:
        return await spend_admitted(connection, ext.workspace_id, ctx.own_key_slots)


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
    app_agents = tuple(
        (agent, agent.provisioned_by)
        for agent in audience.agents
        if agent.provisioned_by in STARTER_APP_EXTENSIONS
    )
    app_states = await asyncio.gather(
        *(ctx.agent_setup(agent.id, member_id) for agent, _extension in app_agents)
    )
    installed = tuple(
        StarterApp(id=agent.id, extension=extension, configured=_setup_configured(state))
        for (agent, extension), state in zip(app_agents, app_states, strict=True)
        if extension is not None
    )
    pending_extensions = frozenset(app.extension for app in installed if not app.configured)
    slate = await StarterCache(
        store=web_extension().store,
        member_id=member_id,
        agents=tuple(
            sorted(
                agent.name
                for agent in audience.agents
                if agent.provisioned_by not in pending_extensions
            )
        ),
        recalled=await _recalled(ctx, member_id),
        model=ctx.model,
        solvent=await _solvent(ctx),
    ).read()
    if slate is None:
        return JSONResponse({"starters": [], "unlock": None})
    held = await _held_providers(ctx, member_id, admin=audience.admin)
    taken = frozenset(agent.name.strip().lower() for agent in audience.agents)
    starters, unlock = fill_starters(slate, held, taken, installed)
    return JSONResponse(
        {
            "starters": [row.model_dump(mode="json") for row in starters],
            "unlock": None if unlock is None else unlock.model_dump(mode="json"),
        }
    )


AUTOMATION_HEROES = 3


class AutomationHero(BaseModel):
    """One suggestion the automations screen heads its list with: the recurring work the card would
    set up, said in the member's own voice by pressing it. A card names only work whose accounts the
    workspace already holds, so pressing one opens on the work rather than on an account request."""

    mark: str
    title: str
    line: str
    ask: str


async def workspace_automations(ctx: SurfaceContext, request: Request) -> Response:
    """The three suggestions the automations screen draws above its list, and alone while the member
    has saved none.

    Ranked the way the start screen's starters are ranked — same memory, same catalog, same cache
    rule — under the automations instructions, which ask for work that repeats rather than an
    application to build. The two slates are stored apart under their own keys, so a member reading
    both screens reads two rankings rather than one screen's rows twice.

    A row short of an account is dropped rather than drawn as an unlock: this screen offers the
    member work to automate now, and the accounts question belongs to the start screen. An empty
    answer is ordinary — a workspace whose memory says nothing yet has nothing to rank — and the
    page draws its own cards for every slot this read does not fill."""
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
        solvent=await _solvent(ctx),
        prompt=AUTOMATIONS_SLATE,
    ).read()
    if slate is None:
        return JSONResponse({"heroes": []})
    held = await _held_providers(ctx, member_id, admin=audience.admin)
    heroes: list[AutomationHero] = []
    for entry in slate.ranked:
        row = UNLOCKS_BY_NAME.get(entry.unlock)
        if row is None or row.missing(held):
            continue
        heroes.append(
            AutomationHero(mark=row.mark, title=entry.title, line=entry.line, ask=entry.ask)
        )
        if len(heroes) == AUTOMATION_HEROES:
            break
    return JSONResponse({"heroes": [hero.model_dump(mode="json") for hero in heroes]})


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
    reads_spoken: bool = False,
    admin_stops_fired: bool = False,
) -> tuple[UUID, UUID, str] | Response:
    """One turn this member may reach, as the member, the turn, and the email their audience is
    resolved from, or the refusal to answer with. A mutation requires the member's own turn — the
    one they spoke, or the one a task or trigger fires on their behalf. A stream (`reads_spoken`)
    also reads any turn of a conversation they may speak in. The stop lane alone passes
    `admin_stops_fired`: an admin may end a fired turn in a conversation the chat lane has already
    admitted them to, the cadence management the task kind grants them, and nothing else — the
    stream and the connect handoff read a turn's frames, which never widen for an admin. The agent
    must still be reachable by their web audience or that conversation."""
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
    spoken = (
        await _spoken_conversation(
            ctx, audience, detail.turn.agent_id, member_id, detail.turn.conversation_id
        )
        if owner != member_id or not agent_visible
        else None
    )
    manages = (
        admin_stops_fired
        and detail.turn.fired_by is not None
        and audience.admin
        and spoken is not None
    )
    if owner != member_id and not manages and (not reads_spoken or spoken is None):
        if owner is None:
            return absent
        return Response(
            "That turn belongs to another member.",
            status_code=403,
            headers={REFUSAL_HEADER: "1"},
        )
    if not agent_visible and spoken is None:
        return absent
    return member_id, turn_id, email


async def stream(ctx: SurfaceContext, request: Request) -> Response:
    reached = await _member_turn(ctx, request, reads_spoken=True)
    if isinstance(reached, Response):
        return reached
    member_id, turn_id, email = reached
    since = request.headers.get("last-event-id", "")
    return StreamingResponse(
        _events(ctx, turn_id, member_id, since, email), media_type="text/event-stream"
    )


def _event(name: str, payload: dict[str, object], cursor: str = "") -> bytes:
    head = f"id: {cursor}\n".encode() if cursor else b""
    return head + f"event: {name}\ndata: ".encode() + json.dumps(payload).encode() + b"\n\n"


async def _pending_prompts(
    ctx: SurfaceContext, request_: CredentialRequest, member_id: UUID
) -> dict[str, object] | None:
    """The credential prompts of a terminal request still awaiting values, as the page renders
    them — an authenticated read renews the short-lived seal, while the request's stable marker
    keeps each fulfilled prompt closed and an unanswered sibling asking."""
    sealed = await ctx.renew_credential_request(request_.sealed, member_id)
    if sealed is None:
        return None
    pending = [
        {"slot": prompt.slot, "prompt": prompt.prompt}
        for prompt in request_.prompts
        if await ctx.credential_prompt_pending(sealed, prompt.slot)
    ]
    if not pending:
        return None
    return {"reason": request_.reason, "sealed": sealed, "prompts": pending}


def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]:
    """One shared file as the chat draws it, carrying a `preview_url` when the file is itself a
    picture — the chat draws those inline in the reply. Both links carry their base: the
    chat is drawn by the portal and by an app page framed on its own origin, so a picture named
    without one resolves against whichever origin happens to draw it. `media_type` is how the chat
    knows which cards the artifacts sidebar can draw as a document, and `role` which files it draws
    as cards and which as the download buttons under the reply that carried them."""
    return {
        "id": str(artifact.id),
        "filename": artifact.filename,
        "subject": artifact.subject,
        "media_type": artifact.media_type,
        "size_bytes": artifact.size_bytes,
        "role": artifact.role,
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
        shared_keys: tuple[str, ...] = ()
        async for cursor, frame in frames:
            if isinstance(frame, ArtifactsChanged):
                artifacts = await ctx.shared_artifacts(turn_id)
                shared = [a for a in artifacts if not a.attached_by_member]
                shared_keys = tuple(artifact.blob_key for artifact in shared)
                yield _event("files", {"files": [_file_payload(ctx, a) for a in shared]}, cursor)
                continue
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
                        yield _event("subagent", _run_payload(run))
                request = frame.frame.connect_request
                if (
                    request is not None
                    and request.requester_member_id == member_id
                    and ctx.connect_available()
                ):
                    yield _event("connect", _connect_control(ctx, request.provider, turn_id))
                if frame.frame.credential_request is not None:
                    prompts = await _pending_prompts(ctx, frame.frame.credential_request, member_id)
                    if prompts is not None:
                        yield _event("credentials", prompts)
                artifacts = await ctx.shared_artifacts(turn_id)
                shared = [a for a in artifacts if not a.attached_by_member]
                keys = tuple(artifact.blob_key for artifact in shared)
                if shared and keys != shared_keys:
                    yield _event("files", {"files": [_file_payload(ctx, a) for a in shared]})
                if frame.frame.created:
                    audience = await web_audience(ctx, web_extension(), email)
                    apps = await _created_apps(
                        ctx, {str(turn_id): frame.frame.created}, _opens(audience)
                    )
                    if apps:
                        yield _event("apps", {"apps": apps[str(turn_id)]})
                question = frame.frame.question
                if question is not None and question.target_member_id not in (None, member_id):
                    frame = Terminal(frame=frame.frame.model_copy(update={"question": None}))
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
    except CredentialValueInvalid as error:
        return Response(f"not stored: {error}", status_code=400)
    return JSONResponse({"stored": slot})


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


async def _detail_agent(
    ctx: SurfaceContext, request: Request, audience: WebAudience, kind: PortalKind, name: str
) -> AgentSummary | Response:
    """The agent namespace one detail read runs in: `agent` where the read names one, else — for a
    conversation alone, whose row is bound to its agent — the conversation's own, gated by the
    viewer's chat reach."""
    if kind.kind != CONVERSATION_KIND or request.query_params.get("agent", ""):
        return _object_agent(request, audience)
    absent = Response(f"no {kind.kind} named {name!r}", status_code=404)
    try:
        conversation_id = UUID(name)
    except ValueError:
        return absent
    agent_id = await ctx.conversation_agent(conversation_id)
    agent = next((held for held in audience.chat_agents if held.id == agent_id), None)
    return absent if agent is None else agent


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


def _action_payloads(views: tuple[ActionView, ...]) -> list[dict[str, object]]:
    return [view.model_dump(mode="json", exclude_none=True) for view in views]


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
    """One opaque continuation for a fanned-out index — each still-walking lane's own kind cursor
    under the lane's key — or None when every lane's walk is done. A lane is one agent for a read
    of one kind, and one kind of one agent for a read merging kinds."""
    if not walking:
        return None
    return json.dumps(walking, sort_keys=True).encode().hex()


def _fanout_walks(token: str) -> dict[str, str] | None:
    """The per-lane cursors a fan-out token carries, or None for a token this route never
    minted."""
    try:
        decoded = json.loads(bytes.fromhex(token).decode())
    except ValueError:
        return None
    if not isinstance(decoded, dict) or not decoded:
        return None
    walks: dict[str, str] = {}
    for lane, held in decoded.items():
        if not lane or not isinstance(held, str) or not held:
            return None
        walks[lane] = held
    return walks


def _task_row(row: ObjectRow, kind: str, agent: AgentSummary) -> dict[str, object]:
    """One row of the merged automations page. A trigger carries the provider slug its feed
    authenticates as; the screen reads the connector's display name, which is this surface's to
    spell — the same name the connectors page draws."""
    provider = row.fields.get("provider")
    labelled = (
        {"provider_label": PROVIDER_LABELS.get(provider, provider)}
        if isinstance(provider, str)
        else {}
    )
    return {
        "name": row.name,
        "summary": row.summary,
        **row.fields,
        **labelled,
        "kind": kind,
        "agent_id": str(agent.id),
        "agent_name": agent.name,
    }


def _task_lane(kind: str, agent_id: UUID) -> str:
    """One lane of the merged automations page: one kind of one agent, each walking its own
    cursor."""
    return kind + TASK_LANE_SEPARATOR + str(agent_id)


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
    agent the viewer's web audience holds, except that artifacts use the member's audience before
    administration widens direct access. Every row names the agent that owns it either way, so a
    section listing one kind across the workspace addresses each edit to the right lane. `q`
    searches, `order_by`/`order` sort, `cursor` continues the walk, and every remaining query
    parameter is an exact filter; a field the kind never declared is the kind's own refusal, so the
    page offers only what the kind admits. A fanned-out read takes `OBJECT_FANOUT_LIMIT` rows from
    each agent, re-ranks the merge, and continues on a compound token — one kind cursor per agent
    still walking, so each agent's page resumes exactly where its own walk stopped and an agent
    whose rows ran out leaves the token. The agents' reads run together, `OBJECT_READ_FANOUT`
    wide, so the member waits for the slowest of them rather than the sum — and no read holds more
    transactions than that on the pool the whole deploy shares."""
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
    fanout_agents = audience.member_agents if kind.kind == ARTIFACT_KIND else audience.agents
    agents = fanout_agents
    continuations: dict[str, str] = {}
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
        agents = tuple(agent for agent in fanout_agents if str(agent.id) in walks)
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
    fanout = asyncio.Semaphore(OBJECT_READ_FANOUT)

    async def page_of(agent: AgentSummary) -> ObjectPage | None:
        async with fanout:
            return await ctx.list_member_objects(
                kind.kind,
                agent.id,
                member_id,
                admin=audience.admin and kind.kind != SITE_KIND,
                query=(
                    query if named else replace(query, cursor=continuations.get(str(agent.id), ""))
                ),
            )

    try:
        pages = await asyncio.gather(*(page_of(agent) for agent in agents))
    except ValueError as error:
        return Response(str(error), status_code=400)
    rows: list[dict[str, object]] = []
    walk: str | None = None
    walking: dict[str, str] = {}
    cut = False
    for agent, page in zip(agents, pages, strict=True):
        if page is None:
            return Response(f"{kind.kind} does not list in the portal", status_code=404)
        cut = cut or page.cut
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
    return JSONResponse(
        {
            **_kind_payload(kind),
            "objects": rows,
            "next_cursor": walk,
            "cut": cut,
        }
    )


async def automations_index(ctx: SurfaceContext, request: Request) -> Response:
    """The workspace's scheduled tasks and source triggers as one page, most recently run first —
    the Automations screen's listing.

    Both kinds declare `last_run_at`, so the merge ranks them on one column: a scheduled task's own
    last fire, a source trigger's newest woken turn, and a row that has never run after every row
    that has. Each kind answers per agent through its own visibility gate, so a member reads exactly
    the rows that kind admits. One lane is one kind of one agent; the page takes
    `OBJECT_FANOUT_LIMIT` rows from each, re-ranks the merge, and continues on one compound cursor
    holding every still-walking lane's own kind cursor, so the next page resumes each lane where
    it stopped.
    `q` searches. A kind this deploy does not register is absent rather than an error, since the
    screen lists whatever the deploy installs."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    kinds = tuple(
        held for held in (ctx.object_kind(name) for name in TASK_KINDS) if held is not None
    )
    cursor = request.query_params.get("cursor", "")
    continuations: dict[str, str] = {}
    if cursor:
        walks = _fanout_walks(cursor)
        if walks is None:
            return Response("malformed fan-out cursor", status_code=400)
        continuations = walks
    query = ObjectListQuery(
        query=request.query_params.get("q", ""),
        filters={},
        order_by=LAST_RUN_FIELD,
        order="desc",
        cursor="",
    )
    lanes = tuple(
        (kind, agent)
        for kind in kinds
        for agent in audience.agents
        if not cursor or _task_lane(kind.kind, agent.id) in continuations
    )
    fanout = asyncio.Semaphore(OBJECT_READ_FANOUT)

    async def page_of(kind: PortalKind, agent: AgentSummary) -> ObjectPage | None:
        async with fanout:
            return await ctx.list_member_objects(
                kind.kind,
                agent.id,
                member_id,
                admin=audience.admin,
                query=replace(query, cursor=continuations.get(_task_lane(kind.kind, agent.id), "")),
            )

    try:
        pages = await asyncio.gather(*(page_of(kind, agent) for kind, agent in lanes))
    except ValueError as error:
        return Response(str(error), status_code=400)
    rows: list[dict[str, object]] = []
    walking: dict[str, str] = {}
    for (kind, agent), page in zip(lanes, pages, strict=True):
        if page is None:
            return Response(f"{kind.kind} does not list in the portal", status_code=404)
        rows.extend(_task_row(row, kind.kind, agent) for row in page.rows[:OBJECT_FANOUT_LIMIT])
        if page.next_cursor:
            walking[_task_lane(kind.kind, agent.id)] = page.next_cursor
    rows.sort(key=lambda row: _merged_rank(row, LAST_RUN_FIELD), reverse=True)
    return JSONResponse(
        {
            "kinds": [_kind_payload(kind) for kind in kinds],
            "objects": rows,
            "next_cursor": _fanout_token(walking),
        }
    )


async def object_detail(ctx: SurfaceContext, request: Request) -> Response:
    """One object as the signed-in member reads it: the spec its kind applied, the declared fields
    that are its live state, its typed outgoing links — each naming an object and saying whether
    this member's read of that row answers, since a kind reading for members is not that row
    reading for this one — the row's timestamps, and the agent it was read under. `spec` is null
    where the kind elides content the member may not read. A row the member may not see is
    not-found, exactly as an absent one is.

    A conversation read naming no `agent` is the `#/c/<id>` permalink's resolve: the conversation
    names its agent, and the reach is chat reach rather than web reach, so an agent reached only
    through a member-private extension conversation resolves that conversation — the member's own —
    and no other. The row itself is the one `objects/conversation` lists, so a permalink an admin
    was offered resolves with `readable` false and `disclosable` true rather than as absent."""
    gated = await _object_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, kind = gated
    name = request.path_params["name"]
    agent = await _detail_agent(ctx, request, audience, kind, name)
    if isinstance(agent, Response):
        return agent
    agent_id, admin = agent.id, audience.admin
    found = await ctx.member_object(kind.kind, name, agent_id, member_id, admin=admin)
    if found is None:
        return Response(f"no {kind.kind} named {name!r}", status_code=404)
    if kind.kind == CONVERSATION_KIND and not _reaches(
        audience, agent_id, str(found.row.fields["audience"]), member_id
    ):
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
            "agent_id": str(agent.id),
            "agent_name": agent.name,
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
    """One SSE event, named by `frame_event`. A non-empty cursor is emitted as the event `id:`,
    which the browser echoes as `Last-Event-ID` on reconnect, so a dropped stream resumes from the
    last frame it rendered. A text delta rides the unnamed `message` event, and a terminal frame
    crosses unwrapped."""
    head = f"id: {cursor}\n".encode() if cursor else b""
    event = frame_event(frame)
    if event is None:
        raise ValueError(f"unmapped live frame {type(frame).__name__}")
    payload = frame_payload(frame).encode()
    if isinstance(frame, TextDelta):
        return head + b"data: " + payload + b"\n\n"
    return head + b"event: " + event.encode() + b"\ndata: " + payload + b"\n\n"


async def intents(ctx: SurfaceContext, request: Request) -> Response:
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, email, _audience, agent_id = gated
    return await submit_intent(ctx, request, agent_id, member_id, email)


async def actions(ctx: SurfaceContext, request: Request) -> Response:
    """The presented-action lane: the path names the kind, the action, and — for an instance
    action — the row; the lane's agent is the path's; the body is the action's own input alone.
    The route binds the target, never the browser."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, email, _audience, agent_id = gated
    params = request.path_params
    return await submit_action(
        ctx,
        request,
        agent_id,
        member_id,
        email,
        kind=params["kind"],
        name=params.get("name"),
        action=params["action"],
    )


async def action_views(ctx: SurfaceContext, request: Request) -> Response:
    """The acts a kind presents, projected from the declarations alone: a collection's for the
    bare kind, and for a named row the instance actions open on it or pinned to it. A panel holding
    a target the portal offers no row read for — an archived app, the workspace, another member's
    private conversation — still draws the controls its declarations offer; dispatch's recheck and
    the handler stay the authority."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    kind = request.path_params["kind"]
    if ctx.object_kind(kind) is None:
        return Response(f"no object kind named {kind!r}", status_code=404)
    name = request.path_params.get("name")
    binding: ActionBinding = "collection" if name is None else "instance"
    return JSONResponse({"actions": _action_payloads(ctx.object_actions(kind, binding, name=name))})


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
        intent = ToolIntent(
            tool="object_delete",
            input={
                "kind": kind,
                "name": name,
            },
        )
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
        intent = ToolIntent(
            tool="object_apply",
            input={
                "manifest": manifest,
            },
        )
    conversation_id = await ctx.conversation_for(
        f"{PORTAL_LANE_PREFIX}{agent.id}/{email}",
        conversation_audience(member_id),
        agent_id=agent.id,
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
    return await agent_settings(
        ctx, agent_id, member_id, admin=audience.admin, archivable=archivable
    )


async def agent_setup(ctx: SurfaceContext, request: Request) -> Response:
    """What the selected app needs before it works: the accounts its provision declared, which of
    them this member can work from, the workspace credentials it cannot run without, and whether
    it holds the standing order that gives it an occasion to run.

    It answers the agent's whole web audience, like the settings read beside it — what an app runs
    on is what the app is, and a member who cannot see it cannot tell a resting app from an unwired
    one. Which account answered a provider is not here; that is `agents/{id}/connections`, gated on
    the member.

    Each account carries the words the portal names that provider by. The declaration holds a slug,
    and a step reading "Connect googlecalendar" names the wire rather than the account a member
    would go and find; the label is added here because this is where the portal's own naming lives,
    beside the connect tiles it has to agree with."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    state = await ctx.agent_setup(agent_id, member_id)
    payload = state.model_dump(mode="json")
    payload["connectors"] = [
        {
            **connector,
            "label": _provider_label(ctx, connector["provider"]),
            "summary": _provider_summary(connector["provider"]),
        }
        for connector in payload["connectors"]
    ]
    summary = next(a for a in audience.agents if a.id == agent_id)
    own_page = await _bound_page(ctx, summary, member_id) is not None
    return JSONResponse({**payload, "own_page": own_page})


async def _bound_page(
    ctx: SurfaceContext,
    summary: AgentSummary,
    member_id: UUID,
) -> ObjectRow | None:
    """The page this workspace built this agent and bound as its homepage, or `None` where it built
    none. One read answers both what the page is and whether the workspace built it, so the
    homepage state and the setup gate can never disagree about whose page this is.

    A bound row is what the workspace built, not what a page a member may look at: a shipped app
    always has a page — the deploy carries one for every workspace — so "is a page available" is
    answered `yes` from the first moment and could gate nothing.

    What it gates is where a shipped app stands. Until the workspace builds, the app stands on its
    setup screen, which is what holds the accounts, the installs, the cadences, and the Build app
    press; from the first build it stands on the page it built, for good. The deploy's own page is
    the shape that build starts from rather than a screen a member browses first — an app the
    workspace has not wired has nothing real to draw on it."""
    page = await ctx.list_member_objects(
        SITE_KIND,
        summary.id,
        member_id,
        admin=True,
        query=ObjectListQuery(filters={"homepage_agent": str(summary.id)}),
    )
    if page is None:
        return None
    return next((row for row in page.rows if "site_url" in row.fields), None)


async def homepage(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's homepage: the frame link of the hosted site `set_homepage` bound.
    Its audience is the one the frame admits, so the read never hands out a link that renders a
    refusal: a forked row follows the agent — every member for a workspace-visible agent, the
    owner and admins for a private one — and a shipped app bundle follows the member's web
    audience alone, which is the whole gate the frame puts on the deploy's own code. The binding
    is read past the row gate (the row's own column is dormant while bound) and this handler
    applies the agent rule itself; a binding that no longer resolves and an agent that never
    bound one answer the same absent state.

    Two states: `set` once a page is bound or shipped, and `none` where none exists yet. A first
    build in flight is simply a page not there yet, never a state of its own."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    summary = next(a for a in audience.agents if a.id == agent_id)
    await _assets_published(ctx.fleet_blob, apps())
    bound = await _bound_page(ctx, summary, member_id)
    return JSONResponse(_homepage_state(ctx, summary, bound, audience.admin, member_id))


def _homepage_state(
    ctx: SurfaceContext,
    summary: AgentSummary,
    bound: ObjectRow | None,
    admin: bool,
    member_id: UUID,
) -> dict[str, JsonValue]:
    """One agent's homepage: `set` for a forked hosted_site row or the shipped bundle, `none` when
    it has no page — a first page still building is simply `none` until it registers, and a page
    being rebuilt keeps its prior version (the forked row's url and generation, or the bundle still
    serving) so it answers `set` throughout. The boot index carries it on the agent object, so a
    screen paints the page from what boot resolved; the granular route begins polling after its
    first interval, avoiding a duplicate initial read while still landing a deployment without a
    reload. Each page is gated the way the frame gates it. A forked row takes the agent rule — a
    private agent's own page answers `none` to anyone but its owner and an admin — while the
    shipped bundle is the deploy's code, identical for every workspace and holding no data, which
    the frame hands to whoever asks: it answers every member whose web audience already holds the
    agent, the member a grant put there included, because the reader passed that gate to reach
    this read at all. A shipped page's `deploy_generation` is the digest folded to a JS-safe int,
    so the frame's identity moves onto the new bundle across the next answer. `bound` is the page
    row the workspace built, read by `_bound_page`."""
    if bound is not None:
        if summary.visibility != "workspace" and member_id != summary.owner_member_id and not admin:
            return {"state": "none"}
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


PREVIEW_PAGE_BATCH = 8


def _preview_start_page(form: FormData) -> int:
    raw = form.get("start_page")
    try:
        return max(1, int(str(raw)))
    except (TypeError, ValueError):
        return 1


def _preview_pages(form: FormData) -> int:
    raw = form.get("pages")
    try:
        return min(max(1, int(str(raw))), PREVIEW_PAGE_BATCH)
    except (TypeError, ValueError):
        return PREVIEW_PAGE_BATCH


async def preview(ctx: SurfaceContext, request: Request) -> Response:
    """Render a batch of one attached file's pages to preview PNGs so the composer shows the member
    the document they are about to send. The bytes go to the preview service and the pictures come
    straight back — nothing is stored and no turn is admitted: this changes nothing, it only renders
    for display, so it is not a member action the chat transport must carry. A valid member session
    is the whole gate; the render is agent-agnostic, so the route is not scoped to one.

    The reply names how many pages the file has, so a caller knows whether asking from a later
    `start_page` would draw anything, and the pictures ride as base64 because the page draws them as
    `data:` URLs — its policy admits those and no `blob:` at all. `pages` is how many the caller
    draws: it is clamped here to `PREVIEW_PAGE_BATCH` rather than trusted from the form, and a
    caller naming none is answered with the whole batch."""
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
    start_page = _preview_start_page(form)
    rendered = await ctx.render_preview(
        kind, await upload.read(), start_page=start_page, pages=_preview_pages(form)
    )
    if rendered is None:
        return Response("no preview", status_code=415)
    return JSONResponse(
        {
            "start_page": rendered.start_page,
            "page_count": rendered.page_count,
            "pages": [base64.b64encode(page).decode() for page in rendered.pages],
        }
    )


async def upload_start(ctx: SurfaceContext, request: Request) -> Response:
    """Mint the URL one attachment's bytes travel by, so a send never carries them. The member
    picks a file, the page names its size and sha256, the browser PUTs the bytes to the blob store
    directly, and the send names the key they already sit under — the composer body then carries
    text and references only, so the 25 MB framing bounds the message rather than the attachment.

    The key is the artifact key the file keeps: nothing is copied later, the row the send records
    points at these bytes, and the preview service draws the cover from them where they lie. The URL
    is measured — the size and the checksum ride the signature, so S3 stores exactly the file the
    member picked — and a detached signature over the key comes back with it, which the send
    presents so the deploy admits only a key it minted rather than any artifact the store holds. The
    size is capped at what a workspace write accepts, since bytes the send cannot deliver are bytes
    the store would keep for nothing. A filesystem dev store signs nothing and says so: dev deploys
    stream attachments through the composer body, under the framings `_parse_inbound` already
    bounds."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    raw = await _bounded_body(request, MAX_FORM_BYTES)
    if isinstance(raw, Response):
        return raw
    try:
        body = json.loads(raw)
        size_bytes = int(body["size_bytes"])
        checksum_sha256 = str(body["sha256"])
        name = str(body.get("name") or "file")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return Response("malformed upload request", status_code=400)
    if size_bytes <= 0:
        return Response("size must be positive", status_code=400)
    if size_bytes > WORKSPACE_WRITE_MAX_BYTES:
        return Response(
            f"an attachment is capped at {WORKSPACE_WRITE_MAX_BYTES} bytes", status_code=413
        )
    grant = await ctx.mint_upload(name, size_bytes, checksum_sha256)
    if grant is None:
        return Response("presigned upload requires the s3 blob store", status_code=409)
    return JSONResponse({"key": grant.blob_key, "put_url": grant.put_url, "sig": grant.signature})


ROUTES = (
    SurfaceRoute(method="GET", path="", handler=portal_page),
    SurfaceRoute(method="POST", path="", handler=open_session),
    SurfaceRoute(method="GET", path="static/{asset:path}", handler=static_asset),
    SurfaceRoute(method="GET", path="api/agents", handler=agents_index),
    SurfaceRoute(method="GET", path="api/agents/status", handler=agents_status),
    SurfaceRoute(method="GET", path="api/chats", handler=chats_index),
    SurfaceRoute(method="POST", path="agents/{agent_id}/chat", handler=chat),
    SurfaceRoute(method="POST", path="preview", handler=preview),
    SurfaceRoute(method="POST", path="uploads", handler=upload_start),
    SurfaceRoute(method="GET", path="agents/{agent_id}/settings", handler=settings),
    SurfaceRoute(method="GET", path="agents/{agent_id}/setup", handler=agent_setup),
    SurfaceRoute(method="GET", path="agents/{agent_id}/homepage", handler=homepage),
    SurfaceRoute(method="POST", path="agents/{agent_id}/intents", handler=intents),
    SurfaceRoute(method="POST", path="agents/{agent_id}/actions/{kind}/{action}", handler=actions),
    SurfaceRoute(
        method="POST", path="agents/{agent_id}/actions/{kind}/{name}/{action}", handler=actions
    ),
    SurfaceRoute(method="GET", path="actions/{kind}", handler=action_views),
    SurfaceRoute(method="GET", path="actions/{kind}/{name}", handler=action_views),
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
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/transcript",
        handler=conversation_transcript,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/follow-ups",
        handler=conversation_follow_ups,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/slots",
        handler=conversation_slots,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/shell",
        handler=conversation_shell,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/slots/{slot_id}",
        handler=conversation_slot,
    ),
    SurfaceRoute(method="GET", path="workspace/team", handler=workspace_team),
    SurfaceRoute(method="GET", path="workspace/email", handler=workspace_email),
    SurfaceRoute(method="GET", path="workspace/profile", handler=workspace_profile),
    SurfaceRoute(method="GET", path="members/{member_id}/photo", handler=member_photo),
    SurfaceRoute(method="GET", path="workspace/sources", handler=workspace_sources),
    SurfaceRoute(method="GET", path="workspace/surfaces", handler=workspace_surfaces),
    SurfaceRoute(method="GET", path="workspace/credentials", handler=workspace_credentials),
    SurfaceRoute(method="GET", path="workspace/memory", handler=workspace_memory),
    SurfaceRoute(method="GET", path="workspace/first-run", handler=workspace_first_run),
    SurfaceRoute(method="GET", path="workspace/starters", handler=workspace_starters),
    SurfaceRoute(method="GET", path="workspace/automations", handler=workspace_automations),
    SurfaceRoute(method="GET", path="automations", handler=automations_index),
    SurfaceRoute(method="GET", path="objects/{kind}", handler=object_index),
    SurfaceRoute(method="GET", path="objects/{kind}/{name}", handler=object_detail),
    SurfaceRoute(method="POST", path="objects/{kind}", handler=object_write),
    SurfaceRoute(method="POST", path="objects/{kind}/{name}/delete", handler=object_write),
    SurfaceRoute(method="GET", path="workspace/object-changes", handler=object_changes),
    SurfaceRoute(method="GET", path="workspace/usage", handler=workspace_usage),
    SurfaceRoute(method="GET", path="turns/{turn_id}/stream", handler=stream),
    SurfaceRoute(method="GET", path="turns/{turn_id}/connect", handler=connect_handoff),
    SurfaceRoute(method="POST", path="credentials", handler=fulfill_credential),
    SurfaceRoute(method="GET", path=ACCOUNTS_PATH, handler=workspace_accounts),
    SurfaceRoute(method="GET", path=SIGN_IN_PATH, handler=connect_arrival),
    SurfaceRoute(method="GET", path=ANTHROPIC_SIGN_IN_PATH, handler=connect_arrival),
    SurfaceRoute(method="POST", path=DEVICE_PATH, handler=openai_device),
    SurfaceRoute(method="GET", path=POLL_PATH, handler=openai_device_poll),
    SurfaceRoute(method="POST", path=ANTHROPIC_AUTHORIZE_PATH, handler=anthropic_authorize),
    SurfaceRoute(method="POST", path=ANTHROPIC_CODE_PATH, handler=anthropic_code),
    SurfaceRoute(
        method="POST", path="accounts/{provider}/" + DISCONNECT_SUFFIX, handler=account_disconnect
    ),
)

SOCKETS = (
    SurfaceSocket(
        path="agents/{agent_id}/conversations/{conversation_id}/shell",
        handler=conversation_shell_socket,
    ),
)
