"""The BUA engine end to end against a REAL Chrome over CDP — the live proof the shell never gave.

A headless Chrome is launched with `--remote-debugging-port`; `BROWSER_CDP_URL` points a small
test-local cdp provider at it, and one `BuaSurface` connects and drives a real page: navigate →
read_page → get_page_text → find → a `computer` screenshot and click → tabs create/context/close.
Nothing here
is faked — the CDP protocol handling (WebSocket transport, DOMSnapshot + accessibility join, input
synthesis, settle) is exercised against Chrome itself. A missing Chrome binary fails the required
integration gate and skips an optional local run."""

import base64
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from ufo_ext_browser.bua.backend import BuaSurface
from ufo_testsupport.browser import MISSING_BROWSER_REASON, chrome_for_testing, headless_flags
from ufo_testsupport.plugin import integration_dependency_available

from ufo.browser import CdpEndpoint, CdpLease, FileBytes

READY_TIMEOUT_S = 60.0
READY_POLL_S = 0.1

PROBE_HTML = (
    "<!doctype html><html><head><title>BUA Probe</title></head>"
    "<body><h1>Probe Heading</h1>"
    '<a href="https://example.com/next">Example Link</a>'
    '<button id="go">Click Me</button>'
    '<input type="text" id="field"></body></html>'
)
PROBE_URL = f"data:text/html,{quote(PROBE_HTML)}"


CHROME = chrome_for_testing()
pytestmark = pytest.mark.skipif(
    not integration_dependency_available(CHROME is not None, MISSING_BROWSER_REASON),
    reason=MISSING_BROWSER_REASON,
)


@pytest.fixture
def chrome_cdp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    assert CHROME is not None
    profile = tmp_path / "chrome-profile"
    chrome_log = tmp_path / "chrome.log"
    with chrome_log.open("wb") as log:
        process = subprocess.Popen(
            [
                CHROME,
                *headless_flags(sys.platform),
                "--remote-debugging-port=0",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=log,
            stderr=log,
        )
        launched = time.monotonic()
        deadline = launched + READY_TIMEOUT_S
        url = None
        port_file_at = None
        try:
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
                    if port_file_at is None:
                        port_file_at = time.monotonic() - launched
                    candidate = f"http://127.0.0.1:{port}"
                    if httpx.get(f"{candidate}/json/version", timeout=0.5).status_code == 200:
                        url = candidate
                        break
                except (FileNotFoundError, IndexError, ValueError, httpx.HTTPError):
                    pass
                time.sleep(READY_POLL_S)
            if url is None:
                details = chrome_log.read_text(errors="replace").strip()
                written = "never" if port_file_at is None else f"{port_file_at:.1f}s"
                raise RuntimeError(
                    f"Chrome CDP startup failed after {time.monotonic() - launched:.1f}s "
                    f"(exit {process.poll()}, DevToolsActivePort written {written}): "
                    f"{details[-2000:]}"
                )
            monkeypatch.setenv("BROWSER_CDP_URL", url)
            yield url
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@dataclass(frozen=True)
class _StaticLease:
    endpoint_: CdpEndpoint

    async def endpoint(self) -> CdpEndpoint:
        return self.endpoint_

    async def token(self) -> str:
        return self.endpoint_.url

    async def place_file(self, path: str, read: FileBytes) -> str:
        return path

    async def download_dir(self) -> str:
        return tempfile.gettempdir()

    async def fetch_download(self, guid: str) -> bytes:
        return (Path(tempfile.gettempdir()) / guid).read_bytes()

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class _EnvCdpProvider:
    """Yields the Chrome the `chrome_cdp` fixture launched, from `BROWSER_CDP_URL` — the transport
    the engine drives against; core ships no such provider, so the test carries its own."""

    async def lease(self, sandbox: object | None = None) -> CdpLease:
        return _StaticLease(CdpEndpoint(url=os.environ["BROWSER_CDP_URL"]))

    async def reattach(self, token: str) -> CdpLease:
        return _StaticLease(CdpEndpoint(url=os.environ["BROWSER_CDP_URL"]))


def _surface() -> BuaSurface:
    return BuaSurface(cdp_provider=_EnvCdpProvider(), find_completer=None, model=None)


async def test_bua_engine_drives_a_real_chrome_over_cdp(chrome_cdp: str) -> None:
    surface = _surface()
    try:
        navigated = await surface.navigate({"url": PROBE_URL})
        assert navigated["title"] == "BUA Probe"
        assert str(navigated["url"]).startswith("data:text/html")

        text = await surface.get_page_text({})
        assert "Probe Heading" in text["text"]
        assert "Example Link" in text["text"]

        page = await surface.read_page({})
        tree = page["tree"]
        assert "button" in tree and "Click Me" in tree
        assert "[ref=" in tree
        assert "example.com/next" in tree

        found = await surface.find({"query": "Click Me"})
        assert found["matches"], found
        assert "Click Me" in found["summary"]

        shot = await surface.computer({"actions": [{"action": "screenshot"}]})
        assert "Screenshot taken" in shot["output"]
        assert base64.b64decode(shot["screenshot_base64"])[:2] == b"\xff\xd8"

        clicked = await surface.computer(
            {"actions": [{"action": "left_click", "coordinate": [20, 20]}]}
        )
        assert "Clicked" in clicked["output"]
        assert clicked["last_click"] is not None

        created = await surface.tabs_create({"url": "about:blank"})
        assert created["tab_id"] == 1
        context = await surface.tabs_context({})
        assert len(context["tabs"]) == 2
        assert context["current_tab"] == 1

        closed = await surface.tabs_close({"tab_id": 1})
        assert len(closed["tabs"]) == 1
    finally:
        await surface.aclose()


async def test_bua_form_input_sets_a_field_by_ref(chrome_cdp: str) -> None:
    """form_input resolves a ref from read_page and writes the field value the DOM then reports —
    the ref-grounding + DOM.setValue path against real Chrome."""
    surface = _surface()
    try:
        await surface.navigate({"url": PROBE_URL})
        page = await surface.read_page({"filter": "interactive"})
        ref = _first_ref(str(page["tree"]), "textbox")
        result = await surface.form_input({"ref": ref, "value": "typed-value"})
        assert result["value"] == "typed-value"
    finally:
        await surface.aclose()


def _first_ref(tree: str, role: str) -> str:
    for line in tree.splitlines():
        if role in line and "[ref=" in line:
            return line.split("[ref=", 1)[1].split("]", 1)[0]
    raise AssertionError(f"no {role} ref in tree:\n{tree}")
