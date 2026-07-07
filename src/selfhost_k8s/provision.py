"""Reconcile one tenant to Ready — the control-plane workflow (RFC 0004 "provisioning workflow").

One frozen dataclass, one public method (``reconcile``), private steps beneath it in execution
order: namespace → Postgres → render → Secret → Helm → observe. Level-triggered like the operator:
every step is idempotent server-side-apply or ``helm upgrade --install``, so re-running the whole
flow each interval converges without special-casing create vs update.

The declarative substrate — Deployment, HPA, Service, Ingress+TLS, NetworkPolicy, RBAC, the
``selfhost init`` Job — lives in the ``selfhost-tenant`` Helm chart; this workflow renders its
values and applies it. Postgres provisioning and the tenant Secret (carrying the DB DSN) are applied
directly by the control plane, out of band from the chart, so no secret ever rides a values file.
"""

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from selfhost_k8s.contract import DeployRequest, DeployStatus
from selfhost_k8s.kube import KubeClient
from selfhost_k8s.platform import TENANT_NAME_LABEL, TENANT_NAMESPACE_LABEL, PlatformConfig
from selfhost_k8s.postgres import ensure_tenant_postgres
from selfhost_k8s.render import TenantChartValues, render_tenant

SERVE_DEPLOYMENT = "selfhost-serve"
INIT_JOB = "selfhost-init"
TENANT_SECRET = "selfhost-tenant"


@dataclass(frozen=True)
class TenantReconciler:
    kube: KubeClient
    platform: PlatformConfig

    async def reconcile(self, request: DeployRequest) -> DeployStatus:
        name = request.tenant.name
        try:
            await self._ensure_namespace(request)
            dsn = await ensure_tenant_postgres(
                request.postgres,
                self.platform.postgres_admin_dsn,
                name,
                self.platform.tenant_postgres_host,
            )
            values, secret_data = self._render(request, dsn)
            await self.kube.apply_secret(request.tenant.namespace, TENANT_SECRET, secret_data)
            await self._helm_apply(values)
            return await self._observe(request)
        except NotImplementedError as error:
            return DeployStatus(tenant=name, phase="Failed", message=str(error))

    async def _ensure_namespace(self, request: DeployRequest) -> None:
        await self.kube.apply_namespace(
            request.tenant.namespace,
            {TENANT_NAMESPACE_LABEL: "true", TENANT_NAME_LABEL: request.tenant.name},
        )

    def _render(self, request: DeployRequest, dsn: str) -> tuple[TenantChartValues, dict[str, str]]:
        rendered = render_tenant(request, self.platform, dsn)
        return rendered.values, rendered.secret.data()

    async def _helm_apply(self, values: TenantChartValues) -> None:
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

    async def _observe(self, request: DeployRequest) -> DeployStatus:
        name = request.tenant.name
        namespace = request.tenant.namespace
        url = f"https://{request.tenant.host}"
        job = await self.kube.read_job(namespace, INIT_JOB)
        if job is not None and _job_failed(job):
            return DeployStatus(
                tenant=name, phase="Failed", url=url, message="selfhost init failed"
            )
        deployment = await self.kube.read_deployment(namespace, SERVE_DEPLOYMENT)
        if _ready_replicas(deployment) >= 1 and job is not None and _job_succeeded(job):
            return DeployStatus(tenant=name, phase="Ready", url=url, message="serving")
        return DeployStatus(
            tenant=name, phase="Provisioning", url=url, message="waiting for serve + init"
        )


def _ready_replicas(deployment: dict | None) -> int:
    if deployment is None:
        return 0
    status = deployment.get("status", {})
    return int(status.get("readyReplicas", 0))


def _job_succeeded(job: dict) -> bool:
    return int(job.get("status", {}).get("succeeded", 0)) >= 1


def _job_failed(job: dict) -> bool:
    return int(job.get("status", {}).get("failed", 0)) >= 1
