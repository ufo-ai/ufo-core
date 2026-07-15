"""The scenario runner end-to-end proof: a simulated member drives multiple real turns on one eval
conversation via ctx.invoke, and the finished conversation — every exchange, the cumulative tool
trajectory, the durable transcript — is what the grader scores.

The scoped context, the durable transcript, and the turn rows are the real dependencies; the
stand-ins are the turn worker (a ScriptedWorker landing the rows and transcript a real serve
would, one turn per invoke) and the member's LLM leg (a scripted message list)."""

from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from evals.harness.capability import CapabilityVerdict
from evals.harness.scenario import (
    MAX_SIMULATOR_REPLY_CHARS,
    OPENING_NUDGE,
    STOP_TOKEN,
    ScenarioCase,
    ScenarioOutcome,
    ScenarioTurn,
    ScenarioUser,
    UserSimulator,
    run_scenario_case,
)
from evals.harness.target import InProcessTarget
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.loop.transcript import Transcript
from ufo.schema import tables
from ufo.sdk.models import Message, ToolResultBlock, ToolUseBlock
from ufo.transcript import Conversation
from ufo.workspace import ws

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
    invoked: int = 0
    idempotency_keys: list[str] = field(default_factory=list)
    transcript: tuple[Message, ...] = ()

    async def invoke(
        self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str
    ) -> UUID:
        index = self.invoked
        self.invoked += 1
        self.idempotency_keys.append(idempotency_key)
        status = self.statuses[index] if index < len(self.statuses) else "done"
        turn_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=self.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=index + 1,
                    status=status,
                    inbound=message,
                    terminal={"status": status, "text": "Done.", "model": MODEL},
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


@dataclass
class DbConversations:
    workspace_id: UUID

    async def open(self, case_name: str, member_key: str | None = None) -> UUID:
        conversation_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=self.workspace_id,
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
) -> InProcessTarget:
    ctx = context_for(EXTENSION, frozenset(), blob=blob, invoker=worker)
    return InProcessTarget(
        ctx=ctx,
        agent_id=agent_id,
        conversations=DbConversations(workspace_id),
        outcome=CorpusOutcome(ctx),
        judge=member,
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
    case = ScenarioCase("progressive-sum", _SUM_USER, _sum_grader, max_turns=4)

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
    assert result.reason == "1/2 trials passed; first failure: no total"
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
