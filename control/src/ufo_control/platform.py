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

from pydantic import BaseModel, ConfigDict

API_GROUP = "flyingobject.ai"
API_VERSION = "v1"
API_GROUP_VERSION = f"{API_GROUP}/{API_VERSION}"
PLATFORM_NAMESPACE = "ufo-system"

TENANT_KIND = "Tenant"
TENANT_PLURAL = "tenants"
TENANT_NAMESPACE_LABEL = f"{API_GROUP}/tenant"
TENANT_NAME_LABEL = f"{API_GROUP}/tenant-name"
PACK_LABEL = f"{API_GROUP}/pack"
ORG_DOMAIN_LABEL = f"{API_GROUP}/org-domain"

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

    ``postgres_admin_dsn_env`` names the env var holding a libpq DSN with CREATEROLE/CREATEDB on the
    shared Postgres; the reconciler mints a per-tenant role+database from it (database-per-tenant
    isolation). ``tenant_postgres_host`` is the ``host:port`` a tenant pod dials that database at.
    The blob and redis fields are the shared S3 bucket and Redis a tenant's core config points at —
    core isolates by ``workspace_id`` on every row and key. ``platform_secret`` is the Secret name
    carrying the model API keys and cloud credentials a tenant's ``serve`` reads from env; the owner
    creates it once in ``ufo-system`` and the reconciler replicates it into each tenant
    namespace during provisioning.
    """

    model_config = ConfigDict(extra="forbid")

    chart_path: Path
    registry: str

    postgres_admin_dsn_env: str = "UFO_CONTROL_POSTGRES_ADMIN_DSN"
    tenant_postgres_host: str

    redis_url: str

    blob_bucket: str
    blob_endpoint_url: str | None = None
    blob_region: str | None = None
    blob_s3_url: str | None = None
    blob_sts_role_arn: str | None = None
    blob_sts_endpoint: str | None = None
    blob_path_style: bool = False

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
