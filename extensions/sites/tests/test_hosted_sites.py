"""Hosted sites end to end: a deploy registers the port it just proved, the frame gates every
viewer on that site's visibility, and the `site` object kind is the same act from chat.

Each test drives the real seams against the real database on both dialects the fixture
parametrizes — the real tools under the real `ExtensionContext` `turn_tools` binds them, the sites
surface mounted the way `serve` mounts it, the real object verbs over the real registry. One
stand-in appears and nothing is ever asserted about it: the sandbox, because a site's bytes are not
under test and what the tools said to it is not the contract — the registry rows, the payloads, and
the rendered frame are. The ingress URL the frame embeds is really minted, and its token really
verifies."""

import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from subprocess import CompletedProcess
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from ufo_ext_sites.conversation_slot import SITES_SLOT
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.objects import SITE_KIND, site_object_name
from ufo_ext_sites.share_card import (
    CARD_EXTENSION,
    CARD_HEIGHT,
    CARD_MEDIA_TYPE,
    CARD_NAME,
    CARD_WIDTH,
)
from ufo_ext_sites.source import CLAIM_TREE_PROG
from ufo_ext_sites.store import (
    HostedSite,
    HostedSites,
    NotTheSiteCreator,
    SiteFile,
    SourceManifest,
    UnhostNeedsASpeaker,
    hosted_site,
)
from ufo_ext_sites.surface import (
    FRAME_PATH,
    GENERIC_SHARE_TITLE,
    LOGOUT_PATH,
    SESSION_COOKIE,
    SHARE_CARD_ALT,
    SHARE_CARD_URL,
    SHARE_DESCRIPTION,
    SITE_CARD_ALT,
    SITE_CARD_CACHE,
    SITE_CARD_PATH,
    UNCONFIGURED_BODY,
    VISIBILITY_BADGES,
    ShippedAddress,
    SiteHostingUnconfigured,
    shipped_address,
    shipped_site_url,
    site_address,
    site_card_url,
    site_token,
    site_url,
)
from ufo_ext_sites.tools import (
    DEPLOY_WEBSITE_TOOL,
    ENUMERATE_PROG,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    PUBLISH_WEBSITE_TOOL,
    SET_HOMEPAGE_TOOL,
    SOURCE_SKIP_NAMES,
)
from ufo_ext_web.manifest import manifest as web_manifest
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.durability import replay_safe_client
from ufo.ext.context import context_for
from ufo.ext.conversation_slots import ConversationSlotContext, ConversationSlotItem
from ufo.ext.loader import member_object_registry, turn_tools
from ufo.hub import InProcessHub
from ufo.media.artifact_url import ARTIFACT_KEY_PREFIX, verify_artifact_url
from ufo.media.image_previews import ImagePreviewGrant
from ufo.objects import AdminRequired, UnknownObject, VerbNotSupported
from ufo.sandbox import containment
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.ingress_token import (
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    ShippedClaim,
    verify_ingress_token,
)
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import (
    SHARED_AUDIENCE,
    Audience,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.sdk.sandbox import serve_port, shipped_anchor
from ufo.serve import RESERVED_HOST_PREFIXES, _mount_shared_surfaces
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

TOKEN_SECRET = "sites-surface-token-secret"
ARTIFACT_SECRET = "sites-artifact-url-secret"
PUBLIC_BASE_URL = "https://ufo.example.test"
DEPLOY_MODELS = ("auto", "claude-opus-4-8")
INGRESS_HOST = "sites.example.test"
INGRESS_BASE_URL = f"https://{INGRESS_HOST}"
OWNER_EMAIL = "owner@example.com"
TEAMMATE_EMAIL = "teammate@example.com"
OTHER_EMAIL = "other@example.com"
ADMIN_EMAIL = "admin@example.com"
TOOL_NARRATION = "putting the site online"
SITE = "marketing"
SOURCE_LISTING = {"index.html": {"size": 18, "sha256": "ab" * 32}}
SOURCE_BYTES = b"<html>site</html>"


def _listed_source() -> ExecResult:
    return ExecResult(stdout=json.dumps(SOURCE_LISTING), stderr="", exit_code=0)


def _source_store() -> WorkspaceBlobStore:
    return WorkspaceBlobStore(
        backend=FilesystemBlobStore(root=Path(tempfile.mkdtemp(prefix="site-blob-")))
    )


@dataclass
class FakeSandbox:
    """Every command succeeds, so the readiness probe passes and registration runs. It records
    nothing: what the tools said to the sandbox is not the contract these tests hold.

    It carries a `handle` because the real session does, and the hosting tools read the conversation
    off it: a site is registered against whichever conversation's sandbox serves the port, which for
    a subagent is the one that spawned it rather than its own."""

    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def write_file(self, path: str, content: bytes) -> None:
        return None

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield SOURCE_BYTES

        return bytes_of()


def _png(color: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), color).save(buffer, format="PNG")
    return buffer.getvalue()


PAGE_PNG = _png("white")
CARD_DIGEST = "5f2c" * 16
REDRAWN_DIGEST = "a91b" * 16


@dataclass
class ShootingSandbox:
    """Stands in for the member's container on a deploy whose page really is photographed: every
    command succeeds and answers the shot's byte count, every in-sandbox program answers the digest
    the card's encode step prints, and reading a shot back hands over one real PNG.

    It answers the same thing to every command, so it records and asserts nothing about what the
    tools said. What the deploy did with the bytes is the contract: the rows it wrote, the picture
    the store now holds, and the card the frame's head then names. An empty `digest` is a container
    that
    photographs the page and composes no card — which is what every site deployed before cards
    existed has."""

    png: bytes = PAGE_PNG
    digest: str = CARD_DIGEST
    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout=str(len(self.png)), stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout=self.digest, stderr="", exit_code=0)

    async def write_file(self, path: str, content: bytes) -> None:
        return None

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield self.png

        return bytes_of()


@dataclass(frozen=True)
class RefusingSandbox:
    """Stands in for the member's container on a deploy that must be refused before it is touched.
    Serving kills whatever holds the port, so a command reaching here at all is the defect — the
    stand-in refuses the act rather than recording it for a test to read back."""

    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        raise AssertionError(f"a refused deploy ran {command!r} in the member's container")

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        raise AssertionError(
            f"a refused deploy ran a program on {args!r} in the member's container"
        )


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
    dbos_client = replay_safe_client(dbos_launched.database.system_url)

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
        manifests = (web_manifest(), sites_manifest())
        _mount_shared_surfaces(
            app,
            manifests,
            None,
            WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path)),
            sandboxes,
            InProcessHub(),
            dbos_client,
            ARTIFACT_SECRET,
            PUBLIC_BASE_URL,
            ingress_public_url,
            DEPLOY_MODELS,
            ambient_reply=UNREACHED_AMBIENT_REPLY,
            skills=EMPTY_SKILL_REGISTRY,
            member_skill_listing=no_member_skills,
            objects=member_object_registry(
                manifests,
                public_base_url=PUBLIC_BASE_URL,
                artifact_token_secret=ARTIFACT_SECRET,
            ),
        )
        return AsyncClient(transport=ASGITransport(app=app), base_url=PUBLIC_BASE_URL)

    async with mounted(INGRESS_BASE_URL) as client, mounted(None) as unhosted:
        yield Deployment(workspace=workspace, client=client, unhosted=unhosted)
    await asyncio.to_thread(dbos_client.destroy)


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
                visibility="workspace",
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


def _tool(
    name: str,
    audience: Audience,
    public_base_url: str | None = PUBLIC_BASE_URL,
    blob: WorkspaceBlobStore | None = None,
) -> tuple[ToolDef, ToolContext]:
    """One real tool and the context the engine dispatches it with, its extension bound."""
    tools, ext_by_tool = turn_tools(
        (sites_manifest(),),
        None,
        audience=audience,
        public_base_url=public_base_url,
        artifact_token_secret=ARTIFACT_SECRET,
    )
    tool = next(entry for entry in tools if entry.name == name)
    return tool, ToolContext(
        sandbox=FakeSandbox(),
        blob=blob or _source_store(),
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
        artifact_token_secret=ARTIFACT_SECRET,
        public_base_url=public_base_url,
        ext=ext_by_tool.get(name),
    )


def _bind(
    ctx: ToolContext,
    workspace: Workspace,
    conversation_id: UUID,
    speaker_member_id: UUID | None,
    *,
    serving_conversation_id: UUID | None = None,
    subagent_profile: str | None = None,
    sandbox: FakeSandbox | ShootingSandbox | None = None,
) -> ToolContext:
    """The turn and the sandbox it runs in, bound together. `serving_conversation_id` is the
    conversation whose sandbox is answering — the turn's own unless this is a subagent turn, which
    runs in the sandbox of the turn that spawned it. `sandbox` is which stand-in answers that
    container's commands: the plain one, or the one that photographs the page."""
    return replace(
        ctx,
        sandbox=replace(
            sandbox or FakeSandbox(),
            handle=SandboxHandle(
                conversation_id=serving_conversation_id or conversation_id, container_id="c1"
            ),
        ),
        turn=ctx.turn.model_copy(
            update={
                "workspace_id": workspace.id,
                "conversation_id": conversation_id,
                "agent_id": workspace.agent_id,
                "subagent_profile": subagent_profile,
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
    sandbox: FakeSandbox | ShootingSandbox | None = None,
    blob: WorkspaceBlobStore | None = None,
) -> dict[str, object]:
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience, blob=blob)
    args: dict[str, object] = {
        "project_path": "/workspace/dist",
        "site_name": site,
        "entry_point": "index.html",
    }
    if visibility is not None:
        args["visibility"] = visibility
    with ws(workspace.id):
        return await _dispatch(
            tool,
            _bind(ctx, workspace, conversation_id, speaker_member_id, sandbox=sandbox),
            **args,
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


def _head_tags(body: str) -> dict[str, str]:
    """The `og:`/`twitter:` tags an unfurler reads, by name. Only the head is read: what the page
    shows a viewer who passed the gate is not what a crawler is told."""
    head = body.partition("<style")[0]
    return {
        name: value.strip('"')
        for name, value in re.findall(
            r'<meta (?:property|name)=((?:og|twitter):[\w:]+) content=("[^"]*"|[^>]+)>', head
        )
    }


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
    assert (row.name, row.port, row.visibility) == (SITE, serve_port(conversation_id), expected)
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


async def test_a_redeploy_bumps_the_deploy_generation(db: None) -> None:
    """The portal keys the framed homepage on this value, so a redeploy of the same site name
    remounts the frame and shows the new bytes. Every register writes strictly above what the row
    holds."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)

    await _deploy(workspace, conversation_id, audience, member_id)
    (row,) = await _stored(workspace)
    first = row.deploy_generation
    assert first > 0

    await _deploy(workspace, conversation_id, audience, member_id)
    (row,) = await _stored(workspace)
    second = row.deploy_generation
    assert second > first

    await _deploy(workspace, conversation_id, audience, member_id)
    (row,) = await _stored(workspace)
    assert row.deploy_generation > second


async def test_the_deploy_generation_survives_the_sites_recreation(db: None) -> None:
    """An unhosted name deployed again keeps the same URL — the token hashes (workspace,
    conversation, name) — so the value the frame is keyed on must land above the deleted row's,
    or the portal would never remount onto the new site's bytes."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)

    await _deploy(workspace, conversation_id, audience, member_id)
    (row,) = await _stored(workspace)
    before_unhost = row.deploy_generation

    with ws(workspace.id):
        await HostedSites(workspace.id, workspace_tx).unregister(conversation_id, SITE)
    await _deploy(workspace, conversation_id, audience, member_id)

    (row,) = await _stored(workspace)
    assert row.deploy_generation > before_unhost


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


async def test_a_subagent_hosts_against_the_conversation_whose_sandbox_serves_it(db: None) -> None:
    """A subagent runs in the sandbox of the turn that spawned it, so the port it brings up is
    answered by the member's own sandbox — and the site has to be registered against that
    conversation, not the child's. Registered against the child, the ingress would resolve the
    child's conversation, find no sandbox handle on it, and answer that the site is gone; the link
    would also move on every rebuild, since each spawn is a new conversation. The child's own
    conversation is deliberately different here, which is what makes the assertion mean anything.

    Shaped as a real subagent turn: no speaker, acting on behalf of the member. A subagent never
    carries a speaker and cannot be given one, so binding one here would prove the path for a turn
    shape that does not exist."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    member_conversation = await _seed_conversation(workspace, audience, member_id)
    child_conversation = await _seed_conversation(workspace, audience, member_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    child = replace(
        _bind(
            ctx,
            workspace,
            child_conversation,
            None,
            serving_conversation_id=member_conversation,
            subagent_profile="website_building",
        ),
        on_behalf_of_member_id=member_id,
    )

    with ws(workspace.id):
        await _dispatch(
            tool,
            child,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
        )

    (row,) = await _stored(workspace)
    assert row.conversation_id == member_conversation
    assert row.conversation_id != child_conversation


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


async def test_a_subagent_rebuilds_its_site_but_cannot_unhost_another(db: None) -> None:
    """Taking a port retires the site on it, and that retire needs the member to have asked. A
    subagent carries no speaker and cannot be given one, and reading its profile as authority would
    not work: a scheduled fire holds `build_website`, so a timer would escalate through the child it
    spawns. So the rule stays a live speaker — which costs the delegate nothing it needs, because
    re-deploying the site it was asked to build displaces nothing, and the refusal it does get comes
    before the port dies, leaving the standing site up for the member to decide about."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, conversation_id, audience, creator_id, site="marketing")

    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    child = replace(
        _bind(ctx, workspace, conversation_id, None, subagent_profile="website_building"),
        sandbox=RefusingSandbox(
            handle=SandboxHandle(conversation_id=conversation_id, container_id="c1")
        ),
        on_behalf_of_member_id=creator_id,
    )
    with ws(workspace.id), pytest.raises(UnhostNeedsASpeaker, match="unhost") as refusal:
        await _dispatch(
            tool,
            child,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )
    (row,) = await _stored(workspace)
    assert row.name == "marketing"
    # The refusal names the standing site so the delegate can report it, and directs no deploy
    # under that name: a same-name deploy displaces nothing, so it passes this gate and repoints
    # the member's live link at the build they were refused. Advising the act would be worse than
    # the refusal it softens, so the text is pinned here and not only in the message constant.
    assert "marketing" in str(refusal.value)
    assert "deploy under" not in str(refusal.value)

    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    rebuild = replace(
        _bind(ctx, workspace, conversation_id, None, subagent_profile="website_building"),
        on_behalf_of_member_id=creator_id,
    )
    with ws(workspace.id):
        await _dispatch(
            tool,
            rebuild,
            project_path="/workspace/dist",
            site_name="marketing",
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

    assert payload["url"] == f"http://localhost:{serve_port(conversation_id)}"
    assert payload["entry_point"] == "index.html"
    assert str(payload["site_url"]).startswith(f"{PUBLIC_BASE_URL}{FRAME_PATH}/")
    (row,) = await _stored(workspace)
    assert (row.name, row.port) == (SITE, serve_port(conversation_id))


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
        serve_port(conversation_id),
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
    assert "value=public" in frame.text

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


async def test_a_deep_link_frames_the_site_at_that_path(deployment: Deployment) -> None:
    """A site's own paths live at the embedded origin, behind a view token minted per render, so a
    path appended to the frame link was the one spelling that could not work — it named a route the
    surface does not serve and answered 404 while the page sat there reachable. It now opens the
    site where it points: the path rides after the view token, and the ingress lands the session it
    binds there instead of at `/`.

    The selector goes on posting to the token's own address. Built from the request path it would
    trail the deep path into the action and post to a route no method serves, so a creator opening
    their own site one page in would lose the control the bare link gives them."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])

    deep = await client.get(f"{link}/send", headers=_cookie(creator_token))
    assert deep.status_code == 200
    assert "value=public" in deep.text
    embedded = _embedded(deep.text)
    assert embedded.endswith("/send")
    view = verify_ingress_token(
        embedded.rpartition(f"{INGRESS_VIEW_PATH}/")[2].partition("/")[0],
        datetime.now(UTC),
        INGRESS_VIEW_KIND,
    )
    assert (view.conversation_id, view.port) == (conversation_id, serve_port(conversation_id))

    flipped = await client.post(
        f"{link}/visibility",
        data={"visibility": "public", "csrf": _csrf(deep.text)},
        headers=_cookie(creator_token),
    )
    assert flipped.status_code == 303


async def test_the_visibility_post_refuses_a_non_creator_and_a_missing_csrf_then_widens(
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

    widened = await client.post(
        f"{link}/visibility",
        data={"visibility": "public", "csrf": csrf},
        headers=_cookie(creator_token),
    )
    assert widened.status_code == 303

    (row,) = await _stored(workspace)
    assert row.visibility == "public"


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
    # The door the page names is the sign-out one: a browser holding a live session for another
    # workspace is answered this same page, and the sign-in door would forward it to its own portal
    # instead of drawing the form.
    assert f'<a href="{LOGOUT_PATH}">Sign in</a>' in anonymous.text
    assert LOGOUT_PATH in RESERVED_HOST_PREFIXES
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


async def test_the_frame_head_carries_the_card_and_names_only_a_public_site(
    deployment: Deployment,
) -> None:
    """A link unfurler passes no gate, so every tag it reads is published to whoever holds the link:
    the card is always drawn, and the site's own name is in the title only while the site is public.
    The card is an absolute https URL on the brand's apex — a site's own bytes are `no-store` behind
    a session, so they could never be an `og:image`."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])

    frame = await client.get(link, headers=_cookie(creator_token))
    private = _head_tags(frame.text)
    assert private["og:title"] == GENERIC_SHARE_TITLE
    assert private["twitter:title"] == GENERIC_SHARE_TITLE
    assert SITE not in " ".join(private.values())
    # The page the creator reads still names their own site; only the crawler's half is generic.
    assert f"<title>{SITE}</title>" in frame.text

    # An anonymous viewer of a non-public site gets the sign-in page, which is also the page an
    # unfurler gets, so it carries the same generic card rather than nothing at all.
    anonymous = _head_tags((await client.get(link)).text)
    assert anonymous["og:title"] == GENERIC_SHARE_TITLE
    assert anonymous["og:image"] == SHARE_CARD_URL
    assert SITE not in " ".join(anonymous.values())

    flipped = await client.post(
        f"{link}/visibility",
        data={"visibility": "public", "csrf": _csrf(frame.text)},
        headers=_cookie(creator_token),
    )
    assert flipped.status_code == 303

    opened = _head_tags((await client.get(link)).text)
    assert opened == {
        "og:type": "website",
        "og:site_name": "UFO",
        "og:url": link,
        "og:title": SITE,
        "og:description": SHARE_DESCRIPTION,
        "og:image": SHARE_CARD_URL,
        "og:image:width": "1200",
        "og:image:height": "630",
        "og:image:type": "image/jpeg",
        "og:image:alt": SHARE_CARD_ALT,
        "twitter:card": "summary_large_image",
        "twitter:title": SITE,
        "twitter:description": SHARE_DESCRIPTION,
        "twitter:image": SHARE_CARD_URL,
    }
    for card in (private["og:image"], anonymous["og:image"], opened["og:image"]):
        assert card.startswith("https://")
    assert opened["og:url"].startswith(f"{PUBLIC_BASE_URL}{FRAME_PATH}/")


async def test_a_deep_link_unfurls_as_the_bare_site_link(deployment: Deployment) -> None:
    """An unfurl of a deep link names the site's front door rather than the page the link opened, so
    one site has one canonical address wherever it is shared from."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])

    deep = await client.get(f"{link}/pricing", headers=_cookie(creator_token))
    assert deep.status_code == 200
    assert _head_tags(deep.text)["og:url"] == link


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
    blob = _source_store()
    hosted = await _deploy(workspace, conversation_id, audience, creator_id, blob=blob)
    name = str(hosted["site"])
    assert name == site_object_name(conversation_id, SITE)

    with ws(workspace.id):
        listed = await _verb("object_list", workspace, conversation_id, creator_id, kind=SITE_KIND)
        fetched = await _get(workspace, conversation_id, creator_id, name, blob=blob)
        applied = await _verb(
            "object_apply",
            workspace,
            conversation_id,
            creator_id,
            manifest=yaml.safe_dump(
                {"kind": SITE_KIND, "name": name, "spec": {"visibility": "workspace"}}
            ),
        )
        widened = await _verb(
            "object_apply",
            workspace,
            conversation_id,
            creator_id,
            manifest=yaml.safe_dump(
                {"kind": SITE_KIND, "name": name, "spec": {"visibility": "public"}}
            ),
        )
        narrowed = await _verb(
            "object_apply",
            workspace,
            conversation_id,
            creator_id,
            manifest=yaml.safe_dump(
                {"kind": SITE_KIND, "name": name, "spec": {"visibility": "private"}}
            ),
        )

    assert [row["name"] for row in listed["objects"]] == [name]
    assert fetched["spec"] == {"visibility": "private"}
    assert fetched["status"]["site_url"] == hosted["site_url"]
    assert fetched["status"]["port"] == serve_port(conversation_id)
    assert fetched["links"] == [
        {"relation": "created_in", "target": {"kind": "conversation", "name": str(conversation_id)}}
    ]
    assert applied["result"] == "updated"
    assert widened["result"] == "updated"
    assert narrowed["result"] == "updated"
    (row,) = await _stored(workspace)
    assert row.visibility == "private"


async def test_the_site_kind_filters_and_orders_on_its_declared_fields(db: None) -> None:
    """Every field `site` declares rides its listing rows, so a filter and an order on each one
    answers from the live listing — the only place the declaration is checked against the rows."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    other_id, _other_token = await _seed_member(workspace, OTHER_EMAIL)
    audience = conversation_audience(creator_id)
    first_conversation = await _seed_conversation(workspace, audience, creator_id)
    second_conversation = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, first_conversation, audience, creator_id)
    await _deploy(workspace, second_conversation, audience, other_id, visibility="workspace")
    first_name = site_object_name(first_conversation, SITE)
    second_name = site_object_name(second_conversation, SITE)
    async with workspace_tx() as connection:
        for conversation_id, day in ((first_conversation, 3), (second_conversation, 4)):
            await connection.execute(
                sa.update(hosted_site)
                .where(hosted_site.c.conversation_id == conversation_id)
                .values(created_at=datetime(2026, 7, day, tzinfo=UTC))
            )

    with ws(workspace.id):
        listed = await _verb(
            "object_list", workspace, first_conversation, creator_id, kind=SITE_KIND
        )
        by_conversation = await _verb(
            "object_list",
            workspace,
            first_conversation,
            creator_id,
            kind=SITE_KIND,
            filters={"conversation": str(second_conversation)},
        )
        by_visibility = await _verb(
            "object_list",
            workspace,
            first_conversation,
            creator_id,
            kind=SITE_KIND,
            filters={"visibility": "private"},
        )
        by_mine = await _verb(
            "object_list",
            workspace,
            first_conversation,
            creator_id,
            kind=SITE_KIND,
            filters={"mine": True},
        )
        newest_first = await _verb(
            "object_list",
            workspace,
            first_conversation,
            creator_id,
            kind=SITE_KIND,
            order_by="created_at",
            order="desc",
        )

    first_row = {row["name"]: row for row in listed["objects"]}[first_name]
    assert first_row["conversation"] == str(first_conversation)
    assert first_row["visibility"] == "private"
    assert first_row["owner_email"] == OWNER_EMAIL
    assert first_row["mine"] is True
    assert first_row["site_url"] == site_url(
        PUBLIC_BASE_URL, workspace.id, first_conversation, SITE
    )
    assert datetime.fromisoformat(first_row["created_at"]).replace(tzinfo=UTC) == datetime(
        2026, 7, 3, tzinfo=UTC
    )
    assert [row["name"] for row in by_conversation["objects"]] == [second_name]
    assert [row["name"] for row in by_visibility["objects"]] == [first_name]
    assert [row["name"] for row in by_mine["objects"]] == [first_name]
    assert [row["name"] for row in newest_first["objects"]] == [second_name, first_name]


async def test_a_listing_on_a_deploy_that_hosts_no_link_omits_it_rather_than_failing(
    db: None,
) -> None:
    """A deploy with no public base URL hosts nothing reachable, which is a state the index answers
    in rather than raising through: the rows arrive without the field, so the screen still lists
    every site and simply offers no way to open one."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, conversation_id, audience, creator_id)

    with ws(workspace.id):
        tool, ctx = _tool("object_list", audience, public_base_url=None)
        listed = await _dispatch(
            tool,
            _bind(ctx, workspace, conversation_id, creator_id),
            kind=SITE_KIND,
        )

    (row,) = listed["objects"]
    assert row["name"] == site_object_name(conversation_id, SITE)
    assert "site_url" not in row


async def test_the_portal_index_carries_each_site_s_link(deployment: Deployment) -> None:
    """The member's index is the one read the sites screen makes, so the link it draws `Open` from
    rides those rows — the field the agent's `object_get` has always carried, on the listing too."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)

    read = await client.get(
        f"/surface/web/objects/site?agent={workspace.agent_id}", headers=_cookie(creator_token)
    )

    assert read.status_code == 200
    assert "site_url" in read.json()["fields"]
    (row,) = read.json()["objects"]
    assert row["name"] == site_object_name(conversation_id, SITE)
    assert row["site_url"] == hosted["site_url"]


def _preview_claims(url: str, secret: str) -> ImagePreviewGrant | None:
    """The grant a published preview link proves, read the way the artifact route reads it."""
    parsed = urlsplit(url)
    artifact_id, filename = parsed.path.removeprefix(f"/{ARTIFACT_KEY_PREFIX}").split("/")
    query = parse_qs(parsed.query)
    claims = verify_artifact_url(
        secret,
        artifact_id,
        filename,
        query["exp"][0],
        query["sig"][0],
        query["preview"][0],
        query["ws"][0],
        datetime.now(UTC),
    )
    return claims.preview


async def test_a_deploy_photographs_the_page_and_the_row_carries_the_picture(
    db: None, tmp_path: Path
) -> None:
    """The producer half, end to end: the deploy stores the shot under the artifact namespace, the
    row records the key and exact size, and the listing publishes a preview link that really grants
    that picture. Without the capture the row has nothing to publish, and without the publish the
    card has nothing to draw."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))

    await _deploy(
        workspace, conversation_id, audience, creator_id, sandbox=ShootingSandbox(), blob=blob
    )

    (row,) = await _stored(workspace)
    assert row.preview_blob_key.startswith(ARTIFACT_KEY_PREFIX)
    assert row.preview_blob_key.endswith(f"/{SITE}.png")
    assert row.preview_size_bytes == len(PAGE_PNG)
    with ws(workspace.id):
        assert await blob.get(row.preview_blob_key) == PAGE_PNG
        listed = await _verb("object_list", workspace, conversation_id, creator_id, kind=SITE_KIND)

    (listed_row,) = listed["objects"]
    preview_url = listed_row["preview_url"]
    assert preview_url.startswith(f"{PUBLIC_BASE_URL}/{ARTIFACT_KEY_PREFIX}")
    assert _preview_claims(preview_url, ARTIFACT_SECRET) == ImagePreviewGrant(
        media_type="image/png", size_bytes=len(PAGE_PNG)
    )


async def test_a_site_whose_page_never_drew_is_hosted_and_keeps_the_picture_it_had(
    db: None, tmp_path: Path
) -> None:
    """A picture is decoration, so a container that draws none still hosts the site and the listing
    simply omits the field. A re-deploy that fails to draw keeps the picture the site already has
    rather than blanking the card."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))

    await _deploy(workspace, conversation_id, audience, creator_id, site="drawn", blob=blob)
    with ws(workspace.id):
        blank = await _verb("object_list", workspace, conversation_id, creator_id, kind=SITE_KIND)
    (blank_row,) = blank["objects"]
    assert "preview_url" not in blank_row

    await _deploy(
        workspace,
        conversation_id,
        audience,
        creator_id,
        site="drawn",
        sandbox=ShootingSandbox(),
        blob=blob,
    )
    (photographed,) = await _stored(workspace)
    await _deploy(workspace, conversation_id, audience, creator_id, site="drawn", blob=blob)

    (row,) = await _stored(workspace)
    assert row.preview_blob_key == photographed.preview_blob_key
    assert row.preview_size_bytes == len(PAGE_PNG)


async def test_the_portal_index_carries_the_site_s_picture(
    deployment: Deployment, tmp_path: Path
) -> None:
    """The consumer's read: the artifacts screen draws a site's band from the row this route
    answers, so the picture has to arrive on that row rather than only in the registry."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))
    await _deploy(
        workspace, conversation_id, audience, creator_id, sandbox=ShootingSandbox(), blob=blob
    )

    read = await client.get(
        f"/surface/web/objects/site?agent={workspace.agent_id}", headers=_cookie(creator_token)
    )

    assert read.status_code == 200
    (row,) = read.json()["objects"]
    assert _preview_claims(row["preview_url"], ARTIFACT_SECRET) == ImagePreviewGrant(
        media_type="image/png", size_bytes=len(PAGE_PNG)
    )


async def test_a_public_site_unfurls_as_its_own_card_over_the_anonymous_route(
    deployment: Deployment, tmp_path: Path
) -> None:
    """The whole card path for the one site kind that may have one published: the deploy composes
    the card and writes it onto the row, the head names its address, and that address answers an
    unfurler that carries nothing.

    The response is asserted header by header because each one is a rule: `image/jpeg` because the
    card is what is served, no `set-cookie` and no redirect because the URL is public and must never
    be a credential, and `max-age=600` without `immutable` because a site that stops being public
    has to stop being previewed within minutes."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    hosted = await _deploy(
        workspace,
        conversation_id,
        audience,
        creator_id,
        visibility="public",
        sandbox=ShootingSandbox(),
        blob=blob,
    )
    link = str(hosted["site_url"])
    token = link.rsplit("/", 1)[-1]

    (row,) = await _stored(workspace)
    assert row.share_card_blob_key.startswith(ARTIFACT_KEY_PREFIX)
    assert row.share_card_blob_key.endswith(f"/{CARD_NAME}.{CARD_EXTENSION}")
    assert row.share_card_hash == CARD_DIGEST

    head = _head_tags((await client.get(link)).text)
    card = f"{PUBLIC_BASE_URL}{FRAME_PATH}/share/site/{token}/{CARD_DIGEST}.jpg"
    assert head["og:image"] == card
    assert head["twitter:image"] == card
    assert head["og:image:width"] == str(CARD_WIDTH)
    assert head["og:image:height"] == str(CARD_HEIGHT)
    assert head["og:image:type"] == CARD_MEDIA_TYPE
    assert head["og:image:alt"] == SITE_CARD_ALT.format(name=SITE)

    served = await client.get(card)

    assert served.status_code == 200
    assert served.headers["content-type"] == CARD_MEDIA_TYPE
    assert served.headers["cache-control"] == SITE_CARD_CACHE
    assert "immutable" not in served.headers["cache-control"]
    assert "set-cookie" not in served.headers
    assert "location" not in served.headers
    with ws(workspace.id):
        assert served.content == await blob.get(row.share_card_blob_key)


async def test_a_card_is_published_for_no_site_but_a_public_one(
    deployment: Deployment, tmp_path: Path
) -> None:
    """A card is a picture of the site's own page, so it answers the visibility rule harder than the
    name does: a workspace-visible site and a private one each have a card on the row and publish
    neither the address nor the bytes, and a site narrowed after the fact stops answering at the
    address that already worked.

    The narrowing case is the one a cached URL cannot be recalled from, which is why the route reads
    the row per request instead of trusting the level the deploy drew under."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))

    for level in ("private", "workspace"):
        hosted = await _deploy(
            workspace,
            conversation_id,
            audience,
            creator_id,
            site=f"{level}-site",
            visibility=level,
            sandbox=ShootingSandbox(),
            blob=blob,
        )
        link = str(hosted["site_url"])
        gated = await _sites_row(workspace, conversation_id, f"{level}-site")
        assert gated.share_card_hash == CARD_DIGEST
        card = str(site_card_url(PUBLIC_BASE_URL, link.rsplit("/", 1)[-1], CARD_DIGEST))
        head = _head_tags((await client.get(link, headers=_cookie(creator_token))).text)
        assert head["og:image"] == SHARE_CARD_URL
        assert head["og:image:alt"] == SHARE_CARD_ALT
        assert (await client.get(card)).status_code == 404

    hosted = await _deploy(
        workspace,
        conversation_id,
        audience,
        creator_id,
        site="open-site",
        visibility="public",
        sandbox=ShootingSandbox(),
        blob=blob,
    )
    link = str(hosted["site_url"])
    card = str(site_card_url(PUBLIC_BASE_URL, link.rsplit("/", 1)[-1], CARD_DIGEST))
    assert (await client.get(card)).status_code == 200

    frame = await client.get(link, headers=_cookie(creator_token))
    narrowed = await client.post(
        f"{link}/visibility",
        data={"visibility": "workspace", "csrf": _csrf(frame.text)},
        headers=_cookie(creator_token),
    )

    assert narrowed.status_code == 303
    assert (await client.get(card)).status_code == 404
    assert _head_tags((await client.get(link)).text)["og:image"] == SHARE_CARD_URL


async def test_a_homepage_bound_site_unfurls_as_the_generic_card(
    deployment: Deployment, tmp_path: Path
) -> None:
    """A homepage's viewers follow the agent, so the site's own `public` says nothing about who may
    see it and its card is neither named nor served — the same rule that keeps its name out of the
    head."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    hosted = await _deploy(
        workspace,
        conversation_id,
        audience,
        creator_id,
        visibility="public",
        sandbox=ShootingSandbox(),
        blob=blob,
    )
    link = str(hosted["site_url"])
    card = str(site_card_url(PUBLIC_BASE_URL, link.rsplit("/", 1)[-1], CARD_DIGEST))
    assert (await client.get(card)).status_code == 200
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)

    with ws(workspace.id):
        await _dispatch(
            tool,
            _bind(ctx, workspace, conversation_id, creator_id),
            site=site_object_name(conversation_id, SITE),
        )

    assert (await client.get(card)).status_code == 404
    assert _head_tags((await client.get(link)).text)["og:image"] == SHARE_CARD_URL


async def test_a_redeploy_moves_the_card_to_a_new_address(
    deployment: Deployment, tmp_path: Path
) -> None:
    """The card's address carries the digest of its own bytes, so a redeploy publishes a new address
    rather than new pixels behind the old one: nothing cached has to be invalidated, and the address
    a crawler kept stops answering."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    hosted = await _deploy(
        workspace,
        conversation_id,
        audience,
        creator_id,
        visibility="public",
        sandbox=ShootingSandbox(),
        blob=blob,
    )
    link = str(hosted["site_url"])
    first = str(site_card_url(PUBLIC_BASE_URL, link.rsplit("/", 1)[-1], CARD_DIGEST))

    await _deploy(
        workspace,
        conversation_id,
        audience,
        creator_id,
        visibility="public",
        sandbox=ShootingSandbox(digest=REDRAWN_DIGEST),
        blob=blob,
    )

    redrawn = _head_tags((await client.get(link)).text)["og:image"]
    assert redrawn == site_card_url(PUBLIC_BASE_URL, link.rsplit("/", 1)[-1], REDRAWN_DIGEST)
    assert redrawn != first
    assert (await client.get(redrawn)).status_code == 200
    assert (await client.get(first)).status_code == 404


async def test_making_a_site_public_composes_a_card_from_the_shot_it_already_had(
    deployment: Deployment, tmp_path: Path
) -> None:
    """A site deployed before cards existed has a picture of its page and no card, and its card is
    composed from that picture on the act that publishes it — so a member does not have to redeploy
    to get a real unfurl. The stand-in that photographs the page and composes no card is exactly
    that site."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    hosted = await _deploy(
        workspace,
        conversation_id,
        audience,
        creator_id,
        visibility="workspace",
        sandbox=ShootingSandbox(digest=""),
        blob=blob,
    )
    link = str(hosted["site_url"])
    cardless = await _sites_row(workspace, conversation_id, SITE)
    assert cardless.preview_blob_key is not None
    assert cardless.share_card_hash is None

    tool, ctx = _tool("object_apply", audience, blob=blob)
    with ws(workspace.id):
        await _dispatch(
            tool,
            _bind(ctx, workspace, conversation_id, creator_id, sandbox=ShootingSandbox()),
            manifest=yaml.safe_dump(
                {
                    "kind": SITE_KIND,
                    "name": site_object_name(conversation_id, SITE),
                    "spec": {"visibility": "public"},
                }
            ),
        )

    published = await _sites_row(workspace, conversation_id, SITE)
    assert published.visibility == "public"
    assert published.share_card_hash == CARD_DIGEST
    card = str(site_card_url(PUBLIC_BASE_URL, link.rsplit("/", 1)[-1], CARD_DIGEST))
    assert _head_tags((await client.get(link)).text)["og:image"] == card
    assert (await client.get(card)).status_code == 200


def test_the_edge_fronts_the_card_at_the_address_the_head_publishes() -> None:
    """One pasted link is unfurled by every chat app that reads it, and each unfurl fetches the
    card. So the address the head publishes is answered at the edge off one stored copy, and the app
    host reads the row and streams the bytes once per window instead of once per request.

    Two files in another language hold that arrangement — the worker that answers the path and the
    route that hands the app host's card path to it — and both are anchored here against the path
    this module publishes, because a prefix that drifts from it returns every unfurl to the origin
    with nothing failing. The card keeps the app host, the one origin `og:url` names, so the page
    and its picture stay one origin."""
    edge = Path(__file__).resolve().parents[3] / "infra/modules/edge"

    assert f'const SITE_CARD_PREFIX = "{SITE_CARD_PATH}/";' in (edge / "worker.js").read_text()
    assert f'pattern = "app.${{var.hostname}}{SITE_CARD_PATH}/*"' in (edge / "main.tf").read_text()
    assert urlsplit(str(site_card_url(PUBLIC_BASE_URL, "site-token", CARD_DIGEST))).netloc == (
        urlsplit(site_url(PUBLIC_BASE_URL, uuid4(), uuid4(), SITE)).netloc
    )


async def _sites_row(workspace: Workspace, conversation_id: UUID, name: str) -> HostedSite:
    """One site's row, read through the registry the tools and the frame share."""
    with ws(workspace.id):
        site = await HostedSites(workspace.id, workspace_tx).read(conversation_id, name)
    assert site is not None
    return site


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
    blob: WorkspaceBlobStore | None = None,
    **args: object,
) -> dict[str, object]:
    tool, ctx = _tool(verb, conversation_audience(speaker_member_id), blob=blob)
    return await _dispatch(tool, _bind(ctx, workspace, conversation_id, speaker_member_id), **args)


async def _get(
    workspace: Workspace,
    conversation_id: UUID,
    speaker_member_id: UUID,
    name: str,
    blob: WorkspaceBlobStore | None = None,
) -> dict[str, object]:
    tool, ctx = _tool("object_get", conversation_audience(speaker_member_id), blob=blob)
    result = await tool.handler(
        _bind(ctx, workspace, conversation_id, speaker_member_id),
        tool.input_model.model_validate(
            {"user_description": TOOL_NARRATION, "kind": SITE_KIND, "name": name}
        ),
    )
    fetched = yaml.safe_load(result.content[0].text)
    assert isinstance(fetched, dict)
    return fetched


async def test_a_refused_deploy_never_touches_the_members_running_site(db: None) -> None:
    """Serving mutates a container the member's own turns share: it kills whatever holds the port,
    which is the member's site. So every refusal hosting can raise without the port has to fire
    first — otherwise it leaves the member with a killed server, no replacement, and a link that
    resolves to nothing. The refusal driven here is a `visibility` argument on a turn with no live
    speaker: naming a disclosure is the member's to make. Both hosting tools take that path, so both
    are driven; the stand-in refuses any command rather than recording one for a test to read back,
    so what proves the ordering is the sandbox itself."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    await _deploy(workspace, conversation_id, audience, member_id, site="marketing")
    for tool_name, args in (
        (DEPLOY_WEBSITE_TOOL, {"site_name": "pricing", "entry_point": "index.html"}),
        (
            PUBLISH_WEBSITE_TOOL,
            {"app_name": "pricing", "dist_path": "dist", "run_command": "node server.js"},
        ),
    ):
        tool, ctx = _tool(tool_name, audience)
        child = replace(
            _bind(ctx, workspace, conversation_id, None, subagent_profile="website_building"),
            sandbox=RefusingSandbox(),
            on_behalf_of_member_id=member_id,
        )
        with ws(workspace.id), pytest.raises(RuntimeError, match="needs a live member"):
            await _dispatch(
                tool, child, project_path="/workspace/dist", visibility="workspace", **args
            )

    (row,) = await _stored(workspace)
    assert row.name == "marketing"


@dataclass(frozen=True)
class FailingSandbox:
    """A sandbox whose serve never comes up — the readiness probe's failure, which is what a broken
    build looks like from here."""

    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout="", stderr="port never opened", exit_code=1)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout="", stderr="", exit_code=0)

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield SOURCE_BYTES

        return bytes_of()


async def test_a_build_that_never_comes_up_leaves_the_members_site_alone(db: None) -> None:
    """Registering writes the row and retires whatever held the port, and that write commits. So it
    happens after the serve, not before: a build that fails its readiness probe must not have
    already deleted the member's site and left a live row pointing at a dead port. The refusals
    still run first — that is `_refuse_before_serving` — but the row is only claimed once something
    is actually answering on the port."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    await _deploy(workspace, conversation_id, audience, member_id, site="marketing")
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    broken = replace(_bind(ctx, workspace, conversation_id, member_id), sandbox=FailingSandbox())

    with ws(workspace.id), pytest.raises(RuntimeError):
        await _dispatch(
            tool,
            broken,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )

    (row,) = await _stored(workspace)
    assert row.name == "marketing"


@dataclass(frozen=True)
class FailingInstallSandbox:
    """A sandbox whose install step fails — `publish_website`'s first mutation, and its earliest
    point of no return once a row has been written."""

    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        if "npm install" in command:
            return ExecResult(stdout="", stderr="install failed", exit_code=1)
        return ExecResult(stdout="", stderr="", exit_code=0)


async def test_publish_leaves_the_members_site_alone_when_it_cannot_come_up(db: None) -> None:
    """`publish_website` has two mutations after the refusals pass — the install command and the
    serve — and registering before either would delete the member's row and leave a live row on a
    port that never opened. Both are driven here, because the deploy path's test does not reach
    this call site. The app is published under a different name from the member's live site, so a
    premature write would displace it: registering under the same name is an update and would
    survive either ordering, proving nothing.

    The serve case passes no `install_command`, which is what makes it a second point rather than a
    repeat of the first: `FailingSandbox` fails every command, so given one it would raise at the
    install and never reach `_serve`, leaving a `_host` placed between the two uncovered."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)

    for sandbox, install_command in (
        (
            FailingInstallSandbox(
                handle=SandboxHandle(conversation_id=conversation_id, container_id="c1")
            ),
            "npm install",
        ),
        (
            FailingSandbox(
                handle=SandboxHandle(conversation_id=conversation_id, container_id="c1")
            ),
            None,
        ),
    ):
        await _deploy(workspace, conversation_id, audience, member_id, site="marketing")
        tool, ctx = _tool(PUBLISH_WEBSITE_TOOL, audience)
        broken = replace(_bind(ctx, workspace, conversation_id, member_id), sandbox=sandbox)
        with ws(workspace.id), pytest.raises(RuntimeError):
            await _dispatch(
                tool,
                broken,
                project_path="/workspace/app",
                dist_path="dist",
                app_name="pricing",
                install_command=install_command,
                run_command="node server.js",
            )
        (row,) = await _stored(workspace)
        assert row.name == "marketing"


@dataclass(frozen=True)
class StoppedShotSandbox:
    """A sandbox that serves the new bytes and then never comes back from the shot — what a turn
    ending inside the render looks like from here. Chromium is given `PREVIEW_TIMEOUT_SECONDS`, so
    the render is the deploy's longest step and the one a turn is likeliest to be cut short in."""

    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        if "--remote-debugging-pipe" in command:
            raise RuntimeError("the turn ended while the page was drawing")
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout="", stderr="", exit_code=0)

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield SOURCE_BYTES

        return bytes_of()


async def test_a_deploy_stopped_inside_the_shot_has_already_moved_the_ports_row(db: None) -> None:
    """The photograph runs after the registration, not between the serve and it. The serve has
    already killed the member's previous server and put this deploy's bytes on the port, and
    registering is the only thing that retires the site that port belonged to — so a deploy cut
    short inside the render leaves the new name registered and the displaced one gone, rather than
    an older link answering with the new deploy's bytes."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    await _deploy(workspace, conversation_id, audience, member_id, site="marketing")
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    stopped = replace(
        _bind(ctx, workspace, conversation_id, member_id),
        sandbox=StoppedShotSandbox(
            handle=SandboxHandle(conversation_id=conversation_id, container_id="c1")
        ),
    )

    with ws(workspace.id), pytest.raises(RuntimeError, match="while the page was drawing"):
        await _dispatch(
            tool,
            stopped,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )

    (row,) = await _stored(workspace)
    assert row.name == "pricing"
    assert row.port == serve_port(conversation_id)
    assert row.preview_blob_key is None


@dataclass(frozen=True)
class RefusedShotSandbox:
    """A sandbox where the shot's own name cannot be cleared — the containment guard's refusal,
    which is what a link planted at that path looks like from here. The server log clears normally,
    so the deploy really reaches the shot."""

    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        if args[0].endswith(".png"):
            return ExecResult(stdout="", stderr="that path is not contained", exit_code=1)
        return ExecResult(stdout="", stderr="", exit_code=0)

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield SOURCE_BYTES

        return bytes_of()


async def test_a_shot_path_the_guard_refuses_still_hosts_the_site(db: None) -> None:
    """Clearing the shot's name is the render's own first step and it can be refused, so it answers
    like every other undrawn shot: logged, no preview, site hosted. The site is already registered
    by then, so raising here would report a failure for a deploy that really is serving."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    refused = replace(
        _bind(ctx, workspace, conversation_id, member_id),
        sandbox=RefusedShotSandbox(
            handle=SandboxHandle(conversation_id=conversation_id, container_id="c1")
        ),
    )

    with ws(workspace.id):
        hosted = await _dispatch(
            tool,
            refused,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
        )

    assert hosted["site_name"] == SITE
    (row,) = await _stored(workspace)
    assert row.name == SITE
    assert row.preview_blob_key is None


async def test_deployed_site_reaches_its_authenticated_conversation_slot(
    deployment: Deployment,
) -> None:
    workspace = deployment.workspace
    owner_id, owner_token = await _seed_member(workspace, OWNER_EMAIL)
    _other_id, other_token = await _seed_member(workspace, OTHER_EMAIL)
    conversation_id = await _seed_conversation(workspace, SHARED_AUDIENCE, None)
    await _deploy(
        workspace,
        conversation_id,
        SHARED_AUDIENCE,
        owner_id,
        site="private-dashboard",
        visibility="private",
    )

    path = f"/surface/web/agents/{workspace.agent_id}/conversations/{conversation_id}/slots/sites"
    owner = await deployment.client.get(path, headers=_cookie(owner_token))
    other = await deployment.client.get(path, headers=_cookie(other_token))

    assert owner.status_code == 200
    assert owner.json()["sites"][0]["name"] == "private-dashboard"
    assert owner.json()["sites"][0]["url"].startswith(PUBLIC_BASE_URL + FRAME_PATH + "/")
    assert "creator_member_id" not in owner.json()["sites"][0]
    assert other.status_code == 200
    assert other.json() == {"type": "sites", "sites": [], "truncated": False}


async def test_sites_slot_bounds_its_durable_rows(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    conversation_id = await _seed_conversation(workspace, SHARED_AUDIENCE, None)
    now = datetime(2026, 8, 7, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(hosted_site),
            [
                {
                    "workspace_id": workspace.id,
                    "conversation_id": conversation_id,
                    "name": f"site-{index:03d}",
                    "port": 10_000 + index,
                    "visibility": "workspace",
                    "creator_member_id": owner_id,
                    "created_at": now,
                    "updated_at": now,
                }
                for index in range(102)
            ],
        )
    ext = context_for("sites", frozenset())
    with ws(workspace.id):
        registered = await HostedSites(workspace.id, workspace_tx).conversation(
            conversation_id,
            tuple(f"site-{index:03d}" for index in range(102)),
            102,
        )
        payload = await SITES_SLOT.read(
            ConversationSlotContext(
                ext=ext,
                conversation_id=conversation_id,
                agent_id=workspace.agent_id,
                audience=SHARED_AUDIENCE,
                messages=(),
                public_base_url=PUBLIC_BASE_URL,
                visible_items=tuple(
                    ConversationSlotItem(
                        site_object_name(conversation_id, row.name), row.generation, True
                    )
                    for row in registered
                ),
            )
        )

    assert len(payload.sites) == 100
    assert payload.truncated is True


async def test_sites_slot_rejects_stale_visibility_and_recreated_row_grants(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    conversation_id = await _seed_conversation(workspace, SHARED_AUDIENCE, None)
    with ws(workspace.id):
        sites = HostedSites(workspace.id, workspace_tx)
        registered = await sites.register(
            conversation_id,
            "dashboard",
            8000,
            owner_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        context = ConversationSlotContext(
            ext=context_for("sites", frozenset()),
            conversation_id=conversation_id,
            agent_id=workspace.agent_id,
            audience=SHARED_AUDIENCE,
            messages=(),
            public_base_url=PUBLIC_BASE_URL,
            visible_items=(
                ConversationSlotItem(
                    site_object_name(conversation_id, registered.name),
                    registered.generation,
                    True,
                ),
            ),
        )
        await sites.set_visibility(conversation_id, registered.name, "private")
        assert (await SITES_SLOT.read(context)).sites == ()
        await sites.unregister(conversation_id, registered.name)
        recreated = await sites.register(
            conversation_id,
            registered.name,
            8001,
            owner_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        assert recreated.generation != registered.generation
        assert (await SITES_SLOT.read(context)).sites == ()


async def test_set_homepage_binds_one_row_per_agent(db: None) -> None:
    """Rebinding moves the pointer in one transaction: the old row's binding clears as the new row
    takes it, so the partial unique index never sees two homepages for one agent."""
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    first = await _seed_conversation(workspace, SHARED_AUDIENCE, None)
    second = await _seed_conversation(workspace, SHARED_AUDIENCE, None)

    with ws(workspace.id):
        sites = HostedSites(workspace.id, workspace_tx)
        await sites.register(
            first, "about", 8000, owner_id, "workspace", SHARED_AUDIENCE, True, manifest=None
        )
        await sites.register(
            second, "status", 8000, owner_id, "workspace", SHARED_AUDIENCE, True, manifest=None
        )

        bound = await sites.set_homepage(workspace.agent_id, first, "about")
        moved = await sites.set_homepage(workspace.agent_id, second, "status")

        assert bound is not None and bound.homepage_agent_id == workspace.agent_id
        assert moved is not None and moved.homepage_agent_id == workspace.agent_id
        former = await sites.read(first, "about")
        assert former is not None and former.homepage_agent_id is None


async def test_a_redeploy_keeps_the_binding(db: None) -> None:
    """A same-name re-deploy updates the port in place, and the homepage pointer is not the
    deploy's to touch — the register update path leaves it exactly as it was."""
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    conversation_id = await _seed_conversation(workspace, SHARED_AUDIENCE, None)

    with ws(workspace.id):
        sites = HostedSites(workspace.id, workspace_tx)
        await sites.register(
            conversation_id,
            "about",
            8000,
            owner_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        await sites.set_homepage(workspace.agent_id, conversation_id, "about")

        redeployed = await sites.register(
            conversation_id, "about", 8000, owner_id, None, SHARED_AUDIENCE, True, manifest=None
        )

        assert redeployed.homepage_agent_id == workspace.agent_id


async def test_a_redeploy_of_a_bound_site_reports_the_agents_visibility(db: None) -> None:
    """A bound site has no level of its own to report: the rebuild that keeps an app's homepage up
    answers with the level the frame gates it on — the agent's — rather than the column lying
    dormant underneath, which would tell the member their app's page is private."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    hosted = await _deploy(workspace, conversation_id, audience, member_id)
    assert hosted["visibility"] == "private"
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)
    with ws(workspace.id):
        await _dispatch(
            tool, _bind(ctx, workspace, conversation_id, member_id), site=str(hosted["site"])
        )

    rebuilt = await _deploy(workspace, conversation_id, audience, member_id)

    assert rebuilt["visibility"] == "workspace"
    (row,) = await _stored(workspace)
    assert (row.visibility, row.homepage_agent_id) == ("private", workspace.agent_id)


async def test_unhost_clears_the_binding(db: None) -> None:
    """The binding is held by the row itself, so unregistering the bound site leaves no dangling
    pointer — and a fresh site binds cleanly afterwards."""
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    conversation_id = await _seed_conversation(workspace, SHARED_AUDIENCE, None)

    with ws(workspace.id):
        sites = HostedSites(workspace.id, workspace_tx)
        await sites.register(
            conversation_id,
            "about",
            8000,
            owner_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        await sites.set_homepage(workspace.agent_id, conversation_id, "about")
        await sites.unregister(conversation_id, "about")

        assert await sites.read(conversation_id, "about") is None
        assert await sites.set_homepage(workspace.agent_id, conversation_id, "about") is None
        await sites.register(
            conversation_id,
            "status",
            8001,
            owner_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
        rebound = await sites.set_homepage(workspace.agent_id, conversation_id, "status")
        assert rebound is not None and rebound.homepage_agent_id == workspace.agent_id


async def test_the_index_holds_one_homepage_per_agent(db: None) -> None:
    """The store clears before it stamps; the partial unique index is what makes a second binding
    impossible rather than merely unwritten. Driven by a raw update because the store never writes
    one — the index is the backstop under any future writer."""
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    first = await _seed_conversation(workspace, SHARED_AUDIENCE, None)
    second = await _seed_conversation(workspace, SHARED_AUDIENCE, None)

    with ws(workspace.id):
        sites = HostedSites(workspace.id, workspace_tx)
        await sites.register(
            first, "about", 8000, owner_id, "workspace", SHARED_AUDIENCE, True, manifest=None
        )
        await sites.register(
            second, "status", 8000, owner_id, "workspace", SHARED_AUDIENCE, True, manifest=None
        )
        await sites.set_homepage(workspace.agent_id, first, "about")

        with pytest.raises(IntegrityError):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(hosted_site)
                    .where(
                        hosted_site.c.workspace_id == workspace.id,
                        hosted_site.c.conversation_id == second,
                    )
                    .values(homepage_agent_id=workspace.agent_id)
                )


async def test_set_homepage_refuses_a_dangling_name(db: None) -> None:
    """A name that resolves to no hosted site is refused naming the site, and nothing binds."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    await _deploy(workspace, conversation_id, audience, member_id)
    dangling = site_object_name(conversation_id, "never-deployed")
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)

    with ws(workspace.id), pytest.raises(ValueError, match=re.escape(dangling)):
        await _dispatch(tool, _bind(ctx, workspace, conversation_id, member_id), site=dangling)

    (row,) = await _stored(workspace)
    assert row.homepage_agent_id is None


async def test_set_homepage_binds_speakerlessly_and_reports_the_agents_visibility(
    db: None,
) -> None:
    """Binding runs on a scheduled turn with no live speaker, which seeding requires, and writes
    nothing but the pointer: the row's own visibility and generation are untouched, and the
    reported visibility is the agent's — the level the frame actually gates the homepage on."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    hosted = await _deploy(workspace, conversation_id, audience, member_id)
    (before,) = await _stored(workspace)
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)
    scheduled = replace(
        _bind(ctx, workspace, conversation_id, None), on_behalf_of_member_id=member_id
    )

    with ws(workspace.id):
        payload = await _dispatch(tool, scheduled, site=str(hosted["site"]))

    assert payload == {
        "site": hosted["site"],
        "site_url": hosted["site_url"],
        "visibility": "workspace",
        "homepage_agent": str(workspace.agent_id),
    }
    (row,) = await _stored(workspace)
    assert row.homepage_agent_id == workspace.agent_id
    assert row.visibility == "private"
    assert row.generation == before.generation


async def test_a_standing_site_binds_for_its_creator_speaking_and_refuses_speakerless(
    db: None,
) -> None:
    """Binding a standing site re-gates it onto the agent's audience, so it takes the creator
    speaking: the same bind on a later speakerless turn refuses naming the rule, and the
    creator's live ask carries it."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    hosted = await _deploy(workspace, conversation_id, audience, member_id)
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)
    scheduled = replace(
        _bind(ctx, workspace, conversation_id, None), on_behalf_of_member_id=member_id
    )
    later = replace(
        scheduled,
        turn=scheduled.turn.model_copy(update={"created_at": datetime(2027, 1, 1, tzinfo=UTC)}),
    )

    with ws(workspace.id), pytest.raises(RuntimeError, match="needs its creator speaking"):
        await _dispatch(tool, later, site=str(hosted["site"]))
    (row,) = await _stored(workspace)
    assert row.homepage_agent_id is None

    spoken = _bind(ctx, workspace, conversation_id, member_id)
    spoken_later = replace(
        spoken,
        turn=spoken.turn.model_copy(update={"created_at": datetime(2027, 1, 1, tzinfo=UTC)}),
    )
    with ws(workspace.id):
        payload = await _dispatch(tool, spoken_later, site=str(hosted["site"]))

    assert payload["visibility"] == "workspace"
    (row,) = await _stored(workspace)
    assert row.homepage_agent_id == workspace.agent_id
    assert row.visibility == "private"


async def test_binding_never_regates_anothers_site(db: None) -> None:
    """Bound, a site answers the agent's audience instead of its own column, so the bind is the
    creator's act whatever the site's visibility: another member's private site would widen and
    their workspace site would re-gate, and both are refused naming the rule."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    other_id, _other_token = await _seed_member(workspace, TEAMMATE_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)

    with ws(workspace.id):
        with pytest.raises(ValueError, match="its creator's act alone"):
            await _dispatch(
                tool, _bind(ctx, workspace, conversation_id, other_id), site=str(hosted["site"])
            )
        await HostedSites(workspace.id, workspace_tx).set_visibility(
            conversation_id, SITE, "workspace"
        )
        with pytest.raises(ValueError, match="its creator's act alone"):
            await _dispatch(
                tool, _bind(ctx, workspace, conversation_id, other_id), site=str(hosted["site"])
            )

    (row,) = await _stored(workspace)
    assert row.homepage_agent_id is None


async def test_binding_a_private_agent_reports_private(db: None) -> None:
    """A private agent's homepage answers its owner and admins, so the bind reports `private` —
    the agent's level — and the row's own column is untouched."""
    workspace = await _seed_workspace()
    specialist = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=specialist,
                workspace_id=workspace.id,
                name="specialist",
                prompt="be narrow",
                model="claude-opus-4-8",
                is_main=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    as_specialist = Workspace(id=workspace.id, agent_id=specialist)
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    hosted = await _deploy(workspace, conversation_id, audience, member_id)
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)

    with ws(workspace.id):
        payload = await _dispatch(
            tool, _bind(ctx, as_specialist, conversation_id, member_id), site=str(hosted["site"])
        )

    assert payload["visibility"] == "private"
    (row,) = await _stored(workspace)
    assert row.homepage_agent_id == specialist
    assert row.visibility == "private"


async def test_a_bound_site_leaves_the_shared_listing_and_answers_by_name(db: None) -> None:
    """A homepage is the agent's page, not a site the workspace shares: binding takes the row out
    of every browse — its creator's as well as another member's — while an ordinary workspace
    site beside it goes on standing in both. By name the row answers the agent's audience the way
    the frame does, so any member who may open a workspace agent's page may read its site object —
    the edit flow every such member may direct starts with that read — a read naming the binding
    still lists it, and an apply naming another level refuses toward the agent object."""
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    other_id, _other_token = await _seed_member(workspace, TEAMMATE_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    shared_conversation = await _seed_conversation(workspace, audience, creator_id)
    blob = _source_store()
    hosted = await _deploy(workspace, conversation_id, audience, creator_id, blob=blob)
    await _deploy(
        workspace,
        shared_conversation,
        audience,
        creator_id,
        site="handbook",
        visibility="workspace",
        blob=blob,
    )
    name = str(hosted["site"])
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)

    with ws(workspace.id):
        await _dispatch(tool, _bind(ctx, workspace, conversation_id, creator_id), site=name)
        listed = await _verb("object_list", workspace, conversation_id, other_id, kind=SITE_KIND)
        browsed = await _verb("object_list", workspace, conversation_id, creator_id, kind=SITE_KIND)
        bound = await _verb(
            "object_list",
            workspace,
            conversation_id,
            creator_id,
            kind=SITE_KIND,
            filters={"homepage_agent": str(workspace.agent_id)},
        )
        fetched = await _get(workspace, conversation_id, creator_id, name, blob=blob)
        others_read = await _get(workspace, conversation_id, other_id, name, blob=blob)
        assert others_read["spec"] == {"visibility": "workspace"}
        with pytest.raises(ValueError, match="follows the agent"):
            await _verb(
                "object_apply",
                workspace,
                conversation_id,
                creator_id,
                manifest=yaml.safe_dump(
                    {"kind": SITE_KIND, "name": name, "spec": {"visibility": "public"}}
                ),
            )

    handbook = site_object_name(shared_conversation, "handbook")
    assert [row["name"] for row in listed["objects"]] == [handbook]
    assert [row["name"] for row in browsed["objects"]] == [handbook]
    (row,) = bound["objects"]
    assert row["name"] == name
    assert row["visibility"] == "workspace"
    assert row["homepage_agent"] == str(workspace.agent_id)
    assert fetched["spec"] == {"visibility": "workspace"}
    stored = {site.name: site for site in await _stored(workspace)}
    assert stored[SITE].visibility == "private"


async def test_a_private_site_opens_for_a_workspace_admin(deployment: Deployment) -> None:
    """Private admits its creator and workspace admins — the same set the object gate has always
    listed for — and the admin reads a badge, never the creator's selector."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, _creator_token = await _seed_member(workspace, OWNER_EMAIL)
    _admin_id, admin_token = await _seed_member(workspace, ADMIN_EMAIL, is_admin=True)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)
    link = str(hosted["site_url"])

    opened = await client.get(link, headers=_cookie(admin_token))

    assert opened.status_code == 200
    assert VISIBILITY_BADGES["private"] in opened.text
    assert "<select name=visibility>" not in opened.text


async def test_a_homepage_frame_follows_the_agent_and_renders_bare(
    deployment: Deployment,
) -> None:
    """A bound site's frame gates on the agent — every member for a workspace agent, owner and
    admins for a private one, the creator holding no standing of their own — renders without the
    header, and refuses the visibility post whole."""
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    _other_id, other_token = await _seed_member(workspace, OTHER_EMAIL)
    _admin_id, admin_token = await _seed_member(workspace, ADMIN_EMAIL, is_admin=True)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    hosted = await _deploy(workspace, conversation_id, audience, creator_id)
    link = str(hosted["site_url"])
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)
    with ws(workspace.id):
        await _dispatch(
            tool, _bind(ctx, workspace, conversation_id, creator_id), site=str(hosted["site"])
        )

    opened = await client.get(link, headers=_cookie(other_token))
    assert opened.status_code == 200
    assert "<header>" not in opened.text
    assert "<select name=visibility>" not in opened.text
    assert INGRESS_HOST in _embedded(opened.text)

    refused = await client.post(
        f"{link}/visibility",
        data={"visibility": "public", "csrf": "stale"},
        headers=_cookie(creator_token),
    )
    assert refused.status_code == 409
    assert "follows the agent" in refused.text

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == workspace.agent_id)
            .values(visibility="private")
        )
    assert (await client.get(link, headers=_cookie(other_token))).status_code == 404
    assert (await client.get(link, headers=_cookie(creator_token))).status_code == 404
    assert (await client.get(link, headers=_cookie(admin_token))).status_code == 200


SHIPPED_DIGEST = "deadbeefdeadbeef"


async def _seed_app_agent(
    workspace: Workspace, slug: str, *, visibility: str = "workspace"
) -> UUID:
    """One app agent as an `app_<slug>` provision would create it — ownerless, with the full
    provenance the `agent_provenance` check requires — the row a shipped page's frame gates on."""
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace.id,
                name=slug,
                prompt="the app",
                model="claude-opus-4-8",
                visibility=visibility,
                provisioned_by=f"app_{slug}",
                provisioned_name=slug,
                provisioned_version="0.1.0",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


def test_a_shipped_url_round_trips_and_never_collides_with_a_site_token() -> None:
    """The shipped frame link carries its whole address in the token — workspace, app agent, slug,
    digest — and reads back as exactly that. A shipped token is not a site token and a site token is
    not a shipped one, so the two frame paths never resolve each other's address. No public base,
    no link."""
    ws_id, agent_id = uuid4(), uuid4()
    url = shipped_site_url(PUBLIC_BASE_URL, ws_id, agent_id, "radar", SHIPPED_DIGEST)
    assert url is not None and url.startswith(f"{PUBLIC_BASE_URL}{FRAME_PATH}/")
    token = url.rpartition("/")[2]
    assert shipped_address(token) == ShippedAddress(ws_id, agent_id, "radar", SHIPPED_DIGEST)
    assert site_address(token) is None
    assert shipped_address(site_token(ws_id, uuid4(), "dash")) is None
    assert shipped_site_url(None, ws_id, agent_id, "radar", SHIPPED_DIGEST) is None


async def test_a_shipped_app_frame_gates_on_the_agent_and_embeds_the_fleet_bundle(
    deployment: Deployment,
) -> None:
    """A shipped app page has no hosted_site row: the frame resolves the agent from the token, gates
    on its visibility exactly as a bound homepage does, renders bare, and embeds the deploy-wide
    bundle from the fleet store — the ingress view token it mints carries the shipped claim and the
    synthetic per-workspace anchor, and an unconfigured ingress embeds nothing rather than crash."""
    client, workspace = deployment.client, deployment.workspace
    _other_id, other_token = await _seed_member(workspace, OTHER_EMAIL)
    _admin_id, admin_token = await _seed_member(workspace, ADMIN_EMAIL, is_admin=True)
    app_agent = await _seed_app_agent(workspace, "radar")
    url = shipped_site_url(PUBLIC_BASE_URL, workspace.id, app_agent, "radar", SHIPPED_DIGEST)
    assert url is not None

    anonymous = await client.get(url)
    assert anonymous.status_code == 200
    assert "not signed in to the workspace" in anonymous.text
    assert "<iframe" not in anonymous.text

    opened = await client.get(url, headers=_cookie(other_token))
    assert opened.status_code == 200
    assert "<header>" not in opened.text
    embedded = _embedded(opened.text)
    assert INGRESS_HOST in embedded
    claims = verify_ingress_token(
        embedded.rpartition(f"{INGRESS_VIEW_PATH}/")[2], datetime.now(UTC), INGRESS_VIEW_KIND
    )
    anchor = shipped_anchor(workspace.id, "radar")
    assert (claims.conversation_id, claims.port) == (anchor, serve_port(anchor))
    assert claims.shipped == ShippedClaim(slug="radar", digest=SHIPPED_DIGEST)

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == app_agent)
            .values(visibility="private")
        )
    assert (await client.get(url, headers=_cookie(other_token))).status_code == 404
    assert (await client.get(url, headers=_cookie(admin_token))).status_code == 200

    unhosted = await deployment.unhosted.get(url, headers=_cookie(admin_token))
    assert unhosted.status_code == 200
    assert "<iframe" not in unhosted.text
    assert UNCONFIGURED_BODY in unhosted.text


async def test_site_rows_carry_homepage_agent(db: None) -> None:
    """The kind row carries `homepage_agent` only on the bound site — the declared field the portal
    reads and filters the binding through — so a browse holds every other site and nothing else,
    and the filtered read holds the bound one alone. The filter takes `mine` beside an agent id,
    because nothing tells a turn its own agent id."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    first = await _seed_conversation(workspace, audience, member_id)
    second = await _seed_conversation(workspace, audience, member_id)
    await _deploy(workspace, first, audience, member_id)
    bound = await _deploy(workspace, second, audience, member_id)
    tool, ctx = _tool(SET_HOMEPAGE_TOOL, audience)

    with ws(workspace.id):
        await _dispatch(tool, _bind(ctx, workspace, second, member_id), site=str(bound["site"]))
        listed = await _verb("object_list", workspace, first, member_id, kind=SITE_KIND)
        filtered = await _verb(
            "object_list",
            workspace,
            first,
            member_id,
            kind=SITE_KIND,
            filters={"homepage_agent": str(workspace.agent_id)},
        )
        mine = await _verb(
            "object_list",
            workspace,
            first,
            member_id,
            kind=SITE_KIND,
            filters={"homepage_agent": "mine"},
        )

    (row,) = listed["objects"]
    assert row["name"] == site_object_name(first, SITE)
    assert "homepage_agent" not in row
    (only,) = filtered["objects"]
    assert only["name"] == str(bound["site"])
    assert only["homepage_agent"] == str(workspace.agent_id)
    (own,) = mine["objects"]
    assert own["name"] == str(bound["site"])


def _manifest_json(conversation_id: UUID, name: str, token: str) -> str:
    listing = {
        path: SiteFile(
            size=entry["size"], media_type="text/html; charset=utf-8", sha256=entry["sha256"]
        )
        for path, entry in SOURCE_LISTING.items()
    }
    return SourceManifest(
        root=f"sites/{conversation_id}/{name}/{token}/", files=listing
    ).model_dump_json()


def test_a_source_root_must_be_a_sites_or_apps_prefix() -> None:
    """`_rooted` admits both serving families — `sites/` for a workspace fork, `apps/` for a shipped
    bundle — and refuses a root that ends elsewhere or does not end at a prefix, so a manifest can
    never name bytes outside a deploy's own trees."""
    files = {"index.html": SiteFile(size=1, media_type="text/html", sha256="ab" * 32)}
    assert SourceManifest(root="sites/c/site/tok/", files=files).root == "sites/c/site/tok/"
    assert SourceManifest(root="apps/9f3a/radar/", files=files).root == "apps/9f3a/radar/"
    for bad in ("workspaces/x/", "apps/9f3a/radar", "../apps/x/", "apps"):
        with pytest.raises(ValidationError):
            SourceManifest(root=bad, files=files)


async def test_a_deploy_promotes_its_source_and_keeps_the_previous_deploys(db: None) -> None:
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    blob = _source_store()

    await _deploy(workspace, conversation_id, audience, member_id, blob=blob)
    (row,) = await _stored(workspace)
    first = SourceManifest.model_validate_json(row.source_manifest)
    assert sorted(first.files) == ["index.html"]
    assert first.files["index.html"].media_type == "text/html; charset=utf-8"
    assert first.files["index.html"].sha256 == "ab" * 32
    with ws(workspace.id):
        assert [entry.key for entry in await blob.list(first.root)] == [f"{first.root}index.html"]
        assert await blob.get(f"{first.root}index.html") == SOURCE_BYTES

    await _deploy(workspace, conversation_id, audience, member_id, blob=blob)
    (row,) = await _stored(workspace)
    second = SourceManifest.model_validate_json(row.source_manifest)
    assert second.root != first.root
    with ws(workspace.id):
        assert [entry.key for entry in await blob.list(first.root)] == [f"{first.root}index.html"]
        assert [entry.key for entry in await blob.list(second.root)] == [f"{second.root}index.html"]


async def test_a_publish_over_a_static_deploy_sheds_the_stored_source(db: None) -> None:
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    blob = _source_store()
    await _deploy(workspace, conversation_id, audience, member_id, blob=blob)
    (row,) = await _stored(workspace)
    root = SourceManifest.model_validate_json(row.source_manifest).root

    tool, ctx = _tool(PUBLISH_WEBSITE_TOOL, audience, blob=blob)
    with ws(workspace.id):
        await _dispatch(
            tool,
            _bind(ctx, workspace, conversation_id, member_id),
            project_path="/workspace/app",
            dist_path="dist",
            app_name=SITE,
            run_command="node server.js",
        )
        (row,) = await _stored(workspace)
        assert row.source_manifest is None
        assert [entry.key for entry in await blob.list(root)] == [f"{root}index.html"]


async def test_the_registry_redeploys_a_stored_source_in_place(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    conversation_id = await _seed_conversation(workspace, SHARED_AUDIENCE, None)
    with ws(workspace.id):
        sites = HostedSites(workspace.id, workspace_tx)
        before = await sites.register(
            conversation_id,
            "home",
            8000,
            owner_id,
            "workspace",
            SHARED_AUDIENCE,
            True,
            manifest=_manifest_json(conversation_id, "home", "aa"),
        )
        await sites.set_homepage(workspace.agent_id, conversation_id, "home")
        bound = await sites.homepage(workspace.agent_id)
        assert bound is not None and bound.name == "home"
        assert await sites.homepage(uuid4()) is None

        replacement = _manifest_json(conversation_id, "home", "bb")
        redeployed = await sites.redeploy(conversation_id, "home", replacement)
        assert redeployed is not None
        assert redeployed.deploy_generation > before.deploy_generation
        assert redeployed.source_manifest == replacement
        assert redeployed.port == before.port
        assert redeployed.creator_member_id == owner_id
        assert redeployed.homepage_agent_id == workspace.agent_id
        assert await sites.redeploy(conversation_id, "vanished", replacement) is None


async def test_an_edit_from_another_conversation_redeploys_the_bound_homepage_in_place(
    db: None,
) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(owner_id)
    seed_conversation = await _seed_conversation(workspace, audience, owner_id)
    blob = _source_store()
    deployed = await _deploy(
        workspace, seed_conversation, audience, owner_id, site="home", blob=blob
    )
    with ws(workspace.id):
        await _verb(
            SET_HOMEPAGE_TOOL,
            workspace,
            seed_conversation,
            owner_id,
            site=str(deployed["site"]),
        )
    (row,) = await _stored(workspace)
    old_root = SourceManifest.model_validate_json(row.source_manifest).root
    old_generation = row.deploy_generation

    chat_conversation = await _seed_conversation(workspace, audience, owner_id)
    payload = await _deploy(
        workspace, chat_conversation, audience, owner_id, site="home", blob=blob
    )

    (row,) = await _stored(workspace)
    assert row.conversation_id == seed_conversation
    assert row.deploy_generation > old_generation
    new_root = SourceManifest.model_validate_json(row.source_manifest).root
    assert new_root != old_root
    assert str(seed_conversation) in new_root
    assert payload["site"] == site_object_name(seed_conversation, "home")
    assert payload["site_url"] == site_url(PUBLIC_BASE_URL, workspace.id, seed_conversation, "home")
    with ws(workspace.id):
        assert [entry.key for entry in await blob.list(old_root)] == [f"{old_root}index.html"]
        assert [entry.key for entry in await blob.list(new_root)] == [f"{new_root}index.html"]

    third_conversation = await _seed_conversation(workspace, audience, owner_id)
    renamed = await _deploy(
        workspace,
        third_conversation,
        audience,
        owner_id,
        site=site_object_name(seed_conversation, "home"),
        blob=blob,
    )
    (row,) = await _stored(workspace)
    assert row.conversation_id == seed_conversation
    assert renamed["site"] == site_object_name(seed_conversation, "home")


async def test_a_homepage_redeploy_refuses_speakerless_and_visibility_turns(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(owner_id)
    seed_conversation = await _seed_conversation(workspace, audience, owner_id)
    blob = _source_store()
    deployed = await _deploy(
        workspace, seed_conversation, audience, owner_id, site="home", blob=blob
    )
    with ws(workspace.id):
        await _verb(
            SET_HOMEPAGE_TOOL,
            workspace,
            seed_conversation,
            owner_id,
            site=str(deployed["site"]),
        )
    chat_conversation = await _seed_conversation(workspace, audience, owner_id)

    with pytest.raises(RuntimeError, match="only a member speaking"):
        await _deploy(workspace, chat_conversation, audience, None, site="home", blob=blob)
    with pytest.raises(ValueError, match="redeploy without a visibility argument"):
        await _deploy(
            workspace,
            chat_conversation,
            audience,
            owner_id,
            site="home",
            visibility="public",
            blob=blob,
        )
    (row,) = await _stored(workspace)
    assert row.conversation_id == seed_conversation


async def test_object_get_materializes_the_stored_source(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(owner_id)
    seed_conversation = await _seed_conversation(workspace, audience, owner_id)
    blob = _source_store()
    deployed = await _deploy(
        workspace, seed_conversation, audience, owner_id, site="home", blob=blob
    )
    with ws(workspace.id):
        await _verb(
            SET_HOMEPAGE_TOOL,
            workspace,
            seed_conversation,
            owner_id,
            site=str(deployed["site"]),
        )
    (row,) = await _stored(workspace)

    name = site_object_name(seed_conversation, "home")
    chat_conversation = await _seed_conversation(workspace, audience, owner_id)
    with ws(workspace.id):
        fetched = await _get(workspace, chat_conversation, owner_id, name, blob=blob)
    assert fetched["status"]["source_path"] == f"/workspace/sites/{name}"
    assert fetched["status"]["files"] == ["index.html"]
    assert fetched["status"]["deploy_generation"] == row.deploy_generation
    assert fetched["status"]["homepage_agent"] == str(workspace.agent_id)


async def test_object_get_gates_a_stranger_and_skips_a_serverful_app(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    teammate_id, _teammate = await _seed_member(workspace, TEAMMATE_EMAIL)
    audience = conversation_audience(owner_id)
    conversation_id = await _seed_conversation(workspace, audience, owner_id)
    blob = _source_store()
    deployed = await _deploy(workspace, conversation_id, audience, owner_id, site="docs", blob=blob)

    with ws(workspace.id):
        with pytest.raises(UnknownObject):
            await _get(workspace, conversation_id, teammate_id, str(deployed["site"]), blob=blob)

    app_conversation = await _seed_conversation(workspace, audience, owner_id)
    tool, ctx = _tool(PUBLISH_WEBSITE_TOOL, audience, blob=blob)
    with ws(workspace.id):
        published = await _dispatch(
            tool,
            _bind(ctx, workspace, app_conversation, owner_id),
            project_path="/workspace/app",
            dist_path="dist",
            app_name="apphost",
            run_command="node server.js",
        )
        served = await _get(
            workspace, app_conversation, owner_id, str(published["site"]), blob=blob
        )
    assert served["status"]["source_path"] is None
    assert served["status"]["files"] == []


@dataclass
class WorkingSandbox:
    """A sandbox whose written files persist, so the materialization stamp round-trips."""

    files: dict[str, bytes] = field(default_factory=dict)
    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        if command.startswith("cat "):
            held = self.files.get(shlex.split(command)[1], b"")
            return ExecResult(stdout=held.decode(), stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield SOURCE_BYTES

        return bytes_of()


async def test_a_repeat_object_get_inside_one_generation_transfers_nothing(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(owner_id)
    seed_conversation = await _seed_conversation(workspace, audience, owner_id)
    blob = _source_store()
    deployed = await _deploy(
        workspace, seed_conversation, audience, owner_id, site="home", blob=blob
    )
    name = str(deployed["site"])
    chat_conversation = await _seed_conversation(workspace, audience, owner_id)
    working = WorkingSandbox()
    tool, ctx = _tool("object_get", audience, blob=blob)
    bound = replace(_bind(ctx, workspace, chat_conversation, owner_id), sandbox=working)
    page = f"/workspace/sites/{name}/index.html"

    with ws(workspace.id):
        await _dispatch_get(tool, bound, name)
        assert working.files[page] == SOURCE_BYTES
        working.files[page] = b"work in progress"
        await _dispatch_get(tool, bound, name)
        assert working.files[page] == b"work in progress"
        await _deploy(workspace, seed_conversation, audience, owner_id, site="home", blob=blob)
        await _dispatch_get(tool, bound, name)
        assert working.files[page] == SOURCE_BYTES


async def _dispatch_get(tool: ToolDef, ctx: ToolContext, name: str) -> None:
    await tool.handler(
        ctx,
        tool.input_model.model_validate(
            {"user_description": TOOL_NARRATION, "kind": SITE_KIND, "name": name}
        ),
    )


async def test_a_homepage_redeploy_unhosts_what_its_scratch_server_displaces(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(owner_id)
    seed_conversation = await _seed_conversation(workspace, audience, owner_id)
    blob = _source_store()
    deployed = await _deploy(
        workspace, seed_conversation, audience, owner_id, site="home", blob=blob
    )
    with ws(workspace.id):
        await _verb(
            SET_HOMEPAGE_TOOL,
            workspace,
            seed_conversation,
            owner_id,
            site=str(deployed["site"]),
        )
    chat_conversation = await _seed_conversation(workspace, audience, owner_id)
    tool, ctx = _tool(PUBLISH_WEBSITE_TOOL, audience, blob=blob)
    with ws(workspace.id):
        await _dispatch(
            tool,
            _bind(ctx, workspace, chat_conversation, owner_id),
            project_path="/workspace/app",
            dist_path="dist",
            app_name="apphost",
            run_command="node server.js",
        )
        await _deploy(workspace, chat_conversation, audience, owner_id, site="home", blob=blob)

    stored = {site.name: site for site in await _stored(workspace)}
    assert "apphost" not in stored
    assert stored["home"].conversation_id == seed_conversation
    assert stored["home"].source_manifest is not None


@dataclass(frozen=True)
class OvergrownSandbox:
    """A sandbox whose source tree fails the deploy caps — and whose serve must never run."""

    handle: SandboxHandle = field(
        default_factory=lambda: SandboxHandle(conversation_id=uuid4(), container_id="c1")
    )

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        raise AssertionError("a refused deploy must never reach the serve")

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return ExecResult(stdout="", stderr="dist holds more than 1000 files", exit_code=1)
        return ExecResult(stdout="", stderr="", exit_code=0)


async def test_an_overgrown_source_refuses_before_anything_serves(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(owner_id)
    conversation_id = await _seed_conversation(workspace, audience, owner_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    bound = replace(_bind(ctx, workspace, conversation_id, owner_id), sandbox=OvergrownSandbox())

    with ws(workspace.id), pytest.raises(RuntimeError, match="more than"):
        await _dispatch(
            tool,
            bound,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )
    assert await _stored(workspace) == ()


async def test_unhosting_by_object_delete_keeps_the_stored_source(db: None) -> None:
    workspace = await _seed_workspace()
    owner_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(owner_id)
    conversation_id = await _seed_conversation(workspace, audience, owner_id)
    blob = _source_store()
    deployed = await _deploy(workspace, conversation_id, audience, owner_id, blob=blob)
    (row,) = await _stored(workspace)
    root = SourceManifest.model_validate_json(row.source_manifest).root

    with ws(workspace.id):
        await _verb(
            "object_delete",
            workspace,
            conversation_id,
            owner_id,
            blob=blob,
            kind=SITE_KIND,
            name=str(deployed["site"]),
        )
        assert await _stored(workspace) == ()
        assert [entry.key for entry in await blob.list(root)] == [f"{root}index.html"]


def test_the_claim_program_takes_a_planted_link_without_following_it(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host bytes")
    workspace = tmp_path / "workspace"
    (workspace / "sites" / "home").mkdir(parents=True)
    planted = workspace / "sites" / "home" / "index.html"
    planted.symlink_to(outside)
    guard_home = str(Path(containment.__file__).parent)

    stale = workspace / "sites" / "home" / "dropped.html"
    stale.write_bytes(b"a page a newer deploy removed")
    claimed = subprocess.run(
        [
            sys.executable,
            "-c",
            CLAIM_TREE_PROG,
            str(workspace),
            str(workspace / "sites" / "home"),
            str(planted),
            str(workspace / "sites" / "home" / "assets" / "app.js"),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": guard_home},
    )

    assert claimed.returncode == 0, claimed.stderr
    assert not planted.is_symlink()
    assert planted.read_bytes() == b""
    assert outside.read_bytes() == b"host bytes"
    assert not stale.exists()
    assert (workspace / "sites" / "home" / "assets" / "app.js").read_bytes() == b""


def test_the_enumeration_program_lists_and_bounds_the_source_tree(tmp_path: Path) -> None:
    site = tmp_path / "dist"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_bytes(b"<html></html>")
    (site / "assets" / "app.js").write_bytes(b"0123456789")
    (site / "twin.html").symlink_to(site / "index.html")
    guard_home = str(Path(containment.__file__).parent)

    def enumerate_site(project: Path, max_files: str, max_bytes: str) -> CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                ENUMERATE_PROG,
                str(project),
                str(tmp_path),
                max_files,
                max_bytes,
                ",".join(SOURCE_SKIP_NAMES),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": guard_home},
        )

    listed = enumerate_site(site, "10", "1000")
    assert listed.returncode == 0, listed.stderr
    files = json.loads(listed.stdout)
    assert set(files) == {"index.html", "assets/app.js"}
    assert files["assets/app.js"]["size"] == 10
    assert files["assets/app.js"]["sha256"] == hashlib.sha256(b"0123456789").hexdigest()

    over_files = enumerate_site(site, "1", "1000")
    assert over_files.returncode == 1
    assert "more than 1 files" in over_files.stderr

    over_bytes = enumerate_site(site, "10", "5")
    assert over_bytes.returncode == 1
    assert "more than 5 bytes" in over_bytes.stderr

    (tmp_path / "void").mkdir()
    empty = enumerate_site(tmp_path / "void", "10", "5")
    assert empty.returncode == 1
    assert "no files to host" in empty.stderr
