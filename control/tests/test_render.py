import base64
import tomllib

from selfhost_k8s.contract import DeployRequest
from selfhost_k8s.platform import PlatformConfig
from selfhost_k8s.render import CONFIG_FILE, render_tenant

DIGEST = "sha256:" + "b" * 64
OPERATOR_CONFIG = """\
[pack]
name = "assistant"

[models]
reasoning_effort = "high"

[database]
url = "sqlite+aiosqlite:///dev.db"

[sandbox]
backend = "docker"
"""


def _request() -> DeployRequest:
    return DeployRequest.model_validate(
        {
            "tenant": {"name": "acme", "host": "acme.selfhost.app", "owner_email": "you@acme.com"},
            "bundle_image": {"repository": "ghcr.io/acme/selfhost", "digest": DIGEST},
            "sandbox_image": {"repository": "ghcr.io/acme/sandbox", "digest": DIGEST},
            "config_toml": OPERATOR_CONFIG,
            "pack": "assistant",
        }
    )


def _platform() -> PlatformConfig:
    return PlatformConfig.model_validate(
        {
            "chart_path": "/charts/selfhost-tenant",
            "registry": "ghcr.io/acme",
            "tenant_postgres_host": "pg.svc:5432",
            "redis_url": "redis://redis.svc:6379",
            "blob_bucket": "acme-blobs",
            "blob_region": "us-east-1",
        }
    )


def test_overlay_replaces_infra_and_preserves_operator_sections() -> None:
    render = render_tenant(_request(), _platform(), "postgresql+asyncpg://u:p@pg.svc:5432/db")
    config = tomllib.loads(render.secret.config_toml)

    assert config["serve"]["host"] == "0.0.0.0"
    assert config["database"]["url"] == "postgresql+asyncpg://u:p@pg.svc:5432/db"
    assert config["blob"] == {"backend": "s3", "bucket": "acme-blobs", "region": "us-east-1"}
    assert config["hub"] == {"backend": "redis", "url": "redis://redis.svc:6379"}
    assert config["connect"]["public_base_url"] == "https://acme.selfhost.app"
    # Operator-authored sections pass through untouched.
    assert config["pack"]["name"] == "assistant"
    assert config["models"]["reasoning_effort"] == "high"
    assert config["sandbox"]["backend"] == "docker"


def test_secret_carries_base64_config_and_minted_keys() -> None:
    render = render_tenant(_request(), _platform(), "dsn")
    data = render.secret.data()
    assert base64.b64decode(data[CONFIG_FILE]).decode() == render.secret.config_toml
    assert (
        base64.b64decode(data["SELFHOST_CREDENTIAL_KEY"]).decode() == render.secret.credential_key
    )
    assert len(render.secret.artifact_token_secret) == 64


def test_values_carry_digest_pinned_refs() -> None:
    render = render_tenant(_request(), _platform(), "dsn")
    assert render.values.namespace == "selfhost-acme"
    assert render.values.bundle_image == f"ghcr.io/acme/selfhost@{DIGEST}"
    assert render.values.cluster_issuer == "letsencrypt"
