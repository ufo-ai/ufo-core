"""The browser pack's proof: its tools drive the sandbox browser surface and its profile registers.

The tools reach the browser only through `SandboxSession.browser`, so a RecordingCarrier stands in
for the container — answering the helper's `exec` with a canned JSON reply and capturing what the
tools sent. It is a stand-in dependency, never the thing asserted: the tests assert the tools' own
marshalling (params shape, dropped `user_description`), their workspace writes (screenshot,
download), and that the browser profile flows through the loader into the SubagentRegistry a spawn
dispatches against."""

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import selfhost_ext_browser.manifest as browser_manifest
from selfhost_ext_browser.subagent import (
    BROWSER_PROFILE,
    BROWSER_SUBAGENT_NAME,
    BROWSER_SUBAGENT_PROMPT,
    BROWSER_SUBAGENT_TOOL_NAMES,
)
from selfhost_ext_browser.tools import BROWSER_TOOL_NAMES, BROWSER_TOOLS

from selfhost.blob import FilesystemBlobStore
from selfhost.ext.loader import turn_subagents
from selfhost.loop.prompts.render import render_system_prompt
from selfhost.loop.subagents import SubagentRegistry, subagent_system_prompt
from selfhost.sandbox.session import (
    BROWSER_HELPER,
    ExecResult,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import ImageContent, SpawnResult, ToolContext


@dataclass
class RecordingCarrier:
    """Answers the browser helper's `exec` with a canned JSON reply and records both the browser
    commands and the file writes the tools drove — the stand-in for a real container, never asserted
    itself. File writes arrive as their own `sh` exec, so the last arg is the resolved path."""

    reply: dict[str, object]
    commands: list[dict[str, object]] = field(default_factory=list)
    writes: list[tuple[str, bytes]] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        raise AssertionError("browser tools reach the sandbox only through exec")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        if argv[0] == BROWSER_HELPER:
            self.commands.append(json.loads(stdin))
            return ExecResult(stdout=json.dumps(self.reply), stderr="", exit_code=0)
        self.writes.append((argv[-1], stdin))
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None:
        raise AssertionError("browser tools reach the sandbox only through exec")


@dataclass
class StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


async def _no_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("browser tools do not spawn subagents")


def _context(carrier: RecordingCarrier, tmp_path: Path) -> ToolContext:
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
        memory=StubMemory(),
        member_id=None,
        artifact_token_secret="",
    )


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


async def test_navigate_marshals_params_and_drops_user_description(tmp_path: Path) -> None:
    carrier = RecordingCarrier(reply={"tab_id": 1, "url": "https://example.com/"})
    result = await _run(
        "navigate",
        _context(carrier, tmp_path),
        url="example.com",
        user_description="open",
        tab_id=2,
    )
    assert carrier.commands[-1] == {
        "command": "navigate",
        "params": {"url": "example.com", "tab_id": 2},
    }
    assert result.content[0].text == json.dumps({"tab_id": 1, "url": "https://example.com/"})
    assert result.is_error is False


async def test_tabs_context_sends_empty_params(tmp_path: Path) -> None:
    carrier = RecordingCarrier(reply={"tabs": []})
    await _run("tabs_context", _context(carrier, tmp_path))
    assert carrier.commands[-1] == {"command": "tabs_context", "params": {}}


async def test_tabs_create_defaults_to_blank(tmp_path: Path) -> None:
    carrier = RecordingCarrier(reply={"tab_id": 3})
    await _run("tabs_create", _context(carrier, tmp_path), user_description="new tab")
    assert carrier.commands[-1]["params"] == {"url": "about:blank"}


async def test_read_page_excludes_user_description_keeps_filter(tmp_path: Path) -> None:
    carrier = RecordingCarrier(reply={"tree": "root"})
    await _run(
        "read_page",
        _context(carrier, tmp_path),
        user_description="inspect",
        depth=2,
        filter="interactive",
    )
    assert carrier.commands[-1] == {
        "command": "read_page",
        "params": {"depth": 2, "filter": "interactive"},
    }


async def test_upload_file_passes_the_workspace_paths(tmp_path: Path) -> None:
    carrier = RecordingCarrier(reply={"ok": True})
    await _run("upload_file", _context(carrier, tmp_path), ref="ref_9", files=["a.pdf", "b.pdf"])
    assert carrier.commands[-1]["params"] == {"ref": "ref_9", "files": ["a.pdf", "b.pdf"]}


async def test_computer_saves_screenshot_into_the_workspace(tmp_path: Path) -> None:
    encoded = base64.b64encode(b"png-bytes").decode()
    carrier = RecordingCarrier(reply={"screenshot_base64": encoded})
    result = await _run(
        "computer",
        _context(carrier, tmp_path),
        actions=[{"type": "screenshot"}],
        user_description="shoot",
        save_to_workspace=True,
    )
    assert ("/workspace/browser-screenshot.jpg", b"png-bytes") in carrier.writes
    assert json.loads(result.content[0].text)["screenshot_path"] == "browser-screenshot.jpg"
    assert "screenshot_base64" not in json.loads(result.content[0].text)
    image = result.content[1]
    assert isinstance(image, ImageContent)
    assert image.media_type == "image/jpeg"
    assert image.data == encoded


async def test_computer_without_save_writes_nothing(tmp_path: Path) -> None:
    carrier = RecordingCarrier(reply={"screenshot_base64": base64.b64encode(b"x").decode()})
    await _run(
        "computer",
        _context(carrier, tmp_path),
        actions=[{"type": "left_click", "coordinate": [1, 2]}],
        user_description="click",
    )
    assert carrier.writes == []
    assert carrier.commands[-1]["params"]["actions"] == [
        {"type": "left_click", "coordinate": [1, 2]}
    ]


async def test_wait_for_download_writes_the_file_and_reports_its_path(tmp_path: Path) -> None:
    carrier = RecordingCarrier(
        reply={
            "filename": "report.pdf",
            "content_base64": base64.b64encode(b"pdf-bytes").decode(),
            "size": 9,
        }
    )
    result = await _run("wait_for_download", _context(carrier, tmp_path), user_description="dl")
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
