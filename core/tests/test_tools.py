import asyncio
import fcntl
import hashlib
import json
import shlex
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from ufo.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.blob import FilesystemBlobStore
from ufo.ext.manifest import SubagentProfile
from ufo.loop.subagents import SubagentRegistry, Subagents
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import (
    ExecResult,
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
    workspace_path,
)
from ufo.schema.records import Agent, Turn
from ufo.skills.runtime import CORE_SKILL_REGISTRY, RuntimeSkill, SkillRegistry
from ufo.subjects import member_subject
from ufo.tools.builtins import (
    BUILTIN_TOOLS,
    FILE_TOOL_RESULT_MAX_CHARS,
    SHARE_PREFLIGHT_CMD,
    _file_tool_result,
)
from ufo.tools.context import Spawn, SpawnResult, SubagentStatus, ToolContext, ToolResult
from ufo.tools.registry import REQUESTED_BY, ToolDef, ToolRegistry

REGISTRY = ToolRegistry(BUILTIN_TOOLS)
ARTIFACT_SECRET = "tools-test-secret"


@dataclass
class FakeSandbox:
    bash_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    files: dict[str, bytes] = field(default_factory=dict)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return self.bash_result

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        return self.bash_result

    async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]:
        raise AssertionError(f"run_sbxfs({op}) must not run once a guard has rejected the call")

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content


async def _unavailable_spawn(
    profile: str,
    payload: dict[str, object],
    background: bool = False,
    dedup_key: str | None = None,
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


@dataclass
class StubSubagentControl:
    """Stand-in for the Subagents workflow: records the ids the lifecycle tools pass and returns
    scripted statuses, so these tests assert the tool's id-parsing and result shaping — the DBOS
    wait/cancel behavior is proven against the real workflow, never against this stand-in."""

    statuses: dict[UUID, SubagentStatus] = field(default_factory=dict)
    waited: list[tuple[UUID, ...]] = field(default_factory=list)
    cancelled: list[UUID] = field(default_factory=list)
    messaged: list[tuple[UUID, str, str]] = field(default_factory=list)

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

    async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus:
        self.messaged.append((turn_id, text, dedup_key))
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


def test_registry_rejects_duplicate_names() -> None:
    bash = REGISTRY.get("bash")
    with pytest.raises(ValueError, match="duplicate tool names: bash"):
        ToolRegistry((bash, bash))


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown tool: nope"):
        REGISTRY.get("nope")


def test_registry_get_returns_named_tool() -> None:
    assert REGISTRY.get("edit").name == "edit"


def test_builtin_tools_are_trusted_by_default() -> None:
    assert all(tool.untrusted is False for tool in BUILTIN_TOOLS)


def test_only_position_read_final_acts_barrier_a_rounds_dispatch() -> None:
    """Same-round calls dispatch concurrently unless the round is read by the call's position:
    ask_user, connect_account, and request_credentials land as the round's last call
    (`_final_act`), so each stays an in-order barrier; every other tool owns its own target and
    parallelizes."""
    barriers = {tool.name for tool in BUILTIN_TOOLS if not tool.parallel_safe}
    assert barriers == {"ask_user", "connect_account", "request_credentials"}


def test_registry_schemas_cover_every_tool() -> None:
    schemas = REGISTRY.schemas()
    assert {schema.name for schema in schemas} == {
        "add_member",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "share_file",
        "spawn_subagent",
        "ask_user",
        "request_credentials",
        "load_skill",
        "connect_account",
        "cancel_subagent",
        "message_subagent",
    }
    bash = next(schema for schema in schemas if schema.name == "bash")
    assert "command" in bash.input_schema["properties"]
    assert all(REQUESTED_BY in schema.input_schema["properties"] for schema in schemas)
    assert bash.input_schema["properties"][REQUESTED_BY]["description"] == (
        "Message ref that explicitly requested this call. Required for any member-specific "
        "authority or capability, including admin actions; omit only for conversation-common work."
    )


def test_registry_reserves_the_message_authority_field() -> None:
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
    result = await run("bash", ctx, command="do it", user_description="running a check")
    assert result.content[0].text == "outerr\nexit code: 1"
    assert result.is_error is True


async def test_bash_silent_failure_reports_the_exit_code(tmp_path: Path) -> None:
    sandbox = FakeSandbox(bash_result=ExecResult(stdout="", stderr="", exit_code=56))
    ctx = make_context(sandbox, tmp_path)
    result = await run(
        "bash", ctx, command="curl -s https://blocked.example", user_description="fetching a page"
    )
    assert result.content[0].text == "exit code: 56"
    assert result.is_error is True


async def test_bash_zero_exit_is_not_error(tmp_path: Path) -> None:
    sandbox = FakeSandbox(bash_result=ExecResult(stdout="ok", stderr="", exit_code=0))
    ctx = make_context(sandbox, tmp_path)
    result = await run("bash", ctx, command="echo ok", user_description="running a check")
    assert result.is_error is False
    assert result.content[0].text == "ok"


def test_edit_and_write_state_the_read_first_rule_in_their_descriptions() -> None:
    """Both tools refuse a path the turn has not read, and the refusal is a hard raise. A rule
    enforced in code and written only in a profile prompt is one the model carries across every
    round from memory; the description is the sentence it re-reads at the moment it calls."""
    schemas = {schema.name: schema for schema in REGISTRY.schemas()}
    assert "Read the file first" in schemas["edit"].description
    assert "REFUSED" in schemas["edit"].description
    assert "Read the file first if it already exists" in schemas["write"].description
    assert "REFUSED" in schemas["write"].description


async def test_edit_requires_read_before_write(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError, match="must be read before it is edited"):
        await run(
            "edit",
            ctx,
            file_path="code.py",
            edits=[{"old_string": "x = 1", "new_string": "x = 2"}],
            user_description="tweaking the script",
        )


async def test_write_and_edit_land_bounded_results_through_the_guard(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM-BYTES"),
            run_token="run-token",
        )
    )
    ctx = make_context(SandboxSession(carrier=carrier, handle=handle), tmp_path)
    written = await run(
        "write",
        ctx,
        file_path="notes.txt",
        content="old /workspace path\n",
        user_description="writing notes",
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
        user_description="editing notes",
    )
    edited_payload = json.loads(edited.content[0].text)
    assert edited_payload["replacements"] == 1
    assert "old /workspace/final path" in edited_payload["snippet"]
    assert (workspace / "notes.txt").read_text() == "old /workspace/final path\n"

    await ctx.sandbox.write_file("large.txt", b"old\n" * 10_000)
    ctx.read_paths.add("large.txt")
    large = await run(
        "write",
        ctx,
        file_path="large.txt",
        content="new\n" * 10_000,
        user_description="writing a large file",
    )
    assert json.loads(large.content[0].text)["size_bytes"] == 40_000
    assert len(large.content[0].text) <= FILE_TOOL_RESULT_MAX_CHARS
    assert (workspace / "large.txt").read_text() == "new\n" * 10_000

    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        await run(
            "write",
            ctx,
            file_path="escape/file.txt",
            content="outside\n",
            user_description="writing outside",
        )
    assert not (outside / "file.txt").exists()

    outside_file = outside / "target.txt"
    outside_file.write_text("outside\n")
    (workspace / "link.txt").symlink_to(outside_file)
    ctx.read_paths.add("link.txt")
    with pytest.raises(ValueError, match="not a regular file"):
        await run(
            "write",
            ctx,
            file_path="link.txt",
            content="inside\n",
            user_description="writing a linked file",
        )
    assert outside_file.read_text() == "outside\n"

    injected = await run(
        "write",
        ctx,
        file_path="name\n+++ injected",
        content="safe\n",
        user_description="writing a file",
    )
    assert json.loads(injected.content[0].text)["path"] == "name\n+++ injected"
    assert (workspace / "name\n+++ injected").read_text() == "safe\n"


async def test_sbxfs_write_waits_for_the_shared_filesystem_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "shared"
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path=str(workspace),
        proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM-BYTES"),
        run_token="run-token",
    )
    first_carrier = LocalCarrier()
    second_carrier = LocalCarrier()
    first = make_context(
        SandboxSession(carrier=first_carrier, handle=await first_carrier.create(spec)), tmp_path
    )
    second = make_context(
        SandboxSession(carrier=second_carrier, handle=await second_carrier.create(spec)), tmp_path
    )
    await run(
        "write",
        first,
        file_path="shared.txt",
        content="old\n",
        user_description="writing shared content",
    )
    second.read_paths.add("shared.txt")
    lock_root = Path(tempfile.gettempdir()) / "ufo-sbxfs-locks"
    lock_root.mkdir(mode=0o700, exist_ok=True)
    lock_path = lock_root / hashlib.sha256(str(workspace / "shared.txt").encode()).hexdigest()
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pending = asyncio.create_task(
            run(
                "write",
                second,
                file_path="shared.txt",
                content="new\n",
                user_description="writing shared content",
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), 0.1)
        assert (workspace / "shared.txt").read_text() == "old\n"
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert json.loads((await pending).content[0].text)["created"] is False
    assert (workspace / "shared.txt").read_text() == "new\n"


def test_file_tool_result_bounds_escaped_paths() -> None:
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


def test_file_tool_paths_bound_the_serialized_envelope() -> None:
    path = "/".join("\u0001" * 120 for _ in range(30))
    with pytest.raises(ValidationError, match="expands beyond its result envelope"):
        REGISTRY.get("write").input_model.model_validate(
            {
                "file_path": path,
                "content": "x",
                "user_description": "writing a file",
            }
        )


async def test_share_file_without_a_secret_fails_loud_and_writes_nothing(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, artifact_secret="")
    with pytest.raises(RuntimeError, match="not configured"):
        await run("share_file", ctx, file_path="report.txt", user_description="sending the report")
    assert not (tmp_path / "artifacts").exists()


async def _local_session(workspace: Path) -> SandboxSession:
    carrier = LocalCarrier()
    spec = SandboxSpec(
        conversation_id=uuid4(),
        image_ref="ufo-sandbox:latest",
        workspace_host_path=str(workspace),
        proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM-BYTES"),
        run_token="run-token",
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


def test_share_traversal_is_refused_before_the_preflight() -> None:
    """A share is the one path a produced file leaves the sandbox on. Traversal is refused where the
    handler resolves the member's argument — `workspace_path` — so a scoped path reaches the
    preflight already confined, and NanoClaw's second escape stays closed."""
    with pytest.raises(ValueError, match="escapes"):
        workspace_path("sub/../../outside.txt")


async def test_the_share_preflight_refuses_a_planted_symlink(tmp_path: Path) -> None:
    """A share of a link the agent planted would copy the target's bytes into the blob store and
    mint a member download link for them — CVE-2026-56692 with one extra hop. The command refuses a
    symlink at the target, the one containment the copy-out needs."""
    workspace = tmp_path / "workspace"
    session = await _local_session(workspace)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host secret")
    (workspace / "report.txt").symlink_to(outside)

    preflight = await _preflight(session, "/workspace/report.txt")

    assert preflight.exit_code != 0
    assert "not a regular file" in preflight.stderr
    assert preflight.stdout == ""


async def test_ask_user_returns_the_structured_question_and_the_end_turn_directive(
    tmp_path: Path,
) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    result = await run(
        "ask_user",
        ctx,
        title="Scope",
        questions=[{"question": "Which environment?", "header": "Deploy"}],
        user_description="checking which environment",
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
        user_description="confirming the send",
    )
    payload = json.loads(result.content[0].text.split("\n", 1)[1])
    assert [option["label"] for option in payload["questions"][0]["options"]] == [
        "Send",
        "Cancel",
    ]


async def test_ask_user_requires_at_least_one_question(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValidationError):
        await run("ask_user", ctx, title="Empty", questions=[], user_description="asking")


async def _load_skill(ctx: ToolContext, name: str):
    tool = REGISTRY.get("load_skill")
    return await tool.handler(ctx, tool.input_model.model_validate({"name": name}))


async def test_load_skill_mounts_files_under_the_workspace_and_returns_instructions(
    tmp_path: Path,
) -> None:
    sandbox = FakeSandbox()
    ctx = make_context(sandbox, tmp_path)
    result = await _load_skill(ctx, "sandbox")
    assert result.is_error is False
    assert "# Skill: sandbox" in result.content[0].text
    assert CORE_SKILL_REGISTRY.named("sandbox").instructions in result.content[0].text
    mounted = sandbox.files["/workspace/.skills/sandbox/SKILL.md"]
    assert b"name: sandbox" in mounted


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
        make_context(sandbox, tmp_path), skills=SkillRegistry({"base": base, "leaf": leaf})
    )

    text = (await _load_skill(ctx, "leaf")).content[0].text

    assert text.index("# Skill: leaf") < text.index("# Skill: base")
    assert text.index("LEAF BODY") < text.index("BASE BODY") < text.index("Mounted files:")
    assert text.count("Mounted files:") == 1
    assert "/workspace/.skills/base/SKILL.md" in sandbox.files
    assert "leaf skill" not in text


async def test_a_second_load_of_a_skill_in_context_mounts_again_without_its_workflow(
    tmp_path: Path,
) -> None:
    """The agent re-loads a skill whose workflow it is already reading: the files are written again
    (cheap, idempotent, and it restores whatever the agent did to them), the tree still lists them,
    and the instructions are named rather than repeated. Never an error — re-loading is fair."""
    sandbox = FakeSandbox()
    ctx = make_context(sandbox, tmp_path)
    skill = CORE_SKILL_REGISTRY.named("sandbox")

    first = (await _load_skill(ctx, "sandbox")).content[0].text
    ctx.loaded_skills.reseed((ctx.skills.closure("sandbox"),))
    sandbox.files.clear()
    repeat = await _load_skill(ctx, "sandbox")

    text = repeat.content[0].text
    tree = first[first.index("Mounted files:") :]
    assert repeat.is_error is False
    assert skill.instructions not in text
    assert text == f"Already in context above, not repeated: sandbox\n\n{tree}"
    assert sandbox.files["/workspace/.skills/sandbox/SKILL.md"] == skill.raw_skill_md.encode()


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
        skills=SkillRegistry({"base": base, "first": first_skill, "second": second_skill}),
    )

    await _load_skill(ctx, "first")
    ctx.loaded_skills.reseed((ctx.skills.closure("first"),))
    text = (await _load_skill(ctx, "second")).content[0].text

    assert "# Skill: second\n\nSECOND BODY" in text
    assert "BASE BODY" not in text
    assert "Already in context above, not repeated: base" in text
    assert text.endswith(
        "Mounted files:\n/workspace/.skills/\n  base/\n    SKILL.md\n  second/\n    SKILL.md"
    )


async def test_load_skill_unknown_name_fails_loud(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError, match="unknown skill 'nope'"):
        await _load_skill(ctx, "nope")


async def test_cancel_subagent_cancels_and_reports_status(tmp_path: Path) -> None:
    child = uuid4()
    control = StubSubagentControl()
    ctx = make_context(FakeSandbox(), tmp_path, subagents=control)
    result = await run("cancel_subagent", ctx, subagent_id=str(child), user_description="x")
    assert control.cancelled == [child]
    assert json.loads(result.content[0].text) == {
        "subagent_id": str(child),
        "status": "cancelled",
    }


async def test_cancel_subagent_malformed_id_raises(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, subagents=StubSubagentControl())
    with pytest.raises(ValueError):
        await run("cancel_subagent", ctx, subagent_id="not-a-uuid", user_description="x")


async def test_message_subagent_forwards_the_message_keyed_on_the_call(tmp_path: Path) -> None:
    child = uuid4()
    control = StubSubagentControl()
    assert REGISTRY.get("message_subagent").side_effecting is True
    ctx = replace(
        make_context(FakeSandbox(), tmp_path, subagents=control),
        idempotency_key="turn-1/message_subagent/call-2",
    )
    result = await run(
        "message_subagent",
        ctx,
        subagent_id=str(child),
        message="also check X",
        user_description="x",
    )
    assert control.messaged == [(child, "also check X", "turn-1/message_subagent/call-2")]
    assert json.loads(result.content[0].text) == {"subagent_id": str(child), "status": "queued"}


async def test_message_subagent_without_an_idempotency_key_fails_loud(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, subagents=StubSubagentControl())
    with pytest.raises(RuntimeError, match="idempotency key"):
        await run(
            "message_subagent",
            ctx,
            subagent_id=str(uuid4()),
            message="also check X",
            user_description="x",
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


async def test_spawn_subagent_unknown_profile_is_an_error_naming_the_valid_profiles(
    tmp_path: Path,
) -> None:
    """A guessed profile name is a recoverable mistake: spawn_subagent returns an is_error result
    naming the bad profile and the registered ones (resolved through the real registry the live
    spawn dispatches against), so the model retries against a valid name instead of dead-ending on
    a bare KeyError."""
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
    )
    ctx = make_context(FakeSandbox(), tmp_path, spawn=subagents.spawn)
    result = await run(
        "spawn_subagent",
        ctx,
        profile="assistant",
        payload={"task": "x"},
        user_description="handing off the research",
    )
    assert result.is_error
    text = result.content[0].text
    assert "assistant" in text
    assert "research" in text


async def test_spawn_subagent_keys_the_child_on_the_calls_idempotency_key(tmp_path: Path) -> None:
    recorded: list[tuple[str | None, bool]] = []

    async def _record(
        profile: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
    ) -> SpawnResult:
        recorded.append((dedup_key, delivers_result))
        return SpawnResult(turn_id=uuid4(), conversation_id=uuid4(), output=None)

    assert REGISTRY.get("spawn_subagent").side_effecting is True
    ctx = replace(
        make_context(FakeSandbox(), tmp_path, spawn=_record),
        idempotency_key="turn-1/spawn_subagent/call-1",
    )
    result = await run(
        "spawn_subagent",
        ctx,
        profile="research",
        payload={"task": "x"},
        background=True,
        user_description="handing off the research",
    )
    assert recorded == [("turn-1/spawn_subagent/call-1", True)]
    assert not result.is_error

    await run(
        "spawn_subagent",
        ctx,
        profile="research",
        payload={"task": "x"},
        user_description="handing off the research",
    )
    assert recorded[1] == ("turn-1/spawn_subagent/call-1", False)


SEAL_MEMBER = UUID("11111111-1111-1111-1111-111111111111")
SEAL_ROOM = room_audience("slack", "CROOM")
SEAL_FOREIGN = foreign_room_audience("slack", "CCONNECT")


@pytest.mark.parametrize(
    ("audience", "speaker", "subjects", "write_to"),
    [
        (SHARED_AUDIENCE, None, {"shared"}, SHARED_AUDIENCE),
        (
            SHARED_AUDIENCE,
            SEAL_MEMBER,
            {"shared", member_subject(SEAL_MEMBER)},
            f"member:{SEAL_MEMBER}",
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
    ],
    ids=[
        "shared",
        "shared+speaker",
        "member",
        "room",
        "room+speaker",
        "foreign",
        "foreign+speaker",
    ],
)
def test_the_audience_seal_holds_for_every_audience_and_speaker(
    audience: Audience, speaker: UUID | None, subjects: set[str], write_to: str
) -> None:
    """The whole disclosure contract of the two properties every read and write scopes on, pinned
    here rather than inferred from any one consumer. A write takes the requester's own subject only
    in a shared conversation; in a room or a foreign channel it stays keyed to that space, so a
    private room's fact never rekeys into the next room and a foreign channel's never into the
    workspace, however the conversation is being driven. Reads still union the requester's own
    subject everywhere except that shared atom a foreign audience is sealed against."""
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
