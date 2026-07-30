"""The portal's per-agent read projections: scheduled tasks shaped by viewer, the agent's skills,
memory search under the viewer's own subjects, and per-agent spend — every route gated by the web
audience exactly like the chat routes, so an out-of-audience agent is not-found everywhere.

The panels run no turns, so the app mounts without the loop runtime: real DB, real extension
stores (ScheduleStore, UserSkillStore, the memory extension's provider over the default index) —
the dependencies are real, only the DBOS client is a stand-in nothing here dispatches through."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest_module
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MemoryIndexer, MemoryStore, MemoryWrite, mem_page
from ufo_ext_skill_create.manifest import manifest as skill_create_manifest
from ufo_ext_skill_create.store import UserSkillStore
from ufo_ext_web.audience import AUDIENCE_PREFIX, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.surface import MAX_USAGE_WINDOW_SECONDS

from ufo.accounting import record_egress_request, record_sandbox_tokens, record_turn_usage
from ufo.agent_scope import agent as bind_agent
from ufo.bearer import mint_token
from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import memory_search, skill_registry, turn_runtime_skills
from ufo.hub import InProcessHub
from ufo.indexing import TextChunker
from ufo.models.catalog import CORE_PRICING
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.scheduling import ScheduleStore
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, Usage
from ufo.sdk.index import OWNER_KIND_PAGE, Chunk
from ufo.serve import _mount_shared_surfaces
from ufo.skills.runtime import RuntimeSkill
from ufo.subjects import member_subject
from ufo.workspace import ws

TOKEN_SECRET = "web-token-secret"
SESSION_COOKIE = "ufo_session"
ADMIN_EMAIL = "admin@example.com"
CREATOR_EMAIL = "creator@example.com"
OTHER_EMAIL = "other@example.com"
NEXT_RUN = datetime(2027, 1, 1, tzinfo=UTC)
LAST_RUN = datetime(2026, 12, 25, 9, 0, tzinfo=UTC)
EXPIRES = datetime(2027, 6, 1, tzinfo=UTC)


async def _stamp_last_run(workspace_id: UUID, name: str, moment: datetime) -> None:
    """Stamp a fire's timing mark straight onto the row — the projection's read half is what the
    test pins; the write half (`ScheduleStore.reschedule`) needs a claimed task and has its own
    proofs in the scheduler's suite."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.scheduled_task)
            .values(last_run_at=moment)
            .where(
                tables.scheduled_task.c.workspace_id == workspace_id,
                tables.scheduled_task.c.name == name,
            )
        )


class StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the panel routes admit nothing")


class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vector = [0.0] * EMBED_DIM
        vector[0] = 1.0
        return tuple(tuple(vector) for _ in texts)


async def _seed_workspace() -> tuple[UUID, UUID, UUID]:
    workspace_id, agent_a, agent_b = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for agent_id, name, main in ((agent_a, "assistant", True), (agent_b, "ops", False)):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="be brief",
                    model="claude-opus-4-8",
                    is_main=main,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id, agent_a, agent_b


async def _seed_member(
    workspace_id: UUID, email: str, *, admin: bool = False
) -> tuple[UUID, dict[str, str]]:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                is_admin=admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    token = mint_token(TOKEN_SECRET, str(workspace_id), email, timedelta(hours=1))
    return member_id, {"cookie": f"{SESSION_COOKIE}={token}"}


async def _grant(workspace_id: UUID, agent_id: UUID, email: str) -> None:
    with ws(workspace_id):
        await web_extension().store.put(
            f"{AUDIENCE_PREFIX}{agent_id}/{email}", {"granted_by": str(uuid4())}
        )


async def _seed_conversation(workspace_id: UUID, agent_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=uuid4().hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


CHILD_SKILL = RuntimeSkill(
    name="delegation/probe",
    description="a child reached through its parent, never the index",
    instructions="probe",
    parent="delegation",
)
DEPLOY_SKILLS = skill_registry((web_manifest(), memory_manifest_module.manifest()), (CHILD_SKILL,))


def _mount_portal(tmp_path: Path, *, with_memory: bool) -> FastAPI:
    index = DefaultIndex(transaction=workspace_tx)
    embed = StubEmbed()
    manifests = (web_manifest(), skill_create_manifest(), memory_manifest_module.manifest())
    credentials = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        manifests,
        credentials,
        FilesystemBlobStore(root=tmp_path),
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        StubDbos(),
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        skills=DEPLOY_SKILLS,
        user_skills=lambda: turn_runtime_skills(manifests, credentials, index, embed),
        memory=memory_search(manifests, None, index, embed) if with_memory else None,
    )
    return app


@pytest.fixture
async def portal(db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, agent_a, agent_b = await _seed_workspace()
    app = _mount_portal(tmp_path, with_memory=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        yield client, workspace_id, agent_a, agent_b


async def test_tasks_shape_by_viewer_and_wall_by_agent(portal) -> None:
    client, workspace_id, agent_a, agent_b = portal
    _admin_id, admin_headers = await _seed_member(workspace_id, ADMIN_EMAIL, admin=True)
    creator_id, creator_headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    _other_id, other_headers = await _seed_member(workspace_id, OTHER_EMAIL)
    with ws(workspace_id):
        conversation_id = await _seed_conversation(workspace_id, agent_a)
        with bind_agent(agent_a):
            await ScheduleStore().create(
                conversation_id,
                "daily-brief",
                "0 9 * * *",
                "write the daily brief",
                "daily brief",
                NEXT_RUN,
                created_by_member_id=creator_id,
                expires_at=EXPIRES,
            )
            await ScheduleStore().create(
                conversation_id,
                "creatorless-sweep",
                "0 3 * * *",
                "sweep the queue",
                "queue sweep",
                NEXT_RUN,
            )
        await _stamp_last_run(workspace_id, "daily-brief", LAST_RUN)
    creator_view = await client.get(f"/surface/web/agents/{agent_a}/tasks", headers=creator_headers)
    (task,) = creator_view.json()["tasks"]
    assert task["name"] == "daily-brief"
    assert task["prompt"] == "write the daily brief"
    assert task["created_by"] == CREATOR_EMAIL
    assert task["next_run_at"] == NEXT_RUN.isoformat()
    assert task["last_run_at"] == LAST_RUN.isoformat()
    assert task["expires_at"] == EXPIRES.isoformat()
    admin_view = await client.get(f"/surface/web/agents/{agent_a}/tasks", headers=admin_headers)
    by_name = {row["name"]: row for row in admin_view.json()["tasks"]}
    management = by_name["daily-brief"]
    assert management["prompt"] is None
    assert management["description"] is None
    assert management["schedule"] == "0 9 * * *"
    assert management["created_by"] == CREATOR_EMAIL
    creatorless = by_name["creatorless-sweep"]
    assert creatorless["prompt"] == "sweep the queue"
    assert creatorless["created_by"] is None
    other_view = await client.get(f"/surface/web/agents/{agent_a}/tasks", headers=other_headers)
    assert other_view.json() == {"tasks": []}
    crossed = await client.get(f"/surface/web/agents/{agent_b}/tasks", headers=creator_headers)
    assert crossed.status_code == 404
    assert (await client.get(f"/surface/web/agents/{agent_b}/tasks")).status_code == 401
    empty_wall = await client.get(f"/surface/web/agents/{agent_b}/tasks", headers=admin_headers)
    assert empty_wall.json() == {"tasks": []}


async def test_skills_list_the_agents_own_and_the_deploys(portal) -> None:
    client, workspace_id, agent_a, agent_b = portal
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    skill_md = (
        b"---\nname: release-notes\ndescription: How this team writes release notes.\n---\n"
        b"Write tersely."
    )
    with ws(workspace_id), bind_agent(agent_a):
        await UserSkillStore(ctx=context_for("skill_create", frozenset())).save(
            "release-notes", {"SKILL.md": skill_md}, frozenset()
        )
    mine = await client.get(f"/surface/web/agents/{agent_a}/skills", headers=headers)
    listed = mine.json()["skills"]
    with ws(workspace_id), bind_agent(agent_a):
        saved = await UserSkillStore(ctx=context_for("skill_create", frozenset())).load_all()
        expected = DEPLOY_SKILLS.merged_with(saved).index()
    assert [(skill["name"], skill["description"]) for skill in listed] == list(expected)
    by_name = {skill["name"]: skill for skill in listed}
    assert by_name["release-notes"]["origin"] == "member"
    assert all(skill["origin"] == "deploy" for skill in listed if skill["name"] != "release-notes")
    assert CHILD_SKILL.name in DEPLOY_SKILLS.by_name
    assert CHILD_SKILL.name not in by_name
    assert by_name["memory"]["origin"] == "deploy"
    theirs = (await client.get(f"/surface/web/agents/{agent_b}/skills", headers=headers)).json()
    assert [skill for skill in theirs["skills"] if skill["origin"] == "member"] == []
    assert [(skill["name"], skill["description"]) for skill in theirs["skills"]] == list(
        DEPLOY_SKILLS.index()
    )
    assert CHILD_SKILL.name not in {skill["name"] for skill in theirs["skills"]}


async def test_memory_search_stays_inside_the_viewers_subjects(portal, tmp_path: Path) -> None:
    client, workspace_id, agent_a, _agent_b = portal
    member_a, headers_a = await _seed_member(workspace_id, CREATOR_EMAIL)
    member_b, _headers_b = await _seed_member(workspace_id, OTHER_EMAIL)
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
        for member_id, body in (
            (member_a, "the launch codename is bluebird"),
            (member_b, "the launch codename is redwood"),
        ):
            await store.commit(MemoryWrite(subject=member_subject(member_id), body=body))
        await MemoryIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=context_for("memory", frozenset(), index=index, embed=embed).page_states,
        ).run()
    found = await client.get(
        f"/surface/web/agents/{agent_a}/memory?q=launch codename", headers=headers_a
    )
    payload = found.json()
    assert payload["available"] is True
    texts = " ".join(match["text"] for match in payload["matches"])
    assert "bluebird" in texts
    assert "redwood" not in texts
    assert all(
        match["ref"] is not None and match["ref"].startswith("memory/")
        for match in payload["matches"]
    )
    async with workspace_tx() as connection:
        committed_at = (
            await connection.execute(
                sa.text("select created_at from memory_item where body like '%bluebird%'")
            )
        ).scalar_one()
    stamped = (
        committed_at if isinstance(committed_at, datetime) else datetime.fromisoformat(committed_at)
    )
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    assert [match["created_at"] for match in payload["matches"]] == [
        stamped.astimezone(UTC).isoformat()
    ]
    assert [match["kind"] for match in payload["matches"]] == ["fact"]
    blank = await client.get(f"/surface/web/agents/{agent_a}/memory", headers=headers_a)
    assert blank.json() == {"available": True, "matches": []}


async def test_memory_panel_fences_source_pages_by_agent_grant(portal) -> None:
    """The reader is authority, not navigation: a source-derived page answers the panel only under
    an agent granted its source — or, with no grant, only to the source's own member under the
    main agent — and the page hit's timestamp rides the wire aware like every other mark."""
    client, workspace_id, agent_a, agent_b = portal
    member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    _other_id, other_headers = await _seed_member(workspace_id, OTHER_EMAIL)
    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    page_id, source_id = uuid4(), uuid4()
    minted_at = datetime(2026, 7, 1, 8, 30, tzinfo=UTC)
    vector = [0.0] * EMBED_DIM
    vector[0] = 1.0
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="test",
                config={},
                subject="shared",
                owner_member_id=member_id,
                next_sync_at=minted_at,
                created_at=minted_at,
                updated_at=minted_at,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="d" * 64,
                body_ref=f"pages/{page_id}",
                stream="notes",
                title="Runway",
                subject="shared",
                tombstone=False,
                created_at=minted_at,
                updated_at=minted_at,
            )
        )
        revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(mem_page).values(
                page_id=page_id,
                workspace_id=workspace_id,
                subject="shared",
                revision=revision,
                created_at=minted_at,
            )
        )
        await connection.execute(
            sa.insert(tables.source_grant).values(
                workspace_id=workspace_id,
                source_id=source_id,
                agent_id=agent_a,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        await DefaultIndex(transaction=workspace_tx).upsert(
            (
                Chunk(
                    "p-" + page_id.hex,
                    OWNER_KIND_PAGE,
                    str(page_id),
                    "shared",
                    0,
                    "the launch runway is painted teal",
                    tuple(vector),
                ),
            )
        )

    def page_refs(payload: dict) -> list[dict]:
        return [match for match in payload["matches"] if match["ref"] == f"page/{page_id}"]

    granted = (
        await client.get(f"/surface/web/agents/{agent_a}/memory?q=runway painted", headers=headers)
    ).json()
    [hit] = page_refs(granted)
    assert hit["kind"] == "source"
    assert hit["created_at"] == minted_at.isoformat()
    ungranted = (
        await client.get(f"/surface/web/agents/{agent_b}/memory?q=runway painted", headers=headers)
    ).json()
    assert page_refs(ungranted) == []
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.source_grant).where(tables.source_grant.c.source_id == source_id)
        )
    owner_on_main = (
        await client.get(f"/surface/web/agents/{agent_a}/memory?q=runway painted", headers=headers)
    ).json()
    assert len(page_refs(owner_on_main)) == 1
    stranger_on_main = (
        await client.get(
            f"/surface/web/agents/{agent_a}/memory?q=runway painted", headers=other_headers
        )
    ).json()
    assert page_refs(stranger_on_main) == []


async def test_a_memoryless_deploy_never_claims_availability(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, agent_a, _agent_b = await _seed_workspace()
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    app = _mount_portal(tmp_path, with_memory=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        blank = await client.get(f"/surface/web/agents/{agent_a}/memory", headers=headers)
        assert blank.json() == {"available": False, "matches": []}
        queried = await client.get(
            f"/surface/web/agents/{agent_a}/memory?q=anything", headers=headers
        )
        assert queried.json() == {"available": False, "matches": []}


async def test_usage_sums_only_the_selected_agents_ledger(portal) -> None:
    """The granted agent's whole ledger, windowed, beside its agent-scoped caps — and the wall:
    the main agent answers the member's chat but not its ledger, because no grant holds it."""
    client, workspace_id, agent_a, agent_b = portal
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    _admin_id, admin_headers = await _seed_member(workspace_id, ADMIN_EMAIL, admin=True)
    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    async with workspace_tx() as connection:
        rich_conversation: UUID | None = None
        for agent_id, tokens in ((agent_b, 1000), (agent_a, 7777)):
            conversation_id, turn_id = uuid4(), uuid4()
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface="web",
                    queue_key=uuid4().hex,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
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
                    inbound="x",
                    terminal=TerminalFrame(status="done").model_dump(mode="json"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await record_turn_usage(
                connection,
                workspace_id,
                turn_id,
                "claude-opus-4-8",
                Usage(input_tokens=tokens, output_tokens=2000),
            )
            if agent_id == agent_b:
                rich_conversation = conversation_id
                await record_egress_request(connection, workspace_id, turn_id)
                await record_sandbox_tokens(
                    connection,
                    workspace_id,
                    turn_id,
                    "claude-opus-4-8",
                    Usage(input_tokens=100, output_tokens=100),
                )
        assert rich_conversation is not None
        second_turn, mid_turn, stale_turn = uuid4(), uuid4(), uuid4()
        for seq, turn_id, usage in (
            (2, second_turn, Usage(input_tokens=500, output_tokens=1500)),
            (3, mid_turn, Usage(input_tokens=22, output_tokens=44)),
            (4, stale_turn, Usage(input_tokens=11, output_tokens=22)),
        ):
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=workspace_id,
                    conversation_id=rich_conversation,
                    agent_id=agent_b,
                    seq=seq,
                    status="done",
                    inbound="x",
                    terminal=TerminalFrame(status="done").model_dump(mode="json"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", usage)
        for turn_id, age in ((mid_turn, timedelta(hours=2)), (stale_turn, timedelta(days=2))):
            await connection.execute(
                sa.update(tables.ledger)
                .values(created_at=datetime.now(UTC) - age)
                .where(tables.ledger.c.turn_id == turn_id)
            )
        for scope, subject, window in (
            ("agent", agent_b, 86_400),
            ("agent", agent_b, 3_600),
            ("agent", agent_a, 86_400),
            ("member", agent_b, 86_400),
            ("member", _member_id, 86_400),
            ("workspace", None, 86_400),
        ):
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope=scope,
                    subject_id=subject,
                    window_seconds=window,
                    limit_micro_usd=5_000_000,
                    on_breach="park",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    priced = {
        tokens: CORE_PRICING.micro_usd("claude-opus-4-8", usage)
        for tokens, usage in (
            (3000, Usage(input_tokens=1000, output_tokens=2000)),
            (2000, Usage(input_tokens=500, output_tokens=1500)),
            (66, Usage(input_tokens=22, output_tokens=44)),
        )
    }
    mine = await client.get(f"/surface/web/agents/{agent_b}/usage", headers=headers)
    report = mine.json()
    lines = {line["dimension"]: line for line in report["by_dimension"]}
    assert report["window_seconds"] == 86_400
    assert set(lines) == {"egress", "sandbox_tokens", "tokens"}
    assert lines["tokens"]["amount"] == 3000 + 2000 + 66
    assert lines["tokens"]["priced_micro_usd"] == priced[3000] + priced[2000] + priced[66]
    assert lines["egress"]["amount"] == 1
    assert lines["sandbox_tokens"]["amount"] == 200
    assert lines["sandbox_tokens"]["priced_micro_usd"] > 0
    assert report["total_micro_usd"] == sum(line["priced_micro_usd"] for line in lines.values())
    hour = (
        await client.get(
            f"/surface/web/agents/{agent_b}/usage?window_seconds=3600", headers=headers
        )
    ).json()
    hour_lines = {line["dimension"]: line for line in hour["by_dimension"]}
    assert hour["window_seconds"] == 3_600
    assert hour_lines["tokens"]["amount"] == 3000 + 2000
    assert hour_lines["tokens"]["priced_micro_usd"] == priced[3000] + priced[2000]
    assert hour["total_micro_usd"] < report["total_micro_usd"]
    assert report["caps"] == [
        {"window_seconds": 3_600, "limit_micro_usd": 5_000_000, "on_breach": "park"},
        {"window_seconds": 86_400, "limit_micro_usd": 5_000_000, "on_breach": "park"},
    ]
    assert report["workspace_spend"] is False
    admin_report = await client.get(f"/surface/web/agents/{agent_b}/usage", headers=admin_headers)
    assert admin_report.json()["workspace_spend"] is True
    async with workspace_tx() as connection:
        workspace_tokens = (
            await connection.execute(
                sa.select(sa.func.sum(tables.ledger.c.amount)).where(
                    tables.ledger.c.workspace_id == workspace_id,
                    tables.ledger.c.dimension == "tokens",
                )
            )
        ).scalar_one()
    assert int(workspace_tokens) == 3000 + 2000 + 66 + 33 + 9777
    walled = await client.get(f"/surface/web/agents/{agent_a}/usage", headers=headers)
    assert walled.status_code == 404
    for bad_window in ("abc", "-5", "0", str(MAX_USAGE_WINDOW_SECONDS + 1), "9" * 30):
        refused = await client.get(
            f"/surface/web/agents/{agent_b}/usage?window_seconds={bad_window}", headers=headers
        )
        assert refused.status_code == 400
        rollup_refused = await client.get(
            f"/surface/web/spend?window_seconds={bad_window}", headers=admin_headers
        )
        assert rollup_refused.status_code == 400


async def test_usage_answers_empty_for_a_spend_free_agent(portal) -> None:
    """Both empty halves of the agent view: an agent with no ledger rows and no caps reports a
    zero window and two empty lists rather than erroring or borrowing workspace rows."""
    client, workspace_id, _agent_a, agent_b = portal
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    report = (await client.get(f"/surface/web/agents/{agent_b}/usage", headers=headers)).json()
    assert report["total_micro_usd"] == 0
    assert report["by_dimension"] == []
    assert report["caps"] == []
