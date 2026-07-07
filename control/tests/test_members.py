"""The join endpoint: `POST /v1/tenants/{name}/members` resolves the tenant's workspace uuid from
its CR status and inserts the member as the OWNER role, idempotently. The Postgres is spun for this
module on port 5545 (never the shared 5541), so the write hits a real database. The org-domain label
set at admission and the workspace_id surfaced on the status read are covered alongside as pure
checks."""

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import asyncpg
import httpx
import pytest
from fastapi import HTTPException
from ufo.deploy import DeployRequest, DeployStatus

from ufo_control.kube import KubeClient, org_domain, status_from_tenant, tenant_body
from ufo_control.members import OWNER_DSN_ENV, MemberRequest, add_member
from ufo_control.platform import ORG_DOMAIN_LABEL

CONTAINER = "ufo-members-test-pg"
PORT = 5545
OWNER_DSN = f"postgresql://ufo:ufo@127.0.0.1:{PORT}/ufo"
IMAGE = "pgvector/pgvector:pg17"
WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"
DIGEST = "sha256:" + "c" * 64

SCHEMA = (
    "create table if not exists workspace (id uuid primary key)",
    "create table if not exists member ("
    "  id uuid primary key,"
    "  workspace_id uuid not null references workspace(id) on delete cascade,"
    "  email text not null,"
    "  created_at timestamptz not null,"
    "  updated_at timestamptz not null,"
    "  unique (workspace_id, email))",
)


async def _prepare_schema() -> None:
    connection = await asyncpg.connect(OWNER_DSN)
    try:
        for statement in SCHEMA:
            await connection.execute(statement)
        await connection.execute(
            "insert into workspace (id) values ($1) on conflict do nothing", uuid.UUID(WORKSPACE_ID)
        )
    finally:
        await connection.close()


async def _member_emails() -> list[str]:
    connection = await asyncpg.connect(OWNER_DSN)
    try:
        rows = await connection.fetch(
            "select email from member where workspace_id = $1", uuid.UUID(WORKSPACE_ID)
        )
    finally:
        await connection.close()
    return [row["email"] for row in rows]


@pytest.fixture(scope="module")
def members_postgres(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker is required to spin the members-endpoint Postgres")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_USER=ufo",
            "-e",
            "POSTGRES_PASSWORD=ufo",
            "-e",
            "POSTGRES_DB=ufo",
            "-p",
            f"127.0.0.1:{PORT}:5432",
            IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start Postgres container: {started.stderr.strip()}")
    try:
        _await_ready()
        asyncio.run(_prepare_schema())
        monkeypatch_module.setenv(OWNER_DSN_ENV, OWNER_DSN)
        yield OWNER_DSN
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)


def _await_ready(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            asyncio.run(_ping())
            return
        except Exception as error:
            last = error
            time.sleep(1.0)
    raise RuntimeError(f"Postgres on {PORT} never became ready: {last}")


async def _ping() -> None:
    connection = await asyncpg.connect(OWNER_DSN)
    await connection.close()


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()


def _kube(handler: httpx.MockTransport) -> KubeClient:
    return KubeClient(http=httpx.AsyncClient(transport=handler, base_url="https://kube.test"))


def _tenant_response(status: dict[str, object] | None) -> httpx.MockTransport:
    body: dict[str, object] = {"metadata": {"name": "acme-x"}}
    if status is not None:
        body["status"] = status
    return httpx.MockTransport(lambda request: httpx.Response(200, json=body))


async def test_add_member_inserts_the_row_idempotently(members_postgres: str) -> None:
    kube = _kube(_tenant_response({"phase": "Ready", "workspaceId": WORKSPACE_ID}))
    result = await add_member(kube, "acme-x", MemberRequest(email="Me@Acme.com"))
    assert result.workspace_id == WORKSPACE_ID
    assert result.email == "me@acme.com"
    assert await _member_emails() == ["me@acme.com"]
    again = await add_member(kube, "acme-x", MemberRequest(email="me@acme.com"))
    assert again.workspace_id == WORKSPACE_ID
    assert await _member_emails() == ["me@acme.com"]
    await kube.http.aclose()


async def test_add_member_409_when_no_workspace_yet(members_postgres: str) -> None:
    kube = _kube(_tenant_response({"phase": "Provisioning"}))
    with pytest.raises(HTTPException) as raised:
        await add_member(kube, "acme-x", MemberRequest(email="new@acme.com"))
    assert raised.value.status_code == 409
    await kube.http.aclose()


def test_org_domain_label_is_set_at_admission() -> None:
    request = DeployRequest.model_validate(
        {
            "tenant": {
                "name": "acme",
                "host": "acme.flyingobject.ai",
                "owner_email": "you@Acme.com",
            },
            "bundle_image": {"repository": "r/b", "digest": DIGEST},
            "sandbox_image": {"repository": "r/s", "digest": DIGEST},
            "config_toml": "[pack]\nname='assistant_hosted'\n",
            "pack": "assistant_hosted",
        }
    )
    labels = tenant_body(request)["metadata"]["labels"]
    assert labels[ORG_DOMAIN_LABEL] == "acme.com"
    assert org_domain("You@Acme.COM") == "acme.com"


def test_status_from_tenant_surfaces_the_workspace_id() -> None:
    obj = {"metadata": {"name": "acme-x"}, "status": {"phase": "Ready", "workspaceId": "ws-7"}}
    status = status_from_tenant(obj)
    assert status == DeployStatus(tenant="acme-x", phase="Ready", workspace_id="ws-7")
    pending = status_from_tenant({"metadata": {"name": "acme-x"}})
    assert pending.phase == "Pending"


async def test_list_tenants_filters_by_org_domain() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"items": []})

    kube = _kube(httpx.MockTransport(handler))
    await kube.list_tenants(org_domain="acme.com")
    assert f"{ORG_DOMAIN_LABEL}=acme.com" in seen["query"]
    await kube.http.aclose()
