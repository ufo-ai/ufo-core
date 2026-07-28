"""Encrypted credential slots: BYOK secrets at rest, decrypted only to inject at the proxy.

A slot's plaintext is Fernet-encrypted per workspace; the raw value is never logged and leaves the
process only as the real secret the egress proxy swaps in for the sentinel the sandbox sees.

Filling a slot from chat is a sealed handoff, like an OAuth grant: the `request_credentials` tool
seals which slots the speaking owner will fill, a capable surface prompts for the values privately,
and fulfillment verifies the seal before writing — the plaintext travels member → surface → store,
never through the transcript or the sandbox."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ValidationError

from ufo.db import workspace_tx
from ufo.schema import tables

CREDENTIAL_REQUEST_TTL_SECONDS = 900


class CredentialSlotUnset(KeyError):
    """A slot has no stored secret for this workspace."""


class CredentialMintFailed(RuntimeError):
    """A `CredentialSource` could not mint from its provider: the external uncertainty a provider
    exchange carries, so the next turn may well succeed. Withheld per slot like every other fault a
    slot's resolution can raise — `derive_credential_rules` isolates the slot rather than the fault
    class, so a source is never obliged to translate what went wrong into a tolerated type."""


class CredentialRequestInvalid(ValueError):
    """A sealed credential request failed verification: tampered, expired, or claiming a workspace,
    member, or slot it was not sealed for."""


CREDENTIAL_REQUEST_PURPOSE = "credential-request"
INSTALLATION_BINDING_PURPOSE = "installation-binding"


class CredentialRequestState(BaseModel):
    """The claims a member credential action seals: workspace, speaking owner, slots, and optional
    provider authorization state. The credential Fernet encrypts it and bounds its lifetime.

    `purpose` separates the two things one deploy key seals. Every seal opens under that one key, so
    without it a request seal — which the member is handed in chat — would open as a binding and
    stand in for one. `member_id` is absent on a binding, which an organization owns rather than a
    member: `open_authorization` compares it, so a binding never satisfies a member's request."""

    workspace_id: UUID
    member_id: UUID | None = None
    slots: tuple[str, ...]
    payload: str | None = None
    purpose: str = CREDENTIAL_REQUEST_PURPOSE


def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str:
    return fernet.encrypt(state.model_dump_json().encode()).decode()


def open_credential_request(
    fernet: Fernet,
    sealed: str,
    *,
    purpose: str,
    ttl: int | None = CREDENTIAL_REQUEST_TTL_SECONDS,
) -> CredentialRequestState:
    """Open a seal this deploy minted for `purpose`. One key seals every credential act, so
    ciphertext from any of them decrypts under every other — the purpose is what keeps a binding
    from being opened as an authorization, and asking for it here is what makes that structural
    rather than a check each caller must remember. `ttl` bounds a request to the prompt it belongs
    to; a binding passes None, since an installation outlives the request that bound it. Total:
    anything that is not this deploy's own well-formed seal for this purpose raises
    `CredentialRequestInvalid`, so a caller never has to guard a decode."""
    try:
        raw = fernet.decrypt(sealed.encode(), ttl=ttl)
    except InvalidToken as error:
        raise CredentialRequestInvalid("credential request is tampered or expired") from error
    try:
        state = CredentialRequestState.model_validate_json(raw)
    except ValidationError as error:
        raise CredentialRequestInvalid("credential request is not a sealed state") from error
    if state.purpose != purpose:
        raise CredentialRequestInvalid(
            f"credential request was sealed for {state.purpose!r}, not {purpose!r}"
        )
    return state


@dataclass(frozen=True)
class CredentialRequests:
    """The member-sealed credential arm: the Fernet that guards the slots and the deploy's declared
    slot set, so a private prompt or provider authorization can name only an installed extension's
    slot. Absent when no credential key is configured.

    `fillable` is the subset a member may type a value into. It gates `seal` alone — the seal a
    private prompt is fulfilled against — while `authorize` keeps the whole declared set, because a
    provider callback binding an installation writes a slot the member must never type. Gating at
    the seal is what makes the refusal total: with no seal minted, no surface holds anything to
    fulfill against, so an unopenable value never reaches the slot at all."""

    fernet: Fernet
    declared: frozenset[str]
    fillable: frozenset[str]

    def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str:
        undeclared = [slot for slot in slots if slot not in self.declared]
        if undeclared:
            raise ValueError(f"no installed extension declares credential slot(s) {undeclared}")
        unfillable = [slot for slot in slots if slot not in self.fillable]
        if unfillable:
            raise ValueError(
                f"credential slot(s) {unfillable} are written by this deploy, never entered — "
                "the value is a seal a typed one cannot stand in for"
            )
        return seal_credential_request(
            self.fernet,
            CredentialRequestState(workspace_id=workspace_id, member_id=member_id, slots=slots),
        )

    def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str:
        if slot not in self.declared:
            raise ValueError(f"no installed extension declares credential slot {slot!r}")
        if not payload:
            raise ValueError("credential authorization provider state is empty")
        return seal_credential_request(
            self.fernet,
            CredentialRequestState(
                workspace_id=workspace_id,
                member_id=member_id,
                slots=(slot,),
                payload=payload,
            ),
        )

    def open_authorization(
        self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str
    ) -> str:
        state = open_credential_request(self.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE)
        if state.workspace_id != workspace_id:
            raise CredentialRequestInvalid("credential authorization belongs to another workspace")
        if state.member_id != member_id:
            raise CredentialRequestInvalid("credential authorization belongs to another member")
        if state.slots != (slot,):
            raise CredentialRequestInvalid("credential authorization names another slot")
        if slot not in self.declared:
            raise ValueError(f"no installed extension declares credential slot {slot!r}")
        if state.payload is None:
            raise CredentialRequestInvalid("credential authorization carries no provider state")
        return state.payload


def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str:
    """Bind a provider installation to the workspace that authorized it. The stored value is this
    seal, never the bare id: an installation id is a small integer anyone can guess, so a slot
    holding one a member typed would let a workspace mint against another organization's install.
    It is the same sealed state every credential act uses, marked with its own purpose so a request
    seal cannot stand in for it, and opened without a TTL — an installation outlives its request."""
    return seal_credential_request(
        fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            slots=(slot,),
            payload=installation_id,
            purpose=INSTALLATION_BINDING_PURPOSE,
        ),
    )


def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str:
    """The installation id this workspace bound, or `CredentialRequestInvalid` for anything else —
    a forged blob, another workspace's binding, a seal minted for some other purpose, or a bare
    id typed into the slot by hand."""
    state = open_credential_request(fernet, sealed, purpose=INSTALLATION_BINDING_PURPOSE, ttl=None)
    if state.workspace_id != workspace_id:
        raise CredentialRequestInvalid("installation binding belongs to another workspace")
    if state.slots != (slot,):
        raise CredentialRequestInvalid("installation binding names another slot")
    if state.payload is None:
        raise CredentialRequestInvalid("installation binding carries no installation")
    return state.payload


_installed_requests: CredentialRequests | None = None


def install_credential_requests(requests: CredentialRequests | None) -> None:
    """The process's single credential-request authority, installed once at serve boot. A provider
    callback arrives in a browser with no turn and no session, so the route that receives it
    resolves its workspace from the sealed state alone and needs the Fernet here rather than
    threaded through a context it does not have."""
    global _installed_requests
    _installed_requests = requests


def installed_credential_requests() -> CredentialRequests:
    if _installed_requests is None:
        raise RuntimeError("credential authorization unavailable: no credential key configured")
    return _installed_requests


def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None:
    """The workspace an authorization seal was minted for, or None for anything that is not this
    deploy's own seal for exactly this slot and purpose. A provider redirects the member's browser
    back with no turn and no session, so the route that receives it resolves its workspace from the
    seal alone — and must pin the slot and purpose here, since a seal minted to authorize one slot
    would otherwise stand in for another."""
    if _installed_requests is None:
        return None
    try:
        state = open_credential_request(
            _installed_requests.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
        )
    except CredentialRequestInvalid:
        return None
    if state.slots != (slot,) or state.payload != payload:
        return None
    return state.workspace_id


@dataclass(frozen=True)
class CredentialStore:
    fernet: Fernet

    async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None:
        if not plaintext:
            raise ValueError("credential value is empty")
        ciphertext = self.fernet.encrypt(plaintext.encode())
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.credential)
                .values(ciphertext=ciphertext, updated_at=sa.func.now())
                .where(
                    tables.credential.c.workspace_id == workspace_id,
                    tables.credential.c.slot == slot,
                )
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(tables.credential).values(
                        workspace_id=workspace_id,
                        slot=slot,
                        ciphertext=ciphertext,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )

    async def get(self, workspace_id: UUID, slot: str) -> str:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.credential.c.ciphertext).where(
                        tables.credential.c.workspace_id == workspace_id,
                        tables.credential.c.slot == slot,
                    )
                )
            ).one_or_none()
        if row is None:
            raise CredentialSlotUnset(slot)
        return self.fernet.decrypt(row.ciphertext).decode()

    async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool:
        """Replace an existing slot only while it still contains `expected`. OAuth clients use
        this after an external refresh so a concurrent call cannot overwrite a newer credential.
        The initial value still enters through the member-sealed fulfillment path."""
        if not plaintext:
            raise ValueError("credential value is empty")
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.credential.c.ciphertext).where(
                        tables.credential.c.workspace_id == workspace_id,
                        tables.credential.c.slot == slot,
                    )
                )
            ).one_or_none()
            if row is None:
                return False
            if self.fernet.decrypt(row.ciphertext).decode() != expected:
                return False
            updated = await connection.execute(
                sa.update(tables.credential)
                .values(
                    ciphertext=self.fernet.encrypt(plaintext.encode()),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.credential.c.workspace_id == workspace_id,
                    tables.credential.c.slot == slot,
                    tables.credential.c.ciphertext == row.ciphertext,
                )
            )
        return updated.rowcount == 1


@dataclass(frozen=True)
class HostChoice:
    """A provider whose API host varies per account, declared as the closed set of hosts it can be:
    the companion `slot` a member fills, the `hosts` that slot may name, the `default` an unchosen
    workspace gets, the `env` the resolved host is exported as, and the `description` the member is
    prompted with.

    The stored value is a **choice, never a hostname** — `credential_host` answers the matching
    declared literal or nothing at all, so the host that reaches a proxy `ScopeRule` is always a
    string this declaration wrote. That is what keeps a member-filled host as trustworthy as a
    code-declared one: an exact `ScopeRule` bypasses the proxy's private-address check (that check
    guards the open-internet path), and free text there would let a stored address or internal name
    decide where the shared proxy dials. A closed set has nothing to validate, so there is no
    pattern, no length cap, no case fold and no suffix bound to get wrong."""

    slot: str
    description: str
    hosts: tuple[str, ...]
    default: str
    env: str | None = None

    def __post_init__(self) -> None:
        if self.default not in self.hosts:
            raise ValueError(
                f"host choice on slot {self.slot!r} defaults to {self.default!r}, which its own "
                f"hosts {self.hosts} do not offer"
            )

    def resolve(self, selected: str) -> str | None:
        """The declared host a stored selection names, matched case-insensitively as DNS is — the
        canonical literal, never the member's spelling — or None for anything the set does not
        offer."""
        wanted = selected.strip().lower()
        return next((host for host in self.hosts if host.lower() == wanted), None)


class CredentialSource(Protocol):
    """A slot whose secret this deploy mints per workspace rather than the member storing one: the
    resolution runs at rule derivation, so a short-lived token is minted for the turn that uses it.
    None means this workspace has nothing to mint from, and the stored value answers instead."""

    async def secret(self, workspace_id: UUID, store: "CredentialStore") -> str | None: ...

    async def bound(self, workspace_id: UUID, store: "CredentialStore") -> bool:
        """Whether this workspace has something to mint from, answered without minting. Every
        sandbox open asks whether a slot is filled; only the wire asks for its value, so the
        question that runs on every turn must not reach the provider.

        Raises for a binding this workspace holds but this deploy cannot use, exactly as `secret`
        does: the two answer for different roles — the export and the wire — and a `False` here
        against a raise there is what exports a credential for a host the wire then refuses."""
        ...


async def slot_secret(
    name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore
) -> str | None:
    """The one answer to "what secret does this slot hold for this workspace" — a minted one where
    the slot declares a source, else the member's stored value, else None. Every consumer resolves
    through here (proxy rules, the sandbox export, the git config) so no role injects a credential
    another role never exported. Takes the name and source rather than the slot itself, because the
    manifest that declares slots already imports this module."""
    if source is not None:
        minted = await source.secret(workspace_id, store)
        if minted is not None:
            return minted
    try:
        return await store.get(workspace_id, name)
    except CredentialSlotUnset:
        return None


async def slot_is_set(
    name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore
) -> bool:
    """Whether this slot would yield a secret, without producing one. `slot_secret`'s question costs
    a provider round trip where the slot mints; this one is the DB read every sandbox open needs to
    decide whether to configure a client at all, so it stays off the wire.

    A source that raises propagates rather than falling through to the stored value, which is what
    keeps this answer identical to `slot_secret`'s: a workspace holding a binding this deploy cannot
    use yields nothing to either role, instead of exporting the member's own token into a sandbox
    whose wire will refuse the host. Callers isolate the raise per slot, as the rule derivation
    does."""
    if source is not None and await source.bound(workspace_id, store):
        return True
    try:
        await store.get(workspace_id, name)
    except CredentialSlotUnset:
        return False
    return True


async def credential_host(
    store: CredentialStore, workspace_id: UUID, host: str | HostChoice
) -> str | None:
    """The provider host an injecting slot's secret rides to for this workspace: a fixed declared
    host as-is, or — for a provider whose host varies per account — the one this workspace selected,
    falling back to the declared default while nothing is selected. None only for a stored value the
    declaration does not offer, which opens no egress at all. The egress proxy resolves the host it
    admits through here and the engine resolves the host it exports into the sandbox through here,
    from the same declaration: two roles, one answer, no registration between them."""
    match host:
        case str():
            return host
        case HostChoice():
            try:
                selected = await store.get(workspace_id, host.slot)
            except CredentialSlotUnset:
                return host.default
            return host.resolve(selected)
