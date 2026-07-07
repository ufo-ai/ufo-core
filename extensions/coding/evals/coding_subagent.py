"""Coding-subagent case: `coding` is a subagent profile, so its case grades that the agent
delegated to it (`spawn_subagent` with profile "coding") and relayed the correct result back —
grading the delegation lane, not just the answer."""

from ufo_ext_eval_harness.capability import CapabilityCase, Grader
from ufo_ext_eval_harness.scorers import combine, exact_scorer, lane_scorer

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

CASES = tuple(
    CapabilityCase(name, brief, grader, digest_tag=f"deliverable:{name}")
    for name, brief, grader in SPECS
)
