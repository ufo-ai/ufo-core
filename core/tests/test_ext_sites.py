import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from selfhost_ext_sites import manifest as sites_manifest
from selfhost_ext_sites import tools as sites_tools
from selfhost_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from selfhost_ext_sites.tools import (
    DeployWebsiteInput,
    PublishWebsiteInput,
    StartServerInput,
    WebsiteInput,
    deploy_website,
    publish_website,
    start_server,
    website,
)

from selfhost.blob import FilesystemBlobStore
from selfhost.sandbox.session import ExecResult
from selfhost.schema.records import Agent, Turn
from selfhost.tools.context import SpawnResult, ToolContext


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
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=_StubMemory(),
        member_id=None,
        artifact_token_secret="",
    )


def test_manifest_declares_the_four_tools_the_profile_and_the_section() -> None:
    manifest = sites_manifest.manifest()
    assert {tool.name for tool in manifest.tools} == {
        "website",
        "start_server",
        "deploy_website",
        "publish_website",
    }
    deploy = next(tool for tool in manifest.tools if tool.name == "deploy_website")
    assert deploy.description.startswith("Serve a website folder from the workspace")
    (profile,) = manifest.subagents
    assert profile.name == "website_building"
    (section,) = manifest.prompt_sections
    assert section.name == "sites" and "<sites>" in section.body


def test_the_website_building_profile_names_only_meaningful_tools() -> None:
    names = set(WEBSITE_BUILDING_PROFILE.tool_names)
    assert {"deploy_website", "publish_website", "write", "share_file"} <= names
    assert WEBSITE_BUILDING_PROFILE.input_model.model_validate(
        {"objective": "build a landing page"}
    ).objective
    assert WEBSITE_BUILDING_PROFILE.max_rounds == 100


async def test_website_builds_and_lists_the_output(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"ls -1A": ExecResult(stdout="index.html\nstyle.css\n", stderr="", exit_code=0)}
    )
    ctx = _context(sandbox, tmp_path)
    result = await website(
        ctx, WebsiteInput(runCommand="npm run build", projectPath="/workspace/site")
    )
    payload = json.loads(result.content[0].text)
    assert payload["projectPath"] == "/workspace/site"
    assert payload["files"] == ["index.html", "style.css"]
    assert any("npm run build" in command for command in sandbox.commands)


async def test_website_build_failure_fails_loud(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"npm run build": ExecResult(stdout="", stderr="build broke", exit_code=1)}
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match="build broke"):
        await website(ctx, WebsiteInput(runCommand="npm run build"))


async def test_deploy_website_serves_static_output_and_returns_the_route(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    result = await deploy_website(
        ctx,
        DeployWebsiteInput(
            project_path="/workspace/dist", site_name="marketing", entry_point="index.html"
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["url"] == f"http://localhost:{sites_tools.APP_SERVE_PORT}"
    assert payload["site_name"] == "marketing"
    serve_command = next(command for command in sandbox.commands if "http.server" in command)
    assert "/workspace/dist" in serve_command
    assert "nohup" in serve_command


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
            ctx, StartServerInput(command="python3 app.py", project_path="/workspace")
        )


async def test_publish_website_installs_before_serving(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    result = await publish_website(
        ctx,
        PublishWebsiteInput(
            project_path="/workspace/app",
            dist_path="/workspace/app/dist",
            app_name="dashboard",
            install_command="npm ci",
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["app_name"] == "dashboard"
    assert any("npm ci" in command for command in sandbox.commands)


def test_start_server_rejects_an_out_of_range_port() -> None:
    with pytest.raises(ValidationError, match="between 1 and 65535"):
        StartServerInput(command="python3 app.py", project_path="/workspace", port=99999)
