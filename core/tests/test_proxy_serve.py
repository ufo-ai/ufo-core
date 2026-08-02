import asyncio
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo import proxy_serve as proxy_serve_module
from ufo.bearer import UFO_TOKEN_SECRET_ENV
from ufo.config import BlobConfig, Config, DatabaseConfig, load_config
from ufo.credentials import CredentialStore
from ufo.db import dispose_db
from ufo.ext.loader import injecting_slots, load_manifests
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.models.catalog import CORE_PRICING
from ufo.proxy_serve import (
    OWNER_DSN_ENV,
    ProxyServe,
    _credential_store,
    _egress_ca,
    model_rule_base,
    owner_dsn,
)
from ufo.sandbox.proxy.rules import ANTHROPIC_HOST, ScopeRule, derive_model_rules
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, EGRESS_CA_KEY_ENV, RunTokenCodec

ANTHROPIC_KEY = "sk-ant-test"


@pytest.fixture(autouse=True)
def _run_token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "proxy-serve-test-run-token-secret")


def _config(owner_url: str | None = "postgresql://owner@db/ufo") -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db", owner_url=owner_url),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
    )


async def _opens_nothing() -> None:
    pass


def _proxy_serve(
    config: Config, manifests: tuple[Manifest, ...], shutdown: asyncio.Event | None = None
) -> ProxyServe:
    return ProxyServe(
        config=config,
        manifests=manifests,
        owner_dsn="postgresql://owner@db/ufo",
        ca_cert="CA",
        ca_key="KEY",
        credentials=None,
        pricing=CORE_PRICING,
        shutdown=shutdown or asyncio.Event(),
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


def test_the_static_base_carries_no_workspace_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared proxy's static base is exactly the model-provider egress derived from the process
    key in env — one ScopeRule for the provider host plus its sentinel→real injection and token
    meter. A workspace's keyed-provider secret never joins it: it is resolved per turn against the
    run token's workspace, so nothing one workspace stored can be baked into what every workspace
    shares."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    base = model_rule_base(_config())
    assert base == derive_model_rules("claude-opus-4-8", ANTHROPIC_KEY)
    scopes = {rule.allowed_hosts for rule in base if isinstance(rule, ScopeRule)}
    assert scopes == {frozenset({ANTHROPIC_HOST})}


def _injecting_manifest() -> Manifest:
    return Manifest(
        name="inj",
        version="1",
        credentials=(
            CredentialSlot(
                name="byok",
                description="a workspace key the proxy swaps onto the wire",
                injection=InjectionTarget(
                    host="api.inj.test", header="authorization", sentinel="S"
                ),
            ),
        ),
    )


def test_a_keyed_pack_opens_the_credential_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared proxy decrypts each workspace's keyed-provider secrets itself, so a pack with an
    injecting slot opens the same Fernet `serve` does, read from the configured key env."""
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    slots = injecting_slots((_injecting_manifest(),))
    store = _credential_store(_config(), slots)
    assert store is not None
    assert store.fernet.decrypt(store.fernet.encrypt(b"round-trip")) == b"round-trip"


def test_a_keyed_pack_without_the_credential_key_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key means every keyed host would be unreachable for every workspace — a silently
    capability-less deploy — so boot fails naming the slot. A pack with no injecting slot needs no
    key and opens no store."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="byok"):
        _credential_store(_config(), injecting_slots((_injecting_manifest(),)))
    assert _credential_store(_config(), ()) is None


def test_base_raises_when_no_model_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no model provider key"):
        model_rule_base(_config())


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
    assert owner_dsn(_config(owner_url="postgresql://config-owner@db/ufo")) == (
        "postgresql+psycopg://env-owner@db/ufo"
    )


def test_owner_dsn_falls_back_to_the_config_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    assert owner_dsn(_config(owner_url="postgresql://owner@db/ufo")) == (
        "postgresql+psycopg://owner@db/ufo"
    )


def test_owner_dsn_keeps_an_explicit_driver_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_DSN_ENV, "postgresql+psycopg://owner@db/ufo")
    assert owner_dsn(_config(owner_url=None)) == "postgresql+psycopg://owner@db/ufo"


def test_owner_dsn_fails_loud_when_env_and_config_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_DSN_ENV):
        owner_dsn(_config(owner_url=None))


def test_bundle_baked_config_satisfies_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy carries no bespoke config — it runs on the same `hosted.toml` the bundle bakes to
    /app/ufo.toml. That baked base must load and satisfy `ProxyServe`: the pack resolves, the model
    base derives, and every keyed provider the pack declares opens its store from the credential key
    the deploy sets — the hosted proxy needs that env, so a pack that grows a keyed provider without
    it crashloops here instead. The crashloop this replaces (a hand-written proxy.toml missing a
    required section) had no test at all."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    baked = Path(__file__).parents[2] / "hosted.toml"
    config = load_config(baked)
    manifests = load_manifests(config.pack.name)
    assert any(isinstance(rule, ScopeRule) for rule in model_rule_base(config))
    assert injecting_slots(manifests)
    assert _credential_store(config, injecting_slots(manifests)) is not None


async def test_proxy_serve_wires_run_tokens_from_the_env_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared proxy authenticates every CONNECT against the run token, so its codec must be
    built from the same signing secret serve encodes with — sourced from env at serve time."""
    captured: dict[str, object] = {}

    class StopServe(Exception):
        pass

    class Proxy:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def start(self, **kwargs: object) -> None:
            raise StopServe

    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(proxy_serve_module, "init_db", lambda dsn: None)
    monkeypatch.setattr(proxy_serve_module, "verify_db_reachable", _opens_nothing)
    monkeypatch.setattr(proxy_serve_module, "EgressProxy", Proxy)

    with pytest.raises(StopServe):
        await _proxy_serve(_config(), ()).serve()

    assert captured["run_tokens"] == RunTokenCodec(b"proxy-serve-test-run-token-secret")


async def test_proxy_serve_resolves_keyed_slots_per_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone `ufoctl proxy` is the process that actually injects on the shared fleet, so
    its resolver carries the store it decrypts with and the slots it derives from — threaded, never
    baked into the base, which stays the model-provider egress every workspace shares. The sibling
    proof for serve's in-process proxy is `test_the_local_proxy_resolves_keyed_slots_per_workspace`;
    without this one, dropping either kwarg here would leave every keyed host unreachable fleet-wide
    with nothing red."""
    captured: dict[str, object] = {}

    class StopServe(Exception):
        pass

    class Proxy:
        def __init__(self, **kwargs: object) -> None: ...

        async def start(self, **kwargs: object) -> None:
            raise StopServe

    def rules(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(resolve=None, turn_live=None)

    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(proxy_serve_module, "init_db", lambda dsn: None)
    monkeypatch.setattr(proxy_serve_module, "verify_db_reachable", _opens_nothing)
    monkeypatch.setattr(proxy_serve_module, "EgressProxy", Proxy)
    monkeypatch.setattr(proxy_serve_module, "PerAgentRules", rules)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    manifest = _injecting_manifest()
    config = _config()
    server = ProxyServe(
        config=config,
        manifests=(manifest,),
        owner_dsn="postgresql://owner@db/ufo",
        ca_cert="CA",
        ca_key="KEY",
        credentials=store,
        pricing=CORE_PRICING,
        shutdown=asyncio.Event(),
    )

    with pytest.raises(StopServe):
        await server.serve()

    assert captured["credentials"] is store
    assert captured["slots"] == injecting_slots((manifest,))
    assert captured["base"] == model_rule_base(config)


async def test_proxy_serve_drains_connections_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[int] = []

    class Proxy:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def start(self, **kwargs: object) -> None:
            pass

        async def stop(self, graceful_shutdown_seconds: int) -> None:
            captured.append(graceful_shutdown_seconds)

    config = _config()
    config.serve.graceful_shutdown_seconds = 600
    shutdown = asyncio.Event()
    shutdown.set()
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(proxy_serve_module, "init_db", lambda dsn: None)
    monkeypatch.setattr(proxy_serve_module, "verify_db_reachable", _opens_nothing)
    monkeypatch.setattr(proxy_serve_module, "EgressProxy", Proxy)

    await _proxy_serve(config, (), shutdown).serve()

    assert captured == [600]


async def test_proxy_serve_sigterm_wakes_the_idle_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process-level `signal.signal` handler never wakes a loop parked in its selector; the
    proxy's steady state is exactly that park, so the drain must hang off the loop's own
    signal machinery."""
    captured: list[int] = []

    class Proxy:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def start(self, **kwargs: object) -> None:
            pass

        async def stop(self, graceful_shutdown_seconds: int) -> None:
            captured.append(graceful_shutdown_seconds)

    config = _config()
    config.serve.graceful_shutdown_seconds = 600
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(proxy_serve_module, "init_db", lambda dsn: None)
    monkeypatch.setattr(proxy_serve_module, "verify_db_reachable", _opens_nothing)
    monkeypatch.setattr(proxy_serve_module, "EgressProxy", Proxy)

    serving = asyncio.create_task(_proxy_serve(config, ()).serve())
    await asyncio.sleep(0)
    assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(serving, timeout=5)

    assert captured == [600]


async def test_proxy_serve_base_admits_the_s3_artifact_store_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone proxy is prod's only egress arbiter, so the artifact host must land in its
    static base exactly as it does in serve's in-process twin — without it, every hosted share_file
    PUT is refused at CONNECT while the local proxy stays green."""
    captured: dict[str, object] = {}

    class StopServe(Exception):
        pass

    class Proxy:
        def __init__(self, **kwargs: object) -> None: ...

        async def start(self, **kwargs: object) -> None:
            raise StopServe

    def rules(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(resolve=None, turn_live=None)

    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "proxy-serve-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "proxy-serve-test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setattr(proxy_serve_module, "init_db", lambda dsn: None)
    monkeypatch.setattr(proxy_serve_module, "verify_db_reachable", _opens_nothing)
    monkeypatch.setattr(proxy_serve_module, "EgressProxy", Proxy)
    monkeypatch.setattr(proxy_serve_module, "PerAgentRules", rules)
    config = _config().model_copy(
        update={"blob": BlobConfig(backend="s3", bucket="ufo-blobs", region="us-east-1")}
    )
    server = ProxyServe(
        config=config,
        manifests=(),
        owner_dsn="postgresql://owner@db/ufo",
        ca_cert="CA",
        ca_key="KEY",
        credentials=None,
        pricing=CORE_PRICING,
        shutdown=asyncio.Event(),
    )

    with pytest.raises(StopServe):
        await server.serve()

    base = captured["base"]
    assert isinstance(base, tuple)
    assert ScopeRule(allowed_hosts=frozenset({"ufo-blobs.s3.amazonaws.com"})) in base


REFUSED_OWNER_DSN = "postgresql+psycopg://ufo:ufo@127.0.0.1:1/ufo"


async def test_the_proxy_refuses_to_bind_when_its_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ufo-sandbox-proxy` has no `/healthz`; a TCP probe confirms the bind (`hosted.yaml.tpl`), so
    a proxy that binds in front of an unreachable database reads as ready and then fails every
    request behind it. Boot has to end before the bind instead.

    `start` is replaced with a sentinel rather than left to bind a real port: without the check the
    boot reaches it and the test says so, instead of hanging on a socket. Every other precondition
    boot enforces is satisfied here, so the sentinel is what the check is holding back. The dsn
    carries the psycopg scheme `owner_dsn` rewrites to, which is the driver this deployment opens
    and the reason the refusal arrives as `OperationalError` rather than the bare `OSError` asyncpg
    would raise."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)

    async def never(*_: object, **__: object) -> None:
        raise AssertionError("bound the proxy with an unreachable database")

    monkeypatch.setattr(proxy_serve_module.EgressProxy, "start", never)
    proxy = ProxyServe(
        config=_config(),
        manifests=(),
        owner_dsn=REFUSED_OWNER_DSN,
        ca_cert="CA",
        ca_key="KEY",
        credentials=None,
        pricing=CORE_PRICING,
        shutdown=asyncio.Event(),
    )
    try:
        with pytest.raises(sa.exc.OperationalError):
            await proxy.serve()
    finally:
        await dispose_db()
