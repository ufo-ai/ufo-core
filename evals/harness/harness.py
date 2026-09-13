"""The eval result record: one case's verdict, and a suite's roll-up with a digest that pins the
cases it scored. A digest is the sha256 of the canonical case payloads, so a report is comparable
only against a run of the identical suite — a changed case moves the digest."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from json import dumps

from pydantic import BaseModel, ConfigDict, Field

from ufo.schema.records import CANCELLED, NON_TERMINAL_STATUSES, RUNNING, TurnStatus

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
    provider_fault: bool = False
    """Whether this case excluded on something no list could have named ahead of the night — a
    timeout, an overload, a 429, or the harness's own wait expiring on a turn that was still
    working — rather than on the sweep's own shape. Each lands on whichever case the clock or the
    provider happened to catch: the nightly cohort gate reads this flag to tell such a night from a
    suite that quietly stopped scoring a case."""
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
    page_evidence_coverage: float | None = Field(
        default=None, ge=0.0, le=1.0, alias="pageEvidenceCoverage"
    )
    benchmark: JsonObject | None = None
    metrics: tuple[EvalMetric, ...] = ()
    uncertified: str | None = None
    """Why the runtime integrity gate refused to certify this report's run — a missing or
    mismatched attestation — or None when certified or local. An uncertified report keeps its
    measurements but fails the run."""
    runtime_identities: dict[str, int] | None = None
    """Cases per runtime identity the remote run attested (`revision#digest12`), most-covered
    first — a fleet deploy rolling mid-run shows as two entries, purely informational. None for a
    local run."""

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
        target = f", target model {self.target_model}" if self.target_model is not None else ""
        summary = (
            f"{self.name} {passed}/{len(self.scored)} passed, {self.excluded_count} excluded "
            f"(rate {self.pass_rate:.0%}){benchmark}{target}"
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
        identities = ""
        if self.runtime_identities and len(self.runtime_identities) > 1:
            counts = ", ".join(f"{key} {count}" for key, count in self.runtime_identities.items())
            identities = f" identities: {counts}"
        taint = f" uncertified: {self.uncertified}" if self.uncertified else ""
        if self.degraded_recall_count is None or self.unmapped_evidence_count is None:
            detail = f", {metric_summary}" if metric_summary else ""
            return f"{summary}{detail}{tier_summary} {self.digest}{identities}{taint}"
        coverage = "mapped evidence coverage n/a"
        if (
            self.mean_mapped_evidence_coverage is not None
            and self.min_mapped_evidence_coverage is not None
        ):
            coverage = (
                f"mapped evidence coverage mean {self.mean_mapped_evidence_coverage:.0%}, "
                f"min {self.min_mapped_evidence_coverage:.0%}"
            )
        pages = (
            "page evidence n/a"
            if self.page_evidence_coverage is None
            else f"page evidence coverage {self.page_evidence_coverage:.0%}"
        )
        return (
            f"{summary}, {coverage}, {pages}, {self.degraded_recall_count} degraded, "
            f"{self.unmapped_evidence_count} unmapped evidence {self.digest}{identities}{taint}"
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
        if self.uncertified is not None:
            result["uncertified"] = self.uncertified
        if self.runtime_identities is not None:
            identities: JsonObject = {key: count for key, count in self.runtime_identities.items()}
            result["runtimeIdentities"] = identities
        return result


def digest_payload(payload: Mapping[str, Json]) -> str:
    raw = dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(raw).hexdigest()}"


INFRA_ERROR_MARKERS = (
    "payment required",
    "rate limit",
    "no_more_credits",
    "no credits remaining",
    "quota",
    "service unavailable",
    "connection",
    "timed out",
    "timeout",
)
INFRA_ERROR_STATUS = re.compile(r"\b(?:402|429|500|502|503)\b")
"""An HTTP status that names an external-service failure, matched as a whole number. Matched as a
bare substring, these digits also read out of any identifier that happens to carry them — a run
directory, a digest, a workspace id — and exclude a case the model genuinely failed."""


def infra_error(errors: Sequence[str]) -> str:
    """The first tool error that signals an external-service failure, or "" if none. A
    false-positive match UNDER-counts (drops a real capability failure from scoring) rather
    than green-washing it into a pass; excluded cases stay visible in the report for audit."""
    for message in errors:
        lowered = message.lower()
        phrase = any(marker in lowered for marker in INFRA_ERROR_MARKERS)
        if phrase or INFRA_ERROR_STATUS.search(lowered):
            return message
    return ""


ACCOUNT_ERROR_MARKERS = (
    "payment required",
    "no_more_credits",
    "no credits remaining",
    "quota",
)
ACCOUNT_ERROR_STATUS = re.compile(r"\b402\b")


def provider_owned_error(message: str) -> bool:
    """Whether an infra tool error names a fault the provider owns — a 429, a 5xx, a dropped
    connection, a timeout — rather than the eval account's own spent credit or quota. It draws
    over a tool error the line `_unclean_verdict` draws over an error class: a transient is
    provider weather the nightly cohort gate accepts, while an account we let run dry is ours and
    stays an unexpected exclusion."""
    lowered = message.lower()
    account = any(
        marker in lowered for marker in ACCOUNT_ERROR_MARKERS
    ) or ACCOUNT_ERROR_STATUS.search(lowered)
    return not account and bool(infra_error((message,)))


TRANSIENT_ERROR_CLASSES = frozenset(
    {
        "RateLimitError",
        "ModelAccountRateLimited",
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
CONFIGURATION_ERROR_CLASSES = frozenset({"CredentialValueInvalid"})


def is_transient_fault(error_class: str | None) -> bool:
    """Whether a turn crashed on a model or transport fault the provider owns — a read/connect
    timeout, an overload, a 5xx — carried on the terminal's `error_class` (or the class of a
    simulator-leg model call that raised). These are external uncertainty, not a capability signal,
    so the run is excluded from scoring rather than counted as a failure (mirroring the capability
    harness's `web_dependent` infra exclusion). Matched by exact class name against the anthropic
    SDK / httpx transient set, never a substring: the terminal `error_class` also carries the class
    of an internal fault (a DB or DBOS wedge the backstop commits as `type(error).__name__`), and a
    builtin `TimeoutError` or `ConnectionError` there is an internal wedge that must surface as a
    failure, never be masked as external. `ModelAccountRateLimited` is the repo's own name for a
    provider 429 that outlived the client's retries — the class a terminal frame carries where the
    SDK's `RateLimitError` used to surface — and is the same external limit."""
    return error_class in TRANSIENT_ERROR_CLASSES


WAIT_EXPIRED = "turn produced no terminal transcript"
WAIT_EXPIRY_STATUSES = frozenset({*NON_TERMINAL_STATUSES, CANCELLED})
"""The statuses a turn can hold when the harness's own wait expires on it. `cancelled` belongs
here because the harness writes it: `WorkspaceDriver._cancel_overdue` terminalizes the turn the
moment the wait deadline fires, so reading `cancelled` back as a capability verdict scores the
harness's own stopwatch against the model (measured on three cases of the 2026-08-21 sweep —
`skill_routing/board-visual-narrative`, `document_visual/kickoff`, `document_visual/quarterly`,
each at 300.0s wall with no transcript). Any other failure reason on a cancelled turn is
untouched."""

LIVE_WAIT_EXPIRY_STATUSES = frozenset({RUNNING})
"""The one status on which an expired wait is the clock's rather than the sweep's, read off what
the turn held when the deadline fired. Only `running` had the turn actually working — with the
model or in its own tool. A turn still `queued` never reached a model and sat behind our own worker
backlog, and a `parked` one waits on us, so an expiry there is the sweep's own shape and stays the
cohort drift the nightly gate refuses. `cancelled` is no member of this set: the harness writes it
over every overdue turn through `cancel_one_turn`, queued ones included, so that terminal names the
harness rather than what the turn was doing. The status alone does not settle it — the row turns
`running` at the dispatch claim — so `provider_owned_fault` asks for the turn's own first step
beside it."""


def infra_owned_fault(
    error_class: str | None, failure_reason: str, status: TurnStatus | None
) -> bool:
    """Whether an unclean run's fault lies outside the model's answer: a provider-owned transient,
    rejected eval credentials, or a wait that expired while the turn was still live or was
    cancelled by the expiry itself. None of them is a capability signal — an expired wait measures
    the budget the shard chose, not what the model could do — so none is scored. A turn that
    reached `done` or `failed` without a transcript for any other reason is a wedge of ours and
    stays a failure."""
    if is_transient_fault(error_class) or error_class in CONFIGURATION_ERROR_CLASSES:
        return True
    return failure_reason == WAIT_EXPIRED and status in WAIT_EXPIRY_STATUSES


def provider_owned_fault(
    error_class: str | None,
    failure_reason: str,
    expiry_status: TurnStatus | None,
    work_started: bool,
) -> bool:
    """Whether an exclusion is one no list could have named ahead of the night, read off that same
    exclusion so the two cannot disagree. A transient is the provider's, and a wait that expired on
    a turn still working is the shard's own stopwatch — `LIVE_WAIT_EXPIRY_STATUSES` names that one.
    Both land on whichever case they land on, so the nightly gate accepts them; a gate that refused
    them would cost the sweep its trend point over a case the harness already declined to score.
    Rejected eval credentials, a turn that never left our queue, and a parked turn are the sweep's
    own shape, and stay the cohort drift the gate refuses.

    `expiry_status` is `TargetResult.expiry_status`: the status the turn held when the wait expired,
    read before the harness's own cancel terminalized it. Never the status the record carries
    afterwards — `cancel_one_turn` commits `cancelled` over `queued`, `parked` and `running` alike,
    so once the cancel lands a turn that never left our queue reads exactly like one that was
    working. An expiry whose status is unknown is ours.

    `work_started` is `TargetResult.work_started`: whether the turn had recorded a step of its own.
    A turn turns `running` at the dispatch claim, before the engine steps, so a `running` turn with
    no step is still inside the rig's own startup — the claim, the sandbox boot, the preloaded
    mounts, the latency `mounts.START_DEADLINE_SECONDS` bounds separately — and no model ever saw
    it. That expiry is ours, exactly as a queued one is."""
    if not infra_owned_fault(error_class, failure_reason, expiry_status):
        return False
    if error_class in CONFIGURATION_ERROR_CLASSES:
        return False
    return is_transient_fault(error_class) or (
        work_started and expiry_status in LIVE_WAIT_EXPIRY_STATUSES
    )
