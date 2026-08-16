"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import uvicorn
from cryptography.fernet import Fernet
from dbos import DBOS, DBOSClient
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ufo.activity import SKILL_LOAD_TOOL
from ufo.ambient_reply import AmbientReplyClassifier
from ufo.bearer import LOGIN_PATH
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
from ufo.connectors import AuthProxy, ConnectorEntry, ConnectorRegistry, SourceCredentialResolver
from ufo.credentials import (
    CredentialRequests,
    CredentialStore,
    install_credential_requests,
)
from ufo.db import (
    current_workspace,
    dispose_loop_engines,
    init_db,
    init_owner_db,
    verify_db_reachable,
)
from ufo.durability import ReplaySafeSerializer, replay_safe_client
from ufo.ext.context import ConversationProbes, CredentialAccess, ModelAccess
from ufo.ext.context import context_for as extension_context_for
from ufo.ext.conversation_slots import BoundConversationSlot
from ufo.ext.loader import (
    CORE_OBJECT_KINDS,
    NotRegisteredError,
    connector_clis,
    core_object_kinds,
    durable_surfaces,
    embed_backend,
    index_backend,
    injecting_slots,
    load_manifests,
    member_object_registry,
    memory_search,
    skill_registry,
    turn_runtime_skills,
    turn_subagent_grants,
    turn_subagents,
    validate_ext_tools,
)
from ufo.ext.manifest import (
    AuthProxySpec,
    CdpProviderSpec,
    Manifest,
    SearchProviderSpec,
    conversation_slot_declarations,
    declared_slots,
    open_connector_namespace,
)
from ufo.ext.surface import (
    DeployExtensionView,
    SubagentDetail,
    SurfaceAuth,
    SurfaceContext,
    SurfaceIdentityContext,
    SurfaceSpec,
    WritebackPoller,
    writeback_workspaces,
)
from ufo.grants import ConnectFlow, GrantStore, OAuthProvider, install_connect_flow
from ufo.hub import Hub, InProcessHub
from ufo.indexing import EmbedClient, IndexBackend
from ufo.jobs import (
    InvokerFactory,
    JobRunner,
    PageChangeRunner,
    TurnDispatcher,
    bindings_from,
    core_jobs,
)
from ufo.loop.delivery import DeliverySweep
from ufo.loop.profiles import CORE_SUBAGENT_PROFILES
from ufo.loop.queue import Runtime, init_runtime
from ufo.loop.subagent_catalog import subagent_catalog_skill
from ufo.loop.subagents import SubagentRegistry, subagent_system_prompt
from ufo.memory import DEFAULT_MEMORY_SEARCH_PROVIDER, MemorySearch
from ufo.models.catalog_skill import model_catalog_skill
from ufo.models.interface import AUTO_MODEL
from ufo.models.pricing import Pricing
from ufo.models.registry import model_registry
from ufo.o11y import init_o11y, log
from ufo.objects import BoundKind
from ufo.proxy_serve import OWNER_DSN_ENV, model_rule_base
from ufo.runtime_instance import (
    CancelReconciler,
    ExecutorRecovery,
    Heartbeat,
    record_fleet_seat,
)
from ufo.sandbox.cache import (
    CACHE_CALLBACK_HOST,
    CACHE_CALLBACK_PORT,
    CACHE_CONTROL_TOKEN_ENV,
    CACHE_HOST,
    CACHE_PKG_HOSTS,
    parse_cache_daemon,
)
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.exec_env import ProbeEnv
from ufo.sandbox.proxy.credential_callback import CredentialCallback
from ufo.sandbox.proxy.rules import (
    connector_transfer_hosts,
    derive_artifact_store_rules,
    derive_manifest_rules,
)
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from ufo.sandbox.select import select_carrier
from ufo.sandbox.session import (
    EGRESS_CA_CERT_ENV,
    ProbeTokenCodec,
    ProxyEndpoint,
    RunTokenCodec,
)
from ufo.sandbox.terminal import Terminals, TerminalTransport
from ufo.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION, DBOS_MAX_EXECUTOR_THREADS
from ufo.search import SearchProvider
from ufo.skills.runtime import RuntimeSkill, SkillRegistry
from ufo.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SourceBackend,
    SourceIdentityResolver,
    SyncDriver,
)
from ufo.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.surfaces.artifacts import router as artifacts_router
from ufo.surfaces.cli import CONNECT_CALLBACK_PATH, callback_router
from ufo.surfaces.hub_tail import HubTailer
from ufo.surfaces.stop import MemberStop
from ufo.workspace import init_workspace_credentials, ws

PROXY_STARTUP_TIMEOUT_SECONDS = 30
RESERVED_HOST_PREFIXES = (LOGIN_PATH, "/v1/onboard", "/ufo")


def _assert_no_reserved_routes(app: FastAPI) -> None:
    """On the shared host one ingress hands `/login`, `/v1/onboard`, and `/ufo` to the onboarding
    gateway and everything else to this fleet, so the sign-in flow is same-origin with the product.
    The fleet must therefore mount nothing under those prefixes — otherwise the ingress silently
    shadows it. Asserting it at boot makes the split a fail-loud invariant, not a hand-kept
    property of the ingress template."""
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


def run() -> None:
    """Start the shared fleet: one process serving every workspace, resolving the workspace per
    request (from the caller's token) and per turn (from the workflow argument), scoping each
    transaction by the ambient `current_workspace`."""
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
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
    validate_ext_tools(manifests, credentials)
    _validate_requires(config, manifests, credentials)
    init_workspace_credentials(credentials)
    blob_backend = blob_store_for(config.blob)
    blob = WorkspaceBlobStore(backend=blob_backend)
    fleet_blob = FleetBlobStore(backend=blob_backend)
    artifact_secret = os.environ.get(config.artifacts.token_secret_env, "")
    hub = _select_hub(config, manifests)
    dbos_client = replay_safe_client(config.database.system_url)
    carrier, carrier_spec = select_carrier(config, manifests)
    registry = model_registry(config, manifests)
    embed = embed_backend(manifests, config.memory.embed_backend, credentials)
    index = index_backend(manifests, config.memory.index_backend, credentials)
    memory = memory_search(manifests, credentials, index, embed)
    subagents = SubagentRegistry((*CORE_SUBAGENT_PROFILES, *turn_subagents(manifests)))
    skills = skill_registry(
        manifests, (model_catalog_skill(registry), subagent_catalog_skill(subagents))
    )
    connectors = _connector_registry(config, manifests, credentials)
    run_tokens = RunTokenCodec.from_env()
    admission = Admission(dbos=dbos_client, durable_surfaces=durable_surfaces(manifests))

    def invoker_for(workspace_id: UUID) -> AdmissionInvoker:
        return AdmissionInvoker(admission=admission, workspace_id=workspace_id)

    runtime = Runtime(
        config=config,
        blob=blob,
        sandboxes=ConversationSandbox(
            carrier=carrier,
            backend=config.sandbox.backend,
            off_cluster=carrier_spec.off_cluster,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=_proxy_endpoint(
                config, manifests, credentials, registry.pricing, run_tokens, blob_backend
            ),
            workspace_root=config.sandbox.workspace_root,
            terminals=_select_terminal_transport(config, manifests, fleet_blob),
        ),
        hub=hub,
        cdp_provider=_select_cdp_provider(config, manifests, credentials),
        search_provider=_select_search_provider(config, manifests, credentials),
        connectors=connectors,
        run_tokens=run_tokens,
        dbos=dbos_client,
        invoker_for=invoker_for,
        subagents=subagents,
        subagent_grants=turn_subagent_grants(manifests),
        manifests=manifests,
        registry=registry,
        skills=skills,
        credentials=credentials,
        index=index,
        embed=embed,
        memory=memory,
        artifact_token_secret=artifact_secret,
        tailer=HubTailer(hub=hub),
    )
    init_runtime(runtime)
    install_connect_flow(_connect_flow(credentials, config, manifests))
    install_credential_requests(
        None
        if credentials is None
        else CredentialRequests(
            fernet=credentials.fernet,
            declared=frozenset(
                slot.name for manifest in manifests for slot in manifest.credentials
            ),
            fillable=frozenset(
                slot.name
                for manifest in manifests
                for slot in manifest.credentials
                if slot.member_filled
            ),
        )
    )
    dbos = DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": config.database.system_url,
            "executor_id": str(instance_id),
            "run_admin_server": False,
            "max_executor_threads": DBOS_MAX_EXECUTOR_THREADS,
            "serializer": ReplaySafeSerializer(),
        }
    )
    DBOS.launch()
    app = FastAPI(lifespan=_serve_lifespan)
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.instance_id = instance_id
    app.state.durable_surfaces = durable_surfaces(manifests)
    app.state.writeback_poller = None
    app.state.blob = blob
    app.state.artifact_token_secret = artifact_secret
    app.include_router(callback_router)
    app.include_router(artifacts_router)
    sync_driver = SyncDriver(
        backends=_source_backends(manifests),
        blob=blob,
        postgres=config.database.url.startswith("postgresql"),
        source_credentials=SourceCredentialResolver(connectors),
        identity_resolvers=_source_identity_resolvers(manifests, credentials, blob),
    )
    page_feed = CorePageFeed(blob=blob)
    _launch_jobs(runtime, invoker_for, sync_driver, page_feed)
    _mount_ext_routes(app, manifests, credentials, index, embed)
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
        ambient_reply=AmbientReplyClassifier(
            model=ModelAccess(replace(registry, auto_model=config.models.ambient_reply_model))
        ),
        sandbox_sizes=carrier_spec.sizes,
        skills=skills,
        user_skills=lambda: turn_runtime_skills(manifests, credentials, index, embed),
        subagents=runtime.subagents,
        memory=memory,
        objects=member_object_registry(
            manifests,
            credentials,
            index,
            embed,
            public_base_url=config.connect.public_base_url,
        ),
    )
    _assert_no_reserved_routes(app)
    log("serve.started", host=config.serve.host, port=config.serve.port)
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
) -> None:
    """Register this workspace's jobs — core's own (the source sync driver, the turn dispatcher
    that recovers queued turns and re-admits parked turns, and the delivery sweep that hands back
    the delegated children their own execution could not) plus every
    installed extension's (the memory extension's memory-index and page-index jobs among them) — as
    DBOS schedules and one-shot enqueues, after
    launch so the system store is live. Registration is the synchronous DBOS API (off the loop, at
    startup); a handler may read a declared credential or the deploy index/embed backends or the
    page feed, so once any job is registered the credential key must be set."""
    probes = ConversationProbes(
        runtime.sandboxes,
        ProbeTokenCodec(secret=runtime.run_tokens.secret),
        ProbeEnv(
            grants=GrantStore() if runtime.credentials is not None else None,
            clis=connector_clis(runtime.manifests),
            credentials=runtime.credentials,
            slots=injecting_slots(runtime.manifests),
        ).exports,
    )
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
    )
    bindings = bindings_from(
        runtime.manifests,
        core_jobs(
            sync_driver,
            TurnDispatcher(client=runtime.dbos),
            page_change_runner,
            DeliverySweep(invoker_for=invoker_for, registry=runtime.subagents),
        ),
    )
    JobRunner(
        bindings=bindings,
        invoker_factory=invoker_for,
        index=runtime.index,
        embed=runtime.embed,
        pages=page_feed,
        blob=runtime.blob,
        sandboxes=runtime.sandboxes,
        registry=runtime.registry,
        probes=probes,
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


_REQUIRED_SEAM_CHECKS: dict[
    str, Callable[[Config, tuple[Manifest, ...], CredentialStore | None], None]
] = {
    "cdp_providers": _require_cdp_provider,
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
) -> None:
    """Mount every extension route under `/ext/<name>/<path>` with a verified workspace-scoped
    context. `RouteSpec.identify` resolves and returns the request's workspace, which core binds for
    the handler; an unresolved request is refused before the handler can touch the database or
    credentials."""
    for manifest in manifests:
        if not manifest.routes:
            continue
        if credentials is None:
            raise RuntimeError(
                f"extension {manifest.name!r} serves routes but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = extension_context_for(manifest.name, declared, index, embed)
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
    ambient_reply: AmbientReplyClassifier,
    skills: SkillRegistry,
    user_skills: "Callable[[], Awaitable[tuple[RuntimeSkill, ...]]]",
    subagents: SubagentRegistry,
    sandbox_sizes: tuple[str, ...] = (),
    memory: MemorySearch | None = None,
    objects: "Mapping[str, BoundKind] | None" = None,
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
    admission = Admission(dbos=dbos_client, durable_surfaces=durable_surfaces(manifests))
    tailer = HubTailer(hub=hub)
    stopper = MemberStop(client=dbos_client, hub=hub, admission=admission)
    registered: dict[str, SurfaceSpec] = {}

    deploy_sandbox_internet = any(manifest.sandbox_internet for manifest in manifests)
    deploy_extensions = tuple(
        DeployExtensionView(
            name=manifest.name, version=manifest.version, sandbox_internet=manifest.sandbox_internet
        )
        for manifest in sorted(manifests, key=lambda manifest: manifest.name)
    )
    slots = declared_slots(manifests)
    conversation_slots = tuple(
        BoundConversationSlot(
            extension=manifest.name,
            provider=provider,
            ext=extension_context_for(
                manifest.name,
                frozenset(slot.name for slot in manifest.credentials),
                credential_sources=tuple(
                    (slot.name, slot.source)
                    for slot in manifest.credentials
                    if slot.source is not None
                ),
                credential_store=credentials,
            ),
        )
        for manifest, provider in conversation_slot_declarations(manifests)
    )
    subagent_roster = tuple(
        SubagentDetail(
            name=profile.name,
            model=profile.model,
            prompt=subagent_system_prompt(profile, skills=skills.index()),
            max_rounds=profile.max_rounds,
            untrusted_output=profile.untrusted_output,
            loads_skills=SKILL_LOAD_TOOL in profile.tool_names,
        )
        for profile in sorted(subagents.profiles, key=lambda profile: profile.name)
    )
    kind_schemas = {
        bound.kind.name: bound.kind.spec_model.model_json_schema()
        for bound in (*CORE_OBJECT_KINDS, *core_object_kinds(manifests, credentials))
    } | {
        kind.name: kind.spec_model.model_json_schema()
        for manifest in manifests
        for kind in manifest.objects
    }

    def context_for(workspace_id: UUID, surface: str) -> SurfaceContext:
        return SurfaceContext(
            workspace_id=workspace_id,
            surface=surface,
            blob=blob,
            _sandboxes=sandboxes,
            _admitter=MemberAdmission(admission=admission, workspace_id=workspace_id),
            _tailer=tailer,
            _stopper=stopper,
            _credentials=credentials,
            _artifact_token_secret=artifact_secret,
            _public_base_url=public_base_url,
            _home_surface=home_surface(manifests),
            _ingress_public_url=ingress_public_url,
            _deploy_sandbox_internet=deploy_sandbox_internet,
            _deploy_extensions=deploy_extensions,
            _models=models,
            _sandbox_sizes=sandbox_sizes,
            _skills=skills,
            _user_skills=user_skills,
            _subagents=subagent_roster,
            _declared_slots=slots,
            _ambient_reply=ambient_reply,
            _object_schemas=kind_schemas,
            _memory=memory,
            _objects=objects or {},
            _conversation_slots=conversation_slots,
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
            for route in spec.routes:

                async def endpoint(
                    request: Request,
                    handler=route.handler,
                    identify=resolver,
                    surface=spec.name,
                    surface_auth=auth,
                ) -> Response:
                    resolution = await identify(request, surface_auth)
                    if isinstance(resolution, Response):
                        return resolution
                    if resolution is None:
                        return Response("unauthorized", status_code=401)
                    workspace_id = resolution
                    current_workspace.set(workspace_id)
                    return await handler(context_for(workspace_id, surface), request)

                app.add_route(
                    f"/surface/{spec.name}/{route.path}".rstrip("/"),
                    endpoint,
                    methods=[route.method],
                )
            log("serve.shared_surface.mounted", surface=spec.name)
    _mount_home(app, manifests)
    if registered:
        app.state.writeback_poller = WritebackPoller(
            worker_id=uuid4().hex,
            surfaces=registered,
            context_for=context_for,
            candidates=writeback_workspaces(),
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
    turns it spawned (cancelling any turn left live under a cancelled ancestor), and, when a durable
    surface is installed, the writeback poller, the durable half of surface delivery off the hub and
    off the turn loop. The shared poller binds each selected workspace before delivery. The
    heartbeat is NOT here: liveness must
    span the whole boot (jobs enqueue under this executor id before uvicorn starts) and survive an
    app-loop stall, so `run` drives it on a dedicated thread from the moment the seat exists, and
    retires the seat only after `DBOS.destroy` has stopped all execution — a seat freed while
    queued workflows still run would hand a peer a second live execution."""
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(ExecutorRecovery().run()),
            group.create_task(CancelReconciler(client=app.state.dbos).run()),
        ]
        poller = app.state.writeback_poller
        if poller is not None:
            tasks.append(group.create_task(poller.run()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()


def _proxy_endpoint(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    pricing: Pricing,
    run_tokens: RunTokenCodec,
    blob: FilesystemBlobStore | S3BlobStore,
) -> ProxyEndpoint:
    """The egress proxy endpoint the carrier threads into every sandbox, in the shape this deploy
    takes. With `[sandbox] proxy_public_url` set (hosted, multi-node) the proxy runs as a standalone
    `ufoctl proxy` outside this process, so serve only carries the address and trust material: the
    stable shared CA from env (the sandbox's trust anchor for the proxy's minted leaves), the
    stable `proxy_port`, and the public base. An unset CA fails loud rather than
    shipping a sandbox that reaches no host. Unset (local, single-node) serve runs the proxy
    in-process, minting its own ephemeral CA — no shared trust material to source, no separate
    service to run alongside."""
    if config.sandbox.proxy_public_url is None:
        return _local_egress_proxy(config, manifests, credentials, pricing, run_tokens, blob)
    ca_cert = os.environ.get(EGRESS_CA_CERT_ENV)
    if not ca_cert:
        raise RuntimeError(
            f"{EGRESS_CA_CERT_ENV} must hold the shared egress proxy's CA certificate (PEM) so the "
            "sandbox trusts the proxy's TLS; the proxy runs as a separate `ufoctl proxy` process"
        )
    return ProxyEndpoint(
        port=config.sandbox.proxy_port,
        ca_cert=ca_cert,
        public_url=config.sandbox.proxy_public_url,
    )


def _local_egress_proxy(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    pricing: Pricing,
    run_tokens: RunTokenCodec,
    blob: FilesystemBlobStore | S3BlobStore,
) -> ProxyEndpoint:
    """The single-node sandbox's sole route out, run in-process on its own event loop — a
    standalone network service, not part of the turn loop, that outlives every turn for the
    process's life. It mints an ephemeral CA with no shared trust material to carry. The resolver
    reads the turn's agent, its workspace's keyed credentials, and its grants per turn through
    `workspace_tx`, binding each request's own workspace, and authorizes each keyed-host CONNECT
    against the turn's live status. It binds `proxy_port` and carries no `public_url`: a local
    carrier forms a process-local address from the port alone."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    cache_daemon = parse_cache_daemon(config.sandbox.cache_daemon)

    async def _boot() -> ProxyEndpoint:
        resolver = PerAgentRules(
            base=(*model_rule_base(config), *await derive_artifact_store_rules(blob)),
            grants=GrantStore() if credentials is not None else None,
            credentials=credentials,
            slots=injecting_slots(manifests),
            internet=derive_manifest_rules(manifests),
            transfer_hosts=connector_transfer_hosts(manifests),
            clis=connector_clis(manifests),
            cache_host=CACHE_HOST if cache_daemon is not None else None,
            cache_pkg_hosts=CACHE_PKG_HOSTS if cache_daemon is not None else (),
        )
        ca_cert, ca_key = await generate_ca()
        endpoint = await EgressProxy(
            resolve=resolver.resolve,
            authorize=resolver.turn_live,
            ca_cert=ca_cert,
            ca_key=ca_key,
            run_tokens=run_tokens,
            generation=resolver.rules_generation,
            pricing=pricing,
            cache_daemon=cache_daemon,
        ).start(port=config.sandbox.proxy_port)
        if cache_daemon is not None:
            # The cache daemon resolves its git credential through this loopback callback; without
            # it every cache-routed clone would 502. Runs for the process's life on the proxy loop.
            token = os.environ.get(CACHE_CONTROL_TOKEN_ENV)
            if not token:
                raise RuntimeError(
                    f"{CACHE_CONTROL_TOKEN_ENV} must be set when the sandbox cache is enabled"
                )
            await CredentialCallback(
                credentials=credentials,
                slots=injecting_slots(manifests),
                token=token,
            ).serve(CACHE_CALLBACK_HOST, CACHE_CALLBACK_PORT)
        return endpoint

    return asyncio.run_coroutine_threadsafe(_boot(), loop).result(PROXY_STARTUP_TIMEOUT_SECONDS)


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
    entries: dict[str, ConnectorEntry] = {}
    for manifest in manifests:
        for connector in manifest.connectors:
            provider = connector.oauth.provider
            if provider in entries:
                raise RuntimeError(f"two extensions register connector provider {provider!r}")
            entries[provider] = ConnectorEntry(
                provider=provider, label=connector.label, broker=connector.broker
            )
    return ConnectorRegistry(
        entries=entries,
        resolver=open_connector_namespace(manifests),
        fallback=_select_auth_proxy(config, manifests, credentials),
    )


def _connect_flow(
    credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]
) -> ConnectFlow | None:
    """The process's connect flow — the tool validates against it, a surface privately authorizes
    through it, and the OAuth callback completes through it — sharing the credential key that seals
    its state and encrypts its tokens. No key means grants cannot be recorded, so all fail loud.
    The provider registry is every installed connector's OAuth descriptor keyed by its provider
    name; the `redirect_uri` is this deploy's external callback URL, the one value both legs of the
    handoff present."""
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
        resolver=open_connector_namespace(manifests),
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
