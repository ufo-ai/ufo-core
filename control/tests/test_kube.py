import json

import httpx
import pytest

from ufo_control.contract import DeployRequest, DeployStatus
from ufo_control.kube import APPLY_CONTENT_TYPE, KubeClient

DIGEST = "sha256:" + "c" * 64


def _client(handler: httpx.MockTransport) -> KubeClient:
    return KubeClient(http=httpx.AsyncClient(transport=handler, base_url="https://kube.test"))


def _request() -> DeployRequest:
    return DeployRequest.model_validate(
        {
            "tenant": {"name": "acme", "host": "acme.ufo.app", "owner_email": "you@acme.com"},
            "bundle_image": {"repository": "r/b", "digest": DIGEST},
            "sandbox_image": {"repository": "r/s", "digest": DIGEST},
            "config_toml": "[pack]\nname='assistant'\n",
            "pack": "assistant",
        }
    )


async def test_apply_tenant_is_a_forced_server_side_apply() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content_type"] = request.headers["content-type"]
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = _client(httpx.MockTransport(handler))
    await client.apply_tenant(_request())

    assert seen["method"] == "PATCH"
    assert seen["content_type"] == APPLY_CONTENT_TYPE
    assert seen["params"] == {"fieldManager": "flyingobject.ai/operator", "force": "true"}
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["kind"] == "Tenant"
    assert body["spec"]["tenant"]["name"] == "acme"
    await client.close()


async def test_status_patch_omits_absent_url() -> None:
    # A Failed status has no url; the CRD's status.url is a non-nullable string, so the applied
    # body must omit url rather than send `null` (which the apiserver rejects 422).
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = _client(httpx.MockTransport(handler))
    status = DeployStatus(tenant="acme", phase="Failed", message="boom")
    await client.patch_tenant_status("acme", status)
    body = seen["body"]
    assert isinstance(body, dict)
    assert "url" not in body["status"]
    assert body["status"]["phase"] == "Failed"
    await client.close()


async def test_get_returns_none_on_absent() -> None:
    client = _client(httpx.MockTransport(lambda request: httpx.Response(404)))
    assert await client.get_tenant("nope") is None
    await client.close()


async def test_list_tenants_unwraps_items() -> None:
    items = [{"metadata": {"name": "a"}}, {"metadata": {"name": "b"}}]
    client = _client(
        httpx.MockTransport(lambda request: httpx.Response(200, json={"items": items}))
    )
    assert len(await client.list_tenants()) == 2
    await client.close()


@pytest.mark.parametrize("status", [403, 404])
async def test_list_tenants_empty_when_crd_absent(status: int) -> None:
    client = _client(httpx.MockTransport(lambda request: httpx.Response(status)))
    assert await client.list_tenants() == []
    await client.close()
