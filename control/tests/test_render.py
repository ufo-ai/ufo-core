import base64
import tomllib

import pytest
from ufo.deploy import DeployRequest

from ufo_control.platform import PlatformConfig
from ufo_control.postgres import TenantPostgres
from ufo_control.render import CONFIG_FILE, render_tenant

DIGEST = "sha256:" + "b" * 64
WORKSPACE_ID = "3f8c1e2a-0b4d-4c6e-9a1f-2b3c4d5e6f70"
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
E2B_CONFIG = '[pack]\nname = "assistant"\n\n[sandbox]\nbackend = "e2b"\n'


def _request(**overrides: object) -> DeployRequest:
    base: dict[str, object] = {
        "tenant": {"name": "acme", "host": "acme.ufo.app", "owner_email": "you@acme.com"},
        "bundle_image": {"repository": "ghcr.io/acme/ufo", "digest": DIGEST},
        "sandbox_image": {"repository": "ghcr.io/acme/sandbox", "digest": DIGEST},
        "config_toml": OPERATOR_CONFIG,
        "pack": "assistant",
    }
    base.update(overrides)
    return DeployRequest.model_validate(base)


def _platform() -> PlatformConfig:
    return PlatformConfig.model_validate(
        {
            "chart_path": "/charts/ufo-tenant",
            "registry": "ghcr.io/acme",
            "tenant_postgres_host": "pg.svc:5432",
            "redis_url": "redis://redis.svc:6379",
            "blob_bucket": "acme-blobs",
            "blob_region": "us-east-1",
        }
    )


def _platform_with_proxy() -> PlatformConfig:
    return PlatformConfig.model_validate(
        {
            "chart_path": "/charts/ufo-tenant",
            "registry": "ghcr.io/acme",
            "tenant_postgres_host": "pg.svc:5432",
            "redis_url": "redis://redis.svc:6379",
            "blob_bucket": "acme-blobs",
            "blob_region": "us-east-1",
            "blob_s3_url": "https://s3.us-east-1.amazonaws.com",
            "sandbox_proxy_url": "http://sandbox-proxy.ufo.app:8888",
            "serve_role_arn": "arn:aws:iam::123456789012:role/ufo-testing-app-s3",
            "otlp_endpoint": "http://otel-collector.ufo-system.svc.cluster.local:4318",
        }
    )


def _database_postgres() -> TenantPostgres:
    return TenantPostgres(url="postgresql+asyncpg://u:p@pg.svc:5432/ufo_acme")


def _rls_postgres() -> TenantPostgres:
    return TenantPostgres(
        url="postgresql+asyncpg://ufo_t_acme:pw@pg.svc:5432/ufo",
        system_url="postgresql+psycopg://ufo_t_acme:pw@pg.svc:5432/ufo_dbos_acme",
        workspace_id=WORKSPACE_ID,
    )


def test_overlay_replaces_infra_and_preserves_operator_sections() -> None:
    render = render_tenant(_request(), _platform(), _database_postgres())
    config = tomllib.loads(render.secret.config_toml)

    assert config["serve"]["host"] == "0.0.0.0"
    assert config["database"]["url"] == "postgresql+asyncpg://u:p@pg.svc:5432/ufo_acme"
    assert config["blob"] == {"backend": "s3", "bucket": "acme-blobs", "region": "us-east-1"}
    assert config["hub"] == {"backend": "redis", "url": "redis://redis.svc:6379"}
    assert config["connect"]["public_base_url"] == "https://acme.ufo.app"
    # Operator-authored sections pass through untouched.
    assert config["pack"]["name"] == "assistant"
    assert config["models"]["reasoning_effort"] == "high"
    assert config["sandbox"]["backend"] == "docker"


def test_overlay_writes_the_sandbox_proxy_url_and_preserves_backend() -> None:
    render = render_tenant(
        _request(config_toml=E2B_CONFIG, sandbox_image=None),
        _platform_with_proxy(),
        _database_postgres(),
    )
    config = tomllib.loads(render.secret.config_toml)
    # The operator's backend passes through; the off-cluster proxy URL is overlaid beside it.
    assert config["sandbox"]["backend"] == "e2b"
    assert config["sandbox"]["proxy_public_url"] == "http://sandbox-proxy.ufo.app:8888"
    # The chart publishes the sandbox-proxy LoadBalancer at the proxy URL's host.
    assert render.values.sandbox_proxy_hostname == "sandbox-proxy.ufo.app"


def test_overlay_includes_blob_s3_url_when_the_platform_sets_it() -> None:
    # The s3fs workspace mount minter fails loud at boot without blob.s3_url; the platform sets the
    # sandbox-reachable S3 endpoint so the mount initializes.
    render = render_tenant(_request(), _platform_with_proxy(), _database_postgres())
    config = tomllib.loads(render.secret.config_toml)
    assert config["blob"]["s3_url"] == "https://s3.us-east-1.amazonaws.com"


def test_overlay_omits_the_proxy_when_the_platform_leaves_it_unset() -> None:
    # The default _platform() sets no sandbox_proxy_url: an in-cluster backend gets no proxy knob
    # and no LoadBalancer hostname, so the operator's [sandbox] section passes through untouched.
    render = render_tenant(_request(), _platform(), _database_postgres())
    config = tomllib.loads(render.secret.config_toml)
    assert "proxy_public_url" not in config["sandbox"]
    assert render.values.sandbox_proxy_hostname == ""


def test_overlay_points_serve_at_the_platform_otlp_collector() -> None:
    # The platform's collector endpoint becomes the tenant's [o11y] otlp_endpoint, so serve's
    # init_o11y exports OTLP to the shared in-cluster collector.
    render = render_tenant(_request(), _platform_with_proxy(), _database_postgres())
    config = tomllib.loads(render.secret.config_toml)
    assert (
        config["o11y"]["otlp_endpoint"] == "http://otel-collector.ufo-system.svc.cluster.local:4318"
    )


def test_overlay_omits_o11y_when_the_platform_leaves_the_collector_unset() -> None:
    # The default _platform() sets no otlp_endpoint, so serve keeps its no-op OTel defaults.
    render = render_tenant(_request(), _platform(), _database_postgres())
    config = tomllib.loads(render.secret.config_toml)
    assert "o11y" not in config


def test_serve_role_arn_rides_the_values_for_the_sa_annotation() -> None:
    # The platform's serve_role_arn becomes the chart value the ufo-serve SA is annotated with, so
    # the pod assumes the IRSA role for blob access + the sandbox-fs mount mint.
    render = render_tenant(_request(), _platform_with_proxy(), _database_postgres())
    assert render.values.serve_role_arn == "arn:aws:iam::123456789012:role/ufo-testing-app-s3"


def test_serve_role_arn_defaults_empty_without_platform_irsa() -> None:
    # The default _platform() supplies no IRSA role: the SA is annotation-free and the chart's
    # `{{- if .Values.serve_role_arn }}` guard drops the annotation block.
    render = render_tenant(_request(), _platform(), _database_postgres())
    assert render.values.serve_role_arn == ""


def test_database_tier_omits_system_url_and_workspace_id() -> None:
    render = render_tenant(_request(), _platform(), _database_postgres())
    config = tomllib.loads(render.secret.config_toml)
    # The database tier lets core derive the _dbos sibling and mint the workspace uuid itself.
    assert "system_url" not in config["database"]
    assert render.values.workspace_id is None


def test_rls_tier_emits_shared_db_dsn_system_url_and_workspace_id() -> None:
    render = render_tenant(_request(), _platform(), _rls_postgres())
    config = tomllib.loads(render.secret.config_toml)
    assert config["database"]["url"] == "postgresql+asyncpg://ufo_t_acme:pw@pg.svc:5432/ufo"
    assert config["database"]["system_url"] == (
        "postgresql+psycopg://ufo_t_acme:pw@pg.svc:5432/ufo_dbos_acme"
    )
    # The workspace uuid rides the chart values → the init Job's --workspace-id.
    assert render.values.workspace_id == WORKSPACE_ID


def test_secret_carries_base64_config_and_minted_keys() -> None:
    render = render_tenant(_request(), _platform(), _database_postgres())
    data = render.secret.data()
    assert base64.b64decode(data[CONFIG_FILE]).decode() == render.secret.config_toml
    assert base64.b64decode(data["UFO_CREDENTIAL_KEY"]).decode() == render.secret.credential_key
    assert len(render.secret.artifact_token_secret) == 64


def test_values_carry_digest_pinned_refs() -> None:
    render = render_tenant(_request(), _platform(), _database_postgres())
    assert render.values.namespace == "ufo-acme"
    assert render.values.bundle_image == f"ghcr.io/acme/ufo@{DIGEST}"
    assert render.values.sandbox_image == f"ghcr.io/acme/sandbox@{DIGEST}"
    assert render.values.cluster_issuer == "letsencrypt"


def test_e2b_backend_renders_an_empty_sandbox_image() -> None:
    # The e2b carrier pulls no image: the request omits sandbox_image and the chart value is "".
    render = render_tenant(
        _request(config_toml=E2B_CONFIG, sandbox_image=None), _platform(), _database_postgres()
    )
    assert render.values.sandbox_image == ""


def test_image_backed_backend_without_sandbox_image_fails_loud() -> None:
    # OPERATOR_CONFIG selects the docker carrier, which pulls the sandbox image, so a request
    # missing sandbox_image cannot render.
    with pytest.raises(RuntimeError, match="sandbox_image"):
        render_tenant(_request(sandbox_image=None), _platform(), _database_postgres())
