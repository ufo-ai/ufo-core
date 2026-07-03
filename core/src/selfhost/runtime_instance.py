"""The scale-out boot guard and its heartbeat — the only instance-aware code in core.

Every live serve process holds a `runtime_instance` row it heartbeats. At boot the guard reads the
live peers (a stale row, past its heartbeat window, does not count): a second instance is safe only
when nothing it depends on is a dev default — SQLite, filesystem blobs, or an in-process hub cannot
be shared across processes, so an instance configured with any of them refuses to start when a peer
is live. With Postgres, a shared blob store, and a shared hub, instances coordinate through Postgres
and any number may run. The heartbeat is a per-process loop, not a shared job, so each instance
keeps only its own row fresh."""

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
    """The backend selection a peer can read off the row: which database, blob store, and hub this
    instance runs, so a divergent second instance is legible in the guard's refusal."""
    dialect = "sqlite" if config.database.url.startswith("sqlite") else "postgres"
    hub = "shared" if config.hub.shared else "in_process"
    return f"db={dialect};blob={config.blob.backend};hub={hub}"


def uses_dev_default(config: Config) -> bool:
    """A backend a second instance cannot safely share: SQLite (single-writer file), filesystem
    blobs (local disk), or the in-process hub (no cross-process fan-out)."""
    sqlite = config.database.url.startswith("sqlite")
    filesystem = config.blob.backend == "filesystem"
    return sqlite or filesystem or not config.hub.shared


@dataclass(frozen=True)
class BootGuard:
    """Admit this instance or refuse it. `admit` is the whole flow: read live peers, refuse when a
    dev default cannot be shared alongside them, else record this instance's row."""

    config: Config
    workspace_id: UUID
    instance_id: UUID

    async def admit(self) -> None:
        fingerprint = fingerprint_of(self.config)
        cutoff = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS)
        async with workspace_tx() as connection:
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
            if peers and uses_dev_default(self.config):
                listed = ", ".join(
                    f"{p.id} started {p.started_at} ({p.fingerprint})" for p in peers
                )
                raise RuntimeError(
                    f"a live instance is already running [{listed}]; this instance's backend "
                    f"({fingerprint}) has a dev default that cannot be shared — scale out needs "
                    f"Postgres, a shared blob store, and a shared hub"
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
    the seat free at once rather than waiting out the stale window."""

    instance_id: UUID
    workspace_id: UUID

    async def run(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.runtime_instance)
                    .values(heartbeat_at=sa.func.now(), updated_at=sa.func.now())
                    .where(tables.runtime_instance.c.id == self.instance_id)
                )

    async def retire(self) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.runtime_instance).where(
                    tables.runtime_instance.c.id == self.instance_id
                )
            )
