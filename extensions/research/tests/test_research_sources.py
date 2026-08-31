from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from ufo_ext_research.observations import (
    SOURCE_LIMIT,
    SOURCES_SLOT,
    RetrievedSource,
    record_sources,
    source_observation,
)
from ufo_ext_research.tools import RESEARCH_TOOLS

from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.session import SandboxSession
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.conversation_slots import ConversationSlotContext
from ufo.runtime.search import FetchedPage, FetchRequest, SearchHit, SearchQuery, SearchResults
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.turns.audience import SHARED_AUDIENCE
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn

NOW = datetime(2026, 8, 6, tzinfo=UTC)
MODEL = "claude-opus-4-8"


class _SearchProvider:
    supports_fetch = True

    async def search(self, _query: SearchQuery) -> SearchResults:
        return SearchResults(
            hits=(
                SearchHit(
                    url="https://example.com/one",
                    title="One",
                    text="First result",
                    published_date="2026-08-01",
                ),
                SearchHit(
                    url="https://example.com/two",
                    title="Two",
                    text="Second result",
                ),
            )
        )

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        return FetchedPage(url=request.url, text="Fetched page")


async def _no_spawn(
    profile: str,
    payload: dict[str, Any],
    background: bool = False,
    dedup_key: str | None = None,
) -> SpawnResult:
    del profile, payload, background, dedup_key
    raise AssertionError("research source tools do not spawn")


def _tool(name: str):
    return next(tool for tool in RESEARCH_TOOLS if tool.name == name)


async def _conversation() -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="research",
                model=MODEL,
                is_main=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=str(conversation_id),
                member_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="research this",
                terminal={"status": "done", "text": "done", "model": MODEL},
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return workspace_id, agent_id, conversation_id, turn_id


async def test_retrieved_results_become_one_conversation_sources_slot(db: None) -> None:
    workspace_id, agent_id, conversation_id, turn_id = await _conversation()
    turn = Turn(
        id=turn_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="research this",
        created_at=NOW,
    )
    with ws(workspace_id):
        ext = context_for("research", frozenset())
        tool = _tool("search_web")
        result = await tool.handler(
            ToolContext(
                sandbox=cast(SandboxSession, None),
                blob=cast(BlobStore, None),
                turn=turn,
                agent=Agent(prompt="research", model=MODEL),
                spawn=_no_spawn,
                speaker_member_id=None,
                audience=SHARED_AUDIENCE,
                artifact_token_secret="",
                ext=ext,
                search_provider=_SearchProvider(),
            ),
            tool.input_model.model_validate({"queries": ["the subject"]}),
        )
        assert result.is_error is False
        slot_context = ConversationSlotContext(
            ext=ext,
            conversation_id=conversation_id,
            agent_id=agent_id,
            audience=SHARED_AUDIENCE,
            messages=(),
            public_base_url=None,
        )
        assert await SOURCES_SLOT.summarize(slot_context) == 2
        payload = await SOURCES_SLOT.read(slot_context)

    assert payload.model_dump(mode="json") == {
        "type": "sources",
        "sources": [
            {
                "url": "https://example.com/one",
                "title": "One",
                "snippet": "First result",
                "published_date": "2026-08-01",
            },
            {
                "url": "https://example.com/two",
                "title": "Two",
                "snippet": "Second result",
                "published_date": None,
            },
        ],
        "truncated": False,
    }


async def test_sources_slot_is_absent_without_retrieved_results(db: None) -> None:
    workspace_id, agent_id, conversation_id, _turn_id = await _conversation()
    with ws(workspace_id):
        slot_context = ConversationSlotContext(
            ext=context_for("research", frozenset()),
            conversation_id=conversation_id,
            agent_id=agent_id,
            audience=SHARED_AUDIENCE,
            messages=(),
            public_base_url=None,
        )
        assert await SOURCES_SLOT.summarize(slot_context) is None


async def test_source_observations_are_bounded_and_use_a_digest_key(db: None) -> None:
    workspace_id, _agent_id, conversation_id, turn_id = await _conversation()
    with ws(workspace_id):
        ext = context_for("research", frozenset())
        await record_sources(
            ext,
            conversation_id,
            turn_id,
            tuple(
                RetrievedSource(
                    url=f"https://example.com/{index}",
                    title=str(index),
                    snippet="result",
                    published_date=None,
                )
                for index in range(SOURCE_LIMIT + 5)
            ),
        )
        async with ext.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(source_observation).where(
                            source_observation.c.workspace_id == workspace_id,
                            source_observation.c.conversation_id == conversation_id,
                        )
                    )
                )
                .mappings()
                .all()
            )

    assert len(rows) == SOURCE_LIMIT + 1
    assert all(len(row["url_digest"]) == 64 for row in rows)
