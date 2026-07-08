from pathlib import Path

import pytest

from ufo.accounting import CORE_PRICING
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.proxy_serve import OWNER_DSN_ENV, ProxyServe, _egress_ca, _owner_dsn
from ufo.sandbox.proxy.rules import ANTHROPIC_HOST, ScopeRule, derive_model_rules
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, EGRESS_CA_KEY_ENV

ANTHROPIC_KEY = "sk-ant-test"


def _config(owner_url: str | None = "postgresql://owner@db/ufo") -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db", owner_url=owner_url),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
    )


def _proxy_serve(config: Config, manifests: tuple[Manifest, ...]) -> ProxyServe:
    return ProxyServe(
        config=config,
        manifests=manifests,
        owner_dsn="postgresql://owner@db/ufo",
        ca_cert="CA",
        ca_key="KEY",
        pricing=CORE_PRICING,
    )


def test_base_is_model_rules_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared proxy's static base is exactly the model-provider egress derived from the platform
    key in env — one ScopeRule for the provider host plus its sentinel→real injection and token
    meter, and nothing tenant-specific."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = _proxy_serve(_config(), ())._base()
    assert base == derive_model_rules("claude-opus-4-8", ANTHROPIC_KEY)
    scopes = {rule.allowed_hosts for rule in base if isinstance(rule, ScopeRule)}
    assert scopes == {frozenset({ANTHROPIC_HOST})}


def test_base_raises_on_an_injecting_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """An injecting credential slot in the active pack means per-tenant secret injection, which the
    one shared proxy cannot do — it fails loud naming the slot, checked before the model base so the
    diagnosis is the injection, not a missing key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    injecting = Manifest(
        name="inj",
        version="1",
        credentials=(
            CredentialSlot(
                name="byok",
                description="a per-tenant key the proxy would swap onto the wire",
                injection=InjectionTarget(
                    host="api.inj.test", header="authorization", sentinel="S"
                ),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="byok"):
        _proxy_serve(_config(), (injecting,))._base()


def test_base_raises_when_no_model_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no model provider key"):
        _proxy_serve(_config(), ())._base()


def test_egress_ca_reads_the_stable_pem_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, "CERT-PEM")
    monkeypatch.setenv(EGRESS_CA_KEY_ENV, "KEY-PEM")
    assert _egress_ca() == ("CERT-PEM", "KEY-PEM")


def test_egress_ca_fails_loud_when_a_half_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, "CERT-PEM")
    monkeypatch.delenv(EGRESS_CA_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=EGRESS_CA_KEY_ENV):
        _egress_ca()
    monkeypatch.delenv(EGRESS_CA_CERT_ENV, raising=False)
    with pytest.raises(RuntimeError, match=EGRESS_CA_CERT_ENV):
        _egress_ca()


def test_owner_dsn_prefers_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A k8s secretKeyRef injects the password-bearing DSN as UFO_OWNER_DSN, which wins over the
    (placeholder) config field the proxy config carries."""
    monkeypatch.setenv(OWNER_DSN_ENV, "postgresql://env-owner@db/ufo")
    assert _owner_dsn(_config(owner_url="postgresql://config-owner@db/ufo")) == (
        "postgresql://env-owner@db/ufo"
    )


def test_owner_dsn_falls_back_to_the_config_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    assert _owner_dsn(_config(owner_url="postgresql://owner@db/ufo")) == "postgresql://owner@db/ufo"


def test_owner_dsn_fails_loud_when_env_and_config_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_DSN_ENV):
        _owner_dsn(_config(owner_url=None))
