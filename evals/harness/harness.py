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


class EvalMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: float = Field(ge=0.0, le=1.0)


class EvalCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    reason: str
    evidence: JsonObject
    excluded: bool = False
    tier: int | None = None


class EvalReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    suite: str
    digest: str
    cases: tuple[EvalCaseResult, ...]
    target_model: str | None = None
    judge_model: str | None = None
    simulator_model: str | None = None
    judge_revision: str | None = None
    mean_mapped_evidence_coverage: float | None = Field(
        default=None, ge=0.0, le=1.0, alias="meanMappedEvidenceCoverage"
    )
    min_mapped_evidence_coverage: float | None = Field(
        default=None, ge=0.0, le=1.0, alias="minMappedEvidenceCoverage"
    )
    degraded_recall_count: int | None = Field(default=None, ge=0, alias="degradedRecallCount")
    unmapped_evidence_count: int | None = Field(default=None, ge=0, alias="unmappedEvidenceCount")
    benchmark: JsonObject | None = None
    metrics: tuple[EvalMetric, ...] = ()

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
    def tier_rates(self) -> tuple[tuple[int, int, int], ...]:
        """(tier, passed, total) per difficulty tier over scored cases that carry one, ascending —
        the boundary-eval signal: a suite with a difficulty gradient reads its frontier here, where
        the top tier stays below 100% until the agent genuinely improves."""
        tiers = sorted({case.tier for case in self.scored if case.tier is not None})
        return tuple(
            (
                tier,
                sum(1 for case in self.scored if case.tier == tier and case.passed),
                sum(1 for case in self.scored if case.tier == tier),
            )
            for tier in tiers
        )

    @property
    def console_summary(self) -> str:
        passed = sum(1 for case in self.scored if case.passed)
        benchmark = ""
        match self.benchmark:
            case {
                "claimCoverageThreshold": float(threshold),
                "performance": {
                    "tier": str(tier),
                    "meanClaimCoverage": float(mean_claim_coverage),
                },
            }:
                benchmark = (
                    f", {tier} tier, mean claim coverage {mean_claim_coverage:.0%} "
                    f"at {threshold:.0%} task threshold"
                )
        summary = (
            f"{self.name} {passed}/{len(self.scored)} passed, {self.excluded_count} excluded "
            f"(rate {self.pass_rate:.0%}){benchmark}"
        )
        metric_summary = ", ".join(
            f"{metric.name.replace('_', ' ')} {metric.value:.1%}" for metric in self.metrics
        )
        tiers = self.tier_rates
        tier_summary = (
            " [" + ", ".join(f"T{tier} {passed}/{total}" for tier, passed, total in tiers) + "]"
            if tiers
            else ""
        )
        if self.degraded_recall_count is None or self.unmapped_evidence_count is None:
            detail = f", {metric_summary}" if metric_summary else ""
            return f"{summary}{detail}{tier_summary} {self.digest}"
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
            "simulatorModel": self.simulator_model,
            "judgeRevision": self.judge_revision,
            "passed": self.passed,
            "passRate": self.pass_rate,
            "excludedCount": self.excluded_count,
            "metrics": [metric.model_dump(mode="json") for metric in self.metrics],
            "tierRates": [
                {"tier": tier, "passed": passed, "total": total}
                for tier, passed, total in self.tier_rates
            ],
            "cases": [
                {
                    "name": case.name,
                    "passed": case.passed,
                    "excluded": case.excluded,
                    "reason": case.reason,
                    "tier": case.tier,
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
        if self.benchmark is not None:
            result["benchmark"] = self.benchmark
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


TRANSIENT_ERROR_CLASSES = frozenset(
    {
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
        "DeadlineExceededError",
        "APIConnectionError",
        "APITimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "WriteTimeout",
        "ReadError",
        "ConnectError",
        "WriteError",
        "RemoteProtocolError",
        "ProxyError",
    }
)


def is_transient_fault(error_class: str | None) -> bool:
    """Whether a turn crashed on a model or transport fault the provider owns — a read/connect
    timeout, an overload, a 5xx — carried on the terminal's `error_class` (or the class of a
    simulator-leg model call that raised). These are external uncertainty, not a capability signal,
    so the run is excluded from scoring rather than counted as a failure (mirroring the capability
    harness's `web_dependent` infra exclusion). Matched by exact class name against the anthropic
    SDK / httpx transient set, never a substring: the terminal `error_class` also carries the class
    of an internal fault (a DB or DBOS wedge the backstop commits as `type(error).__name__`), and a
    builtin `TimeoutError` or `ConnectionError` there is an internal wedge that must surface as a
    failure, never be masked as external."""
    return error_class in TRANSIENT_ERROR_CLASSES
