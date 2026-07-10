"""Reconcile one tenant to Ready — the control-plane workflow (RFC 0004 "provisioning workflow").

One frozen dataclass, one public method (``reconcile``), private steps beneath it in execution
order: bundle advance → namespace → platform Secret → Postgres → minted keys → render → tenant
Secret → Helm → observe. The advance patches only ``spec.bundle_image`` (a single-field SSA under
its own manager) on a Tenant pinning an image other than the platform's, then reconciles onward
from the refetched CR — a control-plane deploy thereby rolls every tenant, the CR spec stays the
record of what runs, and a concurrent re-deploy's spec is neither overwritten nor rendered stale.
The minted keys are read back from the applied tenant Secret and minted only when absent, so
re-reconciles never rotate the Fernet key sealing the workspace's credential rows.

Level-triggered like the operator: the namespace/Secret server-side-applies and the Postgres ensure
are true no-ops when their inputs are unchanged, so they run every interval. The Helm step is not —
``helm upgrade`` cuts a fresh release revision and takes the release lock on every invocation, even
when the rendered values are byte-identical. So it is gated on real drift: the rendered
``TenantChartValues`` are compared (order- and format-insensitively) against ``helm get values`` for
the live release, and ``helm upgrade --install`` runs only when they differ or the release is
absent. A steady-state tenant therefore takes zero release locks per reconcile.

The operator self-heals a stuck release before applying, rather than stranding it: a failed first
install (revision 1) is uninstalled so its ``post-install`` init Job re-runs on the fresh install;
a ``pending-upgrade`` at revision > 1 — the fingerprint of an operator killed mid-upgrade, whose
half-written revision holds the lock and freezes every later reconcile — is cleared by deleting its
dangling Helm release Secret, which atomically reverts the release to its last deployed revision
with no ``pending-rollback`` window, so the next apply proceeds.

The declarative substrate — Deployment, HPA, Service, Ingress+TLS, NetworkPolicy, RBAC, the
``ufoctl init`` Job — lives in the ``ufo-tenant`` Helm chart; this workflow renders its
values and applies it. Postgres provisioning and the tenant Secret (carrying the DB DSN) are applied
directly by the control plane, out of band from the chart, so no secret ever rides a values file.
"""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ufo.deploy import DeployRequest, DeployStatus

from ufo_control.kube import KubeClient, request_from_tenant
from ufo_control.platform import (
    PLATFORM_NAMESPACE,
    TENANT_NAME_LABEL,
    TENANT_NAMESPACE_LABEL,
    PlatformConfig,
    tenant_namespace,
)
from ufo_control.postgres import TenantPostgres, ensure_tenant_postgres
from ufo_control.render import (
    SECRET_ARTIFACT_TOKEN,
    SECRET_CREDENTIAL_KEY,
    MintedKeys,
    TenantChartValues,
    mint_keys,
    render_tenant,
)

SERVE_DEPLOYMENT = "ufo-serve"
INIT_JOB = "ufo-init"
TENANT_SECRET = "ufo-tenant"

DEPLOYED_PHASE = "deployed"
STUCK_UPGRADE_PHASE = "pending-upgrade"

# A first `helm install` that fails (or is interrupted) leaves a revision-1 release in one of these
# phases; every later reconcile then `upgrade`s it and never re-runs the `post-install` init Job.
# The reconciler uninstalls such a release so the next pass installs fresh and the hook fires.
FIRST_INSTALL_REVISION = 1
FAILED_INSTALL_PHASES = frozenset({"failed", "pending-install", "unknown"})

# Helm 3/4 stores each release revision as a Secret named `sh.helm.release.v1.<release>.v<revision>`
# in the release namespace. Deleting the Secret of a stuck `pending-upgrade` revision reverts the
# release to the prior deployed revision atomically — the safe, no-`pending-rollback` recovery.
HELM_RELEASE_SECRET_PREFIX = "sh.helm.release.v1"


@dataclass(frozen=True)
class HelmResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class HelmRelease:
    """The live release as ``helm status`` reports it: its phase and revision. The last-applied
    values are read separately (``helm get values``) only when a drift check needs them."""

    phase: str
    revision: int


async def _shell_helm(*args: str) -> HelmResult:
    process = await asyncio.create_subprocess_exec(
        "helm",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return HelmResult(returncode=process.returncode or 0, stdout=stdout, stderr=stderr)


HelmRun = Callable[..., Awaitable[HelmResult]]


@dataclass(frozen=True)
class Helm:
    """The helm CLI behind one async subprocess boundary (``run``, injectable for tests). ``status``
    and ``values`` are lock-free reads of the release Secret; ``upgrade`` and ``uninstall`` take the
    release lock. The reconciler owns when each runs — a steady-state tenant only reads."""

    run: HelmRun = _shell_helm

    async def status(self, release: str, namespace: str) -> HelmRelease | None:
        result = await self.run("status", release, "--namespace", namespace, "--output", "json")
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        return HelmRelease(phase=info["info"]["status"], revision=int(info["version"]))

    async def values(self, release: str, namespace: str) -> dict[str, Any]:
        result = await self.run(
            "get", "values", release, "--namespace", namespace, "--output", "json"
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout) or {}

    async def upgrade(self, release: str, chart: Path, namespace: str, values_json: str) -> None:
        with TemporaryDirectory() as directory:
            values_file = Path(directory) / "values.json"
            await asyncio.to_thread(values_file.write_text, values_json)
            result = await self.run(
                "upgrade",
                "--install",
                release,
                str(chart),
                "--namespace",
                namespace,
                "--values",
                str(values_file),
            )
        if result.returncode != 0:
            raise RuntimeError(f"helm apply for {release} failed: {result.stderr.decode().strip()}")

    async def uninstall(self, release: str, namespace: str) -> None:
        result = await self.run("uninstall", release, "--namespace", namespace, "--wait")
        if result.returncode != 0:
            raise RuntimeError(
                f"helm uninstall for {release} failed: {result.stderr.decode().strip()}"
            )


@dataclass(frozen=True)
class TenantReconciler:
    kube: KubeClient
    platform: PlatformConfig
    helm: Helm = Helm()

    async def reconcile(self, request: DeployRequest, workspace_id: str) -> DeployStatus:
        name = request.tenant.name
        try:
            request = await self._advance_bundle_image(request)
            await self._ensure_namespace(request)
            await self._ensure_platform_secret(request)
            postgres = await ensure_tenant_postgres(
                self.platform.postgres_model,
                self.platform.postgres_admin_dsn,
                name,
                self.platform.tenant_postgres_host,
                self.platform.app_database,
                workspace_id,
            )
            minted = await self._minted_keys(name)
            values, secret_data = self._render(request, postgres, minted)
            await self.kube.apply_secret(tenant_namespace(name), TENANT_SECRET, secret_data)
            await self._helm_apply(values)
            return await self._observe(request)
        except Exception as error:
            return DeployStatus(tenant=name, phase="Failed", message=str(error))

    async def _advance_bundle_image(self, request: DeployRequest) -> DeployRequest:
        if request.bundle_image == self.platform.bundle_image:
            return request
        name = request.tenant.name
        await self.kube.patch_tenant_bundle_image(name, self.platform.bundle_image)
        obj = await self.kube.get_tenant(name)
        if obj is None:
            raise RuntimeError(f"tenant {name!r} disappeared during its bundle advance")
        return request_from_tenant(obj)

    async def _ensure_namespace(self, request: DeployRequest) -> None:
        await self.kube.apply_namespace(
            tenant_namespace(request.tenant.name),
            {TENANT_NAMESPACE_LABEL: "true", TENANT_NAME_LABEL: request.tenant.name},
        )

    async def _ensure_platform_secret(self, request: DeployRequest) -> None:
        secret = self.platform.platform_secret
        source = await self.kube.read_secret(PLATFORM_NAMESPACE, secret)
        if source is None:
            raise RuntimeError(
                f"platform Secret {secret!r} is absent in {PLATFORM_NAMESPACE} — create it with "
                "the model API keys + cloud credentials tenant pods read from env (see "
                "deploy/control-plane.yaml); the operator replicates it into each tenant namespace"
            )
        await self.kube.apply_secret(
            tenant_namespace(request.tenant.name), secret, source.get("data", {})
        )

    async def _minted_keys(self, name: str) -> MintedKeys:
        secret = await self.kube.read_secret(tenant_namespace(name), TENANT_SECRET)
        if secret is None:
            return mint_keys()
        data = secret["data"]
        return MintedKeys(
            credential_key=base64.b64decode(data[SECRET_CREDENTIAL_KEY]).decode(),
            artifact_token_secret=base64.b64decode(data[SECRET_ARTIFACT_TOKEN]).decode(),
        )

    def _render(
        self, request: DeployRequest, postgres: TenantPostgres, minted: MintedKeys
    ) -> tuple[TenantChartValues, dict[str, str]]:
        rendered = render_tenant(request, self.platform, postgres, minted)
        return rendered.values, rendered.secret.data()

    async def _helm_apply(self, values: TenantChartValues) -> None:
        release = values.namespace
        live = await self.helm.status(release, release)
        if live is not None and _is_stuck(live):
            await self._clear_stuck(release, live)
            live = None
        if live is not None and not await self._drifted(release, live, values):
            return
        await self.helm.upgrade(
            release, self.platform.chart_path, release, values.model_dump_json()
        )

    async def _clear_stuck(self, release: str, live: HelmRelease) -> None:
        if _is_failed_first_install(live.phase, live.revision):
            await self.helm.uninstall(release, release)
        else:
            await self.kube.delete_secret(release, _release_secret(release, live.revision))

    async def _drifted(self, release: str, live: HelmRelease, values: TenantChartValues) -> bool:
        if live.phase != DEPLOYED_PHASE:
            return True
        applied = await self.helm.values(release, release)
        return _canonical(applied) != _canonical(values.model_dump(mode="json"))

    async def _observe(self, request: DeployRequest) -> DeployStatus:
        name = request.tenant.name
        namespace = tenant_namespace(name)
        url = f"https://{request.tenant.host}"
        job = await self.kube.read_job(namespace, INIT_JOB)
        if job is not None and _job_failed(job):
            return DeployStatus(tenant=name, phase="Failed", url=url, message="ufoctl init failed")
        deployment = await self.kube.read_deployment(namespace, SERVE_DEPLOYMENT)
        if _ready_replicas(deployment) >= 1 and job is not None and _job_succeeded(job):
            return DeployStatus(tenant=name, phase="Ready", url=url, message="serving")
        return DeployStatus(
            tenant=name, phase="Provisioning", url=url, message="waiting for serve + init"
        )


def _is_stuck(live: HelmRelease) -> bool:
    return _is_failed_first_install(live.phase, live.revision) or _is_stuck_upgrade(
        live.phase, live.revision
    )


def _is_failed_first_install(phase: str, revision: int) -> bool:
    return revision == FIRST_INSTALL_REVISION and phase in FAILED_INSTALL_PHASES


def _is_stuck_upgrade(phase: str, revision: int) -> bool:
    return revision > FIRST_INSTALL_REVISION and phase == STUCK_UPGRADE_PHASE


def _release_secret(release: str, revision: int) -> str:
    return f"{HELM_RELEASE_SECRET_PREFIX}.{release}.v{revision}"


def _canonical(values: dict[str, Any]) -> str:
    """A stable string for comparing two value maps: null-valued keys dropped (helm treats an
    absent key and an explicit null identically) and keys sorted, so a difference only in key
    ordering or JSON formatting between our render and ``helm get values`` never reads as drift —
    only a changed value (image digest, host, checksum, …) does."""
    present = {key: value for key, value in values.items() if value is not None}
    return json.dumps(present, sort_keys=True)


def _ready_replicas(deployment: dict | None) -> int:
    if deployment is None:
        return 0
    status = deployment.get("status", {})
    return int(status.get("readyReplicas", 0))


def _job_succeeded(job: dict) -> bool:
    return int(job.get("status", {}).get("succeeded", 0)) >= 1


def _job_failed(job: dict) -> bool:
    return int(job.get("status", {}).get("failed", 0)) >= 1
