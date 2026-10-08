import ast
import asyncio
import base64
import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from ufo_ext_sample.operator import OPERATOR_RULE as SAMPLE_OPERATOR_RULE
from ufo_ext_sample.operator import SampleOperatorRule
from ufo_ext_sample.spend import SampleGate

import ufo.db
from ufo import serve
from ufo.blob import FilesystemBlobStore, FleetBlobStore, S3BlobStore, WorkspaceBlobStore
from ufo.config import (
    DEFAULT_BACKGROUND_JOBS_MODEL,
    BlobConfig,
    Config,
    ConnectConfig,
    DatabaseConfig,
    DebuggerConfig,
    ModelsConfig,
    OperatorConfig,
    PackConfig,
    SandboxConfig,
    SitesConfig,
    SourceConfig,
    SourceEntry,
)
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.models.catalog import (
    ANTHROPIC_KEY_ENV,
    CORE_MODEL_SPECS,
    CORE_PRICING,
    OPENAI_KEY_ENV,
)
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.sandbox.exec_env import sandbox_exported_env
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import RunTokenCodec
from ufo.host.ext.loader import deploy_claims, load_manifests
from ufo.proxy_serve import MODEL_KEY_ENVS, OWNER_DSN_ENV, model_bindings
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_control import CACHE_CONTROL_TOKEN_ENV, PROXY_PUBLIC_KEY_ENV
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    RUN_HEADER,
    UFO_MODELS_SECRET,
    Bind,
    HostEntry,
    PolicyScope,
    Route,
    SessionPolicy,
)
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.agent_scope import agent
from ufo.runtime.billing.spend import GateDeploy
from ufo.runtime.ext.manifest import CarrierSpec, CredentialSlot, InjectionTarget, Manifest
from ufo.runtime.ext.operator import install_operator, installed_operator
from ufo.runtime.ext.surface import SurfaceSpec
from ufo.runtime.jobs import model_key_slots
from ufo.runtime.sources.sync import FOLDER_BACKEND
from ufo.runtime.workspace import SeveralWorkspaces, ws
from ufo.schema import tables

ANTHROPIC_KEY = "sk-ant-test"
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
        sandbox=SandboxConfig(backend="local", proxy_url="https://proxy.test"),
    )


def _local_config() -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(backend="local"),
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
def test_one_shot_closes_the_throwaway_loops_connections(database_url: str, tmp_path: Path) -> None:
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
def test_serve_verifies_the_owner_database_before_it_seats_the_instance(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hosted.yaml.tpl` TCP-probes serve too."""
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
            serve.run(serve.WHOLE_FLEET)
    finally:
        asyncio.run(dispose_db())


def test_launch_jobs_reuses_the_boot_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    manifests = (Manifest(name="jobs", version="1"),)
    registry = SimpleNamespace(key_slot_for=lambda _model: None, specs={})
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
        spend=object(),
        ledger=object(),
        sessions=None,
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

    probes = object()
    serve._launch_jobs(runtime, object(), object(), object(), probes)

    assert captured["page"]["manifests"] is manifests
    assert captured["page"]["registry"] is registry
    assert captured["jobs"]["registry"] is registry
    assert captured["launched"] is True
    assert captured["page"]["probes"] is probes
    assert captured["jobs"]["probes"] is probes
    assert captured["page"]["spend"] is captured["jobs"]["spend"] is runtime.spend
    assert captured["page"]["ledger"] is captured["jobs"]["ledger"] is runtime.ledger
    assert captured["disabled"] == frozenset()


def test_launch_jobs_hands_both_runners_the_background_jobs_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both jobs runners carry the configured background-jobs model, so a handler's own metered
    call runs on it — the boot registry stays the deploy default a member turn resolves through."""
    registry = SimpleNamespace(key_slot_for=lambda model: None, specs={})
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
        spend=object(),
        ledger=object(),
        sessions=None,
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

    probes = object()
    serve._launch_jobs(runtime, object(), object(), object(), probes)

    assert captured["page"]["background_model"] == DEFAULT_BACKGROUND_JOBS_MODEL
    assert captured["jobs"]["background_model"] == DEFAULT_BACKGROUND_JOBS_MODEL
    assert captured["jobs"]["registry"] is registry
    assert captured["page"]["probes"] is probes
    assert captured["jobs"]["probes"] is probes


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
            fleet=serve.WHOLE_FLEET,
            dbos=object(),
            writeback_poller=poller,
            mid_turn_reply_poller=speaker,
            surface_listeners=(listener,),
            surface_boots=(),
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


async def test_the_jobs_fleet_runs_the_reconcilers_and_none_of_the_surface_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three reconcilers are the whole deploy's safety net, so both fleets run them — a jobs
    process reclaims a dead turns process's turns and the reverse."""
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
            fleet=serve.JOBS_FLEET,
            dbos=object(),
            writeback_poller=poller,
            mid_turn_reply_poller=speaker,
            surface_listeners=(listener,),
            surface_boots=(),
            configured_sources=(),
        )
    )
    async with serve._serve_lifespan(app):
        await recovery.started.wait()
        await reconciler.started.wait()
        await stranded.started.wait()
        await asyncio.sleep(0)
        assert not poller.started.is_set()
        assert not speaker.started.is_set()
        assert not listener.started.is_set()


async def test_a_surface_boot_starts_its_work_before_the_first_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The work a surface would otherwise start on its first page — the portal's asset publish —
    starts with the app loop instead, handed the fleet store it writes through."""
    monkeypatch.setattr(serve, "ExecutorRecovery", ShutdownProbe)
    monkeypatch.setattr(serve, "CancelReconciler", lambda client: ShutdownProbe())
    monkeypatch.setattr(serve, "StrandedTurnReconciler", lambda client: ShutdownProbe())
    backend = FilesystemBlobStore(root=tmp_path)
    booted: list[object] = []

    def state(fleet: serve.Fleet) -> SimpleNamespace:
        return SimpleNamespace(
            state=SimpleNamespace(
                fleet=fleet,
                dbos=object(),
                writeback_poller=None,
                mid_turn_reply_poller=None,
                surface_listeners=(),
                surface_boots=(booted.append,),
                blob=WorkspaceBlobStore(backend=backend),
                configured_sources=(),
            )
        )

    async with serve._serve_lifespan(state(serve.WHOLE_FLEET)):
        pass
    async with serve._serve_lifespan(state(serve.JOBS_FLEET)):
        pass

    assert booted == [FleetBlobStore(backend=backend)]


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
            fleet=serve.WHOLE_FLEET,
            dbos=object(),
            writeback_poller=None,
            mid_turn_reply_poller=None,
            surface_listeners=(),
            surface_boots=(),
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


async def _found_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def test_the_lifespan_registers_configured_sources_into_the_one_workspace(
    db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(serve, "ExecutorRecovery", ShutdownProbe)
    monkeypatch.setattr(serve, "CancelReconciler", lambda client: ShutdownProbe())
    monkeypatch.setattr(serve, "StrandedTurnReconciler", lambda client: ShutdownProbe())
    app = SimpleNamespace(
        state=SimpleNamespace(
            fleet=serve.JOBS_FLEET,
            dbos=object(),
            writeback_poller=None,
            mid_turn_reply_poller=None,
            surface_listeners=(),
            surface_boots=(),
            configured_sources=(
                SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(tmp_path))),
            ),
        )
    )
    only = await _found_workspace()
    async with serve._serve_lifespan(app):
        pass
    async with workspace_tx() as connection:
        registered = (
            (await connection.execute(sa.select(tables.source.c.workspace_id))).scalars().all()
        )
    assert registered == [only]

    await _found_workspace()
    with pytest.raises(SeveralWorkspaces):
        async with serve._serve_lifespan(app):
            pass


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


def test_model_bindings_prefers_the_ufo_prefixed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "sk-ant-ufo-scoped")
    anthropic = Bind(
        host="api.anthropic.com",
        header="x-api-key",
        secret=UFO_MODELS_SECRET,
        env="ANTHROPIC_API_KEY",
    )
    assert model_bindings(_hosted_config()) == (
        (HostEntry(host="api.anthropic.com"),),
        (anthropic,),
    )
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    assert model_bindings(_hosted_config())[1] == (anthropic,)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    hosts, binds = model_bindings(_hosted_config())
    assert hosts == (HostEntry(host="api.anthropic.com"), HostEntry(host="api.openai.com"))
    assert binds == (
        anthropic,
        Bind(
            host="api.openai.com",
            header="authorization",
            secret=UFO_MODELS_SECRET,
            env="OPENAI_API_KEY",
        ),
    )


def test_model_key_envs_name_each_provider_hosts_key_env() -> None:
    assert MODEL_KEY_ENVS(_hosted_config()) == {
        "api.anthropic.com": "ANTHROPIC_API_KEY",
        "api.openai.com": "OPENAI_API_KEY",
        "openrouter.ai": "OPENROUTER_API_KEY",
    }
    renamed = _hosted_config().model_copy(
        update={
            "models": ModelsConfig(
                anthropic_api_key_env="DEPLOY_ANTHROPIC", openai_api_key_env="DEPLOY_OPENAI"
            )
        }
    )
    assert MODEL_KEY_ENVS(renamed)["api.anthropic.com"] == "DEPLOY_ANTHROPIC"
    assert MODEL_KEY_ENVS(renamed)["api.openai.com"] == "DEPLOY_OPENAI"


def test_model_bindings_export_the_sandbox_key_envs_when_the_deploy_renames_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPLOY_ANTHROPIC", "sk-ant-deploy")
    monkeypatch.setenv("DEPLOY_OPENAI", "sk-openai-deploy")
    renamed = _hosted_config().model_copy(
        update={
            "models": ModelsConfig(
                anthropic_api_key_env="DEPLOY_ANTHROPIC", openai_api_key_env="DEPLOY_OPENAI"
            )
        }
    )

    _, binds = model_bindings(renamed)

    assert [(bind.host, bind.env) for bind in binds] == [
        ("api.anthropic.com", ANTHROPIC_KEY_ENV),
        ("api.openai.com", OPENAI_KEY_ENV),
    ]
    assert {bind.env for bind in binds} <= sandbox_exported_env({})


def test_model_bindings_fails_loud_with_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="no model provider key set"):
        model_bindings(_hosted_config())


def _proxied_config(public_base_url: str | None = "https://serve.test") -> Config:
    return _hosted_config().model_copy(
        update={
            "sandbox": SandboxConfig(
                backend="local",
                proxy_url="https://proxy.test",
                preview_service="ufo-preview.test:8930",
            ),
            "connect": ConnectConfig(public_base_url=public_base_url),
        }
    )


def _proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(serve.PREVIEW_TOKEN_ENV, "preview-real")
    monkeypatch.setenv(CACHE_CONTROL_TOKEN_ENV, "cache-control-secret")
    monkeypatch.setenv(
        PROXY_PUBLIC_KEY_ENV,
        base64.b64encode(
            Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode(),
    )


def test_proxy_control_mounts_the_stamp_routes_and_builds_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _proxy_env(monkeypatch)
    app = FastAPI()

    control, rules, sessions = serve._proxy_control(
        app, _proxied_config(), (), None, RUN_TOKENS, _blob(), None
    )

    client = TestClient(app)
    assert control.resolver is rules
    assert control.stamp_key is not None
    assert control.preview == (("ufo-preview.test", 8930), "preview-real")
    assert sessions is not None
    assert sessions.base_url == "https://proxy.test"
    assert sessions.credentials is None
    assert control.cache_control_token == "cache-control-secret"
    assert client.post("/internal/egress/tool-bridge/request", json={}).status_code == 403
    assert client.post("/internal/egress/preview/render", content=b"x").status_code == 403
    assert client.post("/internal/git-credential", json={}).status_code == 401
    answered = client.post(
        "/internal/git-credential",
        json={},
        headers={"authorization": "Bearer cache-control-secret"},
    )
    assert (answered.status_code, answered.json()) == (200, {"principal": "public"})


def test_proxy_control_requires_the_proxy_public_key_when_a_proxy_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _proxy_env(monkeypatch)
    for value in (None, "", "not-a-key"):
        if value is None:
            monkeypatch.delenv(PROXY_PUBLIC_KEY_ENV)
        else:
            monkeypatch.setenv(PROXY_PUBLIC_KEY_ENV, value)
        with pytest.raises(RuntimeError, match=PROXY_PUBLIC_KEY_ENV):
            serve._proxy_control(FastAPI(), _proxied_config(), (), None, RUN_TOKENS, _blob(), None)


def test_proxy_control_requires_an_https_public_base_url_when_a_proxy_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _proxy_env(monkeypatch)
    for base in (None, "http://serve.test"):
        with pytest.raises(RuntimeError, match=r"\[connect\] public_base_url"):
            serve._proxy_control(
                FastAPI(), _proxied_config(base), (), None, RUN_TOKENS, _blob(), None
            )


def test_preview_settings_pair_the_service_with_its_real_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(
            backend="local",
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


def test_proxy_control_boots_with_no_proxy_url_and_no_stamp_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ufoctl serve` alone comes up with no proxy service and no deploy secret beside it — the
    documented zero-services default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(CACHE_CONTROL_TOKEN_ENV, raising=False)
    monkeypatch.delenv(PROXY_PUBLIC_KEY_ENV, raising=False)
    app = FastAPI()

    control, _, sessions = serve._proxy_control(
        app, _local_config(), (), None, RUN_TOKENS, _blob(), None
    )

    client = TestClient(app)
    assert sessions is None
    assert control.stamp_key is None
    assert control.cache_control_token
    assert client.post("/internal/egress/tool-bridge/request", json={}).status_code == 404
    assert client.post("/internal/git-credential", json={}).status_code == 401


def test_shared_owner_dsn_prefers_the_env_over_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared service opens owner_tx's engine from UFO_OWNER_DSN, whose contract is a plain
    libpq URL."""
    monkeypatch.setenv(OWNER_DSN_ENV, OWNER_LIBPQ_DSN)
    assert serve._shared_owner_dsn(_local_config()) == OWNER_LIBPQ_DSN


def test_shared_owner_dsn_falls_back_to_config_owner_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///ufo.db", owner_url=OWNER_LIBPQ_DSN),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
        sandbox=SandboxConfig(backend="local"),
    )
    assert serve._shared_owner_dsn(config) == OWNER_LIBPQ_DSN


def test_shared_owner_dsn_fails_loud_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OWNER_DSN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=OWNER_DSN_ENV):
        serve._shared_owner_dsn(_local_config())


def _captured_rules(monkeypatch: pytest.MonkeyPatch) -> list[PerAgentRules]:
    built: list[PerAgentRules] = []

    def capture(**fields: object) -> PerAgentRules:
        built.append(PerAgentRules(**fields))
        return built[-1]

    monkeypatch.setattr(serve, "PerAgentRules", capture)
    return built


async def _compiled(rules: PerAgentRules, workspace_id: UUID, agent_id: UUID) -> SessionPolicy:
    with ws(workspace_id), agent(agent_id):
        return await rules.session_policy(PolicyScope(workspace_id, None, True, True, "run-token"))


async def test_the_proxy_resolver_reads_keyed_slots_per_workspace(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    slot = CredentialSlot(
        name="byok",
        description="a workspace key the proxy swaps onto the wire",
        injection=InjectionTarget(host="api.inj.test", header="authorization", env="BYOK_KEY"),
    )
    manifest = Manifest(name="inj", version="1", credentials=(slot,))
    built = _captured_rules(monkeypatch)
    credentials = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await asyncio.to_thread(
        serve._proxy_control,
        FastAPI(),
        _local_config(),
        (manifest,),
        credentials,
        RUN_TOKENS,
        _blob(),
        None,
    )
    (rules,) = built
    claims = deploy_claims((manifest,))
    assert rules.slots == WorkspaceSlots(
        deploy=(slot,), claimed_slots=claims.slots, claimed_env=claims.env
    )
    keyed, unkeyed = await _workspace_agent(), await _workspace_agent()
    with ws(keyed[0]):
        await credentials.put(keyed[0], "byok", "byok-real-secret")

    filled = await _compiled(rules, *keyed)
    empty = await _compiled(rules, *unkeyed)

    byok = Bind(host="api.inj.test", header="authorization", secret="byok", env="BYOK_KEY")
    assert byok in filled.bind
    assert byok not in empty.bind
    assert filled.hosts == (HostEntry(host="api.anthropic.com"), HostEntry(host="api.inj.test"))
    assert "byok-real-secret" not in filled.model_dump_json()


async def _workspace_agent() -> tuple[UUID, UUID]:
    workspace_id, agent_id = uuid4(), uuid4()
    async with ufo.db.workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


def test_the_proxy_resolver_base_admits_the_s3_artifact_store_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "serve-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "serve-test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    built = _captured_rules(monkeypatch)
    store = S3BlobStore(bucket="ufo-blobs", region="us-east-1")

    serve._proxy_control(FastAPI(), _local_config(), (), None, RUN_TOKENS, store, None)

    (rules,) = built
    policy = asyncio.run(_compiled(rules, uuid4(), uuid4()))
    assert policy.hosts == (
        HostEntry(host="api.anthropic.com"),
        HostEntry(host="ufo-blobs.s3.amazonaws.com"),
    )
    assert [bind.host for bind in policy.bind] == ["api.anthropic.com"]


def test_the_proxy_resolver_routes_the_bridge_and_preview_to_the_public_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.setenv(serve.PREVIEW_TOKEN_ENV, "preview-real")
    built = _captured_rules(monkeypatch)
    previewing = SandboxConfig(backend="local", preview_service="ufo-preview.test:8930")
    served = _local_config().model_copy(
        update={
            "sandbox": previewing,
            "connect": ConnectConfig(public_base_url="https://serve.test/"),
        }
    )
    unserved = served.model_copy(update={"connect": ConnectConfig()})

    serve._proxy_control(FastAPI(), served, (), None, RUN_TOKENS, _blob(), None)
    serve._proxy_control(FastAPI(), unserved, (), None, RUN_TOKENS, _blob(), None)

    routed, unrouted = built
    stamp = {RUN_HEADER: "run-token"}
    assert asyncio.run(_compiled(routed, uuid4(), uuid4())).routes == (
        Route(
            host="preview.ufo.internal",
            upstream="https://serve.test/internal/egress/preview",
            headers=stamp,
        ),
        Route(
            host="tools.ufo.internal",
            upstream="https://serve.test/internal/egress/tool-bridge",
            headers=stamp,
        ),
    )
    assert (unrouted.bridge_upstream, unrouted.preview_upstream) == (None, None)
    assert asyncio.run(_compiled(unrouted, uuid4(), uuid4())).routes == ()


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

    def _endpoint(_request: object) -> None: ...

    prefixes = ("/login", "/logout", "/v1/onboard")
    app = FastAPI()
    for path in ("/surface/web", "/v1/connect/callback", "/artifacts/{artifact_id}/{filename}"):
        app.add_route(path, _endpoint)
    serve._assert_no_reserved_routes(app, prefixes)
    app.add_route("/v1/onboard/web", _endpoint)
    serve._assert_no_reserved_routes(app, ())
    with pytest.raises(RuntimeError, match=r"gateway_prefixes .*/v1/onboard/web"):
        serve._assert_no_reserved_routes(app, prefixes)


def test_serve_refuses_a_page_kit_that_names_no_file_before_it_opens_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = tmp_path / "page_kit.tar.gz"
    config = _local_config().model_copy(update={"sites": SitesConfig(page_kit=kit)})

    def opened(url: str) -> None:
        raise AssertionError(f"opened {url}")

    monkeypatch.setattr(serve, "load_config", lambda: config)
    monkeypatch.setattr(serve, "init_o11y", lambda endpoint: None)
    monkeypatch.setattr(serve, "init_db", opened)
    with pytest.raises(RuntimeError, match=r"\[sites\] page_kit names no file"):
        serve.run(serve.WHOLE_FLEET)
    kit.write_bytes(b"kit")
    with pytest.raises(AssertionError, match="opened"):
        serve.run(serve.WHOLE_FLEET)


def test_serve_installs_the_configured_operator_rule_before_it_opens_the_owner_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    links = DebuggerConfig(turn_urls={"Trace": "https://traces.test/{trace_id}"})
    base = _local_config().model_copy(
        update={"pack": PackConfig(name="sample_pack"), "debugger": links}
    )

    def opened(dsn: str) -> None:
        raise AssertionError("opened the owner database")

    monkeypatch.setenv(OWNER_DSN_ENV, OWNER_LIBPQ_DSN)
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(serve, "init_o11y", lambda endpoint: None)
    monkeypatch.setattr(serve, "init_db", lambda url: None)
    monkeypatch.setattr(serve, "init_owner_db", opened)
    monkeypatch.setattr(
        serve,
        "load_config",
        lambda: base.model_copy(update={"operator": OperatorConfig(rule="nobody")}),
    )
    with pytest.raises(ValueError, match=r"\[operator\] rule 'nobody' names no registered rule"):
        serve.run(serve.WHOLE_FLEET)
    monkeypatch.setattr(
        serve,
        "load_config",
        lambda: base.model_copy(update={"operator": OperatorConfig(rule=SAMPLE_OPERATOR_RULE)}),
    )
    try:
        with pytest.raises(AssertionError, match="opened the owner database"):
            serve.run(serve.WHOLE_FLEET)
        setup = installed_operator()
        assert isinstance(setup.rule, SampleOperatorRule)
        assert setup.links == links
    finally:
        install_operator(None)


def test_the_boot_hands_the_sign_in_gateway_and_kit_settings_to_every_consumer() -> None:
    boot = ast.parse(inspect.getsource(serve.run))
    calls = {
        node.func.id: node
        for tree in (boot, ast.parse(inspect.getsource(loop_queue._run_turn)))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    passed = {
        (name, keyword.arg): ast.unparse(keyword.value)
        for name in ("_mount_shared_surfaces", "TurnEngine")
        for keyword in calls[name].keywords
    }
    state = {
        ast.unparse(node.targets[0]): ast.unparse(node.value)
        for node in ast.walk(boot)
        if isinstance(node, ast.Assign)
    }
    assert state["app.state.sign_in_path"] == "config.serve.sign_in_path"
    assert passed["_mount_shared_surfaces", "sign_in_path"] == "config.serve.sign_in_path"
    assert passed["_mount_shared_surfaces", "sites"] == "config.sites"
    assert [ast.unparse(arg) for arg in calls["_assert_no_reserved_routes"].args] == [
        "app",
        "config.serve.gateway_prefixes",
    ]
    assert passed["TurnEngine", "sign_in_path"] == "runtime.config.serve.sign_in_path"
    assert passed["TurnEngine", "page_kit"] == "runtime.config.sites.page_kit"


def test_the_boot_and_the_mount_share_one_admission_construction() -> None:
    """`run()` and `_mount_shared_surfaces` each hold an admission, and a fix applied to one used
    to leave the other — the one bound into the member surface — unchanged."""
    tree = ast.parse(Path(serve.__file__).read_text())
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("Admission") == 1
    assert calls.count("_admission") == 2
    assert "spend" in inspect.signature(serve._admission).parameters


def test_boot_builds_each_spend_gate_once_for_the_gates_and_the_ledger() -> None:
    (manifest,) = (m for m in load_manifests() if m.name == sample.NAME)
    homed = replace(
        manifest, surfaces=(replace(manifest.surfaces[0], home=True), *manifest.surfaces[1:])
    )
    specs = {spec.id: spec for spec in CORE_MODEL_SPECS}
    registry = ModelRegistry(specs=specs, pricing=CORE_PRICING, auto_model=next(iter(specs)))
    spend, ledger = serve.deploy_spend((homed,), registry, "https://ufo.example.com")
    assert spend.gates == (
        SampleGate(
            GateDeploy(
                public_base_url="https://ufo.example.com", home_surface=manifest.surfaces[0].name
            )
        ),
    )
    assert ledger.gates is spend.gates
    assert spend.key_slot_for == registry.key_slot_for
    assert spend.own_key_slots == model_key_slots(registry)
