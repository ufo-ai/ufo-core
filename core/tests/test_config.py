from pathlib import Path

import pytest
from pydantic import ValidationError

from selfhost.config import CONFIG_PATH_ENV, load_config

VALID = """
[postgres]
url = "postgresql+psycopg://selfhost:selfhost@127.0.0.1:5541/selfhost"

[blob]
backend = "filesystem"
root = "./blobs"
"""


def test_load_config_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "selfhost.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.postgres.system_url.endswith("/selfhost_dbos")
    assert config.blob.root == Path("./blobs")
    assert config.serve.port == 8710
    assert config.models.anthropic_api_key_env == "ANTHROPIC_API_KEY"
    assert config.o11y.otlp_endpoint is None


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.toml")


def test_unknown_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "selfhost.toml"
    path.write_text(VALID + "\n[mystery]\nknob = 1\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_filesystem_backend_requires_root(tmp_path: Path) -> None:
    path = tmp_path / "selfhost.toml"
    path.write_text(VALID.replace('root = "./blobs"', ""))
    with pytest.raises(ValidationError):
        load_config(path)


def test_s3_backend_requires_bucket(tmp_path: Path) -> None:
    path = tmp_path / "selfhost.toml"
    path.write_text(VALID.replace('backend = "filesystem"\nroot = "./blobs"', 'backend = "s3"'))
    with pytest.raises(ValidationError):
        load_config(path)


def test_config_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "elsewhere.toml"
    path.write_text(VALID)
    monkeypatch.setenv(CONFIG_PATH_ENV, str(path))
    assert load_config().serve.host == "127.0.0.1"
