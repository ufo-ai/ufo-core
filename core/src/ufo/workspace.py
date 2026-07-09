"""The workspace scope: the one ambient handle every credentialed, billed, or workspace-scoped call
goes through — identical whether it runs in a turn or a job.

`with ws(workspace_id):` binds a workspace for the block; `ws_current()` returns it and raises
`WorkspaceUnbound` if none is bound. A secret is reachable only as `ws_current().credential(slot)` —
the workspace's stored BYOK value, else the platform default from env — so a key is never fetched
without naming the workspace that holds it, and never leaks across one. Spend is booked only through
`with ws_current().billable_event() as bill: bill.usage(...)`, written to that same workspace when
the block succeeds and dropped if it raises. The workspace is never an argument: binding it once at
the turn or job boundary scopes credentials, billing, and RLS within — so a job bills the same
workspace the same way a turn does, by construction."""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from uuid import UUID

from ufo.accounting import CORE_PRICING, Pricing, record_workspace_usage
from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import current_workspace, workspace_tx
from ufo.schema.records import Usage

_store: CredentialStore | None = None


def init_workspace_credentials(store: CredentialStore | None) -> None:
    """Install the deploy's credential store once at boot; `ws_current().credential` resolves BYOK
    through it. None (no credential key set) leaves only the platform env defaults resolvable."""
    global _store
    _store = store


class WorkspaceUnbound(RuntimeError):
    """A credentialed, billed, or workspace-scoped call ran with no workspace bound — a handle used
    outside the `with ws(...)` block a turn or job establishes. Fails loud, never a silent
    cross-workspace read or an unbilled call."""


@dataclass
class BillableEvent:
    """Spend accrued inside one `billable_event()` block. Add model usage as the calls happen;
    nothing is written until the block exits cleanly, so a call that raises is never billed."""

    _usages: list[tuple[str, Usage, Pricing]] = field(default_factory=list)

    def usage(self, model: str, usage: Usage, pricing: Pricing = CORE_PRICING) -> None:
        """Accrue one metered model call to book when the block succeeds."""
        self._usages.append((model, usage, pricing))


@dataclass(frozen=True)
class WorkspaceScope:
    """The bound workspace, and the only handle to its secrets and its spend ledger."""

    workspace_id: UUID

    async def credential(self, slot: str, env: str | None = None) -> str:
        """This workspace's secret for `slot`: its stored BYOK value if set, else the platform
        default read live from env (`env` name, or `SLOT` upper-cased). The single path to any
        secret — fetching one asserts a bound workspace, so a key is always the right workspace's.
        Set in neither place raises `CredentialSlotUnset`, so a missing key fails the call needing
        it, never silently."""
        if _store is not None:
            try:
                return await _store.get(self.workspace_id, slot)
            except CredentialSlotUnset:
                pass
        value = os.environ.get(env or slot.upper())
        if value is None:
            raise CredentialSlotUnset(slot)
        return value

    @asynccontextmanager
    async def billable_event(self) -> AsyncIterator[BillableEvent]:
        """Book spend to this workspace when the block succeeds. Accrue model usage inside; on clean
        exit it is written to the workspace ledger (turn_id NULL, workspace-anchored), and an
        exception drops it unwritten — so a failed call is never billed."""
        event = BillableEvent()
        yield event
        if not event._usages:
            return
        async with workspace_tx() as connection:
            for model, usage, pricing in event._usages:
                await record_workspace_usage(connection, self.workspace_id, model, usage, pricing)


@contextmanager
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]:
    """Bind `workspace_id` as the ambient workspace for the block — the RLS scope every
    `workspace_tx` within pins, and the workspace every `ws_current()` within resolves. A turn or a
    job establishes it once at its boundary; everything inside is scoped without passing it."""
    token = current_workspace.set(workspace_id)
    try:
        yield WorkspaceScope(workspace_id)
    finally:
        current_workspace.reset(token)


def ws_current() -> WorkspaceScope:
    """The bound workspace scope, or raise `WorkspaceUnbound` — so a credentialed or billed call
    outside a `with ws(...)` block fails loud rather than reading or billing the wrong workspace."""
    workspace_id = current_workspace.get()
    if workspace_id is None:
        raise WorkspaceUnbound("no workspace bound; wrap the call in `with ws(workspace_id):`")
    return WorkspaceScope(workspace_id)
