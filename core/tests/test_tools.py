import asyncio
import hashlib
import json
import shlex
import shutil
import sys
from base64 import urlsafe_b64decode
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_sample.spend import CHARGE_TABLE, SampleGate

import ufo.runtime.tools.context as tool_context
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.models.pricing import ModelPrice
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    ExecResult,
    SandboxSession,
    SandboxSpec,
    workspace_path,
)
from ufo.host.ext.loader import skill_registry
from ufo.host.tools.builtins import (
    BUILTIN_TOOLS,
    DOCUMENT_KINDS,
    FILE_TOOL_RESULT_MAX_CHARS,
    REQUEST_CREDENTIALS_TOOL_DEF,
    TOUCH_FIRST_HINT,
    SpawnInput,
    _file_tool_result,
)
from ufo.runtime.billing.accounting import Ledger
from ufo.runtime.billing.spend import GateDeploy
from ufo.runtime.ext.manifest import SubagentProfile
from ufo.runtime.media.image_previews import IMAGE_PREVIEW_MAX_BYTES
from ufo.runtime.media.previews import StoredPreview
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, RuntimeSkill, SkillCard, SkillRegistry
from ufo.runtime.subagents import SubagentRegistry, Subagents
from ufo.runtime.tools.context import (
    SHARE_PREFLIGHT_CMD,
    SHARED_BYTES_LIMIT,
    Spawn,
    SpawnModelRejected,
    SpawnResult,
    SubagentStatus,
    ToolContext,
    ToolResult,
)
from ufo.runtime.tools.registry import REQUESTED_BY, ToolDef, ToolRegistry
from ufo.runtime.turns.audience import (
    SHARED_AUDIENCE,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.runtime.turns.subjects import member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, ModelRouteChange, TerminalFrame, Turn, Usage

REGISTRY = ToolRegistry(BUILTIN_TOOLS)
ARTIFACT_SECRET = "tools-test-secret"


@dataclass
class FakeSandbox:
    bash_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    files: dict[str, bytes] = field(default_factory=dict)
    available_system_skills: frozenset[str] = field(
        default_factory=lambda: frozenset(CORE_SKILL_REGISTRY.by_name)
    )
    skill_loads: list[dict[str, object]] = field(default_factory=list)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return self.bash_result

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        return self.bash_result

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        return self.bash_result

    async def runtime_path(self, relative: str) -> str:
        return f"/runtime/{relative}"

    async def runtime_display_path(self, relative: str) -> str:
        return f"$UFO_HOME/runs/test/{relative}"

    async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]:
        raise AssertionError(f"run_ufo_fs({op}) must not run once a guard has rejected the call")

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

    async def load_skills(self, payload: dict[str, object]) -> dict[str, str]:
        self.skill_loads.append(payload)
        system = payload["system"]
        user = payload["user"]
        assert isinstance(system, dict) and isinstance(user, dict)
        roots = {
            name: f"$UFO_HOME/skills/{name}"
            for name in self.available_system_skills & system.keys()
        }
        for name, wire in user.items():
            assert isinstance(name, str) and isinstance(wire, dict)
            files = wire["files"]
            assert isinstance(files, dict)
            for path, content in files.items():
                assert isinstance(path, str) and isinstance(content, str)
                self.files[f"$UFO_HOME/skills/{name}/{path}"] = urlsafe_b64decode(content)
            roots[name] = f"$UFO_HOME/skills/{name}"
        return roots


async def _unavailable_spawn(
    profile: str,
    payload: dict[str, object],
    background: bool = False,
    dedup_key: str | None = None,
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


@dataclass
class StubSubagentControl:
    statuses: dict[UUID, SubagentStatus] = field(default_factory=dict)
    waited: list[tuple[UUID, ...]] = field(default_factory=list)
    cancelled: list[UUID] = field(default_factory=list)
    messaged: list[tuple[UUID, str, str, UUID | None]] = field(default_factory=list)

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]:
        self.waited.append(turn_ids)
        return tuple(
            self.statuses.get(turn_id, SubagentStatus(turn_id=turn_id, status="done", text="{}"))
            for turn_id in turn_ids
        )

    async def cancel(self, turn_id: UUID) -> SubagentStatus:
        self.cancelled.append(turn_id)
        return self.statuses.get(
            turn_id, SubagentStatus(turn_id=turn_id, status="cancelled", text="")
        )

    async def message(
        self,
        turn_id: UUID,
        text: str,
        dedup_key: str,
        *,
        requesting_message_ref: UUID | None = None,
    ) -> SubagentStatus:
        self.messaged.append((turn_id, text, dedup_key, requesting_message_ref))
        return self.statuses.get(turn_id, SubagentStatus(turn_id=turn_id, status="queued", text=""))


def make_context(
    sandbox: FakeSandbox | SandboxSession,
    tmp_path: Path,
    artifact_secret: str = ARTIFACT_SECRET,
    subagents: StubSubagentControl | None = None,
    spawn: Spawn = _unavailable_spawn,
) -> ToolContext:
    workspace_id, conversation_id, agent_id = uuid4(), uuid4(), uuid4()
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=0,
        status="running",
        inbound="hello",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret=artifact_secret,
        subagents=subagents,
    )


async def run(name: str, ctx: ToolContext, **args: object):
    tool = REGISTRY.get(name)
    return await tool.handler(ctx, tool.input_model.model_validate(args))


def _check_registry_rejects_duplicate_names() -> None:
    bash = REGISTRY.get("bash")
    with pytest.raises(ValueError, match="duplicate tool names: bash"):
        ToolRegistry((bash, bash))


def _check_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown tool: nope"):
        REGISTRY.get("nope")


def _check_registry_get_returns_named_tool() -> None:
    assert REGISTRY.get("edit").name == "edit"


def _check_builtin_tools_are_trusted_by_default() -> None:
    assert all(tool.untrusted is False for tool in BUILTIN_TOOLS)


def test_the_read_description_names_every_document_kind_it_reads() -> None:
    described = next(tool.description for tool in BUILTIN_TOOLS if tool.name == "read").lower()

    for kind in DOCUMENT_KINDS:
        assert kind in described, kind


def _check_a_barrier_is_a_position_read_final_act_or_a_guard_that_reads_the_round() -> None:
    barriers = {tool.name for tool in BUILTIN_TOOLS if not tool.parallel_safe}
    assert barriers == {
        "ask_user",
        "connect_account",
        "write",
        "edit",
        "share_file",
    }
    assert REQUEST_CREDENTIALS_TOOL_DEF.parallel_safe is False


def _check_share_file_dispatch_replays_under_one_idempotency_key() -> None:
    assert REGISTRY.get("share_file").side_effecting is True


def _check_registry_schemas_cover_every_tool() -> None:
    schemas = REGISTRY.schemas()
    assert {schema.name for schema in schemas} == {
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "share_file",
        "spawn",
        "ask_user",
        "load_skill",
        "connect_account",
        "cancel_spawn",
        "message_spawn",
        "get_context_remaining",
    }
    bash = next(schema for schema in schemas if schema.name == "bash")
    assert "command" in bash.input_schema["properties"]
    requester_tools = {
        schema.name for schema in schemas if REQUESTED_BY in schema.input_schema["properties"]
    }
    assert requester_tools == {
        "bash",
        "spawn",
        "connect_account",
    }
    assert bash.input_schema["properties"][REQUESTED_BY]["description"] == (
        "Message ref that explicitly requested this call. Required for any member-specific "
        "authority or capability, including admin actions; omit only for conversation-common work."
    )


def _check_builtin_tool_schema_has_no_user_description() -> None:
    schema = REGISTRY.get("bash").schema().input_schema

    assert "user_description" not in schema["properties"]


def _check_registry_reserves_the_message_authority_field() -> None:
    class CollidingInput(BaseModel):
        requested_by: str

    async def handler(ctx: ToolContext, args: CollidingInput) -> ToolResult:
        raise NotImplementedError

    with pytest.raises(ValueError, match="tool inputs reserve 'requested_by': collision"):
        ToolRegistry(
            (
                ToolDef(
                    name="collision",
                    description="d",
                    input_model=CollidingInput,
                    handler=handler,
                ),
            )
        )


async def test_bash_combines_output_and_flags_nonzero_exit(tmp_path: Path) -> None:
    sandbox = FakeSandbox(bash_result=ExecResult(stdout="out", stderr="err", exit_code=1))
    ctx = make_context(sandbox, tmp_path)
    result = await run("bash", ctx, command="do it")
    assert result.content[0].text == "outerr\nexit code: 1"
    assert result.is_error is True


async def test_bash_silent_failure_reports_the_exit_code(tmp_path: Path) -> None:
    sandbox = FakeSandbox(bash_result=ExecResult(stdout="", stderr="", exit_code=56))
    ctx = make_context(sandbox, tmp_path)
    result = await run("bash", ctx, command="curl -s https://blocked.example")
    assert result.content[0].text == "exit code: 56"
    assert result.is_error is True


async def test_bash_zero_exit_is_not_error(tmp_path: Path) -> None:
    sandbox = FakeSandbox(bash_result=ExecResult(stdout="ok", stderr="", exit_code=0))
    ctx = make_context(sandbox, tmp_path)
    result = await run("bash", ctx, command="echo ok")
    assert result.is_error is False
    assert result.content[0].text == "ok"


@pytest.mark.integration
async def test_bash_keeps_the_carrier_python_after_the_login_profile_resets_path(
    tmp_path: Path, sandbox_client: Path
) -> None:
    scratch = tmp_path / "scratch"
    home = scratch / "home"
    bin_dir = scratch / "bin"
    home.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(sandbox_client, bin_dir / "ufo")
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    decoy = system_bin / "python3"
    decoy.write_text("#!/bin/sh\nprintf system-python\nexit 19\n")
    decoy.chmod(0o755)
    (home / ".bash_profile").write_text(f'export PATH="{system_bin}"\n')
    carrier = LocalCarrier(_scratch=scratch)
    workspace = tmp_path / "workspace"
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace),
        )
    )
    raw = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        "python3 -c 'import reportlab'",
        cwd=workspace,
        env=dict(handle.egress_env),
        stdout=asyncio.subprocess.PIPE,
    )
    raw_stdout, _ = await raw.communicate()
    font_source = (
        Path(__file__).parents[1]
        / "src/ufo/runtime/skills/ufo-style/assets/fonts/Inter-VariableFont_wght.woff2"
    )
    program = (
        "import brotli,reportlab,sys\n"
        "from fontTools.ttLib import TTFont\n"
        "from pathlib import Path\n"
        "from reportlab.pdfgen import canvas\n"
        f"font=TTFont({str(font_source)!r})\n"
        "font.flavor=None\n"
        "font.save('Inter.ttf')\n"
        "pdf=canvas.Canvas('invoice.pdf')\n"
        "pdf.drawString(72, 720, 'Invoice')\n"
        "pdf.save()\n"
        "Path('relative.txt').write_text('relative')\n"
        "print(sys.executable)\n"
        "print(reportlab.Version)\n"
    )
    await carrier.write(handle, "/workspace/generate.py", program.encode())

    result = await run(
        "bash",
        make_context(SandboxSession(carrier=carrier, handle=handle), tmp_path),
        command="python3 generate.py",
    )

    assert raw.returncode == 19
    assert raw_stdout.decode() == "system-python"
    assert result.is_error is False
    output = result.content[0].text.splitlines()
    assert Path(output[0]).parent == Path(sys.executable).parent
    assert (workspace / "Inter.ttf").read_bytes().startswith(b"\x00\x01\x00\x00")
    assert (workspace / "invoice.pdf").read_bytes().startswith(b"%PDF")
    assert (workspace / "relative.txt").read_text() == "relative"


def _check_edit_and_write_state_the_read_or_bash_rule_in_their_descriptions() -> None:
    """Both tools refuse a path the turn has neither read nor named in a bash command, and the
    refusal is a hard raise."""
    schemas = {schema.name: schema for schema in REGISTRY.schemas()}
    for name in ("edit", "write"):
        assert "name it in a bash command" in schemas[name].description
        assert "neither read nor named in a bash command is REFUSED" in schemas[name].description
    assert "Read the file first if it already exists" in schemas["write"].description


async def test_edit_requires_a_read_or_a_bash_command_that_named_the_file(tmp_path: Path) -> None:

    @dataclass
    class EditingSandbox(FakeSandbox):
        async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]:
            return {"replacements": 1}

    ctx = make_context(EditingSandbox(), tmp_path)
    with pytest.raises(ValueError, match="must be read or named in a bash command"):
        await run(
            "edit",
            ctx,
            file_path="code.py",
            edits=[{"old_string": "x = 1", "new_string": "x = 2"}],
        )

    await run("bash", ctx, command="wc -l /workspace/code.py")
    edited = await run(
        "edit", ctx, file_path="code.py", edits=[{"old_string": "x = 1", "new_string": "x = 2"}]
    )

    assert json.loads(edited.content[0].text)["path"] == "code.py"


async def test_a_bash_command_lets_the_write_overwrite_the_file_it_named(tmp_path: Path) -> None:
    """The rule is decided here and carried to the files as `allow_existing`, so the shell command
    that named the file is what the overwrite rides on."""

    @dataclass
    class RecordingSandbox(FakeSandbox):
        calls: list[dict[str, object]] = field(default_factory=list)

        async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]:
            self.calls.append(args)
            return {"created": False}

    sandbox = RecordingSandbox()
    ctx = make_context(sandbox, tmp_path)

    await run("bash", ctx, command="head -n 5 notes.md")
    await run("write", ctx, file_path="/workspace/notes.md", content="body")

    assert sandbox.calls[-1]["allow_existing"] is True


async def test_a_bash_command_that_names_no_file_leaves_the_guard_standing(tmp_path: Path) -> None:
    """The rule takes a command that reached the file, not any command at all — a glob names
    whatever the shell resolves it to, which the host never sees."""
    ctx = make_context(FakeSandbox(), tmp_path)

    await run("bash", ctx, command="ls /workspace/*.py")

    with pytest.raises(ValueError, match="must be read or named in a bash command"):
        await run(
            "edit",
            ctx,
            file_path="code.py",
            edits=[{"old_string": "x = 1", "new_string": "x = 2"}],
        )


async def test_the_touch_guard_refusals_name_the_acts_that_clear_them(tmp_path: Path) -> None:
    """The guard states the rule; the refusal has to state the acts."""

    @dataclass
    class RefusingSandbox(FakeSandbox):
        error: Exception = field(default_factory=lambda: ValueError("boom"))

        async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]:
            raise self.error

    guard = ValueError("file /workspace/notes.md must be read before it is written")
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError) as edited:
        await run("edit", ctx, file_path="code.py", edits=[{"old_string": "a", "new_string": "b"}])
    with pytest.raises(ValueError) as written:
        await run(
            "write",
            make_context(RefusingSandbox(error=guard), tmp_path),
            file_path="/workspace/notes.md",
            content="body",
        )
    staging = ValueError("staged file is gone")
    with pytest.raises(ValueError) as unrelated:
        await run(
            "write",
            make_context(RefusingSandbox(error=staging), tmp_path),
            file_path="/workspace/notes.md",
            content="body",
        )

    assert str(edited.value).endswith(TOUCH_FIRST_HINT.format(path="code.py"))
    assert str(written.value).endswith(TOUCH_FIRST_HINT.format(path="/workspace/notes.md"))
    assert "must be read or named in a bash command" in str(written.value)
    assert str(unrelated.value) == "staged file is gone"


async def test_a_write_the_turn_already_reached_carries_no_touch_hint(tmp_path: Path) -> None:
    """A reached path that still fails failed for another reason, so the hint would name an act the
    turn has already done."""

    @dataclass
    class RefusingSandbox(FakeSandbox):
        async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]:
            raise ValueError("file /workspace/notes.md must be read before it is written")

    ctx = make_context(RefusingSandbox(), tmp_path)
    ctx.touched_paths.add("/workspace/notes.md")

    with pytest.raises(ValueError) as refused:
        await run("write", ctx, file_path="/workspace/notes.md", content="body")

    assert TOUCH_FIRST_HINT.format(path="/workspace/notes.md") not in str(refused.value)


@pytest.mark.integration
async def test_write_and_edit_land_bounded_results(tmp_path: Path, sandbox_client: Path) -> None:
    """The write and the edit land through the real `ufo fs`, so this needs a built client."""
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace),
        )
    )
    ctx = make_context(SandboxSession(carrier=carrier, handle=handle), tmp_path)
    written = await run(
        "write",
        ctx,
        file_path="notes.txt",
        content="old /workspace path\n",
    )
    assert json.loads(written.content[0].text) == {
        "created": True,
        "path": "notes.txt",
        "size_bytes": 20,
        "lines": 1,
    }
    assert (workspace / "notes.txt").read_text() == "old /workspace path\n"

    edited = await run(
        "edit",
        ctx,
        file_path="notes.txt",
        edits=[{"old_string": "/workspace", "new_string": "/workspace/final"}],
    )
    edited_payload = json.loads(edited.content[0].text)
    assert edited_payload["replacements"] == 1
    assert "old /workspace/final path" in edited_payload["snippet"]
    assert (workspace / "notes.txt").read_text() == "old /workspace/final path\n"

    await ctx.sandbox.write_file("large.txt", b"old\n" * 10_000)
    ctx.touched_paths.add("large.txt")
    large = await run(
        "write",
        ctx,
        file_path="large.txt",
        content="new\n" * 10_000,
    )
    assert json.loads(large.content[0].text)["size_bytes"] == 40_000
    assert len(large.content[0].text) <= FILE_TOOL_RESULT_MAX_CHARS
    assert (workspace / "large.txt").read_text() == "new\n" * 10_000

    injected = await run(
        "write",
        ctx,
        file_path="name\n+++ injected",
        content="safe\n",
    )
    assert json.loads(injected.content[0].text)["path"] == "name\n+++ injected"
    assert (workspace / "name\n+++ injected").read_text() == "safe\n"


def _check_file_tool_result_bounds_escaped_paths() -> None:
    path = "/".join(["\\" * 200] * 20 + ["\\" * 73])
    result = _file_tool_result(
        {
            "path": path,
            "message": f"{path}: 1 replacements",
            "replacements": 1,
            "snippet": "\\" * 2_000,
        }
    )
    payload = json.loads(result.content[0].text)
    assert payload["path"] == path
    assert payload["message"] == "1 replacements"
    assert len(result.content[0].text) <= FILE_TOOL_RESULT_MAX_CHARS
    assert "snippet" not in payload


def _check_file_tool_paths_bound_the_serialized_envelope() -> None:
    path = "/".join("\u0001" * 120 for _ in range(30))
    with pytest.raises(ValidationError, match="expands beyond its result envelope"):
        REGISTRY.get("write").input_model.model_validate(
            {
                "file_path": path,
                "content": "x",
            }
        )


async def test_share_file_without_a_secret_fails_loud_and_writes_nothing(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, artifact_secret="")
    with pytest.raises(RuntimeError, match="not configured"):
        await run(
            "share_file",
            ctx,
            files=[{"file_path": "report.txt"}],
        )
    assert not (tmp_path / "artifacts").exists()


async def _local_session(workspace: Path) -> SandboxSession:
    carrier = LocalCarrier()
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path=str(workspace),
    )
    return SandboxSession(carrier=carrier, handle=await carrier.create(spec))


async def _preflight(session: SandboxSession, scoped: str) -> ExecResult:
    return await session.bash(SHARE_PREFLIGHT_CMD.format(path=shlex.quote(scoped)))


async def test_the_share_preflight_measures_the_workspace_file(tmp_path: Path) -> None:
    """What the upload is bound to comes off stock tools every carrier has — `wc` and `openssl` —
    so the same one command measures the file in the container and on a member's own machine."""
    workspace = tmp_path / "workspace"
    session = await _local_session(workspace)
    payload = b"report bytes\n"
    (workspace / "report.txt").write_bytes(payload)

    preflight = await _preflight(session, "/workspace/report.txt")

    assert preflight.exit_code == 0, preflight.stderr
    assert json.loads(preflight.stdout) == {
        "size": len(payload),
        "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
        "is_text": True,
    }


async def test_the_share_preflight_reports_binary_content(tmp_path: Path) -> None:
    """A NUL in the first window marks the file binary, so a picture is not previewed as text."""
    workspace = tmp_path / "workspace"
    session = await _local_session(workspace)
    (workspace / "blob.bin").write_bytes(b"head\x00tail")

    preflight = await _preflight(session, "/workspace/blob.bin")

    assert preflight.exit_code == 0, preflight.stderr
    assert json.loads(preflight.stdout)["is_text"] is False


def _check_share_traversal_is_refused_before_the_preflight() -> None:
    """A share is the one path a produced file leaves the sandbox on."""
    with pytest.raises(ValueError, match="escapes"):
        workspace_path("sub/../../outside.txt")


async def test_ask_user_returns_the_structured_question_and_the_end_turn_directive(
    tmp_path: Path,
) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    result = await run(
        "ask_user",
        ctx,
        title="Scope",
        questions=[{"question": "Which environment?", "header": "Deploy"}],
    )
    assert result.is_error is False
    text = result.content[0].text
    assert "arrives as the next message" in text
    payload = json.loads(text.split("\n", 1)[1])
    assert payload["awaiting"] == "question"
    assert payload["title"] == "Scope"
    assert payload["questions"] == [{"question": "Which environment?", "header": "Deploy"}]


async def test_ask_user_folds_confirmation_as_a_question_with_options(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    result = await run(
        "ask_user",
        ctx,
        title="Confirm send",
        questions=[
            {
                "question": "Send the email to the whole team?",
                "options": [{"label": "Send"}, {"label": "Cancel"}],
            }
        ],
    )
    payload = json.loads(result.content[0].text.split("\n", 1)[1])
    assert [option["label"] for option in payload["questions"][0]["options"]] == [
        "Send",
        "Cancel",
    ]


async def test_ask_user_requires_at_least_one_question(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValidationError):
        await run("ask_user", ctx, title="Empty", questions=[])


async def _load_skill(ctx: ToolContext, name: str):
    tool = REGISTRY.get("load_skill")
    return await tool.handler(ctx, tool.input_model.model_validate({"name": name}))


async def test_load_skill_reads_bundled_files_from_ufo_home_and_returns_instructions(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    ctx = make_context(sandbox, tmp_path)
    result = await _load_skill(ctx, "sandbox")
    assert result.is_error is False
    assert "# Skill: sandbox" in result.content[0].text
    assert CORE_SKILL_REGISTRY.named("sandbox").instructions in result.content[0].text
    assert "Loaded files:\n$UFO_HOME/skills/\n  sandbox/\n    SKILL.md" in result.content[0].text
    assert sandbox.files == {}


async def test_load_skill_mounts_a_deploy_skill_from_the_local_system_bundle(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox(available_system_skills=frozenset({"sandbox"}))
    ctx = make_context(sandbox, tmp_path)

    result = await _load_skill(ctx, "sandbox")

    assert result.is_error is False
    assert sandbox.files == {}
    assert sandbox.skill_loads == [
        {
            "system": {"sandbox": CORE_SKILL_REGISTRY.named("sandbox").content_digest()},
            "user": {},
        }
    ]


async def test_load_skill_installs_a_generated_deploy_skill_from_its_payload(
    tmp_path: Path,
) -> None:
    generated = RuntimeSkill(
        name="model-catalog",
        description="the models",
        instructions="MODEL CATALOG",
        raw_skill_md="---\nname: model-catalog\ndescription: the models\n---\nMODEL CATALOG\n",
    )
    sandbox = FakeSandbox()
    ctx = replace(
        make_context(sandbox, tmp_path),
        skills=skill_registry((), (generated,)),
    )

    result = await _load_skill(ctx, generated.name)

    assert result.is_error is False
    assert sandbox.skill_loads[0]["system"] == {}
    user = sandbox.skill_loads[0]["user"]
    assert isinstance(user, dict)
    assert user[generated.name]["digest"] == generated.content_digest()
    assert sandbox.files["$UFO_HOME/skills/model-catalog/SKILL.md"] == (
        generated.raw_skill_md.encode()
    )


async def test_load_skill_mounts_and_injects_the_skill_then_each_dependency(
    tmp_path: Path,
) -> None:
    """One load does the same three things per skill — mount the files, inject the workflow, print
    the mounted tree — for the asked-for skill first, then everything it depends on."""
    base = RuntimeSkill(name="base", description="base skill", instructions="BASE BODY")
    leaf = RuntimeSkill(
        name="leaf", description="leaf skill", instructions="LEAF BODY", depends=("base",)
    )
    sandbox = FakeSandbox()
    ctx = replace(
        make_context(sandbox, tmp_path),
        skills=SkillRegistry({"base": base, "leaf": leaf}, bundled_names=frozenset()),
    )

    text = (await _load_skill(ctx, "leaf")).content[0].text

    assert text.index("# Skill: leaf") < text.index("# Skill: base")
    assert text.index("LEAF BODY") < text.index("BASE BODY") < text.index("Loaded files:")
    assert text.count("Loaded files:") == 1
    assert "$UFO_HOME/skills/base/SKILL.md" in sandbox.files
    assert "leaf skill" not in text


async def test_a_second_load_of_a_skill_in_context_resolves_without_repeating_its_workflow(
    tmp_path: Path,
) -> None:
    """A repeated load verifies the local files and names the workflow instead of repeating it."""
    sandbox = FakeSandbox()
    ctx = make_context(sandbox, tmp_path)
    skill = CORE_SKILL_REGISTRY.named("sandbox")

    first = (await _load_skill(ctx, "sandbox")).content[0].text
    ctx.loaded_skills.reseed((ctx.skills.closure("sandbox"),))
    sandbox.files.clear()
    repeat = await _load_skill(ctx, "sandbox")

    text = repeat.content[0].text
    tree = first[first.index("Loaded files:") :]
    assert repeat.is_error is False
    assert skill.instructions not in text
    assert text == f"Already in context above, not repeated: sandbox\n\n{tree}"
    assert sandbox.files == {}


async def test_a_load_whose_dependency_is_in_context_still_injects_the_new_workflow(
    tmp_path: Path,
) -> None:
    """Two skills sharing a dependency: the second load pays for its own workflow only."""
    base = RuntimeSkill(name="base", description="base skill", instructions="BASE BODY")
    first_skill = RuntimeSkill(
        name="first", description="first skill", instructions="FIRST BODY", depends=("base",)
    )
    second_skill = RuntimeSkill(
        name="second", description="second skill", instructions="SECOND BODY", depends=("base",)
    )
    sandbox = FakeSandbox()
    ctx = replace(
        make_context(sandbox, tmp_path),
        skills=SkillRegistry(
            {"base": base, "first": first_skill, "second": second_skill},
            bundled_names=frozenset(),
        ),
    )

    await _load_skill(ctx, "first")
    ctx.loaded_skills.reseed((ctx.skills.closure("first"),))
    text = (await _load_skill(ctx, "second")).content[0].text

    assert "# Skill: second\n\nSECOND BODY" in text
    assert "BASE BODY" not in text
    assert "Already in context above, not repeated: base" in text
    assert text.endswith(
        "Loaded files:\n$UFO_HOME/skills/\n  base/\n    SKILL.md\n  second/\n    SKILL.md"
    )


async def test_load_skill_unknown_name_fails_loud(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError, match="unknown skill 'nope'"):
        await _load_skill(ctx, "nope")


def _member_tier(*skills: RuntimeSkill, missing: tuple[SkillCard, ...] = ()) -> SkillRegistry:
    rows = {skill.name: skill for skill in skills}

    async def materialize(name: str) -> RuntimeSkill | None:
        return rows.get(name)

    cards = (*(skill.card() for skill in skills), *missing)
    return CORE_SKILL_REGISTRY.with_member(cards, materialize)


async def test_load_skill_mounts_a_member_skill_from_its_materialized_row(tmp_path: Path) -> None:
    saved = RuntimeSkill(
        name="greet",
        description="say hi",
        instructions="GREET BODY",
        files=(("notes.md", b"kept"),),
        raw_skill_md="---\nname: greet\ndescription: say hi\n---\nGREET BODY\n",
    )
    sandbox = FakeSandbox()
    ctx = replace(make_context(sandbox, tmp_path), skills=_member_tier(saved))

    text = (await _load_skill(ctx, "greet")).content[0].text

    assert "# Skill: greet\n\nGREET BODY" in text
    assert sandbox.files["$UFO_HOME/skills/greet/SKILL.md"] == saved.raw_skill_md.encode()
    assert sandbox.files["$UFO_HOME/skills/greet/notes.md"] == b"kept"


async def test_load_skill_member_tier_does_not_vary_with_the_tool_speaker(tmp_path: Path) -> None:
    saved = RuntimeSkill(
        name="greet",
        description="say hi",
        instructions="GREET BODY",
        raw_skill_md="---\nname: greet\ndescription: say hi\n---\nGREET BODY\n",
    )
    skills = _member_tier(saved)
    outputs: list[str] = []
    for speaker in (None, uuid4()):
        ctx = replace(
            make_context(FakeSandbox(), tmp_path),
            skills=skills,
            speaker_member_id=speaker,
        )
        outputs.append((await _load_skill(ctx, "greet")).content[0].text)

    assert REGISTRY.get("load_skill").binds_member_authority is False
    assert outputs[0] == outputs[1]
    assert skills.closure("greet")[0].card == saved.card()


async def test_member_skill_dependencies_use_the_local_system_bundle(tmp_path: Path) -> None:
    saved = RuntimeSkill(
        name="greet",
        description="say hi",
        instructions="GREET BODY",
        depends=("sandbox",),
        raw_skill_md="---\nname: greet\ndescription: say hi\n---\nGREET BODY\n",
    )
    sandbox = FakeSandbox(available_system_skills=frozenset({"sandbox"}))
    ctx = replace(make_context(sandbox, tmp_path), skills=_member_tier(saved))

    await _load_skill(ctx, "greet")

    assert len(sandbox.skill_loads) == 1
    assert sandbox.skill_loads[0]["system"] == {
        "sandbox": CORE_SKILL_REGISTRY.named("sandbox").content_digest()
    }
    user = sandbox.skill_loads[0]["user"]
    assert isinstance(user, dict)
    assert user["greet"]["digest"] == saved.content_digest()
    assert "$UFO_HOME/skills/greet/SKILL.md" in sandbox.files
    assert "$UFO_HOME/skills/sandbox/SKILL.md" not in sandbox.files


async def test_load_skill_of_a_vanished_member_row_fails_loud(tmp_path: Path) -> None:
    ctx = replace(
        make_context(FakeSandbox(), tmp_path),
        skills=_member_tier(missing=(SkillCard(name="gone", description="d"),)),
    )
    with pytest.raises(ValueError, match="skill 'gone' is no longer available"):
        await _load_skill(ctx, "gone")


async def test_cancel_spawn_cancels_and_reports_status(tmp_path: Path) -> None:
    child = uuid4()
    control = StubSubagentControl()
    ctx = make_context(FakeSandbox(), tmp_path, subagents=control)
    result = await run("cancel_spawn", ctx, spawn_id=str(child))
    assert control.cancelled == [child]
    assert json.loads(result.content[0].text) == {
        "spawn_id": str(child),
        "status": "cancelled",
    }


async def test_cancel_spawn_malformed_id_raises(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, subagents=StubSubagentControl())
    with pytest.raises(ValueError):
        await run("cancel_spawn", ctx, spawn_id="not-a-uuid")


async def test_message_spawn_forwards_the_message_keyed_on_the_call(tmp_path: Path) -> None:
    child = uuid4()
    control = StubSubagentControl()
    assert REGISTRY.get("message_spawn").side_effecting is True
    request_ref = uuid4()
    ctx = replace(
        make_context(FakeSandbox(), tmp_path, subagents=control),
        idempotency_key="turn-1/message_spawn/call-2",
        requesting_message_ref=request_ref,
    )
    result = await run(
        "message_spawn",
        ctx,
        spawn_id=str(child),
        message="also check X",
    )
    assert control.messaged == [(child, "also check X", "turn-1/message_spawn/call-2", request_ref)]
    assert json.loads(result.content[0].text) == {"spawn_id": str(child), "status": "queued"}


async def test_message_spawn_without_an_idempotency_key_fails_loud(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, subagents=StubSubagentControl())
    with pytest.raises(RuntimeError, match="idempotency key"):
        await run(
            "message_spawn",
            ctx,
            spawn_id=str(uuid4()),
            message="also check X",
        )


class _SpawnTask(BaseModel):
    task: str


class _SpawnOutput(BaseModel):
    result: str


def _spawn_profile(name: str) -> SubagentProfile:
    return SubagentProfile(
        name=name,
        prompt=f"{name} instructions",
        tool_names=("read",),
        input_model=_SpawnTask,
        output_model=_SpawnOutput,
    )


class _IdleSpawnClient:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("an unknown profile must be rejected before any child is enqueued")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spawn_unknown_target_is_an_error_naming_the_valid_targets(
    tmp_path: Path, db: None
) -> None:
    parent = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    subagents = Subagents(
        client=_IdleSpawnClient(),
        registry=SubagentRegistry((_spawn_profile("research"), _spawn_profile("coding"))),
        parent=parent,
        audience=conversation_audience(None),
        sessions=None,
    )
    ctx = make_context(FakeSandbox(), tmp_path, spawn=subagents.spawn)
    result = await run(
        "spawn",
        ctx,
        target="assistant",
        payload={"task": "x"},
    )
    assert result.is_error
    text = result.content[0].text
    assert "assistant" in text
    assert "research" in text


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spawn_wrong_payload_is_an_error_naming_the_targets_keys(
    tmp_path: Path, db: None
) -> None:
    """A payload the target refuses is the same class of recoverable mistake as a bad target
    name, and answers with the same two facts: what refused it, and what that target takes."""
    parent = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=0,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    subagents = Subagents(
        client=_IdleSpawnClient(),
        registry=SubagentRegistry((_spawn_profile("coding"),)),
        parent=parent,
        audience=conversation_audience(None),
        sessions=None,
    )
    ctx = make_context(FakeSandbox(), tmp_path, spawn=subagents.spawn)

    wrong_key = await run("spawn", ctx, target="coding", payload={"objective": "review it"})
    empty = await run("spawn", ctx, target="coding", payload={})

    for result in (wrong_key, empty):
        text = result.content[0].text
        assert result.is_error
        assert "coding" in text
        assert "`task`" in text
        assert "task: Field required" in text
        assert "_SpawnTask" not in text


async def test_spawn_keys_the_child_on_the_calls_idempotency_key(tmp_path: Path) -> None:
    recorded: list[tuple[str | None, bool, UUID | None, UUID | None]] = []

    async def _record(
        target: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
        detach_on_arrival: bool = False,
        model: str | None = None,
        *,
        requester_member_id: UUID | None = None,
        requesting_message_ref: UUID | None = None,
    ) -> SpawnResult:
        recorded.append((dedup_key, delivers_result, requester_member_id, requesting_message_ref))
        return SpawnResult(turn_id=uuid4(), conversation_id=uuid4(), output=None)

    assert REGISTRY.get("spawn").side_effecting is True
    speaker = uuid4()
    request_ref = uuid4()
    ctx = replace(
        make_context(FakeSandbox(), tmp_path, spawn=_record),
        idempotency_key="turn-1/spawn/call-1",
        speaker_member_id=speaker,
        requesting_message_ref=request_ref,
    )
    result = await run(
        "spawn",
        ctx,
        target="research",
        payload={"task": "x"},
        background=True,
    )
    assert recorded == [("turn-1/spawn/call-1", True, speaker, request_ref)]
    assert not result.is_error

    await run(
        "spawn",
        ctx,
        target="research",
        payload={"task": "x"},
    )
    assert recorded[1] == ("turn-1/spawn/call-1", False, speaker, request_ref)


async def test_spawn_returns_route_changes_beside_the_validated_output(
    tmp_path: Path,
) -> None:
    async def _record(
        target: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
        detach_on_arrival: bool = False,
        model: str | None = None,
        *,
        requester_member_id: UUID | None = None,
        requesting_message_ref: UUID | None = None,
    ) -> SpawnResult:
        return SpawnResult(
            turn_id=uuid4(),
            conversation_id=uuid4(),
            output=_SpawnOutput(result="done"),
            terminal=TerminalFrame(
                status="done",
                text='{"result":"done"}',
                model_route_changes=(
                    ModelRouteChange(
                        failed_model="claude-opus-5-5",
                        replacement_model="gpt-5.6-sol",
                        failure="unavailable",
                    ),
                ),
            ),
            untrusted=True,
        )

    result = await run(
        "spawn",
        make_context(FakeSandbox(), tmp_path, spawn=_record),
        target="research",
        payload={"task": "x"},
    )

    assert result.content[0].text == '{"result":"done"}'
    assert result.untrusted
    assert result.model_route_changes == (
        ModelRouteChange(
            failed_model="claude-opus-5-5",
            replacement_model="gpt-5.6-sol",
            failure="unavailable",
        ),
    )


async def test_spawn_carries_the_calls_model_to_the_child_and_surfaces_its_refusal(
    tmp_path: Path,
) -> None:
    asked: list[tuple[str | None, UUID | None]] = []

    async def _record(
        target: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
        detach_on_arrival: bool = False,
        model: str | None = None,
        *,
        requester_member_id: UUID | None = None,
        requesting_message_ref: UUID | None = None,
    ) -> SpawnResult:
        asked.append((model, requester_member_id))
        if model == "gpt-5.6-sol":
            return SpawnResult(turn_id=uuid4(), conversation_id=uuid4(), output=None)
        raise SpawnModelRejected.unknown(model or "", ("gpt-5.6-sol",))

    speaker = uuid4()
    ctx = replace(
        make_context(FakeSandbox(), tmp_path, spawn=_record),
        speaker_member_id=speaker,
    )

    pinned = await run(
        "spawn",
        ctx,
        target="research",
        payload={"task": "x"},
        background=True,
        model="gpt-5.6-sol",
    )
    refused = await run(
        "spawn",
        ctx,
        target="research",
        payload={"task": "x"},
        background=True,
        model="gpt-9",
    )

    assert asked == [("gpt-5.6-sol", speaker), ("gpt-9", speaker)]
    assert not pinned.is_error
    assert refused.is_error
    assert "gpt-9" in refused.content[0].text
    assert "gpt-5.6-sol" in refused.content[0].text


def test_spawn_without_a_payload_is_refused_by_this_tools_own_field() -> None:
    """A call that names a target and no payload is invalid at this boundary, and the refusal
    carries the word the caller has to fix."""
    spawn = next(schema for schema in REGISTRY.schemas() if schema.name == "spawn")
    assert "payload" in spawn.input_schema["required"]

    with pytest.raises(ValidationError) as refusal:
        SpawnInput.model_validate({"target": "profile:coding", "background": True, "name": "one"})

    assert [error["loc"] for error in refusal.value.errors()] == [("payload",)]
    assert SpawnInput.model_validate({"target": "profile:coding", "payload": {}}).payload == {}


SEAL_MEMBER = UUID("11111111-1111-1111-1111-111111111111")
SEAL_ROOM = room_audience("slack", "CROOM")
SEAL_FOREIGN = foreign_room_audience("slack", "CCONNECT")


def test_the_audience_seal_holds_for_every_audience_and_speaker() -> None:
    """The whole disclosure contract of the two properties every read and write scopes on, pinned
    here rather than inferred from any one consumer."""
    for audience, speaker, subjects, write_to in [
        (SHARED_AUDIENCE, None, {"shared"}, SHARED_AUDIENCE),
        (
            SHARED_AUDIENCE,
            SEAL_MEMBER,
            {"shared", member_subject(SEAL_MEMBER)},
            SHARED_AUDIENCE,
        ),
        (
            conversation_audience(SEAL_MEMBER),
            None,
            {"shared", member_subject(SEAL_MEMBER)},
            f"member:{SEAL_MEMBER}",
        ),
        (SEAL_ROOM, None, {"shared", str(SEAL_ROOM)}, str(SEAL_ROOM)),
        (
            SEAL_ROOM,
            SEAL_MEMBER,
            {"shared", str(SEAL_ROOM), member_subject(SEAL_MEMBER)},
            str(SEAL_ROOM),
        ),
        (SEAL_FOREIGN, None, {str(SEAL_FOREIGN)}, str(SEAL_FOREIGN)),
        (
            SEAL_FOREIGN,
            SEAL_MEMBER,
            {str(SEAL_FOREIGN), member_subject(SEAL_MEMBER)},
            str(SEAL_FOREIGN),
        ),
    ]:
        ctx = ToolContext(
            sandbox=None,
            blob=None,
            turn=Turn(
                id=uuid4(),
                workspace_id=uuid4(),
                conversation_id=uuid4(),
                agent_id=uuid4(),
                seq=0,
                status="running",
                inbound="hello",
                created_at=datetime(2026, 7, 9, tzinfo=UTC),
            ),
            agent=Agent(prompt="be terse", model="claude-opus-4-8"),
            spawn=_unavailable_spawn,
            speaker_member_id=speaker,
            audience=audience,
            artifact_token_secret=ARTIFACT_SECRET,
        )

        assert ctx.read_subjects == frozenset(subjects)
        assert str(ctx.effective_audience) == write_to


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_share_artifact_hands_the_member_bytes_a_tool_rendered(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    published = 0

    async def publish_artifacts() -> None:
        nonlocal published
        published += 1

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
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
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
                queue_key="share-bytes",
                member_id=member_id,
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
                status="running",
                inbound="render it",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    ctx = ToolContext(
        sandbox=None,
        blob=WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
        turn=Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="render it",
            speaker_member_id=member_id,
            created_at=datetime(2026, 8, 20, tzinfo=UTC),
        ),
        agent=Agent(prompt="be terse", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=member_id,
        audience=conversation_audience(None),
        artifact_token_secret="secret",
        publish_artifacts=publish_artifacts,
    )
    preview = b"\x89PNG the preview"
    preview_key = f"artifacts/{uuid4()}/design-preview.png"
    with ws(workspace_id):
        await ctx.blob.put(preview_key, preview)
        await ctx.share_artifact(
            "design.svg",
            b"<svg></svg>",
            "A caption.",
            preview=StoredPreview(blob_key=preview_key, size_bytes=len(preview)),
        )
        with pytest.raises(ValueError, match="exceeds"):
            await ctx.share_artifact("huge.png", b"x" * (SHARED_BYTES_LIMIT + 1))
        with pytest.raises(ValueError, match=r"preview.*exceeds"):
            await ctx.share_artifact(
                "design.svg",
                b"<svg></svg>",
                preview=StoredPreview(blob_key=preview_key, size_bytes=IMAGE_PREVIEW_MAX_BYTES + 1),
            )
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.turn_id,
                        tables.shared_artifact.c.member_id,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.size_bytes,
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.preview_blob_key,
                        tables.shared_artifact.c.preview_media_type,
                        tables.shared_artifact.c.preview_size_bytes,
                    )
                )
            ).one()
        stored = await ctx.blob.get(row.blob_key)
        stored_preview = await ctx.blob.get(row.preview_blob_key)
        opened = 0

        @asynccontextmanager
        async def commit_then_cancel() -> AsyncIterator[AsyncConnection]:
            nonlocal opened
            opened += 1
            async with workspace_tx() as connection:
                yield connection
            if opened == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(tool_context, "workspace_tx", commit_then_cancel)
        with pytest.raises(asyncio.CancelledError):
            await ctx.share_artifact("cancelled.svg", b"<svg>kept</svg>")
        async with workspace_tx() as connection:
            cancelled_key = (
                await connection.execute(
                    sa.select(tables.shared_artifact.c.blob_key).where(
                        tables.shared_artifact.c.filename == "cancelled.svg"
                    )
                )
            ).scalar_one()
        assert await ctx.blob.exists(cancelled_key)
    assert row.turn_id == turn_id
    assert row.member_id == member_id
    assert row.filename == "design.svg"
    assert row.subject == "A caption."
    assert row.media_type == "image/svg+xml"
    assert row.size_bytes == len(b"<svg></svg>")
    assert row.preview_media_type == "image/png"
    assert row.preview_size_bytes == len(preview)
    assert stored == b"<svg></svg>"
    assert stored_preview == preview
    assert published == 1


def test_tool_static_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 13
    for check in checks:
        check()


GATED_LEDGER = Ledger(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),))


async def _seed_turn_rows(turn: Turn) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=turn.workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=turn.agent_id,
                workspace_id=turn.workspace_id,
                name="assistant",
                prompt="p",
                model="auto",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=turn.conversation_id,
                workspace_id=turn.workspace_id,
                agent_id=turn.agent_id,
                surface="cli",
                queue_key="meter",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                {
                    **{
                        key: value
                        for key, value in turn.model_dump().items()
                        if key in tables.turn.c
                    },
                    "updated_at": sa.func.now(),
                }
            )
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
@pytest.mark.parametrize("byok", [False, True])
async def test_provider_tokens_keep_call_identity_and_funding(
    db: None, tmp_path: Path, byok: bool
) -> None:
    ctx = replace(make_context(FakeSandbox(), tmp_path), ledger=GATED_LEDGER)
    turn = ctx.turn.model_copy(update={"seq": 1})
    price = ModelPrice(42_000, 0, 0, 0, 0)
    usage = Usage(input_tokens=1000, output_tokens=20)
    call_id = uuid4()
    with ws(turn.workspace_id):
        await _seed_turn_rows(turn)
        await ctx.meter_tokens(call_id, "provider-test", usage, price, byok=byok)
        await ctx.meter_tokens(call_id, "provider-test", usage, price, byok=byok)
        await ctx.meter_tokens(uuid4(), "provider-test", usage, price, byok=byok)
        async with workspace_tx() as connection:
            rows = (await connection.execute(sa.select(tables.ledger))).mappings().all()
        assert len(rows) == 2
        assert all(row["turn_id"] == turn.id for row in rows)
        assert all(row["workspace_id"] == turn.workspace_id for row in rows)
        assert all(row["input_tokens"] == 1000 and row["output_tokens"] == 20 for row in rows)
        assert all(row["priced_micro_usd"] == 42 for row in rows)
        assert all(row["byok"] == byok for row in rows)
        async with workspace_tx() as connection:
            charges = (await connection.execute(sa.select(CHARGE_TABLE))).mappings().all()
        assert sorted(charge["ledger_id"] for charge in charges) == sorted(
            row["id"] for row in rows
        )
        assert all(
            charge["turn_id"] == turn.id
            and charge["delta_micro_usd"] == 42
            and charge["platform_paid"] is not byok
            for charge in charges
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_generated_media_charges_each_increment_as_platform_paid(
    db: None, tmp_path: Path
) -> None:
    ctx = replace(make_context(FakeSandbox(), tmp_path), ledger=GATED_LEDGER)
    turn = ctx.turn.model_copy(update={"seq": 1})
    with ws(turn.workspace_id):
        await _seed_turn_rows(turn)
        await ctx.meter_images("image-model", 2, 40_000)
        await ctx.meter_images("image-model", 1, 20_000)
        await ctx.meter_videos("video-model", 1, 60_000)
        async with workspace_tx() as connection:
            priced = (
                await connection.execute(sa.select(sa.func.sum(tables.ledger.c.priced_micro_usd)))
            ).scalar_one()
            charges = (
                await connection.execute(
                    sa.select(
                        CHARGE_TABLE.c.dimension,
                        CHARGE_TABLE.c.delta_micro_usd,
                        CHARGE_TABLE.c.platform_paid,
                    )
                )
            ).all()
    assert [tuple(charge) for charge in charges] == [
        ("images", 40_000, True),
        ("images", 20_000, True),
        ("videos", 60_000, True),
    ]
    assert sum(charge.delta_micro_usd for charge in charges) == priced
