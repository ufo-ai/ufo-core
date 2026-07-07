"""Turn a ``DeployRequest`` + provisioned Postgres into the tenant's rendered config + Helm values.

This is the pure, cluster-free heart of provisioning — the piece unit tests exercise without a
cluster. The reconciler calls ``render_tenant`` once it has a tenant database DSN; the result is a
tenant Secret (the minted keys + the final ``selfhost.toml``, which carries the DB password so it is
a Secret, never a ConfigMap) and the non-secret Helm values.

The overlay is the RFC 0004 "Fills core's config knob" column: the operator authored ``config_toml``
with dev placeholders; here the infra sections (``database``/``blob``/``hub``/``connect``/``serve``)
are replaced with what the control plane provisions, while the operator's own sections (``models``,
``pack``, ``sandbox``, ``sources``, …) pass through untouched.
"""

import base64
import secrets
import tomllib
from dataclasses import dataclass

import tomli_w
from pydantic import BaseModel, ConfigDict

from selfhost_k8s.contract import SECRET_ARTIFACT_TOKEN, SECRET_CREDENTIAL_KEY, DeployRequest
from selfhost_k8s.platform import SERVE_PORT, PlatformConfig

CONFIG_FILE = "selfhost.toml"


class TenantChartValues(BaseModel):
    """The non-secret values the tenant Helm chart renders from. No secret ever rides a values file
    — the minted keys and the config live in the Secret the reconciler applies out of band, which
    the workload references by ``tenant_secret`` name."""

    model_config = ConfigDict(extra="forbid")
    namespace: str
    tenant_name: str
    pack: str
    host: str
    owner_email: str
    bundle_image: str
    sandbox_image: str
    serve_port: int = SERVE_PORT
    tenant_secret: str
    platform_secret: str
    ingress_class: str
    cluster_issuer: str


@dataclass(frozen=True)
class TenantSecret:
    """The per-tenant Secret payload: the config file (with its DB DSN) plus the minted keys."""

    config_toml: str
    credential_key: str
    artifact_token_secret: str

    def data(self) -> dict[str, str]:
        raw = {
            CONFIG_FILE: self.config_toml,
            SECRET_CREDENTIAL_KEY: self.credential_key,
            SECRET_ARTIFACT_TOKEN: self.artifact_token_secret,
        }
        return {key: base64.b64encode(value.encode()).decode() for key, value in raw.items()}


@dataclass(frozen=True)
class TenantRender:
    values: TenantChartValues
    secret: TenantSecret


def mint_fernet_key() -> str:
    """A Fernet key is url-safe base64 of 32 random bytes — core seals its BYOK store with it."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def render_tenant(
    request: DeployRequest, platform: PlatformConfig, tenant_dsn: str
) -> TenantRender:
    config_toml = _overlay_config(request, platform, tenant_dsn)
    secret = TenantSecret(
        config_toml=config_toml,
        credential_key=mint_fernet_key(),
        artifact_token_secret=secrets.token_hex(32),
    )
    values = TenantChartValues(
        namespace=request.tenant.namespace,
        tenant_name=request.tenant.name,
        pack=request.pack,
        host=request.tenant.host,
        owner_email=request.tenant.owner_email,
        bundle_image=request.bundle_image.ref,
        sandbox_image=request.sandbox_image.ref,
        tenant_secret="selfhost-tenant",
        platform_secret=platform.platform_secret,
        ingress_class=platform.ingress_class,
        cluster_issuer=platform.cluster_issuer,
    )
    return TenantRender(values=values, secret=secret)


def _overlay_config(request: DeployRequest, platform: PlatformConfig, tenant_dsn: str) -> str:
    config = tomllib.loads(request.config_toml)
    config["serve"] = {**config.get("serve", {}), "host": "0.0.0.0", "port": SERVE_PORT}
    config["database"] = {"url": tenant_dsn}
    config["blob"] = _blob_section(platform)
    config["hub"] = {"backend": "redis", "url": platform.redis_url}
    config["connect"] = {"public_base_url": f"https://{request.tenant.host}"}
    if platform.otlp_endpoint is not None:
        config["o11y"] = {"otlp_endpoint": platform.otlp_endpoint}
    return tomli_w.dumps(config)


def _blob_section(platform: PlatformConfig) -> dict[str, object]:
    section: dict[str, object] = {"backend": "s3", "bucket": platform.blob_bucket}
    optional = {
        "endpoint_url": platform.blob_endpoint_url,
        "region": platform.blob_region,
        "s3_url": platform.blob_s3_url,
        "sts_role_arn": platform.blob_sts_role_arn,
        "sts_endpoint": platform.blob_sts_endpoint,
    }
    section.update({key: value for key, value in optional.items() if value is not None})
    if platform.blob_path_style:
        section["path_style"] = True
    return section
