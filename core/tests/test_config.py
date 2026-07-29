from pathlib import Path

import pytest
from pydantic import ValidationError

from ufo.config import CONFIG_PATH_ENV, load_config

VALID = """
[database]
url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"
"""


def test_load_config_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.database.system_url == "sqlite:///ufo_dbos.db"
    assert config.blob.root == Path("./blobs")
    assert config.serve.port == 8710
    assert config.serve.request_shutdown_seconds == 30
    assert config.serve.graceful_shutdown_seconds == 0
    assert config.models.anthropic_api_key_env == "ANTHROPIC_API_KEY"
    assert config.sandbox.workspace_root == Path("./workspaces")
    assert config.o11y.otlp_endpoint is None


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.toml")


def test_unknown_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + "\n[mystery]\nknob = 1\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_negative_graceful_shutdown_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + "\n[serve]\ngraceful_shutdown_seconds = -1\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_negative_request_shutdown_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + "\n[serve]\nrequest_shutdown_seconds = -1\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_filesystem_backend_requires_root(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID.replace('root = "./blobs"', ""))
    with pytest.raises(ValidationError):
        load_config(path)


def test_s3_backend_requires_bucket(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID.replace('backend = "filesystem"\nroot = "./blobs"', 'backend = "s3"'))
    with pytest.raises(ValidationError):
        load_config(path)


def test_s3_backend_parses(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(
        VALID.replace(
            'backend = "filesystem"\nroot = "./blobs"',
            'backend = "s3"\nbucket = "b"\nendpoint_url = "https://s3.example:9000"\n'
            'region = "us-west-2"',
        )
    )
    config = load_config(path)
    assert config.blob.bucket == "b"
    assert config.blob.endpoint_url == "https://s3.example:9000"
    assert config.blob.region == "us-west-2"


def test_config_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "elsewhere.toml"
    path.write_text(VALID)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(path))
    assert load_config().serve.host == "127.0.0.1"


def test_ext_defaults_to_off(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    assert load_config(path).ext.store is None


def test_pack_defaults_to_none(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    assert load_config(path).pack.name is None


def test_pack_name_selects_the_active_pack(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[pack]\nname = "assistant"\n')
    assert load_config(path).pack.name == "assistant"


def test_ext_store_parses(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[ext]\nstore = "extensions.catalog.toml"\n')
    assert load_config(path).ext.store == Path("extensions.catalog.toml")


def test_hub_defaults_to_the_in_process_backend(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.hub.backend == "in_process"
    assert config.hub.url is None


def test_hub_backend_and_url_parse(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[hub]\nbackend = "redis"\nurl = "redis://cache:6379/0"\n')
    config = load_config(path)
    assert config.hub.backend == "redis"
    assert config.hub.url == "redis://cache:6379/0"


def test_unknown_hub_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + "\n[hub]\nshared = true\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_models_reasoning_and_auto_default(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.models.reasoning_effort == "high"
    assert config.models.auto_model == "claude-opus-5"


def test_models_reasoning_and_auto_parse(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(
        VALID + '\n[models]\nreasoning_effort = "off"\nauto_model = "claude-sonnet-5"\n'
    )
    config = load_config(path)
    assert config.models.reasoning_effort == "off"
    assert config.models.auto_model == "claude-sonnet-5"


def test_unknown_reasoning_effort_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[models]\nreasoning_effort = "turbo"\n')
    with pytest.raises(ValidationError):
        load_config(path)


def test_auto_model_rejects_the_auto_sentinel(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[models]\nauto_model = "auto"\n')
    with pytest.raises(ValidationError):
        load_config(path)


def test_postgres_system_url_uses_sync_driver(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(
        VALID.replace(
            'url = "sqlite+aiosqlite:///ufo.db"',
            'url = "postgresql+asyncpg://u:p@db:5432/ufo"',
        )
    )
    assert load_config(path).database.system_url == "postgresql+psycopg://u:p@db:5432/ufo_dbos"


def test_explicit_system_url_wins_over_derivation(tmp_path: Path) -> None:
    """An explicitly located DBOS store wins over the derived sibling."""
    path = tmp_path / "ufo.toml"
    path.write_text(
        VALID.replace(
            'url = "sqlite+aiosqlite:///ufo.db"',
            'url = "postgresql+asyncpg://u:p@db:5432/ufo"\n'
            'system_url = "postgresql+psycopg://u:p@db:5432/workflows"',
        )
    )
    assert load_config(path).database.system_url == "postgresql+psycopg://u:p@db:5432/workflows"
