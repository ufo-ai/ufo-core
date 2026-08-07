"""The web presentation of the onboarding machine the terminal client drives.

`GET /login` serves a self-contained sign-in page; `POST /v1/onboard/web` advances the identical
`Onboarding` state machine (the claim row keyed by the page's generated session id) and returns the
directive lines as JSON — a second renderer, never a second machine. The page renders `say` as
transcript lines, `ask` as the next input, and `token`+`workspace` as the signed-in home card:
the member's email, their workspace URL, the terminal install line — and, when the gateway's
`debugger` directive arrived (an operator-domain email only), a form that POSTs the token to the
operator session debugger, which exchanges it for its session cookie — the bearer never rides a
URL.

A conversation the portal redirected here with (`/login?c=<uuid>`) is carried onto the card's
portal action, so the member lands on the conversation they clicked rather than a new chat. Only a
uuid shape is carried."""

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


LOGIN_PAGE = r"""<!doctype html>
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
        <button type="submit" id="go">Continue</button>
      </div>
    </form>
  </section>

  <section class="card" id="home">
    <h1>Signed in</h1>
    <div class="hint"><code id="member-email"></code> · <code id="workspace-url"></code></div>
    <form id="portal-row" method="post">
      <input type="hidden" name="token" id="portal-token">
      <button type="submit">Open your workspace</button>
    </form>
    <div class="hint" style="margin-top:10px">From your terminal:</div>
    <pre id="curl-line"></pre>
    <form id="debugger-row" method="post">
      <input type="hidden" name="token" id="debugger-token">
      <button type="submit">Session debugger</button>
    </form>
  </section>
</main>
<script>
const session = crypto.randomUUID();
const log = document.getElementById('log');
const promptRow = document.getElementById('prompt-row');
const promptLabel = document.getElementById('prompt-label');
const answer = document.getElementById('answer');
const go = document.getElementById('go');
const target = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.exec(
  new URLSearchParams(location.search).get('c') || '');
let token = null;
let workspace = null;
let debuggerUrl = null;
let email = null;
let askedEmail = false;
let finished = false;
let lastPrompt = null;

function line(text, cls) {
  const el = document.createElement('div');
  if (cls) el.className = cls;
  el.textContent = text;
  log.appendChild(el);
  return el;
}

function prompt(text) {
  if (finished) return;
  lastPrompt = text;
  promptLabel.textContent = text;
  promptRow.style.display = 'block';
  askedEmail = text.includes('email');
  answer.type = askedEmail ? 'email' : 'text';
  answer.inputMode = text.includes('code') ? 'numeric' : 'text';
  go.disabled = false;
  answer.focus();
}

function complete() {
  finished = true;
  promptRow.style.display = 'none';
  document.getElementById('member-email').textContent = email || '';
  document.getElementById('workspace-url').textContent = workspace;
  const portal = document.getElementById('portal-row');
  portal.action = workspace + '/surface/web' + (target ? '?c=' + target[0] : '');
  document.getElementById('portal-token').value = token;
  document.getElementById('curl-line').textContent =
    'curl -fsSL ' + location.origin + '/ufo | sh';
  if (debuggerUrl) {
    const row = document.getElementById('debugger-row');
    row.action = debuggerUrl;
    document.getElementById('debugger-token').value = token;
    row.style.display = 'block';
  }
  document.getElementById('home').style.display = 'block';
}

function handle(directive) {
  const arg = directive.fields[0] || '';
  if (directive.verb === 'say') line(arg);
  else if (directive.verb === 'ask') prompt(arg);
  else if (directive.verb === 'token') token = arg;
  else if (directive.verb === 'workspace') workspace = arg;
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
    res = await fetch('/v1/onboard/web',
      { method: 'POST', headers: { 'x-ufo-session': session }, body: body || '' });
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
  const value = answer.value.trim();
  if (!value) return;
  if (askedEmail) email = value.toLowerCase();
  answer.value = '';
  promptRow.style.display = 'none';
  advance(value);
});

advance('');
</script>
</body>
</html>
"""
