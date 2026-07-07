import base64
import json
from typing import Any

import httpx
import pytest

from ufo_control.contract import DeployRequest
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


def _platform() -> PlatformConfig:
    return PlatformConfig.model_validate(
        {
            "chart_path": "/charts/ufo-tenant",
            "registry": "ghcr.io/acme",
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


async def test_reconcile_surfaces_any_error_as_failed_status() -> None:
    reconciler = TenantReconciler(
        kube=_kube(httpx.MockTransport(lambda request: httpx.Response(500, json={"m": "boom"}))),
        platform=_platform(),
    )
    status = await reconciler.reconcile(_request())
    assert status.phase == "Failed"
    assert status.message
