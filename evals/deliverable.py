"""Deliverable cases: build a small site, document, or piece of code, then answer with a fact read
back from the produced artifact — grading the artifact, not just the trajectory. `sites` and
`documents` have no chat tools of their own beyond the sandbox/skill builtins (sites contributes
`start_server`; documents is pure `load_skill` + `bash`/`write`/`read`), so their cases assert the
builtin tools the workflow actually requires. `coding` is a subagent profile: its case grades that
the agent delegated to it (`spawn_subagent` with profile "coding") and relayed the correct
result."""

from selfhost_ext_eval_harness.capability import CapabilityCase, Grader
from selfhost_ext_eval_harness.scorers import (
    combine,
    exact_scorer,
    lane_scorer,
    required_tools_scorer,
)

SPECS: list[tuple[str, str, Grader]] = [
    (
        "static-site-heading",
        "Create a static website at /workspace/site/index.html containing exactly one visible "
        "heading: an <h1> with the exact text 'Selfhost Status: Operational'. Serve it locally "
        "with start_server (for example `python3 -m http.server`) from that directory, then read "
        "index.html back to confirm the exact heading text before replying. Reply with a single "
        "line 'ANSWER: <the exact text inside the h1 tag>'.",
        combine(
            exact_scorer("Selfhost Status: Operational"),
            required_tools_scorer(("write", "start_server", "read"), (("write", "read"),)),
        ),
    ),
    (
        "one-page-pdf",
        "Create a one-page PDF at /workspace/notice.pdf whose only visible content is a heading "
        "with the exact text 'Board Meeting Notice'; set the PDF title metadata to the same text. "
        "Read the PDF back to confirm the heading before replying. Reply with a single line "
        "'ANSWER: <the exact heading text you read back from the PDF>'.",
        combine(
            exact_scorer("Board Meeting Notice"),
            required_tools_scorer(("load_skill", "write", "bash", "read")),
        ),
    ),
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
