"""Workspace candidates: the one RLS-bypass read that names the workspaces a job has work in.

A job never fires unbound. The dispatcher opens `with ws(id)` for each id a job's candidates name
and runs the handler scoped to it, so an empty candidate set fires the handler zero times and every
handler that does run is bound. A candidate read is the sole sanctioned use of `owner_tx` (the
cross-workspace, RLS-bypassing read): it returns workspace ids, never row data, and every id it
returns is re-bound under `ws(...)` before any work runs.

`owner_candidates` is the seam an extension declares its candidates through without reaching
`owner_tx` itself (a core internal the SDK boundary forbids it): the extension hands core a builder
of a `Select` projecting distinct `workspace_id` over its own tables, and core builds it on each
tick and runs it under `owner_tx`. Building per tick is what lets dueness be time-relative — a
builder computes `now` and embeds the cutoff, where a select constructed once at manifest load
would freeze it. A core sweep names its candidates the same shape — a zero-argument async callable
returning workspace ids — computing them however it needs."""

from collections.abc import Awaitable, Callable
from uuid import UUID

import sqlalchemy as sa

from ufo.db import owner_tx

WorkspaceCandidates = Callable[[], Awaitable[tuple[UUID, ...]]]


def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates:
    """Name the workspaces `due()` projects, read through `owner_tx` (the one RLS-bypass path).
    `due` builds, on each tick, a select projecting a single `workspace_id` column over the
    caller's own tables; core reads the first column of each row, so the caller declares which
    workspaces hold pending work without ever reaching the cross-workspace engine. The dispatcher
    binds each workspace before the handler runs, so the returned ids scope the work and the read
    itself yields no tenant data."""

    async def candidates() -> tuple[UUID, ...]:
        async with owner_tx() as connection:
            rows = (await connection.execute(due())).all()
        return tuple(row[0] for row in rows)

    return candidates
