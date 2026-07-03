"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import os
import threading
from collections.abc import AsyncIterator, Mapping
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
from selfhost.config import Config, load_config
from selfhost.credentials import CredentialStore
from selfhost.db import init_db, workspace_tx
from selfhost.ext.context import context_for
from selfhost.ext.loader import load_manifests, turn_subagents, validate_ext_tools
from selfhost.ext.manifest import Manifest
from selfhost.ext.surface import SurfaceContext, SurfaceSpec, WritebackPoller
from selfhost.grants import ConnectFlow, GrantStore, OAuthProvider, install_connect_flow
from selfhost.hub import Hub, InProcessHub
from selfhost.jobs import JobRunner, SpendResume, bindings_from, core_jobs
from selfhost.loop.profiles import CORE_SUBAGENT_PROFILES
from selfhost.loop.queue import Runtime, init_runtime
from selfhost.loop.subagents import SubagentRegistry
from selfhost.memory.chunk import TextChunker
from selfhost.memory.embed import OpenAIEmbedClient
from selfhost.memory.index import index_backend_for
from selfhost.memory.indexer import MemoryIndexer, PageIndexer
from selfhost.memory.service import MemoryService
from selfhost.memory.sources import FOLDER_BACKEND, FolderSource, SyncDriver, register_sources
from selfhost.models.openai import openai_sdk_client
from selfhost.o11y import init_o11y, log
from selfhost.runtime_instance import BootGuard, Heartbeat
from selfhost.sandbox.carrier import DockerCarrier
from selfhost.sandbox.proxy.rules import (
    Rule,
    ScopeRule,
    derive_credential_rules,
    derive_model_rules,
)
from selfhost.sandbox.proxy.server import EgressProxy, PerAgentRules, generate_ca
from selfhost.sandbox.session import ProxyEndpoint
from selfhost.schema import tables
from selfhost.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from selfhost.surfaces.admission import Admission, AdmissionInvoker
from selfhost.surfaces.cli import CONNECT_CALLBACK_PATH, router
from selfhost.surfaces.web import WebSurface
from selfhost.surfaces.web import router as web_router

PROXY_STARTUP_TIMEOUT_SECONDS = 30


def run() -> None:
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
    init_db(config.database.url)
    asyncio.run(_require_bootstrap())
    manifests = load_manifests()
    workspace_id = asyncio.run(_sole_workspace_id())
    instance_id = uuid4()
    guard = BootGuard(config=config, workspace_id=workspace_id, instance_id=instance_id)
    asyncio.run(guard.admit())
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    validate_ext_tools(manifests, workspace_id, credentials)
    embed = OpenAIEmbedClient(
        client=openai_sdk_client(os.environ.get(config.models.openai_api_key_env, ""))
    )
    index = index_backend_for(config.database.url, embed)
    chunker = TextChunker()
    blob = blob_store_for(config.blob)
    artifact_secret = os.environ.get(config.artifacts.token_secret_env, "")
    memory = MemoryService(index=index, embed=embed)
    postgres = config.database.url.startswith("postgresql")
    indexer = MemoryIndexer(index=index, embed=embed, chunker=chunker, postgres=postgres)
    page_indexer = PageIndexer(
        index=index, embed=embed, chunker=chunker, blob=blob, postgres=postgres
    )
    sync_driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=blob,
        postgres=postgres,
    )
    asyncio.run(register_sources(config.sources))
    hub = InProcessHub()
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    init_runtime(
        Runtime(
            config=config,
            blob=blob,
            hub=hub,
            carrier=DockerCarrier(),
            proxy=_egress_proxy(asyncio.run(_resolver(config, credentials))),
            dbos=dbos_client,
            subagents=SubagentRegistry((*CORE_SUBAGENT_PROFILES, *turn_subagents(manifests))),
            manifests=manifests,
            credentials=credentials,
            memory=memory,
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
    _launch_jobs(config, indexer, page_indexer, sync_driver, memory, dbos_client, blob)
    app = FastAPI(lifespan=_serve_lifespan)
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.instance_id = instance_id
    app.state.workspace_id = workspace_id
    app.state.writeback_poller = None
    app.include_router(router)
    _mount_ext_routes(app, manifests, workspace_id, credentials, memory)
    _mount_surfaces(app, manifests, workspace_id, credentials, blob, dbos_client)
    _mount_web_surface(app, config, blob, hub, dbos_client, artifact_secret)
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
    indexer: MemoryIndexer,
    page_indexer: PageIndexer,
    sync_driver: SyncDriver,
    memory: MemoryService,
    dbos_client: DBOSClient,
    blob: BlobStore,
) -> None:
    """Register this workspace's jobs — core's own (the memory + page index derivations, the source
    sync driver, and the spend-resume sweep that re-admits parked turns) plus every installed
    extension's — as DBOS schedules and one-shot enqueues, after launch so the system store is live.
    Registration is the synchronous DBOS API (off the loop, at startup); a handler may read a
    declared credential, so once any job is registered the credential key must be set."""
    bindings = bindings_from(
        load_manifests(),
        core_jobs(indexer, page_indexer, sync_driver, SpendResume(client=dbos_client)),
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
        memory=memory,
        blob=blob,
        invoker=AdmissionInvoker(admission=Admission(dbos=dbos_client), workspace_id=workspace_id),
    ).launch()


async def _sole_workspace_id() -> UUID:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()


def _mount_ext_routes(
    app: FastAPI,
    manifests: tuple[Manifest, ...],
    workspace_id: UUID,
    credentials: CredentialStore | None,
    memory: MemoryService,
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
        context = context_for(workspace_id, manifest.name, declared, credentials, memory)
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
    dbos_client: DBOSClient,
) -> None:
    """Mount every installed surface's ingest at `/surface/<name>`, each request bound to that
    surface's privileged SurfaceContext, and run one writeback poller over them all. A surface reads
    its own credential slots (a bot token, a signing secret) in-process, so an installed surface
    without a credential key set fails loud at boot rather than on the first event."""
    invoker = AdmissionInvoker(workspace_id=workspace_id, admission=Admission(dbos=dbos_client))
    registered: dict[str, tuple[SurfaceSpec, SurfaceContext]] = {}
    for manifest in manifests:
        for spec in manifest.surfaces:
            if credentials is None:
                raise RuntimeError(
                    f"surface {spec.name!r} needs a credential key but none is set"
                )
            context = SurfaceContext(
                workspace_id=workspace_id,
                surface=spec.name,
                blob=blob,
                _invoker=invoker,
                _credentials=credentials,
            )
            registered[spec.name] = (spec, context)

            async def endpoint(
                request: Request, handler=spec.ingest, surface_context=context
            ) -> Response:
                return await handler(surface_context, request)

            app.add_route(f"/surface/{spec.name}", endpoint, methods=["POST"])
    if registered:
        app.state.writeback_poller = WritebackPoller(
            workspace_id=workspace_id, worker_id=uuid4().hex, surfaces=registered
        )


def _mount_web_surface(
    app: FastAPI,
    config: Config,
    blob: BlobStore,
    hub: Hub,
    dbos_client: DBOSClient,
    artifact_secret: str,
) -> None:
    """Mount the web chat surface when the deploy enables it. The surface verifies artifact tokens
    with the deploy's artifact secret — the same one `share_file` mints with — so an enabled web
    surface without that env set fails loud at boot rather than on the first download."""
    web = config.surfaces.web
    if web is None or not web.enable:
        return
    if not artifact_secret:
        raise RuntimeError(
            f"web surface is enabled but {config.artifacts.token_secret_env!r} is unset"
        )
    app.state.web = WebSurface(
        admission=Admission(dbos=dbos_client),
        hub=hub,
        blob=blob,
        artifact_token_secret=artifact_secret,
    )
    app.include_router(web_router)


@asynccontextmanager
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run this instance's background loops for the life of the process: the heartbeat that keeps
    its runtime_instance row live — and retires it on graceful shutdown so peers see the seat free
    at once — and, when a surface is installed, the writeback poller, the durable half of surface
    delivery off the hub and off the turn loop."""
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
    each request's rules through `resolver`, which reads the turn's agent and grants per turn."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def _boot() -> ProxyEndpoint:
        cert, key = await generate_ca()
        return await EgressProxy(resolve=resolver.resolve, ca_cert=cert, ca_key=key).start()

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
    manifests = load_manifests()
    if not any(slot.injection for manifest in manifests for slot in manifest.credentials):
        return ()
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise RuntimeError(
            f"credential key env {config.credentials.key_env!r} is unset but a slot needs injection"
        )
    store = CredentialStore(fernet=Fernet(key.encode()))
    async with workspace_tx() as connection:
        workspace_id = (
            await connection.execute(sa.select(tables.workspace.c.id))
        ).scalar_one()
    return await derive_credential_rules(manifests, workspace_id, store)
