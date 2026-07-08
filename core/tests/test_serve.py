from pathlib import Path

import pytest

from ufo import serve
from ufo.accounting import CORE_PRICING
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.proxy_serve import model_rule_base
from ufo.sandbox.proxy.rules import ANTHROPIC_HOST, ScopeRule
from ufo.sandbox.session import EGRESS_CA_CERT_ENV

CA_PEM = "-----BEGIN CERTIFICATE-----\nshared\n-----END CERTIFICATE-----\n"
ANTHROPIC_KEY = "sk-ant-test"
LEAF_PEM_PREFIX = "-----BEGIN CERTIFICATE-----"


def _hosted_config() -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(
            backend="local", proxy_port=9443, proxy_public_url="https://proxy.test"
        ),
    )


def _local_config() -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(backend="local", proxy_port=0),
    )


def test_hosted_proxy_endpoint_is_built_from_config_and_the_shared_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `proxy_public_url` set the proxy runs as standalone `ufoctl proxy`: serve derives the
    endpoint carriers thread into every sandbox from config — the stable port and off-cluster
    dial-back base — plus the shared CA cert from env, a plain value object with no bound socket."""
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, CA_PEM)
    endpoint = serve._proxy_endpoint(_hosted_config(), (), None, CORE_PRICING)
    assert (endpoint.port, endpoint.ca_cert, endpoint.public_url) == (
        9443,
        CA_PEM,
        "https://proxy.test",
    )


def test_hosted_proxy_endpoint_fails_loud_without_the_shared_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EGRESS_CA_CERT_ENV, raising=False)
    with pytest.raises(RuntimeError, match=EGRESS_CA_CERT_ENV):
        serve._proxy_endpoint(_hosted_config(), (), None, CORE_PRICING)


def test_local_proxy_mints_an_ephemeral_ca_and_needs_no_shared_ca_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no `proxy_public_url` (local, single-node) serve runs the proxy in-process on its own
    loop and mints an ephemeral CA — no `UFO_EGRESS_CA_CERT` to source. The returned endpoint binds
    a real ephemeral port and carries the freshly minted CA, with no off-cluster dial-back base."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(EGRESS_CA_CERT_ENV, raising=False)
    endpoint = serve._proxy_endpoint(_local_config(), (), None, CORE_PRICING)
    assert endpoint.public_url is None
    assert endpoint.port != 0
    assert endpoint.ca_cert.startswith(LEAF_PEM_PREFIX)


async def test_local_rule_base_is_the_model_base_when_no_slot_injects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No injecting slot means the local base is exactly the shared model-provider egress — the
    single-node proxy carries a workspace's credential rules only when a slot needs injection."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = _local_config()
    base = await serve._local_rule_base(config, (), None)
    assert base == model_rule_base(config)
    assert {rule.allowed_hosts for rule in base if isinstance(rule, ScopeRule)} == {
        frozenset({ANTHROPIC_HOST})
    }


async def test_local_rule_base_fails_loud_on_an_injecting_slot_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local proxy injects a workspace's own credential secrets, but only if the credential key
    is set — an injecting slot with no key fails loud rather than shipping a sandbox that cannot
    reach the slot's host."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    slot = CredentialSlot(
        name="byok",
        description="a per-tenant key the local proxy swaps onto the wire",
        injection=InjectionTarget(host="api.inj.test", header="authorization", sentinel="S"),
    )
    manifest = Manifest(name="inj", version="1", credentials=(slot,))
    with pytest.raises(RuntimeError, match="UFO_CREDENTIAL_KEY"):
        await serve._local_rule_base(_local_config(), (manifest,), None)
