"""The eval result record: one case's verdict, and a suite's roll-up with a digest that pins the
cases it scored. A digest is the sha256 of the canonical case payloads, so a report is comparable
only against a run of the identical suite — a changed case moves the digest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from json import dumps

from pydantic import BaseModel, ConfigDict, Field

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]


class EvalCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    reason: str
    evidence: JsonObject
    excluded: bool = False


class EvalReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    suite: str
    digest: str
    cases: tuple[EvalCaseResult, ...]
    target_model: str | None = None
    judge_model: str | None = None
    judge_revision: str | None = None
    mean_mapped_evidence_coverage: float | None = Field(
        default=None, ge=0.0, le=1.0, alias="meanMappedEvidenceCoverage"
    )
    min_mapped_evidence_coverage: float | None = Field(
        default=None, ge=0.0, le=1.0, alias="minMappedEvidenceCoverage"
    )
    degraded_recall_count: int | None = Field(default=None, ge=0, alias="degradedRecallCount")
    unmapped_evidence_count: int | None = Field(default=None, ge=0, alias="unmappedEvidenceCount")

    @property
    def scored(self) -> tuple[EvalCaseResult, ...]:
        """The cases that count toward the verdict. An infra-excluded case (its external service was
        down, so capability could not be tested) is neither pass nor fail, so it drops out of both
        the suite verdict and the pass rate."""
        return tuple(case for case in self.cases if not case.excluded)

    @property
    def excluded_count(self) -> int:
        return len(self.cases) - len(self.scored)

    @property
    def passed(self) -> bool:
        """True iff every scorable case passed. A suite with no scorable case — all excluded, or
        empty — demonstrated no capability, so it is not a pass."""
        return bool(self.scored) and all(case.passed for case in self.scored)

    @property
    def pass_rate(self) -> float:
        scored = self.scored
        if not scored:
            return 0.0
        return sum(1 for case in scored if case.passed) / len(scored)

    @property
    def console_summary(self) -> str:
        passed = sum(1 for case in self.scored if case.passed)
        summary = (
            f"{self.name} {passed}/{len(self.scored)} passed, {self.excluded_count} excluded "
            f"(rate {self.pass_rate:.0%})"
        )
        if self.degraded_recall_count is None or self.unmapped_evidence_count is None:
            return f"{summary} {self.digest}"
        coverage = "mapped evidence coverage n/a"
        if (
            self.mean_mapped_evidence_coverage is not None
            and self.min_mapped_evidence_coverage is not None
        ):
            coverage = (
                f"mapped evidence coverage mean {self.mean_mapped_evidence_coverage:.0%}, "
                f"min {self.min_mapped_evidence_coverage:.0%}"
            )
        return (
            f"{summary}, {coverage}, {self.degraded_recall_count} degraded, "
            f"{self.unmapped_evidence_count} unmapped evidence {self.digest}"
        )

    def to_json(self) -> JsonObject:
        result: JsonObject = {
            "name": self.name,
            "suite": self.suite,
            "digest": self.digest,
            "targetModel": self.target_model,
            "judgeModel": self.judge_model,
            "judgeRevision": self.judge_revision,
            "passed": self.passed,
            "passRate": self.pass_rate,
            "excludedCount": self.excluded_count,
            "cases": [
                {
                    "name": case.name,
                    "passed": case.passed,
                    "excluded": case.excluded,
                    "reason": case.reason,
                    "evidence": case.evidence,
                }
                for case in self.cases
            ],
        }
        if self.degraded_recall_count is not None and self.unmapped_evidence_count is not None:
            result.update(
                {
                    "meanMappedEvidenceCoverage": self.mean_mapped_evidence_coverage,
                    "minMappedEvidenceCoverage": self.min_mapped_evidence_coverage,
                    "degradedRecallCount": self.degraded_recall_count,
                    "unmappedEvidenceCount": self.unmapped_evidence_count,
                }
            )
        return result


def digest_payload(payload: Mapping[str, Json]) -> str:
    raw = dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(raw).hexdigest()}"


INFRA_ERROR_MARKERS = (
    "402",
    "429",
    "payment required",
    "rate limit",
    "no_more_credits",
    "quota",
    "service unavailable",
    " 500",
    " 502",
    " 503",
    "connection",
    "timed out",
    "timeout",
)


def infra_error(errors: Sequence[str]) -> str:
    """The first tool error that signals an external-service failure, or "" if none. A
    false-positive match now UNDER-counts (drops a real capability failure from scoring) rather
    than green-washing it into a pass; excluded cases stay visible in the report for audit."""
    for message in errors:
        lowered = message.lower()
        if any(marker in lowered for marker in INFRA_ERROR_MARKERS):
            return message
    return ""
