"""Join-or-provision over the control-plane API: the assembled deploy request is validated against
the real `ufo.deploy.DeployRequest` (tests are scaffold, so the internal import is allowed), and the
HTTP calls are driven against a mock control plane."""

import json

import httpx
from ufo_ext_gateway.provision import (
    DeployTarget,
    JoinOrProvision,
    deploy_request,
    mint_tenant_name,
    slugify_domain,
)

from ufo.deploy import DeployRequest

BUNDLE = "ghcr.io/metalcraftai/ufo@sha256:" + "a" * 64
SANDBOX = "ghcr.io/metalcraftai/ufo-sandbox@sha256:" + "b" * 64
TARGET = DeployTarget(base_domain="flyingobject.ai", bundle_image=BUNDLE, sandbox_image=SANDBOX)


def test_slugify_and_tenant_name() -> None:
    assert slugify_domain("acme.co.uk") == "acme-co-uk"
    name = mint_tenant_name("acme.com")
    assert name.startswith("acme-com-")
    assert len(name.rsplit("-", 1)[1]) == 8


def test_deploy_request_is_wire_valid() -> None:
    request = deploy_request("acme-com-1a2b3c4d", "me@acme.com", TARGET)
    validated = DeployRequest.model_validate(request)
    assert validated.tenant.name == "acme-com-1a2b3c4d"
    assert validated.tenant.host == "acme-com-1a2b3c4d.flyingobject.ai"
    assert validated.tenant.owner_email == "me@acme.com"
    assert validated.pack == "assistant_hosted"
    assert validated.bundle_image.digest == "sha256:" + "a" * 64


def _join(handler: httpx.MockTransport) -> JoinOrProvision:
    return JoinOrProvision(
        http=httpx.AsyncClient(transport=handler, base_url="http://control.test"), target=TARGET
    )


async def test_tenants_for_domain_counts() -> None:
    body = [{"tenant": "acme-x", "phase": "Ready", "workspace_id": "ws-1"}]
    join = _join(httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    tenants = await join.tenants_for_domain("acme.com")
    assert len(tenants) == 1
    assert tenants[0].name == "acme-x"
    assert tenants[0].workspace_id == "ws-1"
    await join.http.aclose()


async def test_provision_posts_a_wire_valid_deploy_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"tenant": "acme-com-x", "phase": "Pending"})

    join = _join(httpx.MockTransport(handler))
    name = await join.provision("acme.com", "me@acme.com")
    assert name == mint_tenant_name("acme.com")
    assert seen["path"] == "/v1/deploy"
    DeployRequest.model_validate(seen["body"])
    await join.http.aclose()


async def test_join_posts_the_member_and_returns_the_workspace() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"workspace_id": "ws-9", "email": "me@acme.com"})

    join = _join(httpx.MockTransport(handler))
    workspace_id = await join.join("acme-x", "me@acme.com")
    assert workspace_id == "ws-9"
    assert seen["path"] == "/v1/tenants/acme-x/members"
    assert seen["body"] == {"email": "me@acme.com"}
    await join.http.aclose()
