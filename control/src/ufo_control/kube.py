"""A minimal async Kubernetes apiserver client — the stripped re-derivation of metalcraft's
``metalcraft_k8s/kubernetes_client.py``.

metalcraft's client was raw ``urllib`` (no k8s SDK) covering seven CRDs, leases, events, and
``TokenRequest``. This one is raw ``httpx`` (async-native — the operator runs one event loop) and
covers only what the single ``Tenant`` kind needs: server-side-apply of the tenant object, its
namespace and Secret, its status subresource, the leader-election Lease, and readiness reads of the
tenant's Deployment and ``init`` Job. Everything the sandbox-proxy TokenRequest /
SubjectAccessReview machinery did is deliberately gone (RFC 0003 §3.2, decision 7).

In-cluster it reads the ServiceAccount token and CA from the standard mount; env overrides
(``UFO_CONTROL_KUBE_TOKEN_FILE`` / ``_CA_FILE`` / ``KUBERNETES_SERVICE_HOST`` / ``_PORT``) let it
run against a ``kubectl proxy`` in dev.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from ufo.deploy import DeployImage, DeployRequest, DeployStatus

from ufo_control.platform import (
    API_GROUP_VERSION,
    BUNDLE_ADVANCE_FIELD_MANAGER,
    FIELD_MANAGER,
    ORG_DOMAIN_LABEL,
    PACK_LABEL,
    PLATFORM_NAMESPACE,
    TENANT_KIND,
    TENANT_NAME_LABEL,
    TENANT_PLURAL,
    WORKSPACE_ID_STATUS_FIELD,
)

SA_ROOT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
APPLY_CONTENT_TYPE = "application/apply-patch+yaml"
LEASE_API_VERSION = "coordination.k8s.io/v1"


def org_domain(owner_email: str) -> str:
    """The owner email's domain — the label a tenant is discovered by when a second member of the
    same organization onboards (one tenant per work-email domain)."""
    return owner_email.rsplit("@", 1)[-1].lower()


def tenant_body(request: DeployRequest) -> dict[str, Any]:
    """The ``Tenant`` custom resource — its ``.spec`` is the deploy request verbatim (snake_case,
    the same JSON the OSS producer posts), so the operator round-trips spec → ``DeployRequest``. The
    org-domain label is set at admission so join-or-provision can count tenants for a domain.
    ``exclude_none`` drops an absent ``sandbox_image``: the CRD property is a non-nullable object,
    so a ``null`` would be rejected 422 — omitting it lets the optional simply be absent."""
    return {
        "apiVersion": API_GROUP_VERSION,
        "kind": TENANT_KIND,
        "metadata": {
            "name": request.tenant.name,
            "namespace": PLATFORM_NAMESPACE,
            "labels": {
                TENANT_NAME_LABEL: request.tenant.name,
                PACK_LABEL: request.pack,
                ORG_DOMAIN_LABEL: org_domain(request.tenant.owner_email),
            },
        },
        "spec": request.model_dump(mode="json", exclude_none=True),
    }


def request_from_tenant(obj: dict[str, Any]) -> DeployRequest:
    return DeployRequest.model_validate(obj["spec"])


def status_from_tenant(obj: dict[str, Any]) -> DeployStatus:
    """Build the poll response from a Tenant CR, mapping the CR's camelCase ``status.workspaceId``
    onto ``DeployStatus.workspace_id`` and reading it defensively (absent until the tenant is
    Ready)."""
    name = obj["metadata"]["name"]
    status = obj.get("status") or {}
    phase = status.get("phase")
    if not phase:
        return DeployStatus(tenant=name, phase="Pending", message="not yet reconciled")
    return DeployStatus(
        tenant=name,
        phase=phase,
        url=status.get("url"),
        message=status.get("message", ""),
        workspace_id=status.get("workspaceId"),
    )


@dataclass(frozen=True)
class KubeClient:
    http: httpx.AsyncClient

    @classmethod
    def from_env(cls) -> "KubeClient":
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        token_file = Path(os.environ.get("UFO_CONTROL_KUBE_TOKEN_FILE", str(SA_ROOT / "token")))
        ca_file = Path(os.environ.get("UFO_CONTROL_KUBE_CA_FILE", str(SA_ROOT / "ca.crt")))
        token = token_file.read_text().strip()
        verify: str | bool = str(ca_file) if ca_file.exists() else True
        http = httpx.AsyncClient(
            base_url=f"https://{host}:{port}",
            headers={"Authorization": f"Bearer {token}"},
            verify=verify,
            timeout=30.0,
        )
        return cls(http=http)

    async def close(self) -> None:
        await self.http.aclose()

    async def apply(
        self, path: str, body: dict[str, Any], field_manager: str = FIELD_MANAGER
    ) -> dict[str, Any]:
        response = await self.http.patch(
            path,
            content=json.dumps(body).encode(),
            headers={"Content-Type": APPLY_CONTENT_TYPE},
            params={"fieldManager": field_manager, "force": "true"},
        )
        response.raise_for_status()
        return response.json()

    async def get(self, path: str) -> dict[str, Any] | None:
        response = await self.http.get(path)
        if response.status_code in (403, 404):
            return None
        response.raise_for_status()
        return response.json()

    async def list_tenants(self, org_domain: str | None = None) -> list[dict[str, Any]]:
        path = f"/apis/{API_GROUP_VERSION}/namespaces/{PLATFORM_NAMESPACE}/{TENANT_PLURAL}"
        if org_domain is not None:
            path += f"?labelSelector={ORG_DOMAIN_LABEL}={org_domain}"
        listing = await self.get(path)
        if listing is None:
            return []
        items = listing.get("items", [])
        return list(items)

    async def get_tenant(self, name: str) -> dict[str, Any] | None:
        return await self.get(self._tenant_path(name))

    async def apply_tenant(self, request: DeployRequest) -> None:
        await self.apply(self._tenant_path(request.tenant.name), tenant_body(request))

    async def patch_tenant_bundle_image(self, name: str, image: DeployImage) -> None:
        # A single-field SSA under its own manager: the advance owns spec.bundle_image and nothing
        # else, so it can never overwrite a concurrent re-deploy's config_toml/pack/secrets.
        body = {
            "apiVersion": API_GROUP_VERSION,
            "kind": TENANT_KIND,
            "metadata": {"name": name, "namespace": PLATFORM_NAMESPACE},
            "spec": {"bundle_image": image.model_dump(mode="json")},
        }
        await self.apply(self._tenant_path(name), body, BUNDLE_ADVANCE_FIELD_MANAGER)

    async def patch_tenant_status(self, name: str, status: DeployStatus, workspace_id: str) -> None:
        # exclude_none: a Failed status carries no url, and the CRD's status.url is a non-nullable
        # string — emitting `"url": null` is rejected 422, so an errored reconcile would never
        # surface its phase. Omitting the absent optional lets the apply set exactly what is known.
        # workspaceId rides the status alongside the DeployStatus fields (it is not on core's
        # DeployStatus): persisted so re-reconciles reuse the minted uuid — idempotency.
        status_body = status.model_dump(mode="json", exclude_none=True)
        status_body[WORKSPACE_ID_STATUS_FIELD] = workspace_id
        body = {
            "apiVersion": API_GROUP_VERSION,
            "kind": TENANT_KIND,
            "metadata": {"name": name, "namespace": PLATFORM_NAMESPACE},
            "status": status_body,
        }
        await self.apply(f"{self._tenant_path(name)}/status", body)

    async def apply_namespace(self, name: str, labels: dict[str, str]) -> None:
        body = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": name, "labels": labels},
        }
        await self.apply(f"/api/v1/namespaces/{name}", body)

    async def apply_secret(self, namespace: str, name: str, data: dict[str, str]) -> None:
        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": namespace},
            "type": "Opaque",
            "data": data,
        }
        await self.apply(f"/api/v1/namespaces/{namespace}/secrets/{name}", body)

    async def read_secret(self, namespace: str, name: str) -> dict[str, Any] | None:
        # None means proven-absent: only a 404 may report a Secret missing. A 403 raises — the
        # reconciler mints tenant keys when this returns None, and minting on an RBAC failure
        # would rotate the Fernet key sealing the workspace's credential rows.
        response = await self.http.get(f"/api/v1/namespaces/{namespace}/secrets/{name}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def delete_secret(self, namespace: str, name: str) -> None:
        # 404-tolerant so healing a stuck release is idempotent: another reconcile (or a hand fix)
        # may have already removed the dangling Helm release Secret.
        response = await self.http.delete(f"/api/v1/namespaces/{namespace}/secrets/{name}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def get_lease(self, namespace: str, name: str) -> dict[str, Any] | None:
        return await self.get(self._lease_path(namespace, name))

    async def apply_lease(self, namespace: str, name: str, spec: dict[str, Any]) -> None:
        body = {
            "apiVersion": LEASE_API_VERSION,
            "kind": "Lease",
            "metadata": {"name": name, "namespace": namespace},
            "spec": spec,
        }
        await self.apply(self._lease_path(namespace, name), body)

    async def read_deployment(self, namespace: str, name: str) -> dict[str, Any] | None:
        return await self.get(f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}")

    async def read_job(self, namespace: str, name: str) -> dict[str, Any] | None:
        return await self.get(f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}")

    def _tenant_path(self, name: str) -> str:
        return f"/apis/{API_GROUP_VERSION}/namespaces/{PLATFORM_NAMESPACE}/{TENANT_PLURAL}/{name}"

    def _lease_path(self, namespace: str, name: str) -> str:
        return f"/apis/{LEASE_API_VERSION}/namespaces/{namespace}/leases/{name}"
