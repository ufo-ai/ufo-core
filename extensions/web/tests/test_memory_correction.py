"""The portal's memory-writing action lanes end to end: the memory view's correction and the first
run's picks. A row's correction posts `record_correction` on the main agent's lane, the turn
dispatches it verbatim — writing what chat's `memory_update` writes — so a new item lands under
the correcting member's own audience naming the corrected item in `source_ref`. The named item is
never edited or removed; both statements stand until the dedup sweep retires the near-duplicate
original toward the correction, the newest of the two, and a correction further away retires
nothing even then. The refusal polarities: a malformed or cross-paired intent is 400 before any
turn, a walled agent is not-found, and another member's private item is untouchable — a correction
naming it still writes only the corrector's own subject, invisible to the named item's owner. The
first run's picks ride the same lane to the same tool, recording what the team uses under the
picking member's own subject. The wiki's rebuild rides it too, to `rebuild_page_facts`: that intent
writes no memory at all — it clears the cursor the fact deriver rides, and the pass that owns those
rows writes them again."""

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory import manifest as memory_manifest_module
from ufo_ext_memory.condenser import DEDUP_MIN_AGE, MemoryDeduper
from ufo_ext_memory.store import MemoryIndexer, MemoryStore, MemoryWrite, memory_item
from ufo_ext_sources import manifest as sources_manifest_module
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.panels import FRAME_HEADER
from ufo_ext_web.surface import SESSION_COOKIE
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.host.assemble import HostEnvironment
from ufo.host.ext.loader import (
    member_object_registry,
    memory_search,
    skill_registry,
)
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import ScopedStore, context_for
from ufo.runtime.hub import InProcessHub
from ufo.runtime.indexing import TextChunker
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.turns.subjects import member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.serve import _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOKEN_SECRET = "web-token-secret"


@dataclass(frozen=True)
class StandInModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="echo")
        yield Usage(input_tokens=1, output_tokens=1)


STANDIN_REGISTRY = ModelRegistry(
    specs={
        spec.id: replace(spec, client=lambda spec, key: StandInModel(), key_slot="", key_env="")
        for spec in CORE_MODEL_SPECS
    },
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


BODY_VECTORS = {
    "the codename is bluebird": vec((0, 1.0)),
    "the codename is redwood": vec((0, 0.98), (1, 0.02)),
    "the standup is at 9am": vec((2, 1.0)),
    "the standup moved to a written thread and no longer meets": vec((3, 1.0)),
    "the launch is friday": vec((4, 1.0)),
    "the launch is monday": vec((5, 1.0)),
}
UNLISTED_VECTOR = vec((6, 1.0))


@dataclass(frozen=True)
class StubEmbed:
    """Fixes the vector of every body this module writes, so each correction sits deliberately
    inside or outside SUPERSEDE_COSINE of the item it names. A query string is unlisted and embeds
    orthogonally to all of them, leaving the lexical leg to answer the search."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(BODY_VECTORS.get(text, UNLISTED_VECTOR) for text in texts)


async def _seed_workspace() -> tuple[UUID, UUID]:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _seed_member(workspace_id: UUID, email: str) -> tuple[UUID, str]:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    token = mint_token(TOKEN_SECRET, str(workspace_id), email, timedelta(hours=1))
    return member_id, token


CORRECTION_MANIFESTS = (sources_manifest_module.manifest(), memory_manifest_module.manifest())


@pytest.fixture
def memory_runtime(
    dbos_launched: Config, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox]]:
    """A runtime whose manifests carry the memory extension, so an intent turn's tool table holds
    `memory_update` — over the session's one DBOS launch. Whatever runtime another module
    installed is restored afterward, because its turns run on it."""
    config = dbos_launched
    hub = InProcessHub()
    blob = FilesystemBlobStore(root=config.blob.root)
    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=tmp_path_factory.mktemp("workspaces"),
    )
    dbos_client = replay_safe_client(config.database.system_url)
    previous = loop_queue._runtime
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            sandboxes=sandboxes,
            hub=hub,
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(b"memory-correction-run-token"),
            dbos=dbos_client,
            invoker_for=invoker_factory(dbos_client),
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=CORRECTION_MANIFESTS,
            environment=HostEnvironment(
                manifests=CORRECTION_MANIFESTS,
                credentials=CredentialStore(fernet=Fernet(Fernet.generate_key())),
                index=DefaultIndex(transaction=workspace_tx),
                embed=StubEmbed(),
            ),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=CredentialStore(fernet=Fernet(Fernet.generate_key())),
            index=DefaultIndex(transaction=workspace_tx),
            embed=StubEmbed(),
            artifact_token_secret="",
        )
    )
    try:
        yield config, hub, blob, sandboxes
    finally:
        loop_queue.reset_runtime()
        if previous is not None:
            loop_queue.init_runtime(previous)
        dbos_client.destroy()


@pytest.fixture
async def memory_web(
    db: None,
    memory_runtime: tuple[Config, InProcessHub, FilesystemBlobStore, ConversationSandbox],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID, UUID]]:
    config, hub, blob, sandboxes = memory_runtime
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    dbos_client = replay_safe_client(config.database.system_url)
    workspace_id, agent_id = await _seed_workspace()
    manifests = (
        web_manifest(),
        memory_manifest_module.manifest(),
        sources_manifest_module.manifest(),
    )
    index = DefaultIndex(transaction=workspace_tx)
    embed = StubEmbed()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        manifests,
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
        memory=memory_search(manifests, None, index, embed),
        objects=member_object_registry(
            manifests, CredentialStore(fernet=Fernet(Fernet.generate_key())), index, embed
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        yield client, workspace_id, agent_id
    dbos_client.destroy()


async def _remember(workspace_id: UUID, subject: str, body: str) -> None:
    index = DefaultIndex(transaction=workspace_tx)
    embed = StubEmbed()
    with ws(workspace_id):
        extension = context_for("memory", frozenset(), index=index, embed=embed)
        store = MemoryStore(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            workspace_id=workspace_id,
            page_states=extension.page_states,
        )
        await store.commit(MemoryWrite(subject=subject, body=body))
        await _index(workspace_id)


async def _live_bodies(workspace_id: UUID) -> set[str]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(memory_item.c.body).where(
                    memory_item.c.workspace_id == workspace_id,
                    memory_item.c.superseded_by.is_(None),
                )
            )
        ).scalars()
    return set(rows)


async def _sweep(workspace_id: UUID, *bodies_oldest_first: str) -> None:
    """One dedup tick over the pair, with the named bodies aged into history in the order given: the
    sweep reads nothing under DEDUP_MIN_AGE and keeps the newest copy of a cluster, and `now()` is
    second-resolution on sqlite, so which of two rows written in one turn is newer is fixed here
    rather than left to the clock."""
    stamped = datetime.now(UTC) - DEDUP_MIN_AGE * 2
    async with workspace_tx() as connection:
        for offset, body in enumerate(bodies_oldest_first):
            await connection.execute(
                sa.update(memory_item)
                .values(created_at=stamped + timedelta(minutes=offset))
                .where(memory_item.c.workspace_id == workspace_id, memory_item.c.body == body)
            )
    with ws(workspace_id):
        await MemoryDeduper(
            embed=StubEmbed(),
            transaction=workspace_tx,
            workspace_id=workspace_id,
            store=ScopedStore(extension=memory_manifest_module.NAME),
        ).run()


async def _index(workspace_id: UUID) -> None:
    index = DefaultIndex(transaction=workspace_tx)
    embed = StubEmbed()
    with ws(workspace_id):
        await MemoryIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=context_for("memory", frozenset(), index=index, embed=embed).page_states,
        ).run()


def _correction(original_id: UUID, body: str) -> dict[str, object]:
    """A correction as the memory view submits it: the `record_correction` action's own input — the
    item corrected and the statement replacing it; the route's path names the collection and the
    action, and the write derives the rest."""
    return {"corrects": str(original_id), "body": body}


def _correction_path(agent_id: UUID) -> str:
    return f"/surface/web/agents/{agent_id}/actions/memory/record_correction"


async def test_a_correction_stands_beside_its_statement_until_the_sweep(
    memory_web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The whole lane: search finds the member's item, the correction intent applies, and the
    corrective item is re-readable through the same search projection — under the member's own
    subject, naming the corrected item in `source_ref`. The write derives nothing, so both
    statements are live and recallable the moment the turn lands; the dedup sweep is what retires
    the original, once the pair is old enough to be history, onto the correction as the newer of
    the two. The named row is neither edited nor removed either way: it keeps its id and body and
    gains only `superseded_by`, so the stale statement leaves recall while its provenance stands."""
    client, workspace_id, agent_id = memory_web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    await _remember(workspace_id, member_subject(member_id), "the codename is bluebird")
    found = await client.get("/surface/web/workspace/memory?q=codename", headers=cookie)
    [hit] = found.json()["matches"]
    assert hit["ref"].startswith("memory/")
    original_id = UUID(hit["ref"].removeprefix("memory/"))
    corrected = await client.post(
        _correction_path(agent_id),
        json=_correction(original_id, "the codename is redwood"),
        headers=cookie,
    )
    assert corrected.status_code == 200
    outcome = corrected.json()
    assert outcome["applied"] is True, outcome
    assert await _live_bodies(workspace_id) == {
        "the codename is bluebird",
        "the codename is redwood",
    }
    await _sweep(workspace_id, "the codename is bluebird", "the codename is redwood")
    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(
                        memory_item.c.id,
                        memory_item.c.body,
                        memory_item.c.subject,
                        memory_item.c.source_ref,
                        memory_item.c.superseded_by,
                    ).where(memory_item.c.workspace_id == workspace_id)
                )
            )
            .mappings()
            .all()
        )
        turn = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.inbound, tables.turn.c.status).where(
                        tables.turn.c.id == UUID(outcome["turn_id"])
                    )
                )
            )
            .mappings()
            .one()
        )
    by_body = {row["body"]: row for row in rows}
    original = by_body["the codename is bluebird"]
    correction = by_body["the codename is redwood"]
    assert len(rows) == 2
    assert original["id"] == original_id
    assert original["superseded_by"] == correction["id"]
    assert correction["subject"] == member_subject(member_id)
    assert correction["source_ref"] == f"corrects memory/{original_id}"
    assert turn["status"] == "done"
    inbound = json.loads(turn["inbound"])
    assert (inbound["tool"], inbound["input"]["action"]) == ("object_action", "record_correction")
    await _index(workspace_id)
    reread = await client.get("/surface/web/workspace/memory?q=codename", headers=cookie)
    texts = {match["text"] for match in reread.json()["matches"]}
    assert texts == {"the codename is redwood"}


async def test_a_correction_further_than_the_fence_leaves_both_live(
    memory_web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Supersede is a distance, not a lane: a correction whose body sits outside SUPERSEDE_COSINE of
    the item it names survives the sweep that reads the pair, and both statements stay recallable —
    a member who narrows a statement rather than restating it keeps what they narrowed."""
    client, workspace_id, agent_id = memory_web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    replacement = "the standup moved to a written thread and no longer meets"
    await _remember(workspace_id, member_subject(member_id), "the standup is at 9am")
    async with workspace_tx() as connection:
        original_id = (
            await connection.execute(
                sa.select(memory_item.c.id).where(memory_item.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    corrected = await client.post(
        _correction_path(agent_id),
        json=_correction(original_id, replacement),
        headers=cookie,
    )
    assert corrected.status_code == 200
    assert corrected.json()["applied"] is True
    await _sweep(workspace_id, "the standup is at 9am", replacement)
    assert await _live_bodies(workspace_id) == {"the standup is at 9am", replacement}
    await _index(workspace_id)
    reread = await client.get("/surface/web/workspace/memory?q=standup", headers=cookie)
    assert {match["text"] for match in reread.json()["matches"]} == {
        "the standup is at 9am",
        replacement,
    }


async def test_a_correction_never_touches_another_members_item(
    memory_web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """A correction naming another member's private item writes only the corrector's own subject:
    the named row is unchanged, and the named item's owner never sees the corrective text."""
    client, workspace_id, agent_id = memory_web
    owner_id, owner_token = await _seed_member(workspace_id, "owner@example.com")
    _other_id, other_token = await _seed_member(workspace_id, "other@example.com")
    await _remember(workspace_id, member_subject(owner_id), "the launch is friday")
    async with workspace_tx() as connection:
        original_id = (
            await connection.execute(
                sa.select(memory_item.c.id).where(memory_item.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    corrected = await client.post(
        _correction_path(agent_id),
        json=_correction(original_id, "the launch is monday"),
        headers={"cookie": f"{SESSION_COOKIE}={other_token}"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["applied"] is True
    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(memory_item.c.body, memory_item.c.subject).where(
                        memory_item.c.workspace_id == workspace_id
                    )
                )
            )
            .mappings()
            .all()
        )
    by_body = {row["body"]: row for row in rows}
    assert by_body["the launch is friday"]["subject"] == member_subject(owner_id)
    assert by_body["the launch is monday"]["subject"] == member_subject(_other_id)
    await _index(workspace_id)
    owner_view = await client.get(
        "/surface/web/workspace/memory?q=launch",
        headers={"cookie": f"{SESSION_COOKIE}={owner_token}"},
    )
    assert {match["text"] for match in owner_view.json()["matches"]} == {"the launch is friday"}


async def test_the_first_run_records_what_the_team_uses(
    memory_web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The first run's pick lane: the sentence the page writes from the tiles the member pressed
    dispatches the memory collection's `record_first_run` verbatim, so one item lands under their
    own subject naming the first run in `source_ref` with the body the page authored."""
    client, workspace_id, agent_id = memory_web
    member_id, token = await _seed_member(workspace_id, "owner@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    recorded = await client.post(
        f"/surface/web/agents/{agent_id}/actions/memory/record_first_run",
        json={"body": "My team uses Gmail, Notion."},
        headers=cookie,
    )
    assert recorded.status_code == 200
    outcome = recorded.json()
    assert outcome["applied"] is True, outcome
    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(
                        memory_item.c.body, memory_item.c.subject, memory_item.c.source_ref
                    ).where(memory_item.c.workspace_id == workspace_id)
                )
            )
            .mappings()
            .all()
        )
        turn = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).where(
                        tables.turn.c.id == UUID(outcome["turn_id"])
                    )
                )
            )
            .mappings()
            .one()
        )
    assert len(rows) == 1
    assert rows[0]["body"] == "My team uses Gmail, Notion."
    assert rows[0]["subject"] == member_subject(member_id)
    assert rows[0]["source_ref"] == "first run"
    inbound = json.loads(turn["inbound"])
    assert (inbound["tool"], inbound["input"]["action"]) == ("object_action", "record_first_run")


async def test_a_malformed_or_walled_correction_writes_nothing(
    memory_web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The refusal polarities before any turn: an action named on a kind that does not present it
    is refused, an apply on the memory kind is 400 at validation, a walled agent is not-found — and
    none of them writes a turn or an item."""
    client, workspace_id, agent_id = memory_web
    _member_id, token = await _seed_member(workspace_id, "owner@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    walled_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=walled_agent,
                workspace_id=workspace_id,
                name="ops",
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    correction = _correction(uuid4(), "corrected")
    cross_paired = await client.post(
        f"/surface/web/agents/{agent_id}/actions/agent/record_correction",
        json=correction,
        headers=cookie,
    )
    assert cross_paired.status_code == 200
    assert cross_paired.json() == {
        "applied": False,
        "message": "agent has no portal action named 'record_correction'.",
    }
    model_only = await client.post(
        f"/surface/web/agents/{agent_id}/actions/memory/memory_update",
        json={"body": "corrected"},
        headers=cookie,
    )
    assert model_only.json() == {
        "applied": False,
        "message": "memory has no portal action named 'memory_update'.",
    }
    apply_on_memory = await client.post(
        f"/surface/web/agents/{agent_id}/intents",
        json={"verb": "apply", "kind": "memory", "name": str(uuid4())},
        headers=cookie,
    )
    assert apply_on_memory.status_code == 400
    walled = await client.post(_correction_path(walled_agent), json=correction, headers=cookie)
    assert walled.status_code == 404
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
        ).scalar_one()
        items = (
            await connection.execute(sa.select(sa.func.count()).select_from(memory_item))
        ).scalar_one()
    assert turns == 0
    assert items == 0


async def test_the_wiki_rebuild_clears_the_derive_cursor_through_the_lane(
    memory_web: tuple[AsyncClient, UUID, UUID],
) -> None:
    """The rebuild's whole chain: the control is projected from the `page` kind's declarations — a
    kind the portal never lists for a member — the page's post is admitted as a turn, the turn
    dispatches `rebuild_page_facts` verbatim, and the cursor the fact deriver rides is gone — so the
    next tick replays every page and writes each one's facts again. The turn writes no memory
    itself, because the pass that owns that text is what writes it.

    The gate is the tool's, exercised through the lane the button uses: a member who is not an admin
    reads the tool's refusal and the cursor stands exactly where it was. The admin's press arrives
    marked as a page's — the wiki page carries this control — and the rebuild's declaration admits
    the mark."""
    client, workspace_id, agent_id = memory_web
    member_id, token = await _seed_member(workspace_id, "rebuilder@example.com")
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    cursor = ScopedStore(extension=memory_manifest_module.NAME)
    with ws(workspace_id):
        await cursor.put(memory_manifest_module.DERIVE_CURSOR_KEY, "2026-08-01T00:00:00+00:00|page")

    assert (await client.get("/surface/web/objects/page", headers=cookie)).status_code == 404
    projected = await client.get("/surface/web/actions/page", headers=cookie)
    assert projected.status_code == 200, projected.text
    [view] = [v for v in projected.json()["actions"] if v["name"] == "rebuild_page_facts"]
    assert view["label"]
    assert view["call"] == {"kind": "page", "action": "rebuild_page_facts", "input": {}}

    rebuild = f"/surface/web/agents/{agent_id}/actions/page/rebuild_page_facts"
    refused = await client.post(rebuild, json={}, headers=cookie)
    assert refused.status_code == 200
    assert refused.json()["applied"] is False
    assert refused.json()["message"] == memory_manifest_module.REBUILD_ADMIN_ONLY
    with ws(workspace_id):
        assert await cursor.get(memory_manifest_module.DERIVE_CURSOR_KEY) is not None

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).where(tables.member.c.id == member_id).values(is_admin=True)
        )
    queued = await client.post(rebuild, json={}, headers={**cookie, FRAME_HEADER: "1"})
    assert queued.status_code == 200
    assert queued.json()["applied"] is True
    assert queued.json()["message"] == memory_manifest_module.REBUILD_QUEUED
    with ws(workspace_id):
        assert await cursor.get(memory_manifest_module.DERIVE_CURSOR_KEY) is None
    async with workspace_tx() as connection:
        assert (
            await connection.execute(sa.select(sa.func.count()).select_from(memory_item))
        ).scalar_one() == 0
        inbound = (
            await connection.execute(
                sa.select(tables.turn.c.inbound).where(
                    tables.turn.c.id == UUID(queued.json()["turn_id"])
                )
            )
        ).scalar_one()
    assert inbound == (
        '{"tool":"object_action","input":{"kind":"page","action":"rebuild_page_facts","input":{}}}'
    )
