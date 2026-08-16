"""The monitor runner: one probe per due monitor per tick, and the single fire that retires it.

It runs as the extension's recurring job, so it fires on the clock — never on the rows it writes,
and a monitor's own fired turn can only be observed by a re-arm, whose inline probe bakes that
turn's effects into the new baseline. Each tick claims the due monitors under a lease (an
overlapping tick never probes one twice) and, per row: a passed deadline fires whatever the streaks
say; otherwise the probe runs, and quiet output is counted and posted nowhere, changed output fires,
a third consecutive failure fires, and an unreachable sandbox is a counted skip. `next_probe_at`
always moves to one interval from this tick, so an overdue monitor — a deploy roll, a stalled
runner — probes once instead of replaying a backlog.

Each probe acts as the member who armed the watch, so a command reaching that member's own connected
account off-turn reaches it exactly as it did in the arming turn — the same authority a scheduled
fire carries for its creator. A monitor armed with no acting member reaches only the connections
shared with the whole workspace. A fire carries the armer's authority only while they hold a seat:
an unseated member's deadline arrival still lands, acting for nobody.

A fire invokes first and retires second: a crash between the two re-posts under the same
idempotency key, which admits nothing. A tick with failures raises their names."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ufo.sdk.context import ExtensionContext
from ufo.sdk.seats import Seats
from ufo.sdk.terminal import TerminalGone
from ufo.sdk.untrusted import wall
from ufo_ext_monitors.monitors import (
    PROBE_TIMEOUT_SECONDS,
    Monitor,
    MonitorStore,
    capped,
    stderr_tail,
)

CLAIM_LEASE_SECONDS = 300
FAILURE_THRESHOLD = 3
SPILL_DIR = ".monitors"
CHANGED = "changed"
FAILED = "failed"
DEADLINE = "deadline"
FIRED_OPEN = '<monitor_fired name="{name}" cause="{cause}">'
FIRED_CLOSE = "</monitor_fired>"
FIRED_CLOSE_ESCAPE = "&lt;/monitor_fired&gt;"
PROBE_SOURCE = "monitor:{name}"
FIRE_KEY_PREFIX = "monitor-fired:"


@dataclass(frozen=True)
class MonitorRunner:
    ctx: ExtensionContext
    lease_seconds: int = CLAIM_LEASE_SECONDS

    async def run(self) -> None:
        store = MonitorStore(self.ctx)
        now = datetime.now(UTC)
        failures: list[str] = []
        for row in await store.claim_due(now, self.lease_seconds):
            try:
                await self._tick(store, row)
            except Exception as raised:
                failures.append(f"{row.name} ({type(raised).__name__})")
        if failures:
            raise RuntimeError("monitor ticks failed: " + ", ".join(failures))

    async def _tick(self, store: MonitorStore, row: Monitor) -> None:
        """One claimed monitor's probe. Every clock read is this row's own: the interval is spacing
        between probes, so it is measured from the moment this probe finished and not from the tick
        that claimed it — a sweep of fifty rows each holding a probe open would otherwise leave the
        last of them due again the instant it was written."""
        spacing = timedelta(minutes=row.interval_minutes)
        if row.deadline_at <= datetime.now(UTC):
            await self._fire(store, row, DEADLINE, "", None, row.probes_run)
            return
        if not await self._acts_for_a_seated_member(row):
            await store.skipped_tick(row, datetime.now(UTC) + spacing)
            return
        if self.ctx.probes is None:
            raise RuntimeError("the monitor runner requires the probes capability; none is wired")
        try:
            probe = await self.ctx.probes.run(
                row.conversation_id,
                row.command,
                PROBE_TIMEOUT_SECONDS,
                acting_member_id=row.created_by_member_id,
            )
        except TerminalGone:
            await store.skipped_tick(row, datetime.now(UTC) + spacing)
            return
        probed_at = datetime.now(UTC)
        next_probe_at = probed_at + spacing
        if probe.exit_code != 0:
            if row.failure_streak + 1 < FAILURE_THRESHOLD:
                await store.failed_tick(row, probed_at, next_probe_at)
                return
            tail = stderr_tail(probe.stderr)
            await self._fire(
                store,
                row,
                FAILED,
                f"exit code: {probe.exit_code}" + (f"\n{tail}" if tail else ""),
                None,
                row.probes_run + 1,
            )
            return
        output = capped(probe.stdout)
        if output == row.baseline:
            await store.quiet_tick(row, probed_at, next_probe_at)
            return
        spill = None if output == probe.stdout else probe.stdout
        await self._fire(store, row, CHANGED, output, spill, row.probes_run + 1)

    async def _acts_for_a_seated_member(self, row: Monitor) -> bool:
        """Whether the watch may still act as the member who armed it. A probe runs a command
        off-turn under that member's forwarded connections and spends on every tick, so an admin's
        revoke stops their access everywhere at once: an unseated member's watch stops probing and
        the tick counts as a skip — the row stands, seating them again resumes it, and its deadline
        still ends the watch with the one arrival the arming turn is owed. The fire asks the same
        question, so that owed arrival lands acting for nobody rather than carrying an authority
        the revoke ended. A watch armed for nobody reaches only what the whole workspace shares,
        so there is no seat to ask about."""
        if row.created_by_member_id is None:
            return True
        async with self.ctx.transaction() as connection:
            return await Seats(self.ctx.workspace_id).admits(connection, row.created_by_member_id)

    async def _fire(
        self,
        store: MonitorStore,
        row: Monitor,
        cause: str,
        payload: str,
        spill: str | None,
        probes_run: int,
    ) -> None:
        if not await store.claim_holds(row):
            return
        acts_for = row.created_by_member_id if await self._acts_for_a_seated_member(row) else None
        await self.ctx.invoke(
            row.conversation_id,
            row.agent_id,
            await self._body(row, cause, payload, spill, probes_run),
            f"{FIRE_KEY_PREFIX}{row.id}",
            on_behalf_of_member_id=acts_for,
            holds_work_already_done=True,
        )
        await store.retire(row)

    async def _body(
        self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int
    ) -> str:
        """The fire the agent reads. `reason`, `next_steps`, and `metadata` are restated whole, so a
        fire landing after a compaction needs no earlier transcript, and the probe output goes
        through core's own `wall` — it is command output, and the wall escapes its closing delimiter
        inside the body so nothing the output contains can close it and continue as instructions."""
        lines = [
            FIRED_OPEN.format(name=row.name, cause=cause),
            f"reason: {row.reason}",
            f"next_steps: {row.next_steps}",
            f"metadata: {json.dumps(row.metadata)}",
            f"probes_run: {probes_run}  quiet_ticks: {row.quiet_streak}  skipped: {row.skipped}",
        ]
        if spill is not None:
            lines.append(f"full_output: {await self._spilled(row, spill)}")
        fired = "\n".join(lines).replace(FIRED_CLOSE, FIRED_CLOSE_ESCAPE) + f"\n{FIRED_CLOSE}"
        if not payload:
            return fired
        return f"{fired}\n" + wall(PROBE_SOURCE.format(name=row.name), payload)

    async def _spilled(self, row: Monitor, output: str) -> str:
        """Probe output past the fire's cap, landed in the conversation's workspace so the body can
        name a path the agent reads the whole of."""
        if self.ctx.files is None:
            raise RuntimeError("a monitor fire over the output cap requires conversation files")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return await self.ctx.files.write(
            row.conversation_id, f"{SPILL_DIR}/{row.name}-{stamp}.txt", output.encode()
        )
