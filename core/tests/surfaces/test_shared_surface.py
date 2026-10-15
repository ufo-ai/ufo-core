"""Shared surfaces bind one identified workspace for the full request lifecycle."""

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo import serve
from ufo.blob import blob_store_for
from ufo.config import BlobConfig
from ufo.db import current_workspace, workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.ingress_host import parse_site_label
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.ext.surface import SurfaceAuth, SurfaceContext, SurfaceRoute, SurfaceSpec
from ufo.runtime.hub import InProcessHub
from ufo.runtime.surfaces.admission import Admission, MemberAdmission
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.serve import _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

PROBE_SURFACE = "probe"


class NoAdmission:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the lifecycle probe must not admit a turn")


SITE_CONVERSATION = UUID("11111111-1111-1111-1111-111111111111")
SITE_PORT = 8000
SITE_BASE = "https://sites.example.test"


async def _identify_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None:
    raw = request.headers.get("x-workspace")
    return UUID(raw) if raw else None


async def _stream_workspace(ctx: SurfaceContext, request: Request) -> Response:
    async def frames() -> AsyncIterator[bytes]:
        yield f"ctx-ws={ctx.workspace_id}\n".encode()
        yield f"stream-ws={current_workspace.get()}\n".encode()

    return StreamingResponse(frames(), media_type="text/plain")


async def _raise_after_binding(ctx: SurfaceContext, request: Request) -> Response:
    raise HTTPException(404, "no such probe")


async def _challenge(_request: Request, _auth: SurfaceAuth) -> Response:
    assert current_workspace.get() is None
    return Response("challenge")


async def _must_not_run(_ctx: SurfaceContext, _request: Request) -> Response:
    raise AssertionError("a pre-binding response must skip the surface handler")


async def _mint_site_link(ctx: SurfaceContext, request: Request) -> Response:
    return Response(ctx.ingress_url(SITE_CONVERSATION, SITE_PORT, "/") or "")


def _site_link_app(tmp_path: Path, ingress_public_url: str | None) -> FastAPI:
    surface = SurfaceSpec(
        name=PROBE_SURFACE,
        routes=(SurfaceRoute(method="POST", path="site", handler=_mint_site_link),),
        identify=_identify_workspace,
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (Manifest(name="probe_ext", version="0", surfaces=(surface,)),),
        None,
        blob_store_for(BlobConfig(backend="filesystem", root=tmp_path)),
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        NoAdmission(),
        "",
        None,
        ingress_public_url,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    return app


def _app(tmp_path: Path) -> FastAPI:
    surface = SurfaceSpec(
        name=PROBE_SURFACE,
        routes=(
            SurfaceRoute(method="POST", path="ping", handler=_stream_workspace),
            SurfaceRoute(method="POST", path="boom", handler=_raise_after_binding),
        ),
        identify=_identify_workspace,
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (Manifest(name="probe_ext", version="0", surfaces=(surface,)),),
        None,
        blob_store_for(BlobConfig(backend="filesystem", root=tmp_path)),
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        NoAdmission(),
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    return app


def _challenge_app(tmp_path: Path) -> FastAPI:
    surface = SurfaceSpec(
        name=PROBE_SURFACE,
        routes=(SurfaceRoute(method="POST", path="challenge", handler=_must_not_run),),
        identify=_challenge,
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (Manifest(name="probe_ext", version="0", surfaces=(surface,)),),
        None,
        blob_store_for(BlobConfig(backend="filesystem", root=tmp_path)),
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        NoAdmission(),
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    return app


async def test_the_mounted_surface_admits_through_the_process_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate a member's turn passes is the one `serve._admission` built."""
    built: list[Admission] = []
    factory = serve._admission

    def _record(*ingredients: object) -> Admission:
        built.append(factory(*ingredients))
        return built[-1]

    monkeypatch.setattr(serve, "_admission", _record)
    served: list[Admission] = []

    async def _report_gate(ctx: SurfaceContext, _request: Request) -> Response:
        admitter = ctx._admitter
        assert isinstance(admitter, MemberAdmission)
        served.append(admitter.admission)
        return Response("")

    surface = SurfaceSpec(
        name=PROBE_SURFACE,
        routes=(SurfaceRoute(method="POST", path="gate", handler=_report_gate),),
        identify=_identify_workspace,
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (Manifest(name="probe_ext", version="0", surfaces=(surface,)),),
        None,
        blob_store_for(BlobConfig(backend="filesystem", root=tmp_path)),
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        NoAdmission(),
        "",
        None,
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
    )
    baseline = current_workspace.set(None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
            reply = await client.post(
                f"/surface/{PROBE_SURFACE}/gate", headers={"x-workspace": str(uuid4())}
            )
    finally:
        current_workspace.reset(baseline)
    assert reply.status_code == 200, reply.text
    assert len(built) == 1, built
    assert served[0] is built[0]


async def _workspace() -> UUID:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


async def test_binding_survives_the_stream_then_releases(tmp_path: Path) -> None:
    baseline = current_workspace.set(None)
    workspace_id = uuid4()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app(tmp_path)), base_url="http://fleet"
        ) as client:
            reply = await client.post(
                "/surface/probe/ping", headers={"x-workspace": str(workspace_id)}
            )
        assert reply.status_code == 200, reply.text
        assert reply.text == f"ctx-ws={workspace_id}\nstream-ws={workspace_id}\n"
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def test_request_replaces_a_stale_inherited_binding(tmp_path: Path) -> None:
    stale_workspace, request_workspace = uuid4(), uuid4()
    baseline = current_workspace.set(stale_workspace)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app(tmp_path)), base_url="http://fleet"
        ) as client:
            reply = await client.post(
                "/surface/probe/ping", headers={"x-workspace": str(request_workspace)}
            )
        assert reply.status_code == 200, reply.text
        assert reply.text == f"ctx-ws={request_workspace}\nstream-ws={request_workspace}\n"
        assert str(stale_workspace) not in reply.text
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def test_binding_releases_when_the_handler_raises(tmp_path: Path) -> None:
    baseline = current_workspace.set(None)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app(tmp_path)), base_url="http://fleet"
        ) as client:
            reply = await client.post("/surface/probe/boom", headers={"x-workspace": str(uuid4())})
        assert reply.status_code == 404, reply.text
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def test_unidentified_request_clears_an_inherited_binding(tmp_path: Path) -> None:
    baseline = current_workspace.set(uuid4())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app(tmp_path)), base_url="http://fleet"
        ) as client:
            reply = await client.post("/surface/probe/ping")
        assert reply.status_code == 401, reply.text
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def test_pre_binding_response_skips_binding_and_releases_inherited_scope(
    tmp_path: Path,
) -> None:
    baseline = current_workspace.set(uuid4())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_challenge_app(tmp_path)), base_url="http://fleet"
        ) as client:
            reply = await client.post("/surface/probe/challenge")
        assert reply.status_code == 200
        assert reply.text == "challenge"
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def test_surface_auth_resolves_only_the_exact_installation_to_a_workspace(db: None) -> None:
    first, second = await _workspace(), await _workspace()
    async with workspace_tx() as connection:
        for workspace_id, team in ((first, "team-a"), (second, "team-b")):
            await connection.execute(
                sa.insert(tables.surface_installation).values(
                    routes_ingress=True,
                    workspace_id=workspace_id,
                    surface="slack",
                    installation_id=team,
                    agent_id=sa.select(tables.agent.c.id)
                    .where(tables.agent.c.workspace_id == workspace_id)
                    .order_by(tables.agent.c.created_at, tables.agent.c.id)
                    .limit(1)
                    .scalar_subquery(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    slack = SurfaceAuth(_credentials=None, _declared=frozenset(), _surface="slack")
    other = SurfaceAuth(_credentials=None, _declared=frozenset(), _surface="other")
    with ws(first):
        assert await slack.workspace("team-b") == second
        assert await slack.workspace("unknown") is None
        assert await other.workspace("team-b") is None


async def test_the_ingress_base_reaches_a_mounted_surface(
    db: None, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    baseline = current_workspace.set(None)
    try:
        workspace_id = await _workspace()
        async with AsyncClient(
            transport=ASGITransport(app=_site_link_app(tmp_path, SITE_BASE)),
            base_url="http://probe",
        ) as client:
            minted = await client.post(
                f"/surface/{PROBE_SURFACE}/site", headers={"x-workspace": str(workspace_id)}
            )
        assert minted.status_code == 200
        label, _, host = urlsplit(minted.text).netloc.partition(".")
        assert host == "sites.example.test"
        assert parse_site_label(label) == (SITE_CONVERSATION, SITE_PORT)
        async with AsyncClient(
            transport=ASGITransport(app=_site_link_app(tmp_path, None)),
            base_url="http://probe",
        ) as client:
            unset = await client.post(
                f"/surface/{PROBE_SURFACE}/site", headers={"x-workspace": str(workspace_id)}
            )
        assert unset.status_code == 200
        assert unset.text == ""
    finally:
        current_workspace.reset(baseline)
