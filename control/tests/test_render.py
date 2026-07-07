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
