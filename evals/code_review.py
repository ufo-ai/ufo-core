"""Three live exact-head reviews: a comparison with two severe defects, one with none, and one
whose only reported finding was disproven.

The first two grade the returned set exactly, because breadth and restraint are one dimension. A
reviewer that reports every defect it can reach scores the same as one that invents, unless the
count is bounded on both sides: the defect comparison establishes two severe defects and no third,
and the clean comparison establishes none. So a finding this repository never earned fails the run
that produced it, and the grader states which finding was invented.

The third pins the comparison where the reviewer published a disproven finding. It names that one
claim and refuses it, and leaves the rest of a 32-file comparison open — asserting what the evidence
carries and nothing wider. Its pass is a floor, not proof of a fix: that finding was intermittent,
so one silent review does not establish that the reviewer would not publish it again.

Silence counts only when the reviewer took the checkout. A comparison it never fetched returns the
same empty finding list a clean review returns, so the clean case would score a reviewer that read
nothing as perfectly precise — which is how the first run of this suite scored a failed clone.
"""

import os
from dataclasses import dataclass
from uuid import UUID

from cryptography.fernet import Fernet
from pydantic import ValidationError
from ufo_ext_coding.github_app import GIT_SLOT
from ufo_ext_coding.review_checkout import CodeReviewFinding, CodeReviewOutput

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.harness import JsonObject
from evals.harness.scorers import combine, lane_scorer
from ufo.blob import BlobStore
from ufo.credentials import CredentialStore
from ufo.untrusted import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_CLOSE_ESCAPE,
    UNTRUSTED_OPEN,
)

REPOSITORY = "metalcraftai/ufo"
CODE_REVIEW_PROFILE = "code_review"
CHECKOUT_TOOL = "checkout_code_review"
GITHUB_TOKEN_ENV = "UFO_CODE_REVIEW_EVAL_GITHUB_TOKEN"
CREDENTIAL_KEY_ENV = "UFO_CREDENTIAL_KEY"
WORKFLOW_WAIT_SECONDS = 1800.0

DEFECT_PULL_NUMBER = 1539
DEFECT_BASE_SHA = "ade0aacaf858bac4a7f44a86280df2d37207e1d2"
DEFECT_HEAD_SHA = "a41aece5c9fd2edc18a6871959cb60c4e73ebe44"

CLEAN_PULL_NUMBER = 1581
CLEAN_BASE_SHA = "af2632b6b088025711ee556a2b03943df327dabe"
CLEAN_HEAD_SHA = "e883462be12b26079f34a7691bf89e27845c2105"

REFUTED_PULL_NUMBER = 1586
REFUTED_BASE_SHA = "69827acb8890b9ac6f76768dce5ff2c86276e83f"
REFUTED_HEAD_SHA = "7de7fe8d4950f6f97f528955281e13ef6bf78037"


@dataclass(frozen=True)
class ExpectedFinding:
    name: str
    path: str
    line: int
    terms: tuple[str, ...]


DEFECT_FINDINGS = (
    ExpectedFinding(
        "markdown hard-break panic",
        "client/src/ui/markdown.rs",
        365,
        ("hardbreak", "close_link", "panic"),
    ),
    ExpectedFinding(
        "history replay",
        "extensions/ufo/ufo_ext_ufo/surface.py",
        136,
        ("history_directives", "member_message_text", "compaction"),
    ),
)


REFUTED_TERM = "bg-primary"
REFUTED_CLAIM = "the send variant leaves no bg-primary rule in the built stylesheet"


def refuted_claim_scorer(statement: str) -> Grader:
    """Refuse the one finding this comparison is proven not to hold.

    The reviewer published it twice. It is disproven three ways: `theme.css` line 2 declares
    `@source "../node_modules/streamdown/dist/*.js"`, whose chunk carries the class; the built
    stylesheet on that head holds `.bg-primary{background-color:var(--primary)}`; and the `web`
    job ran the theme tests green on the reviewed head. This case asserts only that, rather than
    that a 32-file comparison holds no severe defect at all — the reviewer stays free to report
    anything else it establishes here."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawn = next((call for call in output.calls if call.name == "spawn_subagent"), None)
        if spawn is None or not spawn.succeeded:
            return CapabilityVerdict(False, "the code review produced no result")
        if not any(call.name == CHECKOUT_TOOL and call.succeeded for call in output.calls):
            return CapabilityVerdict(False, "the review took no checkout of the comparison")
        try:
            review = CodeReviewOutput.model_validate_json(_untrusted_payload(spawn.result))
        except ValidationError as error:
            return CapabilityVerdict(False, f"the code review result is invalid: {error}")
        republished = tuple(
            f"{finding.path}:{finding.line} {finding.title}"
            for finding in review.findings
            if REFUTED_TERM
            in f"{finding.path} {finding.title} {finding.trigger} {finding.failure}".casefold()
        )
        evidence: JsonObject = {
            "findingCount": len(review.findings),
            "findingPaths": [finding.path for finding in review.findings],
            "republishedRefuted": list(republished),
        }
        if republished:
            return CapabilityVerdict(
                False, f"republished the refuted claim: {'; '.join(republished)}", evidence
            )
        return CapabilityVerdict(True, "did not republish the refuted claim", evidence)

    return DescribedGrader(statement, grade)


def exact_findings_scorer(expected: tuple[ExpectedFinding, ...], statement: str) -> Grader:
    """Require the established set: every expected finding present, and nothing else."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawn = next((call for call in output.calls if call.name == "spawn_subagent"), None)
        if spawn is None or not spawn.succeeded:
            return CapabilityVerdict(False, "the code review produced no result")
        if not any(call.name == CHECKOUT_TOOL and call.succeeded for call in output.calls):
            return CapabilityVerdict(False, "the review took no checkout of the comparison")
        try:
            review = CodeReviewOutput.model_validate_json(_untrusted_payload(spawn.result))
        except ValidationError as error:
            return CapabilityVerdict(False, f"the code review result is invalid: {error}")
        missing = tuple(
            candidate.name
            for candidate in expected
            if not any(_matches(finding, candidate) for finding in review.findings)
        )
        invented = tuple(
            f"{finding.path}:{finding.line} {finding.title}"
            for finding in review.findings
            if not any(_matches(finding, candidate) for candidate in expected)
        )
        evidence: JsonObject = {
            "findingCount": len(review.findings),
            "findingPaths": [finding.path for finding in review.findings],
            "inventedFindings": list(invented),
        }
        reasons = []
        if missing:
            reasons.append(f"missing {', '.join(missing)}")
        if invented:
            reasons.append(f"invented {'; '.join(invented)}")
        if reasons:
            return CapabilityVerdict(False, "; ".join(reasons), evidence)
        return CapabilityVerdict(True, "returned the established findings and no other", evidence)

    return DescribedGrader(statement, grade)


def _matches(finding: CodeReviewFinding, expected: ExpectedFinding) -> bool:
    text = f"{finding.title} {finding.trigger} {finding.failure}".casefold()
    return (
        finding.path == expected.path
        and finding.line == expected.line
        and all(term in text for term in expected.terms)
    )


def _untrusted_payload(result: str) -> str:
    marker = UNTRUSTED_OPEN.format(source="spawn_subagent")
    _, found, tail = result.partition(marker)
    if not found or not tail.endswith(UNTRUSTED_CLOSE):
        return result
    return tail.removesuffix(UNTRUSTED_CLOSE).replace(UNTRUSTED_CLOSE_ESCAPE, UNTRUSTED_CLOSE)


def _review_message(pull_number: int, base_sha: str, head_sha: str) -> str:
    return (
        "Call `spawn_subagent` once with profile `code_review`, `background` false, and this "
        "exact payload: "
        f'{{"repository":"{REPOSITORY}","pull_number":{pull_number},'
        f'"base_sha":"{base_sha}","head_sha":"{head_sha}"}}. '
        "Return its result. Do not review the code yourself."
    )


async def _seed_github_token(workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    token = os.environ.get(GITHUB_TOKEN_ENV)
    key = os.environ.get(CREDENTIAL_KEY_ENV)
    if not token or not key:
        raise RuntimeError(f"{GITHUB_TOKEN_ENV} and {CREDENTIAL_KEY_ENV} are required")
    await CredentialStore(Fernet(key.encode())).put(workspace_id, GIT_SLOT, token)


CASES = (
    CapabilityCase(
        name="code-review-all-severe-defects",
        message=_review_message(DEFECT_PULL_NUMBER, DEFECT_BASE_SHA, DEFECT_HEAD_SHA),
        grader=combine(
            lane_scorer(frozenset({CODE_REVIEW_PROFILE})),
            exact_findings_scorer(
                DEFECT_FINDINGS,
                "the typed code review result returns the markdown hard-break panic and history "
                "replay as separate findings, and no third finding",
            ),
        ),
        seed=_seed_github_token,
        web_dependent=True,
        digest_tag="exact-review-established-set-1",
    ),
    CapabilityCase(
        name="code-review-clean-comparison",
        message=_review_message(CLEAN_PULL_NUMBER, CLEAN_BASE_SHA, CLEAN_HEAD_SHA),
        grader=combine(
            lane_scorer(frozenset({CODE_REVIEW_PROFILE})),
            exact_findings_scorer(
                (),
                "the typed code review result returns no finding for a comparison that holds no "
                "severe defect",
            ),
        ),
        seed=_seed_github_token,
        web_dependent=True,
        digest_tag="exact-review-clean-comparison-1",
    ),
    CapabilityCase(
        name="code-review-refuted-claim",
        message=_review_message(REFUTED_PULL_NUMBER, REFUTED_BASE_SHA, REFUTED_HEAD_SHA),
        grader=combine(
            lane_scorer(frozenset({CODE_REVIEW_PROFILE})),
            refuted_claim_scorer(
                "the typed code review result does not report the refuted claim that "
                f"{REFUTED_CLAIM}"
            ),
        ),
        seed=_seed_github_token,
        web_dependent=True,
        digest_tag="exact-review-refuted-claim-1",
    ),
)
