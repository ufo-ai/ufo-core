"""Site-build case: build a small static site, serve it locally, then answer with a fact read back
from the produced file — grading the artifact, not just the trajectory. `sites` has no chat tools of
its own beyond the sandbox/skill builtins (it contributes `start_server`), so the case asserts the
builtin tools the workflow actually requires."""

from ufo_ext_eval_harness.capability import CapabilityCase, Grader
from ufo_ext_eval_harness.scorers import combine, exact_scorer, required_tools_scorer

SPECS: list[tuple[str, str, Grader]] = [
    (
        "static-site-heading",
        "Create a static website at /workspace/site/index.html containing exactly one visible "
        "heading: an <h1> with the exact text 'Ufo Status: Operational'. Serve it locally "
        "with start_server (for example `python3 -m http.server`) from that directory, then read "
        "index.html back to confirm the exact heading text before replying. Reply with a single "
        "line 'ANSWER: <the exact text inside the h1 tag>'.",
        combine(
            exact_scorer("Ufo Status: Operational"),
            required_tools_scorer(("write", "start_server", "read"), (("write", "read"),)),
        ),
    ),
]

CASES = tuple(
    CapabilityCase(name, brief, grader, digest_tag=f"deliverable:{name}")
    for name, brief, grader in SPECS
)
