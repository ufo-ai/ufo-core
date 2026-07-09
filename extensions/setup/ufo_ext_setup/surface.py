"""The first-run setup surface: a self-contained portal page that walks the workspace owner through
connecting Slack — the hosted product's default member channel — and shows its live state.

Auth is the stateless HMAC bearer the gateway mints at onboarding, verified by the same codec the
ufo terminal surface uses: `GET /surface/setup?token=…` binds the token as an httponly cookie and
every API route re-verifies it against this workspace, so a token minted for another tenant is
rejected. The Slack card is a state machine (`not_configured → pending → connected`) computed from
the slack surface's four credential slots and the marker blob it writes on the first
signature-verified request — so the member's first DM or @mention is what flips the card to
connected. Saving calls Slack's `auth.test` server-side to validate the bot token
and derive the team and bot-user ids, so the owner pastes two values, not four. Everything
setup-specific lives here, reaching core only through the privileged `SurfaceContext` (credential
read/write, blob, public base URL) — the SDK surface a CI gate pins."""

import json
import os
import re
from dataclasses import dataclass

import httpx
from ufo_ext_slack.surface import (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_BOT_USER_ID_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SLACK_TEAM_ID_SLOT,
    signing_secret_fingerprint,
    url_verified_blob_key,
)
from ufo_ext_ufo.surface import UFO_TOKEN_SECRET_ENV, verify_token

from ufo.sdk.http import HTMLResponse, JSONResponse, PlainTextResponse, Request, Response
from ufo.sdk.surfaces import CredentialSlotUnset, SurfaceContext, SurfaceRoute

SURFACE_SETUP = "setup"
SETUP_COOKIE = "ufo_setup"
SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"
AUTH_TEST_TIMEOUT_SECONDS = 20

SLACK_SLOTS = (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SLACK_TEAM_ID_SLOT,
    SLACK_BOT_USER_ID_SLOT,
)

# Mirrored verbatim as JS regex literals in SETUP_PAGE; a test holds the two in sync.
BOT_TOKEN_PATTERN = r"^xoxb-\S+$"
TEAM_ID_PATTERN = r"^T[A-Z0-9]+$"
BOT_USER_ID_PATTERN = r"^[UW][A-Z0-9]+$"
BOT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,34}$"

TOKEN_REJECTED_ERRORS = ("invalid_auth", "token_revoked", "account_inactive", "not_authed")

# The exact blob the slack-app-setup skill instructs by hand; a test pins the two together so the
# scopes and events can never drift apart.
SLACK_APP_MANIFEST_TEMPLATE = """\
display_information:
  name: {name}
features:
  agent_view:
    agent_description: Answers @mentions in channels and direct messages, replying in-thread.
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: {name}
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - assistant:write
      - channels:history
      - chat:write
      - files:read
      - files:write
      - groups:history
      - im:history
      - mpim:history
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    request_url: {request_url}
    bot_events:
      - app_home_opened
      - app_mention
      - message.channels
      - message.groups
      - message.im
      - message.mpim
  interactivity:
    is_enabled: true
    request_url: {interactivity_url}
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
"""


def _events_url(public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/surface/slack"


def _authenticate(ctx: SurfaceContext, request: Request) -> str | None:
    """The member email the request's bearer authenticates for this workspace, or None. The token
    rides `?token=` on first contact (the onboarding handoff link) and the bound cookie after. A
    missing token secret is an operator misconfiguration, not a bad token — it fails loud (a 500),
    the way the peer ufo surface treats the same condition, rather than masquerading as a 401."""
    secret = os.environ.get(UFO_TOKEN_SECRET_ENV)
    if not secret:
        raise RuntimeError(f"{UFO_TOKEN_SECRET_ENV} is unset — required by the setup surface")
    token = request.query_params.get("token") or request.cookies.get(SETUP_COOKIE, "")
    if not token:
        return None
    return verify_token(secret, token, ctx.workspace_id)


async def _authorize_owner(ctx: SurfaceContext, request: Request) -> str | Response:
    """The owner email for an owner-only action, or the Response to return: 401 when the token is
    missing/invalid, 403 when it is a valid member who is not the workspace owner. Connecting Slack
    rewrites the one bot every member shares, so only the owner may."""
    email = _authenticate(ctx, request)
    if email is None:
        return Response("missing or invalid setup token", status_code=401)
    if not await ctx.is_workspace_owner(email):
        return Response("only the workspace owner can connect Slack", status_code=403)
    return email


async def setup_page(ctx: SurfaceContext, request: Request) -> Response:
    """Serve the portal page. A valid `?token=` binds the setup cookie so the tokened link from
    onboarding signs the browser in; an invalid one binds nothing and the page shows the APIs'
    401 guidance instead. `secure` keeps the bearer cookie off plaintext HTTP."""
    response = HTMLResponse(SETUP_PAGE)
    token = request.query_params.get("token", "")
    if token and _authenticate(ctx, request) is not None:
        response.set_cookie(SETUP_COOKIE, token, httponly=True, secure=True, samesite="strict")
    return response


async def status(ctx: SurfaceContext, request: Request) -> Response:
    email = _authenticate(ctx, request)
    if email is None:
        return Response("missing or invalid setup token", status_code=401)
    return JSONResponse(await _status_payload(ctx, owner=await ctx.is_workspace_owner(email)))


async def _verified_at(ctx: SurfaceContext) -> float | None:
    """The wall-clock second Slack first reached this deploy *with the signing secret currently
    stored* — or None when there is no marker, no stored secret, or a marker written by a
    since-rotated secret, so a stale marker never reads as connected."""
    key = url_verified_blob_key(ctx.workspace_id)
    if not await ctx.blob.exists(key):
        return None
    try:
        marker = json.loads(await ctx.blob.get(key))
    except (ValueError, json.JSONDecodeError):
        return None
    try:
        current = signing_secret_fingerprint(await ctx.credential(SLACK_SIGNING_SECRET_SLOT))
    except CredentialSlotUnset:
        return None
    if not isinstance(marker, dict) or marker.get("fingerprint") != current:
        return None
    at = marker.get("at")
    return float(at) if isinstance(at, (int, float)) else None


async def _status_payload(ctx: SurfaceContext, owner: bool) -> dict[str, object]:
    slots: dict[str, bool] = {}
    for slot in SLACK_SLOTS:
        try:
            await ctx.credential(slot)
            slots[slot] = True
        except CredentialSlotUnset:
            slots[slot] = False
    missing = [slot for slot in SLACK_SLOTS if not slots[slot]]
    verified_at: float | None = None
    if missing:
        state = "not_configured"
        message = "Missing required setup: " + ", ".join(missing)
    else:
        verified_at = await _verified_at(ctx)
        if verified_at is None:
            state = "pending"
            message = (
                "Credentials stored. Invite the bot to a channel and @mention it, or DM it — "
                "its first message flips this to connected."
            )
        else:
            state = "connected"
            message = "Slack reached this deploy and the credentials check out. Talk to the bot."
    base = ctx.public_base_url
    return {
        "slack": {"state": state, "message": message, "slots": slots, "verified_at": verified_at},
        "base_url_set": base is not None,
        "events_url": _events_url(base) if base else None,
        "owner": owner,
    }


async def slack_manifest_yaml(ctx: SurfaceContext, request: Request) -> Response:
    owner = await _authorize_owner(ctx, request)
    if isinstance(owner, Response):
        return owner
    base = ctx.public_base_url
    if not base:
        return Response(
            "no public base URL — set [connect] public_base_url and restart the deploy",
            status_code=409,
        )
    name = request.query_params.get("name") or "ufo"
    if not re.match(BOT_NAME_PATTERN, name):
        return Response("bot display name must be 1-35 plain characters", status_code=400)
    events_url = _events_url(base)
    return PlainTextResponse(
        SLACK_APP_MANIFEST_TEMPLATE.format(
            name=name, request_url=events_url, interactivity_url=f"{events_url}/interactive"
        )
    )


async def save_credentials(ctx: SurfaceContext, request: Request) -> Response:
    """Validate and store the Slack slots. Blank/absent fields keep their stored value; when the
    team or bot-user id isn't given explicitly, both derive from the bot token via `auth.test`, and
    an explicit id that disagrees with `auth.test` is refused (a typo'd gate would silently drop
    every event) — nothing is written unless every provided value validates."""
    owner = await _authorize_owner(ctx, request)
    if isinstance(owner, Response):
        return owner
    try:
        payload = json.loads(await request.body())
    except json.JSONDecodeError:
        return Response("body must be JSON", status_code=400)
    if not isinstance(payload, dict):
        return Response("body must be a JSON object", status_code=400)
    provided = {
        slot: value.strip()
        for slot, value in payload.items()
        if slot in SLACK_SLOTS and isinstance(value, str) and value.strip()
    }
    if not provided:
        return Response("no fields to save — every field was blank", status_code=400)
    for slot, pattern in (
        (SLACK_BOT_TOKEN_SLOT, BOT_TOKEN_PATTERN),
        (SLACK_TEAM_ID_SLOT, TEAM_ID_PATTERN),
        (SLACK_BOT_USER_ID_SLOT, BOT_USER_ID_PATTERN),
    ):
        if slot in provided and not re.match(pattern, provided[slot]):
            return JSONResponse(
                {"field": slot, "error": f"{slot} must match {pattern}"}, status_code=400
            )
    token = provided.get(SLACK_BOT_TOKEN_SLOT)
    ids_missing = SLACK_TEAM_ID_SLOT not in provided or SLACK_BOT_USER_ID_SLOT not in provided
    if token and ids_missing:
        result = await _auth_test(token)
        if not result.ok:
            return JSONResponse(
                {"field": SLACK_BOT_TOKEN_SLOT, "error": _token_diagnosis(result.error)},
                status_code=400,
            )
        # auth.test can answer ok with a shape we can't use; refuse to store an empty/garbage id
        # rather than write a slot that silently drops every real event on the team-id gate.
        if not re.match(TEAM_ID_PATTERN, result.team_id) or not re.match(
            BOT_USER_ID_PATTERN, result.user_id
        ):
            return JSONResponse(
                {
                    "field": SLACK_BOT_TOKEN_SLOT,
                    "error": "Slack auth.test did not return a usable team/bot id — set them "
                    "manually under Advanced.",
                },
                status_code=400,
            )
        for slot, live in (
            (SLACK_TEAM_ID_SLOT, result.team_id),
            (SLACK_BOT_USER_ID_SLOT, result.user_id),
        ):
            if provided.get(slot, live) != live:
                return JSONResponse(
                    {
                        "field": slot,
                        "error": f"The bot token belongs to {live} but {slot} was given as "
                        f"{provided[slot]} — leave it blank to derive from the token.",
                    },
                    status_code=400,
                )
            provided[slot] = live
    for slot, value in provided.items():
        await ctx.put_credential(slot, value)
    return JSONResponse(await _status_payload(ctx, owner=True))


async def test_slack(ctx: SurfaceContext, request: Request) -> Response:
    """A live diagnosis: `auth.test` with the stored bot token, cross-checked against the stored
    team and bot-user ids — one call validates three of the four slots. The signing secret is only
    provable by a signed request from Slack, which the status state machine reports."""
    owner = await _authorize_owner(ctx, request)
    if isinstance(owner, Response):
        return owner
    try:
        token = await ctx.credential(SLACK_BOT_TOKEN_SLOT)
    except CredentialSlotUnset:
        return JSONResponse(
            {
                "ok": False,
                "diagnosis": (
                    "Missing required setup: slack_bot_token — save the Bot User OAuth Token first."
                ),
            }
        )
    result = await _auth_test(token)
    if not result.ok:
        return JSONResponse({"ok": False, "diagnosis": _token_diagnosis(result.error)})
    for slot, live, label in (
        (SLACK_TEAM_ID_SLOT, result.team_id, "team"),
        (SLACK_BOT_USER_ID_SLOT, result.user_id, "bot user"),
    ):
        try:
            stored = await ctx.credential(slot)
        except CredentialSlotUnset:
            continue
        if live and stored != live:
            return JSONResponse(
                {
                    "ok": False,
                    "diagnosis": (
                        f"The bot token belongs to {label} {live} but {slot} is {stored} — "
                        "refill the slots from the same Slack app."
                    ),
                }
            )
    if await _verified_at(ctx) is not None:
        diagnosis = f"Connected to {result.team} as @{result.user}."
    else:
        diagnosis = (
            f"Token valid for {result.team} as @{result.user} — waiting on Slack's first "
            "event. DM the bot or @mention it in a channel."
        )
    return JSONResponse({"ok": True, "diagnosis": diagnosis})


@dataclass(frozen=True)
class AuthTest:
    ok: bool
    error: str
    team_id: str
    user_id: str
    team: str
    user: str


async def _auth_test(bot_token: str) -> AuthTest:
    try:
        async with httpx.AsyncClient(timeout=AUTH_TEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                SLACK_AUTH_TEST_URL, headers={"authorization": f"Bearer {bot_token}"}
            )
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        return AuthTest(
            ok=False, error=f"unreachable: {error}", team_id="", user_id="", team="", user=""
        )
    if not isinstance(payload, dict):
        return AuthTest(
            ok=False, error="malformed response", team_id="", user_id="", team="", user=""
        )
    return AuthTest(
        ok=bool(payload.get("ok")),
        error=str(payload.get("error") or ""),
        team_id=str(payload.get("team_id") or ""),
        user_id=str(payload.get("user_id") or ""),
        team=str(payload.get("team") or ""),
        user=str(payload.get("user") or ""),
    )


def _token_diagnosis(error: str) -> str:
    if error in TOKEN_REJECTED_ERRORS:
        return (
            f"Slack rejected the bot token ({error}) — re-copy the Bot User OAuth Token from "
            "OAuth & Permissions and save it again."
        )
    return f"Slack auth.test failed: {error or 'no error given'}"


ROUTES = (
    SurfaceRoute(method="GET", path="", handler=setup_page),
    SurfaceRoute(method="GET", path="status", handler=status),
    SurfaceRoute(method="GET", path="slack/manifest", handler=slack_manifest_yaml),
    SurfaceRoute(method="POST", path="slack/credentials", handler=save_credentials),
    SurfaceRoute(method="POST", path="slack/test", handler=test_slack),
)


SETUP_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ufo setup</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.5 system-ui, sans-serif; background: Canvas; color: CanvasText; }
  header { padding: 12px 16px; font-weight: 600;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }
  main { max-width: 720px; margin: 0 auto; padding: 24px 16px; display: flex;
         flex-direction: column; gap: 16px; }
  .card { border: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
          border-radius: 12px; padding: 16px; }
  .card h2 { margin: 0 0 6px; font-size: 16px; display: flex; align-items: center; gap: 10px; }
  .badge { font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 999px;
           background: color-mix(in srgb, CanvasText 10%, transparent); }
  .badge.ok { background: color-mix(in srgb, #26953c 22%, transparent); color: #26953c; }
  .badge.warn { background: color-mix(in srgb, #b97d10 22%, transparent); color: #b97d10; }
  .badge.err { background: color-mix(in srgb, #c33636 22%, transparent); color: #c33636; }
  .hint { font-size: 13px; opacity: 0.75; }
  .note { font-size: 13px; margin-top: 8px; }
  .note.err { color: #c33636; }
  .note.ok { color: #26953c; }
  pre { overflow-x: auto; padding: 12px; border-radius: 8px; margin: 8px 0;
        background: color-mix(in srgb, CanvasText 6%, transparent);
        font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  label { display: block; font-size: 13px; margin: 12px 0 4px; }
  input { width: 100%; padding: 8px 10px; border-radius: 8px; background: Field; color: FieldText;
          border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  button { padding: 8px 16px; border: 0; border-radius: 8px; background: CanvasText; color: Canvas;
           font-weight: 600; cursor: pointer; }
  button.ghost { background: transparent; color: CanvasText;
                 border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  button:disabled { opacity: 0.4; cursor: default; }
  details { margin-top: 10px; }
  summary { cursor: pointer; font-size: 14px; font-weight: 600; }
  .row { display: flex; gap: 10px; margin-top: 14px; align-items: center; flex-wrap: wrap; }
  ul.try { margin: 8px 0 0; padding-left: 20px; font-size: 14px; }
  ul.try li { margin: 6px 0; }
  code { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
         background: color-mix(in srgb, CanvasText 8%, transparent);
         padding: 1px 5px; border-radius: 5px; }
  #unauth { display: none; border-color: #c33636; }
</style>
</head>
<body>
<header>ufo · setup</header>
<main>
  <section id="unauth" class="card">
    <h2>Sign in required <span class="badge err">no session</span></h2>
    <div class="hint">Open the tokened setup link from your onboarding (it ends in
      <code>/surface/setup?token=…</code>) to use this page.</div>
  </section>

  <section class="card">
    <h2>Workspace <span class="badge ok">running</span></h2>
    <div class="hint">This deploy is live. Slack events arrive at
      <code id="events-url">…</code></div>
  </section>

  <section class="card">
    <h2>Connect Slack <span id="slack-badge" class="badge">checking…</span></h2>
    <div id="slack-message" class="hint"></div>
    <div id="base-url-note" class="note err" hidden>This deploy has no
      <code>[connect] public_base_url</code> — Slack cannot reach it. Set it and restart.</div>
    <div id="not-owner" class="note" hidden>Only the workspace owner can connect Slack. Ask the
      person who created this workspace to open their setup link.</div>

    <div id="owner-only">
    <details id="create-app">
      <summary>1 · Create the Slack app from a manifest</summary>
      <div class="hint" style="margin-top:8px">At
        <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer">api.slack.com/apps</a>
        choose <b>Create New App → From a manifest</b>, pick your Slack workspace, and paste the
        YAML below. Then <b>Install App → Install to Workspace</b>.</div>
      <label for="bot-name">Bot display name</label>
      <input id="bot-name" value="ufo" autocomplete="off">
      <pre id="manifest">…</pre>
      <div class="row">
        <button type="button" class="ghost" id="copy-manifest">Copy manifest</button>
        <span id="copy-note" class="hint"></span>
      </div>
    </details>

    <details open id="fill-creds">
      <summary>2 · Paste the app's credentials</summary>
      <form id="creds">
        <label for="slack_bot_token">Bot User OAuth Token — OAuth &amp; Permissions, after
          install</label>
        <input id="slack_bot_token" type="password" autocomplete="off" placeholder="xoxb-…">
        <label for="slack_signing_secret">Signing Secret — Basic Information → App
          Credentials</label>
        <input id="slack_signing_secret" type="password" autocomplete="off">
        <details>
          <summary>Advanced — set ids manually</summary>
          <label for="slack_team_id">Team ID</label>
          <input id="slack_team_id" autocomplete="off" placeholder="T…">
          <label for="slack_bot_user_id">Bot User ID</label>
          <input id="slack_bot_user_id" autocomplete="off" placeholder="U…">
          <div class="hint" style="margin-top:6px">Left blank, both derive from the bot token via
            Slack's <code>auth.test</code>.</div>
        </details>
        <div class="row">
          <button type="submit" id="save">Save</button>
          <button type="button" class="ghost" id="test">Test</button>
          <span class="hint">Stored encrypted in your workspace — takes effect immediately.</span>
        </div>
        <div id="diagnosis" class="note"></div>
      </form>
    </details>
    </div>
  </section>

  <section class="card">
    <h2>Done</h2>
    <div class="hint">Invite the bot to a Slack channel and @mention it, or DM it — its first
      message is also what flips the card above to connected. Skipping for now is fine — return to
      this page anytime at <code>/surface/setup</code>.</div>
  </section>

  <section class="card">
    <h2>Try it</h2>
    <ul class="try">
      <li><b>Ask what it can do</b> — DM it <code>what can you do?</code> and it introduces
        itself and its tools.</li>
      <li><b>Hand it a file</b> — attach a CSV, PDF, or log to a message and ask for the
        highlights; attachments land in its workspace.</li>
      <li><b>Follow up in the thread</b> — each thread is one conversation, so it keeps the
        context; @mention it in a channel to start one there.</li>
    </ul>
  </section>
</main>
<script>
const BASE = '/surface/setup';
const PATTERNS = {
  slack_bot_token: /^xoxb-\S+$/,
  slack_team_id: /^T[A-Z0-9]+$/,
  slack_bot_user_id: /^[UW][A-Z0-9]+$/,
};
const FIELDS = ['slack_bot_token', 'slack_signing_secret', 'slack_team_id', 'slack_bot_user_id'];
const badge = document.getElementById('slack-badge');
const message = document.getElementById('slack-message');
const diagnosis = document.getElementById('diagnosis');
const manifestPre = document.getElementById('manifest');
const botName = document.getElementById('bot-name');
let pollTimer = null;
let manifestLoaded = false;

function note(el, text, cls) {
  el.textContent = text;
  el.className = 'note' + (cls ? ' ' + cls : '');
}

function render(status) {
  const slack = status.slack;
  const looks = { not_configured: ['not configured', ''], pending: ['pending', 'warn'],
                  connected: ['connected', 'ok'] };
  const [label, cls] = looks[slack.state] || [slack.state, ''];
  badge.textContent = label;
  badge.className = 'badge' + (cls ? ' ' + cls : '');
  message.textContent = slack.message;
  document.getElementById('base-url-note').hidden = status.base_url_set;
  document.getElementById('events-url').textContent = status.events_url || '(no public base URL)';
  // Only the owner may write the shared Slack credentials; a non-owner member sees the state but
  // not the form (the server enforces this too — the form is just hidden here).
  document.getElementById('not-owner').hidden = status.owner;
  document.getElementById('owner-only').hidden = !status.owner;
  if (!status.owner) return;
  if (!manifestLoaded) loadManifest();
  document.getElementById('create-app').open = slack.state === 'not_configured';
  document.getElementById('fill-creds').open = slack.state !== 'connected';
  for (const field of FIELDS) {
    const input = document.getElementById(field);
    input.placeholder = slack.slots[field] ? 'set — leave blank to keep' : input.placeholder;
  }
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (slack.state === 'pending') pollTimer = setTimeout(refresh, 5000);
}

function statusFailed(reason) {
  badge.textContent = 'error';
  badge.className = 'badge err';
  message.textContent = 'Status check failed (' + reason + ') — retrying every 5 seconds.';
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(refresh, 5000);
}

async function refresh() {
  let res;
  try { res = await fetch(BASE + '/status', { credentials: 'same-origin' }); }
  catch (err) { return statusFailed('network error'); }
  if (res.status === 401) {
    document.getElementById('unauth').style.display = 'block';
    badge.textContent = 'sign in required';
    badge.className = 'badge err';
    return;
  }
  if (!res.ok) return statusFailed('HTTP ' + res.status);
  render(await res.json());
}

async function loadManifest() {
  const name = encodeURIComponent(botName.value || 'ufo');
  let res;
  try { res = await fetch(BASE + '/slack/manifest?name=' + name, { credentials: 'same-origin' }); }
  catch (err) { manifestPre.textContent = '(network error — edit the bot name to retry)'; return; }
  manifestLoaded = res.ok;
  manifestPre.textContent = res.ok ? await res.text() : '(' + await res.text() + ')';
}

document.getElementById('copy-manifest').addEventListener('click', async () => {
  await navigator.clipboard.writeText(manifestPre.textContent);
  document.getElementById('copy-note').textContent = 'copied';
  setTimeout(() => { document.getElementById('copy-note').textContent = ''; }, 2000);
});
botName.addEventListener('change', loadManifest);

document.getElementById('creds').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = {};
  for (const field of FIELDS) {
    const value = document.getElementById(field).value.trim();
    if (!value) continue;
    if (PATTERNS[field] && !PATTERNS[field].test(value)) {
      note(diagnosis, field + ' must match ' + PATTERNS[field].source, 'err');
      return;
    }
    body[field] = value;
  }
  if (!Object.keys(body).length) {
    note(diagnosis, 'nothing to save — every field is blank', 'err');
    return;
  }
  const save = document.getElementById('save');
  save.disabled = true;
  note(diagnosis, 'saving…', '');
  try {
    const res = await fetch(BASE + '/slack/credentials', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
    if (res.ok) {
      for (const field of FIELDS) document.getElementById(field).value = '';
      note(diagnosis, 'saved', 'ok');
      render(await res.json());
    } else {
      const payload = await res.json().catch(() => null);
      note(diagnosis, payload && payload.error ? payload.error : 'error ' + res.status, 'err');
    }
  } catch (err) { note(diagnosis, 'network error', 'err'); }
  save.disabled = false;
});

document.getElementById('test').addEventListener('click', async () => {
  const test = document.getElementById('test');
  test.disabled = true;
  note(diagnosis, 'testing…', '');
  try {
    const res = await fetch(BASE + '/slack/test', { method: 'POST', credentials: 'same-origin' });
    if (res.status === 401) { note(diagnosis, 'sign in required — open the tokened link', 'err'); }
    else {
      const payload = await res.json();
      note(diagnosis, payload.diagnosis, payload.ok ? 'ok' : 'err');
    }
  } catch (err) { note(diagnosis, 'network error', 'err'); }
  test.disabled = false;
  refresh();
});

refresh();
</script>
</body>
</html>
"""
