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

from selfhost.blob import BlobStore, blob_store_for
from selfhost.browser.backend import BrowserBackend, BuaBackend
from selfhost.browser.cdp_provider import BrowserCdpProviderChain, env_browser_cdp_provider
from selfhost.config import BUA_BROWSER_BACKEND, IN_PROCESS_BACKEND, Config, load_config
from selfhost.credentials import CredentialStore
from selfhost.db import init_db, workspace_tx
from selfhost.ext.context import context_for
from selfhost.ext.loader import (
    NotRegisteredError,
    embed_backend,
    index_backend,
    load_manifests,
    skill_registry,
    turn_subagents,
    validate_ext_tools,
)
from selfhost.ext.manifest import Manifest
from selfhost.ext.surface import SurfaceContext, SurfaceSpec, WritebackPoller
from selfhost.grants import ConnectFlow, GrantStore, OAuthProvider, install_connect_flow
from selfhost.hub import Hub, InProcessHub
from selfhost.indexing import EmbedClient, IndexBackend
from selfhost.jobs import JobRunner, SpendResume, bindings_from, core_jobs
from selfhost.loop.profiles import CORE_SUBAGENT_PROFILES
from selfhost.loop.queue import Runtime, init_runtime
from selfhost.loop.subagents import SubagentRegistry
from selfhost.memory.sources import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SourceBackend,
    SyncDriver,
    register_sources,
)
from selfhost.models.registry import model_registry
from selfhost.o11y import init_o11y, log
from selfhost.runtime_instance import BootGuard, Heartbeat
from selfhost.sandbox.local import LocalCarrier
from selfhost.sandbox.proxy.rules import (
    Rule,
    ScopeRule,
    derive_credential_rules,
    derive_model_rules,
)
from selfhost.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from selfhost.sandbox.session import Carrier, ProxyEndpoint
from selfhost.schema import tables
from selfhost.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from selfhost.surfaces.admission import Admission, AdmissionInvoker
from selfhost.surfaces.artifacts import router as artifacts_router
from selfhost.surfaces.cli import CONNECT_CALLBACK_PATH, router
from selfhost.surfaces.hub_tail import HubTailer

PROXY_STARTUP_TIMEOUT_SECONDS = 30


def run() -> None:
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
    init_db(config.database.url)
    asyncio.run(_require_bootstrap())
    manifests = load_manifests(config.pack.name)
    workspace_id = asyncio.run(_sole_workspace_id())
    instance_id = uuid4()
    guard = BootGuard(config=config, workspace_id=workspace_id, instance_id=instance_id)
    asyncio.run(guard.admit())
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    validate_ext_tools(manifests, workspace_id, credentials)
    embed = embed_backend(manifests, config.memory.embed_backend, workspace_id, credentials)
    index = index_backend(manifests, config.memory.index_backend, embed, workspace_id, credentials)
    blob = blob_store_for(config.blob)
    artifact_secret = os.environ.get(config.artifacts.token_secret_env, "")
    postgres = config.database.url.startswith("postgresql")
    page_feed = CorePageFeed(blob=blob)
    sync_driver = SyncDriver(
        backends=_source_backends(manifests),
        blob=blob,
        postgres=postgres,
    )
    asyncio.run(register_sources(config.sources))
    hub = _select_hub(config, manifests)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    init_runtime(
        Runtime(
            config=config,
            blob=blob,
            hub=hub,
            carrier=_select_carrier(config, manifests),
            browser=_select_browser(config, manifests, workspace_id, credentials),
            proxy=_egress_proxy(asyncio.run(_resolver(config, credentials))),
            dbos=dbos_client,
            subagents=SubagentRegistry((*CORE_SUBAGENT_PROFILES, *turn_subagents(manifests))),
            manifests=manifests,
            registry=model_registry(config, manifests),
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
    _launch_jobs(config, sync_driver, index, embed, page_feed, dbos_client, blob)
    app = FastAPI(lifespan=_serve_lifespan)
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.instance_id = instance_id
    app.state.workspace_id = workspace_id
    app.state.writeback_poller = None
    app.state.blob = blob
    app.state.artifact_token_secret = artifact_secret
    app.include_router(router)
    app.include_router(artifacts_router)
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
    log("serve.started", host=config.serve.host, port=config.serve.port)
    try:
        uvicorn.run(app, host=config.serve.host, port=config.serve.port, log_level="warning")
    finally:
        DBOS.destroy()


async def _require_bootstrap() -> None:
    try:
        async with workspace_tx() as connection:
            row = (await connection.execute(sa.select(tables.workspace.c.id))).first()
    except (sa.exc.OperationalError, sa.exc.ProgrammingError) as error:
        raise RuntimeError("schema missing — run `selfhost init` first") from error
    if row is None:
        raise RuntimeError("workspace missing — run `selfhost init` first")


def _launch_jobs(
    config: Config,
    sync_driver: SyncDriver,
    index: IndexBackend,
    embed: EmbedClient,
    page_feed: CorePageFeed,
    dbos_client: DBOSClient,
    blob: BlobStore,
) -> None:
    """Register this workspace's jobs — core's own (the source sync driver and the spend-resume
    sweep that re-admits parked turns) plus every installed extension's (the memory extension's
    memory-index and page-index jobs among them) — as DBOS schedules and one-shot enqueues, after
    launch so the system store is live. Registration is the synchronous DBOS API (off the loop, at
    startup); a handler may read a declared credential or the deploy index/embed backends or the
    page feed, so once any job is registered the credential key must be set."""
    bindings = bindings_from(
        load_manifests(config.pack.name),
        core_jobs(sync_driver, SpendResume(client=dbos_client)),
    )
    if not bindings:
        return
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise RuntimeError(
            f"credential key env {config.credentials.key_env!r} is unset but jobs are registered"
        )
    workspace_id = asyncio.run(_sole_workspace_id())
    JobRunner(
        workspace_id=workspace_id,
        credential_store=CredentialStore(fernet=Fernet(key.encode())),
        bindings=bindings,
        index=index,
        embed=embed,
        pages=page_feed,
        blob=blob,
        invoker=AdmissionInvoker(admission=Admission(dbos=dbos_client), workspace_id=workspace_id),
    ).launch()


async def _sole_workspace_id() -> UUID:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()


def _select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> Carrier:
    """The one sandbox backend this process runs, chosen by `[sandbox] backend`: core's default
    `local` carrier plus every carrier an extension contributes via its `carriers` Manifest point
    (`docker`, `e2b`, a remote runner). An extension name that collides with the built-in or another
    extension fails loud, and a backend name no carrier registers fails loud — so the selected name
    resolves to exactly one factory, built once here and held as `Runtime.carrier`."""
    factories: dict[str, Callable[[], Carrier]] = {"local": LocalCarrier}
    for manifest in manifests:
        for spec in manifest.carriers:
            if spec.name in factories:
                raise RuntimeError(f"two carriers register backend {spec.name!r}")
            factories[spec.name] = spec.factory
    factory = factories.get(config.sandbox.backend)
    if factory is None:
        raise NotRegisteredError(
            f"sandbox backend {config.sandbox.backend!r} is not a registered carrier "
            f"(have {sorted(factories)})"
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


def _select_browser(
    config: Config,
    manifests: tuple[Manifest, ...],
    workspace_id: UUID,
    credentials: CredentialStore | None,
) -> BrowserBackend:
    """The process-wide browser backend the deploy selects: core's default `bua` engine driving
    Chrome over the CDP endpoint the `BROWSER_CDP_URL` provider yields, or a backend an extension
    registers through its Manifest `browsers` point, built once at boot with a credential reader
    scoped to that extension's slots. Two extensions claiming one name fail loud, as does selecting
    a name no extension registers or an extension shadowing the core `bua` default; a named backend
    with no credential key set fails loud, since its factory may read a BYOK slot host-side."""
    if config.browser.backend == BUA_BROWSER_BACKEND:
        return BuaBackend(
            provider=BrowserCdpProviderChain(hosted=env_browser_cdp_provider(), local=None)
        )
    specs = {}
    for manifest in manifests:
        for spec in manifest.browsers:
            if spec.backend == BUA_BROWSER_BACKEND:
                raise RuntimeError(
                    f"extension may not register the core browser backend {spec.backend!r}"
                )
            if spec.backend in specs:
                raise RuntimeError(f"two extensions register browser backend {spec.backend!r}")
            specs[spec.backend] = (spec, manifest)
    found = specs.get(config.browser.backend)
    if found is None:
        raise NotRegisteredError(
            f"config selects browser backend {config.browser.backend!r} "
            "but no extension registers it"
        )
    spec, manifest = found
    if credentials is None:
        raise RuntimeError(
            f"browser backend {config.browser.backend!r} needs a credential key but none is set"
        )
    declared = frozenset(slot.name for slot in manifest.credentials)
    context = context_for(workspace_id, manifest.name, declared, credentials)
    return spec.build(context.credentials)


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
        context = context_for(workspace_id, manifest.name, declared, credentials, index, embed)
        for spec in manifest.routes:

            async def endpoint(
                request: Request, handler=spec.handler, extension_context=context
            ) -> Response:
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
    invoker = AdmissionInvoker(workspace_id=workspace_id, admission=Admission(dbos=dbos_client))
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
                    request: Request, handler=route.handler, surface_context=context
                ) -> Response:
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


@asynccontextmanager
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run this instance's background loops for the life of the process: the heartbeat that keeps
    its runtime_instance row live — and retires it on graceful shutdown so peers see the seat free
    at once — and, when a durable surface is installed, the writeback poller, the durable half of
    surface delivery off the hub and off the turn loop."""
    heartbeat = Heartbeat(instance_id=app.state.instance_id, workspace_id=app.state.workspace_id)
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


def _egress_proxy(resolver: PerAgentRules) -> ProxyEndpoint:
    """The sandbox's sole route out runs on its own event loop: a standalone network service, not
    part of the turn loop, that outlives every turn for the life of the process. The proxy resolves
    each request's rules through `resolver`, which reads the turn's agent and grants per turn, and
    authorizes each keyed-host CONNECT through `resolver.turn_live`, which reads the turn's status
    fresh so a real key is injected only while the turn is running."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def _boot() -> ProxyEndpoint:
        cert, key = await generate_ca()
        return await EgressProxy(
            resolve=resolver.resolve, authorize=resolver.turn_live, ca_cert=cert, ca_key=key
        ).start()

    return asyncio.run_coroutine_threadsafe(_boot(), loop).result(PROXY_STARTUP_TIMEOUT_SECONDS)


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


async def _resolver(config: Config, credentials: CredentialStore | None) -> PerAgentRules:
    """The per-turn rule resolver the proxy consumes: a static workspace base (the model providers
    the deploy holds keys for and every stored credential slot, fixed for the serve's life) plus the
    grant store it derives each turn's agent's granted hosts from. No credential key means no
    connect flow to record grants, so no grant store — the base alone. Grants layer on per turn."""
    grants = GrantStore() if credentials is not None else None
    base = (*_model_rules(config), *await _credential_rules(config))
    return PerAgentRules(base=base, grants=grants)


def _model_rules(config: Config) -> tuple[Rule, ...]:
    """The model-provider egress: each configured provider host is reachable and its sentinel swaps
    to the real key on the wire; every other host is refused at CONNECT."""
    providers = (
        (config.models.anthropic_api_key_env, "claude-opus-4-8"),
        (config.models.openai_api_key_env, "gpt-5"),
    )
    hosts: set[str] = set()
    rules: list[Rule] = []
    for env_name, probe in providers:
        key = os.environ.get(env_name)
        if not key:
            continue
        for rule in derive_model_rules(probe, key):
            if isinstance(rule, ScopeRule):
                hosts |= rule.allowed_hosts
            else:
                rules.append(rule)
    if not hosts:
        raise RuntimeError("no model provider key set; the sandbox would have no egress route")
    return (ScopeRule(allowed_hosts=frozenset(hosts)), *rules)


async def _credential_rules(config: Config) -> tuple[Rule, ...]:
    """Every installed extension's injected credential slots become egress rules for this single
    workspace; fail loud if a slot needs injection but the deploy set no credential key."""
    manifests = load_manifests(config.pack.name)
    if not any(slot.injection for manifest in manifests for slot in manifest.credentials):
        return ()
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise RuntimeError(
            f"credential key env {config.credentials.key_env!r} is unset but a slot needs injection"
        )
    store = CredentialStore(fernet=Fernet(key.encode()))
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
    return await derive_credential_rules(manifests, workspace_id, store)
