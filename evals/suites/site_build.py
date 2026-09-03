"""Site-build cases: build, serve, inspect, archive, and share a small static site — the ephemeral
runtime staying global — and the media neighbor pair: a file already on disk is delivered with
`share_file` and never regenerated, while a picture that does not exist yet is generated through the
artifact collection's action and then delivered. The media pair is authored here; no recorded run
seeded it."""

import shlex
from pathlib import Path

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
)
from evals.harness.scorers import (
    combine,
    exact_scorer,
    required_tools_scorer,
    restraint_scorer,
    site_archive_scorer,
)

GENERATE_IMAGE = "action:artifact:generate_image"
GENERATE_VIDEO = "action:artifact:generate_video"
POSTER_PNG = (Path(__file__).parent / "site_build_poster.png").read_bytes()


INDEX_FILE = "index.html"
FILE_ARGUMENT_COMMANDS = frozenset({"cat", "head", "tail", "wc"})
PATTERN_ARGUMENT_COMMANDS = frozenset({"sed", "awk", "grep"})


def _names_index(argument: object) -> bool:
    text = str(argument)
    return text == INDEX_FILE or text.endswith(f"/{INDEX_FILE}")


def _shell_reads_index(command: str) -> bool:
    """Whether one bash command reads index.html itself, rather than printing text that matches
    it. A pattern command takes its file after the pattern, so its first argument is not a path."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    for token in lexer:
        if token and all(character in ";&|\n" for character in token):
            segments.append([])
        else:
            segments[-1].append(token)
    return any(
        segment
        and (
            (
                segment[0].rsplit("/", 1)[-1] in FILE_ARGUMENT_COMMANDS
                and any(_names_index(argument) for argument in segment[1:])
            )
            or (
                segment[0].rsplit("/", 1)[-1] in PATTERN_ARGUMENT_COMMANDS
                and any(
                    index >= 2 and _names_index(argument) for index, argument in enumerate(segment)
                )
            )
        )
        for segment in segments
    )


def _site_readback_scorer(expected: str) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        served = next(
            (
                index
                for index, call in enumerate(output.calls)
                if call.call == "start_server" and call.succeeded
            ),
            None,
        )
        shared = next(
            (
                index
                for index, call in enumerate(output.calls)
                if call.call == "share_file" and call.succeeded
            ),
            None,
        )
        if served is None or shared is None:
            return CapabilityVerdict(False, "site was not served and shared successfully")
        readback = any(
            call.succeeded
            and expected in call.result
            and (
                (call.call == "read" and _names_index(call.arguments.get("file_path", "")))
                or (
                    call.call == "bash"
                    and _shell_reads_index(str(call.arguments.get("command", "")))
                )
            )
            for call in output.calls[served + 1 : shared]
        )
        return CapabilityVerdict(
            readback,
            "read index.html after serving and before sharing"
            if readback
            else "did not read index.html back after serving",
        )

    return DescribedGrader(
        f"a successful read of index.html after serving returns {expected!r} before sharing",
        grade,
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
                ("start_server", "bash", "share_file"),
                (
                    ("start_server", "bash"),
                    ("bash", "share_file"),
                ),
            ),
            _site_readback_scorer("Ufo Status: Operational"),
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
