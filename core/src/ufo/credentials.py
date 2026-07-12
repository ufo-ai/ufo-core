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
    """The claims a `request_credentials` call seals: which member (the speaking owner) will fill
    which slots of which workspace. Fernet-sealed under the same key that encrypts the slots and
    TTL-bounded at open, so fulfillment trusts the seal without a server-side pending row."""

    workspace_id: UUID
    member_id: UUID
    slots: tuple[str, ...]


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
    """The `request_credentials` tool's sealing arm: the Fernet that guards the slots and the
    deploy's declared slot set, so a request can only ever name slots some installed extension
    declared. Absent (None on the tool context) when no credential key is configured — collecting
    BYOK secrets is then unavailable and the tool fails loud."""

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
