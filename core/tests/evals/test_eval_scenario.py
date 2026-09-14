"""The scenario runner end-to-end proof: a simulated member drives multiple real turns on one eval
conversation via ctx.invoke, and the finished conversation — every exchange, the cumulative tool
trajectory, the durable transcript — is what the grader scores.

The scoped context, the durable transcript, and the turn rows are the real dependencies; the
stand-ins are the turn worker (a ScriptedWorker landing the rows and transcript a real serve
would, one turn per invoke) and the member's LLM leg (a scripted message list)."""

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import gettempdir
from typing import cast
from uuid import UUID, uuid4

import anthropic
import httpx
import lz4.frame
import pytest
import sqlalchemy as sa

from evals.harness.capability import (
    ArtifactProbeResult,
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    EvalTrajectory,
    ProbeCommandResult,
    SharedArtifact,
    ToolInvocation,
)
from evals.harness.harness import WAIT_EXPIRED
from evals.harness.judge import JudgeLeg
from evals.harness.registry import scenario_task
from evals.harness.scenario import (
    MAX_SIMULATOR_REPLY_CHARS,
    OPENING_NUDGE,
    STOP_TOKEN,
    ScenarioCase,
    ScenarioOutcome,
    ScenarioTurn,
    ScenarioUser,
    UserSimulator,
    _infra_owned_result,
    _provider_owned_result,
    run_scenario_case,
)
from evals.harness.scorers import exact_scorer
from evals.harness.target import CapabilityTarget, InProcessTarget, TargetResult
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    compaction_key,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import FiredBy, ModelAccountCapability, TurnContext, TurnRuntimeConfig
from ufo.sdk.models import Message, ToolResultBlock, ToolUseBlock

MODEL = "claude-opus-4-8"
PROMPT = "You are a helpful assistant."
EXTENSION = "evals"


@dataclass
class ScriptedWorker:
    """Plays the DBOS worker across a conversation: each invoke lands the next turn row and
    extends the durable transcript with the member's message and the scripted agent turn."""

    blob: FilesystemBlobStore
    workspace_id: UUID
    replies: tuple[tuple[Message, ...], ...]
    statuses: tuple[str, ...] = ()
    error_classes: tuple[str, ...] = ()
    artifacts: tuple[tuple[str, bytes] | None, ...] = ()
    invoked: int = 0
    idempotency_keys: list[str] = field(default_factory=list)
    speakers: list[UUID | None] = field(default_factory=list)
    transcript: tuple[Message, ...] = ()

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        speaker_key: str | None = None,
    ) -> UUID:
        index = self.invoked
        self.invoked += 1
        self.idempotency_keys.append(idempotency_key or "")
        status = self.statuses[index] if index < len(self.statuses) else "done"
        error_class = self.error_classes[index] if index < len(self.error_classes) else None
        turn_id = uuid4()
        terminal = {"status": status, "text": "Done.", "model": MODEL}
        if error_class is not None:
            terminal["error_class"] = error_class
        async with workspace_tx() as connection:
            conversation = (
                await connection.execute(
                    sa.select(
                        tables.conversation.c.agent_id, tables.conversation.c.member_id
                    ).where(tables.conversation.c.id == conversation_id)
                )
            ).one()
            self.speakers.append(conversation.member_id)
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=self.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=conversation.agent_id,
                    speaker_member_id=conversation.member_id,
                    seq=index + 1,
                    status=status,
                    inbound=message,
                    terminal=terminal,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        artifact = self.artifacts[index] if index < len(self.artifacts) else None
        if artifact is not None:
            name, content = artifact
            key = f"artifacts/{uuid4()}/{name}"
            await self.blob.put(key, content)
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.shared_artifact).values(
                        id=uuid4(),
                        turn_id=turn_id,
                        blob_key=key,
                        workspace_id=self.workspace_id,
                        filename=name,
                        subject=None,
                        media_type="image/svg+xml",
                        size_bytes=len(content),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        self.transcript = (
            *self.transcript,
            Message(role="user", content=message),
            *self.replies[index],
        )
        await Transcript(blob=self.blob, conversation_id=conversation_id).write(
            Conversation(seq=index + 1, messages=self.transcript)
        )
        return turn_id

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        *,
        context: TurnContext | None = None,
        holds_work_already_done: bool = False,
        as_scheduled: bool = False,
        standalone: bool = False,
        unless_member_since: int | None = None,
        unless_member_arrival_since: int | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
        model_accounts: tuple[ModelAccountCapability, ...] = (),
        fired_by: FiredBy | None = None,
    ) -> UUID | None:
        return await self.admit(conversation_id, message, idempotency_key)


@dataclass
class ScriptedMember:
    """A scripted simulator leg: returns the next member message and records what it was shown."""

    script: tuple[str, ...]
    systems: list[str] = field(default_factory=list)
    histories: list[tuple[Message, ...]] = field(default_factory=list)

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        self.systems.append(system)
        self.histories.append(messages)
        return self.script[len(self.histories) - 1]


@dataclass(frozen=True)
class _UnusedWorkspaceProbe:
    async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
        raise AssertionError("scenario artifact capture must use its supplied artifacts")


@dataclass
class DbConversations:
    workspace_id: UUID
    worker: ScriptedWorker

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        speaker_key: str | None = None,
    ) -> UUID:
        return await self.worker.admit(conversation_id, message, idempotency_key)

    def workspace_path(self, conversation_id: UUID, rel: str) -> Path:
        return Path(gettempdir()) / "eval-scenario-workspaces" / str(conversation_id) / rel

    async def open(
        self, case_name: str, member_key: str | None = None, *_: object, **__: object
    ) -> UUID:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
                    agent_id=sa.select(tables.agent.c.id)
                    .where(tables.agent.c.workspace_id == self.workspace_id)
                    .order_by(tables.agent.c.created_at, tables.agent.c.id)
                    .limit(1)
                    .scalar_subquery(),
                    surface="eval",
                    queue_key=str(conversation_id),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        return conversation_id


@dataclass
class CorpusOutcome:
    ctx: ExtensionContext

    async def settle(self, conversation_id: UUID, turn_id: UUID):
        for trajectory in await self.ctx.trajectories():
            if trajectory.conversation_id == conversation_id:
                return trajectory
        return None


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_agent(workspace_id: UUID) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt=PROMPT,
                model=MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


def _target(
    workspace_id: UUID,
    agent_id: UUID,
    blob: FilesystemBlobStore,
    worker: ScriptedWorker,
    member: ScriptedMember,
    judge: JudgeLeg | None = None,
) -> InProcessTarget:
    ctx = context_for(EXTENSION, frozenset(), blob=blob, invoker=worker)
    return InProcessTarget(
        ctx=ctx,
        agent_id=agent_id,
        conversations=DbConversations(workspace_id, worker),
        outcome=CorpusOutcome(ctx),
        judge=judge,
        simulator=member,
        blob=blob,
    )


_SUM_USER = ScenarioUser(
    reason_for_call="You need two purchases totalled.",
    known_info="The first was $137, the second $86.",
    task_instructions="Reveal the second amount only when asked.",
)


async def _sum_grader(outcome: ScenarioOutcome) -> CapabilityVerdict:
    if len(outcome.turns) != 2:
        return CapabilityVerdict(False, f"{len(outcome.turns)} exchanges")
    if not outcome.stopped:
        return CapabilityVerdict(False, "member never stopped")
    if "223" not in outcome.replies[-1]:
        return CapabilityVerdict(False, "total never communicated")
    if outcome.output.tools != ("js_repl",):
        return CapabilityVerdict(False, f"calls {outcome.output.tools}")
    return CapabilityVerdict(True, "totalled across two exchanges")


async def test_scenario_task_runs_cases_serially_holding_a_permit_each(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="First case reply."),),
            (Message(role="assistant", content="Second case reply."),),
        ),
    )
    member = ScriptedMember(
        ("Opening the first case.", STOP_TOKEN, "Opening the second case.", STOP_TOKEN)
    )
    slots = asyncio.Semaphore(1)
    held_during_grading: list[bool] = []

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        held_during_grading.append(slots.locked())
        return CapabilityVerdict(len(outcome.turns) == 1, "one exchange")

    task = scenario_task(
        "serial",
        (
            ScenarioCase("first", _SUM_USER, grade, max_turns=2),
            ScenarioCase("second", _SUM_USER, grade, max_turns=2),
        ),
        simulator_model="claude-haiku-4-5",
    )

    with ws(workspace_id):
        report = await task.run(_target(workspace_id, agent_id, blob, worker, member), slots)

    assert tuple(case.name for case in report.cases) == ("first", "second")
    assert all(case.passed for case in report.cases)
    assert held_during_grading == [True, True]


async def test_scenario_drives_multiple_turns_on_one_conversation(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="Happy to help — what was the second amount?"),),
            (
                Message(
                    role="assistant",
                    content=(ToolUseBlock(id="c1", name="js_repl", input={"code": "137+86"}),),
                ),
                Message(role="user", content=(ToolResultBlock(tool_use_id="c1", content="223"),)),
                Message(role="assistant", content="Your total is $223."),
            ),
        ),
    )
    member = ScriptedMember(
        (
            "Can you total two purchases for me? The first was $137.",
            "The second was $86.",
            STOP_TOKEN,
        )
    )
    case = ScenarioCase(
        "progressive-sum",
        _SUM_USER,
        DescribedGrader("the replies carry the $223 total", _sum_grader),
        max_turns=4,
    )

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert result.passed, result.reason
    assert worker.invoked == 2
    assert len(set(worker.idempotency_keys)) == 2
    assert [key.rsplit(":", 1)[1] for key in worker.idempotency_keys] == ["0", "1"]
    async with workspace_tx() as connection:
        conversations = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.conversation)
                .where(tables.conversation.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert conversations == 1
    attempts = cast(list[dict[str, object]], result.evidence["attempts"])
    turns = cast(list[dict[str, str]], attempts[0]["turns"])
    assert [turn["reply"] for turn in turns] == [
        "Happy to help — what was the second amount?",
        "Your total is $223.",
    ]
    calls = cast(list[dict[str, object]], attempts[0]["calls"])
    assert [call["name"] for call in calls] == ["js_repl"]
    trajectory = cast(dict[str, object], attempts[0]["trajectory"])
    assert len(cast(list[object], trajectory["messages"])) == 6
    assert attempts[0]["stopped"] is True
    timing = cast(dict[str, object], attempts[0]["timing"])
    assert timing["wall_ms"] >= 0
    assert timing["error"] == "no step reader is wired"
    assert "handoffs" in attempts[0]
    assert result.evidence["user"] == _SUM_USER.payload()
    assert result.evidence["grading"] == "the replies carry the $223 total"
    assert result.evidence["memberKey"] is None
    assert result.evidence["maxTurns"] == 4


async def test_scenario_followup_merges_one_internal_flow(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="Application created."),),
            (
                Message(
                    role="assistant",
                    content=(
                        ToolUseBlock(
                            id="build",
                            name="object_action",
                            input={"kind": "site", "action": "deploy_website"},
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="build", content="deployed"),),
                ),
                Message(role="assistant", content="Homepage ready."),
            ),
        ),
    )
    member = ScriptedMember(("Create an application.", STOP_TOKEN))

    async def followup(outcome: ScenarioOutcome, target: CapabilityTarget):
        assert outcome.followup is None
        async with workspace_tx() as connection:
            conversation_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
        return await target.invoke(
            conversation_id,
            agent_id,
            "Build the homepage.",
            "homepage-seed",
            as_scheduled=True,
        )

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        assert outcome.followup is not None
        assert outcome.followup.response == "Homepage ready."
        assert outcome.output.tools == ("action:site:deploy_website",)
        return CapabilityVerdict(True, "creation and homepage build completed")

    case = ScenarioCase(
        "creation-journey",
        _SUM_USER,
        grade,
        max_turns=2,
        followup=followup,
    )

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert result.passed, result.reason
    attempt = cast(list[dict[str, object]], result.evidence["attempts"])[0]
    assert attempt["followupTrajectory"] is not None
    assert len(cast(list[object], attempt["followupTrajectories"])) == 1
    assert [call["name"] for call in cast(list[dict[str, object]], attempt["calls"])] == [
        "object_action"
    ]


async def test_scenario_followup_retains_offline_artifacts(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="Application created."),),
            (Message(role="assistant", content="Homepage built."),),
        ),
    )
    member = ScriptedMember(("Create an application.", STOP_TOKEN))

    async def followup(outcome: ScenarioOutcome, target: CapabilityTarget):
        async with workspace_tx() as connection:
            conversation_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
        return await target.invoke(
            conversation_id,
            agent_id,
            "Build the homepage.",
            "homepage-seed",
            as_scheduled=True,
        )

    async def artifacts(output, probe):
        assert output.workspace_dir is not None
        assert isinstance(probe, _UnusedWorkspaceProbe)
        return ArtifactProbeResult(
            artifacts=(
                SharedArtifact("homepage-interactive.html", b"<main>App</main>"),
                SharedArtifact("homepage-light.png", b"png"),
            )
        )

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        return CapabilityVerdict(bool(outcome.output.artifacts), "artifacts retained")

    case = ScenarioCase(
        "creation-artifacts",
        _SUM_USER,
        grade,
        max_turns=2,
        followup=followup,
        artifact_probe=artifacts,
    )
    target = replace(
        _target(workspace_id, agent_id, blob, worker, member),
        workspace_probe_for=lambda _conversation_id: _UnusedWorkspaceProbe(),
    )

    with ws(workspace_id):
        result = await run_scenario_case(case, target)

    assert result.passed, result.reason
    attempt = cast(list[dict[str, object]], result.evidence["attempts"])[0]
    assert attempt["artifacts"] == ["homepage-interactive.html", "homepage-light.png"]
    contents = cast(list[dict[str, str]], attempt["artifactContents"])
    assert [item["name"] for item in contents] == [
        "homepage-interactive.html",
        "homepage-light.png",
    ]
    assert all(item["dataUri"].startswith("data:") for item in contents)
    assert attempt["artifactError"] is None


async def test_scenario_retains_an_artifact_from_an_earlier_turn(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    design = SharedArtifact("design.svg", b"<svg/>")
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="The design is attached."),),
            (Message(role="assistant", content="Waiting for approval."),),
        ),
        artifacts=((design.name, design.content), None),
    )
    member = ScriptedMember(("Show the design.", "Do not build it."))

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        return CapabilityVerdict(outcome.output.artifacts == (design,), "design retained")

    case = ScenarioCase("early-design", _SUM_USER, grade, max_turns=2)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert result.passed, result.reason
    attempt = cast(list[dict[str, object]], result.evidence["attempts"])[0]
    assert attempt["artifacts"] == ["design.svg"]


async def test_scenario_merges_two_internal_followup_flows(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="Application created."),),
            (
                Message(
                    role="assistant",
                    content=(
                        ToolUseBlock(
                            id="first",
                            name="object_action",
                            input={"kind": "site", "action": "deploy_website"},
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="first", content="blocked"),),
                ),
                Message(role="assistant", content="Build blocked."),
            ),
            (
                Message(
                    role="assistant",
                    content=(
                        ToolUseBlock(
                            id="second",
                            name="object_action",
                            input={"kind": "site", "action": "deploy_website"},
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="second", content="deployed"),),
                ),
                Message(role="assistant", content="Homepage ready."),
            ),
        ),
    )
    member = ScriptedMember(("Create an application.", STOP_TOKEN))

    async def followup(outcome: ScenarioOutcome, target: CapabilityTarget):
        async with workspace_tx() as connection:
            conversation_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
        first = await target.invoke(
            conversation_id,
            agent_id,
            "Build the homepage without authority.",
            "homepage-seed:first",
            as_scheduled=True,
        )
        second = await target.invoke(
            conversation_id,
            agent_id,
            "Repair the homepage with authority.",
            "homepage-seed:second",
            as_scheduled=True,
        )
        return first, second

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        assert [item.response for item in outcome.followups] == [
            "Build blocked.",
            "Homepage ready.",
        ]
        assert outcome.output.tools == (
            "action:site:deploy_website",
            "action:site:deploy_website",
        )
        return CapabilityVerdict(True, "both build attempts retained")

    case = ScenarioCase(
        "repair-journey",
        _SUM_USER,
        grade,
        max_turns=2,
        followup=followup,
    )

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert result.passed, result.reason
    attempt = cast(list[dict[str, object]], result.evidence["attempts"])[0]
    assert len(cast(list[object], attempt["followupTrajectories"])) == 2


async def test_failed_scenario_followup_retains_its_evidence(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="Application created."),),
            (
                Message(
                    role="assistant",
                    content=(
                        ToolUseBlock(
                            id="build",
                            name="object_action",
                            input={"kind": "site", "action": "deploy_website"},
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="build", content="timed out"),),
                ),
            ),
        ),
        statuses=("done", "failed"),
        error_classes=("", "TimeoutError"),
    )
    member = ScriptedMember(("Create an application.", STOP_TOKEN))

    async def followup(outcome: ScenarioOutcome, target: CapabilityTarget):
        async with workspace_tx() as connection:
            conversation_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
        return await target.invoke(
            conversation_id,
            agent_id,
            "Build the homepage.",
            "homepage-seed",
            as_scheduled=True,
        )

    case = ScenarioCase(
        "failed-creation-journey",
        _SUM_USER,
        _sum_grader,
        max_turns=2,
        followup=followup,
    )

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    attempt = cast(list[dict[str, object]], result.evidence["attempts"])[0]
    assert attempt["followupTrajectory"] is not None
    assert [call["name"] for call in cast(list[dict[str, object]], attempt["calls"])] == [
        "object_action"
    ]


async def test_a_followup_that_ends_on_a_transient_is_the_providers_fault(
    db: None, tmp_path
) -> None:
    """A followup turn crashed on a provider overload excludes the case. The archived record has to
    say the provider owns it, or the nightly cohort gate reds on provider weather."""
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="Application created."),),
            (Message(role="assistant", content="..."),),
        ),
        statuses=("done", "failed"),
        error_classes=("", "OverloadedError"),
    )
    member = ScriptedMember(("Create an application.", STOP_TOKEN))

    async def followup(outcome: ScenarioOutcome, target: CapabilityTarget):
        async with workspace_tx() as connection:
            conversation_id = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
        return await target.invoke(
            conversation_id,
            agent_id,
            "Build the homepage.",
            "homepage-seed",
            as_scheduled=True,
        )

    case = ScenarioCase(
        "overloaded-creation-journey",
        _SUM_USER,
        _sum_grader,
        max_turns=2,
        followup=followup,
    )

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    assert result.excluded
    assert result.provider_fault


async def test_scenario_followup_exception_retains_the_completed_conversation(
    db: None, tmp_path
) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=((Message(role="assistant", content="No application created."),),),
    )
    member = ScriptedMember(("Create an application.", STOP_TOKEN))

    async def followup(outcome: ScenarioOutcome, target: CapabilityTarget):
        raise RuntimeError("homepage followup requires one application")

    case = ScenarioCase(
        "missing-application-journey",
        _SUM_USER,
        _sum_grader,
        max_turns=2,
        followup=followup,
    )

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    assert result.reason == (
        "followup raised: RuntimeError: homepage followup requires one application"
    )
    attempt = cast(list[dict[str, object]], result.evidence["attempts"])[0]
    assert attempt["response"] == "No application created."
    assert attempt["trajectory"] is not None


async def test_scenario_shows_the_member_only_its_scenario_and_the_replies(
    db: None, tmp_path
) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=((Message(role="assistant", content="What was the second amount?"),),),
    )
    member = ScriptedMember(("The first purchase was $137.", STOP_TOKEN))
    case = ScenarioCase("disclosure", _SUM_USER, _sum_grader, max_turns=3)

    with ws(workspace_id):
        await run_scenario_case(case, _target(workspace_id, agent_id, blob, worker, member))

    system = member.systems[0]
    assert _SUM_USER.reason_for_call in system
    assert _SUM_USER.known_info in system
    assert STOP_TOKEN in system
    assert member.histories[0] == (Message(role="user", content=OPENING_NUDGE),)
    final = member.histories[1]
    assert [message.role for message in final] == ["user", "assistant", "user"]
    assert final[1].content == "The first purchase was $137."
    assert final[2].content == "What was the second amount?"


async def test_scenario_fails_on_an_unclean_turn(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="What was the second amount?"),),
            (Message(role="assistant", content="Your total is $223."),),
        ),
        statuses=("done", "failed"),
    )
    member = ScriptedMember(
        ("Total two purchases? First was $137.", "The second was $86.", STOP_TOKEN)
    )
    case = ScenarioCase("unclean", _SUM_USER, _sum_grader, max_turns=4)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    assert result.reason == "turn ended with status failed"
    attempts = cast(list[dict[str, object]], result.evidence["attempts"])
    assert len(cast(list[object], attempts[0]["turns"])) == 2


async def test_scenario_fails_when_the_member_never_speaks(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(blob, workspace_id, replies=())
    member = ScriptedMember((STOP_TOKEN,))
    case = ScenarioCase("mute", _SUM_USER, _sum_grader)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    assert result.reason == "simulator ended the conversation before it began"
    assert worker.invoked == 0


async def test_user_simulator_flips_roles_and_bounds_replies() -> None:
    member = ScriptedMember(("next question",))
    simulator = UserSimulator(member, _SUM_USER)
    oversized = "x" * (MAX_SIMULATOR_REPLY_CHARS + 1)
    turns = (
        ScenarioTurn("first ask", "short answer"),
        ScenarioTurn("second ask", oversized),
    )

    message = await simulator.next_message(turns)

    assert message == "next question"
    history = member.histories[0]
    assert [item.role for item in history] == ["user", "assistant", "user", "assistant", "user"]
    assert history[0].content == OPENING_NUDGE
    assert history[1].content == "first ask"
    assert history[2].content == "short answer"
    assert cast(str, history[4].content).endswith("[reply truncated for the simulator]")


async def _seed_a(workspace_id: UUID, agent_id: UUID) -> None:
    return None


async def _seed_b(workspace_id: UUID, agent_id: UUID) -> None:
    _ = "different fixture"


def test_scenario_digest_moves_with_the_seed_source() -> None:
    def case(seed) -> ScenarioCase:
        return ScenarioCase("seeded", _SUM_USER, _sum_grader, seed=seed)

    assert case(_seed_a).payload() == case(_seed_a).payload()
    assert case(_seed_a).payload() != case(_seed_b).payload()
    assert case(None).payload()["seed"] is None


async def test_scenario_seed_runs_before_the_first_turn(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob, workspace_id, replies=((Message(role="assistant", content="Done."),),)
    )
    member = ScriptedMember(("go ahead", STOP_TOKEN))
    seeded: list[tuple[UUID, UUID]] = []

    async def seed(seed_workspace_id: UUID, seed_agent_id: UUID) -> None:
        assert worker.invoked == 0
        seeded.append((seed_workspace_id, seed_agent_id))

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        return CapabilityVerdict(len(outcome.turns) == 1, f"{len(outcome.turns)} exchange(s)")

    case = ScenarioCase("seeded", _SUM_USER, grade, max_turns=2, seed=seed)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert result.passed, result.reason
    assert seeded == [(workspace_id, agent_id)]


async def test_multi_trial_case_reseeds_and_requires_every_trial(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="Your total is $223."),),
            (Message(role="assistant", content="I cannot help with that."),),
        ),
    )
    member = ScriptedMember(
        ("Total: first $137, second $86?", STOP_TOKEN, "Total: first $137, second $86?", STOP_TOKEN)
    )
    seeds: list[int] = []

    async def seed(seed_workspace_id: UUID, seed_agent_id: UUID) -> None:
        seeds.append(worker.invoked)

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        communicated = "223" in outcome.replies[-1]
        return CapabilityVerdict(communicated, "total" if communicated else "no total")

    case = ScenarioCase("consistency", _SUM_USER, grade, max_turns=2, seed=seed, trials=2)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    assert result.reason == "1/2 scored trials passed; first failure: no total"
    assert seeds == [0, 1]
    assert result.evidence["selectedAttempt"] == 1
    attempts = cast(list[dict[str, object]], result.evidence["attempts"])
    assert [attempt["passed"] for attempt in attempts] == [True, False]
    async with workspace_tx() as connection:
        conversations = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.conversation)
                .where(tables.conversation.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert conversations == 2


def test_zero_trials_fails_loud() -> None:
    with pytest.raises(ValueError, match="at least one trial"):
        ScenarioCase("typo", _SUM_USER, _sum_grader, trials=0)


def test_nonpositive_tier_fails_loud() -> None:
    with pytest.raises(ValueError, match="tier must be at least 1"):
        ScenarioCase("typo", _SUM_USER, _sum_grader, tier=0)


async def _always_fail(outcome: ScenarioOutcome) -> CapabilityVerdict:
    return CapabilityVerdict(False, "nope")


async def test_transient_trial_is_excluded_a_real_trial_still_scores(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="..."),),
            (Message(role="assistant", content="answer"),),
        ),
        statuses=("failed", "done"),
        error_classes=("ReadTimeout",),
    )
    member = ScriptedMember(("first ask", "second ask", STOP_TOKEN))
    case = ScenarioCase("mixed", _SUM_USER, _always_fail, max_turns=2, trials=2)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    assert not result.excluded
    assert result.evidence["excludedTrials"] == 1
    assert "infra-excluded" in result.reason
    attempts = cast(list[dict[str, object]], result.evidence["attempts"])
    assert [a["infra"] for a in attempts] == [True, False]


async def test_all_transient_trials_exclude_the_case(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=(
            (Message(role="assistant", content="..."),),
            (Message(role="assistant", content="..."),),
        ),
        statuses=("failed", "failed"),
        error_classes=("ReadTimeout", "ReadError"),
    )
    member = ScriptedMember(("first ask", "second ask"))
    case = ScenarioCase("all-infra", _SUM_USER, _sum_grader, max_turns=2, trials=2)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert result.excluded
    assert result.provider_fault
    assert not result.passed
    assert "all 2 trial(s) infra-excluded" in result.reason


async def test_nontransient_turn_failure_counts_as_a_real_failure(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob,
        workspace_id,
        replies=((Message(role="assistant", content="..."),),),
        statuses=("failed",),
        error_classes=("ValueError",),
    )
    member = ScriptedMember(("ask",))
    case = ScenarioCase("real-fail", _SUM_USER, _sum_grader, max_turns=2)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member)
        )

    assert not result.passed
    assert not result.excluded
    assert result.evidence["excludedTrials"] == 0


def test_scenario_wait_expiry_is_the_rigs_clock_whether_or_not_the_turn_spoke() -> None:
    """The wait is a budget the shard chose. A trial it cuts short never answered the scenario, so
    it leaves the denominator; only a turn that reached its own terminal without a transcript is a
    failure the model owns."""
    call = ToolInvocation("ask_user", {"question": "Which inbox?"}, "awaiting", True)
    cancelled = EvalTrajectory(
        conversation_id=uuid4(),
        turn_id=uuid4(),
        status="cancelled",
        messages=(),
    )
    untouched = TargetResult(
        CapabilityOutput("", ()),
        clean=False,
        failure_reason=WAIT_EXPIRED,
        trajectory=cancelled,
    )
    looping = replace(untouched, output=CapabilityOutput("", (call,), own_calls=(call,)))
    ended = replace(untouched, trajectory=cancelled.model_copy(update={"status": "done"}))

    assert _infra_owned_result(untouched)
    assert _infra_owned_result(looping)
    assert not _infra_owned_result(ended)


def test_a_trial_the_wait_cancelled_names_who_held_the_turn() -> None:
    """An excluded trial that names no owner reaches the nightly cohort gate as drift and reds the
    sweep. The harness cancels the overdue turn before it reads the status back, so every expired
    trial records `cancelled`: only the status the wait expired on, and the turn's own first step
    beside it, say whether the provider was answering or the turn was still behind our own workers
    or inside the rig's own startup, which holds `running` from the dispatch claim onwards."""
    trajectory = EvalTrajectory(
        conversation_id=uuid4(),
        turn_id=uuid4(),
        status="cancelled",
        messages=(),
    )
    expired = TargetResult(
        CapabilityOutput("", ()),
        clean=False,
        failure_reason=WAIT_EXPIRED,
        trajectory=trajectory,
    )
    answering = replace(expired, expiry_status="running", work_started=True)
    booting = replace(expired, expiry_status="running")
    queued = replace(expired, expiry_status="queued", work_started=True)
    credential = replace(answering, error_class="CredentialValueInvalid")

    assert _provider_owned_result(answering)
    assert _infra_owned_result(booting)
    assert not _provider_owned_result(booting)
    assert _infra_owned_result(queued)
    assert not _provider_owned_result(queued)
    assert _infra_owned_result(expired)
    assert not _provider_owned_result(expired)
    assert _infra_owned_result(credential)
    assert not _provider_owned_result(credential)


@dataclass
class RaisingMember:
    """A simulator leg that raises on its first call — stands in for a transient provider fault
    (or a genuine bug) hitting the member simulation."""

    error: Exception

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        raise self.error


async def test_transient_simulator_failure_excludes_the_trial(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(blob, workspace_id, replies=())
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = anthropic.OverloadedError(
        "Overloaded", response=httpx.Response(529, request=request), body=None
    )
    member = RaisingMember(overloaded)
    case = ScenarioCase("sim-overload", _SUM_USER, _sum_grader, max_turns=3)

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, member, member)
        )

    assert result.excluded
    assert not result.passed
    assert worker.invoked == 0
    assert "infra-excluded" in result.reason


async def test_genuine_simulator_error_propagates(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(blob, workspace_id, replies=())
    member = RaisingMember(ValueError("a real bug in the simulator"))
    case = ScenarioCase("sim-bug", _SUM_USER, _sum_grader, max_turns=3)

    with ws(workspace_id):
        with pytest.raises(ValueError, match="a real bug"):
            await run_scenario_case(case, _target(workspace_id, agent_id, blob, worker, member))


async def test_a_judge_transient_excludes_the_trial_as_the_providers_fault(
    db: None, tmp_path
) -> None:
    """The conversation finished and its grader passed; the judge's transport then dropped. The
    trial leaves the denominator as the provider's fault instead of raising out of the shard."""
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = ScriptedWorker(
        blob, workspace_id, replies=((Message(role="assistant", content="The total is 223."),),)
    )
    member = ScriptedMember(("Total my two purchases.", STOP_TOKEN))

    class Judge:
        async def complete(self, _system: str, _messages: tuple[Message, ...]) -> str:
            raise httpx.ConnectError("judge transport dropped")

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        return CapabilityVerdict("223" in outcome.replies[-1], "totalled")

    case = ScenarioCase("judge-weather", _SUM_USER, grade, max_turns=2, rubric=("Totals.",))

    with ws(workspace_id):
        result = await run_scenario_case(
            case, _target(workspace_id, agent_id, blob, worker, member, judge=Judge())
        )

    assert result.excluded
    assert result.provider_fault
    assert "ConnectError" in result.evidence["attempts"][0]["reason"]


@dataclass
class CompactingWorker(ScriptedWorker):
    """A worker whose turns cross the compaction boundary: each admitted turn persists one
    compaction record beside the transcript, the way `Compaction` does in a deploy that selects
    that strategy."""

    compactions: int = 0

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        speaker_key: str | None = None,
    ) -> UUID:
        turn_id = await super().admit(conversation_id, message, idempotency_key, speaker_key)
        self.compactions += 1
        window = CompactionWindow(messages=self.transcript)
        summary = CompactionSummary(
            intent="total the purchases", current_work="summarized", next_step="answer"
        )
        for half, body in (("before", window), ("after", window), ("summary", summary)):
            await self.blob.put(
                compaction_key(conversation_id, self.compactions, half),
                lz4.frame.compress(body.model_dump_json().encode()),
            )
        return turn_id


async def test_the_boundary_count_holds_compactions_as_well_as_rollovers(
    db: None, tmp_path
) -> None:
    """The count a case reports is boundaries crossed, not one strategy's records. A compact
    deploy writes compaction records and no rollover record, and the index walk still counts them
    and renders each as a recovery record whose handoff is the summary."""
    workspace_id = await _workspace()
    agent_id = await _seed_agent(workspace_id)
    blob = FilesystemBlobStore(root=tmp_path)
    worker = CompactingWorker(
        blob, workspace_id, replies=((Message(role="assistant", content="The total is 223."),),)
    )
    target = _target(workspace_id, agent_id, blob, worker, ScriptedMember(()))

    with ws(workspace_id):
        result = await target.run(
            CapabilityCase("boundaries", "Total my two purchases.", exact_scorer("unused"))
        )

    assert result.clean
    assert result.output.rollovers == 1
    snapshot = result.output.rollover_records[0]
    assert "intent: total the purchases" in snapshot.recovery.handoff
