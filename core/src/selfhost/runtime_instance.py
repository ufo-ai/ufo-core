"""The scale-out boot guard and its heartbeat — the only instance-aware code in core.

Every live serve process holds a `runtime_instance` row it heartbeats. At boot the guard reads the
live peers (a stale row, past its heartbeat window, does not count) and refuses to start when any
peer is live: core ships only the in-process hub, which has no cross-process fan-out, so a second
instance can never share it. Multi-instance deploys wait on a shared-hub extension; until one lands
core runs a single instance per workspace. The heartbeat is a per-process loop, not a shared job, so
each instance keeps only its own row fresh."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from selfhost.config import Config
from selfhost.db import workspace_tx
from selfhost.o11y import log
from selfhost.schema import tables

HEARTBEAT_INTERVAL_SECONDS = 10
STALE_AFTER_SECONDS = 30


def fingerprint_of(config: Config) -> str:
    """The backend selection a peer can read off the row: which database and blob store this
    instance runs, and its hub — always the in-process hub in core — so a divergent second instance
    is legible in the guard's refusal."""
    dialect = "sqlite" if config.database.url.startswith("sqlite") else "postgres"
    return f"db={dialect};blob={config.blob.backend};hub=in_process"


@dataclass(frozen=True)
class BootGuard:
    """Admit this instance or refuse it. `admit` is the whole flow: read live peers, refuse when any
    live peer exists — core ships only the in-process hub (no cross-process fan-out), so a workspace
    runs a single instance; the dev-default backends that can't be shared are named in the refusal's
    fingerprint — else record this instance's row. Two instances booting at once are serialized so
    the read-then-insert is atomic — a workspace-scoped advisory lock on Postgres, `begin immediate`
    on SQLite — so a race cannot admit both."""

    config: Config
    workspace_id: UUID
    instance_id: UUID

    async def admit(self) -> None:
        fingerprint = fingerprint_of(self.config)
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
            if peers:
                listed = ", ".join(
                    f"{p.id} started {p.started_at} ({p.fingerprint})" for p in peers
                )
                raise RuntimeError(
                    f"a live instance is already running [{listed}]; this instance ({fingerprint}) "
                    f"carries a dev default that cannot be shared across processes — core's "
                    f"in-process hub has no cross-process fan-out, so it runs a single instance "
                    "per workspace"
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
