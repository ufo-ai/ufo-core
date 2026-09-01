from evals.suites.semantic_quality import CASES


def test_semantic_quality_samples_model_variance() -> None:
    assert all(case.samples == 3 for case in CASES)


def test_commitment_tradeoff_prompt_matches_its_trigger_and_evidence_rubric() -> None:
    case = next(case for case in CASES if case.name == "commitment-tradeoff")

    assert "if security approval fails" in case.message
    assert "later capacity or signed SSO commitment" in case.message
    assert "Add no facts beyond the list" in case.message
