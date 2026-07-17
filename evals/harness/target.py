"""The in-process target: drive one case as a real turn through the agent, then reconstruct the
grader-visible output from the durable transcript.

`invoke` is the whole seam the scoped ExtensionContext exposes — it admits a turn and returns its
id; it neither opens a conversation nor reports when the turn is terminal. So the target is handed
two injected collaborators for what the scoped context cannot do: `conversations` opens a fresh
conversation per case, and `outcome` awaits the admitted turn's terminal transcript."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import sqlalchemy as sa

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    EvalTrajectory,
    SharedArtifact,
    SharedArtifactReference,
    ToolInvocation,
    TurnLog,
    WorkspaceFile,
)
from evals.harness.harness import Json
from evals.harness.judge import JudgeLeg
from ufo.blob import BlobNotFound, BlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.schema.records import CredentialRequest, TerminalFrame, TurnStatus
from ufo.sdk.context import ExtensionContext, Trajectory
from ufo.sdk.models import ImageBlock, Message, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.transcript import TranscriptDecodeError, decode, transcript_key

if TYPE_CHECKING:
    from evals.compaction.target import CompactionTarget
    from evals.mcp_atlas_100.target import McpAtlasTarget

MAX_EVAL_ARTIFACTS = 8
MAX_EVAL_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_EVAL_ARTIFACT_TOTAL_BYTES = 32 * 1024 * 1024
MAX_EVAL_TRAJECTORY_BYTES = 8 * 1024 * 1024
PRIVATE_HANDOFF_TOOL = "request_credentials"
PRIVATE_HANDOFF_REDACTED = "[private handoff redacted]"
TERMINAL_CHILD_STATUSES = frozenset({"done", "failed", "cancelled"})
CHILD_TRANSCRIPT_POLL_SECONDS = 0.2
CHILD_TRANSCRIPT_POLL_ATTEMPTS = 25
FIRST_COMPACTION_INDEX = 1
COMPACTION_SUMMARY_KEY_TEMPLATE = (
    "conversations/{conversation_id}/compactions/{index}/summary.json.lz4"
)


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


@dataclass(frozen=True)
class _Settled:
    """One settled turn's result plus every descendant turn id, which only `run`'s artifact
    collection consumes."""

    result: TargetResult
    descendant_ids: tuple[UUID, ...] = ()


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


class EvalConversations(Protocol):
    async def open(
        self,
        case_name: str,
        member_key: str | None = None,
        workspace_files: tuple[WorkspaceFile, ...] = (),
        prior_messages: tuple[str, ...] = (),
    ) -> UUID: ...

    async def stage(self, conversation_id: UUID, path: str, source: Path) -> None: ...


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
    simulator: JudgeLeg | None = None
    blob: BlobStore | None = None
    logs: TurnLogReader | None = None
    mcp_atlas: McpAtlasTarget | None = None
    compaction: CompactionTarget | None = None

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
        conversation_id = await self.conversations.open(
            case.name, case.member_key, case.workspace_files, case.prior_messages
        )
        for reference in case.references:
            await self.conversations.stage(
                conversation_id, f"references/{reference.path}", reference.source
            )
        try:
            turn_id = await self.ctx.invoke(
                conversation_id,
                self.agent_id,
                case.message,
                f"{case.name}:{conversation_id}",
            )
        except Exception as error:
            return _invoke_failure(conversation_id, error)
        settled = await self._settled(conversation_id, turn_id)
        result = settled.result
        if not result.clean:
            if self.logs is not None:
                await self.logs.discard(turn_id)
            return result
        output = result.output
        if self.logs is not None:
            log = await self.logs.read(turn_id)
            if log is None:
                raise RuntimeError("turn produced no required log")
            output = replace(output, log=log)
        if self.blob is not None:
            collected = await self._shared_artifacts((turn_id, *settled.descendant_ids))
            compactions = int(
                await self.blob.exists(
                    COMPACTION_SUMMARY_KEY_TEMPLATE.format(
                        conversation_id=conversation_id,
                        index=FIRST_COMPACTION_INDEX,
                    )
                )
            )
            output = replace(
                output,
                artifacts=collected.artifacts,
                artifact_references=collected.references,
                artifact_error=collected.error,
                compactions=compactions,
            )
        return replace(result, output=output)

    async def step(self, conversation_id: UUID, message: str, idempotency_key: str) -> TargetResult:
        """Drive one member turn on an existing conversation and reconstruct its result — the
        scenario runner's per-exchange seam. Log and artifact enrichment stay with `run`; a
        scenario grader reads durable state itself."""
        try:
            turn_id = await self.ctx.invoke(
                conversation_id, self.agent_id, message, idempotency_key
            )
        except Exception as error:
            return _invoke_failure(conversation_id, error)
        return (await self._settled(conversation_id, turn_id)).result

    async def _settled(self, conversation_id: UUID, turn_id: UUID) -> _Settled:
        trajectory = await self.outcome.settle(conversation_id, turn_id)
        if trajectory is None:
            tokens, cost_micro_usd = await self._turn_resources((turn_id,))
            return _Settled(
                TargetResult(
                    CapabilityOutput("", (), (), tokens=tokens, cost_micro_usd=cost_micro_usd),
                    False,
                    "turn produced no terminal transcript",
                    trajectory=EvalTrajectory(
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        status=await self._turn_status(turn_id),
                        messages=(),
                        error="turn produced no terminal transcript",
                    ),
                )
            )
        output = capability_output(trajectory.messages)
        status = await self._turn_status(turn_id)
        snapshot = trajectory_snapshot(conversation_id, turn_id, status, trajectory.messages)
        output, descendant_ids, missing_child = await self._merge_descendants(turn_id, output)
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
            error_class = await self._turn_error_class(turn_id)
            reason = f"{turn_failure} ({error_class})" if error_class else turn_failure
            return _Settled(
                TargetResult(
                    output,
                    clean=False,
                    failure_reason=reason,
                    error_class=error_class or None,
                    trajectory=snapshot,
                ),
                descendant_ids,
            )
        return _Settled(TargetResult(output, clean=True, trajectory=snapshot), descendant_ids)

    async def _turn_resources(self, turn_ids: tuple[UUID, ...]) -> tuple[int, int]:
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

    async def _merge_descendants(
        self, turn_id: UUID, output: CapabilityOutput
    ) -> tuple[CapabilityOutput, tuple[UUID, ...], str]:
        """Append every terminal child conversation's calls and tool errors to the scored output —
        a delegated capability (browser_task, wide_browse, spawn_subagent) proves itself by the
        raw calls its children actually dispatched, never by the wrapper's summary, and a child's
        errors keep web-infra exclusion truthful. Each conversation reads once (a message_subagent
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
                    )
                    .where(tables.turn.c.parent_turn_id == turn_id)
                    .order_by(tables.turn.c.created_at)
                )
            ).all()
        conversations: dict[UUID, list[sa.Row]] = {}
        for row in rows:
            conversations.setdefault(row.conversation_id, []).append(row)
        calls = list(output.calls)
        errors = list(output.tool_errors)
        descendant_ids: list[UUID] = []
        for conversation_id, turns in conversations.items():
            descendant_ids.extend(turn.id for turn in turns)
            if not any(turn.status in TERMINAL_CHILD_STATUSES for turn in turns):
                continue
            body = await self._await_child_transcript(conversation_id)
            if body is None:
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
            child = capability_output(decoded.messages)
            for turn in turns:
                child, sub_ids, failure = await self._merge_descendants(turn.id, child)
                descendant_ids.extend(sub_ids)
                if failure:
                    return output, tuple(descendant_ids), failure
            calls.extend(child.calls)
            errors.extend(child.tool_errors)
        merged = replace(output, calls=tuple(calls), tool_errors=tuple(errors))
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

    async def _turn_error_class(self, turn_id: UUID) -> str:
        """The model/transport error class a failed turn recorded on its terminal frame (e.g.
        `ReadTimeout`), so a caller can tell a transient provider fault from a real one — empty when
        the terminal carries no error."""
        async with workspace_tx() as connection:
            terminal = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one_or_none()
        if terminal is None:
            return ""
        return TerminalFrame.model_validate(terminal).error_class or ""

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
            artifacts.append(SharedArtifact(row.filename, content))
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


def capability_output(messages: tuple[Message, ...]) -> CapabilityOutput:
    """Rebuild the grader-visible output from the transcript: the final answer (the last assistant
    text), the ordered tool calls (each tool_use joined to its tool_result by id), and the error
    text of any call that failed."""
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
                        is_error=result.is_error if result is not None else False,
                    )
                )
                if result is not None and result.is_error:
                    errors.append(result_text)
    return CapabilityOutput(
        _redact_text(_final_answer(messages), private_values), tuple(calls), tuple(errors)
    )


def _private_handoffs(messages: tuple[Message, ...]) -> tuple[frozenset[str], tuple[str, ...]]:
    private_results = frozenset(
        block.id
        for message in messages
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolUseBlock) and block.name == PRIVATE_HANDOFF_TOOL
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
