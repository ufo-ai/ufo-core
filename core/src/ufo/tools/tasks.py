"""The detached task journal every tool's shell work rides: the command is launched detached and
waited on, so one still running when the wait expires keeps running and the caller is handed the
handles it is reached by. `bash` and the REPLs run through it, so a foreground budget means one
thing in each — how long the caller waits, never how long the work may take — and a command that
outgrew it is reported the same way whichever tool asked.

The journal is the workspace files a task is named by (`.tasks/<id>.log`, `.pid`, `.exit`): the
wrapper writes them, so the result of a launch survives the exec that made it and a dispatch step
re-running after a crash reads the first run's outcome instead of repeating the command."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from ufo.o11y import log, turn_profile
from ufo.sandbox.session import WORKSPACE_DIR, ExecResult
from ufo.tools.context import ToolContext

MAX_COMMAND_TIMEOUT_MS = 600_000
EXEC_TIMEOUT_VITALS_SECONDS = 5
EXEC_TIMEOUT_COMMAND_MAX_CHARS = 200
EXEC_TIMEOUT_VITALS_CMD = "cat /proc/loadavg; free -m | tail -2; df -P /workspace | tail -1"
BACKGROUND_TASKS_DIR = ".tasks"
TASK_SWEEP_MINUTES = 60
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
TASK_BASH = 'exec bash -c "$1" bash "$2" "$3" "$4"'
"""Hand the launch to bash, replacing the shell rather than nesting inside it. Everything below
rests on `set -m`, and dash — `/bin/sh` on the sandbox image — refuses monitor mode where there is
no tty (`can't access tty; job control turned off`), which would leave every job in the launcher's
own process group. From here down each part rides argv: the script, the task's base path and the
command are positional parameters at every hop, so a command holding the delimiter and a workspace
path holding a quote both arrive verbatim."""
TASK_WRAPPER = (
    'set -m; bash -lc "$2" > "$1.log" 2>&1 & child=$!; '
    'trap "kill -- -$child 2>/dev/null" TERM INT; '
    'echo $$ > "$1.pid"; '
    'wait $child; echo $? > "$1.exit"'
)
"""The command's own parent: it redirects the command — not itself — into the log, so the log
carries the command's interleaved output while the shell's job notices go nowhere, and it writes
the exit code exactly once whether the command ended on its own or was stopped. `set -m` gives the
command a process group of its own, so the trap's `kill -- -$child` reaches the descendants doing
the work rather than the shell in front of them. The command runs under the login shell every bash
call has always used, whose profile is what puts its tools on PATH. The wrapper writes its own pid
only after the trap is armed, so no reader ever holds a pid whose kill would land before the
trap — a stop the instant the handles return still signals."""
TASK_LAUNCH = (
    'dir=$(dirname "$2"); mkdir -p "$dir" || exit 1; '
    f'find "$dir" -maxdepth 1 -name "*.exit" -mmin +{TASK_SWEEP_MINUTES} 2>/dev/null | '
    'while read -r f; do rm -f "${f%.exit}.log" "${f%.exit}.pid" "$f"; done; '
    'if [ ! -e "$2.pid" ]; then '
    'set -m; nohup bash -c "$1" bash "$2" "$3" >/dev/null 2>&1 & task=$!; set +m; '
    "fi; "
    'for _ in $(seq 500); do [ -s "$2.pid" ] && break; sleep 0.01; done; '
)
"""Launch once per task, however many times the exec runs: the pid file is the record that this
task's command is already launched, so a dispatch step re-running after a crash reattaches to the
work instead of repeating it — the sandbox outlives the process that asked, and the launch script
runs sandbox-side to completion whatever happens to the host, so the pid file and the launch are
one fate. The wrapper starts in a process group of its own, which is what lets it outlive this
launcher: a carrier ends an exec by killing the launcher's whole group, so a wrapper sharing that
group dies with it and the exit code no reader would ever see (`set +m` immediately after keeps
monitor mode's job notice off the result). The wrapper writes the pid file itself, past its trap,
and the launch is complete only when it appears — so no reader ever holds a pid whose kill would
land before the trap, and a reattach passes straight through. Completed tasks older than the sweep
window go here, the one place every task passes through — the window outlives any crash recovery,
so a journal is never swept before its reader arrives, and a task still running keeps its files
whatever its age."""
TASK_WAIT = (
    'if [ -n "$task" ]; then wait "$task"; else '
    'until [ -e "$2.exit" ] || ! kill -0 "$(cat "$2.pid")" 2>/dev/null; do sleep 0.2; done; fi; '
    'code=$(cat "$2.exit" 2>/dev/null) || { echo "the command ended without an exit code" >&2; '
    'exit 1; }; cat "$2.log"; exit "$code"'
)
"""Answer as the command itself — its output, its exit code — so a command that fits its budget
costs the single exec it always did. A launch this exec performed is waited on directly, since the
launcher is its parent; a reattached one belongs to a dead exec, so the wait falls back to watching
for the exit file while the wrapper lives. The files stay: they are the journal a re-run of the
same call reads its result from, gone only through the launch-time sweep."""
TASK_DETACH = 'cat "$2.pid"'
TASK_PROBE = (
    'pid=$(cat "$1.pid" 2>/dev/null) || exit 1; '
    'if [ -e "$1.exit" ] || kill -0 "$pid" 2>/dev/null; then printf %s "$pid"; fi'
)
"""Whether the work outlived the exec that launched it, answered by the wrapper's own pid: alive,
or already past its exit file. Silence is the sandbox failing to run the command at all, which is
the one reading that must still be reported as an error."""


@dataclass(frozen=True, slots=True)
class TaskRun:
    """One journal-launched command and what ended it. `pid` is the wrapper's, set only where the
    wait expired with the work still alive — the reading that separates a command that outgrew its
    budget from a sandbox that never ran it, which no exit code can carry. `requested_s` is what
    the caller asked for before the cap, so a notice names a request the cap reduced at the moment
    it costs something."""

    task_id: str
    result: ExecResult
    requested_s: int | None
    pid: str | None


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
    result = await ctx.sandbox.sh(
        TASK_BASH,
        TASK_LAUNCH + TASK_WAIT,
        TASK_WRAPPER,
        task_base(task),
        command,
        timeout_s=timeout_s,
    )
    if result.timed_out_after_s is None:
        return TaskRun(task_id=task, result=result, requested_s=requested_s, pid=None)
    probe = await ctx.sandbox.sh(TASK_PROBE, task_base(task), timeout_s=TASK_PROBE_TIMEOUT_SECONDS)
    pid = probe.stdout.strip() if probe.exit_code == 0 else ""
    if not pid:
        await _record_exec_timeout(ctx, command, result.timed_out_after_s, requested_s)
    return TaskRun(task_id=task, result=result, requested_s=requested_s, pid=pid or None)


def task_id(ctx: ToolContext) -> str:
    """The task's identity across attempts: a dispatch step re-running after a crash carries the
    same idempotency key, so it names the same task and reattaches to its files instead of
    launching the command again. No key — a context outside a recorded dispatch — is a fresh task
    every time, since there is no earlier attempt to reattach to."""
    if ctx.idempotency_key is None:
        return uuid4().hex[:8]
    return hashlib.sha256(ctx.idempotency_key.encode()).hexdigest()[:8]


def task_base(task: str) -> str:
    """Every path the launcher and the result name is absolute: an exec's working directory is
    carrier-dependent (Docker sets none), and the workspace root is the one anchor every carrier
    shares."""
    return f"{WORKSPACE_DIR}/{BACKGROUND_TASKS_DIR}/{task}"


def task_handles(task: str, pid: str, applied_s: int | None = None, note: str = "") -> str:
    """The handles a detached command is reached by, whichever way it got there — one text for a
    command detached on request and one that outgrew its wait, so the two can never drift apart.
    `applied_s` is the wait that expired, naming the seconds that actually applied; unset is a
    command detached from the start. `note` is what the asking tool must add about its own state,
    placed where it is read before the standing directive.

    The pid is the wrapper's, so the advertised stop ends the work AND still writes the exit file —
    the completion signal fires exactly once whether the command finished or was stopped."""
    lead = DETACHED_LEAD if applied_s is None else MOVED_LEAD.format(applied_s=applied_s)
    base = task_base(task)
    payload = {
        "task": task,
        "pid": pid,
        "log": f"{base}.log",
        "exit_file": f"{base}.exit",
        "watch": f'cat "{base}.exit" || true',
        "stop": f'kill "$(cat "{base}.pid")"',
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
