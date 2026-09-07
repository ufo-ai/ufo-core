"""A verdict's evidence has to say which calls the evaluated turn made itself.

`calls` is the parent's with every child's appended, so a criterion that turns on the distinction —
who bound the homepage, who deployed — cannot be re-scored from a saved report without it, and
re-running a suite to settle a boolean costs an hour. One recorded app-bench case stayed unsettled
for exactly this reason: a prefix of tool *names* is not an answer."""

import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.harness.scorers import delegation_only_scorer


def _call(name: str, call_id: str) -> ToolInvocation:
    return ToolInvocation(name=name, input={}, result="{}", has_result=True, call_id=call_id)


@pytest.mark.asyncio
async def test_the_evidence_names_the_parents_own_call_ids() -> None:
    parent = _call("spawn", "p1")
    child = _call("write", "c1")
    output = CapabilityOutput(response="", calls=(parent, child), own_calls=(parent,))

    verdict = await delegation_only_scorer(("write",))(output)

    assert verdict.evidence["ownCallIds"] == ["p1"]
    assert verdict.passed, verdict.reason
