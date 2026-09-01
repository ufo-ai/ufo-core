"""One capability case: a message the live agent answers, a deterministic grader over its answer
and tool trajectory, and an optional semantic rubric. Deterministic checks run first; a passing
rubric case then reaches the target's model judge. A case runs against a `Target` that drives a real
turn and reconstructs the answer + tool calls from the durable transcript. Transcript tool results
retain text, completion, and error state; the agent's configured tool set remains fixed per run."""

from __future__ import annotations

import json
import re
from base64 import b64encode
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from hashlib import sha256
from inspect import getmodule, getsource, isfunction, ismethod
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.handoff import SubagentHandoff
from evals.harness.harness import (
    WAIT_EXPIRED,
    EvalCaseResult,
    Json,
    JsonObject,
    infra_error,
    infra_owned_fault,
    is_transient_fault,
)
from evals.harness.judge import (
    JUDGE_REVISION,
    MAX_ANSWER_CHARS,
    CriterionVerdict,
    RubricVerdict,
    rubric_pass,
    visual_rubric_pass,
)
from evals.harness.timing import CaseTiming
from ufo.blob import WorkspaceBlobStore
from ufo.runtime.tools.registry import OBJECT_ACTION_TOOL
from ufo.runtime.turns.transcript import CompactionSummary
from ufo.schema.records import TurnStatus
from ufo.sdk.models import ImageBlock, ImageSource, Message

PAGE_IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
PAGE_IMAGE_SUFFIXES = tuple(PAGE_IMAGE_MEDIA_TYPES)
ARTIFACT_MEDIA_TYPES = {
    **PAGE_IMAGE_MEDIA_TYPES,
    ".html": "text/html",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ARTIFACT_MEDIA_DEFAULT = "application/octet-stream"
MAX_ARTIFACT_PAYLOAD_BYTES = 12 * 1024 * 1024
ARTIFACT_JOIN = "\n\n"
ARTIFACT_CUT = "\n\n[shared Markdown cut to fit the judge's answer budget]"

if TYPE_CHECKING:
    from evals.harness.target import CapabilityTarget, TargetResult


@dataclass(frozen=True)
class CapabilityVerdict:
    """`excluded` marks a sample the harness could not put a capability question to — its
    environment was not the one the case describes. Neither pass nor fail: counting it as a failure
    charges the model for the harness, so it leaves the case's denominator instead."""

    passed: bool
    reason: str
    evidence: JsonObject = field(default_factory=dict)
    excluded: bool = False


@dataclass(frozen=True)
class ToolInvocation:
    """One tool call and the completion state reconstructed from its transcript result."""

    name: str
    input: JsonObject
    result: str = ""
    has_result: bool = False
    is_error: bool = False
    call_id: str = ""
    activity: str = ""

    @property
    def succeeded(self) -> bool:
        return self.has_result and not self.is_error

    @property
    def call(self) -> str:
        """What the call did, in the engine's own vocabulary: the canonical `action:<kind>:<name>`
        id an `object_action` dispatched, the wire name for every other tool — the name a
        trajectory grades once a capability lives on an object."""
        if self.name != OBJECT_ACTION_TOOL:
            return self.name
        return f"action:{self.input.get('kind', '')}:{self.input.get('action', '')}"

    @property
    def arguments(self) -> JsonObject:
        """The arguments the handler validated: an `object_action`'s own `input` mapping, the
        whole input for every other tool."""
        if self.name != OBJECT_ACTION_TOOL:
            return self.input
        nested = self.input.get("input", {})
        return nested if isinstance(nested, dict) else {}


def merge_tool_calls(
    before: tuple[ToolInvocation, ...], after: tuple[ToolInvocation, ...]
) -> tuple[ToolInvocation, ...]:
    """Merge a later transcript without repeating calls already present by id."""
    known = frozenset(call.call_id for call in before if call.call_id)
    return (*before, *(call for call in after if not call.call_id or call.call_id not in known))


def shared_file_names(call: ToolInvocation) -> tuple[str, ...]:
    """The download names one successful `share_file` call delivered, in share order, read from
    its result payload — one entry per file the call's list named."""
    if call.name != "share_file" or not call.succeeded:
        return ()
    try:
        payload = json.loads(call.result)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(
        entry["name"]
        for entry in payload
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    )


@dataclass(frozen=True)
class SharedArtifact:
    """One artifact durably attached to the evaluated turn."""

    name: str
    content: bytes


@dataclass(frozen=True)
class SharedArtifactReference:
    """Durable identity of one artifact attached to the evaluated turn."""

    name: str
    blob_key: str
    digest: str
    size_bytes: int


@dataclass(frozen=True)
class ProbeCommandResult:
    """One bounded command the harness ran in the evaluated conversation's sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out_after_s: int | None = None


class WorkspaceProbe(Protocol):
    """Run grader-owned commands in the evaluated conversation's sandbox."""

    async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult: ...


@dataclass(frozen=True)
class ArtifactProbeResult:
    """Artifacts and a terminal inspection error produced after an evaluated turn."""

    artifacts: tuple[SharedArtifact, ...] = ()
    error: str = ""
    max_payload_bytes: int = MAX_ARTIFACT_PAYLOAD_BYTES


@dataclass(frozen=True)
class CapabilityReference:
    """One upstream reference staged under the evaluated conversation's `references/` folder."""

    path: str
    source: Path
    digest: str
    size_bytes: int

    def __post_init__(self) -> None:
        parts = PurePosixPath(self.path).parts
        if not parts or self.path.startswith("/") or ".." in parts:
            raise ValueError(f"capability reference path is unsafe: {self.path!r}")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.digest) is None:
            raise ValueError("capability reference digest must be a SHA-256")
        if self.size_bytes < 0:
            raise ValueError("capability reference size must not be negative")


class TurnLog(BaseModel):
    """One allowlisted structured log exported by an evaluated turn."""

    event: str = Field(min_length=1)
    turn_id: UUID
    attributes: JsonObject


class EvalTrajectory(BaseModel):
    model_config = ConfigDict(frozen=True)

    conversation_id: UUID
    turn_id: UUID | None
    status: TurnStatus | None
    messages: tuple[Message, ...]
    error: str = ""


class StoredCompaction(BaseModel):
    """One compaction the evaluated turn performed, in its archive form: the typed summary the head
    was compressed into, the count of messages on each side of the boundary, and the redacted
    before/after windows. `windows_omitted` marks a record whose windows were dropped to keep the
    archive bounded — the summary and counts always survive."""

    model_config = ConfigDict(frozen=True)

    index: int
    summary: CompactionSummary
    before_count: int
    after_count: int
    before: tuple[Message, ...] = ()
    after: tuple[Message, ...] = ()
    windows_omitted: bool = False


@dataclass(frozen=True)
class CapabilityOutput:
    """The answer, tool trajectory, artifacts, and allowlisted log visible to a grader.
    `workspace_dir` is the host directory the turn's `/workspace` was served from, so a grader can
    read what the turn left on disk rather than only what it submitted."""

    response: str
    calls: tuple[ToolInvocation, ...]
    tool_errors: tuple[str, ...] = ()
    artifacts: tuple[SharedArtifact, ...] = ()
    artifact_references: tuple[SharedArtifactReference, ...] = ()
    artifact_error: str = ""
    log: TurnLog | None = None
    compactions: int = 0
    compaction_records: tuple[StoredCompaction, ...] = ()
    tokens: int = 0
    cost_micro_usd: int = 0
    workspace_dir: Path | None = None
    own_tools: tuple[str, ...] = ()
    own_calls: tuple[ToolInvocation, ...] = ()
    timing: CaseTiming | None = None
    handoffs: tuple[SubagentHandoff, ...] = ()

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(call.call for call in self.calls)


type Grader = Callable[[CapabilityOutput], Awaitable[CapabilityVerdict]]
type CapabilityFollowup = Callable[[CapabilityOutput], Awaitable[str | None]]
type ArtifactProbe = Callable[[CapabilityOutput, WorkspaceProbe], Awaitable[ArtifactProbeResult]]
type EvalSeed = Callable[[UUID, UUID], Awaitable[None]]
type CapabilitySeed = Callable[[UUID, UUID, WorkspaceBlobStore], Awaitable[None]]
type WorkspacePrepare = Callable[[UUID, Path], Awaitable[None]]


def source_digest(hook: Callable[..., object]) -> str:
    """A case hook's identity: its own source plus its defining module's, so editing the hook — or a
    helper the module's hooks share — moves the suite digest by itself. A hook carrying state is a
    callable object rather than a function, and its source is its class's."""
    target = hook if isfunction(hook) or ismethod(hook) else type(hook)
    module = getmodule(target)
    if module is None:
        raise RuntimeError(f"case hook {hook!r} has no source module to digest")
    return sha256(f"{getsource(target)}\n{getsource(module)}".encode()).hexdigest()


@runtime_checkable
class Graded(Protocol):
    """A grader that states its criteria; suite grader classes satisfy this structurally."""

    @property
    def grading(self) -> str: ...


@dataclass(frozen=True)
class DescribedGrader[OutputT]:
    """A grader carrying the human-readable statement of what it requires. The statement is
    recorded into case evidence, so the archive shows the criteria a verdict answered to; grader
    factories derive it from the same arguments that drive the checks, so it cannot drift."""

    grading: str
    grade: Callable[[OutputT], Awaitable[CapabilityVerdict]]

    async def __call__(self, output: OutputT) -> CapabilityVerdict:
        return await self.grade(output)


def grading_statement(grader: object) -> str:
    """The grader's criteria statement, or "" for an undescribed grader."""
    match grader:
        case Graded():
            return grader.grading
        case _:
            return ""


@dataclass(frozen=True)
class WorkspaceFile:
    path: str
    content: bytes


@dataclass(frozen=True)
class UndeliveredRound:
    """One round the agent had already run when the member's message arrived: the prose it wrote,
    the tool it called, and the result or error that came back. Seeded ahead of the case message so
    the live turn opens where a real one does once a member writes into a working turn — the
    agent's own narration behind it, streamed to a tailing surface and delivered to nobody. A case
    that seeds one is asking what the closing message does with content only the model can see."""

    narration: str
    tool: str
    input: JsonObject
    result: str
    is_error: bool = False


@dataclass(frozen=True)
class CapabilitySample:
    output: CapabilityOutput
    verdict: CapabilityVerdict
    trajectory: EvalTrajectory | None
    judge: tuple[CriterionVerdict, ...] = ()


@dataclass(frozen=True)
class CapabilityCase:
    """A message and its deterministic and semantic criteria. `web_dependent` infra-excludes an
    external outage; `samples` re-runs the case and passes if any sample passes; `digest_tag`
    stabilizes the suite digest. `member_key`, when set, is the exact email of the workspace member
    whose private memory the eval conversation may recall. `followup`, when set, derives each next
    inbound from the current output and durable state; returning None ends the conversation, and
    `followup_turns` bounds the admitted followup turns.
    `rubric` judges the answer text — with `answer_spans_artifacts`, the Markdown the turn shared
    joins that answer, for a case whose reply is expected to carry its detail in a shared file
    rather than inline; `artifact_rubric` judges the Markdown files the turn shared, or, when
    `written_report` names a workspace glob, the Markdown the turn wrote there without sharing;
    `visual_rubric` judges its rendered page images. Each requires a judge model on the task and
    runs after the deterministic grader passes, or after a failure when
    `judge_on_deterministic_failure` is set. `seed`, when set, receives
    (workspace_id, agent_id, blob) before the case's conversation opens and establishes the state
    the case runs against, resetting whatever it owns. `prepare`, when set, runs once the
    conversation's workspace directory exists and its files are staged, and before the turn opens,
    receiving (workspace_id, that directory) — for a case whose external environment must read the
    very files the agent will write, which `seed` runs too early to know. `cleanup`, when set,
    receives the same arguments as `seed` once the case's turn has settled or failed to start, and
    removes the state `seed` established so no later case runs against it. `undelivered` seeds the
    agent's rounds from before the case message, so they answer the last of the `prior_messages`.
    `artifact_probe`, when set, runs after the clean turn in the same sandbox and adds grader-only
    artifacts without adding instructions or another turn to the evaluated trajectory."""

    name: str
    message: str
    grader: Grader
    samples: int = 1
    web_dependent: bool = False
    digest_tag: str = ""
    rubric: tuple[str, ...] = ()
    artifact_rubric: tuple[str, ...] = ()
    written_report: str = ""
    answer_spans_artifacts: bool = False
    visual_rubric: tuple[str, ...] = ()
    member_key: str | None = None
    shared_audience: bool = False
    workspace_files: tuple[WorkspaceFile, ...] = ()
    prior_messages: tuple[str, ...] = ()
    undelivered: tuple[UndeliveredRound, ...] = ()
    references: tuple[CapabilityReference, ...] = ()
    followup: CapabilityFollowup | None = None
    followup_turns: int = 1
    seed: CapabilitySeed | None = None
    prepare: WorkspacePrepare | None = None
    cleanup: CapabilitySeed | None = None
    artifact_probe: ArtifactProbe | None = None
    followup_artifact_probe: ArtifactProbe | None = None
    judge_on_deterministic_failure: bool = False
    wait_for_background: bool = False

    def __post_init__(self) -> None:
        paths = tuple(reference.path for reference in self.references)
        if len(paths) != len(set(paths)):
            raise ValueError("capability reference paths must be unique")
        if self.undelivered and len(self.prior_messages) % 2 == 0:
            raise ValueError(
                "an undelivered round answers a member message: prior_messages must end on one"
            )
        if self.followup_artifact_probe is not None and self.followup is None:
            raise ValueError("a followup artifact probe requires a followup")
        if self.followup_turns < 1:
            raise ValueError("followup_turns must be at least 1")

    def payload(self) -> JsonObject:
        payload: JsonObject = {
            "name": self.name,
            "message": self.message,
            "samples": self.samples,
            "webDependent": self.web_dependent,
            "grader": self.digest_tag or self.name,
            "rubric": list(self.rubric),
        }
        if self.visual_rubric:
            payload["visualRubric"] = list(self.visual_rubric)
        if self.wait_for_background:
            payload["waitForBackground"] = True
        if self.judge_on_deterministic_failure:
            payload["judgeOnDeterministicFailure"] = True
        if self.artifact_rubric:
            payload["artifactRubric"] = list(self.artifact_rubric)
        if self.written_report:
            payload["writtenReport"] = self.written_report
        if self.answer_spans_artifacts:
            payload["answerSpansArtifacts"] = True
        if self.rubric or self.artifact_rubric or self.visual_rubric:
            payload["judgeRevision"] = JUDGE_REVISION
        if self.member_key is not None:
            payload["memberKey"] = self.member_key
        if self.shared_audience:
            payload["sharedAudience"] = True
        if self.workspace_files:
            payload["workspaceFiles"] = [
                {
                    "path": item.path,
                    "sha256": sha256(item.content).hexdigest(),
                }
                for item in self.workspace_files
            ]
        if self.prior_messages:
            payload["priorMessages"] = [
                sha256(message.encode()).hexdigest() for message in self.prior_messages
            ]
        if self.undelivered:
            payload["undelivered"] = [
                {
                    "narration": sha256(round.narration.encode()).hexdigest(),
                    "tool": round.tool,
                    "input": round.input,
                    "result": sha256(round.result.encode()).hexdigest(),
                    "isError": round.is_error,
                }
                for round in self.undelivered
            ]
        if self.references:
            payload["references"] = [
                {
                    "path": reference.path,
                    "digest": reference.digest,
                    "sizeBytes": reference.size_bytes,
                }
                for reference in self.references
            ]
        if self.followup is not None:
            payload["followup"] = source_digest(self.followup)
            payload["followupTurns"] = self.followup_turns
        if self.seed is not None:
            payload["seed"] = source_digest(self.seed)
        if self.prepare is not None:
            payload["prepare"] = source_digest(self.prepare)
        if self.artifact_probe is not None:
            payload["artifactProbe"] = source_digest(self.artifact_probe)
        if self.followup_artifact_probe is not None:
            payload["followupArtifactProbe"] = source_digest(self.followup_artifact_probe)
        return payload


@runtime_checkable
class CapabilityCleanupTarget(Protocol):
    async def cleanup(self, case: CapabilityCase) -> None:
        """Clean one case after its grader has read the state the turn produced."""
        ...


async def run_capability_case(case: CapabilityCase, target: CapabilityTarget) -> EvalCaseResult:
    """Run the case's samples and fold them into one result. An excluded sample leaves the
    denominator, so a `samples=3` case whose third turn died on the provider's transport is scored
    on the two that ran — the case itself is excluded only when no sample survived, which is the
    call `run_scenario_case` makes on trials."""
    samples = [await sample_capability(case, target) for _ in range(max(case.samples, 1))]
    scored_indexes = [index for index, sample in enumerate(samples) if not sample.verdict.excluded]
    excluded_samples = len(samples) - len(scored_indexes)
    winning_indexes = [index for index in scored_indexes if samples[index].verdict.passed]
    if winning_indexes:
        selected_index = winning_indexes[0]
    elif scored_indexes:
        selected_index = scored_indexes[-1]
    else:
        selected_index = len(samples) - 1
    verdict = samples[selected_index].verdict
    attempts: list[Json] = []
    for sample in samples:
        sample_output = sample.output
        sample_verdict = sample.verdict
        calls: list[Json] = []
        for call in sample_output.calls:
            calls.append(
                {
                    "name": call.name,
                    "callId": call.call_id,
                    "input": call.input,
                    "result": call.result,
                    "hasResult": call.has_result,
                    "isError": call.is_error,
                }
            )
        attempts.append(
            {
                "passed": sample_verdict.passed,
                "reason": sample_verdict.reason,
                "response": sample_output.response,
                "calls": calls,
                "toolErrors": list(sample_output.tool_errors),
                "artifacts": [artifact.name for artifact in sample_output.artifacts],
                "artifactContents": linked_artifacts(
                    sample_output.artifacts, sample_output.artifact_references
                ),
                "artifactReferences": [
                    {
                        "name": artifact.name,
                        "blobKey": artifact.blob_key,
                        "digest": artifact.digest,
                        "sizeBytes": artifact.size_bytes,
                    }
                    for artifact in sample_output.artifact_references
                ],
                "artifactError": sample_output.artifact_error or None,
                "tokens": sample_output.tokens,
                "costMicroUsd": sample_output.cost_micro_usd,
                "ownTools": list(sample_output.own_tools),
                "handoffs": [handoff.model_dump(mode="json") for handoff in sample_output.handoffs]
                or None,
                "timing": (
                    None
                    if sample_output.timing is None
                    else sample_output.timing.model_dump(mode="json")
                ),
                "log": (
                    None if sample_output.log is None else sample_output.log.model_dump(mode="json")
                ),
                "compactions": sample_output.compactions,
                "compactionRecords": (
                    [record.model_dump(mode="json") for record in sample_output.compaction_records]
                    or None
                ),
                "grader": sample_verdict.evidence or None,
                "judge": (
                    [
                        {"criterion": item.criterion, "passed": item.passed, "reason": item.reason}
                        for item in sample.judge
                    ]
                    if sample.judge
                    else None
                ),
                "trajectory": (
                    None if sample.trajectory is None else sample.trajectory.model_dump(mode="json")
                ),
            }
        )
    evidence: JsonObject = {
        "message": case.message,
        "grading": grading_statement(case.grader) or None,
        "rubric": list(case.rubric),
        "artifactRubric": list(case.artifact_rubric),
        "visualRubric": list(case.visual_rubric),
        "memberKey": case.member_key,
        "webDependent": case.web_dependent,
        "selectedAttempt": selected_index,
        "excludedSamples": excluded_samples,
        "attempts": attempts,
    }
    if not winning_indexes and case.web_dependent:
        broke = infra_error(
            tuple(error for sample in samples for error in sample.output.tool_errors)
        )
        if broke:
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=f"infra-excluded (web unavailable): {broke[:120]}",
                evidence=evidence,
                excluded=True,
            )
    if not scored_indexes:
        return EvalCaseResult(
            name=case.name,
            passed=False,
            reason=verdict.reason,
            evidence=evidence,
            excluded=True,
        )
    passed = bool(winning_indexes)
    note = f" ({excluded_samples} infra-excluded)" if excluded_samples else ""
    reason = (
        verdict.reason
        if passed
        else f"{len(winning_indexes)}/{len(scored_indexes)} samples passed{note}: {verdict.reason}"
    )
    return EvalCaseResult(name=case.name, passed=passed, reason=reason, evidence=evidence)


def _unclean_verdict(
    result: TargetResult, current_output: CapabilityOutput | None = None
) -> CapabilityVerdict:
    """The verdict for a turn that never reached a grader. A provider fault, rejected eval
    credential, or harness wait with no model output is excluded rather than scored. A wait that
    expires after a model response or tool call is model behavior, as is every other unclean end.
    Exclusion reaches only turns that never put the capability question to the model, so a graded
    answer, refusal, failed rubric, and incomplete agent loop remain scored."""
    status = result.trajectory.status if result.trajectory is not None else None
    provider_configuration = result.error_class == "APIError" and bool(
        infra_error((result.error_message,))
    )
    local_client_execution = (
        result.error_class == "RuntimeError"
        and "/tmp/ufo-local/bin/ufo" in result.error_message
        and "cannot execute binary file" in result.error_message.casefold()
    )
    output = current_output or result.output
    expired_after_model_output = result.failure_reason == WAIT_EXPIRED and bool(
        output.response or output.own_calls or (current_output is not None and output.calls)
    )
    if (
        not provider_configuration
        and not local_client_execution
        and (
            expired_after_model_output
            or not infra_owned_fault(result.error_class, result.failure_reason, status)
        )
    ):
        return CapabilityVerdict(False, result.failure_reason)
    if is_transient_fault(result.error_class):
        owner = "the provider owns this fault"
    elif result.error_class == "CredentialValueInvalid" or provider_configuration:
        owner = "the eval configuration owns this fault"
    elif local_client_execution:
        owner = "the eval runner owns this fault"
    else:
        owner = "the harness's own wait expired on a working turn"
    return CapabilityVerdict(False, f"{result.failure_reason}; {owner}", excluded=True)


async def sample_capability(case: CapabilityCase, target: CapabilityTarget) -> CapabilitySample:
    if case.cleanup is None:
        return await _sample_capability(case, target)
    if not isinstance(target, CapabilityCleanupTarget):
        raise RuntimeError("a capability case with cleanup requires a cleanup target")
    try:
        return await _sample_capability(case, target)
    finally:
        await target.cleanup(case)


async def _sample_capability(case: CapabilityCase, target: CapabilityTarget) -> CapabilitySample:
    result = await target.run(case)
    if not result.clean:
        return CapabilitySample(result.output, _unclean_verdict(result), result.trajectory)
    if case.followup is not None:
        trajectory = result.trajectory
        first_output = result.output
        for index in range(case.followup_turns):
            message = await case.followup(result.output)
            if message is None:
                break
            if trajectory is None:
                return CapabilitySample(
                    result.output,
                    CapabilityVerdict(False, "followup requires the first turn trajectory"),
                    None,
                )
            prior_output = result.output
            result = await target.step(
                trajectory.conversation_id,
                message,
                (
                    f"{case.name}:followup:{trajectory.conversation_id}"
                    if case.followup_turns == 1
                    else f"{case.name}:followup:{index}:{trajectory.conversation_id}"
                ),
            )
            step_output = result.output
            output = replace(step_output, workspace_dir=first_output.workspace_dir)
            followup_probe_failed = False
            if case.followup_artifact_probe is not None:
                probed = await target.capture_artifacts(
                    trajectory.conversation_id,
                    output,
                    case.followup_artifact_probe,
                )
                followup_probe_failed = probed.artifact_error != output.artifact_error
                output = probed
            followup_owns_artifact_evidence = bool(
                output.artifacts or output.artifact_references or followup_probe_failed
            )
            final_artifacts = (
                output.artifacts if followup_owns_artifact_evidence else prior_output.artifacts
            )
            final_artifact_references = (
                output.artifact_references
                if followup_owns_artifact_evidence
                else prior_output.artifact_references
            )
            _offline_artifacts(final_artifacts, final_artifact_references)
            result = replace(
                result,
                output=replace(
                    output,
                    tokens=prior_output.tokens + step_output.tokens,
                    cost_micro_usd=prior_output.cost_micro_usd + step_output.cost_micro_usd,
                    calls=merge_tool_calls(prior_output.calls, output.calls),
                    tool_errors=(
                        output.tool_errors
                        if frozenset(call.call_id for call in prior_output.calls if call.call_id)
                        & frozenset(call.call_id for call in output.calls if call.call_id)
                        else (*prior_output.tool_errors, *output.tool_errors)
                    ),
                    artifacts=final_artifacts,
                    artifact_references=final_artifact_references,
                    artifact_error="; ".join(
                        error
                        for error in (prior_output.artifact_error, output.artifact_error)
                        if error
                    ),
                    log=first_output.log,
                    compactions=first_output.compactions,
                    compaction_records=first_output.compaction_records,
                    timing=first_output.timing,
                    handoffs=prior_output.handoffs + step_output.handoffs,
                    own_calls=merge_tool_calls(prior_output.own_calls, output.own_calls),
                ),
            )
            if not result.clean:
                return CapabilitySample(
                    result.output,
                    _unclean_verdict(result, step_output),
                    result.trajectory,
                )
    deterministic = await case.grader(result.output)
    has_semantic_rubric = bool(case.rubric or case.artifact_rubric or case.visual_rubric)
    if not has_semantic_rubric or (
        not deterministic.passed and not case.judge_on_deterministic_failure
    ):
        return CapabilitySample(result.output, deterministic, result.trajectory)
    if target.judge is None:
        return CapabilitySample(
            result.output,
            CapabilityVerdict(False, "semantic rubric requires a model judge"),
            result.trajectory,
        )
    verdicts: list[RubricVerdict] = []
    if case.rubric:
        answer = result.output.response
        if case.answer_spans_artifacts:
            answer = _answer_spanning_artifacts(answer, result.output.artifacts)
        verdicts.append(await rubric_pass(case.message, answer, case.rubric, target.judge))
    if case.artifact_rubric:
        if case.written_report:
            markdown = written_markdown(result.output, case.written_report)
            missing = f"no written Markdown report matching {case.written_report} to judge"
        else:
            markdown = tuple(
                artifact
                for artifact in result.output.artifacts
                if artifact.name.lower().endswith(".md")
            )
            missing = "no shared Markdown artifact to judge"
        if not markdown:
            verdicts.append(RubricVerdict(False, missing))
        else:
            try:
                answer = "\n\n".join(
                    f"# {artifact.name}\n\n{artifact.content.decode()}" for artifact in markdown
                )
            except UnicodeDecodeError:
                verdicts.append(RubricVerdict(False, "the Markdown report is not UTF-8"))
            else:
                verdicts.append(
                    await rubric_pass(case.message, answer, case.artifact_rubric, target.judge)
                )
    if case.visual_rubric:
        pages = _page_images(result.output.artifacts)
        verdicts.append(
            await visual_rubric_pass(case.message, pages, case.visual_rubric, target.judge)
        )
    reason = "; ".join((deterministic.reason, *(verdict.reason for verdict in verdicts)))
    return CapabilitySample(
        result.output,
        CapabilityVerdict(
            deterministic.passed and all(verdict.passed for verdict in verdicts),
            reason,
            deterministic.evidence,
        ),
        result.trajectory,
        judge=tuple(criterion for verdict in verdicts for criterion in verdict.criteria),
    )


def written_markdown(output: CapabilityOutput, pattern: str) -> tuple[SharedArtifact, ...]:
    """The Markdown the turn wrote into its own workspace, whether or not it shared it. A case whose
    report is written for the member to ask for has no shared artifact to judge, so the file on disk
    is the record, and the deterministic grader and the judge read the same bytes."""
    if output.workspace_dir is None:
        return ()
    return tuple(
        SharedArtifact(path.name, path.read_bytes())
        for path in sorted(output.workspace_dir.glob(pattern))
        if path.is_file()
    )


def _answer_spanning_artifacts(response: str, artifacts: tuple[SharedArtifact, ...]) -> str:
    """The reply followed by the Markdown it defers to, bounded to the judge's answer budget. The
    reply is never displaced and the files fill what remains, cut at the boundary under a marker the
    judge can see — an agent that writes a thorough deliverable must not fail on the size of the
    file it shared rather than on what it answered."""
    body = "\n\n".join(_markdown_artifacts(artifacts))
    if not body:
        return response
    room = MAX_ANSWER_CHARS - len(response) - len(ARTIFACT_JOIN)
    if room <= len(ARTIFACT_CUT):
        return response
    if len(body) > room:
        body = body[: room - len(ARTIFACT_CUT)] + ARTIFACT_CUT
    return response + ARTIFACT_JOIN + body


def _markdown_artifacts(artifacts: tuple[SharedArtifact, ...]) -> tuple[str, ...]:
    """The turn's shared Markdown, each headed by its filename, for a rubric whose answer spans the
    reply and the files it references. A file that is not UTF-8 carries no readable answer, so it is
    left out and the rubric it was meant to satisfy goes unmet."""
    readable: list[str] = []
    for artifact in artifacts:
        if not artifact.name.lower().endswith(".md"):
            continue
        try:
            readable.append(f"# {artifact.name}\n\n{artifact.content.decode()}")
        except UnicodeDecodeError:
            continue
    return tuple(readable)


def linked_artifacts(
    artifacts: tuple[SharedArtifact, ...],
    references: tuple[SharedArtifactReference, ...],
) -> list[Json]:
    """Each shared artifact that fits the offline report payload budget as a data URI."""
    linked: list[Json] = []
    for artifact in _offline_artifacts(artifacts, references):
        media_type = ARTIFACT_MEDIA_TYPES.get(
            PurePosixPath(artifact.name).suffix.lower(), ARTIFACT_MEDIA_DEFAULT
        )
        linked.append(
            {
                "name": artifact.name,
                "mediaType": media_type,
                "dataUri": f"data:{media_type};base64,{b64encode(artifact.content).decode()}",
            }
        )
    return linked


def _offline_artifacts(
    artifacts: tuple[SharedArtifact, ...],
    references: tuple[SharedArtifactReference, ...],
) -> tuple[SharedArtifact, ...]:
    durable = {(reference.name, reference.digest, reference.size_bytes) for reference in references}
    retained: list[SharedArtifact] = []
    total = 0
    for artifact in artifacts:
        size = len(artifact.content)
        digest = f"sha256:{sha256(artifact.content).hexdigest()}"
        identity = (artifact.name, digest, size)
        if size > MAX_ARTIFACT_PAYLOAD_BYTES:
            if identity in durable:
                continue
            raise ValueError(
                f"artifact {artifact.name!r} ({digest}, {size} bytes) has no durable reference "
                f"and exceeds the {MAX_ARTIFACT_PAYLOAD_BYTES}-byte offline payload limit"
            )
        if total + size > MAX_ARTIFACT_PAYLOAD_BYTES:
            if identity in durable:
                continue
            raise ValueError(
                f"artifact {artifact.name!r} ({digest}, {size} bytes) has no durable reference "
                f"and exceeds the {MAX_ARTIFACT_PAYLOAD_BYTES}-byte cumulative offline payload "
                "limit"
            )
        total += size
        retained.append(artifact)
    return tuple(retained)


def _page_images(artifacts: tuple[SharedArtifact, ...]) -> tuple[ImageBlock, ...]:
    """Every shared rendered page image, ordered by name, as base64 image blocks. A visual case
    that shared no page image, more than the page budget, or more image bytes than the request
    allows yields them all unchanged — `_visual_boundary_error` then decides, failing the case loud
    rather than this silently judging an empty deck, an arbitrary truncated subset, or a set the
    provider's `trim_images` would drop oldest-first."""
    pages = sorted(
        (artifact for artifact in artifacts if artifact.name.lower().endswith(PAGE_IMAGE_SUFFIXES)),
        key=lambda artifact: artifact.name,
    )
    return tuple(
        ImageBlock(
            source=ImageSource(
                media_type=next(
                    media_type
                    for suffix, media_type in PAGE_IMAGE_MEDIA_TYPES.items()
                    if page.name.lower().endswith(suffix)
                ),
                data=b64encode(page.content).decode(),
            )
        )
        for page in pages
    )


def recorded_evidence_missing(result: EvalCaseResult) -> str:
    """What a case's recorded evidence lacks, or "" when it is whole. Every recorder writes the
    case prompt and each attempt's response as strings, so a null where those keys exist marks a
    recorder that scrubbed while recording; naming the gap lets the archive refuse the case
    instead of storing one that looks validly empty. A suite whose evidence carries neither key
    answers to its own shape and is never questioned here."""
    evidence = result.evidence
    if "message" in evidence and not isinstance(evidence["message"], str):
        return "the case prompt is absent"
    attempts = evidence.get("attempts")
    if not isinstance(attempts, list):
        return ""
    for index, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, dict):
            return f"attempt {index} is not a record"
        if "response" in attempt and not isinstance(attempt["response"], str):
            return f"attempt {index} has no recorded response"
    return ""
