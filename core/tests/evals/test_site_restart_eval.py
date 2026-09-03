"""The site-restart graders, against the trajectories they have to tell apart.

The restart and the check are read off the calls that perform them, and the confusions worth
guarding are the ones where a trajectory *mentions* the act without doing it: a `pgrep` over the
server file names it and starts nothing, and a request to the port before the restart proves only
that the site was down. Either read as a pass would score a run that left the member's page
reloading forever, so each has a case here.
"""

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.site_restart import CASES, SITE_PORT, START_SERVER_TOOL

GRADER = CASES[0].grader
LOOKED = f"lsof -nP -iTCP:{SITE_PORT} -sTCP:LISTEN"
CONFIRMED = f"curl -sS -o /dev/null -w '%{{http_code}}' http://localhost:{SITE_PORT}/"


def _bash(command: str) -> ToolInvocation:
    return ToolInvocation(name="bash", input={"command": command})


def _start_server() -> ToolInvocation:
    return ToolInvocation(
        name=START_SERVER_TOOL,
        input={"command": "python3 server.py", "project_path": "/workspace", "port": SITE_PORT},
    )


def _output(*calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response="The site is answering again.", calls=calls)


async def test_the_measured_trajectory_passes() -> None:
    """The shape a real run produced: look, restart through the tool, ask the port for a page."""
    verdict = await GRADER(_output(_bash(LOOKED), _start_server(), _bash(CONFIRMED)))
    assert verdict.passed, verdict.reason


async def test_a_bash_started_server_passes() -> None:
    """A server the tool cannot express is still a server, so running it counts."""
    verdict = await GRADER(_output(_bash("cd /workspace && python3 server.py &"), _bash(CONFIRMED)))
    assert verdict.passed, verdict.reason


async def test_naming_the_server_file_without_running_it_is_not_a_restart() -> None:
    """The miss this file exists for. `pgrep` over the file is diagnosis; a run that only diagnosed
    and confirmed the port was already answering restarted nothing."""
    verdict = await GRADER(_output(_bash('pgrep -fl "server.py|http.server"'), _bash(CONFIRMED)))
    assert not verdict.passed
    assert "never brought back up" in verdict.reason


async def test_a_restart_nobody_checked_fails() -> None:
    """A started process is not an answering site, and the member's reloading page is what
    decides."""
    verdict = await GRADER(_output(_bash(LOOKED), _start_server()))
    assert not verdict.passed
    assert "never requested after the restart" in verdict.reason


async def test_a_check_before_the_restart_does_not_count_as_the_check() -> None:
    """The request has to follow the restart. One that preceded it proves only that the site was
    down, which the fire already said."""
    verdict = await GRADER(_output(_bash(CONFIRMED), _start_server()))
    assert not verdict.passed
    assert "never requested after the restart" in verdict.reason


async def test_answering_without_doing_anything_fails() -> None:
    """A reply is not a repair, however well it reads."""
    verdict = await GRADER(_output())
    assert not verdict.passed
