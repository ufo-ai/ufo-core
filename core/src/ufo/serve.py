"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import os
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from uuid import UUID, uuid4

import sqlalchemy as sa
import uvicorn
from cryptography.fernet import Fernet
from dbos import DBOS, DBOSClient
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from ufo.accounting import Pricing
from ufo.blob import BlobStore, blob_store_for
from ufo.browser import CdpProvider
from ufo.config import (
    IN_PROCESS_BACKEND,
    BlobConfig,
    Config,
    load_config,
)
from ufo.connectors import AuthProxy
from ufo.credentials import CredentialStore
from ufo.db import current_workspace, init_db, init_owner_db, workspace_tx
from ufo.ext.context import CredentialAccess, context_for
from ufo.ext.loader import (
    NotRegisteredError,
    durable_surfaces,
    embed_backend,
    index_backend,
    load_manifests,
    skill_registry,
    turn_subagent_grants,
    turn_subagents,
    validate_ext_tools,
)
from ufo.ext.manifest import AuthProxySpec, CdpProviderSpec, Manifest, SearchProviderSpec
from ufo.ext.surface import SurfaceContext, SurfaceSpec, WritebackPoller
from ufo.grants import ConnectFlow, GrantStore, OAuthProvider, install_connect_flow
from ufo.hub import Hub, InProcessHub
from ufo.indexing import EmbedClient, IndexBackend
from ufo.jobs import (
    JobRunner,
    PageChangeRunner,
    SandboxReaper,
    SpendResume,
    bindings_from,
    core_jobs,
)
from ufo.loop.profiles import CORE_SUBAGENT_PROFILES
from ufo.loop.queue import Runtime, init_runtime
from ufo.loop.subagents import SubagentRegistry
from ufo.models.registry import model_registry
from ufo.o11y import init_o11y, log
from ufo.proxy_serve import OWNER_DSN_ENV, model_rule_base
from ufo.runtime_instance import BootGuard, Heartbeat
from ufo.sandbox.fs_creds import DEFAULT_S3_REGION, AwsStsClient, SandboxFsCredentialMinter
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.proxy.rules import Rule, derive_credential_rules
from ufo.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, Carrier, ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from ufo.search import SearchProvider
from ufo.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SourceBackend,
    SyncDriver,
    register_sources,
)
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.surfaces.artifacts import router as artifacts_router
from ufo.surfaces.cli import CONNECT_CALLBACK_PATH, router
from ufo.surfaces.hub_tail import HubTailer
from ufo.surfaces.scope import ScopedResponse
from ufo.workspace import init_workspace_credentials, ws

PROXY_STARTUP_TIMEOUT_SECONDS = 30


def run() -> None:
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
    init_db(config.database.url)
    manifests = load_manifests(config.pack.name)
    shared = config.serve.shared_workspace
    # The shared fleet has no single workspace to bootstrap-check, admit a seat for, or pin: it
    # connects as an RLS-subject role and resolves the workspace per request/turn. workspace_id
    # stays None, so every provider below builds ambient (context_for(None)) and scopes at use. It
    # also opens the RLS-bypassing owner engine owner_tx enumerates through — the per-tenant deploy
    # pins one workspace by its role default, so its owner_tx falls through the subject engine and
    # needs none.
    if shared:
        init_owner_db(_shared_owner_dsn(config))
    else:
        asyncio.run(_require_bootstrap())
    workspace_id = None if shared else asyncio.run(_sole_workspace_id())
    instance_id = uuid4()
    if workspace_id is not None:
        guard = BootGuard(config=config, workspace_id=workspace_id, instance_id=instance_id)
        asyncio.run(guard.admit())
    session_secret = _session_secret(config) if shared else ""
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    validate_ext_tools(manifests, credentials)
    _validate_requires(config, manifests, credentials)
    init_workspace_credentials(credentials)
    blob = blob_store_for(config.blob)
    artifact_secret = os.environ.get(config.artifacts.token_secret_env, "")
    hub = _select_hub(config, manifests)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    carrier = _select_carrier(config, manifests)
    registry = model_registry(config, manifests)
    # Deploy-global providers built once, with no workspace: each holds a credential reader that
    # resolves the ambient workspace's key (else the platform key) at use, and the index reads the
    # ambient workspace's vector namespace per query — so one boot-built set serves every workspace.
    embed = embed_backend(manifests, config.memory.embed_backend, credentials)
    index = index_backend(manifests, config.memory.index_backend, embed, credentials)
    init_runtime(
        Runtime(
            config=config,
            blob=blob,
            workspace_fs=_sandbox_fs_minter(config.blob),
            hub=hub,
            carrier=carrier,
            cdp_provider=_select_cdp_provider(config, manifests, credentials),
            search_provider=_select_search_provider(config, manifests, credentials),
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
            artifact_token_secret=artifact_secret,
        )
    )
    install_connect_flow(_connect_flow(credentials, config, manifests))
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": config.database.system_url,
            "run_admin_server": False,
        }
    )
    DBOS.launch()
    app = FastAPI(lifespan=_serve_lifespan)
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.instance_id = instance_id
    app.state.workspace_id = workspace_id
    app.state.shared_workspace = shared
    app.state.session_token_secret = session_secret
    app.state.durable_surfaces = durable_surfaces(manifests)
    app.state.writeback_poller = None
    app.state.blob = blob
    app.state.artifact_token_secret = artifact_secret
    app.include_router(router)
    app.include_router(artifacts_router)
    # Jobs run on both tiers: `fire` enumerates workspaces and binds each, so one launch drives the
    # whole fleet and a per-tenant deploy is the single-workspace case of the same path. Only the
    # per-tenant deploy registers its configured sources and mounts its one workspace's extension
    # routes and installed surfaces here; the shared fleet's per-workspace surfaces are admitted
    # elsewhere (the control-plane gateway).
    sync_driver = SyncDriver(
        backends=_source_backends(manifests),
        blob=blob,
        postgres=config.database.url.startswith("postgresql"),
        auth_proxy=_select_auth_proxy(config, manifests, credentials),
    )
    page_feed = CorePageFeed(blob=blob)
    if workspace_id is not None:
        asyncio.run(register_sources(config.sources))
    _launch_jobs(config, sync_driver, index, embed, page_feed, dbos_client, blob, carrier)
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


async def _require_bootstrap() -> None:
    try:
        async with workspace_tx() as connection:
            row = (await connection.execute(sa.select(tables.workspace.c.id))).first()
    except (sa.exc.OperationalError, sa.exc.ProgrammingError) as error:
        raise RuntimeError("schema missing — run `ufoctl init` first") from error
    if row is None:
        raise RuntimeError("workspace missing — run `ufoctl init` first")


def _session_secret(config: Config) -> str:
    """The shared fleet's session-signing secret, read once at boot. The surface verifies every
    member token against it before any RLS-scoped read, so an unset secret leaves the fleet unable
    to authenticate anyone — fail loud here, not on the first request."""
    secret = os.environ.get(config.serve.session_secret_env)
    if not secret:
        raise RuntimeError(
            f"shared serve needs {config.serve.session_secret_env} set to sign and verify member "
            "session tokens"
        )
    return secret


def _shared_owner_dsn(config: Config) -> str:
    """The RLS-bypassing owner DSN the shared fleet opens as `owner_tx`'s engine — the one
    cross-workspace read the fleet-wide job sweeps enumerate through before re-binding each row
    under `ws(...)`. One process serves every workspace, so it carries no tenant-pinned role
    default; `owner_tx` must bypass RLS through the table-owner role, else it falls back to the
    RLS-subject engine and the enumeration reads an unset `app.workspace_id` GUC and crashes. Read
    from `UFO_OWNER_DSN` (a k8s secretKeyRef injects it — a password-bearing DSN cannot ride a
    configmap), falling back to `[database] owner_url`; neither set fails loud. The secret's
    contract is a plain libpq URL; pin the asyncpg driver the fleet's subject engine also dials."""
    dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if not dsn:
        raise RuntimeError(
            f"{OWNER_DSN_ENV} or [database] owner_url must be set for the shared serve fleet — "
            "owner_tx bypasses RLS with the owner role to enumerate every workspace the job sweeps "
            "fan across; without it the enumeration reads an unset app.workspace_id GUC and crashes"
        )
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


def _launch_jobs(
    config: Config,
    sync_driver: SyncDriver,
    index: IndexBackend,
    embed: EmbedClient,
    page_feed: CorePageFeed,
    dbos_client: DBOSClient,
    blob: BlobStore,
    carrier: Carrier,
) -> None:
    """Register this workspace's jobs — core's own (the source sync driver, the spend-resume sweep
    that re-admits parked turns, and the sandbox reaper that reclaims idle containers) plus every
    installed extension's (the memory extension's memory-index and page-index jobs among them) — as
    DBOS schedules and one-shot enqueues, after
    launch so the system store is live. Registration is the synchronous DBOS API (off the loop, at
    startup); a handler may read a declared credential or the deploy index/embed backends or the
    page feed, so once any job is registered the credential key must be set."""
    manifests = load_manifests(config.pack.name)
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise RuntimeError(
            f"credential key env {config.credentials.key_env!r} is unset but jobs are registered"
        )
    admission = Admission(dbos=dbos_client, durable_surfaces=durable_surfaces(manifests))

    def invoker_for(workspace_id: UUID) -> AdmissionInvoker:
        return AdmissionInvoker(admission=admission, workspace_id=workspace_id)

    registry = model_registry(config, manifests)
    page_change_runner = PageChangeRunner(
        manifests=manifests,
        pages=page_feed,
        invoker_factory=invoker_for,
        index=index,
        embed=embed,
        blob=blob,
        registry=registry,
    )
    bindings = bindings_from(
        manifests,
        core_jobs(
            sync_driver,
            SpendResume(client=dbos_client),
            SandboxReaper(carrier=carrier, backend=config.sandbox.backend),
            page_change_runner,
        ),
    )
    JobRunner(
        bindings=bindings,
        invoker_factory=invoker_for,
        index=index,
        embed=embed,
        pages=page_feed,
        blob=blob,
        registry=registry,
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
    resolves to exactly one factory, built once here and held as `Runtime.carrier`. An off-cluster
    backend (its sandbox runs outside the serve pod's network) with no `[sandbox] proxy_public_url`
    fails loud too: its sandbox could reach neither the in-pod proxy nor a metered egress route, so
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
    if config.sandbox.backend in off_cluster and not config.sandbox.proxy_public_url:
        raise RuntimeError(
            f"sandbox backend {config.sandbox.backend!r} runs off-cluster and cannot reach the "
            "in-pod egress proxy; set [sandbox] proxy_public_url to the externally-reachable proxy "
            "URL so in-sandbox egress is credential-injected, default-denied, and metered"
        )
    return factory()


def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]:
    """The sync driver's backend map: core's folder backend plus every backend an extension
    registers through its Manifest `sources` point. Two extensions claiming one backend name fail
    loud at boot, so a source row's backend resolves to exactly one implementation."""
    backends: dict[str, SourceBackend] = {FOLDER_BACKEND: FolderSource()}
    for manifest in manifests:
        for provider in manifest.sources:
            if provider.backend in backends:
                raise RuntimeError(f"two extensions register source backend {provider.backend!r}")
            backends[provider.backend] = provider.source
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


_REQUIRED_SEAM_CHECKS: dict[
    str, Callable[[Config, tuple[Manifest, ...], CredentialStore | None], None]
] = {"cdp_providers": _require_cdp_provider, "search_providers": _require_search_provider}


def _select_auth_proxy(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
) -> AuthProxy | None:
    """The one auth-proxy backend feed-sync resolves connector credentials through, chosen by
    `[connectors] auth_backend`: a backend an extension registers through its Manifest
    `auth_proxies` point, built once at boot with a credential reader scoped to its slots (a direct
    BYOK backend reads its key in-process, host-side, never in the sandbox). No auth-proxy extension
    installed means no connector source can run — folder sources need none — so selection yields
    None rather than fail. Two extensions claiming one name fail loud, as does selecting a name no
    extension registers or building a selected backend with no credential key set."""
    specs: dict[str, tuple[AuthProxySpec, Manifest]] = {}
    for manifest in manifests:
        for spec in manifest.auth_proxies:
            if spec.backend in specs:
                raise RuntimeError(f"two extensions register auth proxy backend {spec.backend!r}")
            specs[spec.backend] = (spec, manifest)
    if not specs:
        return None
    found = specs.get(config.connectors.auth_backend)
    if found is None:
        raise NotRegisteredError(
            f"config selects auth proxy backend {config.connectors.auth_backend!r} "
            "but no extension registers it"
        )
    spec, manifest = found
    if credentials is None:
        raise RuntimeError(
            f"auth proxy backend {config.connectors.auth_backend!r} needs a credential key "
            "but none is set"
        )
    declared = frozenset(slot.name for slot in manifest.credentials)
    return spec.build(CredentialAccess(declared=declared))


def _mount_ext_routes(
    app: FastAPI,
    manifests: tuple[Manifest, ...],
    workspace_id: UUID,
    credentials: CredentialStore | None,
    index: IndexBackend,
    embed: EmbedClient,
) -> None:
    """Mount each extension's declared routes at `/ext/<name>/<path>`, every request bound to that
    extension's workspace-scoped ExtensionContext. An extension serving routes without a credential
    key set fails loud, since its context needs the credential store."""
    for manifest in manifests:
        if not manifest.routes:
            continue
        if credentials is None:
            raise RuntimeError(
                f"extension {manifest.name!r} serves routes but no credential key is set"
            )
        declared = frozenset(slot.name for slot in manifest.credentials)
        context = context_for(manifest.name, declared, index, embed)
        for spec in manifest.routes:

            async def endpoint(
                request: Request,
                handler=spec.handler,
                extension_context=context,
                wsid=workspace_id,
            ) -> Response:
                with ws(wsid):
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
    and tails the hub in its own route, so the poller never sees its turns — the tailer is injected
    the same way the admission invoker is."""
    invoker = AdmissionInvoker(
        workspace_id=workspace_id,
        admission=Admission(dbos=dbos_client, durable_surfaces=durable_surfaces(manifests)),
    )
    tailer = HubTailer(hub=hub)
    registered: dict[str, tuple[SurfaceSpec, SurfaceContext]] = {}
    for manifest in manifests:
        for spec in manifest.surfaces:
            if manifest.credentials and credentials is None:
                raise RuntimeError(f"surface {spec.name!r} needs a credential key but none is set")
            context = SurfaceContext(
                workspace_id=workspace_id,
                surface=spec.name,
                blob=blob,
                _invoker=invoker,
                _tailer=tailer,
                _credentials=credentials,
                _artifact_token_secret=artifact_secret,
                _public_base_url=public_base_url,
            )
            if spec.post is not None:
                registered[spec.name] = (spec, context)
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
        app.state.writeback_poller = WritebackPoller(
            workspace_id=workspace_id, worker_id=uuid4().hex, surfaces=registered
        )


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
    """Mount each shared-fleet-capable surface's routes on the shared fleet, resolving the workspace
    per request instead of pinning one at boot: `SurfaceSpec.identify` verifies the request's signed
    bearer and returns the workspace it claims, which the endpoint binds for the whole request (an
    unresolved token is a 401). It binds via `current_workspace.set` and releases that binding once
    the response has fully streamed (`ScopedResponse`) — not a `with ws(...)` block, which resets
    when the handler returns and would cut the scope out from under the live surface's
    StreamingResponse, whose hub tail reads the durable turn under RLS *after* the handler returns.
    Every shared request thus leaves the workspace unbound — the request→workspace boundary is
    enforced here, never left to per-task context isolation to clean up. The per-request
    SurfaceContext carries an `AdmissionInvoker` bound to that workspace so its admitted turn lands
    scoped to the token's workspace and no other.

    A surface that declares no `identify` cannot scope a shared request and is per-tenant-only, so
    it is skipped here (logged); a durable surface would need the fleet-wide, per-workspace
    writeback poller the shared fleet does not run (`_serve_lifespan`), so only live surfaces mount
    — a durable one is skipped the same way. The turn path is the live `ufo` surface; Slack and the
    setup portal stay per-tenant until the shared fleet grows fleet-wide writeback."""
    admission = Admission(dbos=dbos_client, durable_surfaces=durable_surfaces(manifests))
    tailer = HubTailer(hub=hub)
    for manifest in manifests:
        for spec in manifest.surfaces:
            resolver = spec.identify
            if resolver is None or spec.post is not None:
                log("serve.shared_surface.deferred", surface=spec.name)
                continue
            if manifest.credentials and credentials is None:
                raise RuntimeError(f"surface {spec.name!r} needs a credential key but none is set")
            for route in spec.routes:

                async def endpoint(
                    request: Request,
                    handler=route.handler,
                    identify=resolver,
                    surface=spec.name,
                ) -> Response:
                    workspace_id = identify(request)
                    if workspace_id is None:
                        return Response("unauthorized", status_code=401)
                    current_workspace.set(workspace_id)
                    context = SurfaceContext(
                        workspace_id=workspace_id,
                        surface=surface,
                        blob=blob,
                        _invoker=AdmissionInvoker(admission=admission, workspace_id=workspace_id),
                        _tailer=tailer,
                        _credentials=credentials,
                        _artifact_token_secret=artifact_secret,
                        _public_base_url=public_base_url,
                    )
                    return ScopedResponse(await handler(context, request))

                app.add_route(
                    f"/surface/{spec.name}/{route.path}".rstrip("/"),
                    endpoint,
                    methods=[route.method],
                )
            log("serve.shared_surface.mounted", surface=spec.name)


@asynccontextmanager
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run this instance's background loops for the life of the process: the heartbeat that keeps
    its runtime_instance row live — and retires it on graceful shutdown so peers see the seat free
    at once — and, when a durable surface is installed, the writeback poller, the durable half of
    surface delivery off the hub and off the turn loop. The shared fleet holds no single workspace
    to heartbeat a seat for or poll writebacks under, so it runs neither."""
    workspace_id: UUID | None = app.state.workspace_id
    if workspace_id is None:
        yield
        return
    heartbeat = Heartbeat(instance_id=app.state.instance_id, workspace_id=workspace_id)
    tasks = [asyncio.create_task(heartbeat.run())]
    poller = app.state.writeback_poller
    if poller is not None:
        tasks.append(asyncio.create_task(poller.run()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await heartbeat.retire()


def _proxy_endpoint(
    config: Config,
    manifests: tuple[Manifest, ...],
    credentials: CredentialStore | None,
    pricing: Pricing,
) -> ProxyEndpoint:
    """The egress proxy endpoint the carrier threads into every sandbox, in the shape this deploy
    takes. With `[sandbox] proxy_public_url` set (hosted, multi-node) the proxy runs as a standalone
    `ufoctl proxy` off this pod, so serve only carries the address and trust material: the stable
    platform CA from env (the sandbox's trust anchor for the proxy's minted leaves), the deploy's
    stable `proxy_port`, and that off-cluster dial-back base. An unset CA fails loud rather than
    shipping a sandbox that reaches no host. Unset (local, single-node) serve runs the proxy
    in-process, minting its own ephemeral CA — no shared trust material to source, no separate
    service to run alongside."""
    if config.sandbox.proxy_public_url is None:
        return _local_egress_proxy(config, manifests, credentials, pricing)
    ca_cert = os.environ.get(EGRESS_CA_CERT_ENV)
    if not ca_cert:
        raise RuntimeError(
            f"{EGRESS_CA_CERT_ENV} must hold the shared egress proxy's CA certificate (PEM) so the "
            "sandbox trusts the proxy's TLS; the proxy runs as `ufoctl proxy`, not in this pod"
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
    through `workspace_tx` — already scoped to this deploy's tenant DB by `init_db` — and authorizes
    each keyed-host CONNECT against the turn's live status, so a real key is injected only while the
    turn runs. It binds `proxy_port` and carries no `public_url`: the in-pod sandbox forms a
    host-local address from the port alone."""
    resolver = PerAgentRules(
        base=asyncio.run(_local_rule_base(config, manifests, credentials)),
        grants=GrantStore() if credentials is not None else None,
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


def _connect_flow(
    credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]
) -> ConnectFlow | None:
    """The process's connect flow — the `connect_account` tool authorizes through it and the OAuth
    callback completes through it — sharing the credential key that seals its state and encrypts its
    tokens. No key means grants cannot be recorded, so both fail loud. The provider registry is
    every installed connector's OAuth descriptor keyed by its provider name; the `redirect_uri` is
    this deploy's external callback URL, the one value both legs of the handoff present."""
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
