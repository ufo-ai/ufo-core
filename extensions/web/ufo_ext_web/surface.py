"""The web portal on the core surface seam, in its live mode: the authenticated shell around the
member's agents — the page and the one POST that opens its session, per-agent chat with
cookie-authenticated turn admission (each member holds any number of conversations per agent,
opened by the first message and listed for the rail) and an SSE tail of each turn's live frames,
read projections (the agent index, conversation transcripts, overviews, skills, per-agent usage,
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
surface admits onto; each conversation binds permanently to the agent the member selected, web
admits without writeback and delivers by tailing the hub over SSE in its own stream route, never
through the writeback poller. Everything web-specific lives here, reaching core only through the
privileged `SurfaceContext` — the SDK surface a CI gate pins."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, JsonValue, ValidationError

from ufo.sdk.audience import audience_subjects, conversation_audience
from ufo.sdk.bearer import LOGIN_PATH, SESSION_COOKIE, verify_token, workspace_claim
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
    CostTick,
    LiveFrame,
    Parked,
    SkillLoad,
    Terminal,
    ToolCall,
    tool_activity,
)
from ufo.sdk.listings import ListingCursor, MalformedCursor
from ufo.sdk.manifest import (
    CONVERSATION_ARTIFACTS_MAX,
    CONVERSATION_AUTOMATIONS_MAX,
    CONVERSATION_SITES_MAX,
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
from ufo.sdk.memory import MemoryMatch
from ufo.sdk.models import Message, ModelRequest, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.sdk.o11y import log
from ufo.sdk.objects import ObjectListQuery
from ufo.sdk.seats import Seats
from ufo.sdk.surfaces import (
    AgentSummary,
    ConnectRequestInvalid,
    CredentialRequest,
    CredentialRequestInvalid,
    ListedConversation,
    PortalKind,
    SubagentDetail,
    SubagentRun,
    SurfaceAuth,
    SurfaceContext,
    SurfaceRoute,
    TerminalFrame,
    Turn,
    TurnContext,
    inbox_name,
    member_message_text,
)
from ufo.sdk.tools import REQUESTED_BY
from ufo_ext_web.audience import WebAudience, granted_emails, web_audience, web_extension
from ufo_ext_web.community import COMMUNITY, CommunityUnavailable
from ufo_ext_web.panels import ApplyIntent, agent_create_schema, agent_overview, submit_intent

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
ANSWER_TURN_HEADER = "x-ufo-answer-turn"
ANSWER_QUESTION_HEADER = "x-ufo-answer-question"
SESSION_FAULT_HEADER = "x-ufo-session-fault"
REFUSAL_HEADER = "x-ufo-refusal"
NO_MEMBER_FAULT = "no-member"
MAX_MEMORY_QUERY_CHARS = 500
MEMORY_RECENT_LIMIT = 100
MEMORY_RESULT_LIMIT = 100
ARTIFACT_LIST_LIMIT = 100
ARTIFACT_MEDIA_FILTERS = frozenset(("image", "document", "data", "other"))
OBJECT_FANOUT_LIMIT = 50
CONVERSATION_LIST_LIMIT = 100
SUBAGENT_ACTIVITY_LIMIT = 40
SUBAGENT_EVENT_LIMIT = 100
CHAT_STORE_PREFIX = "chat/"
CHAT_PENDING_PREFIX = "chat_title_pending/"
TITLE_JOB_NAME = "chat_titles"
TITLE_JOB_SCHEDULE = "*/15 * * * * *"
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
PORTAL_PATH = "/surface/web"
CHAT_TARGET_PARAM = "c"
PORTAL_BUILD = (
    "npm --prefix extensions/web/frontend ci && npm --prefix extensions/web/frontend run build"
)
STATIC_DIR = Path(__file__).parent / "static"
PORTAL_FILE = STATIC_DIR / "index.html"
PORTAL_HTML = PORTAL_FILE.read_text() if PORTAL_FILE.is_file() else None
STATIC_PREFIX = f"{PORTAL_PATH}/static/"
ASSET_MEDIA_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
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
    token — the type the signed-in card posts — so an unauthenticated multipart request is
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
    etag = STATIC_ETAGS[name]
    headers = {"etag": etag, "cache-control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=media_type, headers=headers)


async def portal_page(ctx: SurfaceContext, request: Request) -> Response:
    """Serve the portal shell to a request whose session resolved. A session that expires while the
    page is open leaves the shell asking `api/agents`, which answers 401; the page sends the member
    to the same sign-in page an unresolved arrival is redirected to. A deploy that skipped the
    frontend build fails here, naming the command, rather than at import — an unbuilt tree still
    loads the extension, so every route that holds no built asset keeps working and the fault reads
    as what it is."""
    if PORTAL_HTML is None:
        raise RuntimeError(f"portal app is not built — run `{PORTAL_BUILD}`")
    return HTMLResponse(PORTAL_HTML)


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
    a transfer. The assets carry no workspace data."""
    return _static_response(request) or Response("no such asset", status_code=404)


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
    navigation (the gateway's signed-in card posts here) and the redirected GET must already
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
    set_session_cookie(response, SESSION_COOKIE, posted.strip(), samesite="lax")
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
    binding the chat gate checks and the title the rail shows. Validated at construction — a row
    that fails to parse is a fault, never a silent "no chat"."""

    agent_id: UUID
    email: str
    title: str


def _message_text(message: Message) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(block.text for block in message.content if isinstance(block, TextBlock))


def _title_excerpt(messages: tuple[Message, ...]) -> str:
    """The opening exchange the title is written from — the first user and first assistant texts,
    each bounded, joined. Empty until an assistant message exists: a conversation whose first turn
    has not answered keeps its first-message title and its pending marker for the next tick."""
    if all(message.role != "assistant" for message in messages):
        return ""
    parts = []
    for role in ("user", "assistant"):
        text = next(
            (_message_text(message) for message in messages if message.role == role), ""
        ).strip()
        if text:
            parts.append(text[:TITLE_EXCERPT_CHARS])
    return "\n\n".join(parts)


async def summarize_chat_titles(ctx: ExtensionContext) -> None:
    """Retitle each newly opened chat from its opening exchange — the batch job behind the rail's
    summary titles. The pending marker written at open is the whole state machine: the job fires
    only in workspaces holding one, a marker whose conversation has answered is summarized and
    deleted, and one whose conversation has not yet answered waits for the next tick. The rewrite
    is a `put_if` against the row the summary was computed for, so a concurrent writer's newer row
    is never overwritten, and the marker is deleted either way — a summary is written at most
    once, and a failed compare keeps the title the concurrent writer stored."""
    pending = await ctx.store.list(CHAT_PENDING_PREFIX)
    if not pending:
        return
    if ctx.corpus is None or ctx.model is None:
        raise RuntimeError("chat titles need trajectory and model access; serve wires both")
    trajectories = {t.conversation_id: t for t in await ctx.corpus.trajectories()}
    for key, _ in pending:
        conversation_id = UUID(key.removeprefix(CHAT_PENDING_PREFIX))
        stored = await ctx.store.get(_chat_row_key(conversation_id))
        if stored is None:
            await ctx.store.delete(key)
            continue
        trajectory = trajectories.get(conversation_id)
        excerpt = "" if trajectory is None else _title_excerpt(trajectory.messages)
        if not excerpt:
            continue
        record = ChatRecord.model_validate(stored)
        summary = _chat_title(
            await ctx.model.complete(
                ModelRequest(
                    model=ctx.model.model,
                    system=TITLE_SYSTEM_PROMPT,
                    messages=(Message(role="user", content=excerpt),),
                    max_tokens=TITLE_MAX_TOKENS,
                    reasoning="off",
                )
            ),
            (),
        )
        if summary:
            await ctx.store.put_if(
                _chat_row_key(conversation_id),
                record.model_copy(update={"title": summary}).model_dump(mode="json"),
                stored,
            )
        await ctx.store.delete(key)


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
    surviving conversation, whose winner wrote its row."""
    title = _chat_title(text, paths)
    minted = uuid4()
    await store.put(
        _chat_row_key(minted),
        ChatRecord(agent_id=agent_id, email=email, title=title).model_dump(mode="json"),
    )
    conversation_id = await ctx.conversation_for(
        queue_key, conversation_audience(member_id), agent_id=agent_id, conversation_id=minted
    )
    if conversation_id != minted:
        await store.delete(_chat_row_key(minted))
        record = await _own_chat(store, agent_id, email, conversation_id)
        if record is None:
            raise RuntimeError(f"conversation {conversation_id} has no chat row")
        return conversation_id, record.title
    await store.put(f"{CHAT_PENDING_PREFIX}{minted}", {})
    return conversation_id, title


async def _own_chat(
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


def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str:
    """Where a portal message was said, as the agent carries it into anything it creates: the
    portal URL that opens this conversation, plus who asked. The portal routes on the fragment
    (`#/c/<id>`), so the link lands on the conversation rather than the shell. A deploy whose
    public base is unset or empty has no address to give, and names the client and the member
    instead."""
    if not public_base_url:
        return f"{SOURCE} ({email})"
    return f"{public_base_url.rstrip('/')}{PORTAL_PATH}#/c/{conversation_id} ({email})"


async def _audience_for(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, str, WebAudience] | Response:
    auth = await _authenticate(ctx, request)
    if isinstance(auth, Response):
        return auth
    member_id, email = auth
    return member_id, email, await web_audience(ctx, web_extension(), email)


async def agents_index(ctx: SurfaceContext, request: Request) -> Response:
    """The portal's first read: the signed-in member, the agents their web audience holds — every
    agent for a workspace admin, the main agent plus the granted non-main agents for everyone else
    — and the deploy's subagent profiles, the same roster for every member because a subagent
    belongs to none of them. Each profile rides as its summary, the boot read narrowed to what a
    list shows: its own page carries the instructions and the work it did. `agents` stays the set a
    member may open and message, so the roster never reaches the chat paths. `new_agent` is the
    create form's source — the kind's spec schema and the deploy's model ids — and is null for
    everyone but a workspace admin, the only member the `agent` kind admits a create from, so the
    portal draws that act exactly where the lane would honour it."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, email, audience = resolved
    grants = None
    if audience.admin:
        grants = await granted_emails(web_extension().store)
    return JSONResponse(
        {
            "member": {"email": email, "admin": audience.admin},
            "agents": [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "main": agent.main,
                    "model": agent.model,
                    **(
                        {"web_audience": list(grants.get(agent.id, ()))}
                        if grants is not None
                        else {}
                    ),
                }
                for agent in audience.agents
            ],
            "subagents": [subagent.summary().model_dump(mode="json") for subagent in ctx.subagents],
            "new_agent": (
                {"spec_schema": agent_create_schema(), "models": list(ctx.models)}
                if audience.admin
                else None
            ),
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
    note = f"[Attached files, saved in the workspace: {', '.join(paths)}]"
    return f"{text}\n\n{note}" if text.strip() else note


async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
        yield chunk


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


async def chat(ctx: SurfaceContext, request: Request) -> Response:
    """Admit one member message. The `conversation` query parameter continues that conversation —
    gated to the member's own chat with this agent — and the `new` sentinel opens a fresh one: the
    chat POST is the chat transport, so opening a conversation rides the first message rather than
    a separate mutation, and the response names the conversation it landed in."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows(agent_id):
        return Response("no such agent", status_code=404)
    parsed = await _parse_inbound(request)
    if isinstance(parsed, Response):
        return parsed
    text, uploads = parsed
    if not text.strip() and not uploads:
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
    if requested == NEW_CONVERSATION:
        if answer is not None:
            return Response("an answer names the conversation it was asked in", status_code=400)
        conversation_id, title = await _open_conversation(
            ctx, store, agent_id, member_id, email, f"{agent_id}/{email}/{uuid4().hex}", text, paths
        )
    else:
        try:
            conversation_id = UUID(requested)
        except ValueError:
            return Response("no such conversation", status_code=404)
        record = await _own_chat(store, agent_id, email, conversation_id)
        if record is None:
            return Response("no such conversation", status_code=404)
        title = record.title
    key = None if answer is None else f"{conversation_id}:{answer[0]}:answer:{answer[1]}"
    await _deliver_uploads(ctx, conversation_id, uploads, paths)
    admitted = await ctx.admit(
        conversation_id,
        inbound,
        context=TurnContext(
            sender=email, source=_chat_source(ctx.public_base_url, conversation_id, email)
        ),
        idempotency_key=key,
        speaker_member_id=member_id,
    )
    payload: dict[str, str | None] = {
        "turn_id": str(admitted.turn_id),
        "conversation_id": str(conversation_id),
        "title": title,
    }
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
    """One subagent run as the conversation shows it: the profile and the conversation that holds
    the whole record, the work it did, what it answered, and the runs it spawned in turn."""

    profile: str
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
    again rather than being lost beside its parent. The work is read from each run's conversation,
    bounded and concurrently, so a conversation that spawned hundreds still answers in one round
    trip; a run past the bound, and one still going, carries the conversation link that holds it."""
    spawned: list[Turn] = []
    nodes: dict[UUID, SubagentNode] = {}
    for turn in turns:
        profile = turn.subagent_profile
        if turn.parent_turn_id is None or profile is None:
            continue
        spawned.append(turn)
        nodes[turn.id] = SubagentNode(
            profile=profile,
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
) -> list[dict[str, object]]:
    """The transcript as the portal draws it. A user-role message is the member's own bubble, so
    one no member spoke never becomes one: a scheduled task's firing carries its cron envelope and a
    delivered subagent result carries the wire's element around the child's output, and both would
    otherwise read as words the member typed. The reply that answers it still renders — the member
    reads the agent coming back to them, which is what happened."""
    subagents = subagents or {}
    rendered: list[dict[str, object]] = []
    pending: list[dict[str, str]] = []
    answer = ""
    current_turn_id: str | None = None

    def flush_reply(include_subagents: bool) -> None:
        nonlocal answer, pending
        runs = (
            subagents.get(current_turn_id, [])
            if include_subagents and current_turn_id is not None
            else []
        )
        if not answer and not pending and not runs:
            return
        reply: dict[str, object] = {"role": "assistant", "text": answer}
        if pending:
            reply["events"] = pending
        if runs:
            reply["subagents"] = runs
        rendered.append(reply)
        pending = []
        answer = ""

    active = {
        block.tool_use_id
        for message in messages
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.activity
    }
    for message in messages:
        if message.role == "assistant" and not isinstance(message.content, str):
            pending.extend(
                _tool_event(block)
                for block in message.content
                if isinstance(block, ToolUseBlock) and block.id in active
            )
        text = _rendered_text(message)
        if not text:
            continue
        if message.role == "assistant":
            answer = text
            continue
        if not isinstance(message.content, str) or CONTEXT_TAG.match(message.content) is None:
            continue
        match = MESSAGE_REF.match(message.content)
        turn_id = None if match is None else match.group("ref").strip()
        if turn_id in turn_ids and turn_id != current_turn_id:
            flush_reply(True)
            current_turn_id = turn_id
        else:
            flush_reply(False)
        if turn_id in agent_origin:
            continue
        rendered.append({"role": "user", "text": text})
    flush_reply(True)
    return rendered


async def _conversation_messages(
    ctx: SurfaceContext, conversation_id: UUID
) -> tuple[list[dict[str, object]], Turn | None]:
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

    A run answers its parent by calling finish, and the payload that call carried is what the
    transcript closes with — so a conversation whose turns ran a profile states its replies as the
    answer it wrote, whole. Only such a conversation: the same words from a main agent are a reply
    it composed, and reading them as a payload would drop every field it meant to show."""
    recorded, agent_origin = await asyncio.gather(
        ctx.read_transcript(conversation_id),
        ctx.agent_origin_refs(conversation_id),
    )
    if recorded is None:
        rendered: list[dict[str, object]] = []
    else:
        turns, spawned = await asyncio.gather(
            ctx.list_turns(conversation_id),
            ctx.conversation_subagent_turns(conversation_id),
        )
        rendered = _rendered_messages(
            recorded.messages,
            await _subagent_nodes(ctx, spawned),
            frozenset(str(turn.id) for turn in turns),
            agent_origin,
        )
        if any(turn.subagent_profile is not None for turn in turns):
            for reply in rendered:
                if reply["role"] == "assistant":
                    reply["text"] = _run_answer(str(reply["text"]))
    latest = await ctx.latest_turn(conversation_id)
    detail = None if latest is None else await ctx.turn_detail(latest)
    if detail is None:
        return rendered, None
    if detail.turn.terminal is None and str(detail.turn.id) not in agent_origin:
        rendered.append({"role": "user", "text": member_message_text(detail.turn.inbound)})
    return rendered, detail.turn


async def transcript(ctx: SurfaceContext, request: Request) -> Response:
    """One conversation of the member's with this agent, as the portal renders it on load. The
    `conversation` parameter names which one, gated to the member's own like the chat POST that
    writes it. A turn still running names itself, so the page attaches to its live frames instead
    of drawing an empty conversation, and a settled one carries what it still asks of the
    member."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows(agent_id):
        return Response("no such agent", status_code=404)
    requested = request.query_params.get("conversation", "").strip()
    if not requested:
        return Response("conversation is required", status_code=400)
    try:
        conversation_id = UUID(requested)
    except ValueError:
        return Response("no such conversation", status_code=404)
    if await _own_chat(web_extension().store, agent_id, email, conversation_id) is None:
        return Response("no such conversation", status_code=404)
    rendered, turn = await _conversation_messages(ctx, conversation_id)
    if turn is None:
        return JSONResponse({"messages": rendered})
    if turn.terminal is None:
        return JSONResponse({"messages": rendered, "turn": str(turn.id)})
    return JSONResponse({"messages": rendered, **await _open_handoffs(ctx, turn.id, turn.terminal)})


async def _open_handoffs(
    ctx: SurfaceContext, turn_id: UUID, terminal: TerminalFrame
) -> dict[str, object]:
    """What the conversation's newest committed turn still asks of the member, so a reload
    re-renders the same affordances the live stream drew: an unanswered question (a later turn
    would have superseded it), credential prompts still awaiting values, and the turn's shared
    files."""
    handoffs: dict[str, object] = {}
    if terminal.question is not None:
        handoffs["question"] = {
            "turn_id": str(turn_id),
            **terminal.question.model_dump(mode="json"),
        }
    if terminal.credential_request is not None:
        prompts = await _pending_prompts(ctx, terminal.credential_request)
        if prompts is not None:
            handoffs["credentials"] = prompts
    files = await _turn_files(ctx, turn_id)
    if files:
        handoffs["files"] = files
    return handoffs


async def chats_index(ctx: SurfaceContext, request: Request) -> Response:
    """The rail: every readable conversation this member may see across the agents their web
    audience holds, newest activity first. Web conversations use the chat row this surface stores;
    another surface's conversations use their opening message, and their origin names the surface.
    A same-surface conversation without a chat row is dropped: the member's prepared-intent lane."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    store = web_extension().store
    requested = request.query_params.get("conversation", "").strip()
    if requested:
        return await _resolve_chat(ctx, store, audience, member_id, email, requested)
    rows: list[dict[str, object]] = []
    for agent in audience.agents:
        listed = await ctx.list_agent_conversations(
            agent.id, member_id, admin=False, limit=CONVERSATION_LIST_LIMIT
        )
        records = await store.get_many([_chat_row_key(entry.summary.id) for entry in listed])
        for entry in listed:
            value = records.get(_chat_row_key(entry.summary.id))
            if value is None:
                if entry.summary.surface == SURFACE_WEB or not entry.readable:
                    continue
                title = _chat_title(entry.opening_message, ())
            else:
                title = ChatRecord.model_validate(value).title
            rows.append(
                {
                    "conversation_id": str(entry.summary.id),
                    "agent_id": str(agent.id),
                    "agent_name": agent.name,
                    "title": title,
                    "origin": (
                        None
                        if entry.summary.surface == SURFACE_WEB
                        else entry.surface_label or entry.summary.surface
                    ),
                    "last_at": _iso(entry.summary.last_turn_at or entry.summary.created_at),
                }
            )
    rows.sort(key=lambda row: (str(row["last_at"]), str(row["conversation_id"])), reverse=True)
    return JSONResponse({"chats": rows})


async def _resolve_chat(
    ctx: SurfaceContext,
    store: ScopedStore,
    audience: WebAudience,
    member_id: UUID,
    email: str,
    requested: str,
) -> Response:
    """The conversation a `#/c/<id>` permalink names: a web chat returns its rail row; another
    surface returns its read-only conversation projection. The same audience gates as their
    ordinary views answer, down to the viewer's own admin flag — so a row the conversations panel
    offers an admin to disclose resolves here too, carrying `readable: false` rather than reading
    as a conversation that does not exist. A malformed or turnless id is absent."""
    try:
        named = UUID(requested)
    except ValueError:
        return JSONResponse({"chats": []})
    for agent in audience.agents:
        record = await _own_chat(store, agent.id, email, named)
        if record is None:
            continue
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
                        "title": record.title,
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
    titles = await _chat_titles(store, listed)
    return JSONResponse(
        {
            "chats": [],
            "conversation": _conversation_row(
                listed[0],
                titles.get(listed[0].summary.id),
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


def _window_param(request: Request) -> int | Response:
    """The `window_seconds` a spend read covers, or the 400 a bad value earns — non-integer,
    non-positive, or beyond the year that bounds what these views present."""
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


async def skills(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's loadable skills: its own member-authored ones and the deploy's shared
    set."""
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
    carry no item class to narrow on."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    if not ctx.memory_available:
        return JSONResponse({"available": False, "matches": []})
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
    return JSONResponse({"available": True, "matches": _memory_rows(found)})


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


async def usage(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's rolling-window spend and its agent-scoped caps — the agent's whole
    ledger across every member's turns, so it answers an admin or a member whose explicit grant
    put the agent in front of them, and the main-agent default alone opens nothing here (chat
    projects no spend to a member); the workspace-wide rollup stays the workspace usage view."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, _email, audience, agent_id = gated
    if not audience.granted(agent_id):
        return Response("no such agent", status_code=404)
    window = _window_param(request)
    if isinstance(window, Response):
        return window
    report = await ctx.agent_spend(agent_id, window)
    return JSONResponse(
        {
            "window_seconds": report.window_seconds,
            "total_micro_usd": report.total_micro_usd,
            "by_dimension": [
                {
                    "dimension": line.dimension,
                    "amount": line.amount,
                    "priced_micro_usd": line.priced_micro_usd,
                }
                for line in report.by_dimension
            ],
            "caps": [
                {
                    "window_seconds": cap.window_seconds,
                    "limit_micro_usd": cap.limit_micro_usd,
                    "on_breach": cap.on_breach,
                }
                for cap in report.caps
            ],
        }
    )


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
    `ufoctl transcript-reads`."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    listed = await ctx.list_agent_conversations(
        agent_id, member_id, admin=audience.admin, limit=CONVERSATION_LIST_LIMIT
    )
    titles = await _chat_titles(web_extension().store, listed)
    return JSONResponse(
        {
            "conversations": [
                _conversation_row(entry, titles.get(entry.summary.id)) for entry in listed
            ]
        }
    )


async def _chat_titles(store: ScopedStore, listed: Sequence[ListedConversation]) -> dict[UUID, str]:
    """The rail's own label for each listed conversation this surface opened, read in one go. A
    conversation another surface holds carries no chat row, and one this viewer may not read is
    not asked for: its label is cut from its first message, which is content the row withholds."""
    keys = {entry.summary.id: _chat_row_key(entry.summary.id) for entry in listed if entry.readable}
    records = await store.get_many(list(keys.values()))
    titles: dict[UUID, str] = {}
    for conversation_id, key in keys.items():
        value = records.get(key)
        if value is not None:
            titles[conversation_id] = ChatRecord.model_validate(value).title
    return titles


def _conversation_row(
    entry: ListedConversation, title: str | None, agent: dict[str, str] | None = None
) -> dict[str, object]:
    """One conversation as the panel lists it. `description` is what the conversation is called:
    the title this surface stored when it opened the chat — the same string the rail shows, so an
    index row and a rail row never name one conversation two ways — else that same cut taken from
    the words that opened it, which is how a conversation another surface holds gets a name at
    all. A row this viewer may not read carries neither a description nor a speaker: core withholds
    the content, and the title of a chat it did not open is that content by another route.

    `agent` names the owner of a row read across every agent, and is null for a read taken inside
    one agent's namespace, where the pane names it once instead of every row naming it again."""
    return {
        "id": str(entry.summary.id),
        "agent": agent,
        "surface": entry.summary.surface,
        "surface_label": entry.surface_label,
        "audience": entry.audience,
        "member_email": entry.summary.member_email,
        "description": (title or _chat_title(entry.opening_message, ())) if entry.readable else "",
        "speakers": [who.sender or who.email for who in entry.speakers],
        "turn_count": entry.summary.turn_count,
        "created_at": _iso(entry.summary.created_at),
        "last_turn_at": _iso(entry.summary.last_turn_at),
        "readable": entry.readable,
        "disclosable": entry.disclosable,
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
    return agent_id, conversation_id, SlotViewer(member_id, audience.admin)


async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response:
    """One conversation read rather than continued — another member's the admin acknowledged, one
    another surface holds — as the same messages the chat draws. Each reply names the children it
    spawned, and a child carries this conversation's audience, so the card opens that run through
    this conversation and the one gate here authorizes both."""
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    _agent_id, conversation_id, _viewer = authorized
    rendered, _turn = await _conversation_messages(ctx, conversation_id)
    return JSONResponse({"messages": rendered})


@dataclass(frozen=True)
class SlotViewer:
    member_id: UUID
    admin: bool


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
                    parsed = urlsplit(artifact_preview_url)
                    preview_media_type = raster_image_media_type(parsed.path)
                    if preview_media_type is not None:
                        artifact_preview = ImagePreview(
                            media_type=preview_media_type,
                            url=urlunsplit(("", "", parsed.path, parsed.query, "")),
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


async def _subagent_gate(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, WebAudience, SubagentDetail] | Response:
    """The shared entry of every subagent page read: the session's member and audience, plus the
    path's profile — 404 when this deploy registers no such profile. No audience narrows the
    profile itself, so the whole roster reads; the audience decides only whose work the page can
    show, and the work rides on agents, so every read below carries `agent_ids`."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    profile = ctx.subagent(request.path_params["subagent"])
    if profile is None:
        return Response("no such subagent", status_code=404)
    return member_id, audience, profile


def _reachable_agents(audience: WebAudience) -> frozenset[UUID]:
    """The agents this viewer's audience reaches — every agent for an admin, since the audience is
    built that way. A subagent page shows only work these agents spawned, so an out-of-audience
    agent stays not-found here as on every other portal route."""
    return frozenset(agent.id for agent in audience.agents)


async def subagent_overview(ctx: SurfaceContext, request: Request) -> Response:
    """One profile's configuration: the system prompt its children run under, the model it pins or
    inherits from the spawning agent, its round cap, and whether its answer is walled as untrusted
    content wherever a parent receives it."""
    gated = await _subagent_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, _audience, profile = gated
    return JSONResponse({"subagent": profile.model_dump(mode="json")})


async def subagent_skills(ctx: SurfaceContext, request: Request) -> Response:
    """The deploy skills this profile can load, empty when it holds no `load_skill`. A spawn also
    merges the spawning agent's member-authored skills into the child's index, and one profile is
    reached by every agent, so those are listed on each agent's own skills panel and this names the
    part every child of this profile loads."""
    gated = await _subagent_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, _audience, profile = gated
    listed = ctx.deploy_skills if profile.loads_skills else ()
    return JSONResponse(
        {
            "loads_skills": profile.loads_skills,
            "skills": [{"name": name, "description": description} for name, description in listed],
        }
    )


async def subagent_conversations(ctx: SurfaceContext, request: Request) -> Response:
    """The conversations this profile ran in, under the agents this viewer's audience reaches — the
    children of their own requests and of the workspace-shared ones, every one for an admin. A
    spawn copies the spawning conversation's audience onto the child, so whose work a member sees
    is the parent's answer."""
    gated = await _subagent_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, profile = gated
    listed = await ctx.list_subagent_conversations(
        profile.name,
        member_id,
        _reachable_agents(audience),
        admin=audience.admin,
        limit=CONVERSATION_LIST_LIMIT,
    )
    return JSONResponse({"conversations": [_subagent_run_row(run) for run in listed]})


def _subagent_run_row(run: SubagentRun) -> dict[str, object]:
    """One subagent run as both its listing row and the header its own page titles with — the same
    conversation row every other portal index draws, naming the agent that ran it because this read
    spans every agent the viewer reaches."""
    return _conversation_row(
        run.conversation, None, {"id": str(run.agent_id), "name": run.agent_name}
    )


async def subagent_conversation(ctx: SurfaceContext, request: Request) -> Response:
    """One subagent run's own transcript, as the same messages every other conversation reads back
    as, scoped to the profile that ran it and to the agents this viewer's audience reaches. A row
    the listing shows unreadable refuses here. `root` names the conversation the run was spawned
    from, the way the changes and files of a child are already read through it: a member opening
    the card in a transcript they may read reads the run behind it, and that is the one route by
    which an admin's acknowledgement reaches a child. The run itself rides the response, so a
    permalink opened cold titles its page from this one read."""
    gated = await _subagent_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, profile = gated
    root = request.query_params.get("root")
    try:
        conversation_id = UUID(request.path_params["conversation_id"])
        root_conversation_id = None if root is None else UUID(root)
    except ValueError:
        return Response("no such conversation", status_code=404)
    run = await ctx.readable_subagent_conversation(
        conversation_id,
        profile.name,
        member_id,
        _reachable_agents(audience),
        root_conversation_id=root_conversation_id,
        admin=audience.admin,
    )
    if run is None:
        return Response("no such conversation", status_code=404)
    rendered, _turn = await _conversation_messages(ctx, conversation_id)
    return JSONResponse({"run": _subagent_run_row(run), "messages": rendered})


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


async def workspace_artifacts(ctx: SurfaceContext, request: Request) -> Response:
    """One searchable, filterable keyset page of the files turns have shared with this member."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    raw_cursor = request.query_params.get("after", "").strip()
    q = request.query_params.get("q") or None
    media = request.query_params.get("media") or None
    if media is not None and media not in ARTIFACT_MEDIA_FILTERS:
        return Response("invalid artifact media filter", status_code=400)
    cursor: ListingCursor | None = None
    if raw_cursor:
        try:
            cursor = ListingCursor.decode(raw_cursor)
        except MalformedCursor:
            return Response("malformed listing cursor", status_code=400)
    page = await ctx.list_artifacts(
        member_id,
        admin=audience.admin,
        limit=ARTIFACT_LIST_LIMIT,
        cursor=cursor,
        q=q,
        media=media,
    )
    return JSONResponse(
        {
            "artifacts": [
                {
                    "filename": entry.artifact.filename,
                    "subject": entry.artifact.subject,
                    "owner_email": entry.owner_email,
                    "media_type": entry.artifact.media_type,
                    "size_bytes": entry.artifact.size_bytes,
                    "created_at": _iso(entry.created_at),
                    "url": ctx.artifact_link(entry.artifact),
                    "origin": entry.origin,
                    "conversation_id": str(entry.conversation_id),
                }
                for entry in page.rows
            ],
            "older": None if page.older is None else page.older.encode(),
            "newer": None if page.newer is None else page.newer.encode(),
        }
    )


async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response:
    """The reader's own rolling-window spend and their member-scoped caps — a member's own burn is
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
                {"label": subject.label, "priced_micro_usd": subject.priced_micro_usd}
                for subject in rollup.by_member
            ],
            "by_agent": [
                {"label": subject.label, "priced_micro_usd": subject.priced_micro_usd}
                for subject in rollup.by_agent
            ],
        }
    return JSONResponse(payload)


async def stream(ctx: SurfaceContext, request: Request) -> Response:
    """Tail one turn's live frames. Gated like every portal route: the turn must belong to the
    member AND its agent must still be in their web audience, so a revocation ends streaming
    access alongside chat and transcript — an out-of-audience agent's turn is not-found."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    try:
        turn_id = UUID(request.path_params["turn_id"])
    except ValueError:
        return Response("no such turn", status_code=404)
    owner = await ctx.turn_owner(turn_id)
    if owner is None:
        return Response("no such turn", status_code=404)
    if owner != member_id:
        return Response("turn belongs to another member", status_code=403)
    detail = await ctx.turn_detail(turn_id)
    if detail is None or not audience.allows(detail.turn.agent_id):
        return Response("no such turn", status_code=404)
    since = request.headers.get("last-event-id", "")
    return StreamingResponse(
        _events(ctx, turn_id, member_id, since), media_type="text/event-stream"
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


async def _turn_files(ctx: SurfaceContext, turn_id: UUID) -> list[dict[str, object]]:
    return [
        {
            "filename": artifact.filename,
            "subject": artifact.subject,
            "size_bytes": artifact.size_bytes,
            "url": ctx.artifact_link(artifact),
        }
        for artifact in await ctx.shared_artifacts(turn_id)
    ]


async def _events(
    ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str
) -> AsyncIterator[bytes]:
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
                if frame.frame.connect_request is not None:
                    try:
                        url = await ctx.connect_url(turn_id, member_id)
                    except ConnectRequestInvalid:
                        yield _event(
                            "connect_error",
                            {"message": "Connection request unavailable; ask me to connect again."},
                        )
                    else:
                        yield _event("connect", {"url": url})
                if frame.frame.credential_request is not None:
                    prompts = await _pending_prompts(ctx, frame.frame.credential_request)
                    if prompts is not None:
                        yield _event("credentials", prompts)
                files = await _turn_files(ctx, turn_id)
                if files:
                    yield _event("files", {"files": files})
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
            "seats": {"limit": snapshot.limit, "included": snapshot.included},
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
        "applies": kind.kind in ApplyIntent.kinds(),
    }


def _filter_value(raw: str) -> JsonValue:
    """One query-string filter value as the kind's rows carry it. A declared field holds whatever
    scalar its kind produces — `paused=true` is a boolean, `port=3000` a number — so each value is
    read as JSON and falls back to the string it already is."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


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
    searches, `order_by`/`order` sort, `cursor` continues one agent's walk, and every remaining
    query parameter is an exact filter; a field the kind never declared is the kind's own refusal,
    so the page offers only what the kind admits. A fanned-out read takes `OBJECT_FANOUT_LIMIT`
    rows from each agent and answers no cursor — the kind mints one per agent, and there is no
    single walk for the member to continue."""
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
    if cursor and not named:
        return Response("a cursor continues one agent's walk and names that agent", status_code=400)
    agents = audience.agents
    if named:
        one = _object_agent(request, audience)
        if isinstance(one, Response):
            return one
        agents = (one,)
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
    for agent in agents:
        try:
            page = await ctx.list_member_objects(
                kind.kind, agent.id, member_id, admin=audience.admin, query=query
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
    if not named:
        rows.sort(key=lambda row: _merged_rank(row, query.order_by), reverse=order == "desc")
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
        case _:
            return head + b"data: " + frame.model_dump_json().encode() + b"\n\n"


async def intents(ctx: SurfaceContext, request: Request) -> Response:
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, email, _audience, agent_id = gated
    return await submit_intent(ctx, request, agent_id, member_id, email)


async def overview(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's configuration read — its prompt, spec, bound surfaces, and the
    deploy's ceilings — answering the agent's whole web audience, so every member reads the main
    agent's overview; the web-audience grant list inside it stays the admin's."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, _email, audience, agent_id = gated
    return await agent_overview(ctx, agent_id, admin=audience.admin)


ROUTES = (
    SurfaceRoute(method="GET", path="", handler=portal_page),
    SurfaceRoute(method="POST", path="", handler=open_session),
    SurfaceRoute(method="GET", path="static/{asset:path}", handler=static_asset),
    SurfaceRoute(method="GET", path="api/agents", handler=agents_index),
    SurfaceRoute(method="GET", path="api/chats", handler=chats_index),
    SurfaceRoute(method="GET", path="api/admin", handler=admin_index),
    SurfaceRoute(method="POST", path="agents/{agent_id}/chat", handler=chat),
    SurfaceRoute(method="GET", path="agents/{agent_id}/transcript", handler=transcript),
    SurfaceRoute(method="GET", path="agents/{agent_id}/overview", handler=overview),
    SurfaceRoute(method="POST", path="agents/{agent_id}/intents", handler=intents),
    SurfaceRoute(method="GET", path="agents/{agent_id}/connections", handler=connections),
    SurfaceRoute(method="GET", path="connections", handler=connection_pool),
    SurfaceRoute(method="GET", path="github/coverage", handler=github_coverage),
    SurfaceRoute(method="GET", path="agents/{agent_id}/skills", handler=skills),
    SurfaceRoute(method="GET", path="agents/{agent_id}/skills/community", handler=community_skills),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/skills/community/{owner}/{repo}/{skill}",
        handler=community_skill,
    ),
    SurfaceRoute(method="GET", path="agents/{agent_id}/usage", handler=usage),
    SurfaceRoute(method="GET", path="agents/{agent_id}/conversations", handler=conversations),
    SurfaceRoute(method="GET", path="subagents/{subagent}/overview", handler=subagent_overview),
    SurfaceRoute(method="GET", path="subagents/{subagent}/skills", handler=subagent_skills),
    SurfaceRoute(
        method="GET", path="subagents/{subagent}/conversations", handler=subagent_conversations
    ),
    SurfaceRoute(
        method="GET",
        path="subagents/{subagent}/conversations/{conversation_id}",
        handler=subagent_conversation,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/transcript",
        handler=conversation_transcript,
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
    SurfaceRoute(method="GET", path="workspace/credentials", handler=workspace_credentials),
    SurfaceRoute(method="GET", path="workspace/memory", handler=workspace_memory),
    SurfaceRoute(method="GET", path="workspace/artifacts", handler=workspace_artifacts),
    SurfaceRoute(method="GET", path="objects/{kind}", handler=object_index),
    SurfaceRoute(method="GET", path="objects/{kind}/{name}", handler=object_detail),
    SurfaceRoute(method="GET", path="workspace/usage", handler=workspace_usage),
    SurfaceRoute(method="GET", path="turns/{turn_id}/stream", handler=stream),
    SurfaceRoute(method="POST", path="credentials", handler=fulfill_credential),
)
