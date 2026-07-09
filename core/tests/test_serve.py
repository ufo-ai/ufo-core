from pathlib import Path

import pytest

from ufo import serve
from ufo.accounting import CORE_PRICING
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.proxy_serve import OWNER_DSN_ENV, model_rule_base
from ufo.sandbox.proxy.rules import ANTHROPIC_HOST, ScopeRule
from ufo.sandbox.session import EGRESS_CA_CERT_ENV

CA_PEM = "-----BEGIN CERTIFICATE-----\nshared\n-----END CERTIFICATE-----\n"
ANTHROPIC_KEY = "sk-ant-test"
LEAF_PEM_PREFIX = "-----BEGIN CERTIFICATE-----"
OWNER_LIBPQ_DSN = "postgresql://ufo_owner:pw@db.test/ufo"
OWNER_ASYNCPG_DSN = "postgresql+asyncpg://ufo_owner:pw@db.test/ufo"


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


def test_shared_owner_dsn_from_env_pins_the_async_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared fleet opens owner_tx's engine from UFO_OWNER_DSN (a secretKeyRef). The secret's
    contract is a plain libpq URL; serve pins the asyncpg driver its subject engine also dials so
    the owner engine bypasses RLS through the table owner rather than falling back to it."""
    monkeypatch.setenv(OWNER_DSN_ENV, OWNER_LIBPQ_DSN)
    assert serve._shared_owner_dsn(_local_config()) == OWNER_ASYNCPG_DSN


def test_shared_owner_dsn_falls_back_to_config_owner_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db", owner_url=OWNER_LIBPQ_DSN),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(backend="local", proxy_port=0),
    )
    assert serve._shared_owner_dsn(config) == OWNER_ASYNCPG_DSN


def test_shared_owner_dsn_fails_loud_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the owner DSN owner_tx falls back to the RLS-subject engine and the fleet-wide job
    enumeration reads an unset app.workspace_id GUC and crash-loops the dispatcher — fail loud at
    boot instead, naming the missing secret."""
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_DSN_ENV):
        serve._shared_owner_dsn(_local_config())


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
