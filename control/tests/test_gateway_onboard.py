"""The onboarding flow end to end: `GET /ufo` stamping, and `Onboarding.advance` driving email →
code → verify → (provision across polls | join) → a signed-in bearer, over the real `onboard_claim`
table and member write (Postgres) and a mock apiserver."""

import json
import re
from typing import Any

import httpx
import pytest

from ufo_control.gateway import STAMPED_SCRIPT, Onboarding, _stamp_script
from ufo_control.gateway_claim import ClaimWorkflow
from ufo_control.gateway_email import LoggingEmailSender, WorkEmailPolicy
from ufo_control.gateway_provision import DeployTarget, JoinOrProvision
from ufo_control.gateway_store import OnboardStore
from ufo_control.gateway_token import verify_token
from ufo_control.kube import KubeClient

BUNDLE = "ghcr.io/metalcraftai/ufo@sha256:" + "a" * 64
TARGET = DeployTarget(base_domain="flyingobject.ai", bundle_image=BUNDLE)
SECRET = "s3cret"
WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"

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


class FakeCluster:
    """A mock apiserver driving the real `KubeClient`: `list_items` is what GET tenants returns;
    `status_sequence` is walked by successive GET-tenant reads so the poll loop is exercised."""

    def __init__(
        self, list_items: list[dict[str, Any]], status_sequence: list[dict[str, Any]]
    ) -> None:
        self.list_items = list_items
        self.status_sequence = status_sequence
        self.applied: list[dict[str, Any]] = []
        self.status_index = 0

    def kube(self) -> KubeClient:
        return KubeClient(
            http=httpx.AsyncClient(
                transport=httpx.MockTransport(self._handle), base_url="https://kube.test"
            )
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "PATCH":
            body = json.loads(request.content)
            self.applied.append(body)
            return httpx.Response(200, json=body)
        if path.endswith("/tenants"):
            return httpx.Response(200, json={"items": self.list_items})
        name = path.rsplit("/", 1)[1]
        status = self.status_sequence[min(self.status_index, len(self.status_sequence) - 1)]
        self.status_index += 1
        return httpx.Response(200, json={"metadata": {"name": name}, "status": status})


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


def _flow(store: OnboardStore, sender: LoggingEmailSender, kube: KubeClient) -> Onboarding:
    return Onboarding(
        claims=ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender),
        store=store,
        join=JoinOrProvision(kube=kube, target=TARGET),
        token_secret=SECRET,
        base_domain="flyingobject.ai",
    )


async def test_provision_flow_signs_in_after_polling_to_ready(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
    cluster = FakeCluster(
        list_items=[],
        status_sequence=[
            {"phase": "Provisioning"},
            {"phase": "Ready", "workspaceId": "ws-prov"},
        ],
    )
    kube = cluster.kube()
    flow = _flow(store, sender, kube)
    opening = await flow.advance("ufo", "s", "", b"")
    assert "enter your work email" in (_verb(opening, "ask") or "")
    await flow.advance("ufo", "s", "me@acme.com", b"")
    code = sender.last_code("me@acme.com")
    provisioning = await flow.advance("ufo", "s", code, b"")
    assert _verb(provisioning, "poll") == "2"
    assert len(cluster.applied) == 1
    assert _verb(await flow.advance("ufo", "s", "", b""), "poll") == "2"
    signed_in = await flow.advance("ufo", "s", "", b"")
    await kube.http.aclose()
    token = _verb(signed_in, "token")
    assert token is not None
    assert verify_token(token, SECRET)["ws"] == "ws-prov"
    name = cluster.applied[0]["spec"]["tenant"]["name"]
    assert _verb(signed_in, "workspace") == f"https://{name}.flyingobject.ai"


async def test_join_flow_adds_a_member_and_signs_in(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
    ready = {"phase": "Ready", "workspaceId": WORKSPACE_ID}
    cluster = FakeCluster(
        list_items=[{"metadata": {"name": "acme-x"}, "status": ready}],
        status_sequence=[ready],
    )
    kube = cluster.kube()
    flow = _flow(store, sender, kube)
    await flow.advance("ufo", "s", "", b"")
    await flow.advance("ufo", "s", "me@acme.com", b"")
    code = sender.last_code("me@acme.com")
    signed_in = await flow.advance("ufo", "s", code, b"")
    await kube.http.aclose()
    assert cluster.applied == []  # join never provisions
    token = _verb(signed_in, "token")
    assert token is not None
    assert verify_token(token, SECRET)["ws"] == WORKSPACE_ID
    assert _verb(signed_in, "workspace") == "https://acme-x.flyingobject.ai"
    assert await _member_emails(store) == ["me@acme.com"]


async def _member_emails(store: OnboardStore) -> list[str]:
    async with store.pool.acquire() as connection:
        rows = await connection.fetch("select email from member order by email")
    return [row["email"] for row in rows]
