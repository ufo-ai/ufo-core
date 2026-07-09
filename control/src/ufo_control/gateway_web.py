"""The web presentation of the onboarding machine the terminal client drives.

`GET /` serves a self-contained portal page; `POST /v1/onboard/web` advances the identical
`Onboarding` state machine (the claim row keyed by the page's generated session id) and returns the
directive lines as JSON — a second renderer, never a second machine. The page renders `say` as
transcript lines, `ask` as the next input, `status`+`poll` as provisioning progress, and
`token`+`workspace` as the signed-in handoff: a link into the tenant's setup surface
(`/surface/setup?token=…`) to connect Slack, the hosted product's default channel."""

WEB_CHANNEL = "web"


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


PORTAL_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ufo</title>
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
  .status { opacity: 0.7; font-size: 13px; }
  .error { color: #c33636; }
  #prompt-row { display: none; margin-top: 14px; }
  #prompt-label { display: block; font-size: 13px; margin-bottom: 4px; }
  .row { display: flex; gap: 8px; }
  input { flex: 1; padding: 10px 12px; border-radius: 8px; background: Field; color: FieldText;
          border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
  button { padding: 10px 18px; border: 0; border-radius: 8px; background: CanvasText; color: Canvas;
           font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.4; cursor: default; }
  a.button { display: inline-block; padding: 10px 18px; border-radius: 8px; background: CanvasText;
             color: Canvas; font-weight: 600; text-decoration: none; }
  #done { display: none; }
  code { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
         background: color-mix(in srgb, CanvasText 8%, transparent);
         padding: 1px 5px; border-radius: 5px; }
  pre { overflow-x: auto; padding: 12px; border-radius: 8px; margin: 8px 0 0;
        background: color-mix(in srgb, CanvasText 6%, transparent);
        font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  details { margin-top: 12px; }
  summary { cursor: pointer; font-size: 13px; opacity: 0.75; }
</style>
</head>
<body>
<header>u f o</header>
<main>
  <section class="card" id="board">
    <h1>Board your workspace</h1>
    <div class="hint">Sign in with your work email — your team's workspace is provisioned on the
      spot if it doesn't exist yet.</div>
    <div id="log"></div>
    <form id="prompt-row">
      <label id="prompt-label" for="answer"></label>
      <div class="row">
        <input id="answer" autocomplete="off" autofocus>
        <button type="submit" id="go">Continue</button>
      </div>
    </form>
  </section>

  <section class="card" id="done">
    <h1>Workspace ready</h1>
    <div class="hint">Your workspace lives at <code id="workspace-url"></code>. Next, connect
      Slack — it's how your team talks to the agent.</div>
    <div class="row" style="margin-top:14px">
      <a class="button" id="setup-link" href="#">Connect Slack →</a>
    </div>
    <div class="hint" style="margin-top:10px">Skip for now and return anytime via the setup page
      link above — it stays available at <code id="setup-path"></code>.</div>
    <details>
      <summary>Prefer the terminal?</summary>
      <pre id="curl-line"></pre>
    </details>
  </section>
</main>
<script>
const session = crypto.randomUUID();
const log = document.getElementById('log');
const promptRow = document.getElementById('prompt-row');
const promptLabel = document.getElementById('prompt-label');
const answer = document.getElementById('answer');
const go = document.getElementById('go');
let token = null;
let workspace = null;
let finished = false;
let lastPrompt = null;

function line(text, cls) {
  const el = document.createElement('div');
  if (cls) el.className = cls;
  el.textContent = text;
  log.appendChild(el);
  return el;
}

let statusEl = null;
function status(text) {
  if (!statusEl) statusEl = line('', 'status');
  statusEl.textContent = text;
}

function prompt(text) {
  if (finished) return;
  lastPrompt = text;
  promptLabel.textContent = text;
  promptRow.style.display = 'block';
  answer.type = text.includes('email') ? 'email' : 'text';
  answer.inputMode = text.includes('code') ? 'numeric' : 'text';
  go.disabled = false;
  answer.focus();
}

function complete() {
  finished = true;
  promptRow.style.display = 'none';
  const setupUrl = workspace + '/surface/setup?token=' + encodeURIComponent(token);
  document.getElementById('workspace-url').textContent = workspace;
  document.getElementById('setup-link').href = setupUrl;
  document.getElementById('setup-path').textContent = workspace + '/surface/setup';
  document.getElementById('curl-line').textContent =
    'curl -fsSL ' + location.origin + '/ufo | sh';
  document.getElementById('done').style.display = 'block';
}

function handle(directive) {
  const arg = directive.fields[0] || '';
  if (directive.verb === 'say') line(arg);
  else if (directive.verb === 'status') status(arg);
  else if (directive.verb === 'ask') prompt(arg);
  else if (directive.verb === 'poll') setTimeout(() => advance(''), (parseFloat(arg) || 2) * 1000);
  else if (directive.verb === 'token') token = arg;
  else if (directive.verb === 'workspace') workspace = arg;
  else if (directive.verb === 'exit' && arg !== '0')
    line('something went wrong — reload to retry', 'error');
  if (token && workspace && !finished) complete();
}

async function advance(body) {
  go.disabled = true;
  let res;
  try {
    res = await fetch('/v1/onboard/web',
      { method: 'POST', headers: { 'x-ufo-session': session }, body: body || '' });
  } catch (err) {
    status('network error — retrying…');
    setTimeout(() => advance(body), 5000);
    return;
  }
  if (!res.ok) {
    line('error ' + res.status + ' — please try again', 'error');
    // Re-show the last prompt so a transient failure doesn't strand the session (reloading would
    // mint a new session id and restart onboarding from the email step).
    if (lastPrompt && !finished) prompt(lastPrompt);
    else go.disabled = false;
    return;
  }
  const payload = await res.json();
  for (const directive of payload.directives) handle(directive);
}

promptRow.addEventListener('submit', (event) => {
  event.preventDefault();
  const value = answer.value.trim();
  if (!value) return;
  answer.value = '';
  promptRow.style.display = 'none';
  advance(value);
});

advance('');
</script>
</body>
</html>
"""
