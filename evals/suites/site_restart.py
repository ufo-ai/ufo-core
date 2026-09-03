"""What an agent does when its hosted site is reported down.

The message under test is the ingress's own fire — `SITE_NOT_ANSWERING_FIRE`, which no member wrote
and no earlier turn set up. It states one fact (a port in this conversation's sandbox has nothing
listening on it) and asks for two acts, so the suite reads the trajectory for both: the agent looked
at what is actually running before touching anything, and it started the server again and confirmed
the port answers. A member is watching a page that reloads until that succeeds, which is why a reply
without a restart fails the case however well it reads.

The case stages a real server the agent can start and grades the sandbox commands, so the wording is
measured against behavior rather than against a rubric's opinion of it. The restraint half matters
as much: a stopped process is not new bytes, and an agent that answers this by rebuilding and
redeploying the site has spent a build to solve a `python3 server.py`.
"""

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ToolInvocation,
    WorkspaceFile,
)
from evals.harness.scorers import combine, restraint_scorer
from ufo.harness.sandbox.site_report import SITE_NOT_ANSWERING_FIRE

BASH_TOOL = "bash"
START_SERVER_TOOL = "start_server"
"""How the site tools actually bring a server up, and so what a restart looks like in a
trajectory. A bash command is accepted beside it, because a server the tool cannot express is
still a server — but a command merely *naming* the file is not a restart. A pgrep over
server.py is diagnosis, and an earlier draft of this grader passed on exactly that while
missing the real restart, which had gone through the tool."""
SITE_PORT = 39614
SERVER_PATH = "server.py"
DEPLOY_TOOL = "deploy_website"
"""The build the case must not need. The site's bytes are on disk and unchanged; what stopped is the
process serving them."""
WORKFLOW_WAIT_SECONDS = 600

SERVER_SOURCE = f"""from http.server import HTTPServer, SimpleHTTPRequestHandler

HTTPServer(("0.0.0.0", {SITE_PORT}), SimpleHTTPRequestHandler).serve_forever()
"""
INDEX_SOURCE = "<!doctype html><title>Team TODOs</title><h1>Team TODOs</h1>\n"

LOOKED = ("lsof", "netstat", "ss ", "ss -", "ps ", "pgrep", "curl", "wget")
"""Any of the ways the port or the process is actually inspected. A list rather than one command,
because which tool is present is the sandbox image's business and none of them is the behavior under
test — looking before acting is."""


def _command(call: ToolInvocation) -> str:
    return str(call.input.get("command", "")) if call.name == BASH_TOOL else ""


def _restart_index(output: CapabilityOutput) -> int | None:
    """Where the server was actually brought back up: the tool call that starts one, or a command
    that runs the file rather than one that only mentions it."""
    for index, call in enumerate(output.calls):
        if call.name == START_SERVER_TOOL:
            return index
        command = _command(call)
        if SERVER_PATH in command and "python" in command and "pgrep" not in command:
            return index
    return None


def _diagnosed_then_restarted_scorer() -> Grader:
    """Both halves the fire asks for, in order: the agent found out what was running before it
    changed anything, and then brought the server back up.

    Ordering is the assertion, not just presence. An agent that starts a server and only then looks
    has not diagnosed anything — it guessed, and the next report of the same site will find the same
    cause unexamined."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        looked = next(
            (
                index
                for index, call in enumerate(output.calls)
                if any(mark in _command(call) for mark in LOOKED)
            ),
            None,
        )
        started = _restart_index(output)
        if looked is None:
            return CapabilityVerdict(False, "nothing inspected the port or the running processes")
        if started is None:
            return CapabilityVerdict(False, "the server was never brought back up")
        if started < looked:
            return CapabilityVerdict(False, "the server was started before anything was inspected")
        return CapabilityVerdict(True, "")

    return DescribedGrader("the port or process is inspected, then the server is started", grade)


def _confirmed_scorer() -> Grader:
    """The check the fire asks for on top of the restart: something requested the port after the
    server was started. A started process is not an answering site, and the member's page is what
    decides — so an agent that starts a server and reports success without asking it for anything
    has not done the second half."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        started = _restart_index(output)
        if started is None:
            return CapabilityVerdict(False, "the server was never brought back up")
        fetched = any(
            str(SITE_PORT) in _command(call)
            and ("curl" in _command(call) or "wget" in _command(call))
            for call in output.calls[started + 1 :]
        )
        return CapabilityVerdict(
            fetched, "" if fetched else "the port was never requested after the restart"
        )

    return DescribedGrader("the port is requested after the restart", grade)


CASES = (
    CapabilityCase(
        "site-reported-down-is-restarted",
        SITE_NOT_ANSWERING_FIRE.format(port=SITE_PORT),
        combine(
            _diagnosed_then_restarted_scorer(),
            _confirmed_scorer(),
            restraint_scorer((DEPLOY_TOOL,)),
        ),
        workspace_files=(
            WorkspaceFile(SERVER_PATH, SERVER_SOURCE.encode()),
            WorkspaceFile("index.html", INDEX_SOURCE.encode()),
        ),
        rubric=(
            "The reply says the site is answering again, and says what had stopped.",
            "The reply asks the member no question back. Offering a further improvement is fine.",
        ),
        digest_tag=f"site-restart:reported-down:{SITE_PORT}",
    ),
)
