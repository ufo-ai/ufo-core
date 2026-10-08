from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
import ufo_ext_memory.manifest as memory
import ufo_ext_research.tools as research_tools
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_memory.objects import MEMORY_OBJECT, MemoryObjects
from ufo_ext_memory.store import (
    MemoryIndexer,
    body_digest,
    mem_page,
    memory_item,
)
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.index import default_index
from ufo_testsupport.memory_service import MemoryServiceStandIn

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.billing.accounting import MEMORY_SERVICE
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.indexing import OWNER_KIND_PAGE, Chunk, TextChunker, chunk_digest
from ufo.runtime.objects import (
    ObjectListQuery,
)
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
)

"""The memory extension's internal search vertical and its page-derived memory fences, proved with
the research extension's tools: memory ships with the agent module, research with this deploy, and
the seam between them is importable only here."""


INTERNAL_VERTICAL_TOOL = next(
    tool
    for tool in research_tools.RESEARCH_TOOLS
    if tool.name == research_tools.SEARCH_VERTICAL_TOOL
)


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the memory tool tests")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _ext(
    index: object,
    embed: object,
    audience: Audience = SHARED_AUDIENCE,
    name: str = "memory",
) -> ExtensionContext:
    return context_for(
        name,
        frozenset(),
        index=index,
        embed=embed,
        audience=audience,
        cloud_client=name == memory.NAME,
    )


def _tool_ctx(
    ext: ExtensionContext | None,
    member_id: UUID | None,
    tmp_path: Path,
    *,
    workspace_id: UUID | None = None,
    blob: FilesystemBlobStore | None = None,
    audience: Audience | None = None,
) -> ToolContext:
    exact_audience = conversation_audience(member_id) if audience is None else audience
    if ext is not None and ext.audience != exact_audience:
        raise ValueError("tool and extension audiences differ")
    return ToolContext(
        sandbox=None,
        blob=blob if blob is not None else FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id if workspace_id is not None else uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=exact_audience,
        artifact_token_secret="",
        ext=ext,
    )


async def _seed_page_chunk(
    workspace_id: UUID,
    subject: str,
    body: str,
    vector: tuple[float, ...],
    owner_member_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    connection_id, source_id, page_id = uuid4(), uuid7(), uuid7()
    now = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="folder",
                account_id=connection_id.hex,
                host="",
                owner_member_id=owner_member_id,
                shared=owner_member_id is None,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=connection_id,
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                digest=f"sha256:{page_id.hex}",
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Page",
                subject=subject,
                tombstone=False,
                indexed=True,
                created_at=now,
                updated_at=now,
            )
        )
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(mem_page).values(
                page_uid=page_id,
                workspace_id=workspace_id,
                subject=subject,
                revision=revision,
                created_at=now,
            )
        )
    with ws(workspace_id):
        await default_index().upsert(
            (
                Chunk(
                    chunk_digest(OWNER_KIND_PAGE, str(page_id), f"sha256:{page_id.hex}", 0, body),
                    OWNER_KIND_PAGE,
                    str(page_id),
                    subject,
                    0,
                    body,
                    vector,
                ),
            )
        )
    return connection_id, page_id


async def _member(workspace_id: UUID, email: str) -> UUID:
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


async def _reading_agent(
    workspace_id: UUID, agent_id: UUID, connection_id: UUID | None = None
) -> None:
    """The agent a page search runs under: the workspace's main agent, which reaches every feed,
    or a specialist holding a grant on one connection. A page reaches neither without it."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=f"agent-{agent_id.hex[:8]}",
                prompt="p",
                model="m",
                is_main=connection_id is None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if connection_id is not None:
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


def _research_ctx(
    workspace_id: UUID, member: UUID | None, probe: tuple[float, ...], tmp_path: Path
) -> ToolContext:
    """The turn an `internal` vertical search runs on: the research extension's own scoped
    context over the deploy index, which is what reaches this extension's page store."""
    ext = _ext(
        default_index(),
        StubEmbed(probe),
        conversation_audience(member),
        name="research",
    )
    return _tool_ctx(ext, member, tmp_path, workspace_id=workspace_id)


async def _search_internal(ctx: ToolContext, query: str) -> ToolResult:
    tool = INTERNAL_VERTICAL_TOOL
    args = {"vertical": research_tools.INTERNAL_VERTICAL, "query": query}
    return await tool.handler(ctx, tool.input_model.model_validate(args))


async def test_a_page_derived_memory_object_is_fenced_on_the_connector_grant(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    connection_id, source_id, page_id, item_id = uuid4(), uuid7(), uuid7(), uuid4()
    now = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="folder",
                account_id="",
                host="",
                owner_member_id=None,
                shared=True,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=connection_id,
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                digest="sha256:page",
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Page",
                subject="shared",
                tombstone=False,
                created_at=now,
                updated_at=now,
            )
        )
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject="shared",
                body="the vault code is 8842",
                body_digest=body_digest("the vault code is 8842"),
                item_class="fact",
                memory_kind="fact",
                confidence=5,
                created_from_page_uid=page_id,
                created_from_page_revision=revision,
                source_uid=source_id,
                embedding_digest="sha256:seeded",
                created_at=now,
                updated_at=now,
            )
        )
    ext = _ext(default_index(), StubEmbed(vec((0, 1.0))))
    ungranted = _tool_ctx(ext, None, tmp_path, workspace_id=workspace_id)
    granted = _tool_ctx(ext, None, tmp_path, workspace_id=workspace_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=granted.turn.agent_id,
                workspace_id=workspace_id,
                name="granted",
                prompt="p",
                model="m",
                is_main=False,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=granted.turn.agent_id,
                connection_id=connection_id,
                created_at=now,
                updated_at=now,
            )
        )
    query = ObjectListQuery(supported_fields=MEMORY_OBJECT.list_fields)
    with ws(workspace_id):
        ungranted_names = {row.name for row in (await MemoryObjects().list(ungranted, query)).rows}
        granted_names = {row.name for row in (await MemoryObjects().list(granted, query)).rows}
        ungranted_get = await MemoryObjects().get(ungranted, str(item_id))
        granted_get = await MemoryObjects().get(granted, str(item_id))
    assert str(item_id) not in ungranted_names
    assert str(item_id) in granted_names
    assert ungranted_get is None
    assert granted_get is not None


async def test_the_internal_vertical_reports_no_match_on_an_empty_page_store(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    ctx = _research_ctx(workspace_id, member, vec((0, 1.0)), tmp_path)
    await _reading_agent(workspace_id, ctx.turn.agent_id)
    with ws(workspace_id):
        result = await _search_internal(ctx, "anything")
    assert result.content[0].text == research_tools.NO_INTERNAL_MATCHES_MESSAGE


async def test_the_internal_vertical_seals_another_members_private_page(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    alice = await _member(workspace_id, "alice@example.com")
    bob = await _member(workspace_id, "bob@example.com")
    probe = vec((0, 1.0))
    connection_id, page_id = await _seed_page_chunk(
        workspace_id,
        member_subject(alice),
        "alices private onboarding checklist",
        probe,
        owner_member_id=alice,
    )
    contexts = {}
    for member, grant in ((alice, None), (bob, connection_id)):
        contexts[member] = _research_ctx(workspace_id, member, probe, tmp_path)
        await _reading_agent(workspace_id, contexts[member].turn.agent_id, grant)
    with ws(workspace_id):
        mine = await _search_internal(contexts[alice], "onboarding checklist")
        theirs = await _search_internal(contexts[bob], "onboarding checklist")
    assert f"page/{page_id}" in mine.content[0].text
    assert theirs.content[0].text == research_tools.NO_INTERNAL_MATCHES_MESSAGE


async def test_the_internal_vertical_serves_the_page_store_alone(db: None, tmp_path: Path) -> None:
    """The vertical's whole point: the same index legs memory_search reads its page hits from, with
    no recall leg — a fact stating the term is memory_search's row and never this vertical's."""
    workspace_id = await _workspace()
    member = uuid4()
    probe = vec((0, 1.0))
    ext = _ext(default_index(), StubEmbed(probe), conversation_audience(member))
    ctx = _tool_ctx(ext, member, tmp_path, workspace_id=workspace_id)
    await _reading_agent(workspace_id, ctx.turn.agent_id)
    connection_id, page_id = await _seed_page_chunk(
        workspace_id, SHARED_SUBJECT, "the expense policy pays mileage at 45p", probe
    )
    pages_ctx = _research_ctx(workspace_id, member, probe, tmp_path)
    await _reading_agent(workspace_id, pages_ctx.turn.agent_id, connection_id)
    with ws(workspace_id):
        await _run("memory_update", ctx, body="the expense policy is owned by finance")
        await MemoryIndexer(
            index=default_index(),
            embed=StubEmbed(probe),
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=_ext(default_index(), StubEmbed(probe)).page_states,
        ).run()
        pages = await _search_internal(pages_ctx, "expense policy")
        both = await _run("memory_search", ctx, queries=["expense policy"])
    page_text = pages.content[0].text
    assert f"page/{page_id}" in page_text
    assert "the expense policy pays mileage at 45p" in page_text
    assert "owned by finance" not in page_text
    assert "owned by finance" in both.content[0].text


async def test_the_research_internal_vertical_reads_page_passages_alone(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    probe = vec((0, 1.0))
    _connection, page_id = await _seed_page_chunk(
        workspace_id, SHARED_SUBJECT, "13.4 Putaway authorisation", probe
    )
    template = "Template 24 the post format"
    with ws(workspace_id):
        await default_index().upsert(
            (
                Chunk(
                    chunk_digest(
                        OWNER_KIND_PAGE, str(page_id), f"sha256:{page_id.hex}", 1, template
                    ),
                    OWNER_KIND_PAGE,
                    str(page_id),
                    SHARED_SUBJECT,
                    1,
                    template,
                    probe,
                ),
            )
        )
    stand_in = MemoryServiceStandIn()
    selected = context_for(
        memory.NAME,
        frozenset(),
        index=default_index(),
        audience=conversation_audience(member),
        cloud_client=True,
        cloud=cloud_apis_for(stand_in.app, clients=frozenset({MEMORY_SERVICE})),
    )
    pages_ctx = _research_ctx(workspace_id, member, probe, tmp_path)
    await _reading_agent(workspace_id, pages_ctx.turn.agent_id)
    reader = pages_ctx.source_reader()
    with ws(workspace_id):
        matches = await memory.MemorySearchService(selected).search_pages(
            ("putaway", "template", "post format"), reader
        )
        found = await _search_internal(pages_ctx, "putaway")
    assert [match.text for match in matches] == ["13.4 Putaway authorisation", template]
    assert "13.4 Putaway authorisation" in found.content[0].text
    assert stand_in.sent == []


MEMORY_TOOLS = {tool.name: tool for tool in memory.manifest().tools}


async def _run(name: str, ctx: ToolContext, **args: object) -> ToolResult:
    tool = MEMORY_TOOLS[name]
    return await tool.handler(ctx, tool.input_model.model_validate({**args}))
