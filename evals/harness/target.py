"""The in-process target: drive one case as a real turn through the agent, then reconstruct the
grader-visible output from the durable transcript.

`invoke` is the whole seam the scoped ExtensionContext exposes — it admits a turn and returns its
id; it neither opens a conversation nor reports when the turn is terminal. So the target is handed
two injected collaborators for what the scoped context cannot do: `conversations` opens a fresh
conversation per case, and `outcome` awaits the admitted turn's terminal transcript."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import (
    MAX_ARTIFACT_PAYLOAD_BYTES,
    ArtifactProbe,
    ArtifactProbeResult,
    CapabilityCase,
    CapabilityOutput,
    EvalTrajectory,
    ProbeCommandResult,
    SharedArtifact,
    SharedArtifactReference,
    StoredRollover,
    ToolInvocation,
    TurnLog,
    UndeliveredRound,
    WorkspaceFile,
    WorkspaceProbe,
)
from evals.harness.handoff import handoff_record
from evals.harness.harness import WAIT_EXPIRED, Json
from evals.harness.judge import JudgeLeg
from evals.harness.timing import CaseTiming, TurnSteps, TurnTiming, case_timing, turn_timing
from ufo.blob import BlobNotFound, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.replies import marked_artifacts
from ufo.runtime.ext.context import ConversationProbes
from ufo.runtime.tools.registry import OBJECT_ACTION_TOOL
from ufo.runtime.turns.transcript import (
    RolloverRecord,
    TranscriptDecodeError,
    decode,
    read_rollover_records,
    transcript_key,
)
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import (
    DELIVERY_PENDING,
    NON_TERMINAL_STATUSES,
    CredentialRequest,
    TerminalFrame,
    TurnStatus,
)
from ufo.sdk.context import ExtensionContext, Trajectory
from ufo.sdk.models import (
    ImageBlock,
    Message,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

if TYPE_CHECKING:
    from evals.compaction.target import CompactionTarget
    from evals.mcp_atlas_100.target import McpAtlasTarget
    from evals.rollover.target import RolloverTarget

MAX_EVAL_ARTIFACTS = 256
MAX_EVAL_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_EVAL_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024
MAX_EVAL_TRAJECTORY_BYTES = 8 * 1024 * 1024
MAX_EVAL_ROLLOVER_BYTES = 8 * 1024 * 1024
PRIVATE_HANDOFF_ACTION = ("credential", "request_credentials")
PRIVATE_HANDOFF_REDACTED = "[private handoff redacted]"
REASONING_EVIDENCE = "[reasoning]\n{summary}"
REDACTED_REASONING_EVIDENCE = "[reasoning redacted by the provider]"
TERMINAL_CHILD_STATUSES = frozenset({"done", "failed", "cancelled"})
TOKEN_DIMENSION = "tokens"
CHILD_TRANSCRIPT_POLL_SECONDS = 0.2
CHILD_TRANSCRIPT_POLL_ATTEMPTS = 25


@dataclass(frozen=True)
class ArtifactCollection:
    artifacts: tuple[SharedArtifact, ...] = ()
    references: tuple[SharedArtifactReference, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class TargetResult:
    """A target's reconstruction of one run: the grader-visible output, whether the turn terminated
    cleanly, and the failure reason when it did not (the grader is skipped on an unclean run)."""

    output: CapabilityOutput
    clean: bool
    failure_reason: str = ""
    error_class: str | None = None
    trajectory: EvalTrajectory | None = None
    error_message: str = ""
    expiry_status: TurnStatus | None = None
    """The status the turn held when the harness's wait expired on it, read before the cancel that
    expiry fired. `trajectory.status` is that cancel's own terminal — `cancelled` for a turn the
    provider was holding and for one still queued behind our workers alike — so this is the only
    field that names who spent the deadline."""
    work_started: bool = False
    """Whether the turn had recorded a durable engine step of its own when the wait expired. The
    row turns `running` at the dispatch claim, so the status alone cannot separate a turn the
    provider is answering from one still inside the rig's startup; the engine reaches its first
    step only after that claim, the sandbox boot and the preloaded mounts."""


@dataclass(frozen=True)
class _Settled:
    """One settled turn's result plus every descendant turn id, which only `run`'s artifact
    collection consumes."""

    result: TargetResult
    descendant_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class _ConversationWorkspaceProbe(WorkspaceProbe):
    probes: ConversationProbes
    conversation_id: UUID

    async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
        result = await self.probes.run(self.conversation_id, command, timeout_s)
        return ProbeCommandResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out_after_s=result.timed_out_after_s,
        )


def _invoke_failure(conversation_id: UUID, error: Exception) -> TargetResult:
    message = f"{type(error).__name__}: {error}"
    return TargetResult(
        CapabilityOutput("", (), (message,)),
        False,
        f"invoke raised: {message}",
        trajectory=EvalTrajectory(
            conversation_id=conversation_id,
            turn_id=None,
            status=None,
            messages=(),
            error=f"invoke raised: {message}",
        ),
    )


def _seed_failure(error: Exception) -> TargetResult:
    """A seed that raises fails its own case and leaves the suite standing. Raising instead loses
    every case of the suite and the report with them, so a precondition one case cannot meet reads
    as a suite that never ran (`github_connections` produced no report on two 2026-09 nights)."""
    message = f"{type(error).__name__}: {error}"
    return TargetResult(CapabilityOutput("", (), (message,)), False, f"seed raised: {message}")


def _is_turn_inbound(message: Message, inbound: str) -> bool:
    return (
        message.role == "user"
        and isinstance(message.content, str)
        and (
            message.content == inbound
            or message.content.endswith(f"\n{inbound}")
            or message.content.startswith(f"{inbound}\n\n<injected_context>")
            or f"\n{inbound}\n\n<injected_context>" in message.content
        )
    )


def _current_turn_messages(messages: tuple[Message, ...], inbound: str) -> tuple[Message, ...]:
    for index in range(len(messages) - 1, -1, -1):
        if _is_turn_inbound(messages[index], inbound):
            return messages[index:]
    return messages


def _turn_windows(
    messages: tuple[Message, ...], inbounds: tuple[str, ...]
) -> tuple[tuple[int, int], ...]:
    starts: list[int | None] = []
    search = 0
    for inbound in inbounds:
        index = next(
            (
                position
                for position in range(search, len(messages))
                if _is_turn_inbound(messages[position], inbound)
            ),
            None,
        )
        starts.append(index)
        if index is not None:
            search = index + 1
    windows: list[tuple[int, int]] = []
    for position, start in enumerate(starts):
        end = next(
            (candidate for candidate in starts[position + 1 :] if candidate is not None),
            len(messages),
        )
        windows.append((end, end) if start is None else (start, end))
    return tuple(windows)


class CapabilityTarget(Protocol):
    @property
    def judge(self) -> JudgeLeg | None: ...

    @property
    def simulator(self) -> JudgeLeg | None: ...

    @property
    def agent_id(self) -> UUID: ...

    @property
    def conversations(self) -> EvalConversations: ...

    async def run(self, case: CapabilityCase) -> TargetResult: ...

    async def step(
        self, conversation_id: UUID, message: str, idempotency_key: str
    ) -> TargetResult: ...

    async def capture_artifacts(
        self,
        conversation_id: UUID,
        output: CapabilityOutput,
        capture: ArtifactProbe,
    ) -> CapabilityOutput: ...

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        *,
        as_scheduled: bool,
    ) -> TargetResult: ...


class EvalConversations(Protocol):
    async def open(
        self,
        case_name: str,
        member_key: str | None = None,
        workspace_files: tuple[WorkspaceFile, ...] = (),
        prior_messages: tuple[str, ...] = (),
        prior_transcript: tuple[Message, ...] = (),
        undelivered: tuple[UndeliveredRound, ...] = (),
        shared: bool = False,
    ) -> UUID: ...

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        speaker_key: str | None = None,
    ) -> UUID: ...

    async def stage(self, conversation_id: UUID, path: str, source: Path) -> None: ...

    def workspace_path(self, conversation_id: UUID, rel: str) -> Path: ...


class TurnOutcome(Protocol):
    @property
    def workflow_wait_seconds(self) -> float | None:
        """Seconds a logical workflow may take to settle before the harness cancels it and records
        WAIT_EXPIRED; None never expires, so an outcome asserting any other path cannot depend on
        wall time."""
        ...

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None: ...

    async def cancel(self, turn_id: UUID) -> bool: ...

    def cancelled_from(self, turn_id: UUID) -> TurnStatus | None:
        """The status this outcome's own cancel found the turn in, None for a turn it never
        cancelled: what the wait expired on, before the cancel wrote its terminal over it."""
        ...


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
    simulator: JudgeLeg | None = None
    blob: WorkspaceBlobStore | None = None
    logs: TurnLogReader | None = None
    turn_steps: TurnSteps | None = None
    mcp_atlas: McpAtlasTarget | None = None
    rollover: RolloverTarget | None = None
    compaction: CompactionTarget | None = None
    loadable_skills: frozenset[str] | None = None
    workspace_probe_for: Callable[[UUID], WorkspaceProbe] | None = None

    async def preflight_mcp_atlas(self, required_tool_servers: dict[str, str]) -> frozenset[str]:
        if self.mcp_atlas is None:
            raise RuntimeError("MCP-Atlas suite requires --mcp-atlas-url")
        return await self.mcp_atlas.preflight(required_tool_servers)

    async def run_mcp_atlas(
        self,
        prompt: str,
        enabled_tools: tuple[str, ...],
        tool_servers: dict[str, str],
    ) -> TargetResult:
        if self.mcp_atlas is None:
            raise RuntimeError("MCP-Atlas suite requires --mcp-atlas-url")
        return await self.mcp_atlas.run(prompt, enabled_tools, tool_servers)

    async def run(self, case: CapabilityCase) -> TargetResult:
        return await self._run_case(case)

    async def cleanup(self, case: CapabilityCase) -> None:
        """Clean a case after its grader has read the turn's durable effects."""
        cleanup = case.cleanup
        if cleanup is None:
            return
        if self.blob is None:
            raise RuntimeError("a capability case with cleanup requires blob access")
        await cleanup(ws_current().workspace_id, self.agent_id, self.blob)

    async def _run_case(self, case: CapabilityCase) -> TargetResult:
        if case.seed is not None:
            if self.blob is None:
                raise RuntimeError("a seeded capability case requires blob access")
            try:
                await case.seed(ws_current().workspace_id, self.agent_id, self.blob)
            except Exception as error:
                return _seed_failure(error)
        conversation_id = await self.conversations.open(
            case.name,
            case.member_key,
            case.workspace_files,
            case.prior_messages,
            case.prior_transcript,
            case.undelivered,
            shared=case.shared_audience,
        )
        for reference in case.references:
            await self.conversations.stage(
                conversation_id, f"references/{reference.path}", reference.source
            )
        if case.prepare is not None:
            await case.prepare(
                ws_current().workspace_id, self.conversations.workspace_path(conversation_id, "")
            )
        started = perf_counter()
        try:
            turn_id = await self.conversations.admit(
                conversation_id,
                case.message,
                f"{case.name}:{conversation_id}",
                speaker_key=case.member_key,
            )
        except Exception as error:
            return _invoke_failure(conversation_id, error)
        settled = (
            await self._settled_workflow(conversation_id, turn_id, case.message)
            if case.wait_for_background
            else await self._settled(
                conversation_id,
                turn_id,
                case.message,
                score_current_turn_only=bool(case.prior_transcript),
            )
        )
        wall_ms = round((perf_counter() - started) * 1_000)
        result = await self._record_timing(settled, turn_id, wall_ms)
        output = result.output
        if self.blob is not None:
            collected = await self._shared_artifacts((turn_id, *settled.descendant_ids))
            records = await read_rollover_records(self.blob, conversation_id)
            output = replace(
                output,
                artifacts=collected.artifacts,
                artifact_references=collected.references,
                artifact_error=collected.error,
                rollovers=len(records),
                rollover_records=rollover_snapshots(records),
            )
            result = replace(result, output=output)
        if not result.clean:
            if self.logs is not None:
                await self.logs.discard(turn_id)
            return result
        output = replace(
            output,
            workspace_dir=self.conversations.workspace_path(conversation_id, ""),
        )
        if self.logs is not None:
            log = await self.logs.read(turn_id)
            if log is None:
                raise RuntimeError("turn produced no required log")
            output = replace(output, log=log)
        if case.artifact_probe is not None:
            output = await self.capture_artifacts(conversation_id, output, case.artifact_probe)
        return replace(result, output=output)

    async def capture_artifacts(
        self,
        conversation_id: UUID,
        output: CapabilityOutput,
        capture: ArtifactProbe,
    ) -> CapabilityOutput:
        """Run one grader-owned workspace probe and join its bounded artifacts."""

        if self.ctx.probes is not None:
            probe: WorkspaceProbe = _ConversationWorkspaceProbe(self.ctx.probes, conversation_id)
        elif self.workspace_probe_for is not None:
            probe = self.workspace_probe_for(conversation_id)
        else:
            raise RuntimeError("an artifact probe requires conversation probes")
        captured = await capture(output, probe)
        if not isinstance(captured, ArtifactProbeResult):
            raise RuntimeError("an artifact probe returned an invalid result")
        if not 0 < captured.max_payload_bytes <= MAX_ARTIFACT_PAYLOAD_BYTES:
            raise RuntimeError("an artifact probe returned an invalid payload budget")
        output_digests = {
            artifact.name: sha256(artifact.content).hexdigest() for artifact in output.artifacts
        }
        durable = {
            (reference.name, reference.digest, reference.size_bytes)
            for reference in output.artifact_references
        }
        artifacts: list[SharedArtifact] = []
        payload_errors: list[str] = []
        total = 0
        for artifact in output.artifacts:
            size = len(artifact.content)
            digest = f"sha256:{sha256(artifact.content).hexdigest()}"
            identity = (artifact.name, digest, size)
            exceeds_entry = size > MAX_ARTIFACT_PAYLOAD_BYTES
            exceeds_total = total + size > MAX_ARTIFACT_PAYLOAD_BYTES
            if exceeds_entry or exceeds_total:
                if identity in durable:
                    artifacts.append(artifact)
                    continue
                scope = "offline payload" if exceeds_entry else "cumulative offline payload"
                payload_errors.append(
                    f"artifact {artifact.name!r} ({digest}, {size} bytes) has no durable "
                    f"reference and exceeds the {MAX_ARTIFACT_PAYLOAD_BYTES}-byte {scope} limit"
                )
                continue
            total += size
            artifacts.append(artifact)
        captured_total = 0
        for artifact in captured.artifacts:
            size = len(artifact.content)
            digest = f"sha256:{sha256(artifact.content).hexdigest()}"
            prior_digest = output_digests.get(artifact.name)
            if prior_digest == digest.removeprefix("sha256:"):
                continue
            if prior_digest is not None:
                raise RuntimeError("an artifact probe produced a duplicate artifact name")
            exceeds_entry = size > captured.max_payload_bytes
            exceeds_total = captured_total + size > captured.max_payload_bytes
            exceeds_offline_total = total + size > MAX_ARTIFACT_PAYLOAD_BYTES
            if exceeds_entry or exceeds_total or exceeds_offline_total:
                scope = "offline payload" if exceeds_entry else "cumulative offline payload"
                limit = (
                    MAX_ARTIFACT_PAYLOAD_BYTES
                    if exceeds_offline_total and not (exceeds_entry or exceeds_total)
                    else captured.max_payload_bytes
                )
                payload_errors.append(
                    f"artifact {artifact.name!r} ({digest}, {size} bytes) has no durable "
                    f"reference and exceeds the {limit}-byte {scope} limit"
                )
                continue
            total += size
            captured_total += size
            artifacts.append(artifact)
        errors = "; ".join(
            error for error in (output.artifact_error, captured.error, *payload_errors) if error
        )
        return replace(
            output,
            artifacts=tuple(artifacts),
            artifact_error=errors,
        )

    async def _settled_workflow(
        self, conversation_id: UUID, turn_id: UUID, inbound: str
    ) -> _Settled:
        async with workspace_tx() as connection:
            origin_seq = (
                await connection.execute(
                    sa.select(tables.turn.c.seq).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one()
        processed: set[UUID] = set()
        root_turns: tuple[sa.Row, ...] = ()
        workflow_failure = ""
        failure_error_class = ""
        failure_error_message = ""
        timed_out = False
        try:
            async with asyncio.timeout(self.outcome.workflow_wait_seconds):
                while True:
                    async with workspace_tx() as connection:
                        root_turns = tuple(
                            (
                                await connection.execute(
                                    sa.select(
                                        tables.turn.c.id,
                                        tables.turn.c.inbound,
                                        tables.turn.c.status,
                                    )
                                    .where(
                                        tables.turn.c.conversation_id == conversation_id,
                                        tables.turn.c.seq >= origin_seq,
                                    )
                                    .order_by(tables.turn.c.seq)
                                )
                            ).all()
                        )
                    pending = tuple(row for row in root_turns if row.id not in processed)
                    if not pending:
                        break
                    for row in pending:
                        if row.status in NON_TERMINAL_STATUSES:
                            await self.outcome.settle(conversation_id, row.id)
                        status = await self._turn_status(row.id)
                        if status in NON_TERMINAL_STATUSES:
                            raise TimeoutError
                        failure = self._turn_failure(status)
                        _, _, descendant_failure = await self._merge_descendants(
                            row.id,
                            CapabilityOutput("", ()),
                            wait_for_background=True,
                        )
                        if failure or descendant_failure:
                            await self._cancel_workflow(conversation_id, origin_seq)
                            workflow_failure = failure or descendant_failure
                            failure_error_class, failure_error_message = await self._turn_error(
                                row.id
                            )
                            break
                        processed.add(row.id)
                    if workflow_failure:
                        break
        except TimeoutError:
            await self._cancel_workflow(conversation_id, origin_seq)
            workflow_failure = WAIT_EXPIRED
            timed_out = True
        if workflow_failure:
            async with workspace_tx() as connection:
                root_turns = tuple(
                    (
                        await connection.execute(
                            sa.select(
                                tables.turn.c.id,
                                tables.turn.c.inbound,
                                tables.turn.c.status,
                            )
                            .where(
                                tables.turn.c.conversation_id == conversation_id,
                                tables.turn.c.seq >= origin_seq,
                            )
                            .order_by(tables.turn.c.seq)
                        )
                    ).all()
                )
        if not root_turns:
            if timed_out:
                return await self._unsettled(conversation_id, turn_id, inbound)
            return await self._settled(conversation_id, turn_id, inbound)

        latest = root_turns[-1]
        if timed_out:
            settled = await self._unsettled(conversation_id, latest.id, latest.inbound)
        else:
            settled = await self._settled(
                conversation_id,
                latest.id,
                latest.inbound,
                wait_for_background=True,
            )
        if not settled.result.clean and not workflow_failure:
            return settled
        output = settled.result.output
        descendant_ids = list(settled.descendant_ids)
        merge_failure = ""
        for row in root_turns[:-1]:
            output, descendants, failure = await self._merge_descendants(row.id, output)
            descendant_ids.extend(descendants)
            if failure:
                merge_failure = failure
                break
        all_turn_ids = tuple(dict.fromkeys((*[row.id for row in root_turns], *descendant_ids)))
        tokens, cost_micro_usd = await self._turn_resources(all_turn_ids)
        output = replace(output, tokens=tokens, cost_micro_usd=cost_micro_usd)
        trajectory = settled.result.trajectory
        if trajectory is not None:
            trajectory = trajectory.model_copy(update={"turn_id": turn_id})
        result = settled.result
        failure_reason = workflow_failure or merge_failure
        if failure_reason:
            error_class = failure_error_class or result.error_class
            error_message = failure_error_message or result.error_message
            if failure_error_class:
                failure_reason = f"{failure_reason} ({failure_error_class})"
            result = replace(
                result,
                clean=False,
                failure_reason=failure_reason,
                error_class=error_class,
                error_message=error_message,
            )
        return _Settled(
            replace(result, output=output, trajectory=trajectory),
            tuple(item for item in all_turn_ids if item != turn_id),
        )

    async def _cancel_workflow(self, conversation_id: UUID, origin_seq: int) -> None:
        previous: tuple[frozenset[UUID], frozenset[UUID]] | None = None
        while turns := await self._workflow_turns(conversation_id, origin_seq):
            live = tuple(row for row in turns if row.status in NON_TERMINAL_STATUSES)
            pending = tuple(
                row
                for row in turns
                if row.status in TERMINAL_CHILD_STATUSES and row.result_delivery == DELIVERY_PENDING
            )
            if not live and not pending:
                return
            current = (
                frozenset(row.id for row in live),
                frozenset(row.id for row in pending),
            )
            if current == previous:
                raise RuntimeError("logical workflow cancellation made no progress")
            previous = current
            if live:
                live_ids = tuple(row.id for row in live)
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.turn)
                        .values(result_delivery=None, updated_at=sa.func.now())
                        .where(
                            tables.turn.c.id.in_(live_ids),
                            tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                            tables.turn.c.result_delivery == DELIVERY_PENDING,
                        )
                    )
                await asyncio.gather(*(self.outcome.cancel(turn_id) for turn_id in live_ids))
                continue
            await asyncio.gather(
                *(self.outcome.settle(row.conversation_id, row.id) for row in pending)
            )
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(result_delivery=None, updated_at=sa.func.now())
                    .where(
                        tables.turn.c.id.in_(tuple(row.id for row in pending)),
                        tables.turn.c.result_delivery == DELIVERY_PENDING,
                    )
                )

    async def _workflow_turns(self, conversation_id: UUID, origin_seq: int) -> tuple[sa.Row, ...]:
        async with workspace_tx() as connection:
            roots = tuple(
                (
                    await connection.execute(
                        sa.select(tables.turn.c.id).where(
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.seq >= origin_seq,
                        )
                    )
                )
                .scalars()
                .all()
            )
            turn_ids = list(roots)
            frontier = roots
            while frontier:
                frontier = tuple(
                    (
                        await connection.execute(
                            sa.select(tables.turn.c.id).where(
                                tables.turn.c.parent_turn_id.in_(frontier)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                turn_ids.extend(frontier)
            turns = tuple(
                (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.id,
                            tables.turn.c.conversation_id,
                            tables.turn.c.status,
                            tables.turn.c.result_delivery,
                        ).where(
                            tables.turn.c.id.in_(turn_ids),
                        )
                    )
                ).all()
            )
        return turns

    async def _case_timing(
        self,
        wall_ms: int,
        turn_ids: tuple[UUID, ...],
        output: CapabilityOutput,
        messages: tuple[Message, ...],
    ) -> CaseTiming:
        """Where the case's wall-clock went, per turn. Tool names come from the merged trajectory,
        which already carries the evaluated turn's calls and every delegated child's, each keyed by
        the call id its durable step records."""
        if self.turn_steps is None:
            return case_timing(wall_ms, (), "no step reader is wired")
        async with workspace_tx() as connection:
            parent_rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id, tables.turn.c.parent_turn_id, tables.turn.c.status
                    ).where(tables.turn.c.id.in_(turn_ids))
                )
            ).all()
        parents: dict[UUID, UUID | None] = {row.id: row.parent_turn_id for row in parent_rows}
        statuses: dict[UUID, TurnStatus] = {row.id: row.status for row in parent_rows}
        names = {call.call_id: call.name for call in output.calls if call.call_id}
        turns: list[TurnTiming] = []
        for turn_id in turn_ids:
            steps = await self.turn_steps.steps(turn_id)
            tokens, cost_micro_usd = await self._turn_resources((turn_id,))
            step_tokens = tuple(step.tokens for step in steps if step.tokens is not None)
            step_costs = tuple(
                step.cost_micro_usd for step in steps if step.cost_micro_usd is not None
            )
            tokens = max(tokens, sum(step_tokens))
            cost_micro_usd = max(cost_micro_usd, sum(step_costs))
            turns.append(
                turn_timing(
                    turn_id,
                    "child" if parents[turn_id] is not None else "evaluated",
                    steps,
                    names,
                    tokens,
                    cost_micro_usd,
                    messages if turn_id == turn_ids[0] else (),
                    statuses[turn_id],
                )
            )
        return case_timing(wall_ms, tuple(turns))

    async def step(self, conversation_id: UUID, message: str, idempotency_key: str) -> TargetResult:
        """Drive one member turn on an existing conversation and reconstruct its result — the
        scenario runner's per-exchange seam."""
        started = perf_counter()
        try:
            turn_id = await self.conversations.admit(conversation_id, message, idempotency_key)
        except Exception as error:
            return _invoke_failure(conversation_id, error)
        settled = await self._settled(conversation_id, turn_id, message)
        wall_ms = round((perf_counter() - started) * 1_000)
        result = await self._record_timing(settled, turn_id, wall_ms)
        if self.blob is None:
            return result
        collected = await self._shared_artifacts((turn_id, *settled.descendant_ids))
        return replace(
            result,
            output=replace(
                result.output,
                artifacts=collected.artifacts,
                artifact_references=collected.references,
                artifact_error=collected.error,
            ),
        )

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str,
        *,
        as_scheduled: bool,
    ) -> TargetResult:
        """Drive and reconstruct one internal turn admitted by a multi-flow eval case."""
        started = perf_counter()
        try:
            turn_id = await self.ctx.invoke(
                conversation_id,
                agent_id,
                message,
                idempotency_key,
                as_scheduled=as_scheduled,
            )
        except Exception as error:
            return _invoke_failure(conversation_id, error)
        if turn_id is None:
            return _invoke_failure(
                conversation_id, RuntimeError("internal turn admission returned no turn")
            )
        settled = await self._settled(conversation_id, turn_id, message, current_turn_only=True)
        wall_ms = round((perf_counter() - started) * 1_000)
        return await self._record_timing(settled, turn_id, wall_ms)

    async def _record_timing(self, settled: _Settled, turn_id: UUID, wall_ms: int) -> TargetResult:
        result = settled.result
        timing = await self._case_timing(
            wall_ms,
            (turn_id, *settled.descendant_ids),
            result.output,
            result.trajectory.messages if result.trajectory is not None else (),
        )
        recorded_tokens = sum(turn.tokens for turn in timing.turns)
        recorded_cost = sum(turn.cost_micro_usd for turn in timing.turns)
        return replace(
            result,
            output=replace(
                result.output,
                timing=timing,
                tokens=max(result.output.tokens, recorded_tokens),
                cost_micro_usd=max(result.output.cost_micro_usd, recorded_cost),
            ),
        )

    async def _settled(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        inbound: str,
        *,
        current_turn_only: bool = False,
        score_current_turn_only: bool = False,
        wait_for_background: bool = False,
    ) -> _Settled:
        trajectory = await self.outcome.settle(conversation_id, turn_id)
        if trajectory is None:
            return await self._unsettled(
                conversation_id,
                turn_id,
                inbound,
                wait_for_background=wait_for_background,
            )
        snapshot_messages = (
            _current_turn_messages(trajectory.messages, inbound)
            if current_turn_only
            else trajectory.messages
        )
        output_messages = (
            _current_turn_messages(trajectory.messages, inbound)
            if score_current_turn_only
            else snapshot_messages
        )
        output = capability_output(output_messages)
        output = replace(
            output,
            own_tools=tuple(call.call for call in output.calls),
            own_calls=tuple(output.calls),
        )
        status = await self._turn_status(turn_id)
        snapshot = trajectory_snapshot(conversation_id, turn_id, status, snapshot_messages)
        output, descendant_ids, missing_child = await self._merge_descendants(
            turn_id, output, wait_for_background
        )
        tokens, cost_micro_usd = await self._turn_resources((turn_id, *descendant_ids))
        output = replace(output, tokens=tokens, cost_micro_usd=cost_micro_usd)
        if missing_child:
            return _Settled(
                TargetResult(
                    output, clean=False, failure_reason=missing_child, trajectory=snapshot
                ),
                descendant_ids,
            )
        turn_failure = self._turn_failure(status)
        if turn_failure:
            error_class, error_message = await self._turn_error(turn_id)
            reason = f"{turn_failure} ({error_class})" if error_class else turn_failure
            return _Settled(
                TargetResult(
                    output,
                    clean=False,
                    failure_reason=reason,
                    error_class=error_class or None,
                    trajectory=snapshot,
                    error_message=error_message,
                ),
                descendant_ids,
            )
        return _Settled(TargetResult(output, clean=True, trajectory=snapshot), descendant_ids)

    async def _unsettled(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        inbound: str,
        *,
        wait_for_background: bool = False,
    ) -> _Settled:
        """The result for a run whose turn never wrote a terminal transcript.

        A fault before the first round — loading the turn, selecting the boundary, attaching the
        sandbox — leaves no transcript to carry it, and `WAIT_EXPIRED` alone names no fault. The
        turn row's terminal frame carries the class and the message, so this path reads it as the
        settled path does: the record says which fault one reason stood for, and a provider-owned
        transient in setup is excluded rather than scored against the model."""
        steps = () if self.turn_steps is None else await self.turn_steps.steps(turn_id)
        messages = (
            Message(role="user", content=inbound),
            *(message for step in steps for message in step.messages),
        )
        output = capability_output(messages)
        output = replace(
            output,
            own_tools=tuple(call.call for call in output.calls),
            own_calls=tuple(output.calls),
        )
        output, descendant_ids, missing_child = await self._merge_descendants(
            turn_id, output, wait_for_background
        )
        tokens, cost_micro_usd = await self._turn_resources((turn_id, *descendant_ids))
        output = replace(output, tokens=tokens, cost_micro_usd=cost_micro_usd)
        snapshot = trajectory_snapshot(
            conversation_id,
            turn_id,
            await self._turn_status(turn_id),
            messages,
        )
        snapshot_error = WAIT_EXPIRED if not snapshot.error else f"{WAIT_EXPIRED}; {snapshot.error}"
        if missing_child:
            snapshot_error = f"{snapshot_error}; {missing_child}"
        error_class, error_message = await self._turn_error(turn_id)
        if error_class:
            snapshot_error = f"{snapshot_error}; {error_class}: {error_message}"
        snapshot = snapshot.model_copy(update={"error": snapshot_error})
        return _Settled(
            TargetResult(
                output,
                False,
                WAIT_EXPIRED,
                error_class=error_class or None,
                trajectory=snapshot,
                error_message=error_message,
                expiry_status=self.outcome.cancelled_from(turn_id),
                work_started=any(step.started_at_epoch_ms is not None for step in steps),
            ),
            descendant_ids,
        )

    async def _turn_resources(self, turn_ids: tuple[UUID, ...]) -> tuple[int, int]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        sa.func.coalesce(
                            sa.func.sum(
                                sa.case(
                                    (
                                        tables.ledger.c.dimension == TOKEN_DIMENSION,
                                        tables.ledger.c.amount,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                        sa.func.coalesce(sa.func.sum(tables.ledger.c.priced_micro_usd), 0),
                    ).where(tables.ledger.c.turn_id.in_(list(turn_ids)))
                )
            ).one()
        return int(rows[0]), int(rows[1])

    async def _merge_descendants(
        self,
        turn_id: UUID,
        output: CapabilityOutput,
        wait_for_background: bool = False,
    ) -> tuple[CapabilityOutput, tuple[UUID, ...], str]:
        """Append every terminal child conversation's calls and tool errors to the scored output —
        a delegated capability (browser_task, wide_browse, spawn) proves itself by the
        raw calls its children actually dispatched, never by the wrapper's summary, and a child's
        errors keep web-infra exclusion truthful. Each conversation reads once (a message_spawn
        follow-up adds a turn to its child's conversation, not a transcript); a conversation with
        no terminal turn never informed the parent's answer and is skipped; a terminal one whose
        transcript never appears or does not decode is an infrastructure failure (third return),
        never a silently thinner trajectory. Also returns every descendant turn id, so artifact
        collection sees files a child shared."""
        if self.blob is None:
            return output, (), ""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.status,
                        tables.turn.c.terminal,
                        tables.turn.c.inbound,
                        tables.turn.c.result_delivery,
                    )
                    .where(tables.turn.c.parent_turn_id == turn_id)
                    .order_by(tables.turn.c.created_at, tables.turn.c.seq)
                )
            ).all()
        if wait_for_background:
            pending = tuple(
                row
                for row in rows
                if row.status not in TERMINAL_CHILD_STATUSES
                or row.result_delivery == DELIVERY_PENDING
            )
            if pending:
                background_trajectories = await asyncio.gather(
                    *(self.outcome.settle(row.conversation_id, row.id) for row in pending)
                )
                if any(trajectory is None for trajectory in background_trajectories):
                    return output, tuple(row.id for row in rows), "background child did not finish"
                async with workspace_tx() as connection:
                    rows = (
                        await connection.execute(
                            sa.select(
                                tables.turn.c.id,
                                tables.turn.c.conversation_id,
                                tables.turn.c.status,
                                tables.turn.c.terminal,
                                tables.turn.c.inbound,
                                tables.turn.c.result_delivery,
                            )
                            .where(tables.turn.c.parent_turn_id == turn_id)
                            .order_by(tables.turn.c.created_at, tables.turn.c.seq)
                        )
                    ).all()
                undelivered = tuple(row for row in rows if row.result_delivery == DELIVERY_PENDING)
                if undelivered:
                    return (
                        output,
                        tuple(row.id for row in rows),
                        "background child result did not deliver",
                    )
        conversations: dict[UUID, list[sa.Row]] = {}
        for row in rows:
            conversations.setdefault(row.conversation_id, []).append(row)
        calls = list(output.calls)
        errors = list(output.tool_errors)
        handoffs = list(output.handoffs)
        descendant_ids: list[UUID] = []
        for conversation_id, turns in conversations.items():
            descendant_ids.extend(turn.id for turn in turns)
            if not any(turn.status in TERMINAL_CHILD_STATUSES for turn in turns):
                continue
            settled = tuple(turn for turn in turns if turn.status in TERMINAL_CHILD_STATUSES)
            body = await self._await_child_transcript(conversation_id)
            if body is None:
                if all(turn.status != "done" for turn in settled):
                    handoffs.append(
                        handoff_record(conversation_id, (), _terminal_result(settled[-1].terminal))
                    )
                    continue
                failure = f"child turn {turns[0].id} is terminal but its transcript never appeared"
                return output, tuple(descendant_ids), failure
            try:
                decoded = decode(body)
            except TranscriptDecodeError:
                return (
                    output,
                    tuple(descendant_ids),
                    f"child conversation {conversation_id} has a corrupt transcript",
                )
            child_messages = decoded.messages
            if self.turn_steps is not None:
                windows = _turn_windows(decoded.messages, tuple(turn.inbound for turn in turns))
                recovered: dict[int, tuple[Message, ...]] = {}
                for turn, (start, end) in zip(turns, windows, strict=True):
                    if (
                        turn.status not in {"failed", "cancelled"}
                        or capability_output(decoded.messages[start:end]).calls
                    ):
                        continue
                    steps = await self.turn_steps.steps(turn.id)
                    recovered[end] = (
                        *recovered.get(end, ()),
                        *(message for step in steps for message in step.messages),
                    )
                if recovered:
                    rebuilt = list(recovered.get(0, ()))
                    for index, message in enumerate(decoded.messages):
                        rebuilt.append(message)
                        rebuilt.extend(recovered.get(index + 1, ()))
                    child_messages = tuple(rebuilt)
            child = capability_output(child_messages)
            handoffs.append(
                handoff_record(
                    conversation_id,
                    child_messages,
                    _terminal_result(settled[-1].terminal),
                    terminal_texts=tuple(
                        _terminal_text(turn.terminal) for turn in settled if turn.status == "done"
                    ),
                )
            )
            for turn in turns:
                child, sub_ids, failure = await self._merge_descendants(
                    turn.id, child, wait_for_background
                )
                descendant_ids.extend(sub_ids)
                if failure:
                    return output, tuple(descendant_ids), failure
            calls.extend(child.calls)
            errors.extend(child.tool_errors)
            handoffs.extend(child.handoffs)
        merged = replace(
            output, calls=tuple(calls), tool_errors=tuple(errors), handoffs=tuple(handoffs)
        )
        return merged, tuple(descendant_ids), ""

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

    async def _turn_status(self, turn_id: UUID) -> TurnStatus | None:
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one_or_none()
        return status

    def _turn_failure(self, status: TurnStatus | None) -> str:
        if status is None:
            return "turn row disappeared before grading"
        if status != "done":
            return f"turn ended with status {status}"
        return ""

    async def _turn_error(self, turn_id: UUID) -> tuple[str, str]:
        async with workspace_tx() as connection:
            terminal = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one_or_none()
        if terminal is None:
            return "", ""
        frame = TerminalFrame.model_validate(terminal)
        return frame.error_class or "", frame.error_message or ""

    async def _shared_artifacts(self, turn_ids: tuple[UUID, ...]) -> ArtifactCollection:
        """Artifacts the run shared or its replies carried, from the evaluated turn and every
        delegated descendant — a child's share_file records against the child turn, and its file
        must be as visible to a scorer as the call that shared it."""
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
                        tables.shared_artifact.c.role,
                    )
                    .where(tables.shared_artifact.c.turn_id.in_(list(turn_ids)))
                    .order_by(tables.shared_artifact.c.created_at)
                )
            ).all()
        if len(rows) > MAX_EVAL_ARTIFACTS:
            references, reference_error = await self._artifact_references(rows)
            return ArtifactCollection(
                references=references,
                error=reference_error or f"turn shared more than {MAX_EVAL_ARTIFACTS} artifacts",
            )
        references, reference_error = await self._artifact_references(rows)
        if reference_error:
            return ArtifactCollection(references=references, error=reference_error)
        oversized = next((row for row in rows if row.size_bytes > MAX_EVAL_ARTIFACT_BYTES), None)
        if oversized is not None:
            return ArtifactCollection(
                references=references,
                error=f"artifact {oversized.filename!r} exceeds {MAX_EVAL_ARTIFACT_BYTES} bytes",
            )
        if sum(row.size_bytes for row in rows) > MAX_EVAL_ARTIFACT_TOTAL_BYTES:
            return ArtifactCollection(
                references=references,
                error=f"shared artifacts exceed {MAX_EVAL_ARTIFACT_TOTAL_BYTES} total bytes",
            )
        artifacts: list[SharedArtifact] = []
        for row in rows:
            try:
                content = await blob.get(row.blob_key)
            except BlobNotFound:
                return ArtifactCollection(
                    references=references,
                    error=f"artifact {row.filename!r} is missing from storage",
                )
            if len(content) != row.size_bytes:
                return ArtifactCollection(
                    references=references,
                    error=f"artifact {row.filename!r} size does not match its row",
                )
            artifacts.append(SharedArtifact(row.filename, content, row.role))
        return ArtifactCollection(tuple(artifacts), references)

    async def _artifact_references(
        self, rows: Sequence[sa.Row]
    ) -> tuple[tuple[SharedArtifactReference, ...], str]:
        blob = self.blob
        if blob is None:
            raise RuntimeError("artifact collection requires a blob store")
        references: list[SharedArtifactReference] = []
        for row in rows:
            digest = sha256()
            size_bytes = 0
            try:
                async for chunk in blob.get_stream(row.blob_key):
                    digest.update(chunk)
                    size_bytes += len(chunk)
            except BlobNotFound:
                return tuple(references), f"artifact {row.filename!r} is missing from storage"
            if size_bytes != row.size_bytes:
                return (
                    tuple(references),
                    f"artifact {row.filename!r} size does not match its row",
                )
            references.append(
                SharedArtifactReference(
                    name=row.filename,
                    blob_key=row.blob_key,
                    digest=f"sha256:{digest.hexdigest()}",
                    size_bytes=size_bytes,
                )
            )
        return tuple(references), ""


def trajectory_snapshot(
    conversation_id: UUID,
    turn_id: UUID,
    status: TurnStatus | None,
    messages: tuple[Message, ...],
) -> EvalTrajectory:
    """The storable form of one turn's transcript: private handoffs and images redacted, oversized
    snapshots omitted with the omission named. The live target and the offline reconstruction both
    store trajectories only through this."""
    private_results, private_values = _private_handoffs(messages)
    snapshot = EvalTrajectory(
        conversation_id=conversation_id,
        turn_id=turn_id,
        status=status,
        messages=_safe_messages(messages, private_results, private_values),
    )
    if len(snapshot.model_dump_json().encode()) <= MAX_EVAL_TRAJECTORY_BYTES:
        return snapshot
    return EvalTrajectory(
        conversation_id=conversation_id,
        turn_id=turn_id,
        status=status,
        messages=(),
        error=(
            f"stored transcript snapshot exceeds {MAX_EVAL_TRAJECTORY_BYTES} bytes and was omitted"
        ),
    )


def rollover_snapshots(records: tuple[RolloverRecord, ...]) -> tuple[StoredRollover, ...]:
    """The storable form of a conversation's rollovers: each recovery record and window-count kept,
    each window redacted like a trajectory. Windows are dropped (recovery records kept) once their
    combined size crosses the budget, mirroring `trajectory_snapshot` — the same reason a large
    transcript is omitted. The live target and the offline reconstruction both store rollovers only
    through this."""
    snapshots: list[StoredRollover] = []
    for record in records:
        private_results, private_values = _private_handoffs((*record.before, *record.after))
        snapshots.append(
            StoredRollover(
                index=record.index,
                recovery=record.recovery,
                before_count=len(record.before),
                after_count=len(record.after),
                before=_safe_messages(record.before, private_results, private_values),
                after=_safe_messages(record.after, private_results, private_values),
            )
        )
    total_bytes = sum(len(snapshot.model_dump_json().encode()) for snapshot in snapshots)
    if total_bytes <= MAX_EVAL_ROLLOVER_BYTES:
        return tuple(snapshots)
    return tuple(
        StoredRollover(
            index=snapshot.index,
            recovery=snapshot.recovery,
            before_count=snapshot.before_count,
            after_count=snapshot.after_count,
            windows_omitted=True,
        )
        for snapshot in snapshots
    )


def _terminal_result(terminal: Json) -> str:
    """The payload a finished child handed its parent: the `result` its terminal text carried, or
    that text whole when it is not the typed shape a profile's output model produces."""
    text = _terminal_text(terminal)
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return text
    match decoded:
        case {"result": str() as result}:
            return result
        case _:
            return text


def _terminal_text(terminal: Json) -> str:
    if terminal is None:
        return ""
    return TerminalFrame.model_validate(terminal).text


def capability_output(messages: tuple[Message, ...]) -> CapabilityOutput:
    """Rebuild the grader-visible output from the transcript: the final answer (the last assistant
    text, without the file links the window keeps and the member reads as labels — the engine lands
    those as files beside the reply), the ordered tool calls (each tool_use joined to its
    tool_result by id), and the error text of any call that failed."""
    private_results, private_values = _private_handoffs(messages)
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
                result_text = (
                    PRIVATE_HANDOFF_REDACTED
                    if block.id in private_results and result is not None
                    else _redact_text(_result_text(result), private_values)
                )
                calls.append(
                    ToolInvocation(
                        block.name,
                        _redact_object(block.input, private_values),
                        result_text,
                        has_result=result is not None,
                        call_id=block.id,
                        is_error=result.is_error if result is not None else False,
                        activity=result.activity_text if result is not None else "",
                    )
                )
                if result is not None and result.is_error:
                    errors.append(result_text)
    _carried, answer = marked_artifacts(_final_answer(messages))
    return CapabilityOutput(_redact_text(answer, private_values), tuple(calls), tuple(errors))


def _private_handoffs(messages: tuple[Message, ...]) -> tuple[frozenset[str], tuple[str, ...]]:
    """The credential collection's `request_credentials` calls and the sealed values their results
    carry. The action dispatches through `object_action`, so the wire name says nothing and the
    kind and action in the call's input are what mark a handoff."""
    private_results = frozenset(
        block.id
        for message in messages
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolUseBlock)
        and block.name == OBJECT_ACTION_TOOL
        and (block.input.get("kind"), block.input.get("action")) == PRIVATE_HANDOFF_ACTION
    )
    values: list[str] = []
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            if not isinstance(block, ToolResultBlock) or block.tool_use_id not in private_results:
                continue
            _, _, payload = _result_text(block).partition("\n")
            try:
                request = CredentialRequest.model_validate_json(payload.split("\n", 1)[0])
                values.append(request.sealed)
            except ValueError:
                continue
    return private_results, tuple(values)


def _safe_messages(
    messages: tuple[Message, ...],
    private_results: frozenset[str],
    private_values: tuple[str, ...],
) -> tuple[Message, ...]:
    safe: list[Message] = []
    for message in messages:
        if isinstance(message.content, str):
            safe.append(
                Message(role=message.role, content=_redact_text(message.content, private_values))
            )
            continue
        blocks: list[TextBlock | ToolUseBlock | ToolResultBlock] = []
        for block in message.content:
            match block:
                case TextBlock():
                    blocks.append(TextBlock(text=_redact_text(block.text, private_values)))
                case ThinkingBlock():
                    blocks.append(
                        TextBlock(
                            text=REASONING_EVIDENCE.format(
                                summary=_redact_text(block.thinking, private_values)
                            )
                        )
                    )
                case RedactedThinkingBlock():
                    blocks.append(TextBlock(text=REDACTED_REASONING_EVIDENCE))
                case ReasoningItemBlock():
                    blocks.append(
                        TextBlock(
                            text=REASONING_EVIDENCE.format(
                                summary=_redact_text("\n".join(block.summary), private_values)
                            )
                        )
                    )
                case ImageBlock():
                    blocks.append(
                        TextBlock(
                            text=(
                                f"[image omitted: {block.source.media_type}, "
                                f"{len(block.source.data)} encoded characters]"
                            )
                        )
                    )
                case ToolUseBlock():
                    blocks.append(
                        ToolUseBlock(
                            id=block.id,
                            name=block.name,
                            input=_redact_object(block.input, private_values),
                        )
                    )
                case ToolResultBlock():
                    if block.tool_use_id in private_results:
                        content: str | tuple[TextBlock, ...] = PRIVATE_HANDOFF_REDACTED
                    elif isinstance(block.content, str):
                        content = _redact_text(block.content, private_values)
                    else:
                        content = tuple(
                            TextBlock(
                                text=(
                                    _redact_text(item.text, private_values)
                                    if isinstance(item, TextBlock)
                                    else (
                                        f"[image omitted: {item.source.media_type}, "
                                        f"{len(item.source.data)} encoded characters]"
                                    )
                                )
                            )
                            for item in block.content
                        )
                    blocks.append(
                        ToolResultBlock(
                            tool_use_id=block.tool_use_id,
                            content=content,
                            is_error=block.is_error,
                        )
                    )
        safe.append(Message(role=message.role, content=tuple(blocks)))
    return tuple(safe)


def _redact_text(text: str, private_values: tuple[str, ...]) -> str:
    for value in private_values:
        text = text.replace(value, PRIVATE_HANDOFF_REDACTED)
    return text


def _redact_json(value: Json, private_values: tuple[str, ...]) -> Json:
    match value:
        case str():
            return _redact_text(value, private_values)
        case list():
            return [_redact_json(item, private_values) for item in value]
        case dict():
            return _redact_object(value, private_values)
        case _:
            return value


def _redact_object(value: dict[str, Json], private_values: tuple[str, ...]) -> dict[str, Json]:
    return {
        name: (PRIVATE_HANDOFF_REDACTED if name == "sealed" else _redact_json(item, private_values))
        for name, item in value.items()
    }


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
