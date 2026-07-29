"""Typed object links: search finds a ref, `object_get` opens it, links walk the provenance.

The headline chain is the real pipeline end to end — a synced page is distilled into a memory item,
`memory_search` returns the memory's ref, `object_get(memory)` exposes `created_from → page/<id>`,
and the page opens with its bounded body and its `synced_by → source/<name>` link. Around it: the
`conversation` kind resolves artifact and scheduled-task destinations under its disclosure gate, a
superseded memory leaves search and links to its replacement, links stay visibility-congruent (a
hidden object is not-found regardless of who links to it), and malformed relations, kinds, and
target names fail at the boundary."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from pydantic import ValidationError
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.condenser import FactDeriver
from ufo_ext_memory.manifest import manifest as memory_manifest
from ufo_ext_memory.objects import MEMORY_KIND, MEMORY_OBJECT
from ufo_ext_memory.store import memory_item, store_for
from ufo_ext_sources.pages import PAGE_KIND, PAGE_OBJECT
from ufo_ext_sources.registry import SOURCE_KIND, binding_name
from ufo_ext_sources.tools import SOURCE_OBJECT

from ufo.agent_scope import agent
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.conversations import CONVERSATION_KIND, CONVERSATION_OBJECT
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.objects import (
    BoundKind,
    ObjectLink,
    ObjectRef,
    ObjectVerbs,
    UnknownKind,
    UnknownObject,
    VerbNotSupported,
    object_registry,
)
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sources.sync import PageChange
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.tools.context import SpawnResult, TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

MEMORY_TOOLS = {tool.name: tool for tool in memory_manifest().tools}
SEARCH_REF = re.compile(r"\((memory|page)/([0-9a-f-]{36})")
LINK_NARRATION = "following the trail back to the source"
PAGE_BODY = "# Acme contract\n\nThe Acme renewal closes on September 30 for 120k."
DERIVED_FACT = "The Acme renewal closes on September 30"
EXTRACTION = {
    "notability": "high",
    "memory_kind": "fact",
    "confidence": 7,
    "body": DERIVED_FACT,
}


class _StubEmbed:
    """Deterministic EmbedClient at the deploy's one embedding dimension — the corpus contract
    every stored chunk shares, which the index backends enforce (halfvec cast, strict cosine)."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0,) + (0.0,) * (EMBED_DIM - 1) for _ in texts)


class _ExtractionModel:
    model = "stub-extractor"

    def __init__(self, page_id: UUID) -> None:
        self._page_id = page_id

    async def complete(self, request: object) -> str:
        return json.dumps({"facts": [{"page_id": str(self._page_id), **EXTRACTION}]})


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("object link tests never spawn")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member(workspace_id: UUID) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def _agent(workspace_id: UUID) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=agent_id.hex[:8],
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _conversation(
    workspace_id: UUID,
    member_id: UUID | None,
    agent_id: UUID,
    surface: str = "cli",
    created_at: datetime | None = None,
) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=conversation_id.hex,
                member_id=member_id,
                created_at=created_at if created_at is not None else sa.func.now(),
                updated_at=created_at if created_at is not None else sa.func.now(),
            )
        )
    return conversation_id


async def _seed_source(workspace_id: UUID, account: str = "acct-one") -> UUID:
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="asana",
                config={"account": account, "stream": "issues", "base_url": None},
                subject=SHARED_SUBJECT,
                next_sync_at=datetime(2026, 7, 9, tzinfo=UTC),
                created_at=datetime(2026, 7, 9, tzinfo=UTC),
                updated_at=datetime(2026, 7, 9, tzinfo=UTC),
            )
        )
    return source_id


async def _seed_page(
    workspace_id: UUID, source_id: UUID, blob: FilesystemBlobStore, subject: str = SHARED_SUBJECT
) -> UUID:
    page_id = uuid4()
    body_ref = f"pages/{page_id}"
    await blob.put(body_ref, PAGE_BODY.encode())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:abc",
                body_ref=body_ref,
                stream="issues",
                title="Acme contract",
                record_created_at="2026-07-09T00:00:00Z",
                record_updated_at="2026-07-09T00:00:00Z",
                subject=subject,
                tombstone=False,
                created_at=datetime(2026, 7, 9, tzinfo=UTC),
                updated_at=datetime(2026, 7, 9, tzinfo=UTC),
            )
        )
    return page_id


def _verbs() -> dict[str, ToolDef]:
    memory_ctx = context_for("memory", frozenset())
    sources_ctx = context_for("sources", frozenset())
    registry = object_registry(
        (
            BoundKind(kind=MEMORY_OBJECT, extension="memory", context=memory_ctx),
            BoundKind(kind=PAGE_OBJECT, extension="sources", context=sources_ctx),
            BoundKind(kind=SOURCE_OBJECT, extension="sources", context=sources_ctx),
            BoundKind(kind=CONVERSATION_OBJECT, extension=None, context=None),
        )
    )
    return {tool.name: tool for tool in ObjectVerbs(registry=registry).tools()}


def _tool_ctx(
    workspace_id: UUID,
    blob: FilesystemBlobStore,
    member_id: UUID | None = None,
    ext: ExtensionContext | None = None,
) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=blob,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
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
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=ext,
    )


async def _get(tools: dict[str, ToolDef], ctx: ToolContext, kind: str, name: str) -> dict:
    tool = tools["object_get"]
    result: ToolResult = await tool.handler(
        ctx,
        tool.input_model.model_validate(
            {"user_description": LINK_NARRATION, "kind": kind, "name": name}
        ),
    )
    assert result.is_error is False
    block = result.content[0]
    assert isinstance(block, TextContent)
    return yaml.safe_load(block.text)


async def test_search_to_object_get_walks_page_provenance_end_to_end(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    tools = _verbs()
    with ws(workspace_id):
        source_id = await _seed_source(workspace_id)
        page_id = await _seed_page(workspace_id, source_id, blob)

        memory_ext = context_for(
            "memory", frozenset(), index=DefaultIndex(transaction=workspace_tx), embed=_StubEmbed()
        )
        await FactDeriver(store=store_for(memory_ext), model=_ExtractionModel(page_id)).apply(
            (
                PageChange(
                    page_id=page_id,
                    source_id=source_id,
                    subject=SHARED_SUBJECT,
                    stream="notes",
                    title="Cannery office",
                    body=PAGE_BODY,
                    digest="sha256:abc",
                    revision=1,
                    tombstone=False,
                    created_at=datetime(2026, 7, 9, tzinfo=UTC),
                    as_of=datetime(2026, 7, 9, tzinfo=UTC),
                    changed_at=datetime(2026, 7, 9, tzinfo=UTC),
                ),
            )
        )

        search = MEMORY_TOOLS["memory_search"]
        found: ToolResult = await search.handler(
            _tool_ctx(workspace_id, blob, ext=memory_ext),
            search.input_model.model_validate(
                {"user_description": LINK_NARRATION, "queries": ("Acme renewal",)}
            ),
        )
        hit = next(line for line in found.content[0].text.splitlines() if DERIVED_FACT in line)
        ref = SEARCH_REF.search(hit)
        assert ref is not None and ref.group(1) == MEMORY_KIND

        ctx = _tool_ctx(workspace_id, blob)
        memory = await _get(tools, ctx, MEMORY_KIND, ref.group(2))
        assert memory["spec"]["body"] == DERIVED_FACT
        assert memory["created_at"] is not None
        assert {
            "relation": "created_from",
            "target": {"kind": PAGE_KIND, "name": str(page_id)},
        } in memory["links"]

        page = await _get(tools, ctx, PAGE_KIND, str(page_id))
        assert page["spec"]["body"] == PAGE_BODY
        assert page["spec"]["body_truncated"] is False
        source_name = binding_name("asana", "acct-one", None)
        assert page["links"] == [
            {"relation": "synced_by", "target": {"kind": SOURCE_KIND, "name": source_name}}
        ]

        source = await _get(tools, ctx, SOURCE_KIND, source_name)
        assert source["spec"]["provider"] == "asana"


async def test_conversation_kind_gates_on_audience_and_refuses_mutation(db: None) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=Path())
    tools = _verbs()
    with ws(workspace_id):
        member_id = await _member(workspace_id)
        other_id = await _member(workspace_id)
        agent_id = await _agent(workspace_id)
        other_agent_id = await _agent(workspace_id)
        shared_conversation = await _conversation(workspace_id, None, agent_id, surface="slack")
        private_conversation = await _conversation(workspace_id, member_id, agent_id)
        other_agent_conversation = await _conversation(workspace_id, member_id, other_agent_id)

        anyone = _tool_ctx(workspace_id, blob)
        own = _tool_ctx(workspace_id, blob, member_id=member_id)
        get_tool = tools["object_get"]
        with agent(agent_id):
            shared = await _get(tools, anyone, CONVERSATION_KIND, str(shared_conversation))
            assert shared["spec"] == {"surface": "slack", "audience": "shared"}
            assert shared["links"] == []
            assert shared["created_at"] is not None

            mine = await _get(tools, own, CONVERSATION_KIND, str(private_conversation))
            assert mine["spec"] == {"surface": "cli", "audience": f"member:{member_id}"}

            for hidden in (_tool_ctx(workspace_id, blob, member_id=other_id), anyone):
                with pytest.raises(UnknownObject):
                    await get_tool.handler(
                        hidden,
                        get_tool.input_model.model_validate(
                            {
                                "user_description": LINK_NARRATION,
                                "kind": CONVERSATION_KIND,
                                "name": str(private_conversation),
                            }
                        ),
                    )
            with pytest.raises(UnknownObject):
                await get_tool.handler(
                    own,
                    get_tool.input_model.model_validate(
                        {
                            "user_description": LINK_NARRATION,
                            "kind": CONVERSATION_KIND,
                            "name": str(other_agent_conversation),
                        }
                    ),
                )

            apply_tool = tools["object_apply"]
            with pytest.raises(VerbNotSupported):
                await apply_tool.handler(
                    own,
                    apply_tool.input_model.model_validate(
                        {
                            "user_description": LINK_NARRATION,
                            "manifest": yaml.safe_dump(
                                {
                                    "kind": CONVERSATION_KIND,
                                    "name": str(shared_conversation),
                                    "spec": {"surface": "slack", "audience": "shared"},
                                }
                            ),
                        }
                    ),
                )
            delete_tool = tools["object_delete"]
            with pytest.raises(VerbNotSupported):
                await delete_tool.handler(
                    own,
                    delete_tool.input_model.model_validate(
                        {
                            "user_description": LINK_NARRATION,
                            "kind": CONVERSATION_KIND,
                            "name": str(shared_conversation),
                        }
                    ),
                )
            with pytest.raises(UnknownObject):
                await delete_tool.handler(
                    own,
                    delete_tool.input_model.model_validate(
                        {
                            "user_description": LINK_NARRATION,
                            "kind": CONVERSATION_KIND,
                            "name": str(other_agent_conversation),
                        }
                    ),
                )

        with agent(other_agent_id):
            other_mine = await _get(tools, own, CONVERSATION_KIND, str(other_agent_conversation))
            assert other_mine["spec"] == {
                "surface": "cli",
                "audience": f"member:{member_id}",
            }


async def test_conversation_kind_lists_only_visible_rows(db: None) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=Path())
    tools = _verbs()
    with ws(workspace_id):
        member_id = await _member(workspace_id)
        other_id = await _member(workspace_id)
        agent_id = await _agent(workspace_id)
        other_agent_id = await _agent(workspace_id)
        older = await _conversation(
            workspace_id,
            None,
            agent_id,
            surface="cli",
            created_at=datetime(2026, 7, 8, tzinfo=UTC),
        )
        newer = await _conversation(
            workspace_id,
            None,
            agent_id,
            surface="slack",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        )
        mine = await _conversation(
            workspace_id, member_id, agent_id, created_at=datetime(2026, 7, 7, tzinfo=UTC)
        )
        theirs = await _conversation(
            workspace_id, other_id, agent_id, created_at=datetime(2026, 7, 6, tzinfo=UTC)
        )
        walled = await _conversation(
            workspace_id,
            member_id,
            other_agent_id,
            created_at=datetime(2026, 7, 10, tzinfo=UTC),
        )

        list_tool = tools["object_list"]

        async def _names(ctx: ToolContext) -> list[dict[str, object]]:
            result = await list_tool.handler(
                ctx,
                list_tool.input_model.model_validate(
                    {"user_description": LINK_NARRATION, "kind": CONVERSATION_KIND}
                ),
            )
            return json.loads(result.content[0].text)["objects"]

        with agent(agent_id):
            shared_only = await _names(_tool_ctx(workspace_id, blob))
            assert [row["name"] for row in shared_only] == sorted((str(newer), str(older)))
            slack_row = next(row for row in shared_only if row["name"] == str(newer))
            assert slack_row["summary"] == "slack conversation, created 2026-07-09"
            assert slack_row["surface"] == "slack"

            own = await _names(_tool_ctx(workspace_id, blob, member_id=member_id))
            assert [row["name"] for row in own] == sorted((str(newer), str(older), str(mine)))
            assert str(theirs) not in {row["name"] for row in own}
            assert str(walled) not in {row["name"] for row in own}

        with agent(other_agent_id):
            assert [
                row["name"]
                for row in await _names(_tool_ctx(workspace_id, blob, member_id=member_id))
            ] == [str(walled)]


async def test_superseded_memory_leaves_search_and_links_to_its_replacement(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    tools = _verbs()
    with ws(workspace_id):
        memory_ext = context_for(
            "memory", frozenset(), index=DefaultIndex(transaction=workspace_tx), embed=_StubEmbed()
        )
        old_id, new_id = uuid4(), uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(memory_item),
                [
                    {
                        "id": row_id,
                        "workspace_id": workspace_id,
                        "subject": SHARED_SUBJECT,
                        "body": body,
                        "item_class": "fact",
                        "memory_kind": "fact",
                        "confidence": 5,
                        "source_ref": None,
                        "created_from_page_id": None,
                        "as_of": None,
                        "embedding_digest": None,
                        "superseded_by": None,
                        "created_at": datetime(2026, 7, 9, tzinfo=UTC),
                        "updated_at": datetime(2026, 7, 9, tzinfo=UTC),
                    }
                    for row_id, body in (
                        (old_id, "the fleet migration lands friday"),
                        (new_id, "the fleet migration landed and is verified"),
                    )
                ],
            )
            await connection.execute(
                sa.update(memory_item)
                .values(superseded_by=new_id, updated_at=sa.func.now())
                .where(memory_item.c.id == old_id)
            )

        search = MEMORY_TOOLS["memory_search"]
        found: ToolResult = await search.handler(
            _tool_ctx(workspace_id, blob, ext=memory_ext),
            search.input_model.model_validate(
                {"user_description": LINK_NARRATION, "queries": ("fleet migration",)}
            ),
        )
        assert str(old_id) not in found.content[0].text
        assert str(new_id) in found.content[0].text

        ctx = _tool_ctx(workspace_id, blob)
        stale = await _get(tools, ctx, MEMORY_KIND, str(old_id))
        assert stale["links"] == [
            {"relation": "superseded_by", "target": {"kind": MEMORY_KIND, "name": str(new_id)}}
        ]
        replacement = await _get(tools, ctx, MEMORY_KIND, str(new_id))
        assert replacement["spec"]["body"] == "the fleet migration landed and is verified"
        assert replacement["links"] == []

        listing_tool = tools["object_list"]
        listing: ToolResult = await listing_tool.handler(
            ctx,
            listing_tool.input_model.model_validate(
                {"user_description": LINK_NARRATION, "kind": MEMORY_KIND}
            ),
        )
        names = [row["name"] for row in json.loads(listing.content[0].text)["objects"]]
        assert str(new_id) in names
        assert str(old_id) not in names


async def test_links_stay_visibility_congruent_and_hidden_targets_fail_closed(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    tools = _verbs()
    with ws(workspace_id):
        member_id = await _member(workspace_id)
        other_id = await _member(workspace_id)
        source_id = await _seed_source(workspace_id)
        page_id = await _seed_page(workspace_id, source_id, blob, subject=member_subject(member_id))

        memory_ext = context_for("memory", frozenset())
        item_id = uuid4()
        async with workspace_tx() as connection:
            revision = (
                await connection.execute(
                    sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
                )
            ).scalar_one()
            await connection.execute(
                sa.insert(memory_item).values(
                    id=item_id,
                    workspace_id=workspace_id,
                    subject=member_subject(member_id),
                    body="I promised Acme a reply by Monday",
                    item_class="fact",
                    memory_kind="fact",
                    confidence=5,
                    source_ref=None,
                    created_from_page_id=page_id,
                    created_from_page_revision=revision,
                    source_id=source_id,
                    as_of=None,
                    embedding_digest=None,
                    superseded_by=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

        owner_ctx = _tool_ctx(workspace_id, blob, member_id=member_id, ext=memory_ext)
        mine = await _get(tools, owner_ctx, MEMORY_KIND, str(item_id))
        for link in mine["links"]:
            resolved = await _get(tools, owner_ctx, link["target"]["kind"], link["target"]["name"])
            assert resolved["spec"] is not None

        get_tool = tools["object_get"]
        with pytest.raises(UnknownObject):
            await get_tool.handler(
                _tool_ctx(workspace_id, blob, member_id=other_id, ext=memory_ext),
                get_tool.input_model.model_validate(
                    {"user_description": LINK_NARRATION, "kind": MEMORY_KIND, "name": str(item_id)}
                ),
            )


async def test_malformed_relations_kinds_and_target_names_fail_at_the_boundary(
    db: None, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError):
        ObjectLink.model_validate(
            {"relation": "derived_from", "target": {"kind": "page", "name": "abc"}}
        )
    with pytest.raises(ValidationError):
        ObjectRef(kind="Bad-Kind", name="abc")
    with pytest.raises(ValidationError):
        ObjectRef(kind="page", name="NOT_A_NAME")

    workspace_id = await _workspace()
    tools = _verbs()
    blob = FilesystemBlobStore(root=tmp_path)
    with ws(workspace_id):
        ctx = _tool_ctx(workspace_id, blob)
        get_tool = tools["object_get"]
        with pytest.raises(UnknownKind):
            await get_tool.handler(
                ctx,
                get_tool.input_model.model_validate(
                    {"user_description": LINK_NARRATION, "kind": "entity", "name": "acme"}
                ),
            )
        for kind in (MEMORY_KIND, CONVERSATION_KIND):
            with pytest.raises(UnknownObject):
                await get_tool.handler(
                    ctx,
                    get_tool.input_model.model_validate(
                        {"user_description": LINK_NARRATION, "kind": kind, "name": "not-a-uuid"}
                    ),
                )


def test_no_generic_object_or_edge_table_exists() -> None:
    table_names = set(tables.metadata.tables) | set(memory_item.metadata.tables)
    assert not {"object", "objects", "edge", "edges", "object_link", "link"} & table_names
