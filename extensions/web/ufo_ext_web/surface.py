"""The web portal on the core surface seam, in its live mode: the authenticated shell around the
member's agents — the page and the one POST that opens its session, per-agent chat with
cookie-authenticated turn admission (each member holds any number of conversations per agent,
opened by the first message and listed for the rail) and an SSE tail of each turn's live frames,
read projections (the agent index, conversation transcripts, overviews, scheduled tasks, skills,
per-agent usage, and connections) beside the workspace-level views every member holds — sources,
credential slots, memory (latest first, searched across every reachable agent), shared artifacts,
hosted sites, and usage (their own window, plus the workspace rollup for an admin) — plus the
administration view for a workspace admin, and prepared intents, the panels' one mutation path.

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
from collections.abc import AsyncIterator
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel

from ufo.sdk.audience import audience_subjects, conversation_audience
from ufo.sdk.bearer import verify_token, workspace_claim
from ufo.sdk.context import ScopedStore, SourceReader
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
from ufo.sdk.hub import CostTick, LiveFrame, Parked, SkillLoad, Terminal, ToolCall
from ufo.sdk.listings import ListingCursor, MalformedCursor
from ufo.sdk.memory import MemoryMatch
from ufo.sdk.models import Message, TextBlock
from ufo.sdk.seats import Seats
from ufo.sdk.surfaces import (
    ConnectRequestInvalid,
    CredentialRequest,
    CredentialRequestInvalid,
    SubagentDetail,
    SurfaceAuth,
    SurfaceContext,
    SurfaceRoute,
    Turn,
    TurnContext,
    member_message_text,
)
from ufo_ext_web.audience import WebAudience, granted_emails, web_audience, web_extension
from ufo_ext_web.panels import agent_overview, reasoning_levels, submit_intent

SURFACE_WEB = "web"
SOURCE = "ufo web"
SESSION_COOKIE = "ufo_session"
TOKEN_FIELD = "token"
MAX_INBOUND_CHARS = 200_000
MAX_INBOUND_BYTES = 4 * MAX_INBOUND_CHARS
MAX_REQUEST_BYTES = 25 * 1024 * 1024
MAX_FORM_BYTES = 64 * 1024
MAX_SECRET_BYTES = 4_096
UPLOAD_CHUNK_BYTES = 65_536
MAX_NAME_CHARS = 80
UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
WEB_INBOX_DIR = "web-inbox"
ANSWER_TURN_HEADER = "x-ufo-answer-turn"
ANSWER_QUESTION_HEADER = "x-ufo-answer-question"
MAX_MEMORY_QUERY_CHARS = 500
MEMORY_RECENT_LIMIT = 100
MEMORY_RESULT_LIMIT = 100
ARTIFACT_LIST_LIMIT = 100
CONVERSATION_LIST_LIMIT = 100
CHAT_STORE_PREFIX = "chat/"
NEW_CONVERSATION = "new"
MAX_CHAT_TITLE_CHARS = 60
SITE_KIND = "site"
SPEND_WINDOW_DEFAULT_SECONDS = 86_400
MAX_USAGE_WINDOW_SECONDS = 31_536_000
PORTAL_PATH = "/surface/web"
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
    declared media type is served: these answer before authentication, so a build that starts
    emitting source maps publishes nothing until someone declares them here. Nothing raises —
    the page names the assets it needs, and the origin gate fails when one of those is unserved,
    which is the layer that can tell a missing asset from a file the build merely left behind."""
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
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9._-]+")


async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None:
    """The `SurfaceSpec.identify` the shared fleet calls to scope a request before its handler runs:
    the workspace the bearer claims, or None to reject. The bearer rides the `ufo_session` cookie,
    or — for the one POST that opens a session — the form body (`open_session` binds it into the
    cookie for the requests that follow); never a query parameter, so it stays out of URLs, access
    logs, and browser history. Each fallback keys on the previous credential failing to RESOLVE,
    not merely being absent, so a member whose cookie outlived its bearer's expiry recovers by
    posting a fresh token instead of being locked behind the stale cookie. An unresolved GET of
    the portal page serves the same static shell, showing its token form because `api/agents`
    answers 401. Only a urlencoded body is read for the token — the type every token form posts —
    so an unauthenticated multipart request is rejected without its parse ever running. The
    handler re-verifies the same bearer for the member email — workspace here, identity there."""
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
    if workspace is None and request.method == "GET":
        if request.url.path.rstrip("/") == PORTAL_PATH:
            return _portal_response()
        asset = _static_response(request)
        if asset is not None:
            return asset
    return workspace


def _portal_response() -> Response:
    """The built page. A deploy that skipped the frontend build fails here, naming the command,
    rather than at import — an unbuilt tree still loads the extension, so every route that holds
    no built asset keeps working and the fault reads as what it is."""
    if PORTAL_HTML is None:
        raise RuntimeError(f"portal app is not built — run `{PORTAL_BUILD}`")
    return HTMLResponse(PORTAL_HTML)


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
    """Serve the portal shell. The page itself decides between its token form (no session yet) and
    the signed-in shell by asking `api/agents` — the server serves one page either way."""
    return _portal_response()


async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | None:
    """The member and email a request's session cookie authenticates, or None when the cookie is
    missing or its bearer names no email for this workspace. The email is the web
    `surface_identity`, linked to (or created as) a member on first contact."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    email = verify_token(token, ctx.workspace_id)
    if email is None:
        return None
    member_id = await ctx.linked_member(email) or await ctx.link_member(email, email)
    return None if member_id is None else (member_id, email)


async def static_asset(ctx: SurfaceContext, request: Request) -> Response:
    """Serve a portal stylesheet or module to a request whose session resolved. An unresolved
    request never reaches here — `resolve_workspace` answers it with the same bytes, because the
    token card styles itself before any session exists. The assets carry no workspace data."""
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
    if auth is None:
        return Response("missing or unknown session cookie", status_code=401)
    member_id, email = auth
    return member_id, email, await web_audience(ctx, web_extension(), email)


async def agents_index(ctx: SurfaceContext, request: Request) -> Response:
    """The portal's first read: the signed-in member, the agents their web audience holds — every
    agent for a workspace admin, the main agent plus the granted non-main agents for everyone else
    — and the deploy's subagent profiles, the same roster for every member because a subagent
    belongs to none of them. Each profile rides as its summary, the boot read narrowed to what a
    list shows: its own page carries the instructions and the work it did. `agents` stays the set a
    member may open and message, so the roster never reaches the chat paths."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, email, audience = resolved
    return JSONResponse(
        {
            "member": {"email": email, "admin": audience.admin},
            "agents": [
                {"id": str(agent.id), "name": agent.name, "main": agent.main, "model": agent.model}
                for agent in audience.agents
            ],
            "subagents": [subagent.summary().model_dump(mode="json") for subagent in ctx.subagents],
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
        f"{WEB_INBOX_DIR}/{_inbox_name(upload.filename or 'file', used)}" for upload in uploads
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


def _inbox_name(raw: str, used: set[str]) -> str:
    """A safe workspace leaf for a client-chosen filename: path components dropped, everything
    outside a conservative charset collapsed, length capped, dots-only and empty names falling
    back — the browser's content-disposition is untrusted input, never a path."""
    leaf = raw.replace("\\", "/").rsplit("/", 1)[-1]
    leaf = UNSAFE_NAME_CHARS.sub("-", leaf)[:MAX_NAME_CHARS].strip(".")
    leaf = leaf or "file"
    name = leaf
    stem, dot, suffix = leaf.partition(".")
    index = 1
    while name in used:
        name = f"{stem}-{index}{dot}{suffix}"
        index += 1
    used.add(name)
    return name


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
    return rendered.strip()


async def transcript(ctx: SurfaceContext, request: Request) -> Response:
    """One conversation of the member's with this agent, as the portal renders it on load: text
    only, the engine's `<context>` framing stripped, tool traffic elided — a projection of the
    durable transcript, never a second store. The `conversation` parameter names which one, gated
    to the member's own like the chat POST that writes it."""
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
    recorded = await ctx.read_transcript(conversation_id)
    rendered = (
        []
        if recorded is None
        else [
            {"role": message.role, "text": text}
            for message in recorded.messages
            if (text := _rendered_text(message))
        ]
    )
    payload: dict[str, object] = {"messages": rendered}
    latest = await ctx.latest_turn(conversation_id)
    if latest is not None:
        payload.update(await _open_handoffs(ctx, latest))
    return JSONResponse(payload)


async def _open_handoffs(ctx: SurfaceContext, turn_id: UUID) -> dict[str, object]:
    """What the conversation's newest turn still asks of the member, so a reload re-renders the
    same affordances the live stream drew: an unanswered question (a later turn would have
    superseded it), credential prompts still awaiting values, and the turn's shared files."""
    detail = await ctx.turn_detail(turn_id)
    if detail is None or detail.turn.terminal is None:
        return {}
    terminal = detail.turn.terminal
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
    """The rail: every conversation this member opened here, across the agents their web audience
    holds, newest activity first — the surface narrowing runs inside the core read, under its
    bound, so another surface's newer traffic never displaces a rail row. Titles come from the
    chat rows this surface writes before each conversation exists — a crash between the two
    leaves an inert row, never a rowless conversation. One same-surface conversation carries no
    chat row and is dropped after taking a slot under the bound: the member's prepared-intent
    lane."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    store = web_extension().store
    requested = request.query_params.get("conversation", "").strip()
    if requested:
        return await _resolve_chat(ctx, store, audience, email, requested)
    rows: list[dict[str, object]] = []
    for agent in audience.agents:
        listed = await ctx.list_agent_conversations(
            agent.id, member_id, admin=False, limit=CONVERSATION_LIST_LIMIT, surface=SURFACE_WEB
        )
        records = await store.get_many([_chat_row_key(entry.summary.id) for entry in listed])
        for entry in listed:
            value = records.get(_chat_row_key(entry.summary.id))
            if value is None:
                continue
            record = ChatRecord.model_validate(value)
            rows.append(
                {
                    "conversation_id": str(entry.summary.id),
                    "agent_id": str(agent.id),
                    "agent_name": agent.name,
                    "title": record.title,
                    "last_at": _iso(entry.summary.last_turn_at or entry.summary.created_at),
                }
            )
    rows.sort(key=lambda row: (str(row["last_at"]), str(row["conversation_id"])), reverse=True)
    return JSONResponse({"chats": rows})


async def _resolve_chat(
    ctx: SurfaceContext,
    store: ScopedStore,
    audience: WebAudience,
    email: str,
    requested: str,
) -> Response:
    """One rail row by conversation id — how a `#/c/<id>` link resolves when the conversation's
    activity has fallen past the rail's bound. The same ownership gate as the chat POST answers;
    anything else, a malformed id included, is an empty list, and a turnless conversation stays
    as absent as the rail's own read keeps it."""
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
    return JSONResponse({"chats": []})


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


async def tasks(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's recurring tasks, shaped for the viewer in the core read: creators see
    their tasks whole, an admin sees every task's management metadata with private content elided,
    anyone else sees none of it. `spec_schema` is the scheduled_task kind's own spec schema — the
    panel's create and edit forms render their fields from it, never a parallel description."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    listed = await ctx.list_agent_tasks(agent_id, member_id, audience.admin)
    return JSONResponse(
        {
            "tasks": [
                {
                    "name": task.name,
                    "schedule": task.schedule,
                    "prompt": task.prompt,
                    "description": task.description,
                    "created_by": task.created_by_email,
                    "paused": task.paused,
                    "next_run_at": _iso(task.next_run_at),
                    "last_run_at": _iso(task.last_run_at),
                    "expires_at": _iso(task.expires_at),
                }
                for task in listed
            ],
            "spec_schema": ctx.object_spec_schema("scheduled_task"),
        }
    )


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
                {"name": skill.name, "description": skill.description, "origin": skill.origin}
                for skill in listed
            ]
        }
    )


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
    return JSONResponse(
        {
            "conversations": [
                {
                    "id": str(entry.summary.id),
                    "surface": entry.summary.surface,
                    "member_email": entry.summary.member_email,
                    "turn_count": entry.summary.turn_count,
                    "created_at": _iso(entry.summary.created_at),
                    "last_turn_at": _iso(entry.summary.last_turn_at),
                    "readable": entry.readable,
                    "disclosable": entry.disclosable,
                }
                for entry in listed
            ]
        }
    )


async def _readable_conversation(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, UUID] | Response:
    """The agent and conversation a content read is authorized for, or the 404 every unreadable
    case answers: an agent outside the audience, a malformed id, another agent's conversation, a
    room's, and another member's private one until an admin records a disclosure against it. One
    gate, so the turn, subagent, and file reads below cannot disagree."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _email, audience, agent_id = gated
    try:
        conversation_id = UUID(request.path_params["conversation_id"])
    except ValueError:
        return Response("no such conversation", status_code=404)
    if not await ctx.readable_conversation(
        conversation_id, agent_id, member_id, admin=audience.admin
    ):
        return Response("no such conversation", status_code=404)
    return agent_id, conversation_id


def _turn_row(turn: Turn) -> dict[str, object]:
    """The transcript view renders `inbound` as the member's own bubble, so it carries the words the
    member wrote and never the elements a surface named around them."""
    return {
        "id": str(turn.id),
        "seq": turn.seq,
        "status": turn.status,
        "inbound": member_message_text(turn.inbound),
        "created_at": _iso(turn.created_at),
        "parent_turn_id": None if turn.parent_turn_id is None else str(turn.parent_turn_id),
        "subagent_profile": turn.subagent_profile,
        "outcome": None if turn.terminal is None else turn.terminal.text,
        "error_class": None if turn.terminal is None else turn.terminal.error_class,
    }


async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response:
    """One conversation's turns as the portal's transcript view renders them, and beneath them the
    turns each spawned — a subagent runs in its own conversation carrying this one's audience, so
    the same gate authorizes both and the page nests by `parent_turn_id`."""
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    _agent_id, conversation_id = authorized
    turns = await ctx.list_turns(conversation_id)
    spawned = await ctx.conversation_subagent_turns(conversation_id)
    return JSONResponse(
        {
            "turns": [_turn_row(turn) for turn in turns],
            "subagent_turns": [_turn_row(turn) for turn in spawned],
        }
    )


async def conversation_files(ctx: SurfaceContext, request: Request) -> Response:
    """The conversation's live workspace files — the sandbox's own state, empty for a conversation
    whose sandbox is gone."""
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    _agent_id, conversation_id = authorized
    listed = await ctx.list_workspace_files(conversation_id)
    return JSONResponse(
        {
            "files": [
                {
                    "path": entry.path,
                    "size_bytes": entry.size_bytes,
                    "modified_at": _iso(entry.modified_at),
                }
                for entry in listed
            ]
        }
    )


async def conversation_file(ctx: SurfaceContext, request: Request) -> Response:
    """One workspace file's bytes, streamed from the live sandbox under the same gate that listed
    it — the path is workspace-scoped in the session, so it escapes neither the workspace nor the
    container, and a member reads exactly what that conversation's agent wrote."""
    authorized = await _readable_conversation(ctx, request)
    if isinstance(authorized, Response):
        return authorized
    _agent_id, conversation_id = authorized
    try:
        stream = await ctx.read_workspace_file(conversation_id, request.path_params["path"])
    except ValueError:
        return Response("no such file", status_code=404)
    if stream is None:
        return Response("no such file", status_code=404)
    return StreamingResponse(stream, media_type="application/octet-stream")


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
    return JSONResponse(
        {
            "conversations": [
                {
                    "id": str(run.id),
                    "agent_name": run.agent_name,
                    "member_email": run.member_email,
                    "turn_count": run.turn_count,
                    "last_turn_at": _iso(run.last_turn_at),
                    "readable": run.readable,
                }
                for run in listed
            ]
        }
    )


async def subagent_conversation_turns(ctx: SurfaceContext, request: Request) -> Response:
    """One subagent conversation's turns, and beneath them the turns it spawned in turn — the same
    transcript the spawning conversation nests, read here scoped to the profile that ran it and to
    the agents this viewer's audience reaches. A row the listing shows unreadable refuses here."""
    gated = await _subagent_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, profile = gated
    try:
        conversation_id = UUID(request.path_params["conversation_id"])
    except ValueError:
        return Response("no such conversation", status_code=404)
    agent_id = await ctx.readable_subagent_conversation(
        conversation_id, profile.name, member_id, _reachable_agents(audience)
    )
    if agent_id is None:
        return Response("no such conversation", status_code=404)
    turns = await ctx.list_turns(conversation_id)
    spawned = await ctx.conversation_subagent_turns(conversation_id)
    return JSONResponse(
        {
            "turns": [_turn_row(turn) for turn in turns],
            "subagent_turns": [_turn_row(turn) for turn in spawned],
        }
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
    member may add another, and the domain names what an added address must match — both read
    back from the verb's own authorities (the same `member.is_admin` row its gate checks, and the
    one workspace-domain derivation it admits by), never a second copy; the verb refuses
    regardless."""
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
            "domain": await ctx.workspace_domain(),
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
    """One keyset page of the files turns have shared with this member — their own conversations'
    artifacts, every conversation's for an admin — each with the same signed TTL download link a
    delivery would carry, or none when artifact delivery is unconfigured. A cursor this surface
    never minted is the client's error, not a silent walk back to the newest page."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    raw_cursor = request.query_params.get("after", "").strip()
    cursor: ListingCursor | None = None
    if raw_cursor:
        try:
            cursor = ListingCursor.decode(raw_cursor)
        except MalformedCursor:
            return Response("malformed listing cursor", status_code=400)
    page = await ctx.list_artifacts(
        member_id, admin=audience.admin, limit=ARTIFACT_LIST_LIMIT, cursor=cursor
    )
    return JSONResponse(
        {
            "artifacts": [
                {
                    "filename": entry.artifact.filename,
                    "subject": entry.artifact.subject,
                    "media_type": entry.artifact.media_type,
                    "size_bytes": entry.artifact.size_bytes,
                    "created_at": _iso(entry.created_at),
                    "url": ctx.artifact_link(entry.artifact),
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


async def workspace_sites(ctx: SurfaceContext, request: Request) -> Response:
    """The hosted sites this member may see, answered through the site kind's own visibility gate
    — shared sites plus their own private ones, every site for an admin — or `available: false`
    when the deploy installs no sites extension."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    page = await ctx.list_member_objects(SITE_KIND, member_id, admin=audience.admin)
    if page is None:
        return JSONResponse({"available": False, "sites": []})
    return JSONResponse(
        {
            "available": True,
            "sites": [{"name": row.name, "summary": row.summary} for row in page.rows],
        }
    )


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
    async for cursor, frame in ctx.tail(turn_id, since):
        if isinstance(frame, Terminal):
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
    if auth is None:
        return Response("missing or unknown session cookie", status_code=401)
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
            "models": list(ctx.models),
            "reasoning_levels": reasoning_levels(),
            "seats": {"limit": snapshot.limit, "included": snapshot.included},
            "caps": [entry.model_dump(mode="json") for entry in await ctx.spend_caps()],
            "deploy": {
                "sandbox_internet": ctx.deploy_sandbox_internet,
                "extensions": [entry.model_dump(mode="json") for entry in ctx.deploy_extensions],
            },
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
    SurfaceRoute(method="GET", path="agents/{agent_id}/tasks", handler=tasks),
    SurfaceRoute(method="GET", path="agents/{agent_id}/connections", handler=connections),
    SurfaceRoute(method="GET", path="agents/{agent_id}/skills", handler=skills),
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
        handler=subagent_conversation_turns,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/turns",
        handler=conversation_turns,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/files",
        handler=conversation_files,
    ),
    SurfaceRoute(
        method="GET",
        path="agents/{agent_id}/conversations/{conversation_id}/files/{path:path}",
        handler=conversation_file,
    ),
    SurfaceRoute(method="GET", path="workspace/team", handler=workspace_team),
    SurfaceRoute(method="GET", path="workspace/sources", handler=workspace_sources),
    SurfaceRoute(method="GET", path="workspace/credentials", handler=workspace_credentials),
    SurfaceRoute(method="GET", path="workspace/memory", handler=workspace_memory),
    SurfaceRoute(method="GET", path="workspace/artifacts", handler=workspace_artifacts),
    SurfaceRoute(method="GET", path="workspace/sites", handler=workspace_sites),
    SurfaceRoute(method="GET", path="workspace/usage", handler=workspace_usage),
    SurfaceRoute(method="GET", path="turns/{turn_id}/stream", handler=stream),
    SurfaceRoute(method="POST", path="credentials", handler=fulfill_credential),
)
