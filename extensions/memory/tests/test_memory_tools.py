"""The memory extension's tools and recall hook, driven through the real scoped context.

`memory_update`/`memory_search` are the extension's tools and `recall_hook` its `user_prompt_submit`
hook; each is driven here over an `ExtensionContext` carrying the deploy index/embed backends,
exactly as core threads them onto a turn. The page store this extension indexes is read by the
research extension's `page` search vertical, driven here over the same seeded pages. The headline:
a fact committed in one context is recalled in a fresh one — both by the search tool and,
unprompted, by the user_prompt_submit hook."""

import asyncio
import gc
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory
import ufo_ext_research.tools as research_tools
from pydantic import ValidationError
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.events import MEMORY_RECALL_EVENT
from ufo_ext_memory.objects import MEMORY_OBJECT, MemoryObjects
from ufo_ext_memory.store import (
    MEMORY_BODY_MAX_CHARS,
    SEMANTIC,
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    Recalled,
    SourceMatch,
    body_digest,
    mem_page,
    memory_item,
    store_for,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.runtime.ext.context import ExtensionContext, SourceReader, context_for
from ufo.runtime.indexing import OWNER_KIND_PAGE, Chunk, TextChunker
from ufo.runtime.objects import (
    ObjectListQuery,
)
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import Agent, Turn, TurnRuntimeConfig
from ufo.sdk.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.sdk.manifest import HookContext, InjectContext, UserPromptSubmit

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOOL_NARRATION = "remembering what they told me"
MEMORY_TOOLS = {tool.name: tool for tool in memory.manifest().tools}
INTERNAL_VERTICAL_TOOL = next(
    tool
    for tool in research_tools.RESEARCH_TOOLS
    if tool.name == research_tools.SEARCH_VERTICAL_TOOL
)
MEMORY_ACTION_IDS = frozenset(
    {
        f"action:memory:{memory.RECORD_CORRECTION_ACTION}",
        f"action:memory:{memory.RECORD_FIRST_RUN_ACTION}",
    }
)


def test_memory_update_is_side_effecting() -> None:
    assert MEMORY_TOOLS["memory_update"].side_effecting


def test_memory_inputs_refuse_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        memory.MemorySearchInput.model_validate({"queries": ("launch date",), "kind": "memory"})
    with pytest.raises(ValidationError):
        memory.MemoryUpdateInput.model_validate(
            {"body": "Acme Corp — Moved the launch to March.", "kind": "memory"}
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


class BrokenEmbed:
    """A query-embed that always fails, so recall degrades to its lexical leg — the multi-query
    tests need each query to surface only its own lexical matches, not every seeded item at once."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("embed provider unreachable")


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
    return context_for(name, frozenset(), index=index, embed=embed, audience=audience)


def _indexer(embed: object) -> MemoryIndexer:
    return MemoryIndexer(
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        page_states=_ext(DefaultIndex(transaction=workspace_tx), embed).page_states,
    )


def _store(embed: object) -> MemoryStore:
    return store_for(_ext(DefaultIndex(transaction=workspace_tx), embed))


async def _live_bodies(subject: str) -> tuple[str, ...]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(memory_item.c.body)
                .where(memory_item.c.subject == subject, memory_item.c.superseded_by.is_(None))
                .order_by(memory_item.c.body)
            )
        ).scalars()
    return tuple(rows)


async def _rows_by_body() -> dict[str, sa.RowMapping]:
    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(
                        memory_item.c.id,
                        memory_item.c.body,
                        memory_item.c.superseded_by,
                        memory_item.c.created_at,
                    )
                )
            )
            .mappings()
            .all()
        )
    return {row["body"]: row for row in rows}


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


async def _run(name: str, ctx: ToolContext, **args: object) -> ToolResult:
    tool = MEMORY_TOOLS[name]
    return await tool.handler(ctx, tool.input_model.model_validate({**args}))


async def test_memory_search_provider_rejects_an_empty_query_set() -> None:
    (spec,) = memory.manifest().memory_search
    embed = StubEmbed(vec((0, 1.0)))
    provider = spec.build(_ext(DefaultIndex(transaction=workspace_tx), embed))
    with pytest.raises(ValueError, match="requires 1-3 queries"):
        await provider.search((), frozenset({"shared"}))


async def test_recall_hook_observes_search_failure_without_denial(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_name = "MemoryUnavailable" * 16
    memory_unavailable = type(error_name, (RuntimeError,), {})

    class BrokenStore:
        async def recall(
            self,
            query: str,
            subjects: frozenset[str],
            limit: int,
            *,
            source_reader: object,
        ) -> tuple[object, ...]:
            raise memory_unavailable("memory unavailable")

    monkeypatch.setattr(memory, "store_for", lambda ext: BrokenStore())
    workspace_id = uuid4()
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="what is the vault code",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
        admission_source="member",
    )
    with caplog.at_level(logging.INFO, logger="ufo"), ws(workspace_id):
        outcome = await memory.recall_hook(
            HookContext(
                ext=_ext(object(), object()),
                turn=turn,
                agent=Agent(prompt="p", model="claude-opus-4-8"),
                speaker_member_id=None,
                audience=conversation_audience(None),
                payload=UserPromptSubmit(text="what is the vault code"),
            )
        )
    record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
    assert outcome is None
    assert record.ufo["memory_ids"] == []
    assert record.ufo["error_class"] == error_name[: memory.MAX_RECALL_ERROR_CLASS_CHARS]


async def test_a_body_commits_at_the_budget_and_is_refused_past_it(db: None) -> None:
    """One bound over every class, at the commit seam: a body at the budget lands whole whatever
    class it carries, and one character past it is refused with the budget in the message."""
    workspace_id = await _workspace()
    with ws(workspace_id):
        store = _store(StubEmbed(vec((0, 1.0))))
        await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body="r" * MEMORY_BODY_MAX_CHARS))
        await store.commit(
            MemoryWrite(
                subject=SHARED_SUBJECT,
                body="o" * MEMORY_BODY_MAX_CHARS,
                item_class=SEMANTIC,
            )
        )
        assert sorted(len(body) for body in await _live_bodies(SHARED_SUBJECT)) == [
            MEMORY_BODY_MAX_CHARS,
            MEMORY_BODY_MAX_CHARS,
        ]

    with pytest.raises(ValidationError) as long_row:
        MemoryWrite(subject=SHARED_SUBJECT, body="r" * (MEMORY_BODY_MAX_CHARS + 1))
    assert f"a fact body runs to {MEMORY_BODY_MAX_CHARS} characters" in str(long_row.value)

    with pytest.raises(ValidationError) as long_overview:
        MemoryWrite(
            subject=SHARED_SUBJECT,
            body="o" * (MEMORY_BODY_MAX_CHARS + 1),
            item_class=SEMANTIC,
        )
    assert f"a semantic body runs to {MEMORY_BODY_MAX_CHARS} characters" in str(long_overview.value)


def test_memory_update_input_rejects_an_oversized_body() -> None:
    with pytest.raises(ValidationError):
        memory.MemoryUpdateInput(body="x" * (memory.MEMORY_BODY_MAX_CHARS + 1))


async def _retire(body: str, superseded_by: UUID) -> None:
    """Put one row where the dedup sweep leaves it — retired at the copy that replaced it — so a
    test drives the revival that follows through the real `commit` rather than through a sweep it
    is not about."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item)
            .values(superseded_by=superseded_by)
            .where(memory_item.c.body == body)
        )


async def test_re_committing_a_retired_body_revives_it(db: None) -> None:
    """Restating a body the sweep retired resolves to that same row by content address, so without
    the upsert clearing `superseded_by` the restatement would land back under the fence and stay
    invisible to every reader. The re-asserted body is live again; retiring the copy that replaced
    it is the next sweep's business, not this write's."""
    workspace_id = await _workspace()
    original = "the deploy has no code_review profile"
    reworded = "re-confirmed: the deploy still has no code_review profile"
    embed = StubEmbed(vec((0, 1.0)))
    with ws(workspace_id):
        store = _store(embed)
        await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=original))
        await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=reworded))
        await _retire(original, (await _rows_by_body())[reworded]["id"])

        await store.commit(MemoryWrite(subject=SHARED_SUBJECT, body=original))
        bodies = await _live_bodies(SHARED_SUBJECT)
        rows = await _rows_by_body()

    assert bodies == (reworded, original)
    assert rows[original]["superseded_by"] is None


async def _backdate(body: str, created_at: datetime) -> datetime:
    """Age one row's `created_at` and read back what the backend stored, so a test compares two
    values that came out of the same column rather than a Python constant against sqlite's
    second-resolution `now()`."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(memory_item).values(created_at=created_at).where(memory_item.c.body == body)
        )
    return (await _rows_by_body())[body]["created_at"]


def _stub_turn(
    workspace_id: UUID,
    *,
    admission_source: str = "member",
    parent_turn_id: UUID | None = None,
) -> Turn:
    return Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="what do you remember",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
        admission_source=admission_source,
        parent_turn_id=parent_turn_id,
    )


def _hook(workspace_id: UUID, turn: Turn, speaker_member_id: UUID | None = None) -> HookContext:
    return HookContext(
        ext=_ext(object(), object()),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        speaker_member_id=speaker_member_id,
        audience=conversation_audience(None),
        payload=UserPromptSubmit(text="what do you remember"),
    )


async def test_recall_hook_skips_an_internal_root_admission(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    class StubStore:
        async def recall(
            self, query: str, subjects: frozenset[str], limit: int, *, source_reader: object
        ) -> tuple[Recalled, ...]:
            nonlocal calls
            calls += 1
            return ()

    monkeypatch.setattr(memory, "store_for", lambda ext: StubStore())
    workspace_id = uuid4()
    turn = _stub_turn(workspace_id, admission_source="internal", parent_turn_id=None)
    with caplog.at_level(logging.INFO, logger="ufo"), ws(workspace_id):
        outcome = await memory.recall_hook(_hook(workspace_id, turn))

    assert outcome is None
    record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
    assert record.ufo["memory_ids"] == []
    assert record.ufo["skipped"] == memory.RECALL_SKIP_INTERNAL
    assert calls == 0


async def test_recall_hook_serves_a_member_message_folded_onto_an_internal_root(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission folds a member's message onto whatever turn is live, without testing what founded
    it — so a member writing while a monitor fire or a subagent delivery runs fires this hook on an
    internal root turn, carrying their own text and their own speaker. That is a member's prompt and
    must be recalled against; skipping on the turn alone silently denied memory to every message
    that landed mid-notice."""
    calls = 0

    class StubStore:
        async def recall(
            self, query: str, subjects: frozenset[str], limit: int, *, source_reader: object
        ) -> tuple[Recalled, ...]:
            nonlocal calls
            calls += 1
            return ()

    monkeypatch.setattr(memory, "store_for", lambda ext: StubStore())
    workspace_id = uuid4()
    turn = _stub_turn(workspace_id, admission_source="internal", parent_turn_id=None)
    with caplog.at_level(logging.INFO, logger="ufo"), ws(workspace_id):
        await memory.recall_hook(_hook(workspace_id, turn, speaker_member_id=uuid4()))

    record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
    assert "skipped" not in record.ufo
    assert calls == 1


async def test_recall_hook_carries_the_turn_connection_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: tuple[UUID, ...] | None = None

    class StubStore:
        async def recall(
            self,
            query: str,
            subjects: frozenset[str],
            limit: int,
            *,
            source_reader: SourceReader,
        ) -> tuple[Recalled, ...]:
            nonlocal seen
            seen = source_reader.connections
            return ()

    monkeypatch.setattr(memory, "store_for", lambda ext: StubStore())
    workspace_id = uuid4()
    connection_scope = (uuid4(),)
    turn = _stub_turn(workspace_id).model_copy(
        update={"runtime_config": TurnRuntimeConfig(connections=connection_scope)}
    )
    with ws(workspace_id):
        await memory.recall_hook(_hook(workspace_id, turn, speaker_member_id=uuid4()))
    assert seen == connection_scope


async def test_recall_hook_bounds_injected_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Four bodies at exactly the per-item cap sum to precisely RECALL_TOTAL_MAX_CHARS on their raw
    text (8,000) but their rendered lines — each with its "- " prefix and "\\n" separator — sum to
    8,011: 11 bytes over. The budget must be charged against the rendered line, not the bare body,
    so the last item has to drop whole and the joined text must never cross the line."""
    items = tuple(
        Recalled(
            uuid4(), "shared", "fact", letter * memory.RECALL_ITEM_MAX_CHARS, None, 0.9 - n / 10
        )
        for n, letter in enumerate(("w", "x", "y", "z"))
    )

    class StubStore:
        async def recall(
            self, query: str, subjects: frozenset[str], limit: int, *, source_reader: object
        ) -> tuple[Recalled, ...]:
            return items

    monkeypatch.setattr(memory, "store_for", lambda ext: StubStore())
    workspace_id = uuid4()
    with ws(workspace_id):
        outcome = await memory.recall_hook(_hook(workspace_id, _stub_turn(workspace_id)))

    assert isinstance(outcome, InjectContext)
    assert len(outcome.text) <= len(memory.RECALL_CONTEXT_PREFIX) + memory.RECALL_TOTAL_MAX_CHARS
    assert "z" * memory.RECALL_ITEM_MAX_CHARS not in outcome.text


async def test_recall_hook_omits_a_budget_dropped_item_from_the_event(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recall event's memory_ids must never claim an item the char budget dropped. Each body
    is already over the per-item cap and truncated to it, so four of them exceed the total budget
    and the last must drop whole — from both the injected text and the logged ids."""
    items = tuple(
        Recalled(uuid4(), "shared", "fact", letter * 2_500, None, 1.0 - index / 10)
        for index, letter in enumerate("abcd")
    )

    class StubStore:
        async def recall(
            self, query: str, subjects: frozenset[str], limit: int, *, source_reader: object
        ) -> tuple[Recalled, ...]:
            return items

    monkeypatch.setattr(memory, "store_for", lambda ext: StubStore())
    workspace_id = uuid4()
    turn = _stub_turn(workspace_id)
    with caplog.at_level(logging.INFO, logger="ufo"), ws(workspace_id):
        outcome = await memory.recall_hook(_hook(workspace_id, turn))

    record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
    assert record.ufo["memory_ids"] == [str(item.memory_id) for item in items[:3]]
    assert isinstance(outcome, InjectContext)
    assert "d" * 2_500 not in outcome.text
    assert memory.RECALL_TRUNCATION_MARK in outcome.text


async def test_memory_update_writes_only_the_conversation_audience(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    shared_ext = _ext(index, embed)
    bound_ctx = _tool_ctx(shared_ext, member, tmp_path, audience=SHARED_AUDIENCE)
    common_ctx = _tool_ctx(shared_ext, None, tmp_path, audience=SHARED_AUDIENCE)
    with ws(workspace_id):
        await _run("memory_update", bound_ctx, body="a member note")
        await _run("memory_update", common_ctx, body="a team note")
        async with workspace_tx() as connection:
            subjects = sorted(
                row.subject
                for row in (await connection.execute(sa.select(memory_item.c.subject))).all()
            )
        await _indexer(embed).run()
        member_read = await _run("memory_search", bound_ctx, queries=["note"])
        shared_read = await _run("memory_search", common_ctx, queries=["note"])
    assert subjects == ["shared", "shared"]
    assert "a member note" in member_read.content[0].text
    assert "a team note" in member_read.content[0].text
    assert "a member note" in shared_read.content[0].text
    assert "a team note" in shared_read.content[0].text
    with pytest.raises(ValidationError):
        memory.MemoryUpdateInput.model_validate({"body": "widened", "shared": True})


async def test_shared_recall_includes_the_speakers_private_memory(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    ext = _ext(index, embed)
    private_audience = conversation_audience(member)
    with ws(workspace_id):
        await _run(
            "memory_update",
            _tool_ctx(
                _ext(index, embed, private_audience),
                member,
                tmp_path,
                audience=private_audience,
            ),
            body="member private launch note",
        )
        await _run(
            "memory_update",
            _tool_ctx(ext, None, tmp_path, audience=SHARED_AUDIENCE),
            body="common launch note",
        )
        await _indexer(embed).run()
        outcome = await memory.recall_hook(
            HookContext(
                ext=ext,
                turn=Turn(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    conversation_id=uuid4(),
                    agent_id=uuid4(),
                    seq=1,
                    status="running",
                    inbound="launch note",
                    created_at=datetime(2026, 7, 9, tzinfo=UTC),
                    admission_source="member",
                ),
                agent=Agent(prompt="p", model="claude-opus-4-8"),
                speaker_member_id=member,
                audience=SHARED_AUDIENCE,
                payload=UserPromptSubmit(text="launch note"),
            )
        )

    assert isinstance(outcome, InjectContext)
    assert "common launch note" in outcome.text
    assert "member private launch note" in outcome.text


async def test_room_memory_reads_shared_while_foreign_memory_is_sealed(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    shared = _tool_ctx(_ext(index, embed), None, tmp_path)
    room = room_audience("slack", "CPRIVATE")
    other_room = room_audience("slack", "COTHER")
    foreign = foreign_room_audience("slack", "CCONNECT")
    room_ctx = _tool_ctx(_ext(index, embed, room), None, tmp_path, audience=room)
    other_ctx = _tool_ctx(_ext(index, embed, other_room), None, tmp_path, audience=other_room)
    foreign_ctx = _tool_ctx(_ext(index, embed, foreign), None, tmp_path, audience=foreign)

    with ws(workspace_id):
        await _run("memory_update", shared, body="shared launch note")
        await _run("memory_update", room_ctx, body="private room launch note")
        await _run("memory_update", foreign_ctx, body="foreign launch note")
        await _indexer(embed).run()
        room_read = await _run("memory_search", room_ctx, queries=["launch note"])
        other_read = await _run("memory_search", other_ctx, queries=["launch note"])
        foreign_read = await _run("memory_search", foreign_ctx, queries=["launch note"])

    assert "shared launch note" in room_read.content[0].text
    assert "private room launch note" in room_read.content[0].text
    assert "foreign launch note" not in room_read.content[0].text
    assert "shared launch note" in other_read.content[0].text
    assert "private room launch note" not in other_read.content[0].text
    assert "foreign launch note" not in other_read.content[0].text
    assert "foreign launch note" in foreign_read.content[0].text
    assert "shared launch note" not in foreign_read.content[0].text


async def test_a_speaking_member_does_not_unseal_a_foreign_conversation(
    db: None, tmp_path: Path
) -> None:
    """A member speaking in a Slack Connect channel reads their own memory and the channel's, never
    the workspace's — the seal is a property of the channel, not of whether anyone is speaking."""
    workspace_id = await _workspace()
    alice = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    foreign = foreign_room_audience("slack", "CCONNECT")
    shared_ctx = _tool_ctx(_ext(index, embed), None, tmp_path)
    alice_dm = conversation_audience(alice)
    alice_ctx = _tool_ctx(_ext(index, embed, alice_dm), alice, tmp_path, audience=alice_dm)
    foreign_ext = _ext(index, embed, foreign)
    alice_in_foreign = _tool_ctx(foreign_ext, alice, tmp_path, audience=foreign)

    with ws(workspace_id):
        await _run("memory_update", shared_ctx, body="internal shared launch note")
        await _run("memory_update", alice_ctx, body="alice private launch note")
        await _indexer(embed).run()
        read = await _run("memory_search", alice_in_foreign, queries=["launch note"])

    assert "internal shared launch note" not in read.content[0].text
    assert "alice private launch note" in read.content[0].text


async def test_a_members_write_in_a_foreign_conversation_stays_sealed_to_it(
    db: None, tmp_path: Path
) -> None:
    """What a member says in a Slack Connect channel belongs to that channel. Stamping it with the
    requester instead would carry another organization's content into every internal conversation
    that member speaks in."""
    workspace_id = await _workspace()
    alice = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    foreign = foreign_room_audience("slack", "CCONNECT")
    alice_in_foreign = _tool_ctx(_ext(index, embed, foreign), alice, tmp_path, audience=foreign)
    alice_dm = conversation_audience(alice)
    alice_elsewhere = _tool_ctx(_ext(index, embed, alice_dm), alice, tmp_path, audience=alice_dm)

    with ws(workspace_id):
        await _run("memory_update", alice_in_foreign, body="acme renewal terms")
        await _indexer(embed).run()
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(memory_item.c.subject).where(memory_item.c.body == "acme renewal terms")
            )
            stored = list(rows.scalars().all())
        internal_read = await _run("memory_search", alice_elsewhere, queries=["acme renewal"])
        foreign_read = await _run("memory_search", alice_in_foreign, queries=["acme renewal"])

    assert stored == [str(foreign)]
    assert "acme renewal terms" not in internal_read.content[0].text
    assert "acme renewal terms" in foreign_read.content[0].text


async def test_a_room_write_is_the_rooms_and_a_members_own_note_stays_theirs(
    db: None, tmp_path: Path
) -> None:
    """What a member says in a private room belongs to the room — the team recalls it, and it never
    follows the member into another room. A note the member wants to themselves they make in their
    own conversation; that stays theirs, surfaced to them anywhere but never to another room
    member."""
    workspace_id = await _workspace()
    alice, bob = uuid4(), uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    room = room_audience("slack", "CPRIVATE")
    alice_dm = conversation_audience(alice)
    alice_in_room = _tool_ctx(_ext(index, embed, room), alice, tmp_path, audience=room)
    alice_own = _tool_ctx(_ext(index, embed, alice_dm), alice, tmp_path, audience=alice_dm)
    bob_in_room = _tool_ctx(_ext(index, embed, room), bob, tmp_path, audience=room)

    with ws(workspace_id):
        await _run("memory_update", alice_in_room, body="the room launch note")
        await _run("memory_update", alice_own, body="alice's own launch note")
        await _indexer(embed).run()
        alice_read = await _run("memory_search", alice_in_room, queries=["launch note"])
        bob_read = await _run("memory_search", bob_in_room, queries=["launch note"])

    assert "the room launch note" in alice_read.content[0].text
    assert "alice's own launch note" in alice_read.content[0].text
    assert "the room launch note" in bob_read.content[0].text
    assert "alice's own launch note" not in bob_read.content[0].text


async def test_a_members_room_write_does_not_follow_them_into_another_room(
    db: None, tmp_path: Path
) -> None:
    """The leak room-scoping closes: a fact a member states in one private room must not surface
    when that same member speaks in a different room. Stamping the write to the member instead of
    the room would carry #deal-acme's price into #deal-globex the moment she asks there."""
    workspace_id = await _workspace()
    alice = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    acme = room_audience("slack", "CACME")
    globex = room_audience("slack", "CGLOBEX")
    alice_in_acme = _tool_ctx(_ext(index, embed, acme), alice, tmp_path, audience=acme)
    alice_in_globex = _tool_ctx(_ext(index, embed, globex), alice, tmp_path, audience=globex)

    with ws(workspace_id):
        await _run("memory_update", alice_in_acme, body="the acme floor price is 40k")
        await _indexer(embed).run()
        globex_read = await _run("memory_search", alice_in_globex, queries=["acme floor price"])

    assert "the acme floor price is 40k" not in globex_read.content[0].text


async def test_memory_search_reports_no_match_on_empty_memory(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    embed = StubEmbed(vec((0, 1.0)))
    member = uuid4()
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed, conversation_audience(member))
    with ws(workspace_id):
        result = await _run("memory_search", _tool_ctx(ext, member, tmp_path), queries=["anything"])
        assert result.content[0].text == "No matching memory."


def test_date_bound_reads_a_bare_end_date_as_the_whole_day() -> None:
    assert memory._date_bound(None, end=False) is None
    assert memory._date_bound("2026-01-31", end=False) == datetime(2026, 1, 31, tzinfo=UTC)
    assert memory._date_bound("2026-01-31", end=True) == datetime(2026, 2, 1, tzinfo=UTC)
    assert memory._date_bound("2026-01-31T12:00:00", end=True) == datetime(
        2026, 1, 31, 12, tzinfo=UTC
    )


async def test_memory_search_merges_and_dedups_across_queries(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    working = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    ext = _ext(index, BrokenEmbed(), conversation_audience(member))
    ctx = _tool_ctx(ext, member, tmp_path)
    with ws(workspace_id):
        for body in ("apple orchard notes", "banana bread recipe", "apple and banana smoothie"):
            await _run("memory_update", ctx, body=body)
        await _indexer(working).run()

        found = await _run("memory_search", ctx, queries=["apple", "banana"])
        text = found.content[0].text
        assert "apple orchard notes" in text
        assert "banana bread recipe" in text
        assert text.count("apple and banana smoothie") == 1


def test_memory_search_merge_serves_a_statement_in_the_slot_it_first_reached() -> None:
    """Recall dedups within one query, so two twins of one body reach the merge only when two
    queries' candidate pools differ and each recalls a different one. The statement keeps the slot
    the first twin reached — query one's top hit stays first when its newer twin ranked eighth for
    query two — and every distinct statement follows in round-robin order."""
    when = datetime(2026, 1, 1, tzinfo=UTC)
    older = Recalled(
        uuid4(), "shared", "fact", "the launch date is June 12", None, 0.9, created_at=when
    )
    newer = Recalled(
        uuid4(),
        "shared",
        "fact",
        "the launch date is June 12",
        None,
        0.2,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    fillers = tuple(
        Recalled(uuid4(), "shared", "fact", f"filler {n}", None, 0.9 - n / 10, created_at=when)
        for n in range(7)
    )
    service = memory.MemorySearchService(
        ctx=_ext(DefaultIndex(transaction=workspace_tx), StubEmbed(vec((0, 1.0))))
    )

    merged = service._merged([(older,), (*fillers, newer)])
    assert merged[0] == older
    assert newer not in merged
    assert merged == (older, *fillers)[: memory.MEMORY_SEARCH_LIMIT]


def test_memory_search_merge_serves_an_episodic_item_recalled_at_two_ranks_once() -> None:
    """Recall rewrites an episodic hit to a topic pointer carrying its rank, so one item recalled
    first by one query and second by another is two bodies and one ref. The merge dedups by memory
    id before it dedups by statement, and the agent gets the ref once."""
    when = datetime(2026, 1, 1, tzinfo=UTC)
    episode = uuid4()
    at_first = Recalled(
        episode,
        "shared",
        "episodic",
        f"Memory topic 1 (item {episode})",
        None,
        0.9,
        created_at=when,
        recall_mode="topic",
    )
    at_second = Recalled(
        episode,
        "shared",
        "episodic",
        f"Memory topic 2 (item {episode})",
        None,
        0.6,
        created_at=when,
        recall_mode="topic",
    )
    other = Recalled(uuid4(), "shared", "fact", "the auditor is booked", None, 0.8, created_at=when)
    service = memory.MemorySearchService(
        ctx=_ext(DefaultIndex(transaction=workspace_tx), StubEmbed(vec((0, 1.0))))
    )

    assert service._merged([(at_first,), (other, at_second)]) == (at_first, other)


async def test_memory_search_bounds_the_merged_result_across_queries(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    working = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    ext = _ext(index, BrokenEmbed(), conversation_audience(member))
    ctx = _tool_ctx(ext, member, tmp_path)
    with ws(workspace_id):
        for index_n in range(6):
            await _run("memory_update", ctx, body=f"alpha memo {index_n}")
            await _run("memory_update", ctx, body=f"beta memo {index_n}")
        await _indexer(working).run()

        found = await _run("memory_search", ctx, queries=["alpha", "beta"])
        assert len(found.content[0].text.splitlines()) == memory.MEMORY_SEARCH_LIMIT


async def test_memory_search_keeps_each_legs_passage_of_one_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`search_sources` returns one passage per page — the one that answered that query — so a leg
    per query is the only way a manual yields more than one paragraph. Keying the fusion by page
    discarded all but the first, leaving the agent searching over and over for a section it could
    never be handed. Identical passages still collapse: the queries overlap."""
    page = uuid4()

    def _match(text: str) -> SourceMatch:
        return SourceMatch(
            page_id=page, subject="shared", text=text, score=0.5, created_at=datetime.now(UTC)
        )

    legs = {
        "putaway": (_match("13.4 Putaway authorisation"),),
        "template": (_match("Template 24 — the post format"),),
        "formats": (_match("Template 24 — the post format"),),
    }

    class _Store:
        async def recall(self, query, subjects, limit, start, end, *, source_reader):
            return ()

        async def search_sources(self, query, subjects, limit, start, end, *, source_reader):
            return legs[query]

    monkeypatch.setattr(memory, "store_for", lambda ext: _Store())
    ctx = _tool_ctx(_ext(object(), object()), None, tmp_path)
    with ws(uuid4()):
        found = await _run("memory_search", ctx, queries=["putaway", "template", "formats"])
    text = found.content[0].text
    assert "13.4 Putaway authorisation" in text
    assert text.count("Template 24 — the post format") == 1, "an identical passage must collapse"


async def test_memory_search_raises_a_failed_leg_and_abandons_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leg the index rejects reaches the caller as its error, and no other leg is left to fail
    into asyncio's finalizer. Awaiting the source gather only after the recall gather had already
    raised abandoned it: its index error — the same malformed request — surfaced as an
    `exception was never retrieved` log from nobody's handler, and search lost a leg with no
    signal."""

    class _Store:
        async def recall(self, query, subjects, limit, start, end, *, source_reader):
            raise RuntimeError("recall leg rejected")

        async def search_sources(self, query, subjects, limit, start, end, *, source_reader):
            await asyncio.sleep(0)
            raise RuntimeError("source leg rejected")

    monkeypatch.setattr(memory, "store_for", lambda ext: _Store())
    unhandled: list[str] = []
    asyncio.get_running_loop().set_exception_handler(
        lambda loop, context: unhandled.append(str(context["message"]))
    )
    ctx = _tool_ctx(_ext(object(), object()), None, tmp_path)
    raised = ""
    try:
        with ws(uuid4()):
            await _run("memory_search", ctx, queries=["alpha"])
    except RuntimeError as error:
        raised = str(error)
    assert raised == "recall leg rejected"
    for _ in range(3):
        await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    assert unhandled == []


async def test_the_memory_object_kind_is_sealed_against_a_speaking_member(
    db: None, tmp_path: Path
) -> None:
    """`object_list kind=memory` and `object_get` with a memory ref read `ctx.read_subjects` too,
    so the Slack Connect seal has to hold on the object surface exactly as it does on search."""
    workspace_id = await _workspace()
    alice = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    foreign = foreign_room_audience("slack", "CCONNECT")
    shared_ctx = _tool_ctx(_ext(index, embed), None, tmp_path, workspace_id=workspace_id)
    alice_dm = conversation_audience(alice)
    alice_ctx = _tool_ctx(
        _ext(index, embed, alice_dm), alice, tmp_path, workspace_id=workspace_id, audience=alice_dm
    )
    speaking = _tool_ctx(
        _ext(index, embed, foreign), alice, tmp_path, workspace_id=workspace_id, audience=foreign
    )

    with ws(workspace_id):
        await _run("memory_update", shared_ctx, body="internal shared roadmap")
        await _run("memory_update", alice_ctx, body="alice private roadmap")
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(memory_item.c.id, memory_item.c.body, memory_item.c.subject)
            )
            stored = {row.body: (row.id, row.subject) for row in rows.all()}

        listed = await MemoryObjects().list(
            speaking, ObjectListQuery(supported_fields=MEMORY_OBJECT.list_fields)
        )
        fetched_shared = await MemoryObjects().get(
            speaking, str(stored["internal shared roadmap"][0])
        )
        fetched_own = await MemoryObjects().get(speaking, str(stored["alice private roadmap"][0]))

    listed_ids = {row.name for row in listed.rows}
    assert str(stored["internal shared roadmap"][0]) not in listed_ids
    assert str(stored["alice private roadmap"][0]) in listed_ids
    assert fetched_shared is None
    assert fetched_own is not None


async def test_a_page_derived_memory_object_is_fenced_on_the_connector_grant(
    db: None, tmp_path: Path
) -> None:
    """`object_list`/`object_get` with a memory ref recheck a page-derived row's reach: an agent
    holding no grant on the connection behind the source sees nothing, while the granted agent
    reads it in full — the object surface holds the same source fence recall does, so a fact never
    leaks via a listing."""
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
    ext = _ext(DefaultIndex(transaction=workspace_tx), StubEmbed(vec((0, 1.0))))
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


async def _seed_page_chunk(
    workspace_id: UUID,
    subject: str,
    body: str,
    vector: tuple[float, ...],
    owner_member_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    """One synced page under its own source and connection, its mirror row, and the chunk the page
    index job would have derived — the page store a search reads, seeded without the sync driver.
    A connection nobody owns is the workspace's shared feed; one an owner names is that member's."""
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
        await DefaultIndex(transaction=workspace_tx).upsert(
            (Chunk("p-" + page_id.hex, OWNER_KIND_PAGE, str(page_id), subject, 0, body, vector),)
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
        DefaultIndex(transaction=workspace_tx),
        StubEmbed(probe),
        conversation_audience(member),
        name="research",
    )
    return _tool_ctx(ext, member, tmp_path, workspace_id=workspace_id)


async def _search_internal(ctx: ToolContext, query: str) -> ToolResult:
    tool = INTERNAL_VERTICAL_TOOL
    args = {"vertical": research_tools.INTERNAL_VERTICAL, "query": query}
    return await tool.handler(ctx, tool.input_model.model_validate(args))


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


async def test_the_internal_vertical_serves_the_page_store_alone(db: None, tmp_path: Path) -> None:
    """The vertical's whole point: the same index legs memory_search reads its page hits from, with
    no recall leg — a fact stating the term is memory_search's row and never this vertical's."""
    workspace_id = await _workspace()
    member = uuid4()
    probe = vec((0, 1.0))
    ext = _ext(
        DefaultIndex(transaction=workspace_tx), StubEmbed(probe), conversation_audience(member)
    )
    ctx = _tool_ctx(ext, member, tmp_path, workspace_id=workspace_id)
    await _reading_agent(workspace_id, ctx.turn.agent_id)
    connection_id, page_id = await _seed_page_chunk(
        workspace_id, SHARED_SUBJECT, "the expense policy pays mileage at 45p", probe
    )
    pages_ctx = _research_ctx(workspace_id, member, probe, tmp_path)
    await _reading_agent(workspace_id, pages_ctx.turn.agent_id, connection_id)
    with ws(workspace_id):
        await _run("memory_update", ctx, body="the expense policy is owned by finance")
        await _indexer(StubEmbed(probe)).run()
        pages = await _search_internal(pages_ctx, "expense policy")
        both = await _run("memory_search", ctx, queries=["expense policy"])
    page_text = pages.content[0].text
    assert f"page/{page_id}" in page_text
    assert "the expense policy pays mileage at 45p" in page_text
    assert "owned by finance" not in page_text
    assert "owned by finance" in both.content[0].text


async def test_the_internal_vertical_seals_another_members_private_page(
    db: None, tmp_path: Path
) -> None:
    """A page synced under one member's own subject is theirs: their turn reads it, and another
    member's turn is answered nothing even though that member's agent holds the grant on the
    connection behind it — the subject fence, not the grant, is what seals the page."""
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


async def test_search_internal_keeps_each_legs_passage_and_never_recalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each query's passage of one document survives the merge, an identical passage collapses, and
    the recall leg is never asked — a page search costs a page search and nothing else."""
    page = uuid4()

    def _match(text: str) -> SourceMatch:
        return SourceMatch(
            page_id=page, subject="shared", text=text, score=0.5, created_at=datetime.now(UTC)
        )

    legs = {
        "putaway": (_match("13.4 Putaway authorisation"),),
        "template": (_match("Template 24 — the post format"),),
        "formats": (_match("Template 24 — the post format"),),
    }

    class _Store:
        async def recall(self, query, subjects, limit, start, end, *, source_reader):
            raise AssertionError("a page search must not recall memory items")

        async def search_sources(self, query, subjects, limit, start, end, *, source_reader):
            return legs[query]

    monkeypatch.setattr(memory, "store_for", lambda ext: _Store())
    ctx = _tool_ctx(_ext(object(), object()), None, tmp_path)
    with ws(uuid4()):
        matches = await memory.MemorySearchService(ctx.ext).search_pages(
            ("putaway", "template", "formats"), ctx.source_reader()
        )
        found = await _search_internal(ctx, "putaway")
    text = "\n".join(memory.match_line(match) for match in matches)
    assert "13.4 Putaway authorisation" in text
    assert text.count("Template 24 — the post format") == 1
    assert "13.4 Putaway authorisation" in found.content[0].text
