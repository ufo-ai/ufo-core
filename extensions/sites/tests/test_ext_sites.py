import asyncio
import json
import re
import shlex
import subprocess
import sys
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, TypeAdapter, ValidationError
from ufo_ext_repl.manifest import JS_REPL_TOOL, XLSX_REPL_TOOL
from ufo_ext_research.tools import FETCH_URL_TOOL, SEARCH_VERTICAL_TOOL, SEARCH_WEB_TOOL
from ufo_ext_sites import manifest as sites_manifest
from ufo_ext_sites import share_card
from ufo_ext_sites import tools as sites_tools
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_ATTEMPT_KEY,
    APPLICATION_AUDIT_REQUEST_CONTRACT_KEY,
    APPLICATION_AUDIT_TURN_CONTRACT_KEY,
    APPLICATION_DESIGN_FOLD,
    APPLICATION_DESIGN_MAX_HEIGHT,
    APPLICATION_REGION_MIN_AREA,
    APPLICATION_REGION_MIN_HEIGHT,
    APPLICATION_REGION_MIN_WIDTH,
    DESIGN_REGION_FOLD_SLOP,
    DESIGN_VISIBLE_TEXT_MAX_CHARS,
    DESKTOP_WIDTH,
    KIT_QUIET_TEXT_MIN,
    MAX_PRODUCT_QA_CONTROLS,
    NARROW_WIDTH,
    AcceptedApplicationDesignEvidence,
    ApplicationAuditContract,
    ApplicationAuditFact,
    ApplicationAuditFeedback,
    ApplicationAuditRegion,
    ApplicationAuditReport,
    ApplicationDesignFidelity,
    ApplicationQaProof,
    application_design_fidelity,
    application_design_region_fold_failure,
    application_design_region_size_failure,
    application_region_relation,
    audit_application,
)
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL,
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
    APPLICATION_BUILDER_WIREFRAME_TOOL,
    APPLICATION_BUILDER_WRITE_TOOL,
    APPLICATION_CREATION_ROUTE_KEY,
    APPLICATION_CREATION_ROUTE_REASON,
    APPLICATION_DESIGN_ACCEPT,
    APPLICATION_DESIGN_AUDIT_MAX_BYTES,
    APPLICATION_DESIGN_AUDIT_TIMEOUT_SECONDS,
    APPLICATION_DESIGN_PATH,
    APPLICATION_DESIGN_PREVIEW_RELATIVE,
    APPLICATION_DESIGN_RELEASE_ACCEPTED,
    APPLICATION_DESIGN_RELEASE_CLAIM,
    APPLICATION_FIXED_CALL_CLAIM,
    APPLICATION_INDEX,
    APPLICATION_KIT_COMPONENTS,
    APPLICATION_PLACEHOLDER,
    APPLICATION_PREVIEW_SCAFFOLD,
    APPLICATION_SCAFFOLD_PATH,
    APPLICATION_SOURCE_CLAIM,
    APPLICATION_SOURCE_PATH,
    APPLICATION_SOURCE_READ,
    APPLICATION_SOURCE_REQUIRE_CLAIM,
    AcceptApplicationWireframeInput,
    AcceptedApplicationWireframe,
    ApplicationBuildAcceptance,
    ApplicationBuilderResult,
    ApplicationBuilderTask,
    ApplicationSourceEdit,
    ApplicationWireframeResult,
    BuildUfoApplicationInput,
    DesignUfoApplicationInput,
    EditApplicationSourceInput,
    ReadApplicationSourceInput,
    WriteApplicationDesignInput,
    WriteApplicationSourceInput,
    _validate_application_design,
    _validate_application_source,
    accept_application_wireframe,
    application_design_acceptance_relative,
    application_design_evidence_relative,
    build_ufo_application,
    design_ufo_application,
    edit_application_source,
    enforce_application_builder_phase,
    enforce_application_creation_route,
    is_application_creation_request,
    limit_application_builder_repair_reads,
    read_application_source,
    require_application_builder_qa,
    write_application_design,
    write_application_source,
)
from ufo_ext_sites.delegation import BuildWebsiteInput, _build_website
from ufo_ext_sites.objects import (
    CONVERSATION_DIGEST_HEX,
    SITE_KIND,
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
    DEPLOY_LOG,
    ENUMERATE_PROG,
    LOG_CLEAR_PROG,
    LOG_TAIL_TIMEOUT_SECONDS,
    PORT_STOP_PROG,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    PUBLISH_LOG,
    READINESS_TIMEOUT_SECONDS,
    SITES_TOOLS,
    TOOL_OUTPUT_DIR,
    DeployUfoApplicationInput,
    DeployWebsiteInput,
    PublishWebsiteInput,
    QaUfoApplicationInput,
    SetHomepageInput,
    StartServerInput,
    WebsiteInput,
    _audit_builder_application,
    _redeploy_homepage,
    _require_current_application_qa,
    deploy_ufo_application,
    deploy_website,
    publish_website,
    qa_ufo_application,
    set_homepage,
    start_server,
    website,
)

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.harness.sandbox.session import (
    SANDBOX_MODULE_BOOTSTRAP,
    SANDBOX_PYTHON_FLAG,
    ExecResult,
    SandboxHandle,
)
from ufo.host.ext.loader import skill_registry
from ufo.host.tools.builtins import BUILTIN_TOOLS, LoadSkillInput
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.compaction import Compaction
from ufo.runtime.engine import TurnEngine
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.hub import InProcessHub
from ufo.runtime.media.previews import StoredPreview
from ufo.runtime.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.runtime.object_scope import ObjectActionTarget
from ufo.runtime.prompts.render import rendered_prompt
from ufo.runtime.skills.runtime import install_skill
from ufo.runtime.subagents import subagent_system_prompt
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolRegistry
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import ActivitySummarizer
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import INTENT_ADMISSION, MEMBER_ADMISSION, Agent, ToolIntent, Turn, Usage
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.manifest import Deny, HookContext, PreToolUse
from ufo.sdk.objects import AGENT_KIND
from ufo.sdk.sandbox import WORKSPACE_DIR
from ufo.sdk.tools import ObjectBinding, TextContent, ToolResult

TOOL_NARRATION = "building the site"
RUNTIME_ROOT = "/home/user/.ufo/runs/test"
HOUSE_STYLE = "ufo-style"
HOUSE_STYLE_TOKENS = "references/tokens.css"
PLAYWRIGHT_GUIDANCE = "shared/12-playwright-interactive.md"
APPLICATION_QA_GUIDANCE = "shared/13-ufo-application-qa.md"
APPLICATION_DESIGN = """<svg viewBox="0 0 305 844" width="305" height="844">
<g data-app-region="queue"><g data-kit-component="Card"><rect width="183" height="844" /></g></g>
<g data-app-region="detail"><rect x="183" width="122" height="844" /></g>
</svg>"""
AUDIT_DESIGN_REGIONS = (
    {
        "name": "queue",
        "left": 0.0,
        "top": 0.0,
        "width": 0.6,
        "height": 1.0,
        "aboveFold": True,
    },
    {
        "name": "detail",
        "left": 0.6,
        "top": 0.0,
        "width": 0.4,
        "height": 1.0,
        "aboveFold": True,
    },
)
JS_CELL = re.compile(r"```javascript\n(.*?)```", re.S)


@dataclass
class _SkillSandbox:
    written: dict[str, bytes]

    async def load_skills(self, payload: dict[str, object]) -> dict[str, str]:
        user = payload["user"]
        assert isinstance(user, dict)
        roots = {}
        for name, wire in user.items():
            assert isinstance(name, str) and isinstance(wire, dict)
            files = wire["files"]
            assert isinstance(files, dict)
            root = f"$UFO_HOME/skills/{name}"
            for path, content in files.items():
                assert isinstance(path, str) and isinstance(content, str)
                self.written[f"{root}/{path}"] = urlsafe_b64decode(content)
            roots[name] = root
        return roots


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
    scripted_shells: dict[str, ExecResult] = field(default_factory=dict)
    scripted_programs: dict[str, ExecResult] = field(default_factory=dict)
    scripted_paths: dict[str, ExecResult] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    tasks: list[tuple[str, str, bool, int | None]] = field(default_factory=list)
    budgets: dict[str, int | None] = field(default_factory=dict)
    programs: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    shells: list[tuple[str, tuple[str, ...], int | None]] = field(default_factory=list)
    writes: dict[str, bytes] = field(default_factory=dict)
    runtime_writes: list[str] = field(default_factory=list)
    workspace_writes: list[str] = field(default_factory=list)
    workspace_write_error: OSError | None = None
    require_application_source_root: bool = False
    track_design_claim: bool = False
    claimed_paths: set[str] = field(default_factory=set)
    fixed_call_claims: dict[str, str] = field(default_factory=dict)
    delegation_claim_fault: BaseException | None = None
    design_audit_barrier: asyncio.Barrier | None = None
    program_errors: dict[str, BaseException] = field(default_factory=dict)
    claim: ExecResult = field(default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0))
    shell: ExecResult = field(default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0))
    design_audit: ExecResult = field(
        default_factory=lambda: ExecResult(
            stdout=json.dumps(AUDIT_DESIGN_REGIONS), stderr="", exit_code=0
        )
    )
    design_preview: bytes = b"\x89PNG application wireframe"
    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="sites-test")
    )

    @property
    def conversation_id(self) -> UUID:
        return self.handle.conversation_id

    @property
    def design_claimed(self) -> bool:
        return bool(self.claimed_paths or self.fixed_call_claims)

    async def bash(self, command: str, timeout_s: int | None = None) -> ExecResult:
        self.commands.append(command)
        self.budgets[command] = timeout_s
        for needle, result in self.scripted.items():
            if needle in command:
                return result
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def bash_task(
        self, command: str, base: str, *, detach: bool, timeout_s: int | None = None
    ) -> ExecResult:
        self.tasks.append((command, base, detach, timeout_s))
        for needle, result in self.scripted.items():
            if needle in command:
                return result
        return ExecResult(stdout="123\n", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        self.programs.append((program, args))
        if self.require_application_source_root and program == APPLICATION_SOURCE_READ:
            if args[1] != WORKSPACE_DIR:
                return ExecResult("", "application source escaped the workspace", 1)
        if program in self.program_errors:
            raise self.program_errors[program]
        if self.track_design_claim and program == APPLICATION_FIXED_CALL_CLAIM:
            path, _root, key = args
            existing = self.fixed_call_claims.get(path)
            if existing is None:
                self.fixed_call_claims[path] = key
                result = ExecResult("", "", 0)
            else:
                result = ExecResult("", "", 18 if existing == key else 17)
            if self.delegation_claim_fault is not None:
                error = self.delegation_claim_fault
                self.delegation_claim_fault = None
                raise error
            return result
        if self.track_design_claim and program == APPLICATION_SOURCE_CLAIM:
            if args[0] in self.claimed_paths:
                return ExecResult("", "", 17)
            self.claimed_paths.add(args[0])
            return ExecResult("", "", 0)
        if program == APPLICATION_DESIGN_ACCEPT:
            (
                source_path,
                accepted_path,
                evidence_source_path,
                evidence_path,
                claim_path,
                _root,
                identity,
                max_chars,
                max_evidence_chars,
            ) = args
            if self.track_design_claim:
                existing = self.fixed_call_claims.get(claim_path)
                if existing is None:
                    self.fixed_call_claims[claim_path] = identity
                elif existing != identity:
                    return ExecResult("", "", 17)
            content = self.writes[source_path]
            evidence = self.writes[evidence_source_path]
            if len(content) > int(max_chars):
                return ExecResult("", "application design is too large", 1)
            if len(evidence) > int(max_evidence_chars):
                return ExecResult("", "application design evidence is too large", 1)
            if (accepted_path in self.writes and self.writes[accepted_path] != content) or (
                evidence_path in self.writes and self.writes[evidence_path] != evidence
            ):
                return ExecResult("", "application design pair differs", 1)
            self.writes.setdefault(accepted_path, content)
            self.writes.setdefault(evidence_path, evidence)
            return ExecResult("", "", 0)
        if program == APPLICATION_DESIGN_RELEASE_ACCEPTED:
            (
                accepted_path,
                evidence_path,
                digest,
                evidence_digest,
                max_chars,
                max_evidence_chars,
                _root,
                claim_path,
                identity,
            ) = args
            content = self.writes.get(accepted_path)
            evidence = self.writes.get(evidence_path)
            if (
                content is None
                or len(content) > int(max_chars)
                or sha256(content).hexdigest() != digest
                or evidence is None
                or len(evidence) > int(max_evidence_chars)
                or sha256(evidence).hexdigest() != evidence_digest
                or self.fixed_call_claims.get(claim_path) != identity
            ):
                return ExecResult("", "accepted application design is not owned", 1)
            del self.writes[accepted_path]
            del self.writes[evidence_path]
            del self.fixed_call_claims[claim_path]
            return ExecResult("", "", 0)
        if program == APPLICATION_DESIGN_RELEASE_CLAIM:
            claim_path, _root, identity = args
            existing = self.fixed_call_claims.get(claim_path)
            if existing is None:
                return ExecResult("", "", 0)
            if existing != identity:
                return ExecResult("", "application design claim is not owned", 1)
            del self.fixed_call_claims[claim_path]
            return ExecResult("", "", 0)
        for needle, result in self.scripted_paths.items():
            if args and needle in args[0]:
                return result
        for needle, result in self.scripted_programs.items():
            if needle in program:
                return result
        readable = (APPLICATION_SOURCE_READ, sites_tools.APPLICATION_AUDIT_REPORT_READ)
        if program in readable and args and args[0] in self.writes:
            return ExecResult(self.writes[args[0]].decode(), "", 0)
        return self.claim

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        self.shells.append((script, args, timeout_s))
        if "--design" in script:
            if self.design_audit_barrier is not None:
                await self.design_audit_barrier.wait()
            self.writes[args[2]] = self.design_preview
            return self.design_audit
        for needle, result in self.scripted_shells.items():
            if needle in script:
                return result
        return self.shell

    async def write_file(self, path: str, content: bytes) -> None:
        if self.workspace_write_error is not None:
            raise self.workspace_write_error
        self.workspace_writes.append(path)
        self.writes[path] = content

    async def runtime_path(self, relative: str) -> str:
        return f"{RUNTIME_ROOT}/{relative}"

    async def runtime_display_path(self, relative: str) -> str:
        return f"$UFO_HOME/runs/test/{relative}"

    async def write_runtime_file(self, relative: str, content: bytes) -> None:
        path = await self.runtime_path(relative)
        self.runtime_writes.append(path)
        self.writes[path] = content

    async def write_runtime_path(self, path: str, content: bytes) -> None:
        self.runtime_writes.append(path)
        self.writes[path] = content

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield self.writes.get(path, b"")

        return bytes_of()


@dataclass
class JournalSandbox(FakeSandbox):
    """The sandbox with `ufo run --task`'s journal in it: a base that already holds a run is
    reattached to and launches nothing, and a cleared base launches. `launches` is therefore the
    servers that really started, which the recorded `tasks` alone cannot tell apart."""

    journaled: set[str] = field(default_factory=set)
    launches: list[str] = field(default_factory=list)

    async def bash_task(
        self, command: str, base: str, *, detach: bool, timeout_s: int | None = None
    ) -> ExecResult:
        result = await super().bash_task(command, base, detach=detach, timeout_s=timeout_s)
        if detach and base not in self.journaled:
            self.journaled.add(base)
            self.launches.append(base)
        return result

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if script == sites_tools.SERVER_TASK_RESET:
            self.journaled.discard(args[0])
        return await super().sh(script, *args, timeout_s=timeout_s)


@dataclass
class FakeHookStore:
    values: dict[str, object] = field(default_factory=dict)

    async def get(self, key: str) -> object | None:
        return self.values.get(key)

    async def put(self, key: str, value: object) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


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


def _application_context(sandbox: FakeSandbox, tmp_path: Path) -> ToolContext:
    ctx = _context(sandbox, tmp_path)
    return replace(
        ctx,
        idempotency_key=f"{ctx.turn.id}/build_ufo_application/call-1",
    )


def _application_design_context(ctx: ToolContext) -> ToolContext:
    return replace(
        ctx,
        idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-1",
    )


def _seed_application_design(
    sandbox: FakeSandbox, scaffold_path: str = "/workspace/application"
) -> None:
    sandbox.writes[f"{scaffold_path}/application-design.svg"] = APPLICATION_DESIGN.encode()


def _seed_accepted_application_design(sandbox: FakeSandbox, turn_id: UUID) -> None:
    design = APPLICATION_DESIGN.encode()
    accepted = (
        f"{RUNTIME_ROOT}/{application_design_acceptance_relative(APPLICATION_DESIGN_PATH, turn_id)}"
    )
    evidence = (
        f"{RUNTIME_ROOT}/{application_design_evidence_relative(APPLICATION_DESIGN_PATH, turn_id)}"
    )
    sandbox.writes[accepted] = design
    sandbox.writes[evidence] = (
        AcceptedApplicationDesignEvidence(
            design_sha256=sha256(design).hexdigest(),
            kit_components=("Card",),
            regions=tuple(
                ApplicationAuditRegion.model_validate(region) for region in AUDIT_DESIGN_REGIONS
            ),
        )
        .model_dump_json(by_alias=True)
        .encode()
    )


def test_manifest_declares_the_tools_the_profile_and_the_section() -> None:
    manifest = sites_manifest.manifest()
    assert {tool.name for tool in manifest.tools} == {
        "website",
        "start_server",
        "deploy_website",
        APPLICATION_BUILDER_DEPLOY_TOOL,
        APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL,
        "publish_website",
        "set_homepage",
        "build_website",
        APPLICATION_BUILDER_DELEGATION_TOOL,
        APPLICATION_BUILDER_DESIGN_TOOL,
        APPLICATION_BUILDER_EDIT_TOOL,
        APPLICATION_BUILDER_READ_TOOL,
        APPLICATION_BUILDER_QA_TOOL,
        APPLICATION_BUILDER_WRITE_TOOL,
        APPLICATION_BUILDER_WIREFRAME_TOOL,
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
    assert "action:site:deploy_website" in profile.tool_names
    assert "action:agent:set_homepage" in profile.tool_names
    assert {"website", "start_server"} <= set(profile.tool_names)
    assert "action:site:publish_website" not in profile.tool_names
    assert "publish_website" not in profile.tool_names
    assert "this conversation's sandbox" in build.description
    assert [(hook.event, hook.tools) for hook in manifest.hooks] == [
        ("pre_tool_use", ()),
        (
            "pre_tool_use",
            (
                "list_external_tools",
                "describe_external_tools",
                "search_connector_tools",
                "call_external_tool",
                APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL,
                APPLICATION_BUILDER_DESIGN_TOOL,
                APPLICATION_BUILDER_READ_TOOL,
                APPLICATION_BUILDER_EDIT_TOOL,
                APPLICATION_BUILDER_WRITE_TOOL,
                APPLICATION_BUILDER_QA_TOOL,
                APPLICATION_BUILDER_DEPLOY_TOOL,
            ),
        ),
        (
            "pre_tool_use",
            (APPLICATION_BUILDER_READ_TOOL, APPLICATION_BUILDER_EDIT_TOOL),
        ),
        ("pre_tool_use", (APPLICATION_BUILDER_DEPLOY_TOOL,)),
    ]
    (section,) = manifest.prompt_sections
    assert section.name == "sites" and "<sites>" in section.body


def test_application_creation_route_recognizes_apps_not_sites() -> None:
    for text in (
        "lets build an app that displays the current time across pacific, eastern, and utc time.",
        "Build me a new app.",
        "Set up an application for invoice intake.",
        "Create another support app.",
    ):
        assert is_application_creation_request(text)
    for text in (
        "Build a website that displays the current time.",
        "Build a web app with a persistent backend.",
        "Change the application homepage.",
        "Show me the apps in this workspace.",
        "Build an internal project board.",
        "Build a full-stack inventory app with authentication and a persistent database.",
        "Set up Slack in the wiki app.",
        "Set up this app.",
        "Show me the new apps in this workspace.",
        "Fix the bug in the new app I made yesterday.",
        "Add a chart to my new app.",
        "Rename the new application.",
        "Build a Slack app.",
        "Build a mobile app.",
        "Build a desktop application.",
    ):
        assert not is_application_creation_request(text)


async def test_application_creation_route_requires_its_skill(tmp_path: Path) -> None:
    base = _context(FakeSandbox(), tmp_path)
    turn = base.turn.model_copy(
        update={
            "inbound": "Build an app that displays three time zones.",
            "speaker_member_id": uuid4(),
            "admission_source": MEMBER_ADMISSION,
        }
    )
    store = FakeHookStore()
    ext = cast(ExtensionContext, FakeHookExt(store))
    agent = base.agent.model_copy(update={"is_main": True})

    def hook(payload: PreToolUse) -> HookContext:
        return HookContext(ext=ext, payload=payload, turn=turn, agent=agent)

    refused = await enforce_application_creation_route(
        hook(PreToolUse(tool_name="write", tool_input=BuildUfoApplicationInput()))
    )
    assert isinstance(refused, Deny)
    assert refused.reason == APPLICATION_CREATION_ROUTE_REASON
    wrong_skill = LoadSkillInput(name="website-building")
    wrong = await enforce_application_creation_route(
        hook(PreToolUse(tool_name="load_skill", tool_input=wrong_skill))
    )
    assert isinstance(wrong, Deny)
    assert wrong.reason == APPLICATION_CREATION_ROUTE_REASON
    load = LoadSkillInput(name="create-application")
    assert (
        await enforce_application_creation_route(
            hook(PreToolUse(tool_name="load_skill", tool_input=load))
        )
        is None
    )
    assert store.values[APPLICATION_CREATION_ROUTE_KEY.format(turn_id=turn.id)] is True
    assert (
        await enforce_application_creation_route(
            hook(PreToolUse(tool_name="load_skill", tool_input=wrong_skill))
        )
        is None
    )
    assert (
        await enforce_application_creation_route(
            hook(PreToolUse(tool_name="write", tool_input=BuildUfoApplicationInput()))
        )
        is None
    )


async def test_application_creation_route_leaves_a_prepared_intent_alone(tmp_path: Path) -> None:
    base = _context(FakeSandbox(), tmp_path)
    turn = base.turn.model_copy(
        update={
            "inbound": ToolIntent(
                tool="object_apply",
                input={
                    "kind": "agent",
                    "name": "finance-app",
                    "spec": {"prompt": "Set up an application that files new invoices."},
                },
            ).model_dump_json(),
            "speaker_member_id": uuid4(),
            "admission_source": INTENT_ADMISSION,
        }
    )
    store = FakeHookStore()
    ext = cast(ExtensionContext, FakeHookExt(store))
    agent = base.agent.model_copy(update={"is_main": True})
    assert is_application_creation_request(turn.inbound)
    payload = PreToolUse(tool_name="object_apply", tool_input=BuildUfoApplicationInput())
    assert (
        await enforce_application_creation_route(
            HookContext(ext=ext, payload=payload, turn=turn, agent=agent)
        )
        is None
    )
    assert store.values == {}


def test_hosting_writes_declare_side_effecting() -> None:
    tools = {tool.name: tool for tool in SITES_TOOLS}
    assert tools["deploy_website"].side_effecting is True
    assert tools["publish_website"].side_effecting is True
    assert tools["set_homepage"].side_effecting is True
    assert tools["website"].side_effecting is False
    assert tools["start_server"].side_effecting is False


def test_site_actions_bind_to_their_objects_and_the_runtime_stays_global() -> None:
    tools = {tool.name: tool for tool in sites_manifest.manifest().tools}
    collection = ObjectBinding(kind=SITE_KIND, binding="collection")
    for name in (
        "deploy_website",
        "publish_website",
        "build_website",
        APPLICATION_BUILDER_DELEGATION_TOOL,
        APPLICATION_BUILDER_WIREFRAME_TOOL,
    ):
        assert tools[name].bound == collection
        assert tools[name].canonical_id == f"action:site:{name}"
    assert tools["set_homepage"].bound == ObjectBinding(kind=AGENT_KIND, binding="instance")
    assert tools["set_homepage"].canonical_id == "action:agent:set_homepage"
    for name in (
        "website",
        "start_server",
        APPLICATION_BUILDER_QA_TOOL,
        APPLICATION_BUILDER_DEPLOY_TOOL,
        APPLICATION_BUILDER_DESIGN_TOOL,
        APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL,
        APPLICATION_BUILDER_READ_TOOL,
        APPLICATION_BUILDER_EDIT_TOOL,
        APPLICATION_BUILDER_WRITE_TOOL,
    ):
        assert tools[name].bound is None


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            DeployWebsiteInput,
            {"project_path": "/workspace/dist", "site_name": "s", "entry_point": "index.html"},
        ),
        (
            PublishWebsiteInput,
            {"project_path": "/workspace/app", "dist_path": "/workspace/app/dist", "app_name": "a"},
        ),
        (SetHomepageInput, {"site": "s-0011223344556677"}),
        (BuildWebsiteInput, {"objective": "build a page"}),
        (BuildUfoApplicationInput, {}),
        (
            DesignUfoApplicationInput,
            {
                "application_name": "support-desk",
                "application_prompt": "You handle support requests.",
            },
        ),
    ],
)
def test_site_action_inputs_refuse_an_extra_key(
    model: type[BaseModel], payload: dict[str, object]
) -> None:
    model.model_validate(payload)
    with pytest.raises(ValidationError, match="unexpected_key"):
        model.model_validate({**payload, "unexpected_key": "x"})


async def _seeded_workspace() -> UUID:
    workspace_id = uuid4()
    now = datetime(2026, 8, 26, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
    return workspace_id


def _keyed_server_task(sandbox: FakeSandbox, key: str, port: int, log: str) -> None:
    identity = f"{key}:{port}:{log}"
    assert [task_base for _command, task_base, detach, _timeout in sandbox.tasks if detach] == [
        f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/server-tasks/"
        f"{sha256(identity.encode()).hexdigest()[:16]}"
    ]


async def test_deploy_website_keys_its_server_task_on_the_call(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "sites-test-secret")
    workspace_id = await _seeded_workspace()
    listing = {"index.html": {"size": 17, "sha256": "ab" * 32}}
    sandbox = FakeSandbox(
        scripted_programs={ENUMERATE_PROG: ExecResult(json.dumps(listing), "", 0)}
    )
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={"workspace_id": workspace_id, "on_behalf_of_member_id": uuid4()}
        ),
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
        idempotency_key=f"{base.turn.id}/deploy_website/call-1",
        public_base_url="https://ufo.example.test",
        ext=context_for("sites", frozenset()),
    )

    with ws(workspace_id):
        result = await deploy_website(
            ctx,
            DeployWebsiteInput(
                project_path="/workspace/dist", site_name="marketing", entry_point="index.html"
            ),
        )

    payload = json.loads(result.content[0].text)
    assert payload["site_name"] == "marketing"
    assert ctx.idempotency_key is not None
    log = f"{RUNTIME_ROOT}/{DEPLOY_LOG.format(port=payload['port'])}"
    _keyed_server_task(sandbox, ctx.idempotency_key, payload["port"], log)


async def test_publish_website_keys_its_server_task_on_the_call(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "sites-test-secret")
    workspace_id = await _seeded_workspace()
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={"workspace_id": workspace_id, "on_behalf_of_member_id": uuid4()}
        ),
        idempotency_key=f"{base.turn.id}/publish_website/call-1",
        public_base_url="https://ufo.example.test",
        ext=context_for("sites", frozenset()),
    )

    with ws(workspace_id):
        result = await publish_website(
            ctx,
            PublishWebsiteInput(
                project_path="/workspace/app", dist_path="/workspace/app/dist", app_name="crm"
            ),
        )

    payload = json.loads(result.content[0].text)
    assert payload["site_name"] == "crm"
    assert ctx.idempotency_key is not None
    log = f"{RUNTIME_ROOT}/{PUBLISH_LOG.format(port=payload['port'])}"
    _keyed_server_task(sandbox, ctx.idempotency_key, payload["port"], log)


async def test_set_homepage_repeats_cleanly_under_its_call_key(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", "sites-test-secret")
    workspace_id = uuid4()
    member_id = uuid4()
    now = datetime(2026, 8, 26, tzinfo=UTC)
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"workspace_id": workspace_id}),
        speaker_member_id=member_id,
        idempotency_key=f"{base.turn.id}/action:agent:set_homepage/call-1",
        public_base_url="https://ufo.example.test",
        ext=context_for("sites", frozenset()),
        target=ObjectActionTarget(
            kind=AGENT_KIND, name="tasks", agent=None, generation=None, expected_generation=None
        ),
    )
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
                id=ctx.turn.agent_id,
                workspace_id=workspace_id,
                name="tasks",
                prompt="Track the team's work.",
                model="claude-opus-4-8",
                visibility="workspace",
                is_main=False,
                owner_member_id=member_id,
                created_at=now,
                updated_at=now,
            )
        )

    with ws(workspace_id):
        site = await HostedSites(workspace_id, workspace_tx).register(
            sandbox.conversation_id,
            "marketing",
            41000,
            member_id,
            None,
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        args = SetHomepageInput(site=site_object_name(site.conversation_id, site.name))
        first = json.loads((await set_homepage(ctx, args)).content[0].text)
        second = json.loads((await set_homepage(ctx, args)).content[0].text)

    assert ctx.idempotency_key is not None
    assert first == second
    assert first["homepage_agent"] == str(ctx.turn.agent_id)
    async with workspace_tx() as connection:
        bound = (
            (
                await connection.execute(
                    sa.select(hosted_site.c.homepage_agent_id).where(
                        hosted_site.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert bound == [ctx.turn.agent_id]


def test_application_audit_accepts_measured_interactive_facts() -> None:
    report = ApplicationAuditReport.model_validate(
        {
            "url": "http://localhost:3000/preview.html",
            "floor": 4.5,
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 8,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Acme renewal Aug 27 #2042",
                    "regions": AUDIT_DESIGN_REGIONS,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
    without_design = report.model_copy(update={"design_regions": ()})
    assert {issue.code for issue in audit_application(without_design, contract).issues} == {
        "design"
    }


def test_application_audit_uses_the_quiet_floor_only_for_exact_kit_slots() -> None:
    def report(text: dict[str, object]) -> ApplicationAuditReport:
        return ApplicationAuditReport.model_validate(
            {
                "designRegions": AUDIT_DESIGN_REGIONS,
                "views": [
                    {
                        "scheme": scheme,
                        "width": width,
                        "textChecked": 1,
                        "text": [text],
                        "documentWidth": width,
                        "clipped": [],
                        "overlaps": [],
                        "console": [],
                        "aboveFoldText": "Open issues 42",
                        "regions": AUDIT_DESIGN_REGIONS,
                    }
                    for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
                    "console": [],
                },
            }
        )

    measured = {
        "text": "Open issues",
        "selector": "span.text-label",
        "px": 13,
        "weight": 500,
        "ratio": KIT_QUIET_TEXT_MIN,
    }
    kit = report({**measured, "slot": "stat-label"})
    authored = report(measured)
    unreadable = report({**measured, "slot": "stat-label", "ratio": 2.0})

    assert "contrast" not in {issue.code for issue in audit_application(kit).issues}
    assert "contrast" in {issue.code for issue in audit_application(authored).issues}
    assert "contrast" in {issue.code for issue in audit_application(unreadable).issues}
    with pytest.raises(ValidationError):
        report({**measured, "slot": "author-quiet"})


def test_application_design_fidelity_compares_only_the_separating_axis() -> None:
    design_regions = (
        {
            "name": "overdue-queue",
            "left": 0.025,
            "top": 0.1,
            "width": 0.95,
            "height": 0.2,
            "aboveFold": True,
        },
        {
            "name": "watch-list",
            "left": 0.6,
            "top": 0.6,
            "width": 0.3,
            "height": 0.3,
            "aboveFold": True,
        },
    )
    app_regions = (
        {
            "name": "overdue-queue",
            "left": 0.01,
            "top": 0.05,
            "width": 0.98,
            "height": 0.2,
            "aboveFold": True,
        },
        {
            "name": "watch-list",
            "left": 0.35,
            "top": 0.6,
            "width": 0.3,
            "height": 0.3,
            "aboveFold": True,
        },
    )
    report = ApplicationAuditReport.model_validate(
        {
            "designRegions": design_regions,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 1,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": app_regions,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
                for scheme in ("light", "dark")
            ],
            "interaction": {"controls": [], "successes": [], "console": []},
        }
    )

    fidelity = application_design_fidelity(report)

    assert fidelity.failures == ()
    assert fidelity.passed == fidelity.total


def test_application_design_fidelity_preserves_design_fold_placement() -> None:
    design_regions = (
        {
            "name": "summary",
            "left": 0.05,
            "top": 0.05,
            "width": 0.9,
            "height": 0.4,
            "aboveFold": True,
        },
        {
            "name": "details",
            "left": 0.05,
            "top": 0.55,
            "width": 0.9,
            "height": 0.4,
            "aboveFold": False,
        },
    )

    def report(summary_above_fold: bool) -> ApplicationAuditReport:
        app_regions = (
            {**design_regions[0], "aboveFold": summary_above_fold},
            design_regions[1],
        )
        return ApplicationAuditReport.model_validate(
            {
                "designRegions": design_regions,
                "views": [
                    {
                        "scheme": scheme,
                        "width": width,
                        "textChecked": 1,
                        "text": [],
                        "documentWidth": width,
                        "clipped": [],
                        "overlaps": [],
                        "console": [],
                        "aboveFoldText": "Summary",
                        "regions": app_regions,
                    }
                    for width in (DESKTOP_WIDTH, NARROW_WIDTH)
                    for scheme in ("light", "dark")
                ],
                "interaction": {"controls": [], "successes": [], "console": []},
            }
        )

    accepted = application_design_fidelity(report(summary_above_fold=True))
    rejected = application_design_fidelity(report(summary_above_fold=False))

    assert accepted.failures == ()
    assert accepted.passed == accepted.total
    assert rejected.failures == (
        "light desktop lacks visible summary",
        "dark desktop lacks visible summary",
    )


def test_application_design_fidelity_rejects_overlapping_regions() -> None:
    regions = (
        {
            "name": "queue",
            "left": 0.0,
            "top": 0.0,
            "width": 1.0,
            "height": 1.0,
            "aboveFold": True,
        },
        {
            "name": "detail",
            "left": 0.0,
            "top": 0.0,
            "width": 1.0,
            "height": 1.0,
            "aboveFold": True,
        },
    )
    report = ApplicationAuditReport.model_validate(
        {
            "designRegions": regions,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 1,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": regions,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
                for scheme in ("light", "dark")
            ],
            "interaction": {"controls": [], "successes": [], "console": []},
        }
    )

    fidelity = application_design_fidelity(report)

    assert fidelity == ApplicationDesignFidelity(
        passed=0,
        total=1,
        failures=("design regions queue and detail overlap",),
    )
    assert {issue.code for issue in audit_application(report).issues} == {
        "controls",
        "design",
        "interaction",
    }


def test_application_design_fidelity_rejects_exact_text_token_regions() -> None:
    report = ApplicationAuditReport.model_validate(
        {
            "designRegions": (
                {
                    "name": "queue",
                    "left": 0,
                    "top": 0,
                    "width": 0.002,
                    "height": 0.021,
                },
                {
                    "name": "detail",
                    "left": 0.03,
                    "top": 0,
                    "width": 0.001,
                    "height": 0.021,
                },
            ),
            "views": [],
            "interaction": {"controls": [], "successes": [], "console": []},
        }
    )

    assert application_design_fidelity(report) == ApplicationDesignFidelity(
        passed=0,
        total=1,
        failures=("design region queue is too small",),
    )


@pytest.mark.parametrize(
    ("width", "height", "accepted"),
    (
        (APPLICATION_REGION_MIN_WIDTH - 0.001, 0.4, False),
        (0.4, APPLICATION_REGION_MIN_HEIGHT - 0.001, False),
        (0.1, APPLICATION_REGION_MIN_AREA / 0.1 - 0.001, False),
        (APPLICATION_REGION_MIN_WIDTH, 0.4, True),
        (0.4, APPLICATION_REGION_MIN_HEIGHT, True),
        (0.1, APPLICATION_REGION_MIN_AREA / 0.1, True),
    ),
)
def test_application_design_fidelity_region_size_boundaries(
    width: float,
    height: float,
    accepted: bool,
) -> None:
    design_regions = (
        {
            "name": "queue",
            "left": 0,
            "top": 0,
            "width": width,
            "height": height,
        },
        {
            "name": "detail",
            "left": 0.6,
            "top": 0.6,
            "width": 0.4,
            "height": 0.4,
        },
    )
    report = ApplicationAuditReport.model_validate(
        {
            "designRegions": design_regions,
            "views": [
                {
                    "scheme": scheme,
                    "width": 1440,
                    "textChecked": 1,
                    "text": [],
                    "documentWidth": 1440,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": design_regions,
                }
                for scheme in ("light", "dark")
            ],
            "interaction": {"controls": [], "successes": [], "console": []},
        }
    )

    fidelity = application_design_fidelity(report)

    assert ("design region queue is too small" not in fidelity.failures) is accepted


def test_application_design_fidelity_does_not_size_dom_regions() -> None:
    application_regions = (
        {**AUDIT_DESIGN_REGIONS[0], "height": 0.025},
        {**AUDIT_DESIGN_REGIONS[1], "height": 0.025},
    )
    report = ApplicationAuditReport.model_validate(
        {
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": [
                {
                    "scheme": scheme,
                    "width": 1440,
                    "textChecked": 1,
                    "text": [],
                    "documentWidth": 1440,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": application_regions,
                }
                for scheme in ("light", "dark")
            ],
            "interaction": {"controls": [], "successes": [], "console": []},
        }
    )

    fidelity = application_design_fidelity(report)

    assert fidelity.failures == ()
    assert fidelity.passed == fidelity.total


def test_application_design_fidelity_sizes_regions_against_the_design_page() -> None:
    design_regions = (
        {
            "name": "queue",
            "left": 0.05,
            "top": 0.0,
            "width": 0.9,
            "height": 80 / APPLICATION_DESIGN_MAX_HEIGHT,
        },
        {"name": "detail", "left": 0.05, "top": 0.1, "width": 0.9, "height": 0.8},
    )
    report = ApplicationAuditReport.model_validate(
        {
            "designHeight": APPLICATION_DESIGN_MAX_HEIGHT,
            "designRegions": design_regions,
            "views": [
                {
                    "scheme": scheme,
                    "width": 1440,
                    "textChecked": 1,
                    "text": [],
                    "documentWidth": 1440,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": design_regions,
                }
                for scheme in ("light", "dark")
            ],
            "interaction": {"controls": [], "successes": [], "console": []},
        }
    )

    assert application_design_fidelity(report).failures == ()


def test_application_design_region_floors_hold_one_pixel_size_at_every_page_height() -> None:
    def band(page_height: int, pixels: int) -> ApplicationAuditRegion:
        return ApplicationAuditRegion(
            name="queue", left=0.05, top=0.0, width=0.9, height=pixels / page_height
        )

    assert application_design_region_size_failure((band(APPLICATION_DESIGN_FOLD, 80),)) is None
    assert (
        application_design_region_size_failure(
            (band(APPLICATION_DESIGN_MAX_HEIGHT, 80),), APPLICATION_DESIGN_MAX_HEIGHT
        )
        is None
    )
    assert (
        application_design_region_size_failure((band(APPLICATION_DESIGN_FOLD, 20),))
        == "design region queue is too small"
    )
    assert (
        application_design_region_size_failure(
            (band(APPLICATION_DESIGN_MAX_HEIGHT, 20),), APPLICATION_DESIGN_MAX_HEIGHT
        )
        == "design region queue is too small"
    )


def test_application_design_regions_do_not_cross_the_first_screen_boundary() -> None:
    page_height = 1050
    fold = APPLICATION_DESIGN_FOLD / page_height
    above = ApplicationAuditRegion(
        name="above", left=0, top=0, width=1, height=fold, aboveFold=True
    )
    below = ApplicationAuditRegion(
        name="below", left=0, top=fold, width=1, height=1 - fold, aboveFold=False
    )
    crossing = ApplicationAuditRegion(
        name="crossing", left=0, top=fold - 0.01, width=1, height=0.02, aboveFold=True
    )
    spanning = ApplicationAuditRegion(
        name="spanning", left=0, top=0.5, width=1, height=0.4, aboveFold=True
    )

    assert application_design_region_fold_failure((above, below), page_height) is None
    assert application_design_region_fold_failure((crossing,), page_height) == (
        "design region crossing crosses the first-screen boundary"
    )
    assert application_design_region_fold_failure((spanning,), page_height) == (
        "design region spanning crosses the first-screen boundary"
    )


def test_application_design_region_ending_on_the_fold_row_holds_at_every_page_height() -> None:
    def band(page_height: int, top_row: int, rows: int) -> ApplicationAuditRegion:
        return ApplicationAuditRegion(
            name="queue",
            left=0.0,
            top=top_row / page_height,
            width=1.0,
            height=rows / page_height,
        )

    allowed_rows = APPLICATION_DESIGN_FOLD + DESIGN_REGION_FOLD_SLOP + 1
    assert application_design_region_fold_failure((band(846, 32, 812),), 846) is None
    assert application_design_region_fold_failure((band(846, 32, 813),), 846) is None
    assert application_design_region_fold_failure((band(848, 32, 815),), 848) is None
    assert application_design_region_fold_failure((band(848, 32, 816),), 848) == (
        "design region queue crosses the first-screen boundary"
    )

    swept = 0
    rounded = 0
    for page_height in range(APPLICATION_DESIGN_FOLD + 1, APPLICATION_DESIGN_MAX_HEIGHT + 1):
        fold = APPLICATION_DESIGN_FOLD / page_height
        for top_row in range(0, APPLICATION_DESIGN_FOLD, 8):
            swept += 1
            ends_on_fold = band(page_height, top_row, APPLICATION_DESIGN_FOLD - top_row)
            if ends_on_fold.top + ends_on_fold.height <= fold:
                continue
            rounded += 1
            assert application_design_region_fold_failure((ends_on_fold,), page_height) is None
            painted = band(page_height, top_row, APPLICATION_DESIGN_FOLD + 1 - top_row)
            assert application_design_region_fold_failure((painted,), page_height) is None
            if page_height <= allowed_rows:
                continue
            allowance = band(page_height, top_row, allowed_rows - top_row)
            assert application_design_region_fold_failure((allowance,), page_height) is None
            crosses = band(page_height, top_row, allowed_rows + 1 - top_row)
            assert application_design_region_fold_failure((crosses,), page_height) == (
                "design region queue crosses the first-screen boundary"
            )

    assert rounded > swept // 10


def test_application_design_region_stroked_on_the_fold_row_holds() -> None:
    def rows(page_height: int, painted_rows: int) -> ApplicationAuditRegion:
        return ApplicationAuditRegion(
            name="queue",
            left=0.0,
            top=0.0,
            width=1.0,
            height=painted_rows / page_height,
            aboveFold=True,
        )

    for page_height in (848, 1050, APPLICATION_DESIGN_MAX_HEIGHT):
        stroked = rows(page_height, APPLICATION_DESIGN_FOLD + 1)
        assert application_design_region_fold_failure((stroked,), page_height) is None
        allowance = rows(page_height, APPLICATION_DESIGN_FOLD + DESIGN_REGION_FOLD_SLOP + 1)
        assert application_design_region_fold_failure((allowance,), page_height) is None
        past = rows(page_height, APPLICATION_DESIGN_FOLD + DESIGN_REGION_FOLD_SLOP + 2)
        assert application_design_region_fold_failure((past,), page_height) == (
            "design region queue crosses the first-screen boundary"
        )


def _side_by_side_bands(page_height: int) -> tuple[dict[str, object], ...]:
    """Two 60 px bands 100 px down one page, side by side in the 305 px lane."""
    return tuple(
        {
            "name": name,
            "left": left,
            "top": 100 / page_height,
            "width": 0.4,
            "height": 60 / page_height,
            "aboveFold": True,
        }
        for name, left in (("stats", 0.05), ("filters", 0.55))
    )


def _side_by_side_report(design_height: int, application_height: int) -> ApplicationAuditReport:
    return ApplicationAuditReport.model_validate(
        {
            "designHeight": design_height,
            "designRegions": _side_by_side_bands(design_height),
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 4,
                    "text": [],
                    "documentWidth": width,
                    "pageHeight": application_height,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Stats",
                    "regions": _side_by_side_bands(application_height),
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
                "states": [["Stats"]],
                "console": [],
            },
        }
    )


def test_application_design_keeps_side_by_side_bands_horizontal_on_a_tall_design_page() -> None:
    tall_page = 3_000
    stats, filters = (
        ApplicationAuditRegion.model_validate(region) for region in _side_by_side_bands(tall_page)
    )

    assert application_region_relation(stats, filters, tall_page) == ("horizontal", -1)
    report = _side_by_side_report(tall_page, 900)
    fidelity = application_design_fidelity(report)
    assert fidelity.failures == ()
    assert fidelity.passed == fidelity.total
    assert "design" not in {issue.code for issue in audit_application(report).issues}


def test_application_design_keeps_side_by_side_bands_horizontal_on_a_tall_application_page() -> (
    None
):
    tall_page = 3_000
    stats, filters = (
        ApplicationAuditRegion.model_validate(region) for region in _side_by_side_bands(tall_page)
    )

    assert application_region_relation(stats, filters, tall_page) == ("horizontal", -1)
    report = _side_by_side_report(APPLICATION_DESIGN_FOLD, tall_page)
    assert report.views[0].page_height == tall_page
    fidelity = application_design_fidelity(report)
    assert fidelity.failures == ()
    assert fidelity.passed == fidelity.total
    assert "design" not in {issue.code for issue in audit_application(report).issues}


def test_application_audit_reports_the_page_height_regions_divide_by() -> None:
    source = APPLICATION_AUDIT_SCRIPT.decode()

    assert "const pageHeight = Math.max(document.documentElement.scrollHeight, " in source
    assert (
        "    const top = Math.max(0, Math.min(1, (box.top + window.scrollY) / pageHeight));"
        in source
    )
    assert "    viewportHeight: window.innerHeight,\n    pageHeight,\n" in source


def test_application_audit_runs_views_in_parallel_in_declared_order() -> None:
    source = APPLICATION_AUDIT_SCRIPT.decode()

    assert "await Promise.all(VIEWS.map(async (view) => {" in source
    assert "views.push(" not in source
    assert "waitForTimeout(700)" not in source
    assert "waitForTimeout(300)" not in source
    assert "waitForTimeout(100)" not in source
    assert "waitForApplicationReady(indexFrame)" in source
    assert "beginApplicationObservation(frame)" in source
    assert "endApplicationObservation(frame, epoch, APPLICATION_INTERACTION_TIMEOUT_MS)" in source
    assert "waitForApplicationInteraction(frame, stepBefore)" in source
    assert "APPLICATION_INTERACTION_TIMEOUT_MS = 300" in source
    assert "if (!(error instanceof ApplicationLifecycleError)" in source
    assert "sameLifecycle(probe.before, probe.after)" in source
    assert "finally {\n        await context.close();" in source
    assert "if (browser) await browser.close();" in source
    assert "await closeApplicationServer(server, sockets);" in source


def test_application_audit_returns_one_bounded_diagnostic_batch() -> None:
    report = ApplicationAuditReport.model_validate(
        {
            "url": "http://localhost:3000/preview.html",
            "floor": 4.5,
            "designRegions": AUDIT_DESIGN_REGIONS,
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
                    "overlaps": [],
                    "console": ["pageerror: broken"],
                    "aboveFoldText": "Acme renewal",
                    "regions": AUDIT_DESIGN_REGIONS,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
                                    "text": "Needs review",
                                    "selector": "span.muted",
                                    "px": 14,
                                    "weight": 400,
                                    "ratio": 3.2,
                                    "colour": "rgb(120, 120, 120)",
                                    "background": "rgb(255, 255, 255)",
                                }
                            ]
                            if issue
                            else []
                        ),
                        "documentWidth": width,
                        "clipped": [],
                        "overlaps": [],
                        "console": [],
                        "aboveFoldText": "#2042",
                        "regions": AUDIT_DESIGN_REGIONS,
                    }
                    for width in (DESKTOP_WIDTH, NARROW_WIDTH)
                    for scheme in ("light", "dark")
                ],
                "designRegions": AUDIT_DESIGN_REGIONS,
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
    _seed_accepted_application_design(sandbox, ctx.turn.id)

    feedback = await _audit_builder_application(ctx, "/workspace/ufo-app")

    key = APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=ctx.turn.id)
    assert isinstance(feedback, ApplicationAuditFeedback)
    assert feedback.status == "repair_required"
    assert {issue.code for issue in feedback.issues} == {"contrast"}
    assert feedback.issues[0].message == (
        f'Fix text contrast: light {DESKTOP_WIDTH}px "Needs review" at span.muted '
        "rgb(120, 120, 120) on rgb(255, 255, 255) is 3.2:1; needs 4.5:1; "
        f'dark {DESKTOP_WIDTH}px "Needs review" at span.muted rgb(120, 120, 120) on '
        f"rgb(255, 255, 255) is 3.2:1; needs 4.5:1; light {NARROW_WIDTH}px "
        '"Needs review" '
        "at span.muted rgb(120, 120, 120) on rgb(255, 255, 255) is 3.2:1; needs 4.5:1; "
        f'dark {NARROW_WIDTH}px "Needs review" at span.muted rgb(120, 120, 120) on '
        "rgb(255, 255, 255) is 3.2:1; needs 4.5:1."
    )
    assert store.values[key] == 1
    sandbox.scripted_paths["/application-audit/"] = ExecResult(_report(False), "", 0)
    report = await _audit_builder_application(ctx, "/workspace/ufo-app")
    assert isinstance(report, ApplicationAuditReport)
    audits = [entry for entry in sandbox.shells if entry[0].startswith("node ")]
    assert len(audits) == 2
    script_path = f"{RUNTIME_ROOT}/tool-output/application-audit/{ctx.turn.id}.cjs"
    report_path = f"{RUNTIME_ROOT}/tool-output/application-audit/{ctx.turn.id}.json"
    assert audits[0] == (
        'node "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"',
        (
            script_path,
            "/workspace/ufo-app",
            report_path,
            f"{RUNTIME_ROOT}/tool-output/application-audit/{ctx.turn.id}-light.png",
            f"{RUNTIME_ROOT}/tool-output/application-audit/{ctx.turn.id}-dark.png",
            f"{RUNTIME_ROOT}/tool-output/application-audit/{ctx.turn.id}-interactive.html",
            f"{RUNTIME_ROOT}/tool-output/application-audit/{ctx.turn.id}-static.html",
            f"{RUNTIME_ROOT}/"
            f"{application_design_acceptance_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)}",
            f"{RUNTIME_ROOT}/"
            f"{application_design_evidence_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)}",
        ),
        sites_tools.APPLICATION_AUDIT_TIMEOUT_SECONDS,
    )
    assert sandbox.tasks == []
    assert all(program != PORT_STOP_PROG for program, _args in sandbox.programs)


async def test_application_builder_lifecycle_failure_keeps_audit_feedback_bytes(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox(scripted_shells={'node "$1"': ExecResult("", "", 3)})
    sandbox.scripted_paths[".lifecycle.json"] = ExecResult(
        json.dumps(
            {
                "code": "application_lifecycle",
                "reason": "application lifecycle did not become ready",
                "snapshot": {
                    "version": 1,
                    "generation": 1,
                    "epoch": 0,
                    "mounted": True,
                    "state": "active",
                    "revision": 4,
                    "blockingWork": 1,
                    "blocking": {
                        "startup": 0,
                        "observation": 0,
                        "unary": 0,
                        "stream": 0,
                        "timeout": 1,
                        "interval": 0,
                    },
                },
            }
        ),
        "",
        0,
    )
    store = FakeHookStore()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    _seed_accepted_application_design(sandbox, ctx.turn.id)

    feedback = await _audit_builder_application(ctx, "/workspace/ufo-app")

    assert isinstance(feedback, ApplicationAuditFeedback)
    assert feedback.model_dump_json() == (
        '{"status":"repair_required","attempt":1,"attempts_remaining":1,"issues":'
        '[{"code":"audit_run",'
        '"message":"Run the browser audit successfully: '
        'application lifecycle did not become ready",'
        '"terms":[]}]}'
    )


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


def test_application_delegation_claim_publishes_one_safe_complete_key(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "tool-output" / "application-builder" / "turn.delegated"
    containment_dir = Path(__file__).parents[3] / "core" / "src" / "ufo" / "harness"

    def claim(key: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (
                sys.executable,
                "-c",
                APPLICATION_FIXED_CALL_CLAIM,
                str(target),
                str(root),
                key,
            ),
            cwd=containment_dir,
            check=False,
            capture_output=True,
            text=True,
        )

    first = claim("turn-1/build_ufo_application/call-1")
    same = claim("turn-1/build_ufo_application/call-1")
    different = claim("turn-1/build_ufo_application/call-2")

    assert first.returncode == 0
    assert same.returncode == 18
    assert different.returncode == 17
    assert target.read_text() == "turn-1/build_ufo_application/call-1"
    assert not tuple(target.parent.glob(".ufo-staged-*"))

    target.unlink()
    outside = tmp_path / "outside"
    outside.write_text("outside")
    target.symlink_to(outside)

    linked = claim("turn-1/build_ufo_application/call-1")

    assert linked.returncode not in (0, 17, 18)
    assert outside.read_text() == "outside"


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

    sandbox = FakeSandbox(track_design_claim=True)
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        idempotency_key="turn-1/build_ufo_application/call-2",
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )
    assert set(BuildUfoApplicationInput.model_fields) == set()
    result = await build_ufo_application(
        ctx,
        BuildUfoApplicationInput(),
    )

    assert captured == {
        "profile": "profile:ufo_application_builder",
        "payload": {
            "objective": (
                f"Application instructions:\n{ctx.agent.prompt}\n\n"
                f"Current request:\n{ctx.turn.inbound}"
            ),
            "scaffold_path": "/workspace/ufo-app",
            "source_path": "/workspace/ufo-app/app.tsx",
            "phase": "build",
            "accepted_design_digest": "",
            "preload_skills": ("ufo-style",),
        },
        "dedup_key": "turn-1/build_ufo_application/call-2",
    }
    assert sandbox.programs[0] == (
        APPLICATION_FIXED_CALL_CLAIM,
        (
            f"{RUNTIME_ROOT}/tool-output/application-builder/{ctx.turn.id}.delegated",
            RUNTIME_ROOT,
            "turn-1/build_ufo_application/call-2",
        ),
    )
    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert spawns == 1
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

    sandbox = FakeSandbox(
        scripted_programs={APPLICATION_SOURCE_READ: ExecResult("", "not found", 1)}
    )
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    await build_ufo_application(ctx, BuildUfoApplicationInput())

    assert sandbox.writes == {
        "/workspace/ufo-app/index.html": APPLICATION_INDEX,
        "/workspace/ufo-app/app.tsx": APPLICATION_PLACEHOLDER,
        "/workspace/ufo-app/preview.html": APPLICATION_PREVIEW_SCAFFOLD,
    }


async def test_build_ufo_application_keeps_the_wireframe_after_the_prompt_changes(
    tmp_path: Path,
) -> None:
    prompt = "You now review and assign support requests."
    design = APPLICATION_DESIGN.encode()
    digest = sha256(design).hexdigest()
    captured: dict[str, object] = {}

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        captured["profile"] = profile
        captured["payload"] = payload
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="Stop after contract capture.",
            ),
        )

    store = FakeHookStore(
        values={
            "application-wireframe/support-desk": AcceptedApplicationWireframe(
                design_digest=digest,
                content=APPLICATION_DESIGN,
            ).model_dump(mode="json")
        }
    )
    sandbox = FakeSandbox(track_design_claim=True)
    ctx = replace(
        _application_context(sandbox, tmp_path),
        agent=Agent(prompt=prompt, model="claude-opus-4-8", name="support-desk"),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )

    await build_ufo_application(ctx, BuildUfoApplicationInput())

    assert sandbox.writes[APPLICATION_DESIGN_PATH] == design
    payload = cast(dict[str, object], captured["payload"])
    assert payload["phase"] == "build"
    assert payload["accepted_design_digest"] == digest


async def test_build_ufo_application_frees_the_wireframe_once_it_binds_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = sha256(APPLICATION_DESIGN.encode()).hexdigest()
    blocked = ApplicationBuilderResult(
        status="blocked",
        source_path=APPLICATION_SOURCE_PATH,
        browser_batches=0,
        blocker="The connector is unavailable.",
    )
    deployed = ApplicationBuilderResult(
        status="deployed",
        source_path=APPLICATION_SOURCE_PATH,
        site_name="support-desk-homepage",
        site_url="https://ufo.example.test/support-desk-homepage",
        browser_batches=2,
    )
    worker = blocked
    handed: list[str] = []

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        handed.append(str(payload["accepted_design_digest"]))
        return SpawnResult(turn_id=uuid4(), conversation_id=uuid4(), output=worker)

    async def _accept(
        _acceptance: ApplicationBuildAcceptance, result: ApplicationBuilderResult
    ) -> ApplicationBuilderResult:
        return result

    monkeypatch.setattr(ApplicationBuildAcceptance, "accept", _accept)
    store = FakeHookStore(
        values={
            "application-wireframe/support-desk": AcceptedApplicationWireframe(
                design_digest=digest,
                content=APPLICATION_DESIGN,
            ).model_dump(mode="json")
        }
    )
    ctx = replace(
        _application_context(FakeSandbox(track_design_claim=True), tmp_path),
        agent=Agent(
            prompt="You triage support requests.",
            model="claude-opus-4-8",
            name="support-desk",
        ),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )

    await build_ufo_application(ctx, BuildUfoApplicationInput())
    assert "application-wireframe/support-desk" in store.values

    worker = deployed
    later = ctx.turn.model_copy(update={"id": uuid4()})
    ctx = replace(ctx, turn=later, idempotency_key=f"{later.id}/build_ufo_application/call-1")
    await build_ufo_application(ctx, BuildUfoApplicationInput())
    assert "application-wireframe/support-desk" not in store.values

    reshape = ctx.turn.model_copy(update={"id": uuid4()})
    ctx = replace(ctx, turn=reshape, idempotency_key=f"{reshape.id}/build_ufo_application/call-1")
    await build_ufo_application(ctx, BuildUfoApplicationInput())

    assert handed == [digest, digest, ""]


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

    first = _application_context(FakeSandbox(), tmp_path)
    second = _application_context(FakeSandbox(), tmp_path)
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
        _application_context(FakeSandbox(), tmp_path),
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


async def test_build_ufo_application_rejects_a_different_key_after_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = 0

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawns
        spawns += 1
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="deployed",
                source_path=APPLICATION_SOURCE_PATH,
                site_name="tasks-homepage",
                site_url="https://ufo.example.test/tasks-homepage",
                browser_batches=2,
            ),
        )

    async def _accept(
        _acceptance: ApplicationBuildAcceptance, result: ApplicationBuilderResult
    ) -> ApplicationBuilderResult:
        return result

    monkeypatch.setattr(ApplicationBuildAcceptance, "accept", _accept)
    ctx = replace(
        _application_context(FakeSandbox(track_design_claim=True), tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    result = await build_ufo_application(ctx, BuildUfoApplicationInput())
    with pytest.raises(ValueError, match="already ran for this parent turn"):
        await build_ufo_application(
            replace(ctx, idempotency_key=f"{ctx.idempotency_key}:different"),
            BuildUfoApplicationInput(),
        )

    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert returned.status == "deployed"
    assert spawns == 1


async def test_build_ufo_application_rejects_a_different_key_after_a_blocked_result(
    tmp_path: Path,
) -> None:
    spawns = 0

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawns
        spawns += 1
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="The connector is unavailable.",
            ),
        )

    ctx = replace(
        _application_context(FakeSandbox(track_design_claim=True), tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    result = await build_ufo_application(ctx, BuildUfoApplicationInput())
    with pytest.raises(ValueError, match="already ran for this parent turn"):
        await build_ufo_application(
            replace(ctx, idempotency_key=f"{ctx.idempotency_key}:different"),
            BuildUfoApplicationInput(),
        )

    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert returned.status == "blocked"
    assert spawns == 1


async def test_build_ufo_application_same_key_resumes_after_claim_publication_fault(
    tmp_path: Path,
) -> None:
    spawns = 0

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawns
        spawns += 1
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="The connector is unavailable.",
            ),
        )

    sandbox = FakeSandbox(
        track_design_claim=True,
        delegation_claim_fault=SystemExit("process fault"),
    )
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    with pytest.raises(SystemExit, match="process fault"):
        await build_ufo_application(ctx, BuildUfoApplicationInput())

    claim_path = f"{RUNTIME_ROOT}/tool-output/application-builder/{ctx.turn.id}.delegated"
    assert sandbox.fixed_call_claims == {claim_path: ctx.idempotency_key}
    assert spawns == 0

    result = await build_ufo_application(ctx, BuildUfoApplicationInput())

    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert returned.status == "blocked"
    assert spawns == 1


async def test_build_ufo_application_concurrent_same_key_uses_spawn_dedup(tmp_path: Path) -> None:
    both_spawns = asyncio.Event()
    release = asyncio.Event()
    spawn_calls = 0
    worker_starts = 0
    worker: asyncio.Task[SpawnResult] | None = None

    async def _worker() -> SpawnResult:
        nonlocal worker_starts
        worker_starts += 1
        await release.wait()
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="The connector is unavailable.",
            ),
        )

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawn_calls, worker
        spawn_calls += 1
        if worker is None:
            worker = asyncio.create_task(_worker())
        if spawn_calls == 2:
            both_spawns.set()
        return await asyncio.shield(worker)

    ctx = replace(
        _application_context(FakeSandbox(track_design_claim=True), tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    calls = tuple(
        asyncio.create_task(build_ufo_application(ctx, BuildUfoApplicationInput()))
        for _ in range(2)
    )
    await both_spawns.wait()
    release.set()
    results = await asyncio.gather(*calls)

    assert results[0].content == results[1].content
    assert spawn_calls == 2
    assert worker_starts == 1


async def test_build_ufo_application_allows_a_new_parent_turn(tmp_path: Path) -> None:
    spawns = 0

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawns
        spawns += 1
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="The connector is unavailable.",
            ),
        )

    sandbox = FakeSandbox(track_design_claim=True)
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    await build_ufo_application(ctx, BuildUfoApplicationInput())
    next_ctx = replace(ctx, turn=ctx.turn.model_copy(update={"id": uuid4()}))
    await build_ufo_application(next_ctx, BuildUfoApplicationInput())

    claims = [
        args[0] for program, args in sandbox.programs if program == APPLICATION_FIXED_CALL_CLAIM
    ]
    assert claims == [
        f"{RUNTIME_ROOT}/tool-output/application-builder/{ctx.turn.id}.delegated",
        f"{RUNTIME_ROOT}/tool-output/application-builder/{next_ctx.turn.id}.delegated",
    ]
    assert spawns == 2


async def test_build_ufo_application_same_key_retries_after_scaffold_setup_fails(
    tmp_path: Path,
) -> None:
    spawns = 0

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawns
        spawns += 1
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="The connector is unavailable.",
            ),
        )

    sandbox = FakeSandbox(
        scripted_programs={APPLICATION_SOURCE_READ: ExecResult("", "not found", 1)},
        workspace_write_error=OSError("scaffold write failed"),
        track_design_claim=True,
    )
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    with pytest.raises(OSError, match="scaffold write failed"):
        await build_ufo_application(ctx, BuildUfoApplicationInput())

    assert spawns == 0
    claim_path = f"{RUNTIME_ROOT}/tool-output/application-builder/{ctx.turn.id}.delegated"
    assert sandbox.fixed_call_claims == {claim_path: ctx.idempotency_key}
    sandbox.workspace_write_error = None

    result = await build_ufo_application(ctx, BuildUfoApplicationInput())

    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert returned.status == "blocked"
    assert spawns == 1


async def test_build_ufo_application_permanent_claim_closes_the_three_call_setup_race(
    tmp_path: Path,
) -> None:
    @dataclass
    class _SetupRaceSandbox(FakeSandbox):
        first_write_started: asyncio.Event = field(default_factory=asyncio.Event)
        finish_first_write: asyncio.Event = field(default_factory=asyncio.Event)
        write_calls: int = 0

        async def write_file(self, path: str, content: bytes) -> None:
            self.write_calls += 1
            if self.write_calls == 1:
                self.first_write_started.set()
                await self.finish_first_write.wait()
                raise OSError("A setup failed")
            await super().write_file(path, content)

    worker_started = asyncio.Event()
    finish_worker = asyncio.Event()
    spawn_calls = 0

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        nonlocal spawn_calls
        spawn_calls += 1
        worker_started.set()
        await finish_worker.wait()
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="blocked",
                source_path=APPLICATION_SOURCE_PATH,
                browser_batches=0,
                blocker="The connector is unavailable.",
            ),
        )

    sandbox = _SetupRaceSandbox(
        scripted_programs={APPLICATION_SOURCE_READ: ExecResult("", "not found", 1)},
        track_design_claim=True,
    )
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(FakeHookStore())),
    )

    first = asyncio.create_task(build_ufo_application(ctx, BuildUfoApplicationInput()))
    await sandbox.first_write_started.wait()
    second = asyncio.create_task(build_ufo_application(ctx, BuildUfoApplicationInput()))
    await worker_started.wait()
    sandbox.finish_first_write.set()

    with pytest.raises(OSError, match="A setup failed") as raised:
        await first

    with pytest.raises(ValueError, match="already ran for this parent turn"):
        await build_ufo_application(
            replace(ctx, idempotency_key=f"{ctx.idempotency_key}:different"),
            BuildUfoApplicationInput(),
        )

    finish_worker.set()
    result = await second

    returned = ApplicationBuilderResult.model_validate_json(result.content[0].text)
    assert str(raised.value) == "A setup failed"
    assert not getattr(raised.value, "__notes__", ())
    assert returned.status == "blocked"
    assert spawn_calls == 1


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
                "on_behalf_of_member_id": member_id,
            }
        ),
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
            audience=SHARED_AUDIENCE,
            artifact_token_secret="",
            ext=ext,
            public_base_url="https://ufo.example.test",
            idempotency_key="application-build:first",
        )
        rejected = await build_ufo_application(ctx, BuildUfoApplicationInput())
        bound_before_source = await HostedSites(workspace_id, workspace_tx).homepage(agent_id)
        sandbox.scripted_paths[f".{child_turn_id}.accepted"] = ExecResult("a" * 64, "", 0)
        ctx = replace(
            ctx,
            turn=ctx.turn.model_copy(update={"id": uuid4()}),
            idempotency_key="application-build:second",
        )
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


async def test_application_wireframe_shares_and_stores_the_builder_svg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = FakeSandbox()
    shared: list[tuple[str, bytes, str | None, StoredPreview | None]] = []
    design = APPLICATION_DESIGN.encode()
    digest = sha256(design).hexdigest()
    captured: dict[str, object] = {}

    async def _share(
        _ctx: ToolContext,
        filename: str,
        data: bytes,
        subject: str | None = None,
        *,
        preview: StoredPreview | None = None,
    ) -> None:
        shared.append((filename, data, subject, preview))

    async def _store_preview(
        _ctx: ToolContext, path: str, name: str, *, extension: str = "png"
    ) -> StoredPreview:
        assert path == (
            f"{RUNTIME_ROOT}/{APPLICATION_DESIGN_PREVIEW_RELATIVE.format(digest=digest)}"
        )
        assert sandbox.writes[path] == sandbox.design_preview
        assert name == f"support-desk-wireframe-{digest[:12]}-preview"
        assert extension == "png"
        return StoredPreview(
            blob_key=f"artifacts/{uuid4()}/{name}.png",
            size_bytes=len(sandbox.design_preview),
        )

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        captured["profile"] = profile
        captured["payload"] = payload
        captured["dedup_key"] = dedup_key
        sandbox.writes[APPLICATION_DESIGN_PATH] = design
        sandbox.writes[
            f"{RUNTIME_ROOT}/{APPLICATION_DESIGN_PREVIEW_RELATIVE.format(digest=digest)}"
        ] = sandbox.design_preview
        rejected = sha256(APPLICATION_DESIGN.replace("183", "170").encode()).hexdigest()
        sandbox.writes[
            f"{RUNTIME_ROOT}/{APPLICATION_DESIGN_PREVIEW_RELATIVE.format(digest=rejected)}"
        ] = b"\x89PNG rejected wireframe"
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="wireframe",
                design_path=APPLICATION_DESIGN_PATH,
                design_digest=digest,
                browser_batches=0,
            ),
        )

    monkeypatch.setattr(ToolContext, "share_artifact", _share)
    monkeypatch.setattr(ToolContext, "store_preview", _store_preview)
    store = FakeHookStore()
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(store)),
        idempotency_key="turn-1/design_ufo_application/call-1",
    )
    args = DesignUfoApplicationInput(
        application_name="support-desk",
        application_prompt="You review support requests before assignment.",
    )

    result = await design_ufo_application(ctx, args)

    rendered = ApplicationWireframeResult.model_validate_json(result.content[0].text)
    assert rendered.status == "ready"
    assert rendered.shared_filename == f"support-desk-wireframe-{digest[:12]}.svg"
    assert rendered.design_digest == digest
    assert captured == {
        "profile": "profile:ufo_application_builder",
        "payload": {
            "objective": (
                "Application instructions:\nYou review support requests before assignment."
            ),
            "scaffold_path": APPLICATION_SCAFFOLD_PATH,
            "source_path": APPLICATION_SOURCE_PATH,
            "phase": "wireframe",
            "accepted_design_digest": "",
            "preload_skills": ("ufo-style",),
        },
        "dedup_key": "turn-1/design_ufo_application/call-1",
    }
    assert shared[0][:3] == (rendered.shared_filename, design, "Application wireframe")
    assert shared[0][3] is not None
    assert shared[0][3].blob_key.endswith(".png")
    assert shared[0][3].size_bytes == len(sandbox.design_preview)
    stored = AcceptedApplicationWireframe.model_validate(
        store.values["application-wireframe/support-desk"]
    )
    assert stored.design_digest == digest
    assert stored.content == APPLICATION_DESIGN


async def test_wireframe_revision_replaces_the_stored_svg_only_after_share(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prior = AcceptedApplicationWireframe(
        design_digest=sha256(APPLICATION_DESIGN.encode()).hexdigest(),
        content=APPLICATION_DESIGN,
    ).model_dump(mode="json")
    revised = APPLICATION_DESIGN.replace("<svg ", '<svg data-revision="2" ', 1).encode()
    revised_digest = sha256(revised).hexdigest()

    async def _refuse_share(
        _ctx: ToolContext,
        filename: str,
        data: bytes,
        subject: str | None = None,
        *,
        preview: StoredPreview | None = None,
    ) -> None:
        raise RuntimeError("share failed")

    async def _store_preview(
        _ctx: ToolContext, path: str, name: str, *, extension: str = "png"
    ) -> StoredPreview:
        return StoredPreview(
            blob_key=f"artifacts/{uuid4()}/{name}.{extension}",
            size_bytes=len(sandbox.design_preview),
        )

    async def _capture(
        profile: str,
        payload: dict,
        background: bool = False,
        dedup_key: str | None = None,
    ) -> SpawnResult:
        sandbox.writes[APPLICATION_DESIGN_PATH] = revised
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=ApplicationBuilderResult(
                status="wireframe",
                design_path=APPLICATION_DESIGN_PATH,
                design_digest=revised_digest,
                browser_batches=0,
            ),
        )

    monkeypatch.setattr(ToolContext, "share_artifact", _refuse_share)
    monkeypatch.setattr(ToolContext, "store_preview", _store_preview)
    store = FakeHookStore(values={"application-wireframe/support-desk": prior})
    sandbox = FakeSandbox()
    ctx = replace(
        _application_context(sandbox, tmp_path),
        spawn=_capture,
        ext=cast(ExtensionContext, FakeHookExt(store)),
        idempotency_key="turn-1/design_ufo_application/call-2",
    )

    with pytest.raises(RuntimeError, match="share failed"):
        await design_ufo_application(
            ctx,
            DesignUfoApplicationInput(
                application_name="support-desk",
                application_prompt="You review support requests before assignment.",
                revision="Put overdue requests first.",
            ),
        )

    assert store.values["application-wireframe/support-desk"] == prior


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
send({ufo:"call",id:"stream",method:"GET",path:"/turns/t1/stream"});
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
    assert [reply["ufo"] for reply in replies if reply.get("id") == "stream"] == [
        "opened",
        "frame",
        "end",
    ]
    terminal = next(
        reply for reply in replies if reply.get("id") == "stream" and reply["ufo"] == "frame"
    )
    assert terminal["event"] == "terminal"
    assert json.loads(terminal["data"])["status"] == "done"


def test_application_audit_excludes_hidden_text_from_contrast() -> None:
    source = (
        Path(sites_manifest.__file__).parent / "scripts" / "audit_application.cjs"
    ).read_text()

    assert "if (!visible(element, box)) continue;" in source
    assert "visuallyHidden(element, style, box)" not in source


def test_six_maximal_design_regions_fit_the_browser_output_boundary() -> None:
    program = """
const visibleText = String.fromCodePoint(1).repeat(Number(process.argv[1]));
const regions = Array.from({ length: 6 }, (_unused, index) => ({
  name: String.fromCodePoint(97 + index) + 'x'.repeat(79),
  left: 0.12345678901234568,
  top: 0.9876543210987654,
  width: 0.9999999999999999,
  height: 0.1111111111111111,
  aboveFold: true,
  visibleText,
}));
process.stdout.write(JSON.stringify(regions));
"""
    completed = subprocess.run(
        ("node", "-e", program, str(DESIGN_VISIBLE_TEXT_MAX_CHARS)),
        check=True,
        capture_output=True,
        text=True,
    )
    regions = TypeAdapter(tuple[ApplicationAuditRegion, ...]).validate_json(completed.stdout)
    payload = completed.stdout.encode()
    source = (
        Path(sites_manifest.__file__).parent / "scripts" / "audit_application.cjs"
    ).read_text()

    assert len(payload) == 3_991 < APPLICATION_DESIGN_AUDIT_MAX_BYTES
    assert len(regions) == 6
    assert {len(region.visible_text) for region in regions} == {DESIGN_VISIBLE_TEXT_MAX_CHARS}
    assert (
        ApplicationAuditRegion.model_json_schema()["properties"]["visibleText"]["maxLength"]
        == DESIGN_VISIBLE_TEXT_MAX_CHARS
    )
    assert f"const DESIGN_OUTPUT_BYTE_MAX = {APPLICATION_DESIGN_AUDIT_MAX_BYTES};" in source
    assert f"const DESIGN_VISIBLE_TEXT_MAX_CHARS = {DESIGN_VISIBLE_TEXT_MAX_CHARS};" in source


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


def test_application_product_audit_has_no_design_http_pass() -> None:
    source = (
        Path(sites_manifest.__file__).parent / "scripts" / "audit_application.cjs"
    ).read_text()

    assert "designRegionAudit" not in source
    assert "acceptedDesignUrl" not in source
    assert "acceptedDesignRegions(acceptedDesignPath, acceptedEvidencePath)" in source
    assert "design-url" not in source
    assert (
        "const report = { url, floor: AA_FLOOR, designHeight, designRegions, views, interaction };"
        in source
    )


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

    for entry in await registry.materialize(registry.closure("website-building/webapp")):
        await install_skill(_SkillSandbox(written), entry.skill)

    assert "$UFO_HOME/skills/website-building/SKILL.md" in written
    assert "$UFO_HOME/skills/website-building/webapp/SKILL.md" in written
    assert "$UFO_HOME/skills/website-building/shared/01-design-tokens.md" in written
    assert not any(path.startswith("$UFO_HOME/skills/website-building-") for path in written)


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
    assert profile.prompt.count("--accent-primary") == 2
    assert profile.prompt.count("--color-fill-ink") == 2
    assert "`--color-link` is text, not a fill" in profile.prompt
    assert set(profile.tool_names) == {
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "read",
        APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL,
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
    rendered_prompt = subagent_system_prompt(profile)
    assert "<parent_handoff>" not in rendered_prompt
    assert "at most 20 words" not in rendered_prompt
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
        "phase",
        "accepted_design_digest",
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
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 8,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "#2042",
                    "regions": AUDIT_DESIGN_REGIONS,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
    sandbox = FakeSandbox(
        scripted_paths={"/application-audit/": ExecResult(report, "", 0)},
        require_application_source_root=True,
    )
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
    _seed_accepted_application_design(sandbox, ctx.turn.id)
    result = await qa_ufo_application(
        ctx,
        QaUfoApplicationInput(),
    )

    payload = json.loads(result.content[0].text)
    assert payload == {
        "status": "passed",
        "views_checked": [
            f"light {DESKTOP_WIDTH}px",
            f"dark {DESKTOP_WIDTH}px",
            f"light {NARROW_WIDTH}px",
            f"dark {NARROW_WIDTH}px",
        ],
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
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 8,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Dense controls",
                    "regions": AUDIT_DESIGN_REGIONS,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
    _seed_accepted_application_design(sandbox, ctx.turn.id)

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
    _seed_accepted_application_design(sandbox, ctx.turn.id)

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


async def test_application_builder_wireframe_phase_refuses_build_work(tmp_path: Path) -> None:
    base = _context(FakeSandbox(), tmp_path)
    task = ApplicationBuilderTask(
        objective="Design the support desk.",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
        phase="wireframe",
    )
    turn = base.turn.model_copy(
        update={
            "inbound": task.model_dump_json(),
            "subagent_profile": APPLICATION_BUILDER_NAME,
        }
    )
    ext = cast(ExtensionContext, FakeHookExt(FakeHookStore()))

    def phase_hook(tool_name: str) -> HookContext:
        return HookContext(
            ext=ext,
            payload=PreToolUse(tool_name=tool_name, tool_input=BuildUfoApplicationInput()),
            turn=turn,
        )

    assert (
        await enforce_application_builder_phase(phase_hook(APPLICATION_BUILDER_DESIGN_TOOL)) is None
    )
    for tool_name in (
        "list_external_tools",
        APPLICATION_BUILDER_WRITE_TOOL,
        APPLICATION_BUILDER_QA_TOOL,
        APPLICATION_BUILDER_DEPLOY_TOOL,
    ):
        refused = await enforce_application_builder_phase(phase_hook(tool_name))
        assert isinstance(refused, Deny)


def test_application_builder_prompt_states_the_wireframe_phase_contract() -> None:
    wireframe, build = APPLICATION_BUILDER_PROFILE.prompt.split("For `build`", 1)

    assert f"call `{APPLICATION_BUILDER_DESIGN_TOOL}`" in wireframe
    assert "`status: wireframe`" in wireframe
    assert "`design_path` and `design_digest`" in wireframe
    assert "do not inspect connectors or write application source" in wireframe
    assert "list_external_tools" not in wireframe
    assert "Inspect the needed connected sources" in build


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
    ctx = _application_design_context(ctx)

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
        "page_height": APPLICATION_DESIGN_FOLD,
        "rendered_regions": list(AUDIT_DESIGN_REGIONS),
    }
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()
    accepted_evidence_path = (
        f"{RUNTIME_ROOT}/{application_design_evidence_relative(payload['path'], ctx.turn.id)}"
    )
    accepted_evidence = AcceptedApplicationDesignEvidence.model_validate_json(
        sandbox.writes[accepted_evidence_path]
    )
    assert accepted_evidence.design_sha256 == payload["design_digest"]
    assert accepted_evidence.kit_components == ("Card",)
    assert accepted_evidence.regions == tuple(
        ApplicationAuditRegion.model_validate(region) for region in AUDIT_DESIGN_REGIONS
    )
    with pytest.raises(ValueError, match="SVG drawing elements only"):
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(
                content='<svg viewBox="0 0 305 844" width="305" height="844">'
                '<script>fetch("https://bad")</script></svg>',
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
    with pytest.raises(ValueError, match="requires 2 to 6 unique regions"):
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(
                content='<svg viewBox="0 0 305 844" width="305" height="844">'
                '<rect width="305" height="844" /></svg>',
            ),
        )


async def test_application_builder_seals_the_exact_member_wireframe(tmp_path: Path) -> None:
    digest = sha256(APPLICATION_DESIGN.encode()).hexdigest()
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
        accepted_design_digest=digest,
    )
    sandbox = FakeSandbox()
    sandbox.writes[APPLICATION_DESIGN_PATH] = APPLICATION_DESIGN.encode()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        idempotency_key=f"{base.turn.id}/{APPLICATION_BUILDER_ACCEPT_DESIGN_TOOL}/call-1",
    )

    result = await accept_application_wireframe(ctx, AcceptApplicationWireframeInput())

    assert json.loads(result.content[0].text)["design_digest"] == digest
    accepted = (
        f"{RUNTIME_ROOT}/"
        f"{application_design_acceptance_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)}"
    )
    assert sandbox.writes[accepted] == APPLICATION_DESIGN.encode()


async def test_application_builder_design_dispatch_gets_stable_call_key(
    db: None, tmp_path: Path
) -> None:
    call_id = "design-call-1"
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    workspace_id, agent_id, conversation_id, turn_id, parent_turn_id = (uuid4() for _ in range(5))
    created_at = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=APPLICATION_BUILDER_NAME,
                prompt="p",
                model=APPLICATION_BUILDER_MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound=task.model_dump_json(),
                parent_turn_id=parent_turn_id,
                subagent_profile=APPLICATION_BUILDER_NAME,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    turn = Turn(
        id=turn_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="queued",
        inbound=task.model_dump_json(),
        parent_turn_id=parent_turn_id,
        subagent_profile=APPLICATION_BUILDER_NAME,
        created_at=created_at,
    )

    @dataclass(frozen=True)
    class DesignModel:
        async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
            if any(
                isinstance(message.content, tuple)
                and any(isinstance(block, ToolResultBlock) for block in message.content)
                for message in request.messages
            ):
                yield TextDelta(text="done")
                yield Usage(input_tokens=1, output_tokens=1)
                return
            yield ToolCallStart(id=call_id, name=APPLICATION_BUILDER_DESIGN_TOOL)
            yield ToolCallDelta(
                id=call_id,
                partial_json=json.dumps({"content": APPLICATION_DESIGN}),
            )
            yield Usage(input_tokens=1, output_tokens=1)

    @dataclass(frozen=True)
    class ActivityModel:
        model = APPLICATION_BUILDER_MODEL

        async def complete(self, request: ModelRequest) -> str:
            return "Validating design"

    class DispatchSandbox(FakeSandbox):
        @property
        def created(self) -> bool:
            return False

    manifest = sites_manifest.manifest()
    design_tool = next(
        tool for tool in manifest.tools if tool.name == APPLICATION_BUILDER_DESIGN_TOOL
    )
    audience = conversation_audience(None)
    sandbox = DispatchSandbox(
        track_design_claim=True,
        handle=SandboxHandle(conversation_id=conversation_id, container_id="sites-test"),
    )
    blob = FilesystemBlobStore(root=tmp_path)
    model = DesignModel()
    engine = TurnEngine(
        turn=turn,
        agent=Agent(prompt="p", model=APPLICATION_BUILDER_MODEL),
        byok=True,
        system_prompt=rendered_prompt("p"),
        model=model,
        activity_summarizer=ActivitySummarizer(ActivityModel()),
        provider="openrouter",
        transcript=Transcript(blob=blob, conversation_id=conversation_id),
        compaction=Compaction(
            client=model,
            model=APPLICATION_BUILDER_MODEL,
            blob=blob,
            conversation_id=conversation_id,
        ),
        hub=InProcessHub(),
        sandbox=sandbox,
        cdp_provider=None,
        search_provider=None,
        connectors=ConnectorRegistry(entries={}),
        tools=ToolRegistry((*BUILTIN_TOOLS, design_tool)),
        tool_ext={design_tool.name: context_for(manifest.name, frozenset(), blob=blob)},
        hooks=HookChain(audience=audience),
        blob=blob,
        spawn=_unavailable_spawn,
        audience=audience,
        artifact_token_secret="",
        grants=None,
    )

    with ws(workspace_id):
        frame = await engine.run()

    assert design_tool.side_effecting is True
    assert frame is not None and frame.status == "done"
    transcript = await engine.transcript.read()
    assert transcript is not None
    tool_use = next(
        block
        for message in transcript.messages
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolUseBlock)
    )
    tool_result = next(
        block
        for message in transcript.messages
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolResultBlock)
    )
    expected_key = f"{turn_id}/{APPLICATION_BUILDER_DESIGN_TOOL}/{call_id}"
    assert tool_use.id == call_id
    assert tool_result.tool_use_id == call_id
    assert tool_result.is_error is False
    assert isinstance(tool_result.content, str)
    assert json.loads(tool_result.content)["rendered_regions"] == list(AUDIT_DESIGN_REGIONS)
    (claim_identity,) = sandbox.fixed_call_claims.values()
    assert json.loads(claim_identity)[:3] == [str(parent_turn_id), str(turn_id), expected_key]


@pytest.mark.parametrize("crash_after_link", (1, 2))
async def test_write_application_design_recovers_each_partial_pair_and_passes_qa(
    tmp_path: Path, crash_after_link: int
) -> None:
    report = json.dumps(
        {
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 2,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": AUDIT_DESIGN_REGIONS,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
                "states": [["Queue"]],
                "console": [],
            },
        }
    )

    class CrashDesignAcceptSandbox(FakeSandbox):
        def __init__(self) -> None:
            super().__init__(
                scripted_paths={"/application-audit/": ExecResult(report, "", 0)},
                track_design_claim=True,
            )
            self.root = tmp_path / "runtime"
            self.root.mkdir()
            self.acceptance_calls = 0

        async def runtime_path(self, relative: str) -> str:
            return str(self.root / relative)

        async def file_state(self, path: str) -> tuple[bytes, int] | None:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import base64,json,os,sys\n"
                "path = sys.argv[1]\n"
                "if not os.path.exists(path): raise SystemExit(17)\n"
                "with open(path, 'rb') as handle: data = handle.read()\n"
                "print(json.dumps((base64.urlsafe_b64encode(data).decode(), "
                "os.stat(path).st_mode & 0o777)))",
                path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await process.communicate()
            if process.returncode == 17:
                return None
            encoded, mode = json.loads(stdout)
            return urlsafe_b64decode(encoded), mode

        async def python(
            self, program: str, *args: str, timeout_s: int | None = None
        ) -> ExecResult:
            if (
                program == sites_tools.APPLICATION_AUDIT_REPORT_READ
                and "/application-builder/" in args[0]
            ):
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    program,
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                return ExecResult(stdout.decode(), stderr.decode(), process.returncode or 0)
            if program != APPLICATION_DESIGN_ACCEPT:
                return await super().python(program, *args, timeout_s=timeout_s)
            self.programs.append((program, args))
            materialized = json.dumps(
                tuple(
                    (args[index], urlsafe_b64encode(self.writes[args[index]]).decode())
                    for index in (0, 2)
                )
            ).encode()
            writer = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import base64,json,os,sys\n"
                "for path, data in json.load(sys.stdin):\n"
                "    os.makedirs(os.path.dirname(path), exist_ok=True)\n"
                "    with open(path, 'wb') as handle: "
                "handle.write(base64.urlsafe_b64decode(data))",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await writer.communicate(materialized)
            if writer.returncode != 0:
                return ExecResult("", stderr.decode(), writer.returncode or 1)
            selected = program
            if self.acceptance_calls == 0:
                selected = (
                    "import os\n"
                    "real_link = os.link\n"
                    "published = 0\n"
                    "def crash_link(*args, **kwargs):\n"
                    "    global published\n"
                    "    real_link(*args, **kwargs)\n"
                    "    target = os.fspath(args[1])\n"
                    "    if target.endswith(('.accepted.svg', '.accepted-design.json')):\n"
                    "        published += 1\n"
                    f"        if published == {crash_after_link}:\n"
                    "            os._exit(99)\n"
                    "os.link = crash_link\n"
                    f"{program}"
                )
            self.acceptance_calls += 1
            containment_root = Path(__file__).parents[3] / "core" / "src" / "ufo" / "harness"
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                selected,
                *args,
                cwd=containment_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            return ExecResult(stdout.decode(), stderr.decode(), process.returncode or 0)

    sandbox = CrashDesignAcceptSandbox()
    parent_turn_id = uuid4()
    store = FakeHookStore(
        values={
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(
                turn_id=parent_turn_id
            ): ApplicationAuditContract().model_dump()
        }
    )
    base = _context(sandbox, tmp_path)
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    ctx = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "parent_turn_id": parent_turn_id,
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    ctx = _application_design_context(ctx)

    with pytest.raises(RuntimeError, match="accepted application design could not be written"):
        await write_application_design(ctx, WriteApplicationDesignInput(content=APPLICATION_DESIGN))

    accepted_design = await sandbox.runtime_path(
        application_design_acceptance_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)
    )
    accepted_evidence = await sandbox.runtime_path(
        application_design_evidence_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)
    )
    assert (
        sum(
            state is not None
            for state in await asyncio.gather(
                sandbox.file_state(accepted_design), sandbox.file_state(accepted_evidence)
            )
        )
        == crash_after_link
    )

    recovered = await write_application_design(
        ctx, WriteApplicationDesignInput(content=APPLICATION_DESIGN)
    )
    with pytest.raises(ValueError, match="already fixed"):
        await write_application_design(
            replace(ctx, idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-2"),
            WriteApplicationDesignInput(content=APPLICATION_DESIGN),
        )
    idempotent = await write_application_design(
        ctx, WriteApplicationDesignInput(content=APPLICATION_DESIGN)
    )
    source = (
        'import { Card, mountApp } from "ufo/kit";\n'
        'mountApp(document.getElementById("root")!, () => <Card>Queue</Card>);'
    )
    await write_application_source(ctx, WriteApplicationSourceInput(content=source))
    qa = await qa_ufo_application(ctx, QaUfoApplicationInput())

    assert recovered == idempotent
    design_state = await sandbox.file_state(accepted_design)
    evidence_state = await sandbox.file_state(accepted_evidence)
    assert design_state == (APPLICATION_DESIGN.encode(), 0o400)
    assert evidence_state is not None
    accepted_evidence_value = AcceptedApplicationDesignEvidence.model_validate_json(
        evidence_state[0]
    )
    assert accepted_evidence_value.design_sha256 == sha256(APPLICATION_DESIGN.encode()).hexdigest()
    assert accepted_evidence_value.kit_components == ("Card",)
    assert evidence_state[1] == 0o400
    assert all(program != APPLICATION_FIXED_CALL_CLAIM for program, _ in sandbox.programs)
    assert sandbox.acceptance_calls == 4
    assert json.loads(qa.content[0].text)["status"] == "passed"


@pytest.mark.parametrize("partial", ("design", "evidence"))
async def test_application_design_pair_rejects_mismatched_partial(
    tmp_path: Path, partial: str
) -> None:
    class ContainedMismatchDesignAcceptSandbox(FakeSandbox):
        def __init__(self) -> None:
            super().__init__(track_design_claim=True)
            self.root = tmp_path / "runtime"
            self.root.mkdir()

        async def runtime_path(self, relative: str) -> str:
            return str(self.root / relative)

        async def file_state(self, path: str) -> tuple[bytes, int] | None:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import base64,json,os,sys\n"
                "path = sys.argv[1]\n"
                "if not os.path.exists(path): raise SystemExit(17)\n"
                "with open(path, 'rb') as handle: data = handle.read()\n"
                "print(json.dumps((base64.urlsafe_b64encode(data).decode(), "
                "os.stat(path).st_mode & 0o777)))",
                path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await process.communicate()
            if process.returncode == 17:
                return None
            encoded, mode = json.loads(stdout)
            return urlsafe_b64decode(encoded), mode

        async def python(
            self, program: str, *args: str, timeout_s: int | None = None
        ) -> ExecResult:
            if program != APPLICATION_DESIGN_ACCEPT:
                return await super().python(program, *args, timeout_s=timeout_s)
            self.programs.append((program, args))
            materialized = json.dumps(
                tuple(
                    (args[index], urlsafe_b64encode(self.writes[args[index]]).decode())
                    for index in (0, 2)
                )
            ).encode()
            writer = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import base64,json,os,sys\n"
                "for path, data in json.load(sys.stdin):\n"
                "    os.makedirs(os.path.dirname(path), exist_ok=True)\n"
                "    with open(path, 'wb') as handle: "
                "handle.write(base64.urlsafe_b64decode(data))",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await writer.communicate(materialized)
            if writer.returncode != 0:
                return ExecResult("", stderr.decode(), writer.returncode or 1)
            target_index = 2 if partial == "design" else 4
            injected = (
                "import os,sys\n"
                "from containment import contained_file\n"
                "from uuid import uuid4\n"
                f"with contained_file(sys.argv[{target_index}], sys.argv[6], "
                "create_parent=True) as target:\n"
                "    staged = f'.ufo-mismatch-{uuid4().hex}'\n"
                "    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | "
                "os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target.parent_fd)\n"
                "    os.fchmod(descriptor, 0o400)\n"
                "    with os.fdopen(descriptor, 'wb') as handle:\n"
                "        handle.write(b'different')\n"
                "        handle.flush()\n"
                "        os.fsync(handle.fileno())\n"
                "    os.link(staged, target.name, src_dir_fd=target.parent_fd, "
                "dst_dir_fd=target.parent_fd, follow_symlinks=False)\n"
                "    os.unlink(staged, dir_fd=target.parent_fd)\n"
                "    os.fsync(target.parent_fd)\n"
                f"{program}"
            )
            containment_root = Path(__file__).parents[3] / "core" / "src" / "ufo" / "harness"
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                injected,
                *args,
                cwd=containment_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            return ExecResult(stdout.decode(), stderr.decode(), process.returncode or 0)

    sandbox = ContainedMismatchDesignAcceptSandbox()
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    parent_turn_id = uuid4()
    store = FakeHookStore(
        values={
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(
                turn_id=parent_turn_id
            ): ApplicationAuditContract().model_dump()
        }
    )
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "parent_turn_id": parent_turn_id,
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
            ext=cast(ExtensionContext, FakeHookExt(store)),
        )
    )

    with pytest.raises(RuntimeError, match="differs from this application design call"):
        await write_application_design(ctx, WriteApplicationDesignInput(content=APPLICATION_DESIGN))

    accepted_design = await sandbox.runtime_path(
        application_design_acceptance_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)
    )
    accepted_evidence = await sandbox.runtime_path(
        application_design_evidence_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)
    )
    states = {
        "design": await sandbox.file_state(accepted_design),
        "evidence": await sandbox.file_state(accepted_evidence),
    }
    assert states[partial] == (b"different", 0o400)
    assert states["evidence" if partial == "design" else "design"] is None
    assert APPLICATION_DESIGN_PATH not in sandbox.writes
    accept_calls = [
        args for program, args in sandbox.programs if program == APPLICATION_DESIGN_ACCEPT
    ]
    assert len(accept_calls) == 1
    assert json.loads(accept_calls[0][6])[2] == ctx.idempotency_key

    source = (
        'import { mountApp } from "ufo/kit";\n'
        'mountApp(document.getElementById("root")!, () => <main>Queue</main>);'
    )
    with pytest.raises(ValueError, match="write_application_design must complete"):
        await write_application_source(ctx, WriteApplicationSourceInput(content=source))
    audit_shells = len(sandbox.shells)
    with pytest.raises(RuntimeError, match="accepted application design"):
        await qa_ufo_application(ctx, QaUfoApplicationInput())

    assert APPLICATION_SOURCE_PATH not in sandbox.writes
    assert states[partial] == await sandbox.file_state(
        accepted_design if partial == "design" else accepted_evidence
    )
    assert len(sandbox.shells) == audit_shells
    assert sandbox.tasks == []


async def test_application_builder_next_call_repairs_after_design_write_fails(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox(
        workspace_write_error=OSError("write failed"),
        track_design_claim=True,
    )
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
    ctx = _application_design_context(ctx)

    with pytest.raises(OSError, match="write failed"):
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(content=APPLICATION_DESIGN),
        )

    assert not sandbox.design_claimed
    assert sandbox.workspace_writes == []
    assert "/workspace/application/application-design.svg" not in sandbox.writes
    design_path = "/workspace/application/application-design.svg"
    evidence_path = (
        f"{RUNTIME_ROOT}/{application_design_evidence_relative(design_path, ctx.turn.id)}"
    )
    accepted_path = (
        f"{RUNTIME_ROOT}/{application_design_acceptance_relative(design_path, ctx.turn.id)}"
    )
    assert evidence_path not in sandbox.writes
    assert accepted_path not in sandbox.writes
    assert [program for program, _ in sandbox.programs[-2:]] == [
        APPLICATION_DESIGN_ACCEPT,
        APPLICATION_DESIGN_RELEASE_ACCEPTED,
    ]
    sandbox.workspace_write_error = None

    result = await write_application_design(
        replace(
            ctx,
            idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-2",
        ),
        WriteApplicationDesignInput(content=APPLICATION_DESIGN),
    )

    payload = json.loads(result.content[0].text)
    assert sandbox.design_claimed
    assert sandbox.workspace_writes == ["/workspace/application/application-design.svg"]
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()
    assert evidence_path in sandbox.writes
    assert accepted_path in sandbox.writes


async def test_application_builder_next_call_repairs_after_accept_fails_after_claim(
    tmp_path: Path,
) -> None:
    class PostClaimFailureSandbox(FakeSandbox):
        failed = False

        async def python(
            self, program: str, *args: str, timeout_s: int | None = None
        ) -> ExecResult:
            if program != APPLICATION_DESIGN_ACCEPT or self.failed:
                return await super().python(program, *args, timeout_s=timeout_s)
            self.programs.append((program, args))
            claim_path, identity = args[4], args[6]
            self.fixed_call_claims[claim_path] = identity
            self.failed = True
            return ExecResult("", "accept failed after claim", 1)

    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = PostClaimFailureSandbox(track_design_claim=True)
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )
    design_path = "/workspace/application/application-design.svg"
    claim_path = (
        f"{RUNTIME_ROOT}/tool-output/application-builder/"
        f"{sha256(design_path.encode()).hexdigest()}.{ctx.turn.id}.claimed"
    )
    accepted_path = (
        f"{RUNTIME_ROOT}/{application_design_acceptance_relative(design_path, ctx.turn.id)}"
    )
    evidence_path = (
        f"{RUNTIME_ROOT}/{application_design_evidence_relative(design_path, ctx.turn.id)}"
    )

    with pytest.raises(RuntimeError, match="accept failed after claim"):
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(content=APPLICATION_DESIGN),
        )

    assert claim_path not in sandbox.fixed_call_claims
    assert accepted_path not in sandbox.writes
    assert evidence_path not in sandbox.writes
    assert [program for program, _ in sandbox.programs[-2:]] == [
        APPLICATION_DESIGN_ACCEPT,
        APPLICATION_DESIGN_RELEASE_CLAIM,
    ]

    retry = replace(
        ctx,
        idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-2",
    )
    result = await write_application_design(
        retry,
        WriteApplicationDesignInput(content=APPLICATION_DESIGN),
    )

    assert json.loads(result.content[0].text)["path"] == design_path
    assert json.loads(sandbox.fixed_call_claims[claim_path])[2] == retry.idempotency_key
    assert sandbox.writes[accepted_path] == APPLICATION_DESIGN.encode()
    assert evidence_path in sandbox.writes


def test_application_design_release_deletes_the_owned_pair_and_claim(tmp_path: Path) -> None:
    design = tmp_path / "accepted.svg"
    evidence = tmp_path / "accepted-design.json"
    claim = tmp_path / "design.claim"
    design.write_bytes(b"design")
    evidence.write_bytes(b"evidence")
    claim.write_bytes(b"call-1")
    containment_root = Path(__file__).parents[3] / "core" / "src" / "ufo" / "harness"

    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            APPLICATION_DESIGN_RELEASE_ACCEPTED,
            str(design),
            str(evidence),
            sha256(b"design").hexdigest(),
            sha256(b"evidence").hexdigest(),
            "100",
            "100",
            str(tmp_path),
            str(claim),
            "call-1",
        ),
        cwd=containment_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not design.exists()
    assert not evidence.exists()
    assert not claim.exists()


def test_application_design_release_deletes_the_owned_claim(tmp_path: Path) -> None:
    claim = tmp_path / "design.claim"
    claim.write_bytes(b"call-1")
    containment_root = Path(__file__).parents[3] / "core" / "src" / "ufo" / "harness"

    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            APPLICATION_DESIGN_RELEASE_CLAIM,
            str(claim),
            str(tmp_path),
            "call-1",
        ),
        cwd=containment_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not claim.exists()


@pytest.mark.parametrize(
    "cleanup_error",
    (OSError("cleanup failed"), asyncio.CancelledError("cleanup cancelled")),
)
async def test_application_builder_preserves_write_error_and_pair_cleanup_failure(
    tmp_path: Path,
    cleanup_error: BaseException,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    sandbox = FakeSandbox(
        workspace_write_error=OSError("write failed"),
        track_design_claim=True,
        program_errors={APPLICATION_DESIGN_RELEASE_ACCEPTED: cleanup_error},
    )
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
    ctx = _application_design_context(ctx)

    with pytest.raises(OSError, match="write failed") as raised:
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(content=APPLICATION_DESIGN),
        )

    assert sandbox.design_claimed
    assert str(cleanup_error) in getattr(raised.value, "__notes__", ())
    assert sandbox.programs[-1][0] == APPLICATION_DESIGN_RELEASE_ACCEPTED


async def test_application_builder_rejects_duplicate_design_ids_before_claim(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/ufo-app",
        source_path="/workspace/ufo-app/app.tsx",
    )
    sandbox = FakeSandbox(track_design_claim=True)
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
    ctx = _application_design_context(ctx)
    duplicate = APPLICATION_DESIGN.replace(
        '<g data-app-region="queue">',
        '<g id="panel" data-app-region="queue">',
    ).replace(
        '<g data-app-region="detail">',
        '<g id="panel" data-app-region="detail">',
    )

    with pytest.raises(ValueError, match="application design SVG ids must be unique"):
        await write_application_design(ctx, WriteApplicationDesignInput(content=duplicate))

    assert sandbox.runtime_writes == []
    assert sandbox.workspace_writes == []
    assert sandbox.programs == []
    assert not sandbox.design_claimed

    result = await write_application_design(
        ctx,
        WriteApplicationDesignInput(content=APPLICATION_DESIGN),
    )

    payload = json.loads(result.content[0].text)
    assert sandbox.design_claimed
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()


async def test_application_builder_rejects_overlap_before_fixing_design(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/ufo-app",
        source_path="/workspace/ufo-app/app.tsx",
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
    ctx = _application_design_context(ctx)
    overlap = APPLICATION_DESIGN.replace('x="183"', 'x="170"')
    corrected = APPLICATION_DESIGN
    sandbox.design_audit = ExecResult(
        json.dumps(
            (
                AUDIT_DESIGN_REGIONS[0],
                {**AUDIT_DESIGN_REGIONS[1], "left": 0.5},
            )
        ),
        "",
        0,
    )

    with pytest.raises(ValueError, match="design regions queue and detail overlap"):
        await write_application_design(ctx, WriteApplicationDesignInput(content=overlap))

    assert not sandbox.programs
    assert sandbox.workspace_writes == []
    sandbox.design_audit = ExecResult(json.dumps(AUDIT_DESIGN_REGIONS), "", 0)
    result = await write_application_design(
        ctx,
        WriteApplicationDesignInput(content=corrected),
    )

    payload = json.loads(result.content[0].text)
    assert payload["design_digest"] == sha256(corrected.encode()).hexdigest()
    assert sandbox.writes[payload["path"]] == corrected.encode()

    report = json.dumps(
        {
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 2,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": AUDIT_DESIGN_REGIONS,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
                "states": [["Queue"]],
                "console": [],
            },
        }
    )
    source = "import { mountApp } from 'ufo/kit';\n"
    sandbox.writes[task.source_path] = source.encode()
    sandbox.scripted_paths["/application-audit/"] = ExecResult(report, "", 0)
    parent_turn_id = uuid4()
    store = FakeHookStore(
        values={
            APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(
                turn_id=parent_turn_id
            ): ApplicationAuditContract().model_dump()
        }
    )
    ctx = replace(
        ctx,
        turn=ctx.turn.model_copy(update={"parent_turn_id": parent_turn_id}),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )

    qa = await qa_ufo_application(ctx, QaUfoApplicationInput())
    deployment = HookContext(
        ext=cast(ExtensionContext, FakeHookExt(store)),
        payload=PreToolUse(
            tool_name=APPLICATION_BUILDER_DEPLOY_TOOL,
            tool_input=DeployUfoApplicationInput(site_name="queue"),
        ),
        turn=ctx.turn,
    )

    assert json.loads(qa.content[0].text)["status"] == "passed"
    assert await require_application_builder_qa(deployment) is None


async def test_application_builder_rejects_small_region_before_acceptance(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )
    sandbox.design_audit = ExecResult(
        json.dumps(
            (
                {**AUDIT_DESIGN_REGIONS[0], "width": APPLICATION_REGION_MIN_WIDTH - 0.001},
                AUDIT_DESIGN_REGIONS[1],
            )
        ),
        "",
        0,
    )

    with pytest.raises(ValueError, match="design region queue is too small"):
        await write_application_design(ctx, WriteApplicationDesignInput(content=APPLICATION_DESIGN))

    accepted_design = await sandbox.runtime_path(
        application_design_acceptance_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)
    )
    accepted_evidence = await sandbox.runtime_path(
        application_design_evidence_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)
    )
    assert accepted_design not in sandbox.writes
    assert accepted_evidence not in sandbox.writes
    assert sandbox.workspace_writes == []
    assert sandbox.programs == []


async def test_application_builder_accepts_a_compact_band_on_a_tall_page(tmp_path: Path) -> None:
    page_height = APPLICATION_DESIGN_MAX_HEIGHT
    band_height = 80
    design = (
        f'<svg viewBox="0 0 305 {page_height}" width="305" height="{page_height}">'
        f'<g data-app-region="queue" data-kit-component="Card">'
        f'<rect width="305" height="{band_height}" /></g>'
        f'<g data-app-region="detail"><rect y="{APPLICATION_DESIGN_FOLD}" width="305" '
        f'height="{page_height - APPLICATION_DESIGN_FOLD}" /></g>'
        "</svg>"
    )
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )
    sandbox.design_audit = ExecResult(
        json.dumps(
            (
                {
                    "name": "queue",
                    "left": 0.0,
                    "top": 0.0,
                    "width": 1.0,
                    "height": band_height / page_height,
                    "aboveFold": True,
                },
                {
                    "name": "detail",
                    "left": 0.0,
                    "top": APPLICATION_DESIGN_FOLD / page_height,
                    "width": 1.0,
                    "height": (page_height - APPLICATION_DESIGN_FOLD) / page_height,
                    "aboveFold": False,
                },
            )
        ),
        "",
        0,
    )

    result = await write_application_design(ctx, WriteApplicationDesignInput(content=design))

    payload = json.loads(result.content[0].text)
    assert payload["design_digest"] == sha256(design.encode()).hexdigest()
    assert sandbox.writes[payload["path"]] == design.encode()


async def test_application_builder_states_the_region_fold_rule_the_design_gate_enforces(
    tmp_path: Path,
) -> None:
    rule = (
        f"Keep the primary task and the required facts above y={APPLICATION_DESIGN_FOLD}, and do "
        f"not draw one region as a band across y={APPLICATION_DESIGN_FOLD}."
    )
    design_paragraph = next(
        block
        for block in APPLICATION_BUILDER_PROFILE.prompt.split("\n\n")
        if "data-app-region" in block
    )
    design_tool = next(
        tool
        for tool in sites_manifest.manifest().tools
        if tool.name == APPLICATION_BUILDER_DESIGN_TOOL
    )
    for stated in (design_paragraph, design_tool.description):
        assert rule in " ".join(stated.split())

    page_height = 1050
    band_height = 240
    gap = 16

    def design(detail_top: int) -> str:
        return (
            f'<svg viewBox="0 0 305 {page_height}" width="305" height="{page_height}">'
            f'<g data-app-region="queue" data-kit-component="Card">'
            f'<rect width="305" height="{band_height}" /></g>'
            f'<g data-app-region="detail"><rect y="{detail_top}" width="305" '
            f'height="{page_height - detail_top}" /></g>'
            "</svg>"
        )

    def measured(detail_top: int) -> ExecResult:
        return ExecResult(
            json.dumps(
                (
                    {
                        "name": "queue",
                        "left": 0.0,
                        "top": 0.0,
                        "width": 1.0,
                        "height": band_height / page_height,
                        "aboveFold": True,
                    },
                    {
                        "name": "detail",
                        "left": 0.0,
                        "top": detail_top / page_height,
                        "width": 1.0,
                        "height": (page_height - detail_top) / page_height,
                        "aboveFold": detail_top < APPLICATION_DESIGN_FOLD,
                    },
                )
            ),
            "",
            0,
        )

    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )
    sandbox.design_audit = measured(band_height + gap)

    with pytest.raises(ValueError, match="design region detail crosses the first-screen boundary"):
        await write_application_design(
            ctx, WriteApplicationDesignInput(content=design(band_height + gap))
        )

    assert sandbox.workspace_writes == []
    obedient = design(APPLICATION_DESIGN_FOLD)
    sandbox.design_audit = measured(APPLICATION_DESIGN_FOLD)

    result = await write_application_design(ctx, WriteApplicationDesignInput(content=obedient))

    payload = json.loads(result.content[0].text)
    assert payload["design_digest"] == sha256(obedient.encode()).hexdigest()
    assert sandbox.writes[payload["path"]] == obedient.encode()


@pytest.mark.parametrize(
    ("failed_audit", "error_type", "message"),
    (
        (
            ExecResult("", "browser crashed", 1),
            RuntimeError,
            "Run the browser audit successfully",
        ),
        (
            ExecResult("", "browser timed out", 124),
            RuntimeError,
            "Run the browser audit successfully",
        ),
        (
            ExecResult("{", "", 0),
            RuntimeError,
            "application design audit returned malformed output",
        ),
        (ExecResult("[]", "", 0), ValueError, "design has 0 unique visible named regions"),
    ),
)
async def test_application_builder_design_audit_failure_leaves_design_repairable(
    tmp_path: Path,
    failed_audit: ExecResult,
    error_type: type[Exception],
    message: str,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/ufo-app",
        source_path="/workspace/ufo-app/app.tsx",
    )
    sandbox = FakeSandbox(design_audit=failed_audit)
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
    ctx = _application_design_context(ctx)

    with pytest.raises(error_type, match=message):
        await write_application_design(ctx, WriteApplicationDesignInput(content=APPLICATION_DESIGN))

    assert sandbox.programs == []
    assert sandbox.workspace_writes == []
    assert sandbox.shells[-1][2] == APPLICATION_DESIGN_AUDIT_TIMEOUT_SECONDS
    sandbox.design_audit = ExecResult(json.dumps(AUDIT_DESIGN_REGIONS), "", 0)

    result = await write_application_design(
        ctx, WriteApplicationDesignInput(content=APPLICATION_DESIGN)
    )

    payload = json.loads(result.content[0].text)
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()
    preview_path = await sandbox.runtime_path(
        APPLICATION_DESIGN_PREVIEW_RELATIVE.format(digest=payload["design_digest"])
    )
    assert sandbox.shells[-1][1][-1] == preview_path
    assert sandbox.writes[preview_path] == sandbox.design_preview


async def test_application_builder_accepts_one_corrected_native_design(tmp_path: Path) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    sandbox = FakeSandbox(
        design_audit=ExecResult(
            "",
            "Error: application design extends outside its viewBox: region=queue tag=text "
            'text="Queue" edge=right overflow=12px',
            1,
        ),
        track_design_claim=True,
    )
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )

    with pytest.raises(ValueError, match=r"region=queue.*edge=right.*overflow=12px"):
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(content=APPLICATION_DESIGN),
        )

    assert sandbox.programs == []
    assert sandbox.workspace_writes == []
    assert not sandbox.design_claimed
    sandbox.design_audit = ExecResult(json.dumps(AUDIT_DESIGN_REGIONS), "", 0)
    corrected = replace(
        ctx,
        idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-2",
    )
    result = await write_application_design(
        corrected,
        WriteApplicationDesignInput(content=APPLICATION_DESIGN),
    )

    payload = json.loads(result.content[0].text)
    assert payload["design_digest"] == sha256(APPLICATION_DESIGN.encode()).hexdigest()
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()


async def test_application_builder_accepts_one_corrected_internal_overlap_design(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    sandbox = FakeSandbox(
        design_audit=ExecResult(
            "",
            "Error: application design has accidental internal overlap: "
            'region=queue text="Suggested owner:" overlaps text="alex" by 6.5x13px',
            1,
        ),
        track_design_claim=True,
    )
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )

    with pytest.raises(
        ValueError,
        match=r"application design has accidental internal overlap:.*Suggested owner:.*6.5x13px",
    ):
        await write_application_design(
            ctx,
            WriteApplicationDesignInput(content=APPLICATION_DESIGN),
        )

    sandbox.design_audit = ExecResult(json.dumps(AUDIT_DESIGN_REGIONS), "", 0)
    corrected = replace(
        ctx,
        idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-2",
    )
    result = await write_application_design(
        corrected,
        WriteApplicationDesignInput(content=APPLICATION_DESIGN),
    )

    payload = json.loads(result.content[0].text)
    assert payload["design_digest"] == sha256(APPLICATION_DESIGN.encode()).hexdigest()
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()


@pytest.mark.parametrize(
    "effect",
    (
        '<clipPath id="crop"><rect width="20" height="20" /></clipPath>',
        '<mask id="fade"><rect width="20" height="20" /></mask>',
        '<filter id="blur"><feGaussianBlur stdDeviation="2" /></filter>',
        "<style>.queue { clip-path: inset(1px); }</style>",
    ),
)
async def test_application_builder_rejects_svg_effect_before_one_correction(
    tmp_path: Path, effect: str
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    sandbox = FakeSandbox()
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )
    design = APPLICATION_DESIGN.replace("</svg>", f"{effect}</svg>")

    with pytest.raises(
        ValueError,
        match="native bounds do not support clip, mask, or filter effects",
    ):
        await write_application_design(ctx, WriteApplicationDesignInput(content=design))

    assert sandbox.shells == []
    assert sandbox.programs == []
    assert sandbox.workspace_writes == []
    corrected = replace(
        ctx,
        idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-2",
    )
    result = await write_application_design(
        corrected,
        WriteApplicationDesignInput(content=APPLICATION_DESIGN),
    )

    payload = json.loads(result.content[0].text)
    assert payload["design_digest"] == sha256(APPLICATION_DESIGN.encode()).hexdigest()
    assert sandbox.writes[payload["path"]] == APPLICATION_DESIGN.encode()


def test_application_builder_design_uses_rendered_region_contract() -> None:
    names, kit_components, page_height = _validate_application_design(
        '<svg viewBox="0 0 305 844" width="305" height="844">'
        '<g transform="translate(183 0)"><g data-app-region="detail" '
        'data-kit-component="Card">'
        '<text y="100">Detail</text></g></g>'
        '<g data-app-region="queue"><g data-kit-component="Badge">'
        '<use href="#card" /></g><g data-kit-component="Card" /></g>'
        '<defs><symbol id="card"><path d="M0 0H122V844H0Z" /></symbol></defs>'
        "</svg>"
    )
    first = ApplicationAuditRegion(name="queue", left=0, top=0, width=0.6, height=1, aboveFold=True)
    within_slop = ApplicationAuditRegion(
        name="detail", left=0.59, top=0, width=0.4, height=1, aboveFold=True
    )
    beyond_slop = within_slop.model_copy(update={"left": 0.579})

    assert names == ("detail", "queue")
    assert kit_components == ("Card", "Badge")
    assert page_height == APPLICATION_DESIGN_FOLD
    assert application_region_relation(first, within_slop) == ("horizontal", -1)
    assert application_region_relation(first, beyond_slop) is None
    with pytest.raises(ValueError, match="active or external content"):
        _validate_application_design(
            '<svg viewBox="0 0 305 844" width="305" height="844">'
            '<g data-app-region="queue"><image href="https://example.com/a.png" '
            'width="183" height="844" /></g>'
            '<g data-app-region="detail"><rect x="183" width="122" height="844" /></g>'
            "</svg>"
        )
    source = APPLICATION_AUDIT_SCRIPT.decode()
    assert "await context.route(/^https?:/" in source


def test_application_builder_design_requires_the_native_lane() -> None:
    with pytest.raises(ValueError, match='viewBox="0 0 305 H"'):
        _validate_application_design(
            '<svg viewBox="0 0 1280 800" width="1280" height="800">'
            '<g data-app-region="queue"><rect width="640" height="800" /></g>'
            '<g data-app-region="detail"><rect x="640" width="640" height="800" /></g>'
            "</svg>"
        )

    assert _validate_application_design(APPLICATION_DESIGN) == (
        ("queue", "detail"),
        ("Card",),
        844,
    )


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        ("", "application design requires data-kit-component on at least one SVG g element"),
        (
            '<g data-kit-component="" />',
            "application design data-kit-component must name one visual ufo/kit export",
        ),
        (
            '<g data-kit-component="Card Badge" />',
            "application design data-kit-component must be one ComponentName",
        ),
        (
            '<g data-kit-component=" Card" />',
            "application design data-kit-component must be one ComponentName",
        ),
        (
            '<g data-kit-component="MadeUp" />',
            "application design data-kit-component 'MadeUp' is not a visual ufo/kit export",
        ),
        (
            '<rect data-kit-component="Card" width="1" height="1" />',
            "application design data-kit-component must be on an SVG g element",
        ),
    ],
)
def test_application_builder_design_requires_individual_kit_components(
    annotation: str, message: str
) -> None:
    design = (
        '<svg viewBox="0 0 305 844" width="305" height="844">'
        '<g data-app-region="queue"><rect width="183" height="844" /></g>'
        '<g data-app-region="detail"><rect x="183" width="122" height="844" /></g>'
        f"{annotation}</svg>"
    )

    with pytest.raises(ValueError) as error:
        _validate_application_design(design)

    assert str(error.value) == message


def test_ufo_style_uses_the_application_audit_narrow_width() -> None:
    skill = (
        Path(__file__).parents[3] / "core/src/ufo/runtime/skills/ufo-style/SKILL.md"
    ).read_text()

    assert f"narrow width checked at {NARROW_WIDTH}px" in skill
    assert "narrow width checked at 390px" not in skill


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
    first = _application_design_context(first)
    second = _application_design_context(
        replace(first, turn=first.turn.model_copy(update={"id": uuid4()}))
    )

    await write_application_design(
        first,
        WriteApplicationDesignInput(
            content=APPLICATION_DESIGN,
        ),
    )
    await write_application_design(
        second,
        WriteApplicationDesignInput(
            content=APPLICATION_DESIGN.replace("183", "170"),
        ),
    )

    claims = [args[4] for program, args in sandbox.programs if program == APPLICATION_DESIGN_ACCEPT]
    assert len(set(claims)) == 2
    assert b"170" in sandbox.writes["/workspace/application/application-design.svg"]


async def test_application_builder_same_turn_accepts_the_candidate_it_audited(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    first_design = APPLICATION_DESIGN
    second_design = APPLICATION_DESIGN.replace("183", "170")
    sandbox = FakeSandbox(
        track_design_claim=True,
        design_audit_barrier=asyncio.Barrier(2),
    )
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
    ctx = _application_design_context(ctx)

    results = await asyncio.gather(
        write_application_design(
            ctx,
            WriteApplicationDesignInput(content=first_design),
        ),
        write_application_design(
            ctx,
            WriteApplicationDesignInput(content=second_design),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, ToolResult) for result in results) == 1
    assert (
        sum(isinstance(result, ValueError) and "already fixed" in str(result) for result in results)
        == 1
    )
    candidates = {
        path: sandbox.writes[path]
        for path in sandbox.runtime_writes
        if path.endswith(".candidate.svg")
    }
    assert len(candidates) == 2
    assert set(candidates.values()) == {first_design.encode(), second_design.encode()}
    assert {path for path in sandbox.writes if path.endswith(".design.png")} == {
        f"{RUNTIME_ROOT}/"
        + APPLICATION_DESIGN_PREVIEW_RELATIVE.format(digest=sha256(design.encode()).hexdigest())
        for design in (first_design, second_design)
    }
    design_path = "/workspace/application/application-design.svg"
    accepted_path = (
        f"{RUNTIME_ROOT}/{application_design_acceptance_relative(design_path, ctx.turn.id)}"
    )
    assert sandbox.writes[accepted_path] == sandbox.writes[design_path]


async def test_application_builder_rejected_second_design_keeps_the_accepted_preview(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=APPLICATION_SOURCE_PATH,
    )
    accepted_design = APPLICATION_DESIGN
    rejected_design = APPLICATION_DESIGN.replace("183", "170")
    sandbox = FakeSandbox(track_design_claim=True)
    base = _context(sandbox, tmp_path)
    ctx = _application_design_context(
        replace(
            base,
            turn=base.turn.model_copy(
                update={
                    "inbound": task.model_dump_json(),
                    "subagent_profile": APPLICATION_BUILDER_NAME,
                }
            ),
        )
    )
    accepted_preview_path = f"{RUNTIME_ROOT}/" + APPLICATION_DESIGN_PREVIEW_RELATIVE.format(
        digest=sha256(accepted_design.encode()).hexdigest()
    )
    rejected_preview_path = f"{RUNTIME_ROOT}/" + APPLICATION_DESIGN_PREVIEW_RELATIVE.format(
        digest=sha256(rejected_design.encode()).hexdigest()
    )

    accepted = await write_application_design(
        ctx, WriteApplicationDesignInput(content=accepted_design)
    )

    assert json.loads(accepted.content[0].text)["design_digest"] == (
        sha256(accepted_design.encode()).hexdigest()
    )
    assert sandbox.writes[accepted_preview_path] == sandbox.design_preview
    sandbox.design_preview = b"\x89PNG rejected wireframe"

    with pytest.raises(ValueError, match="already fixed"):
        await write_application_design(
            replace(
                ctx,
                idempotency_key=f"{ctx.turn.id}/{APPLICATION_BUILDER_DESIGN_TOOL}/call-2",
            ),
            WriteApplicationDesignInput(content=rejected_design),
        )

    assert accepted_preview_path != rejected_preview_path
    assert sandbox.writes[accepted_preview_path] == b"\x89PNG application wireframe"
    assert sandbox.writes[rejected_preview_path] == b"\x89PNG rejected wireframe"


async def test_application_audit_uses_durable_turn_evidence_without_requesting_the_svg(
    tmp_path: Path,
) -> None:
    task = ApplicationBuilderTask(
        objective="Build the queue",
        scaffold_path=APPLICATION_SCAFFOLD_PATH,
        source_path=f"{APPLICATION_SCAFFOLD_PATH}/app.tsx",
    )
    first_design = APPLICATION_DESIGN
    second_design = APPLICATION_DESIGN.replace("183", "170")
    first_regions = tuple(
        ApplicationAuditRegion.model_validate(region) for region in AUDIT_DESIGN_REGIONS
    )
    report = json.dumps(
        {
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": [
                {
                    "scheme": scheme,
                    "width": width,
                    "textChecked": 2,
                    "text": [],
                    "documentWidth": width,
                    "clipped": [],
                    "overlaps": [],
                    "console": [],
                    "aboveFoldText": "Queue",
                    "regions": AUDIT_DESIGN_REGIONS,
                }
                for width in (DESKTOP_WIDTH, NARROW_WIDTH)
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
                "states": [["Queue"]],
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
    first = replace(
        base,
        turn=base.turn.model_copy(
            update={
                "inbound": task.model_dump_json(),
                "parent_turn_id": parent_turn_id,
                "subagent_profile": APPLICATION_BUILDER_NAME,
            }
        ),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    first = _application_design_context(first)
    second = _application_design_context(
        replace(first, turn=first.turn.model_copy(update={"id": uuid4()}))
    )

    await write_application_design(
        first,
        WriteApplicationDesignInput(content=first_design),
    )
    await write_application_design(
        second,
        WriteApplicationDesignInput(content=second_design),
    )

    first_accepted = (
        f"{RUNTIME_ROOT}/"
        f"{application_design_acceptance_relative(APPLICATION_DESIGN_PATH, first.turn.id)}"
    )
    second_accepted = (
        f"{RUNTIME_ROOT}/"
        f"{application_design_acceptance_relative(APPLICATION_DESIGN_PATH, second.turn.id)}"
    )
    first_evidence = (
        f"{RUNTIME_ROOT}/"
        f"{application_design_evidence_relative(APPLICATION_DESIGN_PATH, first.turn.id)}"
    )
    second_evidence = (
        f"{RUNTIME_ROOT}/"
        f"{application_design_evidence_relative(APPLICATION_DESIGN_PATH, second.turn.id)}"
    )
    assert sandbox.writes[first_accepted] == first_design.encode()
    assert sandbox.writes[second_accepted] == second_design.encode()
    assert (
        AcceptedApplicationDesignEvidence.model_validate_json(
            sandbox.writes[first_evidence]
        ).regions
        == first_regions
    )
    assert (
        AcceptedApplicationDesignEvidence.model_validate_json(
            sandbox.writes[second_evidence]
        ).design_sha256
        == sha256(second_design.encode()).hexdigest()
    )
    assert sandbox.writes[APPLICATION_DESIGN_PATH] == second_design.encode()

    audited = await _audit_builder_application(first, APPLICATION_SCAFFOLD_PATH)

    assert isinstance(audited, ApplicationAuditReport)
    assert audited.design_regions == first_regions
    fidelity = application_design_fidelity(audited)
    assert fidelity.passed == fidelity.total
    assert sandbox.tasks == []
    assert len(sandbox.shells[-1][1]) == 9
    audit_arguments = sandbox.shells[-1][1]
    assert first_accepted in audit_arguments
    assert all(second_accepted not in argument for argument in audit_arguments)
    assert first_evidence in audit_arguments
    assert all(second_evidence not in argument for argument in audit_arguments)

    reversed_regions = (
        {**AUDIT_DESIGN_REGIONS[0], "left": 0.6, "width": 0.4},
        {**AUDIT_DESIGN_REGIONS[1], "left": 0.0, "width": 0.6},
    )
    failed_report = json.loads(report)
    for view in failed_report["views"]:
        view["regions"] = reversed_regions
    sandbox.scripted_paths["/application-audit/"] = ExecResult(json.dumps(failed_report), "", 0)

    failed = await _audit_builder_application(first, APPLICATION_SCAFFOLD_PATH)

    assert isinstance(failed, ApplicationAuditFeedback)
    assert {issue.code for issue in failed.issues} == {"design"}


@pytest.mark.parametrize(
    ("fault", "message"),
    (
        ("missing", "accepted application design evidence is absent"),
        ("corrupt", "accepted application design evidence is invalid"),
        ("digest", "accepted application design evidence digest does not match the design"),
    ),
)
async def test_application_qa_fails_loud_on_invalid_durable_design_evidence(
    tmp_path: Path, fault: str, message: str
) -> None:
    sandbox = FakeSandbox()
    store = FakeHookStore()
    base = _context(sandbox, tmp_path)
    ctx = replace(
        base,
        turn=base.turn.model_copy(update={"subagent_profile": APPLICATION_BUILDER_NAME}),
        ext=cast(ExtensionContext, FakeHookExt(store)),
    )
    _seed_accepted_application_design(sandbox, ctx.turn.id)
    accepted_path = (
        f"{RUNTIME_ROOT}/"
        f"{application_design_acceptance_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)}"
    )
    evidence_path = (
        f"{RUNTIME_ROOT}/"
        f"{application_design_evidence_relative(APPLICATION_DESIGN_PATH, ctx.turn.id)}"
    )
    match fault:
        case "missing":
            del sandbox.writes[evidence_path]
        case "corrupt":
            sandbox.writes[evidence_path] = b"{"
        case "digest":
            sandbox.writes[accepted_path] += b" "

    with pytest.raises(RuntimeError, match=message):
        await qa_ufo_application(ctx, QaUfoApplicationInput())

    assert sandbox.tasks == []
    assert sandbox.shells == []


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
    first = _application_design_context(first)
    await write_application_design(
        first,
        WriteApplicationDesignInput(
            content=APPLICATION_DESIGN,
        ),
    )
    second = replace(first, turn=first.turn.model_copy(update={"id": uuid4()}))
    design_path = "/workspace/application/application-design.svg"
    second_claim = (
        f"{RUNTIME_ROOT}/tool-output/application-builder/"
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

    assert (
        APPLICATION_SOURCE_REQUIRE_CLAIM,
        (second_claim, RUNTIME_ROOT),
    ) in sandbox.programs


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
        'import { Card, mountApp } from "ufo/kit";\nmountApp(document.getElementById("root")!, '
        '() => <Card><main style={{ backgroundColor: "var(--color-ink)", '
        'color: "var(--color-surface)" }} /></Card>);'
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
    check_root = f"{RUNTIME_ROOT}/tool-output/application-builder/{ctx.turn.id}/project"
    assert f"{check_root}/vite.config.ts" in sandbox.runtime_writes
    assert f"{check_root}/sdk.tar.gz" in sandbox.runtime_writes
    assert not any(path.startswith(RUNTIME_ROOT) for path in sandbox.workspace_writes)
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


async def test_application_builder_write_tool_requires_each_designed_kit_component(
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

    with pytest.raises(ValueError) as error:
        await write_application_source(
            ctx,
            WriteApplicationSourceInput(
                content=(
                    'import { Group, mountApp } from "ufo/kit";\n'
                    'mountApp(document.getElementById("root")!, () => <Group />);'
                )
            ),
        )

    assert str(error.value).startswith(
        "app.tsx must directly render designed Kit component: Card Candidate retained;"
    )


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
        'import { Card, mountApp } from "ufo/kit";\n'
        "function App() { return <main><h1>Today's queue</h1>"
        "<Card>Draft the brief</Card><p>Nothing's overdue</p></main>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )

    await write_application_source(
        ctx,
        WriteApplicationSourceInput(content=source),
    )

    assert sandbox.writes["/workspace/application/app.tsx"] == source.encode()


def test_application_source_rejects_literal_white_on_scheme_ink() -> None:
    source = (
        'import { Group, mountApp } from "ufo/kit";\n'
        "function App() { return <Group><button style={{\n"
        '  backgroundColor: active ? "var(--color-ink)" : "var(--color-field)",\n'
        '  color: active ? "#FFFFFF" : "var(--color-ink)",\n'
        "}}>Review</button></Group>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )

    with pytest.raises(ValueError, match="must use --color-surface text"):
        _validate_application_source(source)

    _validate_application_source(source.replace('"#FFFFFF"', '"var(--color-surface)"'))


def _page(body: str) -> str:
    return (
        'import { Group, mountApp } from "ufo/kit";\n'
        f"function App() {{ return <Group>{body}</Group>; }}\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )


def test_application_source_kit_contract_matches_three_app_evidence() -> None:
    root = Path(__file__).parents[3]
    kit = (root / "extensions/web/frontend/src/apps/kit.ts").read_text()
    exports = set(re.findall(r"(?m)^  ([A-Z][A-Za-z0-9]*),$", kit.rsplit("export {", 1)[1]))
    assert APPLICATION_KIT_COMPONENTS <= exports
    meeting_tasks = (
        root / "extensions/app_meetings/ufo_ext_app_meetings/skills/app-meetings-home/app.tsx"
    )
    _validate_application_source(meeting_tasks.read_text())

    for name in ("issue-owner", "pre-meeting-briefs"):
        source = root / f"evals/fixtures/ufo_app_qa_replay/{name}/app.tsx"
        with pytest.raises(ValueError, match="render at least one UI component"):
            _validate_application_source(source.read_text())


@pytest.mark.parametrize(
    "imports",
    [
        "{ mountApp, useState }",
        "type { Card }",
        "{ mountApp, Card }",
        "{ mountApp, Card as MeetingCard }",
    ],
)
def test_application_source_rejects_kit_imports_without_a_rendered_component(
    imports: str,
) -> None:
    mount = 'mountApp(document.getElementById("root")!, () => <main />);'
    runtime = 'import { mountApp } from "ufo/kit";\n' if "mountApp" not in imports else ""
    source = f'import {imports} from "ufo/kit";\n{runtime}{mount}'

    with pytest.raises(ValueError, match="render at least one UI component"):
        _validate_application_source(source)


def test_application_source_accepts_a_rendered_aliased_kit_component_and_hooks() -> None:
    _validate_application_source(
        'import { Card as MeetingCard, mountApp, useState } from "ufo/kit";\n'
        "function App() { const [open] = useState(true); "
        "return open ? <MeetingCard>Ready</MeetingCard> : null; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )


def test_application_source_renders_each_designed_kit_component_through_aliases() -> None:
    source = (
        'import { Badge as State, Card as Panel, mountApp } from "ufo/kit";\n'
        "function App() { return <Panel><State>Ready</State></Panel>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )

    _validate_application_source(source, ("Card", "Badge"))

    with pytest.raises(ValueError) as error:
        _validate_application_source(
            source.replace("<State>Ready</State>", "Ready"), ("Card", "Badge")
        )

    assert str(error.value) == "app.tsx must directly render designed Kit component: Badge"


def test_application_source_rejects_namespace_and_local_look_alike_components() -> None:
    namespace = (
        'import * as Kit from "ufo/kit";\n'
        'import { mountApp } from "ufo/kit";\n'
        'mountApp(document.getElementById("root")!, () => <Kit.Card />);'
    )
    with pytest.raises(ValueError, match="use named imports"):
        _validate_application_source(namespace)

    look_alike = (
        'import { mountApp, useState } from "ufo/kit";\n'
        "function Card({ children }: { children: unknown }) { return <div>{children}</div>; }\n"
        "function App() { useState(false); return <Card>Ready</Card>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    with pytest.raises(ValueError, match="render at least one UI component"):
        _validate_application_source(look_alike)

    shadowed_alias = (
        'import { Card as KitCard, mountApp } from "ufo/kit";\n'
        "function App() { function KitCard() { return <main />; } return <KitCard />; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    with pytest.raises(ValueError, match="render at least one UI component"):
        _validate_application_source(shadowed_alias)

    parameter_alias = (
        'import { Card as KitCard, mountApp } from "ufo/kit";\n'
        "const LocalCard = () => <main />;\n"
        "function App(KitCard = LocalCard) { return <KitCard />; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    with pytest.raises(ValueError, match="render at least one UI component"):
        _validate_application_source(parameter_alias)


def test_application_source_rejects_a_destructured_local_kit_shadow() -> None:
    source = (
        'import { Card, mountApp } from "ufo/kit";\n'
        "const local = { Card: () => <main /> };\n"
        "function App() { const { Card } = local; return <Card />; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )

    with pytest.raises(ValueError, match="render at least one UI component"):
        _validate_application_source(source)


@pytest.mark.parametrize(
    "evidence",
    [
        'const unused = Card; const example = "<Card />";',
        "const unused = Card; /* <Card /> */",
    ],
)
def test_application_source_does_not_count_non_jsx_component_evidence(evidence: str) -> None:
    source = (
        'import { Card, mountApp } from "ufo/kit";\n'
        f"{evidence}\n"
        'mountApp(document.getElementById("root")!, () => <main />);'
    )

    with pytest.raises(ValueError, match="render at least one UI component"):
        _validate_application_source(source)


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        ('<p className="w-[3px]">x</p>', "names a raw value"),
        ('<p className="text-[#676767]">x</p>', "names a raw value"),
        ('<p className="[color:#676767]">x</p>', "names a raw value"),
        ('<p className="flex gap-lg">x</p>', "is not a composition step"),
        ('<p className="flex gap-px">x</p>', "is not a composition step"),
        ('<p className="flex gap-0">x</p>', "is not a composition step"),
        ('<p className="flex gap-4">x</p>', "is not a composition step"),
        ('<p className="flex gap-[10px]">x</p>', "names a raw value"),
        ('<Stat className="border border-edge">x</Stat>', "a Stat carries a border"),
        ('<div className="space-y-2">x</div>', "stack with flex and a gap"),
        ('<p className="dark:text-ink">x</p>', "carries itself"),
        ("<style>{`p{color:red}`}</style>", "may not emit a <style> tag"),
        ("<p className={`flex ${wide}`}>x</p>", "compose classes with cn()"),
    ],
)
def test_application_source_holds_a_generated_page_to_the_shipped_page_rules(
    body: str, refusal: str
) -> None:
    """A shipped app page is walked by `gates.py` in the repo; a generated one exists only in a
    member's sandbox, so this validator is the one place the same rules can be true of it. Each
    refusal is worded as the repair, because the builder's own repair loop is what reads it."""
    with pytest.raises(ValueError, match=re.escape(refusal)):
        _validate_application_source(_page(body))


def test_application_source_accepts_a_page_that_follows_the_rules() -> None:
    _validate_application_source(
        _page('<p className="flex gap-2xl rounded-card bg-fill text-label text-ink-quiet">x</p>')
    )
    # The rule is the tile's own frame, not the word: a part of the stat may be bordered, and a
    # Stat that spaces itself is what the rule asks for.
    _validate_application_source(
        _page('<Stat className="flex gap-sm"><StatValue>1</StatValue></Stat>')
    )
    _validate_application_source(_page('<StatLabel className="border-b border-edge">x</StatLabel>'))


def test_application_source_reserves_kit_slot_ownership() -> None:
    with pytest.raises(ValueError, match="data-slot is reserved for ufo/kit components"):
        _validate_application_source(_page('<p data-slot="stat-label">x</p>'))

    _validate_application_source(
        _page("<Stat><StatLabel>x</StatLabel><StatValue>1</StatValue></Stat>").replace(
            "{ mountApp }", "{ mountApp, Stat, StatLabel, StatValue }"
        )
    )


@pytest.mark.parametrize(
    "data",
    [
        'const REFS = ["#2040", "#2051"];',
        'const SHARES = ["41.2%", "22.8%"];',
        'const ROWS = [{ ref: "#12", pct: "80%" }];',
        "const first = items[0];",
    ],
)
def test_application_source_reads_a_members_own_data_as_data(data: str) -> None:
    """An issue reference and a percentage are spelled exactly like a raw colour and a raw length,
    and a page carries them as its content. The refusal is about a Tailwind arbitrary value, which
    is always a utility holding one or an arbitrary property — never a bare `[...]`, which is a
    JavaScript array. Reading the whole file for brackets refuses a page over its own data, and
    tells the builder's repair loop to resolve an issue number through a theme token."""
    _validate_application_source(_page("<p>x</p>").replace("function App", data + "\nfunction App"))


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
        f"{RUNTIME_ROOT}/tool-output/application-builder/"
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
    source = (
        'import { Card, mountApp } from "ufo/kit";\n'
        "function App() { return <Card />; }\n"
        "mountApp(App);"
    )
    replacement = 'mountApp(document.getElementById("root")!, () => <App />);'
    corrected = source.replace("mountApp(App);", replacement)
    sandbox = FakeSandbox(
        scripted_paths={
            "/workspace/application/application-design.svg": ExecResult(APPLICATION_DESIGN, "", 0)
        },
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
        'import { Card, mountApp } from "ufo/kit";\n'
        "function App() { return <Card><main><section>Old</section></main></Card>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    sandbox = FakeSandbox(
        scripted_paths={
            "/workspace/application/application-design.svg": ExecResult(APPLICATION_DESIGN, "", 0)
        },
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
    sandbox = FakeSandbox(
        scripted_paths={
            "/workspace/application/application-design.svg": ExecResult(APPLICATION_DESIGN, "", 0)
        },
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

    for entry in await registry.materialize(registry.closure("website-building")):
        await install_skill(_SkillSandbox(written), entry.skill)
    assert f"$UFO_HOME/skills/{HOUSE_STYLE}/{HOUSE_STYLE_TOKENS}" in written

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
        {tool.canonical_id for tool in SITES_TOOLS}
        | {tool.name for tool in BUILTIN_TOOLS}
        | {JS_REPL_TOOL, XLSX_REPL_TOOL}
        | {SEARCH_WEB_TOOL, SEARCH_VERTICAL_TOOL, FETCH_URL_TOOL}
    )
    assert names <= available
    # Named, not merely resolvable: the containment above passes just as well with a tool dropped,
    # and the two REPLs are what the child drives a page and a workbook with.
    assert {JS_REPL_TOOL, XLSX_REPL_TOOL} <= names
    assert {
        "website",
        "start_server",
        "write",
        "js_repl",
        "action:site:deploy_website",
        "action:agent:set_homepage",
    } <= names
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
            "exec env PORT": ExecResult(stdout="", stderr="", exit_code=1),
            "tail -n 20": ExecResult(stdout="Traceback: port in use", stderr="", exit_code=0),
        }
    )
    ctx = _context(sandbox, tmp_path)
    with pytest.raises(RuntimeError, match="port in use"):
        await start_server(
            ctx,
            StartServerInput(command="python3 app.py", project_path="/workspace"),
        )


async def test_start_server_stops_its_task_when_readiness_fails(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"deadline = time.time()": ExecResult(stdout="", stderr="not ready", exit_code=1)}
    )
    ctx = _context(sandbox, tmp_path)

    with pytest.raises(RuntimeError, match="not ready"):
        await start_server(
            ctx,
            StartServerInput(command="python3 app.py", project_path="/workspace"),
        )

    started, stopped = sandbox.tasks
    assert started[1] == stopped[1]
    assert started[2] is True
    assert stopped[2] is False
    assert sandbox.shells == [
        (
            sites_tools.SERVER_TASK_RESET,
            (started[1], str(sites_tools.APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS)),
            sites_tools.APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS + 5,
        ),
        (
            sites_tools.SERVER_TASK_STOP,
            ("123", started[1]),
            sites_tools.APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS,
        ),
    ]


async def test_a_second_start_in_one_turn_starts_its_own_server(tmp_path: Path) -> None:
    """One turn starts a server on the same port and log more than once — three product audits are
    permitted, and each one starts the audit server again — so the task identity repeats by design.
    `ufo run --task` reattaches to a journal that holds a pid and launches nothing, and the port is
    free by then, so the second start serves only because the journal it lands on is cleared."""
    sandbox = JournalSandbox()
    ctx = _context(sandbox, tmp_path)
    args = StartServerInput(command="python3 app.py", project_path="/workspace", port=5173)

    first = json.loads((await start_server(ctx, args)).content[0].text)
    second = json.loads((await start_server(ctx, args)).content[0].text)

    base = sandbox.tasks[0][1]
    assert [task[1] for task in sandbox.tasks] == [base, base]
    assert sandbox.launches == [base, base]
    assert first["url"] == second["url"] == "http://localhost:5173"


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
    assert sandbox.tasks == [
        (
            "cd /workspace/site\n"
            "set -C\n"
            "exec env PORT=5173 bash -lc 'python3 -m http.server 5173 --bind 0.0.0.0' "
            f">{RUNTIME_ROOT}/tool-output/server-5173.log 2>&1",
            f"{RUNTIME_ROOT}/tool-output/server-tasks/"
            f"{sha256(f'{ctx.turn.id}:5173:{RUNTIME_ROOT}/tool-output/server-5173.log'.encode()).hexdigest()[:16]}",
            True,
            READINESS_TIMEOUT_SECONDS + 5,
        )
    ]


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
            "exec env PORT": ExecResult(stdout="", stderr="", exit_code=1),
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
            "exec env PORT": ExecResult(
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


async def test_a_server_log_defaults_into_the_runs_own_offload_dir(tmp_path: Path) -> None:
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
    log = f"{RUNTIME_ROOT}/tool-output/server-5173.log"
    assert payload["log"] == log
    assert payload["project_path"] == "/workspace/site"
    assert sandbox.programs == [
        (LOG_CLEAR_PROG, (log, RUNTIME_ROOT)),
        (PORT_STOP_PROG, ("5173",)),
    ]
    launch = next(
        command for command, _base, _detach, _timeout in sandbox.tasks if f">{log}" in command
    )
    redirect = launch.index(f">{log}")
    assert "set -C\n" in launch[:redirect], "the redirect must create the log, never truncate it"
    assert "exec env PORT=5173 bash -lc" in launch[:redirect]


async def test_the_log_name_is_freed_through_the_guard_before_the_redirect_creates_it(
    tmp_path: Path,
) -> None:
    """`exec … >log` follows a link and truncates what it points at, and the log's name is a
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
    shot = f"{RUNTIME_ROOT}/tool-output/share-card-shot-marketing.png"
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
    shot = f"{RUNTIME_ROOT}/tool-output/preview-8000.png"
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
