"""The workspace-scoped user-skill loop end to end: a skill authored in the workspace is applied
as a `skill` object, persisted in the extension's own `user_skill` table with its routing-card
columns, and served back through the member-skills seam — cards for a turn's routing set,
one materialized skill per load — answering every agent of that workspace and no other workspace,
and never able to shadow a core skill.

The store tests drive `UserSkillStore` over a real `ExtensionContext` against a real database; the
object tests drive the kind through the real object verbs, with `FileFrom` resolution running
through a real `SandboxSession` on the local carrier (host subprocesses, no container). The index
job runs over the real `DefaultIndex`; the embed client is a stand-in whose output is never
asserted — the derived chunk rows are."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from pydantic import ValidationError
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_skill_create.manifest import (
    NAME,
    SKILL_INDEX_DESCRIPTION_MAX_CHARS,
    SKILL_INDEX_JOB,
    SKILL_INDEX_SCHEDULE,
    SKILL_KIND,
    SKILL_OBJECT,
    SkillObjects,
    index_skills,
    manifest,
)
from ufo_ext_skill_create.store import (
    MAX_PINNED_USER_SKILLS,
    MAX_USER_SKILLS_PER_WORKSPACE,
    SKILL_OWNER_KIND,
    SKILL_SUBJECT,
    InvalidSkillName,
    PinnedSkillLimit,
    SkillCollidesWithCoreSkill,
    StaleSkillGeneration,
    TooManyUserSkills,
    UserSkillStore,
    user_skill,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    ProxyEndpoint,
    SandboxSession,
    SandboxSpec,
)
from ufo.host.ext.loader import turn_tools
from ufo.runtime.agent_scope import AgentUnbound, agent
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.jobs import CORE_EXTENSION, JobRunner, bindings_from
from ufo.runtime.objects import ObjectListQuery
from ufo.runtime.skills.runtime import CORE_SKILL_NAMES
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.index import IndexScope
from ufo.sdk.skills import SkillCard

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOOL_NARRATION = "saving the workflow"


def _skill_md(
    name: str,
    description: str,
    body: str = "Follow the steps.",
    depends: tuple[str, ...] = (),
    agents: tuple[str, ...] = (),
) -> bytes:
    front: dict[str, object] = {"name": name, "description": description}
    metadata: dict[str, object] = {}
    if depends:
        metadata["depends"] = list(depends)
    if agents:
        metadata["agents"] = list(agents)
    if metadata:
        front["metadata"] = metadata
    return f"---\n{yaml.safe_dump(front)}---\n{body}\n".encode()


def _ext() -> ExtensionContext:
    return context_for(NAME, frozenset())


def _store() -> UserSkillStore:
    return UserSkillStore(_ext())


async def _generation(store: UserSkillStore, name: str) -> UUID:
    record = await store.record(name)
    assert record is not None
    return record.generation


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """Deterministic stand-in EmbedClient the index job embeds through; the tests assert the
    derived chunk rows, never this stand-in."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


class FirstCallEmbed:
    """Fires a callback inside the first embed — the interleaving hook for a writer racing the
    index job mid-embed; the tests assert the rows and chunks, never this stand-in."""

    def __init__(self, vector: tuple[float, ...], callback: Callable[[], Awaitable[None]]) -> None:
        self._vector = vector
        self._callback = callback
        self._fired = False

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not self._fired:
            self._fired = True
            await self._callback()
        return tuple(self._vector for _ in texts)


class RefusingEmbed:
    """Raises on any text containing the marker — one poisoned row among healthy ones."""

    def __init__(self, vector: tuple[float, ...], marker: str) -> None:
        self._vector = vector
        self._marker = marker

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if any(self._marker in text for text in texts):
            raise RuntimeError("embed refused the poisoned text")
        return tuple(self._vector for _ in texts)


def _indexed_ext() -> tuple[ExtensionContext, DefaultIndex]:
    index = DefaultIndex(transaction=workspace_tx)
    return context_for(NAME, frozenset(), index, StubEmbed(vec((0, 1.0)))), index


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _agent(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _workspace_agent(name: str = "assistant") -> tuple[UUID, UUID]:
    workspace_id = await _workspace()
    return workspace_id, await _agent(workspace_id, name)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this context")


async def _skill_chunk_count() -> int:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.text("select count(*) from chunk where owner_kind = 'skill'")
            )
        ).scalar_one()


def test_caps_hold_their_designed_values() -> None:
    assert MAX_USER_SKILLS_PER_WORKSPACE == 5000
    assert MAX_PINNED_USER_SKILLS == 10


async def test_save_persists_and_projects_the_routing_card(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    files = {
        "SKILL.md": _skill_md("greet", "greets people", depends=("tone",)),
        "references/tone.md": b"warm",
    }

    with ws(workspace_id), agent(agent_id):
        saved = await store.save("greet", files, frozenset(CORE_SKILL_NAMES))
    assert saved.name == "greet"
    assert saved.description == "greets people"

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    user_skill.c.name,
                    user_skill.c.generation,
                    user_skill.c.digest,
                    user_skill.c.description,
                    user_skill.c.depends,
                    user_skill.c.agents,
                    user_skill.c.pinned,
                    user_skill.c.indexed_digest,
                ).where(user_skill.c.workspace_id == workspace_id)
            )
        ).one()
    assert row.name == "greet"
    assert row.generation is not None
    assert row.digest.startswith("sha256:")
    assert row.description == "greets people"
    assert json.loads(row.depends) == ["tone"]
    assert json.loads(row.agents) == []
    assert row.pinned is False
    assert row.indexed_digest is None

    with ws(workspace_id), agent(agent_id):
        cards = await store.cards()
    assert cards == (
        SkillCard(name="greet", description="greets people", depends=("tone",), pinned=False),
    )


async def test_materialize_round_trips_and_misses_as_none(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    files = {"SKILL.md": _skill_md("greet", "greets people"), "references/tone.md": b"warm"}
    with ws(workspace_id), agent(agent_id):
        await store.save("greet", files, frozenset())
        loaded = await store.materialize("greet")
        missing = await store.materialize("absent")
    assert missing is None
    assert loaded is not None
    assert loaded.name == "greet"
    assert loaded.instructions == "Follow the steps."
    assert ("references/tone.md", b"warm") in loaded.files


async def test_cards_survive_corrupt_content_and_materialize_fails_loud(db: None) -> None:
    """The projection never decodes content: a row whose stored bundle no longer parses keeps its
    card columns, so the routing set and the portal listing survive. The load of that one name is
    where corruption surfaces — loud, never a silent miss."""
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.save("alpha", {"SKILL.md": _skill_md("alpha", "A")}, frozenset())
        await store.save("beta", {"SKILL.md": _skill_md("beta", "B")}, frozenset())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(user_skill)
            .values(content="{ not valid json")
            .where(
                user_skill.c.workspace_id == workspace_id,
                user_skill.c.name == "alpha",
            )
        )

    with ws(workspace_id), agent(agent_id):
        cards = await store.cards()
        assert [(card.name, card.description) for card in cards] == [("alpha", "A"), ("beta", "B")]
        with pytest.raises(ValidationError):
            await store.materialize("alpha")
        beta = await store.materialize("beta")
    assert beta is not None
    assert beta.description == "B"


async def test_save_after_a_delete_requires_a_fresh_create(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.save("greet", {"SKILL.md": _skill_md("greet", "v1")}, frozenset())
        first = await store.record("greet")
        assert first is not None
        await store.delete("greet")
        with pytest.raises(StaleSkillGeneration, match="deleted"):
            await store.save(
                "greet",
                {"SKILL.md": _skill_md("greet", "stale")},
                frozenset(),
                generation=first.generation,
            )
        await store.save("greet", {"SKILL.md": _skill_md("greet", "anew")}, frozenset())
        record = await store.record("greet")
    assert record is not None
    assert record.generation != first.generation
    assert record.description == "anew"


async def test_pinned_cap_enforced_and_pin_survives_resave(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        for n in range(MAX_PINNED_USER_SKILLS):
            name = f"pin-{n}"
            await store.save(name, {"SKILL.md": _skill_md(name, "d")}, frozenset(), pinned=True)
        with pytest.raises(PinnedSkillLimit):
            await store.save(
                "one-too-many",
                {"SKILL.md": _skill_md("one-too-many", "d")},
                frozenset(),
                pinned=True,
            )
        await store.save(
            "pin-0",
            {"SKILL.md": _skill_md("pin-0", "edited")},
            frozenset({"pin-0"}),
            pinned=True,
            generation=await _generation(store, "pin-0"),
        )
        await store.save("unpinned", {"SKILL.md": _skill_md("unpinned", "d")}, frozenset())
        cards = {card.name: card for card in await store.cards()}
    assert cards["pin-0"].pinned is True
    assert cards["pin-0"].description == "edited"
    assert cards["unpinned"].pinned is False
    assert "one-too-many" not in cards


async def test_a_kept_pin_saves_past_a_cap_the_workspace_already_exceeds(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin cap gates pinning an unpinned skill, never keeping a pin: a workspace the migration
    left above the cap still re-saves its pinned skills — every portal save states the stored pin
    back — and only a new pin is refused."""
    monkeypatch.setattr("ufo_ext_skill_create.store.MAX_PINNED_USER_SKILLS", 1)
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.save("first", {"SKILL.md": _skill_md("first", "1")}, frozenset(), pinned=True)
        await store.save("second", {"SKILL.md": _skill_md("second", "2")}, frozenset())
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(user_skill)
            .values(pinned=True)
            .where(user_skill.c.workspace_id == workspace_id, user_skill.c.name == "second")
        )
    with ws(workspace_id), agent(agent_id):
        await store.save(
            "first",
            {"SKILL.md": _skill_md("first", "kept")},
            frozenset({"first"}),
            pinned=True,
            generation=await _generation(store, "first"),
        )
        with pytest.raises(PinnedSkillLimit):
            await store.save(
                "third", {"SKILL.md": _skill_md("third", "3")}, frozenset(), pinned=True
            )
        cards = {card.name: card for card in await store.cards()}
    assert cards["first"].pinned is True
    assert cards["first"].description == "kept"
    assert cards["second"].pinned is True
    assert "third" not in cards


async def test_resave_without_a_pin_unpins(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.save("greet", {"SKILL.md": _skill_md("greet", "d")}, frozenset(), pinned=True)
        await store.save(
            "greet",
            {"SKILL.md": _skill_md("greet", "d")},
            frozenset({"greet"}),
            generation=await _generation(store, "greet"),
        )
        [card] = await store.cards()
    assert card.pinned is False


async def test_a_saved_skill_is_scoped_to_its_workspace(db: None) -> None:
    author, agent_id = await _workspace_agent()
    other, other_agent = await _workspace_agent()
    with ws(author), agent(agent_id):
        await _store().save("greet", {"SKILL.md": _skill_md("greet", "greets people")}, frozenset())
        assert [card.name for card in await _store().cards()] == ["greet"]
    with ws(other), agent(other_agent):
        assert await _store().cards() == ()
        assert await _store().materialize("greet") is None


async def test_store_requires_an_agent_boundary(db: None) -> None:
    workspace_id, _ = await _workspace_agent()
    with ws(workspace_id), pytest.raises(AgentUnbound):
        await _store().cards()


async def test_save_refuses_a_name_that_shadows_a_core_skill(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    files = {"SKILL.md": _skill_md("sandbox", "a hijack attempt")}

    with ws(workspace_id), agent(agent_id):
        with pytest.raises(SkillCollidesWithCoreSkill):
            await store.save("sandbox", files, frozenset(CORE_SKILL_NAMES))
        assert await store.cards() == ()


async def test_save_refuses_an_unsafe_skill_name(db: None) -> None:
    for bad_name in (
        "../../etc",
        "..",
        ".",
        "a/b",
        "sandbox/child",
        "Sandbox",
        "my skill",
        "",
        "-x",
    ):
        workspace_id, agent_id = await _workspace_agent()
        store = _store()
        with ws(workspace_id), agent(agent_id):
            with pytest.raises(InvalidSkillName):
                await store.save(bad_name, {"SKILL.md": _skill_md(bad_name, "d")}, frozenset())
            assert await store.cards() == ()


async def test_save_refuses_over_the_skill_cap_but_allows_a_resave(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo_ext_skill_create.store.MAX_USER_SKILLS_PER_WORKSPACE", 2)
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.save("one", {"SKILL.md": _skill_md("one", "1")}, frozenset())
        await store.save("two", {"SKILL.md": _skill_md("two", "2")}, frozenset())
        with pytest.raises(TooManyUserSkills):
            await store.save("three", {"SKILL.md": _skill_md("three", "3")}, frozenset())
        await store.save(
            "one",
            {"SKILL.md": _skill_md("one", "1b")},
            frozenset({"one"}),
            generation=await _generation(store, "one"),
        )
        assert sorted(card.name for card in await store.cards()) == ["one", "two"]
    other_agent = await _agent(workspace_id, "other")
    with ws(workspace_id), agent(other_agent):
        with pytest.raises(TooManyUserSkills):
            await store.save("three", {"SKILL.md": _skill_md("three", "3")}, frozenset())


async def test_member_skills_seam_serves_cards_and_materializes_by_name(db: None) -> None:
    """The seam end to end: the extension's `member_skills` declaration is the producer core's
    turn admission consumes — `cards` answers the routing set and `materialize` one named load,
    each under the ambient workspace. Every agent of that workspace reads the same set; another
    workspace sees none of it."""
    author, agent_id = await _workspace_agent()
    other, other_agent = await _workspace_agent()
    author_other_agent = await _agent(author, "other")
    spec = manifest().member_skills
    assert spec is not None
    with ws(author), agent(agent_id):
        await _store().save("greet", {"SKILL.md": _skill_md("greet", "greets people")}, frozenset())
        cards = await spec.cards(_ext())
        loaded = await spec.materialize(_ext(), "greet")
        missing = await spec.materialize(_ext(), "absent")
    assert cards == (
        SkillCard(name="greet", description="greets people", depends=(), pinned=False),
    )
    assert loaded is not None
    assert loaded.instructions == "Follow the steps."
    assert missing is None

    with ws(author), agent(author_other_agent):
        assert await spec.cards(_ext()) == cards
        assert await spec.materialize(_ext(), "greet") is not None
    with ws(other), agent(other_agent):
        assert await spec.cards(_ext()) == ()
        assert await spec.materialize(_ext(), "greet") is None


async def test_delete_prunes_the_skills_index_scope(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    ctx, _ = _indexed_ext()
    store = UserSkillStore(ctx)
    with ws(workspace_id), agent(agent_id):
        await store.save(
            "greet", {"SKILL.md": _skill_md("greet", "greets visitors politely")}, frozenset()
        )
    with ws(workspace_id):
        await index_skills(ctx)
    assert await _skill_chunk_count() >= 1

    with ws(workspace_id), agent(agent_id):
        await store.delete("greet")
    assert await _skill_chunk_count() == 0
    with ws(workspace_id), agent(agent_id):
        assert await store.cards() == ()


async def test_index_job_without_backends_fails_loud(db: None) -> None:
    workspace_id, _ = await _workspace_agent()
    with ws(workspace_id), pytest.raises(RuntimeError, match="index and embed"):
        await index_skills(_ext())


async def test_materialize_all_skips_corrupt_and_keeps_the_rest(db: None) -> None:
    """The listing path's read-time tolerance: one bad bundle is skipped with a log, never hides
    the rest, and never turns into a raise the portal read would carry."""
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.save("alpha", {"SKILL.md": _skill_md("alpha", "A")}, frozenset())
        await store.save(
            "beta", {"SKILL.md": _skill_md("beta", "B"), "references/tone.md": b"warm"}, frozenset()
        )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(user_skill)
            .values(content="{ not valid json")
            .where(
                user_skill.c.workspace_id == workspace_id,
                user_skill.c.name == "alpha",
            )
        )
    with ws(workspace_id), agent(agent_id):
        loaded = await store.materialize_all()
        spec = manifest().member_skills
        assert spec is not None
        seam = await spec.materialize_all(_ext())
    assert [skill.name for skill in loaded] == ["beta"]
    assert ("references/tone.md", b"warm") in loaded[0].files
    assert [skill.name for skill in seam] == ["beta"]


async def test_delete_crash_after_prune_leaves_a_stale_row_the_next_tick_repairs(db: None) -> None:
    """Delete's crash window, driven step by step: the stale-mark and the index prune have run but
    the row delete has not. The row is due again, so the next tick re-derives its chunks and
    settles — a half-deleted skill is findable again, never a settled row with no chunks."""
    workspace_id, agent_id = await _workspace_agent()
    ctx, index = _indexed_ext()
    store = UserSkillStore(ctx)
    with ws(workspace_id), agent(agent_id):
        await store.save(
            "greet", {"SKILL.md": _skill_md("greet", "greets visitors politely")}, frozenset()
        )
    with ws(workspace_id):
        await index_skills(ctx)
    assert await _skill_chunk_count() >= 1

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(user_skill)
            .values(indexed_digest=None)
            .where(user_skill.c.workspace_id == workspace_id, user_skill.c.name == "greet")
        )
    with ws(workspace_id):
        await index.delete(IndexScope(SKILL_OWNER_KIND, "greet"))
    assert await _skill_chunk_count() == 0

    with ws(workspace_id):
        await index_skills(ctx)
        assert (
            len(
                await index.lexical(
                    "greets visitors", frozenset({SKILL_SUBJECT}), SKILL_OWNER_KIND, 10
                )
            )
            == 1
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(sa.select(user_skill.c.digest, user_skill.c.indexed_digest))
        ).one()
    assert row.indexed_digest == row.digest


async def test_row_deleted_mid_embed_gets_its_upsert_undone(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    index = DefaultIndex(transaction=workspace_tx)

    async def delete_row() -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(user_skill).where(
                    user_skill.c.workspace_id == workspace_id, user_skill.c.name == "greet"
                )
            )

    ctx = context_for(NAME, frozenset(), index, FirstCallEmbed(vec((0, 1.0)), delete_row))
    with ws(workspace_id), agent(agent_id):
        await UserSkillStore(ctx).save(
            "greet", {"SKILL.md": _skill_md("greet", "greets visitors politely")}, frozenset()
        )
    with ws(workspace_id):
        await index_skills(ctx)
    assert await _skill_chunk_count() == 0


async def test_one_failing_row_logs_and_the_tick_continues(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    index = DefaultIndex(transaction=workspace_tx)
    ctx = context_for(NAME, frozenset(), index, RefusingEmbed(vec((0, 1.0)), "poisoned"))
    store = UserSkillStore(ctx)
    with ws(workspace_id), agent(agent_id):
        await store.save(
            "aaa-bad", {"SKILL.md": _skill_md("aaa-bad", "the poisoned one")}, frozenset()
        )
        await store.save(
            "bbb-good", {"SKILL.md": _skill_md("bbb-good", "the healthy one")}, frozenset()
        )
    with ws(workspace_id):
        await index_skills(ctx)
    async with workspace_tx() as connection:
        rows = {
            row.name: row.indexed_digest
            for row in (
                await connection.execute(sa.select(user_skill.c.name, user_skill.c.indexed_digest))
            ).all()
        }
    assert rows["aaa-bad"] is None
    assert rows["bbb-good"] is not None


async def test_a_tick_that_settles_nothing_reports_its_failure(db: None) -> None:
    """A tick whose every stale card failed indexed nothing, so it fails rather than reporting
    success: each saved skill of that workspace stays unfindable until somebody acts, and a
    warning inside a job that returned cleanly reaches no monitor. The per-row tolerance above is
    untouched — it is what a tick with one healthy card keeps."""
    workspace_id, agent_id = await _workspace_agent()
    index = DefaultIndex(transaction=workspace_tx)
    ctx = context_for(NAME, frozenset(), index, RefusingEmbed(vec((0, 1.0)), "poisoned"))
    with ws(workspace_id), agent(agent_id):
        await UserSkillStore(ctx).save(
            "only-one", {"SKILL.md": _skill_md("only-one", "the poisoned one")}, frozenset()
        )
    with ws(workspace_id), pytest.raises(RuntimeError, match="embed refused the poisoned text"):
        await index_skills(ctx)
    async with workspace_tx() as connection:
        row = (await connection.execute(sa.select(user_skill.c.indexed_digest))).one()
    assert row.indexed_digest is None
    assert await _skill_chunk_count() == 0


async def test_index_body_is_bounded_next_to_the_embed_call(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    ctx, _ = _indexed_ext()
    oversized = "x" * (SKILL_INDEX_DESCRIPTION_MAX_CHARS + 5_000)
    with ws(workspace_id), agent(agent_id):
        await UserSkillStore(ctx).save(
            "giant", {"SKILL.md": _skill_md("giant", oversized)}, frozenset()
        )
    with ws(workspace_id):
        await index_skills(ctx)
    async with workspace_tx() as connection:
        lengths = (
            await connection.execute(
                sa.text("select length(text) from chunk where owner_kind = 'skill'")
            )
        ).scalars()
        row = (await connection.execute(sa.select(user_skill.c.description))).one()
    assert len(row.description) == len(oversized)
    assert all(length <= SKILL_INDEX_DESCRIPTION_MAX_CHARS + len("giant: ") for length in lengths)


async def test_concurrent_pinned_saves_cannot_exceed_the_cap(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo_ext_skill_create.store.MAX_PINNED_USER_SKILLS", 1)
    workspace_id, agent_id = await _workspace_agent()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        results = await asyncio.gather(
            store.save("p-one", {"SKILL.md": _skill_md("p-one", "1")}, frozenset(), pinned=True),
            store.save("p-two", {"SKILL.md": _skill_md("p-two", "2")}, frozenset(), pinned=True),
            return_exceptions=True,
        )
    refused = [result for result in results if isinstance(result, PinnedSkillLimit)]
    assert len(refused) == 1
    async with workspace_tx() as connection:
        pinned = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(user_skill)
                .where(
                    user_skill.c.workspace_id == workspace_id,
                    user_skill.c.pinned.is_(True),
                )
            )
        ).scalar_one()
    assert pinned == 1


def _object_tool(name: str) -> ToolDef:
    tools, _, _ = turn_tools((manifest(),), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


def _skill_manifest(
    name: str, files: dict[str, object], pinned: bool | None = None, generation: str | None = None
) -> str:
    spec: dict[str, object] = {"files": files}
    if pinned is not None:
        spec["pinned"] = pinned
    document: dict[str, object] = {"kind": SKILL_KIND, "name": name, "spec": spec}
    if generation is not None:
        document["generation"] = generation
    return yaml.safe_dump(document)


async def _dispatch(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
    assert result.is_error is False
    return result.content[0].text


def _tool_ctx(
    workspace_id: UUID,
    sandbox: SandboxSession | None,
    tmp_path,
    agent_id: UUID,
) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="save it",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="secret",
    )


async def _local_session(tmp_path) -> SandboxSession:
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref="ufo-sandbox:latest",
            workspace_host_path=str(tmp_path / "workspace"),
            proxy=ProxyEndpoint(port=9999, ca_cert="CA-PEM"),
            run_token="run-token",
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


async def test_reapplied_skill_updates_and_delete_removes(db: None, tmp_path) -> None:
    workspace_id, agent_id = await _workspace_agent()
    ctx = _tool_ctx(workspace_id, None, tmp_path, agent_id)
    apply = _object_tool("object_apply")
    inline = _skill_md("greet", "v1").decode()
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            ctx=ctx, tool=apply, manifest=_skill_manifest("greet", {"SKILL.md": inline})
        )
        second = json.loads(
            await _dispatch(
                ctx=ctx,
                tool=apply,
                manifest=_skill_manifest("greet", {"SKILL.md": _skill_md("greet", "v2").decode()}),
            )
        )
        assert second["result"] == "updated"
        assert [card.description for card in await _store().cards()] == ["v2"]
        deleted = json.loads(
            await _dispatch(_object_tool("object_delete"), ctx, kind=SKILL_KIND, name="greet")
        )
        assert deleted["deleted"] is True
        assert await _store().cards() == ()


async def test_reapply_keeps_files_by_digest_and_refuses_unknown(db: None, tmp_path) -> None:
    workspace_id, agent_id = await _workspace_agent()
    ctx = _tool_ctx(workspace_id, None, tmp_path, agent_id)
    apply = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            ctx=ctx,
            tool=apply,
            manifest=_skill_manifest(
                "greet",
                {"SKILL.md": _skill_md("greet", "v1").decode(), "assets/note.md": "inline note"},
            ),
        )
        note_ref = {"sha256": hashlib.sha256(b"inline note").hexdigest()}
        await _dispatch(
            ctx=ctx,
            tool=apply,
            manifest=_skill_manifest(
                "greet",
                {"SKILL.md": _skill_md("greet", "v2").decode(), "assets/note.md": note_ref},
            ),
        )
        loaded = await _store().materialize("greet")
        assert loaded is not None
        assert loaded.description == "v2"
        assert ("assets/note.md", b"inline note") in loaded.files
        stale = apply.input_model.model_validate(
            {
                "manifest": _skill_manifest(
                    "greet",
                    {
                        "SKILL.md": _skill_md("greet", "v3").decode(),
                        "assets/note.md": {"sha256": "0" * 64},
                    },
                ),
            }
        )
        with pytest.raises(ValueError, match="no stored content"):
            await apply.handler(ctx, stale)


async def test_apply_refuses_shadow_and_frontmatter_mismatch(db: None, tmp_path) -> None:
    workspace_id, agent_id = await _workspace_agent()
    ctx = _tool_ctx(workspace_id, None, tmp_path, agent_id)
    apply = _object_tool("object_apply")
    shadow = apply.input_model.model_validate(
        {
            "manifest": _skill_manifest(
                "sandbox", {"SKILL.md": _skill_md("sandbox", "d").decode()}
            ),
        }
    )
    mismatch = apply.input_model.model_validate(
        {
            "manifest": _skill_manifest("greet", {"SKILL.md": _skill_md("other", "d").decode()}),
        }
    )
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(SkillCollidesWithCoreSkill):
            await apply.handler(ctx, shadow)
        with pytest.raises(ValueError, match="must match"):
            await apply.handler(ctx, mismatch)
        assert await _store().cards() == ()


@pytest.mark.parametrize(
    "bad_key", ["../../notes.md", "assets/../../../notes.md", "/workspace/notes.md", "."]
)
async def test_apply_refuses_a_file_key_outside_the_skill(db: None, tmp_path, bad_key: str) -> None:
    """A saved skill's file keys are written into the workspace afresh by every later load, so a key
    that is not a path inside `$UFO_HOME/skills/<name>/` is refused where it would be persisted —
    `/workspace/notes.md` is still not this skill's to write."""
    workspace_id, agent_id = await _workspace_agent()
    ctx = _tool_ctx(workspace_id, None, tmp_path, agent_id)
    apply = _object_tool("object_apply")
    escaping = apply.input_model.model_validate(
        {
            "manifest": _skill_manifest(
                "greet",
                {"SKILL.md": _skill_md("greet", "greets people").decode(), bad_key: "planted"},
            ),
        }
    )
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(ValueError, match="not a path inside the skill"):
            await apply.handler(ctx, escaping)
        assert await _store().cards() == ()


async def test_apply_refuses_binary_and_missing_sources(db: None, tmp_path) -> None:
    workspace_id, agent_id = await _workspace_agent()
    session = await _local_session(tmp_path)
    await session.write_file("greet/SKILL.md", _skill_md("greet", "greets people"))
    await session.write_file("greet/logo.png", b"\x89PNG\r\n\x1a\n\xff\xfe")
    ctx = _tool_ctx(workspace_id, session, tmp_path, agent_id)
    apply = _object_tool("object_apply")
    binary = apply.input_model.model_validate(
        {
            "manifest": _skill_manifest(
                "greet",
                {"SKILL.md": {"from": "greet/SKILL.md"}, "logo.png": {"from": "greet/logo.png"}},
            ),
        }
    )
    missing = apply.input_model.model_validate(
        {
            "manifest": _skill_manifest("greet", {"SKILL.md": {"from": "greet/absent.md"}}),
        }
    )
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(ValueError, match="not text"):
            await apply.handler(ctx, binary)
        with pytest.raises(ValueError, match="no such file"):
            await apply.handler(ctx, missing)
        assert await _store().cards() == ()


async def test_apply_enforces_the_cap_but_allows_a_reapply(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo_ext_skill_create.store.MAX_USER_SKILLS_PER_WORKSPACE", 1)
    workspace_id, agent_id = await _workspace_agent()
    ctx = _tool_ctx(workspace_id, None, tmp_path, agent_id)
    apply = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            ctx=ctx,
            tool=apply,
            manifest=_skill_manifest("one", {"SKILL.md": _skill_md("one", "1").decode()}),
        )
        over = apply.input_model.model_validate(
            {
                "manifest": _skill_manifest("two", {"SKILL.md": _skill_md("two", "2").decode()}),
            }
        )
        with pytest.raises(TooManyUserSkills):
            await apply.handler(ctx, over)
        await _dispatch(
            ctx=ctx,
            tool=apply,
            manifest=_skill_manifest("one", {"SKILL.md": _skill_md("one", "1b").decode()}),
        )
        cards = await _store().cards()
    assert [(card.name, card.description) for card in cards] == [("one", "1b")]


async def test_skill_dispatch_and_the_seam_answer_one_workspace_set(db: None, tmp_path) -> None:
    """One name is one skill of the workspace: the agent that saved it holds no wall around it, a
    second agent lists it and gets it by name, and that agent's own apply of the name writes the
    same row rather than a second one."""
    workspace_id, first_agent = await _workspace_agent("first")
    second_agent = await _agent(workspace_id, "second")
    first = _tool_ctx(workspace_id, None, tmp_path, first_agent)
    second = _tool_ctx(workspace_id, None, tmp_path, second_agent)
    apply = _object_tool("object_apply")
    spec = manifest().member_skills
    assert spec is not None

    with ws(workspace_id), agent(first_agent):
        await _dispatch(
            apply,
            first,
            manifest=_skill_manifest(
                "greet", {"SKILL.md": _skill_md("greet", "first agent").decode()}
            ),
        )
        [first_card] = await spec.cards(_ext())

    with ws(workspace_id), agent(second_agent):
        listing = json.loads(await _dispatch(_object_tool("object_list"), second, kind=SKILL_KIND))
        assert [row["name"] for row in listing["objects"]] == ["greet"]
        fetched = yaml.safe_load(
            await _dispatch(_object_tool("object_get"), second, kind=SKILL_KIND, name="greet")
        )
        assert fetched["status"]["description"] == "first agent"
        await _dispatch(
            apply,
            second,
            manifest=_skill_manifest(
                "greet",
                {"SKILL.md": _skill_md("greet", "second agent").decode()},
                generation=fetched["generation"],
            ),
        )
        [second_card] = await spec.cards(_ext())

    assert first_card.description == "first agent"
    assert second_card.description == "second agent"
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(user_skill.c.name, user_skill.c.generation).where(
                    user_skill.c.workspace_id == workspace_id
                )
            )
        ).all()
    assert [row.name for row in rows] == ["greet"]
    assert all(row.generation is not None for row in rows)


async def test_portal_reads_answer_the_workspace_set_from_any_agent(db: None, tmp_path) -> None:
    """The portal reads the workspace's saved set, whichever agent the caller bound: the skill one
    agent saved lists and opens under a second one, and its record names no agent."""
    workspace_id, first_agent = await _workspace_agent("first")
    second_agent = await _agent(workspace_id, "second")
    ctx = _tool_ctx(workspace_id, None, tmp_path, first_agent)
    member_id = uuid4()
    store = SkillObjects()
    ext = context_for(NAME, frozenset())
    query = ObjectListQuery(supported_fields=SKILL_OBJECT.list_fields)
    with ws(workspace_id), agent(first_agent):
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=_skill_manifest(
                "greet", {"SKILL.md": _skill_md("greet", "first agent").decode()}
            ),
        )
        owner_rows = (
            await store.member_page(ext, member_id=member_id, admin=False, query=query)
        ).rows
        owner_read = await store.member_detail(ext, "greet", member_id=member_id, admin=False)
    with ws(workspace_id), agent(second_agent):
        other_rows = (
            await store.member_page(ext, member_id=member_id, admin=True, query=query)
        ).rows
        other_read = await store.member_detail(ext, "greet", member_id=member_id, admin=True)
    assert [(row.name, row.summary) for row in owner_rows] == [("greet", "first agent")]
    assert owner_read is not None
    assert owner_read.detail.links == ()
    assert [(row.name, row.summary) for row in other_rows] == [("greet", "first agent")]
    assert other_read is not None


async def test_index_job_runs_through_the_job_runner_on_stale_candidates(db: None) -> None:
    ws_with_work, agent_id = await _workspace_agent()
    ws_empty = await _workspace()
    ctx, index = _indexed_ext()
    with ws(ws_with_work), agent(agent_id):
        await UserSkillStore(ctx).save(
            "greet", {"SKILL.md": _skill_md("greet", "greets visitors politely")}, frozenset()
        )

    declared = manifest()
    job = next(job for job in declared.jobs if job.name == SKILL_INDEX_JOB)
    assert job.schedule == SKILL_INDEX_SCHEDULE
    candidates = await job.candidates()
    assert set(candidates) == {ws_with_work}
    assert ws_empty not in candidates

    bindings = bindings_from((declared,), ())
    index_key = f"{NAME}:{SKILL_INDEX_JOB}"
    keys = {binding.key for binding in bindings}
    assert index_key in keys
    assert f"{CORE_EXTENSION}:{SKILL_INDEX_JOB}" not in keys
    runner = JobRunner(
        bindings=bindings,
        manifests=(declared,),
        index=index,
        embed=StubEmbed(vec((0, 1.0))),
    )
    for workspace_id in await runner.candidates(index_key):
        await runner.fire(index_key, workspace_id)

    assert await job.candidates() == ()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(sa.select(user_skill.c.digest, user_skill.c.indexed_digest))
        ).one()
    assert row.indexed_digest == row.digest

    with ws(ws_with_work), agent(agent_id):
        store = UserSkillStore(ctx)
        await store.save(
            "greet",
            {"SKILL.md": _skill_md("greet", "sends farewells")},
            frozenset({"greet"}),
            generation=await _generation(store, "greet"),
        )
    assert set(await job.candidates()) == {ws_with_work}
