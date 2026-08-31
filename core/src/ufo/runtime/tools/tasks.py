"""The detached task journal every tool's shell work rides: the command is launched detached and
waited on, so one still running when the wait expires keeps running and the caller is handed the
handles it is reached by. `bash` and the REPLs run through it, so a foreground budget means one
thing in each — how long the caller waits, never how long the work may take — and a command that
outgrew it is reported the same way whichever tool asked.

The journal lives under the conversation's `$UFO_HOME/runs/<id>/tasks`: `ufo run` writes it, so
the result of a launch survives the exec that made it and a dispatch step re-running after a crash
reads the first run's outcome instead of repeating the command."""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from uuid import uuid4

from ufo.harness.o11y import log, turn_profile
from ufo.harness.sandbox.session import ExecResult
from ufo.runtime.tools.context import ToolContext

MAX_COMMAND_TIMEOUT_MS = 600_000
FLAT_SLEEP_OR_LOOP = re.compile(r"(?<![\w./-])(?:sleep\s+(\d+)|(do|done))(?![\w./-])")
QUOTED_OR_HEREDOC = re.compile(r"'[^']*'|\"[^\"]*\"|<<-?\s*'?(\w+)'?[^\n]*\n[\s\S]*?\n\1(?=\s|$)")
FLAT_SLEEP_MAX_SECONDS = 10
FLAT_SLEEP_REFUSAL = (
    "Refused before running: the command pads the turn with a flat `sleep {seconds}`. Poll in a "
    "loop, cover the workload with `timeout`, or run it with `background: true` — the exit file "
    "appearing is the completion signal."
)


def flat_sleeps(command: str) -> tuple[int, ...]:
    """The sleeps the command would pad a foreground turn with: every sleep over
    `FLAT_SLEEP_MAX_SECONDS` outside a shell loop body. A poll loop's own sleep is the wait's
    signal and never padding, and a loop may share its line with the work it polls, so the loop
    body is skipped by `do`/`done` nesting depth rather than by line. Quoted arguments and heredoc
    bodies are data, not the turn's wait, so they are not scanned — a sleep smuggled into a nested
    shell string is the naive pattern's deliberate residual, chosen over refusing a command that
    merely mentions one."""
    hits: list[int] = []
    depth = 0
    for match in FLAT_SLEEP_OR_LOOP.finditer(QUOTED_OR_HEREDOC.sub(" ", command)):
        seconds, keyword = match.group(1), match.group(2)
        if keyword == "do":
            depth += 1
        elif keyword == "done":
            depth = max(depth - 1, 0)
        elif depth == 0:
            hits.append(int(seconds))
    return tuple(seconds for seconds in hits if seconds > FLAT_SLEEP_MAX_SECONDS)


EXEC_TIMEOUT_VITALS_SECONDS = 5
EXEC_TIMEOUT_COMMAND_MAX_CHARS = 200
EXEC_TIMEOUT_VITALS_CMD = "cat /proc/loadavg; free -m | tail -2; df -P /workspace | tail -1"
BACKGROUND_TASKS_DIR = "tasks"
TASK_PROBE_TIMEOUT_SECONDS = 5
DETACHED_LEAD = "The command runs detached."
MOVED_LEAD = "The command did not complete within its {applied_s}s timeout and continues detached."
BACKGROUND_DIRECTIVE = (
    "`read` on `log` shows the output so far. `exit_file` appears exactly once, with the exit "
    "code — `watch` is empty until then, so a monitor on it fires at completion; `stop` ends the "
    "command and still writes `exit_file`."
)
"""What holds of a detached command however it got there, so the lead sentence is the only thing
that differs between a command detached on request and one that outgrew its wait. The log is the
whole interface to a running command: it is read, never carried into the result, which would hand
back a prefix that is already stale."""
TASK_PROBE = (
    'pid=$(cat "$1.pid" 2>/dev/null) || exit 1; '
    'if [ -e "$1.exit" ] || kill -0 "$pid" 2>/dev/null; then printf %s "$pid"; fi'
)
"""Whether the work outlived the exec that launched it, answered by the supervisor's pid: alive,
or already past its exit file. Silence is the sandbox failing to run the command at all, which is
the one reading that must still be reported as an error."""


@dataclass(frozen=True, slots=True)
class TaskRun:
    """One journal-launched command and what ended it. `pid` is the supervisor's, set only where the
    wait expired with the work still alive — the reading that separates a command that outgrew its
    budget from a sandbox that never ran it, which no exit code can carry. `requested_s` is what
    the caller asked for before the cap, so a notice names a request the cap reduced at the moment
    it costs something."""

    task_id: str
    result: ExecResult
    requested_s: int | None
    pid: str | None
    display_base: str


async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None) -> TaskRun:
    """Launch one command through the journal and wait on it for the caller's budget. The command
    is detached from the start, which is what makes the move at the budget free: a command that
    fits answers as itself, for the single exec it always cost, and one still running when the wait
    expires is probed rather than killed.

    The task's identity is the call's, so the whole run is durable across a crash of this process: a
    dispatch step that re-runs on recovery derives the same task, finds the launch already made, and
    answers with the first run's result — the command itself runs once however many times the step
    does.

    A wait that expired with nothing left alive is the sandbox failing to run the command, and only
    there are the container's vitals read: what it looked like the moment it stopped answering."""
    requested_s = None if timeout_ms is None else int(timeout_ms / 1000)
    timeout_s = int(min(timeout_ms, MAX_COMMAND_TIMEOUT_MS) / 1000) if timeout_ms else None
    task = task_id(ctx)
    base = await ctx.sandbox.runtime_path(f"{BACKGROUND_TASKS_DIR}/{task}")
    display_base = await ctx.sandbox.runtime_display_path(f"{BACKGROUND_TASKS_DIR}/{task}")
    result = await ctx.sandbox.bash_task(command, base, detach=False, timeout_s=timeout_s)
    if result.timed_out_after_s is None:
        return TaskRun(
            task_id=task,
            result=result,
            requested_s=requested_s,
            pid=None,
            display_base=display_base,
        )
    probe = await ctx.sandbox.sh(TASK_PROBE, base, timeout_s=TASK_PROBE_TIMEOUT_SECONDS)
    pid = probe.stdout.strip() if probe.exit_code == 0 else ""
    if not pid:
        await _record_exec_timeout(ctx, command, result.timed_out_after_s, requested_s)
    return TaskRun(
        task_id=task,
        result=result,
        requested_s=requested_s,
        pid=pid or None,
        display_base=display_base,
    )


def task_id(ctx: ToolContext) -> str:
    """The task's identity across attempts: a dispatch step re-running after a crash carries the
    same idempotency key, so it names the same task and reattaches to its files instead of
    launching the command again. No key — a context outside a recorded dispatch — is a fresh task
    every time, since there is no earlier attempt to reattach to."""
    if ctx.idempotency_key is None:
        return uuid4().hex[:8]
    return hashlib.sha256(ctx.idempotency_key.encode()).hexdigest()[:8]


def task_handles(
    task: str, pid: str, display_base: str, applied_s: int | None = None, note: str = ""
) -> str:
    """The handles a detached command is reached by, whichever way it got there — one text for a
    command detached on request and one that outgrew its wait, so the two can never drift apart.
    `applied_s` is the wait that expired, naming the seconds that actually applied; unset is a
    command detached from the start. `note` is what the asking tool must add about its own state,
    placed where it is read before the standing directive.

    The pid is the supervisor's, so the advertised stop ends the work and writes the exit file —
    the completion signal fires exactly once whether the command finished or was stopped."""
    lead = DETACHED_LEAD if applied_s is None else MOVED_LEAD.format(applied_s=applied_s)
    payload = {
        "task": task,
        "pid": pid,
        "log": f"{display_base}.log",
        "exit_file": f"{display_base}.exit",
        "watch": f'cat "{display_base}.exit" || true',
        "stop": f'kill "$(cat "{display_base}.pid")"',
    }
    said = " ".join(part for part in (lead, note, BACKGROUND_DIRECTIVE) if part)
    return f"{said}\n{json.dumps(payload)}"


def timeout_notice(applied_s: int, requested_s: int | None) -> str:
    """The budget that fired, in the seconds that actually applied — `timeout` inside a command
    exits 124 exactly as a carrier-stopped one does, so a caller reading the code alone cannot tell
    which deadline ended the work. A request the cap reduced says so here, where it costs
    something, rather than silently at the call."""
    if requested_s is None:
        return (
            f"timed out: the sandbox stopped this command after {applied_s}s, the default when the "
            f"call sets no timeout. Set timeout (up to {MAX_COMMAND_TIMEOUT_MS // 1000}s) to allow "
            "longer work."
        )
    if requested_s > applied_s:
        return (
            f"timed out: the sandbox stopped this command after {applied_s}s, the maximum. The "
            f"{requested_s}s requested was capped."
        )
    return f"timed out: the sandbox stopped this command after {applied_s}s."


async def _record_exec_timeout(
    ctx: ToolContext, command: str, applied_s: int, requested_s: int | None
) -> None:
    """What the container looked like the moment it stopped a command, read from the container
    itself and bounded twice — by the probe's own deadline and by this wait — so a container that
    cannot answer costs seconds rather than a second full timeout.

    The probe failing is the reading that matters. A container merely busy still answers
    `cat /proc/loadavg` in milliseconds, so a probe that returns nothing says the command channel
    stopped answering rather than the work being slow, and those two have opposite fixes. The
    counter alone separates them nowhere: it carries the carrier and no command, no profile, and
    nothing about the container behind it.

    Diagnosis, never the outcome — every fault here is swallowed, because what the caller must
    still be told is the timeout its own command hit."""
    reached, vitals = False, ""
    try:
        async with asyncio.timeout(EXEC_TIMEOUT_VITALS_SECONDS):
            probe = await ctx.sandbox.bash(
                EXEC_TIMEOUT_VITALS_CMD, timeout_s=EXEC_TIMEOUT_VITALS_SECONDS
            )
        reached = probe.exit_code == 0
        vitals = " ".join((probe.stdout + probe.stderr).split())
    except Exception:
        reached, vitals = False, ""
    log(
        "sandbox.exec_timeout",
        profile=turn_profile(ctx.turn.subagent_profile, ctx.turn.spawned),
        applied_seconds=applied_s,
        requested_seconds=requested_s,
        command=command[:EXEC_TIMEOUT_COMMAND_MAX_CHARS],
        vitals_reached=reached,
        vitals=vitals,
    )
