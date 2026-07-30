"""The web surface's audience authority: the grant/revoke chat verbs writing the extension's own
store, and the resolution the portal reads — admins see every agent, everyone else the main agent
plus exactly the non-main agents granted to their email."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_web.audience import (
    AUDIENCE_PREFIX,
    WEB_ACCESS_TOOLS,
    WebAccessInput,
    granted_emails,
    web_audience,
    web_extension,
)
from ufo_ext_web.manifest import NAME
from ufo_ext_web.surface import SURFACE_WEB
from ufo_testsupport.surfaces import EMPTY_SKILL_REGISTRY, no_user_skills

from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.surface import SurfaceContext
from ufo.hub import InProcessHub
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.surfaces.admission import Admission, MemberAdmission
from ufo.surfaces.hub_tail import HubTailer
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

GRANT = WEB_ACCESS_TOOLS[0]
REVOKE = WEB_ACCESS_TOOLS[1]
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
        _credentials=None,
        _declared_slots=(),
        _artifact_token_secret="",
        _skills=EMPTY_SKILL_REGISTRY,
        _user_skills=no_user_skills,
        _public_base_url=None,
        _ingress_public_url=None,
        _deploy_sandbox_internet=False,
        _models=("auto", "claude-opus-4-8", "claude-sonnet-5"),
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
    await _member(workspace_id, MEMBER_EMAIL)
    await _member(workspace_id, "zed@example.com")
    with ws(workspace_id):
        for agent_id, email in (
            (second_agent, "zed@example.com"),
            (second_agent, MEMBER_EMAIL),
            (third_agent, "zed@example.com"),
        ):
            granted = await GRANT.handler(
                _tool_ctx(workspace_id, agent_id, admin_id),
                WebAccessInput(email=email, user_description="granting access"),
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
    await _member(workspace_id, MEMBER_EMAIL)
    with ws(workspace_id):
        surface = _surface(workspace_id, tmp_path)
        extension = context_for(NAME, frozenset())
        granted = await GRANT.handler(
            _tool_ctx(workspace_id, second_agent, admin_id),
            WebAccessInput(email=MEMBER_EMAIL.upper(), user_description="granting access"),
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
            _tool_ctx(workspace_id, second_agent, admin_id),
            WebAccessInput(email=MEMBER_EMAIL, user_description="revoking access"),
        )
        assert not revoked.is_error
        remaining = await web_audience(surface, extension, MEMBER_EMAIL)
        assert [agent.id for agent in remaining.agents] == [main_agent]


async def test_the_main_agent_needs_no_grant_and_revocation_only_clears_stale_rows(
    db: None, tmp_path
) -> None:
    """The main agent answers every member: granting it is a stated no-op that writes no row, and
    revoking it deletes any stale grant row without narrowing the audience — the default is by
    construction, not a deletable grant — while the revoke reply says the member still reaches
    the agent."""
    workspace_id, main_agent, _second_agent = await _seed()
    admin_id = await _member(workspace_id, ADMIN_EMAIL, admin=True)
    await _member(workspace_id, MEMBER_EMAIL)
    with ws(workspace_id):
        extension = context_for(NAME, frozenset())
        granted = await GRANT.handler(
            _tool_ctx(workspace_id, main_agent, admin_id),
            WebAccessInput(email=MEMBER_EMAIL, user_description="granting access"),
        )
        assert not granted.is_error
        assert "already answers every member" in granted.content[0].text
        assert await extension.store.list(AUDIENCE_PREFIX) == ()
        await extension.store.put(
            f"{AUDIENCE_PREFIX}{main_agent}/{MEMBER_EMAIL}", {"granted_by": str(admin_id)}
        )
        revoked = await REVOKE.handler(
            _tool_ctx(workspace_id, main_agent, admin_id),
            WebAccessInput(email=MEMBER_EMAIL, user_description="revoking access"),
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
            _tool_ctx(workspace_id, second_agent, member_id),
            WebAccessInput(email=MEMBER_EMAIL, user_description="granting access"),
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
            _tool_ctx(workspace_id, second_agent, admin_id),
            WebAccessInput(email="carol@example.com", user_description="granting access"),
        )
        assert refused.is_error
        assert "invite" in refused.content[0].text


async def test_speakerless_turn_cannot_change_web_access(db: None, tmp_path) -> None:
    workspace_id, _, second_agent = await _seed()
    await _member(workspace_id, ADMIN_EMAIL, admin=True)
    with ws(workspace_id):
        refused = await GRANT.handler(
            _tool_ctx(workspace_id, second_agent, None),
            WebAccessInput(email=ADMIN_EMAIL, user_description="granting access"),
        )
        assert refused.is_error


async def test_every_web_access_tool_takes_a_required_user_description() -> None:
    for tool in WEB_ACCESS_TOOLS:
        field = tool.input_model.model_fields["user_description"]
        assert field.is_required()


@pytest.mark.parametrize("tool", WEB_ACCESS_TOOLS, ids=lambda tool: tool.name)
def test_web_access_tools_are_side_effecting(tool) -> None:
    assert tool.side_effecting
