import inspect
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import (
    CredentialAccess,
    MemoryAccess,
    OutOfScopeSubject,
    ScopedStore,
    UndeclaredCredentialSlot,
    context_for,
)
from selfhost.memory.index import index_backend_for
from selfhost.memory.service import SHARED_SUBJECT, MemoryService, member_subject
from selfhost.schema import tables
from selfhost.schema.records import MemoryWrite


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _store() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


async def test_scoped_store_round_trips_json_values(db: None) -> None:
    store = ScopedStore(workspace_id=await _workspace(), extension="sample")
    await store.put("obj", {"a": 1, "nested": [True, None]})
    await store.put("scalar", "hello")
    assert await store.get("obj") == {"a": 1, "nested": [True, None]}
    assert await store.get("scalar") == "hello"
    assert await store.get("absent") is None


async def test_scoped_store_upserts(db: None) -> None:
    store = ScopedStore(workspace_id=await _workspace(), extension="sample")
    await store.put("k", "one")
    await store.put("k", "two")
    assert await store.get("k") == "two"


async def test_scoped_store_lists_by_prefix_within_its_extension(db: None) -> None:
    workspace_id = await _workspace()
    sample = ScopedStore(workspace_id=workspace_id, extension="sample")
    other = ScopedStore(workspace_id=workspace_id, extension="other")
    await sample.put("run:2", 2)
    await sample.put("run:1", 1)
    await sample.put("cursor", "x")
    await other.put("run:9", 9)
    assert await sample.list("run:") == (("run:1", 1), ("run:2", 2))
    assert await other.list() == (("run:9", 9),)


async def test_scoped_store_isolates_extensions(db: None) -> None:
    workspace_id = await _workspace()
    sample = ScopedStore(workspace_id=workspace_id, extension="sample")
    other = ScopedStore(workspace_id=workspace_id, extension="other")
    await sample.put("shared_key", "sample-value")
    assert await other.get("shared_key") is None


async def test_credential_access_reads_declared_and_rejects_undeclared(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "sk-real")
    access = CredentialAccess(
        workspace_id=workspace_id, declared=frozenset({"sample_api"}), _store=store
    )
    assert await access.get("sample_api") == "sk-real"
    with pytest.raises(UndeclaredCredentialSlot, match="undeclared_slot"):
        await access.get("undeclared_slot")


class _InertEmbed:
    """A stand-in embed provider: commit never embeds, and this test asserts the persisted row, not
    the embedding — so the provider is a dependency here, never the thing under test."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ()


async def test_memory_access_commits_only_under_the_shared_subject(
    db: None, database_url: str
) -> None:
    embed = _InertEmbed()
    access = MemoryAccess(MemoryService(index=index_backend_for(database_url, embed), embed=embed))
    await _workspace()
    await access.commit(MemoryWrite(subject=SHARED_SUBJECT, body="a workspace fact"))
    with pytest.raises(OutOfScopeSubject, match="member:"):
        await access.commit(MemoryWrite(subject=member_subject(uuid4()), body="a private fact"))
    async with workspace_tx() as connection:
        subjects = (
            (await connection.execute(sa.select(tables.memory_item.c.subject))).scalars().all()
        )
    assert list(subjects) == [SHARED_SUBJECT]


def test_memory_access_recall_cannot_target_a_subject() -> None:
    """recall derives the shared subject and takes no subject argument, so a member's private space
    is unreachable through this handle — the confinement is structural, not a runtime check."""
    assert set(inspect.signature(MemoryAccess.recall).parameters) == {"self", "query", "limit"}


async def test_core_context_builds_and_is_usable(db: None) -> None:
    workspace_id = await _workspace()
    context = context_for(workspace_id, "core", frozenset(), _store())
    assert context.store.extension == "core"
    await context.store.put("tick", {"count": 1})
    assert await context.store.get("tick") == {"count": 1}
    with pytest.raises(UndeclaredCredentialSlot):
        await context.credentials.get("anything")
