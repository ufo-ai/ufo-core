"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import os
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID, uuid4

import sqlalchemy as sa
import uvicorn
from cryptography.fernet import Fernet
from dbos import DBOS, DBOSClient
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ufo.accounting import Pricing
from ufo.blob import BlobStore, blob_store_for
from ufo.browser import CdpProvider
from ufo.config import (
    IN_PROCESS_BACKEND,
    BlobConfig,
    Config,
    load_config,
)
from ufo.connectors import AuthProxy, ConnectorEntry, ConnectorRegistry
from ufo.credentials import CredentialStore
from ufo.db import current_workspace, init_db, init_owner_db, workspace_tx
from ufo.ext.context import CredentialAccess, context_for
from ufo.ext.loader import (
    NotRegisteredError,
    connector_clis,
    durable_surfaces,
    embed_backend,
    index_backend,
    load_manifests,
    memory_search,
    skill_registry,
    turn_subagent_grants,
    turn_subagents,
    validate_ext_tools,
)
from ufo.ext.manifest import AuthProxySpec, CdpProviderSpec, Manifest, SearchProviderSpec
from ufo.ext.surface import (
    SurfaceAuth,
    SurfaceContext,
    SurfaceSpec,
    WritebackPoller,
    writeback_workspaces,
)
from ufo.grants import ConnectFlow, GrantStore, OAuthProvider, install_connect_flow
from ufo.hub import Hub, InProcessHub
from ufo.indexing import EmbedClient, IndexBackend
from ufo.jobs import (
    JobRunner,
    PageChangeRunner,
    SandboxReaper,
    TurnDispatcher,
    bindings_from,
    core_jobs,
)
from ufo.loop.profiles import CORE_SUBAGENT_PROFILES
from ufo.loop.queue import Runtime, init_runtime
from ufo.loop.subagents import SubagentRegistry
from ufo.memory import DEFAULT_MEMORY_SEARCH_PROVIDER
from ufo.models.registry import model_registry
from ufo.o11y import init_o11y, log
from ufo.proxy_serve import OWNER_DSN_ENV, model_rule_base
from ufo.runtime_instance import BootGuard, ExecutorRecovery, Heartbeat, record_fleet_seat
from ufo.sandbox.fs_creds import DEFAULT_S3_REGION, AwsStsClient, SandboxFsCredentialMinter
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.proxy.rules import Rule, connector_transfer_hosts, derive_credential_rules
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, Carrier, ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION, DBOS_MAX_EXECUTOR_THREADS
from ufo.search import SearchProvider
from ufo.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SourceBackend,
    SyncDriver,
    register_sources,
)
from ufo.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.surfaces.artifacts import router as artifacts_router
from ufo.surfaces.cli import CONNECT_CALLBACK_PATH, callback_router, router
from ufo.surfaces.hub_tail import HubTailer
from ufo.workspace import init_workspace_credentials, ws

PROXY_STARTUP_TIMEOUT_SECONDS = 30


def run() -> None:
    """Start the configured dedicated server or shared service."""
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
    shared = config.serve.shared_workspace
    if shared:
        manifests = _shared_fleet_manifests(manifests)
        init_owner_db(_shared_owner_dsn(config))
    else:
        asyncio.run(_require_bootstrap())
    workspace_id = None if shared else asyncio.run(_sole_workspace_id())
    instance_id = uuid4()
    if workspace_id is not None:
        guard = BootGuard(config=config, workspace_id=workspace_id, instance_id=instance_id)
        asyncio.run(guard.admit())
    else:
        asyncio.run(record_fleet_seat(config, instance_id))
    heartbeat = Heartbeat(instance_id=instance_id)
    threading.Thread(
        target=lambda: asyncio.run(heartbeat.run()), name="instance-heartbeat", daemon=True
    ).start()
    validate_ext_tools(manifests, credentials)
    _validate_requires(config, manifests, credentials)
    init_workspace_credentials(credentials)
    blob = blob_store_for(config.blob)
    artifact_secret = os.environ.get(config.artifacts.token_secret_env, "")
    hub = _select_hub(config, manifests)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    carrier = _select_carrier(config, manifests)
    registry = model_registry(config, manifests)
    embed = embed_backend(manifests, config.memory.embed_backend, credentials)
    index = index_backend(manifests, config.memory.index_backend, embed, credentials)
    memory = memory_search(manifests, credentials, index, embed)
    connectors = _connector_registry(config, manifests, credentials)
    runtime = Runtime(
        config=config,
        blob=blob,
        workspace_fs=_sandbox_fs_minter(config.blob),
        hub=hub,
        carrier=carrier,
        cdp_provider=_select_cdp_provider(config, manifests, credentials),
        search_provider=_select_search_provider(config, manifests, credentials),
        connectors=connectors,
        proxy=_proxy_endpoint(config, manifests, credentials, registry.pricing),
        dbos=dbos_client,
        subagents=SubagentRegistry((*CORE_SUBAGENT_PROFILES, *turn_subagents(manifests))),
        subagent_grants=turn_subagent_grants(manifests),
        manifests=manifests,
        registry=registry,
        skills=skill_registry(manifests),
        credentials=credentials,
        index=index,
        embed=embed,
        memory=memory,
        artifact_token_secret=artifact_secret,
    )
    init_runtime(runtime)
    install_connect_flow(_connect_flow(credentials, config, manifests))
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": config.database.system_url,
            "executor_id": str(instance_id),
            "run_admin_server": False,
            "max_executor_threads": DBOS_MAX_EXECUTOR_THREADS,
        }
    )
    DBOS.launch()
    app = FastAPI(lifespan=_serve_lifespan)
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.instance_id = instance_id
    app.state.workspace_id = workspace_id
    app.state.durable_surfaces = durable_surfaces(manifests)
    app.state.writeback_poller = None
    app.state.blob = blob
    app.state.artifact_token_secret = artifact_secret
    if workspace_id is not None:
        app.include_router(router)
    else:
        app.include_router(callback_router)
    app.include_router(artifacts_router)
    sync_driver = SyncDriver(
        backends=_source_backends(manifests),
        blob=blob,
        postgres=config.database.url.startswith("postgresql"),
        auth_proxy=connectors,
    )
    page_feed = CorePageFeed(blob=blob)
    if workspace_id is not None:
        asyncio.run(register_sources(config.sources))
    _launch_jobs(runtime, sync_driver, page_feed)
    if workspace_id is not None:
        _mount_ext_routes(app, manifests, workspace_id, credentials, index, embed)
        _mount_surfaces(
            app,
            manifests,
            workspace_id,
            credentials,
            blob,
            hub,
            dbos_client,
            artifact_secret,
            config.connect.public_base_url,
        )
    else:
        _mount_ext_routes(app, manifests, None, credentials, index, embed)
        _mount_shared_surfaces(
            app,
            manifests,
            credentials,
            blob,
            hub,
            dbos_client,
            artifact_secret,
            config.connect.public_base_url,
        )
    log("serve.started", host=config.serve.host, port=config.serve.port, shared_workspace=shared)
    try:
        uvicorn.run(app, host=config.serve.host, port=config.serve.port, log_level="warning")
    finally:
        DBOS.destroy()
        asyncio.run(heartbeat.retire())


async def _require_bootstrap() -> None:
    try:
        async with workspace_tx() as connection:
            row = (await connection.execute(sa.select(tables.workspace.c.id))).first()
    except (sa.exc.OperationalError, sa.exc.ProgrammingError) as error:
        raise RuntimeError("schema missing — run `ufoctl init` first") from error
    if row is None:
        raise RuntimeError("workspace missing — run `ufoctl init` first")


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
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


def _launch_jobs(
    runtime: Runtime,
    sync_driver: SyncDriver,
    page_feed: CorePageFeed,
) -> None:
    """Register this workspace's jobs — core's own (the source sync driver, the turn dispatcher
    that recovers queued turns and re-admits parked turns, and the sandbox reaper) plus every
    installed extension's (the memory extension's memory-index and page-index jobs among them) — as
    DBOS schedules and one-shot enqueues, after
    launch so the system store is live. Registration is the synchronous DBOS API (off the loop, at
    startup); a handler may read a declared credential or the deploy index/embed backends or the
    page feed, so once any job is registered the credential key must be set."""
    admission = Admission(dbos=runtime.dbos, durable_surfaces=durable_surfaces(runtime.manifests))

    def invoker_for(workspace_id: UUID) -> AdmissionInvoker:
        return AdmissionInvoker(admission=admission, workspace_id=workspace_id)

    page_change_runner = PageChangeRunner(
        manifests=runtime.manifests,
        pages=page_feed,
        invoker_factory=invoker_for,
        index=runtime.index,
        embed=runtime.embed,
        blob=runtime.blob,
        registry=runtime.registry,
    )
    bindings = bindings_from(
        runtime.manifests,
        core_jobs(
            sync_driver,
            TurnDispatcher(client=runtime.dbos),
            SandboxReaper(carrier=runtime.carrier, backend=runtime.config.sandbox.backend),
            page_change_runner,
        ),
    )
    JobRunner(
        bindings=bindings,
        invoker_factory=invoker_for,
        index=runtime.index,
        embed=runtime.embed,
        pages=page_feed,
        blob=runtime.blob,
        registry=runtime.registry,
    ).launch()


async def _sole_workspace_id() -> UUID:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()


def _sandbox_fs_minter(blob: BlobConfig) -> SandboxFsCredentialMinter | None:
    """The STS-scoped-credential minter the S3-backed workspace mount needs, or None on the local
    filesystem backend (a bind mount needs no minter). BlobConfig's validator guarantees the S3
    backend carries bucket/s3_url/sts_role_arn; the None-checks re-read them for the type checker
    and fail loud the same way `blob_store_for` does."""
    if blob.backend != "s3":
        return None
    if blob.bucket is None or blob.s3_url is None or blob.sts_role_arn is None:
        raise ValueError("the s3 blob backend requires bucket, s3_url, and sts_role_arn")
    return SandboxFsCredentialMinter(
        sts=AwsStsClient(endpoint_url=blob.sts_endpoint, region=blob.region),
        role_arn=blob.sts_role_arn,
        bucket=blob.bucket,
        s3_url=blob.s3_url,
        region=blob.region or DEFAULT_S3_REGION,
        path_style=blob.path_style,
    )


def _select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> Carrier:
    """The one sandbox backend this process runs, chosen by `[sandbox] backend`: core's default
    `local` carrier plus every carrier an extension contributes via its `carriers` Manifest point
    (`docker`, `e2b`, a remote runner). An extension name that collides with the built-in or another
    extension fails loud, and a backend name no carrier registers fails loud — so the selected name
    resolves to exactly one factory, built once here and held as `Runtime.carrier`. A remote
    backend with no `[sandbox] proxy_public_url` fails loud too: its sandbox could reach neither the
    process-local proxy nor a metered egress route, so
    it would run open — never a silent default."""
    factories: dict[str, Callable[[], Carrier]] = {"local": LocalCarrier}
    off_cluster: set[str] = set()
    for manifest in manifests:
        for spec in manifest.carriers:
            if spec.name in factories:
                raise RuntimeError(f"two carriers register backend {spec.name!r}")
            factories[spec.name] = spec.factory
            if spec.off_cluster:
                off_cluster.add(spec.name)
    factory = factories.get(config.sandbox.backend)
    if factory is None:
        raise NotRegisteredError(
            f"sandbox backend {config.sandbox.backend!r} is not a registered carrier "
            f"(have {sorted(factories)})"
        )
    if config.sandbox.backend in off_cluster:
        public_url = config.sandbox.proxy_public_url
        if not public_url:
            raise RuntimeError(
                f"sandbox backend {config.sandbox.backend!r} is remote and cannot reach the "
                "process-local egress proxy; set [sandbox] proxy_public_url to the externally "
                "reachable HTTPS proxy URL so in-sandbox egress is credential-injected, "
                "default-denied, and metered"
            )
        parsed = urlparse(public_url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise RuntimeError(
                f"sandbox backend {config.sandbox.backend!r} is remote; "
                "[sandbox] proxy_public_url must be an HTTPS URL so its run token is encrypted "
                "in transit"
            )
    return factory()


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
    workspace_id: UUID | None,
    credentials: CredentialStore | None,
    index: IndexBackend,
    embed: EmbedClient,
) -> None:
    """Mount extension routes with a verified workspace-scoped context.

    A dedicated server mounts every route against its pinned workspace; a route declaring
    `identify` must independently verify that same workspace. Shared serve mounts only identified
    routes and runs each under the workspace its verifier returns. An unverified request is refused
    before the handler can touch the database or credentials."""
    for manifest in manifests:
        routes = tuple(
            spec
            for spec in manifest.routes
            if workspace_id is not None or spec.identify is not None
        )
        if manifest.routes and not routes:
            log("serve.shared_route.deferred", extension=manifest.name)
        if not routes:
            continue
        if credentials is None:
            raise RuntimeError(
                f"extension {manifest.name!r} serves routes but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(manifest.name, declared, index, embed)
        for spec in routes:

            async def endpoint(
                request: Request,
                handler=spec.handler,
                identify=spec.identify,
                extension_context=context,
                pinned_workspace=workspace_id,
            ) -> Response:
                identified = identify(request) if identify is not None else pinned_workspace
                if identified is None or (
                    pinned_workspace is not None and identified != pinned_workspace
                ):
                    return Response("unauthorized", status_code=401)
                with ws(identified):
                    return await handler(extension_context, request)

            app.add_route(
                f"/ext/{manifest.name}/{spec.path.lstrip('/')}",
                endpoint,
                methods=[spec.method],
            )


def _mount_surfaces(
    app: FastAPI,
    manifests: tuple[Manifest, ...],
    workspace_id: UUID,
    credentials: CredentialStore | None,
    blob: BlobStore,
    hub: Hub,
    dbos_client: DBOSClient,
    artifact_secret: str,
    public_base_url: str | None,
) -> None:
    """Mount every installed surface's routes under `/surface/<name>`, each request bound to that
    surface's privileged SurfaceContext, and run one writeback poller over those that deliver by
    writeback. A surface reads its own credential slots (a bot token, a signing secret) in-process,
    so an installed surface whose extension declares slots without a credential key set fails loud
    at boot rather than on the first event; a slotless surface (web) mounts with no key. A durable
    surface (declaring `post`/`attach`) joins the poller; a live surface admits without writeback
    and tails the hub in its own route, so the poller never sees its turns — the tailer and member
    admission are injected capabilities."""
    member_admission = MemberAdmission(
        workspace_id=workspace_id,
        admission=Admission(dbos=dbos_client, durable_surfaces=durable_surfaces(manifests)),
    )
    tailer = HubTailer(hub=hub)
    registered: dict[str, SurfaceSpec] = {}
    contexts: dict[str, SurfaceContext] = {}
    for manifest in manifests:
        for spec in manifest.surfaces:
            if manifest.credentials and credentials is None:
                raise RuntimeError(f"surface {spec.name!r} needs a credential key but none is set")
            context = SurfaceContext(
                workspace_id=workspace_id,
                surface=spec.name,
                blob=blob,
                _admitter=member_admission,
                _tailer=tailer,
                _credentials=credentials,
                _artifact_token_secret=artifact_secret,
                _public_base_url=public_base_url,
            )
            contexts[spec.name] = context
            if spec.post is not None:
                registered[spec.name] = spec
            for route in spec.routes:

                async def endpoint(
                    request: Request,
                    handler=route.handler,
                    surface_context=context,
                    wsid=workspace_id,
                ) -> Response:
                    with ws(wsid):
                        return await handler(surface_context, request)

                app.add_route(
                    f"/surface/{spec.name}/{route.path}".rstrip("/"),
                    endpoint,
                    methods=[route.method],
                )
    if registered:

        def context_for(candidate_workspace: UUID, surface: str) -> SurfaceContext:
            if candidate_workspace != workspace_id:
                raise RuntimeError("dedicated writeback selected another workspace")
            return contexts[surface]

        app.state.writeback_poller = WritebackPoller(
            worker_id=uuid4().hex,
            surfaces=registered,
            context_for=context_for,
            candidates=writeback_workspaces(),
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


def _shared_fleet_capable(spec: SurfaceSpec) -> bool:
    """Whether shared serve can mount this surface: every request must authenticate its workspace
    before core binds it. Live and durable delivery use the same bound context."""
    return spec.identify is not None


def _shared_fleet_manifests(manifests: tuple[Manifest, ...]) -> tuple[Manifest, ...]:
    kept: list[Manifest] = []
    for manifest in manifests:
        blocked = [spec.name for spec in manifest.surfaces if not _shared_fleet_capable(spec)]
        if blocked:
            log(
                "serve.shared_fleet.extension_excluded",
                extension=manifest.name,
                surfaces=",".join(blocked),
            )
            continue
        kept.append(manifest)
    return tuple(kept)


def _mount_shared_surfaces(
    app: FastAPI,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    blob: BlobStore,
    hub: Hub,
    dbos_client: DBOSClient,
    artifact_secret: str,
    public_base_url: str | None,
) -> None:
    """Install the fleet-wide `WorkspaceScopeBoundary` and mount each shared-fleet-capable
    surface's routes, resolving the workspace per request instead of pinning one at boot:
    `SurfaceSpec.identify` verifies the request's signed bearer and returns the workspace it
    claims, which the endpoint binds via `current_workspace.set` for the whole request (an
    unresolved token is a 401) and the boundary releases once the response has fully streamed. The
    per-request SurfaceContext carries a `MemberAdmission` bound to that workspace so its
    admitted turn lands scoped to the token's workspace and no other.

    A surface that declares no `identify` cannot scope a shared request and is skipped. Durable
    surfaces share one bounded writeback poller: its owner read selects only workspace ids, then
    every claim, build, credential read, post, and attachment runs under that workspace's
    binding."""
    app.add_middleware(WorkspaceScopeBoundary)
    admission = Admission(dbos=dbos_client, durable_surfaces=durable_surfaces(manifests))
    tailer = HubTailer(hub=hub)
    registered: dict[str, SurfaceSpec] = {}

    def context_for(workspace_id: UUID, surface: str) -> SurfaceContext:
        return SurfaceContext(
            workspace_id=workspace_id,
            surface=surface,
            blob=blob,
            _admitter=MemberAdmission(admission=admission, workspace_id=workspace_id),
            _tailer=tailer,
            _credentials=credentials,
            _artifact_token_secret=artifact_secret,
            _public_base_url=public_base_url,
        )

    for manifest in manifests:
        for spec in manifest.surfaces:
            auth = SurfaceAuth(
                _credentials=credentials,
                _declared=frozenset(slot.name for slot in manifest.credentials),
                _surface=spec.name,
            )
            resolver = spec.identify
            if resolver is None or not _shared_fleet_capable(spec):
                log("serve.shared_surface.deferred", surface=spec.name)
                continue
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
    if registered:
        app.state.writeback_poller = WritebackPoller(
            worker_id=uuid4().hex,
            surfaces=registered,
            context_for=context_for,
            candidates=writeback_workspaces(),
        )


@asynccontextmanager
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run this instance's app-loop background work: the executor-recovery sweep that re-dispatches
    workflows stranded by dead peers, and, when a durable surface is installed, the writeback
    poller, the durable half of surface delivery off the hub and off the turn loop. The shared
    poller binds each selected workspace before delivery. The heartbeat is NOT here: liveness must
    span the whole boot (jobs enqueue under this executor id before uvicorn starts) and survive an
    app-loop stall, so `run` drives it on a dedicated thread from the moment the seat exists, and
    retires the seat only after `DBOS.destroy` has stopped all execution — a seat freed while
    queued workflows still run would hand a peer a second live execution."""
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(ExecutorRecovery().run())]
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
        return _local_egress_proxy(config, manifests, credentials, pricing)
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
) -> ProxyEndpoint:
    """The single-node sandbox's sole route out, run in-process on its own event loop — a
    standalone network service, not part of the turn loop, that outlives every turn for the
    process's life. One workspace's serve owns it, so (unlike the shared `ufoctl proxy`) it mints an
    ephemeral CA with no shared trust material to carry and injects this workspace's own credential
    secrets with no injecting-slot ban. The resolver reads the turn's agent and grants per turn
    through `workspace_tx` — already scoped to the configured application database by `init_db` —
    and authorizes each keyed-host CONNECT against the turn's live status, so a real key is injected
    only while the turn runs. It binds `proxy_port` and carries no `public_url`: a local carrier
    forms a process-local address from the port alone."""
    resolver = PerAgentRules(
        base=asyncio.run(_local_rule_base(config, manifests, credentials)),
        grants=GrantStore() if credentials is not None else None,
        transfer_hosts=connector_transfer_hosts(manifests),
        clis=connector_clis(manifests),
    )
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def _boot() -> ProxyEndpoint:
        ca_cert, ca_key = await generate_ca()
        return await EgressProxy(
            resolve=resolver.resolve,
            authorize=resolver.turn_live,
            ca_cert=ca_cert,
            ca_key=ca_key,
            pricing=pricing,
        ).start(port=config.sandbox.proxy_port)

    return asyncio.run_coroutine_threadsafe(_boot(), loop).result(PROXY_STARTUP_TIMEOUT_SECONDS)


async def _local_rule_base(
    config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None
) -> tuple[Rule, ...]:
    """The local proxy's static base: the shared model-provider egress plus this single workspace's
    injected credential slots. The shared proxy forbids injecting slots; the local proxy owns the
    one workspace, so it swaps their stored secrets onto the wire itself. A slot that needs
    injection with no credential key set fails loud."""
    base = model_rule_base(config)
    if not any(slot.injection for manifest in manifests for slot in manifest.credentials):
        return base
    if credentials is None:
        raise RuntimeError(
            f"credential key env {config.credentials.key_env!r} is unset but a slot needs injection"
        )
    workspace_id = await _sole_workspace_id()
    return (*base, *await derive_credential_rules(manifests, workspace_id, credentials))


BIND_ADDRESSES = frozenset({"0.0.0.0", "127.0.0.1", "localhost", "::", "::1"})


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
        entries=entries, fallback=_select_auth_proxy(config, manifests, credentials)
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
    )


def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str:
    """The external callback URL both OAuth legs present, derived from `connect.public_base_url`. A
    provider redirects the member's browser here, so a bind address (0.0.0.0 / 127.0.0.1) or a
    scheme-less value is unreachable and fails loud the moment a connector is registered. With no
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
    if parsed.hostname in BIND_ADDRESSES:
        raise RuntimeError(
            f"connect.public_base_url {base!r} is a bind address, not reachable by the provider's "
            "OAuth redirect; set the deploy's public URL"
        )
    return f"{base.rstrip('/')}{CONNECT_CALLBACK_PATH}"
