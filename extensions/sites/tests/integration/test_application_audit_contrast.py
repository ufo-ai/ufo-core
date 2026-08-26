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

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ufo_ext_sites.source import KIT_DIR
from ufo_testsupport.browser import MISSING_BROWSER_REASON, chrome_for_testing, headless_flags

AUDIT_SCRIPT = Path(__file__).parents[2] / "ufo_ext_sites" / "scripts" / "audit_application.cjs"
STRICT_FLOOR = 4.5
DRAW_TIMEOUT_SECONDS = 60
ACCENT_LABEL = "Dispatch task"
FAINT_LABEL = "Secondary caption"

pytestmark = pytest.mark.skipif(chrome_for_testing() is None, reason=MISSING_BROWSER_REASON)


def _measure_source() -> str:
    text = AUDIT_SCRIPT.read_text()
    start = text.index("function measure(floor)")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError("audit_application.cjs has no closed measure function")


def _page() -> str:
    return f"""<link rel="stylesheet" href="kit.css">
<body style="background-color: var(--color-surface)">
  <button style="background-color: var(--color-link); color: var(--color-surface);
                 font-size: 13px; font-weight: 600; border: none; padding: 6px 14px">
    {ACCENT_LABEL}
  </button>
  <span style="color: var(--color-ink-faint); font-size: 12px">{FAINT_LABEL}</span>
</body>
<pre id="out"></pre>
<script>
{_measure_source()}
document.getElementById('out').textContent =
  'BEGIN' + JSON.stringify(measure({STRICT_FLOOR})) + 'END';
</script>
"""


@pytest.fixture
def measured(tmp_path: Path) -> dict:
    shutil.copy(KIT_DIR / "kit.css", tmp_path / "kit.css")
    page = tmp_path / "page.html"
    page.write_text(_page())
    drawn = subprocess.run(
        [
            str(chrome_for_testing()),
            *headless_flags(sys.platform),
            "--virtual-time-budget=5000",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=DRAW_TIMEOUT_SECONDS,
        check=False,
    )
    found = re.search(r"BEGIN(.*?)END", drawn.stdout, re.S)
    if not found:
        pytest.skip(MISSING_BROWSER_REASON)
    return json.loads(html.unescape(found.group(1)))


def test_accent_text_on_its_own_button_clears_the_floor(measured: dict) -> None:
    reported = [item["text"] for item in measured["text"]]
    assert ACCENT_LABEL not in reported


def test_faint_text_reaches_the_audit_and_fails(measured: dict) -> None:
    faint = [item for item in measured["text"] if item["text"] == FAINT_LABEL]
    assert faint, "faint text left the audit instead of failing it"
    assert faint[0]["ratio"] < STRICT_FLOOR


def test_every_visible_leaf_is_counted(measured: dict) -> None:
    assert measured["textChecked"] >= 2
