import base64
from collections.abc import Callable
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from typing import cast

import pytest
import urllib3

SBX_PATH = Path(__file__).parents[2] / "src" / "ufo" / "sandbox" / "image" / "sbx"


def _sbx() -> dict[str, object]:
    loader = SourceFileLoader("ufo_test_sbx", str(SBX_PATH))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module.__dict__


def test_http_pool_encrypts_the_proxy_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "https://run-token:@sandbox-proxy.test")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    http_pool = cast(Callable[[], urllib3.PoolManager], _sbx()["http_pool"])

    pool = http_pool()

    assert isinstance(pool, urllib3.ProxyManager)
    assert pool.proxy.url == "https://sandbox-proxy.test:443"
    encoded = pool.proxy_headers["proxy-authorization"].removeprefix("Basic ")
    assert base64.b64decode(encoded).decode() == "run-token:"
    assert pool.proxy_ssl_context is pool.connection_pool_kw["ssl_context"]
    pool.clear()


def test_post_json_uses_the_configured_pool_and_closes_it() -> None:
    scope = _sbx()
    captured: dict[str, object] = {}

    class Pool:
        def request(self, method: str, url: str, **kwargs: object) -> object:
            captured.update(method=method, url=url, **kwargs)
            return type("Response", (), {"status": 200, "data": b'{"content": []}'})()

        def clear(self) -> None:
            captured["cleared"] = True

    scope["http_pool"] = Pool
    post_json = cast(
        Callable[[str, dict[str, str], dict[str, object]], dict[str, object]],
        scope["post_json"],
    )

    result = post_json("https://api.test/messages", {"x-api-key": "sentinel"}, {"value": 1})

    assert result == {"content": []}
    assert captured == {
        "method": "POST",
        "url": "https://api.test/messages",
        "body": b'{"value": 1}',
        "headers": {"content-type": "application/json", "x-api-key": "sentinel"},
        "timeout": 60.0,
        "retries": False,
        "cleared": True,
    }
