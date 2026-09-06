import asyncio
import json
import re
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
from ufo_ext_sites import tools as sites_tools
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_ATTEMPT_KEY,
    APPLICATION_AUDIT_TURN_CONTRACT_KEY,
    APPLICATION_DESIGN_FOLD,
    APPLICATION_DESIGN_MAX_HEIGHT,
    APPLICATION_REGION_MIN_AREA,
    APPLICATION_REGION_MIN_HEIGHT,
    APPLICATION_REGION_MIN_WIDTH,
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
    BuildUfoApplicationInput,
    DesignUfoApplicationInput,
    EditApplicationSourceInput,
    ReadApplicationSourceInput,
    WriteApplicationDesignInput,
    WriteApplicationSourceInput,
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
from ufo_ext_sites.delegation import BuildWebsiteInput
from ufo_ext_sites.objects import (
    CONVERSATION_DIGEST_HEX,
    site_name_from_object,
    site_object_name,
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
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from ufo_ext_sites.tools import (
    APPLICATION_AUDIT_MAX_ATTEMPTS,
    LOG_CLEAR_PROG,
    LOG_TAIL_TIMEOUT_SECONDS,
    PORT_STOP_PROG,
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
    qa_ufo_application,
    start_server,
    website,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import (
    ExecResult,
    SandboxHandle,
    SandboxProviderUnavailable,
)
from ufo.harness.sandbox.terminal import TerminalAbsent
from ufo.host.ext.loader import skill_registry
from ufo.host.tools.builtins import BUILTIN_TOOLS, LoadSkillInput
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.media.previews import StoredPreview
from ufo.runtime.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.runtime.subagents import subagent_system_prompt
from ufo.runtime.tools.context import SpawnResult, SpeakerRequired, ToolContext
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import INTENT_ADMISSION, MEMBER_ADMISSION, Agent, ToolIntent, Turn
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience
from ufo.sdk.manifest import Deny, HookContext, PreToolUse
from ufo.sdk.sandbox import WORKSPACE_DIR

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

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
    shell_error: BaseException | None = None
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
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
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
        if self.shell_error is not None:
            raise self.shell_error
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
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        result = await super().bash_task(
            command, base, detach=detach, model_authored=model_authored, timeout_s=timeout_s
        )
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
    with pytest.raises(SpeakerRequired, match="only a member speaking"):
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


def test_website_building_parent_keeps_its_own_subdirs_but_not_the_child_subtree() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    parent_files = {path for path, _ in registry.named("website-building").files}
    assert any(path.startswith("game/") for path in parent_files)
    assert any(path.startswith("shared/") for path in parent_files)
    assert any(path.startswith("informational/") for path in parent_files)
    assert not any(path.startswith("webapp/") for path in parent_files)


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
    """A failed compile keeps the candidate and leaves the served source alone. Told only that
    the compile failed, the next call re-sends the same `old_text` against a candidate whose
    text its own last call already replaced, and meets "must occur exactly once"."""
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
    result = await edit_application_source(
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

    assert result.is_error is True
    assert "/workspace/application/app.tsx" not in sandbox.writes
    (candidate,) = [path for path in sandbox.writes if path.endswith(".candidate.tsx")]
    failure = json.loads(result.content[0].text)
    assert "does not compile: Unexpected closing tag" in failure["summary"]
    assert failure["applied"] == [
        {
            "kind": "candidate",
            "identity": candidate,
            "state": "holds the edited source, uncompiled",
        },
        {
            "kind": "served_source",
            "identity": "/workspace/application/app.tsx",
            "state": "unchanged: still the source from before this call",
        },
    ]


@pytest.mark.parametrize(
    "escape",
    (
        SandboxProviderUnavailable("e2b control plane did not recover"),
        TerminalAbsent("no terminal answered"),
    ),
    ids=("provider", "terminal"),
)
async def test_a_gone_sandbox_is_not_reported_as_a_source_that_does_not_compile(
    tmp_path: Path, escape: BaseException
) -> None:
    """The compile reaches the carrier, so both escapes arrive here. The engine parks the turn on
    one and ends it on the other; caught and rendered as an edit failure, neither happens and the
    builder keeps editing against a box that is gone."""
    task = ApplicationBuilderTask(
        objective="Repair the queue",
        scaffold_path="/workspace/application",
        source_path="/workspace/application/app.tsx",
    )
    source = (
        'import { Card, mountApp } from "ufo/kit";\n'
        "function App() { return <Card><main>Old</main></Card>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    sandbox = FakeSandbox(
        scripted_paths={
            "/workspace/application/application-design.svg": ExecResult(APPLICATION_DESIGN, "", 0)
        },
        claim=ExecResult(stdout=source, stderr="", exit_code=0),
        shell_error=escape,
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

    with pytest.raises(type(escape)):
        await edit_application_source(
            ctx,
            EditApplicationSourceInput(
                edits=(ApplicationSourceEdit(old_text="Old", new_text="New"),),
            ),
        )


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


def test_the_qa_guidance_cleans_its_profile_without_a_forced_rm() -> None:
    """The model runs this launch block verbatim, and a client carrier refuses a forced `rm` before
    it spawns. The profile directory is this run's own and always exists, so `rm -r` removes it and
    keeps the block runnable on a member's own machine."""
    for guidance in (_playwright_guidance(), _application_qa_guidance()):
        launch = next(
            block
            for block in re.findall(r"```\n(.*?)```", guidance, re.S)
            if "--headless=new" in block
        )
        assert "trap 'rm -r \"$PROFILE\"' EXIT INT TERM" in launch
        assert "rm -rf" not in launch


async def test_website_build_failure_keeps_the_exit_code_and_both_streams(
    tmp_path: Path,
) -> None:
    """A failed build hands back the code it exited on and everything it said. A toolchain writes
    its reason to stdout as readily as to stderr, so neither stream stands in for the other."""
    sandbox = FakeSandbox(
        scripted={
            "npm run build": ExecResult(stdout="4 warnings", stderr="build broke", exit_code=2)
        }
    )
    ctx = _context(sandbox, tmp_path)
    result = await website(ctx, WebsiteInput(run_command="npm run build"))
    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert failure["operation"] == "npm run build in /workspace"
    assert "exited 2" in failure["summary"]
    assert failure["command"] == {
        "exit_code": 2,
        "stdout": "4 warnings",
        "stderr": "build broke",
    }
    assert failure["applied"] == []


async def test_a_build_the_sandbox_stopped_says_so_rather_than_naming_its_exit_code(
    tmp_path: Path,
) -> None:
    """A command running `timeout` exits 124 exactly as a carrier-stopped one does, and "your
    build is broken" and "your build needs longer" want opposite fixes."""
    sandbox = FakeSandbox(
        scripted={
            "npm run build": ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=900)
        }
    )
    result = await website(_context(sandbox, tmp_path), WebsiteInput(run_command="npm run build"))
    failure = json.loads(result.content[0].text)
    assert "stopped the build after 900s" in failure["summary"]
    assert failure["command"]["timed_out_after_s"] == 900


async def test_start_server_reports_a_serve_failure_from_the_log(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={
            "exec env PORT": ExecResult(stdout="", stderr="", exit_code=1),
            "tail -n 20": ExecResult(stdout="Traceback: port in use", stderr="", exit_code=0),
        }
    )
    ctx = _context(sandbox, tmp_path)
    result = await start_server(
        ctx,
        StartServerInput(command="python3 app.py", project_path="/workspace"),
    )
    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert "Traceback: port in use" in failure["summary"]
    assert failure["command"]["exit_code"] == 1
    assert failure["applied"] == [
        {
            "kind": "port",
            "identity": str(sites_tools.START_SERVER_PORT),
            "state": "freed: whatever was serving it was stopped before this start",
        }
    ]


async def test_a_probe_the_sandbox_stopped_says_whose_deadline_fired(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={
            "deadline = time.time()": ExecResult(
                stdout="", stderr="timed out", exit_code=124, timed_out_after_s=35
            )
        }
    )
    ctx = _context(sandbox, tmp_path)
    result = await start_server(
        ctx,
        StartServerInput(command="python3 app.py", project_path="/workspace"),
    )
    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert "stopped the readiness check after 35s" in failure["summary"]
    assert failure["summary"] != "timed out"
    assert failure["command"]["timed_out_after_s"] == 35


async def test_a_start_that_fails_silently_names_the_command_and_its_log(tmp_path: Path) -> None:
    sandbox = FakeSandbox(scripted={"exec env PORT": ExecResult(stdout="", stderr="", exit_code=1)})
    ctx = _context(sandbox, tmp_path)
    result = await start_server(
        ctx,
        StartServerInput(command="python3 app.py", project_path="/workspace"),
    )
    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert "'python3 app.py' failed with no output" in failure["summary"]
    assert "server-5000.log" in failure["summary"]
    assert failure["command"]["exit_code"] == 1


async def test_start_server_stops_its_task_when_readiness_fails(tmp_path: Path) -> None:
    sandbox = FakeSandbox(
        scripted={"deadline = time.time()": ExecResult(stdout="", stderr="not ready", exit_code=1)}
    )
    ctx = _context(sandbox, tmp_path)

    result = await start_server(
        ctx,
        StartServerInput(command="python3 app.py", project_path="/workspace"),
    )
    assert result.is_error is True
    assert json.loads(result.content[0].text)["command"]["stderr"] == "not ready"

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
    result = await start_server(
        ctx,
        StartServerInput(command="python3 app.py", project_path="/workspace"),
    )
    assert result.is_error is True
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
    result = await start_server(
        ctx,
        StartServerInput(command="python3 app.py", project_path="/workspace"),
    )
    assert result.is_error is True
    failure = json.loads(result.content[0].text)
    assert f"stopped the start after {READINESS_TIMEOUT_SECONDS + 5}s" in failure["summary"]
    assert failure["command"]["timed_out_after_s"] == READINESS_TIMEOUT_SECONDS + 5


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


def test_start_server_rejects_an_out_of_range_port() -> None:
    with pytest.raises(ValidationError, match="between 1 and 65535"):
        StartServerInput(
            command="python3 app.py",
            project_path="/workspace",
            port=99999,
        )


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


def test_website_building_indexes_the_parent_and_the_webapp_route() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    index = dict(registry.index())
    assert "website-building" in index
    assert index["website-building/webapp"].startswith(
        "Load when building a full-stack browser application"
    )
    assert len(index["website-building/webapp"].split()) <= 50
