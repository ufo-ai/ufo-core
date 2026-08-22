"""The share card: the picture a link unfurler draws for a public hosted site.

A hosted site's link unfurls with a card, and the card a crawler used to get was the brand's own
generic one — the same image for every site. This module composes the site's own card instead: the
brandmark panel on the left, the site's own front page on the right. The frame's head points an
`og:image` at it for a public site alone, and the anonymous route that serves it re-reads the site's
visibility on every request (both in `surface.py`).

The composition is fixed and measured: 1200x630, a 456px panel on `--void-900` carrying the kicker,
the repository's own lockup, and the site's name, then the site's page across the remaining 744x630
with no text drawn over it. `assets/` holds the two brand files the panel needs — the lockup and the
Inter face — copied here rather than read from the portal's source tree, because the panel is drawn
inside the member's sandbox where no checkout exists; a test holds each copy byte-identical to its
original, exactly as the gateway's compiled-in copies are held.

The card is drawn where its ingredients already are: the site answers on the sandbox's own loopback
and the sandbox image carries chromium, so one headless run photographs the page and a second draws
the composed card from markup this module builds and hands over. Each run drives the browser over
its own protocol pipe rather than one-shotting it, because the settle point a shot needs is not the
load event — `SETTLE_WALL_SECONDS` says what it is instead. Nothing pastes pixels — the panel
and the shot are one HTML page, the shot riding in it as a `data:` URI — so the composition needs no
image library in the sandbox and no text is ever drawn over the site's own pixels. Pillow, which the
image already carries, does the last step alone: the encode to the progressive JPEG every platform
draws, and the digest that names the card's URL.

The shot is taken at the card's own width rather than fitted into it. A 1200x900 page squeezed into
744px turns its body text to mush at the 400px an unfurl actually draws, so the card's shot is its
own: a 744x630 viewport at deviceScaleFactor 2, drawn at half size, which lays the page out for the
box it lands in and leaves every glyph at natural size. A site deployed before this landed has no
such shot, only the stored 1200x900 preview, so its card draws that at 1:1 and lets the box crop it
from the top-left — never fitted.

A card is decoration, so every failure here is answered the way a failed page shot is: it is logged
and the site keeps the card it already had. The site is hosted either way."""

import base64
import html
import shlex
from pathlib import Path
from uuid import UUID

from ufo.sdk.o11y import log
from ufo.sdk.sandbox import (
    SANDBOX_MODULE_BOOTSTRAP,
    SANDBOX_PYTHON_FLAG,
    TOOL_OUTPUT_DIR,
    WORKSPACE_DIR,
)
from ufo.sdk.tools import ToolContext
from ufo_ext_sites.store import HostedSites

CARD_WIDTH = 1200
CARD_HEIGHT = 630
"""1.91:1, the one card size every platform draws large — Slack renders it at roughly 400px wide."""
PANEL_WIDTH = 456
SHOT_WIDTH = CARD_WIDTH - PANEL_WIDTH
CARD_SHOT_SCALE = 2
"""The card shot's deviceScaleFactor: the page lays out at 744x630 and rasters at 1488x1260, which
the card draws at half size, so no glyph is scaled below the size the page chose for it."""
CARD_QUALITY = 86
CARD_MEDIA_TYPE = "image/jpeg"
CARD_EXTENSION = "jpg"
CARD_NAME = "share_card"
"""What the produced card is called wherever it is kept, so the stored artifact, the row's columns,
and the tests all name one thing."""

VOID = "#0D1418"
EDGE = "#2E373B"
INK = "#FFFAEF"
KICKER_INK = "rgba(243,236,222,0.58)"
NAME_INK = "rgba(243,236,222,0.88)"
KICKER = "Made with"
LOGO_WIDTH = 296
LOGO_HEIGHT = 74
"""The lockup at its own proportions — the file draws 196x49 — big enough to read at 400px wide."""

CARD_SHOT = f"{TOOL_OUTPUT_DIR}/share-card-shot-{{site}}.png"
CARD_PAGE = f"{TOOL_OUTPUT_DIR}/share-card-{{site}}.html"
CARD_DRAWN = f"{TOOL_OUTPUT_DIR}/share-card-{{site}}.png"
CARD_FILE = f"{TOOL_OUTPUT_DIR}/share-card-{{site}}.{CARD_EXTENSION}"
STORED_SHOT = f"{TOOL_OUTPUT_DIR}/share-card-stored-{{site}}.png"
"""Where the picture a past deploy stored is put back down for a card that has none of its own."""

CARD_PROFILE_DIR = "/tmp/ufo-share-card"
"""Its own chromium profile directory, outside the workspace and outside the one the page shot
takes: a one-shot chromium holds the profile lock for its run, and two runs sharing a directory
would fail whichever started second."""
SHOT_DEADLINE_SECONDS = 30
"""The kill wall the browser's whole run is given, held outside the browser because nothing on its
command line holds it: `--timeout` does not bound it either, since `--headless=new` takes the switch
and ignores it. So `timeout` ends the run and kills the whole process group with it, and the shot on
disk rather than the exit status is what says whether the picture was drawn — a browser killed after
it wrote one still drew it. Well inside `CARD_TIMEOUT_SECONDS`, so this is what stops a stuck run
and the caller's wall never has to. The settle the driver waits for is bounded by
`LOAD_WALL_SECONDS` and `SETTLE_WALL_SECONDS` together, both well inside this, so what reaches this
wall is a browser that stopped answering rather than a page still settling."""
LOAD_WALL_SECONDS = 12
"""How long the driver waits for the page's `load` event before it starts reading frames. A page
whose subresource never answers never fires it, so the wait is bounded and the settle below happens
regardless."""
SETTLE_WALL_SECONDS = 5
"""How long the driver then gives the page to stop moving — a frame that repeats with no request in
flight. This is what replaces `--virtual-time-budget`, which this command must never carry: the
capture then waits for that budget to expire, virtual time stands still while any fetch is pending,
and one pending fetch is enough to make the run draw nothing at all — a runner with egress keeps one
pending for as long as the process lives, GCM registration retrying on its own backoff with every
flag in `QUIET_FLAGS` already on the command line, so the run reached the wall with an empty shot.
The budget's failure is that it is unbounded; this wait is bounded, so a page that never goes idle
is photographed here with whatever it has painted."""
FRAME_SECONDS = 0.25
"""How long the driver sits between frames while the page settles: long enough that an entrance
animation moves between two captures, short enough that a page already still costs one of these."""
CARD_TIMEOUT_SECONDS = 90
SHOT_BYTES_MAX = 8 * 1024 * 1024
"""The most a shot may be, read into the page as a `data:` URI: a screenshot of a page, not an
upload. A larger one is refused rather than truncated, which would draw a broken image."""
DETAIL_CHARS = 500
SHOT_TOKEN = "SHARE_CARD_SHOT_DATA_URI"
"""What the page carries where the shot's `data:` URI goes. The substitution happens in the sandbox,
because the shot's bytes are there and never cross this process."""

BROWSER_COMMANDS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
QUIET_FLAGS = (
    "--disable-background-networking --disable-sync --disable-component-update "
    "--no-first-run --no-default-browser-check --disable-client-side-phishing-detection"
)
"""Everything that keeps chromium talking to the network after the page is drawn. They quiet most
of the background services, but not all of them — a CI run with egress still showed GCM
registration retrying on its own 30s backoff with every one of these on the command line — so what
actually bounds the run is `SHOT_DEADLINE_SECONDS` above. They suppress background traffic alone:
the same page renders to byte-identical PNGs with and without them."""
UNATTENDED_FLAGS = (
    "--password-store=basic --use-mock-keychain --no-service-autorun "
    "--disable-breakpad --disable-crash-reporter --disable-domain-reliability "
    "--disable-default-apps --disable-component-extensions-with-background-pages "
    "--disable-features=MediaRouter,DialMediaRouteProvider,OptimizationHints,Translate"
)
"""What stops the browser waiting on a desktop it does not have. A CI runner carries no keyring, no
keychain, no crash server and no cast receiver, and a browser that asks for one of them holds a
one-shot run for as long as the ask takes — which is the shape of the failure the wall below kept
catching: dbus errors, GCM registration retrying, and an empty shot at the wall. So the password
store is `basic` and the keychain is a mock, which asks dbus for neither; the crash and reliability
uploads are off; and the component extensions with background pages — what registers with GCM at all
— are never loaded. Measured on chromium 151: the same page renders to a byte-identical PNG in the
same half second with and without them, so these change what a run waits for and never what it
draws."""
SHOT_FLAGS = " ".join(
    (
        "--headless=new --no-sandbox --disable-dev-shm-usage --disable-gpu",
        QUIET_FLAGS,
        UNATTENDED_FLAGS,
        "--hide-scrollbars --remote-debugging-pipe",
    )
)
"""The browser's own command line, which the driver launches it with: what a headless run inside a
container needs, both quiet sets above, and the pipe the protocol is spoken over. The viewport and
the device scale factor are not here — the driver sets those through `Emulation`, which fixes the
raster the capture answers with rather than asking a window manager the container has not got."""
SHOT_PROG = '''
import base64
import io
import json
import os
import select
import signal
import sys
import time

from containment import ContainmentError, contained_file
from PIL import Image

FLAGS = "{flags}"
LOAD_WALL = {load_wall}
SETTLE_WALL = {settle_wall}
FRAME = {frame}
BROWSER_FD = 3
PAGE_FD = 4
"""The two descriptors `--remote-debugging-pipe` speaks on: the browser reads a request from 3 and
writes its answer to 4, one NUL-terminated JSON message each way, which is the whole transport — no
port is opened and no websocket library is needed to hold a conversation with it."""
SPARE_FD = 20
POLL = 0.05
READ_BYTES = 65536


def main():
    browser, url, width, height, scale, shot, profile, root = sys.argv[1:9]
    driven = Driven([browser] + FLAGS.split() + ["--user-data-dir=" + profile, "about:blank"])
    try:
        picture = settled(driven, url, int(width), int(height), float(scale))
    finally:
        driven.stop()
    if picture is None:
        raise SystemExit("the page drew one flat colour, which is no picture of it")
    try:
        with contained_file(shot, root) as target:
            target.replace_bytes(picture, 0o600)
    except ContainmentError as error:
        raise SystemExit(str(error))


def settled(driven, url, width, height, scale):
    """Drive the page to a settle point inside the walls and answer the picture taken there, or None
    when it never drew more than one flat colour.

    The load event is where a one-shot browser captures, and it is too early: a page that reveals
    its content with an entrance animation has painted none of it yet, and a page that writes its
    DOM after load has nothing to paint. So the settle point is a frame that repeats with no request
    in flight, and every wait it takes is walled — a fetch that never answers keeps the page from
    ever going idle, and waiting on that is what drew nothing at all."""
    session = driven.attach()
    driven.call("Page.enable", None, session)
    driven.call("Network.enable", None, session)
    metrics = dict(width=width, height=height, deviceScaleFactor=scale, mobile=False)
    driven.call("Emulation.setDeviceMetricsOverride", metrics, session)
    driven.call("Page.navigate", dict(url=address(url)), session)
    loading = time.monotonic() + LOAD_WALL
    while not driven.loaded() and time.monotonic() < loading:
        driven.pump(POLL)
    settling = time.monotonic() + SETTLE_WALL
    previous = None
    while True:
        frame = driven.frame(session)
        blank = flat(frame)
        if not blank and frame == previous and driven.inflight() == 0:
            return frame
        if time.monotonic() >= settling:
            return None if blank else frame
        previous = frame
        driven.pump(FRAME)


def address(url):
    """What `Page.navigate` takes. A browser's command line accepts a bare filesystem path, and the
    card's own page arrives as one, so a path is named as the file URL it is."""
    if url.startswith("/"):
        return "file://" + url
    return url


def flat(png):
    """Whether the shot is one flat colour, which is what a page photographed before it drew
    anything looks like — and what a size check cannot tell from a picture, because a white PNG is
    not an empty file."""
    drawn = Image.open(io.BytesIO(png)).convert("RGB")
    found = drawn.getcolors(maxcolors=2)
    return found is not None and len(found) < 2


def spare(pair, first):
    """Move a pipe's two ends clear of the descriptors the browser is given. `dup2` onto a
    descriptor that already is the one it names leaves close-on-exec set, so a pipe end that landed
    on 3 or 4 by itself would reach the browser closed."""
    moved = []
    for offset, held in enumerate(pair):
        os.dup2(held, first + offset)
        os.close(held)
        moved.append(first + offset)
    return moved


class Driven:
    """One browser under the DevTools protocol over its own pipe: launched here, spoken to here, and
    killed here whether or not it drew anything."""

    def __init__(self, command):
        speaking = spare(os.pipe(), SPARE_FD)
        answering = spare(os.pipe(), SPARE_FD + 2)
        actions = [
            (os.POSIX_SPAWN_DUP2, speaking[0], BROWSER_FD),
            (os.POSIX_SPAWN_DUP2, answering[1], PAGE_FD),
        ]
        self.pid = os.posix_spawnp(command[0], command, os.environ, file_actions=actions)
        os.close(speaking[0])
        os.close(answering[1])
        self.speaking = speaking[1]
        self.answering = answering[0]
        self.pending = b""
        self.seen = []
        self.calls = 0

    def attach(self):
        """A page target of its own, attached flat so the one pipe carries that session too."""
        target = self.call("Target.createTarget", dict(url="about:blank"), None)
        attached = dict(targetId=target["targetId"], flatten=True)
        return self.call("Target.attachToTarget", attached, None)["sessionId"]

    def frame(self, session):
        return base64.b64decode(
            self.call("Page.captureScreenshot", dict(format="png"), session)["data"]
        )

    def loaded(self):
        return any(event.get("method") == "Page.loadEventFired" for event in self.seen)

    def inflight(self):
        """How many requests the page has started and not finished — zero is network idle."""
        started = set()
        ended = set()
        for event in self.seen:
            method = event.get("method")
            request = event.get("params", dict()).get("requestId")
            if method == "Network.requestWillBeSent":
                started.add(request)
            elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                ended.add(request)
        return len(started - ended)

    def call(self, method, params, session):
        self.calls += 1
        message = dict(id=self.calls, method=method, params=params or dict())
        if session is not None:
            message["sessionId"] = session
        os.write(self.speaking, json.dumps(message).encode() + b"\\0")
        deadline = time.monotonic() + LOAD_WALL
        while time.monotonic() < deadline:
            answer = self.read(deadline - time.monotonic())
            if answer is None:
                continue
            if answer.get("id") != self.calls:
                self.seen.append(answer)
                continue
            if "error" in answer:
                raise SystemExit(method + ": " + json.dumps(answer["error"]))
            return answer.get("result", dict())
        raise SystemExit(method + ": the browser stopped answering")

    def pump(self, wait):
        """Collect the browser's events for `wait` seconds, which is also how the driver waits."""
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            event = self.read(deadline - time.monotonic())
            if event is not None:
                self.seen.append(event)

    def read(self, wait):
        while b"\\0" not in self.pending:
            if wait <= 0:
                return None
            ready = select.select([self.answering], [], [], min(wait, POLL))[0]
            if not ready:
                return None
            chunk = os.read(self.answering, READ_BYTES)
            if not chunk:
                raise SystemExit("the browser closed the protocol pipe")
            self.pending += chunk
        raw, self.pending = self.pending.split(b"\\0", 1)
        return json.loads(raw)

    def stop(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(self.pid, 0)


main()
'''
"""Drive one browser over the DevTools protocol and write the settled picture — `argv` is the
browser, the url, the width, the height, the device scale factor, the shot, the browser profile
directory, and the workspace root.

The protocol is spoken from the standard library over the browser's own pipe, so the shot costs the
image no driver it does not already carry and the renderer stays the one chromium the sandbox and
the CI runner both have. The picture goes down through the containment guard, the way every other
write inside the sandbox does: the shot's name sits in a directory the agent writes."""
SHOT_HEREDOC = "UFO_SHOT_DRIVER"
SHOT_CMD = (
    "for browser in " + " ".join(BROWSER_COMMANDS) + "; do\n"
    '  command -v "$browser" >/dev/null 2>&1 || continue\n'
    '  timeout --signal=KILL {deadline}s python3 {isolated} - "$browser" {url} {width} {height}'
    " {scale} {shot} {profile} {root} <<'" + SHOT_HEREDOC + "'\n"
    "{driver}\n" + SHOT_HEREDOC + "\n"
    "  test -s {shot}\n"
    "  exit\n"
    "done\n"
    'printf %s "no chromium in this sandbox" >&2; exit 1'
)

PAGE_PROG = """
import base64
import sys
from containment import ContainmentError, contained_file

try:
    with contained_file(sys.argv[2], sys.argv[3]) as shot:
        raw = shot.read_bytes({limit} + 1)
    if len(raw) > {limit}:
        raise SystemExit(sys.argv[2] + " is larger than a page shot may be")
    with contained_file(sys.argv[1], sys.argv[3]) as page:
        markup = page.read_text({limit})
        uri = "data:image/png;base64," + base64.b64encode(raw).decode()
        page.replace_text(markup.replace("{token}", uri), 0o600)
except ContainmentError as error:
    raise SystemExit(str(error))
"""
"""Put the shot into the page the card is drawn from — `argv` is the page, the shot, the workspace
root. The page arrives already written with `SHOT_TOKEN` where the `data:` URI belongs, so the only
thing this decides is which bytes that token becomes."""

ENCODE_PROG = """
import hashlib
import io
import sys
from PIL import Image
from containment import ContainmentError, contained_file

try:
    with contained_file(sys.argv[1], sys.argv[3]) as drawn:
        with drawn.open_bytes() as handle:
            card = Image.open(handle).convert("RGB")
    encoded = io.BytesIO()
    card.save(encoded, format="JPEG", quality={quality}, optimize=True, progressive=True)
    payload = encoded.getvalue()
    with contained_file(sys.argv[2], sys.argv[3]) as target:
        target.replace_bytes(payload, 0o600)
except ContainmentError as error:
    raise SystemExit(str(error))
print(hashlib.sha256(payload).hexdigest())
"""
"""Encode the drawn card as the progressive JPEG a card is and answer its digest — `argv` is the
drawn PNG, the card, the workspace root. Chromium writes baseline JPEG at a quality of its own
choosing, so this one step runs through the image library; the composition is already drawn."""

CARD_SHOT_DRAWN = f"width:{SHOT_WIDTH}px;height:{CARD_HEIGHT}px"
"""How the card's own shot is drawn: 1488x1260 at half size, so the page it laid out for this box is
all there and nothing is cropped."""
STORED_SHOT_DRAWN = "width:auto;height:auto"
"""How a stored 1200x900 page picture is drawn: at 1:1, cropped to the box from its top-left, which
is the only variant whose body text survives the width an unfurl draws."""

_ASSETS = Path(__file__).parent / "assets"
LOGO_ASSET = "ufo-logo.svg"
FONT_ASSET = "Inter-VariableFont_wght.woff2"

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  @font-face {{ font-family:'Inter'; font-style:normal; font-weight:100 900;
    src:url('data:font/woff2;base64,{font}') format('woff2'); }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  html, body {{ width:{width}px; height:{height}px; overflow:hidden; background:{void}; }}
  .card {{ display:flex; width:{width}px; height:{height}px; }}
  .panel {{ flex:none; width:{panel}px; height:{height}px; padding:56px; background:{void};
            display:flex; flex-direction:column; align-items:flex-start;
            border-right:1px solid {edge}; }}
  .kicker {{ font-family:'Inter',system-ui,sans-serif; font-weight:500; font-size:26px;
             letter-spacing:0.15em; text-transform:uppercase; color:{kicker_ink};
             margin-bottom:30px; }}
  .logo {{ margin-bottom:auto; }}
  .logo svg {{ width:{logo_width}px; height:{logo_height}px; display:block; }}
  .logo svg .st0, .logo svg path, .logo svg polygon {{ fill:{ink}; }}
  /* The site's name at the panel's foot. Two lines at most, then an ellipsis — never a third. */
  .name {{ margin-top:36px; font-family:'Inter',system-ui,sans-serif; font-weight:500;
           font-size:34px; line-height:1.28; color:{name_ink}; display:-webkit-box;
           -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
           word-break:break-word; }}
  .shot {{ flex:none; width:{shot}px; height:{height}px; overflow:hidden; }}
  .shot img {{ display:block; {drawn}; }}
</style></head>
<body><div class=card>
  <div class=panel>
    <div class=kicker>{kicker}</div>
    <div class=logo>{logo}</div>
    <div class=name>{name}</div>
  </div>
  <div class=shot><img src="{token}" alt=""></div>
</div></body></html>
"""


def card_page(name: str, drawn: str) -> str:
    """The page one chromium pass draws the card from: the panel's markup and the site's shot, with
    `SHOT_TOKEN` standing where the shot's `data:` URI goes.

    `drawn` is how the shot is sized inside its box — `CARD_SHOT_DRAWN` for a shot taken at the
    card's width, `STORED_SHOT_DRAWN` for a page picture a deploy stored. The name is escaped: it is
    the member's own text and this is markup."""
    return _PAGE.format(
        font=base64.b64encode((_ASSETS / FONT_ASSET).read_bytes()).decode(),
        width=CARD_WIDTH,
        height=CARD_HEIGHT,
        panel=PANEL_WIDTH,
        shot=SHOT_WIDTH,
        void=VOID,
        edge=EDGE,
        ink=INK,
        kicker_ink=KICKER_INK,
        name_ink=NAME_INK,
        logo_width=LOGO_WIDTH,
        logo_height=LOGO_HEIGHT,
        drawn=drawn,
        kicker=KICKER,
        logo=_lockup(),
        name=html.escape(name),
        token=SHOT_TOKEN,
    )


def _lockup() -> str:
    """The lockup's markup, from `<svg` on: the file is a standalone document, and its XML
    declaration is not something a browser reads as markup inside a page."""
    drawing = (_ASSETS / LOGO_ASSET).read_text()
    return drawing[drawing.index("<svg") :]


def shot_command(
    *, url: str, width: int, height: int, scale: int, shot: str, profile: str, root: str
) -> str:
    """One headless chromium run drawing `url` at `width`x`height` into `shot`, under whichever of
    the image's browsers is present — the same browsers the site's own page shot is taken with, and
    the only renderer the sandbox image and the CI runner both carry. `scale` is the
    deviceScaleFactor the raster is multiplied by, and `root` is the workspace the shot is contained
    under.

    The browser is driven rather than one-shot: `SHOT_PROG` launches it over its own protocol pipe,
    waits for the page to load and then for it to stop moving, and captures there — so a page that
    reveals its content with an entrance animation or writes its DOM after load is photographed with
    what it draws, and a shot that is one flat colour is refused instead of stored. Every wait it
    takes is bounded, and `SHOT_DEADLINE_SECONDS` rides outside all of them. The command answers on
    the shot rather than on the browser's status, so a run that draws the picture and then will not
    exit still ends inside the wall with a shot."""
    return SHOT_CMD.format(
        deadline=SHOT_DEADLINE_SECONDS,
        isolated=SANDBOX_PYTHON_FLAG,
        scale=scale,
        width=width,
        height=height,
        profile=shlex.quote(profile),
        shot=shlex.quote(shot),
        url=shlex.quote(url),
        root=shlex.quote(root),
        driver=SANDBOX_MODULE_BOOTSTRAP
        + SHOT_PROG.format(
            flags=SHOT_FLAGS,
            load_wall=LOAD_WALL_SECONDS,
            settle_wall=SETTLE_WALL_SECONDS,
            frame=FRAME_SECONDS,
        ),
    )


async def draw_from_page(
    ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, port: int
) -> None:
    """Compose this site's card from a shot of its own front page, taken for the card's own box.

    Runs in the deploy that just published that page, beside the shot the row's picture comes from:
    the site answers on loopback here and the browser is in the same container, so the card is
    photographed where no view token, no ingress and no cookie are involved."""
    shot = CARD_SHOT.format(site=name)
    if await _shoot(
        ctx,
        name,
        shot,
        url=f"http://127.0.0.1:{port}",
        width=SHOT_WIDTH,
        height=CARD_HEIGHT,
        scale=CARD_SHOT_SCALE,
    ):
        await _compose(ctx, sites, conversation_id, name, shot, CARD_SHOT_DRAWN)


async def draw_from_stored_shot(
    ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, blob_key: str
) -> None:
    """Compose this site's card from the page picture a past deploy stored, for a site that has one
    and no card — every site deployed before cards existed.

    The picture goes back into a container to be composed, because composing is a browser's job and
    the browser is in the sandbox. What it draws is the site's own front page, and it is drawn on
    the act that publishes that page to anyone holding the link."""
    stored = STORED_SHOT.format(site=name)
    try:
        await ctx.sandbox.write_file(stored, await ctx.blob.get(blob_key))
    except OSError as refused:
        _undrawn(name, refused)
        return
    await _compose(ctx, sites, conversation_id, name, stored, STORED_SHOT_DRAWN)


async def _shoot(
    ctx: ToolContext, name: str, shot: str, *, url: str, width: int, height: int, scale: int
) -> bool:
    """Draw one shot into `shot`, answering whether it landed.

    The name is emptied first, through the guarded write every copy-in runs through, and the driver
    puts the picture down through the same guard: the name sits in a directory the agent writes.
    Emptied rather than deleted, because a non-empty shot is the command's verdict and the name is
    the same one this site's last deploy drew into — a run that refuses what the page drew leaves it
    empty, and the site keeps the picture it had."""
    try:
        await ctx.sandbox.write_file(shot, b"")
    except OSError as refused:
        _undrawn(name, refused)
        return False
    result = await ctx.sandbox.bash(
        shot_command(
            url=url,
            width=width,
            height=height,
            scale=scale,
            shot=shot,
            profile=CARD_PROFILE_DIR,
            root=WORKSPACE_DIR,
        ),
        timeout_s=CARD_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        _undrawn(name, result.stderr.strip() or result.stdout.strip())
        return False
    return True


async def _compose(
    ctx: ToolContext,
    sites: HostedSites,
    conversation_id: UUID,
    name: str,
    shot: str,
    drawn: str,
) -> None:
    """Draw the card from a shot already in the sandbox, store it, and write it onto the site's row.

    One chromium pass draws the whole card: the page it draws holds the panel's markup and the shot
    as a `data:` URI, so the browser that lays the card out is what scales the shot, and no step
    pastes anything over the site's own pixels. The row is written last, so a card that never
    encoded or never stored leaves the site with the one it had."""
    page = CARD_PAGE.format(site=name)
    try:
        await ctx.sandbox.write_file(page, card_page(name, drawn).encode())
    except OSError as refused:
        _undrawn(name, refused)
        return
    placed = await ctx.sandbox.python(
        PAGE_PROG.format(limit=SHOT_BYTES_MAX, token=SHOT_TOKEN),
        page,
        shot,
        WORKSPACE_DIR,
        timeout_s=CARD_TIMEOUT_SECONDS,
    )
    if placed.exit_code != 0:
        _undrawn(name, placed.stderr.strip() or placed.stdout.strip())
        return
    drawn_card = CARD_DRAWN.format(site=name)
    if not await _shoot(
        ctx, name, drawn_card, url=page, width=CARD_WIDTH, height=CARD_HEIGHT, scale=1
    ):
        return
    card = CARD_FILE.format(site=name)
    encoded = await ctx.sandbox.python(
        ENCODE_PROG.format(quality=CARD_QUALITY),
        drawn_card,
        card,
        WORKSPACE_DIR,
        timeout_s=CARD_TIMEOUT_SECONDS,
    )
    digest = encoded.stdout.strip()
    if encoded.exit_code != 0 or not digest:
        _undrawn(name, encoded.stderr.strip() or encoded.stdout.strip())
        return
    stored = await ctx.store_preview(card, CARD_NAME, extension=CARD_EXTENSION)
    if stored is None:
        return
    await sites.set_share_card(conversation_id, name, stored.blob_key, digest)


def _undrawn(name: str, detail: object) -> None:
    log("site_card.undrawn", site=name, detail=str(detail)[:DETAIL_CHARS])
