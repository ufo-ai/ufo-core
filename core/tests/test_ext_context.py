from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import (
    ConversationFiles,
    CredentialAccess,
    ScopedStore,
    UndeclaredCredentialSlot,
    context_for,
)
from ufo.ext.surface import (
    SurfaceInstallationConflict,
    UndeclaredSurface,
    workspace_key,
)
from ufo.sandbox.fs_creds import workspace_key_prefix
from ufo.schema import tables
from ufo.sources.sync import CorePageFeed
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import WorkspaceUnbound, init_workspace_credentials, ws


async def _workspace() -> UUID:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
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
        assert context.installations.declared == frozenset()
        await context.store.put("tick", {"count": 1})
        assert await context.store.get("tick") == {"count": 1}


async def test_installation_access_rejects_a_surface_the_manifest_did_not_declare(
    db: None,
) -> None:
    context = context_for("slack", frozenset())
    with ws(await _workspace()), pytest.raises(UndeclaredSurface, match="slack"):
        await context.installations.bind("slack", "team-a")


async def test_installation_access_preserves_fleet_wide_uniqueness(db: None) -> None:
    first, second = await _workspace(), await _workspace()
    context = context_for("slack", frozenset(), surfaces=frozenset({"slack"}))
    with ws(first):
        await context.installations.bind("slack", "team-a")
        await context.installations.bind("slack", "team-a")
    with ws(second), pytest.raises(SurfaceInstallationConflict, match="slack"):
        await context.installations.bind("slack", "team-a")


async def test_set_source_subject_flips_the_row_and_restamps_live_pages(
    db: None, tmp_path: Path
) -> None:
    """The flip restamps every live page with a fresh `updated_at` so a consumer's stored
    PageFeed cursor — sitting exactly at the page's prior position — replays it; a tombstoned
    page's chunks are already gone, so it is left untouched."""
    workspace_id = await _workspace()
    member_id = uuid4()
    old = datetime(2026, 7, 1, tzinfo=UTC)
    much_older = datetime(2026, 6, 1, tzinfo=UTC)
    source_id, live_id, tombstoned_id = uuid4(), uuid4(), uuid4()
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put("pages/live", b"live body")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@x.test",
                created_at=old,
                updated_at=old,
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={"root": "/x"},
                subject=member_subject(member_id),
                owner_member_id=member_id,
                cursor=None,
                next_sync_at=old,
                created_at=old,
                updated_at=old,
            )
        )
        await connection.execute(
            sa.insert(tables.page),
            [
                {
                    "id": live_id,
                    "workspace_id": workspace_id,
                    "source_id": source_id,
                    "digest": "sha256:live",
                    "body_ref": "pages/live",
                    "subject": member_subject(member_id),
                    "tombstone": False,
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "id": tombstoned_id,
                    "workspace_id": workspace_id,
                    "source_id": source_id,
                    "digest": "sha256:gone",
                    "body_ref": "pages/gone",
                    "subject": member_subject(member_id),
                    "tombstone": True,
                    "created_at": much_older,
                    "updated_at": much_older,
                },
            ],
        )
    feed = CorePageFeed(blob=blob)
    cursor = f"{old.isoformat()}|{live_id}"
    with ws(workspace_id):
        stale = await feed.pages_changed_since(cursor, 10)
        assert stale.changes == ()

        context = context_for("core", frozenset())
        await context.set_source_subject((source_id,), SHARED_SUBJECT)

        with pytest.raises(ValueError):
            await context.set_source_subject((uuid4(),), SHARED_SUBJECT)

        replayed = await feed.pages_changed_since(cursor, 10)
    assert [change.page_id for change in replayed.changes] == [live_id]
    assert replayed.changes[0].subject == SHARED_SUBJECT

    async with workspace_tx() as connection:
        source_row = (
            (
                await connection.execute(
                    sa.select(tables.source).where(tables.source.c.id == source_id)
                )
            )
            .mappings()
            .one()
        )
        pages = {
            row["id"]: row
            for row in (
                await connection.execute(
                    sa.select(tables.page).where(tables.page.c.source_id == source_id)
                )
            )
            .mappings()
            .all()
        }

    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    assert source_row["subject"] == SHARED_SUBJECT
    assert pages[live_id]["subject"] == SHARED_SUBJECT
    assert _aware(pages[live_id]["updated_at"]) > old
    assert pages[tombstoned_id]["subject"] == member_subject(member_id)
    assert _aware(pages[tombstoned_id]["updated_at"]) == much_older


async def test_set_source_subject_flips_every_stream_of_a_binding_in_one_transaction(
    db: None,
) -> None:
    """A binding's streams are distinct source rows; one call flips every row and restamps every
    live page across them in a single transaction, so a multi-stream share can never tear across
    per-stream commits. A tombstoned page is left untouched."""
    workspace_id = await _workspace()
    member_id = uuid4()
    old = datetime(2026, 7, 1, tzinfo=UTC)
    much_older = datetime(2026, 6, 1, tzinfo=UTC)
    first_id, second_id = uuid4(), uuid4()
    live_a, live_b, tombstoned_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@x.test",
                created_at=old,
                updated_at=old,
            )
        )
        await connection.execute(
            sa.insert(tables.source),
            [
                {
                    "id": source_id,
                    "workspace_id": workspace_id,
                    "backend": "greenhouse",
                    "config": {"account": "default", "stream": stream, "base_url": None},
                    "subject": member_subject(member_id),
                    "owner_member_id": member_id,
                    "cursor": None,
                    "next_sync_at": old,
                    "created_at": old,
                    "updated_at": old,
                }
                for source_id, stream in ((first_id, "jobs"), (second_id, "candidates"))
            ],
        )
        await connection.execute(
            sa.insert(tables.page),
            [
                {
                    "id": live_a,
                    "workspace_id": workspace_id,
                    "source_id": first_id,
                    "digest": "sha256:a",
                    "body_ref": "pages/a",
                    "subject": member_subject(member_id),
                    "tombstone": False,
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "id": live_b,
                    "workspace_id": workspace_id,
                    "source_id": second_id,
                    "digest": "sha256:b",
                    "body_ref": "pages/b",
                    "subject": member_subject(member_id),
                    "tombstone": False,
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "id": tombstoned_id,
                    "workspace_id": workspace_id,
                    "source_id": second_id,
                    "digest": "sha256:gone",
                    "body_ref": "pages/gone",
                    "subject": member_subject(member_id),
                    "tombstone": True,
                    "created_at": much_older,
                    "updated_at": much_older,
                },
            ],
        )
    context = context_for("core", frozenset())
    with ws(workspace_id):
        await context.set_source_subject((first_id, second_id), SHARED_SUBJECT)

    async with workspace_tx() as connection:
        sources = {
            row["id"]: row
            for row in (
                await connection.execute(
                    sa.select(tables.source).where(tables.source.c.id.in_((first_id, second_id)))
                )
            )
            .mappings()
            .all()
        }
        pages = {
            row["id"]: row
            for row in (
                await connection.execute(
                    sa.select(tables.page).where(tables.page.c.source_id.in_((first_id, second_id)))
                )
            )
            .mappings()
            .all()
        }

    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    assert {row["subject"] for row in sources.values()} == {SHARED_SUBJECT}
    assert pages[live_a]["subject"] == SHARED_SUBJECT
    assert pages[live_b]["subject"] == SHARED_SUBJECT
    assert _aware(pages[live_a]["updated_at"]) > old
    assert _aware(pages[live_b]["updated_at"]) > old
    assert pages[tombstoned_id]["subject"] == member_subject(member_id)
    assert _aware(pages[tombstoned_id]["updated_at"]) == much_older


async def _conversation(workspace_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=conversation_id.hex,
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


def _files(blob: FilesystemBlobStore) -> ConversationFiles:
    context = context_for("sample", frozenset(), blob=blob)
    assert context.files is not None
    return context.files


async def test_conversation_files_write_lands_under_the_mounted_prefix(
    db: None, tmp_path: Path
) -> None:
    """What an off-turn handler writes is what the agent's next turn sees: the returned path is the
    container path, and the key it landed at is inside the one prefix the sandbox mounts."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        path = await _files(blob).write(conversation_id, ".sources/acme/now.jsonl", b"{}\n")

    assert path == "/workspace/.sources/acme/now.jsonl"
    key = workspace_key(conversation_id, ".sources/acme/now.jsonl")
    assert key.startswith(f"{workspace_key_prefix(conversation_id)}/")
    assert await blob.get(key) == b"{}\n"


@pytest.mark.parametrize("rel", ["../messages.json.lz4", "/etc/passwd", "a/../../escape"])
async def test_conversation_files_refuse_a_path_outside_the_workspace(
    db: None, tmp_path: Path, rel: str
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        with pytest.raises(ValueError):
            await _files(blob).write(conversation_id, rel, b"x")


async def test_conversation_files_refuse_another_workspaces_conversation(
    db: None, tmp_path: Path
) -> None:
    """The conversation is resolved through `workspace_tx`, so one tenant's handler cannot write a
    file into another tenant's agent workspace even holding its id."""
    blob = FilesystemBlobStore(root=tmp_path)
    other = await _workspace()
    with ws(other):
        foreign = await _conversation(other)
    with ws(await _workspace()):
        with pytest.raises(ValueError):
            await _files(blob).write(foreign, "note.txt", b"x")
    assert not await blob.exists(workspace_key(foreign, "note.txt"))


async def test_conversation_files_prune_keeps_the_newest(db: None, tmp_path: Path) -> None:
    """An unattended writer is bounded: prune keeps the newest `keep` entries under the prefix and
    drops the rest, and never reaches a sibling directory."""
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id)
        files = _files(blob)
        for minute in range(5):
            await files.write(conversation_id, f"log/2026-07-26T00:0{minute}.jsonl", b"{}\n")
        await files.write(conversation_id, "log-sibling/keep.jsonl", b"{}\n")
        await files.prune(conversation_id, "log", keep=2)

    remaining = await blob.list(f"{workspace_key_prefix(conversation_id)}/log/")
    assert [entry.key.rsplit("/", 1)[-1] for entry in remaining] == [
        "2026-07-26T00:03.jsonl",
        "2026-07-26T00:04.jsonl",
    ]
    assert await blob.exists(workspace_key(conversation_id, "log-sibling/keep.jsonl"))
