"""The recall hook and `memory_search` where the deploy selects the memory service: what each sends
the service, the heads it walks, and the text the model is handed."""

import asyncio
import gc
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory
from ufo_ext_memory.events import MEMORY_RECALL_EVENT
from ufo_ext_memory.heads import HEAD_WALK_MAX
from ufo_ext_memory.store import recall_subjects
from ufo_testsupport.cloud import cloud_apis_for
from ufo_testsupport.index import default_index
from ufo_testsupport.memory_service import MEMORY_WIRE, MemoryServiceStandIn
from ufo_testsupport.service_stand_in import SentRequest

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.billing.accounting import MEMORY_SERVICE
from ufo.runtime.cloud import CloudUnavailable
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.indexing import OWNER_KIND_PAGE, Chunk, chunk_digest
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import SHARED_AUDIENCE, Audience, conversation_audience
from ufo.sdk.manifest import HookContext, InjectContext, UserPromptSubmit

GOLDEN: dict[str, dict] = json.loads(MEMORY_WIRE.read_text(encoding="utf-8"))
GOLDEN_MATCH: dict = GOLDEN["memory.search"]["answer"]["body"]["matches"][0]
MEMORY_TOOLS = {tool.name: tool for tool in memory.manifest().tools}
SELECTED = frozenset({MEMORY_SERVICE})
UNAVAILABLE = {"error": {"code": "unavailable", "message": "The service is unavailable."}}
NOT_FOUND = {"error": {"code": "not_found", "message": "No memory has that id."}}
NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)
PROMPT = "what do you remember about the importer"


def _memory(
    body: str,
    *,
    memory_id: UUID | None = None,
    item_class: str = "fact",
    invalidated_by: UUID | None = None,
    superseded_by: UUID | None = None,
    retired_at: str | None = None,
    sources: list[dict] | None = None,
) -> dict:
    return GOLDEN_MATCH | {
        "id": str(memory_id or uuid4()),
        "body": body,
        "item_class": item_class,
        "invalidated_by": None if invalidated_by is None else str(invalidated_by),
        "invalid_at": None if invalidated_by is None else GOLDEN_MATCH["invalid_at"],
        "superseded_by": None if superseded_by is None else str(superseded_by),
        "retired_at": retired_at,
        "sources": [] if sources is None else sources,
    }


def _ext(
    stand_in: MemoryServiceStandIn,
    *,
    index: object = None,
    embed: object = None,
    audience: Audience = SHARED_AUDIENCE,
) -> ExtensionContext:
    return context_for(
        "memory",
        frozenset(),
        index=index,
        embed=embed,
        audience=audience,
        cloud_client=True,
        cloud=cloud_apis_for(stand_in.app, clients=SELECTED),
    )


def _turn(
    workspace_id: UUID,
    *,
    agent_id: UUID | None = None,
    admission_source: str = "member",
) -> Turn:
    return Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=agent_id or uuid4(),
        seq=1,
        status="running",
        inbound=PROMPT,
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
        admission_source=admission_source,
    )


async def _recall(
    stand_in: MemoryServiceStandIn,
    *,
    text: str = PROMPT,
    turn: Turn | None = None,
    speaker_member_id: UUID | None = None,
) -> object:
    workspace_id = uuid4()
    with ws(workspace_id):
        return await memory.recall_hook(
            HookContext(
                ext=_ext(stand_in),
                turn=turn or _turn(workspace_id),
                agent=Agent(prompt="p", model="claude-opus-4-8"),
                speaker_member_id=speaker_member_id,
                audience=SHARED_AUDIENCE,
                payload=UserPromptSubmit(text=text),
            )
        )


def _sent(stand_in: MemoryServiceStandIn, operation: str) -> list[SentRequest]:
    return [sent for sent in stand_in.sent if sent.operation == operation]


def _event(caplog: pytest.LogCaptureFixture) -> dict:
    return next(record.ufo for record in caplog.records if record.message == MEMORY_RECALL_EVENT)


def _injected(outcome: object) -> str:
    assert isinstance(outcome, InjectContext)
    return outcome.text


async def test_recall_sends_the_audience_subjects_and_the_turn_agents_reach() -> None:
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 200, {"matches": []})
    speaker = uuid4()
    workspace_id = uuid4()
    turn = _turn(workspace_id)

    outcome = await _recall(stand_in, turn=turn, speaker_member_id=speaker)

    (sent,) = _sent(stand_in, "memory.search")
    assert sent.body == {
        "queries": [PROMPT],
        "subjects": sorted(recall_subjects(SHARED_AUDIENCE, speaker)),
        "limit": memory.RECALL_LIMIT,
        "reach": {"agent_id": str(turn.agent_id), "member_id": None},
    }
    assert outcome is None


async def test_recall_drops_topic_pointers(caplog: pytest.LogCaptureFixture) -> None:
    stand_in = MemoryServiceStandIn()
    fact, episode = uuid4(), uuid4()
    stand_in.answer(
        "memory.search",
        200,
        {
            "matches": [
                _memory(
                    "Alice asked which tea the office stocks.",
                    memory_id=episode,
                    item_class="episodic",
                ),
                _memory("The office stocks green tea.", memory_id=fact),
            ]
        },
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        outcome = await _recall(stand_in)

    assert _injected(outcome) == "Relevant memory:\n- The office stocks green tea."
    assert _event(caplog)["memory_ids"] == [str(fact)]


async def test_recall_renders_now_from_the_head() -> None:
    stand_in = MemoryServiceStandIn()
    head = uuid4()
    stand_in.answer(
        "memory.search",
        200,
        {"matches": [_memory("The importer ships in September.", invalidated_by=head)]},
    )
    stand_in.answer("memory.get", 200, _memory("The importer ships in October.", memory_id=head))

    outcome = await _recall(stand_in)

    assert _injected(outcome) == (
        "Relevant memory:\n"
        "- The importer ships in September.\n"
        "  now: The importer ships in October."
    )


async def test_each_hop_sends_the_readers_subjects_and_reach() -> None:
    stand_in = MemoryServiceStandIn()
    first, second = uuid4(), uuid4()
    stand_in.answer(
        "memory.search",
        200,
        {"matches": [_memory("The importer ships in September.", invalidated_by=first)]},
    )
    stand_in.queue(
        "memory.get",
        [
            (200, _memory("The importer ships in October.", memory_id=first, superseded_by=second)),
            (200, _memory("The importer ships in November.", memory_id=second)),
        ],
    )
    speaker = uuid4()
    workspace_id = uuid4()
    turn = _turn(workspace_id)

    outcome = await _recall(stand_in, turn=turn, speaker_member_id=speaker)

    subjects = sorted(recall_subjects(SHARED_AUDIENCE, speaker))
    hops = _sent(stand_in, "memory.get")
    assert [hop.path for hop in hops] == [
        f"/v1/memory/memories/{first}",
        f"/v1/memory/memories/{second}",
    ]
    for hop in hops:
        assert hop.query == (
            *(("subject", subject) for subject in subjects),
            ("reach.agent_id", str(turn.agent_id)),
        )
    assert _injected(outcome).endswith("\n  now: The importer ships in November.")


@pytest.mark.parametrize(
    "hop",
    [
        (404, NOT_FOUND),
        (200, _memory("The importer ships in October.", retired_at="2026-10-06T00:00:00Z")),
    ],
    ids=["fenced", "retired"],
)
async def test_a_fenced_or_retired_hop_ends_the_walk_with_no_head(hop: tuple[int, dict]) -> None:
    stand_in = MemoryServiceStandIn()
    stand_in.answer(
        "memory.search",
        200,
        {"matches": [_memory("The importer ships in September.", invalidated_by=uuid4())]},
    )
    stand_in.answer("memory.get", *hop)

    outcome = await _recall(stand_in)

    assert _injected(outcome) == "Relevant memory:\n- The importer ships in September."
    assert len(_sent(stand_in, "memory.get")) == 1


async def test_heads_are_walked_at_most_eight_hops() -> None:
    stand_in = MemoryServiceStandIn()
    looping = uuid4()
    stand_in.answer(
        "memory.search",
        200,
        {"matches": [_memory("The importer ships in September.", invalidated_by=looping)]},
    )
    stand_in.answer(
        "memory.get",
        200,
        _memory("The importer ships in October.", memory_id=looping, invalidated_by=looping),
    )

    outcome = await _recall(stand_in)

    assert len(_sent(stand_in, "memory.get")) == HEAD_WALK_MAX == 8
    assert _injected(outcome) == "Relevant memory:\n- The importer ships in September."


@pytest.mark.parametrize("text", ["", "   \n"])
async def test_an_empty_member_message_sends_no_search(text: str) -> None:
    stand_in = MemoryServiceStandIn()

    outcome = await _recall(stand_in, text=text)

    assert stand_in.sent == []
    assert outcome is None


async def test_a_long_member_message_is_cut_at_a_word() -> None:
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 200, {"matches": []})
    text = "importer " * 150

    await _recall(stand_in, text=text)

    (sent,) = _sent(stand_in, "memory.search")
    assert isinstance(sent.body, dict)
    (query,) = sent.body["queries"]
    assert query == ("importer " * 111).rstrip()


async def test_the_block_matches_the_base_rendering(caplog: pytest.LogCaptureFixture) -> None:
    stand_in = MemoryServiceStandIn()
    overtaken, head, derived, plain = uuid4(), uuid4(), uuid4(), uuid4()
    stand_in.answer(
        "memory.search",
        200,
        {
            "matches": [
                _memory(
                    "The Q3 plan ships the importer in September.",
                    memory_id=overtaken,
                    invalidated_by=head,
                ),
                _memory(
                    "The renewal notice period is 30 days.",
                    memory_id=derived,
                    sources=[
                        {
                            "kind": "page",
                            "page_id": str(uuid4()),
                            "title": "Northwind contract",
                            "provider": "notion",
                        }
                    ],
                ),
                _memory("Alice prefers green tea.", memory_id=plain),
            ]
        },
    )
    stand_in.answer(
        "memory.get", 200, _memory("The Q3 plan ships the importer in October.", memory_id=head)
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        outcome = await _recall(stand_in)

    assert _injected(outcome) == (
        "Relevant memory:\n"
        "- The Q3 plan ships the importer in September.\n"
        "  now: The Q3 plan ships the importer in October.\n"
        "- The renewal notice period is 30 days.\n"
        "- Alice prefers green tea."
    )
    assert _event(caplog)["memory_ids"] == [str(overtaken), str(derived), str(plain)]


async def test_recall_hook_skips_an_internal_root_admission(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stand_in = MemoryServiceStandIn()
    workspace_id = uuid4()

    with caplog.at_level(logging.INFO, logger="ufo"):
        outcome = await _recall(
            stand_in, turn=_turn(workspace_id, admission_source=memory.INTERNAL_ADMISSION)
        )

    assert outcome is None
    assert _event(caplog)["memory_ids"] == []
    assert _event(caplog)["skipped"] == memory.RECALL_SKIP_INTERNAL
    assert stand_in.sent == []


async def test_recall_hook_serves_a_member_message_folded_onto_an_internal_root(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 200, {"matches": []})
    workspace_id = uuid4()

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _recall(
            stand_in,
            turn=_turn(workspace_id, admission_source=memory.INTERNAL_ADMISSION),
            speaker_member_id=uuid4(),
        )

    assert "skipped" not in _event(caplog)
    assert len(_sent(stand_in, "memory.search")) == 1


async def test_recall_hook_bounds_injected_bytes() -> None:
    stand_in = MemoryServiceStandIn()
    stand_in.answer(
        "memory.search",
        200,
        {
            "matches": [
                _memory(letter * memory.RECALL_ITEM_MAX_CHARS) for letter in ("w", "x", "y", "z")
            ]
        },
    )

    outcome = await _recall(stand_in)

    text = _injected(outcome)
    assert len(text) <= len(memory.RECALL_CONTEXT_PREFIX) + memory.RECALL_TOTAL_MAX_CHARS
    assert "z" * memory.RECALL_ITEM_MAX_CHARS not in text


async def test_recall_hook_omits_a_budget_dropped_item_from_the_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stand_in = MemoryServiceStandIn()
    ids = [uuid4() for _ in range(4)]
    stand_in.answer(
        "memory.search",
        200,
        {
            "matches": [
                _memory(letter * memory.RECALL_ITEM_MAX_CHARS, memory_id=memory_id)
                for letter, memory_id in zip("abcd", ids, strict=True)
            ]
        },
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        outcome = await _recall(stand_in)

    assert _event(caplog)["memory_ids"] == [str(memory_id) for memory_id in ids[:3]]
    assert "d" * memory.RECALL_ITEM_MAX_CHARS not in _injected(outcome)


async def test_recall_hook_observes_search_failure_without_denial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 503, UNAVAILABLE)

    with caplog.at_level(logging.INFO, logger="ufo"):
        outcome = await _recall(stand_in)

    assert outcome is None
    assert _event(caplog)["memory_ids"] == []
    assert _event(caplog)["error_class"] == "CloudUnavailable"


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the recall tests")


def _tool_ctx(ext: ExtensionContext, member_id: UUID | None, agent_id: UUID) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=Path("/nonexistent")),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=ext.audience,
        artifact_token_secret="",
        ext=ext,
    )


async def _search(ctx: ToolContext, **args: object) -> ToolResult:
    tool = MEMORY_TOOLS["memory_search"]
    return await tool.handler(ctx, tool.input_model.model_validate(args))


async def _workspace() -> tuple[UUID, UUID, UUID]:
    workspace_id, agent_id, connection_id, source_id = uuid4(), uuid4(), uuid4(), uuid7()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=NOW, updated_at=NOW)
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="main",
                prompt="p",
                model="m",
                is_main=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="folder",
                account_id=connection_id.hex,
                host="",
                owner_member_id=None,
                shared=True,
                created_at=NOW,
                updated_at=NOW,
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
                next_sync_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return workspace_id, agent_id, source_id


async def _page(workspace_id: UUID, source_id: UUID, body: str, created_at: datetime = NOW) -> UUID:
    page_id = uuid7()
    digest = f"sha256:{page_id.hex}"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
                digest=digest,
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Importer plan",
                subject=SHARED_SUBJECT,
                tombstone=False,
                indexed=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    with ws(workspace_id):
        await default_index().upsert(
            (
                Chunk(
                    chunk_digest(OWNER_KIND_PAGE, str(page_id), digest, 0, body),
                    OWNER_KIND_PAGE,
                    str(page_id),
                    SHARED_SUBJECT,
                    0,
                    body,
                ),
            )
        )
    return page_id


async def test_memory_search_sends_the_acting_members_reach(db: None) -> None:
    workspace_id, agent_id, _source = await _workspace()
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 200, {"matches": []})
    member = uuid4()
    ext = _ext(stand_in, index=default_index(), audience=conversation_audience(member))

    with ws(workspace_id):
        await _search(_tool_ctx(ext, member, agent_id), queries=["importer", "tea"])

    (sent,) = _sent(stand_in, "memory.search")
    assert sent.body == {
        "queries": ["importer", "tea"],
        "subjects": sorted({SHARED_SUBJECT, member_subject(member)}),
        "start": None,
        "end": None,
        "limit": memory.MEMORY_SEARCH_LIMIT,
        "reach": {"agent_id": str(agent_id), "member_id": str(member)},
    }


async def test_memory_search_carries_start_and_end_on_both_legs(db: None) -> None:
    workspace_id, agent_id, source_id = await _workspace()
    early = await _page(
        workspace_id,
        source_id,
        "the importer plan from spring",
        datetime(2026, 3, 1, tzinfo=UTC),
    )
    inside = await _page(
        workspace_id,
        source_id,
        "the importer plan from autumn",
        datetime(2026, 9, 15, tzinfo=UTC),
    )
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 200, {"matches": []})
    ext = _ext(stand_in, index=default_index())

    with ws(workspace_id):
        found = await _search(
            _tool_ctx(ext, None, agent_id),
            queries=["importer"],
            start_date="2026-09-01",
            end_date="2026-09-30",
        )

    (sent,) = _sent(stand_in, "memory.search")
    assert isinstance(sent.body, dict)
    assert (sent.body["start"], sent.body["end"]) == (
        "2026-09-01T00:00:00Z",
        "2026-10-01T00:00:00Z",
    )
    text = found.content[0].text
    assert f"page/{inside}" in text
    assert f"page/{early}" not in text


async def test_memory_search_appends_page_passages_after_memory(db: None) -> None:
    workspace_id, agent_id, source_id = await _workspace()
    page_id = await _page(workspace_id, source_id, "the importer ships with the Q3 plan")
    stand_in = MemoryServiceStandIn()
    golden_get = GOLDEN["memory.get"]["answer"]["body"]
    stand_in.answer("memory.get", 200, golden_get | {"superseded_by": None})
    ext = _ext(stand_in, index=default_index())

    with ws(workspace_id):
        found = await _search(_tool_ctx(ext, None, agent_id), queries=["importer"])

    fact, episode = GOLDEN["memory.search"]["answer"]["body"]["matches"]
    assert found.content[0].text.splitlines() == [
        f"- [fact] {fact['body']} (now: {golden_get['body']}) (memory/{fact['id']}, 2026-10-07)",
        f"- [episodic] Memory topic 2 (item {episode['id']}) (memory/{episode['id']}, 2026-10-07)",
        f"- [source] the importer ships with the Q3 plan (page/{page_id}, 2026-10-01)",
    ]


async def test_memory_search_names_the_page_the_service_names(db: None) -> None:
    workspace_id, _agent, _source = await _workspace()
    stand_in = MemoryServiceStandIn()
    stand_in.answer(
        "memory.get", 200, GOLDEN["memory.get"]["answer"]["body"] | {"superseded_by": None}
    )
    ext = _ext(stand_in, index=default_index())

    with ws(workspace_id):
        matches = await memory.MemorySearchService(ext).search(
            ("importer",), _tool_ctx(ext, None, uuid4()).source_reader()
        )

    fact, episode = GOLDEN["memory.search"]["answer"]["body"]["matches"]
    (page,) = fact["sources"]
    assert (
        matches[0].page_provider,
        matches[0].page_title,
        str(matches[0].created_from_page_id),
    ) == (
        page["provider"],
        page["title"],
        page["page_id"],
    )
    assert str(matches[1].created_from_conversation_id) == episode["conversation_id"]
    assert matches[1].created_from_page_id is None


async def test_memory_search_reports_no_match_on_empty_memory(db: None) -> None:
    workspace_id, agent_id, _source = await _workspace()
    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 200, {"matches": []})
    ext = _ext(stand_in, index=default_index())

    with ws(workspace_id):
        result = await _search(_tool_ctx(ext, None, agent_id), queries=["anything"])

    assert result.content[0].text == "No matching memory."


async def test_memory_search_raises_a_failed_leg_and_abandons_none() -> None:
    class _RejectingIndex:
        async def lexical(self, *args: object) -> tuple[object, ...]:
            raise RuntimeError("page leg rejected")

    stand_in = MemoryServiceStandIn()
    stand_in.answer("memory.search", 503, UNAVAILABLE)
    unhandled: list[str] = []
    asyncio.get_running_loop().set_exception_handler(
        lambda loop, context: unhandled.append(str(context["message"]))
    )
    ext = _ext(stand_in, index=_RejectingIndex())
    raised = ""
    try:
        with ws(uuid4()):
            await _search(_tool_ctx(ext, None, uuid4()), queries=["alpha"])
    except (RuntimeError, CloudUnavailable) as error:
        raised = str(error)
    for _ in range(50):
        if _sent(stand_in, "memory.search"):
            break
        await asyncio.sleep(0)
    for _ in range(10):
        await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)

    assert raised in {"page leg rejected", "/v1/memory/search answered 503."}
    assert len(_sent(stand_in, "memory.search")) == 1
    assert unhandled == []
