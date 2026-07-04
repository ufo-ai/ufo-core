"""The web surface: a self-contained chat page, cookie-authenticated turn admission, an SSE tail of
the turn's live frames, and TTL-token artifact delivery.

Auth mirrors the CLI: a session cookie carries the member's token, and its SHA-256 digest is the
web `surface_identity`. The first time a member's token reaches the web surface it links a web
identity from their CLI identity — the token's canonical home — so one human spans CLI and web under
one member and one memory subject, and a second member reaching web links their own. Admission is
the shared `Admission` both other surfaces use, so the same agent answers everywhere. Artifact
delivery is token-gated, not member-gated: a share link opens for anyone holding an unexpired token
the surface itself minted, and for no unsigned blob key."""

import hashlib
import html
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from selfhost.accounting import MICRO_USD_PER_USD, SpendReport, SpendRollup, SubjectTotal
from selfhost.artifact_token import ArtifactTokenError, verify_artifact_token
from selfhost.blob import BlobNotFound, BlobStore
from selfhost.db import workspace_tx
from selfhost.hub import CostTick, Hub, LiveFrame, Parked, SkillLoad, Terminal, ToolCall
from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import DEFAULT_AGENT_NAME
from selfhost.surfaces.admission import Admission
from selfhost.surfaces.hub_tail import tail_frames

SURFACE_WEB = "web"
SURFACE_CLI = "cli"
SESSION_COOKIE = "selfhost_session"
MAX_INBOUND_CHARS = 200_000
SPEND_WINDOW_DEFAULT_SECONDS = 86_400

router = APIRouter(prefix="/web")


@dataclass(frozen=True)
class WebIdentity:
    member_id: UUID
    workspace_id: UUID
    session_digest: str


@dataclass(frozen=True)
class WebSurface:
    admission: Admission
    hub: Hub
    blob: BlobStore
    artifact_token_secret: str

    async def admit(self, request: Request) -> dict[str, str]:
        identity = await self._authenticate(request)
        inbound = (await request.body()).decode()
        if not inbound.strip():
            raise HTTPException(400, "empty message")
        if len(inbound) > MAX_INBOUND_CHARS:
            raise HTTPException(413, f"message exceeds {MAX_INBOUND_CHARS} characters")
        conversation_id = await self._conversation_for(identity)
        agent_id = await self._default_agent(identity.workspace_id)
        turn_id = await self.admission.admit(
            identity.workspace_id, conversation_id, agent_id, inbound
        )
        return {"turn_id": str(turn_id)}

    async def stream(self, turn_id: UUID, request: Request) -> StreamingResponse:
        identity = await self._authenticate(request)
        await self._require_turn(turn_id, identity)
        return StreamingResponse(self._events(turn_id), media_type="text/event-stream")

    async def spend(self, request: Request, window_seconds: int) -> HTMLResponse:
        """Render the workspace spend rollup over a window — the same sums `selfhost spend` prints,
        for any authenticated member of the workspace."""
        identity = await self._authenticate(request)
        async with workspace_tx() as connection:
            report = await SpendRollup(identity.workspace_id).read(connection, window_seconds)
        return HTMLResponse(_spend_page(report))

    async def download(self, token: str) -> Response:
        if not token:
            raise HTTPException(401, "missing artifact token")
        try:
            claims = verify_artifact_token(token, self.artifact_token_secret, datetime.now(UTC))
        except ArtifactTokenError as error:
            raise HTTPException(403, str(error)) from error
        try:
            data = await self.blob.get(claims.blob_key)
        except BlobNotFound as error:
            raise HTTPException(404, "artifact not found") from error
        headers: dict[str, str] = {}
        if claims.filename:
            encoded = quote(claims.filename, safe="")
            headers["content-disposition"] = (
                f'attachment; filename="{claims.filename}"'
                if encoded == claims.filename
                else f"attachment; filename*=UTF-8''{encoded}"
            )
        return Response(data, media_type="application/octet-stream", headers=headers)

    async def _authenticate(self, request: Request) -> WebIdentity:
        token = request.cookies.get(SESSION_COOKIE, "")
        if not token:
            raise HTTPException(401, "missing session cookie")
        digest = hashlib.sha256(token.encode()).hexdigest()
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.surface_identity.c.member_id,
                        tables.surface_identity.c.workspace_id,
                    ).where(
                        tables.surface_identity.c.surface == SURFACE_WEB,
                        tables.surface_identity.c.external_id == digest,
                    )
                )
            ).one_or_none()
        if row is not None:
            return WebIdentity(row.member_id, row.workspace_id, digest)
        return await self._link(digest)

    async def _link(self, digest: str) -> WebIdentity:
        """Link a web identity from the member's CLI identity the first time their token reaches the
        web surface, mirroring how the Slack surface links on first contact. A token with no CLI
        identity is unknown and refused."""
        async with workspace_tx() as connection:
            cli = (
                await connection.execute(
                    sa.select(
                        tables.surface_identity.c.member_id,
                        tables.surface_identity.c.workspace_id,
                    ).where(
                        tables.surface_identity.c.surface == SURFACE_CLI,
                        tables.surface_identity.c.external_id == digest,
                    )
                )
            ).one_or_none()
        if cli is None:
            raise HTTPException(401, "unknown session token")
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.surface_identity).values(
                        workspace_id=cli.workspace_id,
                        member_id=cli.member_id,
                        surface=SURFACE_WEB,
                        external_id=digest,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("web.identity_link_race")
        return WebIdentity(cli.member_id, cli.workspace_id, digest)

    async def _conversation_for(self, identity: WebIdentity) -> UUID:
        """One private conversation per web session, get-or-create outside the admission tx; a lost
        creation race re-reads the surviving row."""
        lookup = sa.select(tables.conversation.c.id).where(
            tables.conversation.c.surface == SURFACE_WEB,
            tables.conversation.c.queue_key == identity.session_digest,
        )
        async with workspace_tx() as connection:
            found = (await connection.execute(lookup)).one_or_none()
        if found is not None:
            return found.id
        conversation_id = uuid4()
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=identity.workspace_id,
                        surface=SURFACE_WEB,
                        queue_key=identity.session_digest,
                        member_id=identity.member_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("web.conversation_create_lost_race")
            async with workspace_tx() as connection:
                return (await connection.execute(lookup)).one().id
        return conversation_id

    async def _require_turn(self, turn_id: UUID, identity: WebIdentity) -> None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id)
                    .select_from(tables.turn.join(tables.conversation))
                    .where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if row is None:
            raise HTTPException(404, "no such turn")
        if row.member_id != identity.member_id:
            raise HTTPException(403, "turn belongs to another member")

    async def _default_agent(self, workspace_id: UUID) -> UUID:
        async with workspace_tx() as connection:
            agent = (
                await connection.execute(
                    sa.select(tables.agent.c.id).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == DEFAULT_AGENT_NAME,
                    )
                )
            ).one_or_none()
        if agent is None:
            raise HTTPException(404, f"no agent named {DEFAULT_AGENT_NAME!r}")
        return agent.id

    async def _events(self, turn_id: UUID) -> AsyncIterator[bytes]:
        async for frame in tail_frames(self.hub, turn_id):
            yield _sse(frame)


def _sse(frame: LiveFrame) -> bytes:
    if isinstance(frame, Terminal):
        return b"event: terminal\ndata: " + frame.frame.model_dump_json().encode() + b"\n\n"
    if isinstance(frame, Parked):
        return b"event: parked\ndata: " + frame.model_dump_json().encode() + b"\n\n"
    if isinstance(frame, CostTick):
        return b"event: cost\ndata: " + frame.model_dump_json().encode() + b"\n\n"
    if isinstance(frame, ToolCall):
        return b"event: tool\ndata: " + frame.model_dump_json().encode() + b"\n\n"
    if isinstance(frame, SkillLoad):
        return b"event: skill\ndata: " + frame.model_dump_json().encode() + b"\n\n"
    return b"data: " + frame.model_dump_json().encode() + b"\n\n"


@router.get("")
async def chat_page(token: str = "") -> HTMLResponse:
    """Serve the chat page. A `?token=` binds the session cookie so a member can open the surface
    with the token from `selfhost init`; without it the browser's existing cookie authenticates."""
    response = HTMLResponse(CHAT_PAGE)
    if token:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict")
    return response


@router.post("/chat")
async def web_chat(request: Request) -> dict[str, str]:
    return await request.app.state.web.admit(request)


@router.get("/turns/{turn_id}/stream")
async def web_stream(turn_id: UUID, request: Request) -> StreamingResponse:
    return await request.app.state.web.stream(turn_id, request)


@router.get("/spend")
async def web_spend(
    request: Request, window_seconds: int = SPEND_WINDOW_DEFAULT_SECONDS
) -> HTMLResponse:
    return await request.app.state.web.spend(request, window_seconds)


@router.get("/artifacts/download")
async def web_download(request: Request, token: str = "") -> Response:
    return await request.app.state.web.download(token)


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
    res = await fetch('/web/chat', { method: 'POST', body: text, credentials: 'same-origin' });
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
  const source = new EventSource('/web/turns/' + turnId + '/stream');
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
    note('running ' + frame.tool + (frame.preview ? ': ' + frame.preview : ''));
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
