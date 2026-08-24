"""The share card composed for real: a REAL headless chromium draws it and the shipped in-sandbox
programs place the shot and encode the JPEG.

Nothing here is faked. `card_page` builds the same markup the sandbox gets, `PAGE_PROG` and
`ENCODE_PROG` run as the sandbox runs them — through the containment guard, against a workspace root
— and `shot_command` launches the browser available to the sandbox. So this is where the card's
measurements are actually proved: 1200x630, the panel on the left, the site's page on the right at
the size the shot decision fixes, under every platform's byte ceiling, and progressive.

The whole module is preconditioned on a browser that draws: a machine carrying chromium is not a
machine that can drive it, and a required gate must not turn one that cannot into a failure of the
card's measurements. So one trivial page is drawn first, under a wall of its own, and the module
skips whole with a plain reason when that does not land. Nothing here raises at collection. Every
assertion below stays exactly as strict wherever the browser does draw."""

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image
from ufo_ext_sites.share_card import (
    BROWSER_COMMANDS,
    CARD_HEIGHT,
    CARD_SHOT_DRAWN,
    CARD_SHOT_SCALE,
    CARD_TIMEOUT_SECONDS,
    CARD_WIDTH,
    ENCODE_PROG,
    LOAD_WALL_SECONDS,
    PAGE_PROG,
    PANEL_WIDTH,
    SETTLE_WALL_SECONDS,
    SHOT_BYTES_MAX,
    SHOT_DEADLINE_SECONDS,
    SHOT_TOKEN,
    SHOT_WIDTH,
    STORED_SHOT_DRAWN,
    VOID,
    card_page,
    shot_command,
)
from ufo_ext_sites.tools import PREVIEW_HEIGHT, PREVIEW_WIDTH

from ufo.sandbox.session import SANDBOX_MODULE_BOOTSTRAP, SANDBOX_PYTHON_FLAG

CARD_BYTES_MAX = 5 * 1024 * 1024
"""What every platform will draw: a card above this is dropped rather than shown."""
SITE_NAME = "ufo-architecture-map"
PAGE = (
    "<!doctype html><html><head><meta charset=utf-8><style>"
    "body{margin:0;font:14px/1.5 system-ui,sans-serif;color:#111;background:#fff}"
    "header{border-bottom:1px solid #ddd;padding:10px 16px;font-weight:600}"
    "p{margin:12px 16px;max-width:52ch}"
    "</style></head><body><header>metalcraftai/ufo — architecture map</header>"
    "<p>One assistant, reachable from a page, a Slack thread and a terminal, running turns in "
    "containers it can only leave through a gate.</p>"
    "<p>Every line is a claim you can check: each edge carries the file and line of the call that "
    "makes it real.</p></body></html>"
)


ANIMATED_PAGE = (
    "<!doctype html><html><head><meta charset=utf-8><style>"
    "body{margin:0;background:#fff;font:16px/1.6 system-ui,sans-serif;color:#111}"
    ".hero{opacity:0;animation:reveal 700ms ease-out 150ms forwards}"
    "@keyframes reveal{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}"
    "h1{margin:0 0 12px;font-size:44px}p{margin:0 0 10px;max-width:52ch}"
    "</style></head><body><div class=hero><h1>Revealed by the entrance animation</h1>"
    "<p>Nothing here is painted at the load event: the keyframes start after it and the first "
    "frame is fully transparent.</p></div></body></html>"
)
"""A front page of the shape the shot has to survive: its content arrives through a CSS entrance
animation, so a capture at the load event holds a white rectangle of it."""
LATE_DOM_PAGE = (
    "<!doctype html><html><head><meta charset=utf-8><style>"
    "body{margin:0;background:#fff;font:16px/1.6 system-ui,sans-serif;color:#111}"
    "h1{margin:0 0 12px;font-size:44px}p{margin:0 0 10px;max-width:52ch}"
    "</style></head><body><div id=app></div><script>"
    "addEventListener('load', function () { setTimeout(function () {"
    "document.getElementById('app').innerHTML = '<h1>Written 200ms after load</h1>' +"
    "'<p>The document fires load with an empty body and the script fills it afterwards, which "
    "is what a client-rendered front page does.</p>'; }, 200); });"
    "</script></body></html>"
)
"""The other shape: the document fires `load` empty and its script writes the page 200ms later."""
HANGING_PAGE = (
    "<!doctype html><html><head><meta charset=utf-8><style>"
    "body{margin:0;background:#fff;font:16px/1.6 system-ui,sans-serif;color:#111}"
    "h1{margin:0 0 12px;font-size:44px}p{margin:0 0 10px;max-width:52ch}"
    "</style></head><body><h1>One fetch stays pending for ever</h1>"
    "<p>The markup is painted and the request never answers, so network idle never arrives.</p>"
    "<script>fetch('/never').then(function () {}).catch(function () {});</script>"
    "</body></html>"
)
"""The page that killed the runner: it paints, and one request stays in flight for as long as it
lives. A capture that waits for the network to go quiet waits for ever here, which is why the settle
is walled."""
BLANK_PAGE = '<!doctype html><html><body style="background:#fff"></body></html>'
"""A page that paints one flat colour, which is what every shot taken too early looks like."""
SERVED_PAGES = (
    ("animated.html", ANIMATED_PAGE),
    ("late-dom.html", LATE_DOM_PAGE),
    ("hanging.html", HANGING_PAGE),
    ("blank.html", BLANK_PAGE),
)
NEVER_PATH = "/never"
NEVER_SECONDS = 3600
"""How long the endpoint that never answers holds a request: longer than any wall in this module, in
a daemon thread the server drops on shutdown."""
DRAWN_PIXELS_MIN = 2000
"""How much ink a shot of one of these pages must carry to be a picture of it. Each page draws a
headline and a paragraph in black — measured above 17000 dark pixels at 1200x900 — and a capture at
the load event drew exactly none, so this sits far below the one and far above the other."""
DARK_VALUE = 128
PROBE_SECONDS = 10
"""The precondition's own wall. A browser that draws draws a page in well under a second, so this is
long enough for the slowest cold start and short enough that a browser which never draws costs the
job seconds rather than every test's wall."""
PROBE_PAGE = "<!doctype html><html><body>probe</body></html>"
DRAIN_SECONDS = 2
"""How long a killed run's output is still read for. `timeout` puts the browser in a process group
of its own, so the kill below does not reach it and it can hold these pipes open to its own wall —
the wall this read must not sit behind. What is there is reported; what is not is given up."""


def _walled(command: list[str], wall: int) -> tuple[subprocess.CompletedProcess[str], bool]:
    """`command` under `wall` seconds, answering what it did and whether the wall ended it.

    The child gets its own process group: a stalled chromium leaves crashpad handlers holding the
    pipes this read waits on, so killing the child alone would still hang until the job budget."""
    walled = False
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as child:
        try:
            out, err = child.communicate(timeout=wall)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            walled = True
            try:
                out, err = child.communicate(timeout=DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                out, err = "", ""
    return subprocess.CompletedProcess(command, child.returncode, out, err), walled


def _run_shot(url: str, width: int, height: int, scale: int, shot: Path) -> list[str]:
    """The shipped command for one shot, exactly as the deploy path builds it."""
    return [
        "bash",
        "-c",
        shot_command(
            url=url,
            width=width,
            height=height,
            scale=scale,
            shot=str(shot),
            profile=str(shot.parent / f"profile-{shot.stem}"),
            root=str(shot.parent),
        ),
    ]


def _chromium_draws() -> bool:
    """Whether a browser on this machine draws a page at all: one trivial local page through the
    shipped command, walled at `PROBE_SECONDS`, answered on the picture the way production answers.

    A runner gave chromium every quieting flag and a 30s wall and still got an empty file back,
    because the browser it launched sat waiting on desktop services that runner does not have. That
    is a machine which cannot drive a browser, not a card drawn wrong, so it skips the module rather
    than fails it. This never raises: the required integration gate reads a missing or undrivable
    browser as a skip, and the card's assertions stay strict wherever one does draw. Chromium's
    helpers can outlive the shot and write the profile while the scratch directory comes down, so
    its cleanup tolerates a straggler's leavings instead of failing collection."""
    if next((path for path in map(shutil.which, BROWSER_COMMANDS) if path), None) is None:
        return False
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        page = root / "probe.html"
        page.write_text(PROBE_PAGE)
        shot = root / "probe.png"
        _walled(_run_shot(str(page), CARD_WIDTH, CARD_HEIGHT, 1, shot), PROBE_SECONDS)
        return shot.is_file() and shot.stat().st_size > 0


pytestmark = pytest.mark.skipif(not _chromium_draws(), reason="no chromium here that draws a page")


def _run(what: str, command: list[str]) -> subprocess.CompletedProcess[str]:
    """`command` under the same wall the sandbox gives every step of the composition."""
    result, ended = _walled(command, CARD_TIMEOUT_SECONDS)
    if ended:
        pytest.fail(
            f"{what} did not finish inside {CARD_TIMEOUT_SECONDS}s\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _shoot(url: str, width: int, height: int, scale: int, shot: Path) -> None:
    """One shot, held to the contract the deploy path holds it to: a readable picture on disk at the
    size the shot was asked for.

    The browser's exit status is not the contract. The run carries its own kill wall, and a browser
    that draws the picture and then will not exit is killed with the picture already written — which
    is why the shipped command answers on the file, and why this reads the file too."""
    shot.parent.mkdir(parents=True, exist_ok=True)
    drawn = _run(f"chromium drawing {url}", _run_shot(url, width, height, scale, shot))
    assert shot.is_file() and shot.stat().st_size > 0, drawn.stderr
    assert Image.open(shot).size == (width * scale, height * scale), drawn.stderr


def _dark(shot: Path) -> int:
    """How many dark pixels the shot holds — the page's own ink, which is what tells a picture of a
    front page from a picture of nothing."""
    return sum(Image.open(shot).convert("L").histogram()[:DARK_VALUE])


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The settle pages on loopback, with `NEVER_PATH` answering nothing, and the base url of them.

    A page shot is taken of a site the sandbox serves on its own loopback, and one of these pages
    needs a request that stays in flight — which a `file://` page cannot make."""
    root = tmp_path_factory.mktemp("served")
    for name, markup in SERVED_PAGES:
        (root / name).write_text(markup)

    class Pages(SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(root), **kwargs)  # type: ignore[arg-type]

        def do_GET(self) -> None:
            if self.path.startswith(NEVER_PATH):
                time.sleep(NEVER_SECONDS)
                return
            super().do_GET()

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _program(program: str, *args: str) -> str:
    """One of the shipped in-sandbox programs, run the way the sandbox runs it: isolated, with the
    guard importable as `containment` off the shipped bootstrap the program carries — the same text
    the session prepends, so the module under test here is the module a sandbox gets."""
    result = _run(
        f"the in-sandbox program over {args}",
        [
            sys.executable,
            SANDBOX_PYTHON_FLAG,
            "-c",
            SANDBOX_MODULE_BOOTSTRAP + program,
            *args,
        ],
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _compose(root: Path, name: str, shot: Path, drawn: str) -> tuple[Path, str]:
    """The composition exactly as `_compose` drives it in the sandbox: the page is written with the
    token in it, the program puts the shot in, chromium draws the card, the program encodes it."""
    page = root / "share-card.html"
    page.write_text(card_page(name, drawn))
    _program(
        PAGE_PROG.format(limit=SHOT_BYTES_MAX, token=SHOT_TOKEN),
        str(page),
        str(shot),
        str(root),
    )
    assert SHOT_TOKEN not in page.read_text()
    card_png = root / "share-card.png"
    _shoot(str(page), CARD_WIDTH, CARD_HEIGHT, 1, card_png)
    card = root / "share-card.jpg"
    digest = _program(ENCODE_PROG.format(quality=86), str(card_png), str(card), str(root))
    return card, digest


def test_the_card_is_the_measured_composition_at_the_size_every_platform_draws(
    tmp_path: Path,
) -> None:
    """The card the deploy path produces, drawn for real from a shot taken at the card's own width.

    Its own bytes name it, so the digest the encode prints has to be the digest of what landed —
    that value is what the public URL carries and what the route compares a request against."""
    root = tmp_path
    site = root / "site.html"
    site.write_text(PAGE)
    shot = root / "card-shot.png"
    _shoot(str(site), SHOT_WIDTH, CARD_HEIGHT, CARD_SHOT_SCALE, shot)

    card, digest = _compose(root, SITE_NAME, shot, CARD_SHOT_DRAWN)

    payload = card.read_bytes()
    assert digest == hashlib.sha256(payload).hexdigest()
    assert len(payload) < CARD_BYTES_MAX
    drawn = Image.open(card)
    assert drawn.size == (CARD_WIDTH, CARD_HEIGHT)
    assert drawn.format == "JPEG"
    assert drawn.info.get("progressive") == 1
    pixels = drawn.convert("RGB")
    # The panel's ground fills the left, and the site's own page fills the right: the card is a
    # split, not a picture with a caption over it.
    assert _close(pixels.getpixel((PANEL_WIDTH // 2, CARD_HEIGHT - 40)), _rgb(VOID))
    assert not _close(pixels.getpixel((CARD_WIDTH - 40, 8)), _rgb(VOID))


def test_a_site_with_only_a_stored_page_shot_still_gets_a_card(tmp_path: Path) -> None:
    """The fallback path, for every site deployed before cards existed: the stored 1200x900 picture
    is drawn at 1:1 and cropped to the box from its top-left, never fitted into it — so the page's
    own text stays at the size it was rendered at."""
    root = tmp_path
    site = root / "site.html"
    site.write_text(PAGE)
    stored = root / "preview.png"
    _shoot(str(site), PREVIEW_WIDTH, PREVIEW_HEIGHT, 1, stored)

    card, _digest = _compose(root, SITE_NAME, stored, STORED_SHOT_DRAWN)

    drawn = Image.open(card).convert("RGB")
    assert drawn.size == (CARD_WIDTH, CARD_HEIGHT)
    source = Image.open(stored).convert("RGB")
    # 1:1 from the top-left: the page's pixels land where the box starts, unscaled.
    for point in ((60, 12), (300, 120), (SHOT_WIDTH - 20, CARD_HEIGHT - 20)):
        assert _close(drawn.getpixel((PANEL_WIDTH + point[0], point[1])), source.getpixel(point))


def test_a_page_that_reveals_its_content_with_an_animation_is_photographed_with_it(
    tmp_path: Path,
    served: str,
) -> None:
    """A front page whose content arrives through a CSS entrance animation.

    The load event fires before the first keyframe, so a one-shot capture drew a white rectangle of
    this page — measured at 0 dark pixels, in a 5063-byte PNG that `test -s` accepts. The driver
    waits for the page to stop moving instead, so the shot carries the animation's end state."""
    shot = tmp_path / "animated.png"
    _shoot(f"{served}/animated.html", PREVIEW_WIDTH, PREVIEW_HEIGHT, 1, shot)

    assert _dark(shot) > DRAWN_PIXELS_MIN


def test_a_page_that_writes_its_dom_after_load_is_photographed_with_it(
    tmp_path: Path,
    served: str,
) -> None:
    """A front page whose script writes its markup 200ms after load, which is what a client-rendered
    page does. The document fires `load` with an empty body, so the capture has to happen later."""
    shot = tmp_path / "late-dom.png"
    _shoot(f"{served}/late-dom.html", PREVIEW_WIDTH, PREVIEW_HEIGHT, 1, shot)

    assert _dark(shot) > DRAWN_PIXELS_MIN


def test_a_page_whose_fetch_never_answers_is_still_drawn_inside_the_wall(
    tmp_path: Path,
    served: str,
) -> None:
    """The failure that killed the runner: one request stays in flight for as long as the page
    lives, so network idle never arrives.

    The settle is walled rather than waited on, so the shot is taken with what the page has painted
    and the run ends far inside the kill wall. A capture driven by `--virtual-time-budget` spent the
    whole 30s here and wrote nothing."""
    shot = tmp_path / "hanging.png"
    started = time.monotonic()
    _shoot(f"{served}/hanging.html", PREVIEW_WIDTH, PREVIEW_HEIGHT, 1, shot)
    seconds = time.monotonic() - started

    assert _dark(shot) > DRAWN_PIXELS_MIN
    assert seconds < SHOT_DEADLINE_SECONDS
    assert LOAD_WALL_SECONDS + SETTLE_WALL_SECONDS < SHOT_DEADLINE_SECONDS


def test_a_page_that_draws_one_flat_colour_is_not_accepted_as_a_picture(
    tmp_path: Path,
    served: str,
) -> None:
    """The guard the size check cannot be: a white PNG is not an empty file, so `test -s` reads a
    page that drew nothing as a picture of it. The driver refuses a shot that is one flat colour,
    which leaves the name empty, the command failing, and the site with the picture it already
    had."""
    shot = tmp_path / "blank.png"
    shot.write_bytes(b"")

    drawn, ended = _walled(
        _run_shot(f"{served}/blank.html", PREVIEW_WIDTH, PREVIEW_HEIGHT, 1, shot),
        CARD_TIMEOUT_SECONDS,
    )

    assert not ended
    assert drawn.returncode != 0
    assert shot.stat().st_size == 0
    assert "flat colour" in drawn.stderr


def _rgb(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _close(drawn: tuple[int, ...], source: tuple[int, ...]) -> bool:
    """Equal within what the JPEG encode moves a pixel by."""
    return all(abs(one - two) <= 12 for one, two in zip(drawn, source, strict=True))
