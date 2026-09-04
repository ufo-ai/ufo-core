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
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from PIL import Image
from ufo_ext_sites.conversation_slot import SITES_SLOT
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.objects import SITE_KIND, site_object_name
from ufo_ext_sites.source import (
    CLAIM_TREE_PROG,
)
from ufo_ext_sites.store import (
    HostedSite,
    HostedSites,
    NotTheSiteCreator,
    SiteFile,
    SourceManifest,
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
    UNCONFIGURED_BODY,
    VISIBILITY_BADGES,
    ShippedAddress,
    SiteHostingUnconfigured,
    homepage_embed_url,
    shipped_address,
    shipped_homepage_url,
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

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness import containment
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.harness.durability import replay_safe_client
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.ingress_host import site_label
from ufo.harness.sandbox.ingress_token import (
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    FramerClaim,
    ShippedClaim,
    verify_ingress_token,
)
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ExecResult, ProxyEndpoint, SandboxProviderUnavailable
from ufo.harness.sandbox.terminal import TerminalAbsent
from ufo.host.ext.loader import member_object_registry, turn_tools
from ufo.runtime.ext.context import context_for
from ufo.runtime.ext.conversation_slots import ConversationSlotContext, ConversationSlotItem
from ufo.runtime.hub import InProcessHub
from ufo.runtime.media.artifact_url import ARTIFACT_KEY_PREFIX, verify_artifact_url
from ufo.runtime.media.image_previews import ImagePreviewGrant
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.object_scope import ObjectActionTarget
from ufo.runtime.objects import AdminRequired, UnknownObject, VerbNotSupported
from ufo.runtime.skills.runtime import SkillRegistry
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.workspace import ws
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
from ufo.sdk.skills import RuntimeSkill
from ufo.sdk.tools import SpeakerRequired
from ufo.serve import RESERVED_HOST_PREFIXES, _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

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
APP_TREE_LISTING = {
    "index.html": {"size": 300, "sha256": "aa" * 32},
    "assets/index-C0gN-8iR.js": {"size": 900, "sha256": "bb" * 32},
    "assets/index-Dr-unzSL.css": {"size": 400, "sha256": "cc" * 32},
    "src/app.tsx": {"size": 120, "sha256": "dd" * 32},
    "src/index.html": {"size": 90, "sha256": "ee" * 32},
    "src/lib/starters.tsx": {"size": 40, "sha256": "99" * 32},
    "src/vite.config.ts": {"size": 60, "sha256": "ff" * 32},
}
APP_TREE_PATHS = sorted(APP_TREE_LISTING)


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

    It names a conversation because the real sandbox does, and the hosting tools read it off there:
    a site is registered against whichever conversation's sandbox serves the port, which for a
    subagent is the one that spawned it rather than its own."""

    conversation_id: UUID = field(default_factory=uuid4)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        return ExecResult(stdout="123\n" if detach else "", stderr="", exit_code=0)

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def runtime_path(self, relative: str) -> str:
        return f"/runtime/{relative}"

    async def write_runtime_file(self, relative: str, content: bytes) -> None:
        return None

    async def write_runtime_path(self, path: str, content: bytes) -> None:
        return None

    async def write_file(self, path: str, content: bytes) -> None:
        return None

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield SOURCE_BYTES

        return bytes_of()


@dataclass
class WorkingSandbox:
    """A sandbox whose written files persist, so the materialization stamp round-trips."""

    files: dict[str, bytes] = field(default_factory=dict)
    listing: dict[str, dict[str, object]] = field(default_factory=lambda: SOURCE_LISTING)
    conversation_id: UUID = field(default_factory=uuid4)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        if command.startswith("cat "):
            held = self.files.get(shlex.split(command)[1], b"")
            return ExecResult(stdout=held.decode(), stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        return ExecResult(stdout="123\n" if detach else "", stderr="", exit_code=0)

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return ExecResult(stdout=json.dumps(self.listing), stderr="", exit_code=0)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def runtime_path(self, relative: str) -> str:
        return f"/runtime/{relative}"

    async def write_runtime_file(self, relative: str, content: bytes) -> None:
        self.files[f"/runtime/{relative}"] = content

    async def write_runtime_path(self, path: str, content: bytes) -> None:
        self.files[path] = content

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def read_file(self, path: str) -> AsyncIterator[bytes]:
        async def bytes_of() -> AsyncIterator[bytes]:
            yield SOURCE_BYTES

        return bytes_of()


def _png(color: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), color).save(buffer, format="PNG")
    return buffer.getvalue()


PAGE_PNG = _png("white")
PREVIEW_SERVICE_PNG = _png("navy")
CARD_DIGEST = "5f2c" * 16
REDRAWN_DIGEST = "a91b" * 16


@dataclass
class ShootingSandbox:
    """Stands in for the member's container while the preview service photographs its hosted page:
    every command succeeds and answers the share-card byte count, every in-sandbox program answers
    the digest the card's encode step prints, and reading the card back hands over one real PNG.

    It answers the same thing to every command, so it records and asserts nothing about what the
    tools said. What the deploy did with the bytes is the contract: the rows it wrote, the preview
    service's picture the store now holds, and the card the frame's head then names. An empty
    `digest` is a container that composes no card — which is what every site deployed before cards
    existed has."""

    png: bytes = PAGE_PNG
    digest: str = CARD_DIGEST
    conversation_id: UUID = field(default_factory=uuid4)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout=str(len(self.png)), stderr="", exit_code=0)

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        return ExecResult(stdout="123\n" if detach else "", stderr="", exit_code=0)

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout=self.digest, stderr="", exit_code=0)

    async def runtime_path(self, relative: str) -> str:
        return f"/runtime/{relative}"

    async def write_runtime_file(self, relative: str, content: bytes) -> None:
        return None

    async def write_runtime_path(self, path: str, content: bytes) -> None:
        return None

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

    conversation_id: UUID = field(default_factory=uuid4)

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
    portalless: AsyncClient
    """A deploy that installs no browser portal — the one place a kit page can be read — so the
    frame has nowhere to send a visit that arrives outside one."""


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

    def mounted(ingress_public_url: str | None, *, portal: bool = True) -> AsyncClient:
        app = FastAPI()
        manifests = (web_manifest(), sites_manifest()) if portal else (sites_manifest(),)
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

    async with (
        mounted(INGRESS_BASE_URL) as client,
        mounted(None) as unhosted,
        mounted(INGRESS_BASE_URL, portal=False) as portalless,
    ):
        yield Deployment(
            workspace=workspace, client=client, unhosted=unhosted, portalless=portalless
        )
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
    workspace: Workspace,
    audience: Audience,
    member_id: UUID | None,
    conversation_id: UUID | None = None,
) -> UUID:
    conversation_id = conversation_id or uuid4()
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
    """One real tool and the context the engine dispatches it with, its extension bound: a wire
    tool by name, or a site action by its short name out of the action registry."""
    tools, ext_by_tool, verbs = turn_tools(
        (sites_manifest(),),
        None,
        audience=audience,
        public_base_url=public_base_url,
        artifact_token_secret=ARTIFACT_SECRET,
    )
    actions = {
        bound.action.name: bound for held in verbs.actions.values() for bound in held.values()
    }
    if name in actions:
        tool, ext = actions[name].action, actions[name].context
    else:
        tool, ext = next(entry for entry in tools if entry.name == name), ext_by_tool.get(name)
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
        ext=ext,
    )


def _bind(
    ctx: ToolContext,
    workspace: Workspace,
    conversation_id: UUID,
    speaker_member_id: UUID | None,
    *,
    serving_conversation_id: UUID | None = None,
    subagent_profile: str | None = None,
    sandbox: FakeSandbox | ShootingSandbox | WorkingSandbox | None = None,
    skills: SkillRegistry | None = None,
) -> ToolContext:
    """The turn and the sandbox it runs in, bound together. `serving_conversation_id` is the
    conversation whose sandbox is answering — the turn's own unless this is a subagent turn, which
    runs in the sandbox of the turn that spawned it. `sandbox` is which stand-in answers that
    container's commands: the plain one, or the one that photographs the page. `skills` is the
    deploy's registry the turn resolves app pages through."""
    selected_sandbox = replace(
        sandbox or FakeSandbox(),
        conversation_id=serving_conversation_id or conversation_id,
    )
    site_previewer = ctx.site_previewer
    if isinstance(selected_sandbox, ShootingSandbox):

        def preview_response(request: Request) -> Response:
            return Response(
                200,
                content=PREVIEW_SERVICE_PNG,
                headers={
                    "content-type": "image/png",
                    "x-preview-width": str(PREVIEW_WIDTH),
                    "x-preview-height": str(PREVIEW_HEIGHT),
                },
            )

        site_previewer = SitePreviewer(
            blob=ctx.blob,
            service_url="http://preview.svc:8930",
            token="preview-token",
            ingress_public_url=INGRESS_BASE_URL,
            transport=MockTransport(preview_response),
        )
    return replace(
        ctx,
        skills=skills or ctx.skills,
        sandbox=selected_sandbox,
        turn=ctx.turn.model_copy(
            update={
                "workspace_id": workspace.id,
                "conversation_id": conversation_id,
                "agent_id": workspace.agent_id,
                "subagent_profile": subagent_profile,
            }
        ),
        speaker_member_id=speaker_member_id,
        site_previewer=site_previewer,
    )


async def _dispatch(tool: ToolDef, ctx: ToolContext, **args: object) -> dict[str, object]:
    """The handler call the engine makes: an instance action arrives with its target folded on —
    the turn's own agent, the row every homepage bind in these tests names."""
    if tool.bound is not None and tool.bound.binding == "instance":
        async with workspace_tx() as connection:
            name = (
                await connection.execute(
                    sa.select(tables.agent.c.name).where(tables.agent.c.id == ctx.turn.agent_id)
                )
            ).scalar_one()
        ctx = replace(
            ctx,
            target=ObjectActionTarget(
                kind=tool.bound.kind,
                name=name,
                agent=None,
                generation=None,
                expected_generation=None,
            ),
        )
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
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
    sandbox: FakeSandbox | ShootingSandbox | WorkingSandbox | None = None,
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


def _iframe_cookie(token: str) -> dict[str, str]:
    return {
        **_cookie(token),
        "sec-fetch-dest": "iframe",
        "sec-fetch-site": "same-origin",
    }


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
        _bind(ctx, workspace, conversation_id, None),
        turn=_bind(ctx, workspace, conversation_id, None).turn.model_copy(
            update={"on_behalf_of_member_id": creator_id}
        ),
    )

    with ws(workspace.id), pytest.raises(SpeakerRequired):
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
        _bind(ctx, workspace, conversation_id, None),
        turn=_bind(ctx, workspace, conversation_id, None).turn.model_copy(
            update={"on_behalf_of_member_id": creator_id}
        ),
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
        _bind(ctx, workspace, conversation_id, None),
        turn=_bind(ctx, workspace, conversation_id, None).turn.model_copy(
            update={"on_behalf_of_member_id": creator_id}
        ),
    )

    with ws(workspace.id), pytest.raises(SpeakerRequired):
        await _dispatch(tool, scheduled, kind=SITE_KIND, name=name)

    assert len(await _stored(workspace)) == 1


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
    assert "if(ended)location.reload()" in opened.text
    assert "addEventListener('focus',refresh)" in opened.text
    assert opened.text.index("</iframe>") < opened.text.index("const frame=")
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


async def test_an_unseated_members_session_cannot_open_a_non_public_site(
    deployment: Deployment,
) -> None:
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    link = str((await _deploy(workspace, conversation_id, audience, creator_id))["site_url"])
    assert "<iframe" in (await client.get(link, headers=_cookie(creator_token))).text
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == creator_id)
            .values(seated_at=None, updated_at=sa.func.now())
        )

    refused = await client.get(link, headers=_cookie(creator_token))

    assert refused.status_code == 200
    assert "not signed in to the workspace" in refused.text
    assert "<iframe" not in refused.text


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
    assert row.preview_size_bytes == len(PREVIEW_SERVICE_PNG)


async def test_a_site_without_the_preview_service_still_composes_its_share_card(
    db: None, tmp_path: Path
) -> None:
    workspace = await _seed_workspace()
    creator_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    conversation_id = await _seed_conversation(workspace, audience, creator_id)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience, blob=blob)
    bound = replace(
        _bind(
            ctx,
            workspace,
            conversation_id,
            creator_id,
            sandbox=ShootingSandbox(),
        ),
        site_previewer=None,
    )

    with ws(workspace.id):
        await _dispatch(
            tool,
            bound,
            project_path="/workspace/dist",
            site_name=SITE,
            entry_point="index.html",
            visibility="public",
        )

    (row,) = await _stored(workspace)
    assert row.preview_blob_key is None
    assert row.share_card_hash == CARD_DIGEST
    assert row.share_card_blob_key is not None
    with ws(workspace.id):
        assert await blob.get(row.share_card_blob_key) == PAGE_PNG


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
        _bind(ctx, workspace, conversation_id, None),
        turn=_bind(ctx, workspace, conversation_id, None).turn.model_copy(
            update={"on_behalf_of_member_id": creator_id}
        ),
    )

    with ws(workspace.id), pytest.raises(SpeakerRequired):
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
        tool.input_model.model_validate({"ref": f"{SITE_KIND}/{name}"}),
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
            turn=_bind(
                ctx, workspace, conversation_id, None, subagent_profile="website_building"
            ).turn.model_copy(update={"on_behalf_of_member_id": member_id}),
        )
        with ws(workspace.id), pytest.raises(SpeakerRequired):
            await _dispatch(
                tool, child, project_path="/workspace/dist", visibility="workspace", **args
            )

    (row,) = await _stored(workspace)
    assert row.name == "marketing"


@dataclass(frozen=True)
class FailingSandbox:
    """A sandbox whose serve never comes up — the readiness probe's failure, which is what a broken
    build looks like from here."""

    conversation_id: UUID = field(default_factory=uuid4)

    async def bash(self, command: str, timeout_s: int = 120) -> ExecResult:
        return ExecResult(stdout="", stderr="port never opened", exit_code=1)

    async def bash_task(
        self,
        command: str,
        base: str,
        *,
        detach: bool,
        model_authored: bool,
        timeout_s: int | None = None,
    ) -> ExecResult:
        return ExecResult(stdout="123\n" if detach else "", stderr="", exit_code=0)

    async def sh(self, script: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def python(self, program: str, *args: str, timeout_s: int | None = None) -> ExecResult:
        if program is ENUMERATE_PROG:
            return _listed_source()
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def runtime_path(self, relative: str) -> str:
        return f"/runtime/{relative}"

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

    with ws(workspace.id):
        result = await _dispatch(
            tool,
            broken,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )

    assert "serve /workspace/dist" in result["operation"]
    (row,) = await _stored(workspace)
    assert row.name == "marketing"


@dataclass(frozen=True)
class FailingInstallSandbox:
    """A sandbox whose install step fails — `publish_website`'s first mutation, and its earliest
    point of no return once a row has been written."""

    conversation_id: UUID = field(default_factory=uuid4)

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
            FailingInstallSandbox(conversation_id=conversation_id),
            "npm install",
        ),
        (
            FailingSandbox(conversation_id=conversation_id),
            None,
        ),
    ):
        await _deploy(workspace, conversation_id, audience, member_id, site="marketing")
        tool, ctx = _tool(PUBLISH_WEBSITE_TOOL, audience)
        broken = replace(_bind(ctx, workspace, conversation_id, member_id), sandbox=sandbox)
        with ws(workspace.id):
            result = await _dispatch(
                tool,
                broken,
                project_path="/workspace/app",
                dist_path="dist",
                app_name="pricing",
                install_command=install_command,
                run_command="node server.js",
            )
        assert result["summary"]
        (row,) = await _stored(workspace)
        assert row.name == "marketing"


@dataclass(frozen=True)
class StoppedSitePreviewer:
    error: BaseException = field(
        default_factory=lambda: RuntimeError("the turn ended while the preview service was drawing")
    )

    async def render(self, *args: object) -> None:
        raise self.error


async def test_a_deploy_whose_picture_fails_still_reports_the_live_site(db: None) -> None:
    """The picture is drawn after `register`, so by the time it can fail the site is live, holding
    the port, and the site it displaced is already retired. Raising there reports the render error
    as the whole call: the agent never learns the URL its own deploy just published, and reads a
    finished deploy as one that did nothing. The deploy is the act; the picture is decoration on a
    row that exists."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    bound = replace(
        _bind(ctx, workspace, conversation_id, member_id),
        site_previewer=StoppedSitePreviewer(),  # type: ignore[arg-type]
    )

    with ws(workspace.id):
        result = await _dispatch(
            tool,
            bound,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )

    assert result["site_name"] == "pricing"
    assert result["site_url"]
    assert "the preview service was drawing" in str(result["preview_error"])
    (row,) = await _stored(workspace)
    assert row.name == "pricing"
    assert row.preview_blob_key is None


@pytest.mark.parametrize(
    "escape",
    (
        SandboxProviderUnavailable("e2b control plane did not recover"),
        TerminalAbsent("no terminal answered"),
    ),
    ids=("provider", "terminal"),
)
async def test_a_gone_sandbox_is_not_reported_as_a_picture_that_would_not_draw(
    db: None, escape: BaseException
) -> None:
    """`draw_from_page` photographs the page inside the sandbox, so both carrier escapes reach the
    picture step. The engine parks the turn on one and ends it on the other — read as a preview
    note, neither happens and a deploy on a box that is gone reports as finished."""
    workspace = await _seed_workspace()
    member_id, _token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(member_id)
    conversation_id = await _seed_conversation(workspace, audience, member_id)
    tool, ctx = _tool(DEPLOY_WEBSITE_TOOL, audience)
    bound = replace(
        _bind(ctx, workspace, conversation_id, member_id),
        site_previewer=StoppedSitePreviewer(escape),  # type: ignore[arg-type]
    )

    with ws(workspace.id), pytest.raises(type(escape)):
        await _dispatch(
            tool,
            bound,
            project_path="/workspace/dist",
            site_name="pricing",
            entry_point="index.html",
        )


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
        _bind(ctx, workspace, conversation_id, None),
        turn=_bind(ctx, workspace, conversation_id, None).turn.model_copy(
            update={"on_behalf_of_member_id": member_id}
        ),
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
        _bind(ctx, workspace, conversation_id, None),
        turn=_bind(ctx, workspace, conversation_id, None).turn.model_copy(
            update={"on_behalf_of_member_id": member_id}
        ),
    )
    later = replace(
        scheduled,
        turn=scheduled.turn.model_copy(update={"created_at": datetime(2027, 1, 1, tzinfo=UTC)}),
    )

    with ws(workspace.id), pytest.raises(SpeakerRequired):
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


def test_a_homepage_embed_url_preserves_the_site_address_and_marks_the_portal_frame() -> None:
    ws_id, conversation_id = uuid4(), uuid4()
    hosted = site_url(PUBLIC_BASE_URL, ws_id, conversation_id, "dash")

    embedded = homepage_embed_url(hosted)

    address = site_address(embedded.rpartition("/")[2])
    assert address is not None
    assert (address.workspace_id, address.conversation_id, address.name, address.portal_embed) == (
        ws_id,
        conversation_id,
        "dash",
        True,
    )
    with pytest.raises(ValueError, match="hosted-site URL"):
        homepage_embed_url("not-a-site")


def test_a_shipped_homepage_url_round_trips_and_never_collides_with_a_site_token() -> None:
    """The shipped frame link carries its bundle address and never parses as a hosted-site link."""
    ws_id = uuid4()
    url = shipped_homepage_url(PUBLIC_BASE_URL, ws_id, "radar", SHIPPED_DIGEST)
    assert url is not None and url.startswith(f"{PUBLIC_BASE_URL}{FRAME_PATH}/")
    token = url.rpartition("/")[2]
    assert shipped_address(token) == ShippedAddress(ws_id, "radar", SHIPPED_DIGEST)
    assert site_address(token) is None
    assert shipped_address(site_token(ws_id, uuid4(), "dash")) is None
    assert shipped_homepage_url(None, ws_id, "radar", SHIPPED_DIGEST) is None


async def test_a_shipped_app_frame_redirects_without_viewer_or_agent_reads(
    deployment: Deployment,
) -> None:
    """A shipped app page has no hosted_site row: the frame resolves the agent from the token, gates
    on its visibility exactly as a bound homepage does, and redirects to the deploy-wide bundle from
    the fleet store — the ingress view token it mints carries the shipped claim and the synthetic
    per-workspace anchor, an unconfigured ingress renders a refusal rather than redirecting nowhere,
    and a visit from outside that frame is sent to the app's screen in the portal."""
    client, workspace = deployment.client, deployment.workspace
    _other_id, other_token = await _seed_member(workspace, OTHER_EMAIL)
    app_agent = await _seed_app_agent(workspace, "radar")
    url = shipped_homepage_url(PUBLIC_BASE_URL, workspace.id, "radar", SHIPPED_DIGEST)
    assert url is not None

    # A visit from outside the portal's frame is sent to the app's screen there, signed in or not:
    # the page draws from the `init` the portal hands it over the bridge, so on its own it would
    # hold a screen that never receives one. The portal asks whoever arrives to sign in.
    anonymous = await client.get(url)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == f"{PUBLIC_BASE_URL}/surface/web#/agents/{app_agent}"
    assert "<iframe" not in anonymous.text

    # The same rule with a session: a kit page opened outside the portal's frame lands on the app's
    # screen in the portal.
    standalone = await client.get(url, headers=_cookie(other_token))
    assert standalone.status_code == 303
    assert standalone.headers["location"] == f"{PUBLIC_BASE_URL}/surface/web#/agents/{app_agent}"

    opened = await client.get(url, headers=_iframe_cookie(other_token))
    assert opened.status_code == 303
    assert "<iframe" not in opened.text
    embedded = opened.headers["location"]
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
    assert (await client.get(url, headers=_iframe_cookie(other_token))).status_code == 303

    unhosted = await deployment.unhosted.get(url, headers=_iframe_cookie(other_token))
    assert unhosted.status_code == 200
    assert "<iframe" not in unhosted.text
    assert UNCONFIGURED_BODY in unhosted.text


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


def _manifest(root: str, listing: dict[str, dict[str, object]]) -> SourceManifest:
    return SourceManifest(
        root=root,
        files={
            path: SiteFile(
                size=int(str(entry["size"])),
                media_type="text/plain",
                sha256=str(entry["sha256"]),
            )
            for path, entry in listing.items()
        },
    )


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


APP_PAGE_SKILLS = SkillRegistry(
    {"app-chat-home": RuntimeSkill(name="app-chat-home", description="", instructions="")}
)


async def _dispatch_get(tool: ToolDef, ctx: ToolContext, name: str) -> dict[str, object]:
    result = await tool.handler(
        ctx,
        tool.input_model.model_validate({"kind": SITE_KIND, "name": name}),
    )
    fetched = yaml.safe_load(result.content[0].text)
    assert isinstance(fetched, dict)
    status = fetched["status"]
    assert isinstance(status, dict)
    return status


@dataclass(frozen=True)
class OvergrownSandbox:
    """A sandbox whose source tree fails the deploy caps — and whose serve must never run."""

    conversation_id: UUID = field(default_factory=uuid4)

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


async def test_a_site_framed_by_a_sibling_carries_that_siblings_address(
    deployment: Deployment,
) -> None:
    client, workspace = deployment.client, deployment.workspace
    creator_id, creator_token = await _seed_member(workspace, OWNER_EMAIL)
    audience = conversation_audience(creator_id)
    framer_id = await _seed_conversation(workspace, audience, creator_id)
    target_id = await _seed_conversation(workspace, audience, creator_id)
    await _deploy(workspace, framer_id, audience, creator_id)
    target = await _deploy(workspace, target_id, audience, creator_id)
    framer = FramerClaim(conversation_id=framer_id, port=serve_port(framer_id))
    headers = {
        **_iframe_cookie(creator_token),
        "referer": f"https://{site_label(framer.conversation_id, framer.port)}.{INGRESS_HOST}/",
    }

    opened = await client.get(str(target["site_url"]), headers=headers)

    embedded = _embedded(opened.text)
    token = urlsplit(embedded).path.removeprefix(f"{INGRESS_VIEW_PATH}/")
    claims = verify_ingress_token(token, datetime.now(UTC), INGRESS_VIEW_KIND)
    assert claims.framer == framer
