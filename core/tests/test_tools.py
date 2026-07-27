import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from ufo.blob import FilesystemBlobStore
from ufo.ext.manifest import SubagentProfile
from ufo.loop.subagents import SubagentRegistry, Subagents
from ufo.sandbox.session import ExecResult
from ufo.schema.records import Agent, Turn
from ufo.skills.runtime import CORE_SKILL_REGISTRY, RuntimeSkill, SkillRegistry
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import Spawn, SpawnResult, SubagentStatus, ToolContext
from ufo.tools.registry import ToolRegistry

REGISTRY = ToolRegistry(BUILTIN_TOOLS)
ARTIFACT_SECRET = "tools-test-secret"


@dataclass
class FakeSandbox:
    """A stand-in for the carrier-backed session for the handler logic that never reaches the
    sandbox: `bash` output is scripted, and `run_sbxfs` raises so a guard test proves the read
    guard short-circuits before any file op runs. The sbxfs-backed behavior (windowing, edits,
    streamed share) is proven against a real container in test_file_tools, never against this
    stand-in."""

    bash_result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    files: dict[str, bytes] = field(default_factory=dict)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return self.bash_result

    async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]:
        raise AssertionError(f"run_sbxfs({op}) must not run once a guard has rejected the call")

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
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
    messaged: list[tuple[UUID, str]] = field(default_factory=list)

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

    async def message(self, turn_id: UUID, text: str) -> SubagentStatus:
        self.messaged.append((turn_id, text))
        return self.statuses.get(turn_id, SubagentStatus(turn_id=turn_id, status="queued", text=""))


def make_context(
    sandbox: FakeSandbox,
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
        audience_member_id=None,
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


def test_registry_schemas_cover_every_tool() -> None:
    schemas = REGISTRY.schemas()
    assert {schema.name for schema in schemas} == {
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "share_file",
        "spawn_subagent",
        "load_sessions",
        "ask_user",
        "request_credentials",
        "load_skill",
        "connect_account",
        "wait_for_subagents",
        "cancel_subagent",
        "message_subagent",
    }
    bash = next(schema for schema in schemas if schema.name == "bash")
    assert "command" in bash.input_schema["properties"]


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


async def test_edit_requires_read_before_write(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError, match="must be read before it is edited"):
        await run(
            "edit",
            ctx,
            file_path="code.py",
            edits=[{"old_string": "x = 1", "new_string": "x = 2"}],
        )


async def test_share_file_without_a_secret_fails_loud_and_writes_nothing(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path, artifact_secret="")
    with pytest.raises(RuntimeError, match="not configured"):
        await run("share_file", ctx, file_path="report.txt")
    assert not (tmp_path / "artifacts").exists()


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


async def test_load_skill_unknown_name_fails_loud(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(ValueError, match="unknown skill 'nope'"):
        await _load_skill(ctx, "nope")


async def test_wait_for_subagents_awaits_each_child_and_reports_status(tmp_path: Path) -> None:
    child = uuid4()
    control = StubSubagentControl(
        statuses={child: SubagentStatus(turn_id=child, status="done", text='{"result": "ok"}')}
    )
    ctx = make_context(FakeSandbox(), tmp_path, subagents=control)
    result = await run("wait_for_subagents", ctx, subagent_ids=[str(child)], user_description="x")
    assert control.waited == [(child,)]
    reported = json.loads(result.content[0].text)["subagents"]
    assert reported == [{"subagent_id": str(child), "status": "done", "output": '{"result": "ok"}'}]


async def test_wait_for_subagents_without_control_fails_loud(tmp_path: Path) -> None:
    ctx = make_context(FakeSandbox(), tmp_path)
    with pytest.raises(RuntimeError, match="subagent control is not available"):
        await run("wait_for_subagents", ctx, subagent_ids=[str(uuid4())], user_description="x")


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


async def test_message_subagent_forwards_the_message_and_reports_status(tmp_path: Path) -> None:
    child = uuid4()
    control = StubSubagentControl()
    ctx = make_context(FakeSandbox(), tmp_path, subagents=control)
    result = await run(
        "message_subagent",
        ctx,
        subagent_id=str(child),
        message="also check X",
        user_description="x",
    )
    assert control.messaged == [(child, "also check X")]
    assert json.loads(result.content[0].text) == {"subagent_id": str(child), "status": "queued"}


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
    )
    ctx = make_context(FakeSandbox(), tmp_path, spawn=subagents.spawn)
    result = await run("spawn_subagent", ctx, profile="assistant", payload={"task": "x"})
    assert result.is_error
    text = result.content[0].text
    assert "assistant" in text
    assert "research" in text
