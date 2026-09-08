"""Encrypted credential slots: BYOK secrets at rest, decrypted only to inject at the proxy.

A slot's plaintext is Fernet-encrypted per workspace; the raw value is never logged and leaves the
process only as the real secret the egress proxy swaps in for the sentinel the sandbox sees.

Filling a slot from chat is a sealed handoff, like an OAuth grant: the `request_credentials` tool
seals which slots the speaking owner will fill, a capable surface prompts for the values privately,
and fulfillment verifies the seal before writing — the plaintext travels member → surface → store,
never through the transcript or the sandbox."""

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.db import workspace_tx
from ufo.schema import tables

CREDENTIAL_REQUEST_TTL_SECONDS = 900
CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS = 86_400
NAME_DIGEST_LENGTH = 8


def deploy_env(name: str) -> str | None:
    """The deploy-level platform secret named `name`: `UFO_<name>` first, then `name`, empty
    counting as unset. The prefixed form scopes a key to ufo alone, so a repo `.env` can hold
    `UFO_ANTHROPIC_API_KEY` for the stack without handing that key to every other tool reading
    `ANTHROPIC_API_KEY`; the bare form keeps an environment that already exports the upstream
    name working."""
    return os.environ.get(f"UFO_{name}") or os.environ.get(name) or None


MEMBER_SLOT_INFIX = ":member:"


def member_slot(slot: str, member_id: UUID) -> str:
    """The slot holding one member's own value for `slot` — the same encrypted store, keyed to the
    person whose account the value came from, so a workspace holds one row per member beside the
    workspace-wide row."""
    return f"{slot}{MEMBER_SLOT_INFIX}{member_id}"


def named_slots(slots: "tuple[DeclaredSlot, ...]") -> "dict[str, DeclaredSlot]":
    """The `credential` object kind's name for each declared slot — the one naming every read and
    verb shares, so a portal row and an `object_delete` intent address the same instance. The name
    is the slot's own name as a slug, which is how a portal row, a chat `object_apply` and an
    `object_delete` intent all address one slot. A slug collision across extensions gains a stable
    digest qualifier."""
    grouped: dict[str, list[DeclaredSlot]] = {}
    for slot in slots:
        slug = re.sub(r"[^a-z0-9]+", "-", slot.name.lower()).strip("-")
        grouped.setdefault(slug, []).append(slot)
    named: dict[str, DeclaredSlot] = {}
    for plain, group in grouped.items():
        if len(group) == 1:
            named[plain] = group[0]
            continue
        for slot in group:
            qualifier = hashlib.sha256(f"{slot.extension}:{slot.name}".encode()).hexdigest()
            named[f"{plain}-{qualifier[:NAME_DIGEST_LENGTH]}"] = slot
    return named


class CredentialSlotUnset(KeyError):
    """A slot has no stored secret for this workspace."""


class CredentialRequestInvalid(ValueError):
    """A sealed credential request failed verification: tampered, expired, or claiming a workspace,
    member, or slot it was not sealed for."""


class CredentialValueInvalid(ValueError):
    """A credential value cannot serve its provider wire: unrepresentable on it, or refused by the
    provider that read it."""


class CredentialRequestState(BaseModel):
    """The claims a member credential action seals: workspace, speaking owner, slots, and optional
    provider authorization state. The credential Fernet encrypts it and bounds its lifetime."""

    workspace_id: UUID
    member_id: UUID
    slots: tuple[str, ...]
    request_id: UUID | None = None
    issued_at: int | None = None
    payload: str | None = None
    workspace_declarations: dict[str, str] = Field(default_factory=dict)


def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str:
    return fernet.encrypt(state.model_dump_json().encode()).decode()


def open_credential_request(
    fernet: Fernet, sealed: str, *, ttl: int = CREDENTIAL_REQUEST_TTL_SECONDS
) -> CredentialRequestState:
    """Open a seal this deploy minted. `ttl` bounds it to the prompt it belongs to, or to the longer
    renewal window a surface re-offers an unanswered prompt within. Total: anything that is not
    this deploy's own well-formed, unexpired seal raises `CredentialRequestInvalid`, so a caller
    never has to guard a decode."""
    try:
        raw = fernet.decrypt(sealed.encode(), ttl=ttl)
    except InvalidToken as error:
        raise CredentialRequestInvalid("credential request is tampered or expired") from error
    try:
        return CredentialRequestState.model_validate_json(raw)
    except ValidationError as error:
        raise CredentialRequestInvalid("credential request is not a sealed state") from error


@dataclass(frozen=True)
class CredentialRequests:
    """The member-sealed credential arm: the Fernet that guards the slots and the deploy's declared
    slot set, so a private prompt or provider authorization can name only an installed extension's
    slot. Absent when no credential key is configured."""

    fernet: Fernet
    declared: frozenset[str]

    def seal(
        self,
        workspace_id: UUID,
        member_id: UUID,
        slots: tuple[str, ...],
        also_declared: Mapping[str, str] | None = None,
    ) -> str:
        """Seal the slots this member will fill. `also_declared` carries the slots the workspace
        declares for itself, which no manifest names — the caller reads them under the workspace it
        is sealing for, so one workspace's declaration never admits another's."""
        workspace_declarations = {} if also_declared is None else dict(also_declared)
        undeclared = [
            slot
            for slot in slots
            if slot not in self.declared and slot not in workspace_declarations
        ]
        if undeclared:
            raise ValueError(f"no installed extension declares credential slot(s) {undeclared}")
        return seal_credential_request(
            self.fernet,
            CredentialRequestState(
                workspace_id=workspace_id,
                member_id=member_id,
                slots=slots,
                request_id=uuid4(),
                issued_at=int(time.time()),
                workspace_declarations={
                    slot: workspace_declarations[slot]
                    for slot in slots
                    if slot in workspace_declarations
                },
            ),
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

    async def fulfill(
        self,
        workspace_id: UUID,
        slot: str,
        submitted: str,
        request_id: UUID | None,
        member_id: UUID,
        merge: Callable[[str | None, str], str] | None,
    ) -> None:
        """Write while the sealed member is a seated admin, claiming a member prompt once."""
        if not submitted:
            raise ValueError("credential value is empty")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == workspace_id)
                .with_for_update()
            )
            authority = (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == workspace_id,
                        tables.member.c.id == member_id,
                        tables.member.c.is_admin,
                        tables.member.c.seated_at.is_not(None),
                    )
                )
            ).scalar_one_or_none()
            if authority is None:
                raise CredentialRequestInvalid(
                    "credential request requires a seated workspace admin"
                )
            if request_id is not None:
                insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
                claimed = (
                    await connection.execute(
                        insert(tables.credential_fulfillment)
                        .values(
                            workspace_id=workspace_id,
                            request_id=request_id,
                            slot=slot,
                            member_id=member_id,
                            fulfilled_at=sa.func.now(),
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                tables.credential_fulfillment.c.workspace_id,
                                tables.credential_fulfillment.c.request_id,
                                tables.credential_fulfillment.c.slot,
                            ]
                        )
                        .returning(tables.credential_fulfillment.c.request_id)
                    )
                ).scalar_one_or_none()
                if claimed is None:
                    raise CredentialRequestInvalid("credential request was already fulfilled")
            row = (
                await connection.execute(
                    sa.select(tables.credential.c.ciphertext).where(
                        tables.credential.c.workspace_id == workspace_id,
                        tables.credential.c.slot == slot,
                    )
                )
            ).one_or_none()
            current = None if row is None else self.fernet.decrypt(row.ciphertext).decode()
            plaintext = submitted if merge is None else merge(current, submitted)
            if not plaintext:
                raise ValueError("credential value is empty")
            ciphertext = self.fernet.encrypt(plaintext.encode())
            if row is None:
                await connection.execute(
                    sa.insert(tables.credential).values(
                        workspace_id=workspace_id,
                        slot=slot,
                        ciphertext=ciphertext,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            else:
                await connection.execute(
                    sa.update(tables.credential)
                    .values(ciphertext=ciphertext, updated_at=sa.func.now())
                    .where(
                        tables.credential.c.workspace_id == workspace_id,
                        tables.credential.c.slot == slot,
                    )
                )

    async def clear(self, workspace_id: UUID, slot: str) -> None:
        """Drop one slot's stored value. A slot that holds nothing is already cleared, so this is
        the same act either way and never raises for a member disconnecting twice."""
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.credential).where(
                    tables.credential.c.workspace_id == workspace_id,
                    tables.credential.c.slot == slot,
                )
            )

    async def update(
        self,
        workspace_id: UUID,
        slot: str,
        submitted: str,
        merge: Callable[[str | None, str], str],
    ) -> None:
        """Merge one private submission into a slot while holding the workspace write lock."""
        if not submitted:
            raise ValueError("credential value is empty")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.workspace.c.id)
                .where(tables.workspace.c.id == workspace_id)
                .with_for_update()
            )
            row = (
                await connection.execute(
                    sa.select(tables.credential.c.ciphertext).where(
                        tables.credential.c.workspace_id == workspace_id,
                        tables.credential.c.slot == slot,
                    )
                )
            ).one_or_none()
            current = None if row is None else self.fernet.decrypt(row.ciphertext).decode()
            plaintext = merge(current, submitted)
            if not plaintext:
                raise ValueError("credential value is empty")
            ciphertext = self.fernet.encrypt(plaintext.encode())
            if row is None:
                await connection.execute(
                    sa.insert(tables.credential).values(
                        workspace_id=workspace_id,
                        slot=slot,
                        ciphertext=ciphertext,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
            else:
                await connection.execute(
                    sa.update(tables.credential)
                    .values(ciphertext=ciphertext, updated_at=sa.func.now())
                    .where(
                        tables.credential.c.workspace_id == workspace_id,
                        tables.credential.c.slot == slot,
                    )
                )

    async def stored_slots(self, workspace_id: UUID) -> frozenset[str]:
        """Every slot this workspace holds its own secret for, in one read. A caller asking the
        same question of many slots — a feed registrar walking a connector catalogue every tick —
        would otherwise pay a query per slot for an answer that is almost always no."""
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(tables.credential.c.slot).where(
                    tables.credential.c.workspace_id == workspace_id
                )
            )
        return frozenset(rows.scalars())

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


@dataclass(frozen=True)
class DeclaredSlot:
    """One declared BYOK slot as reads project it: the slot name, its documentation, the extension
    that declares it, `host` — the wire target when the slot carries one, either a fixed hostname
    or the `HostChoice` a member selects within — `env`, the sandbox variable the slot's
    sentinel is exported as, empty for a slot the sandbox never sees, and `header`, the header the
    secret rides in on the wire. `merge` updates a structured
    secret from one private submission at the encrypted store boundary."""

    name: str
    description: str
    extension: str
    host: str | HostChoice | None = None
    env: str = ""
    header: str = ""
    merge: Callable[[str | None, str], str] | None = None


def declared_slot_fingerprint(slot: DeclaredSlot) -> str:
    """The immutable declaration a private credential prompt authorizes. Workspace-provided slots
    may change while that prompt is open; binding the wire fields keeps its value from landing on a
    host, header, or sandbox variable the member did not approve."""
    match slot.host:
        case HostChoice() as choice:
            host: str | tuple[object, ...] | None = (
                choice.slot,
                choice.description,
                choice.hosts,
                choice.default,
                choice.env,
            )
        case value:
            host = value
    encoded = json.dumps(
        (slot.name, slot.description, slot.extension, host, slot.env, slot.header),
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
