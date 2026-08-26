import asyncio
import http.client
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from PIL import Image
from pydantic import ValidationError
from ufo_ext_repl.manifest import JS_REPL_TOOL, XLSX_REPL_TOOL
from ufo_ext_research.tools import FETCH_URL_TOOL, SEARCH_VERTICAL_TOOL, SEARCH_WEB_TOOL
from ufo_ext_sites import manifest as sites_manifest
from ufo_ext_sites import share_card
from ufo_ext_sites import tools as sites_tools
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_ATTEMPT_KEY,
    APPLICATION_AUDIT_REQUEST_CONTRACT_KEY,
    APPLICATION_AUDIT_SERVER,
    APPLICATION_AUDIT_TURN_CONTRACT_KEY,
    MAX_PRODUCT_QA_CONTROLS,
    ApplicationAuditContract,
    ApplicationAuditFact,
    ApplicationAuditFeedback,
    ApplicationAuditReport,
    ApplicationQaProof,
    audit_application,
)
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_DELEGATION_TOOL,
    APPLICATION_BUILDER_DEPLOY_GUARD_REASON,
    APPLICATION_BUILDER_DEPLOY_TOOL,
    APPLICATION_BUILDER_DESIGN_TOOL,
    APPLICATION_BUILDER_EDIT_TOOL,
    APPLICATION_BUILDER_MODEL,
    APPLICATION_BUILDER_NAME,
    APPLICATION_BUILDER_PROFILE,
    APPLICATION_BUILDER_QA_PROOF_KEY,
    APPLICATION_BUILDER_QA_TOOL,
    APPLICATION_BUILDER_READ_TOOL,
    APPLICATION_BUILDER_REASONING,
    APPLICATION_BUILDER_REDEPLOY_KEY,
    APPLICATION_BUILDER_REPAIR_READ_LIMIT,
    APPLICATION_BUILDER_REPAIR_READ_REASON,
    APPLICATION_BUILDER_SKILL,
    APPLICATION_BUILDER_WRITE_TOOL,
    APPLICATION_INDEX,
    APPLICATION_PLACEHOLDER,
    APPLICATION_PREVIEW_FILENAME,
    APPLICATION_PREVIEW_SCAFFOLD,
    APPLICATION_PREVIEW_TOOL,
    APPLICATION_SCAFFOLD_PATH,
    APPLICATION_SOURCE_CLAIM,
    APPLICATION_SOURCE_READ,
    APPLICATION_SOURCE_REQUIRE_CLAIM,
    ApplicationBuildAcceptance,
    ApplicationBuilderResult,
    ApplicationBuilderTask,
    ApplicationPreviewResult,
    ApplicationSourceEdit,
    BuildUfoApplicationInput,
    EditApplicationSourceInput,
    ReadApplicationSourceInput,
    RenderApplicationPreviewInput,
    WriteApplicationDesignInput,
    WriteApplicationSourceInput,
    build_ufo_application,
    edit_application_source,
    limit_application_builder_repair_reads,
    read_application_source,
    render_application_preview,
    require_application_builder_qa,
    write_application_design,
    write_application_source,
)
from ufo_ext_sites.delegation import BuildWebsiteInput, _build_website
from ufo_ext_sites.objects import (
    CONVERSATION_DIGEST_HEX,
    site_name_from_object,
    site_object_name,
)
from ufo_ext_sites.share_card import (
    BROWSER_COMMANDS,
    CARD_HEIGHT,
    CARD_SHOT_DRAWN,
    CARD_TIMEOUT_SECONDS,
    CARD_WIDTH,
    FONT_ASSET,
    FRAME_SECONDS,
    LOAD_WALL_SECONDS,
    LOGO_ASSET,
    PANEL_WIDTH,
    SETTLE_WALL_SECONDS,
    SHOT_DEADLINE_SECONDS,
    SHOT_TOKEN,
    SHOT_WIDTH,
    STORED_SHOT_DRAWN,
    UNATTENDED_FLAGS,
    card_page,
    shot_command,
)
from ufo_ext_sites.store import (
    SITE_NAME_MAX,
    HostedSite,
    HostedSites,
    InvalidSiteName,
    SiteFile,
    SourceManifest,
    hosted_site,
    site_name,
)
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE, WebsiteBuildingResult
from ufo_ext_sites.tools import (
    APPLICATION_AUDIT_MAX_ATTEMPTS,
    APPLICATION_AUDIT_SCRIPT,
    LOG_CLEAR_PROG,
    LOG_TAIL_TIMEOUT_SECONDS,
    PORT_STOP_PROG,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    READINESS_TIMEOUT_SECONDS,
    SITES_TOOL_NAMES,
    SITES_TOOLS,
    DeployUfoApplicationInput,
    DeployWebsiteInput,
    QaUfoApplicationInput,
    StartServerInput,
    WebsiteInput,
    _audit_builder_application,
    _redeploy_homepage,
    _require_current_application_qa,
    deploy_ufo_application,
    deploy_website,
    qa_ufo_application,
    start_server,
    website,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.loader import skill_registry
from ufo.loop.subagents import subagent_system_prompt
from ufo.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.sandbox.session import (
    SANDBOX_MODULE_BOOTSTRAP,
    SANDBOX_PYTHON_FLAG,
    ExecResult,
    SandboxHandle,
)
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.manifest import Deny, HookContext, PreToolUse
from ufo.sdk.tools import TextContent, ToolResult
from ufo.skills.runtime import mount_skill
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

TOOL_NARRATION = "building the site"
HOUSE_STYLE = "ufo-style"
HOUSE_STYLE_TOKENS = "references/tokens.css"
PLAYWRIGHT_GUIDANCE = "shared/12-playwright-interactive.md"
APPLICATION_QA_GUIDANCE = "shared/13-ufo-application-qa.md"
APPLICATION_DESIGN = '<svg viewBox="0 0 1440 900"><rect width="1440" height="900" /></svg>'
JS_CELL = re.compile(r"```javascript\n(.*?)```", re.S)


def _playwright_guidance() -> str:
    registry = skill_registry((sites_manifest.manifest(),))
    return dict(registry.named("website-building").files)[PLAYWRIGHT_GUIDANCE].decode()


def _application_qa_guidance() -> str:
    registry = skill_registry((sites_manifest.manifest(),))
    return dict(registry.named("website-building").files)[APPLICATION_QA_GUIDANCE].decode()


@dataclass
class FakeSandbox:
    """Scripts the sandbox for the sites handlers: records every bash command and returns a scripted
    result by matching a substring, defaulting to success — so the serve/build flows are exercised
    without a real container."""

    scripted: dict[str, ExecResult] = field(default_factory=dict)
    scripted_programs: dict[str, ExecResult] = field(default_factory=dict)
    scripted_paths: dict[str, ExecResult] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    budgets: dict[str, int | None] = field(default_factory=dict)
    programs: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    shells: list[tuple[str, tuple[str, ...], int | None]] = field(default_factory=list)
    writes: dict[str, bytes] = field(default_factory=dict)
    claim: ExecResult = field(default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0))
    shell: ExecResult = field(default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0))
    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="sites-test")
    )

    @property
    def conversation_id(self) -> UUID:
        return self.handle.conversation_id

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        self.commands.append(command)
        self.budgets[command] = timeout_s
        for needle, result in self.scripted.items():
            if needle in command:
                return result
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        self.programs.append((program, args))
        for needle, result in self.scripted_paths.items():
            if args and needle in args[0]:
                return result
        for needle, result in self.scripted_programs.items():
            if needle in program:
                return result
        if program == APPLICATION_SOURCE_READ and args and args[0] in self.writes:
            return ExecResult(self.writes[args[0]].decode(), "", 0)
        return self.claim

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        self.shells.append((script, args, timeout_s))
        return self.shell

    async def write_file(self, path: str, content: bytes) -> None:
        self.writes[path] = content


@dataclass
class FakeHookStore:
    values: dict[str, object] = field(default_factory=dict)

    async def get(self, key: str) -> object | None:
        return self.values.get(key)

    async def put(self, key: str, value: object) -> None:
        self.values[key] = value


@dataclass
class FakeHookExt:
    store: FakeHookStore


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


def _seed_application_design(
    sandbox: FakeSandbox, scaffold_path: str = "/workspace/application"
) -> None:
    sandbox.writes[f"{scaffold_path}/application-design.svg"] = APPLICATION_DESIGN.encode()


def test_manifest_declares_the_tools_the_profile_and_the_section() -> None:
    manifest = sites_manifest.manifest()
    assert {tool.name for tool in manifest.tools} == {
        "website",
        "start_server",
        "deploy_website",
        APPLICATION_BUILDER_DEPLOY_TOOL,
        "publish_website",
        "set_homepage",
        "build_website",
        APPLICATION_BUILDER_DELEGATION_TOOL,
        APPLICATION_BUILDER_DESIGN_TOOL,
        APPLICATION_BUILDER_EDIT_TOOL,
        APPLICATION_BUILDER_READ_TOOL,
        APPLICATION_BUILDER_QA_TOOL,
        APPLICATION_BUILDER_WRITE_TOOL,
        APPLICATION_PREVIEW_TOOL,
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
    profile = next(profile for profile in manifest.subagents if profile.name == "website_building")
    assert profile.name == "website_building"
    assert profile.input_model.model_validate({"objective": "x" * 10_000}).objective == "x" * 10_000
    assert "maxLength" not in profile.input_model.model_json_schema()["properties"]["objective"]
    assert "maxLength" not in profile.output_model.model_json_schema()["properties"]["result"]
    assert "deploy_website" in profile.tool_names
    assert "publish_website" not in profile.tool_names
    assert "this conversation's sandbox" in build.description
    assert [(hook.event, hook.tools) for hook in manifest.hooks] == [
        (
            "pre_tool_use",
            (APPLICATION_BUILDER_READ_TOOL, APPLICATION_BUILDER_EDIT_TOOL),
        ),
        ("pre_tool_use", (APPLICATION_BUILDER_DEPLOY_TOOL,)),
    ]
    (section,) = manifest.prompt_sections
    assert section.name == "sites" and "<sites>" in section.body


def test_application_audit_accepts_measured_interactive_facts() -> None:
    report = ApplicationAuditReport.model_validate(
        {
            "url": "http://localhost:3000/preview.html",
            "floor": 4.5,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 8,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "console": [],
                    "aboveFoldText": "Acme renewal Aug 27 #2042",
                }
                for width in (1440, 390)
                for scheme in ("light", "dark")
            ],
            "interaction": {
                "controls": [
                    {"selector": "button:nth-of-type(1)", "name": "Open brief"},
                    {"selector": "button:nth-of-type(2)", "name": "Show risks"},
                ],
                "successes": [
                    {"selector": "button:nth-of-type(1)", "name": "Open brief"},
                    {"selector": "button:nth-of-type(2)", "name": "Show risks"},
                ],
                "states": [["Acme renewal", "Aug 27", "#2042"]],
                "console": [],
            },
        }
    )
    contract = ApplicationAuditContract(
        facts=(
            ApplicationAuditFact(label="customer", alternatives=("Acme renewal",)),
            ApplicationAuditFact(label="date", alternatives=("Aug 27", "August 27")),
            ApplicationAuditFact(label="issue", alternatives=("#2042",)),
        )
    )

    assert audit_application(report, contract).issues == ()


def test_application_audit_runs_views_in_parallel_in_declared_order() -> None:
    source = APPLICATION_AUDIT_SCRIPT.decode()

    assert "await Promise.all(VIEWS.map(async (view) => {" in source
    assert "views.push(" not in source
    assert "finally {\n        await context.close();" in source
    assert "finally {\n    await browser.close();" in source


def test_application_audit_returns_one_bounded_diagnostic_batch() -> None:
    report = ApplicationAuditReport.model_validate(
        {
            "url": "http://localhost:3000/preview.html",
            "floor": 4.5,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 4,
                    "text": [
                        {
                            "text": "Muted status",
                            "selector": "span.muted",
                            "px": 14,
                            "weight": 400,
                            "ratio": 3.2,
                        }
                    ],
                    "documentWidth": width + 12,
                    "clipped": ["td.owner: Alexandra"],
                    "console": ["pageerror: broken"],
                    "aboveFoldText": "Acme renewal",
                }
                for width in (1440, 390)
                for scheme in ("light", "dark")
            ],
            "interaction": {
                "controls": [{"selector": "button", "name": "Open brief"}],
                "successes": [{"selector": "button", "name": "Open brief"}],
                "states": [["Acme renewal"]],
                "console": ["pageerror: action failed"],
            },
        }
    )
    contract = ApplicationAuditContract(
        facts=(ApplicationAuditFact(label="issue", alternatives=("#2042",)),)
    )

    verdict = audit_application(report, contract)

    assert {issue.code for issue in verdict.issues} == {
        "contrast",
        "overflow",
        "clipping",
        "console",
        "controls",
        "interaction",
        "fact",
        "above_fold",
    }
    assert len(verdict.issues) <= 8
    assert all(len(issue.message) <= 500 for issue in verdict.issues)


def test_application_audit_server_maps_root_assets(tmp_path: Path, unused_tcp_port: int) -> None:
    project = tmp_path / "ufo-app"
    asset = project / "dist" / "assets" / "app.js"
    asset.parent.mkdir(parents=True)
    asset.write_text("built application")
    server = tmp_path / "application-audit-server.py"
    server.write_bytes(APPLICATION_AUDIT_SERVER)
    process = subprocess.Popen(
        [sys.executable, str(server), str(project), str(unused_tcp_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 2
    try:
        while True:
            connection = http.client.HTTPConnection("127.0.0.1", unused_tcp_port, timeout=1)
            try:
                connection.request("GET", "/assets/app.js")
                response = connection.getresponse()
                body = response.read()
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError("application audit server stopped before serving") from None
                if time.monotonic() >= deadline:
                    raise TimeoutError("application audit server did not start") from None
                time.sleep(0.01)
                continue
            finally:
                connection.close()
            break
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)

    assert response.status == 200
    assert body == b"built application"


async def test_application_builder_audit_returns_feedback_to_the_same_worker(
    tmp_path: Path,
) -> None:
    def _report(issue: bool) -> str:
        return json.dumps(
            {
                "views": [
                    {
                        "scheme": scheme,
                        "width": width,
                        "textChecked": 8,
                        "text": (
                            [
                                {
                                    "selector": "span.muted",
                                    "px": 14,
                                    "weight": 400,
                                    "ratio": 3.2,
                                }
                            ]
                            if issue
                            else []
                        ),
                        "documentWidth": width,
                        "clipped": [],
                        "console": [],
                        "aboveFoldText": "#2042",
                    }
                    for width in (1440, 390)
                    for scheme in ("light", "dark")
                ],
                "interaction": {
                    "controls": [
                        {"selector": "#first", "name": "First"},
                        {"selector": "#second", "name": "Second"},
                    ],
                    "successes": [
                        {"selector": "#first", "name": "First"},
                        {"selector": "#second", "name": "Second"},
                    ],
                    "states": [["#2042"]],
                    "console": [],
                },
            }
        )

    contract = ApplicationAuditContract(
        facts=(ApplicationAuditFact(label="issue", alternatives=("#2042",)),)
    ).model_dump_json()
    sandbox = FakeSandbox(
        scripted_paths={
            "/application-audit/": ExecResult(_report(True), "", 0),
        }
    )
    parent_turn_id = uuid4()
    store = FakeHookStore(
        values={
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(turn_id=parent_turn_id): json.loads(contract)
        }
    )
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "parent_turn_id": parent_turn_id,
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )

    feedback = await _audit_builder_application(ctx, "/workspace/ufo-app")

    key = APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=ctx.turn.id)
    assert isinstance(feedback, ApplicationAuditFeedback)
    assert feedback.status == "repair_required"
    assert {issue.code for issue in feedback.issues} == {"contrast"}
    assert store.values[key] == 1
    sandbox.scripted_paths["/application-audit/"] = ExecResult(_report(False), "", 0)
    report = await _audit_builder_application(ctx, "/workspace/ufo-app")
    assert isinstance(report, ApplicationAuditReport)
    assert len(sandbox.shells) == 2
    server_path = f"/workspace/.tool-output/application-audit/{ctx.turn.id}-server.py"
    assert sandbox.writes[server_path] == APPLICATION_AUDIT_SERVER
    launches = [command for command in sandbox.commands if "nohup" in command]
    assert launches and all(server_path in command for command in launches)
    port_stops = [program for program, _args in sandbox.programs if program == PORT_STOP_PROG]
    assert len(port_stops) == 4


def test_application_builder_port_cleanup_supports_hosts_without_procfs() -> None:
    assert "if tables:" in PORT_STOP_PROG
    assert '["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]' in PORT_STOP_PROG
    assert "os.kill(pid, 0)" in PORT_STOP_PROG


async def test_application_builder_audit_stops_after_two_failed_attempts(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    store = FakeHookStore()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    key = APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=ctx.turn.id)
    store.values[key] = APPLICATION_AUDIT_MAX_ATTEMPTS

    with pytest.raises(RuntimeError, match="stopped after two failed product audits"):
        await _audit_builder_application(ctx, "/workspace/ufo-app")

    assert sandbox.commands == []


async def test_application_builder_deploy_requires_the_scaffold_root(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
    )

    with pytest.raises(
        RuntimeError,
        match="project_path must be /workspace/ufo-app, not /workspace/ufo-app/dist",
    ):
        await deploy_website(
            ctx,
            DeployWebsiteInput(
                project_path="/workspace/ufo-app/dist",
                site_name="meeting-tasks",
                entry_point="index.html",
            ),
        )

    assert sandbox.commands == []
    assert sandbox.programs == []


async def test_application_deploy_tool_owns_the_fixed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[DeployWebsiteInput] = []
    returned = ToolResult(content=(TextContent(text="deployed"),))

    async def _deploy(_ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult:
        captured.append(args)
        return returned

    monkeypatch.setattr(sites_tools, "deploy_website", _deploy)
    base = _context(FakeSandbox(), tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
    )
    args = DeployUfoApplicationInput(site_name="meeting-tasks")

    assert await deploy_ufo_application(ctx, args) is returned
    assert captured == [
        DeployWebsiteInput(
            project_path="/workspace/ufo-app",
            site_name="meeting-tasks",
            entry_point="index.html",
        )
    ]
    tool = next(
        tool
        for tool in sites_manifest.manifest().tools
        if tool.name == APPLICATION_BUILDER_DEPLOY_TOOL
    )
    assert set(tool.input_model.model_json_schema()["properties"]) == {"site_name"}


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
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=WebsiteBuildingResult(result="built"),
        )

    ctx = replace(
        _context(FakeSandbox(), tmp_path),
        spawn=_capture,
        idempotency_key="turn-1/build_website/call-2",
    )
    result = await _build_website(
        ctx,
        BuildWebsiteInput(
            objective="build a landing page",
            preload_skills=("website-building",),
            extended_context=True,
        ),
    )
    assert captured["profile"] == "profile:website_building"
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
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=WebsiteBuildingResult(result="built"),
        )

    ctx = replace(_context(FakeSandbox(), tmp_path), spawn=_capture)
    await _build_website(ctx, BuildWebsiteInput(objective="minimal"))
    assert captured["payload"] == {"objective": "minimal"}


async def test_build_ufo_application_uses_the_fixed_worker_contract(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    spawns = 0

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawns
        spawns += 1
        captured["profile"] = profile
        captured["payload"] = payload
        captured["dedup_key"] = dedup_key
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path="/workspace/ufo-app/app.tsx",
                browser_batches=0,
                blocker="The connector is unavailable.",
            ),
        )

    ctx = replace(
        _context(FakeSandbox(), tmp_path),
        spawn=_capture,
        idempotency_key="turn-1/build_ufo_application/call-2",
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )
    assert set(BuildUfoApplicationInput.model_fields) == set()
    result = await build_ufo_application(
        ctx,
        BuildUfoApplicationInput(),
    )
    repeated = await build_ufo_application(ctx, BuildUfoApplicationInput())

    assert captured == {
        "profile": "profile:ufo_application_builder",
        "payload": {
            "objective": (
                f"Application instructions:\n{ctx.agent.prompt}\n\n"
                f"Current request:\n{ctx.turn.inbound}"
            ),
            "scaffold_path": "/workspace/ufo-app",
            "source_path": "/workspace/ufo-app/app.tsx",
            "preload_skills": ("ufo-style",),
        },
        "dedup_key": "turn-1/build_ufo_application/call-2",
    }
    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert repeated.content == result.content
    assert spawns == 2
    assert returned.status == "blocked"
    assert returned.blocker == "The connector is unavailable."


async def test_build_ufo_application_creates_the_product_scaffold(tmp_path: Path) -> None:
    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path="/workspace/ufo-app/app.tsx",
                browser_batches=0,
                blocker="No connector.",
            ),
        )

    sandbox = FakeSandbox(claim=ExecResult("", "not found", 1))
    ctx = replace(
        _context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    await build_ufo_application(ctx, BuildUfoApplicationInput())

    assert sandbox.writes == {
        "/workspace/ufo-app/index.html": APPLICATION_INDEX,
        "/workspace/ufo-app/app.tsx": APPLICATION_PLACEHOLDER,
        "/workspace/ufo-app/preview.html": APPLICATION_PREVIEW_SCAFFOLD,
    }


async def test_concurrent_application_requests_bind_their_own_audit_contracts(
    tmp_path: Path,
) -> None:
    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path="/workspace/ufo-app/app.tsx",
                browser_batches=0,
                blocker="No connector.",
            ),
        )

    first = _context(FakeSandbox(), tmp_path)
    second = _context(FakeSandbox(), tmp_path)
    store = FakeHookStore()
    first = replace(
        first,
        turn=first.turn.model_copy(update={"inbound": "Build the first app."}),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    second = replace(
        second,
        turn=second.turn.model_copy(update={"inbound": "Build the second app."}),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    first_contract = {"facts": [{"label": "first", "alternatives": ["one"]}]}
    second_contract = {"facts": [{"label": "second", "alternatives": ["two"]}]}
    for ctx, contract in ((first, first_contract), (second, second_contract)):
        store.values[
            APPLICATION_AUDIT_REQUEST_CONTRACT_KEY.format(
                request_sha256=sha256(ctx.turn.inbound.encode()).hexdigest()
            )
        ] = contract

    await asyncio.gather(
        build_ufo_application(first, BuildUfoApplicationInput()),
        build_ufo_application(second, BuildUfoApplicationInput()),
    )

    assert (
        store.values[APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(turn_id=first.turn.id)]
        == first_contract
    )
    assert (
        store.values[APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(turn_id=second.turn.id)]
        == second_contract
    )


async def test_build_ufo_application_returns_one_blocked_result_when_the_worker_fails(
    tmp_path: Path,
) -> None:
    async def _fail(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        raise RuntimeError("model stream produced no usage")

    member_id = uuid4()
    store = FakeHookStore()
    ctx = replace(
        _context(FakeSandbox(), tmp_path),
        spawn=_fail,
        speaker_member_id=member_id,
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )

    result = await build_ufo_application(ctx, BuildUfoApplicationInput())

    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert returned.status == "blocked"
    assert returned.blocker == (
        "The application worker failed: RuntimeError: model stream produced no usage"
    )
    assert store.values[APPLICATION_BUILDER_REDEPLOY_KEY.format(turn_id=ctx.turn.id)] == str(
        member_id
    )


async def test_application_worker_redeploy_uses_the_exact_parent_speaker(
    tmp_path: Path,
) -> None:
    member_id = uuid4()
    parent_turn_id = uuid4()
    now = datetime(2026, 8, 25, tzinfo=UTC)
    store = FakeHookStore(
        values={APPLICATION_BUILDER_REDEPLOY_KEY.format(turn_id=parent_turn_id): str(member_id)}
    )
    base = _context(FakeSandbox(), tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "parent_turn_id": parent_turn_id,
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        on_behalf_of_member_id=member_id,
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    bound = HostedSite(
        conversation_id=uuid4(),
        name="tasks-homepage",
        port=5000,
        visibility="private",
        creator_member_id=member_id,
        generation=uuid4(),
        deploy_generation=1,
        homepage_agent_id=ctx.turn.agent_id,
        preview_blob_key=None,
        preview_size_bytes=None,
        share_card_blob_key=None,
        share_card_hash=None,
        source_manifest=None,
        created_at=now,
        updated_at=now,
    )
    args = DeployWebsiteInput(
        project_path=APPLICATION_SCAFFOLD_PATH,
        site_name=bound.name,
        entry_point="index.html",
        visibility="private",
    )

    with pytest.raises(ValueError, match="answers the agent's visibility"):
        await _redeploy_homepage(ctx, args, bound, 40000)

    store.values[APPLICATION_BUILDER_REDEPLOY_KEY.format(turn_id=parent_turn_id)] = str(uuid4())
    with pytest.raises(RuntimeError, match="only a member speaking"):
        await _redeploy_homepage(ctx, args, bound, 40000)


async def test_application_build_acceptance_binds_only_verified_worker_output(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "sites-test-secret")
    workspace_id = uuid4()
    member_id = uuid4()
    agent_id = uuid4()
    conversation_id = uuid4()
    child_turn_id = uuid4()
    now = datetime(2026, 8, 24, tzinfo=UTC)
    manifest = SourceManifest(
        root=f"sites/{uuid4()}/",
        files={
            "src/app.tsx": SiteFile(size=120, media_type="text/plain", sha256="a" * 64),
            "index.html": SiteFile(size=80, media_type="text/html", sha256="b" * 64),
        },
    ).model_dump_json()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="owner@example.com",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="tasks",
                prompt="Track the team's work.",
                model="claude-opus-4-8",
                visibility="private",
                is_main=False,
                owner_member_id=member_id,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key="homepage/tasks/owner",
                audience=SHARED_AUDIENCE,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(hosted_site).values(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                name="tasks-homepage",
                port=5000,
                visibility="private",
                creator_member_id=member_id,
                generation=uuid4(),
                deploy_generation=1,
                source_manifest=manifest,
                created_at=now,
                updated_at=now,
            )
        )

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        return SpawnResult(
            turn_id=child_turn_id,
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="deployed",
                source_path="/workspace/ufo-app/app.tsx",
                site_name="tasks-homepage",
                site_url="https://untrusted.example",
                browser_batches=4,
                controls_checked=("Open task",),
            ),
        )

    with ws(workspace_id):
        ext = context_for("sites", frozenset())
        await ext.store.put(
            APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=child_turn_id),
            ApplicationQaProof(source_sha256="a" * 64, browser_batches=2).model_dump(),
        )
        sandbox = FakeSandbox(
            scripted_paths={
                f".{child_turn_id}.accepted": ExecResult("", "current source is absent", 1),
                ".accepted": ExecResult("", "accepted source is absent", 1),
            },
            handle=SandboxHandle(conversation_id=conversation_id, container_id="sites-test"),
        )
        ctx = ToolContext(
            sandbox=sandbox,
            blob=FilesystemBlobStore(root=tmp_path),
            turn=Turn(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="Build the homepage.",
                created_at=now,
                on_behalf_of_member_id=member_id,
            ),
            agent=Agent(
                prompt="Track the team's work.",
                model="claude-opus-4-8",
                visibility="private",
            ),
            spawn=_capture,
            speaker_member_id=None,
            on_behalf_of_member_id=member_id,
            audience=SHARED_AUDIENCE,
            artifact_token_secret="",
            ext=ext,
            public_base_url="https://ufo.example.test",
        )
        rejected = await build_ufo_application(ctx, BuildUfoApplicationInput())
        bound_before_source = await HostedSites(workspace_id, workspace_tx).homepage(agent_id)
        sandbox.scripted_paths[f".{child_turn_id}.accepted"] = ExecResult("a" * 64, "", 0)
        ctx = replace(ctx, turn=ctx.turn.model_copy(update={"id": uuid4()}))
        result = await build_ufo_application(ctx, BuildUfoApplicationInput())
        bound = await HostedSites(workspace_id, workspace_tx).homepage(agent_id)

    refused = ApplicationBuilderResult.model_validate_json(rejected.content[0].text)
    assert refused.status == "blocked"
    assert refused.blocker == "The worker produced no accepted app.tsx."
    assert bound_before_source is None
    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert returned.status == "deployed"
    assert returned.browser_batches == 2
    assert returned.site_url.startswith("https://ufo.example.test/surface/sites/")
    assert bound is not None
    assert bound.name == "tasks-homepage"

    other_conversation = uuid4()
    other_sandbox = FakeSandbox(
        scripted_paths={f".{child_turn_id}.accepted": ExecResult("a" * 64, "", 0)},
        handle=SandboxHandle(conversation_id=other_conversation, container_id="sites-test"),
    )
    other_ctx = replace(
        ctx,
        sandbox=other_sandbox,
        turn=ctx.turn.model_copy(update={"id": uuid4(), "conversation_id": other_conversation}),
    )
    with ws(workspace_id):
        cross_conversation = await ApplicationBuildAcceptance(other_ctx, child_turn_id).accept(
            returned
        )
    assert cross_conversation.status == "deployed"
    assert cross_conversation.site_url.startswith("https://ufo.example.test/surface/sites/")


async def test_application_build_acceptance_blocks_missing_qa(tmp_path: Path) -> None:
    ctx = replace(
        _context(FakeSandbox(), tmp_path),
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )
    result = ApplicationBuilderResult(
        status="deployed",
        source_path="/workspace/ufo-app/app.tsx",
        site_name="tasks-homepage",
        site_url="https://untrusted.example",
        browser_batches=4,
    )

    accepted = await ApplicationBuildAcceptance(ctx, uuid4()).accept(result)

    assert accepted.status == "blocked"
    assert accepted.browser_batches == 0
    assert accepted.blocker == "The worker returned no passed product QA proof."


async def test_application_preview_is_one_fixed_product_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = FakeSandbox()
    shared: list[tuple[str, bytes, str | None]] = []

    async def _share(
        _ctx: ToolContext, filename: str, data: bytes, subject: str | None = None
    ) -> None:
        shared.append((filename, data, subject))

    monkeypatch.setattr(ToolContext, "share_artifact", _share)
    args = RenderApplicationPreviewInput(
        purpose="Review support requests before assignment.",
        first_screen_priority="Overdue queue",
        regions=("Overdue", "Unassigned", "Recent activity"),
        layout="queue-detail",
        design_direction="Compact and factual.",
    )

    result = await render_application_preview(_context(sandbox, tmp_path), args)

    rendered = ApplicationPreviewResult.model_validate_json(result.content[0].text)
    assert rendered.shared_filename == APPLICATION_PREVIEW_FILENAME
    assert len(rendered.design_digest) == 64
    assert sandbox.writes == {}
    assert [(filename, subject) for filename, _, subject in shared] == [
        (APPLICATION_PREVIEW_FILENAME, "Application preview")
    ]
    image = Image.open(BytesIO(shared[0][1]))
    assert image.size == (1280, 800)
    assert image.mode == "RGB"


async def test_application_preview_does_not_block_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def _slow_preview(_args: RenderApplicationPreviewInput) -> bytes:
        started.set()
        assert release.wait(1)
        return b"preview"

    async def _share(
        _ctx: ToolContext, _filename: str, _data: bytes, _subject: str | None = None
    ) -> None:
        return None

    monkeypatch.setattr(
        "ufo_ext_sites.application_builder._PillowApplicationPreview.render", _slow_preview
    )
    monkeypatch.setattr(ToolContext, "share_artifact", _share)
    args = RenderApplicationPreviewInput(
        purpose="Review support requests before assignment.",
        first_screen_priority="Overdue queue",
        regions=("Overdue", "Unassigned"),
        layout="queue-detail",
        design_direction="Compact and factual.",
    )

    task = asyncio.create_task(render_application_preview(_context(FakeSandbox(), tmp_path), args))
    assert await asyncio.to_thread(started.wait, 1)
    release.set()
    await task


def test_application_preview_contract_has_bounded_regions() -> None:
    with pytest.raises(ValidationError):
        RenderApplicationPreviewInput(
            purpose="Review support requests.",
            first_screen_priority="Overdue queue",
            regions=("Only one",),
            layout="queue-detail",
        )


def test_application_preview_scaffold_answers_the_app_bridge() -> None:
    assert b'message.ufo==="ready"' in APPLICATION_PREVIEW_SCAFFOLD
    assert b'ufo:"init"' in APPLICATION_PREVIEW_SCAFFOLD
    assert b"agents:[agent]" in APPLICATION_PREVIEW_SCAFFOLD
    assert b'ufo:"data"' in APPLICATION_PREVIEW_SCAFFOLD
    assert b'path.startsWith("objects/")' in APPLICATION_PREVIEW_SCAFFOLD
    assert b"response={ok:true,name,detail:result}" in APPLICATION_PREVIEW_SCAFFOLD
    assert b'status:{state:"applied",result:stored.result}' in APPLICATION_PREVIEW_SCAFFOLD


def test_application_preview_scaffold_answers_object_reads_and_actions() -> None:
    script = APPLICATION_PREVIEW_SCAFFOLD.decode().split("<script>", 1)[1].split("</script>", 1)[0]
    program = """
const handlers=[];
const values=new Map();
global.location={origin:"http://preview.test"};
global.localStorage={
  getItem:key=>values.get(key)||null,
  setItem:(key,value)=>values.set(key,value)
};
global.window={addEventListener:(_name,handler)=>handlers.push(handler)};
eval(process.argv[1]);
const replies=[];
const source={postMessage:message=>replies.push(message)};
const send=data=>handlers[0]({data,source});
send({ufo:"ready"});
send({ufo:"call",id:"list",method:"GET",path:"/objects/conversation?agent=preview-agent"});
send({ufo:"call",id:"write",method:"POST",path:"/objects/eval_app_action",body:JSON.stringify({name:"assign",spec:{owner:"Alex"}})});
send({ufo:"call",id:"read",method:"GET",path:"/objects/eval_app_action/assign"});
process.stdout.write(JSON.stringify(replies));
"""

    completed = subprocess.run(
        ("node", "-e", program, script),
        check=True,
        capture_output=True,
        text=True,
    )
    replies = json.loads(completed.stdout)
    bodies = {reply["id"]: json.loads(reply["body"]) for reply in replies if reply["ufo"] == "data"}

    assert replies[0]["ufo"] == "init"
    assert bodies["list"]["objects"] == []
    assert bodies["list"]["next_cursor"] is None
    assert bodies["write"] == {
        "ok": True,
        "name": "assign",
        "detail": "Prepared action accepted.",
    }
    assert bodies["read"]["spec"] == {"owner": "Alex"}
    assert bodies["read"]["status"] == {
        "state": "applied",
        "result": "Prepared action accepted.",
    }


def test_application_audit_excludes_hidden_text_from_contrast() -> None:
    source = (
        Path(sites_manifest.__file__).parent / "scripts" / "audit_application.cjs"
    ).read_text()

    assert "if (!visible(element, box)) continue;" in source
    assert "visuallyHidden(element, style, box)" not in source


def test_application_audit_returns_copy_grader_text() -> None:
    source = (
        Path(sites_manifest.__file__).parent / "scripts" / "audit_application.cjs"
    ).read_text()

    returned = source.split("textUnderFloor: underFloor,", 1)[1].split("};", 1)[0]
    assert "renderedText," in returned
    assert "renderedParts," in returned
    assert "aboveFoldText," in returned
    assert "aboveFold.join" not in returned


def test_application_audit_accepts_framed_and_direct_pages() -> None:
    source = (
        Path(sites_manifest.__file__).parent / "scripts" / "audit_application.cjs"
    ).read_text()

    application_frame = source.split("async function applicationFrame(page) {", 1)[1].split(
        "\n}", 1
    )[0]
    assert "await page.$" in application_frame
    assert "if (!element) return page;" in application_frame
    assert "await element.contentFrame()" in application_frame


def test_application_page_builds_with_relative_asset_urls() -> None:
    vite_config = (Path(sites_manifest.__file__).parent / "page" / "vite.config.ts").read_text()

    assert 'base: "./"' in vite_config


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

    assert [ref.card.name for ref in registry.closure("website-building/webapp")] == [
        "website-building/webapp",
        "website-building",
        HOUSE_STYLE,
    ]

    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    for entry in await registry.materialize(registry.closure("website-building/webapp")):
        await mount_skill(_Sandbox(), entry.skill)

    assert "/workspace/.skills/website-building/SKILL.md" in written
    assert "/workspace/.skills/website-building/webapp/SKILL.md" in written
    assert "/workspace/.skills/website-building/shared/01-design-tokens.md" in written
    assert not any(path.startswith("/workspace/.skills/website-building-") for path in written)


def test_application_builder_profile_is_typed_pinned_and_isolated() -> None:
    manifest = sites_manifest.manifest()
    profile = next(
        profile for profile in manifest.subagents if profile.name == APPLICATION_BUILDER_NAME
    )
    write_tool = next(
        tool for tool in manifest.tools if tool.name == APPLICATION_BUILDER_WRITE_TOOL
    )

    assert profile is APPLICATION_BUILDER_PROFILE
    assert profile.model == APPLICATION_BUILDER_MODEL
    assert profile.reasoning == APPLICATION_BUILDER_REASONING
    assert profile.max_rounds == 35
    assert profile.isolated_tools is True
    assert profile.connector_read_only is True
    assert set(profile.tool_names) == {
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "read",
        APPLICATION_BUILDER_DESIGN_TOOL,
        APPLICATION_BUILDER_QA_TOOL,
        APPLICATION_BUILDER_EDIT_TOOL,
        APPLICATION_BUILDER_READ_TOOL,
        APPLICATION_BUILDER_WRITE_TOOL,
        APPLICATION_BUILDER_DEPLOY_TOOL,
    }
    assert profile.untrusted_output is True
    assert profile.input_model is ApplicationBuilderTask
    assert profile.output_model is ApplicationBuilderResult
    assert "Inspect the needed connected sources" in profile.prompt
    assert "Call `qa_ufo_application`" in profile.prompt
    assert "Deploy only after product QA passes" in profile.prompt
    assert "Your result is evidence, not a verdict" in profile.prompt
    edit_tool = next(tool for tool in manifest.tools if tool.name == APPLICATION_BUILDER_EDIT_TOOL)
    edit_schema = edit_tool.input_model.model_json_schema()
    edit_fields = edit_schema["$defs"]["ApplicationSourceEdit"]["properties"]
    assert set(edit_fields) == {"old_text", "new_text"}
    encoded = EditApplicationSourceInput.model_validate(
        {
            "edits": [
                json.dumps(
                    {
                        "old_text": "old",
                        "new_text": "new",
                    }
                )
            ],
        }
    )
    assert encoded.edits == (ApplicationSourceEdit(old_text="old", new_text="new"),)
    patch = EditApplicationSourceInput.model_validate(
        {
            "edits": [
                "<<<<<<< SEARCH\n"
                "const { React } = (window as any).UfoAppKit;\n"
                "=======\n"
                "const { React } = UfoAppKit;\n"
                ">>>>>>> REPLACE"
            ],
        }
    )
    assert patch.edits == (
        ApplicationSourceEdit(
            old_text="const { React } = (window as any).UfoAppKit;",
            new_text="const { React } = UfoAppKit;",
        ),
    )
    pairs = EditApplicationSourceInput.model_validate(
        {
            "edits": ["old one", "new one", "old two", "new two"],
        }
    )
    assert pairs.edits == (
        ApplicationSourceEdit(old_text="old one", new_text="new one"),
        ApplicationSourceEdit(old_text="old two", new_text="new two"),
    )
    trailing_newline = EditApplicationSourceInput.model_validate(
        {
            "edits": ["<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>>\n"],
        }
    )
    assert trailing_newline.edits == (ApplicationSourceEdit(old_text="old", new_text="new"),)
    assert write_tool.profile_only is True
    assert {"spawn", "share_file", "ask_user", "connect_account", "write", "edit"}.isdisjoint(
        profile.tool_names
    )

    with pytest.raises(ValidationError, match=r"source_path must be app\.tsx"):
        ApplicationBuilderTask(
            objective="Build the queue",
            scaffold_path="/workspace/application",
            source_path="/workspace/other/index.html",
        )
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    assert task.objective == "Build the queue"
    assert APPLICATION_BUILDER_SKILL == "ufo-style"
    assert task.preload_skills == (APPLICATION_BUILDER_SKILL,)
    assert set(ApplicationBuilderTask.model_fields) == {
        "objective",
        "scaffold_path",
        "source_path",
        "preload_skills",
    }
    deployed = ApplicationBuilderResult(
        status="deployed",
        source_path=task.source_path,
        site_name="queue",
        site_url="https://queue.example",
        browser_batches=2,
        controls_checked=("Approve", "Filter"),
    )
    assert deployed.site_url == "https://queue.example"
    with pytest.raises(ValidationError, match="requires site_name and site_url"):
        ApplicationBuilderResult(
            status="deployed",
            source_path=task.source_path,
            browser_batches=2,
        )
    with pytest.raises(ValidationError, match="requires a blocker"):
        ApplicationBuilderResult(
            status="blocked",
            source_path=task.source_path,
            browser_batches=0,
        )


async def test_application_product_qa_owns_the_fixed_root_and_records_passed_proof(
    tmp_path: Path,
) -> None:
    report = json.dumps(
        {
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 8,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "console": [],
                    "aboveFoldText": "#2042",
                }
                for width in (1440, 390)
                for scheme in ("light", "dark")
            ],
            "interaction": {
                "controls": [
                    {"selector": "#first", "name": "First"},
                    {"selector": "#second", "name": "Second"},
                ],
                "successes": [
                    {"selector": "#first", "name": "First"},
                    {"selector": "#second", "name": "Second"},
                ],
                "states": [["#2042"]],
                "console": [],
            },
        }
    )
    source = "import { mountApp } from 'ufo/kit';\n"
    sandbox = FakeSandbox(scripted_paths={"/application-audit/": ExecResult(report, "", 0)})
    sandbox.writes["/workspace/ufo-app/app.tsx"] = source.encode()
    parent_turn_id = uuid4()
    store = FakeHookStore(
        values={
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(
                turn_id=parent_turn_id
            ): ApplicationAuditContract(
                facts=(ApplicationAuditFact(label="issue", alternatives=("#2042",)),)
            ).model_dump()
        }
    )
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "parent_turn_id": parent_turn_id,
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    result = await qa_ufo_application(
        ctx,
        QaUfoApplicationInput(),
    )

    payload = json.loads(result.content[0].text)
    assert payload == {
        "status": "passed",
        "views_checked": ["light 1440px", "dark 1440px", "light 390px", "dark 390px"],
        "controls_checked": ["First", "Second"],
        "interactions_verified": ["First", "Second"],
    }
    proof = ApplicationQaProof.model_validate(
        store.values[APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id)]
    )
    assert proof == ApplicationQaProof(
        source_sha256=sha256(source.encode()).hexdigest(),
        browser_batches=1,
    )
    qa_tool = next(
        tool for tool in sites_manifest.manifest().tools if tool.name == APPLICATION_BUILDER_QA_TOOL
    )
    assert set(qa_tool.input_model.model_json_schema()["properties"]) == set()
    await qa_ufo_application(ctx, QaUfoApplicationInput())
    await qa_ufo_application(ctx, QaUfoApplicationInput())
    proof = ApplicationQaProof.model_validate(
        store.values[APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id)]
    )
    assert proof.browser_batches == 3
    with pytest.raises(RuntimeError, match="stopped after 3 product audits"):
        await qa_ufo_application(
            ctx,
            QaUfoApplicationInput(),
        )


async def test_application_product_qa_bounds_dense_control_evidence_before_proof(
    tmp_path: Path,
) -> None:
    controls = [
        {"selector": f"#control-{index}", "name": f"Control {index}"}
        for index in range(MAX_PRODUCT_QA_CONTROLS + 20)
    ]
    report = json.dumps(
        {
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 8,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "console": [],
                    "aboveFoldText": "Dense controls",
                }
                for width in (1440, 390)
                for scheme in ("light", "dark")
            ],
            "interaction": {
                "controls": controls,
                "successes": controls,
                "states": [["Dense controls"]],
                "console": [],
            },
        }
    )
    source = "import { mountApp } from 'ufo/kit';\n"
    sandbox = FakeSandbox(scripted_paths={"/application-audit/": ExecResult(report, "", 0)})
    sandbox.writes["/workspace/ufo-app/app.tsx"] = source.encode()
    parent_turn_id = uuid4()
    store = FakeHookStore(
        values={
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(
                turn_id=parent_turn_id
            ): ApplicationAuditContract().model_dump()
        }
    )
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "parent_turn_id": parent_turn_id,
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )

    result = await qa_ufo_application(
        ctx,
        QaUfoApplicationInput(),
    )

    payload = json.loads(result.content[0].text)
    assert len(payload["controls_checked"]) == MAX_PRODUCT_QA_CONTROLS
    assert len(payload["interactions_verified"]) == MAX_PRODUCT_QA_CONTROLS
    assert APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id) in store.values


async def test_application_product_qa_does_not_record_its_failed_audit(
    tmp_path: Path,
) -> None:
    report = json.dumps(
        {
            "views": [],
            "interaction": {
                "controls": [],
                "successes": [],
                "states": [],
                "console": [],
            },
        }
    )
    sandbox = FakeSandbox(scripted_paths={"/application-audit/": ExecResult(report, "", 0)})
    parent_turn_id = uuid4()
    store = FakeHookStore(
        values={
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(
                turn_id=parent_turn_id
            ): ApplicationAuditContract().model_dump()
        }
    )
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "parent_turn_id": parent_turn_id,
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )

    result = await qa_ufo_application(
        ctx,
        QaUfoApplicationInput(),
    )

    payload = json.loads(result.content[0].text)
    assert payload["status"] == "repair_required"
    assert payload["attempt"] == 1
    assert payload["attempts_remaining"] == 1
    assert APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id) not in store.values
    assert store.values[APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=ctx.turn.id)] == 1


async def test_application_builder_deployment_requires_passed_product_qa(
    tmp_path: Path,
) -> None:
    ctx = _context(FakeSandbox(), tmp_path)
    turn = ctx.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME})
    store = FakeHookStore()
    ext = cast(ExtensionContext, FakeHookExt(store))
    deployment = HookContext(
        ext=ext,
        payload=PreToolUse(
            tool_name=APPLICATION_BUILDER_DEPLOY_TOOL,
            tool_input=DeployUfoApplicationInput(site_name="meeting-tasks"),
        ),
        turn=turn,
    )
    refused = await require_application_builder_qa(deployment)
    assert isinstance(refused, Deny)
    assert refused.reason == APPLICATION_BUILDER_DEPLOY_GUARD_REASON
    store.values[APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=turn.id)] = ApplicationQaProof(
        source_sha256="a" * 64, browser_batches=1
    ).model_dump()
    assert await require_application_builder_qa(deployment) is None


async def test_application_deploy_accepts_only_the_exact_qa_source(tmp_path: Path) -> None:
    source = "import { mountApp } from 'ufo/kit';\n"
    sandbox = FakeSandbox()
    sandbox.writes["/workspace/ufo-app/app.tsx"] = source.encode()
    store = FakeHookStore()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    key = APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id)
    store.values[key] = ApplicationQaProof(
        source_sha256=sha256(source.encode()).hexdigest(),
        browser_batches=1,
    ).model_dump()

    await _require_current_application_qa(ctx)

    sandbox.writes["/workspace/ufo-app/app.tsx"] = b"changed"
    with pytest.raises(RuntimeError, match="changed after product QA passed"):
        await _require_current_application_qa(ctx)
    assert sandbox.shells == []


async def test_application_builder_limits_consecutive_source_reads_after_audit(
    tmp_path: Path,
) -> None:
    ctx = _context(FakeSandbox(), tmp_path)
    turn = ctx.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME})
    store = FakeHookStore()
    ext = cast(ExtensionContext, FakeHookExt(store))

    def source_hook(tool_name: str) -> HookContext:
        return HookContext(
            ext=ext,
            payload=PreToolUse(
                tool_name=tool_name,
                tool_input=ReadApplicationSourceInput(),
            ),
            turn=turn,
        )

    read = source_hook(APPLICATION_BUILDER_READ_TOOL)
    edit = source_hook(APPLICATION_BUILDER_EDIT_TOOL)
    assert await limit_application_builder_repair_reads(read) is None
    await store.put(APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=turn.id), 1)

    for _ in range(APPLICATION_BUILDER_REPAIR_READ_LIMIT):
        assert await limit_application_builder_repair_reads(read) is None
    refused = await limit_application_builder_repair_reads(read)
    assert isinstance(refused, Deny)
    assert refused.reason == APPLICATION_BUILDER_REPAIR_READ_REASON

    assert await limit_application_builder_repair_reads(edit) is None
    for _ in range(APPLICATION_BUILDER_REPAIR_READ_LIMIT):
        assert await limit_application_builder_repair_reads(read) is None


async def test_application_builder_design_is_one_safe_fixed_svg(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    result = await write_application_design(
        ctx,
        WriteApplicationDesignInput(
            content=APPLICATION_DESIGN,
        ),
    )

    payload = json.loads(result.content[0].text)
    assert payload == {
        "path": "/workspace/application/application-design.svg",
        "design_digest": sha256(APPLICATION_DESIGN.encode()).hexdigest(),
        "size_bytes": len(APPLICATION_DESIGN.encode()),
    }
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()
    with pytest.raises(ValueError, match="SVG drawing elements only"):
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(
                content='<svg viewBox="0 0 1 1"><script>fetch("https://bad")</script></svg>',
            ),
        )
    for invalid in (
        '<svg viewBox="0 0 0 0"><rect width="1" height="1" /></svg>',
        '<svg viewBox="not-a-box"><rect width="1" height="1" /></svg>',
        '<svg viewBox="0 0 1280 800"><metadata>no screen</metadata></svg>',
        '<svg viewBox="0 0 1280 800"><rect /></svg>',
        '<svg viewBox="0 0 1280 800"><path /></svg>',
        '<svg viewBox="0 0 1280 800"><text /></svg>',
        '<svg viewBox="0 0 1280 800"><use /></svg>',
    ):
        with pytest.raises(ValueError):
            await write_application_design(
                replace(ctx, turn=ctx.turn.model_copy(update={"id": uuid4()})),
                WriteApplicationDesignInput(
                    content=invalid,
                ),
            )


async def test_application_builder_design_is_isolated_per_build_turn(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    first = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )
    second = replace(first, turn=first.turn.model_copy(update={"id": uuid4()}))

    await write_application_design(
        first,
        WriteApplicationDesignInput(
            content=APPLICATION_DESIGN,
        ),
    )
    await write_application_design(
        second,
        WriteApplicationDesignInput(
            content=APPLICATION_DESIGN.replace("900", "800"),
        ),
    )

    claims = [args[0] for program, args in sandbox.programs if program == APPLICATION_SOURCE_CLAIM]
    assert len(set(claims)) == 2
    assert b"800" in sandbox.writes["/workspace/application/application-design.svg"]


async def test_application_source_requires_the_svg_design(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    base = _context(FakeSandbox(), tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    with pytest.raises(ValueError, match="write_application_design must complete"):
        await write_application_source(
            ctx,
            WriteApplicationSourceInput(
                content=(
                    'import { mountApp } from "ufo/kit";\n'
                    'mountApp(document.getElementById("root")!, () => <main />);'
                ),
            ),
        )


async def test_application_source_requires_this_build_turns_svg_design(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    first = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )
    await write_application_design(
        first,
        WriteApplicationDesignInput(
            content=APPLICATION_DESIGN,
        ),
    )
    second = replace(first, turn=first.turn.model_copy(update={"id": uuid4()}))
    design_path = "/workspace/application/application-design.svg"
    second_claim = (
        "/workspace/.tool-output/application-builder/"
        f"{sha256(design_path.encode()).hexdigest()}.{second.turn.id}.claimed"
    )
    sandbox.scripted_paths[second_claim] = ExecResult("", "", 17)

    with pytest.raises(ValueError, match="write_application_design must complete"):
        await write_application_source(
            second,
            WriteApplicationSourceInput(
                content=(
                    'import { mountApp } from "ufo/kit";\n'
                    'mountApp(document.getElementById("root")!, () => <main />);'
                ),
            ),
        )

    assert (APPLICATION_SOURCE_REQUIRE_CLAIM, (second_claim,)) in sandbox.programs


async def test_application_builder_write_tool_writes_only_the_contract_source(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    _seed_application_design(sandbox)
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    source = (
        'import { mountApp } from "ufo/kit";\nmountApp(document.getElementById("root")!, '
        "() => <main />);"
    )
    assert WriteApplicationSourceInput.model_fields["content"].description == (
        "Complete app.tsx source. Use named ufo/kit imports and no export declarations."
    )
    result = await write_application_source(
        ctx,
        WriteApplicationSourceInput(content=source),
    )

    assert sandbox.writes["/workspace/application/app.tsx"] == source.encode()
    assert [
        content for path, content in sandbox.writes.items() if path.endswith(".candidate.tsx")
    ] == [source.encode()]
    assert [content for path, content in sandbox.writes.items() if path.endswith(".accepted")] == [
        sha256(source.encode()).hexdigest().encode()
    ]
    assert len(sandbox.shells) == 2
    assert all(script == 'cd "$1" && vite build' for script, _, _ in sandbox.shells)
    assert json.loads(result.content[0].text) == {
        "path": "/workspace/application/app.tsx",
        "size_bytes": len(source.encode()),
    }


async def test_application_builder_write_tool_rejects_another_module(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    _seed_application_design(sandbox)
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    with pytest.raises(ValueError, match="may import only from ufo/kit"):
        await write_application_source(
            ctx,
            WriteApplicationSourceInput(
                content=(
                    'import React from "react";\n'
                    'import { mountApp } from "ufo/kit";\n'
                    'mountApp(document.getElementById("root")!, () => <main />);'
                ),
            ),
        )

    assert "/workspace/application/app.tsx" not in sandbox.writes
    assert [path for path in sandbox.writes if path.endswith(".candidate.tsx")]


async def test_application_builder_write_tool_allows_apostrophes_in_jsx_text(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    _seed_application_design(sandbox)
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )
    source = (
        'import { mountApp } from "ufo/kit";\n'
        "function App() { return <main>What's next</main>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )

    await write_application_source(
        ctx,
        WriteApplicationSourceInput(content=source),
    )

    assert sandbox.writes["/workspace/application/app.tsx"] == source.encode()


async def test_application_builder_write_tool_rejects_the_old_runtime_global(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    _seed_application_design(sandbox)
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    with pytest.raises(ValueError, match="import from ufo/kit instead of using UfoAppKit"):
        await write_application_source(
            ctx,
            WriteApplicationSourceInput(
                content=(
                    'import { mountApp } from "ufo/kit";\n'
                    "const oldRuntime = UfoAppKit;\n"
                    'mountApp(document.getElementById("root")!, () => <main />);'
                ),
            ),
        )

    assert "/workspace/application/app.tsx" not in sandbox.writes
    assert [path for path in sandbox.writes if path.endswith(".candidate.tsx")]


async def test_application_builder_write_tool_rejects_an_invalid_mount(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    _seed_application_design(sandbox)
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    with pytest.raises(ValueError, match="root element and a render callback"):
        await write_application_source(
            ctx,
            WriteApplicationSourceInput(
                content='import { mountApp } from "ufo/kit";\nmountApp(App);',
            ),
        )

    assert "/workspace/application/app.tsx" not in sandbox.writes
    assert [path for path in sandbox.writes if path.endswith(".candidate.tsx")]


async def test_application_builder_write_tool_rejects_a_second_initial_build(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox()
    _seed_application_design(sandbox)
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )
    source_claim = (
        "/workspace/.tool-output/application-builder/"
        f"{sha256(task.source_path.encode()).hexdigest()}.{ctx.turn.id}.claimed"
    )
    sandbox.scripted_paths[source_claim] = ExecResult("", "", 17)

    with pytest.raises(ValueError, match=r"initial app\.tsx candidate already exists"):
        await write_application_source(
            ctx,
            WriteApplicationSourceInput(
                content=(
                    'import { mountApp } from "ufo/kit";\n'
                    'mountApp(document.getElementById("root")!, () => <main />);'
                ),
            ),
        )

    assert "/workspace/application/app.tsx" not in sandbox.writes
    assert sandbox.writes == {
        "/workspace/application/application-design.svg": APPLICATION_DESIGN.encode()
    }


async def test_application_builder_read_tool_returns_bounded_repair_excerpts(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Repair the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    source = "\n".join(
        (
            'import { mountApp } from "ufo/kit";',
            *(f"const line{number} = {number};" for number in range(300)),
            'function App() { return <main className="status-copy" />; }',
            'mountApp(document.getElementById("root")!, () => <App />);',
        )
    )
    sandbox = FakeSandbox(
        claim=ExecResult(stdout=source, stderr="", exit_code=0),
    )
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    result = await read_application_source(
        ctx,
        ReadApplicationSourceInput(
            terms=("status-copy", "mountApp"),
        ),
    )

    assert "status-copy" in result.content[0].text
    assert "mountApp(document.getElementById" in result.content[0].text
    assert "SOURCE LINES " in result.content[0].text
    assert "END SOURCE" in result.content[0].text
    assert "const line150" not in result.content[0].text
    assert len(result.content[0].text) <= 5_000


async def test_application_builder_read_tool_balances_repeated_and_distinct_terms(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Repair the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    source = "\n".join(
        (
            'import { mountApp } from "ufo/kit";',
            *(f"const repeated{number} = 'color-mark-soft';" for number in range(80)),
            "const badge = 'Prepared';",
            *(f"const middle{number} = {number};" for number in range(80)),
            "const load = 'open issues now';",
            'mountApp(document.getElementById("root")!, () => <App />);',
        )
    )
    sandbox = FakeSandbox(claim=ExecResult(stdout=source, stderr="", exit_code=0))
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    result = await read_application_source(
        ctx,
        ReadApplicationSourceInput(
            terms=("color-mark-soft", "Prepared", "open issues now"),
        ),
    )

    text = result.content[0].text
    assert 'matches for "color-mark-soft": 80' in text
    assert 'matches for "Prepared": 1' in text
    assert 'matches for "open issues now": 1' in text
    assert "const repeated40 = 'color-mark-soft';" in text
    assert "const repeated79 = 'color-mark-soft';" in text
    assert "const badge = 'Prepared';" in text
    assert "const load = 'open issues now';" in text
    assert len(text) <= 5_000


async def test_application_builder_read_tool_allows_another_bounded_read(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Repair the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    source = (
        'import { mountApp } from "ufo/kit";\n'
        'mountApp(document.getElementById("root")!, () => <main>Old title</main>);'
    )
    sandbox = FakeSandbox(scripted_programs={APPLICATION_SOURCE_READ: ExecResult(source, "", 0)})
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    first = await read_application_source(
        ctx,
        ReadApplicationSourceInput(
            terms=("Old title",),
        ),
    )
    second = await read_application_source(
        ctx,
        ReadApplicationSourceInput(
            terms=("mountApp",),
        ),
    )

    assert "Old title" in first.content[0].text
    assert "mountApp" in second.content[0].text


async def test_application_builder_edit_tool_applies_one_bounded_repair(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Repair the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    source = 'import { mountApp } from "ufo/kit";\nmountApp(App);'
    replacement = 'mountApp(document.getElementById("root")!, () => <App />);'
    corrected = f'import {{ mountApp }} from "ufo/kit";\n{replacement}'
    sandbox = FakeSandbox(claim=ExecResult(stdout=source, stderr="", exit_code=0))
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    result = await edit_application_source(
        ctx,
        EditApplicationSourceInput(
            edits=(
                ApplicationSourceEdit(
                    old_text="mountApp(App);",
                    new_text=replacement,
                ),
            ),
        ),
    )

    assert sandbox.writes["/workspace/application/app.tsx"] == corrected.encode()
    assert [
        content for path, content in sandbox.writes.items() if path.endswith(".candidate.tsx")
    ] == [corrected.encode()]
    assert [content for path, content in sandbox.writes.items() if path.endswith(".accepted")] == [
        sha256(corrected.encode()).hexdigest().encode()
    ]
    assert json.loads(result.content[0].text) == {
        "path": "/workspace/application/app.tsx",
        "replacements": 1,
        "size_bytes": len(corrected.encode()),
    }


async def test_application_builder_edit_tool_rejects_structural_damage(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Repair the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    source = (
        'import { mountApp } from "ufo/kit";\n'
        "function App() { return <main><section>Old</section></main>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    sandbox = FakeSandbox(
        claim=ExecResult(stdout=source, stderr="", exit_code=0),
        shell=ExecResult(stdout="", stderr="Unexpected closing tag", exit_code=1),
    )
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )
    with pytest.raises(ValueError, match="does not compile: Unexpected closing tag"):
        await edit_application_source(
            ctx,
            EditApplicationSourceInput(
                edits=(
                    ApplicationSourceEdit(
                        old_text="Old",
                        new_text="Old</section>",
                    ),
                ),
            ),
        )

    assert "/workspace/application/app.tsx" not in sandbox.writes
    assert [path for path in sandbox.writes if path.endswith(".candidate.tsx")]


async def test_application_builder_edit_tool_rejects_ambiguous_old_text(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Repair the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    source = (
        'import { mountApp } from "ufo/kit";\n'
        "function App() { return <main>Old Old</main>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    sandbox = FakeSandbox(claim=ExecResult(stdout=source, stderr="", exit_code=0))
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    with pytest.raises(ValueError, match="old_text must occur exactly once"):
        await edit_application_source(
            ctx,
            EditApplicationSourceInput(
                edits=(ApplicationSourceEdit(old_text="Old", new_text="New"),),
            ),
        )

    assert sandbox.writes == {}


async def test_application_builder_rejects_source_the_product_compiler_rejects(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox(shell=ExecResult("", "Unexpected token at 12:4", 1))
    _seed_application_design(sandbox)
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    with pytest.raises(ValueError, match="Candidate retained"):
        await write_application_source(
            ctx,
            WriteApplicationSourceInput(
                content=(
                    'import { mountApp } from "ufo/kit";\n'
                    'mountApp(document.getElementById("root")!, () => <main />);'
                ),
            ),
        )

    assert "/workspace/application/app.tsx" not in sandbox.writes
    assert [path for path in sandbox.writes if path.endswith(".candidate.tsx")]
    assert [program for program, _ in sandbox.programs[:3]] == [
        APPLICATION_SOURCE_REQUIRE_CLAIM,
        APPLICATION_SOURCE_READ,
        APPLICATION_SOURCE_CLAIM,
    ]


async def test_application_builder_read_tool_rejects_an_initial_build(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox(claim=ExecResult("", "", 17))
    ctx = _context(sandbox, tmp_path)
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
    )

    with pytest.raises(ValueError, match="write_application_source must complete"):
        await read_application_source(ctx, ReadApplicationSourceInput())

    assert len(sandbox.programs) == 1


async def test_website_building_pulls_the_house_style_and_scopes_it_to_our_own_pages() -> None:
    """A page of ours is built in the house tokens with no second load, so the tokens mount with the
    skill and the art-direction ladder states the exception before its first rung. A site with a
    subject of its own, or a member who named a style, still overrides it — without that the ladder
    below would be dead wording and every site would come out looking like the portal."""
    registry = skill_registry((sites_manifest.manifest(),))
    assert [ref.card.name for ref in registry.closure("website-building")] == [
        "website-building",
        HOUSE_STYLE,
    ]

    written: dict[str, bytes] = {}

    class _Sandbox:
        async def write_file(self, path: str, content: bytes) -> None:
            written[path] = content

    for entry in await registry.materialize(registry.closure("website-building")):
        await mount_skill(_Sandbox(), entry.skill)
    assert f"/workspace/.skills/{HOUSE_STYLE}/{HOUSE_STYLE_TOKENS}" in written

    instructions = registry.named("website-building").instructions
    assert HOUSE_STYLE in instructions
    assert "Member-supplied design direction wins" in instructions


def test_ufo_application_homepages_use_one_end_to_end_worker() -> None:
    instructions = (
        skill_registry((sites_manifest.manifest(),)).named("website-building").instructions
    )
    application = instructions.partition("## ufo application homepage")[2].partition(
        "## Build and verify"
    )[0]
    application_text = " ".join(application.split())

    assert application
    assert PLAYWRIGHT_GUIDANCE not in application
    assert "checks decide acceptance and bind the homepage" in application_text
    assert "build_ufo_application(" not in application
    assert "user_description" not in application
    assert "worker receives the fixed scaffold and source paths" in application_text
    assert "parent does not inspect connector data, source, or browser output" in application_text
    assert "does not repair, deploy, verify, or delegate again" in application_text
    assert "Deterministic product checks decide acceptance" in application_text
    assert "no stable worker or harness rule" in application
    assert "repair_diagnostics" not in application
    assert "source_facts" not in application
    assert "requirements" not in application
    assert "Do not call `build_website`" in application


def test_interactive_internal_homepages_route_to_the_application_workflow() -> None:
    instructions = (
        skill_registry((sites_manifest.manifest(),)).named("website-building").instructions
    )
    shape = instructions.partition("## Choose the build shape")[2].partition(
        "For a sparse internal-page request"
    )[0]

    assert (
        "Interactive homepage, dashboard, tracker, board, console, or operational workspace"
        in shape
    )
    assert "Non-interactive internal reference page" in shape


def test_ufo_application_qa_is_progressive_and_batches_full_proof() -> None:
    guidance = _application_qa_guidance()

    assert 'start_server(project_path="/workspace/ufo-app", port=3000)' in guidance
    assert "open the returned URL at `/preview.html`" in guidance
    assert 'page.frame({ name: "ufo-app" })' in guidance
    assert "Run locators and evaluations on that" in guidance
    assert "frame. The top page is only the product preview shell" in guidance
    assert "Call 1 opens the page and completes every functional check" in guidance
    assert "Call 2 completes every visual check" in guidance
    assert "Call `emitImage` three times" in guidance
    assert "Do not call `js_repl` a fifth time" in guidance
    assert "separate smoke, setup" in guidance
    assert "Never carry variables" in guidance
    assert "Playwright objects across calls" in guidance
    assert "console.log(JSON.stringify(out))" in guidance
    assert "returns stdout, not the value of a final expression" in guidance
    assert "complete defect-discovery pass" in guidance
    assert "Reserve calls 3 and 4 for final repair proof" in guidance
    assert "Do not edit after a repair-proof call" in guidance
    assert "page default timeout to 5 seconds" in guidance
    assert "accessible role and name" in guidance
    assert "guessed CSS selector" in guidance
    assert "every accessible control" in guidance
    assert "light and dark" in guidance
    assert "initial, hover, focus" in guidance
    assert "phone width" in guidance
    assert "Measure every visible text node" in guidance
    assert "for contrast in light and dark modes" in guidance
    assert "console errors" in guidance
    assert "Do not use one call per control" in guidance
    assert Path(PLAYWRIGHT_GUIDANCE).name in guidance
    assert "--remote-debugging-port" in guidance
    assert "background=true" in guidance
    assert "connectOverCDP" in guidance
    assert "browser.close()" in guidance


def test_start_server_schema_makes_the_static_path_the_default() -> None:
    schema = StartServerInput.model_json_schema()

    assert "command" not in schema["required"]
    assert (
        "Omit to serve the folder as static files" in schema["properties"]["command"]["description"]
    )
    for command in ("", "   "):
        with pytest.raises(ValidationError, match="command must contain"):
            StartServerInput(
                command=command,
                project_path="/workspace/site",
            )


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
    assert {tool.name for tool in SITES_TOOLS if tool.profile_only}.isdisjoint(names)
    assert "share_file" not in names
    assert WEBSITE_BUILDING_PROFILE.input_model.model_validate(
        {"objective": "build a landing page"}
    ).objective
    assert WEBSITE_BUILDING_PROFILE.max_rounds == 100


def test_the_website_building_profile_uses_the_shared_finish_contract() -> None:
    prompt = subagent_system_prompt(WEBSITE_BUILDING_PROFILE)
    assert "A delivery crosses an agent boundary" in prompt
    assert "call `finish` directly" not in WEBSITE_BUILDING_PROFILE.prompt
    assert "End the turn by calling the `finish` tool" in prompt


def test_the_playwright_guidance_keeps_the_browser_outside_the_cell() -> None:
    """`js_repl` runs one `node` process per call and returns only when that process exits, so a
    browser held open for the next cell keeps the process on the event loop until the budget expires
    and the run is killed. The browser has to outlive the cell as a process of its own: started once
    through `bash` behind a debug port, connected to per cell."""
    guidance = _playwright_guidance()
    assert "--remote-debugging-port" in guidance
    assert "background: true" in guidance
    assert "connectOverCDP" in guidance
    assert "chromium.launch(" not in guidance
    lowered = guidance.lower()
    assert "keep the handles alive" not in lowered
    assert "handles alive across" not in lowered
    assert "bootstrap" not in lowered, "the bootstrap cell is the timeout this guidance replaced"


def test_the_qa_guidance_starts_a_browser_that_touches_no_keychain() -> None:
    for guidance in (_playwright_guidance(), _application_qa_guidance()):
        launch = next(
            block
            for block in re.findall(r"```\n(.*?)```", guidance, re.S)
            if "--headless=new" in block
        )
        for text in (
            "--use-mock-keychain",
            "--password-store=basic",
            'PROFILE="$(mktemp -d /tmp/ufo-chrome-qa.XXXXXX)"',
            "trap 'rm -rf \"$PROFILE\"' EXIT INT TERM",
            '--user-data-dir="$PROFILE"',
        ):
            assert text in launch
        assert "/Applications" not in launch
        assert 'if [ "$(uname -s)" = Linux ]; then CONTAINED=' in launch
        assert "--password-store=basic $CONTAINED" in launch


def test_every_playwright_example_cell_can_exit() -> None:
    """Each example is a cell the model runs verbatim, so a connect with no matching close is a
    documented deadline loss. The close sits in `finally`, so a check that throws still exits."""
    cells = [cell for cell in JS_CELL.findall(_playwright_guidance()) if "connectOverCDP" in cell]
    assert len(cells) >= 3
    for cell in cells:
        assert "browser.close()" in cell
        assert "} finally {" in cell


def test_the_playwright_signoff_gates_a_visual_claim_on_a_screenshot() -> None:
    """A turn whose every browser call failed still reported "Verified at desktop and phone widths".
    Signoff names the evidence a visual claim rests on, and names what the reply must say when that
    evidence does not exist."""
    signoff = _playwright_guidance().split("### Signoff", 1)[1].split("\n## ", 1)[0]
    assert "At least one screenshot came back successfully" in signoff
    assert "exit_code: 0" in signoff
    assert "make no visual claim" in signoff
    assert "visual verification was skipped" in signoff


def test_the_website_building_prompt_gates_a_visual_claim_on_a_screenshot() -> None:
    """The child's result is what the parent repeats to the member, so the same gate has to hold in
    the profile prompt: the skill file only binds a child that read it."""
    prompt = WEBSITE_BUILDING_PROFILE.prompt
    assert "connects to Chromium over CDP" in prompt
    assert "exit_code: 0" in prompt
    assert "visual verification was skipped" in prompt


async def test_website_builds_and_lists_the_output(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"ls -1A": ExecResult(stdout="index.html\nstyle.css\n", stderr="", exit_code=0)}
    )
    ctx = _context(sandbox, tmp_path)
    result = await website(
        ctx,
        WebsiteInput(
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
        await website(ctx, WebsiteInput(run_command="npm run build"))


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
            StartServerInput(command="python3 app.py", project_path="/workspace"),
        )


async def test_start_server_serves_a_static_folder_without_a_command(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)

    result = await start_server(
        ctx,
        StartServerInput(
            project_path="/workspace/site",
            port=5173,
        ),
    )

    payload = json.loads(result.content[0].text)
    assert payload["url"] == "http://localhost:5173"
    launch = next(command for command in sandbox.commands if "nohup" in command)
    assert "nohup env PORT=5173 python3 -m http.server 5173 --bind 0.0.0.0" in launch


async def test_application_builder_start_server_returns_the_framed_preview(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
    )

    result = await start_server(
        ctx,
        StartServerInput(
            project_path="/workspace/ufo-app",
            port=5173,
        ),
    )

    payload = json.loads(result.content[0].text)
    assert payload["url"] == "http://localhost:5173/preview.html"


async def test_application_builder_start_server_rejects_an_alternate_server(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
    )

    with pytest.raises(RuntimeError, match="project_path must be /workspace/ufo-app"):
        await start_server(
            ctx,
            StartServerInput(
                project_path="/workspace/ufo-app/dist",
            ),
        )
    with pytest.raises(RuntimeError, match="does not accept a command"):
        await start_server(
            ctx,
            StartServerInput(
                command="npm run dev",
                project_path="/workspace/ufo-app",
            ),
        )

    assert sandbox.commands == []


async def test_the_failure_log_tail_states_its_own_budget(tmp_path: Path) -> None:
    """The tail runs after a start that already ended, and reads twenty lines out of one file: it
    must not sit on the sandbox's 120s default, which is a budget for work rather than for a failure
    report."""
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
            StartServerInput(command="python3 app.py", project_path="/workspace"),
        )
    tail = next(command for command in sandbox.commands if command.startswith("tail -n 20"))
    assert sandbox.budgets[tail] == LOG_TAIL_TIMEOUT_SECONDS


async def test_a_start_the_sandbox_killed_names_the_deadline_when_the_log_is_empty(
    tmp_path: Path,
) -> None:
    """The carrier's kill leaves its own `timed out` and nothing else, so a server that never bound
    its port and wrote no log would be reported as an empty error. Name the deadline that ended it
    and say the log holds nothing."""
    sandbox = FakeSandbox(
        scripted={
            "nohup": ExecResult(
                stdout="",
                stderr="",
                exit_code=124,
                timed_out_after_s=READINESS_TIMEOUT_SECONDS + 5,
            )
        }
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match=f"after {READINESS_TIMEOUT_SECONDS + 5}s"):
        await start_server(
            ctx,
            StartServerInput(command="python3 app.py", project_path="/workspace"),
        )


async def test_a_model_named_path_is_scoped_to_the_workspace(tmp_path: Path) -> None:
    """These tools quote their path arguments into `cd` and `>` rather than opening them, and under
    the local carrier those run on the host. So every path the model names is resolved under
    /workspace first: a traversal is refused before a command is built, and nothing runs."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        await website(
            ctx,
            WebsiteInput(
                run_command="npm run build",
                project_path="/workspace/../etc",
            ),
        )
    with pytest.raises(ValueError, match="escapes"):
        await start_server(
            ctx,
            StartServerInput(
                command="python3 app.py",
                project_path="/workspace",
                log_file="/etc/cron.d/server.log",
            ),
        )
    assert sandbox.commands == []


async def test_a_server_log_defaults_into_the_engines_own_offload_dir(tmp_path: Path) -> None:
    """A log path defaulted into a shared directory is a predictable name in a place the agent can
    write, which is the setup for a swap rather than merely untidy. The default is `.tool-output`,
    the engine's own directory under the workspace the tool already serves from — a server log is
    scaffolding, and the workspace listing is the member's own file list. The tool reports it."""
    sandbox = FakeSandbox()
    ctx = _context(sandbox, tmp_path)
    result = await start_server(
        ctx,
        StartServerInput(
            command="python3 app.py",
            project_path="site",
            port=5173,
        ),
    )
    payload = json.loads(result.content[0].text)
    assert payload["log"] == "/workspace/.tool-output/server-5173.log"
    assert payload["project_path"] == "/workspace/site"
    assert sandbox.programs == [
        (LOG_CLEAR_PROG, ("/workspace/.tool-output/server-5173.log", "/workspace")),
        (PORT_STOP_PROG, ("5173",)),
    ]
    launch = next(
        command
        for command in sandbox.commands
        if ">/workspace/.tool-output/server-5173.log" in command
    )
    redirect = launch.index(">/workspace/.tool-output/server-5173.log")
    assert "set -C\n" in launch[:redirect], "the redirect must create the log, never truncate it"
    assert "set +C\n" in launch[redirect:], "noclobber must not outlive the log's own redirect"


async def test_the_log_name_is_freed_through_the_guard_before_the_redirect_creates_it(
    tmp_path: Path,
) -> None:
    """`nohup … >log` follows a link and truncates what it points at, and the log's name is a
    predictable one in a directory the agent writes. So the name is emptied through the containment
    guard first and the redirect runs under `set -C`, which creates the file `O_CREAT|O_EXCL`: a
    link replanted between the two commands fails the launch rather than steering it. A clear the
    guard refuses stops the launch instead of running the redirect anyway."""
    sandbox = FakeSandbox(
        claim=ExecResult(stdout="", stderr="log.txt is not a regular file", exit_code=1)
    )
    ctx = _context(sandbox, tmp_path)

    with pytest.raises(RuntimeError, match="not a regular file"):
        await start_server(
            ctx,
            StartServerInput(
                command="python3 app.py",
                project_path="/workspace",
                log_file="log.txt",
            ),
        )

    assert sandbox.programs == [(LOG_CLEAR_PROG, ("/workspace/log.txt", "/workspace"))]
    assert "containment" in LOG_CLEAR_PROG
    assert sandbox.commands == []


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
    conversation_id = uuid4()
    object_name = site_object_name(conversation_id, "dashboard")
    assert site_name_from_object(conversation_id, object_name) == "dashboard"
    assert site_name_from_object(uuid4(), object_name) is None
    for nameless in ("---", "   ", "🌱🌱", "!!!"):
        with pytest.raises(InvalidSiteName, match="letters or digits"):
            site_name(nameless)


def test_the_card_page_carries_the_approved_composition() -> None:
    """The card's measurements live in one page, so they are read back off it: the 1200x630 board,
    the 456px panel and the 744px the site's page fills, the kicker above the lockup, and the name
    at the foot clamped to two lines. The brand files ride inside the page rather than being
    fetched, because the sandbox that draws it has no checkout and no route to the portal.

    The name is the member's own text landing in markup, so the escape is asserted here too."""
    page = card_page("Marketing <script>alert(1)</script>", CARD_SHOT_DRAWN)

    assert CARD_WIDTH == PANEL_WIDTH + SHOT_WIDTH
    assert f"width:{CARD_WIDTH}px; height:{CARD_HEIGHT}px" in page
    assert f"width:{PANEL_WIDTH}px" in page
    assert f"width:{SHOT_WIDTH}px" in page
    assert ">Made with<" in page
    assert "font-size:26px" in page and "letter-spacing:0.15em" in page
    assert "font-size:34px" in page and "line-height:1.28" in page
    assert "-webkit-line-clamp:2" in page
    assert "<svg" in page and "<?xml" not in page
    assert "data:font/woff2;base64," in page
    assert SHOT_TOKEN in page
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    # The shot is sized two ways and fitted neither: halved for a shot taken at the card's own
    # width, at its own size for a stored page picture the box then crops.
    assert f"width:{SHOT_WIDTH}px;height:{CARD_HEIGHT}px" in page
    assert "width:auto;height:auto" in card_page("marketing", STORED_SHOT_DRAWN)


def test_the_cards_brand_files_are_the_portals_own() -> None:
    """The panel is drawn inside the sandbox, so the lockup and the face are copied into this
    package rather than read from the portal's source tree. A copy is only honest while it stays
    identical, so both are held byte for byte — the rule the gateway's own mark is held to."""
    portal = Path("extensions/web/frontend/src/assets")
    assets = Path(share_card.__file__).parent / "assets"

    assert (assets / LOGO_ASSET).read_bytes() == (portal / "ufo-logo.svg").read_bytes()
    assert (assets / FONT_ASSET).read_bytes() == (portal / "fonts" / FONT_ASSET).read_bytes()


def test_the_shot_is_drawn_by_the_sandbox_browser_under_its_own_wall() -> None:
    """Both shots a card costs are taken by the browser available to the sandbox and the CI runner,
    which is the one the site's page shot is taken with, so a card costs no second renderer.

    The wall is asserted with it, because nothing on the browser's command line ends a run: the
    driver walls itself and kills the browser through its one exit. `--virtual-time-budget` is
    asserted absent, because the capture waits for that budget and virtual time stands still while
    any fetch is pending, so a browser carrying it draws nothing at all wherever a background
    service holds one open.

    The unattended flags ride in every run for the same reason: a runner has no keyring, no keychain
    and no crash server, and a browser that waits on one of those spends the whole wall and draws
    nothing."""
    shot = "/workspace/.tool-output/share-card-shot-marketing.png"
    command = shot_command(
        url="http://127.0.0.1:8000",
        width=SHOT_WIDTH,
        height=CARD_HEIGHT,
        scale=2,
        shot=shot,
        root="/workspace",
    )

    assert f"for candidate in {shlex.join(BROWSER_COMMANDS)}; do" in command
    assert "/Applications" not in command
    assert "playwright" not in command
    assert "--virtual-time-budget" not in command
    for flag in UNATTENDED_FLAGS.split():
        assert flag in command
    assert "--password-store=basic" in command
    assert "--use-mock-keychain" in command
    assert "--disable-component-extensions-with-background-pages" in command
    assert f"DEADLINE = {SHOT_DEADLINE_SECONDS}" in command
    assert "timeout " not in command
    assert SHOT_DEADLINE_SECONDS < CARD_TIMEOUT_SECONDS
    assert f"test -s {shlex.quote(shot)}" in command
    assert 'CONTAINED = "--no-sandbox --disable-dev-shm-usage"' in command
    assert 'CONTAINED.split() if sys.platform.startswith("linux") else []' in command
    assert 'profile="$(mktemp -d /tmp/ufo-share-card.XXXXXX)"' in command
    assert "trap 'rm -rf \"$profile\"' EXIT INT TERM" in command


def test_the_shot_is_driven_to_a_settle_point_inside_its_wall() -> None:
    """The browser is driven rather than one-shot, because the load event is the only settle point a
    one-shot chromium offers and it is too early: a page that reveals its content with an entrance
    animation, or writes its DOM after load, is photographed blank there — and a blank shot is a
    white PNG, which is not an empty file, so the size check accepts it as a picture.

    So the run carries the driver: it speaks the DevTools protocol over the browser's own pipe,
    waits for the load event and then for a frame that repeats with no request in flight, and
    refuses a shot that is one flat colour. Every wait it takes is walled, and the two walls
    together sit inside the kill wall, so a page that never goes idle is photographed rather than
    waited on — the failure `--virtual-time-budget` had, which is why it is asserted absent
    above."""
    shot = "/workspace/.tool-output/preview-8000.png"
    command = shot_command(
        url="http://127.0.0.1:8000",
        width=PREVIEW_WIDTH,
        height=PREVIEW_HEIGHT,
        scale=1,
        shot=shot,
        root="/workspace",
    )

    assert "--screenshot=" not in command
    assert "--remote-debugging-pipe" in command
    assert "Page.loadEventFired" in command
    assert "Network.enable" in command
    assert "Page.captureScreenshot" in command
    assert "Emulation.setDeviceMetricsOverride" in command
    assert "flat colour" in command
    # The driver runs isolated with the baked guard on its path, and writes the shot through the
    # guard rather than letting the browser create a name the agent's own directory holds.
    assert f"python3 {SANDBOX_PYTHON_FLAG} -" in command
    assert SANDBOX_MODULE_BOOTSTRAP in command
    assert "contained_file" in command
    assert LOAD_WALL_SECONDS + SETTLE_WALL_SECONDS < SHOT_DEADLINE_SECONDS
    assert f"LOAD_WALL = {LOAD_WALL_SECONDS}" in command
    assert f"SETTLE_WALL = {SETTLE_WALL_SECONDS}" in command
    assert f"FRAME = {FRAME_SECONDS}" in command
    for argument in (shlex.quote(shot), '"$profile"', "/workspace"):
        assert argument in command


def test_start_server_rejects_an_out_of_range_port() -> None:
    with pytest.raises(ValidationError, match="between 1 and 65535"):
        StartServerInput(
            command="python3 app.py",
            project_path="/workspace",
            port=99999,
        )
