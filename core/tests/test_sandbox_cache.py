import pytest

from ufo.sandbox.cache import CACHE_HOST, cache_git_config, parse_cache_daemon


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
