"""Site-build cases: build, serve, inspect, archive, and share a small static site — the ephemeral
runtime staying global — and the media neighbor pair: a file already on disk is delivered with
`share_file` and never regenerated, while a picture that does not exist yet is generated through the
artifact collection's action and then delivered. The media pair is authored here; no recorded run
seeded it."""

from evals.harness.capability import CapabilityCase, Grader, WorkspaceFile
from evals.harness.scorers import (
    combine,
    exact_scorer,
    required_tools_scorer,
    restraint_scorer,
    site_archive_scorer,
)

GENERATE_IMAGE = "action:artifact:generate_image"
GENERATE_VIDEO = "action:artifact:generate_video"
POSTER_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
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

CASES = (
    *(
        CapabilityCase(name, brief, grader, digest_tag=f"site-archive:{name}")
        for name, brief, grader in SPECS
    ),
    CapabilityCase(
        "share-the-poster-on-disk",
        "The poster at /workspace/poster.png is finished. Send it to me, then reply with a single "
        "line 'ANSWER: sent'.",
        combine(
            exact_scorer("sent"),
            required_tools_scorer(("share_file",)),
            restraint_scorer((GENERATE_IMAGE, GENERATE_VIDEO)),
        ),
        workspace_files=(WorkspaceFile(path="poster.png", content=POSTER_PNG),),
        digest_tag="site-media:authored:share-the-poster-on-disk",
    ),
    CapabilityCase(
        "generate-then-share-a-poster",
        "Draw one poster of a red panda astronaut in a studio, save it as poster, send it to me, "
        "then reply with a single line 'ANSWER: sent'.",
        combine(
            exact_scorer("sent"),
            required_tools_scorer(
                (GENERATE_IMAGE, "share_file"), ((GENERATE_IMAGE, "share_file"),)
            ),
        ),
        web_dependent=True,
        digest_tag="site-media:authored:generate-then-share-a-poster",
    ),
)
