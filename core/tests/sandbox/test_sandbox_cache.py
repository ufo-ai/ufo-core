import re
from pathlib import Path

import pytest

from ufo.sandbox.cache import CACHE_HOST, CACHE_PKG_HOSTS, cache_git_config, parse_cache_daemon


def test_parse_cache_daemon_splits_host_and_port() -> None:
    assert parse_cache_daemon("127.0.0.1:9110") == ("127.0.0.1", 9110)


def test_parse_cache_daemon_is_none_when_unset() -> None:
    assert parse_cache_daemon(None) is None


@pytest.mark.parametrize("value", ["nohost", ":9110", ""])
def test_parse_cache_daemon_fails_loud_on_a_malformed_address(value: str) -> None:
    with pytest.raises(ValueError):
        parse_cache_daemon(value)


def test_cache_git_config_routes_github_to_the_cache_and_keeps_pushes_direct() -> None:
    settings = dict(cache_git_config())
    assert settings[f"url.https://{CACHE_HOST}/git/github.com/.insteadOf"] == "https://github.com/"
    assert settings["url.https://github.com/.pushInsteadOf"] == "https://github.com/"


def test_the_pkg_host_allowlist_matches_the_daemons_default() -> None:
    """The proxy intercepts exactly the hosts the daemon will fetch: a host the proxy routes but the
    daemon refuses would 404 every fetch of it. Both default to one list — the proxy's Python and
    the daemon's Rust — so this gate keeps the two copies in step, as no deploy overrides them."""
    config_rs = Path(__file__).resolve().parents[3] / "servers" / "cache" / "src" / "config.rs"
    block = re.search(r"DEFAULT_PKG_HOSTS[^=]*=\s*&\[(.*?)\];", config_rs.read_text(), re.DOTALL)
    assert block is not None, "DEFAULT_PKG_HOSTS not found in servers/cache/src/config.rs"
    daemon_hosts = tuple(re.findall(r'"([^"]+)"', block.group(1)))
    assert daemon_hosts == CACHE_PKG_HOSTS
