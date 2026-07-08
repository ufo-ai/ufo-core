"""Control-plane constants and the platform's own config — one platform.toml, fail loud.

The Kubernetes-facing identity of the control plane (mirrors metalcraft_contracts/platform.py,
stripped from a 7-CRD product surface to the single kind this control plane owns: ``Tenant``). The
seven product CRDs are gone — core's DB schema is the store (RFC 0003 §2). What remains is one kind:
a tenant, which is a whole ufo runtime in its own namespace.

``PlatformConfig`` is the control plane's own configuration — distinct from a tenant's
``ufo.toml``. It names the shared backing services (Postgres, Redis, S3) and cluster facts
(ingress class, cert-manager issuer, the platform Secret holding model + cloud keys) that the
reconciler overlays onto each tenant's core config.
"""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

API_GROUP = "flyingobject.ai"
API_VERSION = "v1"
API_GROUP_VERSION = f"{API_GROUP}/{API_VERSION}"
PLATFORM_NAMESPACE = "ufo-system"

TENANT_KIND = "Tenant"
TENANT_PLURAL = "tenants"
TENANT_NAMESPACE_PREFIX = "ufo-"
TENANT_NAMESPACE_LABEL = f"{API_GROUP}/tenant"
TENANT_NAME_LABEL = f"{API_GROUP}/tenant-name"
PACK_LABEL = f"{API_GROUP}/pack"
ORG_DOMAIN_LABEL = f"{API_GROUP}/org-domain"

# The control-plane-minted workspace uuid, persisted on the Tenant CR's status so re-reconciles
# reuse it (idempotency). Kept off core's DeployStatus (extra="forbid"); the operator threads it and
# kube.patch_tenant_status merges it into the status body.
WORKSPACE_ID_STATUS_FIELD = "workspaceId"


def tenant_namespace(name: str) -> str:
    return f"{TENANT_NAMESPACE_PREFIX}{name}"


FIELD_MANAGER = f"{API_GROUP}/operator"
OPERATOR_LEASE_NAME = "ufo-operator"

RECONCILE_INTERVAL_SECONDS = 15
LEASE_DURATION_SECONDS = 30

TENANT_SECRET_NAME = "ufo-tenant"
SERVE_PORT = 8710

CONFIG_PATH_ENV = "UFO_CONTROL_CONFIG"
DEFAULT_CONFIG_PATH = Path("platform.toml")


class PlatformConfig(BaseModel):
    """The shared services and cluster facts the reconciler overlays onto every tenant.

    ``postgres_model`` picks the tenant Postgres tier server-side (``DeployRequest.postgres`` is a
    request the platform overrides): ``rls`` — the hosted default — mints an RLS-subject role on the
    one shared ``app_database`` and a per-tenant DBOS sibling; ``database`` mints a whole database
    per tenant. ``postgres_admin_dsn_env`` names the env var holding a libpq DSN with
    CREATEROLE/CREATEDB on the shared Postgres; the reconciler provisions roles/databases from it.
    ``tenant_postgres_host`` is the ``host:port`` a tenant pod dials Postgres at. The blob and redis
    fields are the shared S3 bucket and Redis a tenant's core config points at — core isolates by
    ``workspace_id`` on every row and key. ``platform_secret`` is the Secret name carrying the model
    API keys and cloud credentials a tenant's ``serve`` reads from env; the owner creates it once in
    ``ufo-system`` and the reconciler replicates it into each tenant namespace during provisioning.
    """

    model_config = ConfigDict(extra="forbid")

    chart_path: Path
    registry: str

    postgres_model: Literal["database", "rls"] = "rls"
    postgres_admin_dsn_env: str = "UFO_CONTROL_POSTGRES_ADMIN_DSN"
    tenant_postgres_host: str
    app_database: str = "ufo"

    redis_url: str

    blob_bucket: str
    blob_endpoint_url: str | None = None
    blob_region: str | None = None
    blob_s3_url: str | None = None
    blob_sts_role_arn: str | None = None
    blob_sts_endpoint: str | None = None
    blob_path_style: bool = False

    # The IRSA role the tenant chart annotates onto the ufo-serve ServiceAccount, so the pod's boto3
    # reaches the blob bucket and assumes blob_sts_role_arn for the per-conversation s3fs mount. Set
    # on any deploy whose runtime AWS identity comes from IRSA (EKS); unset for a local/dev backend
    # that supplies credentials another way.
    serve_role_arn: str | None = None

    # The externally-reachable base an off-cluster sandbox (e2b) dials the tenant's in-pod egress
    # proxy at (e.g. http://sandbox-proxy.<domain>:8888). Set only for an off-cluster sandbox
    # backend; the reconciler overlays it as [sandbox] proxy_public_url and the tenant chart exposes
    # the proxy port on a LoadBalancer at this host. Unset for an in-cluster backend (docker/pod).
    sandbox_proxy_url: str | None = None

    ingress_class: str = "nginx"
    cluster_issuer: str = "letsencrypt"
    platform_secret: str = "ufo-platform-secrets"

    otlp_endpoint: str | None = None

    reconcile_interval_seconds: int = RECONCILE_INTERVAL_SECONDS

    @property
    def postgres_admin_dsn(self) -> str:
        dsn = os.environ.get(self.postgres_admin_dsn_env)
        if not dsn:
            raise RuntimeError(f"{self.postgres_admin_dsn_env} is unset — no Postgres admin DSN")
        return dsn


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)))


def load_platform_config(path: Path | None = None) -> PlatformConfig:
    resolved = path or config_path()
    if not resolved.exists():
        raise FileNotFoundError(
            f"missing platform config {resolved} (create platform.toml or set {CONFIG_PATH_ENV})"
        )
    return PlatformConfig.model_validate(tomllib.loads(resolved.read_text()))
