"""The knowledge-graph query surface — the graph_search tool and the user_prompt_submit context
hook — and its loader discovery.

Both consumers are driven over a real `ExtensionContext` against a real graph the extractor built
from real pages: the tool through its handler, the hook through a real `HookContext`. The producer
(the extract page_change hook) and these consumers land together and are proven together —
extracted graph in, cited traversal out — and the discovery test confirms the extension registers
under its name with its page_change derivation hook and no background job of its own.
"""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_knowledge_graph.manifest as kg
from ufo_ext_knowledge_graph.store import GraphExtractor, UnknownEdgeType

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.loader import discovered
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.manifest import HookContext, InjectContext, UserPromptSubmit
from ufo.sources.sync import CorePageFeed
from ufo.subjects import SHARED_SUBJECT
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

EXTENSION = "knowledge_graph"
WHEN = datetime(2026, 1, 1, tzinfo=UTC)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the graph search tests")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed(blob: FilesystemBlobStore, workspace_id: UUID, body: str) -> None:
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
    batch = await CorePageFeed(blob=blob).pages_changed_since(None, 50)
    await GraphExtractor(transaction=workspace_tx, workspace_id=workspace_id).apply(batch.changes)


def _tool_ctx(ext: ExtensionContext) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=ext.store.workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        member_id=None,
        artifact_token_secret="",
        ext=ext,
    )


def _hook_ctx(ext: ExtensionContext, text: str, payload: object) -> HookContext:
    return HookContext(
        ext=ext,
        turn=Turn(
            id=uuid4(),
            workspace_id=ext.store.workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound=text,
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        member_id=None,
        payload=payload,
    )


def test_extension_is_discovered_and_registers_its_page_change_hook() -> None:
    assert EXTENSION in discovered()
    manifest = kg.manifest()
    assert "page_change" in {hook.event for hook in manifest.hooks}
    assert manifest.jobs == ()


async def test_graph_search_returns_cited_relations(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seed(
            FilesystemBlobStore(root=tmp_path), workspace_id, "# Sam Altman\n[[founded::OpenAI]]"
        )
        ext = context_for(EXTENSION, frozenset())

        result = await kg.graph_search_handler(
            _tool_ctx(ext), kg.GraphSearchInput(entity="Sam Altman")
        )
        text = result.content[0].text
        assert "founded" in text
        assert "OpenAI" in text
        assert "[page " in text


async def test_graph_search_filters_by_edge_type(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seed(
            FilesystemBlobStore(root=tmp_path),
            workspace_id,
            "# Sam Altman\n[[founded::OpenAI]] and [[advises::Acme]]",
        )
        ext = context_for(EXTENSION, frozenset())

        result = await kg.graph_search_handler(
            _tool_ctx(ext), kg.GraphSearchInput(entity="Sam Altman", edge_types=("founded",))
        )
        text = result.content[0].text
        assert "OpenAI" in text
        assert "Acme" not in text


async def test_graph_search_rejects_an_unknown_edge_type(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id), pytest.raises(UnknownEdgeType):
        ext = context_for(EXTENSION, frozenset())
        await kg.graph_search_handler(
            _tool_ctx(ext), kg.GraphSearchInput(entity="x", edge_types=("acquired",))
        )


async def test_graph_search_reports_no_relations_on_an_empty_graph(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        ext = context_for(EXTENSION, frozenset())
        result = await kg.graph_search_handler(_tool_ctx(ext), kg.GraphSearchInput(entity="nobody"))
        assert "No graph relations" in result.content[0].text


async def test_user_prompt_submit_hook_injects_the_relevant_subgraph(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        await _seed(
            FilesystemBlobStore(root=tmp_path), workspace_id, "# Sam Altman\n[[founded::OpenAI]]"
        )
        ext = context_for(EXTENSION, frozenset())

        outcome = await kg.graph_context_hook(
            _hook_ctx(
                ext, "tell me about Sam Altman", UserPromptSubmit(text="tell me about Sam Altman")
            )
        )
        assert isinstance(outcome, InjectContext)
        assert "OpenAI" in outcome.text


async def test_user_prompt_submit_hook_ignores_a_non_prompt_payload(db: None, tmp_path) -> None:
    workspace_id = await _workspace()
    with ws(workspace_id):
        ext = context_for(EXTENSION, frozenset())
        assert await kg.graph_context_hook(_hook_ctx(ext, "hi", None)) is None
