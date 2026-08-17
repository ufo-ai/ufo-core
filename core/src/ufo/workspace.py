"""The workspace scope: the one ambient handle every credentialed, billed, or workspace-scoped call
goes through — identical whether it runs in a turn or a job.

`with ws(workspace_id):` binds a workspace for the block; `ws_current()` returns it and raises
`WorkspaceUnbound` if none is bound. A secret is reachable only as `ws_current().credential(slot)` —
the workspace's stored BYOK value, else the platform default from env — so a key is never fetched
without naming the workspace that holds it, and never leaks across one. Spend is booked only through
`with ws_current().billable_event() as bill: bill.usage(...)`, written to that same workspace when
the block exits. The workspace is never an argument: binding it once at
the turn or job boundary scopes credentials, billing, and RLS within — so a job bills the same
workspace the same way a turn does, by construction."""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from uuid import UUID

from ufo.accounting import record_workspace_usage
from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import current_workspace, workspace_tx
from ufo.models.catalog import CORE_PRICING
from ufo.models.pricing import Pricing
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
    """Spend accrued inside one `billable_event()` block. Add provider usage as it arrives; the
    block exit writes every reported attempt, including an attempt whose output cannot be used."""

    _usages: list[tuple[str, Usage, Pricing, bool]] = field(default_factory=list)

    def usage(
        self,
        model: str,
        usage: Usage,
        pricing: Pricing = CORE_PRICING,
        byok: bool = False,
    ) -> None:
        """Accrue one metered model attempt to book when the block exits. `byok` is decided by the
        caller, against the key that served this call, so a call the workspace's own key paid for is
        billed nothing and the answer cannot drift before the bill is written."""
        self._usages.append((model, usage, pricing, byok))


@dataclass(frozen=True)
class WorkspaceScope:
    """The bound workspace, and the only handle to its secrets and its spend ledger."""

    workspace_id: UUID

    async def credential(self, slot: str, env: str | None = None) -> str:
        """This workspace's secret for `slot`: its stored BYOK value if set, else the platform
        default read live from env (`env` name, or `SLOT` upper-cased). The single path to any
        secret — fetching one asserts a bound workspace, so a key is always the right workspace's.
        Missing or empty raises `CredentialSlotUnset`, so an unconfigured key fails the call
        needing it, never silently."""
        if _store is not None:
            try:
                return await _store.get(self.workspace_id, slot)
            except CredentialSlotUnset:
                pass
        value = os.environ.get(env or slot.upper())
        if not value:
            raise CredentialSlotUnset(slot)
        return value

    async def credential_is_stored(self, slot: str) -> bool:
        """Whether this workspace holds its own value for `slot` rather than running on the platform
        default — what `credential` resolved, asked as a question. Spend on a workspace's own
        provider key is already paid to that provider and is never the platform's to bill."""
        if _store is None:
            return False
        try:
            await _store.get(self.workspace_id, slot)
        except CredentialSlotUnset:
            return False
        return True

    async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool:
        """Compare-and-swap an existing encrypted workspace credential. A platform environment
        default has no row to rotate and remains unchanged."""
        if _store is None:
            return False
        return await _store.rotate(self.workspace_id, slot, expected, plaintext)

    async def put_credential(self, slot: str, plaintext: str) -> None:
        """Store an owner-authorized initial credential through the bound workspace."""
        if _store is None:
            raise RuntimeError("credential store is not configured")
        await _store.put(self.workspace_id, slot, plaintext)

    @asynccontextmanager
    async def billable_event(self) -> AsyncIterator[BillableEvent]:
        """Book reported provider usage to this workspace when the block exits. A model-output
        error cannot remove a charge the provider already reported."""
        event = BillableEvent()
        try:
            yield event
        finally:
            if event._usages:
                async with workspace_tx() as connection:
                    for model, usage, pricing, byok in event._usages:
                        await record_workspace_usage(
                            connection, self.workspace_id, model, usage, pricing, byok
                        )


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
