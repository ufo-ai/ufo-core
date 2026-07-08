from pathlib import Path

import pytest

from ufo import serve
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.sandbox.session import EGRESS_CA_CERT_ENV

CA_PEM = "-----BEGIN CERTIFICATE-----\nshared\n-----END CERTIFICATE-----\n"


def _config() -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(
            backend="local", proxy_port=9443, proxy_public_url="https://proxy.test"
        ),
    )


def test_proxy_endpoint_is_built_from_config_and_the_shared_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """serve no longer runs the proxy: it derives the endpoint carriers thread into every sandbox
    from config — the stable port and off-cluster dial-back base — plus the shared CA cert from env,
    a plain value object with no bound socket."""
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, CA_PEM)
    endpoint = serve._proxy_endpoint(_config())
    assert (endpoint.port, endpoint.ca_cert, endpoint.public_url) == (
        9443,
        CA_PEM,
        "https://proxy.test",
    )


def test_proxy_endpoint_fails_loud_without_the_shared_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EGRESS_CA_CERT_ENV, raising=False)
    with pytest.raises(RuntimeError, match=EGRESS_CA_CERT_ENV):
        serve._proxy_endpoint(_config())


def test_serve_no_longer_starts_an_in_process_proxy() -> None:
    """The in-process proxy machinery moved to `ufoctl proxy`: serve keeps neither the starter nor
    the per-turn resolver it fed."""
    for gone in ("_egress_proxy", "_resolver", "_model_rules", "_credential_rules"):
        assert not hasattr(serve, gone)
