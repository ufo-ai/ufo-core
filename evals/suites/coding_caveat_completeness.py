"""Direct coding decisions for issue sketches with named unfinished acceptance surfaces."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.suites.coding_subagent import profile_proxy_message, profile_proxy_scorer

CASE_ROOT = Path(__file__).with_name("coding_caveat_completeness_cases")
DECISION_PREFIX = (
    "You are at the pre-implementation decision point of a coding task. Do not use tools, "
    "inspect a repository, or implement. Return only a concise implementation plan and test "
    "checklist based on the exact objective below.\n\n"
)
OBJECTIVES = (
    (
        "astropy__astropy-13398",
        "3f964e7684358167c70e45ad0b9c840833c2c697a806fb7d60f586395d2cff10",
    ),
    (
        "astropy__astropy-13579",
        "0ab1ef54aaa049d55b2e5f015f975befa2cbeb9b30091f43084416971660fcd7",
    ),
    (
        "astropy__astropy-14369",
        "061f08a59442d57167b1e12c33af539312b05c5435dcb3d9f840e892888bef9d",
    ),
)
REFRACTION_IMPLEMENTATION = re.compile(
    r"(?:implement|add|support|include|apply)[^.\n]{0,80}refraction|"
    r"refraction[^.\n]{0,80}(?:must|should|will|needs? to)[^.\n]{0,20}"
    r"(?:implement|support|include|apply)"
)
REFRACTION_REJECTION = re.compile(
    r"(?:do not|don't|without|omit|skip|defer|leave out)[^.\n]{0,80}refraction|"
    r"refraction[^.\n]{0,80}(?:out of scope|not implement|documentation only|document instead)"
)


def _evidence_scorer(
    statement: str,
    required: tuple[tuple[str, tuple[str, ...]], ...],
    forbidden: tuple[str, ...] = (),
    required_pattern: re.Pattern[str] | None = None,
    rejected_pattern: re.Pattern[str] | None = None,
) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.calls:
            return CapabilityVerdict(False, "used tools at the isolated decision step")
        text = output.response.casefold().replace("`", "").replace("()", "")
        missing = [label for label, choices in required if not any(x in text for x in choices)]
        expanded = [term for term in forbidden if term in text]
        if required_pattern is not None and required_pattern.search(text) is None:
            missing.append("an affirmative implementation decision")
        rejected = rejected_pattern is not None and rejected_pattern.search(text) is not None
        if not missing and not expanded and not rejected:
            return CapabilityVerdict(True, "all decision evidence present")
        reasons = [*(f"missing {label}" for label in missing)]
        if expanded:
            reasons.append("expanded to " + ", ".join(expanded))
        if rejected:
            reasons.append("rejected the named acceptance surface")
        return CapabilityVerdict(False, "; ".join(reasons))

    return DescribedGrader(statement, grade)


REFRACTION_ACCEPTANCE_SCORER = _evidence_scorer(
    "the plan implements the named refraction surface from adjacent observed-frame behavior "
    "and tests both direct transforms",
    (
        ("AltAz coverage", ("altaz",)),
        ("HADec coverage", ("hadec",)),
        ("regression coverage", ("regression", "test")),
    ),
    required_pattern=REFRACTION_IMPLEMENTATION,
    rejected_pattern=REFRACTION_REJECTION,
)
SLICED_WCS_NEIGHBOR_SCORER = _evidence_scorer(
    "the plan ties the sliced fixed pixel axis to the coupled world-to-pixel conversion",
    (
        ("SlicedLowLevelWCS", ("slicedlowlevelwcs",)),
        ("the fixed slice pixel", ("fixed pixel", "slice pixel", "sliced pixel")),
        ("the world-to-pixel conversion", ("world_to_pixel_values", "world_to_pixel")),
        ("a coupled-PC regression", ("pc matrix", "pcij", "coupled")),
        ("regression coverage", ("regression", "test")),
    ),
    ("refraction", "itrs", "altaz", "hadec", "ascii.cds"),
)
CDS_DIVISION_NEIGHBOR_SCORER = _evidence_scorer(
    "the plan repairs CDS chained-division parsing and covers both reported units",
    (
        ("the CDS reader", ("ascii.cds", "cds")),
        ("the unit parser", ("parser", "grammar", "parse")),
        (
            "chained-division order",
            (
                "chained division",
                "division order",
                "left-associative",
                "left associative",
                "remains in the denominator",
                "stays in the denominator",
            ),
        ),
        ("the continuum reproduction", ("10+3j/m/s/kpc2",)),
        ("the line reproduction", ("10-7j/s/kpc2",)),
        ("regression coverage", ("regression", "test")),
    ),
    ("refraction", "itrs", "altaz", "hadec", "slicedlowlevelwcs"),
)


def _objective(case_name: str, expected_digest: str) -> str:
    body = (CASE_ROOT / f"{case_name}.txt").read_bytes()
    observed_digest = sha256(body).hexdigest()
    if observed_digest != expected_digest:
        raise ValueError(
            f"{case_name} objective digest mismatch: expected {expected_digest}, "
            f"found {observed_digest}"
        )
    return body.decode()


SCORERS = (
    REFRACTION_ACCEPTANCE_SCORER,
    SLICED_WCS_NEIGHBOR_SCORER,
    CDS_DIVISION_NEIGHBOR_SCORER,
)
CASE_OBJECTIVES = tuple(
    DECISION_PREFIX + _objective(case_name, digest) for case_name, digest in OBJECTIVES
)
CASES = tuple(
    CapabilityCase(
        name=case_name,
        message=profile_proxy_message(objective),
        grader=profile_proxy_scorer(objective, scorer),
        digest_tag=f"{digest}:profile-proxy-v1",
    )
    for (case_name, digest), objective, scorer in zip(
        OBJECTIVES, CASE_OBJECTIVES, SCORERS, strict=True
    )
)
