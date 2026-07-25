from pathlib import Path
from types import MethodType

import pytest

from ufo import proxy_serve as proxy_serve_module
from ufo.config import BlobConfig, Config, DatabaseConfig, load_config
from ufo.ext.loader import load_manifests
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.models.catalog import CORE_PRICING
from ufo.proxy_serve import OWNER_DSN_ENV, ProxyServe, _egress_ca, _owner_dsn, model_rule_base
from ufo.sandbox.proxy.rules import ANTHROPIC_HOST, ScopeRule, derive_model_rules
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, EGRESS_CA_KEY_ENV

ANTHROPIC_KEY = "sk-ant-test"


def _config(owner_url: str | None = "postgresql://owner@db/ufo") -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db", owner_url=owner_url),
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


def test_model_rule_base_derives_provider_egress_from_the_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared model-rule builder — the one both `ProxyServe` and serve's in-process local proxy
    call — turns each configured provider whose key is set into its reachable host, sentinel→real
    injection, and token meter; no key set anywhere fails loud."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert model_rule_base(_config()) == derive_model_rules("claude-opus-4-8", ANTHROPIC_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no model provider key"):
        model_rule_base(_config())


def test_base_is_model_rules_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared proxy's static base is exactly the model-provider egress derived from the process
    key in env — one ScopeRule for the provider host plus its sentinel→real injection and token
    meter, and no workspace-specific secret."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = _proxy_serve(_config(), ())._base()
    assert base == derive_model_rules("claude-opus-4-8", ANTHROPIC_KEY)
    scopes = {rule.allowed_hosts for rule in base if isinstance(rule, ScopeRule)}
    assert scopes == {frozenset({ANTHROPIC_HOST})}


def test_base_raises_on_an_injecting_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """An injecting credential slot in the active pack means workspace secret injection, which the
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
                description="a workspace key the proxy would swap onto the wire",
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


def test_owner_dsn_prefers_the_env_and_pins_the_async_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The password-bearing UFO_OWNER_DSN environment value wins over the config field. It holds a
    plain libpq URL; the
    proxy pins the async psycopg driver so SQLAlchemy never resolves the sync psycopg2 dialect."""
    monkeypatch.setenv(OWNER_DSN_ENV, "postgresql://env-owner@db/ufo")
    assert _owner_dsn(_config(owner_url="postgresql://config-owner@db/ufo")) == (
        "postgresql+psycopg://env-owner@db/ufo"
    )


def test_owner_dsn_falls_back_to_the_config_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    assert _owner_dsn(_config(owner_url="postgresql://owner@db/ufo")) == (
        "postgresql+psycopg://owner@db/ufo"
    )


def test_owner_dsn_keeps_an_explicit_driver_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_DSN_ENV, "postgresql+psycopg://owner@db/ufo")
    assert _owner_dsn(_config(owner_url=None)) == "postgresql+psycopg://owner@db/ufo"


def test_owner_dsn_fails_loud_when_env_and_config_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_DSN_ENV):
        _owner_dsn(_config(owner_url=None))


def test_bundle_baked_config_satisfies_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy carries no bespoke config — it runs on the same `hosted.toml` the bundle bakes to
    /app/ufo.toml. That baked base must load and satisfy `ProxyServe`: the pack resolves, declares
    no injecting slot the shared proxy cannot honor, and the model base derives. The crashloop this
    replaces (a hand-written proxy.toml missing a required section) had no test at all."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    baked = Path(__file__).parents[2] / "hosted.toml"
    config = load_config(baked)
    manifests = load_manifests(config.pack.name)
    base = _proxy_serve(config, manifests)._base()
    assert any(isinstance(rule, ScopeRule) for rule in base)


async def test_proxy_serve_wires_workspace_credential_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class StopServe(Exception):
        pass

    class WorkspaceFs:
        async def refresh(self, token: str) -> object:
            return token

    class Proxy:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def start(self, **kwargs: object) -> None:
            raise StopServe

    workspace_fs = WorkspaceFs()
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(proxy_serve_module, "init_db", lambda dsn: None)
    monkeypatch.setattr(proxy_serve_module, "sandbox_fs_minter", lambda blob: workspace_fs)
    monkeypatch.setattr(proxy_serve_module, "EgressProxy", Proxy)

    with pytest.raises(StopServe):
        await _proxy_serve(_config(), ()).serve()

    refresh = captured["workspace_credentials"]
    assert isinstance(refresh, MethodType)
    assert refresh.__self__ is workspace_fs
