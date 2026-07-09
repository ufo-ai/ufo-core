import json

import httpx
import pytest
from ufo.deploy import DeployRequest, DeployStatus

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


async def test_status_patch_omits_absent_url_and_carries_workspace_id() -> None:
    # A Failed status has no url; the CRD's status.url is a non-nullable string, so the applied
    # body must omit url rather than send `null` (which the apiserver rejects 422). The minted
    # workspaceId is persisted even on a Failed reconcile so the next pass reuses it (idempotency).
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = _client(httpx.MockTransport(handler))
    status = DeployStatus(tenant="acme", phase="Failed", message="boom")
    await client.patch_tenant_status("acme", status, "3f8c1e2a-0b4d-4c6e-9a1f-2b3c4d5e6f70")
    body = seen["body"]
    assert isinstance(body, dict)
    assert "url" not in body["status"]
    assert body["status"]["phase"] == "Failed"
    assert body["status"]["workspaceId"] == "3f8c1e2a-0b4d-4c6e-9a1f-2b3c4d5e6f70"
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


async def test_read_secret_returns_none_only_on_a_true_404() -> None:
    client = _client(httpx.MockTransport(lambda request: httpx.Response(404)))
    assert await client.read_secret("ufo-acme", "ufo-tenant") is None
    await client.close()


async def test_read_secret_raises_on_forbidden() -> None:
    # A 403 must fail the reconcile, never read as absence — absence mints fresh tenant keys.
    client = _client(httpx.MockTransport(lambda request: httpx.Response(403)))
    with pytest.raises(httpx.HTTPStatusError):
        await client.read_secret("ufo-acme", "ufo-tenant")
    await client.close()
