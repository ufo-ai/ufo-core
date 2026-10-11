"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import hmac
import json
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
import uvicorn
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dbos import DBOS, DBOSClient
from fastapi import FastAPI, WebSocket
from openfeature.provider import FeatureProvider
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ufo.blob import (
    BlobStore,
    FleetBlobStore,
    WorkspaceBlobStore,
    blob_store_for,
)
from ufo.browser import CdpProvider
from ufo.config import (
    IN_PROCESS_BACKEND,
    Config,
    SitesConfig,
    load_config,
)
from ufo.db import (
    current_workspace,
    dispose_loop_engines,
    init_db,
    init_owner_db,
    verify_db_reachable,
)
from ufo.flags import init_flags
from ufo.harness.document_renderer import DocumentRenderer
from ufo.harness.durability import ReplaySafeSerializer, replay_safe_client
from ufo.harness.models.catalog_skill import model_catalog_skill
from ufo.harness.models.interface import AUTO_MODEL
from ufo.harness.models.registry import ModelRegistry, model_registry
from ufo.harness.o11y import init_o11y, init_service_checks, log, warn
from ufo.harness.sandbox.conversation import ConversationSandbox
from ufo.harness.sandbox.exec_env import ProbeEnv
from ufo.harness.sandbox.preview import parse_preview_service
from ufo.harness.sandbox.select import select_carriers
from ufo.harness.sandbox.session import RunTokenCodec
from ufo.harness.sandbox.site_report import SiteReports
from ufo.harness.sandbox.terminal import Terminals, TerminalTransport
from ufo.host.assemble import HostEnvironment
from ufo.host.environment import store_environment_document, store_environment_file
from ufo.host.ext.loader import (
    CORE_OBJECT_KINDS,
    MemberObjectRegistry,
    connection_hooks,
    connector_clis,
    core_object_kinds,
    durable_surfaces,
    embed_backend,
    frame_admissible,
    index_backend,
    load_manifests,
    member_object_registry,
    member_skill_listing,
    memory_search,
    proxy_credentials,
    skill_registry,
    turn_subagent_grants,
    turn_subagents,
    validate_ext_tools,
    workspace_slot_source,
)
from ufo.product import ProductCensus
from ufo.proxy_serve import MODEL_KEY_ENVS, OWNER_DSN_ENV, model_bindings
from ufo.runtime.access.connectors import (
    AuthProxy,
    ConnectorEntry,
    ConnectorRegistry,
    SourceCredentialResolver,
)
from ufo.runtime.access.credentials import (
    CredentialStore,
    deploy_env,
)
from ufo.runtime.access.egress_control import (
    CACHE_CONTROL_TOKEN_ENV,
    PREVIEW_RELAY_TIMEOUT_SECONDS,
    PROXY_PUBLIC_KEY_ENV,
    EgressControl,
    PreviewRelay,
    load_proxy_public_key,
)
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    HostEntry,
    connector_transfer_hosts,
    derive_artifact_store_hosts,
    derive_manifest_internet,
)
from ufo.runtime.access.grants import ConnectFlow, GrantStore, OAuthProvider, install_connect_flow
from ufo.runtime.access.proxy_sessions import PROXY_CALL_TIMEOUT_SECONDS, ProxySessions
from ufo.runtime.access.turn_sessions import DeploySessions, ProbeSessions
from ufo.runtime.access.vault import VaultReads
from ufo.runtime.background_tasks import BackgroundTaskSweep
from ufo.runtime.billing.accounting import UNGATED_LEDGER, Ledger
from ufo.runtime.billing.spend import NO_SPEND_GATES, GateDeploy, SpendGates, built_gates
from ufo.runtime.context_boundary import (
    CORE_FLAGS,
    select_context_boundary,
    select_flagged_context_boundary,
)
from ufo.runtime.delivery import DeliverySweep
from ufo.runtime.ext.context import ConversationProbes, CredentialAccess, ModelAccess
from ufo.runtime.ext.context import context_for as extension_context_for
from ufo.runtime.ext.conversation_slots import BoundConversationSlot
from ufo.runtime.ext.deploy import DeployContext
from ufo.runtime.ext.manifest import (
    AuthProxySpec,
    CarrierSpec,
    CdpProviderSpec,
    FlagProviderSpec,
    FlagSpec,
    Manifest,
    NotRegisteredError,
    SearchProviderSpec,
    conversation_slot_declarations,
    declared_slots,
    minted_slots,
    open_connector_namespace,
)
from ufo.runtime.ext.operator import OperatorSetup, install_operator, select_operator_rule
from ufo.runtime.ext.surface import (
    SURFACE_MODEL_JOB_PREFIX,
    SurfaceAuth,
    SurfaceBoot,
    SurfaceContext,
    SurfaceIdentityContext,
    SurfaceListenerRunner,
    SurfaceModel,
    SurfaceRoute,
    SurfaceSocket,
    SurfaceSpec,
    WorkspaceResolver,
    handshake_request,
)
from ufo.runtime.ext.writeback import (
    MidTurnReplyPoller,
    WritebackPoller,
    mid_turn_reply_workspaces,
    writeback_workspaces,
)
from ufo.runtime.hub import Hub, InProcessHub
from ufo.runtime.indexing import EmbedClient, IndexBackend
from ufo.runtime.jobs import (
    JOB_QUEUE_NAME,
    InvokerFactory,
    JobRunner,
    PageChangeRunner,
    TurnDispatcher,
    bindings_from,
    core_jobs,
    model_key_slots,
    register_job_queue,
)
from ufo.runtime.media.preview_renderer import (
    PREVIEW_SERVICE_URL_ENV,
    PREVIEW_TOKEN_ENV,
    PreviewRenderer,
)
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.memory import DEFAULT_MEMORY_SEARCH_PROVIDER, MemorySearch
from ufo.runtime.profiles import CORE_SUBAGENT_PROFILES
from ufo.runtime.provisioning import Provisioning
from ufo.runtime.queue import Runtime, init_runtime, register_turn_queues
from ufo.runtime.runtime_instance import (
    CancelReconciler,
    ExecutorRecovery,
    Heartbeat,
    StrandedTurnReconciler,
    record_fleet_seat,
)
from ufo.runtime.search import SearchProvider
from ufo.runtime.skills.runtime import RuntimeSkill, SkillRegistry, SystemSkillBundle
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SourceBackend,
    SourceIdentityResolver,
    SyncDriver,
    register_sources,
)
from ufo.runtime.steps import DurableTurnSteps
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.surfaces.admission import (
    Admission,
    AdmissionInvoker,
    ConnectResume,
    MemberAdmission,
)
from ufo.runtime.surfaces.artifacts import router as artifacts_router
from ufo.runtime.surfaces.cli import CONNECT_CALLBACK_PATH, callback_router, portal_url
from ufo.runtime.surfaces.hub_tail import HubTailer
from ufo.runtime.surfaces.stop import MemberStop
from ufo.runtime.tool_bridge import ToolBridge
from ufo.runtime.tools.bridge import bridge_tools
from ufo.runtime.turns.ambient_reply import AMBIENT_REPLY_JOB, AmbientReplyClassifier
from ufo.runtime.workspace import init_workspace_credentials, sole_workspace, ws
from ufo.schema.records import (
    DBOS_APP_NAME,
    DBOS_APP_VERSION,
    DBOS_MAX_EXECUTOR_THREADS,
    DBOS_SYSTEM_DATABASE_POOL_SIZE,
    EXPRESS_QUEUE_NAME,
    TURN_QUEUE_NAME,
    UNSCOPED_EXPRESS_QUEUE_NAME,
    UNSCOPED_TURN_QUEUE_NAME,
    RuntimeIdentity,
)
from ufo.sdk.http import same_origin_handshake

FOREIGN_HANDSHAKE = "This connection did not come from the page it addresses."
DEPLOY_ROUTE_PREFIX = "/internal"
RUNTIME_REVISION_ENV = "UFO_RUNTIME_REVISION"
RUNTIME_IMAGE_ENV = "UFO_RUNTIME_IMAGE"


@dataclass(frozen=True)
class Fleet:
    """Which durable work a serve process claims. Every process runs the same composition root over
    the same image and config; the fleet is the whole difference between them — the DBOS queues this
    executor dequeues from, and whether it carries the surfaces' live delivery (their inbound
    listeners and the writeback pollers).

    A turn is model rounds and network waits; indexing a changed page is chunking and embedding,
    which holds the GIL. Run on one interpreter they contend, and a large reindex starves the
    portal, the turn loop, and every surface at once. Splitting them across fleets is what makes
    that impossible rather than unlikely — and it splits their capacity too, since each fleet's
    replica count is now its own.

    The queue sets partition the application registry: turns and express carry member work and
    jobs carries background executions. Recurring ticks use DBOS's internal queue, which every
    executor drains. A fleet with no queues serves routes and recurring job ticks only.
    `WHOLE_FLEET` claims every application queue for a deploy that is one process (a node, a
    stack, an eval run). Every fleet names its queues in full — a queue no fleet claims is one
    nothing would ever dequeue, which `test_fleet.py` refuses."""

    name: str
    queues: tuple[str, ...]
    surfaces: bool


TURNS_FLEET = Fleet(
    name="turns",
    queues=(
        TURN_QUEUE_NAME,
        EXPRESS_QUEUE_NAME,
        UNSCOPED_TURN_QUEUE_NAME,
        UNSCOPED_EXPRESS_QUEUE_NAME,
    ),
    surfaces=True,
)
JOBS_FLEET = Fleet(name="jobs", queues=(JOB_QUEUE_NAME,), surfaces=False)
API_FLEET = Fleet(name="api", queues=(), surfaces=False)
WHOLE_FLEET = Fleet(name="all", queues=TURNS_FLEET.queues + JOBS_FLEET.queues, surfaces=True)
FLEETS = {fleet.name: fleet for fleet in (WHOLE_FLEET, TURNS_FLEET, JOBS_FLEET, API_FLEET)}


def _payload_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def _runtime_identity(config: Config, carrier: CarrierSpec) -> RuntimeIdentity:
    revision = os.environ.get(RUNTIME_REVISION_ENV, "").strip()
    image = os.environ.get(RUNTIME_IMAGE_ENV, "").strip()
    if bool(revision) != bool(image):
        raise RuntimeError(f"{RUNTIME_REVISION_ENV} and {RUNTIME_IMAGE_ENV} must be set together")
    image_digest = (image.rpartition("@")[2] if "@" in image else image) or None
    carrier_digest = None if carrier.runtime_digest is None else carrier.runtime_digest()
    return RuntimeIdentity(
        revision=revision or None,
        image_digest=image_digest,
        config_digest=_payload_digest(config.model_dump(mode="json")),
        sandbox_backend=config.sandbox.backend,
        sandbox_digest=_payload_digest(
            {
                "config": config.sandbox.model_dump(mode="json"),
                "carrier": carrier_digest,
            }
        ),
    )


def _assert_no_reserved_routes(app: FastAPI, prefixes: tuple[str, ...]) -> None:
    """The ingress hands these prefixes to the gateway, so a fleet route under one is silently
    shadowed."""
    conflicts = [
        route.path
        for route in app.routes
        if isinstance(route, Route) and any(route.path.startswith(prefix) for prefix in prefixes)
    ]
    if conflicts:
        raise RuntimeError(
            f"shared fleet mounts routes under [serve] gateway_prefixes {prefixes}: {conflicts}"
        )


def _admission(
    dbos_client: DBOSClient, manifests: tuple[Manifest, ...], hub: Hub, spend: SpendGates
) -> Admission:
    """`gates.py` refuses a second `Admission(...)` in shipped code."""
    return Admission(
        dbos=dbos_client,
        durable_surfaces=durable_surfaces(manifests),
        hub=hub,
        spend=spend,
    )


def deploy_spend(
    manifests: tuple[Manifest, ...], registry: ModelRegistry, public_base_url: str | None
) -> tuple[SpendGates, Ledger]:
    """Build the active manifests' spend gates once, in lockfile order, and the two handles every
    turn, job, surface, and ledger write reaches them through."""
    gates = built_gates(
        tuple(spec for manifest in manifests for spec in manifest.spend_gates),
        GateDeploy(public_base_url=public_base_url, home_surface=home_surface(manifests)),
    )
    return (
        SpendGates(
            gates=gates,
            key_slot_for=registry.key_slot_for,
            own_key_slots=model_key_slots(registry),
        ),
        Ledger(gates=gates),
    )


def run(fleet: Fleet) -> None:
    """Start one process of `fleet`: it serves every workspace, resolving the workspace per request
    (from the caller's token) and per turn (from the workflow argument), scoping each transaction by
    the ambient `current_workspace`. The fleet decides only which durable work this process claims —
    the boot below is the same one every fleet runs."""
    config = load_config()
    if config.sites.page_kit is not None and not config.sites.page_kit.is_file():
        raise RuntimeError(f"[sites] page_kit names no file: {config.sites.page_kit}")
    init_o11y(config.o11y.otlp_endpoint)
    init_service_checks(
        config.o11y.datadog_check_url,
        config.o11y.datadog_env,
        os.environ.get(config.o11y.datadog_api_key_env),
    )
    init_db(config.database.url)
    manifests = load_manifests(config.pack.name)
    install_operator(
        OperatorSetup(
            rule=select_operator_rule(config.operator.rule, manifests), links=config.debugger
        )
    )
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise RuntimeError(
            f"credential key env {config.credentials.key_env!r} is unset but jobs are registered"
        )
    credentials = CredentialStore(fernet=Fernet(key.encode()))
    init_owner_db(_shared_owner_dsn(config))
    _one_shot(verify_db_reachable())
    instance_id = uuid4()
    _one_shot(record_fleet_seat(instance_id))
    heartbeat = Heartbeat(instance_id=instance_id)
    threading.Thread(
        target=lambda: asyncio.run(heartbeat.run()), name="instance-heartbeat", daemon=True
    ).start()
    deploy_actions = validate_ext_tools(manifests, credentials)
    _validate_requires(config, manifests, credentials)
    init_workspace_credentials(credentials)
    init_flags(_select_flag_provider(config, manifests))
    blob_backend = blob_store_for(config.blob)
    blob = WorkspaceBlobStore(backend=blob_backend)
    fleet_blob = FleetBlobStore(backend=blob_backend)
    artifact_secret = os.environ.get(config.artifacts.token_secret_env, "")
    hub = _select_hub(config, manifests)
    dbos_client = replay_safe_client(config.database.system_url)
    carriers = select_carriers(config, manifests)
    carrier, carrier_spec = carriers.carrier, carriers.spec
    runtime_identity = _runtime_identity(config, carrier_spec)
    registry = model_registry(config, manifests)
    embed = embed_backend(manifests, config.memory.embed_backend, credentials)
    index = index_backend(manifests, config.memory.index_backend, credentials)
    memory = memory_search(manifests, credentials, index, embed)
    search = _select_search_provider(config, manifests, credentials)
    subagents = SubagentRegistry((*CORE_SUBAGENT_PROFILES, *turn_subagents(manifests)))
    subagent_grants = turn_subagent_grants(manifests)
    skills = skill_registry(manifests, (model_catalog_skill(registry),))
    system_skill_bundle = SystemSkillBundle.from_skills(skills.bundled_skills())
    connectors = _connector_registry(config, manifests, credentials)
    run_tokens = RunTokenCodec.from_env()
    preview = _preview_settings(config)
    document_renderer = (
        DocumentRenderer(service_url=f"http://{preview[0][0]}:{preview[0][1]}", token=preview[1])
        if preview is not None
        else None
    )
    browser_home = home_surface(manifests)
    spend, ledger = deploy_spend(manifests, registry, config.connect.public_base_url)
    admission = _admission(dbos_client, manifests, hub, spend)

    def invoker_for(workspace_id: UUID) -> AdmissionInvoker:
        return AdmissionInvoker(admission=admission, workspace_id=workspace_id)

    app = FastAPI(lifespan=_serve_lifespan)
    tailer = HubTailer(hub=hub, spend=spend)
    proxy_sessions = deploy_proxy_sessions(config, manifests)
    rules = deploy_rules(
        config, manifests, credentials, _one_shot(derive_artifact_store_hosts(blob_backend))
    )
    deploy_sessions = (
        None if proxy_sessions is None else DeploySessions(proxy_sessions, rules, run_tokens)
    )
    tool_bridge = ToolBridge(
        dbos=dbos_client,
        tailer=tailer,
        tools=bridge_tools(manifests),
        subagents=subagents,
        subagent_grants=subagent_grants,
        sessions=deploy_sessions,
        actions=deploy_actions,
    )
    _proxy_control(app, config, rules, run_tokens, tool_bridge, proxy_sessions)
    sandboxes = ConversationSandbox(
        carrier=carrier,
        backend=config.sandbox.backend,
        off_cluster=carrier_spec.off_cluster,
        resume_carriers=carriers.resume,
        image_ref=config.sandbox.image_ref,
        workspace_root=config.sandbox.workspace_root,
        terminals=_select_terminal_transport(config, manifests, fleet_blob),
        document_renderer=document_renderer,
        system_skill_archive=system_skill_bundle.archive,
    )
    probes = ConversationProbes(
        sandboxes,
        None if proxy_sessions is None else ProbeSessions(proxy_sessions, rules),
        ProbeEnv(
            grants=GrantStore() if credentials is not None else None,
            clis=connector_clis(manifests),
            credentials=credentials,
            slots=workspace_slot_source(manifests),
        ).exports,
    )
    runtime = Runtime(
        config=config,
        blob=blob,
        sandboxes=sandboxes,
        hub=hub,
        cdp_provider=_select_cdp_provider(config, manifests, credentials),
        search_provider=search,
        connectors=connectors,
        run_tokens=run_tokens,
        dbos=dbos_client,
        invoker_for=invoker_for,
        subagents=subagents,
        subagent_grants=subagent_grants,
        manifests=manifests,
        environment=HostEnvironment(
            manifests=manifests,
            credentials=credentials,
            index=index,
            embed=embed,
            search=search,
            blob=blob,
            tailer=tailer,
            public_base_url=config.connect.public_base_url,
            home_surface=browser_home,
            artifact_token_secret=artifact_secret,
            invoker_for=invoker_for,
            probes=probes,
            spend=spend,
            ledger=ledger,
        ),
        registry=registry,
        skills=skills,
        credentials=credentials,
        index=index,
        embed=embed,
        memory=memory,
        artifact_token_secret=artifact_secret,
        site_previewer=(
            SitePreviewer(
                blob=blob,
                service_url=f"http://{preview[0][0]}:{preview[0][1]}",
                token=preview[1],
                ingress_public_url=config.sandbox.ingress_public_url,
            )
            if preview is not None and config.sandbox.ingress_public_url is not None
            else None
        ),
        spend=spend,
        ledger=ledger,
        home_surface=browser_home,
        tailer=tailer,
        rules=rules,
        sessions=proxy_sessions,
    )
    init_runtime(runtime)
    install_connect_flow(
        _connect_flow(
            credentials,
            config,
            manifests,
            index,
            embed,
            ConnectResume(admission),
            spend=spend,
            ledger=ledger,
        )
    )
    dbos = DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": config.database.system_url,
            "executor_id": str(instance_id),
            "max_executor_threads": DBOS_MAX_EXECUTOR_THREADS,
            "sys_db_pool_size": DBOS_SYSTEM_DATABASE_POOL_SIZE,
            "serializer": ReplaySafeSerializer(),
        }
    )
    DBOS.listen_queues(fleet.queues)
    DBOS.launch()
    register_turn_queues()
    register_job_queue()
    app.state.fleet = fleet
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.deploy_sessions = deploy_sessions
    app.state.instance_id = instance_id
    app.state.durable_surfaces = durable_surfaces(manifests)
    app.state.writeback_poller = None
    app.state.mid_turn_reply_poller = None
    app.state.surface_listeners = ()
    app.state.surface_boots = ()
    app.state.blob = blob
    app.state.artifact_token_secret = artifact_secret
    app.state.sign_in_path = config.serve.sign_in_path
    app.include_router(callback_router)
    app.include_router(artifacts_router)
    app.include_router(SiteReports(invoker_for=invoker_for).router())
    _mount_deploy_routes(
        app,
        manifests,
        blob,
        Provisioning(
            founded=tuple(spec for manifest in manifests for spec in manifest.workspace_founded)
        ),
        config.flags.backend,
    )
    sync_driver = SyncDriver(
        backends=_source_backends(manifests),
        blob=blob,
        postgres=config.database.url.startswith("postgresql"),
        source_credentials=SourceCredentialResolver(connectors),
        identity_resolvers=_source_identity_resolvers(manifests, credentials, blob),
        spend=spend,
    )
    unknown_backends = sorted(
        {entry.backend for entry in config.sources} - sync_driver.backends.keys()
    )
    if unknown_backends:
        raise RuntimeError(f"[[sources]] names unknown backends: {', '.join(unknown_backends)}")
    app.state.configured_sources = config.sources
    page_feed = CorePageFeed(blob=blob)
    _launch_jobs(runtime, invoker_for, sync_driver, page_feed, probes)
    _mount_ext_routes(
        app,
        manifests,
        credentials,
        index,
        embed,
        config.connect.public_base_url,
        spend,
        ledger,
        vault=VaultReads(
            credentials,
            workspace_slot_source(manifests),
            connector_clis(manifests),
            MODEL_KEY_ENVS(config),
        ),
    )
    _mount_shared_surfaces(
        app,
        manifests,
        credentials,
        blob,
        runtime.sandboxes,
        hub,
        dbos_client,
        artifact_secret,
        config.connect.public_base_url,
        config.sandbox.ingress_public_url,
        (AUTO_MODEL, *sorted(registry.specs)),
        deploy_sessions=deploy_sessions,
        probes=probes,
        runtime_identity=runtime_identity,
        connectors=connectors,
        spend=spend,
        ledger=ledger,
        ambient_reply=AmbientReplyClassifier(
            model=ModelAccess(
                replace(registry, auto_model=config.models.ambient_reply_model),
                AMBIENT_REPLY_JOB,
                spend,
                ledger,
            )
        ),
        ambient_reply_for=lambda model: AmbientReplyClassifier(
            model=ModelAccess(
                replace(registry, auto_model=registry.resolve(model)),
                AMBIENT_REPLY_JOB,
                spend,
                ledger,
            )
        ),
        surface_model=lambda name: ModelAccess(
            replace(registry, auto_model=config.models.background_jobs_model),
            f"{SURFACE_MODEL_JOB_PREFIX}{name}",
            spend,
            ledger,
        ),
        sandbox_sizes=carrier_spec.sizes,
        skills=skills,
        member_skill_listing=lambda: member_skill_listing(manifests, credentials, index, embed),
        memory=memory,
        sign_in_path=config.serve.sign_in_path,
        sites=config.sites,
        objects=member_object_registry(
            manifests,
            credentials,
            index,
            embed,
            public_base_url=config.connect.public_base_url,
            artifact_token_secret=artifact_secret,
            spend=spend,
            ledger=ledger,
        ),
    )
    _assert_no_reserved_routes(app, config.serve.gateway_prefixes)
    log("serve.started", fleet=fleet.name, host=config.serve.host, port=config.serve.port)
    try:
        uvicorn.run(
            app,
            host=config.serve.host,
            port=config.serve.port,
            log_level="warning",
            timeout_graceful_shutdown=config.serve.request_shutdown_seconds,
        )
    finally:
        _stop_executor(dbos, heartbeat, config.serve.graceful_shutdown_seconds)


def _one_shot[T](coro: Coroutine[Any, Any, T]) -> T:
    """A pooled connection abandoned to a closed loop can never be closed again."""

    async def step() -> T:
        try:
            return await coro
        finally:
            await dispose_loop_engines()

    return asyncio.run(step())


def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None:
    """`DBOS.destroy` bounds its force-cancel with a ten-second join, so a non-empty active set
    means work still runs here and retiring the seat would let a peer start a second execution."""
    DBOS.destroy(workflow_completion_timeout_sec=graceful_shutdown_seconds)
    active = dbos._active_workflows_set.activeList()
    if active:
        log("serve.seat_kept_for_active_workflows", workflows=len(active))
        return
    _one_shot(heartbeat.retire())


def _shared_owner_dsn(config: Config) -> str:
    """`owner_tx` must bypass RLS through the table-owner role, else the enumeration reads an unset
    `app.workspace_id` GUC."""
    dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if not dsn:
        raise RuntimeError(
            f"{OWNER_DSN_ENV} or [database] owner_url must be set for shared serve — "
            "owner_tx bypasses RLS with the owner role to enumerate every workspace the job sweeps "
            "fan across; without it the enumeration reads an unset app.workspace_id GUC and crashes"
        )
    return dsn


def _launch_jobs(
    runtime: Runtime,
    invoker_for: InvokerFactory,
    sync_driver: SyncDriver,
    page_feed: CorePageFeed,
    probes: ConversationProbes,
) -> None:
    page_change_runner = PageChangeRunner(
        manifests=runtime.manifests,
        pages=page_feed,
        invoker_factory=invoker_for,
        index=runtime.index,
        embed=runtime.embed,
        blob=runtime.blob,
        sandboxes=runtime.sandboxes,
        registry=runtime.registry,
        probes=probes,
        background_model=runtime.config.models.background_jobs_model,
        spend=runtime.spend,
        ledger=runtime.ledger,
    )
    preview_url = os.environ.get(PREVIEW_SERVICE_URL_ENV)
    preview_renderer = (
        PreviewRenderer(blob=runtime.blob, service_url=preview_url.rstrip("/"))
        if preview_url
        else None
    )
    bindings = bindings_from(
        runtime.manifests,
        core_jobs(
            sync_driver,
            TurnDispatcher(client=runtime.dbos, spend=runtime.spend),
            page_change_runner,
            DeliverySweep(invoker_for=invoker_for, registry=runtime.subagents),
            BackgroundTaskSweep(probes=probes, invoker_for=invoker_for, sessions=runtime.sessions),
            preview_renderer,
            ProductCensus(
                contributions=tuple(
                    spec for manifest in runtime.manifests for spec in manifest.census
                )
            ),
        ),
        disabled=frozenset(runtime.config.serve.disabled_jobs),
    )
    JobRunner(
        bindings=bindings,
        manifests=runtime.manifests,
        invoker_factory=invoker_for,
        index=runtime.index,
        embed=runtime.embed,
        pages=page_feed,
        blob=runtime.blob,
        sandboxes=runtime.sandboxes,
        registry=runtime.registry,
        probes=probes,
        background_model=runtime.config.models.background_jobs_model,
        spend=runtime.spend,
        ledger=runtime.ledger,
        public_base_url=runtime.config.connect.public_base_url,
        home_surface=home_surface(runtime.manifests),
    ).launch()


def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]:
    backends: dict[str, SourceBackend] = {FOLDER_BACKEND: FolderSource()}
    for manifest in manifests:
        credentials = CredentialAccess(
            declared=frozenset(slot.name for slot in manifest.credentials)
        )
        for provider in manifest.sources:
            if provider.backend in backends:
                raise RuntimeError(f"two extensions register source backend {provider.backend!r}")
            backends[provider.backend] = provider.build(credentials)
    return backends


def _source_identity_resolvers(
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    blob: WorkspaceBlobStore,
) -> dict[str, SourceIdentityResolver]:
    resolvers: dict[str, SourceIdentityResolver] = {}
    for manifest in manifests:
        declared = frozenset(slot.name for slot in manifest.credentials)
        for surface in manifest.surfaces:
            if surface.self_user_id is None:
                continue
            if surface.name in resolvers:
                raise RuntimeError(f"two surfaces resolve source identity for {surface.name!r}")

            async def resolve(
                workspace_id: UUID,
                handler=surface.self_user_id,
                slots=declared,
                store=credentials,
            ) -> str | None:
                async def credential(credential_slot: str) -> str:
                    if credential_slot not in slots:
                        raise ValueError(
                            f"surface identity reads undeclared credential slot {credential_slot!r}"
                        )
                    if store is None:
                        raise RuntimeError(
                            "surface identity reads a credential but no store is configured"
                        )
                    return await store.get(workspace_id, credential_slot)

                with ws(workspace_id):
                    return await handler(
                        SurfaceIdentityContext(
                            workspace_id=workspace_id,
                            blob=blob,
                            credential=credential,
                        )
                    )

            resolvers[surface.name] = resolve
    return resolvers


def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub:
    builders: dict[str, Callable[[str | None], Hub]] = {
        IN_PROCESS_BACKEND: lambda _url: InProcessHub()
    }
    for manifest in manifests:
        for spec in manifest.hubs:
            if spec.backend in builders:
                raise RuntimeError(f"two extensions register hub backend {spec.backend!r}")
            builders[spec.backend] = spec.build
    build = builders.get(config.hub.backend)
    if build is None:
        raise NotRegisteredError(
            f"config selects hub backend {config.hub.backend!r} but no extension registers it"
        )
    return build(config.hub.url)


def _select_terminal_transport(
    config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore
) -> TerminalTransport:
    """A cross-process hub declares a multi-instance fleet, where the in-process terminal rendezvous
    cannot reach the pod holding the member's connection."""
    if config.terminal.backend == IN_PROCESS_BACKEND and config.hub.backend != IN_PROCESS_BACKEND:
        raise RuntimeError(
            f"terminal.backend is the in-process transport but hub.backend is "
            f"{config.hub.backend!r}, a cross-process backend — a multi-instance fleet's held "
            "connection and its turn's workflow land on different pods, which the process-local "
            "terminal transport cannot serve; select a cross-process transport (terminal.backend = "
            '"redis")'
        )
    builders: dict[str, Callable[[str | None, BlobStore], TerminalTransport]] = {
        IN_PROCESS_BACKEND: lambda _url, _blob: Terminals()
    }
    for manifest in manifests:
        for spec in manifest.terminal_transports:
            if spec.backend in builders:
                raise RuntimeError(
                    f"two extensions register terminal transport backend {spec.backend!r}"
                )
            builders[spec.backend] = spec.build
    build = builders.get(config.terminal.backend)
    if build is None:
        raise NotRegisteredError(
            f"config selects terminal transport backend {config.terminal.backend!r} "
            "but no extension registers it"
        )
    return build(config.hub.url, blob)


def _select_cdp_provider(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> CdpProvider | None:
    specs: dict[str, tuple[CdpProviderSpec, Manifest]] = {}
    for manifest in manifests:
        for spec in manifest.cdp_providers:
            if spec.backend in specs:
                raise RuntimeError(f"two extensions register cdp provider {spec.backend!r}")
            specs[spec.backend] = (spec, manifest)
    found = specs.get(config.browser.cdp_provider)
    if found is None:
        return None
    spec, manifest = found
    declared = frozenset(slot.name for slot in manifest.credentials)
    if declared and credentials is None:
        raise RuntimeError(
            f"cdp provider {config.browser.cdp_provider!r} declares credential slots "
            "but no credential key is set"
        )
    return spec.build(CredentialAccess(declared=declared))


def _validate_requires(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> None:
    for manifest in manifests:
        for seam in manifest.requires:
            check = _REQUIRED_SEAM_CHECKS.get(seam)
            if check is None:
                raise RuntimeError(
                    f"extension {manifest.name!r} requires unknown seam {seam!r} "
                    f"(have {sorted(_REQUIRED_SEAM_CHECKS)})"
                )
            try:
                check(config, manifests, credentials)
            except Exception as error:
                raise RuntimeError(
                    f"extension {manifest.name!r} requires the {seam!r} seam but it is "
                    f"unavailable: {error}"
                ) from error


def _require_cdp_provider(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> None:
    if _select_cdp_provider(config, manifests, credentials) is None:
        raise RuntimeError(
            f"cdp provider {config.browser.cdp_provider!r} is required but no active extension "
            "registers it"
        )


def _select_search_provider(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> SearchProvider | None:
    specs: dict[str, tuple[SearchProviderSpec, Manifest]] = {}
    for manifest in manifests:
        for spec in manifest.search_providers:
            if spec.backend in specs:
                raise RuntimeError(
                    f"two extensions register search provider backend {spec.backend!r}"
                )
            specs[spec.backend] = (spec, manifest)
    if config.research.search_provider is None:
        return None
    found = specs.get(config.research.search_provider)
    if found is None:
        raise NotRegisteredError(
            f"config selects search provider backend {config.research.search_provider!r} "
            "but no extension registers it"
        )
    spec, manifest = found
    if credentials is None:
        raise RuntimeError(
            f"search provider backend {config.research.search_provider!r} needs a credential key "
            "but none is set"
        )
    declared = frozenset(slot.name for slot in manifest.credentials)
    return spec.build(CredentialAccess(declared=declared))


def _require_search_provider(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> None:
    if config.research.search_provider is None:
        raise RuntimeError(
            "[research] search_provider is unset; set it to a registered search backend so the "
            "research tools have a provider"
        )
    _select_search_provider(config, manifests, credentials)


def declared_flags(manifests: tuple[Manifest, ...]) -> dict[str, FlagSpec]:
    """Every flag a read in this deploy consults, core's and each active extension's, by key."""
    return {
        spec.key: spec
        for spec in (*CORE_FLAGS, *(spec for manifest in manifests for spec in manifest.flags))
    }


def _select_flag_provider(
    config: Config, manifests: tuple[Manifest, ...]
) -> FeatureProvider | None:
    specs: dict[str, FlagProviderSpec] = {}
    for manifest in manifests:
        for spec in manifest.flag_providers:
            if spec.backend in specs:
                raise RuntimeError(
                    f"two extensions register flag provider backend {spec.backend!r}"
                )
            specs[spec.backend] = spec
    if config.flags.backend is None:
        return None
    selected = specs.get(config.flags.backend)
    if selected is None:
        raise NotRegisteredError(
            f"config selects flag provider backend {config.flags.backend!r} but no extension "
            "registers it"
        )
    provider = selected.build(config.flags.cache_ttl_seconds, declared_flags(manifests))
    if provider is None:
        warn("flags.backend_unkeyed", backend=config.flags.backend)
    return provider


def _require_memory_search(
    _config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> None:
    """Require exactly one usable default memory-search provider."""
    providers = tuple(
        (manifest, spec)
        for manifest in manifests
        for spec in manifest.memory_search
        if spec.name == DEFAULT_MEMORY_SEARCH_PROVIDER
    )
    if not providers:
        raise RuntimeError("no active extension registers memory search")
    if len(providers) > 1:
        raise RuntimeError(
            f"two extensions register memory search provider "
            f"{DEFAULT_MEMORY_SEARCH_PROVIDER!r}: "
            + ", ".join(sorted(manifest.name for manifest, _spec in providers))
        )
    manifest, _spec = providers[0]
    declared = frozenset(slot.name for slot in manifest.credentials)
    if declared and credentials is None:
        raise RuntimeError(
            f"memory search provider {manifest.name!r} declares credential slots "
            "but no credential key is set"
        )


def _require_context_boundary(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> None:
    select_context_boundary(config, manifests)
    select_flagged_context_boundary(config, manifests)


_REQUIRED_SEAM_CHECKS: dict[
    str, Callable[[Config, tuple[Manifest, ...], CredentialStore | None], None]
] = {
    "cdp_providers": _require_cdp_provider,
    "context_boundaries": _require_context_boundary,
    "memory_search": _require_memory_search,
    "search_providers": _require_search_provider,
}


def _select_auth_proxy(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> AuthProxy | None:
    specs: dict[str, tuple[AuthProxySpec, Manifest]] = {}
    for manifest in manifests:
        for spec in manifest.auth_proxies:
            if spec.backend in specs:
                raise RuntimeError(f"two extensions register auth proxy backend {spec.backend!r}")
            specs[spec.backend] = (spec, manifest)
    backend = config.connectors.auth_backend
    if backend is None:
        if not specs:
            return None
        if len(specs) > 1:
            choices = ", ".join(repr(name) for name in sorted(specs))
            raise RuntimeError(
                f"[connectors] auth_backend is unset; choose one of the installed auth proxy "
                f"backends: {choices}"
            )
        backend = next(iter(specs))
    found = specs.get(backend)
    if found is None:
        raise NotRegisteredError(
            f"config selects auth proxy backend {backend!r} but no extension registers it"
        )
    spec, manifest = found
    if credentials is None:
        raise RuntimeError(f"auth proxy backend {backend!r} needs a credential key but none is set")
    declared = frozenset(slot.name for slot in manifest.credentials)
    return spec.build(CredentialAccess(declared=declared))


def _mount_ext_routes(
    app: FastAPI,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    index: IndexBackend,
    embed: EmbedClient,
    public_base_url: str | None,
    spend: SpendGates,
    ledger: Ledger,
    *,
    vault: VaultReads | None = None,
) -> None:
    for manifest in manifests:
        if not manifest.routes:
            continue
        if credentials is None:
            raise RuntimeError(
                f"extension {manifest.name!r} serves routes but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = extension_context_for(
            manifest.name,
            declared,
            index,
            embed,
            public_base_url=public_base_url,
            home_surface=home_surface(manifests),
            minted=minted_slots(manifest),
            spend=spend,
            ledger=ledger,
            vault_read=manifest.vault_read,
            vault=vault,
        )
        for spec in manifest.routes:

            async def endpoint(
                request: Request,
                handler=spec.handler,
                identify=spec.identify,
                extension_context=context,
            ) -> Response:
                if identify is None:
                    return await handler(extension_context, request)
                identified = identify(request)
                if identified is None:
                    return JSONResponse(
                        {
                            "error": {
                                "code": "unauthorized",
                                "message": "The request names no workspace.",
                            }
                        },
                        status_code=401,
                    )
                with ws(identified):
                    return await handler(extension_context, request)

            app.add_route(
                f"/ext/{manifest.name}/{spec.path.lstrip('/')}",
                endpoint,
                methods=[spec.method],
            )


def _mount_deploy_routes(
    app: FastAPI,
    manifests: tuple[Manifest, ...],
    blob: WorkspaceBlobStore,
    provisioning: Provisioning,
    flag_backend: str | None,
) -> None:
    core_segments = {
        route.path.split("/")[2]
        for route in app.routes
        if isinstance(route, Route) and route.path.startswith(f"{DEPLOY_ROUTE_PREFIX}/")
    }
    flag_keys = frozenset(declared_flags(manifests))
    for manifest in manifests:
        if not manifest.deploy_routes and manifest.deploy_bearer_env is None:
            continue
        name, env = manifest.name, manifest.deploy_bearer_env
        if not manifest.deploy_routes:
            raise RuntimeError(f"extension {name!r} names {env} but serves no deploy routes")
        if env is None:
            raise RuntimeError(f"extension {name!r} serves deploy routes but names no bearer env")
        if env not in manifest.deploy_keys:
            raise RuntimeError(f"extension {name!r} leaves {env} out of deploy_keys")
        if name in core_segments:
            raise RuntimeError(f"extension {name!r} collides with core's {DEPLOY_ROUTE_PREFIX}/")
        token = deploy_env(env)
        if token is None:
            raise RuntimeError(f"extension {name!r} serves deploy routes but {env} is unset")
        bearer = f"Bearer {token}".encode()
        for spec in manifest.deploy_routes:
            path = f"{DEPLOY_ROUTE_PREFIX}/{name}/{spec.path.lstrip('/')}"
            context = DeployContext(
                extension=name,
                route=path,
                blob=blob,
                provisioning=provisioning,
                flag_backend=flag_backend,
                flag_keys=flag_keys,
            )

            async def endpoint(
                request: Request, handler=spec.handler, context=context, expected=bearer
            ) -> Response:
                presented = request.headers.get("authorization", "").encode()
                if not hmac.compare_digest(presented, expected):
                    return Response("unauthorized", status_code=401)
                return await handler(context, request)

            app.add_route(path, endpoint, methods=[spec.method])


@dataclass(frozen=True)
class WorkspaceScopeBoundary:
    """The shared fleet's request→workspace boundary: one process serves every workspace, each
    request binds its own as the ambient `current_workspace` before it reads anything
    (the shared surface endpoints below), and this middleware guarantees every request begins and
    ends with none bound. On entry it clears any stale value the task's context inherited
    (cpython#140947: a request task can start on a prior task's contextvars); its `finally` releases
    the binding once the downstream app has fully sent the response — raises included, which never
    yield a response a wrapper could ride. The release must outlive the handler: a live surface's
    StreamingResponse tails the hub, reading the durable turn under RLS after the handler returns,
    so a `with ws(...)` block inside the route would cut that read off. `await self.app(...)` runs
    in the request's own task and returns only once the whole response — the body Starlette streams
    in a child task (a *copy* of this context) included — is sent or torn down, so the release lands
    in exactly the context the binding was set in. Raw ASGI deliberately: BaseHTTPMiddleware runs
    downstream in a separate task whose contextvar writes never line up with this one's."""

    app: ASGIApp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        current_workspace.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            current_workspace.set(None)


def _mount_surface_route(
    app: FastAPI,
    surface: str,
    route: SurfaceRoute,
    identify: WorkspaceResolver,
    auth: SurfaceAuth,
    context_for: Callable[[UUID, str], SurfaceContext],
) -> None:
    """Mount one surface route behind the surface's resolver, which binds the request's workspace
    before the handler reads anything."""

    async def endpoint(request: Request) -> Response:
        resolution = await identify(request, auth)
        if isinstance(resolution, Response):
            return resolution
        if resolution is None:
            return Response("unauthorized", status_code=401)
        current_workspace.set(resolution)
        return await route.handler(context_for(resolution, surface), request)

    app.add_route(f"/surface/{surface}/{route.path}".rstrip("/"), endpoint, methods=[route.method])


def _mount_surface_socket(
    app: FastAPI,
    surface: str,
    socket: SurfaceSocket,
    identify: WorkspaceResolver,
    auth: SurfaceAuth,
    context_for: Callable[[UUID, str], SurfaceContext],
) -> None:
    """The surface cookie is host-only but same-site with every label the deploy serves, so a
    browser attaches it to sockets a hosted site or app frame opens."""

    async def endpoint(websocket: WebSocket) -> None:
        if not same_origin_handshake(websocket):
            return await websocket.send_denial_response(
                Response(FOREIGN_HANDSHAKE, status_code=403)
            )
        resolution = await identify(handshake_request(websocket), auth)
        if isinstance(resolution, Response):
            return await websocket.send_denial_response(resolution)
        if resolution is None:
            return await websocket.send_denial_response(Response("unauthorized", status_code=401))
        with ws(resolution):
            await socket.handler(context_for(resolution, surface), websocket)

    app.add_api_websocket_route(f"/surface/{surface}/{socket.path}".rstrip("/"), endpoint)


DEFAULT_SITES = SitesConfig()


def _mount_shared_surfaces(
    app: FastAPI,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    blob: WorkspaceBlobStore,
    sandboxes: ConversationSandbox,
    hub: Hub,
    dbos_client: DBOSClient,
    artifact_secret: str,
    public_base_url: str | None,
    ingress_public_url: str | None,
    models: tuple[str, ...],
    *,
    deploy_sessions: DeploySessions | None,
    probes: ConversationProbes | None = None,
    runtime_identity: RuntimeIdentity | None = None,
    connectors: ConnectorRegistry | None = None,
    ambient_reply: AmbientReplyClassifier,
    skills: SkillRegistry,
    member_skill_listing: "Callable[[], Awaitable[tuple[RuntimeSkill, ...]]]",
    sandbox_sizes: tuple[str, ...] = (),
    memory: MemorySearch | None = None,
    sign_in_path: str | None = None,
    sites: SitesConfig = DEFAULT_SITES,
    surface_model: "Callable[[str], SurfaceModel] | None" = None,
    objects: MemberObjectRegistry | None = None,
    spend: SpendGates = NO_SPEND_GATES,
    ledger: Ledger = UNGATED_LEDGER,
    ambient_reply_for: "Callable[[str], AmbientReplyClassifier] | None" = None,
) -> None:
    app.add_middleware(WorkspaceScopeBoundary)
    if connectors is None:
        connectors = ConnectorRegistry(
            entries=_connector_entries(manifests),
            resolver=open_connector_namespace(manifests),
        )
    admission = _admission(dbos_client, manifests, hub, spend)
    tailer = HubTailer(hub=hub, spend=spend)
    stopper = MemberStop(client=dbos_client, hub=hub, admission=admission, sessions=deploy_sessions)
    turn_steps = DurableTurnSteps(client=dbos_client)
    system_skill_bundle = SystemSkillBundle.from_skills(skills.bundled_skills())
    registered: dict[str, SurfaceSpec] = {}
    listeners = []
    boots: list[SurfaceBoot] = []

    deploy_sandbox_internet = any(manifest.sandbox_internet for manifest in manifests)
    slots = declared_slots(manifests)
    conversation_slots = tuple(
        BoundConversationSlot(
            extension=manifest.name,
            provider=provider,
            ext=extension_context_for(
                manifest.name,
                frozenset(slot.name for slot in manifest.credentials),
                minted=minted_slots(manifest),
                spend=spend,
                ledger=ledger,
            ),
        )
        for manifest, provider in conversation_slot_declarations(manifests)
    )
    kind_schemas = {
        bound.kind.name: bound.kind.spec_model.model_json_schema()
        for bound in (*CORE_OBJECT_KINDS, *core_object_kinds(manifests, credentials))
    } | {
        kind.name: kind.spec_model.model_json_schema()
        for manifest in manifests
        for kind in manifest.objects
    }

    preview_service_url = os.environ.get(PREVIEW_SERVICE_URL_ENV)
    preview_token = os.environ.get(PREVIEW_TOKEN_ENV)

    def context_for(workspace_id: UUID, surface: str) -> SurfaceContext:
        return SurfaceContext(
            workspace_id=workspace_id,
            surface=surface,
            blob=blob,
            _sandboxes=sandboxes,
            _admitter=MemberAdmission(admission=admission, workspace_id=workspace_id),
            _tailer=tailer,
            _stopper=stopper,
            _turn_steps=turn_steps,
            _credentials=credentials,
            _artifact_token_secret=artifact_secret,
            _spend=spend,
            _public_base_url=public_base_url,
            _home_surface=home_surface(manifests),
            _ingress_public_url=ingress_public_url,
            _deploy_sandbox_internet=deploy_sandbox_internet,
            _models=models,
            _store_environment_document=store_environment_document,
            _store_environment_file=store_environment_file,
            _sandbox_sizes=sandbox_sizes,
            _skills=skills,
            _system_skill_bundle=system_skill_bundle,
            _member_skill_listing=member_skill_listing,
            _declared_slots=slots,
            _workspace_slots=workspace_slot_source(manifests),
            _ambient_reply=ambient_reply,
            _ambient_reply_for=ambient_reply_for,
            _connectors=connectors,
            _runtime=runtime_identity,
            _object_schemas=kind_schemas,
            _memory=memory,
            _sign_in_path=sign_in_path,
            _sites=sites,
            _model=None if surface_model is None else surface_model(surface),
            _objects={} if objects is None else objects.kinds,
            _actions={} if objects is None else objects.actions,
            _frame_admissible=(
                frozenset() if objects is None else frame_admissible(manifests, objects)
            ),
            _conversation_slots=conversation_slots,
            _preview_url=preview_service_url.rstrip("/") if preview_service_url else None,
            _preview_token=preview_token,
            _probes=probes,
        )

    for manifest in manifests:
        for spec in manifest.surfaces:
            auth = SurfaceAuth(
                _credentials=credentials,
                _declared=frozenset(slot.name for slot in manifest.credentials),
                _surface=spec.name,
            )
            resolver = spec.identify
            if manifest.credentials and credentials is None:
                raise RuntimeError(f"surface {spec.name!r} needs a credential key but none is set")
            if spec.post is not None:
                registered[spec.name] = spec
            if spec.listen is not None:
                listeners.append(
                    SurfaceListenerRunner(
                        surface=spec.name,
                        instance_id=app.state.instance_id,
                        listener=spec.listen,
                        public_base_url=public_base_url,
                        _auth=auth,
                        _context_for=context_for,
                    )
                )
            if spec.boot is not None:
                boots.append(spec.boot)
            if spec.routes or spec.sockets:
                if resolver is None:
                    raise RuntimeError(
                        f"surface {spec.name!r} serves traffic but has no workspace resolver"
                    )
                for route in spec.routes:
                    _mount_surface_route(app, spec.name, route, resolver, auth, context_for)
                for socket in spec.sockets:
                    _mount_surface_socket(app, spec.name, socket, resolver, auth, context_for)
            log("serve.shared_surface.mounted", surface=spec.name)
    app.state.surface_listeners = tuple(listeners)
    app.state.surface_boots = tuple(boots)
    _mount_home(app, manifests)
    if registered:
        worker_id = uuid4().hex
        app.state.writeback_poller = WritebackPoller(
            worker_id=worker_id,
            surfaces=registered,
            context_for=context_for,
            candidates=writeback_workspaces(),
        )
        app.state.mid_turn_reply_poller = MidTurnReplyPoller(
            worker_id=worker_id,
            surfaces=registered,
            context_for=context_for,
            candidates=mid_turn_reply_workspaces(),
        )


def home_surface(manifests: tuple[Manifest, ...]) -> str | None:
    """The name of the surface a browser belongs on, or None when the deploy installs no browser
    surface at all. A pack claiming two homes names no single door, so it is refused here — at
    boot, and equally in the CLI verb that opens the same path."""
    homes = sorted(spec.name for manifest in manifests for spec in manifest.surfaces if spec.home)
    if len(homes) > 1:
        raise RuntimeError(f"more than one surface claims the browser home: {homes}")
    return homes[0] if homes else None


def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None:
    surface = home_surface(manifests)
    if surface is None:
        return
    path = f"/surface/{surface}"

    async def home(_request: Request) -> Response:
        return RedirectResponse(path, status_code=303)

    app.add_route("/", home, methods=["GET"])


@asynccontextmanager
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The heartbeat runs on `run`'s own thread: jobs enqueue under this executor id before uvicorn
    starts, and liveness must survive an app-loop stall."""
    if app.state.configured_sources:
        with ws(await sole_workspace()):
            await register_sources(app.state.configured_sources)
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(ExecutorRecovery().run()),
                group.create_task(
                    CancelReconciler(
                        client=app.state.dbos, sessions=app.state.deploy_sessions
                    ).run()
                ),
                group.create_task(
                    StrandedTurnReconciler(
                        client=app.state.dbos, sessions=app.state.deploy_sessions
                    ).run()
                ),
            ]
            if app.state.fleet.surfaces:
                for boot in app.state.surface_boots:
                    boot(FleetBlobStore(backend=app.state.blob.backend))
                for poller in (app.state.writeback_poller, app.state.mid_turn_reply_poller):
                    if poller is not None:
                        tasks.append(group.create_task(poller.run()))
                for listener in app.state.surface_listeners:
                    tasks.append(group.create_task(listener.run()))
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
    finally:
        for client in app.state.http_clients:
            await client.aclose()


def _preview_settings(config: Config) -> tuple[tuple[str, int], str] | None:
    preview_service = parse_preview_service(config.sandbox.preview_service)
    if preview_service is None:
        return None
    preview_token = os.environ.get(PREVIEW_TOKEN_ENV)
    if not preview_token:
        raise RuntimeError(
            f"{PREVIEW_TOKEN_ENV} must be set when the sandbox preview service is enabled"
        )
    return preview_service, preview_token


def deploy_rules(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    artifact_hosts: tuple[HostEntry, ...],
) -> PerAgentRules:
    """What compiles the deploy's session policies: its model hosts and binds, `artifact_hosts`,
    and the grant, slot and CLI sources of `manifests`."""
    preview = _preview_settings(config)
    model_hosts, model_binds = model_bindings(config)
    public_base_url = config.connect.public_base_url
    served = None if public_base_url is None else public_base_url.rstrip("/")
    return PerAgentRules(
        hosts=(*model_hosts, *artifact_hosts),
        binds=model_binds,
        grants=GrantStore() if credentials is not None else None,
        credentials=credentials,
        slots=workspace_slot_source(manifests),
        internet=derive_manifest_internet(manifests),
        transfer_hosts=connector_transfer_hosts(manifests),
        clis=connector_clis(manifests),
        bridge_upstream=None if served is None else f"{served}/internal/egress/tool-bridge",
        preview_upstream=(
            None if served is None or preview is None else f"{served}/internal/egress/preview"
        ),
    )


def _proxy_control(
    app: FastAPI,
    config: Config,
    rules: PerAgentRules,
    run_tokens: RunTokenCodec,
    bridge: ToolBridge | None,
    sessions: ProxySessions | None,
) -> EgressControl:
    preview = _preview_settings(config)
    public_base_url = config.connect.public_base_url
    proxy_url = config.sandbox.proxy_url
    if proxy_url is not None and (
        public_base_url is None or not public_base_url.startswith("https://")
    ):
        raise RuntimeError(
            "[connect] public_base_url must be this deploy's https:// URL when [sandbox] proxy_url "
            "is set, because the proxy service relays its routes only to a TLS upstream."
        )
    stamp_key = None if proxy_url is None else _proxy_public_key()
    control = EgressControl(
        cache_control_token=os.environ.get(CACHE_CONTROL_TOKEN_ENV) or None,
        resolver=rules,
        run_tokens=run_tokens,
        bridge=bridge,
        stamp_key=stamp_key,
        preview=(
            None
            if stamp_key is None or preview is None
            else PreviewRelay(*preview, httpx.AsyncClient(timeout=PREVIEW_RELAY_TIMEOUT_SECONDS))
        ),
    )
    if control.cache_control_token is not None:
        app.include_router(control.git_credential_router())
    if stamp_key is not None:
        app.include_router(control.router())
    app.state.http_clients = (
        *(() if control.preview is None else (control.preview.http,)),
        *(() if sessions is None else (sessions.http,)),
    )
    return control


def deploy_proxy_sessions(config: Config, manifests: tuple[Manifest, ...]) -> ProxySessions | None:
    """The client of the proxy service's session API under `[sandbox] proxy_url`, or None when
    the deploy runs no proxy service."""
    proxy_url = config.sandbox.proxy_url
    if proxy_url is None:
        return None
    return ProxySessions(
        proxy_url.rstrip("/"),
        proxy_credentials(manifests),
        httpx.AsyncClient(timeout=PROXY_CALL_TIMEOUT_SECONDS),
    )


def _proxy_public_key() -> Ed25519PublicKey:
    raw = os.environ.get(PROXY_PUBLIC_KEY_ENV)
    if not raw:
        raise RuntimeError(
            f"{PROXY_PUBLIC_KEY_ENV} must hold the proxy service's Ed25519 public key so the "
            "routes it relays can be verified."
        )
    return load_proxy_public_key(raw)


WILDCARD_BINDS = frozenset({"0.0.0.0", "::"})


def _connector_registry(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> ConnectorRegistry:
    return ConnectorRegistry(
        entries=_connector_entries(manifests),
        resolver=open_connector_namespace(manifests),
        fallback=_select_auth_proxy(config, manifests, credentials),
    )


def _connector_entries(manifests: tuple[Manifest, ...]) -> dict[str, ConnectorEntry]:
    entries: dict[str, ConnectorEntry] = {}
    for manifest in manifests:
        for connector in manifest.connectors:
            provider = connector.oauth.provider
            if provider in entries:
                raise RuntimeError(f"two extensions register connector provider {provider!r}")
            entries[provider] = ConnectorEntry(
                provider=provider, label=connector.label, broker=connector.broker
            )
    return entries


def _connect_flow(
    credentials: CredentialStore | None,
    config: Config,
    manifests: tuple[Manifest, ...],
    index: IndexBackend | None = None,
    embed: EmbedClient | None = None,
    resumption: ConnectResume | None = None,
    *,
    spend: SpendGates,
    ledger: Ledger,
) -> ConnectFlow | None:
    if credentials is None:
        return None
    providers: dict[str, OAuthProvider] = {}
    for manifest in manifests:
        for connector in manifest.connectors:
            if connector.oauth.provider in providers:
                raise RuntimeError(
                    f"two extensions register connector provider {connector.oauth.provider!r}"
                )
            providers[connector.oauth.provider] = connector.oauth
    return ConnectFlow(
        providers=providers,
        fernet=credentials.fernet,
        store=GrantStore(),
        redirect_uri=_connect_redirect_uri(config, providers),
        portal_url=portal_url(config.connect.public_base_url, home_surface(manifests)),
        resolver=open_connector_namespace(manifests),
        connections=connection_hooks(
            manifests, credentials, index, embed, spend=spend, ledger=ledger
        ),
        resumption=resumption,
        labels={
            connector.oauth.provider: connector.label
            for manifest in manifests
            for connector in manifest.connectors
        },
    )


def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str:
    """Providers redirect the member's browser here and accept loopback as for native apps; a
    scheme-less value or wildcard bind (0.0.0.0, ::) never opens in a browser."""
    base = config.connect.public_base_url
    if not providers:
        return f"{base.rstrip('/')}{CONNECT_CALLBACK_PATH}" if base else ""
    if not base:
        raise RuntimeError(
            "connect.public_base_url must be this deploy's externally reachable base URL when a "
            "connector provider is registered"
        )
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RuntimeError(
            f"connect.public_base_url {base!r} must include a scheme and host — the provider "
            "redirects the member's browser to it"
        )
    if parsed.hostname in WILDCARD_BINDS:
        raise RuntimeError(
            f"connect.public_base_url {base!r} is a wildcard bind, not a host a browser can open; "
            "set the deploy's public URL"
        )
    return f"{base.rstrip('/')}{CONNECT_CALLBACK_PATH}"
