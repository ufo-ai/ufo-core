"""The operator session debugger: a read-only operator surface over one workspace's sessions.

Authorization is entirely in `resolve_operator_workspace`, the surface's shared-fleet `identify`:
the request's gateway bearer must verify against this deploy's `UFO_TOKEN_SECRET` AND carry an
email in `OPERATOR_EMAIL_DOMAIN`, and only then does `?ws=` pick the workspace the request is
scoped to — a raw workspace UUID, or a customer domain (the shared fleet derives a workspace id as
`uuid5(NAMESPACE_DNS, domain)`, so the domain IS the address). Whatever the resolver returns becomes
the request's RLS binding and `SurfaceContext.workspace_id`, so every read below is
workspace-scoped by construction; without `?ws=` the bearer's own workspace claim is the scope.

The page is a built React app served whole from `static/index.html`; everything it renders comes
from the JSON routes under `api/`, all thin dumps of the `SurfaceContext` read views plus an SSE
tail of a live turn (the web surface's delivery pattern, rendered raw for debugging). A session
starts with `POST /surface/debug` carrying the bearer in its form body — never in a URL, so no
access log ever records it — which lands it as the httponly session cookie and redirects to the
app."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from ufo.sdk.http import (
    HTMLResponse,
    JSONResponse,
    Request,
    Response,
    StreamingResponse,
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
)
from ufo.sdk.operator import bind_operator_session
from ufo.sdk.surfaces import SurfaceContext, SurfaceRoute

SURFACE_DEBUG = "debug"
SLACK_SURFACE = "slack"
SLACK_TEAM_PREFIX = "team:"
APP_FILE = Path(__file__).parent / "static" / "index.html"
APP_HTML = APP_FILE.read_text() if APP_FILE.is_file() else None


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
    async with ctx.tail(turn_id, since) as frames:
        async for cursor, frame in frames:
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
        case SubagentActivity():
            kind, payload = b"subagent_activity", frame.model_dump_json()
        case Absorbed():
            kind, payload = b"absorbed", frame.model_dump_json()
        case Resumed():
            kind, payload = b"resumed", frame.model_dump_json()
        case Reply():
            kind, payload = b"reply", frame.model_dump_json()
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
    SurfaceRoute(method="POST", path="", handler=bind_operator_session),
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
