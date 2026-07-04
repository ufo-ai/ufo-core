from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import (
    CredentialAccess,
    ScopedStore,
    UndeclaredCredentialSlot,
    context_for,
)
from selfhost.schema import tables


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


async def test_core_context_builds_and_is_usable(db: None) -> None:
    workspace_id = await _workspace()
    context = context_for(workspace_id, "core", frozenset(), _store())
    assert context.store.extension == "core"
    await context.store.put("tick", {"count": 1})
    assert await context.store.get("tick") == {"count": 1}
    with pytest.raises(UndeclaredCredentialSlot):
        await context.credentials.get("anything")
