"""The browser pack's proof: its tools drive the turn's browser surface, and its profile registers.

The tools reach the browser only through `_browser(ctx)` — the one per-turn `BuaSurface` built from
the selected cdp provider and cached against the turn. Two seams are proved here without a live
Chrome. The inversion: a fake `CdpProvider` on the context is leased exactly once per turn, the
built surface is cached across tool calls, and its `aclose` (which releases the lease) is registered
on `ctx.cleanup` for the loop to drain. The marshalling: the tools' own params shape, dropped
blank-tab defaults and workspace writes, asserted by seeding a recording
stand-in into the per-turn cache so `_browser` returns it — a dependency stand-in, never the thing
asserted. The BUA engine keeps its own live-CDP end-to-end proof in test_browser_engine.py."""

import base64
import json
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_browser.bua.backend as backend_module
import ufo_ext_browser.bua.session as session_module
import ufo_ext_browser.manifest as browser_manifest
import ufo_ext_browser.tools as browser_tools
from pydantic import JsonValue
from ufo_ext_browser.bua.backend import CDP_TOKEN_KEY, MAX_READ_BYTES, BuaSurface
from ufo_ext_browser.bua.downloads import Download
from ufo_ext_browser.bua.session import BrowserSession
from ufo_ext_browser.subagent import (
    BROWSER_PROFILE,
    BROWSER_SUBAGENT_PROMPT,
    BrowserTask,
)
from ufo_ext_browser.tools import BROWSER_TOOL_NAMES, BROWSER_TOOLS

from ufo.blob import FilesystemBlobStore
from ufo.browser import CdpEndpoint, CdpLease, CdpProvider, FileBytes, SessionGone
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import (
    ExecResult,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.runtime.engine import MAIN_ROUND_LIMIT
from ufo.runtime.ext.context import ScopedStore
from ufo.runtime.ext.manifest import SUBAGENT_ROUND_LIMIT
from ufo.runtime.subagents import FINISH_CONTRACT, subagent_system_prompt
from ufo.runtime.tools.context import ImageContent, SpawnResult, ToolContext, TurnCleanup
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOOL_NARRATION = "clicking through the page"


@dataclass
class RecordingSurface:
    """Answers each browser tool with a canned reply and records the (method, args) it received —
    the stand-in for a live surface seeded into the per-turn cache, never asserted itself."""

    reply: dict[str, JsonValue]
    calls: list[tuple[str, dict[str, JsonValue]]] = field(default_factory=list)
    attached: list[int] = field(default_factory=list)

    def _record(self, method: str, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.calls.append((method, args))
        return dict(self.reply)

    async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("navigate", args)

    async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("tabs_context", args)

    async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("tabs_create", args)

    async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("tabs_close", args)

    async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("upload_file", args)

    async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("read_page", args)

    async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("get_page_text", args)

    async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("find", args)

    async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("form_input", args)

    async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("computer", args)

    async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self._record("wait_for_download", args)

    async def attached_sizes(self, args: dict[str, JsonValue]) -> list[int]:
        return list(self.attached)

    async def aclose(self) -> None:
        return None


class _StopAtConnect(RuntimeError):
    """The fake lease's `endpoint` raises this to short-circuit `_open` before a real websocket
    dial, so the lease-lifecycle can be proved without a live Chrome."""


@dataclass
class FakeCdpLease:
    """Records that it was released; its `endpoint` never yields, so `_open` stops before a dial.
    `token` is the session id it is bound to — a fresh id when minted, the reattached id when
    reconnected. A real `CdpLease` the surface leases and releases, not a mock."""

    session_id: str = "session"
    released: bool = False

    async def endpoint(self) -> CdpEndpoint:
        raise _StopAtConnect()

    async def token(self) -> str:
        return self.session_id

    async def place_file(self, path: str, read: FileBytes) -> str:
        return path

    async def download_dir(self) -> str:
        return "/tmp/ufo-downloads"

    async def fetch_download(self, guid: str) -> bytes:
        return b""

    async def aclose(self) -> None:
        self.released = True


@dataclass
class FakeCdpProvider:
    """A real `CdpProvider` modelling a hosted provider: each `lease` mints a fresh session id,
    while `reattach` reconnects to the exact session its token names (unless `gone`, when it raises
    `SessionGone`). Records mints and reattaches so a test can prove a recovered turn reattaches to
    the live session rather than minting a new one."""

    leases: list[FakeCdpLease] = field(default_factory=list)
    reattached: list[str] = field(default_factory=list)
    reattached_sandboxes: list[SandboxSession | None] = field(default_factory=list)
    leased_sandboxes: list[SandboxSession | None] = field(default_factory=list)
    gone: bool = False
    _minted: int = 0

    async def lease(self, sandbox: SandboxSession | None = None) -> CdpLease:
        self._minted += 1
        self.leased_sandboxes.append(sandbox)
        lease = FakeCdpLease(session_id=f"session-{self._minted}")
        self.leases.append(lease)
        return lease

    async def reattach(self, token: str, sandbox: SandboxSession | None = None) -> CdpLease:
        self.reattached.append(token)
        self.reattached_sandboxes.append(sandbox)
        if self.gone:
            raise SessionGone(token)
        return FakeCdpLease(session_id=token)


@dataclass
class WritesCarrier:
    """Records the workspace writes the tools drive (screenshot, download) and refuses any browser
    reach — the browser is driven through the surface, never the sandbox exec seam."""

    writes: list[tuple[str, bytes]] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("browser tools do not create containers")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        self.writes.append((path, content))

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)


@dataclass
class FileCarrier:
    """Serves the two reads the surface makes of a workspace file it must ship to a remote browser
    (its size, then its bytes) and records every command with the budget it was given, so a test can
    prove a sandbox-local transport reads nothing at all and that each read states its own wait.
    `deadline_on` expires the command whose prefix it names, which is the carrier killing it rather
    than a failure the command reported for itself."""

    files: dict[str, bytes] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    timeouts: list[int] = field(default_factory=list)
    deadline_on: str | None = None

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("browser tools do not create containers")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        self.files[path] = content

    async def exec(
        self,
        handle: SandboxHandle,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        command = argv[-1]
        self.commands.append(command)
        self.timeouts.append(timeout_s)
        if self.deadline_on is not None and command.startswith(self.deadline_on):
            return ExecResult(
                stdout="", stderr="timed out", exit_code=124, timed_out_after_s=timeout_s
            )
        for path, content in self.files.items():
            if shlex.quote(path) not in command:
                continue
            if command.startswith("stat"):
                return ExecResult(stdout=f"{len(content)}\n", stderr="", exit_code=0)
            if command.startswith("base64"):
                return ExecResult(stdout=base64.b64encode(content).decode(), stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="no such file", exit_code=1)


@dataclass
class UploadingLease:
    """A remote transport's lease: `place_file` ships the bytes it is handed and answers where it
    put them, exactly as the browserbase provider does. Records what it received so a test can prove
    the surface read the sandbox's real bytes."""

    uploaded: list[tuple[str, bytes]] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)

    async def endpoint(self) -> CdpEndpoint:
        raise _StopAtConnect()

    async def token(self) -> str:
        return "remote-session"

    async def place_file(self, path: str, read: FileBytes) -> str:
        data = await read()
        name = path.rsplit("/", 1)[-1]
        self.uploaded.append((name, data))
        return f"/tmp/.uploads/{name}"

    async def download_dir(self) -> str:
        return "downloads"

    async def fetch_download(self, guid: str) -> bytes:
        self.fetched.append(guid)
        return b"downloaded bytes"

    async def aclose(self) -> None:
        return None


@dataclass
class SandboxLocalLease:
    """A transport whose Chrome shares the turn's sandbox: `place_file` answers the path it was
    given and leaves `read` unawaited, the shape sandbox_chrome's own lease implements."""

    async def endpoint(self) -> CdpEndpoint:
        raise _StopAtConnect()

    async def token(self) -> str:
        return "sandbox-session"

    async def place_file(self, path: str, read: FileBytes) -> str:
        return path

    async def download_dir(self) -> str:
        return "/tmp/ufo-downloads"

    async def fetch_download(self, guid: str) -> bytes:
        return b""

    async def aclose(self) -> None:
        return None


def _staging_surface(carrier: FileCarrier, lease: CdpLease) -> tuple[BuaSurface, RecordingSurface]:
    """A surface already holding its lease and an open session, which is the state every upload runs
    in: `upload_file` stages the paths through the lease, then drives CDP through the session."""
    session = RecordingSurface(reply={"ok": True})
    surface = BuaSurface(
        cdp_provider=cast(CdpProvider, None),
        find_completer=None,
        model=None,
        sandbox=SandboxSession(
            carrier=carrier, handle=SandboxHandle(conversation_id=uuid4(), container_id="test")
        ),
        lease=lease,
        session=cast(object, session),  # type: ignore[arg-type]
    )
    return surface, session


async def test_upload_through_a_remote_transport_ships_the_sandboxs_bytes() -> None:
    """The producer half of `place_file`: a remote browser cannot open a workspace path, so the
    surface reads the file out of the turn's sandbox, hands it to the lease, and drives CDP against
    the location the transport answers — never the workspace path."""
    carrier = FileCarrier(files={"/workspace/report.pdf": b"%PDF-1.7 body"})
    lease = UploadingLease()
    surface, session = _staging_surface(carrier, lease)
    session.attached = [len(b"%PDF-1.7 body")]
    await surface.upload_file({"ref": "ref_3", "files": ["/workspace/report.pdf"]})
    assert lease.uploaded == [("report.pdf", b"%PDF-1.7 body")]
    assert session.calls[-1] == (
        "upload_file",
        {"ref": "ref_3", "files": ["/tmp/.uploads/report.pdf"]},
    )


async def test_upload_through_a_sandbox_local_transport_copies_nothing() -> None:
    """The other half: a transport whose Chrome shares the sandbox answers the path unchanged (what
    sandbox_chrome's lease does), so the surface must not pull the file out of the sandbox to hand a
    browser a file it can already open."""
    carrier = FileCarrier(files={"/workspace/report.pdf": b"%PDF-1.7 body"})
    surface, session = _staging_surface(carrier, SandboxLocalLease())
    await surface.upload_file({"ref": "ref_3", "files": ["/workspace/report.pdf"]})
    assert session.calls[-1] == (
        "upload_file",
        {"ref": "ref_3", "files": ["/workspace/report.pdf"]},
    )
    assert carrier.commands == []


async def test_upload_refuses_a_file_larger_than_the_read_cap() -> None:
    carrier = FileCarrier(files={"/workspace/huge.bin": b"x" * (MAX_READ_BYTES + 1)})
    lease = UploadingLease()
    surface, session = _staging_surface(carrier, lease)
    with pytest.raises(ValueError, match="upload limit"):
        await surface.upload_file({"ref": "ref_3", "files": ["/workspace/huge.bin"]})
    assert lease.uploaded == []
    assert session.calls == []


class _StopBootstrap(RuntimeError):
    """Ends `_bootstrap` once the download behaviour has been sent, so the payload can be read
    without standing up every later CDP exchange."""


@dataclass
class RecordingConnection:
    """Stands in for the websocket so the REAL `_bootstrap` runs and its emitted CDP calls can be
    read back — the payload is the thing asserted, this is only the wire under it."""

    sent: list[tuple[str, dict[str, JsonValue]]] = field(default_factory=list)

    async def send(
        self,
        method: str,
        params: dict[str, JsonValue] | None = None,
        session_id: str | None = None,
    ) -> dict[str, JsonValue]:
        self.sent.append((method, params or {}))
        if method == "Browser.getVersion":
            return {"userAgent": "X11; Linux"}
        if method == "Target.getTargets":
            raise _StopBootstrap()
        return {}

    def on(self, event: str, handler: object) -> None:
        return None

    async def close(self) -> None:
        return None

    def payload(self, method: str) -> dict[str, JsonValue]:
        for name, params in self.sent:
            if name == method:
                return params
        raise AssertionError(f"{method} was never sent: {[n for n, _ in self.sent]}")


async def _bootstrap_payloads(
    monkeypatch: pytest.MonkeyPatch, download_dir: str
) -> RecordingConnection:
    recorder = RecordingConnection()

    class _Opener:
        @classmethod
        async def open(cls, ws_url: str, headers: dict[str, str] | None = None) -> object:
            return recorder

    monkeypatch.setattr(session_module, "CdpConnection", _Opener)
    session = BrowserSession(
        cdp=CdpEndpoint(url="ws://browser.test/devtools"),
        download_dir=download_dir,
    )
    with pytest.raises(_StopBootstrap):
        await session.open()
    return recorder


async def test_an_upload_that_never_lands_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:

    @dataclass
    class _NeverFills(RecordingSurface):
        async def attached_sizes(self, args: dict[str, JsonValue]) -> list[int]:
            return [0]

    monkeypatch.setattr(backend_module, "UPLOAD_SETTLE_ATTEMPTS", 3)
    monkeypatch.setattr(backend_module, "UPLOAD_SETTLE_SLEEP_SECONDS", 0.0)
    carrier = FileCarrier(files={"/workspace/report.pdf": b"%PDF-1.7 body"})
    surface, _ = _staging_surface(carrier, UploadingLease())
    surface.session = cast(BrowserSession, _NeverFills(reply={"ok": True}))
    with pytest.raises(RuntimeError, match="never received the uploaded file"):
        await surface.upload_file({"ref": "ref_3", "files": ["/workspace/report.pdf"]})


async def test_a_download_reports_only_a_file_name_never_a_path() -> None:
    """The visited page chooses the download's name and a caller joins it into a workspace path, so
    a name carrying `../` would put the write in a directory that caller never asked for. What
    comes back is one path segment."""

    @dataclass
    class _NamesATraversal(RecordingSurface):
        suggested: str = "../../notes.md"

        async def wait_for_download(self, args: dict[str, JsonValue]) -> Download:
            return Download(guid="guid-7", filename=self.suggested, state="completed")

    for suggested, expected in (
        ("../../notes.md", "notes.md"),
        ("/etc/passwd", "passwd"),
        ("report.pdf", "report.pdf"),
        ("..", "download"),
    ):
        surface, _ = _staging_surface(FileCarrier(), UploadingLease())
        surface.session = cast(BrowserSession, _NamesATraversal(reply={}, suggested=suggested))
        assert (await surface.wait_for_download({}))["filename"] == expected


async def test_upload_refuses_a_path_outside_the_workspace() -> None:
    """`upload_file` reads through an unconfined shell and a remote transport ships what it reads,
    so a path escaping the workspace must never reach either."""
    carrier = FileCarrier(files={"/etc/passwd": b"root:x:0:0"})
    lease = UploadingLease()
    surface, session = _staging_surface(carrier, lease)
    for escape in ("/etc/passwd", "../../etc/passwd", "/workspace/../etc/passwd"):
        with pytest.raises(ValueError, match="escape"):
            await surface.upload_file({"ref": "ref_3", "files": [escape]})
    assert lease.uploaded == []
    assert carrier.commands == []
    assert session.calls == []


async def test_upload_surfaces_a_missing_workspace_file() -> None:
    surface, session = _staging_surface(FileCarrier(), UploadingLease())
    with pytest.raises(ValueError, match="no such file"):
        await surface.upload_file({"ref": "ref_3", "files": ["/workspace/gone.pdf"]})
    assert session.calls == []


async def _no_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("browser tools do not spawn subagents")


def _context(
    carrier: WritesCarrier, tmp_path: Path, cdp_provider: CdpProvider | None = None
) -> ToolContext:
    return ToolContext(
        sandbox=SandboxSession(
            carrier=carrier,
            handle=SandboxHandle(conversation_id=uuid4(), container_id="test"),
        ),
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        cdp_provider=cdp_provider,
    )


def _recording_context(
    surface: RecordingSurface, carrier: WritesCarrier, tmp_path: Path
) -> ToolContext:
    """A context whose per-turn surface cache is pre-seeded with the recording stand-in, so
    `_browser(ctx)` returns it and the tools' marshalling into it is what the test asserts."""
    ctx = _context(carrier, tmp_path)
    browser_tools._TURN_SURFACES[ctx.cleanup] = cast(BuaSurface, surface)
    return ctx


async def _run(name: str, ctx: ToolContext, **args: object) -> object:
    tool = next(tool for tool in BROWSER_TOOLS if tool.name == name)
    return await tool.handler(ctx, tool.input_model.model_validate({**args}))


def test_raw_browser_tools_are_profile_only_and_delegation_is_not() -> None:
    """Main agents never hold the raw browser surface: every browser/computer-use tool is
    profile-only (reachable via the browser profile's tool_names), while the delegation pair a
    main agent keeps is not."""
    assert all(tool.profile_only for tool in BROWSER_TOOLS)
    for tool in browser_manifest.manifest().tools:
        assert tool.profile_only == (tool.name in BROWSER_TOOL_NAMES)


async def test_the_surface_is_built_once_per_turn_leased_and_released_on_cleanup(
    tmp_path: Path,
) -> None:
    """The inversion end to end: the first browser-tool call builds one `BuaSurface` from the turn's
    cdp provider and registers its `aclose` on `ctx.cleanup`; the provider is leased once and the
    surface cached, so a second tool call reuses both; and draining the cleanup registry (what the
    loop does at turn end) releases the lease — the turn never leaks a CDP connection."""
    provider = FakeCdpProvider()
    ctx = _context(WritesCarrier(), tmp_path, cdp_provider=provider)

    with pytest.raises(_StopAtConnect):
        await _run("navigate", ctx, url="https://x.test")
    surface = browser_tools._TURN_SURFACES[ctx.cleanup]
    assert isinstance(surface, BuaSurface)
    assert len(provider.leases) == 1

    with pytest.raises(_StopAtConnect):
        await _run("read_page", ctx)
    assert browser_tools._TURN_SURFACES[ctx.cleanup] is surface
    assert len(provider.leases) == 1

    assert provider.leases[0].released is False
    await ctx.cleanup.drain()
    assert provider.leases[0].released is True


async def test_a_tool_without_a_cdp_provider_fails_loud(tmp_path: Path) -> None:
    ctx = _context(WritesCarrier(), tmp_path, cdp_provider=None)
    with pytest.raises(RuntimeError, match="no cdp provider is configured"):
        await _run("navigate", ctx, url="x")


async def test_tabs_context_sends_empty_params(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"tabs": []})
    await _run("tabs_context", _recording_context(surface, WritesCarrier(), tmp_path))
    assert surface.calls[-1] == ("tabs_context", {})


async def test_tabs_create_defaults_to_blank(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"tab_id": 3})
    await _run(
        "tabs_create",
        _recording_context(surface, WritesCarrier(), tmp_path),
    )
    assert surface.calls[-1] == ("tabs_create", {"url": "about:blank"})


async def test_read_page_keeps_filter(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"tree": "root"})
    await _run(
        "read_page",
        _recording_context(surface, WritesCarrier(), tmp_path),
        depth=2,
        filter="interactive",
    )
    assert surface.calls[-1] == ("read_page", {"depth": 2, "filter": "interactive"})


async def test_upload_file_passes_the_workspace_paths(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"ok": True})
    await _run(
        "upload_file",
        _recording_context(surface, WritesCarrier(), tmp_path),
        ref="ref_9",
        files=["a.pdf", "b.pdf"],
    )
    assert surface.calls[-1] == ("upload_file", {"ref": "ref_9", "files": ["a.pdf", "b.pdf"]})


async def test_tabs_close_forwards_the_tab_and_nothing_else(tmp_path: Path) -> None:
    """The narration a member reads is the surface's to show, never a parameter the browser backend
    is handed: closing a tab forwards the tab it targets and, with none named, an empty payload the
    backend reads as the active tab."""
    surface = RecordingSurface(reply={"ok": True})
    ctx = _recording_context(surface, WritesCarrier(), tmp_path)
    await _run("tabs_close", ctx, tab_id=2)
    assert surface.calls[-1] == ("tabs_close", {"tab_id": 2})
    await _run("tabs_close", ctx)
    assert surface.calls[-1] == ("tabs_close", {})


async def test_computer_saves_screenshot_into_the_workspace(tmp_path: Path) -> None:
    encoded = base64.b64encode(b"png-bytes").decode()
    carrier = WritesCarrier()
    surface = RecordingSurface(reply={"screenshot_base64": encoded})
    result = await _run(
        "computer",
        _recording_context(surface, carrier, tmp_path),
        actions=[{"action": "screenshot"}],
        save_to_workspace=True,
    )
    assert surface.calls[-1][0] == "computer"
    assert ("/workspace/browser-screenshot.jpg", b"png-bytes") in carrier.writes
    assert json.loads(result.content[0].text)["screenshot_path"] == "browser-screenshot.jpg"
    assert "screenshot_base64" not in json.loads(result.content[0].text)
    image = result.content[1]
    assert isinstance(image, ImageContent)
    assert image.media_type == "image/jpeg"
    assert image.data == encoded


async def test_computer_without_save_writes_nothing(tmp_path: Path) -> None:
    carrier = WritesCarrier()
    surface = RecordingSurface(reply={"screenshot_base64": base64.b64encode(b"x").decode()})
    await _run(
        "computer",
        _recording_context(surface, carrier, tmp_path),
        actions=[{"action": "left_click", "coordinate": [1, 2]}],
    )
    assert carrier.writes == []
    assert surface.calls[-1] == (
        "computer",
        {"actions": [{"action": "left_click", "coordinate": [1, 2]}]},
    )


def test_a_browser_session_runs_at_the_main_ceiling_and_a_narrowed_spawn_does_not() -> None:
    """The producer half of the round budget. `extended_context` rides the child's inbound payload,
    which is where the queue reads it to lift a subagent to MAIN_ROUND_LIMIT, so it has to survive
    the input model's own serialization: on by default, so browser_task and a bare spawn
    both get a session that can page through a site, and off when a caller sends it off —
    wide_browse, which then takes the profile's declared budget."""
    assert BROWSER_PROFILE.max_rounds == SUBAGENT_ROUND_LIMIT
    assert SUBAGENT_ROUND_LIMIT < MAIN_ROUND_LIMIT
    default = json.loads(BrowserTask.model_validate({"task": "browse"}).model_dump_json())
    assert default["extended_context"] is True
    narrowed = json.loads(
        BrowserTask.model_validate({"task": "browse", "extended_context": False}).model_dump_json()
    )
    assert narrowed["extended_context"] is False


def test_browser_prompt_preserves_no_skill_index_slot() -> None:
    assert "{{" not in BROWSER_SUBAGENT_PROMPT
    assert "skill_index" not in BROWSER_SUBAGENT_PROMPT
    assert subagent_system_prompt(BROWSER_PROFILE).endswith(FINISH_CONTRACT)


async def test_aclose_releases_the_lease_even_when_session_close_raises() -> None:
    """A broken CDP socket on an errored turn makes the session close raise; the transport lease
    (a paid hosted browser, browserbase-style) must still be released, never orphaned."""

    class _RaisingSession:
        async def close(self) -> None:
            raise RuntimeError("CDP websocket already broken")

    lease = FakeCdpLease()
    surface = BuaSurface(
        cdp_provider=FakeCdpProvider(),
        find_completer=None,
        model=None,
        lease=lease,
        session=_RaisingSession(),
    )
    with pytest.raises(RuntimeError):
        await surface.aclose()
    assert lease.released is True


async def test_turn_cleanup_drain_isolates_a_failing_closer() -> None:
    """One closer raising during drain must not skip the rest — else a failed CDP-session close
    would strand the lease closer registered beside it."""
    cleanup = TurnCleanup()
    ran: list[str] = []

    async def ok() -> None:
        ran.append("ok")

    async def boom() -> None:
        raise RuntimeError("teardown failed")

    cleanup.register(ok)
    cleanup.register(boom)  # LIFO: popped first; its raise must not skip `ok`
    await cleanup.drain()  # must not propagate
    assert ran == ["ok"]


async def _token_store(workspace_id: UUID) -> ScopedStore:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return ScopedStore(extension="browser")


def _surface(
    provider: FakeCdpProvider,
    store: ScopedStore,
    conversation_id: UUID,
    sandbox: SandboxSession | None = None,
) -> BuaSurface:
    return BuaSurface(
        cdp_provider=provider,
        find_completer=None,
        model=None,
        sandbox=sandbox,
        store=store,
        conversation_id=conversation_id,
    )


async def test_a_recovered_turn_reattaches_to_the_live_cdp_session_via_the_durable_token(
    db: None,
) -> None:
    """Phase 3, both ends: the surface persists the lease's reattach token under its conversation's
    key on first use, so a hard crash (which skips aclose, leaving the token) followed by a recovery
    replay — a fresh surface for the same conversation — reattaches to the live session rather than
    minting a new one. Proven without a live Chrome: the fake lease's endpoint short-circuits before
    a dial, but the lease acquisition (reattach vs mint) is exactly what recovery hinges on."""
    workspace_id, conversation_id = uuid4(), uuid4()
    store = await _token_store(workspace_id)
    provider = FakeCdpProvider()
    sandbox = SandboxSession(
        carrier=WritesCarrier(),  # type: ignore[arg-type]
        handle=SandboxHandle(conversation_id=conversation_id, container_id="test"),
    )

    with ws(workspace_id):
        with pytest.raises(_StopAtConnect):
            await _surface(provider, store, conversation_id, sandbox)._open()
        assert len(provider.leases) == 1
        assert provider.reattached == []
        token = provider.leases[0].session_id
        assert await store.get(CDP_TOKEN_KEY.format(conversation_id=conversation_id)) == token

        with pytest.raises(_StopAtConnect):
            await _surface(provider, store, conversation_id, sandbox)._open()
        assert provider.reattached == [token]
        assert provider.reattached_sandboxes == [sandbox]
        assert len(provider.leases) == 1


async def test_aclose_clears_the_durable_token_so_a_later_turn_never_reattaches_it(
    db: None,
) -> None:
    """A clean turn end releases the session and clears its token, so the next turn on the same
    conversation mints fresh — only a crash (which skips aclose) leaves a token to reattach."""
    workspace_id, conversation_id = uuid4(), uuid4()
    store = await _token_store(workspace_id)
    provider = FakeCdpProvider()
    surface = _surface(provider, store, conversation_id)

    with ws(workspace_id):
        with pytest.raises(_StopAtConnect):
            await surface._open()
        assert await store.get(CDP_TOKEN_KEY.format(conversation_id=conversation_id)) is not None
        await surface.aclose()
        assert await store.get(CDP_TOKEN_KEY.format(conversation_id=conversation_id)) is None
        assert provider.leases[0].released is True
