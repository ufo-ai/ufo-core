"""The web portal on the core surface seam, in its live mode: the authenticated shell around the
member's agents — an agent switcher over the surface's own audience, per-agent chat with
cookie-authenticated turn admission, an SSE tail of each turn's live frames, read projections
(agents, transcripts, scheduled tasks, skills, memory search, per-agent usage, connections,
credential slots, sources), and — for workspace admins — the administration view and the spend
view.

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

import html
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from uuid import UUID

from ufo.sdk.accounting import MICRO_USD_PER_USD, SpendReport, SubjectTotal
from ufo.sdk.audience import audience_subjects, conversation_audience
from ufo.sdk.bearer import verify_token, workspace_claim
from ufo.sdk.context import SourceReader
from ufo.sdk.http import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Request,
    Response,
    StreamingResponse,
    set_session_cookie,
)
from ufo.sdk.hub import CostTick, LiveFrame, Parked, SkillLoad, Terminal, ToolCall
from ufo.sdk.models import Message, TextBlock
from ufo.sdk.seats import Seats
from ufo.sdk.surfaces import ConnectRequestInvalid, SurfaceAuth, SurfaceContext, SurfaceRoute
from ufo_ext_web.audience import WebAudience, granted_emails, web_audience, web_extension

SURFACE_WEB = "web"
SESSION_COOKIE = "ufo_session"
TOKEN_FIELD = "token"
MAX_INBOUND_CHARS = 200_000
MAX_MEMORY_QUERY_CHARS = 500
SPEND_WINDOW_DEFAULT_SECONDS = 86_400
MAX_USAGE_WINDOW_SECONDS = 31_536_000
PORTAL_PATH = "/surface/web"
PORTAL_FILE = Path(__file__).parent / "static" / "portal.html"
PORTAL_HTML = PORTAL_FILE.read_text()
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
    the portal page itself is the one pre-binding response: the same static shell serves, showing
    its token form because `api/agents` answers 401. The handler re-verifies the same bearer for
    the member email — workspace here, identity there."""
    cookie = request.cookies.get(SESSION_COOKIE, "")
    workspace = workspace_claim(cookie) if cookie else None
    if workspace is None and request.method == "POST":
        posted = (await request.form()).get(TOKEN_FIELD, "")
        if isinstance(posted, str) and posted.strip():
            workspace = workspace_claim(posted.strip())
    if (
        workspace is None
        and request.method == "GET"
        and request.url.path.rstrip("/") == PORTAL_PATH
    ):
        return HTMLResponse(PORTAL_HTML)
    return workspace


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


async def portal_page(ctx: SurfaceContext, request: Request) -> Response:
    """Serve the portal shell. The page itself decides between its token form (no session yet) and
    the signed-in shell by asking `api/agents` — the server serves one page either way."""
    return HTMLResponse(PORTAL_HTML)


async def open_session(ctx: SurfaceContext, request: Request) -> Response:
    """Open a session: land the POSTed bearer as the session cookie and redirect into the portal.
    The token crosses only in the form body — never a URL. The identify resolver has verified this
    form token whenever it is what scoped the request; behind a cookie that still resolves the form
    goes unread and the new bearer lands unverified, which changes nothing, because the cookie is
    verified again on every request that follows (`resolve_workspace`, then `_authenticate`) and one
    that verifies against nothing authenticates nobody. The shape check is transport, not
    authentication: a pasted value outside the bearer alphabet cannot ride a Set-Cookie header
    (control characters and non-latin-1 raise inside the cookie writer), so it answers 400 before a
    header is built. The cookie is `lax`, not `strict`, because arrival IS a cross-site navigation
    (the gateway's signed-in card posts here) and the redirected GET must already carry it."""
    posted = (await request.form()).get(TOKEN_FIELD, "")
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


def _conversation_key(agent_id: UUID, email: str) -> str:
    return f"{agent_id}/{email}"


async def _audience_for(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, str, WebAudience] | Response:
    auth = await _authenticate(ctx, request)
    if auth is None:
        return Response("missing or unknown session cookie", status_code=401)
    member_id, email = auth
    return member_id, email, await web_audience(ctx, web_extension(), email)


async def agents_index(ctx: SurfaceContext, request: Request) -> Response:
    """The portal's first read: the signed-in member and the agents their web audience holds —
    every agent for a workspace admin, exactly the granted set for everyone else."""
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
        }
    )


async def chat(ctx: SurfaceContext, request: Request) -> Response:
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows(agent_id):
        return Response("no such agent", status_code=404)
    inbound = (await request.body()).decode()
    if not inbound.strip():
        return Response("empty message", status_code=400)
    if len(inbound) > MAX_INBOUND_CHARS:
        return Response(f"message exceeds {MAX_INBOUND_CHARS} characters", status_code=413)
    conversation_id = await ctx.conversation_for(
        _conversation_key(agent_id, email), conversation_audience(member_id), agent_id=agent_id
    )
    admitted = await ctx.admit(conversation_id, inbound, speaker_member_id=member_id)
    return JSONResponse({"turn_id": str(admitted.turn_id)})


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
    """The member's exchange with one agent as the portal renders it on load: text only, the
    engine's `<context>` framing stripped, tool traffic elided — a projection of the durable
    transcript, never a second store."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows(agent_id):
        return Response("no such agent", status_code=404)
    conversation_id = await ctx.find_conversation(_conversation_key(agent_id, email))
    if conversation_id is None:
        return JSONResponse({"messages": []})
    recorded = await ctx.read_transcript(conversation_id)
    if recorded is None:
        return JSONResponse({"messages": []})
    rendered = [
        {"role": message.role, "text": text}
        for message in recorded.messages
        if (text := _rendered_text(message))
    ]
    return JSONResponse({"messages": rendered})


async def _panel_gate(
    ctx: SurfaceContext, request: Request
) -> tuple[UUID, WebAudience, UUID] | Response:
    """The shared entry of every per-agent panel read: the session's member and audience, plus the
    path's agent — 404 when the agent is outside the viewer's web audience, like every portal
    route."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    member_id, _email, audience = resolved
    agent_id = _agent_param(request)
    if agent_id is None or not audience.allows(agent_id):
        return Response("no such agent", status_code=404)
    return member_id, audience, agent_id


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
    anyone else sees none of it."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, agent_id = gated
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
                    "next_run_at": _iso(task.next_run_at),
                    "last_run_at": _iso(task.last_run_at),
                    "expires_at": _iso(task.expires_at),
                }
                for task in listed
            ]
        }
    )


async def skills(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's loadable skills: its own member-authored ones and the deploy's shared
    set."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, _audience, agent_id = gated
    listed = await ctx.agent_skills(agent_id)
    return JSONResponse(
        {
            "skills": [
                {"name": skill.name, "description": skill.description, "origin": skill.origin}
                for skill in listed
            ]
        }
    )


async def memory(ctx: SurfaceContext, request: Request) -> Response:
    """Search the memory the viewer may read — their own subject plus shared, the same atoms
    recall uses, so another member's private items can never match. The selected agent gates
    source-derived hits: the reader carries the agent whose source grants fence page results,
    exactly as a turn's tools search, so the panel and the agent answer identically; member-written
    items stay subject-scoped."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, _audience, agent_id = gated
    query = request.query_params.get("q", "").strip()
    if not query or not ctx.memory_available:
        return JSONResponse({"available": ctx.memory_available, "matches": []})
    reader = SourceReader(
        agent_id=agent_id,
        requesting_member_id=member_id,
        subjects=audience_subjects(conversation_audience(member_id)),
    )
    found = await ctx.search_memory(reader, (query[:MAX_MEMORY_QUERY_CHARS],))
    return JSONResponse(
        {
            "available": True,
            "matches": [
                {
                    "kind": match.kind,
                    "text": match.text,
                    "ref": None if match.ref is None else f"{match.ref.kind}/{match.ref.name}",
                    "created_at": _iso(match.created_at),
                }
                for match in found
            ],
        }
    )


async def usage(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's rolling-window spend and its agent-scoped caps — visible to every
    member of the agent's audience; the workspace-wide rollup stays the admin's spend page."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, audience, agent_id = gated
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
            "workspace_spend": audience.admin,
        }
    )


async def connections(ctx: SurfaceContext, request: Request) -> Response:
    """The selected agent's connector accounts this member may see — their own private grants plus
    agent-shared ones, every edge for a workspace admin. The member gate is the query's, the wall
    is the agent id, and the panel only renders what the read returned."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, agent_id = gated
    listed = await ctx.list_agent_connections(agent_id, member_id, admin=audience.admin)
    return JSONResponse({"connections": [entry.model_dump(mode="json") for entry in listed]})


async def credentials(ctx: SurfaceContext, request: Request) -> Response:
    """Member-fillable declared BYOK slots and their fill state — never a value, and never the
    `member_filled=False` seals the `credential` object kind still lists (deploy machinery, not a
    member's key). The route rides the agent path only for the panel's navigation, and the
    audience gate keeps an out-of-audience agent not-found here too."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    _member_id, _audience, _agent_id = gated
    listed = await ctx.list_credential_slots()
    return JSONResponse({"slots": [entry.model_dump(mode="json") for entry in listed]})


async def sources(ctx: SurfaceContext, request: Request) -> Response:
    """The live source bindings this member may see — their own registrations plus shared ones,
    all of them for a workspace admin. A member-subject source's indexed pages stay gated to that
    member; the panel shows the subject so that stays legible."""
    gated = await _panel_gate(ctx, request)
    if isinstance(gated, Response):
        return gated
    member_id, audience, _agent_id = gated
    listed = await ctx.list_sources(member_id, admin=audience.admin)
    return JSONResponse({"sources": [entry.model_dump(mode="json") for entry in listed]})


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


async def _events(
    ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str
) -> AsyncIterator[bytes]:
    async for cursor, frame in ctx.tail(turn_id, since):
        if isinstance(frame, Terminal) and frame.frame.connect_request is not None:
            try:
                url = await ctx.connect_url(turn_id, member_id)
            except ConnectRequestInvalid:
                yield (
                    b"event: connect_error\ndata: "
                    + json.dumps(
                        {"message": "Connection request unavailable; ask me to connect again."}
                    ).encode()
                    + b"\n\n"
                )
            else:
                yield b"event: connect\ndata: " + json.dumps({"url": url}).encode() + b"\n\n"
        yield _sse(cursor, frame)


async def admin_index(ctx: SurfaceContext, request: Request) -> Response:
    """One of the two workspace-shaped reads (this view and the spend rollup — spec.md names
    both): every agent with its policy, surface installations, and web-audience grants, plus
    members and seat state. The workspace's shape answers a workspace admin only and is not-found
    for everyone else. Reads only; every mutation stays a chat act."""
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
                {"email": entry.email, "admin": entry.admin, "seated": entry.seated}
                for entry in snapshot.members
            ],
            "seats": {"limit": snapshot.limit, "included": snapshot.included},
        }
    )


async def spend(ctx: SurfaceContext, request: Request) -> Response:
    """Render the workspace spend rollup over a window — the same sums `ufoctl spend` prints, for a
    workspace admin. The rollup is the workspace's financial state, not a member's own: it names
    every agent and every member's burn, so a non-admin is not-found here for the same reason an
    out-of-audience agent is not-found on every other portal route, and the footer offers the link
    only to an admin."""
    resolved = await _audience_for(ctx, request)
    if isinstance(resolved, Response):
        return resolved
    _member_id, _email, audience = resolved
    if not audience.admin:
        return Response("no such page", status_code=404)
    window = _window_param(request)
    if isinstance(window, Response):
        return window
    report = await ctx.spend_rollup(window)
    return HTMLResponse(_spend_page(report))


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


def _money(micro_usd: int) -> str:
    return f"${micro_usd / MICRO_USD_PER_USD:,.6f}"


def _subject_rows(subjects: tuple[SubjectTotal, ...]) -> str:
    body = "".join(
        f"<tr><td>{html.escape(s.label)}</td><td>{_money(s.priced_micro_usd)}</td></tr>"
        for s in subjects
    )
    return body or "<tr><td colspan=2>none</td></tr>"


def _spend_page(report: SpendReport) -> str:
    dimensions = "".join(
        f"<tr><td>{html.escape(d.dimension)}</td><td>{d.amount:,}</td>"
        f"<td>{_money(d.priced_micro_usd)}</td></tr>"
        for d in report.by_dimension
    )
    return (
        "<!doctype html><meta charset=utf-8><title>ufo spend</title>"
        "<style>body{font:15px/1.5 system-ui,sans-serif;margin:24px;max-width:720px}"
        "table{border-collapse:collapse;width:100%;margin:8px 0 24px}"
        "th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #8884}"
        "td+td,th+th{text-align:right}h1{font-size:20px}h2{font-size:15px;opacity:.7}</style>"
        f"<h1>Spend · last {report.window_seconds / 3600:g}h · "
        f"{_money(report.total_micro_usd)}</h1>"
        "<h2>by dimension</h2><table><tr><th>dimension</th><th>units</th><th>cost</th></tr>"
        f"{dimensions or '<tr><td colspan=3>no spend in window</td></tr>'}</table>"
        "<h2>by member</h2><table><tr><th>member</th><th>cost</th></tr>"
        f"{_subject_rows(report.by_member)}</table>"
        "<h2>by agent</h2><table><tr><th>agent</th><th>cost</th></tr>"
        f"{_subject_rows(report.by_agent)}</table>"
    )


ROUTES = (
    SurfaceRoute(method="GET", path="", handler=portal_page),
    SurfaceRoute(method="POST", path="", handler=open_session),
    SurfaceRoute(method="GET", path="api/agents", handler=agents_index),
    SurfaceRoute(method="GET", path="api/admin", handler=admin_index),
    SurfaceRoute(method="POST", path="agents/{agent_id}/chat", handler=chat),
    SurfaceRoute(method="GET", path="agents/{agent_id}/transcript", handler=transcript),
    SurfaceRoute(method="GET", path="agents/{agent_id}/tasks", handler=tasks),
    SurfaceRoute(method="GET", path="agents/{agent_id}/connections", handler=connections),
    SurfaceRoute(method="GET", path="agents/{agent_id}/credentials", handler=credentials),
    SurfaceRoute(method="GET", path="agents/{agent_id}/sources", handler=sources),
    SurfaceRoute(method="GET", path="agents/{agent_id}/skills", handler=skills),
    SurfaceRoute(method="GET", path="agents/{agent_id}/memory", handler=memory),
    SurfaceRoute(method="GET", path="agents/{agent_id}/usage", handler=usage),
    SurfaceRoute(method="GET", path="turns/{turn_id}/stream", handler=stream),
    SurfaceRoute(method="GET", path="spend", handler=spend),
)
