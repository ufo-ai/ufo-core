"""A live exact-head review with two independent severe defects."""

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
PULL_NUMBER = 1539
BASE_SHA = "ade0aacaf858bac4a7f44a86280df2d37207e1d2"
HEAD_SHA = "a41aece5c9fd2edc18a6871959cb60c4e73ebe44"
CODE_REVIEW_PROFILE = "code_review"
GITHUB_TOKEN_ENV = "UFO_CODE_REVIEW_EVAL_GITHUB_TOKEN"
CREDENTIAL_KEY_ENV = "UFO_CREDENTIAL_KEY"
WORKFLOW_WAIT_SECONDS = 900.0


@dataclass(frozen=True)
class ExpectedFinding:
    name: str
    path: str
    line: int
    terms: tuple[str, ...]


EXPECTED_FINDINGS = (
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


def all_findings_scorer() -> Grader:
    """Require both known independent findings in the typed child result."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawn = next((call for call in output.calls if call.name == "spawn_subagent"), None)
        if spawn is None or not spawn.succeeded:
            return CapabilityVerdict(False, "the code review produced no result")
        try:
            review = CodeReviewOutput.model_validate_json(_untrusted_payload(spawn.result))
        except ValidationError as error:
            return CapabilityVerdict(False, f"the code review result is invalid: {error}")
        missing = tuple(
            expected.name
            for expected in EXPECTED_FINDINGS
            if not any(_matches(finding, expected) for finding in review.findings)
        )
        evidence: JsonObject = {
            "findingCount": len(review.findings),
            "findingPaths": [finding.path for finding in review.findings],
        }
        if missing:
            return CapabilityVerdict(False, f"missing {', '.join(missing)}", evidence)
        return CapabilityVerdict(True, "returned both independent findings", evidence)

    return DescribedGrader(
        "the typed code review result returns the markdown hard-break panic and history replay "
        "as separate findings",
        grade,
    )


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


async def _seed_github_token(workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
    token = os.environ.get(GITHUB_TOKEN_ENV)
    key = os.environ.get(CREDENTIAL_KEY_ENV)
    if not token or not key:
        raise RuntimeError(f"{GITHUB_TOKEN_ENV} and {CREDENTIAL_KEY_ENV} are required")
    await CredentialStore(Fernet(key.encode())).put(workspace_id, GIT_SLOT, token)


CASES = (
    CapabilityCase(
        name="code-review-all-severe-defects",
        message=(
            "Call `spawn_subagent` once with profile `code_review`, `background` false, and this "
            "exact payload: "
            f'{{"repository":"{REPOSITORY}","pull_number":{PULL_NUMBER},'
            f'"base_sha":"{BASE_SHA}","head_sha":"{HEAD_SHA}"}}. '
            "Return its result. Do not review the code yourself."
        ),
        grader=combine(
            lane_scorer(frozenset({CODE_REVIEW_PROFILE})),
            all_findings_scorer(),
        ),
        seed=_seed_github_token,
        web_dependent=True,
        digest_tag="exact-review-all-findings-1",
    ),
)
