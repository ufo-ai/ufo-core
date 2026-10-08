import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from ufo_testsupport.cloud import TEST_BEARER, cloud_apis_for

from ufo.config import BlobConfig, CloudConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.host.ext.loader import proxy_credentials
from ufo.runtime.access.credentials import CredentialSlotUnset, CredentialStore
from ufo.runtime.cloud import (
    CLOUD_BODY_MAX_BYTES,
    IDEMPOTENCY_HEADER,
    CloudApis,
    CloudRefused,
    CloudUnavailable,
    LoopClients,
    proxy_bearer,
)
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.ext.manifest import CredentialSlot, Manifest, ProxyCredentialSpec
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.serve import _require_cloud

pytestmark = pytest.mark.usefixtures("db")

API_URL = "https://api.test"
SEARCH_PATH = "/v1/memory/search"
SYSTEM_TOKEN_SLOT = "cloud_system_token"


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str
    limit: int | None = None


class Echo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ok: bool


@dataclass(frozen=True)
class Sent:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class _MintingBearer:
    ctx: ExtensionContext

    async def bearer(self) -> str:
        try:
            return await self.ctx.credentials.get(SYSTEM_TOKEN_SLOT)
        except CredentialSlotUnset:
            minted = secrets.token_urlsafe(24)
            await self.ctx.credentials.put(SYSTEM_TOKEN_SLOT, minted)
            return minted


@dataclass(frozen=True)
class _Unreadable:
    ctx: ExtensionContext

    async def bearer(self) -> str:
        raise RuntimeError("the slot could not be decrypted")


DECLARING = Manifest(
    name="acme",
    version="0",
    credentials=(
        CredentialSlot(
            name=SYSTEM_TOKEN_SLOT, description="The workspace's system token.", minted=True
        ),
    ),
    proxy_credentials=ProxyCredentialSpec(build=_MintingBearer),
)


def _recorder(status: int = 200, answer: bytes = b'{"ok":true}') -> Starlette:
    async def record(request: Request) -> Response:
        request.app.state.sent.append(
            Sent(request.method, str(request.url), dict(request.headers), await request.body())
        )
        return Response(answer, status_code=status, media_type="application/json")

    app = Starlette(
        routes=[Route("/{path:path}", record, methods=["GET", "POST", "PATCH", "DELETE"])]
    )
    app.state.sent = []
    return app


@pytest.fixture
def store() -> CredentialStore:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    return store


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


@pytest.fixture
async def recorder() -> Starlette:
    return _recorder()


@pytest.fixture
async def apis(store: CredentialStore, recorder: Starlette) -> AsyncIterator[CloudApis]:
    credentials = proxy_credentials((DECLARING,))
    assert credentials is not None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=recorder)) as client:
        yield CloudApis(API_URL, lambda: client, proxy_bearer(credentials))


async def _slot(store: CredentialStore, workspace_id: UUID) -> str:
    with ws(workspace_id):
        return await store.get(workspace_id, SYSTEM_TOKEN_SLOT)


async def test_send_presents_the_bound_workspaces_bearer(
    store: CredentialStore, recorder: Starlette, apis: CloudApis
) -> None:
    first, second = await _workspace(), await _workspace()
    for workspace_id in (first, second):
        with ws(workspace_id):
            await store.put(workspace_id, SYSTEM_TOKEN_SLOT, f"bearer-{workspace_id}")

    answered = await apis.bound(first).send("POST", SEARCH_PATH, body=Probe(q="a"), answer=Echo)
    await apis.bound(second).send("POST", SEARCH_PATH, body=Probe(q="a"), answer=Echo)

    assert answered == Echo(ok=True)
    one, two = recorder.state.sent
    assert one.url == f"{API_URL}{SEARCH_PATH}"
    assert one.method == "POST"
    assert one.headers["authorization"] == f"Bearer bearer-{first}"
    assert one.headers["content-type"] == "application/json"
    assert one.body == b'{"q":"a","limit":null}'
    assert two.headers["authorization"] == f"Bearer bearer-{second}"


async def test_an_empty_slot_mints_and_the_next_call_presents_the_same_bearer(
    store: CredentialStore, recorder: Starlette, apis: CloudApis
) -> None:
    workspace_id = await _workspace()
    with pytest.raises(CredentialSlotUnset):
        await _slot(store, workspace_id)

    await apis.bound(workspace_id).send("GET", "/v1/memory/pages", answer=Echo)
    await apis.bound(workspace_id).send("GET", "/v1/memory/pages", answer=Echo)

    minted = await _slot(store, workspace_id)
    first, second = recorder.state.sent
    assert first.headers["authorization"] == second.headers["authorization"] == f"Bearer {minted}"


async def test_a_bearer_read_that_raises_surfaces_as_unavailable(recorder: Starlette) -> None:
    credentials = proxy_credentials(
        (Manifest(name="acme", version="0", proxy_credentials=ProxyCredentialSpec(_Unreadable)),)
    )
    assert credentials is not None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=recorder)) as client:
        apis = CloudApis(API_URL, lambda: client, proxy_bearer(credentials))
        with pytest.raises(CloudUnavailable) as raised:
            await apis.bound(await _workspace()).send("GET", SEARCH_PATH, answer=Echo)

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert recorder.state.sent == []


async def test_idempotency_key_rides_its_header(recorder: Starlette, apis: CloudApis) -> None:
    await apis.bound(await _workspace()).send(
        "POST", SEARCH_PATH, body=Probe(q="a"), idempotency_key="trigger:7", answer=Echo
    )

    (sent,) = recorder.state.sent
    assert sent.headers[IDEMPOTENCY_HEADER.lower()] == "trigger:7"


async def test_params_repeat_in_order(recorder: Starlette, apis: CloudApis) -> None:
    await apis.bound(await _workspace()).send(
        "GET",
        "/v1/memory/records",
        params=(("subject", "shared"), ("subject", "member:x")),
        answer=Echo,
    )

    (sent,) = recorder.state.sent
    assert sent.url == f"{API_URL}/v1/memory/records?subject=shared&subject=member%3Ax"
    assert "content-type" not in sent.headers
    assert sent.body == b""


async def test_a_full_body_sends_every_field(recorder: Starlette, apis: CloudApis) -> None:
    await apis.bound(await _workspace()).send("POST", SEARCH_PATH, body=Probe(q="a"), answer=Echo)

    (sent,) = recorder.state.sent
    assert json.loads(sent.body) == {"q": "a", "limit": None}


async def test_a_partial_body_sends_only_the_fields_its_caller_set(
    recorder: Starlette, apis: CloudApis
) -> None:
    await apis.bound(await _workspace()).send(
        "PATCH", "/v1/sources/connections/c1", body=Probe(q="a"), partial=True, answer=Echo
    )

    (sent,) = recorder.state.sent
    assert sent.body == b'{"q":"a"}'


async def _refusal(status: int, answer: bytes) -> CloudRefused:
    apis = cloud_apis_for(_recorder(status, answer))
    with pytest.raises(CloudRefused) as raised:
        await apis.bound(await _workspace()).send("GET", "/v1/sources/s1", answer=Echo)
    return raised.value


async def test_a_4xx_raises_refused_with_the_envelope() -> None:
    enveloped = await _refusal(
        400, b'{"error": {"code": "invalid_request", "message": "The name is not in the body."}}'
    )
    plain = await _refusal(404, b"Not Found")

    assert (enveloped.status, enveloped.code, enveloped.message) == (
        400,
        "invalid_request",
        "The name is not in the body.",
    )
    assert (plain.status, plain.code) == (404, "unknown")


async def test_a_5xx_a_timeout_and_a_refused_connection_raise_unavailable() -> None:
    def raising(error: Exception) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            raise error

        return httpx.MockTransport(handler)

    workspace_id = await _workspace()
    unavailable = cloud_apis_for(_recorder(503, b"{}"))
    with pytest.raises(CloudUnavailable, match="/v1/memory/search answered 503"):
        await unavailable.bound(workspace_id).send("GET", SEARCH_PATH, answer=Echo)
    for error, reason in (
        (httpx.ReadTimeout("slow"), "ReadTimeout"),
        (httpx.ConnectError("refused"), "ConnectError"),
    ):
        async with httpx.AsyncClient(transport=raising(error)) as client:
            apis = CloudApis(API_URL, lambda client=client: client, unavailable.bearer_for)
            with pytest.raises(CloudUnavailable) as raised:
                await apis.bound(workspace_id).send("GET", SEARCH_PATH, answer=Echo)
        assert raised.value.reason == reason


async def test_a_body_past_the_bound_is_refused_before_any_io(
    recorder: Starlette, apis: CloudApis
) -> None:
    with pytest.raises(ValueError, match=f"at most {CLOUD_BODY_MAX_BYTES} bytes"):
        await apis.bound(uuid4()).send(
            "POST", SEARCH_PATH, body=Probe(q="a" * CLOUD_BODY_MAX_BYTES), answer=Echo
        )

    assert recorder.state.sent == []


async def test_cloud_api_presents_the_bound_workspace(
    store: CredentialStore, recorder: Starlette, apis: CloudApis
) -> None:
    workspace_id = await _workspace()
    context = context_for("acme", frozenset(), cloud_client=True, cloud=apis)
    with ws(workspace_id):
        await store.put(workspace_id, SYSTEM_TOKEN_SLOT, "bearer-bound")
        await context.cloud_api().send("GET", SEARCH_PATH, answer=Echo)

    (sent,) = recorder.state.sent
    assert sent.headers["authorization"] == "Bearer bearer-bound"


def test_cloud_api_needs_the_manifest_flag(apis: CloudApis) -> None:
    undeclared = Manifest(name="acme", version="0")
    context = context_for("acme", frozenset(), cloud_client=undeclared.cloud_client, cloud=apis)

    with pytest.raises(PermissionError, match="'acme' does not declare cloud_client"):
        context.cloud_api()


def test_cloud_api_without_a_base_raises() -> None:
    context = context_for("acme", frozenset(), cloud_client=True, cloud=None)

    with pytest.raises(RuntimeError, match=r"\[cloud\] api_url is unset"):
        context.cloud_api()


@pytest.mark.parametrize("api_url", ["ftp://api.test", "https://api.test/v1", "https://"])
def test_api_url_is_a_scheme_and_a_host_alone(api_url: str) -> None:
    with pytest.raises(ValueError, match=r"\[cloud\] api_url must be"):
        CloudConfig(api_url=api_url)
    assert CloudConfig(api_url="https://api.test").api_url == "https://api.test"
    assert CloudConfig(api_url="http://api-router:8080").api_url == "http://api-router:8080"


def test_boot_fails_when_a_cloud_client_loads_without_api_url_or_proxy_credentials() -> None:
    base = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root="/tmp/blobs"),
    )
    configured = base.model_copy(update={"cloud": CloudConfig(api_url=API_URL)})
    client = Manifest(name="memoryish", version="0", cloud_client=True)

    with pytest.raises(RuntimeError, match=r"'memoryish' declares cloud_client, so \[cloud\]"):
        _require_cloud(base, (client, DECLARING))
    with pytest.raises(RuntimeError, match="'memoryish' declares cloud_client, so an extension"):
        _require_cloud(configured, (client,))
    _require_cloud(configured, (client, DECLARING))
    _require_cloud(base, (DECLARING,))


async def test_cloud_apis_for_serves_every_apps_routes() -> None:
    def answering(path: str, ok: bool) -> Starlette:
        async def answer(_: Request) -> Response:
            return JSONResponse({"ok": ok})

        return Starlette(routes=[Route(path, answer)])

    apis = cloud_apis_for(answering("/v1/memory/pages", True), answering("/v1/sources", False))
    bound = apis.bound(await _workspace())

    assert await bound.send("GET", "/v1/memory/pages", answer=Echo) == Echo(ok=True)
    assert await bound.send("GET", "/v1/sources", answer=Echo) == Echo(ok=False)
    assert apis.base_url == API_URL
    assert await apis.bearer_for(uuid4()) == TEST_BEARER


async def test_no_bearer_reaches_a_log_record(
    caplog: pytest.LogCaptureFixture, store: CredentialStore, apis: CloudApis
) -> None:
    workspace_id = await _workspace()
    caplog.set_level(logging.DEBUG)

    await apis.bound(workspace_id).send("POST", SEARCH_PATH, body=Probe(q="a"), answer=Echo)
    refusing = cloud_apis_for(_recorder(403, b'{"error": {"code": "forbidden", "message": "No."}}'))
    with pytest.raises(CloudRefused):
        await (
            CloudApis(API_URL, refusing.client, apis.bearer_for)
            .bound(workspace_id)
            .send("GET", SEARCH_PATH, answer=Echo)
        )

    minted = await _slot(store, workspace_id)
    assert caplog.records
    assert not [record for record in caplog.records if minted in record.getMessage()]
    assert not [record for record in caplog.records if minted in repr(record.__dict__)]


def test_one_client_serves_each_event_loop() -> None:
    clients = LoopClients()

    async def twice() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
        return clients(), clients()

    first, again = asyncio.run(twice())
    other, _ = asyncio.run(twice())

    assert first is again
    assert other is not first
