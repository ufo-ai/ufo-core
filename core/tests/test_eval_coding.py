import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.harness.coding import PinnedRepositoryRoute


def output(*, lane: str = "coding", command: str = "") -> CapabilityOutput:
    calls = (
        ToolInvocation(
            name="spawn",
            input={"target": lane, "payload": {"objective": "work"}},
            result='{"result": "done"}',
            has_result=True,
        ),
        ToolInvocation(
            name="bash",
            input={"command": command},
            result="",
            has_result=True,
        ),
    )
    return CapabilityOutput(response="done", calls=calls)


async def test_pinned_repository_route_requires_the_coding_lane_and_exact_fetch() -> None:
    gate = PinnedRepositoryRoute("sympy/sympy", "a" * 40)
    assert (await gate(output(command=f"git fetch --depth 1 origin {'a' * 40}"))).passed
    assert not (await gate(output(lane="research", command="a" * 40))).passed
    assert "no call fetches" in (await gate(output(command="git status"))).reason


@pytest.mark.parametrize(
    "command",
    (
        "git clone https://github.com/sympy/sympy.git",
        "curl https://github.com/sympy/sympy/archive/main.tar.gz",
        "gh api repos/sympy/sympy/contents/sympy/core.py",
        "curl https://raw.githubusercontent.com/sympy/sympy/main/sympy/core.py",
    ),
)
async def test_pinned_repository_route_rejects_history_leaks(command: str) -> None:
    gate = PinnedRepositoryRoute("sympy/sympy", "a" * 40)
    verdict = await gate(output(command=command))
    assert not verdict.passed
