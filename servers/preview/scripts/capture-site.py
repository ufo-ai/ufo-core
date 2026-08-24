import base64
import json
import os
import select
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

LOAD_WALL = 12
SETTLE_WALL = 5
FRAME = 0.25
BROWSER_FD = 3
PAGE_FD = 4
SPARE_FD = 20
POLL = 0.05
READ_BYTES = 65536


def main():
    browser, url, host, width, height, shot, profile = sys.argv[1:8]
    flags = [
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-component-update",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-client-side-phishing-detection",
        "--password-store=basic",
        "--use-mock-keychain",
        "--no-service-autorun",
        "--disable-breakpad",
        "--disable-crash-reporter",
        "--disable-domain-reliability",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-features=MediaRouter,DialMediaRouteProvider,OptimizationHints,Translate",
        "--hide-scrollbars",
        "--renderer-process-limit=1",
        "--js-flags=--max-old-space-size=128",
        "--remote-debugging-pipe",
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE " + host,
        "--user-data-dir=" + profile,
        "about:blank",
    ]
    driven = Driven([browser, *flags], url)
    try:
        picture = settled(driven, url, int(width), int(height))
        with Path(shot).open("xb") as target:
            target.write(picture)
    finally:
        driven.stop()


def settled(driven, url, width, height):
    session = driven.attach()
    driven.call("Page.enable", None, session)
    driven.call("Network.enable", None, session)
    driven.call("Fetch.enable", dict(patterns=[dict(urlPattern="*")]), session)
    metrics = dict(width=width, height=height, deviceScaleFactor=1, mobile=False)
    driven.call("Emulation.setDeviceMetricsOverride", metrics, session)
    driven.call("Page.navigate", dict(url=url), session)
    loading = time.monotonic() + LOAD_WALL
    while not driven.loaded() and time.monotonic() < loading:
        driven.pump(POLL)
    settling = time.monotonic() + SETTLE_WALL
    previous = None
    while True:
        frame = driven.frame(session)
        if frame == previous and driven.inflight() == 0:
            return frame
        if time.monotonic() >= settling:
            return frame
        previous = frame
        driven.pump(FRAME)


def spare(pair, first):
    moved = []
    for offset, held in enumerate(pair):
        target = first + offset
        if held == target:
            os.set_inheritable(held, True)
        else:
            os.dup2(held, target)
            os.close(held)
        moved.append(target)
    return moved


class Driven:
    def __init__(self, command, url):
        speaking = spare(os.pipe(), SPARE_FD)
        answering = spare(os.pipe(), SPARE_FD + 2)
        actions = [
            (os.POSIX_SPAWN_DUP2, speaking[0], BROWSER_FD),
            (os.POSIX_SPAWN_DUP2, answering[1], PAGE_FD),
            *((os.POSIX_SPAWN_CLOSE, held) for held in speaking + answering),
        ]
        self.pid = os.posix_spawn(command[0], command, os.environ, file_actions=actions)
        os.close(speaking[0])
        os.close(answering[1])
        self.speaking = speaking[1]
        self.answering = answering[0]
        self.pending = b""
        self.seen = []
        self.calls = 0
        self.origin = origin(url)

    def attach(self):
        target = self.call("Target.createTarget", dict(url="about:blank"), None)
        attached = dict(targetId=target["targetId"], flatten=True)
        return self.call("Target.attachToTarget", attached, None)["sessionId"]

    def frame(self, session):
        result = self.call("Page.captureScreenshot", dict(format="png"), session)
        return base64.b64decode(result["data"])

    def loaded(self):
        return any(event.get("method") == "Page.loadEventFired" for event in self.seen)

    def inflight(self):
        started = set()
        ended = set()
        for event in self.seen:
            method = event.get("method")
            request = event.get("params", {}).get("requestId")
            if method == "Network.requestWillBeSent":
                started.add(request)
            elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                ended.add(request)
        return len(started - ended)

    def call(self, method, params, session):
        self.calls += 1
        call_id = self.calls
        message = dict(id=call_id, method=method, params=params or {})
        if session is not None:
            message["sessionId"] = session
        os.write(self.speaking, json.dumps(message).encode() + b"\0")
        deadline = time.monotonic() + LOAD_WALL
        while time.monotonic() < deadline:
            answer = self.read(deadline - time.monotonic())
            if answer is None:
                continue
            if answer.get("id") != call_id:
                self.accept(answer)
                continue
            if "error" in answer:
                raise SystemExit(method + ": " + json.dumps(answer["error"]))
            return answer.get("result", {})
        raise SystemExit(method + ": the browser stopped answering")

    def pump(self, wait):
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            event = self.read(deadline - time.monotonic())
            if event is not None:
                self.accept(event)

    def accept(self, event):
        if event.get("method") != "Fetch.requestPaused":
            self.seen.append(event)
            return
        params = event.get("params", {})
        request = params.get("request", {})
        parsed = urlsplit(request.get("url", ""))
        allowed = (
            parsed.scheme in ("data", "blob", "about")
            or origin(request.get("url", "")) == self.origin
        )
        method = "Fetch.continueRequest" if allowed else "Fetch.failRequest"
        command = dict(requestId=params.get("requestId"))
        if not allowed:
            command["errorReason"] = "BlockedByClient"
        self.calls += 1
        message = dict(id=self.calls, method=method, params=command)
        session = event.get("sessionId")
        if session is not None:
            message["sessionId"] = session
        os.write(self.speaking, json.dumps(message).encode() + b"\0")

    def read(self, wait):
        while b"\0" not in self.pending:
            if wait <= 0:
                return None
            ready = select.select([self.answering], [], [], min(wait, POLL))[0]
            if not ready:
                return None
            chunk = os.read(self.answering, READ_BYTES)
            if not chunk:
                raise SystemExit("the browser closed the protocol pipe")
            self.pending += chunk
        raw, self.pending = self.pending.split(b"\0", 1)
        return json.loads(raw)

    def stop(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass


def origin(url):
    parsed = urlsplit(url)
    port = parsed.port
    if port is None:
        port = {"http": 80, "https": 443}.get(parsed.scheme)
    return parsed.scheme, parsed.hostname, port


main()
