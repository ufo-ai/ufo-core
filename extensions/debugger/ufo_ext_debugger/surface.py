"""The admin session debugger: a read-only operator surface over one workspace's sessions.

Authorization is entirely in `resolve_admin_workspace`, the surface's shared-fleet `identify`: the
request's gateway bearer must verify against this deploy's `UFO_TOKEN_SECRET` AND carry an email in
`UFO_ADMIN_EMAIL_DOMAIN`, and only then does `?ws=` pick the workspace the request is scoped to — a
raw workspace UUID, or a customer domain (the shared fleet derives a workspace id as
`uuid5(NAMESPACE_DNS, domain)`, so the domain IS the address). Whatever the resolver returns becomes
the request's RLS binding and `SurfaceContext.workspace_id`, so every read below is
workspace-scoped by construction; without `?ws=` the bearer's own workspace claim is the scope.

The page is a built React app served whole from `static/index.html`; everything it renders comes
from the JSON routes under `api/`, all thin dumps of the `SurfaceContext` read views plus an SSE
tail of a live turn (the web surface's delivery pattern, rendered raw for debugging). A session
starts with `POST /surface/debug` carrying the bearer in its form body — never in a URL, so no
access log ever records it — which lands it as the httponly session cookie and redirects to the
app."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid5

from ufo.sdk.bearer import verified_claims
from ufo.sdk.http import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Request,
    Response,
    StreamingResponse,
)
from ufo.sdk.hub import CostTick, LiveFrame, Parked, SkillLoad, Terminal, TextDelta, ToolCall
from ufo.sdk.surfaces import SurfaceAuth, SurfaceContext, SurfaceRoute

SURFACE_DEBUG = "debug"
DEBUG_COOKIE = "ufo_debug"
TOKEN_FIELD = "token"
ADMIN_EMAIL_DOMAIN_ENV = "UFO_ADMIN_EMAIL_DOMAIN"
SLACK_SURFACE = "slack"
SLACK_TEAM_PREFIX = "team:"
APP_FILE = Path(__file__).parent / "static" / "index.html"
APP_HTML = APP_FILE.read_text() if APP_FILE.is_file() else None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} must be set for the {SURFACE_DEBUG!r} surface")
    return value


def _email_domain(email: str) -> str:
    local, _, domain = email.strip().lower().rpartition("@")
    return domain if local and domain else ""


async def _bearer(request: Request) -> str:
    """The request's bearer: the Authorization header, the session cookie, or — for the one POST
    that opens a session — the form body. Never a query parameter, so the long-lived credential
    stays out of URLs, access logs, and browser history."""
    scheme, _, header_token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and header_token.strip():
        return header_token.strip()
    cookie = request.cookies.get(DEBUG_COOKIE, "").strip()
    if cookie:
        return cookie
    if request.method == "POST":
        posted = (await request.form()).get(TOKEN_FIELD, "")
        if isinstance(posted, str):
            return posted.strip()
    return ""


async def resolve_admin_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None:
    """The workspace this admin request is scoped to, or None to reject. The domain gate is the
    whole authorization: the verified bearer's email domain must equal the deploy's admin domain
    before `?ws=` may re-scope the request to any workspace in the fleet."""
    admin_domain = _require_env(ADMIN_EMAIL_DOMAIN_ENV).lower()
    token = await _bearer(request)
    if not token:
        return None
    claims = verified_claims(token)
    if claims is None:
        return None
    claimed_workspace, email = claims
    if _email_domain(email) != admin_domain:
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


async def bind_session(ctx: SurfaceContext, request: Request) -> Response:
    """Open a session: land the POSTed bearer as the httponly session cookie and redirect into the
    app. The token crosses only in the form body — never a URL — so access logs and browser
    history hold no credential; the identify resolver has already verified this exact form token
    before the handler runs. The cookie is `lax`, not `strict`, because arrival IS a cross-site
    navigation (the apex login page posts here, a Slack footer links here) and the redirected GET
    must already carry it."""
    posted = (await request.form()).get(TOKEN_FIELD, "")
    if not isinstance(posted, str) or not posted.strip():
        return JSONResponse({"error": "token form field is required"}, status_code=400)
    response = RedirectResponse(str(request.url), status_code=303)
    response.set_cookie(DEBUG_COOKIE, posted.strip(), httponly=True, samesite="lax")
    return response


async def app_page(ctx: SurfaceContext, request: Request) -> Response:
    if APP_HTML is None:
        raise RuntimeError(
            "debugger app is not built — run `npm run build` in extensions/debugger/frontend"
        )
    return HTMLResponse(APP_HTML)


async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response:
    installation = await ctx.installation(SLACK_SURFACE)
    slack_team = (
        installation.removeprefix(SLACK_TEAM_PREFIX)
        if installation is not None and installation.startswith(SLACK_TEAM_PREFIX)
        else None
    )
    return JSONResponse({"workspace_id": str(ctx.workspace_id), "slack_team": slack_team})


async def conversations(ctx: SurfaceContext, request: Request) -> Response:
    listed = await ctx.list_conversations()
    return JSONResponse([entry.model_dump(mode="json") for entry in listed])


async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response:
    conversation_id = _uuid_param(request, "conversation_id")
    if conversation_id is None:
        return JSONResponse({"error": "no such conversation"}, status_code=404)
    turns = await ctx.list_turns(conversation_id)
    return JSONResponse([turn.model_dump(mode="json") for turn in turns])


async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response:
    conversation_id = _uuid_param(request, "conversation_id")
    if conversation_id is None:
        return JSONResponse({"error": "no such conversation"}, status_code=404)
    transcript = await ctx.read_transcript(conversation_id)
    if transcript is None:
        return JSONResponse({"error": "no transcript"}, status_code=404)
    return JSONResponse(transcript.model_dump(mode="json"))


async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response:
    conversation_id = _uuid_param(request, "conversation_id")
    if conversation_id is None:
        return JSONResponse({"error": "no such conversation"}, status_code=404)
    return JSONResponse(list(await ctx.list_compactions(conversation_id)))


async def compaction_record(ctx: SurfaceContext, request: Request) -> Response:
    conversation_id = _uuid_param(request, "conversation_id")
    index = request.path_params["index"]
    if conversation_id is None or not index.isdigit():
        return JSONResponse({"error": "no such compaction"}, status_code=404)
    record = await ctx.read_compaction(conversation_id, int(index))
    if record is None:
        return JSONResponse({"error": "no such compaction"}, status_code=404)
    return JSONResponse(
        {
            "index": record.index,
            "before": [message.model_dump(mode="json") for message in record.before],
            "after": [message.model_dump(mode="json") for message in record.after],
            "summary": record.summary.model_dump(mode="json"),
        }
    )


async def workspace_files(ctx: SurfaceContext, request: Request) -> Response:
    conversation_id = _uuid_param(request, "conversation_id")
    if conversation_id is None:
        return JSONResponse({"error": "no such conversation"}, status_code=404)
    files = await ctx.list_workspace_files(conversation_id)
    return JSONResponse([entry.model_dump(mode="json") for entry in files])


async def workspace_file(ctx: SurfaceContext, request: Request) -> Response:
    conversation_id = _uuid_param(request, "conversation_id")
    if conversation_id is None:
        return JSONResponse({"error": "no such file"}, status_code=404)
    try:
        stream = await ctx.read_workspace_file(conversation_id, request.path_params["path"])
    except ValueError:
        return JSONResponse({"error": "no such file"}, status_code=404)
    if stream is None:
        return JSONResponse({"error": "no such file"}, status_code=404)
    return StreamingResponse(stream, media_type="application/octet-stream")


async def turn(ctx: SurfaceContext, request: Request) -> Response:
    turn_id = _uuid_param(request, "turn_id")
    if turn_id is None:
        return JSONResponse({"error": "no such turn"}, status_code=404)
    detail = await ctx.turn_detail(turn_id)
    if detail is None:
        return JSONResponse({"error": "no such turn"}, status_code=404)
    return JSONResponse(detail.model_dump(mode="json"))


async def stream(ctx: SurfaceContext, request: Request) -> Response:
    turn_id = _uuid_param(request, "turn_id")
    if turn_id is None or await ctx.turn_detail(turn_id) is None:
        return JSONResponse({"error": "no such turn"}, status_code=404)
    since = request.headers.get("last-event-id", "")
    return StreamingResponse(_events(ctx, turn_id, since), media_type="text/event-stream")


async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]:
    async for cursor, frame in ctx.tail(turn_id, since):
        yield _sse(cursor, frame)


def _sse(cursor: str, frame: LiveFrame) -> bytes:
    """One SSE event per live frame, named by kind and carrying the frame's raw JSON — a debug
    rendering, never the member-facing one. A non-empty cursor becomes the event id so a dropped
    stream resumes via `Last-Event-ID`."""
    head = f"id: {cursor}\n".encode() if cursor else b""
    match frame:
        case Terminal():
            kind, payload = b"terminal", frame.frame.model_dump_json()
        case Parked():
            kind, payload = b"parked", frame.model_dump_json()
        case CostTick():
            kind, payload = b"cost", frame.model_dump_json()
        case ToolCall():
            kind, payload = b"tool", frame.model_dump_json()
        case SkillLoad():
            kind, payload = b"skill", frame.model_dump_json()
        case TextDelta():
            kind, payload = b"text", frame.model_dump_json()
        case _:
            raise ValueError(f"unmapped live frame {type(frame).__name__}")
    return head + b"event: " + kind + b"\ndata: " + payload.encode() + b"\n\n"


def _uuid_param(request: Request, name: str) -> UUID | None:
    try:
        return UUID(request.path_params[name])
    except ValueError:
        return None


ROUTES = (
    SurfaceRoute(method="GET", path="", handler=app_page),
    SurfaceRoute(method="POST", path="", handler=bind_session),
    SurfaceRoute(method="GET", path="api/workspace", handler=workspace_meta),
    SurfaceRoute(method="GET", path="api/conversations", handler=conversations),
    SurfaceRoute(
        method="GET", path="api/conversations/{conversation_id}/turns", handler=conversation_turns
    ),
    SurfaceRoute(
        method="GET",
        path="api/conversations/{conversation_id}/transcript",
        handler=conversation_transcript,
    ),
    SurfaceRoute(
        method="GET",
        path="api/conversations/{conversation_id}/compactions",
        handler=conversation_compactions,
    ),
    SurfaceRoute(
        method="GET",
        path="api/conversations/{conversation_id}/compactions/{index}",
        handler=compaction_record,
    ),
    SurfaceRoute(
        method="GET", path="api/conversations/{conversation_id}/files", handler=workspace_files
    ),
    SurfaceRoute(
        method="GET",
        path="api/conversations/{conversation_id}/files/{path:path}",
        handler=workspace_file,
    ),
    SurfaceRoute(method="GET", path="api/turns/{turn_id}", handler=turn),
    SurfaceRoute(method="GET", path="api/turns/{turn_id}/stream", handler=stream),
)
