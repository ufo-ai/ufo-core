"""The in-process target: drive one case as a real turn through the agent, then reconstruct the
grader-visible output from the durable transcript.

`invoke` is the whole seam the scoped ExtensionContext exposes — it admits a turn and returns its
id; it neither opens a conversation nor reports when the turn is terminal. So the target is handed
two injected collaborators for what the scoped context cannot do: `conversations` opens a fresh
conversation per case, and `outcome` awaits the admitted turn's terminal transcript."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    SharedArtifact,
    ToolInvocation,
    TurnLog,
)
from evals.harness.judge import JudgeLeg
from ufo.blob import BlobNotFound, BlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.sdk.context import ExtensionContext, Trajectory
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.transcript import TranscriptDecodeError, decode, transcript_key

MAX_EVAL_ARTIFACTS = 8
MAX_EVAL_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_EVAL_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024
TERMINAL_CHILD_STATUSES = frozenset({"done", "failed", "cancelled"})
CHILD_TRANSCRIPT_POLL_SECONDS = 0.2
CHILD_TRANSCRIPT_POLL_ATTEMPTS = 25


@dataclass(frozen=True)
class _ChildTrajectory:
    """One delegated child conversation's contribution to the scored output: the id strings that
    reference it (its conversation id and every turn id in it — follow-up turns included), and its
    trajectory with its own descendants already merged in."""

    refs: frozenset[str]
    output: CapabilityOutput


@dataclass(frozen=True)
class ArtifactCollection:
    artifacts: tuple[SharedArtifact, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class TargetResult:
    """A target's reconstruction of one run: the grader-visible output, whether the turn terminated
    cleanly, and the failure reason when it did not (the grader is skipped on an unclean run)."""

    output: CapabilityOutput
    clean: bool
    failure_reason: str = ""


class CapabilityTarget(Protocol):
    @property
    def judge(self) -> JudgeLeg | None: ...

    async def run(self, case: CapabilityCase) -> TargetResult: ...


class EvalConversations(Protocol):
    async def open(self, case_name: str, member_key: str | None = None) -> UUID: ...


class TurnOutcome(Protocol):
    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None: ...


class TurnLogReader(Protocol):
    async def read(self, turn_id: UUID) -> TurnLog | None: ...

    async def discard(self, turn_id: UUID) -> None: ...


@dataclass(frozen=True)
class InProcessTarget:
    ctx: ExtensionContext
    agent_id: UUID
    conversations: EvalConversations
    outcome: TurnOutcome
    judge: JudgeLeg | None = None
    blob: BlobStore | None = None
    logs: TurnLogReader | None = None

    async def run(self, case: CapabilityCase) -> TargetResult:
        conversation_id = await self.conversations.open(case.name, case.member_key)
        try:
            turn_id = await self.ctx.invoke(
                conversation_id,
                self.agent_id,
                case.message,
                f"{case.name}:{conversation_id}",
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            return TargetResult(
                CapabilityOutput("", (), (message,)), False, f"invoke raised: {message}"
            )
        trajectory = await self.outcome.settle(conversation_id, turn_id)
        if trajectory is None:
            if self.logs is not None:
                await self.logs.discard(turn_id)
            return TargetResult(
                CapabilityOutput("", (), ()), False, "turn produced no terminal transcript"
            )
        output = capability_output(trajectory.messages)
        children, descendant_ids, missing_child = await self._descendants(turn_id)
        if missing_child:
            return TargetResult(output, clean=False, failure_reason=missing_child)
        output = _splice(output, children)
        turn_failure = await self._turn_failure(turn_id)
        if turn_failure:
            if self.logs is not None:
                await self.logs.discard(turn_id)
            return TargetResult(output, clean=False, failure_reason=turn_failure)
        if self.logs is not None:
            log = await self.logs.read(turn_id)
            if log is None:
                raise RuntimeError("turn produced no required log")
            output = replace(output, log=log)
        if self.blob is not None:
            collected = await self._shared_artifacts((turn_id, *descendant_ids))
            output = replace(output, artifacts=collected.artifacts, artifact_error=collected.error)
        return TargetResult(output, clean=True)

    async def _descendants(
        self, turn_id: UUID
    ) -> tuple[tuple[_ChildTrajectory, ...], tuple[UUID, ...], str]:
        """Every child conversation this turn delegated to, in spawn order, each read once — a
        follow-up turn (message_subagent) shares its child's conversation and transcript, so the
        conversation is the merge unit, never the turn. A delegated capability (browser_task,
        wide_browse, spawn_subagent) proves itself by the raw calls its children actually
        dispatched, never by the wrapper's summary. A conversation with no terminal turn never
        informed the parent's answer and is skipped; a terminal one whose transcript never
        appears or does not decode is an infrastructure failure (third return), never a silently
        thinner trajectory. Also returns every descendant turn id, so artifact collection sees
        files a child shared."""
        if self.blob is None:
            return (), (), ""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.status,
                    )
                    .where(tables.turn.c.parent_turn_id == turn_id)
                    .order_by(tables.turn.c.created_at)
                )
            ).all()
        conversations: dict[UUID, list[sa.Row]] = {}
        for row in rows:
            conversations.setdefault(row.conversation_id, []).append(row)
        children: list[_ChildTrajectory] = []
        turn_ids: list[UUID] = []
        for conversation_id, turns in conversations.items():
            turn_ids.extend(turn.id for turn in turns)
            if not any(turn.status in TERMINAL_CHILD_STATUSES for turn in turns):
                continue
            body = await self._await_child_transcript(conversation_id)
            if body is None:
                failure = f"child turn {turns[0].id} is terminal but its transcript never appeared"
                return (), (), failure
            try:
                decoded = decode(body)
            except TranscriptDecodeError:
                return (), (), f"child conversation {conversation_id} has a corrupt transcript"
            output = capability_output(decoded.messages)
            grandchildren: list[_ChildTrajectory] = []
            for turn in turns:
                sub_children, sub_ids, failure = await self._descendants(turn.id)
                if failure:
                    return (), (), failure
                grandchildren.extend(sub_children)
                turn_ids.extend(sub_ids)
            refs = frozenset({str(conversation_id), *(str(turn.id) for turn in turns)})
            children.append(_ChildTrajectory(refs=refs, output=_splice(output, grandchildren)))
        return tuple(children), tuple(turn_ids), ""

    async def _await_child_transcript(self, conversation_id: UUID) -> bytes | None:
        """A terminal turn's transcript lands after its terminal commit, so a child observed
        terminal by its parent may not have written it yet — poll briefly, exactly as the driver's
        settle polls the turn row."""
        if self.blob is None:
            return None
        for _ in range(CHILD_TRANSCRIPT_POLL_ATTEMPTS):
            try:
                return await self.blob.get(transcript_key(conversation_id))
            except BlobNotFound:
                await asyncio.sleep(CHILD_TRANSCRIPT_POLL_SECONDS)
        return None

    async def _turn_failure(self, turn_id: UUID) -> str:
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one_or_none()
        if status is None:
            return "turn row disappeared before grading"
        if status != "done":
            return f"turn ended with status {status}"
        return ""

    async def _shared_artifacts(self, turn_ids: tuple[UUID, ...]) -> ArtifactCollection:
        """Artifacts the run shared, from the evaluated turn and every delegated descendant — a
        child's share_file records against the child turn, and its file must be as visible to a
        scorer as the call that shared it."""
        blob = self.blob
        if blob is None:
            raise RuntimeError("artifact collection requires a blob store")
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.size_bytes,
                    )
                    .where(tables.shared_artifact.c.turn_id.in_(list(turn_ids)))
                    .order_by(tables.shared_artifact.c.created_at)
                )
            ).all()
        if len(rows) > MAX_EVAL_ARTIFACTS:
            return ArtifactCollection(error=f"turn shared more than {MAX_EVAL_ARTIFACTS} artifacts")
        oversized = next((row for row in rows if row.size_bytes > MAX_EVAL_ARTIFACT_BYTES), None)
        if oversized is not None:
            return ArtifactCollection(
                error=f"artifact {oversized.filename!r} exceeds {MAX_EVAL_ARTIFACT_BYTES} bytes"
            )
        if sum(row.size_bytes for row in rows) > MAX_EVAL_ARTIFACT_TOTAL_BYTES:
            return ArtifactCollection(
                error=f"shared artifacts exceed {MAX_EVAL_ARTIFACT_TOTAL_BYTES} total bytes"
            )
        artifacts: list[SharedArtifact] = []
        for row in rows:
            try:
                content = await blob.get(row.blob_key)
            except BlobNotFound:
                return ArtifactCollection(
                    error=f"artifact {row.filename!r} is missing from storage"
                )
            if len(content) != row.size_bytes:
                return ArtifactCollection(
                    error=f"artifact {row.filename!r} size does not match its row"
                )
            artifacts.append(SharedArtifact(row.filename, content))
        return ArtifactCollection(tuple(artifacts))


def capability_output(messages: tuple[Message, ...]) -> CapabilityOutput:
    """Rebuild the grader-visible output from the transcript: the final answer (the last assistant
    text), the ordered tool calls (each tool_use joined to its tool_result by id), and the error
    text of any call that failed."""
    result_by_id: dict[str, ToolResultBlock] = {}
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                result_by_id[block.tool_use_id] = block
    calls: list[ToolInvocation] = []
    errors: list[str] = []
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                result = result_by_id.get(block.id)
                calls.append(
                    ToolInvocation(
                        block.name,
                        dict(block.input),
                        _result_text(result),
                        has_result=result is not None,
                        is_error=result.is_error if result is not None else False,
                    )
                )
                if result is not None and result.is_error:
                    errors.append(_result_text(result))
    return CapabilityOutput(_final_answer(messages), tuple(calls), tuple(errors))


def _splice(
    output: CapabilityOutput, children: tuple[_ChildTrajectory, ...] | list[_ChildTrajectory]
) -> CapabilityOutput:
    """Fold each child conversation's calls and tool errors into the trajectory at the point its
    output became visible: after the last call whose result names the child (wrappers surface the
    subagent id — a background spawn's ack and its later wait both do, so a waited child lands at
    the wait). A child no call references appends at the end, so membership scoring never loses
    it."""
    if not children:
        return output
    placed: dict[int, list[_ChildTrajectory]] = {}
    unreferenced: list[_ChildTrajectory] = []
    for child in children:
        anchor_index = None
        for index, call in enumerate(output.calls):
            if any(ref in call.result for ref in child.refs):
                anchor_index = index
        if anchor_index is None:
            unreferenced.append(child)
        else:
            placed.setdefault(anchor_index, []).append(child)
    calls: list[ToolInvocation] = []
    errors = list(output.tool_errors)
    for index, call in enumerate(output.calls):
        calls.append(call)
        for child in placed.get(index, ()):
            calls.extend(child.output.calls)
            errors.extend(child.output.tool_errors)
    for child in unreferenced:
        calls.extend(child.output.calls)
        errors.extend(child.output.tool_errors)
    return replace(output, calls=tuple(calls), tool_errors=tuple(errors))


def _final_answer(messages: tuple[Message, ...]) -> str:
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        if isinstance(message.content, str):
            return message.content
        return "".join(block.text for block in message.content if isinstance(block, TextBlock))
    return ""


def _result_text(result: ToolResultBlock | None) -> str:
    if result is None:
        return ""
    if isinstance(result.content, str):
        return result.content
    return "".join(block.text for block in result.content if isinstance(block, TextBlock))
