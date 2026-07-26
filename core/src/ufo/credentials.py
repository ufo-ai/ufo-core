"""Encrypted credential slots: BYOK secrets at rest, decrypted only to inject at the proxy.

A slot's plaintext is Fernet-encrypted per workspace; the raw value is never logged and leaves the
process only as the real secret the egress proxy swaps in for the sentinel the sandbox sees.

Filling a slot from chat is a sealed handoff, like an OAuth grant: the `request_credentials` tool
seals which slots the speaking owner will fill, a capable surface prompts for the values privately,
and fulfillment verifies the seal before writing — the plaintext travels member → surface → store,
never through the transcript or the sandbox."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel

from ufo.db import workspace_tx
from ufo.schema import tables

CREDENTIAL_REQUEST_TTL_SECONDS = 900


class CredentialSlotUnset(KeyError):
    """A slot has no stored secret for this workspace."""


class CredentialRequestInvalid(ValueError):
    """A sealed credential request failed verification: tampered, expired, or claiming a workspace,
    member, or slot it was not sealed for."""


class CredentialRequestState(BaseModel):
    """The claims a member credential action seals: workspace, speaking owner, slots, and optional
    provider authorization state. The credential Fernet encrypts it and bounds its lifetime."""

    workspace_id: UUID
    member_id: UUID
    slots: tuple[str, ...]
    payload: str | None = None


def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str:
    return fernet.encrypt(state.model_dump_json().encode()).decode()


def open_credential_request(fernet: Fernet, sealed: str) -> CredentialRequestState:
    try:
        raw = fernet.decrypt(sealed.encode(), ttl=CREDENTIAL_REQUEST_TTL_SECONDS)
    except InvalidToken as error:
        raise CredentialRequestInvalid("credential request is tampered or expired") from error
    return CredentialRequestState.model_validate_json(raw)


@dataclass(frozen=True)
class CredentialRequests:
    """The member-sealed credential arm: the Fernet that guards the slots and the deploy's declared
    slot set, so a private prompt or provider authorization can name only an installed extension's
    slot. Absent when no credential key is configured."""

    fernet: Fernet
    declared: frozenset[str]

    def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str:
        undeclared = [slot for slot in slots if slot not in self.declared]
        if undeclared:
            raise ValueError(f"no installed extension declares credential slot(s) {undeclared}")
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
        state = open_credential_request(self.fernet, sealed)
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
