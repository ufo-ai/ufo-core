"""The onboarding state machine end to end: `GET /ufo` stamping, and `Onboarding.advance` driving
email → code → verify → (provision across polls | join) → a signed-in bearer, over the real store
(SQLite + Postgres) and a mock control plane."""

import json
import re
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from ufo_ext_gateway.claim import ClaimWorkflow
from ufo_ext_gateway.email_domain import WorkEmailPolicy
from ufo_ext_gateway.email_sender import LoggingEmailSender
from ufo_ext_gateway.onboard import STAMPED_SCRIPT, Onboarding, _stamp_script
from ufo_ext_gateway.provision import DeployTarget, JoinOrProvision
from ufo_ext_gateway.store import OnboardStore
from ufo_ext_gateway.token import verify_token

from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.schema import tables

BUNDLE = "ghcr.io/metalcraftai/ufo@sha256:" + "a" * 64
SANDBOX = "ghcr.io/metalcraftai/ufo-sandbox@sha256:" + "b" * 64
TARGET = DeployTarget(base_domain="flyingobject.ai", bundle_image=BUNDLE, sandbox_image=SANDBOX)
SECRET = "s3cret"


RAW_SCRIPT = 'UFO_SCRIPT_VERSION=dev\nUFO_URL="${UFO_URL:-https://flyingobject.ai}"\n'


def test_stamp_substitutes_version_and_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_PUBLIC_BASE_URL", "https://testing.flyingobject.ai")
    stamped = _stamp_script(RAW_SCRIPT)
    assert "UFO_SCRIPT_VERSION=dev" not in stamped
    assert re.search(r"UFO_SCRIPT_VERSION=[0-9a-f]{12}", stamped) is not None
    assert 'UFO_URL="${UFO_URL:-https://testing.flyingobject.ai}"' in stamped


def test_shipped_script_is_stamped_and_carries_the_adapted_chat_target() -> None:
    assert "UFO_SCRIPT_VERSION=dev" not in STAMPED_SCRIPT
    assert re.search(r"UFO_SCRIPT_VERSION=[0-9a-f]{12}", STAMPED_SCRIPT) is not None
    assert "surface/ufo/" in STAMPED_SCRIPT


def _lines(body: bytes) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for raw in body.decode().splitlines():
        verb, _, rest = raw.partition("\t")
        parsed.append((verb, rest))
    return parsed


def _verb(body: bytes, verb: str) -> str | None:
    for name, rest in _lines(body):
        if name == verb:
            return rest
    return None


class FakeControl:
    """A mock control plane: `tenants` is what GET /v1/tenants returns; the tenant status walks
    Provisioning → Ready so the poll loop is exercised across two calls."""

    def __init__(self, tenants: list[dict[str, object]]) -> None:
        self.tenants = tenants
        self.deployed: list[dict[str, object]] = []
        self.joined: list[dict[str, object]] = []
        self.status_calls = 0

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), base_url="http://c")

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/tenants" and request.method == "GET":
            return httpx.Response(200, json=self.tenants)
        if path == "/v1/deploy":
            body = json.loads(request.content)
            self.deployed.append(body)
            return httpx.Response(200, json={"tenant": body["tenant"]["name"], "phase": "Pending"})
        if path.endswith("/members"):
            body = json.loads(request.content)
            self.joined.append({"path": path, **body})
            return httpx.Response(200, json={"workspace_id": "ws-join", "email": body["email"]})
        if path.startswith("/v1/tenants/"):
            self.status_calls += 1
            name = path.rsplit("/", 1)[1]
            if self.status_calls >= 2:
                return httpx.Response(
                    200, json={"tenant": name, "phase": "Ready", "workspace_id": "ws-prov"}
                )
            return httpx.Response(200, json={"tenant": name, "phase": "Provisioning"})
        return httpx.Response(404)


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _flow(store: OnboardStore, sender: LoggingEmailSender, http: httpx.AsyncClient) -> Onboarding:
    return Onboarding(
        claims=ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender),
        store=store,
        join=JoinOrProvision(http=http, target=TARGET),
        token_secret=SECRET,
        base_domain="flyingobject.ai",
    )


@pytest.mark.usefixtures("db")
async def test_provision_flow_signs_in_after_polling_to_ready() -> None:
    store = OnboardStore(context_for(await _workspace(), "gateway", frozenset(), None))
    sender = LoggingEmailSender()
    control = FakeControl(tenants=[])
    async with control.client() as http:
        flow = _flow(store, sender, http)
        opening = await flow.advance("ufo", "s", "", b"")
        assert "enter your work email" in (_verb(opening, "ask") or "")
        await flow.advance("ufo", "s", "me@acme.com", b"")
        code = sender.last_code("me@acme.com")
        provisioning = await flow.advance("ufo", "s", code, b"")
        assert _verb(provisioning, "poll") == "2"
        assert len(control.deployed) == 1
        assert _verb(await flow.advance("ufo", "s", "", b""), "poll") == "2"
        signed_in = await flow.advance("ufo", "s", "", b"")
    token = _verb(signed_in, "token")
    assert token is not None
    assert verify_token(token, SECRET)["ws"] == "ws-prov"
    name = control.deployed[0]["tenant"]["name"]
    assert _verb(signed_in, "workspace") == f"https://{name}.flyingobject.ai"


@pytest.mark.usefixtures("db")
async def test_join_flow_adds_a_member_and_signs_in() -> None:
    store = OnboardStore(context_for(await _workspace(), "gateway", frozenset(), None))
    sender = LoggingEmailSender()
    control = FakeControl(tenants=[{"tenant": "acme-x", "phase": "Ready", "workspace_id": "ws-1"}])
    async with control.client() as http:
        flow = _flow(store, sender, http)
        await flow.advance("ufo", "s", "", b"")
        await flow.advance("ufo", "s", "me@acme.com", b"")
        code = sender.last_code("me@acme.com")
        signed_in = await flow.advance("ufo", "s", code, b"")
    assert control.joined == [{"path": "/v1/tenants/acme-x/members", "email": "me@acme.com"}]
    token = _verb(signed_in, "token")
    assert token is not None
    assert verify_token(token, SECRET)["ws"] == "ws-join"
    assert _verb(signed_in, "workspace") == "https://acme-x.flyingobject.ai"
