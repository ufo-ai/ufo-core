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
import ufo_ext_sources.manifest as sources_manifest_module
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_imessage.manifest import manifest as imessage_manifest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.manifest import RECORD_CORRECTION_ACTION, RecordCorrectionInput
from ufo_ext_memory.store import (
    MemoryStore,
    MemoryWrite,
    mem_page,
    memory_item,
)
from ufo_ext_scheduled_tasks.manifest import NAME as SCHEDULED_TASKS_NAME
from ufo_ext_scheduled_tasks.schedules import ScheduleStore
from ufo_ext_scheduled_tasks.schedules import scheduled_task as schedule_table
from ufo_ext_scheduled_tasks.tools import (
    PRIVATE_PROMPT,
    PROMPT_EXCERPT_MAX,
    SCHEDULED_TASK_OBJECT,
    SUMMARY_MAX,
)
from ufo_ext_skill_create.manifest import manifest as skill_create_manifest
from ufo_ext_skill_create.store import UserSkillStore
from ufo_ext_slack.manifest import manifest as slack_manifest
from ufo_ext_sources.manifest import NAME as SOURCES_NAME
from ufo_ext_sources.tools import trigger_name
from ufo_ext_sources.triggers import SourceTriggerStore
from ufo_ext_web import surface as web_surface
from ufo_ext_web.audience import AUDIENCE_PREFIX, web_extension
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_ext_web.surface import MEMORY_RECENT_LIMIT
from ufo_testsupport.surfaces import UNREACHED_AMBIENT_REPLY

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.host.ext.loader import (
    member_object_registry,
    member_skill_listing,
    memory_search,
    skill_registry,
)
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent as bind_agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.surface import record_transcript_access
from ufo.runtime.hub import InProcessHub
from ufo.runtime.skills.runtime import RuntimeSkill
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.turns.subjects import member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import TerminalFrame
from ufo.sdk.grants import account_object_name
from ufo.sdk.index import OWNER_KIND_PAGE, Chunk
from ufo.sdk.manifest import Manifest
from ufo.serve import _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOKEN_SECRET = "web-token-secret"
SESSION_COOKIE = "ufo_session"
ADMIN_EMAIL = "admin@example.com"
CREATOR_EMAIL = "creator@example.com"
OTHER_EMAIL = "other@example.com"
NEXT_RUN = datetime(2027, 1, 1, tzinfo=UTC)
EXPIRES = datetime(2027, 6, 1, tzinfo=UTC)
TASK_RAN = datetime(2026, 8, 27, 9, tzinfo=UTC)
TRIGGER_RAN = datetime(2026, 8, 27, 18, tzinfo=UTC)
WATCHED_PULL = "https://github.com/metalcraftai/ufo/pull/3459"


async def _task_ran(task_id: UUID, at: datetime) -> None:
    """One task's own record of its latest fire, which the fire itself writes as it reschedules."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(schedule_table)
            .where(schedule_table.c.id == task_id)
            .values(last_run_at=at, updated_at=sa.func.now())
        )


async def _trigger_fired(
    workspace_id: UUID, conversation_id: UUID, agent_id: UUID, name: str, at: datetime
) -> None:
    """One turn the named trigger woke — a trigger's whole run history, since the trigger row keeps
    no last-run column."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=uuid7(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="the feed changed",
                admission_source="internal",
                terminal=TerminalFrame(status="done", text="the pull request moved").model_dump(
                    mode="json"
                ),
                fired_by_kind="source_trigger",
                fired_by_name=name,
                fired_by_title=name,
                created_at=at,
                updated_at=at,
            )
        )


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


def _mount_portal(
    tmp_path: Path, *, with_memory: bool, installed: tuple[Manifest, ...] = ()
) -> FastAPI:
    index = DefaultIndex(transaction=workspace_tx)
    embed = StubEmbed()
    manifests = (
        web_manifest(),
        skill_create_manifest(),
        SCHEDULED_TASK_KIND_ONLY,
        sources_manifest_module.manifest(),
        memory_manifest_module.manifest(),
        *installed,
    )
    credentials = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    app = FastAPI()
    app.state.instance_id = uuid4()
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
        objects=member_object_registry(manifests, credentials, index, embed),
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
    by where it reports: the private conversation's own member lists and reads it, and the admin
    meets it elided like the rest. Each read is walled by the agent named on it. The row's
    `prompt` is the task's own prompt, whole — past both the summary's line and the excerpt a
    turn's own read is bounded to: a member's screen holds its own length, and a cut made here is
    one it cannot undo."""
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
        "run_now",
        "connections",
    }
    rows = {row["name"]: row for row in creator_view.json()["objects"]}
    assert set(rows) == {"daily-brief", "creatorless-sweep"}
    assert rows["creatorless-sweep"]["prompt"] == "sweep the queue"
    row = rows["daily-brief"]
    assert row["paused"] is False
    assert row["next_run_at"] == NEXT_RUN.isoformat()
    assert row["summary"] == "0 9 * * * — daily brief"
    assert row["prompt"] == "write the daily brief"
    assert row["connections"] == []
    assert (row["agent_id"], row["agent_name"]) == (str(agent_a), "assistant")
    mine = await client.get(
        f"/surface/web/objects/scheduled_task/daily-brief?agent={agent_a}", headers=creator_headers
    )
    assert mine.json()["spec"]["prompt"] == "write the daily brief"
    assert mine.json()["spec"]["connections"] == []
    assert mine.json()["spec"]["expires_at"].startswith("2027-06-01T00:00:00")

    admin_view = await client.get(index, headers=admin_headers)
    by_name = {row["name"]: row for row in admin_view.json()["objects"]}
    assert by_name["daily-brief"]["summary"] == "0 9 * * * — private member task"
    assert by_name["creatorless-sweep"]["summary"] == "0 3 * * * — private member task"
    assert by_name["daily-brief"]["prompt"] == PRIVATE_PROMPT
    assert by_name["creatorless-sweep"]["prompt"] == PRIVATE_PROMPT
    assert by_name["daily-brief"]["connections"] is None
    assert by_name["creatorless-sweep"]["connections"] is None
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
    assert read.json()["spec"]["connections"] == []

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


async def test_the_automations_read_merges_both_kinds_on_one_last_run_column(portal) -> None:
    """The Automations screen's listing: scheduled tasks and source triggers as one page, most
    recently run first, each row naming the kind and the agent it belongs to. A trigger keeps no
    last-run column of its own, so its last run is the newest turn it fired; a row that has never
    run falls after every row that has. The row carries the connector's display name beside the
    provider slug, because the screen says GitHub where the feed says github."""
    client, workspace_id, agent_a, agent_b = portal
    creator_id, creator_headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    with ws(workspace_id):
        watching = await _seed_conversation(workspace_id, agent_a)
        with bind_agent(agent_a):
            ran = await _schedule_store().create(
                watching,
                "daily-brief",
                "0 9 * * *",
                "write the daily brief",
                "daily brief",
                NEXT_RUN,
                created_by_member_id=creator_id,
            )
            connection_id = await GrantStore().record(
                provider="github",
                account_id="acct-one",
                host="api.github.test",
                grantor_member_id=creator_id,
                shared=True,
            )
            await SourceTriggerStore(context_for(SOURCES_NAME, frozenset())).create(
                watching,
                connection_id,
                "current",
                created_by_member_id=creator_id,
                resource=WATCHED_PULL,
            )
        sweeping = await _seed_conversation(workspace_id, agent_b)
        with bind_agent(agent_b):
            await _schedule_store().create(
                sweeping,
                "alpha-sweep",
                "0 3 * * *",
                "sweep the queue",
                "queue sweep",
                NEXT_RUN,
                created_by_member_id=creator_id,
            )
    await _task_ran(ran.id, TASK_RAN)
    watched = trigger_name(account_object_name("github", "acct-one"), watching, WATCHED_PULL)
    await _trigger_fired(workspace_id, watching, agent_a, watched, TRIGGER_RAN)

    page = await client.get("/surface/web/automations", headers=creator_headers)
    assert [held["kind"] for held in page.json()["kinds"]] == ["scheduled_task", "source_trigger"]
    assert [(row["kind"], row["name"], row["last_run_at"]) for row in page.json()["objects"]] == [
        ("source_trigger", watched, TRIGGER_RAN.isoformat()),
        ("scheduled_task", "daily-brief", TASK_RAN.isoformat()),
        ("scheduled_task", "alpha-sweep", None),
    ]
    assert page.json()["next_cursor"] is None
    trigger_row, task_row, swept = page.json()["objects"]
    assert (trigger_row["provider"], trigger_row["provider_label"]) == ("github", "GitHub")
    assert trigger_row["resource"] == WATCHED_PULL
    assert (trigger_row["agent_id"], trigger_row["agent_name"]) == (str(agent_a), "assistant")
    assert task_row["description"] == "daily brief"
    assert (swept["agent_id"], swept["agent_name"]) == (str(agent_b), "ops")

    searched = await client.get("/surface/web/automations?q=sweep", headers=creator_headers)
    assert [row["name"] for row in searched.json()["objects"]] == ["alpha-sweep"]
    walking = await client.get("/surface/web/automations?cursor=abc", headers=creator_headers)
    assert walking.status_code == 400
    assert (await client.get("/surface/web/automations")).status_code == 401


async def test_the_automations_read_hides_a_private_task_and_elides_it_for_an_admin(
    portal,
) -> None:
    """Who reads another member's private automation on the Automations screen. A member who is not
    an admin never learns it exists: the row is absent from the listing and its detail is
    not-found, so no row stands there naming a task they can neither read nor act on. An admin
    lists it as the management row whose cadence is theirs to change, and its content stays elided
    until they acknowledge the conversation it reports into — the one sanctioned way into another
    member's private words. That acknowledgement opens the task with the transcript: the prompt,
    the description and the spec answer the admin, on the record, for as long as the disclosure
    stands, and the member who is not an admin still learns nothing."""
    client, workspace_id, agent_a, _agent_b = portal
    admin_id, admin_headers = await _seed_member(workspace_id, ADMIN_EMAIL, admin=True)
    creator_id, _creator_headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    _other_id, other_headers = await _seed_member(workspace_id, OTHER_EMAIL)
    with ws(workspace_id):
        private = await _seed_conversation(workspace_id, agent_a, creator_id)
        with bind_agent(agent_a):
            await _schedule_store().create(
                private,
                "daily-brief",
                "0 9 * * *",
                "write the daily brief",
                "daily brief",
                NEXT_RUN,
                created_by_member_id=creator_id,
            )
    detail = f"/surface/web/objects/scheduled_task/daily-brief?agent={agent_a}"

    theirs = await client.get("/surface/web/automations", headers=other_headers)
    assert theirs.json()["objects"] == []
    assert (await client.get(detail, headers=other_headers)).status_code == 404

    managed = await client.get("/surface/web/automations", headers=admin_headers)
    (row,) = managed.json()["objects"]
    assert (row["name"], row["description"]) == ("daily-brief", "")
    assert row["prompt"] == PRIVATE_PROMPT
    assert row["summary"] == f"0 9 * * * — {PRIVATE_PROMPT}"
    assert row["mine"] is False
    assert row["readable"] is False
    read = await client.get(detail, headers=admin_headers)
    assert read.json()["spec"] is None
    assert read.json()["status"]["next_run_at"] == NEXT_RUN.isoformat()

    with ws(workspace_id):
        recorded = await record_transcript_access(workspace_id, private, agent_a, admin_id)
    assert recorded is not None

    disclosed = await client.get("/surface/web/automations", headers=admin_headers)
    (opened,) = disclosed.json()["objects"]
    assert opened["prompt"] == "write the daily brief"
    assert opened["description"] == "daily brief"
    assert opened["summary"] == "0 9 * * * — daily brief"
    assert opened["readable"] is True
    assert opened["mine"] is False
    opened_detail = await client.get(detail, headers=admin_headers)
    assert opened_detail.json()["spec"]["prompt"] == "write the daily brief"

    still_hidden = await client.get("/surface/web/automations", headers=other_headers)
    assert still_hidden.json()["objects"] == []


async def test_skills_list_the_workspaces_own_and_the_deploys(portal) -> None:
    """The skills read answers the workspace's saved set beside the deploy's, whichever agent the
    read names — the set the portal's workspace page manages, and an app's own setting decides only
    whether its turns load it."""
    client, workspace_id, agent_a, agent_b = portal
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    skill_md = (
        b"---\nname: release-notes\ndescription: How this team writes release notes.\n"
        b"metadata:\n  agents: [research]\n---\n"
        b"Write tersely."
    )
    checklist = b"one line per pull request\n"
    with ws(workspace_id), bind_agent(agent_a):
        await UserSkillStore(ctx=context_for("skill_create", frozenset())).save(
            "release-notes",
            {"SKILL.md": skill_md, "references/checklist.md": checklist},
            frozenset(),
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
    assert by_name["release-notes"]["depends"] == []
    assert by_name["release-notes"]["agents"] == ["research"]
    assert by_name[DEPLOY_ONLY_SKILL.name]["agents"] == []
    assert all(skill["origin"] == "deploy" for skill in listed if skill["name"] != "release-notes")
    assert CHILD_SKILL.name in DEPLOY_SKILLS.by_name
    assert CHILD_SKILL.name not in by_name
    assert by_name[DEPLOY_ONLY_SKILL.name]["origin"] == "deploy"
    theirs = (await client.get(f"/surface/web/agents/{agent_b}/skills", headers=headers)).json()
    assert [skill["name"] for skill in theirs["skills"] if skill["origin"] == "member"] == [
        "release-notes"
    ]
    assert [(skill["name"], skill["description"]) for skill in theirs["skills"]] == expected
    assert CHILD_SKILL.name not in {skill["name"] for skill in theirs["skills"]}


async def test_memory_panel_shows_a_shared_source_to_every_member_through_the_main_agent(
    portal,
) -> None:
    """The workspace memory search unions the member's reachable agents, and every member reaches
    the main agent, which reads every shared source without a grant. So a shared page granted only
    to a non-main agent answers the member that agent was granted to and the member who reaches
    only the main agent alike, and keeps answering both once no grant is left; the hit's timestamp
    rides the wire aware."""
    client, workspace_id, _agent_a, agent_b = portal
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    _other_id, other_headers = await _seed_member(workspace_id, OTHER_EMAIL)
    await _grant(workspace_id, agent_b, CREATOR_EMAIL)
    page_id, source_id = uuid7(), uuid7()
    minted_at = datetime(2026, 7, 1, 8, 30, tzinfo=UTC)
    vector = [0.0] * EMBED_DIM
    vector[0] = 1.0
    connection_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="test",
                account_id="",
                host="",
                owner_member_id=None,
                shared=True,
                created_at=minted_at,
                updated_at=minted_at,
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend="test",
                config={},
                feed_handle=feed_handle_for({}, frozenset()),
                connection_id=connection_id,
                next_sync_at=minted_at,
                created_at=minted_at,
                updated_at=minted_at,
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                uid=page_id,
                workspace_id=workspace_id,
                source_uid=source_id,
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
                sa.select(tables.page.c.revision).where(tables.page.c.uid == page_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(mem_page).values(
                page_uid=page_id,
                workspace_id=workspace_id,
                subject="shared",
                revision=revision,
                created_at=minted_at,
            )
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_b,
                connection_id=connection_id,
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
    through_main = (await client.get(path, headers=other_headers)).json()
    assert len(page_refs(through_main)) == 1
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.connector_grant).where(
                tables.connector_grant.c.connection_id == connection_id
            )
        )
    owner_ungranted = (await client.get(path, headers=headers)).json()
    assert len(page_refs(owner_ungranted)) == 1
    stranger_ungranted = (await client.get(path, headers=other_headers)).json()
    assert len(page_refs(stranger_ungranted)) == 1


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
    """The correction form on this page collects a body the memory action refuses past, so the read
    that draws the rows carries how long one may run — inside the projected action's own schema,
    which is what enforces it. Both shapes carry it, because either can be the read a correction is
    opened from, and the number is the action's own: a copy kept in the portal would drift the day
    the provider moves its bound, and the member would meet the difference as a refusal."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, _agent_a, _agent_b = await _seed_workspace()
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    app = _mount_portal(tmp_path, with_memory=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        listing = (await client.get("/surface/web/workspace/memory", headers=headers)).json()
        searched = (
            await client.get("/surface/web/workspace/memory?q=anything", headers=headers)
        ).json()

    written = RecordCorrectionInput.model_json_schema()["properties"]["body"]["maxLength"]
    for read in (listing, searched):
        [correction] = [v for v in read["actions"] if v["name"] == RECORD_CORRECTION_ACTION]
        assert correction["input_schema"]["properties"]["body"]["maxLength"] == written


async def test_a_memoryless_deploy_never_claims_availability(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    workspace_id, _agent_a, _agent_b = await _seed_workspace()
    _member_id, headers = await _seed_member(workspace_id, CREATOR_EMAIL)
    app = _mount_portal(tmp_path, with_memory=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        blank = await client.get("/surface/web/workspace/memory", headers=headers)
        assert blank.json() == {"available": False, "kinds": [], "matches": [], "actions": []}
        queried = await client.get("/surface/web/workspace/memory?q=anything", headers=headers)
        assert queried.json() == {"available": False, "kinds": [], "matches": [], "actions": []}


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
    assert set(unfiltered["kinds"]) == {"fact", "episodic", "semantic", "section", "overview"}
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


async def _offered(app: FastAPI, workspace_id: UUID, email: str) -> dict[str, bool]:
    _member_id, headers = await _seed_member(workspace_id, email)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://web") as client:
        answer = await client.get("/surface/web/workspace/surfaces", headers=headers)
    assert answer.status_code == 200
    return {row["name"]: row["offered"] for row in answer.json()["surfaces"]}


async def test_a_chat_surface_is_offered_only_where_its_connect_act_would_dispatch(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A row states `offered` from the deploy's own declarations, so the act it draws is one
    dispatch will take. A pack without the Slack or iMessage extension declares neither connect
    action and offers neither row. A pack holding both declares them, and Slack is offered — while
    iMessage still is not, because this deploy has no provider: the declaration and the provider are
    both required."""
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    for name in ("SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"UFO_{name}", raising=False)
    workspace_id, _agent_a, _agent_b = await _seed_workspace()

    bare = await _offered(_mount_portal(tmp_path, with_memory=False), workspace_id, "bare@x.com")
    assert bare == {"slack": False, "imessage": False, "ufo": True}

    packed = await _offered(
        _mount_portal(
            tmp_path, with_memory=False, installed=(slack_manifest(), imessage_manifest())
        ),
        workspace_id,
        "packed@x.com",
    )
    assert packed == {"slack": True, "imessage": False, "ufo": True}
