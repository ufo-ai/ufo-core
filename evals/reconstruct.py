"""Rebuild a diagnostic copy of a run recorded without evidence from durable workspace state.

A case's turns, transcripts, and artifacts outlive its recording: the eval conversation row names
its case in `queue_key`, the turn row holds the inbound prompt and terminal accounting, and the
blob store holds the transcript. This workflow re-derives what the recorder failed to keep —
prompt, response, tool calls, trajectory, resources — and writes the result beside the archive
under `reconstructions/`, leaving the immutable original run untouched. Verdicts and scalar grader
evidence are copied from the original: grading saw the full output even when recording scrubbed
it. Conversations are matched to attempts chronologically, newest before the run's completion
first, so a workspace that hosted several runs of one case can mismatch — every reconstructed
attempt carries its conversation id for the operator to verify."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field

from evals.driver import EVAL_SURFACE
from evals.harness.capability import EvalTrajectory, recorded_evidence_missing
from evals.harness.harness import EvalCaseResult, EvalReport, Json, JsonObject
from evals.harness.target import (
    capability_output,
    compaction_snapshots,
    trajectory_snapshot,
)
from evals.harness.viewer import EvalRun, render_viewer, write_atomic
from ufo.blob import BlobNotFound, BlobStore
from ufo.db import workspace_tx
from ufo.runtime.turns.transcript import (
    Conversation,
    TranscriptDecodeError,
    decode,
    read_compaction_records,
    transcript_key,
)
from ufo.schema import tables
from ufo.schema.records import TerminalFrame

RECONSTRUCTIONS_DIR = "reconstructions"


class RecordedCall(BaseModel):
    name: str
    has_result: bool = Field(alias="hasResult")
    is_error: bool = Field(alias="isError")


class RecordedAttempt(BaseModel):
    """The fields of a scrubbed attempt that survived recording — the historical
    redact-at-record shape a reconstruction starts from."""

    passed: bool
    calls: tuple[RecordedCall, ...]
    tool_errors: tuple[str, ...] = Field(alias="toolErrors")
    artifact_error: str | None = Field(default=None, alias="artifactError")
    tokens: int = 0
    cost_micro_usd: int = Field(default=0, alias="costMicroUsd")
    compactions: int = 0
    grader: JsonObject | None = None


class RecordedEvidence(BaseModel):
    selected_attempt: int = Field(alias="selectedAttempt")
    attempts: tuple[RecordedAttempt, ...]


def write_reconstruction(out: Path, run: EvalRun) -> tuple[Path, Path]:
    """Write one reconstructed run and its single-run viewer page beside the archive, returning
    both paths. Sync file I/O, so it runs off the loop, after the async rebuild returns."""
    directory = out / RECONSTRUCTIONS_DIR
    record = directory / f"{run.id}.json"
    write_atomic(record, run.model_dump_json(indent=2, by_alias=True, exclude_none=True).encode())
    page = directory / f"{run.id}.html"
    write_atomic(page, render_viewer((run,), run.id))
    return record, page


@dataclass(frozen=True)
class RunReconstruction:
    workspace_id: UUID
    blob: BlobStore
    run: EvalRun

    async def reconstruct(self) -> EvalRun:
        """Rebuild every case the recorder scrubbed into a diagnostic copy of the run."""
        reports = tuple([await self._report(report) for report in self.run.reports])
        label = f"{self.run.label or self.run.revision} · reconstructed"
        return self.run.model_copy(update={"label": label, "reports": reports})

    async def _report(self, report: EvalReport) -> EvalReport:
        cases = tuple([await self._case(case) for case in report.cases])
        return report.model_copy(update={"cases": cases})

    async def _case(self, case: EvalCaseResult) -> EvalCaseResult:
        if not recorded_evidence_missing(case):
            return case
        recorded = RecordedEvidence.model_validate(case.evidence)
        conversation_ids = await self._case_conversations(case.name, len(recorded.attempts))
        attempts: list[Json] = []
        message: str | None = None
        for index, attempt in enumerate(recorded.attempts):
            if index >= len(conversation_ids):
                attempts.append(self._unreconstructable(attempt))
                continue
            rebuilt, inbound = await self._attempt(attempt, conversation_ids[index])
            attempts.append(rebuilt)
            message = message or inbound
        evidence: JsonObject = {
            "message": message,
            "grading": case.evidence.get("grading") or None,
            "rubric": case.evidence.get("rubric") or [],
            "reconstructed": True,
            "selectedAttempt": recorded.selected_attempt,
            "attempts": attempts,
        }
        return case.model_copy(update={"evidence": evidence})

    async def _case_conversations(self, case_name: str, count: int) -> tuple[UUID, ...]:
        """The case's most recent `count` eval conversations completed before the run was
        recorded, oldest first — the order attempts executed in."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.conversation.c.id)
                    .where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.queue_key.startswith(
                            f"{EVAL_SURFACE}:{case_name}:", autoescape=True
                        ),
                        tables.conversation.c.created_at <= self.run.created_at,
                    )
                    .order_by(tables.conversation.c.created_at)
                )
            ).all()
        return tuple(row.id for row in rows[-count:])

    async def _attempt(
        self, attempt: RecordedAttempt, conversation_id: UUID
    ) -> tuple[Json, str | None]:
        async with workspace_tx() as connection:
            turns = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.seq,
                        tables.turn.c.status,
                        tables.turn.c.inbound,
                    )
                    .where(tables.turn.c.conversation_id == conversation_id)
                    .order_by(tables.turn.c.seq)
                )
            ).all()
        if not turns:
            return self._unreconstructable(attempt), None
        turn = turns[-1]
        conversation, transcript_error = await self._transcript(conversation_id, turn.seq)
        if conversation is None:
            unreconstructable = self._unreconstructable(attempt)
            unreconstructable["trajectory"] = EvalTrajectory(
                conversation_id=conversation_id,
                turn_id=turn.id,
                status=turn.status,
                messages=(),
                error=transcript_error,
            ).model_dump(mode="json")
            return unreconstructable, turn.inbound
        output = capability_output(conversation.messages)
        snapshot = trajectory_snapshot(conversation_id, turn.id, turn.status, conversation.messages)
        turn_ids = await self._turn_tree(turn.id)
        tokens, cost_micro_usd = await self._resources(turn_ids)
        records = await read_compaction_records(self.blob, conversation_id)
        calls: list[Json] = [
            {
                "name": call.name,
                "input": call.input,
                "result": call.result,
                "hasResult": call.has_result,
                "isError": call.is_error,
            }
            for call in output.calls
        ]
        rebuilt: JsonObject = {
            "passed": attempt.passed,
            "reason": "passed" if attempt.passed else "failed",
            "response": output.response,
            "calls": calls,
            "toolErrors": list(output.tool_errors),
            "artifacts": await self._artifact_names(turn_ids),
            "artifactError": None,
            "tokens": tokens,
            "costMicroUsd": cost_micro_usd,
            "log": None,
            "compactions": len(records),
            "compactionRecords": (
                [record.model_dump(mode="json") for record in compaction_snapshots(records)] or None
            ),
            "grader": attempt.grader,
            "trajectory": snapshot.model_dump(mode="json"),
        }
        return rebuilt, turn.inbound

    async def _transcript(
        self, conversation_id: UUID, turn_seq: int
    ) -> tuple[Conversation | None, str]:
        try:
            body = await self.blob.get(transcript_key(conversation_id))
        except BlobNotFound:
            return None, "transcript is missing from the blob store"
        try:
            conversation = decode(body)
        except TranscriptDecodeError:
            return None, "transcript does not decode"
        if conversation.seq != turn_seq:
            return (
                None,
                f"transcript sequence {conversation.seq} does not match turn sequence {turn_seq}",
            )
        return conversation, ""

    async def _turn_tree(self, turn_id: UUID) -> tuple[UUID, ...]:
        collected: list[UUID] = [turn_id]
        frontier: list[UUID] = [turn_id]
        while frontier:
            async with workspace_tx() as connection:
                rows = (
                    await connection.execute(
                        sa.select(tables.turn.c.id).where(
                            tables.turn.c.parent_turn_id.in_(frontier)
                        )
                    )
                ).all()
            frontier = [row.id for row in rows]
            collected.extend(frontier)
        return tuple(collected)

    async def _resources(self, turn_ids: tuple[UUID, ...]) -> tuple[int, int]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id.in_(list(turn_ids)))
                )
            ).all()
        frames = tuple(
            TerminalFrame.model_validate(row.terminal) for row in rows if row.terminal is not None
        )
        return sum(frame.tokens for frame in frames), sum(frame.cost_micro_usd for frame in frames)

    async def _artifact_names(self, turn_ids: tuple[UUID, ...]) -> list[Json]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.shared_artifact.c.filename)
                    .where(tables.shared_artifact.c.turn_id.in_(list(turn_ids)))
                    .order_by(tables.shared_artifact.c.created_at)
                )
            ).all()
        return [row.filename for row in rows]

    def _unreconstructable(self, attempt: RecordedAttempt) -> JsonObject:
        """The original attempt, kept in its recorded-without-evidence shape so the viewer still
        reports it as unavailable rather than dressing it as reconstructed."""
        return {
            "passed": attempt.passed,
            "reason": "passed" if attempt.passed else "failed",
            "response": None,
            "calls": [
                {
                    "name": call.name,
                    "input": {},
                    "result": "",
                    "hasResult": call.has_result,
                    "isError": call.is_error,
                }
                for call in attempt.calls
            ],
            "toolErrors": list(attempt.tool_errors),
            "artifacts": [],
            "artifactError": attempt.artifact_error,
            "tokens": attempt.tokens,
            "costMicroUsd": attempt.cost_micro_usd,
            "log": None,
            "compactions": attempt.compactions,
            "grader": attempt.grader,
            "trajectory": None,
        }
