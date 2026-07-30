"""Hosted sites end to end: a deploy registers the port it just proved, the frame gates every
viewer on that site's visibility, and the `site` object kind is the same act from chat.

Each test drives the real seams against the real database on both dialects the fixture
parametrizes — the real tools under the real `ExtensionContext` `turn_tools` binds them, the sites
surface mounted the way `serve` mounts it, the real object verbs over the real registry. One
stand-in appears and nothing is ever asserted about it: the sandbox, because a site's bytes are not
under test and what the tools said to it is not the contract — the registry rows, the payloads, and
the rendered frame are. The ingress URL the frame embeds is really minted, and its token really
verifies."""

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.objects import SITE_KIND, site_object_name
from ufo_ext_sites.store import (
    HostedSites,
    NotTheSiteCreator,
    UnhostNeedsASpeaker,
    hosted_site,
)
from ufo_ext_sites.surface import (
    FRAME_PATH,
    LOGIN_PATH,
    SESSION_COOKIE,
    UNCONFIGURED_BODY,
    VISIBILITY_BADGES,
    SiteHostingUnconfigured,
    site_token,
)
from ufo_ext_sites.tools import (
    APP_SERVE_PORT,
    DEPLOY_WEBSITE_TOOL,
    PUBLISH_WEBSITE_TOOL,
)
from ufo_testsupport.surfaces import EMPTY_SKILL_REGISTRY, no_user_skills

from ufo.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.ext.loader import turn_tools
from ufo.hub import InProcessHub
from ufo.objects import AdminRequired, UnknownObject, VerbNotSupported
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.ingress_token import (
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    verify_ingress_token,
)
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ExecResult, ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.serve import RESERVED_HOST_PREFIXES, _mount_shared_surfaces
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

TOKEN_SECRET = "sites-surface-token-secret"
PUBLIC_BASE_URL = "https://ufo.example.test"
DEPLOY_MODELS = ("auto", "claude-opus-4-8")
INGRESS_HOST = "sites.example.test"
INGRESS_BASE_URL = f"https://{INGRESS_HOST}"
OWNER_EMAIL = "owner@example.com"
OTHER_EMAIL = "other@example.com"
TOOL_NARRATION = "putting the site online"
SITE = "marketing"


@dataclass
class FakeSandbox:
    """Every command succeeds, so the readiness probe passes and registration runs. It records
    nothing: what the tools said to the sandbox is not the contract these tests hold."""

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)


@dataclass(frozen=True)
class Workspace:
    """One seeded workspace: what every call in a test needs to name itself."""

    id: UUID
    agent_id: UUID


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("the website tools must not spawn")


@pytest.fixture(autouse=True)
def token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one deploy secret both the site token and the session bearer are signed over."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, TOKEN_SECRET)


@dataclass(frozen=True)
class Deployment:
    """One seeded workspace behind the sites surface, mounted twice: as a deploy that configures a
    sandbox ingress, and as one that configures none."""

    workspace: Workspace
    client: AsyncClient
    unhosted: AsyncClient


@pytest.fixture
async def deployment(db: None, dbos_launched: Config, tmp_path: Path) -> AsyncIterator[Deployment]:
    """The sites surface mounted exactly as `serve` mounts a shared-fleet surface: its own
    `identify` resolves each request's workspace from the site token in the URL."""
    workspace = await _seed_workspace()
    dbos_client = DBOSClient(system_database_url=dbos_launched.database.system_url)

    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=tmp_path / "workspaces",
    )

    def mounted(ingress_public_url: str | None) -> AsyncClient:
        app = FastAPI()
        _mount_shared_surfaces(
            app,
            (sites_manifest(),),
            None,
            FilesystemBlobStore(root=tmp_path),
            sandboxes,
            InProcessHub(),
            dbos_client,
            "",
            PUBLIC_BASE_URL,
            ingress_public_url,
            DEPLOY_MODELS,
            skills=EMPTY_SKILL_REGISTRY,
            user_skills=no_user_skills,
        )
        return AsyncClient(transport=ASGITransport(app=app), base_url=PUBLIC_BASE_URL)

    async with mounted(INGRESS_BASE_URL) as client, mounted(None) as unhosted:
        yield Deployment(workspace=workspace, client=client, unhosted=unhosted)
    dbos_client.destroy()


async def _seed_workspace() -> Workspace:
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
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Workspace(id=workspace_id, agent_id=agent_id)


async def _seed_member(
    workspace: Workspace, email: str, is_admin: bool = False
) -> tuple[UUID, str]:
    """A member and the signed bearer their `ufo_session` cookie carries."""
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace.id,
                email=email,
                is_admin=is_admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id, mint_token(TOKEN_SECRET, str(workspace.id), email, timedelta(hours=1))


async def _seed_conversation(
    workspace: Workspace, audience: Audience, member_id: UUID | None
) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace.id,
                agent_id=workspace.agent_id,
                surface="web",
                queue_key=uuid4().hex,
                member_id=member_id,
                audience=str(audience),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


def _tool(name: str, audience: Audience) -> tuple[ToolDef, ToolContext]:
    """One real tool and the context the engine dispatches it with, its extension bound."""
    tools, ext_by_tool = turn_tools((sites_manifest(),), None, audience=audience)
    tool = next(entry for entry in tools if entry.name == name)
    return tool, ToolContext(
        sandbox=FakeSandbox(),
        blob=FilesystemBlobStore(root=Path("/nonexistent")),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=0,
            status="running",
            inbound="build me a site",
            created_at=datetime(2026, 7, 28, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience=audience,
        artifact_token_secret="",
        public_base_url=PUBLIC_BASE_URL,
        ext=ext_by_tool.get(name),
    )


def _bind(
    ctx: ToolContext, workspace: Workspace, conversation_id: UUID, speaker_member_id: UUID | None
) -> ToolContext:
    return replace(
        ctx,
        turn=ctx.turn.model_copy(
            update={
                "workspace_id": workspace.id,
                "conversation_id": conversation_id,
                "agent_id": workspace.agent_id,
            }
        ),
        speaker_member_id=speaker_member_id,
    )


async def _dispatch(tool: ToolDef, ctx: ToolContext, **args: object) -> dict[str, object]:
    result = await tool.handler(
        ctx, tool.input_model.model_validate({"user_description": TOOL_NARRATION, **args})
    )
    payload = json.loads(result.content[0].text)
    assert isinstance(payload, dict)
    return payload


async def _deploy(
    workspace: Workspace,
    conversation_id: UUID,
    audience: Audience,
    speaker_member_id: UUID | None,
    *,
    site: str = SITE,
    visibility: str | None = None,
) -> dict[str, object]:
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    args: dict[str, object] = {
        "project_path": "/workspace/dist",
        "site_name": site,
        "entry_point": "index.html",
    }
    if visibility is not None:
        args["visibility"] = visibility
    with ws(workspace.id):
        return await _dispatch(
            tool, _bind(ctx, workspace, conversation_id, speaker_member_id), **args
        )


async def _stored(workspace: Workspace) -> tuple[sa.Row, ...]:
    async with workspace_tx() as connection:
        return tuple(
            (
                await connection.execute(
                    sa.select(hosted_site).where(hosted_site.c.workspace_id == workspace.id)
                )
            ).all()
        )


def _cookie(token: str) -> dict[str, str]:
    return {"cookie": f"{SESSION_COOKIE}={token}"}


def _embedded(body: str) -> str:
    """The origin the frame embedded: the iframe's own src."""
    found = re.search(r'<iframe src="([^"]+)"', body)
    assert found is not None, body
    return found.group(1)


def _csrf(body: str) -> str:
    found = re.search(r'name=csrf value="([^"]+)"', body)
    assert found is not None, body
    return found.group(1)


@pytest.mark.parametrize(
    ("audience_of", "expected"),
    [
        ("member", "private"),
        ("room", "workspace"),
        ("shared", "workspace"),
        ("foreign", "private"),
    ],
)
async def test_default_visibility_follows_the_conversation_audience(
    db: None, audience_of: str, expected: str
) -> None:
    """The RFC's table, including the one that is doctrine: a site built in a sealed external room
    defaults private, never into the whole company."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = {
        "member": conversation_audience(member_id),
        "room": room_audience("slack", "C0FFEE"),
        "shared": SHARED_AUDIENCE,
        "foreign": foreign_room_audience("slack", "C0NNECT"),
    }[audience_of]
    conversation_id = await _seed_conversation(
        workspace, audience, member_id if audience_of == "member" else None
    )

    payload = await _deploy(workspace, conversation_id, audience, member_id)

    assert payload["visibility"] == expected
    (row,) = await _stored(workspace)
    assert (row.name, row.port, row.visibility) == (SITE, APP_SERVE_PORT, expected)
    assert row.creator_member_id == member_id
    assert row.conversation_id == conversation_id


async def test_an_explicit_visibility_wins_and_a_redeploy_keeps_it(db: None) -> None:
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)

    hosted = await _deploy(workspace, conversation_id, audience, member_id, visibility="public")
    assert hosted["visibility"] == "public"

    again = await _deploy(workspace, conversation_id, audience, member_id)
    assert again["visibility"] == "public"
    assert again["site_url"] == hosted["site_url"]
    (row,) = await _stored(workspace)
    assert row.visibility == "public"


async def test_registering_retires_another_site_on_the_same_port(db: None) -> None:
    """A port serves one origin, so the newer name takes it and the older stops resolving —
    otherwise the old link would serve the new deploy's bytes."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)

    await _deploy(workspace, conversation_id, audience, member_id, site="first")
    await _deploy(workspace, conversation_id, audience, member_id, site="second")

    assert [row.name for row in await _stored(workspace)] == ["second"]


async def test_only_the_creator_may_re_gate_a_site_through_a_deploy(db: None) -> None:
    """The visibility column has one authorization rule, and a re-deploy is not a second door to
    it — a teammate re-deploying the same site cannot widen or narrow what its creator chose."""
    workspace = await _seed_workspace()
    creator_id, _creator_token = await _seed_member(workspace, OWNER_EMAIL)
    other_id, _other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = room_audience("slack", "C0FFEE")
    conversation_id = await _seed_conversation(workspace, audience, None)
    await _deploy(workspace, conversation_id, audience, creator_id)

    with pytest.raises(NotTheSiteCreator, match="who can open it"):
        await _deploy(workspace, conversation_id, audience, other_id, visibility="public")

    (row,) = await _stored(workspace)
    assert row.visibility == "workspace"


async def test_a_teammate_may_redeploy_without_touching_the_visibility(db: None) -> None:
    """The other half of the same rule: a re-deploy that names no visibility is not a re-gating, so
    it goes through and leaves the creator's choice exactly as it was."""
    workspace = await _seed_workspace()
    creator_id, _creator_token = await _seed_member(workspace, OWNER_EMAIL)
    other_id, _other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = room_audience("slack", "C0FFEE")
    conversation_id = await _seed_conversation(workspace, audience, None)
    await _deploy(workspace, conversation_id, audience, creator_id, visibility="private")

    hosted = await _deploy(workspace, conversation_id, audience, other_id)

    assert hosted["visibility"] == "private"
    (row,) = await _stored(workspace)
    assert (row.visibility, row.creator_member_id) == ("private", creator_id)


async def test_a_speakerless_turn_cannot_name_a_visibility(db: None) -> None:
    """Choosing who can open a site is a granting act, so it needs a live member — a scheduled turn
    acting on the creator's behalf may re-deploy but never re-gate, the same rule the `site` kind
    enforces on apply."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, conversation_id, audience, creator_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    scheduled = replace(
        _bind(ctx, workspace, conversation_id, None), on_behalf_of_member_id=creator_id
    )

    with ws(workspace.id), pytest.raises(RuntimeError, match="needs a live member"):
        await _dispatch(
            tool,
            scheduled,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
            visibility="public",
        )

    (row,) = await _stored(workspace)
    assert row.visibility == "private"


async def test_a_speakerless_turn_may_still_redeploy(db: None) -> None:
    """The same turn without a visibility argument is not a granting act: it re-deploys and the
    setting stands."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, conversation_id, audience, creator_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    scheduled = replace(
        _bind(ctx, workspace, conversation_id, None), on_behalf_of_member_id=creator_id
    )

    with ws(workspace.id):
        payload = await _dispatch(
            tool,
            scheduled,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
        )

    assert payload["visibility"] == "private"
    (row,) = await _stored(workspace)
    assert row.visibility == "private"


async def test_a_speakerless_turn_cannot_unhost_a_site(db: None) -> None:
    """Unhosting stops every viewer, so it is a revocation and wants a live member too — the delete
    half of the same rule as apply."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    name = str((await _deploy(workspace, conversation_id, audience, creator_id))["site"])
    tool, ctx = _tool("object_delete", audience)
    scheduled = replace(
        _bind(ctx, workspace, conversation_id, None), on_behalf_of_member_id=creator_id
    )

    with ws(workspace.id), pytest.raises(AdminRequired):
        await _dispatch(tool, scheduled, kind=SITE_KIND, name=name)

    assert len(await _stored(workspace)) == 1


async def test_a_subagent_turn_cannot_host_a_site(db: None) -> None:
    """A subagent runs in its own conversation with its own disposable sandbox, and the link
    resolves a conversation to the sandbox serving it — so a site registered there would die with
    that sandbox and a rebuild would mint a different link. The refusal names where to deploy
    instead, and nothing is written."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    child = _bind(ctx, workspace, conversation_id, member_id)
    child = replace(
        child, turn=child.turn.model_copy(update={"subagent_profile": "website_building"})
    )

    with ws(workspace.id), pytest.raises(RuntimeError, match="subagent turn cannot host"):
        await _dispatch(
            tool,
            child,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
        )

    assert await _stored(workspace) == ()


async def test_a_teammate_cannot_unhost_a_site_by_taking_its_port(db: None) -> None:
    """Retiring the site on a taken port is an unhost, so a deploy cannot perform it on someone
    else's site — otherwise the gate on the visibility column and the `site` kind's delete rule both
    have a way around them."""
    workspace = await _seed_workspace()
    creator_id, _creator_token = await _seed_member(workspace, OWNER_EMAIL)
    other_id, _other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = room_audience("slack", "C0FFEE")
    conversation_id = await _seed_conversation(workspace, audience, None)
    await _deploy(workspace, conversation_id, audience, creator_id, site="marketing")

    with pytest.raises(NotTheSiteCreator, match="another member deployed"):
        await _deploy(workspace, conversation_id, audience, other_id, site="pricing")

    (row,) = await _stored(workspace)
    assert (row.name, row.creator_member_id) == ("marketing", creator_id)


async def test_a_speakerless_turn_cannot_take_a_port_from_a_site(db: None) -> None:
    """Even the creator's own scheduled turn cannot displace their site: unhosting needs a live
    member, and taking the port is unhosting."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, conversation_id, audience, creator_id, site="marketing")
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    scheduled = replace(
        _bind(ctx, workspace, conversation_id, None), on_behalf_of_member_id=creator_id
    )

    with ws(workspace.id), pytest.raises(UnhostNeedsASpeaker, match="needs a live member"):
        await _dispatch(
            tool,
            scheduled,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )

    (row,) = await _stored(workspace)
    assert row.name == "marketing"


async def test_a_site_in_another_conversation_keeps_its_own_name_and_link(db: None) -> None:
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    first = await _seed_conversation(workspace, audience, member_id)
    second = await _seed_conversation(workspace, audience, member_id)

    one = await _deploy(workspace, first, audience, member_id)
    two = await _deploy(workspace, second, audience, member_id)

    assert one["site_url"] != two["site_url"]
    assert one["site"] != two["site"]
    assert {row.conversation_id for row in await _stored(workspace)} == {first, second}


async def test_hosting_without_an_acting_member_fails_loud(db: None) -> None:
    """A site needs an owner: the gate every visibility decision is made against."""
    workspace = await _seed_workspace()
    conversation_id = await _seed_conversation(workspace, SHARED_AUDIENCE, None)

    with pytest.raises(RuntimeError, match="owner"):
        await _deploy(workspace, conversation_id, SHARED_AUDIENCE, None)

    assert await _stored(workspace) == ()


async def test_deploy_serves_the_static_output_and_hosts_it(db: None) -> None:
    """The sandbox-local url still names the running server; the hosted link joins it."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    bound = _bind(ctx, workspace, conversation_id, member_id)

    with ws(workspace.id):
        payload = await _dispatch(
            tool,
            bound,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
        )

    assert payload["url"] == f"http://localhost:{APP_SERVE_PORT}"
    assert payload["entry_point"] == "index.html"
    assert str(payload["site_url"]).startswith(f"{PUBLIC_BASE_URL}{FRAME_PATH}/")
    (row,) = await _stored(workspace)
    assert (row.name, row.port) == (SITE, APP_SERVE_PORT)


async def test_a_published_app_installs_serves_and_hosts(db: None) -> None:
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = room_audience("slack", "C0FFEE")
    conversation_id = await _seed_conversation(workspace, audience, None)
    tool, ctx = _tool(PUBLISH_WEBSITE_TOOL, audience)
    bound = _bind(ctx, workspace, conversation_id, member_id)

    with ws(workspace.id):
        payload = await _dispatch(
            tool,
            bound,
            project_path="/workspace/app",
            dist_path="/workspace/app/dist",
            app_name="Team Dashboard",
            install_command="npm ci",
        )

    assert payload["site_name"] == "team-dashboard"
    assert payload["visibility"] == "workspace"
    assert payload["site_url"] == f"{PUBLIC_BASE_URL}{FRAME_PATH}/" + site_token(
        workspace.id, conversation_id, "team-dashboard"
    )
    (row,) = await _stored(workspace)
    assert row.name == "team-dashboard"


async def test_a_dm_site_opens_for_its_creator_and_hides_from_another_member(
    deployment: Deployment,
) -> None:
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    _other_id, other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)
    link = str(hosted["site_url"])

    opened = await client.get(link, headers=_cookie(creator_token))
    assert opened.status_code == 200
    assert "<select name=visibility>" in opened.text
    embedded = _embedded(opened.text)
    assert embedded.startswith("https://") and INGRESS_HOST in embedded
    assert (
        'sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals '
        'allow-downloads allow-pointer-lock"'
    ) in opened.text
    assert "allow-top-navigation" not in opened.text
    view = verify_ingress_token(
        embedded.rpartition(f"{INGRESS_VIEW_PATH}/")[2], datetime.now(UTC), INGRESS_VIEW_KIND
    )
    assert (view.workspace_id, view.conversation_id, view.port) == (
        workspace.id,
        conversation_id,
        APP_SERVE_PORT,
    )

    denied = await client.get(link, headers=_cookie(other_token))
    assert denied.status_code == 404
    assert SITE not in denied.text


async def test_flipping_to_workspace_opens_the_frame_for_another_member(
    deployment: Deployment,
) -> None:
    """The selector's POST and the read gate are both ends of the one visibility column."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    _other_id, other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])
    frame = await client.get(link, headers=_cookie(creator_token))

    flipped = await client.post(
        f"{link}/visibility",
        data={"visibility": "workspace", "csrf": _csrf(frame.text)},
        headers=_cookie(creator_token),
    )
    assert flipped.status_code == 303
    assert flipped.headers["location"] == link.removeprefix(PUBLIC_BASE_URL)

    shared = await client.get(link, headers=_cookie(other_token))
    assert shared.status_code == 200
    assert INGRESS_HOST in _embedded(shared.text)
    assert VISIBILITY_BADGES["workspace"] in shared.text
    assert "<select name=visibility>" not in shared.text


async def test_the_visibility_post_refuses_a_non_creator_and_a_missing_csrf(
    deployment: Deployment,
) -> None:
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    _other_id, other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = room_audience("slack", "C0FFEE")
    conversation_id = await _seed_conversation(workspace, audience, None)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])
    frame = await client.get(link, headers=_cookie(creator_token))
    csrf = _csrf(frame.text)

    by_other = await client.post(
        f"{link}/visibility",
        data={"visibility": "public", "csrf": csrf},
        headers=_cookie(other_token),
    )
    assert by_other.status_code == 404

    without_csrf = await client.post(
        f"{link}/visibility", data={"visibility": "public"}, headers=_cookie(creator_token)
    )
    assert without_csrf.status_code == 403

    rotated = mint_token(TOKEN_SECRET, str(workspace.id), OWNER_EMAIL, timedelta(hours=2))
    assert rotated != creator_token
    other_session = await client.post(
        f"{link}/visibility",
        data={"visibility": "public", "csrf": csrf},
        headers=_cookie(rotated),
    )
    assert other_session.status_code == 403

    unknown_level = await client.post(
        f"{link}/visibility",
        data={"visibility": "everyone", "csrf": csrf},
        headers=_cookie(creator_token),
    )
    assert unknown_level.status_code == 400

    (row,) = await _stored(workspace)
    assert row.visibility == "workspace"


async def test_an_unauthenticated_viewer_is_sent_to_sign_in_unless_the_site_is_public(
    deployment: Deployment,
) -> None:
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])

    anonymous = await client.get(link)
    assert anonymous.status_code == 200
    assert "not signed in to the workspace" in anonymous.text
    assert '<a href="/login">Sign in</a>' in anonymous.text
    assert LOGIN_PATH in RESERVED_HOST_PREFIXES
    assert "<iframe" not in anonymous.text

    frame = await client.get(link, headers=_cookie(creator_token))
    await client.post(
        f"{link}/visibility",
        data={"visibility": "public", "csrf": _csrf(frame.text)},
        headers=_cookie(creator_token),
    )

    public = await client.get(link)
    assert public.status_code == 200
    assert INGRESS_HOST in _embedded(public.text)
    assert "not signed in" not in public.text


async def test_an_unknown_or_forged_token_is_the_same_404_as_a_hidden_site(
    deployment: Deployment,
) -> None:
    client, workspace = deployment.client, deployment.workspace
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hidden = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])

    unregistered = site_token(workspace.id, conversation_id, "never-deployed")
    for token in (unregistered, "not-a-token", site_token(workspace.id, uuid4(), SITE)):
        response = await client.get(f"{FRAME_PATH}/{token}")
        assert response.status_code == 404

    tampered = await client.get(f"{hidden}x")
    assert tampered.status_code == 404
    assert tampered.text == (await client.get(f"{FRAME_PATH}/{unregistered}")).text


async def test_an_unconfigured_ingress_renders_the_frame_without_the_site(
    deployment: Deployment,
) -> None:
    """A deploy with no ingress has no origin to embed, so the frame says so — it neither crashes
    nor frames a dead origin."""
    workspace = deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])

    unconfigured = await deployment.unhosted.get(link, headers=_cookie(creator_token))
    assert unconfigured.status_code == 200
    assert "<iframe" not in unconfigured.text
    assert UNCONFIGURED_BODY in unconfigured.text
    assert "<select name=visibility>" in unconfigured.text

    hosted = await deployment.client.get(link, headers=_cookie(creator_token))
    assert INGRESS_HOST in _embedded(hosted.text)


async def test_a_bearer_from_another_workspace_never_opens_this_workspace_s_site(
    deployment: Deployment,
) -> None:
    """The confused-deputy seam: the workspace comes from the token in the URL, the viewer from a
    cookie the request brings, and only `verify_token(token, ctx.workspace_id)` joins them. The
    stranger carries the *same* email as a member of the hosting workspace, so nothing but that join
    can shut them out — an unscoped cookie check would hand them a member here and open the site."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = room_audience("slack", "C0FFEE")
    conversation_id = await _seed_conversation(workspace, audience, None)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])
    stranger = await _seed_workspace()
    _stranger_id, stranger_token = await _seed_member(stranger, OWNER_EMAIL)

    foreign = await client.get(link, headers=_cookie(stranger_token))

    assert "<iframe" not in foreign.text
    assert SITE not in foreign.text
    assert INGRESS_HOST not in foreign.text
    assert (await client.get(link, headers=_cookie(creator_token))).status_code == 200


async def test_a_deploy_without_a_public_base_url_fails_loud(db: None) -> None:
    """The deliverable every description promises is the link, so a deploy that cannot mint one says
    so instead of returning a payload with the field missing."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    unconfigured = replace(_bind(ctx, workspace, conversation_id, member_id), public_base_url=None)

    with ws(workspace.id), pytest.raises(SiteHostingUnconfigured, match="public_base_url"):
        await _dispatch(
            tool,
            unconfigured,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
        )

    assert await _stored(workspace) == ()


async def test_the_site_kind_reads_and_regates_a_hosted_site(db: None) -> None:
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)
    name = str(hosted["site"])
    assert name == site_object_name(conversation_id, SITE)

    with ws(workspace.id):
        listed = await _verb("object_list", workspace, conversation_id, creator_id, kind=SITE_KIND)
        fetched = await _get(workspace, conversation_id, creator_id, name)
        applied = await _verb(
            "object_apply",
            workspace,
            conversation_id,
            creator_id,
            manifest=yaml.safe_dump(
                {"kind": SITE_KIND, "name": name, "spec": {"visibility": "workspace"}}
            ),
        )

    assert [row["name"] for row in listed["objects"]] == [name]
    assert fetched["spec"] == {"visibility": "private"}
    assert fetched["status"]["site_url"] == hosted["site_url"]
    assert fetched["status"]["port"] == APP_SERVE_PORT
    assert fetched["links"] == [
        {"relation": "created_in", "target": {"kind": "conversation", "name": str(conversation_id)}}
    ]
    assert applied["result"] == "updated"
    (row,) = await _stored(workspace)
    assert row.visibility == "workspace"


async def test_the_site_kind_refuses_create_naming_the_deploy(db: None) -> None:
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)

    with ws(workspace.id), pytest.raises(VerbNotSupported, match="deploy_website"):
        await _verb(
            "object_apply",
            workspace,
            conversation_id,
            creator_id,
            manifest=yaml.safe_dump(
                {"kind": SITE_KIND, "name": "invented", "spec": {"visibility": "public"}}
            ),
        )

    assert await _stored(workspace) == ()


async def test_deleting_the_object_unhosts_the_site(
    deployment: Deployment,
) -> None:
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)
    link = str(hosted["site_url"])
    assert (await client.get(link, headers=_cookie(creator_token))).status_code == 200

    with ws(workspace.id):
        deleted = await _verb(
            "object_delete",
            workspace,
            conversation_id,
            creator_id,
            kind=SITE_KIND,
            name=str(hosted["site"]),
        )

    assert deleted["deleted"] is True
    assert await _stored(workspace) == ()
    assert (await client.get(link, headers=_cookie(creator_token))).status_code == 404


async def test_another_members_private_site_is_invisible_and_unchangeable(db: None) -> None:
    workspace = await _seed_workspace()
    creator_id, _creator_token = await _seed_member(workspace, OWNER_EMAIL)
    other_id, _other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)
    name = str(hosted["site"])

    with ws(workspace.id):
        listed = await _verb("object_list", workspace, conversation_id, other_id, kind=SITE_KIND)
        with pytest.raises(UnknownObject):
            await _verb(
                "object_apply",
                workspace,
                conversation_id,
                other_id,
                manifest=yaml.safe_dump(
                    {"kind": SITE_KIND, "name": name, "spec": {"visibility": "public"}}
                ),
            )

    assert listed["objects"] == []
    (row,) = await _stored(workspace)
    assert row.visibility == "private"


async def test_an_admin_may_narrow_a_shared_site_but_never_widen_one(db: None) -> None:
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    admin_id, _admin_token = await _seed_member(workspace, "admin@example.com", is_admin=True)
    audience = room_audience("slack", "C0FFEE")
    conversation_id = await _seed_conversation(workspace, audience, None)
    name = str((await _deploy(workspace, conversation_id, audience, creator_id))["site"])

    def widen(level: str) -> str:
        return yaml.safe_dump({"kind": SITE_KIND, "name": name, "spec": {"visibility": level}})

    with ws(workspace.id):
        with pytest.raises(AdminRequired):
            await _verb(
                "object_apply", workspace, conversation_id, admin_id, manifest=widen("public")
            )
        await _verb("object_apply", workspace, conversation_id, admin_id, manifest=widen("private"))

    (row,) = await _stored(workspace)
    assert row.visibility == "private"


async def test_a_speakerless_turn_cannot_change_who_can_open_a_site(db: None) -> None:
    """Re-gating a site discloses it, so it needs a live member — never a scheduled turn."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    name = str((await _deploy(workspace, conversation_id, audience, creator_id))["site"])
    tool, ctx = _tool("object_apply", audience)
    scheduled = replace(
        _bind(ctx, workspace, conversation_id, None), on_behalf_of_member_id=creator_id
    )

    with ws(workspace.id), pytest.raises(AdminRequired):
        await _dispatch(
            tool,
            scheduled,
            manifest=yaml.safe_dump(
                {"kind": SITE_KIND, "name": name, "spec": {"visibility": "public"}}
            ),
        )

    (row,) = await _stored(workspace)
    assert row.visibility == "private"


async def test_the_registry_is_scoped_to_its_own_workspace(db: None) -> None:
    """Every read the frame and the kind make goes through this one scoping."""
    workspace = await _seed_workspace()
    other = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, conversation_id, audience, creator_id)

    with ws(other.id):
        assert await HostedSites(other.id, workspace_tx).all() == ()
        assert await HostedSites(other.id, workspace_tx).read(conversation_id, SITE) is None
    with ws(workspace.id):
        assert len(await HostedSites(workspace.id, workspace_tx).all()) == 1


async def _verb(
    verb: str,
    workspace: Workspace,
    conversation_id: UUID,
    speaker_member_id: UUID | None,
    **args: object,
) -> dict[str, object]:
    tool, ctx = _tool(verb, conversation_audience(speaker_member_id))
    return await _dispatch(tool, _bind(ctx, workspace, conversation_id, speaker_member_id), **args)


async def _get(
    workspace: Workspace, conversation_id: UUID, speaker_member_id: UUID, name: str
) -> dict[str, object]:
    tool, ctx = _tool("object_get", conversation_audience(speaker_member_id))
    result = await tool.handler(
        _bind(ctx, workspace, conversation_id, speaker_member_id),
        tool.input_model.model_validate(
            {"user_description": TOOL_NARRATION, "kind": SITE_KIND, "name": name}
        ),
    )
    fetched = yaml.safe_load(result.content[0].text)
    assert isinstance(fetched, dict)
    return fetched
