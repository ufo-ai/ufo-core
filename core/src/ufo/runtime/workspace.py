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
from ufo.runtime.authority import ExecutionAuthority, MemberAuthority, authority_member_id
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

_current_model_authority: ContextVar[tuple[ExecutionAuthority, frozenset[str]] | None] = ContextVar(
    "ufo_model_authority", default=None
)


@contextmanager
def model_authority(
    authority: ExecutionAuthority, models: frozenset[str] = frozenset()
) -> Iterator[None]:
    """Bind the member whose own provider account serves `models`, so those models resolve the key
    they connected. The binding names the models and not the provider, because the member's account
    and the deploy's own background work can want the same slot inside one turn — a coding turn
    summarizing its tool calls asks for an OpenAI key twice, and only one of those two calls is the
    member's to pay for. Every other turn binds workspace authority and spends the workspace's key,
    because a member connects an account for coding and not for normal operation."""
    token = _current_model_authority.set((authority, models))
    try:
        yield
    finally:
        _current_model_authority.reset(token)


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

        A member-routed slot resolves the bound speaker's own key before the workspace row, and
        only for the models that speaker's account was bound to serve: `model` names the call
        asking. A read that names no model, and every call the binding does not cover, reads the
        workspace row exactly as an unrouted slot does — nobody's personal account pays for someone
        else's call."""
        if _store is not None:
            for candidate in self._slot_order(slot, model):
                try:
                    return await _store.get(self.workspace_id, candidate)
                except CredentialSlotUnset:
                    pass
        value = deploy_env(env or slot.upper())
        if not value:
            raise CredentialSlotUnset(slot)
        return value

    async def model_credential(self, slot: str, env: str | None, model: str) -> ModelCredential:
        """The credential and payer one model client resolves together. A connected-account grant
        is plan-funded, a stored API key is paid directly by its holder, and the deploy environment
        is platform-funded. `payer` names the exact stored slot or the platform, so a retry cannot
        move the same attempt onto a different account while retaining its first billing verdict."""
        if _store is not None:
            for candidate in self._slot_order(slot, model):
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

    def member_routed_call(self, slot: str, model: str) -> bool:
        """Whether this call resolves the speaking member's own slot — which is the only slot that
        can hold a grant, and so the only one whose credential a refresh can replace."""
        return len(self._slot_order(slot, model)) > 1

    def _slot_order(self, slot: str, model: str | None) -> list[str]:
        bound = _current_model_authority.get()
        if slot not in MEMBER_ROUTED_SLOTS or bound is None:
            return [slot]
        authority, models = bound
        if model is None or model not in models:
            return [slot]
        match authority:
            case MemberAuthority(member_id):
                return [member_slot(slot, member_id), slot]
            case _:
                return [slot]

    async def member_holds_own_model_key(self, authority: ExecutionAuthority) -> bool:
        """Whether the authority's member signed in with a provider account of their own — either
        one, since the coding subagent runs on whichever they connected."""
        return await self.member_model_provider(authority) is not None

    async def member_model_provider(self, authority: ExecutionAuthority) -> str | None:
        """The provider the authority's member signed in with, or None if they connected neither.
        A member who skipped that step in onboarding holds no key of their own, and the workspace's
        own is not theirs: this asks about the person, not the deploy, so it never reads the admin
        or platform fallbacks. Declaration order decides for a member who connected both."""
        member_id = authority_member_id(authority)
        if _store is None or member_id is None:
            return None
        for slot, provider in MEMBER_ROUTED_SLOTS.items():
            try:
                await _store.get(self.workspace_id, member_slot(slot, member_id))
            except CredentialSlotUnset:
                continue
            return provider
        return None

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
