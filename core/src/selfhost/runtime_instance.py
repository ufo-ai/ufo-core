"""The scale-out boot guard and its heartbeat — the only instance-aware code in core.

Every live serve process holds a `runtime_instance` row it heartbeats. At boot the guard reads the
live peers (a stale row, past its heartbeat window, does not count) and refuses to start when any
peer is live AND this instance runs the in-process hub, which has no cross-process fan-out, so a
second instance could never share its live stream. A shared-hub backend an extension registers
(`config.hub.backend` other than the in-process default) fans frames out across processes and so
lifts the refusal — multiple instances then run per workspace. The heartbeat is a per-process loop,
not a shared job, so each instance keeps only its own row fresh."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from selfhost.config import IN_PROCESS_BACKEND, Config
from selfhost.db import workspace_tx
from selfhost.o11y import log
from selfhost.schema import tables

HEARTBEAT_INTERVAL_SECONDS = 10
STALE_AFTER_SECONDS = 30


def fingerprint_of(config: Config) -> str:
    """The backend selection a peer can read off the row: which database and blob store this
    instance runs, and which hub backend — so a divergent second instance is legible in the guard's
    refusal, and a shared-hub instance is recognizable as one that may coexist."""
    dialect = "sqlite" if config.database.url.startswith("sqlite") else "postgres"
    return f"db={dialect};blob={config.blob.backend};hub={config.hub.backend}"


@dataclass(frozen=True)
class BootGuard:
    """Admit this instance or refuse it. `admit` is the whole flow: read live peers, refuse when a
    live peer exists and this instance carries a single-instance backend that cannot be shared
    across processes — the in-process hub (no cross-process fan-out), a filesystem blob store, or a
    SQLite database — naming the offenders; a deploy with all three shared (a shared hub, S3, and
    Postgres) coexists with peers, so scale-out lands with the shared-hub extension. Else record
    this instance's row. Two instances booting at once are serialized so the read-then-insert is
    atomic — a workspace-scoped advisory lock on Postgres, `begin immediate` on SQLite — so a race
    cannot admit both."""

    config: Config
    workspace_id: UUID
    instance_id: UUID

    async def admit(self) -> None:
        fingerprint = fingerprint_of(self.config)
        single_instance = self._single_instance_backends()
        cutoff = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS)
        async with workspace_tx() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    sa.text("select pg_advisory_xact_lock(hashtext(:ws))"),
                    {"ws": str(self.workspace_id)},
                )
            peers = (
                await connection.execute(
                    sa.select(
                        tables.runtime_instance.c.id,
                        tables.runtime_instance.c.started_at,
                        tables.runtime_instance.c.fingerprint,
                    ).where(
                        tables.runtime_instance.c.workspace_id == self.workspace_id,
                        tables.runtime_instance.c.heartbeat_at >= cutoff,
                    )
                )
            ).all()
            if peers and single_instance:
                listed = ", ".join(
                    f"{p.id} started {p.started_at} ({p.fingerprint})" for p in peers
                )
                raise RuntimeError(
                    f"a live instance is already running [{listed}]; this instance ({fingerprint}) "
                    f"carries single-instance backends ({', '.join(single_instance)}) that cannot "
                    "be shared across processes — select shared backends (a shared hub, S3, "
                    "Postgres) to scale out"
                )
            await connection.execute(
                sa.insert(tables.runtime_instance).values(
                    id=self.instance_id,
                    workspace_id=self.workspace_id,
                    started_at=sa.func.now(),
                    heartbeat_at=sa.func.now(),
                    fingerprint=fingerprint,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        log("instance.admitted", instance=str(self.instance_id), fingerprint=fingerprint)

    def _single_instance_backends(self) -> tuple[str, ...]:
        """The configured backends that have no cross-process story, named for the refusal. Empty
        means every backend is shareable and peers may coexist."""
        return (
            *(("in-process hub",) if self.config.hub.backend == IN_PROCESS_BACKEND else ()),
            *(("filesystem blob store",) if self.config.blob.backend == "filesystem" else ()),
            *(("sqlite database",) if self.config.database.url.startswith("sqlite") else ()),
        )


@dataclass(frozen=True)
class Heartbeat:
    """Keep this instance's row fresh on an interval, and drop it on graceful shutdown so peers see
    the seat free at once rather than waiting out the stale window. A transient database error on
    one tick is logged and the loop continues — a single failed update must not kill the heartbeat
    and let a healthy instance's row go stale, which would wrongly free the seat to a peer; only a
    sustained outage lets the row age out, which is the correct signal that the instance is gone."""

    instance_id: UUID
    workspace_id: UUID

    async def run(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.runtime_instance)
                        .values(heartbeat_at=sa.func.now(), updated_at=sa.func.now())
                        .where(tables.runtime_instance.c.id == self.instance_id)
                    )
            except sa.exc.SQLAlchemyError as error:
                log(
                    "instance.heartbeat_failed",
                    instance=str(self.instance_id),
                    error_class=type(error).__name__,
                )

    async def retire(self) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.runtime_instance).where(
                    tables.runtime_instance.c.id == self.instance_id
                )
            )
