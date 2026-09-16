"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import json
import os
import secrets
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import uvicorn
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from dbos import DBOS, DBOSClient
from fastapi import FastAPI, WebSocket
from openfeature.provider import FeatureProvider
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ufo.blob import (
    BlobStore,
    FilesystemBlobStore,
    FleetBlobStore,
    S3BlobStore,
    WorkspaceBlobStore,
    blob_store_for,
)
from ufo.browser import CdpProvider
from ufo.config import (
    IN_PROCESS_BACKEND,
    Config,
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
from ufo.harness.auth.bearer import JOIN_PATH, LOGIN_PATH, LOGOUT_PATH
from ufo.harness.document_renderer import DocumentRenderer
from ufo.harness.durability import ReplaySafeSerializer, replay_safe_client
from ufo.harness.models.catalog_skill import model_catalog_skill
from ufo.harness.models.interface import AUTO_MODEL
from ufo.harness.models.pricing import Pricing
from ufo.harness.models.registry import model_registry
from ufo.harness.o11y import init_o11y, init_service_checks, log, warn
from ufo.harness.sandbox.cache import (
    CACHE_CONTROL_TOKEN_ENV,
    CACHE_HOST,
    CACHE_PKG_HOSTS,
    parse_cache_daemon,
)
from ufo.harness.sandbox.conversation import ConversationSandbox
from ufo.harness.sandbox.exec_env import ProbeEnv
from ufo.harness.sandbox.preview import parse_preview_service
from ufo.harness.sandbox.select import select_carriers
from ufo.harness.sandbox.session import (
    EGRESS_CA_CERT_ENV,
    EGRESS_CONTROL_TOKEN_ENV,
    ProbeTokenCodec,
    ProxyEndpoint,
    RunTokenCodec,
)
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
    skill_registry,
    turn_subagent_grants,
    turn_subagents,
    validate_ext_tools,
    workspace_slot_source,
)
from ufo.onboard.onboard_control import ONBOARD_CONTROL_TOKEN_ENV, OnboardControl
from ufo.proxy_serve import OWNER_DSN_ENV, model_rule_base
from ufo.runtime.access.connectors import (
    AuthProxy,
    ConnectorEntry,
    ConnectorRegistry,
    SourceCredentialResolver,
)
from ufo.runtime.access.credentials import (
    CredentialStore,
)
from ufo.runtime.access.egress_control import EgressControl
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    connector_transfer_hosts,
    derive_artifact_store_rules,
    derive_manifest_rules,
    derive_residential_rules,
)
from ufo.runtime.access.grants import ConnectFlow, GrantStore, OAuthProvider, install_connect_flow
from ufo.runtime.background_tasks import BackgroundTaskSweep
from ufo.runtime.billing.balance import billing_screen_url
from ufo.runtime.context_boundary import (
    CORE_FLAGS,
    select_context_boundary,
    select_flagged_context_boundary,
)
from ufo.runtime.delivery import DeliverySweep
from ufo.runtime.email import EmailSends, email_sends_from_env
from ufo.runtime.ext.context import ConversationProbes, CredentialAccess, ModelAccess
from ufo.runtime.ext.context import context_for as extension_context_for
from ufo.runtime.ext.conversation_slots import BoundConversationSlot
from ufo.runtime.ext.manifest import (
    AuthProxySpec,
    CarrierSpec,
    CdpProviderSpec,
    FlagProviderSpec,
    Manifest,
    NotRegisteredError,
    SearchProviderSpec,
    conversation_slot_declarations,
    declared_slots,
    open_connector_namespace,
)
from ufo.runtime.ext.surface import (
    SURFACE_MODEL_JOB_PREFIX,
    MidTurnReplyPoller,
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
    WritebackPoller,
    handshake_request,
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
)
from ufo.runtime.media.preview_renderer import (
    PREVIEW_SERVICE_URL_ENV,
    PREVIEW_TOKEN_ENV,
    PreviewRenderer,
)
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.memory import DEFAULT_MEMORY_SEARCH_PROVIDER, MemorySearch
from ufo.runtime.profiles import CORE_SUBAGENT_PROFILES
from ufo.runtime.queue import Runtime, init_runtime
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
    SourceWatchReader,
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
from ufo.runtime.workspace import init_workspace_credentials, ws
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

# The one `/surface/*` prefix the gateway answers: the founder-email HUD reads a ledger in the
# gateway's own schema, which no core surface can reach.
FOUNDER_EMAIL_PATH = "/surface/email"
RESERVED_HOST_PREFIXES = (
    LOGIN_PATH,
    LOGOUT_PATH,
    JOIN_PATH,
    "/v1/onboard",
    "/ufo",
    FOUNDER_EMAIL_PATH,
)
FOREIGN_HANDSHAKE = "This connection did not come from the page it addresses."
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
    executor drains. `WHOLE_FLEET` claims every application queue for a deploy that is one process
    (a node, a stack, an eval run). Every fleet names its queues in full — a queue no fleet claims
    is one nothing would ever dequeue, which `test_fleet.py` refuses."""

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
WHOLE_FLEET = Fleet(name="all", queues=TURNS_FLEET.queues + JOBS_FLEET.queues, surfaces=True)
FLEETS = {fleet.name: fleet for fleet in (WHOLE_FLEET, TURNS_FLEET, JOBS_FLEET)}


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


def _assert_no_reserved_routes(app: FastAPI) -> None:
    """On the shared host one ingress hands `/login`, `/logout`, `/join`, `/v1/onboard`, and `/ufo`
    to the onboarding gateway and everything else to this fleet, so the sign-in flow is same-origin
    with the product. The fleet must therefore mount nothing under those prefixes — otherwise the
    ingress silently shadows it. Asserting it at boot makes the split a fail-loud invariant, not a
    hand-kept property of the ingress template."""
    conflicts = [
        route.path
        for route in app.routes
        if isinstance(route, Route)
        and any(route.path.startswith(prefix) for prefix in RESERVED_HOST_PREFIXES)
    ]
    if conflicts:
        raise RuntimeError(
            f"shared fleet mounts routes under gateway-reserved prefixes "
            f"{RESERVED_HOST_PREFIXES}: {conflicts}"
        )


def _admission(
    dbos_client: DBOSClient,
    manifests: tuple[Manifest, ...],
    hub: Hub,
    key_slot_for: Callable[[str], str | None] | None,
    billing_url: str | None,
) -> Admission:
    """The one construction of the turn gate. Every admission a process holds is assembled here, so
    a change to the gate reaches the instance the member surface serves rather than a copy beside
    it; `gates.py` refuses a second `Admission(...)` in shipped code."""
    return Admission(
        dbos=dbos_client,
        durable_surfaces=durable_surfaces(manifests),
        hub=hub,
        key_slot_for=key_slot_for,
        billing_url=billing_url,
    )


def run(fleet: Fleet) -> None:
    """Start one process of `fleet`: it serves every workspace, resolving the workspace per request
    (from the caller's token) and per turn (from the workflow argument), scoping each transaction by
    the ambient `current_workspace`. The fleet decides only which durable work this process claims —
    the boot below is the same one every fleet runs."""
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
    init_service_checks(
        config.o11y.datadog_check_url,
        config.o11y.datadog_env,
        os.environ.get(config.o11y.datadog_api_key_env),
    )
    init_db(config.database.url)
    manifests = load_manifests(config.pack.name)
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
    billing_url = billing_screen_url(config.connect.public_base_url, browser_home)
    admission = _admission(dbos_client, manifests, hub, registry.key_slot_for, billing_url)

    def invoker_for(workspace_id: UUID) -> AdmissionInvoker:
        return AdmissionInvoker(admission=admission, workspace_id=workspace_id)

    app = FastAPI(lifespan=_serve_lifespan)
    tailer = HubTailer(hub=hub, billing_url=billing_url, key_slot_for=registry.key_slot_for)
    tool_bridge = ToolBridge(
        dbos=dbos_client,
        tailer=tailer,
        tools=bridge_tools(manifests),
        subagents=subagents,
        subagent_grants=subagent_grants,
        actions=deploy_actions,
    )
    sandboxes = ConversationSandbox(
        carrier=carrier,
        backend=config.sandbox.backend,
        off_cluster=carrier_spec.off_cluster,
        resume_carriers=carriers.resume,
        image_ref=config.sandbox.image_ref,
        proxy=_proxy_endpoint(
            app,
            config,
            manifests,
            credentials,
            registry.pricing,
            run_tokens,
            blob_backend,
            tool_bridge,
        ),
        workspace_root=config.sandbox.workspace_root,
        terminals=_select_terminal_transport(config, manifests, fleet_blob),
        document_renderer=document_renderer,
        system_skill_archive=system_skill_bundle.archive,
    )
    probes = ConversationProbes(
        sandboxes,
        ProbeTokenCodec(secret=run_tokens.secret),
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
            email=email_sends_from_env(),
            invoker_for=invoker_for,
            probes=probes,
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
        billing_url=billing_url,
        home_surface=browser_home,
        tailer=tailer,
    )
    init_runtime(runtime)
    install_connect_flow(
        _connect_flow(credentials, config, manifests, index, embed, ConnectResume(admission))
    )
    dbos = DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": config.database.system_url,
            "executor_id": str(instance_id),
            "run_admin_server": False,
            "max_executor_threads": DBOS_MAX_EXECUTOR_THREADS,
            "sys_db_pool_size": DBOS_SYSTEM_DATABASE_POOL_SIZE,
            "serializer": ReplaySafeSerializer(),
        }
    )
    DBOS.listen_queues(fleet.queues)
    DBOS.launch()
    app.state.fleet = fleet
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.instance_id = instance_id
    app.state.durable_surfaces = durable_surfaces(manifests)
    app.state.writeback_poller = None
    app.state.mid_turn_reply_poller = None
    app.state.surface_listeners = ()
    app.state.surface_boots = ()
    app.state.blob = blob
    app.state.artifact_token_secret = artifact_secret
    app.include_router(callback_router)
    app.include_router(artifacts_router)
    onboard_token = os.environ.get(ONBOARD_CONTROL_TOKEN_ENV, "")
    if onboard_token:
        app.include_router(
            OnboardControl(
                control_token=onboard_token,
                messages=tuple(message for manifest in manifests for message in manifest.messages),
            ).router()
        )
    app.include_router(SiteReports(invoker_for=invoker_for).router())
    sync_driver = SyncDriver(
        backends=_source_backends(manifests),
        blob=blob,
        postgres=config.database.url.startswith("postgresql"),
        source_credentials=SourceCredentialResolver(connectors),
        identity_resolvers=_source_identity_resolvers(manifests, credentials, blob),
        watch_readers=_source_watch_readers(manifests),
        own_key_slots=model_key_slots(registry),
    )
    unknown_backends = sorted(
        {entry.backend for entry in config.sources} - sync_driver.backends.keys()
    )
    if unknown_backends:
        raise RuntimeError(f"[[sources]] names unknown backends: {', '.join(unknown_backends)}")
    app.state.configured_sources = config.sources
    page_feed = CorePageFeed(blob=blob)
    _launch_jobs(runtime, invoker_for, sync_driver, page_feed, probes)
    _mount_ext_routes(app, manifests, credentials, index, embed, config.connect.public_base_url)
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
        probes=probes,
        runtime_identity=runtime_identity,
        connectors=connectors,
        key_slot_for=registry.key_slot_for,
        own_key_slots=model_key_slots(registry),
        ambient_reply=AmbientReplyClassifier(
            model=ModelAccess(
                replace(registry, auto_model=config.models.ambient_reply_model),
                AMBIENT_REPLY_JOB,
            )
        ),
        ambient_reply_for=lambda model: AmbientReplyClassifier(
            model=ModelAccess(
                replace(registry, auto_model=registry.resolve(model)),
                AMBIENT_REPLY_JOB,
            )
        ),
        surface_model=lambda name: ModelAccess(
            replace(registry, auto_model=config.models.background_jobs_model),
            f"{SURFACE_MODEL_JOB_PREFIX}{name}",
        ),
        sandbox_sizes=carrier_spec.sizes,
        skills=skills,
        member_skill_listing=lambda: member_skill_listing(manifests, credentials, index, embed),
        memory=memory,
        objects=member_object_registry(
            manifests,
            credentials,
            index,
            embed,
            public_base_url=config.connect.public_base_url,
            artifact_token_secret=artifact_secret,
        ),
        email=email_sends_from_env(),
    )
    _assert_no_reserved_routes(app)
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
    """Drive one DB-touching step on a throwaway loop, disposing that loop's engines before the loop
    closes — a pooled connection abandoned to a closed loop can never be closed again, by this
    process or any other. The loops that persist (uvicorn's, DBOS's, the heartbeat thread's) keep
    their engines for the life of the process, which is what makes a pool worth holding at all."""

    async def step() -> T:
        try:
            return await coro
        finally:
            await dispose_loop_engines()

    return asyncio.run(step())


def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None:
    """Drain, then retire the seat only when the executor emptied. `DBOS.destroy` waits out the
    drain window, then force-cancels surviving workflow coroutines — a cancelled workflow writes
    no outcome and stays PENDING, and retiring the seat is exactly what lets a peer resume it.
    But destroy bounds even that cancellation with a ten-second join, so a cancellation-resistant
    workflow can outlive it — and an active-set entry is released only when its workflow task
    finishes, so a non-empty set here means work still executes in this process: retiring the
    seat would let a peer's recovery sweep start a second concurrent execution. A kept seat
    stays fresh under the daemon heartbeat and ages out with the process."""
    DBOS.destroy(workflow_completion_timeout_sec=graceful_shutdown_seconds)
    active = dbos._active_workflows_set.activeList()
    if active:
        log("serve.seat_kept_for_active_workflows", workflows=len(active))
        return
    _one_shot(heartbeat.retire())


def _shared_owner_dsn(config: Config) -> str:
    """The RLS-bypassing owner DSN the shared service opens as `owner_tx`'s engine — the one
    cross-workspace read job sweeps enumerate through before re-binding each row under `ws(...)`.
    `owner_tx` must bypass RLS through the table-owner role, else it falls back to the RLS-subject
    engine and the enumeration reads an unset `app.workspace_id` GUC. Read from `UFO_OWNER_DSN`,
    falling back to `[database] owner_url`; neither set fails loud. A plain libpq URL is normalized
    to the asyncpg driver used by the subject engine."""
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
    """Register this workspace's jobs — core's own (the source sync driver, the turn dispatcher
    that recovers queued turns and re-admits parked turns, and the delivery sweep that hands back
    the delegated children their own execution could not) plus every
    installed extension's (the memory extension's memory-index and page-index jobs among them) — as
    DBOS schedules and one-shot enqueues, after
    launch so the system store is live. Registration is the synchronous DBOS API (off the loop, at
    startup); a handler may read a declared credential or the deploy index/embed backends or the
    page feed, so once any job is registered the credential key must be set. Both runners carry
    `models.background_jobs_model`, so a handler's own metered call runs on the deploy's cheap
    background model rather than the model a member's turn runs on."""
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
        own_key_slots=model_key_slots(runtime.registry),
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
            TurnDispatcher(client=runtime.dbos, key_slot_for=runtime.registry.key_slot_for),
            page_change_runner,
            DeliverySweep(invoker_for=invoker_for, registry=runtime.subagents),
            BackgroundTaskSweep(probes=probes, invoker_for=invoker_for),
            preview_renderer,
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
        email=email_sends_from_env(),
        background_model=runtime.config.models.background_jobs_model,
        own_key_slots=model_key_slots(runtime.registry),
        public_base_url=runtime.config.connect.public_base_url,
        home_surface=home_surface(runtime.manifests),
    ).launch()


def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]:
    """The sync driver's backend map: core's folder backend plus every backend an extension
    registers through its Manifest `sources` point, built with a credential reader restricted to
    the declaring extension's slots. Two extensions claiming one backend name fail loud at boot,
    so a source row's backend resolves to exactly one implementation."""
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


def _source_watch_readers(manifests: tuple[Manifest, ...]) -> dict[str, SourceWatchReader]:
    """The sync driver's watch map: the reader of pinned resources each source backend's own
    extension answers, built on that extension's scoped context. A backend whose extension declares
    none is absent, and its runs pin no partition."""
    readers: dict[str, SourceWatchReader] = {}
    for manifest in manifests:
        declared = frozenset(slot.name for slot in manifest.credentials)
        for provider in manifest.sources:
            if provider.watches is None:
                continue
            readers[provider.backend] = provider.watches(
                extension_context_for(manifest.name, declared)
            )
    return readers


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
    """The process-wide live-frame hub the deploy selects: core's in-process default, or a backend
    an extension registers through its Manifest `hubs` point built from `config.hub.url`. Two
    extensions claiming one backend name fail loud, as does selecting a name no extension registers,
    so the running hub resolves to exactly one implementation."""
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
    """The process-wide terminal rendezvous the deploy selects, mirroring `_select_hub`: core's
    in-process default, or a backend an extension registers through its Manifest
    `terminal_transports` point, built from `config.hub.url` (reused, so one Redis serves hub and
    terminal) and the deploy's blob store (the op's copy-in body and any large copy-out reply ride
    the blob). Two extensions claiming one backend name fail loud, as does selecting a name no
    extension registers, so the running transport resolves to exactly one implementation. A shared
    fleet selects the Redis transport so a member's held connection and their turn's workflow reach
    one terminal even when they land on different pods.

    A cross-process hub with the in-process terminal transport fails loud here: a cross-process hub
    declares a multi-instance fleet, where the held connection and the turn's workflow land on
    different pods, and the process-local terminal rendezvous cannot reach across them — a
    conversation bound `client:<dir>` on one pod would strand every later turn a peer pod runs. The
    old coupling derived admissibility from the hub backend; decoupling the transport reopened the
    combination, so the boot guard is restored explicitly."""
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
    """The process-wide cdp provider the deploy selects, built once at boot with a credential reader
    over its declared slots — or None when no active extension registers that name (core ships none,
    so a deploy without a browser extension boots with None). The reader resolves the ambient
    workspace's BYOK key (else the platform key) at each browse, so one boot-built provider serves
    every workspace. Two extensions claiming one name fail loud; a provider whose extension declares
    slots with no key set fails loud; a browser extension's `requires` turns a None into a boot
    failure through `_validate_requires`."""
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
    """Boot-validation for every active extension's declared `requires`: eagerly resolve each named
    sub-seam so a consumer whose backend is absent, unknown, or unkeyed fails `serve` here — naming
    the extension and the seam — rather than on the first tool call. A seam an extension names that
    core does not know is itself a boot error. Deploy-level: the checks resolve the selected backend
    and its key presence, never a per-workspace build."""
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
    """The `cdp_providers` readiness contract for an active browser extension: the selected provider
    must be registered by an active extension (and, if its extension declares credential slots,
    keyed) — else boot fails here naming it, rather than the first browse failing. Core ships no
    provider, so a name no active extension registers resolves to None and fails loud here only
    because a browser extension requires it."""
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
    """The process-wide search provider the deploy selects (by `[research] search_provider`), built
    once at boot with a credential reader over its declared slots — or None when the knob is unset
    (a deploy without a research extension still boots). The reader resolves the ambient workspace's
    BYOK key (else the platform key) at each search, so one boot-built backend serves every
    workspace. Selecting a name no extension registers, a name collision, or a keyless selected
    backend each fail loud; a research extension's `requires` turns the unset knob into a boot
    failure through `_validate_requires`."""
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
    """The `search_providers` readiness contract: `[research] search_provider` is set and the named
    backend resolves (unknown name, collision, or missing credential key each fail loud) — so a
    research extension active with no search backend fails at boot, not on the first search."""
    if config.research.search_provider is None:
        raise RuntimeError(
            "[research] search_provider is unset; set it to a registered search backend so the "
            "research tools have a provider"
        )
    _select_search_provider(config, manifests, credentials)


def _select_flag_provider(
    config: Config, manifests: tuple[Manifest, ...]
) -> FeatureProvider | None:
    """The process-wide OpenFeature provider the deploy selects (by `[flags] backend`), built once
    at boot — or None when the knob is unset or the selected backend carries no credential, in which
    case every flag resolves to the default its call site passes. Selecting a name no extension
    registers, or a name two register, fails loud: a deploy that thinks it reads flags and silently
    reads none would gate features on nothing."""
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
    declared = {
        spec.key: spec
        for spec in (
            *CORE_FLAGS,
            *(spec for manifest in manifests for spec in manifest.flags),
        )
    }
    provider = selected.build(config.flags.cache_ttl_seconds, declared)
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
    """The `context_boundaries` readiness contract: both names in `[context]` name a registered
    strategy. An extension that ships its own boundary requires this seam, so a deploy whose toml
    still selects a name nothing registers fails at boot rather than at the first window that
    fills — the flagged name included, since a flag flip is what selects it and no deploy follows
    it."""
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
    """The fallback auth-proxy backend the `ConnectorRegistry` resolves an unbrokered provider's
    feed-sync credential through: the sole registered backend is automatic, while `[connectors]
    auth_backend` selects among several. The backend is built once at boot with a credential reader
    scoped to its slots (a direct BYOK backend reads its key in-process, host-side, never in the
    sandbox). A brokered provider resolves through its own broker regardless. Duplicate names,
    ambiguous selection, an unknown explicit name, and a missing credential key each fail loud."""
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
) -> None:
    """Mount every extension route under `/ext/<name>/<path>` with a verified workspace-scoped
    context. `RouteSpec.identify` resolves and returns the request's workspace, which core binds for
    the handler; an unresolved request is refused before the handler can touch the database or
    credentials. These routes answer a member's own browser — a provider's return leg lands on one —
    so the context carries the deploy's public base and browser home, which is how a route renders
    the link back to a surface that can carry the conversation on."""
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
        )
        for spec in manifest.routes:

            async def endpoint(
                request: Request,
                handler=spec.handler,
                identify=spec.identify,
                extension_context=context,
            ) -> Response:
                identified = identify(request)
                if identified is None:
                    return Response("unauthorized", status_code=401)
                with ws(identified):
                    return await handler(extension_context, request)

            app.add_route(
                f"/ext/{manifest.name}/{spec.path.lstrip('/')}",
                endpoint,
                methods=[spec.method],
            )


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
    """Mount one surface socket behind the resolver its HTTP routes are mounted behind, and behind
    the same-origin check the resolver's cookie cannot make for itself. A refusal is sent as the
    very response the equivalent GET would have answered, before the handshake is accepted; an
    admitted connection runs inside the workspace binding for as long as it is held, since the
    socket handler owns the connection rather than returning a response the middleware could
    release around.

    The origin is read before the session is, because a handshake off a foreign page is refused
    whoever it authenticates as: the surface's cookie is host-only but same-site with every other
    label the deploy serves, so the browser attaches it to a socket a hosted site or an app frame
    opens, and a socket admitted from there would carry that page's script into the member's own
    session."""

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
    probes: ConversationProbes | None = None,
    runtime_identity: RuntimeIdentity | None = None,
    connectors: ConnectorRegistry | None = None,
    ambient_reply: AmbientReplyClassifier,
    skills: SkillRegistry,
    member_skill_listing: "Callable[[], Awaitable[tuple[RuntimeSkill, ...]]]",
    sandbox_sizes: tuple[str, ...] = (),
    memory: MemorySearch | None = None,
    surface_model: "Callable[[str], SurfaceModel] | None" = None,
    objects: MemberObjectRegistry | None = None,
    key_slot_for: Callable[[str], str | None] | None = None,
    own_key_slots: tuple[str, ...] = (),
    ambient_reply_for: "Callable[[str], AmbientReplyClassifier] | None" = None,
    email: EmailSends | None = None,
) -> None:
    """Install the fleet-wide `WorkspaceScopeBoundary` and mount each shared-fleet-capable
    surface's routes, resolving the workspace per request instead of pinning one at boot:
    `SurfaceSpec.identify` verifies the request's signed bearer and returns the workspace it
    claims, which the endpoint binds via `current_workspace.set` for the whole request (an
    unresolved token is a 401) and the boundary releases once the response has fully streamed. The
    per-request SurfaceContext carries a `MemberAdmission` bound to that workspace so its
    admitted turn lands scoped to the token's workspace and no other.

    `SurfaceSpec.identify` is a required field, so every mounted surface scopes its own requests.
    Durable surfaces share one bounded writeback poller: its owner read selects only workspace ids,
    then every claim, build, credential read, post, and attachment runs under that workspace's
    binding."""
    app.add_middleware(WorkspaceScopeBoundary)
    if connectors is None:
        connectors = ConnectorRegistry(
            entries=_connector_entries(manifests),
            resolver=open_connector_namespace(manifests),
        )
    billing_url = billing_screen_url(public_base_url, home_surface(manifests))
    admission = _admission(dbos_client, manifests, hub, key_slot_for, billing_url)
    tailer = HubTailer(hub=hub, billing_url=billing_url, key_slot_for=key_slot_for)
    stopper = MemberStop(client=dbos_client, hub=hub, admission=admission)
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
                manifest.name, frozenset(slot.name for slot in manifest.credentials)
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
            _key_slot_for=key_slot_for,
            _own_key_slots=own_key_slots,
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
            _email=email,
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
    """Answer `GET /` with a redirect to the home surface, so the bare host is a door instead of a
    404. The redirected surface decides what an arriving browser sees — its own page for a resolved
    session, the sign-in page for none — which keeps one sign-in for the whole deploy."""
    surface = home_surface(manifests)
    if surface is None:
        return
    path = f"/surface/{surface}"

    async def home(_request: Request) -> Response:
        return RedirectResponse(path, status_code=303)

    app.add_route("/", home, methods=["GET"])


@asynccontextmanager
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run this instance's app-loop background work: the executor-recovery sweep that re-dispatches
    workflows stranded by dead peers, the cancel reconciler that cascades a cancel to the descendant
    turns it spawned (cancelling any turn left live under a cancelled ancestor), the stranded-turn
    reconciler that cancels a claimed turn whose workflow can no longer reach it, and, when a
    durable surface is installed, the writeback poller, the durable half of surface delivery off
    the hub and off the turn loop. The shared poller binds each selected workspace before
    delivery. The heartbeat is NOT here: liveness must
    span the whole boot (jobs enqueue under this executor id before uvicorn starts) and survive an
    app-loop stall, so `run` drives it on a dedicated thread from the moment the seat exists, and
    retires the seat only after `DBOS.destroy` has stopped all execution — a seat freed while
    queued workflows still run would hand a peer a second live execution. Configured `[[sources]]`
    rows register first: once at boot, off the sync poll.

    The three reconcilers run on every fleet: each is the fleet-wide safety net for durable work
    the whole deploy holds, so a jobs process reclaims a dead turns process's turns and the reverse.
    Surface delivery is the turns fleet's alone — an inbound listener is one connection admitted
    across all replicas by a fenced lease, and a jobs process winning that lease would land a
    member's messages on the fleet that runs the indexer.

    A surface's `boot` runs on the fleet that serves it and returns at once, so the work a request
    would otherwise be the first to ask for — the portal's asset publish — is already under way
    when the process takes its first request."""
    await register_sources(app.state.configured_sources)
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(ExecutorRecovery().run()),
            group.create_task(CancelReconciler(client=app.state.dbos).run()),
            group.create_task(StrandedTurnReconciler(client=app.state.dbos).run()),
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


def _proxy_endpoint(
    app: FastAPI,
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    pricing: Pricing,
    run_tokens: RunTokenCodec,
    blob: FilesystemBlobStore | S3BlobStore,
    bridge: ToolBridge | None,
) -> ProxyEndpoint:
    """The egress endpoint the carrier threads into every sandbox, plus the egress-control RPC the
    standalone Rust `ufo-egress` proxy calls back into. The wire is that separate process, local and
    hosted alike; serve owns the policy. It builds the `PerAgentRules` resolver — the model-provider
    base, this deploy's S3 artifact host, and each turn's keyed credentials and grants — and mounts
    `EgressControl` on `app` so the proxy resolves, authorizes, forwards, and meters through it,
    gated by `UFO_EGRESS_CONTROL_TOKEN`. The endpoint carries only what the sandbox needs to trust
    and reach the proxy: the stable `proxy_port`, the public dial-back base, and the CA cert — the
    trust anchor for the proxy's minted leaves. A hosted deploy (`proxy_public_url` set) has a real
    `ufo-egress` process behind that URL, so it sources the shared CA and the control token from env
    and fails loud without them, since that process holds the matching key. A local boot runs no
    proxy unless the dev rig (`make stack`) starts one: `ufoctl serve` alone comes up with the
    control RPC mounted and a throwaway trust anchor, so an in-sandbox CONNECT to the unmanned proxy
    port is refused — no egress, the documented local default. The dev rig supplies the real shared
    CA (both serve and `ufo-egress` read it) through the same env, and egress works."""
    if config.sandbox.proxy_public_url is not None:
        ca_cert = os.environ.get(EGRESS_CA_CERT_ENV)
        if not ca_cert:
            raise RuntimeError(
                f"{EGRESS_CA_CERT_ENV} must hold the shared egress CA certificate (PEM) so the "
                "sandbox trusts the proxy's TLS; the egress wire runs as a separate `ufo-egress` "
                "process that holds the matching key"
            )
        control_token = os.environ.get(EGRESS_CONTROL_TOKEN_ENV)
        if not control_token:
            raise RuntimeError(
                f"{EGRESS_CONTROL_TOKEN_ENV} must be set so `ufo-egress` authenticates to serve's "
                "egress-control RPC"
            )
    else:
        ca_cert = os.environ.get(EGRESS_CA_CERT_ENV) or _ephemeral_egress_ca()
        control_token = os.environ.get(EGRESS_CONTROL_TOKEN_ENV) or secrets.token_urlsafe(32)
    cache_daemon = parse_cache_daemon(config.sandbox.cache_daemon)
    preview = _preview_settings(config)
    cache_control_token = os.environ.get(CACHE_CONTROL_TOKEN_ENV)
    if cache_daemon is not None and not cache_control_token:
        raise RuntimeError(
            f"{CACHE_CONTROL_TOKEN_ENV} must be set when the sandbox cache is enabled so the cache "
            "daemon authenticates to serve's git-credential route; without it every cache-routed "
            "git request is refused"
        )
    clis = connector_clis(manifests)
    resolver = PerAgentRules(
        base=(
            *model_rule_base(config),
            *_one_shot(derive_artifact_store_rules(blob)),
            *derive_residential_rules(config.sandbox.residential_hosts),
        ),
        grants=GrantStore() if credentials is not None else None,
        credentials=credentials,
        slots=workspace_slot_source(manifests),
        internet=derive_manifest_rules(manifests),
        transfer_hosts=connector_transfer_hosts(manifests),
        clis=clis,
        cache_host=CACHE_HOST if cache_daemon is not None else None,
        cache_pkg_hosts=CACHE_PKG_HOSTS if cache_daemon is not None else (),
        preview_token=None if preview is None else preview[1],
    )
    control = EgressControl(
        control_token=control_token,
        cache_control_token=cache_control_token or secrets.token_urlsafe(32),
        resolver=resolver,
        pricing=pricing,
        run_tokens=run_tokens,
        bridge=bridge,
    )
    app.include_router(control.router())
    app.include_router(control.git_credential_router())
    return ProxyEndpoint(
        port=config.sandbox.proxy_port,
        ca_cert=ca_cert,
        public_url=config.sandbox.proxy_public_url,
    )


def _ephemeral_egress_ca() -> str:
    """The sandbox's egress trust anchor for a local boot with no shared CA. `ufoctl serve` alone
    runs no `ufo-egress`, so an in-sandbox CONNECT to the unmanned proxy port is refused before this
    ever validates a leaf — it is a well-formed anchor for a proxy that isn't there, the documented
    no-egress local default. Cert only: serve holds no signing key (the wire would), so the dev rig
    supplies a shared CA whose key its `ufo-egress` holds when real local egress is wanted."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ufo-egress-local")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


WILDCARD_BINDS = frozenset({"0.0.0.0", "::"})


def _connector_registry(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> ConnectorRegistry:
    """The one connector routing object, built from every manifest's `connectors` point: the
    dynamic connector tools read it off the turn's ToolContext, and the sync runner resolves
    feed-sync credentials through it — a brokered provider via its own broker, any other via the
    deploy-selected fallback backend. Two extensions claiming one provider fail loud, as the
    connect flow's OAuth registry would collide on the same name."""
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
) -> ConnectFlow | None:
    """The process's connect flow — the tool validates against it, a surface privately authorizes
    through it, and the OAuth callback completes through it — sharing the credential key that seals
    its state and encrypts its tokens. No key means grants cannot be recorded, so all fail loud.
    The provider registry is every installed connector's OAuth descriptor keyed by its provider
    name; the `redirect_uri` is this deploy's external callback URL, the one value both legs of the
    handoff present; `connections` is the chain a landed connection publishes to, so an extension
    creates what the connection implies while the callback is still open."""
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
        connections=connection_hooks(manifests, credentials, index, embed),
        resumption=resumption,
        labels={
            connector.oauth.provider: connector.label
            for manifest in manifests
            for connector in manifest.connectors
        },
    )


def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str:
    """The external callback URL both OAuth legs present, derived from `connect.public_base_url`. A
    provider redirects the member's *browser* here, so the value must be a URL that browser can
    open: a scheme-less value or a wildcard bind (0.0.0.0, ::) never is, and fails loud the moment a
    connector is registered. Loopback is fine — on a local node the browser runs on that same
    machine, which is the loopback redirect providers already accept from native apps. With no
    connector installed connect is inert, so the config may be absent."""
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
