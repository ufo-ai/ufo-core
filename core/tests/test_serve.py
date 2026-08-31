import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

import ufo.db
from ufo import serve
from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.config import (
    DEFAULT_BACKGROUND_JOBS_MODEL,
    BlobConfig,
    Config,
    DatabaseConfig,
    SandboxConfig,
)
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.harness.models.catalog import CORE_PRICING
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import EGRESS_CA_CERT_ENV, RunTokenCodec
from ufo.proxy_serve import OWNER_DSN_ENV, model_rule_base
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_rules import InjectionRule, ScopeRule
from ufo.runtime.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.runtime.ext.manifest import CarrierSpec, CredentialSlot, InjectionTarget, Manifest
from ufo.runtime.ext.surface import SurfaceSpec

CA_PEM = "-----BEGIN CERTIFICATE-----\nshared\n-----END CERTIFICATE-----\n"
ANTHROPIC_KEY = "sk-ant-test"
CONTROL_TOKEN = "serve-egress-control-secret"
OWNER_LIBPQ_DSN = "postgresql://ufo_owner:pw@db.test/ufo"
RUN_TOKENS = RunTokenCodec(b"serve-test-run-token-secret")


@pytest.fixture(autouse=True)
def _run_token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "serve-test-run-token-secret")
    monkeypatch.delenv("UFO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("UFO_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(serve.RUNTIME_REVISION_ENV, raising=False)
    monkeypatch.delenv(serve.RUNTIME_IMAGE_ENV, raising=False)


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


def _blob() -> FilesystemBlobStore:
    return FilesystemBlobStore(root=Path("/tmp/blobs"))


def test_runtime_identity_of_an_unpackaged_process_carries_no_image() -> None:
    config = _hosted_config()
    identity = serve._runtime_identity(config, CarrierSpec(name="local", factory=LocalCarrier))

    assert identity.revision is None
    assert identity.image_digest is None
    assert identity.config_digest == serve._payload_digest(config.model_dump(mode="json"))
    assert identity.sandbox_backend == "local"


def test_runtime_identity_binds_deployed_image_config_and_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(serve.RUNTIME_REVISION_ENV, "abc12345")
    monkeypatch.setenv(
        serve.RUNTIME_IMAGE_ENV,
        f"registry.test/ufo@sha256:{'a' * 64}",
    )
    config = _hosted_config()
    identity = serve._runtime_identity(
        config,
        CarrierSpec(
            name="local",
            factory=LocalCarrier,
            runtime_digest=lambda: f"sha256:{'b' * 64}",
        ),
    )

    assert identity is not None
    assert identity.revision == "abc12345"
    assert identity.image_digest == f"sha256:{'a' * 64}"
    assert identity.config_digest == serve._payload_digest(config.model_dump(mode="json"))
    assert identity.sandbox_backend == "local"
    assert identity.sandbox_digest == serve._payload_digest(
        {
            "config": config.sandbox.model_dump(mode="json"),
            "carrier": f"sha256:{'b' * 64}",
        }
    )


def test_runtime_identity_requires_revision_and_image_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(serve.RUNTIME_REVISION_ENV, "abc12345")

    with pytest.raises(RuntimeError, match="must be set together"):
        serve._runtime_identity(_hosted_config(), CarrierSpec(name="local", factory=LocalCarrier))


def test_one_shot_closes_the_throwaway_loops_connections(database_url: str, tmp_path: Path) -> None:
    """The boot steps `run()` drives through `_one_shot` each get a throwaway `asyncio.run` loop,
    and the wrapper closes that loop's pooled connections before it closes — a socket abandoned to
    a closed loop is one nothing left in the process can close. The empty registry is not that
    property: popping the key alone would satisfy it. Disposal replaces the engine's pool, so that
    replacement is what is asserted.

    On sqlite this opens its own copy rather than `database_url`, which is the session-scoped
    template every other test reaches through a private copy of."""
    url = database_url
    if url.startswith("sqlite"):
        private = tmp_path / "one_shot.db"
        shutil.copy(make_url(url).database or "", private)
        url = f"sqlite+aiosqlite:///{private}"
    init_db(url)
    escaped: list[tuple[object, object]] = []
    try:

        async def touch() -> int:
            async with workspace_tx() as connection:
                loop = asyncio.get_running_loop()
                engine = next(
                    built for (held, _), built in ufo.db._APP.engines.items() if held is loop
                )
                escaped.append((engine, engine.pool))
                return (await connection.execute(sa.text("select 1"))).scalar_one()

        assert serve._one_shot(touch()) == 1
        engine, pool_before = escaped[0]
        assert engine.pool is not pool_before  # type: ignore[attr-defined]
        assert engine not in ufo.db._APP.engines.values()
    finally:
        asyncio.run(dispose_db())


def test_serve_verifies_the_owner_database_before_it_seats_the_instance(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hosted.yaml.tpl` TCP-probes serve too.

    The check runs for real here rather than through a stub, and only the owner dsn is unreachable:
    that is what pins the call *after* `init_owner_db`, since a check that ran before it would find
    only the app url, dial it happily, and boot on. `record_fleet_seat` is the sentinel for both the
    ordering and the call itself — a process that seats itself against a database it cannot read has
    already told the fleet it is alive."""
    reachable = database_url
    if reachable.startswith("sqlite"):
        private = tmp_path / "serve_boot.db"
        shutil.copy(make_url(reachable).database or "", private)
        reachable = f"sqlite+aiosqlite:///{private}"

    def seated(_: object) -> None:
        raise AssertionError("seated the instance against an unreachable owner database")

    monkeypatch.setenv(OWNER_DSN_ENV, "postgresql://ufo:ufo@127.0.0.1:1/ufo")
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(serve, "load_config", lambda: _hosted_config())
    monkeypatch.setattr(serve, "init_o11y", lambda endpoint: None)
    monkeypatch.setattr(serve, "init_db", lambda opened: init_db(reachable))
    monkeypatch.setattr(serve, "record_fleet_seat", seated)
    try:
        with pytest.raises(ConnectionRefusedError):
            serve.run()
    finally:
        asyncio.run(dispose_db())


def test_launch_jobs_reuses_the_boot_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    manifests = (Manifest(name="jobs", version="1"),)
    registry = SimpleNamespace(key_slot_for=lambda _model: None)
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
        sandboxes=object(),
        subagents=object(),
        run_tokens=RunTokenCodec(b"launch-jobs-test-secret"),
        credentials=None,
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

    def bindings(*args: object, disabled: frozenset[str]) -> tuple[str]:
        captured["disabled"] = disabled
        return ("bindings",)

    monkeypatch.setattr(serve, "bindings_from", bindings)
    monkeypatch.setattr(serve, "JobRunner", Runner)
    monkeypatch.setattr(
        serve, "load_manifests", lambda *args: pytest.fail("manifests loaded twice")
    )
    monkeypatch.setattr(
        serve, "model_registry", lambda *args: pytest.fail("model registry built twice")
    )

    serve._launch_jobs(runtime, object(), object(), object())

    assert captured["page"]["manifests"] is manifests
    assert captured["page"]["registry"] is registry
    assert captured["jobs"]["registry"] is registry
    assert captured["launched"] is True
    assert captured["page"]["probes"] is captured["jobs"]["probes"]
    assert captured["disabled"] == frozenset()


def test_launch_jobs_hands_both_runners_the_background_jobs_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both jobs runners carry the configured background-jobs model, so a handler's own metered
    call runs on it — the boot registry stays the deploy default a member turn resolves through."""
    registry = SimpleNamespace(key_slot_for=lambda model: None)
    captured: dict[str, object] = {}
    runtime = SimpleNamespace(
        config=_local_config(),
        manifests=(Manifest(name="jobs", version="1"),),
        dbos=object(),
        index=object(),
        embed=object(),
        blob=object(),
        registry=registry,
        sandboxes=object(),
        subagents=object(),
        run_tokens=RunTokenCodec(b"launch-jobs-test-secret"),
        credentials=None,
    )

    class Runner:
        def __init__(self, **kwargs: object) -> None:
            captured["jobs"] = kwargs

        def launch(self) -> None:
            return None

    def page_change_runner(**kwargs: object) -> object:
        captured["page"] = kwargs
        return object()

    monkeypatch.setattr(serve, "PageChangeRunner", page_change_runner)
    monkeypatch.setattr(serve, "core_jobs", lambda *args: ())
    monkeypatch.setattr(serve, "bindings_from", lambda *args, disabled: ("bindings",))
    monkeypatch.setattr(serve, "JobRunner", Runner)

    serve._launch_jobs(runtime, object(), object(), object())

    assert captured["page"]["background_model"] == DEFAULT_BACKGROUND_JOBS_MODEL
    assert captured["jobs"]["background_model"] == DEFAULT_BACKGROUND_JOBS_MODEL
    assert captured["jobs"]["registry"] is registry


async def test_serve_lifespan_waits_for_background_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = ShutdownProbe()
    reconciler = ShutdownProbe()
    stranded = ShutdownProbe()
    poller = ShutdownProbe()
    speaker = ShutdownProbe()
    listener = ShutdownProbe()
    monkeypatch.setattr(serve, "ExecutorRecovery", lambda: recovery)
    monkeypatch.setattr(serve, "CancelReconciler", lambda client: reconciler)
    monkeypatch.setattr(serve, "StrandedTurnReconciler", lambda client: stranded)
    app = SimpleNamespace(
        state=SimpleNamespace(
            dbos=object(),
            writeback_poller=poller,
            mid_turn_reply_poller=speaker,
            surface_listeners=(listener,),
            configured_sources=(),
        )
    )
    async with serve._serve_lifespan(app):
        await recovery.started.wait()
        await reconciler.started.wait()
        await stranded.started.wait()
        await poller.started.wait()
        await speaker.started.wait()
        await listener.started.wait()
    assert recovery.stopped.is_set()
    assert reconciler.stopped.is_set()
    assert stranded.stopped.is_set()
    assert poller.stopped.is_set()
    assert speaker.stopped.is_set()
    assert listener.stopped.is_set()


async def test_serve_lifespan_propagates_a_background_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = FailureProbe()
    reconciler = ShutdownProbe()
    stranded = ShutdownProbe()
    monkeypatch.setattr(serve, "ExecutorRecovery", lambda: recovery)
    monkeypatch.setattr(serve, "CancelReconciler", lambda client: reconciler)
    monkeypatch.setattr(serve, "StrandedTurnReconciler", lambda client: stranded)
    app = SimpleNamespace(
        state=SimpleNamespace(
            dbos=object(),
            writeback_poller=None,
            mid_turn_reply_poller=None,
            surface_listeners=(),
            configured_sources=(),
        )
    )
    with pytest.raises(ExceptionGroup) as raised:
        async with serve._serve_lifespan(app):
            await recovery.started.wait()
            recovery.release.set()
            await asyncio.wait_for(asyncio.Event().wait(), timeout=1)
    assert any(
        isinstance(error, RuntimeError) and str(error) == "background failed"
        for error in raised.value.exceptions
    )


class StubActiveWorkflows:
    def __init__(self, active: list[str]) -> None:
        self._active = active

    def activeList(self) -> list[str]:
        return self._active


class StubDbosInstance:
    def __init__(self, active: list[str]) -> None:
        self._active_workflows_set = StubActiveWorkflows(active)


def test_stop_executor_drains_before_retiring_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class StubDbos:
        @classmethod
        def destroy(cls, *, workflow_completion_timeout_sec: int = 0) -> None:
            calls.append(("destroy", workflow_completion_timeout_sec))

    class StubHeartbeat:
        async def retire(self) -> None:
            calls.append("retire")

    monkeypatch.setattr(serve, "DBOS", StubDbos)
    serve._stop_executor(StubDbosInstance([]), StubHeartbeat(), 600)
    assert calls == [("destroy", 600), "retire"]


def test_stop_executor_keeps_the_seat_when_workflows_outlive_the_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DBOS.destroy` bounds its post-drain force-cancel with a ten-second join, so a
    cancellation-resistant workflow can outlive it with its active-set entry retained — the entry
    is released only when the workflow task finishes. Retiring the seat then would let a peer
    re-dispatch a workflow this process is still executing."""
    calls: list[object] = []

    class StubDbos:
        @classmethod
        def destroy(cls, *, workflow_completion_timeout_sec: int = 0) -> None:
            calls.append(("destroy", workflow_completion_timeout_sec))

    class StubHeartbeat:
        async def retire(self) -> None:
            calls.append("retire")

    monkeypatch.setattr(serve, "DBOS", StubDbos)
    serve._stop_executor(StubDbosInstance(["wf-live"]), StubHeartbeat(), 600)
    assert calls == [("destroy", 600)]


def test_dbos_destroy_contract_for_the_executor_drain(tmp_path: Path) -> None:
    """The real interaction `_stop_executor` retires the seat on, no stubs: queued async
    workflows run on DBOS's background loop — off the main thread, beyond any main-thread
    `asyncio.run` teardown (uvicorn's shutdown) — so `DBOS.destroy`'s bounded drain is what ends
    them. A cooperatively-parked workflow is cancelled and released; a cancellation-absorbing one
    outlives destroy with its active-set entry retained — the keep-seat branch's case. The
    stubbed tests above assert `_stop_executor`'s branch on that contract; this proves the
    contract against the installed dbos, in a subprocess because DBOS is a process-global
    singleton."""
    probe = Path(__file__).parent / "executor_drain_probe.py"
    run = subprocess.run(
        [sys.executable, str(probe), str(tmp_path / "drain_sys.db")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    evidence = json.loads(run.stdout.splitlines()[-1])
    assert evidence["on_main_thread"] is False
    assert evidence["active_while_parked"] == 2
    assert evidence["active_after_main_loop_teardown"] == 2
    assert evidence["retained_is_stubborn"] is True
    assert evidence["destroy_seconds"] < 30


def test_model_rule_base_prefers_the_ufo_prefixed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`UFO_ANTHROPIC_API_KEY` scopes a key to ufo alone, winning over the bare upstream name; an
    empty prefixed value (compose interpolates `${UFO_ANTHROPIC_API_KEY:-}`) counts as unset and
    falls back to the bare name."""
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "sk-ant-ufo-scoped")
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    injected = [r for r in model_rule_base(_hosted_config()) if isinstance(r, InjectionRule)]
    assert [rule.real for rule in injected] == ["sk-ant-ufo-scoped"]

    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "")
    injected = [r for r in model_rule_base(_hosted_config()) if isinstance(r, InjectionRule)]
    assert [rule.real for rule in injected] == [ANTHROPIC_KEY]


def test_model_rule_base_boots_on_the_ufo_prefixed_key_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "sk-ant-ufo-scoped")
    injected = [r for r in model_rule_base(_hosted_config()) if isinstance(r, InjectionRule)]
    assert [rule.real for rule in injected] == ["sk-ant-ufo-scoped"]


def test_proxy_endpoint_is_built_from_config_and_the_shared_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The egress wire is the standalone Rust proxy; serve carries the endpoint the carrier threads
    into every sandbox — the stable port, the public dial-back base, and the shared CA cert from
    env, a plain value object with no bound socket — and mounts the egress-control RPC that proxy
    calls back into."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, CA_PEM)
    monkeypatch.setenv(serve.EGRESS_CONTROL_TOKEN_ENV, CONTROL_TOKEN)
    app = FastAPI()
    endpoint = serve._proxy_endpoint(
        app, _hosted_config(), (), None, CORE_PRICING, RUN_TOKENS, _blob(), None
    )
    assert (endpoint.port, endpoint.ca_cert, endpoint.public_url) == (
        9443,
        CA_PEM,
        "https://proxy.test",
    )
    mounted = TestClient(app).post("/internal/egress/resolve", json={"proxy_auth": ""})
    assert mounted.status_code == 401


def test_proxy_endpoint_fails_loud_without_the_shared_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EGRESS_CA_CERT_ENV, raising=False)
    with pytest.raises(RuntimeError, match=EGRESS_CA_CERT_ENV):
        serve._proxy_endpoint(
            FastAPI(), _hosted_config(), (), None, CORE_PRICING, RUN_TOKENS, _blob(), None
        )


def test_proxy_endpoint_fails_loud_without_the_control_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hosted deploy never falls back to an ephemeral CA: a missing control token dies at boot the
    same way a missing CA does, so a misconfigured fleet fails loud rather than minting a throwaway
    CA that mismatches the real `ufo-egress` proxy's and silently breaks sandbox egress."""
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, CA_PEM)
    monkeypatch.delenv(serve.EGRESS_CONTROL_TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=serve.EGRESS_CONTROL_TOKEN_ENV):
        serve._proxy_endpoint(
            FastAPI(), _hosted_config(), (), None, CORE_PRICING, RUN_TOKENS, _blob(), None
        )


def test_proxy_endpoint_fails_loud_when_the_cache_is_on_without_its_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the sandbox cache is enabled, the daemon authenticates to the git-credential route with
    UFO_CACHE_CONTROL_TOKEN; serve minting a random one in its place would 401 every cache-routed
    git request, so a missing token dies at boot rather than silently breaking sandbox git."""
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, CA_PEM)
    monkeypatch.setenv(serve.EGRESS_CONTROL_TOKEN_ENV, CONTROL_TOKEN)
    monkeypatch.delenv(serve.CACHE_CONTROL_TOKEN_ENV, raising=False)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(
            backend="local",
            proxy_port=9443,
            proxy_public_url="https://proxy.test",
            cache_daemon="127.0.0.1:9110",
        ),
    )
    with pytest.raises(RuntimeError, match=serve.CACHE_CONTROL_TOKEN_ENV):
        serve._proxy_endpoint(FastAPI(), config, (), None, CORE_PRICING, RUN_TOKENS, _blob(), None)


def test_preview_settings_pair_the_service_with_its_real_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(
            backend="local",
            proxy_port=9443,
            preview_service="ufo-preview.ufo.svc.cluster.local:8930",
        ),
    )
    monkeypatch.delenv(serve.PREVIEW_TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=serve.PREVIEW_TOKEN_ENV):
        serve._preview_settings(config)
    monkeypatch.setenv(serve.PREVIEW_TOKEN_ENV, "")
    with pytest.raises(RuntimeError, match=serve.PREVIEW_TOKEN_ENV):
        serve._preview_settings(config)
    monkeypatch.setenv(serve.PREVIEW_TOKEN_ENV, "preview-real")
    assert serve._preview_settings(config) == (
        ("ufo-preview.ufo.svc.cluster.local", 8930),
        "preview-real",
    )


def test_proxy_endpoint_boots_a_local_serve_without_a_shared_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local boot (no `proxy_public_url`) runs no `ufo-egress` unless the dev rig starts one, so
    `ufoctl serve` alone must come up — the documented zero-services default — not fail loud. With
    no shared material it mints a throwaway trust anchor and control token and mounts the control
    RPC; an in-sandbox CONNECT to the unmanned proxy port is refused, which is the no-egress default
    a hosted deploy (which has a real proxy behind `proxy_public_url`) never falls back to."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(EGRESS_CA_CERT_ENV, raising=False)
    monkeypatch.delenv(serve.EGRESS_CONTROL_TOKEN_ENV, raising=False)
    app = FastAPI()
    endpoint = serve._proxy_endpoint(
        app, _local_config(), (), None, CORE_PRICING, RUN_TOKENS, _blob(), None
    )
    assert endpoint.port == 0
    assert "BEGIN CERTIFICATE" in endpoint.ca_cert
    mounted = TestClient(app).post("/internal/egress/resolve", json={"proxy_auth": ""})
    assert mounted.status_code == 401


def test_shared_owner_dsn_prefers_the_env_over_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared service opens owner_tx's engine from UFO_OWNER_DSN, whose contract is a plain
    libpq URL. This resolves which DSN to use and nothing else — the async driver is pinned by
    `init_owner_db`, at the one place the URL is registered, so no caller can hold a form the async
    engine cannot build."""
    monkeypatch.setenv(OWNER_DSN_ENV, OWNER_LIBPQ_DSN)
    assert serve._shared_owner_dsn(_local_config()) == OWNER_LIBPQ_DSN


def test_shared_owner_dsn_falls_back_to_config_owner_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db", owner_url=OWNER_LIBPQ_DSN),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(backend="local", proxy_port=0),
    )
    assert serve._shared_owner_dsn(config) == OWNER_LIBPQ_DSN


def test_shared_owner_dsn_fails_loud_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the owner DSN owner_tx falls back to the RLS-subject engine and the fleet-wide job
    enumeration reads an unset app.workspace_id GUC and crash-loops the dispatcher — fail loud at
    boot instead, naming the missing secret."""
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_DSN_ENV):
        serve._shared_owner_dsn(_local_config())


def test_the_proxy_resolver_reads_keyed_slots_per_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy serves every workspace on the shared fleet, so a keyed slot cannot be baked into
    the resolver's static base — it is threaded to the resolver instead, which reads each secret
    against the run token's own workspace. The base itself stays the model-provider egress alone."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, CA_PEM)
    monkeypatch.setenv(serve.EGRESS_CONTROL_TOKEN_ENV, CONTROL_TOKEN)
    slot = CredentialSlot(
        name="byok",
        description="a workspace key the proxy swaps onto the wire",
        injection=InjectionTarget(host="api.inj.test", header="authorization", sentinel="S"),
    )
    manifest = Manifest(name="inj", version="1", credentials=(slot,))
    captured: dict[str, object] = {}

    def rules(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(resolve=None, turn_live=None, rules_generation=None)

    monkeypatch.setattr(serve, "PerAgentRules", rules)
    credentials = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    config = _local_config()
    serve._proxy_endpoint(
        FastAPI(), config, (manifest,), credentials, CORE_PRICING, RUN_TOKENS, _blob(), None
    )
    assert captured["credentials"] is credentials
    assert captured["slots"] == (slot,)
    assert captured["base"] == model_rule_base(config)


def test_the_proxy_resolver_base_admits_the_s3_artifact_store_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deploy whose artifacts live in S3 admits that bucket's host in the static base, so the
    sandbox's presigned PUT is not refused at CONNECT — the base is where it belongs, since it is a
    deploy-wide fact and not a per-turn one."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "serve-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "serve-test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, CA_PEM)
    monkeypatch.setenv(serve.EGRESS_CONTROL_TOKEN_ENV, CONTROL_TOKEN)
    captured: dict[str, object] = {}

    def rules(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(resolve=None, turn_live=None, rules_generation=None)

    monkeypatch.setattr(serve, "PerAgentRules", rules)
    store = S3BlobStore(bucket="ufo-blobs", region="us-east-1")

    serve._proxy_endpoint(
        FastAPI(), _local_config(), (), None, CORE_PRICING, RUN_TOKENS, store, None
    )

    assert ScopeRule(allowed_hosts=frozenset({"ufo-blobs.s3.amazonaws.com"})) in captured["base"]


def _home_manifest(name: str, home: bool) -> Manifest:
    async def identify(_request: object, _auth: object) -> None:
        return None

    return Manifest(
        name=name,
        version="0.1.0",
        surfaces=(SurfaceSpec(name=name, routes=(), identify=identify, home=home),),
    )


def test_the_bare_host_opens_the_surface_that_claims_the_browser_home() -> None:
    """A browser typing the deploy's host lands on a door, not a 404: `/` redirects to the home
    surface, which decides between its own page and the sign-in page from the request's session."""
    app = FastAPI()
    serve._mount_home(app, (_home_manifest("slack", False), _home_manifest("web", True)))

    landed = TestClient(app).get("/", follow_redirects=False)

    assert landed.status_code == 303
    assert landed.headers["location"] == "/surface/web"


def test_a_deploy_with_no_home_surface_mounts_no_root_route() -> None:
    app = FastAPI()
    serve._mount_home(app, (_home_manifest("slack", False),))

    assert TestClient(app).get("/").status_code == 404


def test_two_home_surfaces_fail_the_boot() -> None:
    """Two claims name no single door, so the pack is refused where every other surface conflict
    is — at boot, rather than by whichever manifest loaded last."""
    with pytest.raises(RuntimeError, match="browser home"):
        serve._mount_home(FastAPI(), (_home_manifest("web", True), _home_manifest("debug", True)))


def test_reserved_host_prefixes_guard_fails_loud_on_a_gateway_route() -> None:
    """The shared fleet shares the app host with the onboarding gateway via one ingress; the boot
    guard rejects any fleet route under a gateway-reserved prefix before the ingress can shadow it.
    Product paths (`/surface`, the OAuth callback, artifacts) are clear; a `/v1/onboard` route is
    not."""

    def _endpoint(_request: object) -> None: ...

    app = FastAPI()
    for path in ("/surface/web", "/v1/connect/callback", "/artifacts/{artifact_id}/{filename}"):
        app.add_route(path, _endpoint)
    serve._assert_no_reserved_routes(app)
    app.add_route("/v1/onboard/web", _endpoint)
    with pytest.raises(RuntimeError, match="reserved"):
        serve._assert_no_reserved_routes(app)


def test_every_admission_carries_the_model_resolution() -> None:
    """The balance gate's own-key exemption needs the model an agent runs, and `Admission` is the
    only thing that can supply it. Built without it on any surface, a workspace serving its own
    provider key is refused there while exempt everywhere else — and the surface most members
    speak on is the shared-fleet one, not the local mount."""
    source = (Path(serve.__file__)).read_text()
    blocks = [block.split("\n    )")[0] for block in source.split("admission = Admission(")[1:]]
    assert len(blocks) == 2
    assert all("key_slot_for=" in block for block in blocks), blocks
