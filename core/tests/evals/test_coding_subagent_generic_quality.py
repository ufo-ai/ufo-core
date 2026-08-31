import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.coding_subagent import (
    PROFILE_CASES,
    DecisionEvidence,
    annotation_state_evidence,
    composite_modulus_boundary_evidence,
    cross_layer_error_emitter_evidence,
    derived_state_evidence,
    direct_error_emitter_evidence,
    discovery_boundary_evidence,
    middleware_override_evidence,
    precedence_evidence,
    prime_zero_boundary_evidence,
    pyreverse_consumer_evidence,
    transform_state_evidence,
    validation_scope_evidence,
)

EVERY_EVIDENCE = (
    cross_layer_error_emitter_evidence(),
    direct_error_emitter_evidence(),
    composite_modulus_boundary_evidence(),
    prime_zero_boundary_evidence(),
    middleware_override_evidence(repository_wide=True),
    middleware_override_evidence(repository_wide=False),
    annotation_state_evidence(referenced=True),
    annotation_state_evidence(referenced=False),
    pyreverse_consumer_evidence(public_output=True),
    pyreverse_consumer_evidence(public_output=False),
    discovery_boundary_evidence(public_discovery=True),
    discovery_boundary_evidence(public_discovery=False),
    precedence_evidence(python_mro=True),
    precedence_evidence(python_mro=False),
    transform_state_evidence(stateful=True),
    transform_state_evidence(stateful=False),
    derived_state_evidence(surviving_owner=True),
    derived_state_evidence(surviving_owner=False),
    validation_scope_evidence(cross_cutting=True),
    validation_scope_evidence(cross_cutting=False),
)


@pytest.mark.parametrize("evidence", EVERY_EVIDENCE, ids=lambda e: e.statement[:40])
def test_every_requirement_becomes_one_judged_criterion(evidence: DecisionEvidence) -> None:
    criteria = evidence.rubric
    expected = len(evidence.required) + (1 if evidence.forbidden else 0)
    assert len(criteria) == expected
    for (label, choices), criterion in zip(evidence.required, criteria, strict=False):
        assert label in criterion
        assert choices[0] in criterion
    if evidence.forbidden:
        assert all(term in criteria[-1] for term in evidence.forbidden)
        assert "reject" in criteria[-1]


async def test_the_decision_grader_bans_tools_and_leaves_content_to_the_judge() -> None:
    evidence = prime_zero_boundary_evidence()
    grader = evidence.grader()
    clean = await grader(CapabilityOutput("any prose at all", ()))
    assert clean.passed
    called = await grader(
        CapabilityOutput(
            "any prose at all",
            (ToolInvocation(name="bash", input={"command": "true"}),),
        )
    )
    assert not called.passed
    assert "tools" in called.reason


def test_every_decision_case_carries_its_rubric() -> None:
    decision_cases = [case for case in PROFILE_CASES if case.name.startswith("coding-subagent-")]
    with_rubric = [case for case in decision_cases if case.rubric]
    assert len(with_rubric) == 20
    for case in with_rubric:
        assert all(criterion for criterion in case.rubric)
