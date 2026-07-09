import base64
import json
from typing import Any

import httpx
import pytest
from ufo.deploy import DeployRequest

from ufo_control.kube import KubeClient
from ufo_control.platform import PlatformConfig
from ufo_control.provision import TenantReconciler, _is_failed_first_install

DIGEST = "sha256:" + "f" * 64
PLATFORM_SECRET_DATA = {"ANTHROPIC_API_KEY": base64.b64encode(b"sk-test").decode()}
SOURCE_SECRET = {
    "kind": "Secret",
    "metadata": {"name": "ufo-platform-secrets", "namespace": "ufo-system"},
    "type": "Opaque",
    "data": PLATFORM_SECRET_DATA,
}


def _kube(handler: httpx.MockTransport) -> KubeClient:
    return KubeClient(http=httpx.AsyncClient(transport=handler, base_url="https://kube.test"))


def _platform(bundle_image: str = f"ghcr.io/acme/ufo@{DIGEST}") -> PlatformConfig:
    return PlatformConfig.model_validate(
        {
            "chart_path": "/charts/ufo-tenant",
            "registry": "ghcr.io/acme",
            "bundle_image": bundle_image,
            "tenant_postgres_host": "pg.svc:5432",
            "redis_url": "redis://redis.svc:6379",
            "blob_bucket": "acme-blobs",
        }
    )


def _request() -> DeployRequest:
    return DeployRequest.model_validate(
        {
            "tenant": {"name": "acme", "host": "acme.ufo.app", "owner_email": "you@acme.com"},
            "bundle_image": {"repository": "ghcr.io/acme/ufo", "digest": DIGEST},
            "sandbox_image": {"repository": "ghcr.io/acme/sandbox", "digest": DIGEST},
            "config_toml": "[pack]\nname='assistant'\n",
            "pack": "assistant",
        }
    )


@pytest.mark.parametrize(
    ("phase", "revision", "expected"),
    [
        ("failed", 1, True),
        ("pending-install", 1, True),
        ("unknown", 1, True),
        ("deployed", 1, False),
        ("failed", 2, False),
        ("pending-upgrade", 2, False),
        ("deployed", 3, False),
    ],
)
def test_only_a_failed_first_install_is_cleared(phase: str, revision: int, expected: bool) -> None:
    assert _is_failed_first_install(phase, revision) is expected


async def test_platform_secret_is_replicated_into_the_tenant_namespace() -> None:
    applied: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.path.endswith("/ufo-system/secrets/ufo-platform-secrets")
            return httpx.Response(200, json=SOURCE_SECRET)
        applied["path"] = request.url.path
        applied["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    reconciler = TenantReconciler(kube=_kube(httpx.MockTransport(handler)), platform=_platform())
    await reconciler._ensure_platform_secret(_request())

    assert applied["path"].endswith("/ufo-acme/secrets/ufo-platform-secrets")
    assert applied["body"]["data"] == PLATFORM_SECRET_DATA


async def test_missing_platform_secret_fails_loud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        raise AssertionError("must not apply a tenant Secret when the source is absent")

    reconciler = TenantReconciler(kube=_kube(httpx.MockTransport(handler)), platform=_platform())
    with pytest.raises(RuntimeError, match="absent"):
        await reconciler._ensure_platform_secret(_request())


def _advanced_tenant_obj(new_digest: str, **spec_overrides: object) -> dict[str, Any]:
    spec = _request().model_dump(mode="json", exclude_none=True)
    spec["bundle_image"]["digest"] = new_digest
    spec.update(spec_overrides)
    return {"metadata": {"name": "acme"}, "spec": spec}


async def test_reconcile_advances_a_stale_bundle_image_on_the_tenant() -> None:
    new_digest = "sha256:" + "0" * 64
    applied: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH" and request.url.path.endswith("/tenants/acme"):
            applied["params"] = dict(request.url.params)
            applied["spec"] = json.loads(request.content)["spec"]
            return httpx.Response(200, json={})
        if request.method == "GET" and request.url.path.endswith("/tenants/acme"):
            return httpx.Response(200, json=_advanced_tenant_obj(new_digest))
        return httpx.Response(500, json={})

    reconciler = TenantReconciler(
        kube=_kube(httpx.MockTransport(handler)),
        platform=_platform(bundle_image=f"ghcr.io/acme/ufo@{new_digest}"),
    )
    await reconciler.reconcile(_request(), "3f8c1e2a-0b4d-4c6e-9a1f-2b3c4d5e6f70")

    assert applied["spec"] == {
        "bundle_image": {"repository": "ghcr.io/acme/ufo", "digest": new_digest}
    }
    assert applied["params"]["fieldManager"] == "flyingobject.ai/bundle-advance"


async def test_the_advance_reconciles_onward_from_the_refetched_tenant() -> None:
    new_digest = "sha256:" + "0" * 64
    redeployed_config = "[pack]\nname='assistant-next'\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json=_advanced_tenant_obj(new_digest, config_toml=redeployed_config)
            )
        return httpx.Response(200, json={})

    reconciler = TenantReconciler(
        kube=_kube(httpx.MockTransport(handler)),
        platform=_platform(bundle_image=f"ghcr.io/acme/ufo@{new_digest}"),
    )
    advanced = await reconciler._advance_bundle_image(_request())
    assert advanced.bundle_image == reconciler.platform.bundle_image
    assert advanced.config_toml == redeployed_config


async def test_a_tenant_on_the_current_bundle_is_not_reapplied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not apply the tenant when its bundle image is current")

    reconciler = TenantReconciler(kube=_kube(httpx.MockTransport(handler)), platform=_platform())
    request = _request()
    assert await reconciler._advance_bundle_image(request) is request


async def test_minted_keys_are_reused_from_the_applied_tenant_secret() -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    token = "a" * 64
    existing = {
        "kind": "Secret",
        "metadata": {"name": "ufo-tenant", "namespace": "ufo-acme"},
        "type": "Opaque",
        "data": {
            "UFO_CREDENTIAL_KEY": base64.b64encode(key.encode()).decode(),
            "UFO_ARTIFACT_TOKEN_SECRET": base64.b64encode(token.encode()).decode(),
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/ufo-acme/secrets/ufo-tenant")
        return httpx.Response(200, json=existing)

    reconciler = TenantReconciler(kube=_kube(httpx.MockTransport(handler)), platform=_platform())
    minted = await reconciler._minted_keys("acme")
    assert minted.credential_key == key
    assert minted.artifact_token_secret == token


async def test_minted_keys_are_minted_only_when_the_tenant_secret_is_absent() -> None:
    reconciler = TenantReconciler(
        kube=_kube(httpx.MockTransport(lambda request: httpx.Response(404))),
        platform=_platform(),
    )
    first = await reconciler._minted_keys("acme")
    second = await reconciler._minted_keys("acme")
    assert first.credential_key and first.artifact_token_secret
    assert first.credential_key != second.credential_key


async def test_reconcile_surfaces_any_error_as_failed_status() -> None:
    reconciler = TenantReconciler(
        kube=_kube(httpx.MockTransport(lambda request: httpx.Response(500, json={"m": "boom"}))),
        platform=_platform(),
    )
    status = await reconciler.reconcile(_request(), "3f8c1e2a-0b4d-4c6e-9a1f-2b3c4d5e6f70")
    assert status.phase == "Failed"
    assert status.message
