"""One capability case: a message the live agent answers, a deterministic grader over its answer
and tool trajectory, and an optional semantic rubric. Deterministic checks run first; a passing
rubric case then reaches the target's model judge. A case runs against a `Target` that drives a real
turn and reconstructs the answer + tool calls from the durable transcript. Transcript tool results
retain text, completion, and error state; the agent's configured tool set remains fixed per run."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.harness import EvalCaseResult, Json, JsonObject, infra_error
from evals.harness.judge import JUDGE_REVISION, CriterionVerdict, rubric_pass
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message

if TYPE_CHECKING:
    from evals.harness.target import CapabilityTarget


@dataclass(frozen=True)
class CapabilityVerdict:
    passed: bool
    reason: str
    evidence: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ToolInvocation:
    """One tool call and the completion state reconstructed from its transcript result."""

    name: str
    input: JsonObject
    result: str = ""
    has_result: bool = False
    is_error: bool = False

    @property
    def succeeded(self) -> bool:
        return self.has_result and not self.is_error


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


@dataclass(frozen=True)
class CapabilityOutput:
    """The answer, tool trajectory, artifacts, and allowlisted log visible to a grader."""

    response: str
    calls: tuple[ToolInvocation, ...]
    tool_errors: tuple[str, ...] = ()
    artifacts: tuple[SharedArtifact, ...] = ()
    artifact_references: tuple[SharedArtifactReference, ...] = ()
    artifact_error: str = ""
    log: TurnLog | None = None
    compactions: int = 0
    tokens: int = 0
    cost_micro_usd: int = 0

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.calls)


type Grader = Callable[[CapabilityOutput], Awaitable[CapabilityVerdict]]


@dataclass(frozen=True)
class WorkspaceFile:
    path: str
    content: bytes


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
    whose private memory the eval conversation may recall. `redact_evidence` marks the recorded
    result so `redacted_case` scrubs its source and candidate content before publication; the
    local run archive keeps everything."""

    name: str
    message: str
    grader: Grader
    samples: int = 1
    web_dependent: bool = False
    digest_tag: str = ""
    rubric: tuple[str, ...] = ()
    member_key: str | None = None
    workspace_files: tuple[WorkspaceFile, ...] = ()
    prior_messages: tuple[str, ...] = ()
    redact_evidence: bool = False
    references: tuple[CapabilityReference, ...] = ()

    def __post_init__(self) -> None:
        paths = tuple(reference.path for reference in self.references)
        if len(paths) != len(set(paths)):
            raise ValueError("capability reference paths must be unique")

    def payload(self) -> JsonObject:
        payload: JsonObject = {
            "name": self.name,
            "message": self.message,
            "samples": self.samples,
            "webDependent": self.web_dependent,
            "grader": self.digest_tag or self.name,
            "rubric": list(self.rubric),
        }
        if self.rubric:
            payload["judgeRevision"] = JUDGE_REVISION
        if self.member_key is not None:
            payload["memberKey"] = self.member_key
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
        if self.redact_evidence:
            payload["redactEvidence"] = True
        if self.references:
            payload["references"] = [
                {
                    "path": reference.path,
                    "digest": reference.digest,
                    "sizeBytes": reference.size_bytes,
                }
                for reference in self.references
            ]
        return payload


async def run_capability_case(case: CapabilityCase, target: CapabilityTarget) -> EvalCaseResult:
    samples = [await sample_capability(case, target) for _ in range(max(case.samples, 1))]
    winning_indexes = [index for index, sample in enumerate(samples) if sample.verdict.passed]
    selected_index = winning_indexes[0] if winning_indexes else len(samples) - 1
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
                "log": (
                    None if sample_output.log is None else sample_output.log.model_dump(mode="json")
                ),
                "compactions": sample_output.compactions,
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
        "rubric": list(case.rubric),
        "memberKey": case.member_key,
        "webDependent": case.web_dependent,
        "selectedAttempt": selected_index,
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
                redact_evidence=case.redact_evidence,
            )
    passed = bool(winning_indexes)
    reason = (
        verdict.reason
        if passed
        else f"{len(winning_indexes)}/{len(samples)} samples passed: {verdict.reason}"
    )
    return EvalCaseResult(
        name=case.name,
        passed=passed,
        reason=reason,
        evidence=evidence,
        redact_evidence=case.redact_evidence,
    )


async def sample_capability(case: CapabilityCase, target: CapabilityTarget) -> CapabilitySample:
    result = await target.run(case)
    if not result.clean:
        return CapabilitySample(
            result.output, CapabilityVerdict(False, result.failure_reason), result.trajectory
        )
    deterministic = await case.grader(result.output)
    if not deterministic.passed or not case.rubric:
        return CapabilitySample(result.output, deterministic, result.trajectory)
    if target.judge is None:
        return CapabilitySample(
            result.output,
            CapabilityVerdict(False, "semantic rubric requires a model judge"),
            result.trajectory,
        )
    verdict = await rubric_pass(case.message, result.output.response, case.rubric, target.judge)
    return CapabilitySample(
        result.output,
        CapabilityVerdict(
            verdict.passed,
            f"{deterministic.reason}; {verdict.reason}",
            deterministic.evidence,
        ),
        result.trajectory,
        judge=verdict.criteria,
    )


class _RecordedCall(BaseModel):
    name: str
    has_result: bool = Field(alias="hasResult")
    is_error: bool = Field(alias="isError")


class _RecordedAttempt(BaseModel):
    passed: bool
    calls: tuple[_RecordedCall, ...]
    tool_errors: tuple[str, ...] = Field(alias="toolErrors")
    artifact_error: str | None = Field(default=None, alias="artifactError")
    tokens: int = 0
    cost_micro_usd: int = Field(default=0, alias="costMicroUsd")
    compactions: int = 0
    grader: JsonObject | None = None


class _RecordedEvidence(BaseModel):
    selected_attempt: int = Field(alias="selectedAttempt")
    attempts: tuple[_RecordedAttempt, ...]


def redacted_case(result: EvalCaseResult) -> EvalCaseResult:
    """The publishable form of one case. A flagged case keeps verdicts, tool names, counts,
    usage, and boolean or numeric grader evidence; its benchmark source, candidate output, grader
    text, and trajectory do not leave the machine. An unflagged case passes through whole."""
    if not result.redact_evidence:
        return result
    recorded = _RecordedEvidence.model_validate(result.evidence)
    attempts: list[Json] = [
        {
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
            "toolErrors": ["tool error"] * len(attempt.tool_errors),
            "artifacts": [],
            "artifactError": "artifact error" if attempt.artifact_error else None,
            "tokens": attempt.tokens,
            "costMicroUsd": attempt.cost_micro_usd,
            "log": None,
            "compactions": attempt.compactions,
            "grader": (
                {
                    key: value
                    for key, value in attempt.grader.items()
                    if isinstance(value, bool | int | float)
                }
                if attempt.grader
                else None
            ),
            "trajectory": None,
        }
        for attempt in recorded.attempts
    ]
    if result.excluded:
        reason = "infra-excluded (web unavailable)"
    else:
        reason = "passed" if result.passed else "failed"
    evidence: JsonObject = {
        "message": None,
        "rubric": [],
        "selectedAttempt": recorded.selected_attempt,
        "attempts": attempts,
    }
    return result.model_copy(update={"reason": reason, "evidence": evidence})
