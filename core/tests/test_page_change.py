"""The data-plane page_change seam and its core runner, proven through the installed sample.

The sample registers a page_change hook that records — through its own scoped store — the page ids
each delivered batch carried and whether the runner wired the model into its off-turn context. A
core test seeds a real page, drives the runner, and reads those rows back through the public
ScopedStore: the runner delivers changed pages, advances each consumer's own cursor (a second drive
over the same window delivers only the newly-changed page), and builds the jobs-way context with
the model wired. `core_jobs` registers one `page_change:<ext>:<hook>` job per consumer, so a
consumer that raises fails only its own workflow — proven by driving one consumer that raises and
confirming its
cursor did not advance while a second consumer still makes progress. No mock call-log — a real
consumer records through its capability APIs."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from cryptography.fernet import Fernet

from ufo.accounting import CORE_PRICING
from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore
from ufo.ext.loader import load_manifests
from ufo.ext.manifest import HookContext, HookOutcome, HookSpec, Manifest
from ufo.jobs import (
    CORE_EXTENSION,
    PAGE_CHANGE_CURSOR_KEY,
    PAGE_CHANGE_JOB,
    PageChangeRunner,
    SandboxReaper,
    SpendResume,
    bindings_from,
    core_jobs,
)
from ufo.models.registry import ModelRegistry
from ufo.sandbox.local import LocalCarrier
from ufo.schema import tables
from ufo.sources.sync import CorePageFeed, FolderSource, SyncDriver
from ufo.subjects import SHARED_SUBJECT
from ufo.workspace import ws


def _sample_manifest() -> object:
    found = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert found is not None, "sample extension not discovered — run `uv sync`"
    return found


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_page(blob: FilesystemBlobStore, workspace_id: UUID, body: str) -> UUID:
    source_id, page_id = uuid4(), uuid4()
    when = datetime.now(UTC)
    await blob.put(f"pages/{page_id}", body.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                cursor=None,
                next_sync_at=when,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                body_ref=f"pages/{page_id}",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=when,
                updated_at=when,
            )
        )
    return page_id


def _runner(
    workspace_id: UUID,
    blob: FilesystemBlobStore,
    manifests: tuple[object, ...] = (),
    registry: ModelRegistry | None = None,
) -> PageChangeRunner:
    return PageChangeRunner(
        workspace_id=workspace_id,
        credential_store=CredentialStore(fernet=Fernet(Fernet.generate_key())),
        manifests=manifests or (_sample_manifest(),),
        pages=CorePageFeed(blob=blob),
        registry=registry,
    )


async def _drive_all(runner: PageChangeRunner) -> None:
    for consumer in runner.consumers():
        await runner.drive(consumer)


async def test_runner_delivers_changed_pages_and_advances_the_cursor(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page_one = await _seed_page(blob, workspace_id, "the first page body")
    runner = _runner(workspace_id, blob)
    await _drive_all(runner)

    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        first = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
    assert first == {"page_ids": [str(page_one)], "model_wired": False}

    page_two = await _seed_page(blob, workspace_id, "the second page body")
    await _drive_all(runner)
    with ws(workspace_id):
        second = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
    assert second == {"page_ids": [str(page_two)], "model_wired": False}


async def test_runner_wires_the_model_into_the_off_turn_context(db: None, tmp_path: object) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "a page for model wiring")
    registry = ModelRegistry(providers=(), pricing=CORE_PRICING, auto_model="claude-opus-4-8")
    await _drive_all(_runner(workspace_id, blob, registry=registry))

    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        record = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
    assert record is not None
    assert record["model_wired"] is True


async def _raise(ctx: HookContext) -> HookOutcome:
    raise RuntimeError("page_change consumer exploded")


def test_each_page_change_consumer_registers_as_its_own_job(tmp_path: object) -> None:
    """core_jobs fans a `page_change:<ext>:<hook>` job out per registered consumer, so the memory
    page indexer and the graph extractor are independent DBOS workflows again — each keyed in the
    core namespace under its extension and its handler-name discriminator, so two hooks in one
    extension get two jobs rather than colliding on one name."""
    blob = FilesystemBlobStore(root=tmp_path)
    boom = Manifest(
        name="boom_ext", version="0", hooks=(HookSpec(event="page_change", handler=_raise),)
    )
    runner = PageChangeRunner(
        workspace_id=uuid4(),
        credential_store=CredentialStore(fernet=Fernet(Fernet.generate_key())),
        manifests=(_sample_manifest(), boom),
        pages=CorePageFeed(blob=blob),
    )
    specs = core_jobs(
        SyncDriver(backends={"folder": FolderSource()}, blob=blob, postgres=False),
        SpendResume(client=None),
        SandboxReaper(carrier=LocalCarrier(), backend="local"),
        runner,
    )
    page_change = [spec.name for spec in specs if spec.name.startswith(f"{PAGE_CHANGE_JOB}:")]
    assert page_change == [
        f"{PAGE_CHANGE_JOB}:{sample.NAME}:_record_page_change",
        f"{PAGE_CHANGE_JOB}:boom_ext:_raise",
    ]
    keys = {binding.key for binding in bindings_from((), specs)}
    assert f"{CORE_EXTENSION}:{PAGE_CHANGE_JOB}:{sample.NAME}:_record_page_change" in keys
    assert f"{CORE_EXTENSION}:{PAGE_CHANGE_JOB}:boom_ext:_raise" in keys


async def test_a_failing_consumer_neither_advances_its_cursor_nor_blocks_another(
    db: None, tmp_path: object
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    page = await _seed_page(blob, workspace_id, "a page both consumers replay")
    boom = Manifest(
        name="boom_ext", version="0", hooks=(HookSpec(event="page_change", handler=_raise),)
    )
    runner = _runner(workspace_id, blob, manifests=(boom, _sample_manifest()))
    consumers = {consumer.extension: consumer for consumer in runner.consumers()}
    boom_consumer, sample_consumer = consumers["boom_ext"], consumers[sample.NAME]

    with pytest.raises(RuntimeError):
        await runner.drive(boom_consumer)
    with ws(workspace_id):
        boom_cursor = await ScopedStore(extension="boom_ext").get(
            f"{PAGE_CHANGE_CURSOR_KEY}:{boom_consumer.discriminator}"
        )
    assert boom_cursor is None

    await runner.drive(sample_consumer)
    with ws(workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        record = await scoped.get(sample.HOOK_PAGE_CHANGE_KEY)
        sample_cursor = await scoped.get(
            f"{PAGE_CHANGE_CURSOR_KEY}:{sample_consumer.discriminator}"
        )
    assert record == {"page_ids": [str(page)], "model_wired": False}
    assert isinstance(sample_cursor, str) and sample_cursor != boom_cursor
