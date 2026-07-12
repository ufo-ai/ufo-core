from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import (
    CredentialAccess,
    ScopedStore,
    UndeclaredCredentialSlot,
    context_for,
)
from ufo.schema import tables
from ufo.workspace import WorkspaceUnbound, init_workspace_credentials, ws


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
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        await store.put("obj", {"a": 1, "nested": [True, None]})
        await store.put("scalar", "hello")
        assert await store.get("obj") == {"a": 1, "nested": [True, None]}
        assert await store.get("scalar") == "hello"
        assert await store.get("absent") is None


async def test_scoped_store_upserts(db: None) -> None:
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        await store.put("k", "one")
        await store.put("k", "two")
        assert await store.get("k") == "two"


async def test_scoped_store_delete_removes_only_its_key(db: None) -> None:
    with ws(await _workspace()):
        store = ScopedStore(extension="sample")
        await store.put("watch:a", 1)
        await store.put("watch:b", 2)
        await store.delete("watch:a")
        assert await store.get("watch:a") is None
        assert await store.list("watch:") == (("watch:b", 2),)


async def test_scoped_store_lists_by_prefix_within_its_extension(db: None) -> None:
    with ws(await _workspace()):
        sample = ScopedStore(extension="sample")
        other = ScopedStore(extension="other")
        await sample.put("run:2", 2)
        await sample.put("run:1", 1)
        await sample.put("cursor", "x")
        await other.put("run:9", 9)
        assert await sample.list("run:") == (("run:1", 1), ("run:2", 2))
        assert await other.list() == (("run:9", 9),)


async def test_scoped_store_isolates_extensions(db: None) -> None:
    with ws(await _workspace()):
        sample = ScopedStore(extension="sample")
        other = ScopedStore(extension="other")
        await sample.put("shared_key", "sample-value")
        assert await other.get("shared_key") is None


async def test_scoped_store_scopes_to_the_bound_workspace(db: None) -> None:
    """The store reads the ambient workspace, so rebinding to another workspace never sees the
    first's rows — the isolation is the `with ws(...)` scope, not a field the caller passes."""
    first, second = await _workspace(), await _workspace()
    with ws(first):
        await ScopedStore(extension="sample").put("k", "first-value")
    with ws(second):
        assert await ScopedStore(extension="sample").get("k") is None


async def test_scoped_store_outside_a_workspace_scope_fails_loud(db: None) -> None:
    """No ambient workspace → the store raises rather than reading a NULL or wrong workspace."""
    with pytest.raises(WorkspaceUnbound):
        await ScopedStore(extension="sample").get("k")


async def test_credential_access_reads_declared_and_rejects_undeclared(db: None) -> None:
    workspace_id = await _workspace()
    store = _store()
    await store.put(workspace_id, "sample_api", "sk-real")
    init_workspace_credentials(store)
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with ws(workspace_id):
        assert await access.get("sample_api") == "sk-real"
        with pytest.raises(UndeclaredCredentialSlot, match="undeclared_slot"):
            await access.get("undeclared_slot")


async def test_credential_access_falls_back_to_platform_env(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared slot with no stored BYOK resolves the platform default from env — the same read a
    turn and a job share, so rotating the deploy value reaches every workspace."""
    monkeypatch.setenv("SAMPLE_API", "sk-platform")
    init_workspace_credentials(_store())
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with ws(await _workspace()):
        assert await access.get("sample_api") == "sk-platform"


async def test_empty_platform_credential_is_unset(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAMPLE_API", "")
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with ws(await _workspace()), pytest.raises(CredentialSlotUnset):
        await access.get("sample_api")


async def test_credential_access_outside_a_workspace_scope_fails_loud(db: None) -> None:
    init_workspace_credentials(_store())
    access = CredentialAccess(declared=frozenset({"sample_api"}))
    with pytest.raises(WorkspaceUnbound):
        await access.get("sample_api")


async def test_core_context_builds_and_is_usable(db: None) -> None:
    with ws(await _workspace()):
        context = context_for("core", frozenset())
        assert context.store.extension == "core"
        await context.store.put("tick", {"count": 1})
        assert await context.store.get("tick") == {"count": 1}
