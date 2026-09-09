import re
from typing import Literal

from ufo_ext_sites.application_audit import MEASURED_VIEWS

from evals.harness.capability import CapabilityOutput, SharedArtifact
from evals.suites.ufo_app_qa_replay import (
    FIXTURES,
    RepairTurns,
    ReplayEvidence,
    ReplayFixture,
    _repair_followup,
)

ISSUE_VIEW_LABEL = re.compile(r"\b(light|dark) (\d+)px\b")


def _evidence_artifact(
    fixture: ReplayFixture, phase: Literal["initial", "final"]
) -> SharedArtifact:
    evidence = ReplayEvidence(
        phase=phase,
        sourceSha256=fixture.provenance.source_sha256,
        feedbackSha256=fixture.expected.sha256,
        issues=fixture.expected.issues,
        compileMs=0,
        auditMs=0,
        agentMs=0,
        agentResponse="READY",
        agentCalls=0,
        repairTurn=0 if phase == "initial" else 1,
    )
    return SharedArtifact(
        f"{fixture.name}-{phase}-evidence.json",
        evidence.model_dump_json(by_alias=True).encode(),
    )


async def test_every_repair_instruction_states_the_bound_the_environment_enforces() -> None:
    """`bound_app_qa_repair_tools` denies `replace_all` for every fixture, and the grader fails the
    denied call, so a repair instruction that leaves the bound unsaid scores the harness."""
    for fixture in FIXTURES:
        first = await _repair_followup(fixture, RepairTurns())(
            CapabilityOutput("", (), artifacts=(_evidence_artifact(fixture, "initial"),))
        )
        later = await _repair_followup(fixture, RepairTurns(turn=1))(
            CapabilityOutput(
                "",
                (),
                artifacts=(
                    _evidence_artifact(fixture, "initial"),
                    _evidence_artifact(fixture, "final"),
                ),
            )
        )

        assert first is not None
        assert later is not None
        assert "Never use replace_all." in first
        assert "Never use replace_all." in later


def test_every_pinned_issue_names_a_view_the_audit_still_measures() -> None:
    """`audit_application` keeps only the views `MEASURED_VIEWS` names, so dropping one deletes
    every pinned issue that fails there alone. The recorded feedback then never comes back,
    `_repair_followup` raises before the case reaches its repair turn, and nothing says so until a
    live replay run with a browser and a model behind it."""

    for fixture in FIXTURES:
        labelled = 0
        for issue in fixture.expected.issues:
            for scheme, width in ISSUE_VIEW_LABEL.findall(issue.message):
                labelled += 1
                assert (scheme, int(width)) in MEASURED_VIEWS, (
                    f"{fixture.name} pins {issue.code} on unmeasured {scheme} {width}px"
                )
        assert labelled, f"{fixture.name} pins no view label, so this check reads nothing"
