"""The memory extension's tools and recall hook, driven through the real scoped context.

`memory_update`/`memory_search` are the extension's tools and `recall_hook` its `user_prompt_submit`
hook; each is driven here over an `ExtensionContext` carrying the deploy index/embed backends,
exactly as core threads them onto a turn. The headline: a fact committed in one context is recalled
in a fresh one — both by the search tool and, unprompted, by the user_prompt_submit hook."""

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
from ufo_ext_memory.objects import MEMORY_OBJECT, MemoryObjects
from ufo_ext_memory.store import MemoryIndexer, memory_item

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.indexing import TextChunker
from ufo.objects import ObjectListQuery
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
        assert isinstance(without_turn, InjectContext)
        assert "the vault code is 4821" in without_turn.text

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
            self, query: str, subjects: frozenset[str], limit: int
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
            self, query: str, subjects: object, limit: int, start: object, end: object
        ):
            return legs[query]

        async def search_sources(
            self, query: str, subjects: object, limit: int, start: object, end: object
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
