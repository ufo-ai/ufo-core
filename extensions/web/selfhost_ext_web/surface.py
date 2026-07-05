"""The web chat surface on the core surface seam, in its live mode: a self-contained chat page,
cookie-authenticated turn admission, an SSE tail of the turn's live frames, and a spend view.

Auth mirrors the CLI: a session cookie carries the member's token, and its SHA-256 digest is the web
`surface_identity`. The first time a member's token reaches web it adopts the member from their CLI
identity — the token's canonical home — so one human spans CLI and web under one member and one
memory subject. Admission is the shared durable queue every surface admits onto, so the same agent
answers everywhere; web admits without writeback and delivers by tailing the hub over SSE in its own
stream route, never through the writeback poller. Everything web-specific lives here, reaching core
only through the privileged `SurfaceContext` (admit, identity, tail, spend) — the SDK surface a CI
gate pins."""

import hashlib
import html
from collections.abc import AsyncIterator
from uuid import UUID

from selfhost.sdk.accounting import MICRO_USD_PER_USD, SpendReport, SubjectTotal
from selfhost.sdk.http import HTMLResponse, JSONResponse, Request, Response, StreamingResponse
from selfhost.sdk.hub import CostTick, LiveFrame, Parked, SkillLoad, Terminal, ToolCall
from selfhost.sdk.surfaces import SurfaceContext, SurfaceRoute

SURFACE_WEB = "web"
SURFACE_CLI = "cli"
SESSION_COOKIE = "selfhost_session"
MAX_INBOUND_CHARS = 200_000
SPEND_WINDOW_DEFAULT_SECONDS = 86_400


async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | None:
    """The member and session digest a request's cookie authenticates, or None when the cookie is
    missing or its token names no known member. On first contact the web identity is adopted from
    the token's CLI identity — the digest's canonical home — so one human spans both surfaces."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    member_id = await ctx.linked_member(digest) or await ctx.adopt_identity(SURFACE_CLI, digest)
    return None if member_id is None else (member_id, digest)


async def chat_page(ctx: SurfaceContext, request: Request) -> Response:
    """Serve the chat page. A `?token=` binds the session cookie so a member can open the surface
    with the token from `selfhost init`; without it the browser's existing cookie authenticates."""
    response = HTMLResponse(CHAT_PAGE)
    token = request.query_params.get("token", "")
    if token:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict")
    return response


async def chat(ctx: SurfaceContext, request: Request) -> Response:
    auth = await _authenticate(ctx, request)
    if auth is None:
        return Response("missing or unknown session cookie", status_code=401)
    member_id, digest = auth
    inbound = (await request.body()).decode()
    if not inbound.strip():
        return Response("empty message", status_code=400)
    if len(inbound) > MAX_INBOUND_CHARS:
        return Response(f"message exceeds {MAX_INBOUND_CHARS} characters", status_code=413)
    conversation_id = await ctx.conversation_for(digest, member_id)
    agent_id = await ctx.default_agent()
    turn_id = await ctx.admit(conversation_id, agent_id, inbound, writeback=False)
    return JSONResponse({"turn_id": str(turn_id)})


async def stream(ctx: SurfaceContext, request: Request) -> Response:
    auth = await _authenticate(ctx, request)
    if auth is None:
        return Response("missing or unknown session cookie", status_code=401)
    member_id, _digest = auth
    try:
        turn_id = UUID(request.path_params["turn_id"])
    except ValueError:
        return Response("no such turn", status_code=404)
    owner = await ctx.turn_owner(turn_id)
    if owner is None:
        return Response("no such turn", status_code=404)
    if owner != member_id:
        return Response("turn belongs to another member", status_code=403)
    since = request.headers.get("last-event-id", "")
    return StreamingResponse(_events(ctx, turn_id, since), media_type="text/event-stream")


async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]:
    async for cursor, frame in ctx.tail(turn_id, since):
        yield _sse(cursor, frame)


async def spend(ctx: SurfaceContext, request: Request) -> Response:
    """Render the workspace spend rollup over a window — the same sums `selfhost spend` prints, for
    any authenticated member of the workspace."""
    auth = await _authenticate(ctx, request)
    if auth is None:
        return Response("missing or unknown session cookie", status_code=401)
    window = int(request.query_params.get("window_seconds", SPEND_WINDOW_DEFAULT_SECONDS))
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
        "<!doctype html><meta charset=utf-8><title>selfhost spend</title>"
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
    SurfaceRoute(method="GET", path="", handler=chat_page),
    SurfaceRoute(method="POST", path="chat", handler=chat),
    SurfaceRoute(method="GET", path="turns/{turn_id}/stream", handler=stream),
    SurfaceRoute(method="GET", path="spend", handler=spend),
)


CHAT_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>selfhost</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.5 system-ui, sans-serif; display: flex; flex-direction: column;
         height: 100vh; background: Canvas; color: CanvasText; }
  header { padding: 12px 16px; font-weight: 600;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }
  #log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column;
         gap: 10px; }
  .bubble { max-width: 70ch; padding: 8px 12px; border-radius: 12px; white-space: pre-wrap;
            word-wrap: break-word; }
  .me { align-self: flex-end; background: color-mix(in srgb, CanvasText 12%, transparent); }
  .agent { align-self: flex-start; background: color-mix(in srgb, CanvasText 6%, transparent); }
  .meta { font-size: 12px; opacity: 0.6; margin-top: 4px; }
  form { display: flex; gap: 8px; padding: 12px 16px;
         border-top: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }
  #msg { flex: 1; padding: 10px 12px; border-radius: 8px; border: 1px solid
         color-mix(in srgb, CanvasText 25%, transparent); background: Field; color: FieldText; }
  button { padding: 10px 18px; border: 0; border-radius: 8px; background: CanvasText; color: Canvas;
           font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.4; cursor: default; }
</style>
</head>
<body>
<header>selfhost</header>
<div id="log"></div>
<form id="composer">
  <input id="msg" autocomplete="off" placeholder="Message the agent…" autofocus>
  <button type="submit">Send</button>
</form>
<script>
const log = document.getElementById('log');
const input = document.getElementById('msg');
const form = document.getElementById('composer');
const button = form.querySelector('button');

function bubble(cls, text) {
  const el = document.createElement('div');
  el.className = 'bubble ' + cls;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  button.disabled = true;
  bubble('me', text);
  const reply = bubble('agent', '');
  let res;
  try {
    res = await fetch('/surface/web/chat',
      { method: 'POST', body: text, credentials: 'same-origin' });
  } catch (err) {
    reply.textContent = '(network error)';
    button.disabled = false;
    return;
  }
  if (!res.ok) {
    reply.textContent = '(error ' + res.status + ')';
    button.disabled = false;
    return;
  }
  const turnId = (await res.json()).turn_id;
  const source = new EventSource('/surface/web/turns/' + turnId + '/stream');
  let meter = null;
  let activity = null;
  function note(text) {
    if (!activity) {
      activity = document.createElement('div');
      activity.className = 'meta';
      reply.appendChild(activity);
    }
    activity.textContent = text;
    log.scrollTop = log.scrollHeight;
  }
  source.onmessage = (event) => {
    reply.textContent += JSON.parse(event.data).text;
    log.scrollTop = log.scrollHeight;
  };
  source.addEventListener('tool', (event) => {
    const frame = JSON.parse(event.data);
    const detail = frame.description || frame.preview;
    note('running ' + frame.tool + (detail ? ': ' + detail : ''));
  });
  source.addEventListener('skill', (event) => {
    note('loading skill: ' + JSON.parse(event.data).skill);
  });
  source.addEventListener('cost', (event) => {
    const frame = JSON.parse(event.data);
    if (!meter) {
      meter = document.createElement('div');
      meter.className = 'meta';
      reply.appendChild(meter);
    }
    meter.textContent = frame.tokens + ' tok · $' + (frame.cost_micro_usd / 1e6).toFixed(6);
    log.scrollTop = log.scrollHeight;
  });
  source.addEventListener('terminal', (event) => {
    const frame = JSON.parse(event.data);
    if (frame.status === 'done') {
      if (frame.text && !reply.textContent) reply.textContent = frame.text;
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = frame.model + ' · ' + frame.tokens + ' tok · $'
        + (frame.cost_micro_usd / 1e6).toFixed(6);
      reply.appendChild(meta);
    } else {
      const detail = frame.text
        || '(' + frame.status + (frame.error_class ? ': ' + frame.error_class : '') + ')';
      reply.textContent += (reply.textContent ? '\\n' : '') + detail;
    }
    source.close();
    button.disabled = false;
    input.focus();
  });
  source.addEventListener('parked', (event) => {
    const frame = JSON.parse(event.data);
    reply.textContent += (reply.textContent ? '\\n' : '') + frame.message;
    source.close();
    button.disabled = false;
    input.focus();
  });
  source.onerror = () => { source.close(); button.disabled = false; };
});
</script>
</body>
</html>
"""
