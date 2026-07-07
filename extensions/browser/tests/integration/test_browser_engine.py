"""The BUA engine end to end against a REAL Chrome over CDP — the live proof the shell never gave.

A headless Chrome is launched with `--remote-debugging-port`; `BROWSER_CDP_URL` points the default
`SandboxCdpProvider` at it, and one `BuaSurface` connects and drives a real page: navigate →
read_page → get_page_text → find → a `computer` screenshot and click → tabs create/context/close.
Nothing here
is faked — the CDP protocol handling (WebSocket transport, DOMSnapshot + accessibility join, input
synthesis, settle) is exercised against Chrome itself. Skips with a clear reason where no Chrome
binary is present, so the suite stays collectable everywhere."""

import base64
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from ufo_ext_browser.bua.backend import BuaSurface

from ufo.browser import SandboxCdpProvider

CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
READY_TIMEOUT_S = 20.0
READY_POLL_S = 0.1

PROBE_HTML = (
    "<!doctype html><html><head><title>BUA Probe</title></head>"
    "<body><h1>Probe Heading</h1>"
    '<a href="https://example.com/next">Example Link</a>'
    '<button id="go">Click Me</button>'
    '<input type="text" id="field"></body></html>'
)
PROBE_URL = f"data:text/html,{quote(PROBE_HTML)}"


def _chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        found = shutil.which(candidate) if "/" not in candidate else candidate
        if found and Path(found).exists():
            return found
    return None


pytestmark = pytest.mark.skipif(_chrome() is None, reason="no Chrome/Chromium binary for live CDP")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def chrome_cdp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    binary = _chrome()
    assert binary is not None
    port = _free_port()
    profile = tmp_path / "chrome-profile"
    process = subprocess.Popen(
        [
            binary,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + READY_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/json/version", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(READY_POLL_S)
        else:
            raise RuntimeError("Chrome did not expose its CDP endpoint in time")
        monkeypatch.setenv("BROWSER_CDP_URL", url)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def _surface() -> BuaSurface:
    return BuaSurface(cdp_provider=SandboxCdpProvider.from_env(), find_completer=None, model=None)


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
