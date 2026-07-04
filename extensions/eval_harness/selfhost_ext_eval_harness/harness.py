"""The eval result record: one case's verdict, and a suite's roll-up with a digest that pins the
cases it scored. A digest is the sha256 of the canonical case payloads, so a report is comparable
only against a run of the identical suite — a changed case moves the digest."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from json import dumps

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]


@dataclass(frozen=True)
class EvalCaseResult:
    name: str
    passed: bool
    reason: str
    evidence: JsonObject


@dataclass(frozen=True)
class EvalReport:
    name: str
    suite: str
    digest: str
    cases: tuple[EvalCaseResult, ...]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(1 for case in self.cases if case.passed) / len(self.cases)

    def to_json(self) -> JsonObject:
        return {
            "name": self.name,
            "suite": self.suite,
            "digest": self.digest,
            "passed": self.passed,
            "passRate": self.pass_rate,
            "cases": [
                {
                    "name": case.name,
                    "passed": case.passed,
                    "reason": case.reason,
                    "evidence": case.evidence,
                }
                for case in self.cases
            ],
        }


def digest_payload(payload: Mapping[str, Json]) -> str:
    raw = dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(raw).hexdigest()}"


# Substrings in a tool error that mean the EXTERNAL service failed (quota, rate limit, 5xx, network)
# rather than the agent's mistake. A web-dependent case that fails behind one of these is
# infra-excluded — reported passed, not a capability failure.
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
    """The first tool error that signals an external-service failure, or "" if none."""
    for message in errors:
        lowered = message.lower()
        if any(marker in lowered for marker in INFRA_ERROR_MARKERS):
            return message
    return ""
