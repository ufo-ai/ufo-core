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

import asyncio
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from ufo.db import current_workspace, workspace_tx
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT, CORE_PRICING, OPENAI_KEY_SLOT
from ufo.harness.models.grant import (
    REFRESH_LEASE_SECONDS,
    Grant,
    GrantRefusedRefresh,
    read_grant,
    refreshed,
)
from ufo.harness.models.interface import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    ModelClient,
    ModelEvent,
    ModelRequest,
)
from ufo.harness.models.pricing import Pricing
from ufo.runtime.access.credentials import (
    CredentialSlotUnset,
    CredentialStore,
    deploy_env,
    member_slot,
)
from ufo.runtime.billing.accounting import record_workspace_usage
from ufo.schema.records import Usage

MEMBER_ROUTED_SLOTS: Mapping[str, str] = MappingProxyType(
    {ANTHROPIC_KEY_SLOT: PROVIDER_ANTHROPIC, OPENAI_KEY_SLOT: PROVIDER_OPENAI}
)

"""What buys a model call's tokens. `platform` is the deploy's own key, which the workspace is
billed for. `key` is a metered provider key that is not the deploy's — the workspace's own row or a
member's — whose tokens cost real money the provider charges its holder directly. `plan` is a
connected account's grant, which serves calls under a subscription its holder already pays: those
tokens cost nobody anything per token, so pricing them against a rate card invents money."""
Funding = Literal["platform", "key", "plan"]
PLATFORM_FUNDED: Funding = "platform"
KEY_FUNDED: Funding = "key"
PLAN_FUNDED: Funding = "plan"
PLATFORM_PAYER = "platform"

_current_model_credentials: ContextVar[Mapping[str, str]] = ContextVar(
    "ufo_model_credentials", default=MappingProxyType({})
)


@contextmanager
def model_credentials(routes: Mapping[str, str]) -> Iterator[None]:
    """Bind exact stored credential slots to the models one turn may use."""
    token = _current_model_credentials.set(MappingProxyType(dict(routes)))
    try:
        yield
    finally:
        _current_model_credentials.reset(token)


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


class ModelFundingChanged(RuntimeError):
    """A model client would continue an attempt on a different payer than the one it began on."""


@dataclass(frozen=True)
class ModelCredential:
    """A model credential bound to the funding and payer its client must retain."""

    value: str = field(repr=False)
    funding: Funding
    payer: str


@dataclass(frozen=True)
class ResolvedModelClient:
    """A constructed model client and the immutable payer facts its usage must carry."""

    client: ModelClient = field(repr=False)
    funding: Funding
    payer: str

    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        return self.client.complete(request)


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


"""How often a caller waiting on someone else's refresh looks for the pair they bought, and how
long it waits before deciding that refresh will never land. The wait covers the lease, so a claimer
that died is followed rather than waited on forever."""
REFRESH_POLL_SECONDS = 0.25
REFRESH_WAITS = int(REFRESH_LEASE_SECONDS / REFRESH_POLL_SECONDS) + 1


@dataclass(frozen=True)
class WorkspaceScope:
    """The bound workspace, and the only handle to its secrets and its spend ledger."""

    workspace_id: UUID

    async def credential(self, slot: str, env: str | None = None, model: str | None = None) -> str:
        """This workspace's secret for `slot`: its stored BYOK value if set, else the platform
        default read live from env through `deploy_env` (`UFO_`-prefixed name first, then the
        `env` name or `SLOT` upper-cased). The single path to any secret — fetching one asserts a
        bound workspace, so a key is always the right workspace's.
        Missing or empty raises `CredentialSlotUnset`, so an unconfigured key fails the call
        needing it, never silently.

        An exact model credential capability resolves only its named stored slot, and only for the
        model it was bound to serve. A read that names no model, and every call the binding does not
        cover, reads the workspace row exactly as an unrouted slot does. A missing bound slot fails
        closed instead of falling through to workspace or platform funding."""
        routed = self._routed_slot(slot, model)
        if _store is not None:
            for candidate in (slot,) if routed is None else (routed,):
                try:
                    return await _store.get(self.workspace_id, candidate)
                except CredentialSlotUnset:
                    pass
        if routed is not None:
            raise CredentialSlotUnset(routed)
        value = deploy_env(env or slot.upper())
        if not value:
            raise CredentialSlotUnset(slot)
        return value

    async def model_credential(self, slot: str, env: str | None, model: str) -> ModelCredential:
        """The credential and payer one model client resolves together. A connected-account grant
        is plan-funded, a stored API key is paid directly by its holder, and the deploy environment
        is platform-funded. `payer` names the exact stored slot or the platform, so a retry cannot
        move the same attempt onto a different account while retaining its first billing verdict."""
        routed = self._routed_slot(slot, model)
        if _store is not None:
            for candidate in (slot,) if routed is None else (routed,):
                try:
                    stored = await _store.get(self.workspace_id, candidate)
                except CredentialSlotUnset:
                    continue
                grant = read_grant(stored)
                if grant is None:
                    return ModelCredential(stored, KEY_FUNDED, candidate)
                if not grant.spent:
                    return ModelCredential(grant.access, PLAN_FUNDED, candidate)
                return await self._refreshed_credential(_store, candidate, slot, stored, grant)
        if routed is not None:
            raise CredentialSlotUnset(routed)
        value = deploy_env(env or slot.upper())
        if not value:
            raise CredentialSlotUnset(slot)
        return ModelCredential(value, PLATFORM_FUNDED, PLATFORM_PAYER)

    async def _refreshed_credential(
        self, store: "CredentialStore", candidate: str, slot: str, stored: str, grant: Grant
    ) -> ModelCredential:
        """A spent grant, refreshed once however many calls wanted it at once.

        The claim is a compare-and-swap that leases the refresh, and the provider call happens
        outside every transaction: a database lock held across it would stall every other write in
        the process, and on SQLite every read too. A caller that does not win the claim waits for
        the winner's pair rather than spending the same one-time token behind it — providers rotate
        that token and read a second exchange of one as reuse, revoking the account."""
        for _ in range(REFRESH_WAITS):
            if not grant.claimed:
                leased = grant.model_copy(
                    update={"refreshing_until": time.time() + REFRESH_LEASE_SECONDS}
                )
                if await store.rotate(self.workspace_id, candidate, stored, leased.stored()):
                    bought = await refreshed(grant, slot)
                    await store.rotate(
                        self.workspace_id, candidate, leased.stored(), bought.stored()
                    )
                    return ModelCredential(bought.access, PLAN_FUNDED, candidate)
            await asyncio.sleep(REFRESH_POLL_SECONDS)
            stored = await store.get(self.workspace_id, candidate)
            held = read_grant(stored)
            if held is None:
                return ModelCredential(stored, KEY_FUNDED, candidate)
            if not held.spent:
                return ModelCredential(held.access, PLAN_FUNDED, candidate)
            grant = held
        raise GrantRefusedRefresh(slot)

    def routed_model_call(self, model: str) -> bool:
        return model in _current_model_credentials.get()

    def routed_model_payer(self, model: str) -> str | None:
        return _current_model_credentials.get().get(model)

    def _slot_order(self, slot: str, model: str | None) -> tuple[str, ...]:
        routed = self._routed_slot(slot, model)
        return (slot,) if routed is None else (routed,)

    def _routed_slot(self, slot: str, model: str | None) -> str | None:
        routed = None if model is None else _current_model_credentials.get().get(model)
        if routed is None or not routed.startswith(f"{slot}:member:"):
            return None
        return routed

    async def member_model_accounts(self, member_id: UUID | None) -> tuple[tuple[str, str], ...]:
        if _store is None or member_id is None:
            return ()
        connected: list[tuple[str, str]] = []
        for slot, provider in MEMBER_ROUTED_SLOTS.items():
            exact = member_slot(slot, member_id)
            try:
                await _store.get(self.workspace_id, exact)
            except CredentialSlotUnset:
                continue
            connected.append((provider, exact))
        return tuple(connected)

    async def stored_credential_slots(self) -> frozenset[str]:
        """Every slot this workspace holds its own value for, in one read — `credential_is_stored`
        asked of all of them at once, for a caller that walks a catalogue rather than one slot."""
        if _store is None:
            return frozenset()
        return await _store.stored_slots(self.workspace_id)

    async def credential_is_stored(self, slot: str, model: str | None = None) -> bool:
        """Whether this workspace holds its own value for `slot` rather than running on the platform
        default — what `credential` resolved, asked as a question, so `model` names the call the
        same way. Spend on a provider key that is not the deploy's — the workspace's own, or the
        member's for a call their account served — is already paid and never the platform's to
        bill."""
        if _store is None:
            return False
        for candidate in self._slot_order(slot, model):
            try:
                await _store.get(self.workspace_id, candidate)
            except CredentialSlotUnset:
                continue
            return True
        return False

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

    async def clear_credential(self, slot: str) -> None:
        """Drop this workspace's own stored value for a slot. A platform environment default has no
        row and remains unchanged, so the slot falls back to it exactly as an unfilled one does."""
        if _store is None:
            return
        await _store.clear(self.workspace_id, slot)

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
