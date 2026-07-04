"""The browser pack's proof: its tools drive the turn's browser surface, and its profile registers.

The tools reach the browser only through `_browser(ctx)` — the one per-turn `BuaSurface` built from
the selected cdp provider and cached against the turn. Two seams are proved here without a live
Chrome. The inversion: a fake `CdpProvider` on the context is leased exactly once per turn, the
built surface is cached across tool calls, and its `aclose` (which releases the lease) is registered
on `ctx.cleanup` for the loop to drain. The marshalling: the tools' own params shape, dropped
`user_description`, blank-tab default, and workspace writes, asserted by seeding a recording
stand-in into the per-turn cache so `_browser` returns it — a dependency stand-in, never the thing
asserted. The BUA engine keeps its own live-CDP end-to-end proof in test_browser_engine.py."""

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
import selfhost_ext_browser.manifest as browser_manifest
import selfhost_ext_browser.tools as browser_tools
from pydantic import JsonValue
from selfhost_ext_browser.bua.backend import BuaSurface
from selfhost_ext_browser.subagent import (
    BROWSER_PROFILE,
    BROWSER_SUBAGENT_NAME,
    BROWSER_SUBAGENT_PROMPT,
    BROWSER_SUBAGENT_TOOL_NAMES,
)
from selfhost_ext_browser.tools import BROWSER_TOOL_NAMES, BROWSER_TOOLS

from selfhost.blob import FilesystemBlobStore
from selfhost.browser import CdpEndpoint, CdpLease, CdpProvider
from selfhost.ext.loader import skill_registry, turn_subagents
from selfhost.loop.prompts.render import render_system_prompt
from selfhost.loop.subagents import SubagentRegistry, subagent_system_prompt
from selfhost.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import ImageContent, SpawnResult, ToolContext, TurnCleanup


@dataclass
class RecordingSurface:
    """Answers each browser tool with a canned reply and records the (method, args) it received —
    the stand-in for a live surface seeded into the per-turn cache, never asserted itself."""

    reply: dict[str, JsonValue]
    calls: list[tuple[str, dict[str, JsonValue]]] = field(default_factory=list)

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

    async def aclose(self) -> None:
        return None


class _StopAtConnect(RuntimeError):
    """The fake lease's `endpoint` raises this to short-circuit `_open` before a real websocket
    dial, so the lease-lifecycle can be proved without a live Chrome."""


@dataclass
class FakeCdpLease:
    """Records that it was released; its `endpoint` never yields, so `_open` stops before a dial.
    A real `CdpLease` the surface leases and releases, not a mock."""

    released: bool = False

    async def endpoint(self) -> CdpEndpoint:
        raise _StopAtConnect()

    async def aclose(self) -> None:
        self.released = True


@dataclass
class FakeCdpProvider:
    """A real `CdpProvider` recording every lease it mints, so the test can prove the turn leases
    exactly once and releases at cleanup."""

    leases: list[FakeCdpLease] = field(default_factory=list)

    async def lease(self) -> CdpLease:
        lease = FakeCdpLease()
        self.leases.append(lease)
        return lease


@dataclass
class WritesCarrier:
    """Records the workspace writes the tools drive (screenshot, download) and refuses any browser
    reach — the browser is driven through the surface, never the sandbox exec seam."""

    writes: list[tuple[str, bytes]] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("browser tools do not create containers")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.writes.append((argv[-1], stdin))
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None:
        raise AssertionError("browser tools do not destroy containers")


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
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        member_id=None,
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
    return await tool.handler(ctx, tool.input_model.model_validate(args))


def test_manifest_declares_the_browser_tools_and_profile() -> None:
    manifest = browser_manifest.manifest()
    assert {tool.name for tool in manifest.tools} == set(BROWSER_TOOL_NAMES) | {
        "browser_task",
        "wide_browse",
    }
    assert len(BROWSER_TOOL_NAMES) == 11
    assert {profile.name for profile in manifest.subagents} == {BROWSER_SUBAGENT_NAME}


def test_browser_operator_skill_parses_and_indexes() -> None:
    index = dict(skill_registry((browser_manifest.manifest(),)).index())
    assert "browser-operator" in index


def test_manifest_requires_the_cdp_providers_seam() -> None:
    """The consumer half of the `requires` seam: the browser pack declares it consumes
    `cdp_providers`, which serve's boot-validation resolves so a browser deploy with no cdp endpoint
    fails at boot rather than on the first browse."""
    assert browser_manifest.manifest().requires == ("cdp_providers",)


def test_page_derived_tools_are_marked_untrusted() -> None:
    untrusted = {tool.name for tool in BROWSER_TOOLS if tool.untrusted}
    assert untrusted == {"read_page", "get_page_text", "find", "tabs_context"}
    tabs_context = next(tool for tool in BROWSER_TOOLS if tool.name == "tabs_context")
    assert tabs_context.untrusted is True


def test_tool_descriptions_are_the_ported_verbatim_strings() -> None:
    described = {tool.name: tool.description for tool in BROWSER_TOOLS}
    assert described["navigate"] == "Navigate to a URL, or go forward/back in browser history."
    assert described["tabs_context"] == "Get context for all browser tabs."
    assert described["upload_file"] == "Set a file input from workspace paths."
    assert described["read_page"] == "Read the browser page accessibility tree."
    assert described["find"] == "Find browser page elements by role, text, name, or URL."
    assert (
        described["computer"]
        == "Interact with the browser using mouse, keyboard, wait, scroll, and screenshot actions."
    )
    assert (
        described["wait_for_download"]
        == "Wait for a browser download and write it to the workspace."
    )


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
        await _run("navigate", ctx, url="https://x.test", user_description="open")
    surface = browser_tools._TURN_SURFACES[ctx.cleanup]
    assert isinstance(surface, BuaSurface)
    assert len(provider.leases) == 1

    with pytest.raises(_StopAtConnect):
        await _run("read_page", ctx, user_description="inspect")
    assert browser_tools._TURN_SURFACES[ctx.cleanup] is surface
    assert len(provider.leases) == 1

    assert provider.leases[0].released is False
    await ctx.cleanup.drain()
    assert provider.leases[0].released is True


async def test_a_tool_without_a_cdp_provider_fails_loud(tmp_path: Path) -> None:
    ctx = _context(WritesCarrier(), tmp_path, cdp_provider=None)
    with pytest.raises(RuntimeError, match="no cdp provider is configured"):
        await _run("navigate", ctx, url="x", user_description="")


async def test_navigate_marshals_params_and_drops_user_description(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"tab_id": 1, "url": "https://example.com/"})
    result = await _run(
        "navigate",
        _recording_context(surface, WritesCarrier(), tmp_path),
        url="example.com",
        user_description="open",
        tab_id=2,
    )
    assert surface.calls[-1] == ("navigate", {"url": "example.com", "tab_id": 2})
    assert result.content[0].text == json.dumps({"tab_id": 1, "url": "https://example.com/"})
    assert result.is_error is False


async def test_tabs_context_sends_empty_params(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"tabs": []})
    await _run("tabs_context", _recording_context(surface, WritesCarrier(), tmp_path))
    assert surface.calls[-1] == ("tabs_context", {})


async def test_tabs_create_defaults_to_blank(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"tab_id": 3})
    await _run(
        "tabs_create",
        _recording_context(surface, WritesCarrier(), tmp_path),
        user_description="new",
    )
    assert surface.calls[-1] == ("tabs_create", {"url": "about:blank"})


async def test_read_page_excludes_user_description_keeps_filter(tmp_path: Path) -> None:
    surface = RecordingSurface(reply={"tree": "root"})
    await _run(
        "read_page",
        _recording_context(surface, WritesCarrier(), tmp_path),
        user_description="inspect",
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


async def test_computer_saves_screenshot_into_the_workspace(tmp_path: Path) -> None:
    encoded = base64.b64encode(b"png-bytes").decode()
    carrier = WritesCarrier()
    surface = RecordingSurface(reply={"screenshot_base64": encoded})
    result = await _run(
        "computer",
        _recording_context(surface, carrier, tmp_path),
        actions=[{"action": "screenshot"}],
        user_description="shoot",
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
        user_description="click",
    )
    assert carrier.writes == []
    assert surface.calls[-1] == (
        "computer",
        {"actions": [{"action": "left_click", "coordinate": [1, 2]}]},
    )


async def test_wait_for_download_writes_the_file_and_reports_its_path(tmp_path: Path) -> None:
    carrier = WritesCarrier()
    surface = RecordingSurface(
        reply={
            "filename": "report.pdf",
            "content_base64": base64.b64encode(b"pdf-bytes").decode(),
            "size": 9,
        }
    )
    result = await _run(
        "wait_for_download", _recording_context(surface, carrier, tmp_path), user_description="dl"
    )
    assert ("/workspace/downloads/report.pdf", b"pdf-bytes") in carrier.writes
    assert json.loads(result.content[0].text) == {
        "file_path": "downloads/report.pdf",
        "filename": "report.pdf",
        "size": 9,
    }


def test_manifest_contributes_the_browser_prompt_section_into_the_rendered_shell() -> None:
    """Both ends of the contribution seam: the browser pack declares a prompt section, and the same
    tuple the loop builds from `manifest.prompt_sections` renders into the shell's `{{sections}}`
    slot — so the browse-vs-search rules reach the agent's system prompt."""
    (section,) = browser_manifest.manifest().prompt_sections
    assert section.name == "browser"
    rendered = render_system_prompt("You are the assistant.", ((section.name, section.body),))
    assert "job boards directly with the browser" in rendered.content
    assert "no saved sessions or cookies" in rendered.content
    assert "{{" not in rendered.content


def test_browser_profile_registers_and_is_spawnable() -> None:
    registry = SubagentRegistry(turn_subagents((browser_manifest.manifest(),)))
    profile = registry.get(BROWSER_SUBAGENT_NAME)
    assert profile.tool_names == BROWSER_SUBAGENT_TOOL_NAMES
    available = set(BROWSER_TOOL_NAMES) | {tool.name for tool in BUILTIN_TOOLS}
    assert set(profile.tool_names) <= available
    assert "web automation subagent" in profile.prompt


def test_browser_prompt_preserves_no_skill_index_slot() -> None:
    assert "{{" not in BROWSER_SUBAGENT_PROMPT
    assert "skill_index" not in BROWSER_SUBAGENT_PROMPT
    assert "JSON" in subagent_system_prompt(BROWSER_PROFILE)


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
