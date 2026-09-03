from pathlib import Path

import pytest
from pydantic import ValidationError

from ufo.config import CONFIG_PATH_ENV, load_config
from ufo.sdk.http import plain_local

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


def test_terminal_defaults_to_the_in_process_backend(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.terminal.backend == "in_process"


def test_terminal_backend_parses(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[terminal]\nbackend = "redis"\n')
    config = load_config(path)
    assert config.terminal.backend == "redis"


def test_unknown_terminal_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[terminal]\nbackend = "redis"\nurl = "redis://cache:6379/0"\n')
    with pytest.raises(ValidationError):
        load_config(path)


def test_models_auto_default(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.models.auto_model == "claude-opus-5"


def test_models_auto_parse(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[models]\nauto_model = "claude-sonnet-5"\n')
    config = load_config(path)
    assert config.models.auto_model == "claude-sonnet-5"


def test_auto_model_rejects_the_auto_sentinel(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[models]\nauto_model = "auto"\n')
    with pytest.raises(ValidationError):
        load_config(path)


def test_background_jobs_model_defaults_to_the_cheap_one_shot_model(tmp_path: Path) -> None:
    """A background job is one bounded one-shot over a payload nothing re-reads, so it reads no
    cache and pays full price on every token. The default names the cheap model rather than the
    model a member's turn runs on — which stays `auto_model`, untouched by this knob."""
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.models.background_jobs_model == "gpt-5.6-luna"
    assert config.models.auto_model == "claude-opus-5"


def test_background_jobs_model_parse(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID + '\n[models]\nbackground_jobs_model = "claude-haiku-4-5"\n')
    config = load_config(path)
    assert config.models.background_jobs_model == "claude-haiku-4-5"


def test_background_jobs_model_rejects_the_auto_sentinel_and_the_empty_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ufo.toml"
    for rejected in ("auto", ""):
        path.write_text(VALID + f'\n[models]\nbackground_jobs_model = "{rejected}"\n')
        with pytest.raises(ValidationError):
            load_config(path)


def test_ingress_public_url_must_be_a_full_https_base(tmp_path: Path) -> None:
    """Every site's address is built by putting a label in front of this host, so a bare hostname or
    a path-only value would mint links to nowhere. `http` is refused for a reason of its own: a
    site carries a member's session, and a public deploy serving it over plain http hands that to
    the network. The one exempt host is `localhost`, which never leaves the machine — browsers
    resolve `http://*.localhost` to the loopback, so a zero-services dev run serves sites with no
    certificate. Caught at load, not at the first site."""
    path = tmp_path / "ufo.toml"
    for rejected in ("sites.example.com", "http://sites.example.com", "ws://sites.example.com"):
        path.write_text(VALID + f'\n[sandbox]\ningress_public_url = "{rejected}"\n')
        with pytest.raises(ValidationError):
            load_config(path)
    for accepted in (
        "https://sites.example.com",
        "http://localhost:8100",
        "http://ufo.localhost:8100",
        "https://localhost:8443",
    ):
        path.write_text(VALID + f'\n[sandbox]\ningress_public_url = "{accepted}"\n')
        assert load_config(path).sandbox.ingress_public_url == accepted


def test_plain_local_names_the_one_http_base_that_never_leaves_the_machine() -> None:
    for local in (
        "http://localhost:8710",
        "http://ufo.localhost:8710",
        "http://ufo-3.localhost:18280",
    ):
        assert plain_local(local) is True
    for other in (
        None,
        "",
        "https://ufo.localhost:8710",
        "http://ufo.example.com",
        "http://localhost.example.com",
        "ufo.localhost",
    ):
        assert plain_local(other) is False


def test_ingress_public_url_is_a_scheme_and_a_host_and_nothing_else(tmp_path: Path) -> None:
    """The knob's two readers take it apart differently — the ingress strips its `hostname` off each
    request's Host, `SurfaceContext.ingress_url` puts a label in front of its `netloc` — so anything
    beyond scheme and authority makes them disagree in silence: a path would vanish from every
    minted link, and userinfo would ride into the host the label goes in front of. A port is the one
    extra both readers survive, so it stays legal."""
    path = tmp_path / "ufo.toml"
    for rejected in (
        "https://sites.example.com/base",
        "https://sites.example.com/",
        "https://sites.example.com?x=1",
        "https://sites.example.com#f",
        "https://user:pw@sites.example.com",
    ):
        path.write_text(VALID + f'\n[sandbox]\ningress_public_url = "{rejected}"\n')
        with pytest.raises(ValidationError):
            load_config(path)
    path.write_text(VALID + '\n[sandbox]\ningress_public_url = "https://sites.example.com:8443"\n')
    assert load_config(path).sandbox.ingress_public_url == "https://sites.example.com:8443"


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
