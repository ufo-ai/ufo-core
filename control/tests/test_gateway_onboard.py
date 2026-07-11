"""The onboarding flow end to end: `GET /ufo` stamping, and `Onboarding.advance` driving email →
code → verify → (provision across polls | join) → a signed-in bearer, over the real `onboard_claim`
table and member write (Postgres) and a mock apiserver."""

import json
import re
from typing import Any

import httpx
import pytest

from ufo_control.gateway import (
    BASE_DOMAIN_ENV,
    SHARED_TIER,
    STAMPED_SCRIPT,
    WORKSPACE_BASE_URL_ENV,
    Onboarding,
    _resolver,
    _stamp_script,
)
from ufo_control.gateway_claim import ClaimWorkflow
from ufo_control.gateway_email import LoggingEmailSender, WorkEmailPolicy
from ufo_control.gateway_invite import CODE_ALPHABET, InviteCodes, hash_invite, mint_code
from ufo_control.gateway_provision import (
    DeployTarget,
    JoinOrProvision,
    TenantJoin,
    TenantNotReady,
    TooManyTenantsForDomain,
)
from ufo_control.gateway_shared import SharedWorkspaces
from ufo_control.gateway_store import OnboardStore
from ufo_control.gateway_token import verify_token
from ufo_control.kube import KubeClient
from ufo_control.platform import ORG_DOMAIN_LABEL

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


def test_shared_resolver_uses_the_workspace_serve_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared tier signs a member in against the serve host their `ufo` surface talks to
    (`app.<apex>`), not the onboarding apex — separate hosts, so the `workspace` directive must
    carry the serve host or the member's turns 404 on the gateway."""
    monkeypatch.setenv(WORKSPACE_BASE_URL_ENV, "https://app.testing.flyingobject.ai")
    monkeypatch.setenv(BASE_DOMAIN_ENV, "testing.flyingobject.ai")
    resolver = _resolver(SHARED_TIER, {"kube": FakeCluster([], []).kube()})
    assert isinstance(resolver, SharedWorkspaces)
    assert resolver.workspace_url == "https://app.testing.flyingobject.ai"
    assert resolver.joins.base_domain == "testing.flyingobject.ai"


def test_shared_resolver_fails_loud_without_the_workspace_serve_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WORKSPACE_BASE_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match=WORKSPACE_BASE_URL_ENV):
        _resolver(SHARED_TIER, {})


def test_shared_resolver_fails_loud_without_the_base_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WORKSPACE_BASE_URL_ENV, "https://app.testing.flyingobject.ai")
    monkeypatch.delenv(BASE_DOMAIN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=BASE_DOMAIN_ENV):
        _resolver(SHARED_TIER, {"kube": FakeCluster([], []).kube()})


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
            selector = request.url.params.get("labelSelector")
            items = self.list_items
            if selector:
                key, _, value = selector.partition("=")
                items = [
                    item
                    for item in items
                    if item.get("metadata", {}).get("labels", {}).get(key) == value
                ]
            return httpx.Response(200, json={"items": items})
        name = path.rsplit("/", 1)[1]
        status = self.status_sequence[min(self.status_index, len(self.status_sequence) - 1)]
        self.status_index += 1
        return httpx.Response(200, json={"metadata": {"name": name}, "status": status})


def _tenant_item(name: str, status: dict[str, Any], domain: str) -> dict[str, Any]:
    return {
        "metadata": {"name": name, "labels": {ORG_DOMAIN_LABEL: domain}},
        "status": status,
    }


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
        resolver=JoinOrProvision(kube=kube, target=TARGET),
        invites=InviteCodes(pool=store.pool),
        token_secret=SECRET,
        apex_host="flyingobject.ai",
    )


def test_minted_codes_are_grouped_and_hash_ignores_case_and_whitespace() -> None:
    code = mint_code()
    groups = code.split("-")
    assert len(groups) == 3
    assert all(len(group) == 4 and set(group) <= set(CODE_ALPHABET) for group in groups)
    assert hash_invite(f"  {code.upper()} ") == hash_invite(code)


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
    gated = await flow.advance("ufo", "s", code, b"")
    assert "invite" in (_verb(gated, "ask") or "")
    assert cluster.applied == []  # nothing provisions until an invite opens the gate
    invite = await InviteCodes(pool=store.pool).mint()
    provisioning = await flow.advance("ufo", "s", invite, b"")
    assert _verb(provisioning, "poll") == "2"
    assert len(cluster.applied) == 1
    claim = await store.live_claim("ufo", "s")
    assert claim is not None and claim.invite_id is not None  # burned and stamped as one
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
        list_items=[_tenant_item("acme-x", ready, "acme.com")],
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


async def test_shared_flow_joins_the_domains_existing_tenant(store: OnboardStore) -> None:
    """A member of an org that already runs a dedicated tenant never lands on a parallel shared
    row: the shared tier joins the tenant — codeless, like any join of an existing workspace — and
    the sign-in carries the tenant's workspace uuid and the tenant's own reconciled URL, not
    `app.<apex>` and not a hostname recomputed from the base domain. Another org's tenant in the
    same cluster is invisible to the lookup — the org-domain label scopes it."""
    sender = LoggingEmailSender()
    ready = {"phase": "Ready", "workspaceId": WORKSPACE_ID, "url": "https://acme-x.custom.example"}
    other = {"phase": "Ready", "workspaceId": "22222222-2222-2222-2222-222222222222"}
    cluster = FakeCluster(
        list_items=[
            _tenant_item("acme-x", ready, "acme.com"),
            _tenant_item("other-z", other, "other.org"),
        ],
        status_sequence=[ready],
    )
    kube = cluster.kube()
    flow = Onboarding(
        claims=ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender),
        store=store,
        resolver=SharedWorkspaces(
            workspace_url="https://app.flyingobject.ai",
            joins=TenantJoin(kube=kube, base_domain="flyingobject.ai"),
        ),
        invites=InviteCodes(pool=store.pool),
        token_secret=SECRET,
        apex_host="flyingobject.ai",
    )
    await flow.advance("ufo", "s", "", b"")
    await flow.advance("ufo", "s", "me@acme.com", b"")
    signed_in = await flow.advance("ufo", "s", sender.last_code("me@acme.com"), b"")
    await kube.http.aclose()
    assert cluster.applied == []
    token = _verb(signed_in, "token")
    assert token is not None
    assert verify_token(token, SECRET)["ws"] == WORKSPACE_ID
    assert _verb(signed_in, "workspace") == "https://acme-x.custom.example"
    assert await _member_emails(store) == ["me@acme.com"]


async def test_join_refuses_an_ambiguous_domain() -> None:
    ready = {"phase": "Ready", "workspaceId": WORKSPACE_ID}
    cluster = FakeCluster(
        list_items=[
            _tenant_item("acme-x", ready, "acme.com"),
            _tenant_item("acme-y", ready, "acme.com"),
        ],
        status_sequence=[ready],
    )
    kube = cluster.kube()
    joins = TenantJoin(kube=kube, base_domain="flyingobject.ai")
    with pytest.raises(TooManyTenantsForDomain):
        await joins.join_existing("acme.com", "me@acme.com")
    await kube.http.aclose()


async def test_join_refuses_a_tenant_that_is_not_serving() -> None:
    """A Provisioning or Failed tenant may already carry a persisted workspaceId; joining it would
    sign the member into a deploy that cannot answer them, so the join refuses instead."""
    provisioning = {"phase": "Provisioning", "workspaceId": WORKSPACE_ID}
    cluster = FakeCluster(
        list_items=[_tenant_item("acme-x", provisioning, "acme.com")],
        status_sequence=[provisioning],
    )
    kube = cluster.kube()
    joins = TenantJoin(kube=kube, base_domain="flyingobject.ai")
    with pytest.raises(TenantNotReady):
        await joins.join_existing("acme.com", "me@acme.com")
    await kube.http.aclose()


async def test_tenant_listing_failure_refuses_rather_than_forking() -> None:
    """A 403 from the apiserver means 'could not look', never 'no tenant': answering an empty list
    would send the flow down the row path and fork an org whose tenant merely couldn't be seen."""
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={}))
    kube = KubeClient(http=httpx.AsyncClient(transport=transport, base_url="https://kube.test"))
    joins = TenantJoin(kube=kube, base_domain="flyingobject.ai")
    with pytest.raises(httpx.HTTPStatusError):
        await joins.join_existing("acme.com", "me@acme.com")
    await kube.http.aclose()


async def test_invalid_invite_reasks_with_the_waitlist_hint(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
    cluster = FakeCluster(list_items=[], status_sequence=[{"phase": "Provisioning"}])
    kube = cluster.kube()
    flow = _flow(store, sender, kube)
    await flow.advance("ufo", "s", "", b"")
    await flow.advance("ufo", "s", "me@acme.com", b"")
    await flow.advance("ufo", "s", sender.last_code("me@acme.com"), b"")
    rejected = await flow.advance("ufo", "s", "not-a-real-code", b"")
    await kube.http.aclose()
    assert cluster.applied == []
    assert "isn't valid or was already used" in rejected.decode()
    assert "curl https://flyingobject.ai/waitlist" in rejected.decode()
    assert "invite" in (_verb(rejected, "ask") or "")


async def test_a_burned_invite_cannot_open_a_second_workspace(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
    invite = await InviteCodes(pool=store.pool).mint()
    first_cluster = FakeCluster(
        list_items=[], status_sequence=[{"phase": "Ready", "workspaceId": "ws-first"}]
    )
    first_kube = first_cluster.kube()
    first = _flow(store, sender, first_kube)
    await first.advance("ufo", "a", "", b"")
    await first.advance("ufo", "a", "one@acme.com", b"")
    await first.advance("ufo", "a", sender.last_code("one@acme.com"), b"")
    await first.advance("ufo", "a", invite, b"")
    signed_in = await first.advance("ufo", "a", "", b"")
    await first_kube.http.aclose()
    assert _verb(signed_in, "token") is not None
    second_cluster = FakeCluster(list_items=[], status_sequence=[{"phase": "Provisioning"}])
    second_kube = second_cluster.kube()
    second = _flow(store, sender, second_kube)
    await second.advance("ufo", "b", "", b"")
    await second.advance("ufo", "b", "two@othercorp.com", b"")
    await second.advance("ufo", "b", sender.last_code("two@othercorp.com"), b"")
    rejected = await second.advance("ufo", "b", invite, b"")
    await second_kube.http.aclose()
    assert second_cluster.applied == []
    assert "isn't valid or was already used" in rejected.decode()


async def _member_emails(store: OnboardStore) -> list[str]:
    async with store.pool.acquire() as connection:
        rows = await connection.fetch("select email from member order by email")
    return [row["email"] for row in rows]
