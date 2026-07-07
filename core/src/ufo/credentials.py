"""Encrypted credential slots: BYOK secrets at rest, decrypted only to inject at the proxy.

A slot's plaintext is Fernet-encrypted per workspace; the raw value is never logged and leaves the
process only as the real secret the egress proxy swaps in for the sentinel the sandbox sees."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.db import workspace_tx
from ufo.schema import tables


class CredentialSlotUnset(KeyError):
    """A slot has no stored secret for this workspace."""


@dataclass(frozen=True)
class CredentialStore:
    fernet: Fernet

    async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None:
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
