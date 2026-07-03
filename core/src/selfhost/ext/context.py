"""The capability-scoped view a job or extension handler receives.

A handler never sees a raw DB handle or another workspace: it gets a `ScopedStore` (its own
key space under one workspace) and `CredentialAccess` (only the slots its manifest declared).
`context_for` builds the same shape for an extension (its manifest name + declared slots) and for
a core job (the `core` namespace, no slots) — so a core job rides the exact path an extension does.
The `ExtensionContext` shape is open: later units add methods (memory writes, governed proposals,
invoke) without reshaping what handlers already hold."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa

from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.memory.service import MemoryService
from selfhost.schema import tables
from selfhost.schema.records import MemoryWrite

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class UndeclaredCredentialSlot(KeyError):
    """A handler asked for a credential slot its manifest never declared."""


@dataclass(frozen=True)
class ScopedStore:
    """One extension's durable key space within one workspace, reached only through workspace_tx."""

    workspace_id: UUID
    extension: str

    async def get(self, key: str) -> JsonValue | None:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.ext_store.c.value).where(
                        tables.ext_store.c.workspace_id == self.workspace_id,
                        tables.ext_store.c.extension == self.extension,
                        tables.ext_store.c.key == key,
                    )
                )
            ).one_or_none()
        return None if row is None else row.value

    async def put(self, key: str, value: JsonValue) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.ext_store)
                .values(value=value, updated_at=sa.func.now())
                .where(
                    tables.ext_store.c.workspace_id == self.workspace_id,
                    tables.ext_store.c.extension == self.extension,
                    tables.ext_store.c.key == key,
                )
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(tables.ext_store).values(
                        workspace_id=self.workspace_id,
                        extension=self.extension,
                        key=key,
                        value=value,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )

    async def list(self, prefix: str = "") -> tuple[tuple[str, JsonValue], ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.ext_store.c.key, tables.ext_store.c.value)
                    .where(
                        tables.ext_store.c.workspace_id == self.workspace_id,
                        tables.ext_store.c.extension == self.extension,
                        tables.ext_store.c.key.startswith(prefix, autoescape=True),
                    )
                    .order_by(tables.ext_store.c.key)
                )
            ).all()
        return tuple((row.key, row.value) for row in rows)


@dataclass(frozen=True)
class CredentialAccess:
    """Reads only the slots a manifest declared; an undeclared slot never reaches the store."""

    workspace_id: UUID
    declared: frozenset[str]
    store: CredentialStore

    async def get(self, slot: str) -> str:
        if slot not in self.declared:
            raise UndeclaredCredentialSlot(slot)
        return await self.store.get(self.workspace_id, slot)


@dataclass(frozen=True)
class ExtensionContext:
    store: ScopedStore
    credentials: CredentialAccess
    memory: MemoryService | None = None

    async def memory_write(self, write: MemoryWrite) -> None:
        """Commit a memory item for this workspace. Fails loud when no memory service is wired,
        rather than silently dropping the write."""
        if self.memory is None:
            raise RuntimeError("memory_write requires a memory service; none is wired")
        await self.memory.commit(write)


def context_for(
    workspace_id: UUID,
    extension: str,
    declared: frozenset[str],
    credential_store: CredentialStore,
    memory: MemoryService | None = None,
) -> ExtensionContext:
    store = ScopedStore(workspace_id=workspace_id, extension=extension)
    credentials = CredentialAccess(
        workspace_id=workspace_id, declared=declared, store=credential_store
    )
    return ExtensionContext(store=store, credentials=credentials, memory=memory)
