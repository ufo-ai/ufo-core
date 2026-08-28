import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from ufo_ext_sites.source import KIT_DIR

AUDIT_SCRIPT = Path(__file__).parents[2] / "ufo_ext_sites" / "scripts" / "audit_application.cjs"
DRAW_TIMEOUT_SECONDS = 30
CONTAINER_FIXTURE_MODE = 0o755

pytestmark = pytest.mark.docker


def _page() -> str:
    title = "Application lifecycle"
    return f"""<!doctype html>
<html><head><title>{title}</title><style>body {{ color: #111; background: #fff; }}</style></head>
<body><div id="root"></div><pre id="out"></pre>
<script>
window.__nativeSetTimeout = window.setTimeout.bind(window);
window.__nativeSetInterval = window.setInterval.bind(window);
window.__nativeClearInterval = window.clearInterval.bind(window);
window.__capturedRafCallbacks = 0;
const rawRequestAnimationFrame = window.requestAnimationFrame.bind(window);
window.requestAnimationFrame = (callback) => rawRequestAnimationFrame((time) => {{
  window.__capturedRafCallbacks += 1;
  callback(time);
}});
window.__capturedRequestAnimationFrame = window.requestAnimationFrame;
window.__missingSignalBeforeKit = window.__ufoApplicationLifecycle === undefined;
window.__rejections = [];
window.__streams = {{}};
window.addEventListener('unhandledrejection', (event) => {{
  window.__rejections.push(String(event.reason));
  if (!String(event.reason).includes('fixture rejected')) {{
    document.getElementById('out').textContent = 'BEGIN' + JSON.stringify({{
      fatal: String(event.reason),
      lifecycle: window.__ufoApplicationLifecycle?.snapshot?.(),
    }}) + 'END';
  }}
  event.preventDefault();
}});
window.addEventListener('error', (event) => {{
  document.getElementById('out').textContent = 'BEGIN' + JSON.stringify({{
    fatal: event.message,
    lifecycle: window.__ufoApplicationLifecycle?.snapshot?.(),
  }}) + 'END';
}});
window.addEventListener('message', (event) => {{
  const message = event.data || {{}};
  if (message.ufo === 'ready') {{
    window.postMessage({{
      ufo: 'init', member: {{ email: 'member@example.com', admin: true }}, agents: [],
      agentId: 'agent-1', place: {{}}, portal: location.origin,
    }}, '*');
    return;
  }}
  if (message.ufo === 'close') {{
    const timer = window.__streams[message.id];
    if (timer) window.__nativeClearInterval(timer);
    delete window.__streams[message.id];
    return;
  }}
  if (message.ufo !== 'call') return;
  if (message.path === '/unary') {{
    window.__nativeSetTimeout(() => window.postMessage({{
      ufo: 'data', id: message.id, ok: true, status: 200, body: 'unary done',
    }}, '*'), 30);
    return;
  }}
  window.postMessage({{ ufo: 'opened', id: message.id }}, '*');
  if (message.path === '/finite') {{
    window.__nativeSetTimeout(() => window.postMessage({{
      ufo: 'frame', id: message.id, event: 'message', data: 'finite done',
    }}, '*'), 20);
    window.__nativeSetTimeout(() => window.postMessage({{ ufo: 'end', id: message.id }}, '*'), 35);
    return;
  }}
  if (message.path === '/persistent') {{
    window.__nativeSetTimeout(() => window.postMessage({{
      ufo: 'frame', id: message.id, event: 'message', data: 'persistent first',
    }}, '*'), 20);
    return;
  }}
  if (message.path === '/high') {{
    window.__streams[message.id] = window.__nativeSetInterval(() => window.postMessage({{
      ufo: 'frame', id: message.id, event: 'message', data: String(performance.now()),
    }}, '*'), 10);
  }}
}});
</script>
<script type="module">
import {{ React, mountApp, useEffect, useState }} from './kit/kit.js';
const h = React.createElement;
function Fixture({{ name }}) {{
  const [text, setText] = useState(name);
  useEffect(() => {{
    if (name === 'unary') void fetch('/surface/web/unary')
      .then((response) => response.text()).then(setText);
    if (name === 'finite') {{
      const source = new EventSource('/surface/web/finite');
      source.onmessage = (event) => setText(event.data);
      return () => source.close();
    }}
    if (name === 'oneshot') setTimeout(() => setText('oneshot done'), 30);
    if (name === 'nested') setTimeout(() => setTimeout(() => setText('nested done'), 20), 20);
    if (name === 'promise') setTimeout(() => new Promise((resolve) =>
      window.__nativeSetTimeout(() => {{ setText('promise done'); resolve(); }}, 30)
    ), 10);
    if (name === 'rejection') setTimeout(() => Promise.resolve().then(() => {{
      setText('rejection rendered');
      throw new Error('fixture rejected');
    }}), 10);
    if (name === 'cancel') {{
      const timer = setTimeout(() => setText('wrong'), 30);
      clearTimeout(timer);
      setText('cancel done');
    }}
    if (name === 'interval') {{
      setText('interval');
      window.__fixtureInterval = setInterval(() => setText('interval tick'), 30000);
      return () => clearInterval(window.__fixtureInterval);
    }}
    if (name === 'poll') {{
      window.__pollBlocking = [];
      window.__pollInterval = setInterval(() => {{
        window.__pollBlocking.push(
          window.__ufoApplicationLifecycle.snapshot().blocking.interval
        );
        setText('poll ' + window.__pollBlocking.length);
      }}, 250);
      return () => clearInterval(window.__pollInterval);
    }}
    if (name === 'timeout-poll') {{
      window.__timeoutPollBlocking = [];
      const poll = () => {{
        window.__timeoutPollBlocking.push(
          window.__ufoApplicationLifecycle.snapshot().blocking.timeout
        );
        setText('timeout poll ' + window.__timeoutPollBlocking.length);
        window.__timeoutPoll = setTimeout(poll, 30000);
      }};
      window.__timeoutPoll = setTimeout(poll, 10);
      return () => clearTimeout(window.__timeoutPoll);
    }}
    if (name === 'measurement-race') {{
      window.__measurementAttempts = 0;
      const createElement = document.createElement.bind(document);
      document.createElement = (tagName, options) => {{
        const element = createElement(tagName, options);
        if (tagName !== 'canvas') return element;
        window.__measurementAttempts += 1;
        if (window.__measurementAttempts === 1) {{
          setTimeout(() => setText('measurement settled'), 0);
        }}
        return element;
      }};
      return () => {{ document.createElement = createElement; }};
    }}
    if (name === 'persistent') {{
      const source = new EventSource('/surface/web/persistent');
      source.onmessage = (event) => setText(event.data);
      window.__fixtureSource = source;
      return () => source.close();
    }}
    if (name === 'high') {{
      const source = new EventSource('/surface/web/high');
      source.onmessage = (event) => setText(event.data);
      window.__fixtureSource = source;
      return () => source.close();
    }}
    if (name === 'strict') {{
      window.__strictSetups = (window.__strictSetups || 0) + 1;
      return () => {{ window.__strictCleanups = (window.__strictCleanups || 0) + 1; }};
    }}
    if (name === 'late-contrast') setTimeout(() => setText('Late low contrast'), 50);
  }}, [name]);
  if (name === 'interaction') return h('button', {{ onClick: () =>
    setTimeout(() => setText('interaction done'), 30) }}, text);
  if (name === 'delayed-interaction') return h('div', null,
    h('span', null, text),
    h('button', {{ 'aria-label': 'Delayed', onClick: () =>
      setTimeout(() => setText('delayed-interaction done'), 200) }}, 'Run'),
    h('button', {{ 'aria-label': 'No change' }}, 'Run'),
    h('button', {{ 'aria-label': 'Busy', onClick: () =>
      {{ window.__busySource = new EventSource('/surface/web/persistent'); }} }}, 'Run'),
  );
  return h('span', name === 'late-contrast' && text.startsWith('Late') ? {{
    style: {{ color: '#aaaaaa', backgroundColor: '#ffffff' }},
  }} : {{}}, text);
}}
function Harness() {{
  const [fixture, setFixture] = useState(
    new URLSearchParams(location.search).get('fixture') || 'initial'
  );
  useEffect(() => {{
    window.__strictSetups = (window.__strictSetups || 0) + 1;
    return () => {{ window.__strictCleanups = (window.__strictCleanups || 0) + 1; }};
  }}, []);
  window.__setFixture = setFixture;
  return h(Fixture, {{ name: fixture }});
}}
mountApp(document.getElementById('root'), () => h(Harness));
</script></body></html>"""


@pytest.fixture(scope="module")
def lifecycle_results(tmp_path_factory: pytest.TempPathFactory) -> dict:
    image = os.environ.get("UFO_SANDBOX_TEST_IMAGE", "ufo-sandbox:latest")
    if shutil.which("docker") is None:
        pytest.skip("Docker executable is not available")
    tmp_path = tmp_path_factory.mktemp("application-lifecycle")
    shutil.copytree(KIT_DIR, tmp_path / "kit")
    lifecycle_bundle = next(
        candidate
        for candidate in (tmp_path / "kit").glob("*.js")
        if "application lifecycle is already installed" in candidate.read_text()
    )
    shutil.copy2(lifecycle_bundle, tmp_path / "kit" / "lifecycle-reload.js")
    shutil.copy2(AUDIT_SCRIPT, tmp_path / "audit_application.cjs")
    (tmp_path / "missing.html").write_text("<!doctype html><p>No application kit</p>")
    (tmp_path / "accepted-design.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800">'
        '<rect width="1280" height="800" fill="white"/></svg>'
    )
    page = tmp_path / "index.html"
    page.write_text(_page())
    runner = tmp_path / "runner.cjs"
    runner.write_text(
        """const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('/usr/local/lib/node_modules/playwright');
const {
  ApplicationLifecycleError,
  beginApplicationObservation,
  endApplicationObservation,
  measure,
  measureApplication,
  waitForApplicationReady,
} = require('/fixture/audit_application.cjs');
const root = '/fixture';
const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  const relative = decodeURIComponent(pathname).slice(1) || 'index.html';
  const target = path.resolve(root, relative);
  if (!target.startsWith(root + path.sep)) {
    response.writeHead(403).end();
    return;
  }
  fs.readFile(target, (error, content) => {
    if (error) {
      response.writeHead(404).end();
      return;
    }
    const type = target.endsWith('.js') ? 'text/javascript' :
      target.endsWith('.css') ? 'text/css' :
      target.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    response.writeHead(200, { 'Content-Type': type });
    response.end(content);
  });
});
server.listen(0, '127.0.0.1', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const page = await browser.newPage();
    await page.goto(`${origin}/index.html`, { waitUntil: 'load' });
    await waitForApplicationReady(page, 2000);
    const results = {};
    const checkRafReplacement = async (kind) => {
      const replacement = await page.evaluate((replacementKind) => {
        const before = window.__capturedRafCallbacks;
        window.__replacementRafCalls = 0;
        window.requestAnimationFrame = replacementKind === 'never' ?
          () => { window.__replacementRafCalls += 1; return 1; } :
          (callback) => {
            window.__replacementRafCalls += 1;
            callback(performance.now());
            return 1;
          };
        return { before, replacementKind };
      }, kind);
      const started = Date.now();
      await waitForApplicationReady(page, 500);
      const elapsed = Date.now() - started;
      return page.evaluate(({ before, elapsed }) => {
        const proof = {
          elapsed,
          capturedFrames: window.__capturedRafCallbacks - before,
          replacementCalls: window.__replacementRafCalls,
        };
        window.requestAnimationFrame = window.__capturedRequestAnimationFrame;
        return proof;
      }, { ...replacement, elapsed });
    };
    results.rafNever = await checkRafReplacement('never');
    results.rafSync = await checkRafReplacement('sync');
    const stalled = await browser.newPage();
    await stalled.setContent(`<script>
      Object.defineProperty(window, '__ufoApplicationLifecycle', {
        value: Object.freeze({
          snapshot: () => Object.freeze({
            generation: 1, epoch: 0, mounted: true, state: 'idle', revision: 1,
            blockingWork: 0, blocking: Object.freeze({}),
          }),
          afterPaint: () => new Promise(() => {}),
          beginObservation: () => 1,
          endObservation: () => new Promise(() => {}),
        }),
      });
    </script>`);
    const stalledStarted = Date.now();
    try {
      await waitForApplicationReady(stalled, 75);
      results.stalledEvaluate = { message: 'became ready' };
    } catch (error) {
      if (!(error instanceof ApplicationLifecycleError)) throw error;
      results.stalledEvaluate = {
        message: error.message,
        elapsed: Date.now() - stalledStarted,
      };
    } finally {
      await stalled.close();
    }
    results.moduleReload = await page.evaluate(async () => {
      const before = window.setTimeout;
      try {
        await import('./kit/lifecycle-reload.js');
        return { message: 'loaded', sameTimeout: window.setTimeout === before };
      } catch (error) {
        return { message: String(error), sameTimeout: window.setTimeout === before };
      }
    });
    const text = () => page.locator('#root').innerText();
    const select = async (name, timeout = 2000) => {
      const epoch = await beginApplicationObservation(page);
      await page.evaluate((fixture) => window.__setFixture(fixture), name);
      return endApplicationObservation(page, epoch, timeout);
    };
    const expectTimeout = async (name) => {
      try {
        await select(name, 250);
        results[`${name}-timeout`] = { message: 'became ready' };
      } catch (error) {
        if (!(error instanceof ApplicationLifecycleError)) throw error;
        results[`${name}-timeout`] = { message: error.message, snapshot: error.snapshot };
      }
    };
    for (const name of ['unary', 'finite', 'oneshot', 'nested', 'promise', 'rejection', 'cancel']) {
      await select(name);
      results[name] = await text();
    }
    results.intervalReady = await select('interval');
    results.interval = await text();
    results.pollReady = await select('poll');
    await page.waitForFunction(() => (window.__pollBlocking || []).length >= 2);
    results.pollBlocking = await page.evaluate(() => window.__pollBlocking);
    results.pollSettled = await waitForApplicationReady(page, 4000);
    results.poll = await text();
    results.timeoutPollReady = await select('timeout-poll');
    await page.waitForFunction(() => (window.__timeoutPollBlocking || []).length >= 1);
    results.timeoutPollBlocking = await page.evaluate(() => window.__timeoutPollBlocking);
    results.timeoutPollSettled = await waitForApplicationReady(page, 2000);
    results.timeoutPoll = await text();
    await select('measurement-race');
    results.measurementRace = await measureApplication(page, 4.5, 2000);
    results.measurementAttempts = await page.evaluate(() => window.__measurementAttempts);
    await expectTimeout('persistent');
    await page.evaluate(() => window.__fixtureSource.close());
    await waitForApplicationReady(page, 2000);
    results.persistent = await text();
    await expectTimeout('high');
    await page.evaluate(() => window.__fixtureSource.close());
    await waitForApplicationReady(page, 2000);
    await select('strict');
    results.strict = await page.evaluate(() => window.__ufoApplicationLifecycle.snapshot());
    await select('interaction');
    const epoch = await beginApplicationObservation(page);
    await page.locator('button').click();
    await endApplicationObservation(page, epoch, 2000);
    results.interaction = await text();
    await select('late-contrast');
    results.lateContrast = (await page.evaluate(measure, 4.5)).text.map((item) => item.text);
    results.rejections = await page.evaluate(() => window.__rejections);
    results.missingSignalBeforeKit = await page.evaluate(() => window.__missingSignalBeforeKit);
    results.final = await page.evaluate(() => window.__ufoApplicationLifecycle.snapshot());
    const missing = await browser.newPage();
    await missing.setContent('<p>no application kit</p>');
    try {
      await waitForApplicationReady(missing, 250);
      results.missingSignal = 'became ready';
    } catch (error) {
      if (!(error instanceof ApplicationLifecycleError)) throw error;
      results.missingSignal = error.message;
    } finally {
      await missing.close();
    }
    const runCli = (pathname, name) => new Promise((resolve, reject) => {
      const report = `/tmp/${name}.json`;
      const child = spawn(process.execPath, [
        '/fixture/audit_application.cjs', `${origin}${pathname}`, report,
        `/tmp/${name}-light.png`, `/tmp/${name}-dark.png`,
        `/tmp/${name}-interactive.html`, `/tmp/${name}-static.html`,
        `${origin}/accepted-design.svg`,
      ]);
      let stdout = '';
      let stderr = '';
      child.stdout.on('data', (data) => { stdout += data; });
      child.stderr.on('data', (data) => { stderr += data; });
      child.on('error', reject);
      child.on('close', (code) => {
        const diagnosticPath = report + '.lifecycle.json';
        resolve({
          code,
          stdout,
          stderr,
          diagnostic: fs.existsSync(diagnosticPath) ?
            JSON.parse(fs.readFileSync(diagnosticPath, 'utf8')) : null,
        });
      });
    });
    results.cliMissing = await runCli('/missing.html', 'missing');
    results.cliPersistent = await runCli('/index.html?fixture=persistent', 'persistent');
    const fatal = await page.locator('#out').textContent();
    if (fatal) throw new Error(fatal);
    process.stdout.write('BEGIN' + JSON.stringify(results) + 'END');
  } finally {
    await browser.close();
    server.close();
  }
});
"""
    )
    tmp_path.chmod(CONTAINER_FIXTURE_MODE)
    drawn = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "node",
            "-v",
            f"{tmp_path}:/fixture:ro",
            image,
            "/fixture/runner.cjs",
        ],
        capture_output=True,
        text=True,
        timeout=DRAW_TIMEOUT_SECONDS,
        check=False,
    )
    if drawn.returncode != 0:
        pytest.fail((drawn.stderr or drawn.stdout)[-2000:])
    found = re.search(r"BEGIN(.*?)END", drawn.stdout, re.S)
    if not found:
        pytest.fail((drawn.stderr or drawn.stdout)[-2000:])
    return json.loads(html.unescape(found.group(1)))


def test_terminal_application_work_reaches_stable_render(lifecycle_results: dict) -> None:
    assert lifecycle_results["unary"] == "unary done"
    assert lifecycle_results["finite"] == "finite done"
    assert lifecycle_results["oneshot"] == "oneshot done"
    assert lifecycle_results["nested"] == "nested done"
    assert lifecycle_results["promise"] == "promise done"
    assert lifecycle_results["rejection"] == "rejection rendered"
    assert lifecycle_results["cancel"] == "cancel done"
    assert lifecycle_results["interaction"] == "interaction done"
    assert lifecycle_results["final"]["blockingWork"] == 0
    assert lifecycle_results["final"]["state"] == "idle"


def test_interaction_audit_waits_for_delayed_visible_state(tmp_path: Path) -> None:
    image = os.environ.get("UFO_SANDBOX_TEST_IMAGE", "ufo-sandbox:latest")
    if shutil.which("docker") is None:
        pytest.skip("Docker executable is not available")
    shutil.copytree(KIT_DIR, tmp_path / "kit")
    shutil.copy2(AUDIT_SCRIPT, tmp_path / "audit_application.cjs")
    (tmp_path / "index.html").write_text(_page())
    (tmp_path / "runner.cjs").write_text(
        """const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('/usr/local/lib/node_modules/playwright');
const { interactionAudit } = require('/fixture/audit_application.cjs');
const root = '/fixture';
const server = http.createServer((request, response) => {
  const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname).slice(1) ||
    'index.html';
  const target = path.resolve(root, relative);
  if (!target.startsWith(root + path.sep)) {
    response.writeHead(403).end();
    return;
  }
  fs.readFile(target, (error, content) => {
    if (error) {
      response.writeHead(404).end();
      return;
    }
    const type = target.endsWith('.js') ? 'text/javascript' :
      target.endsWith('.css') ? 'text/css' : 'text/html';
    response.writeHead(200, { 'Content-Type': type });
    response.end(content);
  });
});
server.listen(0, '127.0.0.1', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const started = Date.now();
    const result = await interactionAudit(
      browser, `${origin}/index.html?fixture=delayed-interaction`
    );
    process.stdout.write(JSON.stringify({ elapsed: Date.now() - started, result }));
  } finally {
    await browser.close();
    server.close();
  }
});
"""
    )
    tmp_path.chmod(CONTAINER_FIXTURE_MODE)
    drawn = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "node",
            "-v",
            f"{tmp_path}:/fixture:ro",
            image,
            "/fixture/runner.cjs",
        ],
        capture_output=True,
        text=True,
        timeout=DRAW_TIMEOUT_SECONDS,
        check=False,
    )
    assert drawn.returncode == 0, drawn.stderr or drawn.stdout
    proof = json.loads(drawn.stdout)
    result = proof["result"]
    assert proof["elapsed"] < 10_000
    assert [control["name"] for control in result["controls"]] == [
        "Delayed",
        "No change",
        "Busy",
    ]
    assert [control["name"] for control in result["successes"]] == ["Delayed"]
    assert any("delayed-interaction done" in part for state in result["states"] for part in state)
    assert result["console"] == []


def test_nonterminal_application_work_fails_closed(lifecycle_results: dict) -> None:
    for name, kind in (("persistent", "stream"), ("high", "stream")):
        failure = lifecycle_results[f"{name}-timeout"]
        assert failure["message"] == "application lifecycle did not become ready"
        assert failure["snapshot"]["blockingWork"] == 1
        assert failure["snapshot"]["blocking"][kind] == 1
    assert lifecycle_results["persistent"] == "persistent first"


def test_a_polling_page_reaches_ready_between_its_ticks(lifecycle_results: dict) -> None:
    for name in ("intervalReady", "pollReady", "pollSettled"):
        ready = lifecycle_results[name]
        assert ready["state"] == "idle"
        assert ready["blockingWork"] == 0
        assert ready["blocking"]["interval"] == 0
    assert lifecycle_results["interval"] == "interval"
    assert lifecycle_results["pollBlocking"][:2] == [1, 1]
    assert lifecycle_results["poll"].startswith("poll ")


def test_a_recursive_timeout_reaches_ready_between_callbacks(lifecycle_results: dict) -> None:
    for name in ("timeoutPollReady", "timeoutPollSettled"):
        ready = lifecycle_results[name]
        assert ready["state"] == "idle"
        assert ready["blockingWork"] == 0
        assert ready["blocking"]["timeout"] == 0
    assert lifecycle_results["timeoutPollBlocking"] == [1]
    assert lifecycle_results["timeoutPoll"] == "timeout poll 1"


def test_measurement_restarts_after_application_work(lifecycle_results: dict) -> None:
    assert lifecycle_results["measurementAttempts"] >= 2
    assert lifecycle_results["measurementRace"]["renderedText"] == "measurement settled"


def test_strict_rejection_and_late_contrast_remain_visible(lifecycle_results: dict) -> None:
    assert lifecycle_results["strict"]["generation"] == 1
    assert lifecycle_results["strict"]["mounted"] is True
    assert lifecycle_results["strict"]["state"] == "idle"
    assert lifecycle_results["rejections"] == ["Error: fixture rejected"]
    assert lifecycle_results["missingSignalBeforeKit"] is True
    assert lifecycle_results["missingSignal"] == "application lifecycle signal is missing"
    assert "Late low contrast" in lifecycle_results["lateContrast"]
    assert (
        "application lifecycle is already installed" in lifecycle_results["moduleReload"]["message"]
    )
    assert lifecycle_results["moduleReload"]["sameTimeout"] is True


def test_readiness_uses_captured_frames_and_a_node_deadline(lifecycle_results: dict) -> None:
    for name in ("rafNever", "rafSync"):
        proof = lifecycle_results[name]
        assert proof["capturedFrames"] >= 2
        assert proof["replacementCalls"] == 0
        assert proof["elapsed"] < 500
    assert lifecycle_results["stalledEvaluate"]["message"] == (
        "application lifecycle did not become ready"
    )
    assert lifecycle_results["stalledEvaluate"]["elapsed"] < 500


def test_full_cli_fails_quietly_with_private_lifecycle_artifact(lifecycle_results: dict) -> None:
    missing = lifecycle_results["cliMissing"]
    persistent = lifecycle_results["cliPersistent"]
    assert (missing["code"], missing["stdout"], missing["stderr"]) == (3, "", "")
    assert missing["diagnostic"] == {
        "code": "application_lifecycle",
        "reason": "application lifecycle signal is missing",
        "snapshot": None,
    }
    assert (persistent["code"], persistent["stdout"], persistent["stderr"]) == (3, "", "")
    assert persistent["diagnostic"]["code"] == "application_lifecycle"
    assert persistent["diagnostic"]["reason"] == "application lifecycle did not become ready"
    assert persistent["diagnostic"]["snapshot"]["blocking"]["stream"] == 1


def test_success_removes_a_stale_lifecycle_artifact(tmp_path: Path) -> None:
    image = os.environ.get("UFO_SANDBOX_TEST_IMAGE", "ufo-sandbox:latest")
    if shutil.which("docker") is None:
        pytest.skip("Docker executable is not available")
    shutil.copytree(KIT_DIR, tmp_path / "kit")
    shutil.copy2(AUDIT_SCRIPT, tmp_path / "audit_application.cjs")
    (tmp_path / "index.html").write_text(_page())
    (tmp_path / "missing.html").write_text("<!doctype html><p>No application kit</p>")
    (tmp_path / "accepted-design.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800">'
        '<rect width="1280" height="800" fill="white"/></svg>'
    )
    runner = tmp_path / "stale-runner.cjs"
    runner.write_text(
        """const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');
const root = '/fixture';
const server = http.createServer((request, response) => {
  const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname).slice(1) ||
    'index.html';
  const target = path.resolve(root, relative);
  if (!target.startsWith(root + path.sep)) {
    response.writeHead(403).end();
    return;
  }
  fs.readFile(target, (error, content) => {
    if (error) {
      response.writeHead(404).end();
      return;
    }
    const type = target.endsWith('.js') ? 'text/javascript' :
      target.endsWith('.css') ? 'text/css' :
      target.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    response.writeHead(200, { 'Content-Type': type });
    response.end(content);
  });
});
server.listen(0, '127.0.0.1', async () => {
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const report = '/tmp/reused.json';
    const run = (pathname) => new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [
        '/fixture/audit_application.cjs', `${origin}${pathname}`, report,
        '/tmp/reused-light.png', '/tmp/reused-dark.png',
        '/tmp/reused-interactive.html', '/tmp/reused-static.html',
        `${origin}/accepted-design.svg`,
      ]);
      let stdout = '';
      let stderr = '';
      child.stdout.on('data', (data) => { stdout += data; });
      child.stderr.on('data', (data) => { stderr += data; });
      child.on('error', reject);
      child.on('close', (code) => resolve({ code, stdout, stderr }));
    });
    const failed = await run('/missing.html');
    const afterFailure = fs.existsSync(report + '.lifecycle.json');
    const passed = await run('/index.html?fixture=interval');
    process.stdout.write('BEGIN' + JSON.stringify({
      failed,
      afterFailure,
      passed,
      afterSuccess: fs.existsSync(report + '.lifecycle.json'),
    }) + 'END');
  } finally {
    server.close();
  }
});
"""
    )
    tmp_path.chmod(CONTAINER_FIXTURE_MODE)
    drawn = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "node",
            "-v",
            f"{tmp_path}:/fixture:ro",
            image,
            "/fixture/stale-runner.cjs",
        ],
        capture_output=True,
        text=True,
        timeout=DRAW_TIMEOUT_SECONDS,
        check=False,
    )
    if drawn.returncode != 0:
        pytest.fail((drawn.stderr or drawn.stdout)[-2000:])
    found = re.search(r"BEGIN(.*?)END", drawn.stdout, re.S)
    if not found:
        pytest.fail((drawn.stderr or drawn.stdout)[-2000:])
    result = json.loads(html.unescape(found.group(1)))
    assert (result["failed"]["code"], result["failed"]["stdout"], result["failed"]["stderr"]) == (
        3,
        "",
        "",
    )
    assert result["afterFailure"] is True
    assert result["passed"]["code"] == 0
    assert result["passed"]["stderr"] == ""
    assert result["afterSuccess"] is False
