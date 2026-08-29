"""The audit's contrast measurement proved against a REAL browser drawing the REAL kit stylesheet.

A computed colour carries whatever syntax the engine chose, so only an engine can settle what the
audit reads. The shipped `measure` is lifted out of `audit_application.cjs` by brace match and run
as it ships: the test cannot drift from the code it proves. The kit's accent tokens resolve through
`color-mix()`, which Chromium computes to `color(srgb ...)`, and a reader that only knows `rgb()`
drops those colours — reporting legible accent text as a 1:1 failure and dropping unreadable faint
text out of the audit entirely.

The module is preconditioned on a browser that draws: a machine carrying chromium is not a machine
that can drive it, and a required gate must not turn one that cannot into a failure of the audit's
measurements. Nothing here raises at collection.
"""

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from ufo_ext_sites.application_audit import (
    DESIGN_VISIBLE_TEXT_MAX_CHARS,
    ApplicationAuditContract,
    ApplicationAuditFact,
    ApplicationAuditReport,
    audit_application,
)
from ufo_ext_sites.source import KIT_DIR
from ufo_testsupport.browser import MISSING_BROWSER_REASON, chrome_for_testing, headless_flags
from websockets.sync.client import connect

AUDIT_SCRIPT = Path(__file__).parents[2] / "ufo_ext_sites" / "scripts" / "audit_application.cjs"
STRICT_FLOOR = 4.5
ACCENT_LABEL = "Dispatch task"
FAINT_LABEL = "Secondary caption"
KIT_BADGE_LABEL = "Waiting"
KIT_STAT_LABEL = "Open issues"
KIT_CHART_FROM = "Jul 1"
KIT_CHART_TO = "Jul 31"
AUTHOR_QUIET_LABEL = "Author quiet prose"

pytestmark = pytest.mark.skipif(chrome_for_testing() is None, reason=MISSING_BROWSER_REASON)


def _function_source(name: str) -> str:
    text = AUDIT_SCRIPT.read_text()
    start = text.index(f"function {name}(")
    if text[start - 6 : start] == "async ":
        start -= 6
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"audit_application.cjs has no closed {name} function")


def _measure_source() -> str:
    return _function_source("measure")


def _page() -> str:
    return f"""<link rel="stylesheet" href="kit/kit.css">
<body style="background-color: var(--color-surface)">
  <button style="background-color: var(--color-link); color: var(--color-surface);
                 font-size: 13px; font-weight: 600; border: none; padding: 6px 14px">
    {ACCENT_LABEL}
  </button>
  <span style="color: var(--color-ink-faint); font-size: 12px">{FAINT_LABEL}</span>
  <div id="root"></div>
</body>
<pre id="out"></pre>
<script>
{_measure_source()}
</script>
<script type="module">
import {{
  Badge,
  ChartBars,
  Stat,
  StatHeader,
  StatLabel,
  StatValue,
  jsx,
  jsxs,
  mountApp,
}} from "./kit/kit.js";

window.postMessage({{
  ufo: "init",
  member: {{ email: "member@example.com", admin: false }},
  agents: [],
  agentId: "fixture-agent",
  banded: false,
  place: {{}},
  portal: location.origin,
}}, "*");
mountApp(document.getElementById("root"), () =>
  jsxs("main", {{
    className: "flex flex-col gap-6xl p-6xl",
    children: [
      jsx(Badge, {{ children: "{KIT_BADGE_LABEL}" }}),
      jsxs(Stat, {{
        children: [
          jsx(StatHeader, {{ children: jsx(StatLabel, {{ children: "{KIT_STAT_LABEL}" }}) }}),
          jsx(StatValue, {{ children: "42" }}),
        ],
      }}),
      jsx(ChartBars, {{
        label: "Issues opened by day",
        bars: [{{ value: 4, tone: "primary" }}, {{ value: 6, tone: "secondary" }}],
        from: "{KIT_CHART_FROM}",
        to: "{KIT_CHART_TO}",
      }}),
      jsx("p", {{ className: "text-ink-quiet", children: "{AUTHOR_QUIET_LABEL}" }}),
    ],
  }})
);

(async () => {{
  for (let attempt = 0; attempt < 60; attempt += 1) {{
    if (document.querySelector('[data-slot="chart-caption"]')) break;
    await new Promise(requestAnimationFrame);
  }}
  document.getElementById('out').textContent =
    'BEGIN' + JSON.stringify(await measure({STRICT_FLOOR})) + 'END';
}})().catch((error) => {{ document.getElementById('out').textContent = 'ERROR' + error.stack; }});
</script>
"""


def _browser_output(page: Path) -> str:
    profile = page.parent / "chrome-profile"
    log_path = page.parent / "chrome.log"
    server = None
    server_thread = None
    url = page.as_uri()
    if (page.parent / "kit").is_dir():
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=page.parent)
        )
        server_thread = Thread(target=server.serve_forever)
        server_thread.start()
        url = f"http://127.0.0.1:{server.server_port}/{page.name}"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                str(chrome_for_testing()),
                *headless_flags(sys.platform),
                "--window-size=1280,800",
                "--remote-debugging-port=0",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            port = None
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
                    break
                except (FileNotFoundError, IndexError, ValueError):
                    time.sleep(0.05)
            if port is None:
                raise RuntimeError(log_path.read_text(errors="replace")[-2000:])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as response:
                targets = json.loads(response.read())
            target = next(item for item in targets if item["type"] == "page")
            with connect(target["webSocketDebuggerUrl"], open_timeout=5) as socket:
                call_id = 0

                def call(method: str, params: dict | None = None) -> dict:
                    nonlocal call_id
                    call_id += 1
                    socket.send(
                        json.dumps({"id": call_id, "method": method, "params": params or {}})
                    )
                    while True:
                        answer = json.loads(socket.recv())
                        if answer.get("id") != call_id:
                            continue
                        if "error" in answer:
                            raise RuntimeError(str(answer["error"]))
                        return answer["result"]

                call("Page.enable")
                call("Runtime.enable")
                call("Page.navigate", {"url": url})
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    result = call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.getElementById('out')?.textContent || ''",
                            "returnByValue": True,
                        },
                    )
                    output = str(result["result"].get("value", ""))
                    if output.startswith("BEGIN") and output.endswith("END"):
                        return output
                    if output.startswith("ERROR"):
                        raise RuntimeError(output)
                    time.sleep(0.05)
                raise TimeoutError("application audit browser probe did not finish")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if server is not None and server_thread is not None:
                server.shutdown()
                server.server_close()
                server_thread.join()


@pytest.fixture
def measured(tmp_path: Path) -> dict:
    shutil.copytree(KIT_DIR, tmp_path / "kit")
    page = tmp_path / "page.html"
    page.write_text(_page())
    output = _browser_output(page)
    return json.loads(output.removeprefix("BEGIN").removesuffix("END"))


def test_accent_text_on_its_own_button_clears_the_floor(measured: dict) -> None:
    reported = [item["text"] for item in measured["text"]]
    assert ACCENT_LABEL not in reported


def test_faint_text_reaches_the_audit_and_fails(measured: dict) -> None:
    faint = [item for item in measured["text"] if item["text"] == FAINT_LABEL]
    assert faint, "faint text left the audit instead of failing it"
    assert faint[0]["ratio"] < STRICT_FLOOR


def test_real_kit_quiet_labels_carry_exact_slot_evidence(measured: dict) -> None:
    evidence = {item["text"]: item["slot"] for item in measured["text"]}

    assert evidence[KIT_BADGE_LABEL] == "badge"
    assert evidence[KIT_STAT_LABEL] == "stat-label"
    assert evidence[KIT_CHART_FROM] == "chart-caption"
    assert evidence[AUTHOR_QUIET_LABEL] is None


def test_real_kit_quiet_labels_pass_but_author_quiet_prose_fails(measured: dict) -> None:
    kit_labels = {KIT_BADGE_LABEL, KIT_STAT_LABEL, KIT_CHART_FROM}
    quiet = [item for item in measured["text"] if item["text"] in kit_labels]
    authored = next(item for item in measured["text"] if item["text"] == AUTHOR_QUIET_LABEL)
    regions = (
        {"name": "summary", "left": 0, "top": 0, "width": 0.5, "height": 1},
        {"name": "detail", "left": 0.5, "top": 0, "width": 0.5, "height": 1},
    )

    def report(text: list[dict]) -> ApplicationAuditReport:
        return ApplicationAuditReport.model_validate(
            {
                "designRegions": regions,
                "views": [
                    {
                        "scheme": scheme,
                        "width": width,
                        "textChecked": measured["textChecked"],
                        "text": text,
                        "documentWidth": width,
                        "clipped": [],
                        "console": [],
                        "aboveFoldText": measured["aboveFoldText"],
                        "regions": regions,
                    }
                    for width in (1440, 390)
                    for scheme in ("light", "dark")
                ],
                "interaction": {
                    "controls": [
                        {"selector": "#first", "name": "First"},
                        {"selector": "#second", "name": "Second"},
                    ],
                    "successes": [
                        {"selector": "#first", "name": "First"},
                        {"selector": "#second", "name": "Second"},
                    ],
                    "console": [],
                },
            }
        )

    assert "contrast" not in {issue.code for issue in audit_application(report(quiet)).issues}
    assert "contrast" in {issue.code for issue in audit_application(report([authored])).issues}


def test_every_visible_leaf_is_counted(measured: dict) -> None:
    assert measured["textChecked"] >= 2


def test_painted_text_reconstructs_inline_and_block_facts(tmp_path: Path) -> None:
    page = tmp_path / "painted-text.html"
    page.write_text(
        f"""<!doctype html>
<style>html,body{{margin:0;width:1280px;min-height:800px;font:16px sans-serif}}</style>
<body>
  <p><span>$</span>1,204</p>
  <p><span>42</span>%</p>
  <p><span>Status</span>: ready</p>
  <p><span>Normal</span> <span>spaced</span> words</p>
  <div>First block</div><div>Second block</div>
  <p><span>売上</span><span>合計</span></p>
  <pre id="out"></pre>
<script>
{_measure_source()}
(async () => {{
  const measured = await measure({STRICT_FLOOR});
  document.getElementById('out').textContent =
    'BEGIN' + JSON.stringify(measured) + 'END';
}})().catch((error) => {{ document.getElementById('out').textContent = 'ERROR' + error.stack; }});
</script>
</body>"""
    )
    output = _browser_output(page)
    measured = json.loads(output.removeprefix("BEGIN").removesuffix("END"))

    for expected in (
        "$1,204",
        "42%",
        "Status: ready",
        "Normal spaced words",
        "First block Second block",
        "売上合計",
    ):
        assert expected in measured["renderedText"]
        assert expected in measured["aboveFoldText"]

    regions = (
        {"name": "summary", "left": 0, "top": 0, "width": 0.5, "height": 1},
        {"name": "detail", "left": 0.5, "top": 0, "width": 0.5, "height": 1},
    )
    report = ApplicationAuditReport.model_validate(
        {
            "designRegions": regions,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": measured["textChecked"],
                    "text": measured["text"],
                    "documentWidth": width,
                    "clipped": [],
                    "console": [],
                    "aboveFoldText": measured["aboveFoldText"],
                    "regions": regions,
                }
                for width in (1440, 390)
                for scheme in ("light", "dark")
            ],
            "interaction": {
                "controls": [
                    {"selector": "button:nth-of-type(1)", "name": "One"},
                    {"selector": "button:nth-of-type(2)", "name": "Two"},
                ],
                "successes": [
                    {"selector": "button:nth-of-type(1)", "name": "One"},
                    {"selector": "button:nth-of-type(2)", "name": "Two"},
                ],
                "states": [[measured["renderedText"]]],
                "console": [],
            },
        }
    )
    contract = ApplicationAuditContract(
        facts=(ApplicationAuditFact(label="run rate", alternatives=("$1,204",)),)
    )

    assert audit_application(report, contract).issues == ()


def test_design_text_audit_runs_in_the_svg_document_namespace(tmp_path: Path) -> None:
    page = tmp_path / "design.svg"
    page.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800">
<defs><linearGradient id="ink"><stop stop-color="black"/></linearGradient></defs>
<g data-app-region="queue">
  <text y="20" fill="rgba(0, 0, 0, 0)">transparent-copy</text>
  <text y="50" fill="none" stroke="black" stroke-width="1">stroke-copy</text>
</g>
<g data-app-region="detail">
  <text x="640" y="20" fill="url(#ink)">server-copy</text>
</g>
<text id="out" y="790"></text>
<script><![CDATA[
{_function_source("visibleRegionText")}
(async () => {{
  const regions = Array.from(document.querySelectorAll('[data-app-region]'));
  const result = await visibleRegionText(regions, 0.15, {DESIGN_VISIBLE_TEXT_MAX_CHARS});
  document.getElementById('out').textContent = 'BEGIN' + JSON.stringify(result) + 'END';
}})().catch((error) => {{ document.getElementById('out').textContent = 'ERROR' + error.stack; }});
]]></script>
</svg>"""
    )

    output = _browser_output(page)

    assert json.loads(output.removeprefix("BEGIN").removesuffix("END")) == [
        "stroke-copy",
        "server-copy",
    ]


def test_text_truth_matches_real_chromium_paint_and_intersection(tmp_path: Path) -> None:
    cases = (
        ("html-default", "<span>html-default</span>", True, True),
        ("display-none", '<span style="display:none">display-none</span>', False, False),
        (
            "visibility-hidden",
            '<span style="visibility:hidden">visibility-hidden</span>',
            False,
            False,
        ),
        (
            "content-hidden",
            '<span style="content-visibility:hidden">content-hidden</span>',
            False,
            False,
        ),
        ("opacity-hidden", '<span style="opacity:.14">opacity-hidden</span>', False, False),
        ("opacity-boundary", '<span style="opacity:.15">opacity-boundary</span>', True, True),
        (
            "ancestor-opacity",
            '<span style="opacity:.1"><b>ancestor-opacity</b></span>',
            False,
            False,
        ),
        (
            "html-transparent",
            '<span style="-webkit-text-fill-color:transparent">html-transparent</span>',
            False,
            False,
        ),
        (
            "html-transparent-color",
            '<span style="color:transparent">html-transparent-color</span>',
            False,
            False,
        ),
        (
            "html-transparent-stroke",
            '<span style="color:transparent;-webkit-text-stroke:1px transparent">'
            "html-transparent-stroke</span>",
            False,
            False,
        ),
        (
            "html-stroke-only",
            '<span style="-webkit-text-fill-color:transparent;-webkit-text-stroke:1px black">'
            "html-stroke-only</span>",
            True,
            True,
        ),
        (
            "svg-default",
            '<svg width="180" height="22"><text x="1" y="16">svg-default</text></svg>',
            True,
            True,
        ),
        (
            "svg-fill-none",
            '<svg width="180" height="22"><text x="1" y="16" fill="none">'
            "svg-fill-none</text></svg>",
            False,
            False,
        ),
        (
            "svg-fill-transparent",
            '<svg width="180" height="22"><text x="1" y="16" fill="transparent">'
            "svg-fill-transparent</text></svg>",
            False,
            False,
        ),
        (
            "svg-fill-opacity",
            '<svg width="180" height="22"><text x="1" y="16" fill-opacity="0">'
            "svg-fill-opacity</text></svg>",
            False,
            False,
        ),
        (
            "svg-transparent-stroke",
            '<svg width="180" height="22"><text x="1" y="16" fill="none" '
            'stroke="transparent" stroke-width="2">svg-transparent-stroke</text></svg>',
            False,
            False,
        ),
        (
            "svg-inherited-zero-paint",
            '<svg width="180" height="22"><g fill="none" stroke="black" stroke-opacity="0">'
            '<text x="1" y="16">svg-inherited-zero-paint</text></g></svg>',
            False,
            False,
        ),
        (
            "svg-stroke-only",
            '<svg width="180" height="22"><text x="1" y="16" fill="none" stroke="black" '
            'stroke-width="1">svg-stroke-only</text></svg>',
            True,
            True,
        ),
        (
            "svg-fill-server",
            '<svg width="180" height="22"><defs><linearGradient id="paint"><stop '
            'stop-color="black"/></linearGradient></defs><text x="1" y="16" fill="url(#paint)">'
            "svg-fill-server</text></svg>",
            True,
            True,
        ),
        (
            "clipped-html",
            '<span style="position:absolute;left:120px">clipped-html</span>',
            False,
            False,
        ),
        (
            "clipped-svg",
            '<svg width="100" height="22"><defs><clipPath id="cut"><rect width="2" height="22"/>'
            '</clipPath></defs><text x="20" y="16" clip-path="url(#cut)">clipped-svg</text></svg>',
            False,
            False,
        ),
        (
            "opaque-overlay",
            '<span>opaque-overlay</span><span style="position:absolute;left:0;top:0;width:100%;'
            'height:100%;background:white;z-index:2"></span>',
            False,
            False,
        ),
        (
            "translucent-overlay",
            '<span>translucent-overlay</span><span style="position:absolute;left:0;top:0;'
            'width:100%;height:100%;background:rgba(0,0,0,.03);z-index:2"></span>',
            True,
            True,
        ),
        (
            "gradient-text",
            '<span style="background:linear-gradient(90deg,black,gray);background-clip:text;'
            '-webkit-background-clip:text;-webkit-text-fill-color:transparent">gradient-text</span>',
            True,
            True,
        ),
        (
            "viewport-edge",
            '<span style="font-size:40px;line-height:40px">viewport-edge</span>',
            True,
            True,
        ),
        (
            "offscreen-html",
            '<span style="position:fixed;left:-10000px">offscreen-html</span>',
            False,
            False,
        ),
        (
            "offscreen-svg",
            '<svg width="100" height="22" overflow="hidden"><text x="-1000" y="16">'
            "offscreen-svg</text></svg>",
            False,
            False,
        ),
        ("below-fold", "<span>below-fold</span>", True, False),
    )
    regions = []
    for index, (name, markup, _, _above_fold) in enumerate(cases):
        left = 10 + index % 4 * 250
        top = 10 + index // 4 * 45
        if name == "below-fold":
            top = 1000
        elif name == "viewport-edge":
            top = 640
        overflow = "overflow:hidden;width:100px" if name == "clipped-html" else "width:220px"
        regions.append(
            f'<section id="{name}" data-app-region="{name}" '
            f'style="position:absolute;left:{left}px;top:{top}px;height:24px;{overflow}">'
            f"{markup}</section>"
        )
        assert (name != "below-fold") is (top < 800)
    page = tmp_path / "text-truth.html"
    page.write_text(
        f"""<!doctype html><style>html,body{{margin:0;width:1280px;min-height:1100px}}</style>
{"".join(regions)}
<pre id="out"></pre>
<script>
{_measure_source()}
{_function_source("visibleRegionText")}
(async () => {{
  const regionText = {{}};
  const regions = Array.from(document.querySelectorAll('[data-app-region]'));
  const texts = await visibleRegionText(regions, 0.15, {DESIGN_VISIBLE_TEXT_MAX_CHARS});
  for (let index = 0; index < regions.length; index += 1) {{
    regionText[regions[index].id] = texts[index];
  }}
  const measured = await measure({STRICT_FLOOR});
  document.getElementById('out').textContent =
    'BEGIN' + JSON.stringify({{ measured, regionText }}) + 'END';
}})();
</script>"""
    )
    output = _browser_output(page)
    result = json.loads(output.removeprefix("BEGIN").removesuffix("END"))
    rendered = set(result["measured"]["renderedText"].split())
    above_fold = set(result["measured"]["aboveFoldText"].split())
    for name, _, expected_rendered, expected_above_fold in cases:
        assert (name in rendered) is expected_rendered, name
        assert (name in above_fold) is expected_above_fold, name
        assert (result["regionText"][name] == name) is expected_rendered, name
