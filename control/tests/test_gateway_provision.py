"""Join-or-provision driving the real `KubeClient` over a mock apiserver: the assembled deploy
request is validated against the real `ufo.deploy.DeployRequest`, and the apply/list calls are
checked against a mock transport."""

import json
import tomllib
from typing import Any

import httpx
from ufo.config import BrowserConfig, SandboxConfig
from ufo.deploy import DeployRequest

from ufo_control.gateway_provision import (
    CONFIG_TOML,
    DeployTarget,
    JoinOrProvision,
    deploy_request,
    mint_tenant_name,
    slugify_domain,
)
from ufo_control.kube import KubeClient

BUNDLE = "ghcr.io/metalcraftai/ufo@sha256:" + "a" * 64
TARGET = DeployTarget(base_domain="flyingobject.ai", bundle_image=BUNDLE)


def _kube(handler: httpx.MockTransport) -> KubeClient:
    return KubeClient(http=httpx.AsyncClient(transport=handler, base_url="https://kube.test"))


def test_slugify_and_tenant_name() -> None:
    assert slugify_domain("acme.co.uk") == "acme-co-uk"
    name = mint_tenant_name("acme.com")
    assert name.startswith("acme-com-")
    assert len(name.rsplit("-", 1)[1]) == 8
    assert mint_tenant_name("acme.com") == name  # deterministic per domain


def test_deploy_request_is_wire_valid() -> None:
    request = deploy_request("acme-com-1a2b3c4d", "me@acme.com", TARGET)
    assert isinstance(request, DeployRequest)
    assert request.tenant.name == "acme-com-1a2b3c4d"
    assert request.tenant.host == "acme-com-1a2b3c4d.flyingobject.ai"
    assert request.tenant.owner_email == "me@acme.com"
    assert request.pack == "assistant_hosted"
    assert request.bundle_image.digest == "sha256:" + "a" * 64
    assert request.sandbox_image is None  # the hosted e2b carrier pulls no image


def test_hosted_config_selects_the_sandbox_chrome_cdp_provider() -> None:
    """The browser and sandbox sections the gateway ships parse as the real core config models and
    select the in-sandbox Chrome transport on the e2b carrier — the hosted tenant drives Chrome
    inside its own sandbox, not a static endpoint. (database/blob come from the deploy env, so only
    the sections this TOML carries are validated here.)"""
    parsed = tomllib.loads(CONFIG_TOML)
    assert BrowserConfig.model_validate(parsed["browser"]).cdp_provider == "sandbox_chrome"
    assert SandboxConfig.model_validate(parsed["sandbox"]).backend == "e2b"


async def test_tenants_for_domain_counts() -> None:
    items = [{"metadata": {"name": "acme-x"}, "status": {"phase": "Ready", "workspaceId": "ws-1"}}]
    kube = _kube(httpx.MockTransport(lambda request: httpx.Response(200, json={"items": items})))
    tenants = await kube_join(kube).tenants_for_domain("acme.com")
    assert len(tenants) == 1
    assert tenants[0].tenant == "acme-x"
    assert tenants[0].workspace_id == "ws-1"
    await kube.http.aclose()


def test_workspace_url_is_the_tenant_subdomain() -> None:
    kube = _kube(httpx.MockTransport(lambda r: httpx.Response(200)))
    join = JoinOrProvision(kube=kube, target=TARGET)
    assert join.workspace_url("acme-com-1a2b3c4d") == "https://acme-com-1a2b3c4d.flyingobject.ai"


async def test_provision_applies_a_wire_valid_tenant() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=seen["body"])

    kube = _kube(httpx.MockTransport(handler))
    name = await kube_join(kube).provision("acme.com", "me@acme.com")
    assert name == mint_tenant_name("acme.com")
    assert seen["method"] == "PATCH"
    assert seen["path"].endswith(f"/tenants/{name}")
    DeployRequest.model_validate(seen["body"]["spec"])
    await kube.http.aclose()


def kube_join(kube: KubeClient) -> JoinOrProvision:
    return JoinOrProvision(kube=kube, target=TARGET)
