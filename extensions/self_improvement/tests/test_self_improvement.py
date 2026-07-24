"""The self-improvement loop's end-to-end proof: real transcripts in, a governed proposal out.

The corpus read, the scoped store, and the governed proposal are the real dependencies; only the
model legs are injected stand-ins (a fixed proposer, an arm-echoing replay, a marker judge) so the
proposer/replay/grader run deterministically. The loop proves the consecutive-pass gate withholds a
promotion until it stabilizes, that promotion opens a pending Proposal (never a direct prompt
write), and that a resolved candidate is suppressed on later ticks."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_self_improvement import manifest as si
from ufo_ext_self_improvement.corpus import task_classes
from ufo_ext_self_improvement.cron import (
    CANDIDATE_KEY,
    EVALUATING,
    PROMOTED,
    REJECTED,
    CandidateState,
    ImproveCron,
)
from ufo_ext_self_improvement.evaluation import CandidateEvaluation
from ufo_ext_self_improvement.gate import (
    OutcomeLabel,
    score_gate,
    two_stage_gate,
)
from ufo_ext_self_improvement.model import ModelAccessLeg
from ufo_ext_self_improvement.proposer import PromptProposer
from ufo_ext_self_improvement.replay import ReplayEvaluation

from ufo.accounting import TOKENS_DIMENSION, Pricing
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ModelAccess, Trajectory, context_for
from ufo.ext.loader import load_manifests
from ufo.governance import prompt_digest
from ufo.loop.transcript import Transcript
from ufo.models.catalog import CORE_PRICING
from ufo.models.interface import (
    Message,
    ModelClient,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.transcript import Conversation
from ufo.workspace import ws

SEED_PROMPT = "You are a helpful assistant."
IMPROVED_MARKER = "IMPROVED"
IMPROVED_PROMPT = (
    f"{SEED_PROMPT}\nWhen a tool errors, retry with corrected arguments. {IMPROVED_MARKER}"
)
MODEL = "claude-opus-4-8"


@dataclass(frozen=True)
class FixedProposerLeg:
    """A proposer stand-in returning one fixed revised prompt regardless of input."""

    prompt: str

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        return self.prompt


class ArmEchoLeg:
    """A replay stand-in whose regenerated answer is the arm's prompt and calls no tool, so the
    judge can tell the candidate arm from the current arm."""

    async def turn(
        self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]
    ) -> Message:
        return Message(role="assistant", content=system)


@dataclass(frozen=True)
class MarkerJudgeLeg:
    """A grader stand-in that accepts an answer exactly when it carries the marker."""

    marker: str

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        content = messages[0].content
        body = content if isinstance(content, str) else ""
        return json.dumps({"accepted": self.marker in body})


def _bad_messages(index: int) -> tuple[Message, ...]:
    return (
        Message(role="user", content=f"do task {index}"),
        Message(
            role="assistant",
            content=(ToolUseBlock(id="t1", name="bash", input={"command": "ls"}),),
        ),
        Message(
            role="user",
            content=(
                ToolResultBlock(tool_use_id="t1", content="command not found", is_error=True),
            ),
        ),
        Message(role="assistant", content="I couldn't complete it."),
    )


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_agent(workspace_id: UUID, blob: FilesystemBlobStore, count: int) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt=SEED_PROMPT,
                model=MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    for index in range(count):
        conversation_id, turn_id = uuid4(), uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    surface="cli",
                    queue_key=str(conversation_id),
                    member_id=None,
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
                    status="done",
                    inbound=f"do task {index}",
                    terminal={"status": "done", "text": "I couldn't complete it.", "model": MODEL},
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await Transcript(blob=blob, conversation_id=conversation_id).write(
            Conversation(seq=2, messages=_bad_messages(index))
        )
    return agent_id


def _context(blob: FilesystemBlobStore):
    return context_for(si.NAME, frozenset(), blob=blob)


def _cron(ctx, judge_marker: str = IMPROVED_MARKER) -> ImproveCron:
    return ImproveCron(
        ctx=ctx,
        proposer=PromptProposer(model=FixedProposerLeg(prompt=IMPROVED_PROMPT)),
        evaluation=CandidateEvaluation(
            replay_model=ArmEchoLeg(), judge=MarkerJudgeLeg(judge_marker)
        ),
    )


async def _state(ctx, agent_id: UUID) -> CandidateState:
    row = await ctx.store.get(CANDIDATE_KEY.format(agent_id=agent_id))
    assert isinstance(row, dict)
    return CandidateState.model_validate(row)


async def _proposals(workspace_id: UUID):
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.proposal.c.extension,
                    tables.proposal.c.agent_id,
                    tables.proposal.c.status,
                    tables.proposal.c.from_digest,
                    tables.proposal.c.body,
                ).where(tables.proposal.c.workspace_id == workspace_id)
            )
        ).all()


async def _agent_prompt(workspace_id: UUID, agent_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.agent.c.prompt).where(tables.agent.c.id == agent_id)
            )
        ).scalar_one()


def test_manifest_declares_only_the_scheduled_eval_cron() -> None:
    found = next((m for m in load_manifests() if m.name == si.NAME), None)
    assert found is not None, "self_improvement extension not discovered — run `uv sync`"
    assert {job.name for job in found.jobs} == {si.EVAL_JOB}
    assert found.jobs[0].schedule == si.EVAL_SCHEDULE
    assert found.credentials == ()


async def test_eval_cron_fails_without_model_access(tmp_path) -> None:
    job = si.manifest().jobs[0]
    with pytest.raises(RuntimeError, match="requires model access"):
        await job.handler(_context(FilesystemBlobStore(root=tmp_path)))


def test_corpus_groups_tool_errors_into_a_split_class() -> None:
    agent_id = uuid4()
    trajectories = tuple(
        Trajectory(
            conversation_id=uuid4(),
            agent_id=agent_id,
            agent_prompt=SEED_PROMPT,
            agent_prompt_digest=prompt_digest(SEED_PROMPT),
            messages=_bad_messages(index),
        )
        for index in range(4)
    )
    classes = task_classes(trajectories)
    assert len(classes) == 1
    assert classes[0].name == "tool:bash"
    assert len(classes[0].mine) >= 1
    assert len(classes[0].held_out) >= 1


async def test_loop_withholds_promotion_until_it_stabilizes(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    agent_id = await _seed_agent(workspace_id, blob, count=8)
    ctx = _context(blob)
    cron = _cron(ctx)

    with ws(workspace_id):
        await cron.run()
        first = await _state(ctx, agent_id)
        assert first.status == EVALUATING
        assert first.gate_passes == 1
        assert await _proposals(workspace_id) == []

        await cron.run()
        promoted = await _state(ctx, agent_id)
        assert promoted.status == PROMOTED
        assert promoted.proposal_id is not None

        proposals = await _proposals(workspace_id)
        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.extension == si.NAME
        assert proposal.agent_id == agent_id
        assert proposal.status == "pending"
        assert proposal.from_digest == prompt_digest(SEED_PROMPT)
        assert proposal.body == {"prompt": IMPROVED_PROMPT}
        assert await _agent_prompt(workspace_id, agent_id) == SEED_PROMPT

        await cron.run()
        assert len(await _proposals(workspace_id)) == 1


async def test_loop_rejects_and_suppresses_a_candidate_without_lift(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    agent_id = await _seed_agent(workspace_id, blob, count=8)
    ctx = _context(blob)
    cron = _cron(ctx, judge_marker="NEVER_IN_ANY_ANSWER")

    with ws(workspace_id):
        await cron.run()
        rejected = await _state(ctx, agent_id)
        assert rejected.status == REJECTED
        assert rejected.gate_passes == 0
        assert await _proposals(workspace_id) == []

        await cron.run()
        assert await _proposals(workspace_id) == []
        assert (await _state(ctx, agent_id)).status == REJECTED


@dataclass
class ScriptedReplayLeg:
    """A replay stand-in that plays a fixed list of turns and records the messages it last saw, so a
    test can assert the archived tool result was fed back into the replay context."""

    turns: list[Message]
    seen: tuple[Message, ...] = ()
    index: int = 0

    async def turn(
        self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]
    ) -> Message:
        self.seen = messages
        played = self.turns[self.index]
        self.index += 1
        return played


def _archived_bash(command: str, result: str) -> tuple[Message, ...]:
    return (
        Message(role="user", content="run it"),
        Message(
            role="assistant",
            content=(ToolUseBlock(id="t1", name="bash", input={"command": command}),),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content=result, is_error=False),),
        ),
        Message(role="assistant", content="original answer"),
    )


@dataclass
class MeteredReplayClient:
    requests: list[ModelRequest]

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ToolCallStart(id="r1", name="bash")
            yield ToolCallDelta(id="r1", partial_json='{"command":"ls"}')
            yield Usage(input_tokens=4, output_tokens=2)
            return
        yield TextDelta(text="regenerated final")
        yield Usage(input_tokens=3, output_tokens=1)


@dataclass(frozen=True)
class ModelResolver:
    auto_model: str
    pricing: Pricing
    client: ModelClient

    async def client_for(self, model: str) -> ModelClient:
        assert model == self.auto_model
        return self.client


async def test_replay_uses_metered_model_access_and_feeds_archived_results(db: None) -> None:
    workspace_id = await _workspace()
    client = MeteredReplayClient(requests=[])
    model = ModelAccess(ModelResolver(MODEL, CORE_PRICING, client))

    with ws(workspace_id):
        result = await ReplayEvaluation(ModelAccessLeg(model)).replay(
            _archived_bash("ls", "file-a\nfile-b"), SEED_PROMPT
        )
        async with workspace_tx() as connection:
            billed = (
                await connection.execute(
                    sa.select(sa.func.count(), sa.func.sum(tables.ledger.c.amount)).where(
                        tables.ledger.c.workspace_id == workspace_id,
                        tables.ledger.c.turn_id.is_(None),
                        tables.ledger.c.dimension == TOKENS_DIMENSION,
                    )
                )
            ).one()

    assert result.final_text == "regenerated final"
    assert not result.diverged
    assert billed[0] == 2
    assert billed[1] == 10
    fed = client.requests[1].messages[-1]
    assert not isinstance(fed.content, str)
    assert any(
        isinstance(block, ToolResultBlock) and block.content == "file-a\nfile-b"
        for block in fed.content
    )


async def test_replay_feeds_the_archived_tool_result_back() -> None:
    archived = _archived_bash("ls", "file-a\nfile-b")
    leg = ScriptedReplayLeg(
        turns=[
            Message(
                role="assistant",
                content=(ToolUseBlock(id="r1", name="bash", input={"command": "ls"}),),
            ),
            Message(role="assistant", content="regenerated final"),
        ]
    )
    result = await ReplayEvaluation(leg).replay(archived, "SYSTEM PROMPT")
    assert result.final_text == "regenerated final"
    assert not result.diverged
    assert result.rounds == 2
    fed = leg.seen[-1]
    assert not isinstance(fed.content, str)
    assert any(
        isinstance(block, ToolResultBlock) and block.content == "file-a\nfile-b"
        for block in fed.content
    )


async def test_replay_diverges_when_a_call_has_no_archived_result() -> None:
    archived = _archived_bash("ls", "file-a")
    leg = ScriptedReplayLeg(
        turns=[
            Message(
                role="assistant",
                content=(ToolUseBlock(id="r1", name="bash", input={"command": "rm -rf /"}),),
            )
        ]
    )
    result = await ReplayEvaluation(leg).replay(archived, "SYSTEM PROMPT")
    assert result.diverged


def _labels(present_accepted: int, present_total: int, absent_accepted: int, absent_total: int):
    present = [
        OutcomeLabel(present=True, success=i < present_accepted) for i in range(present_total)
    ]
    absent = [OutcomeLabel(present=False, success=i < absent_accepted) for i in range(absent_total)]
    return tuple(present + absent)


def test_gate_crosses_on_a_clear_lift_at_floor() -> None:
    verdict = score_gate(_labels(4, 4, 0, 4))
    assert verdict.passed
    assert verdict.acceptance_lower_bound > 0.05


def test_gate_fails_below_the_per_arm_floor() -> None:
    verdict = score_gate(_labels(3, 3, 0, 3))
    assert not verdict.passed
    assert "nFloor" in verdict.reason


def test_gate_fails_a_no_lift_candidate() -> None:
    assert not score_gate(_labels(4, 4, 4, 4)).passed


def test_two_stage_gate_blocks_a_global_regression() -> None:
    local = _labels(4, 4, 0, 4)
    regressed = _labels(0, 4, 4, 4)
    assert score_gate(local).passed
    verdict = two_stage_gate(local, regressed)
    assert not verdict.passed
    assert "global regression" in verdict.reason
