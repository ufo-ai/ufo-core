from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ExtensionContext, ScopedStore
from selfhost.ext.manifest import Manifest, RouteSpec
from selfhost.schema import tables
from selfhost.serve import _mount_ext_routes

EXTENSION = "sample"


async def _hook(ctx: ExtensionContext, request: Request) -> Response:
    body = (await request.body()).decode()
    await ctx.store.put("last_hook", {"body": body})
    return PlainTextResponse(f"{ctx.store.extension}:{body}")


HOOK_ROUTE = RouteSpec(method="POST", path="hook", handler=_hook)


async def _mount(workspace_id: UUID) -> AsyncClient:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    manifest = Manifest(name=EXTENSION, version="0.1.0", routes=(HOOK_ROUTE,))
    app = FastAPI()
    _mount_ext_routes(app, (manifest,), workspace_id, store)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://serve")


async def test_extension_route_reaches_its_scoped_context(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    client = await _mount(workspace_id)
    async with client:
        response = await client.post("/ext/sample/hook", content="payload")
    assert response.status_code == 200
    assert response.text == "sample:payload"
    scoped = ScopedStore(workspace_id=workspace_id, extension=EXTENSION)
    assert await scoped.get("last_hook") == {"body": "payload"}


def test_mount_ext_routes_fails_loud_without_credential_key() -> None:
    manifest = Manifest(name=EXTENSION, version="0.1.0", routes=(HOOK_ROUTE,))
    with pytest.raises(RuntimeError, match="serves routes but no credential key"):
        _mount_ext_routes(FastAPI(), (manifest,), uuid4(), None)
