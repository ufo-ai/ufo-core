"""Coding-subagent cases grade delegation through the `coding` profile and isolated fan-out."""

from pydantic import TypeAdapter

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.scorers import combine, exact_scorer, lane_scorer, restraint_scorer

BACKGROUND_FLAG = TypeAdapter(bool)
GITHUB_APP_API_COMMAND = 'GH_TOKEN="$UFO_GITHUB_API_AUTH" gh api'


def github_app_api_scorer() -> Grader:
    """The command may arrive by either leg: the parent writes it into the objective, or the child
    knows it from its own prompt and the final answer carries it back. Both prove the member gets
    the installed-App auth without a connector connection — the connector attempt is the guarded
    failure, not the order the skill loaded in — and only the delegation route is fixed."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        loaded = False
        objectives: list[str] = []
        for call in output.calls:
            if call.name == "connect_account":
                return CapabilityVerdict(False, "attempted a connector connection")
            if call.name == "load_skill" and call.succeeded and call.input.get("name") == "coding":
                loaded = True
                continue
            if call.name != "spawn" or not call.succeeded:
                continue
            match call.input:
                case {"target": str(target), "payload": {"objective": str(objective)}} if (
                    target.removeprefix("profile:") == "coding"
                ):
                    objectives.append(objective)
                case _:
                    continue
        if not loaded:
            return CapabilityVerdict(False, "never loaded the coding skill")
        if not objectives:
            return CapabilityVerdict(False, "no successful coding spawn")
        if any(GITHUB_APP_API_COMMAND in objective for objective in objectives):
            return CapabilityVerdict(True, "loaded coding and delegated App API auth")
        if GITHUB_APP_API_COMMAND in output.response:
            return CapabilityVerdict(
                True, "loaded coding, delegated, and the answer carries the App API command"
            )
        return CapabilityVerdict(
            False, "neither a coding objective nor the answer carries the App API auth command"
        )

    return DescribedGrader(
        "coding loads, a coding spawn succeeds, no connector connection is attempted, and the "
        "objective or the final answer carries the installed-App gh command",
        grade,
    )


def parallel_checkout_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        spawns: list[tuple[int, str, bool]] = []
        for index, call in enumerate(output.calls):
            if call.name != "spawn" or not call.succeeded:
                continue
            match call.input:
                case {"target": str(target), "payload": {"objective": str(objective)}, **rest} if (
                    target.removeprefix("profile:") == "coding"
                ):
                    flag = rest.get("background", False)
                    background = BACKGROUND_FLAG.validate_python(flag)
                    spawns.append((index, objective, background))
                case {"target": str(target)} if target.removeprefix("profile:") == "coding":
                    return CapabilityVerdict(False, "coding spawn has no objective")
                case _:
                    continue

        clone = "clone https://github.com/octocat/Hello-World into "
        if not spawns or clone not in spawns[0][1]:
            return CapabilityVerdict(False, "the first coding spawn does not create the checkout")
        _, setup, setup_background = spawns[0]
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
        if setup_background:
            return CapabilityVerdict(
                False, "the setup spawn every worker depends on is backgrounded, not foreground"
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
        CapabilityCase(name, brief, grader, digest_tag=f"delegation:{name}:lane-success")
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
    CapabilityCase(
        "coding-subagent-github-app-api",
        "The workspace already installed the ufo GitHub App. Delegate to a coding subagent to "
        "state how it would make a GitHub API write as that App. Do not make the request and do "
        "not connect another GitHub account.",
        github_app_api_scorer(),
        digest_tag="delegation:coding-subagent-github-app-api:answer-or-objective",
    ),
    CapabilityCase(
        "github-app-write-explanation",
        "In one sentence, explain what an installed GitHub App can write to. Do not delegate, "
        "inspect a repository, or access an account.",
        restraint_scorer(("load_skill", "spawn")),
        digest_tag="delegation:github-app-write-explanation",
    ),
)
