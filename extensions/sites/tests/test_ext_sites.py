import json
import re
from base64 import urlsafe_b64decode
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from ufo_ext_repl.manifest import JS_REPL_TOOL, XLSX_REPL_TOOL
from ufo_ext_research.tools import FETCH_URL_TOOL, SEARCH_VERTICAL_TOOL, SEARCH_WEB_TOOL
from ufo_ext_sites import manifest as sites_manifest
from ufo_ext_sites import tools as sites_tools
from ufo_ext_sites.application_audit import (
    APPLICATION_DESIGN_FOLD,
    APPLICATION_DESIGN_MAX_HEIGHT,
    APPLICATION_DESIGN_WIDTH,
    APPLICATION_REGION_MIN_AREA,
    APPLICATION_REGION_MIN_HEIGHT,
    APPLICATION_REGION_MIN_WIDTH,
    COMPOSITION_STEPS,
    DESKTOP_WIDTH,
    KIT_QUIET_TEXT_MIN,
    MAX_MESSAGE_CHARS,
    MEASURED_VIEWS,
    NARROW_WIDTH,
    PAGE_CLASS_REFUSALS,
    ApplicationAuditRegion,
    ApplicationAuditReport,
    ApplicationDesign,
    ApplicationDesignFidelity,
    application_design_fidelity,
    application_design_region_fold_failure,
    application_design_region_size_failure,
    application_region_failure,
    application_region_relation,
    audit_application,
    validate_application_design,
    validate_application_source,
)
from ufo_ext_sites.application_homepage import (
    APPLICATION_AUDIT_SCRIPT_PATH,
    APPLICATION_BUILDER_NAME,
    APPLICATION_BUILDER_PROFILE,
    APPLICATION_BUILDER_ROUND_LIMIT,
    APPLICATION_DESIGN_PATH,
    APPLICATION_HOMEPAGE_SKILL,
    APPLICATION_SKILL_DIR,
    APPLICATION_TEMPLATE_DIR,
    PAGE_REFUSAL_OPENING,
    ApplicationBuildResult,
)
from ufo_ext_sites.delegation import BuildWebsiteInput
from ufo_ext_sites.objects import (
    CONVERSATION_DIGEST_HEX,
    site_name_from_object,
    site_object_name,
)
from ufo_ext_sites.source import PROJECT_FILE_ABSENT, PROJECT_FILE_READ
from ufo_ext_sites.store import (
    SITE_NAME_MAX,
    InvalidSiteName,
    site_name,
)
from ufo_ext_sites.subagent import WEBSITE_BUILDING_PROFILE
from ufo_ext_sites.tools import (
    LOG_CLEAR_PROG,
    LOG_TAIL_TIMEOUT_SECONDS,
    PORT_STOP_PROG,
    READINESS_TIMEOUT_SECONDS,
    SITES_TOOLS,
    TOOL_OUTPUT_DIR,
    DeployWebsiteInput,
    PublishWebsiteInput,
    SetHomepageInput,
    StartServerInput,
    start_server,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import (
    ExecResult,
    SandboxHandle,
)
from ufo.host.ext.loader import skill_registry
from ufo.host.tools.builtins import BUILTIN_TOOLS
from ufo.runtime.object_name import OBJECT_NAME_MAX_LENGTH
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience

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
APPLICATION_DESIGN = """<svg viewBox="0 0 360 844" width="360" height="844">
<g data-app-region="queue"><g data-kit-component="Card"><rect width="216" height="844" /></g></g>
<g data-app-region="detail"><rect x="216" width="144" height="844" /></g>
</svg>"""
APPLICATION_SOURCE = """import { mountApp, Card } from "ufo/kit";

function App() {
  return (
    <div>
      <div data-app-region="queue"><Card /></div>
      <div data-app-region="detail" />
    </div>
  );
}

mountApp(document.getElementById("root")!, () => <App />);
"""
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
    program_errors: dict[str, BaseException] = field(default_factory=dict)
    shell_error: BaseException | None = None
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
        if program in self.program_errors:
            raise self.program_errors[program]
        for needle, result in self.scripted_paths.items():
            if args and needle in args[0]:
                return result
        for needle, result in self.scripted_programs.items():
            if needle in program:
                return result
        readable = (PROJECT_FILE_READ, sites_tools.APPLICATION_AUDIT_REPORT_READ)
        if program in readable and args:
            held = self.writes.get(args[0])
            if held is None:
                return ExecResult("", "", PROJECT_FILE_ABSENT)
            return ExecResult(held.decode(), "", 0)
        return self.claim

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        self.shells.append((script, args, timeout_s))
        if self.shell_error is not None:
            raise self.shell_error
        for needle, result in self.scripted_shells.items():
            if needle in script:
                return result
        for needle, result in self.scripted_shells.items():
            if any(needle in argument for argument in args):
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


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            DeployWebsiteInput,
            {"project_path": "/workspace/dist", "site_name": "s", "entry_point": "index.html"},
        ),
        (
            PublishWebsiteInput,
            {"project_path": "/workspace/app", "app_name": "a", "run_command": "node s.js"},
        ),
        (SetHomepageInput, {"site": "s-0011223344556677"}),
        (BuildWebsiteInput, {"objective": "build a page"}),
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


def _side_by_side_bands(page_height: int) -> tuple[dict[str, object], ...]:
    """Two 60 px bands 100 px down one page, side by side in the 360 px lane."""
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


def test_website_building_parent_keeps_its_own_subdirs_but_not_the_child_subtree() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    parent_files = {path for path, _ in registry.named("website-building").files}
    assert any(path.startswith("game/") for path in parent_files)
    assert any(path.startswith("shared/") for path in parent_files)
    assert any(path.startswith("informational/") for path in parent_files)
    assert not any(path.startswith("webapp/") for path in parent_files)


def _page(body: str) -> str:
    return (
        'import { Group, mountApp } from "ufo/kit";\n'
        f"function App() {{ return <Group>{body}</Group>; }}\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )


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
    available = (
        {tool.canonical_id for tool in SITES_TOOLS}
        | {tool.name for tool in BUILTIN_TOOLS}
        | {JS_REPL_TOOL, XLSX_REPL_TOOL}
        | {SEARCH_WEB_TOOL, SEARCH_VERTICAL_TOOL, FETCH_URL_TOOL}
    )
    assert names <= available
    assert {JS_REPL_TOOL, XLSX_REPL_TOOL} <= names
    assert {
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


def test_a_build_failure_keeps_the_exit_code_and_both_streams() -> None:
    """A failed build hands back the code it exited on and everything it said. A toolchain writes
    its reason to stdout as readily as to stderr, so neither stream stands in for the other."""

    failure = sites_tools._build_failed(
        "npm run build",
        "/workspace",
        ExecResult(stdout="4 warnings", stderr="build broke", exit_code=2),
    )

    assert failure.operation == "npm run build in /workspace"
    assert "exited 2" in failure.summary
    assert failure.command is not None
    assert failure.command.stdout == "4 warnings"
    assert failure.command.stderr == "build broke"


def test_a_build_the_sandbox_stopped_says_so_rather_than_naming_its_exit_code() -> None:
    """A command running `timeout` exits 124 exactly as a carrier-stopped one does, and "your
    build is broken" and "your build needs longer" want opposite fixes."""

    failure = sites_tools._build_failed(
        "npm run build",
        "/workspace",
        ExecResult(stdout="", stderr="", exit_code=124, timed_out_after_s=900),
    )

    assert "stopped the build after 900s" in failure.summary
    assert failure.command is not None
    assert failure.command.timed_out_after_s == 900


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


def test_website_building_indexes_the_parent_and_the_webapp_route() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    index = dict(registry.index())
    assert "website-building" in index
    assert index["website-building/webapp"].startswith(
        "Load when building a full-stack browser application"
    )
    assert len(index["website-building/webapp"].split()) <= 50


def _seed_application_project(sandbox: FakeSandbox, project: str) -> None:
    sandbox.writes[f"{project}/app.tsx"] = APPLICATION_SOURCE.encode()
    sandbox.writes[f"{project}/application-design.svg"] = APPLICATION_DESIGN.encode()


def test_manifest_declares_the_tools_the_profiles_and_the_skills() -> None:
    manifest = sites_manifest.manifest()
    assert {tool.name for tool in manifest.tools} == {
        "start_server",
        "deploy_website",
        "publish_website",
        "set_homepage",
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
    profile = next(profile for profile in manifest.subagents if profile.name == "website_building")
    assert profile.input_model.model_validate({"objective": "x" * 10_000}).objective == "x" * 10_000
    assert "maxLength" not in profile.input_model.model_json_schema()["properties"]["objective"]
    assert "maxLength" not in profile.output_model.model_json_schema()["properties"]["result"]
    assert "action:site:deploy_website" in profile.tool_names
    assert "action:agent:set_homepage" in profile.tool_names
    assert "start_server" in profile.tool_names
    assert "action:site:publish_website" not in profile.tool_names
    assert "publish_website" not in profile.tool_names
    assert "this conversation's sandbox" in build.description
    assert manifest.hooks == ()
    assert {spec.path.name for spec in manifest.skills} == {
        "website-building",
        APPLICATION_HOMEPAGE_SKILL,
    }
    (section,) = manifest.prompt_sections
    assert section.name == "sites" and "<sites>" in section.body


def test_the_application_builder_profile_holds_generic_tools_and_one_site_action() -> None:
    """Every act the builder makes is a core builtin or the one action that hosts. What the profile
    still carries is what a skill cannot say: the model, the round cap, read-only connectors, the
    untrusted wall, and a payload spawn validates."""

    profile = APPLICATION_BUILDER_PROFILE
    assert profile.name == APPLICATION_BUILDER_NAME
    assert set(profile.tool_names) == {
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "action:site:deploy_website",
    }
    assert profile.untrusted_output is True
    assert profile.isolated_tools is True
    assert profile.connector_read_only is True
    assert profile.max_rounds == APPLICATION_BUILDER_ROUND_LIMIT
    assert profile.input_model.model_validate({"objective": "build it"}).preload_skills == (
        APPLICATION_HOMEPAGE_SKILL,
    )
    with pytest.raises(ValidationError):
        profile.input_model.model_validate({"objective": "build it", "phase": "qa"})


def _audit_report(regions: tuple[dict[str, object], ...]) -> bytes:
    """A clean browser report: four views, no failure, the design's own regions rendered."""

    view = {
        "textChecked": 12,
        "text": [],
        "documentWidth": 0,
        "pageHeight": 844,
        "clipped": [],
        "overlaps": [],
        "console": [],
        "aboveFoldText": "queue detail",
        "regions": list(regions),
    }
    return (
        ApplicationAuditReport.model_validate(
            {
                "designHeight": 844,
                "designRegions": list(regions),
                "views": [
                    {**view, "scheme": scheme, "width": width, "documentWidth": width}
                    for scheme, width in MEASURED_VIEWS
                ],
                "interaction": {
                    "controls": [
                        {"selector": "button.a", "name": "Filter"},
                        {"selector": "button.b", "name": "Sort"},
                    ],
                    "successes": [
                        {"selector": "button.a", "name": "Filter"},
                        {"selector": "button.b", "name": "Sort"},
                    ],
                    "states": [],
                    "console": [],
                },
            }
        )
        .model_dump_json(by_alias=True)
        .encode()
    )


def _gate(sandbox: FakeSandbox, tmp_path: Path, project: str = "/workspace/ufo-app"):
    return sites_tools.ApplicationPageGate(_context(sandbox, tmp_path), project)


async def test_a_folder_with_no_page_source_is_hosted_as_it_stands(tmp_path: Path) -> None:
    """The gate is what an app page is held to, not what a member's website is."""

    sandbox = FakeSandbox()
    sandbox.scripted_programs[sites_tools.ENUMERATE_PROG] = ExecResult(
        json.dumps({"index.html": {"size": 12, "sha256": "a" * 64}}), "", 0
    )
    project, listing = await sites_tools._served_directory(
        _context(sandbox, tmp_path), "/workspace/site"
    )
    assert project == "/workspace/site"
    assert listing == {"index.html": {"size": 12, "sha256": "a" * 64}}
    assert sandbox.shells == []


async def test_the_source_gate_refuses_before_the_build_is_paid_for(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.writes["/workspace/ufo-app/app.tsx"] = APPLICATION_SOURCE.replace(
        'import { mountApp, Card } from "ufo/kit";',
        'import { mountApp, Card } from "ufo/kit";\nimport confetti from "canvas-confetti";',
    ).encode()
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await _gate(sandbox, tmp_path).built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "source"
    assert "may import only from ufo/kit" in issue.message
    assert not any("vite build" in script for script, _args, _timeout in sandbox.shells)


async def test_the_source_gate_wants_every_designed_region_marked(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.writes["/workspace/ufo-app/app.tsx"] = APPLICATION_SOURCE.replace(
        '<div data-app-region="detail" />', "<div />"
    ).encode()
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await _gate(sandbox, tmp_path).built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "source"
    assert "data-app-region: detail" in issue.message


async def test_a_build_failure_is_a_repair_not_a_crash(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.scripted_shells["vite build"] = ExecResult("", "app.tsx:4 unexpected token", 1)
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await _gate(sandbox, tmp_path).built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "build"
    assert "unexpected token" in issue.message


async def test_a_long_build_failure_keeps_the_error_and_drops_the_warnings(
    tmp_path: Path,
) -> None:
    """vite writes its warnings first and its fatal error last. A recorded build was handed a
    `configLoader` deprecation notice as its whole repair list while the error that stopped it sat
    past the cut, so the builder spent a round on something it could do nothing about."""

    warnings = (
        "(!) Your Vite config uses features that are unsupported by `configLoader: 'native'`\n"
        "  - ESM syntax in a file loaded as CommonJS (vite.config.ts:17:1)\n"
        "(!) Some chunks are larger than 500 kB after minification.\n"
    ) * 6
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.scripted_shells["vite build"] = ExecResult(
        "", warnings + 'error during build:\napp.tsx:12:3: ERROR: Expected "}"', 1
    )
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await _gate(sandbox, tmp_path).built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.message.endswith('app.tsx:12:3: ERROR: Expected "}"')
    assert not issue.message.startswith("(!)")
    assert len(issue.message) <= sites_tools.MAX_MESSAGE_CHARS


async def test_a_page_that_passes_is_built_audited_and_answered_as_its_dist(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.writes[f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/application-audit/report.json"] = b""
    gate = _gate(sandbox, tmp_path)
    report_path = f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/application-audit/{gate.ctx.turn.id}.json"
    sandbox.writes[report_path] = _audit_report(AUDIT_DESIGN_REGIONS)
    assert await gate.built_page() == "/workspace/ufo-app/dist"
    assert "/workspace/ufo-app/vite.config.ts" in sandbox.workspace_writes
    assert "/workspace/ufo-app/preview.html" in sandbox.workspace_writes
    audit = next(args for script, args, _timeout in sandbox.shells if "node" in script)
    assert audit[1] == "/workspace/ufo-app"
    assert audit[-1] == "/workspace/ufo-app/application-design.svg"


async def test_a_project_without_a_design_is_built_and_served_unaudited(tmp_path: Path) -> None:
    """The audit drives the page in a preview whose every read answers with an empty workspace, so
    it measures what a page draws on its own. An app page an extension ships draws the workspace's
    own rows: it renders its blank state there, exposes no control, and the audit refuses it for
    the preview's emptiness. Those pages carry no design, and a design is what says a page was
    drawn to be measured this way. The kit rules and the build still hold."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    del sandbox.writes["/workspace/ufo-app/application-design.svg"]
    gate = _gate(sandbox, tmp_path)

    assert await gate.built_page() == "/workspace/ufo-app/dist"

    assert any("vite build" in script for script, _args, _timeout in sandbox.shells)
    assert not any("node" in script for script, _args, _timeout in sandbox.shells)


async def test_the_audit_verdict_is_the_refusal_and_it_never_relents(tmp_path: Path) -> None:
    """A page that fails the browser audit is refused on every attempt, and the gate counts none.

    The bound is the child's rounds and the member's next message, never a budget that turns the
    third attempt into a hosted page nobody checked."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    gate = _gate(sandbox, tmp_path)
    report_path = f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/application-audit/{gate.ctx.turn.id}.json"
    report = json.loads(_audit_report(AUDIT_DESIGN_REGIONS))
    report["interaction"]["successes"] = []
    sandbox.writes[report_path] = json.dumps(report).encode()
    for _attempt in range(3):
        with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
            await gate.built_page()
        assert [issue.code for issue in refused.value.verdict.issues] == ["interaction"]


async def test_an_audit_that_cannot_run_is_not_a_repair(tmp_path: Path) -> None:
    """Chromium dying is infrastructure. Telling the builder to edit `app.tsx` over it would send
    it hunting a fault that is not in the page."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.scripted_shells["node"] = ExecResult("", "chromium exited with 137", 1)
    with pytest.raises(RuntimeError) as raised:
        await _gate(sandbox, tmp_path).built_page()
    assert not isinstance(raised.value, sites_tools.ApplicationPageRefused)
    assert "chromium exited with 137" in str(raised.value)


async def test_a_design_the_browser_measured_and_refused_is_a_repair(tmp_path: Path) -> None:
    """The audit found the fault, so the fault is the page's. A recorded build spent two of
    thirteen deploys on a design overflow that arrived as an uncaught Node exception, which the
    builder could only read as the deploy being broken."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.scripted_shells["node"] = ExecResult(
        "",
        "application design extends outside its viewBox: "
        "region=brief-feed edge=right overflow=26px",
        sites_tools.APPLICATION_DESIGN_FAULT_EXIT,
    )
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await _gate(sandbox, tmp_path).built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "design"
    assert "overflow=26px" in issue.message


async def test_a_page_that_never_became_ready_is_a_repair(tmp_path: Path) -> None:
    """The page's own lifecycle never signalled ready, which is the page's fault and says so. It
    reached the builder as a bare RuntimeError before, and three deploys of one recorded build went
    to guessing at it."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    gate = _gate(sandbox, tmp_path)
    root = f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/application-audit/{gate.ctx.turn.id}"
    sandbox.writes[f"{root}.json{sites_tools.APPLICATION_LIFECYCLE_SUFFIX}"] = json.dumps(
        {
            "code": "application_lifecycle",
            "reason": "application lifecycle did not become ready",
            "snapshot": None,
        }
    ).encode()
    sandbox.scripted_shells["node"] = ExecResult("", "", sites_tools.APPLICATION_LIFECYCLE_EXIT)
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await gate.built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "lifecycle"
    assert "did not become ready" in issue.message


async def test_a_refusal_names_the_work_that_never_drained(tmp_path: Path) -> None:
    """The audit records which of six kinds of work is still outstanding, and a builder that reads
    "interval 2" clears two timers. Reading only "did not become ready", one recorded build drew
    and built the same page six times over and never reached the fault."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    gate = _gate(sandbox, tmp_path)
    root = f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/application-audit/{gate.ctx.turn.id}"
    sandbox.writes[f"{root}.json{sites_tools.APPLICATION_LIFECYCLE_SUFFIX}"] = json.dumps(
        {
            "code": "application_lifecycle",
            "reason": "application lifecycle did not become ready",
            "snapshot": {
                "version": 1,
                "generation": 1,
                "epoch": 3,
                "mounted": True,
                "state": "active",
                "revision": 9,
                "blockingWork": 3,
                "blocking": {
                    "startup": 0,
                    "observation": 0,
                    "unary": 1,
                    "stream": 0,
                    "timeout": 0,
                    "interval": 2,
                },
            },
        }
    ).encode()
    sandbox.scripted_shells["node"] = ExecResult("", "", sites_tools.APPLICATION_LIFECYCLE_EXIT)
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await gate.built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "lifecycle"
    assert "state active, 3 blocking (unary 1, interval 2)" in issue.message


async def test_a_page_that_never_mounted_names_what_threw(tmp_path: Path) -> None:
    """The dominant lifecycle refusal is `the page never mounted`, and the throw that stopped the
    mount is already on the page's console. Unnamed, the builder cannot tell a broken import from a
    broken layout: in one recorded run every case spent its whole turn budget rebuilding."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    gate = _gate(sandbox, tmp_path)
    root = f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/application-audit/{gate.ctx.turn.id}"
    sandbox.writes[f"{root}.json{sites_tools.APPLICATION_LIFECYCLE_SUFFIX}"] = json.dumps(
        {
            "code": "application_lifecycle",
            "reason": "application lifecycle did not become ready",
            "snapshot": {
                "version": 1,
                "generation": 1,
                "epoch": 0,
                "mounted": False,
                "state": "booting",
                "revision": 0,
                "blockingWork": 0,
                "blocking": {
                    "startup": 0,
                    "observation": 0,
                    "unary": 0,
                    "stream": 0,
                    "timeout": 0,
                    "interval": 0,
                },
            },
            "problems": ["pageerror: Segmented is not exported from ufo/kit"],
        }
    ).encode()
    sandbox.scripted_shells["node"] = ExecResult("", "", sites_tools.APPLICATION_LIFECYCLE_EXIT)
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await gate.built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "lifecycle"
    assert "the page never mounted" in issue.message
    assert "Segmented is not exported from ufo/kit" in issue.message


async def test_a_refusal_that_names_every_console_line_still_validates(tmp_path: Path) -> None:
    """A page can print four console errors of 500 characters each, which is five times what an
    audit issue's message holds. Unbounded, the verdict raised a validation error and the deploy
    ended in an internal fault instead of the repair the audit had already written."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    gate = _gate(sandbox, tmp_path)
    root = f"{RUNTIME_ROOT}/{TOOL_OUTPUT_DIR}/application-audit/{gate.ctx.turn.id}"
    problems = [f"pageerror {index}: ".ljust(500, "x") for index in range(4)]
    sandbox.writes[f"{root}.json{sites_tools.APPLICATION_LIFECYCLE_SUFFIX}"] = json.dumps(
        {
            "code": "application_lifecycle",
            "reason": "application lifecycle did not become ready",
            "snapshot": None,
            "problems": problems,
        }
    ).encode()
    sandbox.scripted_shells["node"] = ExecResult("", "", sites_tools.APPLICATION_LIFECYCLE_EXIT)
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await gate.built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "lifecycle"
    assert len(issue.message) <= MAX_MESSAGE_CHARS
    assert issue.message.startswith("application lifecycle did not become ready: pageerror 0: ")
    assert issue.message.endswith("; …")
    assert "pageerror 1" not in issue.message


def test_the_lifecycle_reason_keeps_whole_problems_up_to_the_message_cap() -> None:
    """What the cap keeps and what it drops, at the join itself: whole lines in the order the audit
    found them, an ellipsis for the lines left in the diagnostic, and the head of the first line
    when even one line is longer than the message holds."""

    reason = "application lifecycle did not become ready"
    assert sites_tools._lifecycle_message(reason, ()) == reason
    short = ("the page never mounted", "pageerror: Segmented is not exported")
    assert sites_tools._lifecycle_message(reason, short) == f"{reason}: {'; '.join(short)}"
    kept = sites_tools._lifecycle_message(reason, ("a" * 400, "b" * 400))
    assert kept == f"{reason}: {'a' * 400}; {'b' * 51}; …"
    assert len(kept) == MAX_MESSAGE_CHARS
    cut = sites_tools._lifecycle_message(reason, ("c" * 480, "d" * 480))
    assert cut == f"{reason}: {'c' * 453}; …"
    assert len(cut) == MAX_MESSAGE_CHARS


async def test_a_refusal_survives_a_diagnostic_it_cannot_read(tmp_path: Path) -> None:
    """An issue message cannot be empty. A diagnostic too large for the gate's read leaves the
    reason empty, and the refusal the exit code earned would raise a validation error instead."""

    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    gate = _gate(sandbox, tmp_path)
    sandbox.scripted_shells["node"] = ExecResult("", "", sites_tools.APPLICATION_LIFECYCLE_EXIT)
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await gate.built_page()
    (issue,) = refused.value.verdict.issues
    assert issue.code == "lifecycle"
    assert issue.message == sites_tools.APPLICATION_LIFECYCLE_UNREAD


def test_the_audit_bounds_a_console_line_in_bytes() -> None:
    """The gate reads the diagnostic under a byte cap, and one CJK character is three bytes. Four
    lines bounded by character count pass that cap, so the read fails and the refusal arrives
    empty."""

    script = APPLICATION_AUDIT_SCRIPT_PATH.read_text()
    assert "const APPLICATION_LIFECYCLE_PROBLEM_BYTES = 600;" in script
    assert ".map(boundedProblem)" in script
    assert "Buffer.from(text, 'utf8')" in script
    bound = int(re.search(r"APPLICATION_LIFECYCLE_PROBLEM_BYTES = (\d+)", script).group(1))
    lines = int(re.search(r"APPLICATION_LIFECYCLE_PROBLEM_MAX = (\d+)", script).group(1))
    assert bound * lines < sites_tools.APPLICATION_LIFECYCLE_DIAGNOSTIC_MAX_BYTES


def test_the_audit_hands_the_readiness_wait_the_page_console() -> None:
    """Both ends of one field: the script collects console and pageerror lines on the page it is
    waiting on, and the wait that gives up carries them into the diagnostic the gate reads."""

    script = APPLICATION_AUDIT_SCRIPT_PATH.read_text()
    assert "frame, timeoutMs = APPLICATION_LIFECYCLE_TIMEOUT_MS, problems = []" in script
    assert (
        script.count("waitForApplicationReady(frame, APPLICATION_LIFECYCLE_TIMEOUT_MS, problems)")
        == 2
    )
    assert "problems: error.problems || []" in script
    assert "problems.slice(0, APPLICATION_LIFECYCLE_PROBLEM_MAX)" in script


def test_a_page_that_never_mounted_and_one_that_never_settled_read_apart() -> None:
    """Three outcomes the check cannot tell apart in one word: nothing mounted, work outstanding,
    and a page that mounts and idles but re-renders under the audit's own paint."""

    def state(**fields: object) -> str:
        blocking = {
            "startup": 0,
            "observation": 0,
            "unary": 0,
            "stream": 0,
            "timeout": 0,
            "interval": 0,
        }
        snapshot = {
            "version": 1,
            "generation": 1,
            "epoch": 0,
            "mounted": True,
            "state": "idle",
            "revision": 0,
            "blockingWork": 0,
            "blocking": blocking,
            **fields,
        }
        return sites_tools._lifecycle_state(
            sites_tools._ApplicationLifecycleSnapshot.model_validate(snapshot)
        )

    assert state(mounted=False) == "the page never mounted"
    assert state() == "the page kept re-rendering"
    assert state(state="booting") == "state booting"
    assert (
        state(
            blockingWork=4,
            blocking={
                "startup": 1,
                "observation": 0,
                "unary": 0,
                "stream": 3,
                "timeout": 0,
                "interval": 0,
            },
        )
        == "4 blocking (startup 1, stream 3)"
    )


def test_a_static_deploy_needs_no_entry_point_named() -> None:
    """Every static site serves `index.html`, and a required field spent two of thirteen deploys in
    one recorded build teaching the builder that it was required."""

    deployed = sites_tools.DeployWebsiteInput(project_path="/workspace/site", site_name="s")
    assert deployed.entry_point == "index.html"


def test_the_design_contract_is_one_360_px_lane() -> None:
    assert APPLICATION_DESIGN_WIDTH == NARROW_WIDTH
    design = validate_application_design(APPLICATION_DESIGN)
    assert design == ApplicationDesign(("queue", "detail"), ("Card",), 844)
    with pytest.raises(ValueError, match='viewBox="0 0 360 H"'):
        validate_application_design(
            APPLICATION_DESIGN.replace('"0 0 360 844" width="360"', '"0 0 305 844" width="305"')
        )


def test_a_page_without_its_design_is_still_held_to_the_kit() -> None:
    validate_application_source(APPLICATION_SOURCE)
    with pytest.raises(ValueError, match="at least one UI component"):
        validate_application_source(
            'import { mountApp } from "ufo/kit";\n'
            'mountApp(document.getElementById("root")!, () => <main>hi</main>);\n'
        )


def test_every_shipped_app_page_passes_the_gate_that_deploys_it() -> None:
    """An app's home skill copies the page this gate then reads, so a rule the shipped pages do not
    keep is a rule that refuses the product's own homepages. The Radar page reads its tour from
    `./tour.md?raw`, which the deploy's config carries through the build."""

    root = Path(__file__).parents[3]
    pages = sorted(root.glob("extensions/app_*/ufo_ext_app_*/skills/*/app.tsx"))
    assert pages, "no shipped app pages found — the test lost its subjects"
    for path in pages:
        validate_application_source(path.read_text())


def test_a_page_may_read_one_file_beside_it_and_nothing_else() -> None:
    kit = 'import { mountApp, Card } from "ufo/kit";\n'
    page = APPLICATION_SOURCE.replace(kit, f'{kit}import TOUR from "./tour.md?raw";\n')
    validate_application_source(page)
    for module in ("./sheet.pdf?url", "./cover.png?url"):
        validate_application_source(page.replace("./tour.md?raw", module))
    for module in (
        "../tour.md?raw",
        "./docs/tour.md?raw",
        "./tour.md",
        "./tour.ts?raw",
        "./sheet.pdf",
        "./sheet.pdf?raw",
        "../sheet.pdf?url",
    ):
        with pytest.raises(ValueError, match="may import only from ufo/kit"):
            validate_application_source(page.replace("./tour.md?raw", module))
    with pytest.raises(ValueError, match="named imports"):
        validate_application_source(page.replace(kit, f'{kit}import Kit from "ufo/kit";\n'))


def test_design_fidelity_reads_the_narrow_views_and_leaves_desktop_alone() -> None:
    """The design is a 360 px lane, so the lane is what it is held to. A desktop layout answers
    the same page checks and carries no region verdict — comparing a one-column lane with a 1440 px
    layout measures nothing the designer chose."""

    report = ApplicationAuditReport.model_validate_json(_audit_report(AUDIT_DESIGN_REGIONS))
    assert application_design_fidelity(report).failures == ()
    moved = tuple(
        {**dict(region), "name": "elsewhere"} if region["name"] == "detail" else dict(region)
        for region in AUDIT_DESIGN_REGIONS
    )
    payload = json.loads(_audit_report(AUDIT_DESIGN_REGIONS))
    for view in payload["views"]:
        if view["width"] == DESKTOP_WIDTH:
            view["regions"] = list(moved)
    assert (
        application_design_fidelity(ApplicationAuditReport.model_validate(payload)).failures == ()
    )
    for view in payload["views"]:
        if view["width"] == NARROW_WIDTH:
            view["regions"] = list(moved)
    narrow = application_design_fidelity(ApplicationAuditReport.model_validate(payload))
    assert narrow.failures
    assert all(str(NARROW_WIDTH) in failure for failure in narrow.failures)


def test_the_lane_width_is_named_once_and_every_copy_of_it_is_watched() -> None:
    """The design lane is `NARROW_WIDTH` and nothing else may say so on its own.

    The Python validator reads the constant, the audit script takes it as an argument with no
    default, and the skill is markdown that has to print the number — so the one place a second
    copy exists is the one place a gate watches it. A width that drifted would draw a wireframe
    against a fold the audit never measures, which is the fault the field record recorded."""

    assert APPLICATION_DESIGN_WIDTH == NARROW_WIDTH
    script = APPLICATION_AUDIT_SCRIPT_PATH.read_text()
    assert "DESIGN_INITIAL_VIEWPORT" not in script
    assert "usage: node ${SELF} --design <lane-width> <application-design.svg>" in script
    assert "app-audit.cjs" not in script
    skill = (APPLICATION_SKILL_DIR / "SKILL.md").read_text()
    widths = re.findall(r"\b(\d{3,4}) px\b|--design (\d+)\b", skill)
    printed = {int(value) for pair in widths for value in pair if value}
    assert printed == {NARROW_WIDTH}
    assert f'viewBox="0 0 {NARROW_WIDTH} H"' in skill
    assert f'width="{NARROW_WIDTH}"' in skill


def test_a_build_result_carries_the_evidence_its_status_claims() -> None:
    """A `deployed` with no link is a claim the parent would bind nothing from, and a `blocked` with
    no blocker is a turn that ended for no stated reason. Both reach the child as a validation error
    it can still fix, rather than the member as a page that is not there."""

    for status, missing in (
        ("designed", "design_path"),
        ("deployed", "site, site_url"),
        ("blocked", "blocker"),
    ):
        with pytest.raises(ValidationError, match=f"{status} needs {missing}"):
            ApplicationBuildResult.model_validate({"status": status})

    assert (
        ApplicationBuildResult.model_validate(
            {"status": "deployed", "site": "queue", "site_url": "https://ufo.test/queue"}
        ).site
        == "queue"
    )
    assert (
        ApplicationBuildResult.model_validate(
            {"status": "designed", "design_path": APPLICATION_DESIGN_PATH}
        ).design_path
        == APPLICATION_DESIGN_PATH
    )


def test_the_skill_names_every_class_the_deploy_refuses() -> None:
    """The audit refuses four class patterns outright. A pattern the skill leaves unmentioned is
    one the child meets as a refusal, and a refusal costs a whole deploy to learn — two of the four
    recorded in one run were `space-y-` and a `className` template literal, neither of which the
    skill named. The reason text is what the child reads either way, so the skill carries it."""

    skill = " ".join((APPLICATION_SKILL_DIR / "SKILL.md").read_text().split())
    for _pattern, reason in PAGE_CLASS_REFUSALS:
        assert " ".join(reason.split()) in skill, f"the skill never tells the child: {reason}"
    for step in COMPOSITION_STEPS:
        assert f"`gap-{step}`" in skill, f"the skill never names the step gap-{step}"


def test_a_deploy_refusal_cannot_be_handed_back_as_a_blocker() -> None:
    """`BUILDER_CONTRACT` says a refusal lists the repairs to make and deploy again. Two recorded
    builds answered `blocked` with the refusal pasted into the blocker instead, and each time the
    parent took the repair on itself — 205 calls, then 105. The result refuses it, so the child
    reads an error it can still act on rather than the member reading a page that is not there."""

    refused = f"ApplicationPageRefused: {PAGE_REFUSAL_OPENING}\n- light 360px lacks a region"
    with pytest.raises(ValidationError, match="a deploy refusal is not a blocker"):
        ApplicationBuildResult.model_validate({"status": "blocked", "blocker": refused})

    stands = "the connector holds no account for this workspace"
    assert (
        ApplicationBuildResult.model_validate({"status": "blocked", "blocker": stands}).blocker
        == stands
    )


def test_a_design_answers_with_the_drawing_and_not_its_picture() -> None:
    """The audit renders `design.png` beside the SVG so the child can look at its own work, and a
    child that answered with the picture handed its parent a file the design pass cannot carry
    forward. The drawing is the SVG; the error reaches the child as one it can still fix."""

    with pytest.raises(ValidationError, match="design_path is the wireframe you wrote"):
        ApplicationBuildResult.model_validate(
            {"status": "designed", "design_path": "/workspace/ufo-app/design.png"}
        )

    assert (
        ApplicationBuildResult.model_validate(
            {"status": "deployed", "site": "clock", "site_url": "https://ufo.test/clock"}
        ).design_path
        == ""
    )


def test_the_scaffold_copies_files_and_never_the_template_directory() -> None:
    """`cp -r` on the template sets the read-only mount's permission bits on the project directory,
    which the sandbox refuses: one recorded build blocked on `cp: setting permissions for
    '/workspace/ufo-app': Permission denied`, and its second attempt copied the template *into* the
    project instead of onto it. The template is flat, so copying its files needs no recursion, and
    `-n` makes the step safe to repeat once the page is written."""

    skill = (APPLICATION_SKILL_DIR / "SKILL.md").read_text()
    assert 'cp -n "$UFO_HOME/skills/application-homepage/template/"*' in skill
    scaffold = skill.split("## Scaffold")[1].split("##")[0]
    assert "cp -r" not in scaffold.split("Do not use")[0]
    assert all(path.is_file() for path in (APPLICATION_TEMPLATE_DIR).iterdir()), (
        "a nested template needs recursion, so the skill's flat copy would miss it"
    )


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
        "light 360px renders summary below the fold, where the design puts it above",
        "dark 360px renders summary below the fold, where the design puts it above",
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
                    "width": APPLICATION_DESIGN_WIDTH,
                    "textChecked": 1,
                    "text": [],
                    "documentWidth": APPLICATION_DESIGN_WIDTH,
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
                    "width": APPLICATION_DESIGN_WIDTH,
                    "textChecked": 1,
                    "text": [],
                    "documentWidth": APPLICATION_DESIGN_WIDTH,
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


def test_a_region_the_page_never_rendered_says_so() -> None:
    """One message covered a region the page never rendered and one it rendered below the fold,
    and a repair treats them differently: the first is written or unhidden, the second is moved.
    A recorded build spent six of its ten deploys on that line. Nothing can separate an absent
    region from one hidden behind a control that shows a single region at a time — neither is
    measured — so the message names both rather than implying the one."""

    designed = ApplicationAuditRegion(name="summary", left=0.05, top=0.0, width=0.9, height=0.1)

    missing = application_region_failure("light", "summary", None, designed)
    assert missing is not None
    assert "measured no summary" in missing
    assert "hides it behind a control" in missing

    below = ApplicationAuditRegion(
        name="summary", left=0.05, top=0.9, width=0.9, height=0.05, above_fold=False
    )
    assert application_region_failure("light", "summary", below, designed) == (
        "light 360px renders summary below the fold, where the design puts it above"
    )

    assert application_region_failure("light", "summary", designed, designed) is None


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
        validate_application_source(source)

    validate_application_source(source.replace('"#FFFFFF"', '"var(--color-surface)"'))


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
        validate_application_source(source)


def test_application_source_rejects_namespace_and_local_look_alike_components() -> None:
    namespace = (
        'import * as Kit from "ufo/kit";\n'
        'import { mountApp } from "ufo/kit";\n'
        'mountApp(document.getElementById("root")!, () => <Kit.Card />);'
    )
    with pytest.raises(ValueError, match="use named imports"):
        validate_application_source(namespace)

    look_alike = (
        'import { mountApp, useState } from "ufo/kit";\n'
        "function Card({ children }: { children: unknown }) { return <div>{children}</div>; }\n"
        "function App() { useState(false); return <Card>Ready</Card>; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    with pytest.raises(ValueError, match="render at least one UI component"):
        validate_application_source(look_alike)

    shadowed_alias = (
        'import { Card as KitCard, mountApp } from "ufo/kit";\n'
        "function App() { function KitCard() { return <main />; } return <KitCard />; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    with pytest.raises(ValueError, match="render at least one UI component"):
        validate_application_source(shadowed_alias)

    parameter_alias = (
        'import { Card as KitCard, mountApp } from "ufo/kit";\n'
        "const LocalCard = () => <main />;\n"
        "function App(KitCard = LocalCard) { return <KitCard />; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )
    with pytest.raises(ValueError, match="render at least one UI component"):
        validate_application_source(parameter_alias)


def test_application_source_rejects_a_destructured_local_kit_shadow() -> None:
    source = (
        'import { Card, mountApp } from "ufo/kit";\n'
        "const local = { Card: () => <main /> };\n"
        "function App() { const { Card } = local; return <Card />; }\n"
        'mountApp(document.getElementById("root")!, () => <App />);'
    )

    with pytest.raises(ValueError, match="render at least one UI component"):
        validate_application_source(source)


def test_application_source_reserves_kit_slot_ownership() -> None:
    with pytest.raises(ValueError, match="data-slot is reserved for ufo/kit components"):
        validate_application_source(_page('<p data-slot="stat-label">x</p>'))

    validate_application_source(
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
    validate_application_source(_page("<p>x</p>").replace("function App", data + "\nfunction App"))


def test_ufo_style_uses_the_application_audit_narrow_width() -> None:
    skill = (
        Path(__file__).parents[3] / "core/src/ufo/runtime/skills/ufo-style/SKILL.md"
    ).read_text()

    assert f"narrow width checked at {NARROW_WIDTH}px" in skill
    assert "narrow width checked at 390px" not in skill


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
