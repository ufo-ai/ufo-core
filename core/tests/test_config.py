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
    assert config.models.auto_model == "claude-opus-5-5"


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
    cache and pays full price on every token."""
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.models.background_jobs_model == "gpt-5.6-luna"
    assert config.models.auto_model == "claude-opus-5-5"


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
    """Every site's address is built by putting a label in front of this host, so a bare hostname
    or a path-only value would mint links to nowhere."""
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


def test_apps_dev_server_is_an_http_origin_and_nothing_else(tmp_path: Path) -> None:
    """The ingress puts a shipped page's own path after this value, so anything beyond scheme and
    authority would be silently folded into every relayed URL."""
    path = tmp_path / "ufo.toml"
    for rejected in (
        "web:5174",
        "ws://web:5174",
        "http://web:5174/",
        "http://web:5174/apps",
        "http://web:5174?x=1",
        "http://user@web:5174",
    ):
        path.write_text(VALID + f'\n[sandbox]\napps_dev_server = "{rejected}"\n')
        with pytest.raises(ValidationError):
            load_config(path)
    for accepted in ("http://web:5174", "https://apps.example.com"):
        path.write_text(VALID + f'\n[sandbox]\napps_dev_server = "{accepted}"\n')
        assert load_config(path).sandbox.apps_dev_server == accepted
    path.write_text(VALID)
    assert load_config(path).sandbox.apps_dev_server is None


def test_ingress_public_url_is_a_scheme_and_a_host_and_nothing_else(tmp_path: Path) -> None:
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


def test_a_deploy_signs_no_browser_in_and_builds_no_app_page_unless_configured(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.serve.sign_in_path is None
    assert config.serve.gateway_prefixes == ()
    assert config.sites.page_kit is None
    path.write_text(
        VALID + '\n[serve]\nsign_in_path = "/login"\ngateway_prefixes = ["/login", "/logout"]\n'
        '\n[sites]\npage_kit = "bundle/kit.tar.gz"\n'
    )
    config = load_config(path)
    assert config.serve.sign_in_path == "/login"
    assert config.serve.gateway_prefixes == ("/login", "/logout")
    assert config.sites.page_kit == Path("bundle/kit.tar.gz")


def test_sign_in_and_gateway_paths_are_paths_on_the_deploys_own_host(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    for rejected in (
        "login",
        "https://gateway.example.com/login",
        "//gateway.example.com",
        "/login?debug=1",
        "/login#a",
    ):
        for key in ("sign_in_path", "gateway_prefixes"):
            value = f'"{rejected}"' if key == "sign_in_path" else f'["{rejected}"]'
            path.write_text(VALID + f"\n[serve]\n{key} = {value}\n")
            with pytest.raises(ValidationError, match="paths on this deploy's host"):
                load_config(path)


def test_a_sites_sign_in_link_and_generic_card_are_unset_unless_configured(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    sites = load_config(path).sites
    assert (sites.sign_in_url, sites.share_card_url) == (None, None)
    path.write_text(
        VALID + '\n[serve]\nsign_in_path = "/login"\n'
        '\n[sites]\nsign_in_url = "/logout"\n'
        'share_card_url = "https://cdn.example.com/share/og-site.jpg"\n'
    )
    sites = load_config(path).sites
    assert (sites.sign_in_url, sites.share_card_url) == (
        "/logout",
        "https://cdn.example.com/share/og-site.jpg",
    )


def test_a_sites_sign_in_link_is_a_path_and_needs_the_deploys_sign_in(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    serve = '\n[serve]\nsign_in_path = "/login"\n'
    for rejected in (
        "logout",
        "//gateway.example.com/logout",
        "https://gateway.example.com/logout",
        "/logout?next=sites",
        "/logout#sites",
    ):
        path.write_text(VALID + serve + f'\n[sites]\nsign_in_url = "{rejected}"\n')
        with pytest.raises(ValidationError, match=r"sites\.sign_in_url is a path"):
            load_config(path)
    path.write_text(VALID + '\n[sites]\nsign_in_url = "/logout"\n')
    with pytest.raises(ValidationError, match=r"serve\.sign_in_path is not"):
        load_config(path)


def test_a_sites_generic_card_is_an_absolute_web_url(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    for rejected in (
        "/share/og-site.jpg",
        "cdn.example.com/og.jpg",
        "ftp://cdn.example.com/og.jpg",
    ):
        path.write_text(VALID + f'\n[sites]\nshare_card_url = "{rejected}"\n')
        with pytest.raises(ValidationError, match=r"sites\.share_card_url is an absolute"):
            load_config(path)


def test_operator_and_debugger_sections_default_to_the_built_in_rule_and_no_links(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(VALID)
    config = load_config(path)
    assert config.operator.rule == "seated_admin"
    assert config.debugger.turn_urls == {}
    assert config.debugger.conversation_urls == {}


def test_debugger_links_take_only_their_placeholders(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(
        VALID
        + '\n[debugger.turn_urls]\nTrace = "https://t.test/{trace_id}?from={start_ms}&to={end_ms}"\n'
        + 'Calls = "https://t.test/c/{conversation_id}"\n'
        + '[debugger.conversation_urls]\nchat = ["https://c.test/{installation[1]}/{address[0]}", '
        + '"https://c.test/{installation}/{address}"]\n'
    )
    debugger = load_config(path).debugger
    assert list(debugger.turn_urls) == ["Trace", "Calls"]
    assert debugger.conversation_urls == {
        "chat": (
            "https://c.test/{installation[1]}/{address[0]}",
            "https://c.test/{installation}/{address}",
        )
    }
    for section in (
        '[debugger.turn_urls]\nTrace = "https://t.test/{address}"\n',
        '[debugger.conversation_urls]\nchat = ["https://c.test/{trace_id}"]\n',
        '[debugger.conversation_urls]\nchat = ["https://c.test/{address[x]}"]\n',
        '[debugger.conversation_urls]\nchat = ["https://c.test/{address[0][1]}"]\n',
        '[debugger.conversation_urls]\nchat = ["https://c.test/{address.real}"]\n',
        '[debugger.conversation_urls]\nchat = "https://c.test/{address}"\n',
        '[debugger.turn_urls]\nTrace = "https://t.test/{trace_id!r}"\n',
        '[debugger.turn_urls]\nTrace = "https://t.test/{trace_id:>9}"\n',
        '[debugger.turn_urls]\nTrace = "https://t.test/{}"\n',
        '[debugger.turn_urls]\nTrace = "https://t.test/{trace_id"\n',
    ):
        path.write_text(VALID + "\n" + section)
        with pytest.raises(ValidationError):
            load_config(path)
