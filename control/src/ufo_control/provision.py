"""Reconcile one tenant to Ready — the control-plane workflow (RFC 0004 "provisioning workflow").

One frozen dataclass, one public method (``reconcile``), private steps beneath it in execution
order: namespace → platform Secret → Postgres → render → tenant Secret → Helm → observe.
Level-triggered like the operator: every step is idempotent server-side-apply or ``helm upgrade
--install``, so re-running the whole flow each interval converges without special-casing create vs
update — and a failed first install is uninstalled so its ``post-install`` hook re-runs.

The declarative substrate — Deployment, HPA, Service, Ingress+TLS, NetworkPolicy, RBAC, the
``ufoctl init`` Job — lives in the ``ufo-tenant`` Helm chart; this workflow renders its
values and applies it. Postgres provisioning and the tenant Secret (carrying the DB DSN) are applied
directly by the control plane, out of band from the chart, so no secret ever rides a values file.
"""

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ufo.deploy import DeployRequest, DeployStatus

from ufo_control.kube import KubeClient
from ufo_control.platform import (
    PLATFORM_NAMESPACE,
    TENANT_NAME_LABEL,
    TENANT_NAMESPACE_LABEL,
    PlatformConfig,
    tenant_namespace,
)
from ufo_control.postgres import TenantPostgres, ensure_tenant_postgres
from ufo_control.render import TenantChartValues, render_tenant

SERVE_DEPLOYMENT = "ufo-serve"
INIT_JOB = "ufo-init"
TENANT_SECRET = "ufo-tenant"

# A first `helm install` that fails (or is interrupted) leaves a revision-1 release in one of these
# phases; every later reconcile then `upgrade`s it and never re-runs the `post-install` init Job.
# The reconciler uninstalls such a release so the next pass installs fresh and the hook fires.
FIRST_INSTALL_REVISION = 1
FAILED_INSTALL_PHASES = frozenset({"failed", "pending-install", "unknown"})


@dataclass(frozen=True)
class TenantReconciler:
    kube: KubeClient
    platform: PlatformConfig

    async def reconcile(self, request: DeployRequest, workspace_id: str) -> DeployStatus:
        name = request.tenant.name
        try:
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
            values, secret_data = self._render(request, postgres)
            await self.kube.apply_secret(tenant_namespace(name), TENANT_SECRET, secret_data)
            await self._helm_apply(values)
            return await self._observe(request)
        except Exception as error:
            return DeployStatus(tenant=name, phase="Failed", message=str(error))

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

    def _render(
        self, request: DeployRequest, postgres: TenantPostgres
    ) -> tuple[TenantChartValues, dict[str, str]]:
        rendered = render_tenant(request, self.platform, postgres)
        return rendered.values, rendered.secret.data()

    async def _helm_apply(self, values: TenantChartValues) -> None:
        await self._clear_failed_install(values.namespace)
        with tempfile.TemporaryDirectory() as directory:
            values_file = Path(directory) / "values.json"
            await asyncio.to_thread(values_file.write_text, values.model_dump_json())
            process = await asyncio.create_subprocess_exec(
                "helm",
                "upgrade",
                "--install",
                values.namespace,
                str(self.platform.chart_path),
                "--namespace",
                values.namespace,
                "--values",
                str(values_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"helm apply for {values.namespace} failed: {stderr.decode().strip()}"
            )

    async def _clear_failed_install(self, release: str) -> None:
        status = await self._helm_status(release)
        if status is not None and _is_failed_first_install(*status):
            await self._helm_uninstall(release)

    async def _helm_status(self, release: str) -> tuple[str, int] | None:
        process = await asyncio.create_subprocess_exec(
            "helm",
            "status",
            release,
            "--namespace",
            release,
            "--output",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None
        info = json.loads(stdout)
        return info["info"]["status"], int(info["version"])

    async def _helm_uninstall(self, release: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "helm",
            "uninstall",
            release,
            "--namespace",
            release,
            "--wait",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"helm uninstall for {release} failed: {stderr.decode().strip()}")

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


def _is_failed_first_install(phase: str, revision: int) -> bool:
    return revision == FIRST_INSTALL_REVISION and phase in FAILED_INSTALL_PHASES


def _ready_replicas(deployment: dict | None) -> int:
    if deployment is None:
        return 0
    status = deployment.get("status", {})
    return int(status.get("readyReplicas", 0))


def _job_succeeded(job: dict) -> bool:
    return int(job.get("status", {}).get("succeeded", 0)) >= 1


def _job_failed(job: dict) -> bool:
    return int(job.get("status", {}).get("failed", 0)) >= 1
