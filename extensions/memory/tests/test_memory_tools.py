"""The memory extension's tools and recall hook, driven through the real scoped context.

`memory_update`/`memory_search` are the extension's tools and `recall_hook` its `user_prompt_submit`
hook; each is driven here over an `ExtensionContext` carrying the deploy index/embed backends,
exactly as core threads them onto a turn. The headline: a fact committed in one context is recalled
in a fresh one — both by the search tool and, unprompted, by the user_prompt_submit hook.
`load_sessions` stays a core builtin and keeps its proof here, driven with a memory-free context."""

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.events import MEMORY_RECALL_EVENT
from ufo_ext_memory.store import MemoryIndexer, memory_item

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.indexing import TextChunker
from ufo.models.interface import Message, TextBlock, ToolUseBlock
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.manifest import HookContext, InjectContext, UserPromptSubmit
from ufo.subjects import member_subject
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.tools.registry import ToolRegistry
from ufo.transcript import Conversation, encode, transcript_key
from ufo.workspace import ws

BUILTIN_REGISTRY = ToolRegistry(BUILTIN_TOOLS)
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


@pytest.fixture
async def clean(db: None, database_url: str) -> AsyncIterator[None]:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))
    yield


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _ext(index: object, embed: object) -> ExtensionContext:
    return context_for("memory", frozenset(), index=index, embed=embed)


def _indexer(embed: object) -> MemoryIndexer:
    return MemoryIndexer(
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
    )


def _tool_ctx(
    ext: ExtensionContext | None,
    member_id: UUID | None,
    tmp_path: Path,
    *,
    workspace_id: UUID | None = None,
    blob: FilesystemBlobStore | None = None,
) -> ToolContext:
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
        audience_member_id=member_id,
        artifact_token_secret="",
        ext=ext,
    )


async def _run(name: str, ctx: ToolContext, **args: object) -> ToolResult:
    tool = MEMORY_TOOLS[name]
    return await tool.handler(ctx, tool.input_model.model_validate(args))


async def test_memory_update_then_search_recalls_in_a_new_conversation(
    clean: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((6, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed)

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
        await provider.search((), None)


async def test_user_prompt_submit_hook_injects_and_observes_a_recalled_fact(
    clean: None,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline: a fact committed in one context is auto-injected into a fresh turn by the
    user_prompt_submit recall hook — no tool call, the recall path is the hook itself."""
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((7, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed)
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
                audience_member_id=member,
                artifact_token_secret="",
                ext=ext,
            ),
            memory.MemoryUpdateInput(body="the vault code is 4821"),
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
            audience_member_id=member,
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
                audience_member_id=member,
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
                audience_member_id=None,
                payload=UserPromptSubmit(text="what is the vault code"),
            )
        )
    record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
    assert outcome is None
    assert record.ufo["memory_ids"] == []
    assert record.ufo["error_class"] == error_name[: memory.MAX_RECALL_ERROR_CLASS_CHARS]


async def test_recall_hook_excludes_episodic_topic_pointers(
    clean: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An episodic hit is rewritten to a topic pointer and dropped from the auto-injected context;
    a durable fact is injected verbatim — the episodic→topic exclusion, end to end through the
    user_prompt_submit hook."""
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((9, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed)
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
                    audience_member_id=member,
                    payload=UserPromptSubmit(text="api key pricing"),
                )
            )
        record = next(record for record in caplog.records if record.message == MEMORY_RECALL_EVENT)
        assert isinstance(outcome, InjectContext)
        assert "the api key rotates monthly" in outcome.text
        assert "browsed the pricing page once" not in outcome.text
        assert record.ufo["memory_ids"] == [memory_ids["the api key rotates monthly"]]


async def test_recall_hook_ignores_a_non_prompt_payload(clean: None) -> None:
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
                audience_member_id=None,
                payload=None,
            )
        )
        assert outcome is None


async def test_memory_update_scopes_to_member_by_default_and_shared_on_flag(
    clean: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    embed = StubEmbed(vec((0, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed)
    ctx = _tool_ctx(ext, member, tmp_path)
    with ws(workspace_id):
        await _run("memory_update", ctx, body="a private note")
        await _run("memory_update", ctx, body="a team note", shared=True)
        async with workspace_tx() as connection:
            subjects = sorted(
                row.subject
                for row in (await connection.execute(sa.select(memory_item.c.subject))).all()
            )
    assert subjects == sorted([member_subject(member), "shared"])


async def test_memory_search_reports_no_match_on_empty_memory(clean: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    embed = StubEmbed(vec((0, 1.0)))
    ext = _ext(DefaultIndex(transaction=workspace_tx), embed)
    with ws(workspace_id):
        result = await _run(
            "memory_search", _tool_ctx(ext, uuid4(), tmp_path), queries=["anything"]
        )
        assert result.content[0].text == "No matching memory."


def test_date_bound_reads_a_bare_end_date_as_the_whole_day() -> None:
    assert memory._date_bound(None, end=False) is None
    assert memory._date_bound("2026-01-31", end=False) == datetime(2026, 1, 31, tzinfo=UTC)
    assert memory._date_bound("2026-01-31", end=True) == datetime(2026, 2, 1, tzinfo=UTC)
    assert memory._date_bound("2026-01-31T12:00:00", end=True) == datetime(
        2026, 1, 31, 12, tzinfo=UTC
    )


async def test_memory_search_merges_and_dedups_across_queries(clean: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    working = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    ext = _ext(index, BrokenEmbed())
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
    clean: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member = uuid4()
    working = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    ext = _ext(index, BrokenEmbed())
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
    ctx = _tool_ctx(_ext(object(), object()), uuid4(), tmp_path)
    with ws(uuid4()):
        found = await _run("memory_search", ctx, queries=["alpha", "beta"])
    bodies = [line.split("] ", 1)[1] for line in found.content[0].text.splitlines()]
    assert bodies == ["a-one", "b-one", "a-two"]


async def _load_sessions(ctx: ToolContext, **args: object) -> ToolResult:
    tool = BUILTIN_REGISTRY.get("load_sessions")
    return await tool.handler(ctx, tool.input_model.model_validate(args))


async def test_load_sessions_returns_named_transcripts_and_reports_the_rest(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    member, other = uuid4(), uuid4()
    conversation_id, other_conversation_id, unknown = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        for member_id, email in ((member, "a@example.test"), (other, "b@example.test")):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        for cid, owner in ((conversation_id, member), (other_conversation_id, other)):
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=cid,
                    workspace_id=workspace_id,
                    surface="slack",
                    queue_key=cid.hex,
                    member_id=owner,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    await blob.put(
        transcript_key(conversation_id),
        encode(
            Conversation(
                seq=2,
                messages=(
                    Message(role="user", content="remind me about the launch"),
                    Message(
                        role="assistant",
                        content=(
                            ToolUseBlock(id="t1", name="memory_search", input={"queries": ["x"]}),
                            TextBlock(text="the launch is march 3"),
                        ),
                    ),
                ),
            )
        ),
    )
    await blob.put(
        transcript_key(other_conversation_id),
        encode(Conversation(seq=1, messages=(Message(role="user", content="private"),))),
    )
    ctx = _tool_ctx(None, member, tmp_path, workspace_id=workspace_id, blob=blob)

    with ws(workspace_id):
        result = await _load_sessions(
            ctx,
            session_ids=[str(conversation_id), str(other_conversation_id), str(unknown)],
        )
    payload = json.loads(result.content[0].text)

    assert [session["session_id"] for session in payload["sessions"]] == [str(conversation_id)]
    session = payload["sessions"][0]
    assert session["surface"] == "slack"
    assert [message["text"] for message in session["messages"]] == [
        "remind me about the launch",
        "the launch is march 3",
    ]
    assert set(payload["failed"]) == {str(other_conversation_id), str(unknown)}
