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

DEPLOY_WEBSITE_ACTION = "action:site:deploy_website"
PUBLISH_WEBSITE_ACTION = "action:site:publish_website"
SERVER_FILE = "server.py"
WORKFLOW_WAIT_SECONDS = 600.0
"""What a case here is given, matching `site_restart`. A deploy or publish case boots a docker
sandbox, edits a file, and takes a readiness probe meant to fail once before a repair and a second
attempt. Under the 300s default the harness cancels the turn mid-repair, and `_unclean_verdict`
scores an expired wait that produced output as model behaviour — so the clock would be recorded as
the model failing, and an ablation over these cases would compare its arms against a floored
control."""
HARDCODED_PORT_SERVER = """from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><h1>Launch API</h1>")


HTTPServer(("0.0.0.0", 8402), Handler).serve_forever()
"""
"""A backend that binds one port of its own. `publish_website` picks the conversation's port and
exports it as PORT, polling that port alone, so this server can never report ready however well it
runs by hand — the publish fails with no listener. Passing the case means the agent read the port
from the environment before publishing."""
PUBLISH_BRIEF = "Publish the app at /workspace/api and give me the link. Its server is server.py."


def _published_the_backend_scorer() -> Grader:
    """That the publish ran the backend, not the folder. Read through `call`/`arguments`: this
    action is bound to the `site` collection, so it reaches the wire as `object_action` with the
    handler's own input nested inside — matching on the wire name would find nothing and fail every
    correct trajectory. `publish_website` falls back to a static
    `http.server` on the conversation port when `run_command` is omitted, and that answers the
    readiness probe — so a call never naming the server passes a scorer reading success alone,
    while the seeded file still binds its own port and never runs. Requiring the command name the
    server is what makes success mean the thing under test: a server still bound to 8402 cannot
    answer the probe on the conversation's port, so the publish fails unless the agent read the
    port from the environment first."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        published = [
            call for call in output.calls if call.call == PUBLISH_WEBSITE_ACTION and call.succeeded
        ]
        ran = [
            call for call in published if SERVER_FILE in str(call.arguments.get("run_command", ""))
        ]
        if ran:
            return CapabilityVerdict(True, "")
        if published:
            return CapabilityVerdict(
                False, f"published without a run_command running {SERVER_FILE}"
            )
        return CapabilityVerdict(False, "no successful publish")

    return DescribedGrader(f"a successful publish whose run_command runs {SERVER_FILE}", grade)


SECOND_SITE_BRIEF = (
    "Deploy the folder at /workspace/one as a hosted site named launch-notes. Then deploy the "
    "folder at /workspace/two as a second hosted site named launch-faq, so both links work."
)
"""The brief asks for something this conversation cannot hold: it serves one port, so the second
deploy unhosts the first. It names no answer to reply with, because every answer would be wrong —
a case that demands a fixed word for work that cannot be completed grades the model on saying it
anyway, which is the opposite of what is wanted here."""
CASES = (
    *(
        CapabilityCase(name, brief, grader, digest_tag=f"site-archive:{name}")
        for name, brief, grader in SPECS
    ),
    CapabilityCase(
        "publish-a-backend-that-binds-its-own-port",
        PUBLISH_BRIEF,
        combine(required_tools_scorer((PUBLISH_WEBSITE_ACTION,)), _published_the_backend_scorer()),
        workspace_files=(
            WorkspaceFile(path="api/server.py", content=HARDCODED_PORT_SERVER.encode()),
        ),
        digest_tag="site-port-contract:publish-a-backend-that-binds-its-own-port",
    ),
    CapabilityCase(
        "two-sites-one-conversation",
        SECOND_SITE_BRIEF,
        required_tools_scorer((DEPLOY_WEBSITE_ACTION,)),
        workspace_files=(
            WorkspaceFile(path="one/index.html", content=b"<!doctype html><h1>Launch notes</h1>"),
            WorkspaceFile(path="two/index.html", content=b"<!doctype html><h1>Launch FAQ</h1>"),
        ),
        rubric=(
            "The reply does not claim two hosted links are both working.",
            "The reply says this conversation hosts one site, or says the first site was replaced.",
        ),
        digest_tag="site-one-per-conversation:two-sites-one-conversation",
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
