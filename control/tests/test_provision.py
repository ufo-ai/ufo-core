import base64
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from ufo.deploy import DeployRequest

from ufo_control.kube import KubeClient
from ufo_control.platform import PlatformConfig
from ufo_control.provision import (
    Helm,
    HelmResult,
    TenantReconciler,
    _canonical,
    _is_failed_first_install,
)
from ufo_control.render import TenantChartValues

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


# The Helm CLI seam: a fake `run` that records the argv the reconciler would shell and returns
# scripted results, so drift/heal decisions are asserted on the commands — never against real helm.
@dataclass
class FakeHelm:
    status_result: HelmResult
    values_result: HelmResult = field(default_factory=lambda: HelmResult(0, b"{}", b""))
    calls: list[list[str]] = field(default_factory=list)

    async def run(self, *args: str) -> HelmResult:
        self.calls.append(list(args))
        if args[0] == "status":
            return self.status_result
        if args[0] == "get":
            return self.values_result
        return HelmResult(returncode=0, stdout=b"", stderr=b"")

    @property
    def ran_upgrade(self) -> bool:
        return any(call[:2] == ["upgrade", "--install"] for call in self.calls)

    @property
    def ran_uninstall(self) -> bool:
        return any(call[0] == "uninstall" for call in self.calls)

    @property
    def read_values(self) -> bool:
        return any(call[:2] == ["get", "values"] for call in self.calls)


def _status_result(phase: str, revision: int) -> HelmResult:
    payload = json.dumps({"info": {"status": phase}, "version": revision})
    return HelmResult(returncode=0, stdout=payload.encode(), stderr=b"")


def _absent_release() -> HelmResult:
    return HelmResult(returncode=1, stdout=b"", stderr=b"Error: release: not found")


def _values_result(values: dict[str, Any]) -> HelmResult:
    return HelmResult(returncode=0, stdout=json.dumps(values).encode(), stderr=b"")


def _chart_values(**overrides: Any) -> TenantChartValues:
    base: dict[str, Any] = {
        "namespace": "ufo-acme",
        "tenant_name": "acme",
        "pack": "assistant",
        "host": "acme.ufo.app",
        "owner_email": "you@acme.com",
        "bundle_image": f"ghcr.io/acme/ufo@{DIGEST}",
        "sandbox_image": f"ghcr.io/acme/sandbox@{DIGEST}",
        "tenant_secret": "ufo-tenant",
        "tenant_secret_checksum": "cafe1234",
        "platform_secret": "ufo-platform-secrets",
        "ingress_class": "nginx",
        "cluster_issuer": "letsencrypt",
        "workspace_id": "3f8c1e2a-0b4d-4c6e-9a1f-2b3c4d5e6f70",
    }
    base.update(overrides)
    return TenantChartValues.model_validate(base)


def _forbidden_kube() -> KubeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"_helm_apply must not touch kube: {request.method} {request.url.path}"
        )

    return _kube(httpx.MockTransport(handler))


def _reconciler(fake: FakeHelm, kube: KubeClient | None = None) -> TenantReconciler:
    return TenantReconciler(
        kube=kube or _forbidden_kube(), platform=_platform(), helm=Helm(run=fake.run)
    )


def test_canonical_ignores_key_order_and_null_values() -> None:
    assert _canonical({"b": "2", "a": "1", "c": None}) == _canonical({"a": "1", "b": "2"})
    assert _canonical({"a": "1"}) != _canonical({"a": "2"})


async def test_steady_state_tenant_runs_no_helm_upgrade() -> None:
    values = _chart_values()
    fake = FakeHelm(
        status_result=_status_result("deployed", 5),
        values_result=_values_result(values.model_dump(mode="json")),
    )
    await _reconciler(fake)._helm_apply(values)
    # A deployed release whose live values match the render: two lock-free reads, no upgrade lock.
    assert [call[0] for call in fake.calls] == ["status", "get"]
    assert not fake.ran_upgrade
    assert not fake.ran_uninstall


async def test_reordered_or_reformatted_live_values_are_not_drift() -> None:
    # helm re-serializes values in its own key order and drops explicit nulls; neither is drift.
    values = _chart_values(workspace_id=None)
    live = {
        key: value for key, value in values.model_dump(mode="json").items() if value is not None
    }
    scrambled = dict(reversed(list(live.items())))
    fake = FakeHelm(_status_result("deployed", 12), _values_result(scrambled))
    await _reconciler(fake)._helm_apply(values)
    assert not fake.ran_upgrade


async def test_helm_upgrade_runs_when_rendered_values_drift() -> None:
    values = _chart_values()
    stale = values.model_dump(mode="json") | {"bundle_image": f"ghcr.io/acme/ufo@sha256:{'0' * 64}"}
    fake = FakeHelm(_status_result("deployed", 5), _values_result(stale))
    await _reconciler(fake)._helm_apply(values)
    assert fake.ran_upgrade


async def test_helm_installs_when_the_release_is_absent() -> None:
    fake = FakeHelm(_absent_release())
    await _reconciler(fake)._helm_apply(_chart_values())
    assert fake.ran_upgrade
    assert not fake.read_values  # nothing deployed to diff against


async def test_a_deployed_release_is_never_healed() -> None:
    values = _chart_values()
    fake = FakeHelm(_status_result("deployed", 3), _values_result(values.model_dump(mode="json")))
    # _forbidden_kube proves no release Secret is deleted; the asserts prove no uninstall/upgrade.
    await _reconciler(fake)._helm_apply(values)
    assert not fake.ran_uninstall
    assert not fake.ran_upgrade


async def test_stuck_pending_upgrade_is_healed_then_reapplied() -> None:
    deleted: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        deleted["path"] = request.url.path
        return httpx.Response(200, json={})

    fake = FakeHelm(_status_result("pending-upgrade", 8878))
    reconciler = _reconciler(fake, kube=_kube(httpx.MockTransport(handler)))
    await reconciler._helm_apply(_chart_values())
    # The dangling pending-upgrade revision Secret is deleted (reverting to the last deployed rev)…
    assert deleted["path"].endswith("/ufo-acme/secrets/sh.helm.release.v1.ufo-acme.v8878")
    # …then desired is applied once. No uninstall (that would destroy a live tenant's resources).
    assert fake.ran_upgrade
    assert not fake.ran_uninstall
    assert not fake.read_values


async def test_failed_first_install_is_uninstalled_then_reinstalled() -> None:
    fake = FakeHelm(_status_result("failed", 1))
    await _reconciler(fake)._helm_apply(_chart_values())
    heads = [call[0] for call in fake.calls]
    assert "uninstall" in heads
    assert fake.ran_upgrade
    assert heads.index("uninstall") < heads.index("upgrade")
