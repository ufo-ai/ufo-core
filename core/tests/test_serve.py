import asyncio
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI

from ufo import serve
from ufo.config import BlobConfig, Config, DatabaseConfig, SandboxConfig
from ufo.ext.manifest import CredentialSlot, InjectionTarget, Manifest
from ufo.models.catalog import CORE_PRICING
from ufo.proxy_serve import OWNER_DSN_ENV, model_rule_base
from ufo.sandbox.fs_creds import SandboxFsCredentialMinter
from ufo.sandbox.proxy.rules import ANTHROPIC_HOST, ScopeRule
from ufo.sandbox.session import EGRESS_CA_CERT_ENV

CA_PEM = "-----BEGIN CERTIFICATE-----\nshared\n-----END CERTIFICATE-----\n"
ANTHROPIC_KEY = "sk-ant-test"
LEAF_PEM_PREFIX = "-----BEGIN CERTIFICATE-----"
OWNER_LIBPQ_DSN = "postgresql://ufo_owner:pw@db.test/ufo"
OWNER_ASYNCPG_DSN = "postgresql+asyncpg://ufo_owner:pw@db.test/ufo"


class ShutdownProbe:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def run(self) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            self.stopped.set()


class FailureProbe:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self) -> None:
        self.started.set()
        await self.release.wait()
        raise RuntimeError("background failed")


def _hosted_config() -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(
            backend="local", proxy_port=9443, proxy_public_url="https://proxy.test"
        ),
    )


def _local_config() -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(backend="local", proxy_port=0),
    )


def test_launch_jobs_reuses_the_boot_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    manifests = (Manifest(name="jobs", version="1"),)
    registry = object()
    page_runner = object()
    captured: dict[str, object] = {}
    runtime = SimpleNamespace(
        config=_local_config(),
        manifests=manifests,
        dbos=object(),
        index=object(),
        embed=object(),
        blob=object(),
        registry=registry,
        carrier=object(),
    )

    def page_change_runner(**kwargs: object) -> object:
        captured["page"] = kwargs
        return page_runner

    class Runner:
        def __init__(self, **kwargs: object) -> None:
            captured["jobs"] = kwargs

        def launch(self) -> None:
            captured["launched"] = True

    monkeypatch.setattr(serve, "PageChangeRunner", page_change_runner)
    monkeypatch.setattr(serve, "core_jobs", lambda *args: ())
    monkeypatch.setattr(serve, "bindings_from", lambda *args: ("bindings",))
    monkeypatch.setattr(serve, "JobRunner", Runner)
    monkeypatch.setattr(
        serve, "load_manifests", lambda *args: pytest.fail("manifests loaded twice")
    )
    monkeypatch.setattr(
        serve, "model_registry", lambda *args: pytest.fail("model registry built twice")
    )

    serve._launch_jobs(runtime, object(), object())

    assert captured["page"]["manifests"] is manifests
    assert captured["page"]["registry"] is registry
    assert captured["jobs"]["registry"] is registry
    assert captured["launched"] is True


async def test_serve_lifespan_waits_for_background_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = ShutdownProbe()
    reconciler = ShutdownProbe()
    poller = ShutdownProbe()
    monkeypatch.setattr(serve, "ExecutorRecovery", lambda: recovery)
    monkeypatch.setattr(serve, "CancelReconciler", lambda client: reconciler)
    app = SimpleNamespace(state=SimpleNamespace(dbos=object(), writeback_poller=poller))
    async with serve._serve_lifespan(app):
        await recovery.started.wait()
        await reconciler.started.wait()
        await poller.started.wait()
    assert recovery.stopped.is_set()
    assert reconciler.stopped.is_set()
    assert poller.stopped.is_set()


async def test_serve_lifespan_propagates_a_background_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = FailureProbe()
    reconciler = ShutdownProbe()
    monkeypatch.setattr(serve, "ExecutorRecovery", lambda: recovery)
    monkeypatch.setattr(serve, "CancelReconciler", lambda client: reconciler)
    app = SimpleNamespace(state=SimpleNamespace(dbos=object(), writeback_poller=None))
    with pytest.raises(ExceptionGroup) as raised:
        async with serve._serve_lifespan(app):
            await recovery.started.wait()
            recovery.release.set()
            await asyncio.wait_for(asyncio.Event().wait(), timeout=1)
    assert any(
        isinstance(error, RuntimeError) and str(error) == "background failed"
        for error in raised.value.exceptions
    )


def test_hosted_proxy_endpoint_is_built_from_config_and_the_shared_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With `proxy_public_url` set the proxy runs as standalone `ufoctl proxy`: serve derives the
    endpoint carriers thread into every sandbox from config — the stable port and public
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
    a real ephemeral port and carries the freshly minted CA, with no public proxy base."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(EGRESS_CA_CERT_ENV, raising=False)
    endpoint = serve._proxy_endpoint(_local_config(), (), None, CORE_PRICING)
    assert endpoint.public_url is None
    assert endpoint.port != 0
    assert endpoint.ca_cert.startswith(LEAF_PEM_PREFIX)


def test_local_proxy_wires_workspace_credential_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def generate_ca() -> tuple[str, str]:
        return "CERT", "KEY"

    class Proxy:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def start(self, port: int) -> object:
            return SimpleNamespace(port=port, ca_cert="CERT", public_url=None)

    class WorkspaceFs:
        async def refresh(self, token: str) -> object:
            return token

    workspace_fs = WorkspaceFs()
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(serve, "generate_ca", generate_ca)
    monkeypatch.setattr(serve, "EgressProxy", Proxy)

    serve._proxy_endpoint(
        _local_config(),
        (),
        None,
        CORE_PRICING,
        cast(SandboxFsCredentialMinter, workspace_fs),
    )

    refresh = captured["workspace_credentials"]
    assert isinstance(refresh, MethodType)
    assert refresh.__self__ is workspace_fs


def test_local_rule_base_is_the_model_base_when_no_slot_injects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-process proxy's base is exactly the shared model-provider egress — on the shared fleet
    it carries no workspace credential rules at all."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = _local_config()
    base = serve._local_rule_base(config, ())
    assert base == model_rule_base(config)
    assert {rule.allowed_hosts for rule in base if isinstance(rule, ScopeRule)} == {
        frozenset({ANTHROPIC_HOST})
    }


def test_shared_owner_dsn_from_env_pins_the_async_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared service opens owner_tx's engine from UFO_OWNER_DSN. The value's
    contract is a plain libpq URL; serve pins the asyncpg driver its subject engine also dials so
    the owner engine bypasses RLS through the table owner rather than falling back to it."""
    monkeypatch.setenv(OWNER_DSN_ENV, OWNER_LIBPQ_DSN)
    assert serve._shared_owner_dsn(_local_config()) == OWNER_ASYNCPG_DSN


def test_shared_owner_dsn_falls_back_to_config_owner_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db", owner_url=OWNER_LIBPQ_DSN),
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


def test_local_rule_base_fails_loud_on_an_injecting_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-process proxy serves every workspace on the shared fleet, so it cannot bake one
    workspace's secrets into its rule base — an injecting credential slot fails loud, directing the
    deploy to the standalone `ufoctl proxy` that injects per request."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    slot = CredentialSlot(
        name="byok",
        description="a workspace key the local proxy swaps onto the wire",
        injection=InjectionTarget(host="api.inj.test", header="authorization", sentinel="S"),
    )
    manifest = Manifest(name="inj", version="1", credentials=(slot,))
    with pytest.raises(RuntimeError, match="cannot inject workspace credential secrets"):
        serve._local_rule_base(_local_config(), (manifest,))


def test_reserved_host_prefixes_guard_fails_loud_on_a_gateway_route() -> None:
    """The shared fleet shares the app host with the onboarding gateway via one ingress; the boot
    guard rejects any fleet route under a gateway-reserved prefix before the ingress can shadow it.
    Product paths (`/surface`, the OAuth callback, artifacts) are clear; a `/v1/onboard` route is
    not."""

    def _endpoint(_request: object) -> None: ...

    app = FastAPI()
    for path in ("/surface/web", "/v1/connect/callback", "/artifacts/download"):
        app.add_route(path, _endpoint)
    serve._assert_no_reserved_routes(app)
    app.add_route("/v1/onboard/web", _endpoint)
    with pytest.raises(RuntimeError, match="reserved"):
        serve._assert_no_reserved_routes(app)
