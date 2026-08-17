"""The web presentation of the onboarding machine the terminal client drives.

`GET /login` serves a self-contained sign-in page; `POST /v1/onboard/web` advances the identical
`Onboarding` state machine (the claim row keyed by the onboarding session) and returns the directive
lines as JSON — a second renderer, never a second machine. The browser collects the work email and
the code inline, exactly as the terminal does: the machine's `say`/`ask` directives render as the
transcript and the next input, so the page reads and answers them and never leaves for a hosted
sign-in page. The session is the `__Host-ufo_onboard` cookie the gateway mints and seals
server-side — `__Host-`, so the browser keeps it host-only and refuses to let a sibling host plant
it. `POST /v1/onboard/web` mints a fresh one whenever the presented cookie stands behind no live
claim, so a claim is only ever started under a session minted here and bound to this browser from
the submit that starts it; the id is never typed, named, or read by the page, so nothing outside
that browser can name the session the verified claim is keyed to, and the page's POSTs carry the
cookie same-origin. `Continue with Google` navigates top-level to
`GET /v1/onboard/auth/start`, which mints the same cookie, signs the carry into the OAuth state, and
302s to WorkOS with `provider=GoogleOAuth` — WorkOS goes straight to Google, no hosted page — and
the callback stamps the claim verified and 303s back here, where the machine resumes.

The page renders `/login?error=<sentence>` as the refusal above a `Sign in again`
link. It renders `say` as transcript lines — reading the member's email off the machine's own
`Signed in: ` line — `ask` as the next input, and `token`+`workspace` as the signed-in home card:
that email, their workspace URL, the terminal install line — and, when the gateway's `debugger`
directive arrived (an operator-domain email only), a form that POSTs the token to the operator
session debugger, which exchanges it for its session cookie — the bearer never rides a URL.

The `slack` directive (an admin only, since only an admin installs) names what to ask the workspace
for. The card states it rather than linking it: an install link seals one workspace and one member
for fifteen minutes, so it can only be minted by the turn that answers the member, never printed
onto a page they may leave open.

A conversation the portal redirected here with (`/login?c=<uuid>`) is carried onto the card's
portal action and onto the Google hop, so the member lands on the conversation they clicked rather
than a new chat. Only a uuid shape is carried, and it rides the hop's query out and back, so it
survives the sign-in."""

WEB_CHANNEL = "web"
ONBOARD_SESSION_COOKIE = "__Host-ufo_onboard"


def parse_directives(payload: bytes) -> list[dict[str, object]]:
    """The directive lines as JSON-able dicts — the exact inverse of `directive()`'s escaping, so
    a field's tabs and newlines survive the line framing."""
    parsed: list[dict[str, object]] = []
    for raw in payload.decode().split("\n"):
        if not raw:
            continue
        verb, *fields = raw.split("\t")
        parsed.append({"verb": verb, "fields": [_unescape(field) for field in fields]})
    return parsed


def _unescape(field: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(field):
        char = field[index]
        if char == "\\" and index + 1 < len(field):
            escaped = {"\\": "\\", "t": "\t", "n": "\n"}.get(field[index + 1])
            if escaped is not None:
                out.append(escaped)
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


LOGIN_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · ufo</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.5 system-ui, sans-serif; background: Canvas; color: CanvasText; }
  header { padding: 12px 16px; font-weight: 600;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 15%, transparent); }
  main { max-width: 560px; margin: 0 auto; padding: 32px 16px; display: flex;
         flex-direction: column; gap: 16px; }
  .card { border: 1px solid color-mix(in srgb, CanvasText 15%, transparent);
          border-radius: 12px; padding: 20px; }
  .card h1 { margin: 0 0 4px; font-size: 18px; }
  .hint { font-size: 13px; opacity: 0.75; }
  #log { display: flex; flex-direction: column; gap: 4px; margin: 14px 0 0; }
  #log div { white-space: pre-wrap; word-wrap: break-word; }
  .error { color: #c33636; }
  #prompt-row { display: none; margin-top: 14px; }
  #prompt-label { display: block; font-size: 13px; margin-bottom: 4px; }
  .row { display: flex; gap: 8px; }
  input, select { flex: 1; padding: 10px 12px; border-radius: 8px; background: Field;
                  color: FieldText;
                  border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  button { padding: 10px 18px; border: 0; border-radius: 8px; background: CanvasText; color: Canvas;
           font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.4; cursor: default; }
  a.button { display: inline-block; padding: 10px 18px; border-radius: 8px; background: CanvasText;
             color: Canvas; font-weight: 600; text-decoration: none; }
  a.button.secondary { display: block; text-align: center; background: transparent;
                       color: CanvasText;
                       border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  #google-group { margin-top: 14px; }
  .divider { text-align: center; font-size: 13px; opacity: 0.6; margin: 0 0 10px; }
  #home { display: none; }
  #portal-row { margin-top: 14px; }
  #debugger-row { display: none; margin-top: 14px; }
  code { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
         background: color-mix(in srgb, CanvasText 8%, transparent);
         padding: 1px 5px; border-radius: 5px; }
  pre { overflow-x: auto; padding: 12px; border-radius: 8px; margin: 8px 0 0;
        background: color-mix(in srgb, CanvasText 6%, transparent);
        font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
</head>
<body>
<header>ufo</header>
<main>
  <section class="card" id="board">
    <h1>Sign in</h1>
    <div id="log"></div>
    <form id="prompt-row">
      <label id="prompt-label" for="answer"></label>
      <div class="row">
        <input id="answer" autocomplete="off" autofocus>
        <select id="choice" style="display:none"></select>
        <button type="submit" id="go">Continue</button>
      </div>
    </form>
    <div id="google-group">
      <div class="divider">or</div>
      <a class="button secondary" id="google">Continue with Google</a>
    </div>
  </section>

  <section class="card" id="home">
    <h1>Signed in</h1>
    <div class="hint"><code id="member-email"></code> · <code id="workspace-url"></code></div>
    <form id="portal-row" method="post">
      <input type="hidden" name="token" id="portal-token">
      <button type="submit">Open your workspace</button>
    </form>
    <div class="hint" id="slack-line" style="margin-top:10px"></div>
    <div class="hint" style="margin-top:10px">From your terminal:</div>
    <pre id="curl-line"></pre>
    <form id="debugger-row" method="post">
      <input type="hidden" name="token" id="debugger-token">
      <button type="submit">Session debugger</button>
    </form>
  </section>
</main>
<script>
const SIGNED_IN = 'Signed in: ';
const START = '/v1/onboard/auth/start';
const params = new URLSearchParams(location.search);
const fault = params.get('error');
const log = document.getElementById('log');
const promptRow = document.getElementById('prompt-row');
const promptLabel = document.getElementById('prompt-label');
const answer = document.getElementById('answer');
const choice = document.getElementById('choice');
const go = document.getElementById('go');
const googleGroup = document.getElementById('google-group');
const target = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.exec(
  new URLSearchParams(location.search).get('c') || '');
const artifactRaw = new URLSearchParams(location.search).get('a') || '';
const artifact = artifactRaw.startsWith('/artifacts/') ? artifactRaw : null;
let token = null;
let workspace = null;
let debuggerUrl = null;
let slackAsk = null;
let email = null;
let choosing = false;
let finished = false;
let lastPrompt = null;

function line(text, cls) {
  const el = document.createElement('div');
  if (cls) el.className = cls;
  el.textContent = text;
  log.appendChild(el);
  return el;
}

function prompt(text, options) {
  if (finished) return;
  lastPrompt = text;
  promptLabel.textContent = text;
  promptRow.style.display = 'block';
  choosing = Boolean(options && options.length);
  promptLabel.htmlFor = choosing ? 'choice' : 'answer';
  answer.type = text.includes('email') ? 'email' : 'text';
  answer.inputMode = text.includes('code') ? 'numeric' : 'text';
  answer.style.display = choosing ? 'none' : 'block';
  choice.style.display = choosing ? 'block' : 'none';
  choice.replaceChildren(...(options || []).map((option) => new Option(option, option)));
  googleGroup.style.display = !choosing && text.includes('email') ? 'block' : 'none';
  go.disabled = false;
  (choosing ? choice : answer).focus();
}

function complete() {
  finished = true;
  promptRow.style.display = 'none';
  googleGroup.style.display = 'none';
  document.getElementById('member-email').textContent = email || '';
  document.getElementById('workspace-url').textContent = workspace;
  const portal = document.getElementById('portal-row');
  portal.action = workspace + '/surface/web' +
    (target ? '?c=' + target[0] : artifact ? '?a=' + encodeURIComponent(artifact) : '');
  document.getElementById('portal-token').value = token;
  document.getElementById('curl-line').textContent =
    'curl -fsSL ' + location.origin + '/ufo | sh';
  if (slackAsk) {
    const slackLine = document.getElementById('slack-line');
    slackLine.textContent = 'To install ufo in Slack, ask your workspace: ' + slackAsk;
    slackLine.style.display = 'block';
  }
  if (debuggerUrl) {
    const row = document.getElementById('debugger-row');
    row.action = debuggerUrl;
    document.getElementById('debugger-token').value = token;
    row.style.display = 'block';
  }
  document.getElementById('home').style.display = 'block';
  document.title = 'Signed in · ufo';
}

function handle(directive) {
  const arg = directive.fields[0] || '';
  if (directive.verb === 'say') {
    if (arg.startsWith(SIGNED_IN)) email = arg.slice(SIGNED_IN.length);
    line(arg);
  }
  else if (directive.verb === 'ask') prompt(arg);
  else if (directive.verb === 'choose') prompt(arg, directive.fields.slice(1));
  else if (directive.verb === 'token') token = arg;
  else if (directive.verb === 'workspace') workspace = arg;
  else if (directive.verb === 'slack') slackAsk = arg;
  else if (directive.verb === 'debugger') debuggerUrl = arg;
  else if (directive.verb === 'exit') {
    if (arg !== '0') line('Failed — reload to retry.', 'error');
    finished = true;
    promptRow.style.display = 'none';
  }
}

async function advance(body) {
  go.disabled = true;
  let res;
  try {
    res = await fetch('/v1/onboard/web', { method: 'POST', body: body || '' });
  } catch (err) {
    line('Network error — retrying…', 'error');
    setTimeout(() => advance(body), 5000);
    return;
  }
  if (!res.ok) {
    line('Error ' + res.status + ' — try again.', 'error');
    if (lastPrompt && !finished) prompt(lastPrompt);
    else go.disabled = false;
    return;
  }
  const payload = await res.json();
  for (const directive of payload.directives) handle(directive);
  if (token && workspace && !finished) complete();
}

promptRow.addEventListener('submit', (event) => {
  event.preventDefault();
  const value = (choosing ? choice.value : answer.value).trim();
  if (!value) return;
  answer.value = '';
  promptRow.style.display = 'none';
  advance(value);
});

const gq = new URLSearchParams();
if (target) gq.set('c', target[0]);
if (artifact) gq.set('a', artifact);
document.getElementById('google').href = START + (gq.size ? '?' + gq : '');

if (fault) {
  line(fault, 'error');
  const again = document.createElement('a');
  again.className = 'button';
  again.style.alignSelf = 'flex-start';
  again.textContent = 'Sign in again';
  const q = new URLSearchParams();
  if (target) q.set('c', target[0]);
  if (artifact) q.set('a', artifact);
  again.href = '/login' + (q.size ? '?' + q : '');
  log.appendChild(again);
} else {
  advance('');
}
</script>
</body>
</html>
"""
