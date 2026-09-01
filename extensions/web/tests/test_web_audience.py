"""The web surface's audience authority: the admin-only chat verbs the portal's acts ride, and the
resolution the portal reads — admins see every agent, everyone else the main agent
plus exactly the non-main agents granted to their email."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_web.audience import (
    AUDIENCE_PREFIX,
    WEB_ACCESS_TOOLS,
    PrivateTranscriptInput,
    WebAccessInput,
    granted_emails,
    web_audience,
    web_extension,
)
from ufo_ext_web.manifest import NAME
from ufo_ext_web.surface import SURFACE_WEB, _open_conversation
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    EMPTY_TURN_STEPS,
    UNREACHED_AMBIENT_REPLY,
    UNREACHED_STOPPER,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.host.kinds.conversations import CONVERSATION_KIND
from ufo.host.kinds.members import MEMBER_KIND
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.surface import SurfaceContext
from ufo.runtime.hub import InProcessHub
from ufo.runtime.object_scope import ObjectActionTarget, ObjectAgent
from ufo.runtime.surfaces.admission import Admission, MemberAdmission
from ufo.runtime.surfaces.hub_tail import HubTailer
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience, foreign_room_audience

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

GRANT = WEB_ACCESS_TOOLS[0]
REVOKE = WEB_ACCESS_TOOLS[1]
TRANSCRIPT = WEB_ACCESS_TOOLS[2]
ADMIN_EMAIL = "alice@example.com"
MEMBER_EMAIL = "bob@example.com"


async def _seed() -> tuple[UUID, UUID, UUID]:
    workspace_id, main_agent, second_agent = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for agent_id, name, main in ((main_agent, "assistant", True), (second_agent, "ops", False)):
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
    return workspace_id, main_agent, second_agent


async def _member(workspace_id: UUID, email: str, *, admin: bool = False) -> UUID:
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
    return member_id


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the audience tests")


def _targeting(
    ctx: ToolContext, kind: str, name: UUID, agent: tuple[UUID, str] | None = None
) -> ToolContext:
    return replace(
        ctx,
        target=ObjectActionTarget(
            kind=kind,
            name=str(name),
            agent=None if agent is None else ObjectAgent(id=agent[0], name=agent[1]),
            generation=None,
            expected_generation=None,
        ),
    )


def _tool_ctx(workspace_id: UUID, agent_id: UUID, speaker: UUID | None) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=agent_id,
            seq=0,
            status="running",
            inbound="please grant access",
            created_at=datetime(2026, 7, 28, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker,
        audience=conversation_audience(speaker),
        artifact_token_secret="",
        ext=context_for(NAME, frozenset()),
    )


def _surface(workspace_id: UUID, tmp_path) -> SurfaceContext:
    return SurfaceContext(
        workspace_id=workspace_id,
        surface=SURFACE_WEB,
        blob=FilesystemBlobStore(root=tmp_path),
        _sandboxes=ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        _admitter=MemberAdmission(
            workspace_id=workspace_id,
            admission=Admission(dbos=None, durable_surfaces=frozenset()),
        ),
        _tailer=HubTailer(hub=InProcessHub()),
        _stopper=UNREACHED_STOPPER,
        _turn_steps=EMPTY_TURN_STEPS,
        _credentials=None,
        _declared_slots=(),
        _artifact_token_secret="",
        _connectors=ConnectorRegistry(entries={}),
        _skills=EMPTY_SKILL_REGISTRY,
        _member_skill_listing=no_member_skills,
        _public_base_url=None,
        _home_surface=None,
        _ingress_public_url=None,
        _deploy_sandbox_internet=False,
        _models=("auto", "claude-opus-4-8", "claude-sonnet-5"),
        _ambient_reply=UNREACHED_AMBIENT_REPLY,
    )


async def test_granted_emails_groups_per_agent_and_sorts(db: None, tmp_path) -> None:
    """The administration view's read over the grant rows: grants group under their agent, emails
    sort within a group, and one agent's grants never bleed into another's."""
    workspace_id, _main_agent, second_agent = await _seed()
    third_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=third_agent,
                workspace_id=workspace_id,
                name="research",
                prompt="be thorough",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    zed_id = await _member(workspace_id, "zed@example.com")
    with ws(workspace_id):
        for agent_id, granted_member in (
            (second_agent, zed_id),
            (second_agent, member_id),
            (third_agent, zed_id),
        ):
            granted = await GRANT.handler(
                _targeting(
                    _tool_ctx(workspace_id, agent_id, admin_id), MEMBER_KIND, granted_member
                ),
                WebAccessInput(),
            )
            assert not granted.is_error
        grants = await granted_emails(web_extension().store)
    assert grants == {
        second_agent: (MEMBER_EMAIL, "zed@example.com"),
        third_agent: ("zed@example.com",),
    }


async def test_admin_grant_and_revoke_shape_the_member_audience(db: None, tmp_path) -> None:
    workspace_id, main_agent, second_agent = await _seed()
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    with ws(workspace_id):
        surface = _surface(workspace_id, tmp_path)
        extension = context_for(NAME, frozenset())
        granted = await GRANT.handler(
            _targeting(_tool_ctx(workspace_id, second_agent, admin_id), MEMBER_KIND, member_id),
            WebAccessInput(),
        )
        assert not granted.is_error
        audience = await web_audience(surface, extension, MEMBER_EMAIL)
        assert not audience.admin
        assert [agent.id for agent in audience.agents] == [main_agent, second_agent]
        assert audience.allows(second_agent) and audience.allows(main_agent)
        admin_view = await web_audience(surface, extension, ADMIN_EMAIL)
        assert admin_view.admin
        assert [agent.id for agent in admin_view.agents] == [main_agent, second_agent]
        revoked = await REVOKE.handler(
            _targeting(_tool_ctx(workspace_id, second_agent, admin_id), MEMBER_KIND, member_id),
            WebAccessInput(),
        )
        assert not revoked.is_error
        remaining = await web_audience(surface, extension, MEMBER_EMAIL)
        assert [agent.id for agent in remaining.agents] == [main_agent]

        aimed = await GRANT.handler(
            _targeting(
                _tool_ctx(workspace_id, main_agent, admin_id),
                MEMBER_KIND,
                member_id,
                agent=(second_agent, "ops"),
            ),
            WebAccessInput(),
        )
        assert not aimed.is_error
        assert "reach ops in the web portal" in aimed.content[0].text
        regranted = await web_audience(surface, extension, MEMBER_EMAIL)
        assert [agent.id for agent in regranted.agents] == [main_agent, second_agent]


async def test_the_wiki_app_is_private_and_reachable_by_its_audience_only(
    db: None, tmp_path
) -> None:
    """The wiki app provisions a private agent (#2530): no workspace-visible row ships it to every
    member. The private rung still reaches it — the owner, a workspace admin, and a member granted
    web access — while an uninvolved member is left with the main agent alone."""
    workspace_id, main_agent, _second_agent = await _seed()
    wiki_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=wiki_agent,
                workspace_id=workspace_id,
                name="wiki",
                prompt="be the wiki",
                model="claude-sonnet-5",
                icon="book",
                visibility="private",
                provisioned_by="app_wiki",
                provisioned_name="wiki",
                provisioned_version="0.1.0",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    await _member(workspace_id, "carol@example.com")
    with ws(workspace_id):
        surface = _surface(workspace_id, tmp_path)
        extension = context_for(NAME, frozenset())
        audience = await web_audience(surface, extension, MEMBER_EMAIL)
        assert [agent.id for agent in audience.agents] == [main_agent]
        assert not audience.allows(wiki_agent)
        admin_view = await web_audience(surface, extension, ADMIN_EMAIL)
        assert admin_view.admin
        assert wiki_agent in {agent.id for agent in admin_view.agents}
        granted = await GRANT.handler(
            _targeting(_tool_ctx(workspace_id, wiki_agent, admin_id), MEMBER_KIND, member_id),
            WebAccessInput(),
        )
        assert not granted.is_error
        widened = await web_audience(surface, extension, MEMBER_EMAIL)
        assert [agent.id for agent in widened.agents] == [main_agent, wiki_agent]
        assert widened.allows(wiki_agent)
        assert wiki_agent not in {agent.id for agent in widened.conversation_agents}
        other = await web_audience(surface, extension, "carol@example.com")
        assert [agent.id for agent in other.agents] == [main_agent]


async def test_a_workspace_visible_agent_joins_the_roster_without_a_grant(
    db: None, tmp_path
) -> None:
    """The workspace rung is the agent's own: flipping `visibility` to workspace puts the agent
    in every member's roster with no grant row, and flipping it back removes it."""
    workspace_id, main_agent, second_agent = await _seed()
    await _member(workspace_id, MEMBER_EMAIL)
    with ws(workspace_id):
        surface = _surface(workspace_id, tmp_path)
        extension = context_for(NAME, frozenset())
        before = await web_audience(surface, extension, MEMBER_EMAIL)
        assert [agent.id for agent in before.agents] == [main_agent]
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.id == second_agent)
                .values(visibility="workspace")
            )
        widened = await web_audience(surface, extension, MEMBER_EMAIL)
        assert [agent.id for agent in widened.agents] == [main_agent, second_agent]
        assert await granted_emails(web_extension().store) == {}


async def test_a_private_extension_conversation_grants_only_its_chat_agent(
    db: None, tmp_path
) -> None:
    workspace_id, main_agent, second_agent = await _seed()
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=second_agent,
                surface="extension:coding",
                queue_key=f"code-review:{member_id}:2026-08-15",
                member_id=member_id,
                audience=str(conversation_audience(member_id)),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        audience = await web_audience(
            _surface(workspace_id, tmp_path), context_for(NAME, frozenset()), MEMBER_EMAIL
        )
    assert [agent.id for agent in audience.agents] == [main_agent]
    assert [agent.id for agent in audience.conversation_agents] == [second_agent]
    assert [agent.id for agent in audience.chat_agents] == [main_agent, second_agent]
    assert not audience.allows(second_agent)


async def test_the_main_agent_needs_no_grant_and_revocation_only_clears_stale_rows(
    db: None, tmp_path
) -> None:
    """The main agent answers every member: granting it is a stated no-op that writes no row, and
    revoking it deletes any stale grant row without narrowing the audience — the default is by
    construction, not a deletable grant — while the revoke reply says the member still reaches
    the agent."""
    workspace_id, main_agent, _second_agent = await _seed()
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    with ws(workspace_id):
        extension = context_for(NAME, frozenset())
        granted = await GRANT.handler(
            _targeting(_tool_ctx(workspace_id, main_agent, admin_id), MEMBER_KIND, member_id),
            WebAccessInput(),
        )
        assert not granted.is_error
        assert "already answers every member" in granted.content[0].text
        assert await extension.store.list(AUDIENCE_PREFIX) == ()
        await extension.store.put(
            f"{AUDIENCE_PREFIX}{main_agent}/{MEMBER_EMAIL}", {"granted_by": str(admin_id)}
        )
        revoked = await REVOKE.handler(
            _targeting(_tool_ctx(workspace_id, main_agent, admin_id), MEMBER_KIND, member_id),
            WebAccessInput(),
        )
        assert not revoked.is_error
        assert "still reaches it in the portal" in revoked.content[0].text
        assert "no longer reaches" not in revoked.content[0].text
        assert await extension.store.list(AUDIENCE_PREFIX) == ()
        audience = await web_audience(_surface(workspace_id, tmp_path), extension, MEMBER_EMAIL)
        assert main_agent in {agent.id for agent in audience.agents}


async def test_non_admin_speaker_cannot_change_web_access(db: None, tmp_path) -> None:
    workspace_id, _, second_agent = await _seed()
    await _member(workspace_id, ADMIN_EMAIL, admin=True)
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    with ws(workspace_id):
        refused = await GRANT.handler(
            _targeting(_tool_ctx(workspace_id, second_agent, member_id), MEMBER_KIND, member_id),
            WebAccessInput(),
        )
        assert refused.is_error
        extension = context_for(NAME, frozenset())
        audience = await web_audience(_surface(workspace_id, tmp_path), extension, MEMBER_EMAIL)
        assert second_agent not in {agent.id for agent in audience.agents}


async def test_grant_requires_an_existing_member(db: None, tmp_path) -> None:
    workspace_id, _, second_agent = await _seed()
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    with ws(workspace_id):
        refused = await GRANT.handler(
            _targeting(_tool_ctx(workspace_id, second_agent, admin_id), MEMBER_KIND, uuid4()),
            WebAccessInput(),
        )
        assert refused.is_error
        assert "invite" in refused.content[0].text


async def test_speakerless_turn_cannot_change_web_access(db: None, tmp_path) -> None:
    workspace_id, _, second_agent = await _seed()
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    with ws(workspace_id):
        refused = await GRANT.handler(
            _targeting(_tool_ctx(workspace_id, second_agent, None), MEMBER_KIND, admin_id),
            WebAccessInput(),
        )
        assert refused.is_error


async def test_reading_a_private_transcript_is_admin_only_and_records_the_reader(
    db: None, tmp_path
) -> None:
    """The acknowledgement tool itself, the lane's dispatch target: an admin's call records the
    disclosure and names the subject back, while a non-admin bystander, an admin reading their own
    conversation, a speakerless turn, and a conversation of another agent each refuse — every gate
    holds inside the tool, not only at the route that prepares it."""
    workspace_id, main_agent, second_agent = await _seed()
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    bystander_id = await _member(workspace_id, "bystander@example.com")
    conversation_id, own_conversation_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        for seeded, subject, queue_key in (
            (conversation_id, member_id, "theirs"),
            (own_conversation_id, admin_id, "the admin's own"),
        ):
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=seeded,
                    workspace_id=workspace_id,
                    agent_id=main_agent,
                    surface="web",
                    queue_key=queue_key,
                    member_id=subject,
                    audience=f"member:{subject}",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    args = PrivateTranscriptInput()

    def reading(
        agent_id: UUID, speaker: UUID | None, conversation: UUID = conversation_id
    ) -> ToolContext:
        return _targeting(
            _tool_ctx(workspace_id, agent_id, speaker), CONVERSATION_KIND, conversation
        )

    with ws(workspace_id):
        # The non-admin is a bystander rather than the subject, because the admin gate is the first
        # thing a non-admin subject would hit — the reader's-own branch sits inside the writer,
        # behind that gate, so only an admin subject reaches it. `own_args` is that case.
        refused = await TRANSCRIPT.handler(reading(main_agent, bystander_id), args)
        assert refused.is_error
        assert "admin" in refused.content[0].text
        own = await TRANSCRIPT.handler(reading(main_agent, admin_id, own_conversation_id), args)
        assert own.is_error
        assert own.content[0].text != refused.content[0].text
        # The one refusal answers three branches — no such conversation on this agent, a room or
        # externally-shared channel, and the reader's own — so it may not claim the id is unknown.
        assert "your own" in own.content[0].text
        assert "has that id" not in own.content[0].text
        speakerless = await TRANSCRIPT.handler(reading(main_agent, None), args)
        assert speakerless.is_error
        assert "speaking member" in speakerless.content[0].text
        walled = await TRANSCRIPT.handler(reading(second_agent, admin_id), args)
        assert walled.is_error
        foreign = await TRANSCRIPT.handler(
            replace(reading(main_agent, admin_id), audience=foreign_room_audience("slack", "C1")),
            args,
        )
        assert foreign.is_error
        assert "another organization" in foreign.content[0].text
        async with workspace_tx() as connection:
            assert (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.transcript_access)
                )
            ).scalar_one() == 0

        recorded = await TRANSCRIPT.handler(reading(main_agent, admin_id), args)
        assert not recorded.is_error
        assert MEMBER_EMAIL in recorded.content[0].text
        # The record is the operator's, so the admin is told it exists and is told no reader.
        assert "are on the record" in recorded.content[0].text
        assert "can read that record" not in recorded.content[0].text
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.transcript_access.c.reader_member_id,
                        tables.transcript_access.c.subject_member_id,
                    )
                )
            ).all()
        assert [(row.reader_member_id, row.subject_member_id) for row in rows] == [
            (admin_id, member_id)
        ]


def test_the_disclosure_tool_promises_a_record_it_does_not_promise_a_reader() -> None:
    """No member surface lists the disclosure rows, so neither the tool's description nor the
    result it returns may offer a member one. What each still owes: the description says the act
    is admin-only, what it records, and that the transcript is read in the portal; the result names
    the subject and says the record exists."""
    described = TRANSCRIPT.description
    assert "workspace admins only" in described
    assert "Records who read it, whose it was, and when." in described
    assert "read in the portal, not here" in described
    assert "can read that record" not in described


async def test_open_conversation_lands_a_lost_race_on_the_winner_and_its_row(
    db: None, tmp_path
) -> None:
    """Two first messages racing one queue key converge: the loser's pre-written chat row is
    deleted, the winner's conversation and row stand, and the loser's caller receives the
    winner's identity and title."""
    workspace_id, main_agent, _second = await _seed()
    member_id = await _member(workspace_id, MEMBER_EMAIL)
    with ws(workspace_id):
        surface = _surface(workspace_id, tmp_path)
        store = context_for(NAME, frozenset()).store
        key = f"{main_agent}/{MEMBER_EMAIL}"
        winner, winner_title = await _open_conversation(
            surface, store, main_agent, member_id, MEMBER_EMAIL, key, "first words", ()
        )
        loser, loser_title = await _open_conversation(
            surface, store, main_agent, member_id, MEMBER_EMAIL, key, "second words", ()
        )
        assert (loser, loser_title) == (winner, winner_title)
        assert winner_title == "first words"
        rows = await store.list("chat/")
        assert [key for key, _ in rows] == [f"chat/{winner}"]
