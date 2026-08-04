import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from ufo_ext_repl.manifest import JS_REPL_TOOL, XLSX_REPL_TOOL
from ufo_ext_research.tools import FETCH_URL_TOOL, SEARCH_VERTICAL_TOOL, SEARCH_WEB_TOOL
from ufo_ext_sites import manifest as sites_manifest
from ufo_ext_sites.delegation import BuildWebsiteInput, _build_website
from ufo_ext_sites.objects import CONVERSATION_DIGEST_HEX, site_object_name
from ufo_ext_sites.store import SITE_NAME_MAX, InvalidSiteName, site_name
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE, WebsiteBuildingResult
from ufo_ext_sites.tools import (
    SITES_TOOL_NAMES,
    StartServerInput,
    WebsiteInput,
    start_server,
    website,
)

from ufo.blob import FilesystemBlobStore
from ufo.ext.loader import skill_registry
from ufo.loop.subagents import subagent_system_prompt
from ufo.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.sandbox.session import ExecResult
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.skills.runtime import mount_skill
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import SpawnResult, ToolContext

TOOL_NARRATION = "building the site"


@dataclass
class FakeSandbox:
    """Scripts the sandbox for the sites handlers: records every bash command and returns a scripted
    result by matching a substring, defaulting to success — so the serve/build flows are exercised
    without a real container."""

    scripted: dict[str, ExecResult] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        self.commands.append(command)
        for needle, result in self.scripted.items():
            if needle in command:
                return result
        return ExecResult(stdout="", stderr="", exit_code=0)


async def _unavailable_spawn(profile: str, payload: dict, background: bool = False) -> SpawnResult:
    raise AssertionError("sites tools must not spawn")


@dataclass
class _StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


def _context(sandbox: FakeSandbox, tmp_path: Path) -> ToolContext:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )


def test_manifest_declares_the_tools_the_profile_and_the_section() -> None:
    manifest = sites_manifest.manifest()
    assert {tool.name for tool in manifest.tools} == {
        "website",
        "start_server",
        "deploy_website",
        "publish_website",
        "build_website",
    }
    deploy = next(tool for tool in manifest.tools if tool.name == "deploy_website")
    assert deploy.description.startswith("Serve a website folder and host it")
    assert "visibility" in deploy.input_model.model_json_schema()["properties"]
    (kind,) = manifest.objects
    assert kind.name == "site"
    (surface,) = manifest.surfaces
    assert surface.name == "sites"
    build = next(tool for tool in manifest.tools if tool.name == "build_website")
    assert build.side_effecting is True
    assert {"objective", "preload_skills", "extended_context"} <= set(
        build.input_model.model_json_schema()["properties"]
    )
    (profile,) = manifest.subagents
    assert profile.name == "website_building"
    assert profile.input_model.model_validate({"objective": "x" * 10_000}).objective == "x" * 10_000
    assert "maxLength" not in profile.input_model.model_json_schema()["properties"]["objective"]
    assert "maxLength" not in profile.output_model.model_json_schema()["properties"]["result"]
    assert "deploy_website" in profile.tool_names
    assert "publish_website" not in profile.tool_names
    assert "this conversation's sandbox" in build.description
    (section,) = manifest.prompt_sections
    assert section.name == "sites" and "<sites>" in section.body


def test_website_profile_receives_the_complete_per_turn_skill_index() -> None:
    profile = sites_manifest.manifest().subagents[0]
    prompt = subagent_system_prompt(
        profile, skills=(("extension-skill", "A turn-specific website workflow."),)
    )
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt
    assert "- extension-skill: A turn-specific website workflow." in prompt


async def test_build_website_forwards_the_optional_knobs_into_the_spawn(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        captured["profile"] = profile
        captured["payload"] = payload
        captured["dedup_key"] = dedup_key
        return SpawnResult(turn_id=uuid4(), output=WebsiteBuildingResult(result="built"))

    ctx = replace(
        _context(FakeSandbox(), tmp_path),
        spawn=_capture,
        idempotency_key="turn-1/build_website/call-2",
    )
    result = await _build_website(
        ctx,
        BuildWebsiteInput(
            user_description=TOOL_NARRATION,
            objective="build a landing page",
            preload_skills=("website-building",),
            extended_context=True,
        ),
    )
    assert captured["profile"] == "website_building"
    assert captured["payload"] == {
        "objective": "build a landing page",
        "preload_skills": ("website-building",),
        "extended_context": True,
    }
    assert captured["dedup_key"] == "turn-1/build_website/call-2"
    assert json.loads(result.content[0].text)["result"] == "built"


async def test_build_website_omits_the_unset_knobs(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        captured["payload"] = payload
        return SpawnResult(turn_id=uuid4(), output=WebsiteBuildingResult(result="built"))

    ctx = replace(_context(FakeSandbox(), tmp_path), spawn=_capture)
    await _build_website(
        ctx, BuildWebsiteInput(user_description=TOOL_NARRATION, objective="minimal")
    )
    assert captured["payload"] == {"objective": "minimal"}


def test_website_building_indexes_the_parent_and_hides_the_nested_child() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    index = dict(registry.index())
    assert "website-building" in index
    assert "website-building/webapp" not in index


def test_website_building_parent_keeps_its_own_subdirs_but_not_the_child_subtree() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    parent_files = {path for path, _ in registry.named("website-building").files}
    assert any(path.startswith("game/") for path in parent_files)
    assert any(path.startswith("shared/") for path in parent_files)
    assert any(path.startswith("informational/") for path in parent_files)
    assert not any(path.startswith("webapp/") for path in parent_files)


async def test_the_webapp_child_declares_its_parent_and_mounts_it_nested() -> None:
    registry = skill_registry((sites_manifest.manifest(),))

    child = registry.named("website-building/webapp")
    assert child.name == "website-building/webapp"
    assert child.parent == "website-building"

    assert [entry.skill.name for entry in registry.closure("website-building/webapp")] == [
        "website-building/webapp",
        "website-building",
    ]

    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    for entry in registry.closure("website-building/webapp"):
        await mount_skill(_Sandbox(), entry.skill)

    assert "/workspace/.skills/website-building/SKILL.md" in written
    assert "/workspace/.skills/website-building/webapp/SKILL.md" in written
    assert "/workspace/.skills/website-building/shared/01-design-tokens.md" in written
    assert not any(path.startswith("/workspace/.skills/website-building-") for path in written)


def test_the_website_building_profile_names_only_meaningful_tools() -> None:
    """It builds, serves, validates and hosts — and delivers nothing itself. `share_file` exists to
    hand the member a copy, and the child has no member to hand one to: the workspace it shares with
    the parent is the handoff, read back with `glob` and `read`."""
    names = set(WEBSITE_BUILDING_PROFILE.tool_names)
    # The queue projects the profile by filtering the live tool set on these names, so a name that
    # resolves to nothing is dropped in silence — a typo would leave the child short of a tool and
    # every test green. Whatever this profile names has to exist somewhere that ships.
    available = (
        set(SITES_TOOL_NAMES)
        | {tool.name for tool in BUILTIN_TOOLS}
        | {JS_REPL_TOOL, XLSX_REPL_TOOL}
        | {SEARCH_WEB_TOOL, SEARCH_VERTICAL_TOOL, FETCH_URL_TOOL}
    )
    assert names <= available
    # Named, not merely resolvable: the containment above passes just as well with a tool dropped,
    # and the two REPLs are what the child drives a page and a workbook with.
    assert {JS_REPL_TOOL, XLSX_REPL_TOOL} <= names
    assert {"website", "start_server", "write", "js_repl", "deploy_website"} <= names
    assert "share_file" not in names
    assert WEBSITE_BUILDING_PROFILE.input_model.model_validate(
        {"user_description": TOOL_NARRATION, "objective": "build a landing page"}
    ).objective
    assert WEBSITE_BUILDING_PROFILE.max_rounds == 100


def test_the_website_building_profile_uses_the_shared_finish_contract() -> None:
    prompt = subagent_system_prompt(WEBSITE_BUILDING_PROFILE)
    assert "A delivery crosses an agent boundary" in prompt
    assert "call `finish` directly" not in WEBSITE_BUILDING_PROFILE.prompt
    assert "End the turn by calling the `finish` tool" in prompt


async def test_website_builds_and_lists_the_output(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"ls -1A": ExecResult(stdout="index.html\nstyle.css\n", stderr="", exit_code=0)}
    )
    ctx = _context(sandbox, tmp_path)
    result = await website(
        ctx,
        WebsiteInput(
            user_description=TOOL_NARRATION,
            run_command="npm run build",
            project_path="/workspace/site",
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["project_path"] == "/workspace/site"
    assert payload["files"] == ["index.html", "style.css"]
    assert any("npm run build" in command for command in sandbox.commands)


async def test_website_build_failure_fails_loud(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"npm run build": ExecResult(stdout="", stderr="build broke", exit_code=1)}
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match="build broke"):
        await website(
            ctx, WebsiteInput(user_description=TOOL_NARRATION, run_command="npm run build")
        )


async def test_start_server_reports_a_serve_failure_from_the_log(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={
            "nohup": ExecResult(stdout="", stderr="", exit_code=1),
            "tail -n 20": ExecResult(stdout="Traceback: port in use", stderr="", exit_code=0),
        }
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match="port in use"):
        await start_server(
            ctx,
            StartServerInput(
                user_description=TOOL_NARRATION, command="python3 app.py", project_path="/workspace"
            ),
        )


def test_site_name_slugs_bounds_and_refuses_a_nameless_site() -> None:
    """The name is the site's identity, half its link, and its object name, so a name that slugs to
    nothing is refused rather than stored as one — and the bound leaves room for the conversation
    digest the object name appends."""
    assert site_name("My Marketing Site!") == "my-marketing-site"
    assert site_name("Q3  --  Report") == "q3-report"
    assert site_name("x" * 80) == "x" * SITE_NAME_MAX
    assert SITE_NAME_MAX + len("-") + CONVERSATION_DIGEST_HEX <= OBJECT_NAME_MAX_LENGTH, (
        "a site's longest name plus its object suffix must stay addressable from chat"
    )
    assert len(site_object_name(uuid4(), site_name("x" * 80))) <= OBJECT_NAME_MAX_LENGTH
    for nameless in ("---", "   ", "🌱🌱", "!!!"):
        with pytest.raises(InvalidSiteName, match="letters or digits"):
            site_name(nameless)


def test_start_server_rejects_an_out_of_range_port() -> None:
    with pytest.raises(ValidationError, match="between 1 and 65535"):
        StartServerInput(
            user_description=TOOL_NARRATION,
            command="python3 app.py",
            project_path="/workspace",
            port=99999,
        )
