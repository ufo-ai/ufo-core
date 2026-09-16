import asyncio
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg
import httpx
import pytest
import sqlalchemy as sa
import sqlalchemy.exc as sa_exc
import ufo_ext_memory.manifest as memory_manifest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MemoryStore, PageIndexer, mem_page, recall_subjects

from ufo.blob import FilesystemBlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.sandbox.session import ExecResult
from ufo.product import PRODUCT_CENSUS_JOB
from ufo.runtime import jobs
from ufo.runtime.access.connectors import Credential
from ufo.runtime.access.grants import INDEX_REAP_KEY_PREFIX, GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.background_tasks import BACKGROUND_TASKS_JOB, BackgroundTaskSweep
from ufo.runtime.billing.accounting import JOB_DAY_ROLLUP_JOB
from ufo.runtime.billing.balance import credit, set_reserve
from ufo.runtime.delivery import DeliverySweep
from ufo.runtime.ext.context import (
    ExtensionContext,
    ScopedStore,
    SourceReader,
    context_for,
)
from ufo.runtime.ext.manifest import (
    PAGE_CHANGE_CURSOR_KEY,
    HookContext,
    HookOutcome,
    HookSpec,
    JobSpec,
    Manifest,
    PageChangeBatch,
)
from ufo.runtime.gravatar import GRAVATAR_JOB
from ufo.runtime.indexing import OWNER_KIND_PAGE, Chunk, IndexScope, TextChunker
from ufo.runtime.jobs import (
    CORE_EXTENSION,
    INDEX_REAP_JOB,
    PAGE_CHANGE_JOB,
    RESULT_DELIVERY_JOB,
    TURN_DISPATCH_JOB,
    JobRunner,
    PageChangeConsumer,
    PageChangeRunner,
    TurnDispatcher,
    bindings_from,
    core_jobs,
    reap_index_queue,
)
from ufo.runtime.sources import rest, sync
from ufo.runtime.sources.backend import ConnectorBackend, ConnectorSourceConfig
from ufo.runtime.sources.connector import (
    PASS_FROM_KEY,
    Connector,
    ParentEdge,
    Partition,
    PartitionBound,
    Run,
    StreamPage,
    StreamSpec,
    WalkPage,
    fanned_out,
)
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    SOURCE_EMPTY_IDLE_THRESHOLD,
    SOURCE_PARK_RETRY_SECONDS,
    SOURCE_REFUSAL_PARK_THRESHOLD,
    SOURCE_SYNC_CHECK,
    SOURCE_SYNC_FAILED_METRIC,
    SOURCE_SYNC_INTERVAL_SECONDS,
    SOURCE_SYNC_JOB,
    SOURCE_SYNC_PARKED_METRIC,
    SYNC_PROVIDER_FAULT_MAX_CHARS,
    ClaimedSource,
    CorePageFeed,
    CursorExpired,
    FolderSource,
    Page,
    PageBatch,
    PageFeed,
    SourceAuth,
    StreamFault,
    StreamSkipped,
    SyncDriver,
    SyncResult,
    feed_handle,
    feed_handle_for,
    register_sources,
    source_body_ref_matches,
)
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws, ws_current
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import Agent, Turn, TurnRuntimeConfig

TOOL_NARRATION = "looking through what they synced"

MEMORY_TOOLS = {tool.name: tool for tool in memory_manifest.manifest().tools}


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """A deterministic stand-in EmbedClient the indexers embed through and search queries through;
    the tests assert the page rows and the SourceMatch snippets, never this stand-in."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


class CountingEmbed:
    """Counts embed calls so a test can witness that a resumed cursor re-embeds nothing already
    indexed — the call count is the only witness; the derived chunks are asserted elsewhere."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector
        self.calls = 0

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return tuple(self._vector for _ in texts)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the source tests")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    agent_id = uuid5(NAMESPACE_URL, f"{workspace_id}/main")
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
                name="main",
                prompt="p",
                model="m",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def _member(workspace_id: UUID, email: str = "owner@example.com") -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def _connection(
    workspace_id: UUID,
    provider: str = FOLDER_BACKEND,
    *,
    account_id: str = "",
    owner_member_id: UUID | None = None,
    shared: bool = True,
    base_url: str | None = None,
) -> UUID:
    async with workspace_tx() as connection:
        held = (
            await connection.execute(
                sa.select(tables.connection.c.id).where(
                    tables.connection.c.workspace_id == workspace_id,
                    tables.connection.c.provider == provider,
                    tables.connection.c.account_id == account_id,
                )
            )
        ).scalar_one_or_none()
        if held is not None:
            return held
        connection_id = uuid4()
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=provider,
                account_id=account_id,
                host="",
                base_url=base_url,
                owner_member_id=owner_member_id,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return connection_id


async def _grant(workspace_id: UUID, agent_id: UUID, connection_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_id,
                connection_id=connection_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


def _wire(
    database_url: str, vector: tuple[float, ...], blob_root: Path, workspace_id: UUID
) -> tuple[SyncDriver, Callable[[], Awaitable[None]], MemoryStore]:
    embed = StubEmbed(vector)
    index = DefaultIndex(transaction=workspace_tx)
    blob = FilesystemBlobStore(root=blob_root)
    feed = CorePageFeed(blob=blob)
    driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    indexer = PageIndexer(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )

    async def index_pages() -> None:
        batch = await feed.pages_changed_since(None, 50)
        with ws(workspace_id):
            await indexer.apply(batch.changes)

    service = MemoryStore(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
        readable_page_states=context_for("memory", frozenset()).readable_page_states,
        readable_source_ids=context_for("memory", frozenset()).readable_source_ids,
    )
    return driver, index_pages, service


async def _register_folder(root: Path) -> None:
    await register_sources(
        (SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(root))),)
    )


async def _sync(driver: SyncDriver) -> None:
    """Drive the sync job through the real dispatch: the source-sync JobSpec's candidate names the
    workspaces holding a due source, and `fire` binds each before running the driver — so the claim,
    fetch, and commit run scoped per workspace and never unbound, exactly as the fleet fires it."""

    async def _handler(context: ExtensionContext) -> None:
        await driver.run()

    spec = JobSpec(
        name=SOURCE_SYNC_JOB,
        schedule=None,
        handler=_handler,
        candidates=driver.candidate_workspaces,
    )
    runner = JobRunner(bindings=bindings_from((), (spec,)), manifests=())
    for workspace_id in await runner.candidates(f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}"):
        await runner.fire(f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}", workspace_id)


async def _fire_page_change(runner: PageChangeRunner, consumer: PageChangeConsumer) -> None:
    """Drive one page-change consumer through the real dispatch: its JobSpec candidate names the
    workspaces with pages changed since the consumer's cursor, and `fire` binds each before running
    the consumer's cursor loop — so the replay runs scoped per workspace and a change-free workspace
    (absent from the candidates) is never opened."""

    async def _handler(context: ExtensionContext) -> None:
        await runner.drive(consumer)

    async def _candidates() -> tuple[UUID, ...]:
        return await runner.workspaces_with_changes(consumer)

    name = f"{PAGE_CHANGE_JOB}:{consumer.extension}:{consumer.discriminator}"
    spec = JobSpec(name=name, schedule=None, handler=_handler, candidates=_candidates)
    job_runner = JobRunner(bindings=bindings_from((), (spec,)), manifests=())
    for workspace_id in await job_runner.candidates(f"{CORE_EXTENSION}:{name}"):
        await job_runner.fire(f"{CORE_EXTENSION}:{name}", workspace_id)


async def _pages() -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.page.c.uid,
                        tables.page.c.source_uid,
                        tables.page.c.stream,
                        tables.page.c.title,
                        tables.page.c.record_created_at,
                        tables.page.c.record_updated_at,
                        tables.page.c.subject,
                        tables.page.c.digest,
                        tables.page.c.body_ref,
                        tables.page.c.tombstone,
                        tables.page.c.indexed,
                        tables.page.c.revision,
                        tables.page.c.updated_at,
                    )
                )
            ).mappings()
        )


async def _chunk_count() -> int:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()


async def _claims(driver: SyncDriver) -> tuple[UUID, ...]:
    """Which sources the driver's next pass would claim, through its own due-selection — so a test
    asserts what the driver will actually do next rather than re-deriving the rule."""
    return tuple(claimed.source_uid for claimed in await driver._claim_due("probe"))


async def _make_due() -> None:
    """Simulate the sync interval elapsing so the next `run()` re-claims the source."""
    async with workspace_tx() as connection:
        await connection.execute(sa.update(tables.source).values(next_sync_at=sa.func.now()))


def _context(memory: MemoryStore, member_id: UUID | None, blob_root: Path) -> ToolContext:
    ext = context_for(
        "memory",
        frozenset(),
        index=memory.index,
        embed=memory.embed,
    )
    return ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=blob_root),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid5(NAMESPACE_URL, f"{memory.workspace_id}/main"),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=ext,
    )


async def _search(memory: MemoryStore, member_id: UUID | None, blob_root: Path, query: str) -> str:
    tool = MEMORY_TOOLS["memory_search"]
    with ws(memory.workspace_id):
        result: ToolResult = await tool.handler(
            _context(memory, member_id, blob_root),
            tool.input_model.model_validate({"queries": [query]}),
        )
    return result.content[0].text


def _reader(
    workspace_id: UUID,
    subjects: frozenset[str],
    requesting_member_id: UUID | None = None,
) -> SourceReader:
    return SourceReader(
        agent_id=uuid5(NAMESPACE_URL, f"{workspace_id}/main"),
        requesting_member_id=requesting_member_id,
        subjects=subjects,
    )


async def test_folder_syncs_a_page_body_to_blob_no_chunk_until_indexed(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "brief.md").write_text("the quarterly revenue target is twelve million dollars")
    driver, index_pages, _ = _wire(database_url, vec((7, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(root)

    await _sync(driver)
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["subject"] == SHARED_SUBJECT
    assert pages[0]["digest"].startswith("sha256:")
    assert pages[0]["tombstone"] is False or pages[0]["tombstone"] == 0
    body = await FilesystemBlobStore(root=tmp_path / "blobs").get(pages[0]["body_ref"])
    assert b"quarterly revenue" in body
    assert await _chunk_count() == 0

    feed = CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs"))
    with ws(workspace_id):
        changes = (await feed.pages_changed_since(None, 50)).changes
    async with workspace_tx() as connection:
        source_uid = await connection.scalar(
            sa.select(tables.source.c.uid).where(tables.source.c.uid == pages[0]["source_uid"])
        )
    assert [change.source_id for change in changes] == [source_uid]
    assert pages[0]["source_uid"] == source_uid

    await index_pages()
    assert await _chunk_count() >= 1


async def test_a_folder_page_carries_the_files_modification_date(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    note = root / "seat-audit.md"
    note.write_text("this workspace holds 52 seats")
    stamped = datetime(2026, 8, 7, 9, 30, tzinfo=UTC).timestamp()
    os.utime(note, (stamped, stamped))
    driver, _, _ = _wire(database_url, vec((7, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(root)

    await _sync(driver)

    page = (await _pages())[0]
    assert page["record_updated_at"].startswith("2026-08-07")
    with ws(workspace_id):
        states = await context_for("memory", frozenset()).page_states((page["uid"],))
    assert states[page["uid"]].as_of == "2026-08-07"


async def test_folder_sync_preserves_bare_carriage_returns(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    body = b"first line\rsecond line"
    (root / "transcript.txt").write_bytes(body)
    driver, _, _ = _wire(database_url, vec((7, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(root)

    await _sync(driver)

    page = (await _pages())[0]
    stored = await FilesystemBlobStore(root=tmp_path / "blobs").get(page["body_ref"])
    assert stored == body
    assert page["digest"] == "sha256:" + hashlib.sha256(body).hexdigest()


async def test_the_driver_stamps_pages_with_its_connections_disclosure(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "note.md").write_text("member scoped note")
    member_id = await _member(workspace_id, "member@example.com")
    connection_id = await _connection(
        workspace_id, owner_member_id=member_id, shared=False, account_id="acct"
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=uuid7(),
                workspace_id=workspace_id,
                backend=FOLDER_BACKEND,
                config={"root": str(root)},
                feed_handle=feed_handle_for({"root": str(root)}, frozenset()),
                connection_id=connection_id,
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    driver, _, _ = _wire(database_url, vec((18, 1.0)), tmp_path / "blobs", workspace_id)

    await _sync(driver)

    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["subject"] == f"member:{member_id}"


async def test_register_source_settles_on_one_row_per_connection_and_config(db: None) -> None:
    """One stream of one connection is one row however many callers register it, and the same
    config under a second connection is a second row — the connection is half the identity, so two
    accounts syncing the same stream never collapse onto each other's pages."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    config = SourceConfig(root="/shared")
    first_connection = await _connection(workspace_id, account_id="one")
    second_connection = await _connection(workspace_id, account_id="two")
    with ws(workspace_id):
        first = await ctx.register_source(FOLDER_BACKEND, config, connection_id=first_connection)
        assert (
            await ctx.register_source(FOLDER_BACKEND, config, connection_id=first_connection)
            == first
        )
        second = await ctx.register_source(FOLDER_BACKEND, config, connection_id=second_connection)
    assert second != first
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.source.c.uid, tables.source.c.connection_id, tables.source.c.feed_handle
                )
            )
        ).all()
    assert {row.uid: row.connection_id for row in rows} == {
        first: first_connection,
        second: second_connection,
    }
    # The same stream under two connections shares one handle, which is why the unique key over it
    # also carries the connection.
    assert {row.feed_handle for row in rows} == {feed_handle(config)}


async def test_register_source_refuses_a_connection_that_is_not_the_backends(db: None) -> None:
    """A source row's backend and its connection's provider are one fact stated twice, so they must
    agree: a `gmail` row hanging off a Slack connection would authenticate its fetch as an account
    that cannot serve it, and its pages would carry a disclosure the member never granted for mail.
    A connection from another workspace fails the same check — the whole model rests on that
    boundary, so registering across it is refused rather than silently scoped away."""
    workspace_id, other = await _workspace(), await _workspace()
    ctx = context_for("probe", frozenset())
    config = SourceConfig(root="/shared")
    wrong_provider = await _connection(workspace_id, "slack", account_id="ca_T0ACME")
    elsewhere = await _connection(other)
    with ws(workspace_id):
        with pytest.raises(ValueError, match="to register a source against"):
            await ctx.register_source(FOLDER_BACKEND, config, connection_id=wrong_provider)
        with pytest.raises(ValueError, match="to register a source against"):
            await ctx.register_source(FOLDER_BACKEND, config, connection_id=elsewhere)
        with pytest.raises(ValueError, match="to register a source against"):
            await ctx.register_source(FOLDER_BACKEND, config, connection_id=uuid4())
    async with workspace_tx() as connection:
        assert (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.source))
        ).scalar_one() == 0


async def test_a_source_row_cannot_exist_without_a_connection(db: None) -> None:
    """`connection_id` is NOT NULL, so there is no such thing as a source row with no authority —
    a row nothing could say the disclosure or the reach of. The database is what holds it, since
    the sync driver reads the connection to stamp every page it commits."""
    workspace_id = await _workspace()
    with pytest.raises(sa_exc.IntegrityError):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.source).values(
                    uid=uuid7(),
                    workspace_id=workspace_id,
                    backend=FOLDER_BACKEND,
                    config={"root": "/shared"},
                    feed_handle=feed_handle_for({"root": "/shared"}, frozenset()),
                    connection_id=None,
                    next_sync_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


async def test_remove_connection_takes_what_an_extension_minted_and_refuses_a_members(
    db: None,
) -> None:
    """The inverse of `register_connection`: a connection an extension minted goes, with its
    streams, when the extension no longer has a reason for it. A member's connection is theirs to
    disconnect, never an extension's to remove, and one already gone is not one to remove twice."""
    workspace_id = await _workspace()
    member_id = await _member(workspace_id)
    ctx = context_for("probe", frozenset())
    with ws(workspace_id):
        minted = await ctx.register_connection(FOLDER_BACKEND)
        await ctx.register_source(
            FOLDER_BACKEND, SourceConfig(root="/shared"), connection_id=minted
        )
        owned = await ctx.register_connection(
            FOLDER_BACKEND, account_id="member-owned", owner_member_id=member_id
        )

        await ctx.remove_connection(minted)

        assert [record.id for record in await ctx.sources()] == []
        with pytest.raises(ValueError, match="not one an extension minted"):
            await ctx.remove_connection(owned)
        with pytest.raises(ValueError, match="not one an extension minted"):
            await ctx.remove_connection(minted)


async def test_register_connection_is_idempotent_and_owned_by_nobody(db: None) -> None:
    """The workspace's own connection to a provider is the authority behind a feed no member owns:
    a BYOK key, a configured folder root, an extension's own feed. It has no account handle, so
    there is one per provider and a repeated boot settles on the row its sources already hang off
    rather than orphaning them under a new one. Nobody owns it, so it is shared — a connection with
    no owner has no member to be private to, which the `connection_shared` check makes true."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    with ws(workspace_id):
        first = await ctx.register_connection(FOLDER_BACKEND)
        assert await ctx.register_connection(FOLDER_BACKEND) == first
        source_id = await ctx.register_source(
            FOLDER_BACKEND, SourceConfig(root="/shared"), connection_id=first
        )
        assert await ctx.register_connection(FOLDER_BACKEND) == first
        assert [record.id for record in await ctx.sources()] == [source_id]
    async with workspace_tx() as connection:
        held = (
            (
                await connection.execute(
                    sa.select(
                        tables.connection.c.account_id,
                        tables.connection.c.owner_member_id,
                        tables.connection.c.shared,
                        tables.connection.c.base_url,
                    ).where(tables.connection.c.id == first)
                )
            )
            .mappings()
            .one()
        )
    assert held["account_id"] == ""
    assert held["owner_member_id"] is None
    assert held["shared"]
    assert held["base_url"] is None


def _check_source_body_ref_matches_the_claim_scoped_page_digest() -> None:
    source_id, page_id = uuid4(), uuid4()
    digest = f"sha256:{'a' * 64}"

    assert source_body_ref_matches(
        f"sources/{source_id}/{page_id}/{'b' * 32}/{'a' * 64}", source_id, page_id, digest
    )
    assert not source_body_ref_matches(
        f"sources/{source_id}/{page_id}/{'a' * 64}", source_id, page_id, digest
    )


async def test_register_source_refuses_a_live_row_on_a_different_window(db: None) -> None:
    """The window is deliberately not part of the row id, so two turns registering one stream of
    one connection on different windows race onto the same row: the first insert lands and the
    second conflicts. The
    loser must be told, under the same `for update` lock the authority check takes — the pages that
    row syncs were selected by the window it holds, so silently keeping the first window would
    report a binding that was never created, and a binding of several streams could end up with its
    rows on two windows. Re-registering the window the row already holds stays the no-op it has
    always been."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    owner_id = await _member(workspace_id)
    connection_id = await _connection(
        workspace_id, "gmail", account_id="acct", owner_member_id=owner_id, shared=False
    )
    seven = ConnectorSourceConfig(
        stream="messages",
        backfill_days=7,
        backfill_after=datetime(2026, 7, 30, 3, 50, 40, tzinfo=UTC),
    )
    ninety = ConnectorSourceConfig(
        stream="messages",
        backfill_days=90,
        backfill_after=datetime(2026, 5, 8, 3, 50, 40, tzinfo=UTC),
    )
    with ws(workspace_id):
        source_id = await ctx.register_source("gmail", seven, connection_id=connection_id)
        with pytest.raises(ValueError, match="asking for a different backfill_days"):
            await ctx.register_source("gmail", ninety, connection_id=connection_id)
        assert await ctx.register_source("gmail", seven, connection_id=connection_id) == source_id
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.source.c.config).where(tables.source.c.uid == source_id)
            )
        ).scalar_one()
    assert stored["backfill_days"] == 7
    assert stored["backfill_after"] == seven.backfill_after.isoformat().replace("+00:00", "Z")


async def test_a_losing_racer_on_one_stream_set_creates_no_row_at_all(db: None) -> None:
    """What keeps two concurrent registrations of ONE connection's streams off two windows is not a
    transaction around the connection — each `register_source` is its own — but the order the
    streams are registered in. The extension registers them sorted, so both racers contend for the
    same stream first; whoever loses it is refused before it has created any other, and the
    connection's streams are left whole on the winner's window rather than split across both.

    That argument holds only for racers submitting the same stream set. A racer whose set is a
    superset creates the streams the other never asked for before it reaches the contested one, and
    can still split a connection — the same exposure a differing stream set already carries, and
    the reason the guarantee is stated as narrowly as it is rather than as "cannot split"."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    owner_id = await _member(workspace_id)
    connection_id = await _connection(
        workspace_id, "outlook", account_id="acct", owner_member_id=owner_id, shared=False
    )
    ordered = sorted(("messages", "conversations"))

    def streams(days: int) -> list[ConnectorSourceConfig]:
        return [
            ConnectorSourceConfig(
                stream=stream,
                backfill_days=days,
                backfill_after=datetime(2026, 7, 30, tzinfo=UTC) - timedelta(days=days),
            )
            for stream in ordered
        ]

    with ws(workspace_id):
        for config in streams(7):
            await ctx.register_source("outlook", config, connection_id=connection_id)
        with pytest.raises(ValueError, match="asking for a different backfill_days"):
            for config in streams(30):
                await ctx.register_source("outlook", config, connection_id=connection_id)
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.source.c.config).where(
                    tables.source.c.workspace_id == workspace_id,
                    tables.source.c.backend == "outlook",
                )
            )
        ).all()
    assert len(rows) == 2
    assert {row.config["backfill_days"] for row in rows} == {7}


async def test_rewindow_sources_breaks_the_claim_of_a_sync_already_in_flight(db: None) -> None:
    """Widening a row that a sync is already running would otherwise be undone by that run. It
    completes by writing `cursor=next_cursor` under `claimed_by == its claim`, so it puts the row
    back on the delta path it was just taken off — and nothing reports that: the stored config and
    `status` name the wider window, the mail between the old floor and the new one is never
    fetched, and re-applying the same request matches what is stored and does nothing, so there is
    no way back through the supported path.

    Breaking the claim with the cursor makes that write match no row. This drives the real
    completion write's predicate rather than asserting on the column, so it fails if the claim is
    left in place. `next_sync_at` needs no equivalent — `_rescheduled` already preserves a value
    set after the claim was taken."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    owner_id = await _member(workspace_id)
    connection_id = await _connection(
        workspace_id, "gmail", account_id="acct", owner_member_id=owner_id, shared=False
    )
    config = ConnectorSourceConfig(
        stream="messages",
        backfill_days=7,
        backfill_after=datetime(2026, 7, 30, tzinfo=UTC),
    )
    claim = "worker-mid-flight"
    with ws(workspace_id):
        source_id = await ctx.register_source("gmail", config, connection_id=connection_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(
                    cursor="9001",
                    claimed_by=claim,
                    claim_expires_at=datetime.now(UTC) + timedelta(seconds=300),
                )
                .where(tables.source.c.uid == source_id)
            )
        widened = config.model_copy(
            update={"backfill_days": 365, "backfill_after": datetime(2026, 1, 30, tzinfo=UTC)}
        )
        await ctx.rewindow_sources({source_id: widened}, refetch=frozenset({source_id}))

        async with workspace_tx() as connection:
            landed = await connection.execute(
                sa.update(tables.source)
                .values(cursor="9002", claimed_by=None, claim_expires_at=None)
                .where(
                    tables.source.c.uid == source_id,
                    tables.source.c.claimed_by == claim,
                )
            )
            row = (
                await connection.execute(
                    sa.select(tables.source.c.cursor, tables.source.c.config).where(
                        tables.source.c.uid == source_id
                    )
                )
            ).one()
    assert landed.rowcount == 0  # the stale run's write matched nothing
    assert row.cursor is None  # so the widened backfill still runs
    assert row.config["backfill_days"] == 365


async def test_rewindow_sources_refuses_a_config_that_would_move_the_row(db: None) -> None:
    """`rewindow_sources` rewrites a live row's config in place, so the one thing it must not admit
    is an edit to a field the row id is derived from: the row would keep its id while its config
    hashed to a different one, and every later registration of that binding would mint a second row
    rather than settle on it — a corruption nothing downstream could detect. Re-hashing each config
    against the row it is written to catches that, and the non-identity fields it exists to move
    still go through. `refetch` clears only the cursor of the rows it names."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    owner_id = await _member(workspace_id)
    connection_id = await _connection(
        workspace_id, "gmail", account_id="acct", owner_member_id=owner_id, shared=False
    )
    config = ConnectorSourceConfig(stream="messages", backfill_days=7)
    with ws(workspace_id):
        source_id = await ctx.register_source("gmail", config, connection_id=connection_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(cursor="9001", partition_cursor="{}")
                .where(tables.source.c.uid == source_id)
            )
        with pytest.raises(ValueError, match="would change which dataset"):
            await ctx.rewindow_sources(
                {source_id: config.model_copy(update={"stream": "contacts"})}
            )
        async with workspace_tx() as connection:
            untouched = (
                await connection.execute(
                    sa.select(tables.source.c.config, tables.source.c.cursor).where(
                        tables.source.c.uid == source_id
                    )
                )
            ).one()
        assert untouched.config["stream"] == "messages"
        assert untouched.cursor == "9001"

        widened = config.model_copy(
            update={"backfill_days": 90, "backfill_after": datetime(2026, 5, 8, tzinfo=UTC)}
        )
        await ctx.rewindow_sources({source_id: widened}, refetch=frozenset({source_id}))
        async with workspace_tx() as connection:
            moved = (
                await connection.execute(
                    sa.select(
                        tables.source.c.config,
                        tables.source.c.cursor,
                        tables.source.c.partition_cursor,
                    ).where(tables.source.c.uid == source_id)
                )
            ).one()
    assert moved.config["backfill_days"] == 90
    assert moved.cursor is None
    assert moved.partition_cursor is None


async def test_register_source_settles_two_racers_that_asked_for_the_same_window(db: None) -> None:
    """Two turns asking for the same seven days resolve them against their own `now`, so their pins
    differ by however long separated the two applies. They asked for one window, so the loser has to
    settle on the row and read back the pin the winner stored: refusing here would report a window
    change nobody asked for, over microseconds. What the comparison refuses is a different request,
    which is the case above."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    connection_id = await _connection(workspace_id, "gmail")
    winner = ConnectorSourceConfig(
        stream="messages",
        backfill_days=7,
        backfill_after=datetime(2026, 7, 30, 3, 50, 40, 118_000, tzinfo=UTC),
    )
    loser = ConnectorSourceConfig(
        stream="messages",
        backfill_days=7,
        backfill_after=datetime(2026, 7, 30, 3, 50, 40, 402_931, tzinfo=UTC),
    )
    with ws(workspace_id):
        source_id = await ctx.register_source("gmail", winner, connection_id=connection_id)
        assert await ctx.register_source("gmail", loser, connection_id=connection_id) == source_id
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.source.c.config).where(tables.source.c.uid == source_id)
            )
        ).scalar_one()
    assert stored["backfill_after"] == winner.backfill_after.isoformat().replace("+00:00", "Z")


async def test_register_source_settles_on_a_live_row_that_reaches_all_history(db: None) -> None:
    """A stream whose window fields are unset reaches all history, and re-registering it settles on
    the row already doing that rather than refusing: what the comparison holds a live row to is the
    window a caller asked for, and `None` is a request like any other. The row keeps its cursor, so
    the pages it already synced are not re-walked."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    connection_id = await _connection(workspace_id, "gmail", account_id="acct")
    unwindowed = ConnectorSourceConfig(stream="messages")
    with ws(workspace_id):
        source_id = await ctx.register_source("gmail", unwindowed, connection_id=connection_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(cursor="9001")
                .where(tables.source.c.uid == source_id)
            )
        assert (
            await ctx.register_source("gmail", unwindowed, connection_id=connection_id) == source_id
        )
        with pytest.raises(ValueError, match="asking for a different backfill_days"):
            await ctx.register_source(
                "gmail",
                ConnectorSourceConfig(stream="messages", backfill_days=7),
                connection_id=connection_id,
            )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.source.c.config, tables.source.c.cursor).where(
                    tables.source.c.uid == source_id
                )
            )
        ).one()
    assert row.config["backfill_days"] is None
    assert row.cursor == "9001"


async def _configured_roots(workspace_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.source.c.uid.label("source_uid"),
                        tables.source.c.config,
                        tables.connection.c.id.label("connection_id"),
                        tables.connection.c.provider,
                        tables.connection.c.account_id,
                        tables.connection.c.owner_member_id,
                        tables.connection.c.shared,
                    )
                    .select_from(
                        tables.source.join(
                            tables.connection,
                            tables.source.c.connection_id == tables.connection.c.id,
                        )
                    )
                    .where(tables.source.c.workspace_id == workspace_id)
                )
            )
            .mappings()
            .all()
        )


async def test_boot_registered_folder_roots_are_each_their_own_connection(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """Every `[[sources]]` root is a connection of its own, keyed by `feed_handle` of its config —
    ownerless and shared, which is what makes the pages it syncs readable by the workspace — and a
    second boot settles each root back onto its own connection rather than minting another. One
    connection per root is what keeps removal contained: a `page` follows its source and a source
    follows its connection by cascade, so disconnecting one root's connection takes that root's
    rows and leaves the neighbouring root's source and pages standing."""
    workspace_id = await _workspace()
    first, second = tmp_path / "src", tmp_path / "notes"
    first.mkdir()
    second.mkdir()
    (first / "a.md").write_text("first root")
    (second / "b.md").write_text("second root")
    configured = tuple(
        SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(root)))
        for root in (first, second)
    )
    await register_sources(configured)
    await register_sources(configured)

    rows = await _configured_roots(workspace_id)
    by_root = {row["config"]["root"]: row for row in rows}
    assert set(by_root) == {str(first), str(second)}
    assert len({row["connection_id"] for row in rows}) == 2
    assert all(row["provider"] == FOLDER_BACKEND for row in rows)
    for entry in configured:
        assert by_root[entry.config.root]["account_id"] == feed_handle(entry.config)
    assert all(row["owner_member_id"] is None for row in rows)
    assert all(row["shared"] for row in rows)

    driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    await _sync(driver)
    assert {page["source_uid"] for page in await _pages()} == {
        by_root[str(first)]["source_uid"],
        by_root[str(second)]["source_uid"],
    }

    admin = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=admin,
                workspace_id=workspace_id,
                email="admin@example.com",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        assert await GrantStore().disconnect(
            by_root[str(first)]["connection_id"], actor_member_id=admin
        )
    remaining = await _configured_roots(workspace_id)
    assert [row["source_uid"] for row in remaining] == [by_root[str(second)]["source_uid"]]
    assert [row["connection_id"] for row in remaining] == [by_root[str(second)]["connection_id"]]
    assert [(page["source_uid"], page["title"]) for page in await _pages()] == [
        (by_root[str(second)]["source_uid"], "b.md")
    ]


async def test_synced_page_content_is_found_via_memory_search(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "wiki.md").write_text("the office fire assembly point is the north car park")
    driver, index_pages, service = _wire(
        database_url, vec((8, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()

    found = await _search(service, uuid4(), tmp_path / "blobs", "fire assembly point")
    assert "[source]" in found
    assert "north car park" in found


async def test_disconnect_reaps_the_index_of_the_pages_its_cascade_takes(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "wiki.md").write_text("the office fire assembly point is the north car park")
    driver, index_pages, service = _wire(
        database_url, vec((8, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    connection_id = (await _configured_roots(workspace_id))[0]["connection_id"]
    admin = await _member(workspace_id, "admin@example.com")
    async with workspace_tx() as connection:
        page_uid = (await connection.execute(sa.select(tables.page.c.uid))).scalar_one()
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == admin)
        )
    scope = IndexScope(OWNER_KIND_PAGE, str(page_uid))

    with ws(workspace_id):
        assert await service.index.has_chunks(scope)
        assert await GrantStore().disconnect(connection_id, actor_member_id=admin)
        assert await service.index.has_chunks(scope)
        await reap_index_queue(ScopedStore(extension=CORE_EXTENSION), service.index)
        assert not await service.index.has_chunks(scope)


@dataclass(frozen=True)
class _IndexRefusingAfterOne(DefaultIndex):
    deleted: list[str] = field(default_factory=list)

    async def delete(self, scope: IndexScope) -> None:
        if self.deleted:
            raise RuntimeError("index unavailable")
        self.deleted.append(scope.owner_id)
        await super().delete(scope)


async def test_a_faulted_index_reap_keeps_what_is_left_and_the_next_tick_finishes_it(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "INDEX_REAP_BATCH", 1)
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "wiki.md").write_text("the office fire assembly point is the north car park")
    (root / "map.md").write_text("the north car park is behind the loading bay")
    (root / "bay.md").write_text("the loading bay is locked outside office hours")
    driver, index_pages, service = _wire(
        database_url, vec((8, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    connection_id = (await _configured_roots(workspace_id))[0]["connection_id"]
    admin = await _member(workspace_id, "admin@example.com")
    async with workspace_tx() as connection:
        page_uids = (await connection.execute(sa.select(tables.page.c.uid))).scalars().all()
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == admin)
        )
    scopes = [IndexScope(OWNER_KIND_PAGE, str(page_uid)) for page_uid in page_uids]
    assert len(scopes) == 3
    store = ScopedStore(extension=CORE_EXTENSION)

    with ws(workspace_id):
        assert await GrantStore().disconnect(connection_id, actor_member_id=admin)
        refusing = _IndexRefusingAfterOne(transaction=workspace_tx)
        with pytest.raises(RuntimeError):
            await reap_index_queue(store, refusing)
        assert len(refusing.deleted) == 1
        assert [len(value) for _, value in await store.list(INDEX_REAP_KEY_PREFIX)] == [2]
        assert [await service.index.has_chunks(scope) for scope in scopes].count(True) == 2
        await reap_index_queue(store, service.index)
        assert [await service.index.has_chunks(scope) for scope in scopes] == [False, False, False]
        assert await store.list(INDEX_REAP_KEY_PREFIX) == ()


async def test_unchanged_doc_resync_does_not_reindex_or_duplicate(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "note.md").write_text("the mascot is a friendly otter named pip")
    driver, index_pages, _ = _wire(database_url, vec((9, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    stamped = (await _pages())[0]["updated_at"]
    chunks = await _chunk_count()

    await _make_due()
    await _sync(driver)
    resynced = await _pages()
    assert len(resynced) == 1
    assert resynced[0]["updated_at"] == stamped
    await index_pages()
    assert await _chunk_count() == chunks


async def test_changed_doc_resync_marks_due_and_reindexes(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    doc = root / "spec.md"
    doc.write_text("the release date is friday")
    driver, index_pages, service = _wire(
        database_url, vec((10, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    first_digest = (await _pages())[0]["digest"]

    doc.write_text("the release date is monday")
    await _make_due()
    await _sync(driver)
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["digest"] != first_digest
    await index_pages()
    found = await _search(service, uuid4(), tmp_path / "blobs", "release date monday")
    assert "monday" in found


async def _set_archived(agent_id: UUID, archived: bool) -> None:
    """Archive and restore the way the verb does: the row takes an internal name and keeps the
    member-facing one beside it, which the table's CHECK holds to."""
    values = (
        {
            "name": f"~archived-{agent_id}",
            "archived_name": tables.agent.c.name,
            "archived_at": sa.func.now(),
        }
        if archived
        else {
            "name": tables.agent.c.archived_name,
            "archived_name": None,
            "archived_at": None,
        }
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(**values, updated_at=sa.func.now())
            .where(tables.agent.c.id == agent_id)
        )


async def test_a_source_no_live_agent_can_read_stops_syncing(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A private connection's streams are a feed for the agents granted that connection. Archive
    every one of them and each pass still costs a fetch, a page write, and the model tokens the
    page's facts are extracted with — for a feed no turn can reach. So the sweep leaves it alone,
    and takes it up again on the pass after a restore returns it a reader. A shared connection is
    different: the main agent reads it with no grant, so the test beside this one keeps it
    syncing."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "first.md").write_text("the first note")
    driver, _index, _service = _wire(database_url, vec((21, 1.0)), tmp_path / "blobs", workspace_id)
    research_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=research_id,
                workspace_id=workspace_id,
                name="research",
                prompt="p",
                model="m",
                is_main=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    owner_id = await _member(workspace_id)
    connection_id = await _connection(
        workspace_id, account_id="acct", owner_member_id=owner_id, shared=False
    )
    await _grant(workspace_id, research_id, connection_id)
    with ws(workspace_id):
        source_id = await context_for("probe", frozenset()).register_source(
            FOLDER_BACKEND, SourceConfig(root=str(root)), connection_id=connection_id
        )
    await _sync(driver)
    assert len(await _pages()) == 1

    await _set_archived(research_id, True)
    (root / "second.md").write_text("the second note")
    await _make_due()
    assert await driver.candidate_workspaces() == ()
    with ws(workspace_id):
        assert await _claims(driver) == ()
    await _sync(driver)
    assert len(await _pages()) == 1

    await _set_archived(research_id, False)
    await _make_due()
    assert await driver.candidate_workspaces() == (workspace_id,)
    await _sync(driver)
    assert {page["title"] for page in await _pages()} == {"first.md", "second.md"}
    assert source_id is not None


async def test_a_shared_source_keeps_syncing_for_the_main_agent_after_its_grantee_is_archived(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The main agent reads every shared connection's streams without a grant, so a shared
    connection always has a reader while a main agent lives: archiving its only grantee changes
    nothing about the sweep, and the main agent never answers members from pages a stopped feed
    left behind."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "first.md").write_text("the first note")
    driver, _index, _service = _wire(database_url, vec((23, 1.0)), tmp_path / "blobs", workspace_id)
    research_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=research_id,
                workspace_id=workspace_id,
                name="research",
                prompt="p",
                model="m",
                is_main=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    connection_id = await _connection(workspace_id, account_id="acct")
    await _grant(workspace_id, research_id, connection_id)
    with ws(workspace_id):
        await context_for("probe", frozenset()).register_source(
            FOLDER_BACKEND, SourceConfig(root=str(root)), connection_id=connection_id
        )
    await _sync(driver)
    assert len(await _pages()) == 1

    await _set_archived(research_id, True)
    (root / "second.md").write_text("the second note")
    await _make_due()
    assert await driver.candidate_workspaces() == (workspace_id,)
    await _sync(driver)
    assert {page["title"] for page in await _pages()} == {"first.md", "second.md"}


async def test_a_source_a_second_live_agent_reads_keeps_syncing(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """One connection's streams serve every agent granted that connection, so archiving one grantee
    settles nothing about the feed. While any live agent still reads it, the sweep treats it exactly
    as before — and the connection here is private, so the answer is that second grantee rather than
    the main agent's read of anything shared."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "first.md").write_text("the first note")
    driver, _index, _service = _wire(database_url, vec((22, 1.0)), tmp_path / "blobs", workspace_id)
    research_id, sales_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        for agent_id, name in ((research_id, "research"), (sales_id, "sales")):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="m",
                    is_main=False,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    owner_id = await _member(workspace_id)
    connection_id = await _connection(
        workspace_id, account_id="acct", owner_member_id=owner_id, shared=False
    )
    for agent_id in (research_id, sales_id):
        await _grant(workspace_id, agent_id, connection_id)
    with ws(workspace_id):
        await context_for("probe", frozenset()).register_source(
            FOLDER_BACKEND, SourceConfig(root=str(root)), connection_id=connection_id
        )
    await _sync(driver)

    await _set_archived(research_id, True)
    (root / "second.md").write_text("the second note")
    await _make_due()
    assert await driver.candidate_workspaces() == (workspace_id,)
    await _sync(driver)
    assert {page["title"] for page in await _pages()} == {"first.md", "second.md"}


async def test_edited_page_leaves_no_stale_chunk_in_search_sources(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """Re-indexing an edited page prunes the old body's chunks: a term unique to the prior body no
    longer surfaces through `search_sources`, while the new body's term does. Without the prune the
    orphaned old chunk stays indexed under the same page and returns as a stale snippet."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    doc = root / "spec.md"
    doc.write_text("the launch codename is thunderbird")
    driver, index_pages, service = _wire(
        database_url, vec((16, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    with ws(workspace_id):
        before = await service.search_sources(
            "launch codename",
            frozenset({SHARED_SUBJECT}),
            8,
            source_reader=_reader(workspace_id, frozenset({SHARED_SUBJECT})),
        )
        assert before and "thunderbird" in before[0].text

    doc.write_text("the launch codename is nighthawk")
    await _make_due()
    await _sync(driver)
    await index_pages()

    with ws(workspace_id):
        matches = await service.search_sources(
            "launch codename thunderbird",
            frozenset({SHARED_SUBJECT}),
            8,
            source_reader=_reader(workspace_id, frozenset({SHARED_SUBJECT})),
        )
    assert matches and all("thunderbird" not in match.text for match in matches)
    assert "nighthawk" in matches[0].text


async def test_removed_file_tombstones_page_and_index_drops_its_chunks(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "keep.md").write_text("penguins huddle for warmth in antarctica")
    gone = root / "gone.md"
    gone.write_text("volcano magma chamber pressure readings")
    driver, index_pages, service = _wire(
        database_url, vec((11, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    assert "magma" in await _search(service, uuid4(), tmp_path / "blobs", "volcano magma chamber")

    gone.unlink()
    await _make_due()
    await _sync(driver)
    tombstoned = [p for p in await _pages() if p["tombstone"] not in (False, 0)]
    assert len(tombstoned) == 1
    await index_pages()

    blobs = tmp_path / "blobs"
    assert "magma" not in await _search(service, uuid4(), blobs, "volcano magma chamber")
    assert "penguins" in await _search(service, uuid4(), blobs, "penguins antarctica")


async def test_page_change_runner_cursor_resumes_across_ticks(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The core page-change runner rides each consumer's own cursor, so a later tick re-embeds
    nothing already indexed and picks up only the pages changed since — the embed call count is the
    witness. Driven with the real memory page-indexer consumer over the real feed (the memory
    manifest registers two page_change hooks; this selects the indexer by its discriminator),
    proving the runner's producer and the consumer end to end."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.md").write_text("alpha document about apples")
    embed = CountingEmbed(vec((15, 1.0)))
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(backends={FOLDER_BACKEND: FolderSource()}, blob=blob, postgres=postgres)
    runner = PageChangeRunner(
        manifests=(memory_manifest.manifest(),),
        pages=CorePageFeed(blob=blob),
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
    )
    consumer = next(c for c in runner.consumers() if c.discriminator == "index_pages")

    await _register_folder(root)
    await _sync(driver)
    with ws(workspace_id):
        await runner.drive(consumer)
    indexed_a = embed.calls
    assert indexed_a >= 1

    with ws(workspace_id):
        await runner.drive(consumer)
    assert embed.calls == indexed_a

    (root / "b.md").write_text("beta document about bananas")
    await _make_due()
    await _sync(driver)
    with ws(workspace_id):
        await runner.drive(consumer)
    assert embed.calls == indexed_a + 1


PROBE_EXTENSION = "page_change_probe"
SEEN_PAGES_KEY = "seen_pages"


async def record_pages(ctx: HookContext) -> HookOutcome:
    """A `page_change` probe consumer: records the page ids it was handed into its own scoped store,
    so a test reads back — through the public store, scoped to the ambient workspace — which pages
    each workspace's drive replayed. The discriminator is this handler's `__name__`."""
    assert isinstance(ctx.payload, PageChangeBatch)
    await ctx.ext.store.put(
        SEEN_PAGES_KEY, ",".join(sorted(str(change.page_id) for change in ctx.payload.changes))
    )
    return None


async def _seed_page(workspace_id: UUID) -> UUID:
    """A workspace with one source and one (tombstoned) page — enough for the runner to enumerate
    the workspace and replay the page; tombstoned so the feed inlines an empty body without a blob.
    Returns the page id the drive should replay to this workspace."""
    source_uid = uuid7()
    page_uid = uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    connection_id = await _connection(workspace_id, FOLDER_BACKEND)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_uid,
                workspace_id=workspace_id,
                backend=FOLDER_BACKEND,
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=connection_id,
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_uid,
                workspace_id=workspace_id,
                source_uid=source_uid,
                digest="d",
                body_ref="",
                subject=SHARED_SUBJECT,
                tombstone=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return page_uid


@dataclass
class _RecordingFeed:
    """Wraps the real `CorePageFeed` and records the workspace each `pages_changed_since` runs
    under — the witness of which workspaces a drive actually opened, since the feed is read only
    inside `drive`, under the `with ws(...)` block the dispatcher opens per candidate. A real feed
    delegated to, never a fake: the delivered pages come from Postgres/SQLite as always; this only
    observes the scope."""

    inner: CorePageFeed
    opened: list[UUID] = field(default_factory=list)

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch:
        self.opened.append(ws_current().workspace_id)
        return await self.inner.pages_changed_since(cursor, limit)


def _probe_runner(tmp_path: Path, pages: PageFeed | None = None) -> PageChangeRunner:
    manifest = Manifest(
        name=PROBE_EXTENSION,
        version="0",
        hooks=(HookSpec(event="page_change", handler=record_pages),),
    )
    return PageChangeRunner(
        manifests=(manifest,),
        pages=pages or CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs")),
    )


async def test_page_change_drive_enumerates_workspaces_with_changes(
    db: None, tmp_path: Path
) -> None:
    """One consumer's drive finds every workspace with pages changed since its cursor through the
    single owner_tx read (two freshly-seeded workspaces here, neither driven before) and runs the
    cursor loop bound to each — no caller binds a workspace. Each workspace's probe records its own
    page into its own scoped store, so two stores written, each carrying its own page, is the
    witness the drive fanned per workspace and scoped each."""
    ws_a, ws_b = uuid4(), uuid4()
    page_a = await _seed_page(ws_a)
    page_b = await _seed_page(ws_b)
    runner = _probe_runner(tmp_path)
    (consumer,) = runner.consumers()

    await _fire_page_change(runner, consumer)

    with ws(ws_a):
        seen_a = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    with ws(ws_b):
        seen_b = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(seen_a, str) and str(page_a) in seen_a
    assert isinstance(seen_b, str) and str(page_b) in seen_b


async def test_page_change_drive_skips_a_workspace_unchanged_since_its_cursor(
    db: None, tmp_path: Path
) -> None:
    """Selectivity: a workspace that holds pages but has none changed since this consumer's cursor
    is never opened. The recording feed logs the workspace each drive opens. ws_a is seeded and
    drained by a first drive (opened once); then ws_b is seeded fresh. The second drive opens ws_b
    alone — ws_a, still holding its page but unchanged since its cursor, is not a candidate, so no
    transaction runs against it on the tick."""
    ws_a, ws_b = uuid4(), uuid4()
    page_a = await _seed_page(ws_a)
    feed = _RecordingFeed(inner=CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs")))
    runner = _probe_runner(tmp_path, pages=feed)
    (consumer,) = runner.consumers()

    await _fire_page_change(runner, consumer)
    assert feed.opened == [ws_a]
    with ws(ws_a):
        drained = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(drained, str) and str(page_a) in drained

    page_b = await _seed_page(ws_b)
    feed.opened.clear()
    await _fire_page_change(runner, consumer)
    assert feed.opened == [ws_b]
    with ws(ws_b):
        seen_b = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(seen_b, str) and str(page_b) in seen_b


async def test_page_change_candidates_isolate_an_invalid_cursor(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A workspace whose stored cursor predates the revision-based format (or is otherwise
    corrupt) counts as pending rather than aborting the fleet-wide candidate read — one broken
    workspace must not stop every other workspace's tick from finding its own pending work — and
    names itself, its consumer and its extension in `jobs.page_change_cursor_invalid`, the one
    record that reaches an operator before the failure reappears inside a per-workspace drive.
    Reading the cursor stays fail-loud: the broken workspace's own drive still raises, so it never
    advances past pages it did not deliver."""
    broken_id, healthy_id = uuid4(), uuid4()
    await _seed_page(broken_id)
    healthy_page = await _seed_page(healthy_id)
    runner = _probe_runner(tmp_path)
    (consumer,) = runner.consumers()
    with ws(broken_id):
        await ScopedStore(extension=PROBE_EXTENSION).put(
            f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}",
            f"{datetime(2030, 1, 1, tzinfo=UTC).isoformat()}|{uuid4()}",
        )

    with caplog.at_level(logging.WARNING, logger="ufo"):
        pending = await runner.workspaces_with_changes(consumer)
    assert set(pending) == {broken_id, healthy_id}
    record = next(
        record for record in caplog.records if record.message == "jobs.page_change_cursor_invalid"
    )
    assert record.ufo == {
        "workspace_id": str(broken_id),
        "extension": PROBE_EXTENSION,
        "discriminator": consumer.discriminator,
    }

    with ws(healthy_id):
        await runner.drive(consumer)
        seen = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(seen, str) and str(healthy_page) in seen

    with ws(broken_id), pytest.raises(ValueError, match="invalid page cursor"):
        await runner.drive(consumer)


async def test_shared_page_scoping_excludes_a_member_only_search(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "policy.md").write_text("expense reports are due on the last business day")
    driver, index_pages, service = _wire(
        database_url, vec((12, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()

    with ws(workspace_id):
        shared = await service.search_sources(
            "expense reports due",
            frozenset({SHARED_SUBJECT}),
            8,
            source_reader=_reader(workspace_id, frozenset({SHARED_SUBJECT})),
        )
        assert len(shared) == 1 and "expense reports" in shared[0].text

        member_only = await service.search_sources(
            "expense reports due",
            frozenset({member_subject(uuid4())}),
            8,
            source_reader=_reader(workspace_id, frozenset({member_subject(uuid4())})),
        )
        assert member_only == ()

        with_shared = await service.search_sources(
            "expense reports due",
            recall_subjects(conversation_audience(uuid4())),
            8,
            source_reader=_reader(
                workspace_id,
                recall_subjects(conversation_audience(uuid4())),
            ),
        )
        assert len(with_shared) == 1


async def test_member_scoped_page_is_invisible_to_another_member(
    db: None, database_url: str, tmp_path: Path
) -> None:
    source_uid = uuid7()
    workspace_id = await _workspace()
    alice = await _member(workspace_id, "alice@example.com")
    bob = await _member(workspace_id, "bob@example.com")
    probe = vec((13, 1.0))
    page_id, page_uid = uuid4(), uuid7()
    connection_id = await _connection(
        workspace_id, account_id="alice", owner_member_id=alice, shared=False
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_uid,
                workspace_id=workspace_id,
                backend=FOLDER_BACKEND,
                config={"root": "/seed"},
                feed_handle=feed_handle_for({"root": "/seed"}, frozenset()),
                connection_id=connection_id,
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_uid,
                workspace_id=workspace_id,
                source_uid=source_uid,
                digest="sha256:seed",
                body_ref="sources/seed",
                subject=member_subject(alice),
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        revision = await connection.scalar(
            sa.select(tables.page.c.revision).where(tables.page.c.uid == page_uid)
        )
        await connection.execute(
            sa.insert(mem_page).values(
                page_uid=page_uid,
                workspace_id=workspace_id,
                subject=member_subject(alice),
                revision=revision,
                created_at=sa.func.now(),
            )
        )
    _, _, service = _wire(database_url, probe, tmp_path / "blobs", workspace_id)
    with ws(workspace_id):
        await service.index.upsert(
            (
                Chunk(
                    "pg-" + page_id.hex,
                    OWNER_KIND_PAGE,
                    str(page_uid),
                    member_subject(alice),
                    0,
                    "alices private onboarding checklist",
                    probe,
                ),
            )
        )

    with ws(workspace_id):
        mine = await service.search_sources(
            "onboarding checklist",
            recall_subjects(conversation_audience(alice)),
            8,
            source_reader=_reader(
                workspace_id,
                recall_subjects(conversation_audience(alice)),
                alice,
            ),
        )
        assert len(mine) == 1 and mine[0].page_id == page_uid
        assert (
            await service.search_sources(
                "onboarding checklist",
                recall_subjects(conversation_audience(bob)),
                8,
                source_reader=_reader(
                    workspace_id,
                    recall_subjects(conversation_audience(bob)),
                    bob,
                ),
            )
            == ()
        )


@dataclass(frozen=True)
class _Authority:
    workspace_id: UUID
    owner_id: UUID
    stranger_id: UUID
    main_agent_id: UUID
    granted_agent_id: UUID
    ungranted_agent_id: UUID
    owned_connection_id: UUID
    shared_connection_id: UUID
    owned_source_id: UUID
    unowned_source_id: UUID


async def _authority() -> _Authority:
    """A member's private connection one specialist holds the only grant on — so main's exact-owner
    exception is the sole other way in — beside the workspace's own shared connection nobody holds a
    grant for at all. Each carries one stream."""
    state = _Authority(*(uuid4() for _ in range(10)))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=state.workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": member_id,
                    "workspace_id": state.workspace_id,
                    "email": f"{member_id.hex}@x.test",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for member_id in (state.owner_id, state.stranger_id)
            ],
        )
        await connection.execute(
            sa.insert(tables.agent),
            [
                {
                    "id": agent_id,
                    "workspace_id": state.workspace_id,
                    "name": name,
                    "prompt": "p",
                    "model": "m",
                    "is_main": is_main,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for agent_id, name, is_main in (
                    (state.main_agent_id, "ufo", True),
                    (state.granted_agent_id, "research", False),
                    (state.ungranted_agent_id, "scout", False),
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.connection),
            [
                {
                    "id": connection_id,
                    "workspace_id": state.workspace_id,
                    "provider": FOLDER_BACKEND,
                    "account_id": account_id,
                    "host": "",
                    "owner_member_id": owner_member_id,
                    "shared": shared,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for connection_id, account_id, owner_member_id, shared in (
                    (state.owned_connection_id, "owned", state.owner_id, False),
                    (state.shared_connection_id, "", None, True),
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.source),
            [
                {
                    "id": uuid4(),
                    "uid": source_id,
                    "workspace_id": state.workspace_id,
                    "backend": FOLDER_BACKEND,
                    "config": {"root": f"/{source_id.hex}"},
                    "feed_handle": feed_handle_for({"root": f"/{source_id.hex}"}, frozenset()),
                    "connection_id": connection_id,
                    "cursor": None,
                    "next_sync_at": datetime.now(UTC),
                    "claimed_by": None,
                    "claim_expires_at": None,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for source_id, connection_id in (
                    (state.owned_source_id, state.owned_connection_id),
                    (state.unowned_source_id, state.shared_connection_id),
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=state.workspace_id,
                agent_id=state.granted_agent_id,
                connection_id=state.owned_connection_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return state


async def _reachable(state: _Authority, reader: SourceReader) -> frozenset[UUID]:
    with ws(state.workspace_id):
        return await context_for("probe", frozenset()).readable_source_ids(reader)


def _authority_reader(
    state: _Authority, agent_id: UUID, requesting_member_id: UUID | None
) -> SourceReader:
    return SourceReader(
        agent_id=agent_id,
        requesting_member_id=requesting_member_id,
        subjects=frozenset({SHARED_SUBJECT, member_subject(state.owner_id)}),
    )


async def test_main_reads_an_owned_connection_only_while_its_exact_owner_is_speaking(
    db: None,
) -> None:
    """The exact shape of main's exception on a private connection: it needs all three of the main
    agent, a live requesting member, and that member owning the connection. Drop any one and only a
    `connector_grant` opens its streams. The shared connection's stream rides along in every
    main-agent read, because main reads every shared connection without an edge."""
    state = await _authority()

    assert await _reachable(
        state, _authority_reader(state, state.main_agent_id, state.owner_id)
    ) == {state.owned_source_id, state.unowned_source_id}

    assert await _reachable(state, _authority_reader(state, state.main_agent_id, None)) == {
        state.unowned_source_id
    }
    assert await _reachable(
        state, _authority_reader(state, state.main_agent_id, state.stranger_id)
    ) == {state.unowned_source_id}
    assert (
        await _reachable(state, _authority_reader(state, state.ungranted_agent_id, state.owner_id))
        == frozenset()
    )
    assert await _reachable(state, _authority_reader(state, state.granted_agent_id, None)) == {
        state.owned_source_id
    }


async def test_main_reads_every_shared_connection_and_a_specialist_only_what_it_is_granted(
    db: None,
) -> None:
    """Sharing a connection opens its streams to the workspace's main agent with no edge and no
    speaker: the main agent is the one every member talks to and expects to know what the workspace
    shares. A specialist agent reads only the connections granted to it, shared or not, so its feed
    set stays the narrow one it was given — the shared connection here is granted to nobody, so the
    specialist that holds the private grant does not pick it up."""
    state = await _authority()

    assert await _reachable(state, _authority_reader(state, state.main_agent_id, None)) == {
        state.unowned_source_id
    }
    assert (
        await _reachable(state, _authority_reader(state, state.ungranted_agent_id, None))
        == frozenset()
    )
    assert (
        await _reachable(state, _authority_reader(state, state.ungranted_agent_id, state.owner_id))
        == frozenset()
    )
    assert await _reachable(state, _authority_reader(state, state.granted_agent_id, None)) == {
        state.owned_source_id
    }


async def test_a_specialist_granted_a_shared_connection_reads_its_streams(db: None) -> None:
    """The grant is the whole of a specialist's reach, so granting it the shared connection adds
    that connection's streams and nothing else. It is the other direction of the rule beside this
    one: a specialist reads what it is granted whether the connection is shared or private, and
    sharing alone never reaches it."""
    state = await _authority()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=state.workspace_id,
                agent_id=state.ungranted_agent_id,
                connection_id=state.shared_connection_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    assert await _reachable(state, _authority_reader(state, state.ungranted_agent_id, None)) == {
        state.unowned_source_id
    }
    assert await _reachable(state, _authority_reader(state, state.granted_agent_id, None)) == {
        state.owned_source_id
    }


async def test_making_a_connection_private_takes_its_streams_off_the_main_agent(db: None) -> None:
    """Disclosure is the connection's, never the source row's, so flipping `shared` moves every
    stream under it at once. The main agent read the shared connection with no grant; once it is
    private and owned by a member nobody is speaking for, main reads nothing of it, while the
    specialist granted it reads it exactly as before — the grant is reach, not disclosure."""
    state = await _authority()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=state.workspace_id,
                agent_id=state.ungranted_agent_id,
                connection_id=state.shared_connection_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(tables.connection)
            .values(shared=False, owner_member_id=state.owner_id, updated_at=sa.func.now())
            .where(tables.connection.c.id == state.shared_connection_id)
        )

    assert (
        await _reachable(state, _authority_reader(state, state.main_agent_id, None)) == frozenset()
    )
    assert await _reachable(
        state, _authority_reader(state, state.main_agent_id, state.owner_id)
    ) == {state.owned_source_id, state.unowned_source_id}
    assert await _reachable(state, _authority_reader(state, state.ungranted_agent_id, None)) == {
        state.unowned_source_id
    }


async def test_a_turn_with_no_live_speaker_never_inherits_the_owner_exception(db: None) -> None:
    """The owner exception is the live speaker's alone. A scheduled run or subagent carries no
    member identity, so the main agent reaches only the shared source."""
    state = await _authority()
    ctx = ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.workspace_id,
            conversation_id=uuid4(),
            agent_id=state.main_agent_id,
            seq=1,
            status="running",
            inbound="summarise what changed",
            created_at=datetime(2026, 7, 27, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )
    assert ctx.read_subjects == frozenset({SHARED_SUBJECT})
    assert ctx.source_reader().requesting_member_id is None
    assert await _reachable(state, ctx.source_reader()) == {state.unowned_source_id}


async def test_a_turn_connection_scope_is_an_exact_source_read_allowlist(db: None) -> None:
    state = await _authority()
    owned_page_id, shared_page_id = uuid7(), uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page),
            [
                {
                    "uid": page_id,
                    "workspace_id": state.workspace_id,
                    "source_uid": source_id,
                    "digest": f"sha256:{page_id.hex}",
                    "body_ref": f"sources/{page_id}",
                    "stream": "notes",
                    "title": f"page {page_id}",
                    "subject": subject,
                    "tombstone": False,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for page_id, source_id, subject in (
                    (owned_page_id, state.owned_source_id, member_subject(state.owner_id)),
                    (shared_page_id, state.unowned_source_id, SHARED_SUBJECT),
                )
            ],
        )
    for connections, expected_sources, expected_pages in (
        (
            None,
            {state.owned_source_id, state.unowned_source_id},
            {owned_page_id, shared_page_id},
        ),
        ((state.owned_connection_id,), {state.owned_source_id}, {owned_page_id}),
        ((state.shared_connection_id,), {state.unowned_source_id}, {shared_page_id}),
        ((), set(), set()),
    ):
        runtime_config = None if connections is None else TurnRuntimeConfig(connections=connections)
        ctx = ToolContext(
            sandbox=None,
            blob=None,
            turn=Turn(
                id=uuid4(),
                workspace_id=state.workspace_id,
                conversation_id=uuid4(),
                agent_id=state.main_agent_id,
                seq=1,
                status="running",
                inbound="summarise what changed",
                created_at=datetime(2026, 7, 27, tzinfo=UTC),
                runtime_config=runtime_config,
            ),
            agent=Agent(prompt="p", model="claude-opus-4-8"),
            spawn=_unavailable_spawn,
            speaker_member_id=state.owner_id,
            audience=conversation_audience(state.owner_id),
            artifact_token_secret="",
        )
        reader = ctx.source_reader()
        assert reader.connections == connections
        with ws(state.workspace_id):
            ext = context_for("probe", frozenset())
            assert await ext.readable_source_ids(reader) == expected_sources
            assert {page.id for page in await ext.source_pages(reader)} == expected_pages
            assert (
                set(await ext.readable_page_states((owned_page_id, shared_page_id), reader))
                == expected_pages
            )

    automatic = ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.workspace_id,
            conversation_id=uuid4(),
            agent_id=state.granted_agent_id,
            seq=1,
            status="running",
            inbound="summarise what changed",
            created_at=datetime(2026, 7, 27, tzinfo=UTC),
            runtime_config=TurnRuntimeConfig(connections=(state.owned_connection_id,)),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )
    with ws(state.workspace_id):
        ext = context_for("probe", frozenset())
        assert await ext.readable_source_ids(automatic.source_reader()) == {state.owned_source_id}
        assert {page.id for page in await ext.source_pages(automatic.source_reader())} == {
            owned_page_id
        }


async def test_a_failing_source_is_isolated_and_released(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """One bad source must not wedge the run: its fetch raises, but the sibling still syncs and the
    failing source is released (claim cleared) and backed off (next_sync_at advanced) rather than
    left claimed to re-fail every lease cycle."""
    workspace_id = await _workspace()
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    missing = tmp_path / "missing"  # never created → FolderSource._read raises FileNotFoundError
    driver, _, _ = _wire(database_url, vec((14, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(good)
    await _register_folder(missing)

    async def _row(root: Path) -> sa.RowMapping:
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            tables.source.c.config,
                            tables.source.c.claimed_by,
                            tables.source.c.next_sync_at,
                        )
                    )
                )
                .mappings()
                .all()
            )
        return next(row for row in rows if row["config"]["root"] == str(root))

    before = (await _row(missing))["next_sync_at"]

    await _sync(driver)

    assert len(await _pages()) == 1  # the good source synced despite the bad sibling
    good_row, missing_row = await _row(good), await _row(missing)
    assert good_row["claimed_by"] is None
    assert missing_row["claimed_by"] is None  # released, not stuck claimed
    assert missing_row["next_sync_at"] > before  # backed off, won't re-fail every lease


async def test_a_failed_run_records_the_proxying_hops_own_reason(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A broker transport that could not reach the provider raises `httpx.ProxyError` carrying the
    reason it authored, and the failure record renders that reason as the fault — the way it renders
    a `StreamFault` — so the alert's reader learns which hop failed and on what, not the class
    alone. The provider's payload never rides in a proxy-layer error, which keeps it renderable."""
    workspace_id = await _workspace()
    await _seed_scripted_source(workspace_id, None)
    reason = "composio proxy-execute 400: Connection failed to https://api.example.com/v1/items"
    driver, _ = _scripted_driver([httpx.ProxyError(reason)], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.ERROR, logger="ufo"):
        await _sync(driver)

    (record,) = [record for record in caplog.records if record.message == "source_sync.failed"]
    assert record.ufo["error_class"] == "ProxyError"
    assert record.ufo["provider_fault"] == reason


SCRIPTED_BACKEND = "scripted"


class _ScriptedSource:
    """A `SourceBackend` stand-in whose `fetch` is scripted per call: it raises the given error or
    returns the given result, recording the cursor it was handed. The dependency the driver drives,
    never the thing asserted — the tests read the driver's effect (the stored cursor, error count,
    and backoff) back from the real source row."""

    config_model: ClassVar[type[SourceConfig]] = SourceConfig

    def __init__(self, outcomes: list[SyncResult | Exception]) -> None:
        self._outcomes = outcomes
        self.cursors: list[str | None] = []

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
        self.cursors.append(cursor)
        outcome = self._outcomes.pop(0)
        match outcome:
            case Exception():
                raise outcome
            case _:
                return outcome


@dataclass
class _ResyncingSource:
    """A backend that pulls its own source due from inside `fetch` — the live shape of a reconnect
    releasing the row while that source's sync already holds the claim. The request goes through
    the sanctioned API, so what the driver's completing writer must not clobber is exactly what
    production writes."""

    source_id: UUID
    workspace_id: UUID
    outcome: SyncResult | Exception
    config_model: ClassVar[type[SourceConfig]] = SourceConfig

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
        with ws(self.workspace_id):
            await context_for("sources", frozenset()).schedule_source_sync((self.source_id,))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@dataclass
class _BlockingSource:
    result: SyncResult
    config_model: ClassVar[type[SourceConfig]] = SourceConfig
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
        self.entered.set()
        await self.release.wait()
        return self.result


@dataclass
class _ConnectionProbeSource:
    config_model: ClassVar[type[SourceConfig]] = SourceConfig
    slow_entered: asyncio.Event = field(default_factory=asyncio.Event)
    fast_finished: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
        if config.root == "slow":
            self.slow_entered.set()
            await self.release.wait()
        else:
            self.fast_finished.set()
        return SyncResult(pages=())


@dataclass(frozen=True)
class _BlockingReadBlob(FilesystemBlobStore):
    armed: asyncio.Event = field(default_factory=asyncio.Event)
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def get(self, key: str) -> bytes:
        if self.armed.is_set():
            self.entered.set()
            await self.release.wait()
        return await super().get(key)


@dataclass(frozen=True)
class _BlockingWriteBlob(FilesystemBlobStore):
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def put(self, key: str, data: bytes) -> None:
        self.entered.set()
        await self.release.wait()
        await super().put(key, data)


def _scripted_driver(
    outcomes: list[SyncResult | Exception], database_url: str, blob_root: Path
) -> tuple[SyncDriver, _ScriptedSource]:
    backend = _ScriptedSource(outcomes)
    driver = SyncDriver(
        backends={SCRIPTED_BACKEND: backend},
        blob=FilesystemBlobStore(root=blob_root),
        postgres=database_url.startswith("postgresql"),
    )
    return driver, backend


async def _seed_scripted_source(
    workspace_id: UUID,
    cursor: str | None,
    *,
    source_id: UUID | None = None,
    account_id: str = "",
    root: str | None = None,
) -> UUID:
    source_id = uuid4() if source_id is None else source_id
    connection_id = await _connection(workspace_id, SCRIPTED_BACKEND, account_id=account_id)
    configured_root = f"/{source_id.hex}" if root is None else root
    source_uid = uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_uid,
                workspace_id=workspace_id,
                backend=SCRIPTED_BACKEND,
                config={"root": configured_root},
                feed_handle=feed_handle_for({"root": configured_root}, frozenset()),
                connection_id=connection_id,
                cursor=cursor,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_uid


async def test_connections_sync_concurrently(db: None, database_url: str, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    await _seed_scripted_source(
        workspace_id, None, source_id=UUID(int=1), account_id="slow", root="slow"
    )
    await _seed_scripted_source(
        workspace_id, None, source_id=UUID(int=2), account_id="fast", root="fast"
    )
    backend = _ConnectionProbeSource()
    driver = SyncDriver(
        backends={SCRIPTED_BACKEND: backend},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )

    with ws(workspace_id):
        running = asyncio.create_task(driver.run())
        try:
            await backend.slow_entered.wait()
            async with asyncio.timeout(1):
                await backend.fast_finished.wait()
        finally:
            backend.release.set()
            await running


async def test_rate_limit_commits_progress_and_defers_the_connection(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_id = await _workspace()
    first = await _seed_scripted_source(
        workspace_id, None, source_id=UUID(int=1), account_id="limited"
    )
    second = await _seed_scripted_source(
        workspace_id, None, source_id=UUID(int=2), account_id="limited"
    )
    waiting = await _seed_scripted_source(
        workspace_id, None, source_id=UUID(int=3), account_id="limited"
    )
    page = Page(source_ref="doc", body="body", stream="docs", title="Doc")
    driver, backend = _scripted_driver(
        [SyncResult(pages=(page,), next_cursor="checkpoint", retry_after_seconds=60)],
        database_url,
        tmp_path / "blobs",
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .values(consecutive_errors=2)
            .where(tables.source.c.uid.in_((first, second, waiting)))
        )
        await connection.execute(
            sa.update(tables.source)
            .values(next_sync_at=datetime.now(UTC) + timedelta(seconds=30))
            .where(tables.source.c.uid == waiting)
        )

    with caplog.at_level(logging.INFO, logger="ufo"), ws(workspace_id):
        await driver.run()

    first_state = await _source_state(first)
    second_state = await _source_state(second)
    waiting_state = await _source_state(waiting)
    now = datetime.now(UTC)
    for state in (first_state, second_state, waiting_state):
        retry_at = state["next_sync_at"]
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        assert retry_at > now
        assert state["claimed_by"] is None
        assert state["consecutive_errors"] == 2
    assert first_state["cursor"] == "checkpoint"
    assert second_state["cursor"] is None
    assert waiting_state["cursor"] is None
    assert backend.cursors == [None]
    assert len(await _pages()) == 1
    assert len(_events(caplog, "source_sync.started")) == 1
    assert len(_events(caplog, "source_sync.rate_limited")) == 1
    assert _events(caplog, "source_sync.ok") == []


async def test_source_claims_renew_while_an_earlier_fetch_is_running(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    source_ids = {
        await _seed_scripted_source(workspace_id, None),
        await _seed_scripted_source(workspace_id, None),
    }
    backend = _BlockingSource(SyncResult(pages=()))
    driver = SyncDriver(
        backends={SCRIPTED_BACKEND: backend},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    monkeypatch.setattr(sync, "CLAIM_REFRESH_SECONDS", 0.01)
    refresh = SyncDriver._refresh_claim
    claimed_at: dict[UUID, datetime] = {}
    all_refreshed = asyncio.Event()

    async def record_refresh(syncing: SyncDriver, source: ClaimedSource) -> None:
        await refresh(syncing, source)
        claimed_at[source.source_uid] = source.claimed_at
        if set(claimed_at) == source_ids:
            all_refreshed.set()

    monkeypatch.setattr(SyncDriver, "_refresh_claim", record_refresh)

    with ws(workspace_id):
        running = asyncio.create_task(driver.run())
        try:
            await backend.entered.wait()
            await all_refreshed.wait()
            async with workspace_tx() as connection:
                expiries = dict(
                    (
                        await connection.execute(
                            sa.select(
                                tables.source.c.uid,
                                tables.source.c.claim_expires_at,
                            ).where(tables.source.c.uid.in_(source_ids))
                        )
                    ).all()
                )
            contender = await driver._claim_due("contender")
        finally:
            backend.release.set()
            await running

    assert contender == ()
    assert all(
        (
            expiries[source_id]
            if expiries[source_id].tzinfo is not None
            else expiries[source_id].replace(tzinfo=UTC)
        )
        > claimed_at[source_id] + timedelta(seconds=sync.CLAIM_LEASE_SECONDS)
        for source_id in source_ids
    )


async def test_sync_stamps_the_disclosure_its_connection_holds_after_the_fetch(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The commit reads the connection's `shared` under the claim it still holds, not the value the
    claim was taken with, so a member sharing the account while its first sync is in flight has
    every page that sync lands disclosed to the workspace. Reading it before the fetch would commit
    a batch under a disclosure the member had already changed."""
    workspace_id = await _workspace()
    source_id = uuid4()
    backend = _BlockingSource(
        SyncResult(
            pages=(
                Page(
                    source_ref="docs/plan",
                    body="launch plan",
                    stream="docs",
                    title="Launch plan",
                ),
            )
        )
    )
    driver = SyncDriver(
        backends={SCRIPTED_BACKEND: backend},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    member_id = await _member(workspace_id, "member@example.com")
    connection_id = await _connection(
        workspace_id, SCRIPTED_BACKEND, account_id="acct", owner_member_id=member_id, shared=False
    )
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.source).values(
                    uid=source_id,
                    workspace_id=workspace_id,
                    backend=SCRIPTED_BACKEND,
                    config={"root": f"/{source_id.hex}"},
                    feed_handle=feed_handle_for({"root": f"/{source_id.hex}"}, frozenset()),
                    connection_id=connection_id,
                    cursor=None,
                    next_sync_at=sa.func.now(),
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        running = asyncio.create_task(driver.run())
        try:
            await backend.entered.wait()
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.connection)
                    .values(shared=True, updated_at=sa.func.now())
                    .where(tables.connection.c.id == connection_id)
                )
        finally:
            backend.release.set()
            await running
        async with workspace_tx() as connection:
            page_subject = (
                await connection.execute(
                    sa.select(tables.page.c.subject).where(tables.page.c.source_uid == source_id)
                )
            ).scalar_one()
    assert page_subject == SHARED_SUBJECT


async def test_a_sync_in_flight_when_its_connection_goes_writes_nothing_back(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """Disconnecting deletes the connection, and its source rows and their pages follow by cascade.
    A sync already fetching finishes into a row that is gone: the commit's write matches nothing, so
    it takes the claim-lost path and deletes the bodies it had already staged. Nothing outlives the
    authority that fetched it — not a row, not a page, not a blob."""
    workspace_id = await _workspace()
    blob = _BlockingWriteBlob(root=tmp_path / "blobs")
    driver = SyncDriver(
        backends={
            SCRIPTED_BACKEND: _ScriptedSource(
                [
                    SyncResult(
                        pages=(
                            Page(
                                source_ref="alice/doc",
                                body="alice data",
                                stream="docs",
                                title="Alice",
                            ),
                        ),
                        next_cursor="alice-cursor",
                    )
                ]
            )
        },
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    alice = await _member(workspace_id, "alice@example.com")
    connection_id = await _connection(
        workspace_id, SCRIPTED_BACKEND, account_id="acct", owner_member_id=alice, shared=False
    )
    with ws(workspace_id):
        source_id = await context_for("probe", frozenset()).register_source(
            SCRIPTED_BACKEND, SourceConfig(root="/unused"), connection_id=connection_id
        )
        running = asyncio.create_task(driver.run())
        try:
            await blob.entered.wait()
            assert await GrantStore().disconnect(connection_id, actor_member_id=alice)
        finally:
            blob.release.set()
            await running
        async with workspace_tx() as connection:
            sources = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.source)
                    .where(tables.source.c.uid == source_id)
                )
            ).scalar_one()
            pages = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.page)
                    .where(tables.page.c.source_uid == source_id)
                )
            ).scalar_one()
    assert sources == 0
    assert pages == 0
    assert await blob.list("sources/") == ()


async def test_a_cancelled_commit_keeps_the_bodies_its_rows_name(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled write is not a failed one: `_opened` runs the commit to completion under
    `asyncio.shield` and re-raises the cancellation, so the page rows stand and name the bodies
    just written. The cleanup keeps them — a row whose body the store lacks stops the source's page
    feed for every consumer, and the stored digest makes the next sync skip that page."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    driver = SyncDriver(
        backends={SCRIPTED_BACKEND: _ScriptedSource([])},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    result = SyncResult(
        pages=(
            Page(source_ref="docs/plan", body="launch plan", stream="docs", title="Launch plan"),
        ),
        next_cursor="plan-cursor",
    )
    write = SyncDriver._write

    async def commit_then_cancel(syncing: SyncDriver, *args: object, **kwargs: object) -> int:
        await write(syncing, *args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(SyncDriver, "_write", commit_then_cancel)
    with ws(workspace_id):
        (claimed,) = await driver._claim_due("cancelled")
        with pytest.raises(asyncio.CancelledError):
            await driver._commit(claimed, result)
        async with workspace_tx() as connection:
            body_ref = (
                await connection.execute(
                    sa.select(tables.page.c.body_ref).where(tables.page.c.source_uid == source_id)
                )
            ).scalar_one()

    assert await blob.get(body_ref) == b"launch plan"


async def test_page_feed_reads_one_immutable_page_version_during_a_sync(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    await _seed_scripted_source(workspace_id, None)
    old = Page(source_ref="docs/plan", body="old plan", stream="docs", title="Plan")
    new = old.model_copy(update={"body": "new plan"})
    blob = _BlockingReadBlob(root=tmp_path / "blobs")
    driver = SyncDriver(
        backends={
            SCRIPTED_BACKEND: _ScriptedSource([SyncResult(pages=(old,)), SyncResult(pages=(new,))])
        },
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )

    await _sync(driver)
    blob.armed.set()
    with ws(workspace_id):
        reading = asyncio.create_task(CorePageFeed(blob).pages_changed_since(None, 1))
        await blob.entered.wait()
        await _make_due()
        await driver.run()
        blob.release.set()
        (change,) = (await reading).changes

    assert (change.body, change.digest) == (old.body, old.digest)
    async with workspace_tx() as connection:
        body_ref, digest = (
            await connection.execute(
                sa.select(tables.page.c.body_ref, tables.page.c.digest).where(
                    tables.page.c.uid == change.page_id
                )
            )
        ).one()
    assert digest == new.digest
    assert body_ref.endswith(new.digest.removeprefix("sha256:"))


async def test_source_identity_attaches_without_replaying_and_survives_a_ref_change(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    legacy_ref = "organizations/acme"
    identity = "organizations/42"
    body = "# sentry organization: Acme\n\n{}"
    page = Page(
        source_ref=legacy_ref,
        source_identity=identity,
        body=body,
        stream="organizations",
        title="Acme",
    )
    page_id = uuid7()
    body_ref = f"sources/{source_id}/{page_id}/{page.digest.removeprefix('sha256:')}"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                source_identity=legacy_ref,
                digest=page.digest,
                body_ref=body_ref,
                stream=page.stream,
                title=page.title,
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()
    moved = page.model_copy(update={"source_ref": "organizations/acme-renamed"})
    driver, _ = _scripted_driver(
        [SyncResult(pages=(page,), snapshot=True), SyncResult(pages=(moved,), snapshot=True)],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)
    await _make_due()
    await _sync(driver)

    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(
                        tables.page.c.uid,
                        tables.page.c.source_identity,
                        tables.page.c.digest,
                        tables.page.c.body_ref,
                        tables.page.c.revision,
                        tables.page.c.tombstone,
                    ).where(tables.page.c.source_uid == source_id)
                )
            )
            .mappings()
            .all()
        )
    assert rows == [
        {
            "uid": page_id,
            "source_identity": identity,
            "digest": page.digest,
            "body_ref": body_ref,
            "revision": revision,
            "tombstone": False,
        }
    ]

    split_identity = legacy_ref
    split = page.model_copy(
        update={
            "source_identity": split_identity,
            "body": "# sentry organization: Other\n\n{}",
            "title": "Other",
        }
    )
    collision_driver, _ = _scripted_driver(
        [SyncResult(pages=(page, split), snapshot=True)],
        database_url,
        tmp_path / "collision-blobs",
    )
    await _make_due()
    await _sync(collision_driver)

    async with workspace_tx() as connection:
        identities = dict(
            (
                await connection.execute(
                    sa.select(tables.page.c.source_identity, tables.page.c.uid).where(
                        tables.page.c.source_uid == source_id
                    )
                )
            ).all()
        )
        retained_revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()
    assert set(identities) == {identity, split_identity}
    assert identities[identity] == page_id
    assert identities[split_identity] != page_id
    assert retained_revision == revision


async def test_database_revision_orders_an_old_writer_after_a_new_cursor(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    page_id = uuid4()
    old_ref, new_ref = f"pages/{page_id}/old", f"pages/{page_id}/new"
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    await blob.put(old_ref, b"old body")
    await blob.put(new_ref, b"new body")
    old_stamp = datetime(2020, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                digest="sha256:old",
                body_ref=old_ref,
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=old_stamp,
                updated_at=old_stamp,
            )
        )
        first_revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()

    feed = CorePageFeed(blob)
    cursor = f"{first_revision}|{page_id}"
    with ws(workspace_id):
        assert (await feed.pages_changed_since(cursor, 1)).changes == ()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(
                    digest="sha256:new",
                    body_ref=new_ref,
                    updated_at=old_stamp - timedelta(days=1),
                )
                .where(tables.page.c.uid == page_id)
            )
        (change,) = (await feed.pages_changed_since(cursor, 1)).changes
        with pytest.raises(ValueError, match="invalid page cursor"):
            await feed.pages_changed_since(
                f"{datetime(2030, 1, 1, tzinfo=UTC).isoformat()}|{page_id}", 1
            )

    assert change.body == "new body"
    assert change.revision > first_revision


async def _source_state(source_id: UUID) -> sa.RowMapping:
    async with workspace_tx() as connection:
        return (
            (
                await connection.execute(
                    sa.select(
                        tables.source.c.cursor,
                        tables.source.c.partition_cursor,
                        tables.source.c.synced_at,
                        tables.source.c.config,
                        tables.source.c.consecutive_errors,
                        tables.source.c.consecutive_refusals,
                        tables.source.c.parked_at,
                        tables.source.c.parked_reason,
                        tables.source.c.next_sync_at,
                        tables.source.c.claimed_by,
                    ).where(tables.source.c.uid == source_id)
                )
            )
            .mappings()
            .one()
        )


async def _page_of(source_uid: UUID, identity: str) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.page.c.uid).where(
                    tables.page.c.source_uid == source_uid,
                    tables.page.c.source_identity == identity,
                )
            )
        ).scalar_one()


async def _registered_source(connection_id: UUID, handle: str) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.source.c.uid).where(
                    tables.source.c.connection_id == connection_id,
                    tables.source.c.feed_handle == handle,
                )
            )
        ).scalar_one()


async def _tombstone(page_id: UUID) -> bool:
    async with workspace_tx() as connection:
        return bool(
            (
                await connection.execute(
                    sa.select(tables.page.c.tombstone).where(tables.page.c.uid == page_id)
                )
            ).scalar_one()
        )


def _check_page_requires_browse_metadata() -> None:
    page = {
        "source_ref": "docs/launch",
        "body": "Launch window",
        "stream": "docs",
        "title": "Launch window",
    }
    for field_name in ("stream", "title"):
        with pytest.raises(ValueError):
            Page.model_validate(page | {field_name: ""})


def _check_page_derives_digest_from_body() -> None:
    page = Page(source_ref="docs/launch", body="Launch window", stream="docs", title="Launch")
    assert page.digest == "sha256:" + hashlib.sha256(page.body.encode()).hexdigest()
    with pytest.raises(ValueError):
        Page.model_validate(page.model_dump() | {"digest": "sha256:caller-controlled"})


def _check_page_normalizes_browse_timestamps() -> None:
    page = Page(
        source_ref="docs/launch",
        body="Launch window",
        stream="docs",
        title="Launch window",
        created_at="2026-07-23T14:30:00.5-04:00",
        updated_at="2026-07-23T18:30:00Z",
    )
    assert page.created_at == "2026-07-23T18:30:00.500000+00:00"
    assert page.updated_at == "2026-07-23T18:30:00.000000+00:00"


def _check_page_normalizes_provider_timestamp_shapes() -> None:
    for value, expected in [
        ("2026-07-23", "2026-07-23T00:00:00.000000+00:00"),
        ("1700000000", "2023-11-14T22:13:20.000000+00:00"),
        ("1700000000000", "2023-11-14T22:13:20.000000+00:00"),
    ]:
        page = Page(
            source_ref="docs/launch",
            body="Launch window",
            stream="docs",
            title="Launch window",
            created_at=value,
        )
        assert page.created_at == expected


def _check_page_rejects_invalid_browse_timestamps() -> None:
    for value in ["not-a-time", "2026-07-23T18:30:00"]:
        with pytest.raises(ValueError, match="timestamp"):
            Page(
                source_ref="docs/launch",
                body="Launch window",
                stream="docs",
                title="Launch window",
                created_at=value,
            )


async def _seed_prior_page(workspace_id: UUID, source_id: UUID, source_ref: str) -> UUID:
    """A page a prior snapshot of the source already landed and indexed — active, unrelated to the
    delta run under test."""
    page_id = uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                source_identity=source_ref,
                digest="sha256:prior",
                body_ref=f"sources/{source_id}/{page_id}",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return page_id


async def test_cursor_expired_clears_stored_cursor_and_next_run_refetches(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source on an expiring delta token must recover: when the backend raises `CursorExpired`,
    the driver clears the stored cursor so the next run refetches from scratch rather than
    re-failing on the dead cursor forever. The reported run says which of the two it was:
    `cursor_reset` separates a failure that dropped its cursor from one that resumed it, and the
    counter carries the class itself rather than sharing one bucket with every unlisted class."""
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, "stale-token")
    page = Page(
        source_ref="doc",
        body="the launch window opens at dawn",
        stream="docs",
        title="Launch window",
    )
    driver, backend = _scripted_driver(
        [
            CursorExpired("delta token aged out"),
            SyncResult(pages=(page,), next_cursor="fresh-token"),
        ],
        database_url,
        tmp_path / "blobs",
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
    expired = await _source_state(source_id)
    assert backend.cursors == ["stale-token"]
    assert expired["cursor"] is None
    assert expired["consecutive_errors"] == 1
    failure = _events(caplog, "source_sync.failed")[0]
    assert failure.ufo["cursor_reset"] is True
    assert failure.ufo["error_class"] == "CursorExpired"
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": SCRIPTED_BACKEND,
            "stream": "",
            "error_class": "CursorExpired",
        }
    ]

    await _make_due()
    await _sync(driver)
    refetched = await _source_state(source_id)
    assert backend.cursors == ["stale-token", None]
    assert refetched["cursor"] == "fresh-token"
    assert refetched["consecutive_errors"] == 0
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["stream"] == page.stream
    assert pages[0]["title"] == page.title


async def test_sync_persists_backend_page_browse_metadata(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    await _seed_scripted_source(workspace_id, None)
    page = Page(
        source_ref="issues/ENG-42",
        body="Issue body",
        stream="issues",
        title="Fix launch sequencing",
        created_at="2026-07-01T12:00:00Z",
        updated_at="2026-07-23T18:30:00Z",
    )
    driver, _ = _scripted_driver(
        [SyncResult(pages=(page,))],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)

    stored = (await _pages())[0]
    assert stored["stream"] == page.stream
    assert stored["title"] == page.title
    assert stored["record_created_at"] == page.created_at
    assert stored["record_updated_at"] == page.updated_at
    feed = CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs"))
    with ws(workspace_id):
        change = (await feed.pages_changed_since(None, 1)).changes[0]
    assert change.as_of == datetime.fromisoformat(page.updated_at)


async def test_sync_refreshes_browse_metadata_without_replaying_unchanged_content(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    await _seed_scripted_source(workspace_id, None)
    original = Page(
        source_ref="issues/ENG-42",
        body="Issue body",
        stream="issues",
        title="Launch sequence",
        updated_at="2026-07-23T18:30:00Z",
    )
    refreshed = original.model_copy(
        update={"title": "Fix launch sequencing", "updated_at": "2026-07-24T09:00:00Z"}
    )
    driver, _ = _scripted_driver(
        [SyncResult(pages=(original,)), SyncResult(pages=(refreshed,))],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)
    stamped = (await _pages())[0]["updated_at"]
    await _make_due()
    await _sync(driver)

    stored = (await _pages())[0]
    assert stored["title"] == refreshed.title
    assert stored["record_updated_at"] == refreshed.updated_at
    assert stored["updated_at"] == stamped


async def test_a_run_brings_every_row_of_its_source_to_the_streams_declaration(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A row the outgoing image landed during a roll carries the column default under a stream
    that declares otherwise, and a cursor stream never fetches it again. The declaration rides the
    run, not the page, so the first run of any shape — here one landing nothing — moves it; the
    move is a revision the feed replays once with `indexed=False`; a run that finds every row
    agreeing assigns nothing; and a row a run inserts is written under the declaration."""
    workspace_id = await _workspace()
    source_uid = await _seed_scripted_source(workspace_id, None)
    stale = await _seed_prior_page(workspace_id, source_uid, "workflow_runs/41")
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    await blob.put(f"sources/{source_uid}/{stale}", b"run 41 passed")
    landed = Page(
        source_ref="workflow_runs/42", body="run 42 passed", stream="workflow_runs", title="ci #42"
    )
    driver, _ = _scripted_driver(
        [SyncResult(pages=(), indexed=False), SyncResult(pages=(landed,), indexed=False)],
        database_url,
        tmp_path / "blobs",
    )
    seeded = (await _pages())[0]
    assert seeded["indexed"] is True

    await _sync(driver)
    moved = (await _pages())[0]
    with ws(workspace_id):
        replayed = (
            await CorePageFeed(blob=blob).pages_changed_since(f"{seeded['revision']}|{stale}", 10)
        ).changes
    assert (moved["indexed"], moved["revision"] > seeded["revision"]) == (False, True)
    assert [(change.page_id, change.indexed) for change in replayed] == [(stale, False)]

    await _make_due()
    await _sync(driver)
    rows = {row["uid"]: (row["indexed"], row["revision"]) for row in await _pages()}
    inserted = next(uid for uid in rows if uid != stale)
    assert rows[stale] == (False, moved["revision"])
    assert rows[inserted][0] is False


async def test_moving_indexed_alone_is_a_revision_and_writing_its_value_again_is_not(
    db: None,
) -> None:
    """`indexed` is in the revision trigger's column list and in its distinct-from row: an update
    that moves only it assigns a revision the feed orders, and an update that names it at its
    current value beside a browse-metadata change assigns none."""
    workspace_id = await _workspace()
    source_uid = await _seed_scripted_source(workspace_id, None)
    page_id = await _seed_prior_page(workspace_id, source_uid, "workflow_runs/41")

    async def revision() -> int:
        async with workspace_tx() as connection:
            return await connection.scalar(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )

    seeded = await revision()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page).values(indexed=False).where(tables.page.c.uid == page_id)
        )
    moved = await revision()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page)
            .values(indexed=False, title="ci #41")
            .where(tables.page.c.uid == page_id)
        )
    assert (moved, await revision()) == (seeded + 1, moved)


async def test_a_page_the_outgoing_image_indexed_leaves_memory_on_the_new_images_first_run(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """During the roll the outgoing image lands a page under the column default and its page
    indexer writes chunks and a mirror row — after any drain walk ended, or in a workspace no
    migration marked. The new image's first run over the source moves the row, the move is a
    revision, and the replay reaches the page indexer, which drops the chunks and the mirror row
    with no marker involved."""
    workspace_id = await _workspace()
    source_uid = await _seed_scripted_source(workspace_id, None)
    page_id = await _seed_prior_page(workspace_id, source_uid, "workflow_runs/41")
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    await blob.put(
        f"sources/{source_uid}/{page_id}", b"the nightly billing build ran twelve minutes"
    )
    index = DefaultIndex(transaction=workspace_tx)
    indexer = PageIndexer(
        index=index,
        embed=StubEmbed(vec((11, 1.0))),
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )
    feed = CorePageFeed(blob=blob)
    scope = IndexScope(OWNER_KIND_PAGE, str(page_id))

    async def mirrors() -> set[UUID]:
        async with workspace_tx() as connection:
            return set(
                (
                    await connection.execute(
                        sa.select(mem_page.c.page_uid).where(
                            mem_page.c.workspace_id == workspace_id
                        )
                    )
                ).scalars()
            )

    with ws(workspace_id):
        outgoing = await feed.pages_changed_since(None, 50)
        await indexer.apply(outgoing.changes)
        assert await index.has_chunks(scope)
    assert await mirrors() == {page_id}

    driver, _ = _scripted_driver(
        [SyncResult(pages=(), indexed=False)], database_url, tmp_path / "blobs"
    )
    await _sync(driver)
    with ws(workspace_id):
        replayed = await feed.pages_changed_since(outgoing.next_cursor, 50)
        await indexer.apply(replayed.changes)
        assert [(change.page_id, change.indexed) for change in replayed.changes] == [
            (page_id, False)
        ]
        assert not await index.has_chunks(scope)
    assert await mirrors() == set()


async def test_consecutive_errors_back_off_and_a_success_resets_the_counter(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A persistently-failing source counts its errors and pushes next_sync_at out further each
    time, so it doesn't hammer its provider every interval; a successful sync resets the counter."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    driver, _ = _scripted_driver(
        [RuntimeError("provider 500"), RuntimeError("provider 500"), SyncResult(pages=())],
        database_url,
        tmp_path / "blobs",
    )

    baseline_first = (await _source_state(source_id))["next_sync_at"]
    await _sync(driver)
    first = await _source_state(source_id)
    assert first["consecutive_errors"] == 1
    gap_first = first["next_sync_at"] - baseline_first

    await _make_due()
    baseline_second = (await _source_state(source_id))["next_sync_at"]
    await _sync(driver)
    second = await _source_state(source_id)
    assert second["consecutive_errors"] == 2
    gap_second = second["next_sync_at"] - baseline_second
    assert gap_second > gap_first

    await _make_due()
    await _sync(driver)
    assert (await _source_state(source_id))["consecutive_errors"] == 0


async def test_a_resync_requested_during_a_sync_survives_the_completing_writer(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The panel's Resync is honoured even when it lands mid-sync: the request writes
    `next_sync_at=now` while the claim is held, and the writer that finishes reschedules only the
    sync it ran, so the driver's very next pass claims the source again. Both completion paths that
    reschedule a claim they still hold are covered — the success interval and the error backoff the
    `errors` column invites a member to resync past — and each releases its claim either way."""
    workspace_id = await _workspace()
    for outcome, expected_errors in ((SyncResult(pages=()), 0), (RuntimeError("provider 500"), 1)):
        source_id = await _seed_scripted_source(workspace_id, None)
        driver = SyncDriver(
            backends={
                SCRIPTED_BACKEND: _ResyncingSource(
                    source_id=source_id, workspace_id=workspace_id, outcome=outcome
                )
            },
            blob=FilesystemBlobStore(root=tmp_path / "blobs"),
            postgres=database_url.startswith("postgresql"),
        )

        await _sync(driver)

        assert (await _source_state(source_id))["consecutive_errors"] == expected_errors
        async with workspace_tx() as connection:
            claim = (
                await connection.execute(
                    sa.select(tables.source.c.claimed_by).where(tables.source.c.uid == source_id)
                )
            ).scalar_one()
        assert claim is None  # the lease is still released on both paths
        assert await _claims(driver) == (source_id,)  # the request stands: due again immediately


async def test_an_undisturbed_sync_still_reschedules_itself(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The other polarity of the same writer: with no request landing under the claim, a completed
    sync pushes `next_sync_at` out to its own interval and a failed one to its backoff, so the
    driver does not re-claim a source it just finished."""
    workspace_id = await _workspace()
    for outcome in (SyncResult(pages=()), RuntimeError("provider 500")):
        await _seed_scripted_source(workspace_id, None)
        driver, _ = _scripted_driver([outcome], database_url, tmp_path / "blobs")

        await _sync(driver)

        assert await _claims(driver) == ()  # rescheduled out to its own interval or backoff


async def test_delta_delete_tombstones_only_named_page_never_blanket_sweeps(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A delta backend (`snapshot=False`) that lands a page, then next run explicitly deletes it,
    tombstones only that page — an unrelated prior page from a different snapshot survives. This is
    the both-ends proof that a partial fetch never triggers the blanket unseen-page sweep."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    kept_id = await _seed_prior_page(workspace_id, source_id, "kept/doc")
    delta = Page(
        source_ref="delta/doc",
        body="the launch window opens at dawn",
        stream="docs",
        title="Launch window",
    )
    driver, _ = _scripted_driver(
        [
            SyncResult(pages=(delta,), snapshot=False),
            SyncResult(pages=(), deletes=("delta/doc",), snapshot=False),
        ],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)
    assert await _tombstone(await _page_of(source_id, "delta/doc")) is False
    assert await _tombstone(kept_id) is False  # delta run 1 did not sweep the unseen prior page

    await _make_due()
    await _sync(driver)
    assert await _tombstone(await _page_of(source_id, "delta/doc")) is True  # deleted → tombstoned
    assert await _tombstone(kept_id) is False  # survives: no blanket sweep on a delta run


async def test_snapshot_fetch_tombstones_prior_pages_absent_from_the_fetch(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A `snapshot=True` fetch is an authoritative full collection: a prior page the fetch no longer
    holds is swept to a tombstone, while a page still present stays active. The sweep names no
    delete refs of its own, so what the run reports it tombstoned is counted off the write."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    gone_id = await _seed_prior_page(workspace_id, source_id, "gone/doc")
    kept = Page(
        source_ref="kept/doc",
        body="the mascot is a friendly otter named pip",
        stream="docs",
        title="Mascot",
    )
    driver, _ = _scripted_driver(
        [SyncResult(pages=(kept,), snapshot=True)], database_url, tmp_path / "blobs"
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
    assert await _tombstone(gone_id) is True  # absent from the authoritative snapshot → swept
    assert await _tombstone(await _page_of(source_id, "kept/doc")) is False
    synced = _events(caplog, "source_sync.ok")[0]
    assert (synced.ufo["pages_fetched"], synced.ufo["pages_written"]) == (1, 1)
    assert synced.ufo["pages_tombstoned"] == 1  # the swept page, named by no delete ref


async def test_stream_skipped_records_a_skip_not_a_failure_and_never_tombstones(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    skipped_id = await _seed_scripted_source(workspace_id, "held-cursor")
    kept_id = await _seed_prior_page(workspace_id, skipped_id, "kept/doc")
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    missing = tmp_path / "missing"
    driver = SyncDriver(
        backends={
            SCRIPTED_BACKEND: _ScriptedSource(
                [StreamSkipped("github: org scope not granted (403)")]
            ),
            FOLDER_BACKEND: FolderSource(),
        },
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    await _register_folder(good)
    await _register_folder(missing)
    baseline = (await _source_state(skipped_id))["next_sync_at"]
    real_log = sync.log

    def _log_gone(event: str, **fields: object) -> None:
        real_log(event, **fields)
        raise RuntimeError("log pipeline unreachable")

    monkeypatch.setattr(sync, "log", _log_gone)

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    skips = _events(caplog, "source_sync.skipped")
    assert len(skips) == 1
    assert skips[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(skipped_id),
        "provider": SCRIPTED_BACKEND,
        "stream": "",
        "reason": "github: org scope not granted (403)",
    }
    skip_state = await _source_state(skipped_id)
    assert skip_state["consecutive_errors"] == 0
    assert skip_state["cursor"] == "held-cursor"
    assert skip_state["next_sync_at"] > baseline
    assert skip_state["consecutive_refusals"] == 1  # counted, and under the park threshold
    assert (skip_state["parked_at"], skip_state["parked_reason"]) == (None, None)
    assert await _tombstone(kept_id) is False

    failed_id = await _registered_source(
        await _connection(
            workspace_id,
            FOLDER_BACKEND,
            account_id=feed_handle(SourceConfig(root=str(missing))),
        ),
        feed_handle_for({"root": str(missing)}, frozenset()),
    )
    assert (await _source_state(failed_id))["consecutive_errors"] == 1

    active = [page for page in await _pages() if page["tombstone"] in (False, 0)]
    assert len(active) == 2


PARK_REASON = "google_drive: drive.readonly scope not granted (403)"
PARKED_METRIC = f"ufo.{SOURCE_SYNC_PARKED_METRIC}"


async def _park(source_id: UUID, reason: str = PARK_REASON) -> None:
    """The row exactly as the run that reached the threshold left it, without driving the refusals
    that got it there: the two marks, the counter at the threshold, the claim freed, and the park
    retry ahead of it. What the tests below are about is what a row in that state does next."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .values(
                consecutive_refusals=SOURCE_REFUSAL_PARK_THRESHOLD,
                parked_at=datetime.now(UTC),
                parked_reason=reason,
                claimed_by=None,
                claim_expires_at=None,
                next_sync_at=datetime.now(UTC) + timedelta(seconds=SOURCE_PARK_RETRY_SECONDS),
                updated_at=sa.func.now(),
            )
            .where(tables.source.c.uid == source_id)
        )


async def _seed_connected_source(workspace_id: UUID) -> tuple[UUID, UUID, UUID]:
    """A member's connection and one connector source bound to it — the shape a reconnect of that
    account lands on. Answers the member, the conversation it was connected in, and the source."""
    member_id, conversation_id, connection_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=uuid5(NAMESPACE_URL, f"{workspace_id}/main"),
                surface="probe",
                queue_key=str(conversation_id),
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=CONNECTOR_PROVIDER,
                account_id=CONNECTOR_ACCOUNT,
                host=CONNECTOR_HOST,
                owner_member_id=member_id,
                shared=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend=CONNECTOR_PROVIDER,
                config={"stream": CONNECTOR_STREAM},
                feed_handle=feed_handle_for({"stream": CONNECTOR_STREAM}, frozenset()),
                connection_id=connection_id,
                cursor="held-cursor",
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id, conversation_id, source_id


async def test_a_refused_stream_parks_on_the_threshold_run_and_records_it_without_alerting(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grant does not widen between two attempts, so retrying a refused stream every interval buys
    nothing and costs a request a minute for as long as nobody re-grants the scope. The refusals
    count, and the run that reaches the threshold parks the row with the reason the backend wrote.

    That run records the park where a sweep for stopped streams looks — a warning log naming the
    row, the count and the reason, and one point on the park counter carrying the provider stream —
    and
    submits no service-check status at all. Nobody is paged for a park: only a member widening a
    grant ends the refusal, and the row reads itself back every hour meanwhile. `ufo.source_sync`
    stays the failure path's own signal, so a refused run leaves it untouched on either side of the
    threshold. Parking writes nothing else — the cursor stands and a prior page keeps its place, so
    the run that resumes the stream picks up where it stopped."""
    reader = _meter(monkeypatch)
    submitted: list[tuple[str, int, str, dict[str, str]]] = []

    async def _record(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append((name, status, message, tags))

    monkeypatch.setattr(sync, "emit_service_check", _record)
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, "held-cursor")
    kept_id = await _seed_prior_page(workspace_id, source_id, "kept/doc")
    driver, _ = _scripted_driver(
        [StreamSkipped(PARK_REASON)] * SOURCE_REFUSAL_PARK_THRESHOLD,
        database_url,
        tmp_path / "blobs",
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        for refusals in range(1, SOURCE_REFUSAL_PARK_THRESHOLD):
            await _sync(driver)
            under = await _source_state(source_id)
            assert under["consecutive_refusals"] == refusals
            assert under["parked_at"] is None  # rescheduled as before, and claimed again next pass
            await _make_due()
        assert _events(caplog, "source_sync.parked") == []

        await _sync(driver)

    parked = await _source_state(source_id)
    assert parked["consecutive_refusals"] == SOURCE_REFUSAL_PARK_THRESHOLD
    assert parked["parked_at"] is not None
    assert parked["parked_reason"] == PARK_REASON
    assert parked["cursor"] == "held-cursor"
    assert parked["next_sync_at"] - under["next_sync_at"] >= timedelta(
        seconds=SOURCE_PARK_RETRY_SECONDS - SOURCE_SYNC_INTERVAL_SECONDS
    )
    assert await _tombstone(kept_id) is False
    parks = _events(caplog, "source_sync.parked")
    assert len(parks) == 1
    assert parks[0].levelno == logging.WARNING
    assert parks[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(source_id),
        "provider": SCRIPTED_BACKEND,
        "stream": "",
        "consecutive_refusals": SOURCE_REFUSAL_PARK_THRESHOLD,
        "reason": PARK_REASON,
    }
    assert _metric_points(reader, PARKED_METRIC) == [{"provider": SCRIPTED_BACKEND, "stream": ""}]
    assert submitted == []  # the park is not the failure path's check to speak on


async def test_a_grant_settled_refusal_parks_until_the_grant_changes_not_on_the_hour(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A broker reporting its account unusable answers the same way on every read until the member
    reconnects, and that reconnect is an event `GrantStore.record` delivers. An hourly request would
    ask a question already answered elsewhere, so this park holds far out instead, and the release
    is what wakes the row.

    The hold is a date and not an infinity, so it cannot wedge if every release path misses one. And
    it is the raiser's call alone: a missing scope takes the hourly park in the test above, because
    an administrator can widen one out of band and no event tells us."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, "held-cursor")
    settled = StreamSkipped("the grant needs the member to reconnect", awaits_grant=True)
    driver, _ = _scripted_driver(
        [settled] * SOURCE_REFUSAL_PARK_THRESHOLD, database_url, tmp_path / "blobs"
    )

    for _ in range(SOURCE_REFUSAL_PARK_THRESHOLD - 1):
        await _sync(driver)
        await _make_due()
    before = await _source_state(source_id)

    await _sync(driver)

    parked = await _source_state(source_id)
    assert parked["parked_at"] is not None
    assert parked["cursor"] == "held-cursor"
    ahead = parked["next_sync_at"] - before["next_sync_at"]
    assert ahead > timedelta(seconds=SOURCE_PARK_RETRY_SECONDS)  # not the hourly park
    assert ahead > timedelta(days=300)  # held for the grant, not for a clock
    assert await _claims(driver) == ()  # and no run reaches it meanwhile


async def test_a_parked_source_waits_the_park_retry_and_then_reads_again(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A park slows a stream, it never stops one. Nothing but `next_sync_at` holds a parked row
    back, so both due reads pass it over while the hour runs and hand it straight back when the hour
    is up — the marks are state a member reads, never a gate on the driver. This is what keeps a
    refusal that clears by itself, on a connector that cannot tell a throttle from a missing scope,
    from needing a member: the run after the wait is the one that recovers it. A live source beside
    it syncs throughout, because parking one feed is not a stop on the workspace."""
    workspace_id = await _workspace()
    parked_id = await _seed_scripted_source(workspace_id, None)
    live_id = await _seed_scripted_source(workspace_id, None)
    driver, _ = _scripted_driver([], database_url, tmp_path / "blobs")
    await _park(parked_id)

    assert await driver.candidate_workspaces() == (workspace_id,)
    with ws(workspace_id):
        assert await _claims(driver) == (live_id,)  # the hour is still running

    await _park(live_id)

    assert await driver.candidate_workspaces() == ()
    with ws(workspace_id):
        assert await _claims(driver) == ()

    await _make_due()  # the park retry elapses

    assert await driver.candidate_workspaces() == (workspace_id,)
    with ws(workspace_id):
        assert set(await _claims(driver)) == {parked_id, live_id}


async def test_the_run_that_finally_succeeds_releases_a_parked_source(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The recovery every refusal shares, with no member and no operator in it: the park expires,
    the provider answers this time, and the writer clears both marks and the counter so the stream
    is back on the interval. Without that clearing a recovered stream would read as parked forever
    on the panel and hold its WARNING check."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    page = Page(
        source_ref="docs/plan",
        body="the launch window opens at dawn",
        stream="docs",
        title="Launch plan",
    )
    driver, _ = _scripted_driver([SyncResult(pages=(page,))], database_url, tmp_path / "blobs")
    await _park(source_id)
    await _make_due()
    due = (await _source_state(source_id))["next_sync_at"]  # the park retry, elapsed

    await _sync(driver)

    state = await _source_state(source_id)
    assert (state["parked_at"], state["parked_reason"]) == (None, None)
    assert state["consecutive_refusals"] == 0
    ahead = state["next_sync_at"] - due
    assert timedelta(0) < ahead < timedelta(seconds=SOURCE_PARK_RETRY_SECONDS)


async def test_a_reconnect_of_the_same_account_unparks_the_sources_it_carries(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The whole recovery story, with no operator in it: the member re-grants the scope, the connect
    reuses the connection row the refused source hangs off, and every live source of that
    connection comes back — marks cleared, both counters at zero, due now — so the next pass claims
    it and resumes from the cursor it stopped on. A source of no connection stays where it is:
    nothing about this reconnect says its own refusal was dealt with."""
    workspace_id = await _workspace()
    main_id = uuid5(NAMESPACE_URL, f"{workspace_id}/main")
    member_id, _, source_id = await _seed_connected_source(workspace_id)
    unrelated_id = await _seed_scripted_source(workspace_id, None)
    driver, _ = _scripted_driver([], database_url, tmp_path / "blobs")
    for row_id in (source_id, unrelated_id):
        await _park(row_id)

    with ws(workspace_id), agent(main_id):
        await GrantStore().record(
            provider=CONNECTOR_PROVIDER,
            account_id=CONNECTOR_ACCOUNT,
            host=CONNECTOR_HOST,
            grantor_member_id=member_id,
            shared=False,
        )
        assert await _claims(driver) == (source_id,)  # unparked and due, through the driver's read

    released = await _source_state(source_id)
    assert (released["parked_at"], released["parked_reason"]) == (None, None)
    assert (released["consecutive_refusals"], released["consecutive_errors"]) == (0, 0)
    assert released["cursor"] == "held-cursor"
    held = await _source_state(unrelated_id)
    assert held["parked_reason"] == PARK_REASON
    assert held["consecutive_refusals"] == SOURCE_REFUSAL_PARK_THRESHOLD


async def _seed_peer_source(workspace_id: UUID) -> UUID:
    """A second member's own connector source on the same provider, bound to its own connection —
    the row a grantor's reconnect must not reach."""
    peer_id, conversation_id, connection_id, source_id = uuid4(), uuid4(), uuid4(), uuid4()
    account = f"{CONNECTOR_ACCOUNT}_peer"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=peer_id,
                workspace_id=workspace_id,
                email="peer@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=uuid5(NAMESPACE_URL, f"{workspace_id}/main"),
                surface="probe",
                queue_key=str(conversation_id),
                member_id=peer_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=CONNECTOR_PROVIDER,
                account_id=account,
                host=CONNECTOR_HOST,
                owner_member_id=peer_id,
                shared=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend=CONNECTOR_PROVIDER,
                config={"stream": CONNECTOR_STREAM},
                feed_handle=feed_handle_for({"stream": CONNECTOR_STREAM}, frozenset()),
                connection_id=connection_id,
                cursor="peer-cursor",
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_id


async def test_a_reconnect_under_a_new_account_id_still_releases_the_parked_feed(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The member repairs the grant, and the repair lands on an account id this workspace has never
    seen. Connections are keyed on that id, so the connect writes a second connection row beside the
    first, and the parked feed hangs off the first. Releasing only the matched row would leave that
    feed parked while its member believed they had just fixed it — and a feed parked on a grant
    event is never polled, so nothing else would ever find it.

    Every parked stream of this grantor's own connections to the provider is released instead,
    whichever account each names. Nothing is rebound: the older account usually still
    authenticates, and the feed proves that or parks again within three runs.

    Two rows are left alone, and each says a different thing. A stream on another provider is not
    this repair. A stream under another member's connection to this same provider is not this
    grantor's to touch — a member never acts on another's connection, and a reconnect must not
    reach past that gate."""
    workspace_id = await _workspace()
    main_id = uuid5(NAMESPACE_URL, f"{workspace_id}/main")
    member_id, _, source_id = await _seed_connected_source(workspace_id)
    other_provider_id = await _seed_scripted_source(workspace_id, None)
    peer_source_id = await _seed_peer_source(workspace_id)
    driver, _ = _scripted_driver([], database_url, tmp_path / "blobs")
    for row_id in (source_id, other_provider_id, peer_source_id):
        await _park(row_id)

    with ws(workspace_id), agent(main_id):
        await GrantStore().record(
            provider=CONNECTOR_PROVIDER,
            account_id=f"{CONNECTOR_ACCOUNT}_reconnected",  # a second account, not the parked one
            host=CONNECTOR_HOST,
            grantor_member_id=member_id,
            shared=False,
        )
        assert await _claims(driver) == (source_id,)  # released and due, through the driver's read

    released = await _source_state(source_id)
    assert (released["parked_at"], released["parked_reason"]) == (None, None)
    assert released["consecutive_refusals"] == 0
    assert released["cursor"] == "held-cursor"  # released, never rebound or reset
    held = await _source_state(other_provider_id)
    assert held["parked_reason"] == PARK_REASON  # another provider is not this repair
    peer_held = await _source_state(peer_source_id)
    assert peer_held["parked_reason"] == PARK_REASON  # nor is another member's feed
    assert peer_held["consecutive_refusals"] == SOURCE_REFUSAL_PARK_THRESHOLD


async def test_a_resync_unparks_the_source_it_names(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The manual override, for a scope fixed on the provider's side where no connect flow runs and
    no connection row is written. A parked row is never claimed, so a resync that only pulled
    `next_sync_at` forward would do nothing on exactly the row someone is trying to revive."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, "held-cursor")
    driver, _ = _scripted_driver([], database_url, tmp_path / "blobs")
    await _park(source_id)

    with ws(workspace_id):
        await context_for("sources", frozenset()).schedule_source_sync((source_id,))
        assert await _claims(driver) == (source_id,)

    state = await _source_state(source_id)
    assert (state["parked_at"], state["parked_reason"], state["consecutive_refusals"]) == (
        None,
        None,
        0,
    )
    assert state["cursor"] == "held-cursor"


async def test_a_successful_run_clears_the_refusal_counter(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """Refusals count consecutive runs, so the count ends where the refusals end. A token that
    momentarily fails to refresh is one refusal, and a stream that carried that count forever would
    park on two more of them however far apart they fell."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    page = Page(
        source_ref="docs/plan",
        body="the launch window opens at dawn",
        stream="docs",
        title="Launch plan",
    )
    driver, _ = _scripted_driver(
        [StreamSkipped(PARK_REASON), SyncResult(pages=(page,))], database_url, tmp_path / "blobs"
    )

    await _sync(driver)
    assert (await _source_state(source_id))["consecutive_refusals"] == 1
    await _make_due()
    await _sync(driver)

    state = await _source_state(source_id)
    assert (state["consecutive_refusals"], state["parked_at"]) == (0, None)


async def test_an_unpark_landing_under_the_claim_survives_the_refused_run(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The member acts once, and once is enough. A source one refusal short of the threshold is
    claimed, and the resync or reconnect that clears its counter lands while that claim is held —
    neither unpark touches `claimed_by`, so the refused run's writer still matches the row. Counting
    from the value the claim was taken on would park it on a count the member had already reset,
    with the row unclaimable, no check or counter saying so, and nothing left to tell them the act
    was discarded. The count is read where it is written, so their zero is what this refusal counts
    from: one refusal, no marks, and the row due again on the next pass."""
    reader = _meter(monkeypatch)
    submitted: list[tuple[str, int, str, dict[str, str]]] = []

    async def _record(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append((name, status, message, tags))

    monkeypatch.setattr(sync, "emit_service_check", _record)
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, "held-cursor")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .values(consecutive_refusals=SOURCE_REFUSAL_PARK_THRESHOLD - 1)
            .where(tables.source.c.uid == source_id)
        )
    driver = SyncDriver(
        backends={
            SCRIPTED_BACKEND: _ResyncingSource(
                source_id=source_id,
                workspace_id=workspace_id,
                outcome=StreamSkipped(PARK_REASON),
            )
        },
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )

    await _sync(driver)

    state = await _source_state(source_id)
    assert (state["parked_at"], state["parked_reason"]) == (None, None)
    assert state["consecutive_refusals"] == 1  # the member's zero, then this refusal
    assert state["cursor"] == "held-cursor"
    assert submitted == []
    assert _metric_points(reader, PARKED_METRIC) == []
    assert await _claims(driver) == (source_id,)  # the request stands: due again immediately


CONNECTOR_PROVIDER = "slack"
CONNECTOR_STREAM = "messages"
CONNECTOR_ACCOUNT = "ca_T0ACME"
CONNECTOR_HOST = "slack.com"
SYNC_METRIC = f"ufo.{SOURCE_SYNC_FAILED_METRIC}"


class _ConnectorSource:
    """The scripted backend under a connector's config shape: one provider stream for one account,
    which is what a registered Slack or GitHub source row is."""

    config_model: ClassVar[type[ConnectorSourceConfig]] = ConnectorSourceConfig

    def __init__(self, outcomes: list[SyncResult | Exception]) -> None:
        self._outcomes = outcomes

    async def fetch(
        self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        outcome = self._outcomes.pop(0)
        match outcome:
            case Exception():
                raise outcome
            case _:
                return outcome


class _NeverOpens:
    """A transaction that never opens, the way one fails against a pool with nothing left.
    SQLAlchemy's own `TimeoutError` is what a checkout past the ceiling raises — a class the
    database layer alone can raise, and never the socket class of the same name."""

    async def __aenter__(self) -> object:
        raise sa_exc.TimeoutError(
            "QueuePool limit of size 1 overflow 0 reached, connection timed out"
        )

    async def __aexit__(self, *_: object) -> None: ...


class _FullVolume(FilesystemBlobStore):
    """A blob volume that refuses every body, the shape a full or read-only filesystem raises out of
    `put` while the database under the same commit is healthy."""

    async def put(self, key: str, data: bytes) -> None:
        raise OSError("no space left on device")


class _ConnectionDies:
    """A transaction whose connection is already gone, the shape a database outage raises through
    the asyncpg driver."""

    async def __aenter__(self) -> object:
        raise sa_exc.InterfaceError(
            "connection was closed", None, asyncpg.InterfaceError("connection is closed")
        )

    async def __aexit__(self, *_: object) -> None: ...


async def _seed_connector_source(workspace_id: UUID, *, consecutive_errors: int = 0) -> UUID:
    source_uid = uuid7()
    connection_id = await _connection(
        workspace_id, CONNECTOR_PROVIDER, account_id=CONNECTOR_ACCOUNT
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_uid,
                workspace_id=workspace_id,
                backend=CONNECTOR_PROVIDER,
                config={"stream": CONNECTOR_STREAM},
                feed_handle=feed_handle_for({"stream": CONNECTOR_STREAM}, frozenset()),
                connection_id=connection_id,
                cursor=None,
                consecutive_errors=consecutive_errors,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_uid


def _connector_driver(
    outcomes: list[SyncResult | Exception], database_url: str, blob_root: Path
) -> SyncDriver:
    return SyncDriver(
        backends={CONNECTOR_PROVIDER: _ConnectorSource(outcomes)},
        blob=FilesystemBlobStore(root=blob_root),
        postgres=database_url.startswith("postgresql"),
    )


def _meter(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    return reader


def _metric_points(reader: InMemoryMetricReader, name: str) -> list[dict[str, str]]:
    """A reader that collected nothing returns no data at all rather than an empty envelope, and
    whether this one collected anything depends on which instruments were already bound to an
    earlier provider when the test started — `db_tx_acquire_ms` fires on every transaction. So the
    absence of a metric has to read the same as the absence of every metric."""
    collected = reader.get_metrics_data()
    if collected is None:
        return []
    return [
        dict(point.attributes or {})
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.message == event]


def _utc(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


async def test_a_failed_stream_reports_its_provider_stream_and_cause(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The event names the provider stream, the account, the class that raised, how many runs in a
    row have failed and when the backoff lets the next one start — the same numbers the row lands on
    — at error severity, where a sweep for failures looks. The counter carries provider and stream,
    so an alert points at the source rather than at whatever the failure exhausted."""
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    driver = _connector_driver(
        [RuntimeError("provider 500: conversations.history")], database_url, tmp_path / "blobs"
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    state = await _source_state(source_id)
    assert state["consecutive_errors"] == 1
    failures = _events(caplog, "source_sync.failed")
    assert len(failures) == 1
    assert failures[0].levelno == logging.ERROR
    assert failures[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(source_id),
        "provider": CONNECTOR_PROVIDER,
        "stream": CONNECTOR_STREAM,
        "account_id": CONNECTOR_ACCOUNT,
        "error_class": "RuntimeError",
        "provider_fault": "",
        "consecutive_errors": 1,
        "next_sync_at": _utc(state["next_sync_at"]).isoformat(),
        "cursor_reset": False,
    }
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "RuntimeError",
        }
    ]


TREE_PROVIDER = "probe"
FAR_FUTURE = datetime(2999, 1, 1, tzinfo=UTC)
CONTRACTS_WATERMARK = "contracts-watermark"
OUTGOING_WATERMARK = "1700000000.000100"


class _TreeConnector(Connector):
    """A root `contracts` stream and a `tasks` child hanging under it, driven through the real
    fan-out so the pages the driver commits are the ones a declared child yields. Every partition
    the walk asks for answers one task."""

    name = TREE_PROVIDER
    base_url = "https://probe.example"

    def __init__(
        self,
        *,
        delete_missing: bool,
        fetch_budget: int | None = None,
        contracts: tuple[str, ...] = ("c1",),
    ) -> None:
        self._delete_missing = delete_missing
        self._fetch_budget = fetch_budget
        self._contracts = contracts
        self.asked: list[str] = []

    def streams(self) -> list[StreamSpec]:
        return [
            StreamSpec(name="contracts", source_object="contracts"),
            StreamSpec(
                name="tasks",
                source_object="tasks",
                delete_missing=self._delete_missing,
                fetch_budget=self._fetch_budget,
                parents=(ParentEdge(stream="contracts", path="/contracts/{id}/tasks"),),
            ),
        ]

    async def fetch_page(
        self,
        stream: StreamSpec,
        *,
        cursor: str | None,
        credential: Credential,
        base_url: str,
        self_user_id: str | None,
        backfill_after: datetime | None = None,
        yield_rate_limits: bool = True,
        parents: Any = None,
        watched: Any = None,
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if not stream.parents:
            yield StreamPage(
                records=[{"id": contract} for contract in self._contracts],
                next_cursor=CONTRACTS_WATERMARK,
            )
            return
        async for page in fanned_out(stream, Run(cursor=cursor, parents=parents), self._pages):
            yield page

    async def _pages(self, partition: Partition, bound: PartitionBound) -> AsyncIterator[WalkPage]:
        self.asked.append(partition.path)
        yield WalkPage(records=[{"id": "t1"}])


class _BearerForEveryConnection:
    def bind(self, connection_id: UUID) -> "_BearerForEveryConnection":
        return self

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(bearer="unused")


async def _seed_tree(workspace_id: UUID) -> tuple[UUID, UUID, UUID]:
    """Two source rows of one connection: `contracts`, landed and not due, and `tasks`, due. The
    contracts landed two live pages — one projected, one from before the projection existed — and
    the tasks landed one page under the unprojected contract. Returns the tasks source, the
    unprojected contract page and the task page."""
    connection_id = await _connection(workspace_id, TREE_PROVIDER, account_id="acct")
    contracts_uid, tasks_uid = uuid7(), uuid7()
    unprojected_uid, task_uid = uuid7(), uuid7()
    async with workspace_tx() as connection:
        for source_uid, stream, due in (
            (contracts_uid, "contracts", FAR_FUTURE),
            (tasks_uid, "tasks", sa.func.now()),
        ):
            await connection.execute(
                sa.insert(tables.source).values(
                    uid=source_uid,
                    workspace_id=workspace_id,
                    backend=TREE_PROVIDER,
                    config={"stream": stream},
                    feed_handle=feed_handle_for({"stream": stream}, frozenset()),
                    connection_id=connection_id,
                    cursor=None,
                    next_sync_at=due,
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        for page_uid, source_uid, stream, identity, parent_fields in (
            (uuid7(), contracts_uid, "contracts", "contracts/c1", {"id": "c1"}),
            (unprojected_uid, contracts_uid, "contracts", "contracts/c2", None),
            (task_uid, tasks_uid, "tasks", "tasks/c2/t9", None),
        ):
            await connection.execute(
                sa.insert(tables.page).values(
                    uid=page_uid,
                    workspace_id=workspace_id,
                    source_uid=source_uid,
                    source_identity=identity,
                    digest="d",
                    body_ref="",
                    stream=stream,
                    title=identity,
                    parent_fields=parent_fields,
                    subject=SHARED_SUBJECT,
                    tombstone=False,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return tasks_uid, unprojected_uid, task_uid


async def _live_task_identities(tasks_uid: UUID) -> set[str]:
    async with workspace_tx() as connection:
        return set(
            (
                await connection.execute(
                    sa.select(tables.page.c.source_identity).where(
                        tables.page.c.source_uid == tasks_uid,
                        tables.page.c.tombstone.is_(False),
                    )
                )
            ).scalars()
        )


def _tree_driver(
    database_url: str,
    blob_root: Path,
    *,
    delete_missing: bool,
    fetch_budget: int | None = None,
    contracts: tuple[str, ...] = ("c1",),
) -> tuple[SyncDriver, _TreeConnector]:
    connector = _TreeConnector(
        delete_missing=delete_missing,
        fetch_budget=fetch_budget,
        contracts=contracts,
    )
    driver = SyncDriver(
        backends={TREE_PROVIDER: ConnectorBackend(connector=connector)},
        blob=FilesystemBlobStore(root=blob_root),
        postgres=database_url.startswith("postgresql"),
        source_credentials=_BearerForEveryConnection(),
    )
    return driver, connector


async def _seed_contracts(
    workspace_id: UUID, ids: tuple[str, ...], *, due: str = "tasks"
) -> tuple[UUID, UUID]:
    """A `contracts` row holding one projected page per id and a `tasks` row holding nothing, one
    of them due. Returns both source uids."""
    connection_id = await _connection(workspace_id, TREE_PROVIDER, account_id="acct")
    contracts_uid, tasks_uid = uuid7(), uuid7()
    async with workspace_tx() as connection:
        for source_uid, stream in ((contracts_uid, "contracts"), (tasks_uid, "tasks")):
            due_at = sa.func.now() if stream == due else FAR_FUTURE
            await connection.execute(
                sa.insert(tables.source).values(
                    uid=source_uid,
                    workspace_id=workspace_id,
                    backend=TREE_PROVIDER,
                    config={"stream": stream},
                    feed_handle=feed_handle_for({"stream": stream}, frozenset()),
                    connection_id=connection_id,
                    cursor=None,
                    next_sync_at=due_at,
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        for contract in ids:
            await _land_contract(connection, workspace_id, contracts_uid, contract)
    return contracts_uid, tasks_uid


async def _land_contract(
    connection: AsyncConnection, workspace_id: UUID, contracts_uid: UUID, contract: str
) -> None:
    await connection.execute(
        sa.insert(tables.page).values(
            uid=uuid7(),
            workspace_id=workspace_id,
            source_uid=contracts_uid,
            source_identity=f"contracts/{contract}",
            digest="d",
            body_ref="",
            stream="contracts",
            title=contract,
            parent_fields={"id": contract},
            subject=SHARED_SUBJECT,
            tombstone=False,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def _tick(driver: SyncDriver, connector: _TreeConnector, tasks_uid: UUID) -> list[str]:
    """One due run of the tasks row: what it asked, in the order it asked."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .values(next_sync_at=sa.func.now())
            .where(tables.source.c.uid == tasks_uid)
        )
    connector.asked.clear()
    await _sync(driver)
    return list(connector.asked)


async def test_a_budgeted_pass_resumes_past_a_mark_whose_parent_is_gone(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The mark a truncated pass leaves is the last key it fetched. A resume that looked for that
    exact key would, once the parent behind it was deleted, discard the whole enumeration, close the
    pass with the tail unvisited and hold it for the interval. Resuming means skipping every key at
    or below the mark in the order the walk imposes, so the next tick carries on from the first key
    after it and the pass ends where the enumeration does."""
    workspace_id = await _workspace()
    _, tasks_uid = await _seed_contracts(workspace_id, tuple(f"c{i:02d}" for i in range(10)))
    driver, connector = _tree_driver(
        database_url, tmp_path / "blobs", delete_missing=False, fetch_budget=4
    )

    first = await _tick(driver, connector, tasks_uid)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.page).where(tables.page.c.source_identity == "contracts/c03")
        )
    second = await _tick(driver, connector, tasks_uid)
    third = await _tick(driver, connector, tasks_uid)

    assert first == [f"/contracts/c{i:02d}/tasks" for i in range(4)]
    assert second == [f"/contracts/c{i:02d}/tasks" for i in range(4, 8)]
    assert third == ["/contracts/c08/tasks", "/contracts/c09/tasks"]
    assert json.loads((await _source_state(tasks_uid))["partition_cursor"]) == {}


async def test_a_parent_landed_below_the_mark_waits_for_the_next_pass(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A parent that lands between two ticks at a key below the mark is not what the pass is
    resuming toward: the tick after the mark spends its budget on the tail it has not reached, and
    the new parent is fetched by the pass that opens once this one completes."""
    workspace_id = await _workspace()
    contracts_uid, tasks_uid = await _seed_contracts(
        workspace_id, tuple(f"c{i:02d}" for i in range(6))
    )
    driver, connector = _tree_driver(
        database_url, tmp_path / "blobs", delete_missing=False, fetch_budget=4
    )

    first = await _tick(driver, connector, tasks_uid)
    async with workspace_tx() as connection:
        await _land_contract(connection, workspace_id, contracts_uid, "c01a")
    second = await _tick(driver, connector, tasks_uid)
    third = await _tick(driver, connector, tasks_uid)

    assert first == [f"/contracts/c{i:02d}/tasks" for i in range(4)]
    assert second == ["/contracts/c04/tasks", "/contracts/c05/tasks"]
    assert third == [
        "/contracts/c00/tasks",
        "/contracts/c01/tasks",
        "/contracts/c01a/tasks",
        "/contracts/c02/tasks",
    ]


async def test_a_snapshot_child_fails_rather_than_sweep_under_an_unprojected_parent(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The deploy shape: `page.parent_fields` arrives NULL on every page the previous image landed,
    so the first pass of a `delete_missing` child would enumerate none of those parents, fetch a
    snapshot holding nothing under them, and tombstone every one of its live pages. The run fails
    instead — recorded as a failure, committing nothing — and the page under the unprojected
    contract stands. Once the contract re-lands with its projection the run passes and the sweep
    behaves as a snapshot's should: the task the provider no longer holds is tombstoned, the ones it
    does land."""
    workspace_id = await _workspace()
    tasks_uid, unprojected_uid, _ = await _seed_tree(workspace_id)
    driver, _ = _tree_driver(database_url, tmp_path / "blobs", delete_missing=True)

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    assert await _live_task_identities(tasks_uid) == {"tasks/c2/t9"}
    (failed,) = _events(caplog, "source_sync.failed")
    assert failed.ufo["stream"] == "tasks"
    assert failed.ufo["error_class"] == "RuntimeError"
    assert _events(caplog, "source_sync.ok") == []
    assert (await _source_state(tasks_uid))["consecutive_errors"] == 1

    caplog.clear()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.page)
            .values(parent_fields={"id": "c2"})
            .where(tables.page.c.uid == unprojected_uid)
        )
        await connection.execute(
            sa.update(tables.source)
            .values(next_sync_at=sa.func.now())
            .where(tables.source.c.uid == tasks_uid)
        )
    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    assert _events(caplog, "source_sync.failed") == []
    assert len(_events(caplog, "source_sync.ok")) == 1
    assert (await _source_state(tasks_uid))["consecutive_errors"] == 0
    assert await _live_task_identities(tasks_uid) == {"tasks/c1/t1", "tasks/c2/t1"}


async def test_an_empty_parent_catalog_becomes_authoritative_only_after_it_completes(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    contracts_uid, tasks_uid = await _seed_contracts(workspace_id, ())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                uid=uuid7(),
                workspace_id=workspace_id,
                source_uid=tasks_uid,
                source_identity="tasks/c1/t1",
                digest="d",
                body_ref="",
                stream="tasks",
                title="t1",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    driver, connector = _tree_driver(
        database_url,
        tmp_path / "blobs",
        delete_missing=True,
        contracts=(),
    )

    await _sync(driver)

    assert connector.asked == []
    assert await _live_task_identities(tasks_uid) == {"tasks/c1/t1"}
    assert (await _source_state(tasks_uid))["consecutive_errors"] == 1

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .values(next_sync_at=sa.func.now())
            .where(tables.source.c.uid == contracts_uid)
        )
        await connection.execute(
            sa.update(tables.source)
            .values(next_sync_at=FAR_FUTURE)
            .where(tables.source.c.uid == tasks_uid)
        )
    await _sync(driver)
    assert (await _source_state(contracts_uid))["synced_at"] is not None

    await _tick(driver, connector, tasks_uid)

    assert await _live_task_identities(tasks_uid) == set()


async def test_an_incremental_child_passes_over_a_parent_landed_without_its_projection(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A child that asserts nothing about deletions loses nothing by skipping a parent it cannot
    read yet: the run passes, lands the tasks of the projected contract, and leaves the page under
    the unprojected one standing for the pass after that contract re-lands."""
    workspace_id = await _workspace()
    tasks_uid, _, _ = await _seed_tree(workspace_id)
    driver, _ = _tree_driver(database_url, tmp_path / "blobs", delete_missing=False)

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    assert _events(caplog, "source_sync.failed") == []
    assert len(_events(caplog, "source_sync.ok")) == 1
    assert (await _source_state(tasks_uid))["consecutive_errors"] == 0
    assert await _live_task_identities(tasks_uid) == {"tasks/c1/t1", "tasks/c2/t9"}


async def test_a_tree_rows_map_lives_beside_the_watermark_the_outgoing_image_keeps(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """While the fleet rolls, both images run the same row. The outgoing one reads `cursor` as its
    own watermark and writes one back; this one keeps a fanned-out stream's map in
    `partition_cursor`, so neither ever reads what the other wrote. The watermark the outgoing image
    left stands untouched after a run, and the map records the tree key the pass stopped at."""
    workspace_id = await _workspace()
    _, tasks_uid = await _seed_contracts(workspace_id, tuple(f"c{i:02d}" for i in range(6)))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .values(cursor=OUTGOING_WATERMARK)
            .where(tables.source.c.uid == tasks_uid)
        )
    driver, connector = _tree_driver(
        database_url, tmp_path / "blobs", delete_missing=False, fetch_budget=4
    )

    await _tick(driver, connector, tasks_uid)

    state = await _source_state(tasks_uid)
    assert state["cursor"] == OUTGOING_WATERMARK
    assert json.loads(state["partition_cursor"])[PASS_FROM_KEY] == (
        "contracts/c03\n/contracts/c03/tasks"
    )


async def test_a_root_rows_watermark_stays_in_the_column_it_always_had(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A stream that walks its collection itself keeps `cursor` exactly as today, through the same
    connector backend, and never touches the map column."""
    workspace_id = await _workspace()
    contracts_uid, _ = await _seed_contracts(workspace_id, (), due="contracts")
    driver, _ = _tree_driver(database_url, tmp_path / "blobs", delete_missing=False)

    await _sync(driver)

    state = await _source_state(contracts_uid)
    assert state["cursor"] == CONTRACTS_WATERMARK
    assert state["partition_cursor"] is None


async def test_a_stream_whose_pool_had_nothing_left_defers_instead_of_failing(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool-exhaustion shape: every checkout after the claim reaches the pool's ceiling and
    raises `sqlalchemy.exc.TimeoutError`, so the release that would have freed the row cannot write
    either. A pool this fleet saturated is this deploy's own fault and never the provider's, so the
    run is deferred rather than failed, the release that cannot write is suppressed, and the claim
    lease is what frees the row. The stream and the class stay on record."""
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    driver = _connector_driver([SyncResult(pages=())], database_url, tmp_path / "blobs")
    opened = 0
    real_tx = sync.workspace_tx

    def _only_the_claim_opens() -> object:
        nonlocal opened
        opened += 1
        return real_tx() if opened == 1 else _NeverOpens()

    monkeypatch.setattr(sync, "workspace_tx", _only_the_claim_opens)

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    monkeypatch.undo()
    assert not _events(caplog, "source_sync.failed")
    deferrals = _events(caplog, "source_sync.deferred")
    assert [
        (record.ufo["provider"], record.ufo["stream"], record.ufo["error_class"])
        for record in deferrals
    ] == [(CONNECTOR_PROVIDER, CONNECTOR_STREAM, "TimeoutError")]
    assert deferrals[0].ufo["consecutive_errors"] == 0
    assert (await _source_state(source_id))["consecutive_errors"] == 0


async def test_a_database_outage_defers_the_run_instead_of_counting_it_against_the_source(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection this deploy's database lost is not a stream that failed: the provider answered,
    and what raised was the transaction the run wrote through. The event names the same fields as a
    failure so one search reads both, and the counter, the metric and the CRITICAL check all stay
    where they were — the database has its own monitor, and a page from this one would name the
    wrong subsystem. The row comes back at the normal interval, not at a backoff earned by a source
    that did nothing wrong."""
    reader = _meter(monkeypatch)
    submitted: list[tuple[str, int, str, dict[str, str]]] = []

    async def _record(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append((name, status, message, tags))

    monkeypatch.setattr(sync, "emit_service_check", _record)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id, consecutive_errors=2)
    driver = _connector_driver([SyncResult(pages=())], database_url, tmp_path / "blobs")
    opened = 0
    real_tx = sync.workspace_tx

    def _the_commit_loses_its_connection() -> object:
        nonlocal opened
        opened += 1
        return _ConnectionDies() if opened == 2 else real_tx()

    monkeypatch.setattr(sync, "workspace_tx", _the_commit_loses_its_connection)
    started = datetime.now(UTC)

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    monkeypatch.setattr(sync, "workspace_tx", real_tx)
    state = await _source_state(source_id)
    assert state["consecutive_errors"] == 2
    assert state["claimed_by"] is None
    assert not _events(caplog, "source_sync.failed")
    deferrals = _events(caplog, "source_sync.deferred")
    assert len(deferrals) == 1
    assert deferrals[0].levelno == logging.WARNING
    assert deferrals[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(source_id),
        "provider": CONNECTOR_PROVIDER,
        "stream": CONNECTOR_STREAM,
        "account_id": CONNECTOR_ACCOUNT,
        "error_class": "InterfaceError",
        "provider_fault": "",
        "consecutive_errors": 2,
        "next_sync_at": _utc(state["next_sync_at"]).isoformat(),
        "cursor_reset": False,
    }
    assert _utc(state["next_sync_at"]) >= started + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS)
    assert _metric_points(reader, SYNC_METRIC) == []
    assert submitted == []


async def test_a_provider_http_error_is_still_a_failed_run(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deferral is for this deploy's database alone. A provider that answered with a status the
    stream cannot use is the failure this driver reports: the event, the counter, the row's error
    count and the CRITICAL check all stand as they did."""
    reader = _meter(monkeypatch)
    submitted: list[tuple[str, int, str, dict[str, str]]] = []

    async def _record(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append((name, status, message, tags))

    monkeypatch.setattr(sync, "emit_service_check", _record)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    request = httpx.Request("GET", f"https://{CONNECTOR_HOST}/api/conversations.history")
    with pytest.raises(httpx.HTTPStatusError) as raised:
        rest._raise_for_status(httpx.Response(500, text="server error", request=request))
    driver = _connector_driver([raised.value], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    assert not _events(caplog, "source_sync.deferred")
    failures = _events(caplog, "source_sync.failed")
    assert len(failures) == 1
    assert failures[0].ufo["error_class"] == "HTTPStatusError"
    assert failures[0].ufo["consecutive_errors"] == 1
    assert (await _source_state(source_id))["consecutive_errors"] == 1
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "HTTPStatusError",
        }
    ]
    assert [status for _name, status, _message, _tags in submitted] == [o11y.SERVICE_CHECK_CRITICAL]


async def test_a_provider_socket_timeout_is_still_a_failed_run(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider socket that never answered raises the builtin `TimeoutError`, the class a lost
    database dial raises too. The stream is the one thing the run did reach for, so the class alone
    never buys a deferral: the event, the counter, the row's error count and the CRITICAL check all
    stand as they did."""
    reader = _meter(monkeypatch)
    submitted: list[tuple[str, int, str, dict[str, str]]] = []

    async def _record(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append((name, status, message, tags))

    monkeypatch.setattr(sync, "emit_service_check", _record)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    driver = _connector_driver(
        [TimeoutError("provider timed out")], database_url, tmp_path / "blobs"
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    assert not _events(caplog, "source_sync.deferred")
    failures = _events(caplog, "source_sync.failed")
    assert len(failures) == 1
    assert failures[0].ufo["error_class"] == "TimeoutError"
    assert failures[0].ufo["consecutive_errors"] == 1
    assert (await _source_state(source_id))["consecutive_errors"] == 1
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "TimeoutError",
        }
    ]
    assert [status for _name, status, _message, _tags in submitted] == [o11y.SERVICE_CHECK_CRITICAL]


async def test_a_blob_volume_that_refuses_a_body_is_still_a_failed_run(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body write sits inside the commit, past the fetch, and a full or read-only volume raises
    `OSError` there — the class a lost database dial raises as well. Deferring it would refetch the
    whole provider every interval and leave the sync monitor blind to a volume that takes no writes,
    so the blob store's own fault stays the failed run: the event, the counter, the row's error
    count and the CRITICAL check all stand."""
    reader = _meter(monkeypatch)
    submitted: list[tuple[str, int, str, dict[str, str]]] = []

    async def _record(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append((name, status, message, tags))

    monkeypatch.setattr(sync, "emit_service_check", _record)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    page = Page(
        source_ref="C1/1700000000.1",
        body="the deploy is green",
        stream=CONNECTOR_STREAM,
        title="#general",
    )
    driver = SyncDriver(
        backends={CONNECTOR_PROVIDER: _ConnectorSource([SyncResult(pages=(page,))])},
        blob=_FullVolume(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    assert not _events(caplog, "source_sync.deferred")
    failures = _events(caplog, "source_sync.failed")
    assert len(failures) == 1
    assert failures[0].ufo["error_class"] == "OSError"
    assert failures[0].ufo["consecutive_errors"] == 1
    assert (await _source_state(source_id))["consecutive_errors"] == 1
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "OSError",
        }
    ]
    assert [status for _name, status, _message, _tags in submitted] == [o11y.SERVICE_CHECK_CRITICAL]


async def test_a_synced_stream_reports_what_it_wrote_and_what_it_dropped(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful run reports the records it could not represent beside the pages it wrote. A drop
    warns per record, and no health query selects a warning, so a run that lands nothing but drops
    everything would otherwise read as a healthy stream."""
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    gone_id = await _seed_prior_page(workspace_id, source_id, "C1/1600000000.1")
    page = Page(
        source_ref="C1/1700000000.1",
        body="the deploy is green",
        stream=CONNECTOR_STREAM,
        title="#general",
    )
    driver = _connector_driver(
        [
            SyncResult(pages=(page,), deletes=("C1/1600000000.1", "C1/1500000000.1")),
            SyncResult(pages=(page.model_copy(update={"title": "#deploys"}),), dropped=2),
        ],
        database_url,
        tmp_path / "blobs",
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
        await _make_due()
        await _sync(driver)

    synced = _events(caplog, "source_sync.ok")
    assert [record.levelno for record in synced] == [logging.INFO, logging.INFO]
    assert synced[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(source_id),
        "provider": CONNECTOR_PROVIDER,
        "stream": CONNECTOR_STREAM,
        "account_id": CONNECTOR_ACCOUNT,
        "pages_fetched": 1,
        "pages_written": 1,
        "pages_tombstoned": 1,
        "pages_dropped": 0,
    }
    assert await _tombstone(gone_id) is True
    assert (synced[1].ufo["pages_written"], synced[1].ufo["pages_tombstoned"]) == (1, 0)
    assert synced[1].ufo["pages_dropped"] == 2
    assert not _events(caplog, "source_sync.failed")
    assert not _metric_points(reader, SYNC_METRIC)


async def test_a_provider_fault_reports_its_status_and_url_and_never_its_body_or_a_key(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record an operator reads must not be where a secret lands. A status error's message is
    built out of the provider's response body, and a header a protocol rejects is quoted raw — the
    key a member pasted with a trailing newline — so neither message reaches the pipeline: the fault
    renders as the status and URL of the request that drew it, query dropped, and any other class is
    named by `error_class` alone. On the counter a status is a class of its own, not a bucket it
    shares with an extension's crash."""
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    request = httpx.Request(
        "GET", "https://slack.com/api/conversations.history?token=xoxb-SUPERSECRET&limit=200"
    )
    body = '{"ok":false,"error":"invalid_auth","provided":"xoxb-SUPERSECRET"}'
    with pytest.raises(httpx.HTTPStatusError) as raised:
        rest._raise_for_status(httpx.Response(401, text=body, request=request))
    rejected_header = httpx.LocalProtocolError("Illegal header value b'Bearer xoxb-SUPERSECRET\\n'")
    driver = _connector_driver([raised.value, rejected_header], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
        await _make_due()
        await _sync(driver)

    failures = _events(caplog, "source_sync.failed")
    assert [(record.ufo["error_class"], record.ufo["provider_fault"]) for record in failures] == [
        ("HTTPStatusError", "401 GET https://slack.com/api/conversations.history"),
        ("LocalProtocolError", ""),
    ]
    assert not [record for record in failures if "SUPERSECRET" in str(record.ufo)]
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "HTTPStatusError",
        },
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "LocalProtocolError",
        },
    ]


async def test_a_graphql_fault_carries_the_reason_its_errors_array_names(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A GraphQL endpoint refuses a document its schema cannot validate with a transport 400 and
    names the offending part in the body — Linear names the field. The status and the URL alone say
    that a request was refused and never which part of it, so the reasons the `errors` array carries
    ride onto the event, each with the code beside it. The rest of the body stays out: an error body
    echoes what the request sent."""
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    request = httpx.Request("POST", "https://api.linear.app/graphql")
    body = json.dumps(
        {
            "errors": [
                {
                    "message": 'Cannot query field "descriptionState" on type "Issue".',
                    "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
                }
            ],
            "provided": "lin_api_SUPERSECRET",
        }
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        rest._raise_for_status(httpx.Response(400, text=body, request=request))
    driver = _connector_driver([raised.value], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    failure = _events(caplog, "source_sync.failed")[0]
    assert failure.ufo["provider_fault"] == (
        "400 POST https://api.linear.app/graphql: "
        'Cannot query field "descriptionState" on type "Issue". [GRAPHQL_VALIDATION_FAILED]'
    )
    assert "SUPERSECRET" not in str(failure.ufo)


async def test_a_google_fault_carries_the_reason_and_status_its_nested_error_names(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A quota throttle, a token whose scopes were never granted, and a calendar nobody shared all
    answer `403`, so the status and the URL cannot say which one a sweep is looking at. Google names
    the refusal in a closed vocabulary nested under `error` — under `errors` on the older APIs and
    `details` on the newer ones — and that reason rides beside the envelope's canonical status, or
    the status alone rides where neither array does. The free-text `message` stays out of all three,
    because an error body echoes what the request sent."""
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    events = httpx.Request(
        "GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events?syncToken=CJDx"
    )
    throttled = {
        "error": {
            "errors": [
                {
                    "domain": "usageLimits",
                    "reason": "rateLimitExceeded",
                    "message": "Rate Limit Exceeded",
                }
            ],
            "code": 403,
            "message": "Rate Limit Exceeded",
            "status": "RESOURCE_EXHAUSTED",
        },
        "provided": "ya29.SUPERSECRET",
    }
    unscoped = {
        "error": {
            "code": 403,
            "message": "Request had insufficient authentication scopes.",
            "status": "PERMISSION_DENIED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                    "domain": "googleapis.com",
                }
            ],
        }
    }
    unnamed = {
        "error": {
            "code": 403,
            "message": "The caller does not have permission",
            "status": "PERMISSION_DENIED",
        }
    }
    bodies = (throttled, unscoped, unnamed)
    raised = []
    for body in bodies:
        with pytest.raises(httpx.HTTPStatusError) as error:
            rest._raise_for_status(httpx.Response(403, json=body, request=events))
        raised.append(error.value)
    driver = _connector_driver(list(raised), database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        for _ in bodies:
            await _make_due()
            await _sync(driver)

    request = "403 GET https://www.googleapis.com/calendar/v3/calendars/primary/events"
    failures = _events(caplog, "source_sync.failed")
    assert [record.ufo["provider_fault"] for record in failures] == [
        f"{request}: rateLimitExceeded [RESOURCE_EXHAUSTED]",
        f"{request}: ACCESS_TOKEN_SCOPE_INSUFFICIENT [PERMISSION_DENIED]",
        f"{request}: PERMISSION_DENIED",
    ]
    assert not [record for record in failures if "SUPERSECRET" in str(record.ufo)]
    assert not [record for record in failures if "Rate Limit Exceeded" in str(record.ufo)]


async def test_a_stream_fault_reports_the_reason_the_backend_authored_for_it(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A backend that cannot read what the provider answered knows the request it made and the shape
    that broke, and nothing else knows either — so `StreamFault` carries that reason onto the event
    and a sweep reads which read failed instead of the class alone, which is all a bare `ValueError`
    ever left behind."""
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    reason = "googlesheets: values:batchGet on spreadsheet s1 asked for 2 ranges and returned 1"
    driver = _connector_driver([StreamFault(reason)], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    failure = _events(caplog, "source_sync.failed")[0]
    assert (failure.ufo["error_class"], failure.ufo["provider_fault"]) == ("StreamFault", reason)


async def test_a_rejected_page_reports_the_field_and_rule_that_rejected_it(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A record the page model rejects fails the run that carried it, and the class alone says
    nothing about which record or which field — the shape a stuck stream leaves an operator with.
    The fault names the field paths and the rules that rejected them, and the rejected values stay
    out: they are the provider's payload, or the parameters a member registered the source with."""
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    with pytest.raises(ValidationError) as raised:
        Page(
            source_ref="messages/m1", body="body", stream="messages", title="", created_at="MMXXVI"
        )
    driver = _connector_driver([raised.value], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    failure = _events(caplog, "source_sync.failed")[0]
    assert (failure.ufo["error_class"], failure.ufo["provider_fault"]) == (
        "ValidationError",
        "title: string_too_short; created_at: value_error",
    )
    assert "MMXXVI" not in str(failure.ufo)


async def test_a_provider_fault_is_bounded_before_it_reaches_the_record(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The URL is the provider's, not ours: a path built out of ids and filters runs as long as the
    provider cares to make it, so the rendering is capped where it is emitted."""
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    request = httpx.Request(
        "GET", "https://slack.com/api/" + "c" * (SYNC_PROVIDER_FAULT_MAX_CHARS * 2)
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        rest._raise_for_status(httpx.Response(429, text="ratelimited", request=request))
    driver = _connector_driver([raised.value], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    fault = _events(caplog, "source_sync.failed")[0].ufo["provider_fault"]
    assert fault.startswith("429 GET https://slack.com/api/c")
    assert len(fault) == SYNC_PROVIDER_FAULT_MAX_CHARS


async def test_a_failing_stream_holds_a_critical_check_until_a_run_succeeds(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flake and an outage read the same on a counter — one failure, counted — so what separates
    them is the state the next run reports. Every run submits its own outcome under the row's own
    tags: the failed run holds that row CRITICAL and names how many runs failed in a row, and the
    run that succeeds submits OK on the same tags, which is what takes the alert down with no
    operator and no window to wait out. The row id is one of those tags, because a check instance is
    its name, host, and tags together — without it the other workspace that connects the same
    provider stream submits its OK onto this row's status history and clears an alert it knows
    nothing about. The counter keeps the stream dimensions alone, with the class that raised."""
    reader = _meter(monkeypatch)
    submitted: list[tuple[str, int, str, dict[str, str]]] = []

    async def _record(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append((name, status, message, tags))

    monkeypatch.setattr(sync, "emit_service_check", _record)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    page = Page(
        source_ref="C1/1700000000.1",
        body="the deploy is green",
        stream=CONNECTOR_STREAM,
        title="#general",
    )
    driver = _connector_driver(
        [TimeoutError("provider timed out"), SyncResult(pages=(page,))],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)
    await _make_due()
    await _sync(driver)

    tags = {"provider": CONNECTOR_PROVIDER, "stream": CONNECTOR_STREAM}
    check_tags = {**tags, "source_id": str(source_id)}
    assert submitted == [
        (
            SOURCE_SYNC_CHECK,
            o11y.SERVICE_CHECK_CRITICAL,
            "1 consecutive failed runs, last TimeoutError",
            check_tags,
        ),
        (SOURCE_SYNC_CHECK, o11y.SERVICE_CHECK_OK, "", check_tags),
    ]
    assert SOURCE_SYNC_CHECK in o11y.SERVICE_CHECKS
    assert _metric_points(reader, SYNC_METRIC) == [{**tags, "error_class": "TimeoutError"}]


async def test_telemetry_that_raises_never_breaks_the_sync_run(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    failing_id = await _seed_connector_source(workspace_id)
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    driver = SyncDriver(
        backends={
            CONNECTOR_PROVIDER: _ConnectorSource([RuntimeError("provider 500")]),
            FOLDER_BACKEND: FolderSource(),
        },
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    await _register_folder(good)

    def _collector_gone(*_: object, **__: object) -> None:
        raise RuntimeError("collector unreachable")

    failure: list[tuple[object, object, object]] = []
    success: list[tuple[object, object, object]] = []

    def _log_error_gone(event: str, **fields: object) -> None:
        assert event == "source_sync.failed"
        failure.append((fields["provider"], fields["stream"], fields["error_class"]))
        raise RuntimeError("log pipeline unreachable")

    def _log_gone(event: str, **fields: object) -> None:
        assert event == "source_sync.ok"
        success.append((fields["provider"], fields["stream"], fields["pages_written"]))
        raise RuntimeError("log pipeline unreachable")

    submitted: list[int] = []

    async def _intake_gone(name: str, status: int, message: str = "", /, **tags: str) -> None:
        submitted.append(status)
        raise RuntimeError("check intake unreachable")

    monkeypatch.setattr(sync, "emit_metric", _collector_gone)
    monkeypatch.setattr(sync, "log_error", _log_error_gone)
    monkeypatch.setattr(sync, "log", _log_gone)
    monkeypatch.setattr(sync, "emit_service_check", _intake_gone)

    await _sync(driver)

    assert (await _source_state(failing_id))["consecutive_errors"] == 1
    assert len(await _pages()) == 1
    assert await _claims(driver) == ()
    assert failure == [(CONNECTOR_PROVIDER, CONNECTOR_STREAM, "RuntimeError")]
    assert success == [(FOLDER_BACKEND, "", 1)]
    assert submitted == [o11y.SERVICE_CHECK_CRITICAL, o11y.SERVICE_CHECK_OK]


class _NoProbes:
    async def run(self, conversation_id: UUID, command: str, timeout_s: int) -> ExecResult:
        raise AssertionError("no probe expected")


def test_source_sync_and_turn_dispatch_register_as_core_jobs(
    database_url: str, tmp_path: Path
) -> None:
    driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    runner = PageChangeRunner(
        manifests=(),
        pages=CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "pages")),
    )
    specs = core_jobs(
        driver,
        TurnDispatcher(client=None),
        runner,
        DeliverySweep(invoker_for=lambda _: None, registry=SubagentRegistry(())),
        BackgroundTaskSweep(probes=_NoProbes(), invoker_for=lambda _: None),
        None,
    )
    assert [spec.name for spec in specs] == [
        SOURCE_SYNC_JOB,
        TURN_DISPATCH_JOB,
        RESULT_DELIVERY_JOB,
        BACKGROUND_TASKS_JOB,
        INDEX_REAP_JOB,
        JOB_DAY_ROLLUP_JOB,
        PRODUCT_CENSUS_JOB,
        GRAVATAR_JOB,
    ]
    assert all(spec.schedule is not None for spec in specs)
    keys = {binding.key for binding in bindings_from((), specs)}
    assert keys == {
        f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}",
        f"{CORE_EXTENSION}:{TURN_DISPATCH_JOB}",
        f"{CORE_EXTENSION}:{RESULT_DELIVERY_JOB}",
        f"{CORE_EXTENSION}:{BACKGROUND_TASKS_JOB}",
        f"{CORE_EXTENSION}:{INDEX_REAP_JOB}",
        f"{CORE_EXTENSION}:{JOB_DAY_ROLLUP_JOB}",
        f"{CORE_EXTENSION}:{PRODUCT_CENSUS_JOB}",
        f"{CORE_EXTENSION}:{GRAVATAR_JOB}",
    }


def test_sources_pure_sync_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 6
    for check in checks:
        check()


async def _hold_under_the_line(workspace_id: UUID) -> None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 1_000_000, 0, "grant")
            await set_reserve(connection, workspace_id, 2_000_000)


async def _credit_above_the_line(workspace_id: UUID) -> None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 5_000_000, 5_000_000, "card/1")


async def test_a_workspace_under_its_balance_line_syncs_nothing_until_credited(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """Under the line the gate refuses the model the page's facts are derived with, so a fetch there
    feeds a consumer that cannot run. The driver leaves every source of the workspace alone — none
    is a candidate, none is claimed — and the credit that lifts the balance is the whole wake: the
    rows were due all along, and the next pass takes them."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "first.md").write_text("the first note")
    driver, _index, _service = _wire(database_url, vec((21, 1.0)), tmp_path / "blobs", workspace_id)
    connection_id = await _connection(workspace_id, FOLDER_BACKEND)
    with ws(workspace_id):
        await context_for("probe", frozenset()).register_source(
            FOLDER_BACKEND, SourceConfig(root=str(root)), connection_id=connection_id
        )
    await _sync(driver)
    assert len(await _pages()) == 1

    await _hold_under_the_line(workspace_id)
    (root / "second.md").write_text("the second note")
    await _make_due()
    assert await driver.candidate_workspaces() == ()
    with ws(workspace_id):
        assert await _claims(driver) == ()
    await _sync(driver)
    assert len(await _pages()) == 1

    await _credit_above_the_line(workspace_id)
    assert await driver.candidate_workspaces() == (workspace_id,)
    await _sync(driver)
    assert {page["title"] for page in await _pages()} == {"first.md", "second.md"}


async def test_page_change_candidates_skip_a_workspace_under_its_balance_line(
    db: None, tmp_path: Path
) -> None:
    """A workspace whose pages run past the consumer's cursor is still no candidate while its
    balance sits at or under the line: the consumer's model call would be refused and the cursor
    would hold through a stall logged every tick. The credit that lifts the balance makes it a
    candidate again with no other write."""
    funded_id, held_id = uuid4(), uuid4()
    await _seed_page(funded_id)
    await _seed_page(held_id)
    await _hold_under_the_line(held_id)
    runner = _probe_runner(tmp_path)
    (consumer,) = runner.consumers()

    assert await runner.workspaces_with_changes(consumer) == (funded_id,)

    await _credit_above_the_line(held_id)
    assert set(await runner.workspaces_with_changes(consumer)) == {funded_id, held_id}


async def _store_own_key(workspace_id: UUID) -> None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.credential).values(
                    workspace_id=workspace_id,
                    slot="anthropic_api_key",
                    ciphertext=b"sealed",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


async def test_a_workspace_serving_the_consumers_model_on_its_own_key_is_never_held(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The gate admits a job's model call under the line while the workspace holds its own key for
    that model and its balance is above zero, so the hold must not reach that workspace: its sources
    stay candidates and are claimed, on the driver that knows which slots the deploy's models
    key from. A driver told no slots holds it, as the gate would with no key to weigh."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "first.md").write_text("the first note")
    driver, _index, _service = _wire(database_url, vec((21, 1.0)), tmp_path / "blobs", workspace_id)
    connection_id = await _connection(workspace_id, FOLDER_BACKEND)
    with ws(workspace_id):
        await context_for("probe", frozenset()).register_source(
            FOLDER_BACKEND, SourceConfig(root=str(root)), connection_id=connection_id
        )
    await _hold_under_the_line(workspace_id)
    await _store_own_key(workspace_id)
    assert await driver.candidate_workspaces() == ()

    own_key = replace(driver, own_key_slots=("anthropic_api_key",))
    assert await own_key.candidate_workspaces() == (workspace_id,)
    with ws(workspace_id):
        assert len(await _claims(own_key)) == 1


async def test_page_change_candidates_keep_a_workspace_on_its_own_key(
    db: None, tmp_path: Path
) -> None:
    held_id = uuid4()
    await _seed_page(held_id)
    await _hold_under_the_line(held_id)
    await _store_own_key(held_id)
    runner = _probe_runner(tmp_path)
    (consumer,) = runner.consumers()
    assert await runner.workspaces_with_changes(consumer) == ()

    own_key = replace(runner, own_key_slots=("anthropic_api_key",))
    assert await own_key.workspaces_with_changes(consumer) == (held_id,)


def _utc(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


async def test_a_stream_this_account_does_not_use_falls_back_to_a_daily_look(
    db: None,
    database_url: str,
    tmp_path: Path,
) -> None:
    """`canonical` is a connector constant, so a stream that is content for the accounts using it
    and empty for the rest cannot be flagged per account. Registering it anyway is only affordable
    if a row that never lands anything stops costing a request a minute."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    driver, _ = _scripted_driver(
        [SyncResult(pages=[], snapshot=False) for _ in range(SOURCE_EMPTY_IDLE_THRESHOLD)],
        database_url,
        tmp_path / "blobs",
    )

    async def _state() -> sa.RowMapping:
        async with workspace_tx() as connection:
            return (
                (
                    await connection.execute(
                        sa.select(
                            tables.source.c.consecutive_empty,
                            tables.source.c.next_sync_at,
                        ).where(tables.source.c.uid == source_id)
                    )
                )
                .mappings()
                .one()
            )

    with ws(workspace_id):
        for run in range(SOURCE_EMPTY_IDLE_THRESHOLD):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.source).values(next_sync_at=sa.func.now())
                )
            await driver.run()
            state = await _state()
            assert state["consecutive_empty"] == run + 1

        due = _utc(state["next_sync_at"]) - datetime.now(UTC)
        assert due > timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS * 10)


async def test_a_quiet_stream_that_has_landed_a_page_keeps_the_interval(
    db: None,
    database_url: str,
    tmp_path: Path,
) -> None:
    """Being quiet is not being unused. A row that has ever landed a page keeps the fast cadence
    however many empty runs follow, so a populated stream between changes is never slowed."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    landed = SyncResult(
        pages=[Page(source_ref="only", title="one", body="body", stream="scripted")],
        snapshot=False,
    )
    driver, _ = _scripted_driver(
        [
            landed,
            *(SyncResult(pages=[], snapshot=False) for _ in range(SOURCE_EMPTY_IDLE_THRESHOLD)),
        ],
        database_url,
        tmp_path / "blobs",
    )

    with ws(workspace_id):
        for _ in range(SOURCE_EMPTY_IDLE_THRESHOLD + 1):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.source).values(next_sync_at=sa.func.now())
                )
            await driver.run()

        async with workspace_tx() as connection:
            state = (
                (
                    await connection.execute(
                        sa.select(tables.source.c.next_sync_at).where(
                            tables.source.c.uid == source_id
                        )
                    )
                )
                .mappings()
                .one()
            )
    due = _utc(state["next_sync_at"]) - datetime.now(UTC)
    assert due < timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS * 5)
