"""The portal's per-agent read projections: scheduled tasks shaped by viewer, the agent's skills,
and memory search under the viewer's own subjects — every route gated by the web audience exactly
like the chat routes, so an out-of-audience agent is not-found everywhere.

The panels run no turns, so the app mounts without the loop runtime: real DB, real extension
stores (ScheduleStore, UserSkillStore, the memory extension's provider over the default index) —
the dependencies are real, only the DBOS client is a stand-in nothing here dispatches through."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest_module
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.manifest import MemoryUpdateInput
from ufo_ext_memory.store import (
    MEMORY_BODY_MAX_CHARS,
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    mem_page,
    memory_item,
)
from ufo_ext_scheduled_tasks.manifest import NAME as SCHEDULED_TASKS_NAME
from ufo_ext_scheduled_tasks.schedules import ScheduleStore
from ufo_ext_scheduled_tasks.tools import (
    PRIVATE_PROMPT,
    PROMPT_EXCERPT_MAX,
    SCHEDULED_TASK_OBJECT,
    SUMMARY_MAX,
)
from ufo_ext_skill_create.manifest import manifest as skill_create_manifest
from ufo_ext_skill_create.store import UserSkillStore
from ufo_ext_web import surface as web_surface
from ufo_ext_web.audience import AUDIENCE_PREFIX, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.panels import FIRST_RUN_PROVIDERS, TOOLING_PREFIX, _tools_recorded
from ufo_ext_web.surface import MEMORY_RECENT_LIMIT
from ufo_testsupport.surfaces import UNREACHED_AMBIENT_REPLY

from ufo.agent_scope import agent as bind_agent
from ufo.audience import conversation_audience
from ufo.bearer import mint_token
from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import (
    member_object_registry,
    member_skill_listing,
    memory_search,
    skill_registry,
)
from ufo.hub import InProcessHub
from ufo.indexing import TextChunker
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.sdk.index import OWNER_KIND_PAGE, Chunk
from ufo.sdk.manifest import Manifest
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
EXPIRES = datetime(2027, 6, 1, tzinfo=UTC)


def _schedule_store() -> ScheduleStore:
    """The relocated store, which now reads its workspace and object agent off the context the
    extension's own callers hand it."""
    return ScheduleStore(context_for(SCHEDULED_TASKS_NAME, frozenset()))


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
                    visibility="workspace" if main else "private",
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


async def _seed_conversation(
    workspace_id: UUID, agent_id: UUID, member_id: UUID | None = None
) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=uuid4().hex,
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


CHILD_SKILL = RuntimeSkill(
    name="sandbox/probe",
    description="a child reached through its parent, never the index",
    instructions="probe",
    parent="sandbox",
)
DEPLOY_ONLY_SKILL = RuntimeSkill(
    name="deploy-probe",
    description="a deploy skill the core default does not carry",
    instructions="probe",
)
DEPLOY_SKILLS = skill_registry(
    (web_manifest(), memory_manifest_module.manifest()), (CHILD_SKILL, DEPLOY_ONLY_SKILL)
)


SCHEDULED_TASK_KIND_ONLY = Manifest(
    name="scheduled_tasks",
    version="0.1.0",
    objects=(SCHEDULED_TASK_OBJECT,),
)


def _mount_portal(tmp_path: Path, *, with_memory: bool) -> FastAPI:
    index = DefaultIndex(transaction=workspace_tx)
    embed = StubEmbed()
    manifests = (
        web_manifest(),
        skill_create_manifest(),
        SCHEDULED_TASK_KIND_ONLY,
        memory_manifest_module.manifest(),
    )
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
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=DEPLOY_SKILLS,
        member_skill_listing=lambda: member_skill_listing(manifests, credentials, index, embed),
        objects=member_object_registry(manifests),
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


async def test_task_pages_shape_by_viewer_and_wall_by_agent(portal) -> None:
    """The task index and detail carry exactly what the scheduled_task kind discloses: a task
    reporting into a shared conversation is read whole by every member, since its replies land
    there for all of them anyway; a task reporting into one member's own conversation is that
    member's alone, reaching an admin as a management row with its content elided (`spec` null,
    the summary saying so) and no one else at all. A task no member created is read the same way —
    by where it reports, so the private conversation elides it from the admin too. Each read is
    walled by the agent named on it. The row's `prompt` is the task's own prompt, whole — past
    both the summary's line and the excerpt a turn's own read is bounded to: a member's screen
    holds its own length, and a cut made here is one it cannot undo."""
    client, workspace_id, agent_a, agent_b = portal
    _admin_id, admin_headers = await _seed_member(workspace_id, ADMIN_EMAIL, admin=True)
    creator_id, creator_headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    _other_id, other_headers = await _seed_member(workspace_id, OTHER_EMAIL)
    with ws(workspace_id):
        conversation_id = await _seed_conversation(workspace_id, agent_a, creator_id)
        with bind_agent(agent_a):
            await _schedule_store().create(
                conversation_id,
                "daily-brief",
                "0 9 * * *",
                "write the daily brief",
                "daily brief",
                NEXT_RUN,
                created_by_member_id=creator_id,
                expires_at=EXPIRES,
            )
            await _schedule_store().create(
                conversation_id,
                "creatorless-sweep",
                "0 3 * * *",
                "sweep the queue",
                "queue sweep",
                NEXT_RUN,
            )
    index = f"/surface/web/objects/scheduled_task?agent={agent_a}"
    creator_view = await client.get(index, headers=creator_headers)
    assert set(creator_view.json()["spec_schema"]["properties"]) == {
        "schedule",
        "prompt",
        "description",
        "expires_at",
        "paused",
    }
    (row,) = creator_view.json()["objects"]
    assert row["name"] == "daily-brief"
    assert row["paused"] is False
    assert row["next_run_at"] == NEXT_RUN.isoformat()
    assert row["summary"] == "0 9 * * * — daily brief"
    assert row["prompt"] == "write the daily brief"
    assert (row["agent_id"], row["agent_name"]) == (str(agent_a), "assistant")
    mine = await client.get(
        f"/surface/web/objects/scheduled_task/daily-brief?agent={agent_a}", headers=creator_headers
    )
    assert mine.json()["spec"]["prompt"] == "write the daily brief"
    assert mine.json()["spec"]["expires_at"].startswith("2027-06-01T00:00:00")

    admin_view = await client.get(index, headers=admin_headers)
    by_name = {row["name"]: row for row in admin_view.json()["objects"]}
    assert by_name["daily-brief"]["summary"] == "0 9 * * * — private member task"
    assert by_name["creatorless-sweep"]["summary"] == "0 3 * * * — private member task"
    assert by_name["daily-brief"]["prompt"] == PRIVATE_PROMPT
    assert by_name["creatorless-sweep"]["prompt"] == PRIVATE_PROMPT
    management = await client.get(
        f"/surface/web/objects/scheduled_task/daily-brief?agent={agent_a}", headers=admin_headers
    )
    assert management.json()["spec"] is None
    assert management.json()["status"]["next_run_at"] == NEXT_RUN.isoformat()
    creatorless = await client.get(
        f"/surface/web/objects/scheduled_task/creatorless-sweep?agent={agent_a}",
        headers=admin_headers,
    )
    assert creatorless.json()["spec"] is None
    assert creatorless.json()["status"]["next_run_at"] == NEXT_RUN.isoformat()

    other_view = await client.get(index, headers=other_headers)
    assert other_view.json()["objects"] == []
    assert (
        await client.get(
            f"/surface/web/objects/scheduled_task/daily-brief?agent={agent_a}",
            headers=other_headers,
        )
    ).status_code == 404

    with ws(workspace_id):
        shared_conversation = await _seed_conversation(workspace_id, agent_a)
        with bind_agent(agent_a):
            await _schedule_store().create(
                shared_conversation,
                "channel-digest",
                "0 8 * * *",
                "post the channel digest",
                "channel digest",
                NEXT_RUN,
                created_by_member_id=creator_id,
            )
    shared_view = await client.get(index, headers=other_headers)
    (shared_row,) = shared_view.json()["objects"]
    assert shared_row["name"] == "channel-digest"
    assert shared_row["summary"] == "0 8 * * * — channel digest"
    read = await client.get(
        f"/surface/web/objects/scheduled_task/channel-digest?agent={agent_a}", headers=other_headers
    )
    assert read.json()["spec"]["prompt"] == "post the channel digest"

    sprawling = "Weekly customer and prospect signal digest, proposals only. " * 6
    sprawling_prompt = "Post the long digest, then thread the open proposals under it. " * 8
    assert len(sprawling_prompt) > PROMPT_EXCERPT_MAX > SUMMARY_MAX
    with ws(workspace_id):
        with bind_agent(agent_a):
            await _schedule_store().create(
                shared_conversation,
                "long-digest",
                "0 7 * * *",
                sprawling_prompt,
                sprawling,
                NEXT_RUN,
                created_by_member_id=creator_id,
            )
    sprawled = await client.get(
        f"/surface/web/objects/scheduled_task/long-digest?agent={agent_a}", headers=other_headers
    )
    assert sprawled.status_code == 200
    assert sprawled.json()["spec"]["description"] == sprawling
    listed = await client.get(index, headers=other_headers)
    sprawled_row = next(row for row in listed.json()["objects"] if row["name"] == "long-digest")
    assert len(sprawled_row["summary"]) == SUMMARY_MAX
    assert sprawled_row["prompt"] == sprawling_prompt

    crossed = await client.get(
        f"/surface/web/objects/scheduled_task?agent={agent_b}", headers=creator_headers
    )
    assert crossed.status_code == 404
    assert (
        await client.get(f"/surface/web/objects/scheduled_task?agent={agent_b}")
    ).status_code == 401
    empty_wall = await client.get(
        f"/surface/web/objects/scheduled_task?agent={agent_b}", headers=admin_headers
    )
    assert empty_wall.json()["objects"] == []


async def test_the_index_without_an_agent_fans_out_over_the_audience(portal) -> None:
    """The Scheduled section's read: no `agent` names one namespace, so the index answers every
    agent the viewer's web audience holds, each row naming its own. An agent no grant reaches
    contributes nothing until it does, the merged page is ordered across agents rather than
    standing in agent blocks, and it carries no cursor — the kind mints one per agent, and there
    is no single walk to continue. A colleague who created none of them reads the same page: these
    report into shared conversations, so the rows are no more private than the replies they post
    there."""
    client, workspace_id, agent_a, agent_b = portal
    creator_id, creator_headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    with ws(workspace_id):
        for agent_id, name, schedule in (
            (agent_a, "daily-brief", "0 9 * * *"),
            (agent_b, "alpha-sweep", "0 3 * * *"),
        ):
            conversation_id = await _seed_conversation(workspace_id, agent_id)
            with bind_agent(agent_id):
                await _schedule_store().create(
                    conversation_id,
                    name,
                    schedule,
                    f"run {name}",
                    name,
                    NEXT_RUN,
                    created_by_member_id=creator_id,
                )
    index = "/surface/web/objects/scheduled_task"

    walled = await client.get(index, headers=creator_headers)
    assert [row["name"] for row in walled.json()["objects"]] == ["daily-brief"]
    assert walled.json()["next_cursor"] is None

    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    fanned = await client.get(index, headers=creator_headers)
    assert [
        (row["name"], row["agent_id"], row["agent_name"]) for row in fanned.json()["objects"]
    ] == [
        ("alpha-sweep", str(agent_b), "ops"),
        ("daily-brief", str(agent_a), "assistant"),
    ]

    descending = await client.get(index + "?order=desc", headers=creator_headers)
    assert [row["name"] for row in descending.json()["objects"]] == ["daily-brief", "alpha-sweep"]
    searched = await client.get(index + "?q=sweep", headers=creator_headers)
    assert [row["name"] for row in searched.json()["objects"]] == ["alpha-sweep"]

    _colleague_id, colleague_headers = await _seed_member(workspace_id, OTHER_EMAIL)
    await _grant(workspace_id, agent_b, OTHER_EMAIL)
    colleague = await client.get(index, headers=colleague_headers)
    assert [row["name"] for row in colleague.json()["objects"]] == ["alpha-sweep", "daily-brief"]
    assert [row["summary"] for row in colleague.json()["objects"]] == [
        "0 3 * * * — alpha-sweep",
        "0 9 * * * — daily-brief",
    ]

    walking = await client.get(index + "?cursor=abc", headers=creator_headers)
    assert walking.status_code == 400
    assert (await client.get(index)).status_code == 401


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
        cards = await UserSkillStore(ctx=context_for("skill_create", frozenset())).cards()
    expected = [*DEPLOY_SKILLS.index(), *((card.name, card.description) for card in cards)]
    assert [(skill["name"], skill["description"]) for skill in listed] == expected
    by_name = {skill["name"]: skill for skill in listed}
    assert by_name["release-notes"]["origin"] == "member"
    assert by_name["release-notes"]["instructions"] == "Write tersely."
    assert all(skill["origin"] == "deploy" for skill in listed if skill["name"] != "release-notes")
    assert CHILD_SKILL.name in DEPLOY_SKILLS.by_name
    assert CHILD_SKILL.name not in by_name
    assert by_name[DEPLOY_ONLY_SKILL.name]["origin"] == "deploy"
    theirs = (await client.get(f"/surface/web/agents/{agent_b}/skills", headers=headers)).json()
    assert [skill for skill in theirs["skills"] if skill["origin"] == "member"] == []
    assert [(skill["name"], skill["description"]) for skill in theirs["skills"]] == list(
        DEPLOY_SKILLS.index()
    )
    assert CHILD_SKILL.name not in {skill["name"] for skill in theirs["skills"]}


async def test_memory_search_stays_inside_the_viewers_subjects(portal, tmp_path: Path) -> None:
    """The workspace memory view: search and the no-query listing both answer only the viewer's
    own subject plus shared — another member's private items never match or list."""
    client, workspace_id, _agent_a, _agent_b = portal
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
    found = await client.get("/surface/web/workspace/memory?q=launch codename", headers=headers_a)
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
    listing = await client.get("/surface/web/workspace/memory", headers=headers_a)
    listed = listing.json()
    assert listed["available"] is True
    assert [match["text"] for match in listed["matches"]] == ["the launch codename is bluebird"]
    assert [match["created_at"] for match in listed["matches"]] == [
        stamped.astimezone(UTC).isoformat()
    ]


async def test_memory_panel_fences_source_pages_by_agent_grant(portal) -> None:
    """The workspace memory search unions the member's reachable agents, and source grants stay
    the fence: a page granted only to a non-main agent answers the member that agent was granted
    to (their union includes it) and never a member who reaches only the main agent — or, with no
    grant at all, only the source's own member; the hit's timestamp rides the wire aware."""
    client, workspace_id, _agent_a, agent_b = portal
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
                agent_id=agent_b,
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

    path = "/surface/web/workspace/memory?q=runway painted"
    granted = (await client.get(path, headers=headers)).json()
    [hit] = page_refs(granted)
    assert hit["kind"] == "source"
    assert hit["created_at"] == minted_at.isoformat()
    unreachable = (await client.get(path, headers=other_headers)).json()
    assert page_refs(unreachable) == []
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.source_grant).where(tables.source_grant.c.source_id == source_id)
        )
    owner_ungranted = (await client.get(path, headers=headers)).json()
    assert len(page_refs(owner_ungranted)) == 1
    stranger_ungranted = (await client.get(path, headers=other_headers)).json()
    assert page_refs(stranger_ungranted) == []


async def test_memory_listing_is_newest_first_and_bounded(portal, tmp_path: Path) -> None:
    """The no-query view lists the newest live items under the viewer's subjects, newest first,
    capped at the real `MEMORY_RECENT_LIMIT` — a superseded item never lists."""
    client, workspace_id, _agent_a, _agent_b = portal
    member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
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
        for count in range(MEMORY_RECENT_LIMIT + 3):
            await store.commit(
                MemoryWrite(subject=member_subject(member_id), body=f"note {count:03d}")
            )
    newest = f"note {MEMORY_RECENT_LIMIT + 2:03d}"
    async with workspace_tx() as connection:
        rows = (await connection.execute(sa.select(memory_item.c.id, memory_item.c.body))).all()
        stamped = datetime(2026, 7, 1, tzinfo=UTC)
        for row in rows:
            await connection.execute(
                sa.update(memory_item)
                .where(memory_item.c.id == row.id)
                .values(created_at=stamped + timedelta(minutes=int(row.body.split()[1])))
            )
        superseding = next(row.id for row in rows if row.body == f"note {MEMORY_RECENT_LIMIT:03d}")
        await connection.execute(
            sa.update(memory_item)
            .where(memory_item.c.body == newest)
            .values(superseded_by=superseding)
        )
    listing = (await client.get("/surface/web/workspace/memory", headers=headers)).json()
    assert listing["available"] is True
    assert len(listing["matches"]) == MEMORY_RECENT_LIMIT
    stamps = [match["created_at"] for match in listing["matches"]]
    assert stamps == sorted(stamps, reverse=True)
    assert stamps[0].endswith("+00:00")
    listed = {match["text"] for match in listing["matches"]}
    assert newest not in listed
    assert f"note {MEMORY_RECENT_LIMIT + 1:03d}" in listed
    assert "note 002" in listed
    assert {"note 000", "note 001"}.isdisjoint(listed)


async def test_the_memory_read_states_the_bound_the_writing_tool_holds_a_body_to(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The correction form on this page collects a body the memory tool refuses past, so the read
    that draws the rows carries how long one may run — from the installed provider, which is what
    enforces it. Both shapes state it, because either can be the read a correction is opened from,
    and the number is the tool's own: a copy kept in the portal would drift the day the provider
    moves its bound, and the member would meet the difference as a refusal."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, _agent_a, _agent_b = await _seed_workspace()
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    app = _mount_portal(tmp_path, with_memory=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        listing = (await client.get("/surface/web/workspace/memory", headers=headers)).json()
        searched = (
            await client.get("/surface/web/workspace/memory?q=anything", headers=headers)
        ).json()

    written = MemoryUpdateInput.model_json_schema()["properties"]["body"]["maxLength"]
    assert listing["body_max_chars"] == written
    assert searched["body_max_chars"] == written


async def test_a_memoryless_deploy_never_claims_availability(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, _agent_a, _agent_b = await _seed_workspace()
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    app = _mount_portal(tmp_path, with_memory=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        blank = await client.get("/surface/web/workspace/memory", headers=headers)
        assert blank.json() == {"available": False, "matches": []}
        queried = await client.get("/surface/web/workspace/memory?q=anything", headers=headers)
        assert queried.json() == {"available": False, "matches": []}


async def _seed_notes(
    workspace_id: UUID,
    member_id: UUID,
    bodies: tuple[tuple[str, str], ...],
    stamps: dict[str, datetime],
) -> None:
    """Commit one memory item per body under the member's own subject, then stamp each so the
    browse ordering is the test's to state rather than the clock's."""
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
        for body, item_class in bodies:
            await store.commit(
                MemoryWrite(subject=member_subject(member_id), body=body, item_class=item_class)
            )
    async with workspace_tx() as connection:
        for body, stamp in stamps.items():
            await connection.execute(
                sa.update(memory_item).where(memory_item.c.body == body).values(created_at=stamp)
            )


def _texts(payload: dict) -> list[str]:
    return [match["text"] for match in payload["matches"]]


async def _walk_older(client: AsyncClient, path: str, headers: dict[str, str]) -> list[str]:
    """Every item the Older control reaches, in the order the pages render them."""
    walked: list[str] = []
    payload = (await client.get(path, headers=headers)).json()
    walked.extend(_texts(payload))
    while payload["older"]:
        joiner = "&" if "?" in path else "?"
        payload = (
            await client.get(f"{path}{joiner}after={quote(payload['older'])}", headers=headers)
        ).json()
        walked.extend(_texts(payload))
    return walked


async def test_memory_listing_walks_pages_without_repeating_or_skipping(
    portal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keyset walk: Older reaches every item exactly once and Newer returns the page it came
    from, with each page's boundary cursors saying which controls exist. The page size is patched
    small so the walk's own arithmetic is what the assertions read — the real
    `MEMORY_RECENT_LIMIT` is pinned by the bounded-listing test."""
    client, workspace_id, _agent_a, _agent_b = portal
    member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    base = datetime(2026, 7, 1, tzinfo=UTC)
    bodies = tuple((f"note {index}", "fact") for index in range(5))
    stamps = {f"note {index}": base + timedelta(minutes=index) for index in range(5)}
    await _seed_notes(workspace_id, member_id, bodies, stamps)
    monkeypatch.setattr(web_surface, "MEMORY_RECENT_LIMIT", 3)
    path = "/surface/web/workspace/memory"

    first = (await client.get(path, headers=headers)).json()
    assert _texts(first) == ["note 4", "note 3", "note 2"]
    assert first["newer"] is None
    assert first["older"] is not None

    second = (await client.get(f"{path}?after={quote(first['older'])}", headers=headers)).json()
    assert _texts(second) == ["note 1", "note 0"]
    assert second["older"] is None
    assert second["newer"] is not None
    assert set(_texts(first)).isdisjoint(_texts(second))

    back = (await client.get(f"{path}?after={quote(second['newer'])}", headers=headers)).json()
    assert _texts(back) == _texts(first)
    assert back["newer"] is None


async def test_memory_paging_breaks_a_shared_timestamp_at_the_boundary(
    portal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two items minted in the same instant land either side of a page boundary: the cursor
    carries the item id beside the timestamp, so neither repeats and neither is skipped — a
    timestamp-only cursor loses exactly one of them."""
    client, workspace_id, _agent_a, _agent_b = portal
    member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    tie = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    bodies = (("tied a", "fact"), ("tied b", "fact"), ("older one", "fact"))
    stamps = {"tied a": tie, "tied b": tie, "older one": tie - timedelta(minutes=1)}
    await _seed_notes(workspace_id, member_id, bodies, stamps)
    monkeypatch.setattr(web_surface, "MEMORY_RECENT_LIMIT", 1)

    walked = await _walk_older(client, "/surface/web/workspace/memory", headers)
    assert sorted(walked) == ["older one", "tied a", "tied b"]
    assert len(walked) == len(set(walked))


async def test_memory_paging_is_stable_across_a_concurrent_write(
    portal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An item landing while a member reads shifts no page boundary: the cursor names a row, not
    an offset, so the second page is exactly what it would have been. Offset paging repeats the
    row the insert displaced."""
    client, workspace_id, _agent_a, _agent_b = portal
    member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    base = datetime(2026, 7, 1, tzinfo=UTC)
    bodies = tuple((f"note {index}", "fact") for index in range(4))
    stamps = {f"note {index}": base + timedelta(minutes=index) for index in range(4)}
    await _seed_notes(workspace_id, member_id, bodies, stamps)
    monkeypatch.setattr(web_surface, "MEMORY_RECENT_LIMIT", 2)
    path = "/surface/web/workspace/memory"

    first = (await client.get(path, headers=headers)).json()
    assert _texts(first) == ["note 3", "note 2"]
    await _seed_notes(
        workspace_id,
        member_id,
        (("landed mid-read", "fact"),),
        {"landed mid-read": base + timedelta(hours=1)},
    )
    second = (await client.get(f"{path}?after={quote(first['older'])}", headers=headers)).json()
    assert _texts(second) == ["note 1", "note 0"]


async def test_a_cursor_this_surface_never_minted_is_refused(portal) -> None:
    """A token naming no position is refused rather than silently answering the newest page — a
    stale or hand-edited link tells the member instead of quietly moving them."""
    client, workspace_id, _agent_a, _agent_b = portal
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    path = "/surface/web/workspace/memory"
    for token in (
        "garbage",
        "older|not-a-timestamp|11111111-1111-4111-8111-111111111111",
        "older|2026-07-01T00:00:00+00:00|not-a-uuid",
        "sideways|2026-07-01T00:00:00+00:00|11111111-1111-4111-8111-111111111111",
        "older|2026-07-01T00:00:00+00:00",
    ):
        refused = await client.get(f"{path}?after={quote(token)}", headers=headers)
        assert refused.status_code == 400, token


async def test_memory_filter_narrows_to_one_class_and_composes_with_paging(
    portal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filter offers exactly the provider's own classes and narrows the listing to one of
    them; a page walked under a filter stays inside it, a class nothing wrote lists empty, and a
    class the provider does not write is refused."""
    client, workspace_id, _agent_a, _agent_b = portal
    member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    base = datetime(2026, 7, 1, tzinfo=UTC)
    bodies = (
        ("a fact", "fact"),
        ("an episode", "episodic"),
        ("another fact", "fact"),
        ("a third fact", "fact"),
    )
    stamps = {
        "a fact": base + timedelta(minutes=1),
        "an episode": base + timedelta(minutes=2),
        "another fact": base + timedelta(minutes=3),
        "a third fact": base + timedelta(minutes=4),
    }
    await _seed_notes(workspace_id, member_id, bodies, stamps)
    path = "/surface/web/workspace/memory"

    unfiltered = (await client.get(path, headers=headers)).json()
    assert set(unfiltered["kinds"]) == {"fact", "episodic", "semantic"}
    assert unfiltered["kind"] is None
    assert "an episode" in _texts(unfiltered)

    facts = (await client.get(f"{path}?kind=fact", headers=headers)).json()
    assert facts["kind"] == "fact"
    assert _texts(facts) == ["a third fact", "another fact", "a fact"]
    assert {match["kind"] for match in facts["matches"]} == {"fact"}

    monkeypatch.setattr(web_surface, "MEMORY_RECENT_LIMIT", 2)
    page = (await client.get(f"{path}?kind=fact", headers=headers)).json()
    assert _texts(page) == ["a third fact", "another fact"]
    rest = (
        await client.get(f"{path}?kind=fact&after={quote(page['older'])}", headers=headers)
    ).json()
    assert _texts(rest) == ["a fact"]

    empty = (await client.get(f"{path}?kind=semantic", headers=headers)).json()
    assert empty["matches"] == []
    unknown = await client.get(f"{path}?kind=invented", headers=headers)
    assert unknown.status_code == 400


async def test_the_subject_fence_holds_on_every_page(
    portal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another member's private item is absent from every page of the walk — paging narrows the
    window, never the fence."""
    client, workspace_id, _agent_a, _agent_b = portal
    member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    other_id, _other_headers = await _seed_member(workspace_id, OTHER_EMAIL)
    base = datetime(2026, 7, 1, tzinfo=UTC)
    await _seed_notes(
        workspace_id,
        member_id,
        tuple((f"mine {index}", "fact") for index in range(3)),
        {f"mine {index}": base + timedelta(minutes=index) for index in range(3)},
    )
    await _seed_notes(
        workspace_id,
        other_id,
        (("theirs alone", "fact"),),
        {"theirs alone": base + timedelta(minutes=1, seconds=30)},
    )
    monkeypatch.setattr(web_surface, "MEMORY_RECENT_LIMIT", 1)

    walked = await _walk_older(client, "/surface/web/workspace/memory", headers)
    assert sorted(walked) == ["mine 0", "mine 1", "mine 2"]


def test_the_first_run_records_every_pick_inside_the_row_it_is_drawn_as() -> None:
    """A member may pick every tile. Naming all 21 runs to 208 characters and nine of the longer
    labels to 120, so the sentence is built to the bound rather than refused after they continue."""
    labels = tuple(tile.label for tile in FIRST_RUN_PROVIDERS)
    for picked in range(1, len(labels) + 1):
        body = _tools_recorded(labels[:picked], MEMORY_BODY_MAX_CHARS)
        assert len(body) <= MEMORY_BODY_MAX_CHARS, (picked, len(body), body)
        assert body.startswith(TOOLING_PREFIX)
        memory_manifest_module.MemoryUpdateInput(
            body=body, user_description="Record what the team uses from the first run."
        )


def test_the_first_run_says_how_many_picks_it_could_not_name() -> None:
    labels = tuple(tile.label for tile in FIRST_RUN_PROVIDERS)
    whole = _tools_recorded(labels[:2], MEMORY_BODY_MAX_CHARS)
    assert whole == f"{TOOLING_PREFIX}{labels[0]}, {labels[1]}."
    every = _tools_recorded(labels, MEMORY_BODY_MAX_CHARS)
    named = every.removeprefix(TOOLING_PREFIX).split(", and ")[0].split(", ")
    assert every.endswith(f", and {len(labels) - len(named)} more.")
