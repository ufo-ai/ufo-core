"""The in-process target: drive one case as a real turn through the agent, then reconstruct the
grader-visible output from the durable transcript.

`invoke` is the whole seam the scoped ExtensionContext exposes — it admits a turn and returns its
id; it neither opens a conversation nor reports when the turn is terminal. So the target is handed
two injected collaborators for what the scoped context cannot do: `conversations` opens a fresh
conversation per case, and `outcome` awaits the admitted turn's terminal transcript."""

from __future__ import annotations

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

MAX_EVAL_ARTIFACTS = 8
MAX_EVAL_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_EVAL_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024


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
            collected = await self._shared_artifacts(turn_id)
            output = replace(output, artifacts=collected.artifacts, artifact_error=collected.error)
        return TargetResult(output, clean=True)

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

    async def _shared_artifacts(self, turn_id: UUID) -> ArtifactCollection:
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
                    .where(tables.shared_artifact.c.turn_id == turn_id)
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
