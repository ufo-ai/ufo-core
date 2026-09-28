import json
import re
import subprocess
import sys
import tarfile
from base64 import urlsafe_b64decode
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from ufo_ext_sites import manifest as sites_manifest
from ufo_ext_sites import tools as sites_tools
from ufo_ext_sites.delegation import BuildWebsiteInput
from ufo_ext_sites.objects import (
    CONVERSATION_DIGEST_HEX,
    site_name_from_object,
    site_object_name,
)
from ufo_ext_sites.source import (
    KIT_ARCHIVE,
    KIT_MOUNT,
    PROJECT_FILE_ABSENT,
    PROJECT_FILE_READ,
    UNPACK_KIT_PROG,
    validate_application_source,
)
from ufo_ext_sites.store import (
    SITE_NAME_MAX,
    InvalidSiteName,
    site_name,
)
from ufo_ext_sites.tools import (
    LOG_CLEAR_PROG,
    LOG_TAIL_TIMEOUT_SECONDS,
    PORT_STOP_PROG,
    READINESS_TIMEOUT_SECONDS,
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
APPLICATION_DESIGN = """<svg viewBox="0 0 1440 900" width="1440" height="900">
<g data-app-region="queue"><g data-kit-component="Card"><rect width="864" height="900" /></g></g>
<g data-app-region="detail"><rect x="864" width="576" height="900" /></g>
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
KIT = b"the deploy's kit archive"
APPLICATION_LISTING = {"app.tsx": {"size": 12, "sha256": "a" * 64}}


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
        if program == PROJECT_FILE_READ and args:
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
    reattached to and launches nothing, and a cleared base launches."""

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


def test_website_building_parent_keeps_its_own_subdirs_but_not_the_child_subtree() -> None:
    registry = skill_registry((sites_manifest.manifest(),))
    parent_files = {path for path, _ in registry.named("website-building").files}
    assert any(path.startswith("game/") for path in parent_files)
    assert any(path.startswith("shared/") for path in parent_files)
    assert any(path.startswith("informational/") for path in parent_files)
    assert not any(path.startswith("webapp/") for path in parent_files)


def test_the_webapp_template_takes_sqlite_from_a_release_that_ships_a_binary() -> None:

    registry = skill_registry((sites_manifest.manifest(),))
    files = dict(registry.named("website-building/webapp").files)
    package = json.loads(files["template/package.json"])
    requirement = package["dependencies"]["better-sqlite3"]
    assert requirement.startswith("^")
    assert int(requirement.removeprefix("^").split(".")[0]) >= 13, requirement


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


def test_the_playwright_guidance_keeps_the_browser_outside_the_cell() -> None:
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
            (started[1], str(sites_tools.SERVER_STOP_TIMEOUT_SECONDS)),
            sites_tools.SERVER_STOP_TIMEOUT_SECONDS + 5,
        ),
        (
            sites_tools.SERVER_TASK_STOP,
            ("123", started[1]),
            sites_tools.SERVER_STOP_TIMEOUT_SECONDS,
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
    """The carrier's kill leaves its own `timed out` and nothing else, so a server that never
    bound its port and wrote no log would be reported as an empty error."""
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
    """These tools quote their path arguments into `cd` and `>` rather than opening them, and
    under the local carrier those run on the host."""
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
        (LOG_CLEAR_PROG, (log,)),
        (PORT_STOP_PROG, ("5173",)),
    ]
    launch = next(
        command for command, _base, _detach, _timeout in sandbox.tasks if f">{log}" in command
    )
    redirect = launch.index(f">{log}")
    assert "set -C\n" in launch[:redirect], "the redirect must create the log, never truncate it"
    assert "exec env PORT=5173 bash -lc" in launch[:redirect]


def test_site_name_slugs_bounds_and_refuses_a_nameless_site() -> None:
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
    (profile,) = manifest.subagents
    assert profile.name == "website_building"
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
    }
    (section,) = manifest.prompt_sections
    assert section.name == "sites" and "<sites>" in section.body


def _gate(sandbox: FakeSandbox, tmp_path: Path, project: str = "/workspace/ufo-app"):
    return sites_tools.ApplicationPageGate(_context(sandbox, tmp_path), project, KIT)


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


async def test_an_app_page_is_refused_naming_the_setting_where_the_deploy_names_no_kit(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.scripted_programs[sites_tools.ENUMERATE_PROG] = ExecResult(
        json.dumps(APPLICATION_LISTING), "", 0
    )
    with pytest.raises(RuntimeError, match=r"sets no \[sites\] page_kit, so /workspace/ufo-app"):
        await sites_tools._served_directory(_context(sandbox, tmp_path), "/workspace/ufo-app")
    assert sandbox.shells == []
    assert sandbox.workspace_writes == []


async def test_an_app_page_builds_against_the_kit_archive_the_deploy_names(
    tmp_path: Path,
) -> None:
    kit = tmp_path / "kit.tar.gz"
    kit.write_bytes(KIT)
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.scripted_programs[sites_tools.ENUMERATE_PROG] = ExecResult(
        json.dumps(APPLICATION_LISTING), "", 0
    )
    project, _listing = await sites_tools._served_directory(
        replace(_context(sandbox, tmp_path), page_kit=kit), "/workspace/ufo-app"
    )
    assert project == "/workspace/ufo-app/dist"
    assert sandbox.writes[f"/workspace/ufo-app/{KIT_ARCHIVE}"] == KIT


def test_the_kit_archive_unpacks_its_files_under_the_mount_and_removes_itself(
    tmp_path: Path,
) -> None:
    built = tmp_path / "built"
    (built / "chunks").mkdir(parents=True)
    (built / "kit.js").write_text("export const kit = 1;")
    (built / "chunks" / "a.js").write_text("export const a = 1;")
    archive = tmp_path / "project" / KIT_ARCHIVE
    archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(built, arcname=".")
    mount = tmp_path / "project" / KIT_MOUNT
    subprocess.run(
        [sys.executable, "-I", "-c", UNPACK_KIT_PROG, str(archive), str(mount)], check=True
    )
    assert (mount / "kit.js").read_text() == "export const kit = 1;"
    assert (mount / "chunks" / "a.js").read_text() == "export const a = 1;"
    assert not archive.exists()


async def test_the_source_gate_refuses_before_the_build_is_paid_for(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.writes["/workspace/ufo-app/app.tsx"] = APPLICATION_SOURCE.replace(
        'import { mountApp, Card } from "ufo/kit";',
        'import { mountApp, Card } from "ufo/kit";\nimport confetti from "canvas-confetti";',
    ).encode()
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await _gate(sandbox, tmp_path).built_page()
    issue = refused.value.issue
    assert issue.code == "source"
    assert "may import only from ufo/kit" in issue.message
    assert not any("vite build" in script for script, _args, _timeout in sandbox.shells)


async def test_a_build_failure_is_a_repair_not_a_crash(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    sandbox.scripted_shells["vite build"] = ExecResult("", "app.tsx:4 unexpected token", 1)
    with pytest.raises(sites_tools.ApplicationPageRefused) as refused:
        await _gate(sandbox, tmp_path).built_page()
    issue = refused.value.issue
    assert issue.code == "build"
    assert "unexpected token" in issue.message


async def test_a_long_build_failure_keeps_the_error_and_drops_the_warnings(
    tmp_path: Path,
) -> None:
    """vite writes its warnings first and its fatal error last."""

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
    issue = refused.value.issue
    assert issue.message.endswith('app.tsx:12:3: ERROR: Expected "}"')
    assert not issue.message.startswith("(!)")
    assert len(issue.message) <= sites_tools.MAX_MESSAGE_CHARS


@pytest.mark.parametrize("design", [None, APPLICATION_DESIGN, "invalid SVG"])
async def test_a_page_builds_with_or_without_a_design(
    tmp_path: Path,
    design: str | None,
) -> None:
    sandbox = FakeSandbox()
    _seed_application_project(sandbox, "/workspace/ufo-app")
    if design is not None:
        sandbox.writes["/workspace/ufo-app/application-design.svg"] = design.encode()
    gate = _gate(sandbox, tmp_path)
    assert await gate.built_page() == "/workspace/ufo-app/dist"
    assert "/workspace/ufo-app/vite.config.ts" in sandbox.workspace_writes
    assert sandbox.writes[f"/workspace/ufo-app/{KIT_ARCHIVE}"] == KIT
    assert (
        UNPACK_KIT_PROG,
        (f"/workspace/ufo-app/{KIT_ARCHIVE}", f"/workspace/ufo-app/{KIT_MOUNT}"),
    ) in (sandbox.programs)
    assert all("node" not in script for script, _, _ in sandbox.shells)
    assert all("application-design.svg" not in args[0] for _, args in sandbox.programs if args)


def test_a_static_deploy_needs_no_entry_point_named() -> None:
    """Every static site serves `index.html`, and a required field spent two of thirteen deploys in
    one recorded build teaching the builder that it was required."""

    deployed = sites_tools.DeployWebsiteInput(project_path="/workspace/site", site_name="s")
    assert deployed.entry_point == "index.html"


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
    """An issue reference and a percentage are spelled exactly like a raw colour and a raw
    length, and a page carries them as its content."""
    validate_application_source(_page("<p>x</p>").replace("function App", data + "\nfunction App"))
