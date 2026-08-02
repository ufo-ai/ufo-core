"""Coding-subagent cases grade delegation through the `coding` profile and isolated fan-out."""

from pydantic import TypeAdapter

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.scorers import combine, exact_scorer, lane_scorer

BACKGROUND_FLAG = TypeAdapter(bool)


def parallel_checkout_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawns: list[tuple[int, str, bool]] = []
        waits: list[int] = []
        for index, call in enumerate(output.calls):
            if call.name == "wait_for_subagents" and call.succeeded:
                waits.append(index)
                continue
            if call.name != "spawn_subagent" or not call.succeeded:
                continue
            match call.input:
                case {"profile": "coding", "payload": {"objective": str(objective)}, **rest}:
                    flag = rest.get("background", False)
                    background = BACKGROUND_FLAG.validate_python(flag)
                    spawns.append((index, objective, background))
                case {"profile": "coding"}:
                    return CapabilityVerdict(False, "coding spawn has no objective")
                case _:
                    continue

        clone = "clone https://github.com/octocat/Hello-World into "
        if not spawns or clone not in spawns[0][1]:
            return CapabilityVerdict(False, "the first coding spawn does not create the checkout")
        setup_index, setup, setup_background = spawns[0]
        if (
            "verify the checkout, report the checked-out branch as the base, then finish without "
            "task work" not in setup
        ):
            return CapabilityVerdict(False, "the setup coding spawn also receives task work")
        source_tail = setup.split(clone, 1)[1]
        if " with git" not in source_tail:
            return CapabilityVerdict(False, "the setup checkout path has no terminator")
        source = source_tail.split(" with git", 1)[0]

        marker = f"copy the committed tree at {source} to "
        workers = [spawn for spawn in spawns[1:] if marker in spawn[1]]
        if len(workers) < 2:
            return CapabilityVerdict(False, "fewer than two workers use local checkouts")
        first_worker_index = workers[0][0]
        if setup_background and not any(
            setup_index < wait_index < first_worker_index for wait_index in waits
        ):
            return CapabilityVerdict(
                False, "the backgrounded setup spawn is not awaited before the first worker"
            )
        tails = [objective.split(marker, 1)[1] for _, objective, _ in workers]
        if any(" with git" not in tail for tail in tails):
            return CapabilityVerdict(False, "a worker checkout path has no terminator")
        paths = [tail.split(" with git", 1)[0] for tail in tails]
        if len(paths) != len(set(paths)):
            return CapabilityVerdict(False, "workers share one local checkout")
        if any("base the work on " not in objective for _, objective, _ in workers):
            return CapabilityVerdict(False, "a worker does not name its base branch")
        if any("keep the source as workspace" not in objective for _, objective, _ in workers):
            return CapabilityVerdict(False, "a worker does not preserve the source remote")
        return CapabilityVerdict(True, f"setup plus {len(workers)} isolated workers")

    return DescribedGrader(
        "a setup coding spawn precedes at least two distinct local-checkout coding spawns", grade
    )


SPECS: list[tuple[str, str, Grader]] = [
    (
        "coding-subagent-palindrome",
        "Delegate to the coding subagent: write a Python function is_palindrome(s: str) -> bool "
        "(ignoring case and spaces) at /workspace/palindrome.py, then self-test it against "
        "'racecar' and 'hello' and report both boolean results. Reply with a single line "
        "'ANSWER: <result for racecar>,<result for hello>' (for example 'ANSWER: True,False').",
        combine(exact_scorer("True,False"), lane_scorer(frozenset({"coding"}))),
    ),
]

CASES = (
    *(
        CapabilityCase(name, brief, grader, digest_tag=f"delegation:{name}")
        for name, brief, grader in SPECS
    ),
    CapabilityCase(
        "coding-subagent-parallel-checkouts",
        "Use two coding subagents concurrently to inspect "
        "https://github.com/octocat/Hello-World. Have one report the tracked file count and the "
        "other report the current commit. Return both results.",
        parallel_checkout_scorer(),
        web_dependent=True,
        digest_tag="delegation:coding-subagent-parallel-checkouts",
    ),
)
