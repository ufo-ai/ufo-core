"""Tier B — the metered model pass — driven against a stub ModelClient, never a live model.

A real `ModelAccess` wraps a stub client returning a canned typed extraction: the extractor lands
the typed edges it names and the completion's tokens are priced onto the workspace ledger
(turn_id NULL). An out-of-vocabulary edge type from the model is rejected, not silently dropped.
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from selfhost_ext_knowledge_graph.store import (
    GraphExtractor,
    GraphStore,
    UnknownEdgeType,
    graph_edge,
    graph_subjects,
    render_subgraph,
)

from selfhost.accounting import CORE_PRICING
from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ModelAccess
from selfhost.models.interface import ModelEvent, ModelRequest, TextDelta
from selfhost.schema import tables
from selfhost.schema.records import Usage
from selfhost.sources.sync import CorePageFeed
from selfhost.subjects import SHARED_SUBJECT

EXTENSION = "knowledge_graph"
WHEN = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class StubModelClient:
    """A ModelClient standing in for the provider: streams one canned completion and one usage
    event, so the metered wrapper's pricing runs against a fixed burn without a live model."""

    payload: str
    usage: Usage

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=self.payload)
        yield self.usage


def _model(workspace_id: UUID, payload: str, usage: Usage) -> ModelAccess:
    return ModelAccess(
        workspace_id, "claude-opus-4-8", CORE_PRICING, lambda: StubModelClient(payload, usage)
    )


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_page(blob: FilesystemBlobStore, workspace_id: UUID, body: str) -> None:
    source_id, page_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                cursor=None,
                next_sync_at=datetime.now(UTC),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await blob.put(f"pages/{page_id}", body.encode())
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                body_ref=f"pages/{page_id}",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=WHEN,
                updated_at=WHEN,
            )
        )


async def _run(blob: FilesystemBlobStore, workspace_id: UUID, model: ModelAccess) -> None:
    batch = await CorePageFeed(blob=blob).pages_changed_since(None, 50)
    await GraphExtractor(transaction=workspace_tx, workspace_id=workspace_id, model=model).apply(
        batch.changes
    )


async def test_tier_b_lands_typed_edges_and_meters_the_call(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "# Jane Doe\nJane works at Acme and advises Globex.")
    payload = (
        '{"relations": ['
        '{"edge_type": "works_at", "target": "Acme", "confidence": 0.8},'
        '{"edge_type": "advises", "target": "Globex", "confidence": 0.6}]}'
    )
    await _run(
        blob, workspace_id, _model(workspace_id, payload, Usage(input_tokens=100, output_tokens=50))
    )

    async with workspace_tx() as connection:
        edges = (
            await connection.execute(
                sa.select(graph_edge.c.edge_type, graph_edge.c.confidence).where(
                    graph_edge.c.workspace_id == workspace_id
                )
            )
        ).all()
        ledger = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.turn_id,
                    tables.ledger.c.dimension,
                    tables.ledger.c.model,
                    tables.ledger.c.priced_micro_usd,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
    by_type = {edge.edge_type: edge.confidence for edge in edges}
    assert by_type["works_at"] == pytest.approx(0.8)
    assert by_type["advises"] == pytest.approx(0.6)
    assert len(ledger) == 1
    assert ledger[0].turn_id is None
    assert ledger[0].dimension == "tokens"
    assert ledger[0].model == "claude-opus-4-8"
    assert int(ledger[0].priced_micro_usd) == 1750

    subgraph = await GraphStore(transaction=workspace_tx, workspace_id=workspace_id).traverse(
        "Jane Doe", graph_subjects(None), 2, frozenset()
    )
    rendered = "\n".join(render_subgraph(subgraph))
    assert "works_at" in rendered
    assert "Acme" in rendered


async def test_tier_b_rejects_an_out_of_vocab_edge_type(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    await _seed_page(blob, workspace_id, "# Jane Doe\nJane acquired Foo.")
    payload = '{"relations": [{"edge_type": "acquired", "target": "Foo", "confidence": 0.9}]}'
    with pytest.raises(UnknownEdgeType):
        await _run(blob, workspace_id, _model(workspace_id, payload, Usage(input_tokens=10)))
