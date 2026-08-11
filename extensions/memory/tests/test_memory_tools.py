"""The memory extension's tools and recall hook, driven through the real scoped context.

`memory_update`/`memory_search` are the extension's tools and `recall_hook` its `user_prompt_submit`
hook; each is driven here over an `ExtensionContext` carrying the deploy index/embed backends,
exactly as core threads them onto a turn. The headline: a fact committed in one context is recalled
in a fresh one — both by the search tool and, unprompted, by the user_prompt_submit hook."""

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
from pydantic import ValidationError
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.events import MEMORY_RECALL_EVENT
from ufo_ext_memory.objects import MEMORY_KIND, MEMORY_OBJECT, MemoryObjects
from ufo_ext_memory.store import MemoryIndexer, SourceMatch, memory_item

from ufo.agent_scope import agent
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.indexing import TextChunker
from ufo.objects import BoundKind, ObjectListQuery, ObjectVerbs, object_registry
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.sdk.manifest import HookContext, InjectContext, UserPromptSubmit
from ufo.subjects import member_subject
from ufo.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.workspace import ws

TOOL_NARRATION = "remembering what they told me"

MEMORY_TOOLS = {tool.name: tool for tool in memory.manifest().tools}


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


def _ext(index: object, embed: object, audience: Audience = SHARED_AUDIENCE) -> ExtensionContext:
    return context_for("memory", frozenset(), index=index, embed=embed, audience=audience)


def _indexer(embed: object) -> MemoryIndexer:
    return MemoryIndexer(
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        page_states=_ext(DefaultIndex(transaction=workspace_tx), embed).page_states,
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


async def _run(name: str, ctx: ToolContext, **args: object) -> ToolResult:
    tool = MEMORY_TOOLS[name]
    return await tool.handler(
        ctx, tool.input_model.model_validate({"user_description": TOOL_NARRATION, **args})
    )


async def test_memory_update_then_search_recalls_in_a_new_conversation(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((6, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed, conversation_audience(member))

    with ws(workspace_id):
        stored = await _run(
            "memory_update",
            _tool_ctx(ext, member, tmp_path),
            body="the deploy password is hunter2",
        )
        assert stored.is_error is False
        await _indexer(embed).run()

        found = await _run("memory_search", _tool_ctx(ext, member, tmp_path), queries=["hunter2"])
        assert "hunter2" in found.content[0].text


async def test_memory_search_provider_rejects_an_empty_query_set() -> None:
    (spec,) = memory.manifest().memory_search
    embed = StubEmbed(vec((0, 1.0)))
    provider = spec.build(_ext(DefaultIndex(transaction=workspace_tx), embed))
    with pytest.raises(ValueError, match="requires 1-3 queries"):
        await provider.search((), frozenset({"shared"}))


async def test_user_prompt_submit_hook_injects_and_observes_a_recalled_fact(
    db: None,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline: a fact committed in one context is auto-injected into a fresh turn by the
    user_prompt_submit recall hook — no tool call, the recall path is the hook itself."""
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((7, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed, conversation_audience(member))
    with ws(workspace_id):
        await MEMORY_TOOLS["memory_update"].handler(
            ToolContext(
                sandbox=None,
                blob=None,
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
                speaker_member_id=member,
                audience=conversation_audience(member),
                artifact_token_secret="",
                ext=ext,
            ),
            memory.MemoryUpdateInput(
                body="the vault code is 4821", user_description=TOOL_NARRATION
            ),
        )
        await _indexer(embed).run()
        async with workspace_tx() as connection:
            memory_id = (
                await connection.execute(
                    sa.select(memory_item.c.id).where(
                        memory_item.c.workspace_id == workspace_id,
                        memory_item.c.body == "the vault code is 4821",
                    )
                )
            ).scalar_one()

        turn_id = uuid4()
        hook = HookContext(
            ext=ext,
            turn=Turn(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=uuid4(),
                agent_id=uuid4(),
                seq=1,
                status="running",
                inbound="what is the vault code",
                created_at=datetime(2026, 7, 9, tzinfo=UTC),
            ),
            agent=Agent(prompt="p", model="claude-opus-4-8"),
            speaker_member_id=member,
            audience=conversation_audience(member),
            payload=UserPromptSubmit(text="what is the vault code"),
        )
        with caplog.at_level(logging.INFO, logger="ufo"):
            outcome = await memory.recall_hook(hook)
        record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
        assert record.ufo["turn_id"] == str(turn_id)
        assert record.ufo["memory_ids"] == [str(memory_id)]
        assert "error_class" not in record.ufo
        assert "vault code" not in str(record.ufo)
        assert isinstance(outcome, InjectContext)
        assert "the vault code is 4821" in outcome.text

        without_turn = await memory.recall_hook(
            HookContext(
                ext=ext,
                turn=None,
                agent=Agent(prompt="p", model="claude-opus-4-8"),
                speaker_member_id=member,
                audience=conversation_audience(member),
                payload=UserPromptSubmit(text="what is the vault code"),
            )
        )
        assert without_turn is None

        def fail_log(_event: str, **_fields: object) -> None:
            raise RuntimeError("collector unavailable")

        monkeypatch.setattr(memory, "log", fail_log)
        outcome = await memory.recall_hook(hook)
        assert isinstance(outcome, InjectContext)
        assert "the vault code is 4821" in outcome.text


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


async def test_recall_hook_excludes_episodic_topic_pointers(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An episodic hit is rewritten to a topic pointer and dropped from the auto-injected context;
    a durable fact is injected verbatim — the episodic→topic exclusion, end to end through the
    user_prompt_submit hook."""
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((9, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed, conversation_audience(member))
    with ws(workspace_id):
        await _run(
            "memory_update", _tool_ctx(ext, member, tmp_path), body="the api key rotates monthly"
        )
        await _run(
            "memory_update",
            _tool_ctx(ext, member, tmp_path),
            body="browsed the pricing page once",
            item_class="episodic",
        )
        await _indexer(embed).run()
        async with workspace_tx() as connection:
            memory_ids = {
                row.body: str(row.id)
                for row in (
                    await connection.execute(
                        sa.select(memory_item.c.id, memory_item.c.body).where(
                            memory_item.c.workspace_id == workspace_id
                        )
                    )
                ).all()
            }

        with caplog.at_level(logging.INFO, logger="ufo"):
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
                        inbound="api key pricing",
                        created_at=datetime(2026, 7, 9, tzinfo=UTC),
                    ),
                    agent=Agent(prompt="p", model="claude-opus-4-8"),
                    speaker_member_id=member,
                    audience=conversation_audience(member),
                    payload=UserPromptSubmit(text="api key pricing"),
                )
            )
        record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
        assert isinstance(outcome, InjectContext)
        assert "the api key rotates monthly" in outcome.text
        assert "browsed the pricing page once" not in outcome.text
        assert record.ufo["memory_ids"] == [memory_ids["the api key rotates monthly"]]


async def test_recall_hook_ignores_a_non_prompt_payload(db: None) -> None:
    workspace_id = await _workspace()
    embed = StubEmbed(vec((0, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed)
    with ws(workspace_id):
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
                    inbound="hi",
                    created_at=datetime(2026, 7, 9, tzinfo=UTC),
                ),
                agent=Agent(prompt="p", model="claude-opus-4-8"),
                speaker_member_id=None,
                audience=conversation_audience(None),
                payload=None,
            )
        )
        assert outcome is None


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
        await _run("memory_update", bound_ctx, body="a private note")
        await _run("memory_update", common_ctx, body="a team note")
        async with workspace_tx() as connection:
            subjects = sorted(
                row.subject
                for row in (await connection.execute(sa.select(memory_item.c.subject))).all()
            )
        await _indexer(embed).run()
        private_read = await _run("memory_search", bound_ctx, queries=["note"])
        shared_read = await _run("memory_search", common_ctx, queries=["note"])
    assert subjects == sorted([member_subject(member), "shared"])
    assert "a private note" in private_read.content[0].text
    assert "a team note" in private_read.content[0].text
    assert "a private note" not in shared_read.content[0].text
    assert "a team note" in shared_read.content[0].text
    with pytest.raises(ValidationError):
        memory.MemoryUpdateInput.model_validate({"body": "widened", "shared": True})


async def test_shared_recall_excludes_message_bound_private_memory(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    ext = _ext(index, embed)
    with ws(workspace_id):
        await _run(
            "memory_update",
            _tool_ctx(ext, member, tmp_path, audience=SHARED_AUDIENCE),
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
                ),
                agent=Agent(prompt="p", model="claude-opus-4-8"),
                speaker_member_id=member,
                audience=SHARED_AUDIENCE,
                payload=UserPromptSubmit(text="launch note"),
            )
        )

    assert isinstance(outcome, InjectContext)
    assert "common launch note" in outcome.text
    assert "member private launch note" not in outcome.text


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


async def test_memory_search_interleaves_per_query_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The merge interleaves the per-query legs round-robin — query-1's top, query-2's top, then
    query-1's second — so a sparse query's hit precedes a dense query's tail. A global best-score
    top-N would order the low-scoring b-one behind the higher-scoring a-two; interleave keeps each
    query represented, which is what the merge is for. The store is faked to fix the per-query legs;
    the assertion is on the handler's merge order, not the store."""
    from ufo_ext_memory.store import Recalled

    def _recalled(body: str, score: float) -> Recalled:
        return Recalled(
            memory_id=uuid4(),
            subject="s",
            item_class="fact",
            body=body,
            source_ref=None,
            score=score,
        )

    legs = {
        "alpha": (_recalled("a-one", 0.9), _recalled("a-two", 0.8)),
        "beta": (_recalled("b-one", 0.1),),
    }

    class _Store:
        async def recall(
            self,
            query: str,
            subjects: object,
            limit: int,
            start: object,
            end: object,
            *,
            source_reader: object,
        ):
            return legs[query]

        async def search_sources(
            self,
            query: str,
            subjects: object,
            limit: int,
            start: object,
            end: object,
            *,
            source_reader: object,
        ):
            return ()

    monkeypatch.setattr(memory, "store_for", lambda ext: _Store())
    ctx = _tool_ctx(_ext(object(), object()), None, tmp_path)
    with ws(uuid4()):
        found = await _run("memory_search", ctx, queries=["alpha", "beta"])
    bodies = [
        line.split("] ", 1)[1].rsplit(" (memory/", 1)[0]
        for line in found.content[0].text.splitlines()
    ]
    assert bodies == ["a-one", "b-one", "a-two"]


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
    """`object_list kind=memory` and `object_get kind=memory` read `ctx.read_subjects` too, so the
    Slack Connect seal has to hold on the object surface exactly as it does on search."""
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


async def test_the_memory_kind_filters_and_orders_on_its_declared_fields(
    db: None, tmp_path: Path
) -> None:
    """Every field `memory` declares rides its listing rows, so a filter and an order on each one
    answers from the live listing — the only place the declaration is checked against the rows."""
    workspace_id = await _workspace()
    alice = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    alice_dm = conversation_audience(alice)
    alice_ctx = _tool_ctx(
        _ext(index, embed, alice_dm), alice, tmp_path, workspace_id=workspace_id, audience=alice_dm
    )
    verbs = ObjectVerbs(
        registry=object_registry(
            (BoundKind(kind=MEMORY_OBJECT, extension="memory", context=alice_ctx.ext),)
        )
    )

    async def listed(**args: object) -> list[dict]:
        tool = next(tool for tool in verbs.tools() if tool.name == "object_list")
        result = await tool.handler(
            alice_ctx,
            tool.input_model.model_validate(
                {"user_description": TOOL_NARRATION, "kind": MEMORY_KIND, **args}
            ),
        )
        assert result.is_error is False
        return json.loads(result.content[0].text)["objects"]

    with ws(workspace_id):
        await _run(
            "memory_update",
            alice_ctx,
            body="alice prefers plaintext email",
            item_class="semantic",
            memory_kind="preference",
        )
        await _run(
            "memory_update",
            alice_ctx,
            body="alice shipped the billing migration",
            item_class="fact",
            memory_kind="event",
        )

        rows = await listed()
        by_kind = await listed(filters={"memory_kind": "preference"})
        by_class = await listed(filters={"item_class": "fact"})
        by_subject = await listed(filters={"subject": member_subject(alice)})
        ordered = await listed(order_by="memory_kind")

    assert {row["memory_kind"] for row in rows} == {"preference", "event"}
    assert {row["item_class"] for row in rows} == {"semantic", "fact"}
    assert {row["subject"] for row in rows} == {member_subject(alice)}
    assert [row["memory_kind"] for row in by_kind] == ["preference"]
    assert [row["item_class"] for row in by_class] == ["fact"]
    assert len(by_subject) == 2
    assert [row["memory_kind"] for row in ordered] == ["event", "preference"]


async def test_portal_reads_hold_the_subject_and_source_gates_the_turn_holds(
    db: None, tmp_path: Path
) -> None:
    """The portal reads memory on the subjects a member's own conversation carries and behind the
    bound agent's source grant: another member's private item is absent from the index and
    not-found by name, and a shared page-derived item answers only under an agent granted its
    source."""
    workspace_id = await _workspace()
    alice, bob = uuid4(), uuid4()
    granted_agent, ungranted_agent = uuid4(), uuid4()
    source_id, page_id, item_id = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 9, tzinfo=UTC)
    index = DefaultIndex(transaction=workspace_tx)
    embed = StubEmbed(vec((0, 1.0)))
    alice_dm = conversation_audience(alice)
    alice_ctx = _tool_ctx(
        _ext(index, embed, alice_dm), alice, tmp_path, workspace_id=workspace_id, audience=alice_dm
    )
    async with workspace_tx() as connection:
        for agent_id, name in ((granted_agent, "granted"), (ungranted_agent, "ungranted")):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="m",
                    is_main=False,
                    created_at=now,
                    updated_at=now,
                )
            )
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                subject="shared",
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.source_grant).values(
                workspace_id=workspace_id,
                source_id=source_id,
                agent_id=granted_agent,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
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
                sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject="shared",
                body="the vault code is 8842",
                item_class="fact",
                memory_kind="fact",
                confidence=5,
                created_from_page_id=page_id,
                created_from_page_revision=revision,
                source_id=source_id,
                embedding_digest="sha256:seeded",
                created_at=now,
                updated_at=now,
            )
        )
    store = MemoryObjects()
    ext = alice_ctx.ext
    query = ObjectListQuery(supported_fields=MEMORY_OBJECT.list_fields)
    with ws(workspace_id):
        await _run("memory_update", alice_ctx, body="alice private roadmap")
        async with workspace_tx() as connection:
            private_id = (
                await connection.execute(
                    sa.select(memory_item.c.id).where(
                        memory_item.c.subject == member_subject(alice)
                    )
                )
            ).scalar_one()
        with agent(granted_agent):
            alice_names = {
                row.name
                for row in (
                    await store.member_page(ext, member_id=alice, admin=False, query=query)
                ).rows
            }
            bob_names = {
                row.name
                for row in (
                    await store.member_page(ext, member_id=bob, admin=True, query=query)
                ).rows
            }
            bob_read = await store.member_detail(ext, str(private_id), member_id=bob, admin=True)
            granted_read = await store.member_detail(ext, str(item_id), member_id=bob, admin=False)
        with agent(ungranted_agent):
            ungranted_names = {
                row.name
                for row in (
                    await store.member_page(ext, member_id=bob, admin=False, query=query)
                ).rows
            }
            ungranted_read = await store.member_detail(
                ext, str(item_id), member_id=bob, admin=False
            )
    assert {str(private_id), str(item_id)} <= alice_names
    assert bob_names == {str(item_id)}
    assert bob_read is None
    assert granted_read is not None
    assert granted_read.row.fields["subject"] == "shared"
    assert ungranted_names == set()
    assert ungranted_read is None


async def test_a_page_derived_memory_object_is_fenced_on_the_source_grant(
    db: None, tmp_path: Path
) -> None:
    """`object_list`/`object_get kind=memory` recheck a page-derived row's source grant: an agent
    without a grant for the source sees nothing, while the granted agent reads it in full — the
    object surface holds the same source fence recall does, so a fact never leaks via a listing."""
    workspace_id = await _workspace()
    source_id, page_id, item_id = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 9, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={},
                subject="shared",
                next_sync_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
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
                sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(memory_item).values(
                id=item_id,
                workspace_id=workspace_id,
                subject="shared",
                body="the vault code is 8842",
                item_class="fact",
                memory_kind="fact",
                confidence=5,
                created_from_page_id=page_id,
                created_from_page_revision=revision,
                source_id=source_id,
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
            sa.insert(tables.source_grant).values(
                workspace_id=workspace_id,
                source_id=source_id,
                agent_id=granted.turn.agent_id,
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


async def test_list_recent_carries_each_row_subject(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    ext = _ext(index, embed)
    bound_ctx = _tool_ctx(ext, member, tmp_path, audience=SHARED_AUDIENCE)
    common_ctx = _tool_ctx(ext, None, tmp_path, audience=SHARED_AUDIENCE)
    with ws(workspace_id):
        await _run("memory_update", bound_ctx, body="a private note")
        await _run("memory_update", common_ctx, body="a team note")
        listed = await memory.MemorySearchService(ext).list_recent(
            frozenset({member_subject(member), "shared"}), 25
        )
    assert {(row.text, row.subject) for row in listed.rows} == {
        ("a private note", member_subject(member)),
        ("a team note", "shared"),
    }
