"""The render-previews job: it registers only where the preview service is configured, fills the
three preview columns from the service's reply, and leaves a row untouched when the service refuses.
The render and store are the service's own (proven in the preview crate); here a MockTransport
returns the service's contractual reply so core's query → mint → post → parse → update is exercised
against a real database and a real S3 presign."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa

from ufo.blob import S3BlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.jobs import RENDER_PREVIEWS_JOB, core_jobs
from ufo.loop.delivery import DeliverySweep
from ufo.loop.subagents import SubagentRegistry
from ufo.preview_renderer import PreviewRenderer
from ufo.schema import tables
from ufo.sources.sync import FolderSource, SyncDriver
from ufo.workspace import ws


def _renderer(blob: WorkspaceBlobStore) -> PreviewRenderer:
    return PreviewRenderer(blob=blob, service_url="http://preview.svc:8930")


def _specs_with(preview_renderer: PreviewRenderer | None) -> list[str]:
    specs = core_jobs(
        SyncDriver(backends={"folder": FolderSource()}, blob=None, postgres=False),
        _dispatcher(),
        _runner(),
        DeliverySweep(invoker_for=lambda _: None, registry=SubagentRegistry(())),
        preview_renderer,
    )
    return [spec.name for spec in specs]


def test_render_previews_registers_only_when_configured(
    s3_store: S3BlobStore,
) -> None:
    blob = WorkspaceBlobStore(backend=s3_store)
    assert RENDER_PREVIEWS_JOB in _specs_with(_renderer(blob))
    assert RENDER_PREVIEWS_JOB not in _specs_with(None)


async def _seed_shared_artifact(
    workspace_id, *, filename: str, blob_key: str, created_at: datetime, preview: bool = False
) -> None:
    agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
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
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="preview",
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="hi",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=blob_key,
                id=uuid4(),
                workspace_id=workspace_id,
                filename=filename,
                subject="a file",
                media_type="application/pdf",
                size_bytes=10,
                preview_blob_key=("artifacts/x/p.png" if preview else None),
                preview_media_type=("image/png" if preview else None),
                preview_size_bytes=(1 if preview else None),
                created_at=created_at,
                updated_at=created_at,
            )
        )


async def _preview_columns(workspace_id, blob_key: str) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.shared_artifact.c.preview_blob_key,
                    tables.shared_artifact.c.preview_media_type,
                    tables.shared_artifact.c.preview_size_bytes,
                ).where(tables.shared_artifact.c.blob_key == blob_key)
            )
        ).one()


def _service(handler, monkeypatch: pytest.MonkeyPatch) -> None:
    from ufo import preview_renderer

    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(preview_renderer.httpx, "AsyncClient", factory)


async def test_render_previews_fills_columns_from_the_service(
    s3_store: S3BlobStore, db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = uuid4()
    blob_key = "artifacts/abc/report.pdf"
    with ws(workspace_id):
        await _seed_shared_artifact(
            workspace_id, filename="report.pdf", blob_key=blob_key, created_at=datetime.now(UTC)
        )

    seen: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["url"] = str(request.url).encode()
        return httpx.Response(200, json={"size_bytes": 4242, "page_count": 1})

    _service(handler, monkeypatch)
    with ws(workspace_id):
        await _renderer(WorkspaceBlobStore(backend=s3_store)).run()
        row = await _preview_columns(workspace_id, blob_key)

    assert row.preview_media_type == "image/png"
    assert row.preview_size_bytes == 4242
    assert row.preview_blob_key is not None and row.preview_blob_key.endswith("/report.png")
    assert seen["url"].endswith(b"/render")
    assert b"source_url" in seen["body"]
    assert b"put_url" in seen["body"]
    assert b'"kind": "pdf"' in seen["body"]


async def test_render_previews_leaves_the_row_on_refusal(
    s3_store: S3BlobStore, db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = uuid4()
    blob_key = "artifacts/def/broken.pdf"
    with ws(workspace_id):
        await _seed_shared_artifact(
            workspace_id, filename="broken.pdf", blob_key=blob_key, created_at=datetime.now(UTC)
        )

    _service(lambda request: httpx.Response(502, json={"error": "fetch_refused"}), monkeypatch)
    with ws(workspace_id):
        await _renderer(WorkspaceBlobStore(backend=s3_store)).run()
        row = await _preview_columns(workspace_id, blob_key)

    assert row.preview_blob_key is None
    assert row.preview_media_type is None
    assert row.preview_size_bytes is None


async def test_render_previews_skips_a_stale_row(
    s3_store: S3BlobStore, db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row older than the retry window is not a candidate — a permanent failure ages out rather
    than being retried forever."""
    workspace_id = uuid4()
    blob_key = "artifacts/ghi/old.pdf"
    with ws(workspace_id):
        await _seed_shared_artifact(
            workspace_id,
            filename="old.pdf",
            blob_key=blob_key,
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"size_bytes": 1})

    _service(handler, monkeypatch)
    with ws(workspace_id):
        await _renderer(WorkspaceBlobStore(backend=s3_store)).run()

    assert not called


def _dispatcher():
    from ufo.jobs import TurnDispatcher

    return TurnDispatcher(client=None)


def _runner():
    from ufo.jobs import PageChangeRunner

    return PageChangeRunner(
        manifests=(),
        pages=None,
        invoker_factory=lambda _: None,
        index=None,
        embed=None,
        sandboxes=None,
        registry=None,
        probes=None,
    )
