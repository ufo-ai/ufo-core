"""Site-build case: build, serve, inspect, archive, and share a small static site."""

from evals.harness.capability import CapabilityCase, Grader
from evals.harness.scorers import (
    combine,
    exact_scorer,
    required_tools_scorer,
    site_archive_scorer,
)

SPECS: list[tuple[str, str, Grader]] = [
    (
        "static-site-heading",
        "Create a static website at /workspace/site/index.html containing exactly one visible "
        "heading: an <h1> with the exact text 'Ufo Status: Operational'. Serve it locally "
        "with start_server from that directory, then read index.html back to confirm the exact "
        "heading text. Use bash and tar to archive the complete site as "
        "/workspace/site.tar.gz, and deliver that archive with share_file. Reply with a single "
        "line 'ANSWER: <the exact text inside the h1 tag>'.",
        combine(
            exact_scorer("Ufo Status: Operational"),
            required_tools_scorer(
                ("write", "start_server", "read", "bash", "share_file"),
                (
                    ("write", "start_server"),
                    ("start_server", "read"),
                    ("read", "bash"),
                    ("bash", "share_file"),
                ),
            ),
            site_archive_scorer("Ufo Status: Operational"),
        ),
    ),
]

CASES = tuple(
    CapabilityCase(name, brief, grader, digest_tag=f"site-archive:{name}")
    for name, brief, grader in SPECS
)
