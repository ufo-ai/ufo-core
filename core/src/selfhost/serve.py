"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
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
from selfhost.ext.loader import load_manifests, validate_ext_tools
from selfhost.ext.manifest import Manifest
from selfhost.grants import ConnectFlow, GrantStore, install_connect_flow
from selfhost.hub import Hub, InProcessHub
from selfhost.jobs import JobRunner, SpendResume, bindings_from, core_jobs
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
from selfhost.surfaces.admission import Admission
from selfhost.surfaces.cli import CONNECT_CALLBACK_PATH, router
from selfhost.surfaces.slack import SlackSurface, WritebackPoller
from selfhost.surfaces.slack import router as slack_router
from selfhost.surfaces.web import WebSurface
from selfhost.surfaces.web import router as web_router

PROXY_STARTUP_TIMEOUT_SECONDS = 30
SLACK_HTTP_TIMEOUT_SECONDS = 20


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
    indexer = MemoryIndexer(index=index, embed=embed, chunker=chunker)
    page_indexer = PageIndexer(index=index, embed=embed, chunker=chunker, blob=blob)
    sync_driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=blob,
        postgres=config.database.url.startswith("postgresql"),
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
            subagents=SubagentRegistry(()),
            manifests=manifests,
            credentials=credentials,
            memory=memory,
            artifact_token_secret=artifact_secret,
        )
    )
    install_connect_flow(_connect_flow(credentials, config))
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": config.database.system_url,
            "run_admin_server": False,
        }
    )
    DBOS.launch()
    _launch_jobs(config, indexer, page_indexer, sync_driver, memory, dbos_client)
    app = FastAPI(lifespan=_serve_lifespan)
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.instance_id = instance_id
    app.state.workspace_id = workspace_id
    app.state.writeback_poller = None
    app.include_router(router)
    _mount_ext_routes(app, manifests, workspace_id, credentials, memory)
    _mount_slack_surface(app, config, workspace_id, credentials, dbos_client)
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
    JobRunner(
        workspace_id=asyncio.run(_sole_workspace_id()),
        credential_store=CredentialStore(fernet=Fernet(key.encode())),
        bindings=bindings,
        memory=memory,
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


def _mount_slack_surface(
    app: FastAPI,
    config: Config,
    workspace_id: UUID,
    credentials: CredentialStore | None,
    dbos_client: DBOSClient,
) -> None:
    """Mount the Slack ingress when the deploy enables it. The surface reads its signing secret and
    bot token from the credential store, so an enabled Slack surface without a credential key set
    fails loud at boot rather than on the first event."""
    slack = config.surfaces.slack
    if slack is None or not slack.enable:
        return
    if credentials is None:
        raise RuntimeError("Slack surface is enabled but no credential key is set")
    http = httpx.AsyncClient(timeout=SLACK_HTTP_TIMEOUT_SECONDS)
    app.state.slack = SlackSurface(
        admission=Admission(dbos=dbos_client),
        credentials=credentials,
        http=http,
        config=slack,
        workspace_id=workspace_id,
    )
    app.state.writeback_poller = WritebackPoller(
        credentials=credentials, http=http, workspace_id=workspace_id, worker_id=uuid4().hex
    )
    app.include_router(slack_router)


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
    at once — and, when Slack is enabled, the writeback poller, the durable half of Slack delivery
    off the hub and off the turn loop."""
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


def _connect_flow(credentials: CredentialStore | None, config: Config) -> ConnectFlow | None:
    """The process's connect flow — the `connect_account` tool authorizes through it and the OAuth
    callback completes through it — sharing the credential key that seals its state and encrypts its
    tokens. No key means grants cannot be recorded, so both fail loud. The provider map is empty
    until a connectors extension installs one; the `redirect_uri` is this deploy's callback URL, the
    one value both legs of the handoff present."""
    if credentials is None:
        return None
    store = GrantStore(fernet=credentials.fernet)
    redirect_uri = f"http://{config.serve.host}:{config.serve.port}{CONNECT_CALLBACK_PATH}"
    return ConnectFlow(
        providers={}, fernet=credentials.fernet, store=store, redirect_uri=redirect_uri
    )


async def _resolver(config: Config, credentials: CredentialStore | None) -> PerAgentRules:
    """The per-turn rule resolver the proxy consumes: a static workspace base (the model providers
    the deploy holds keys for and every stored credential slot, fixed for the serve's life) plus the
    grant store it derives each turn's agent's grants from. No credential key means no token can be
    decrypted, so no grant store — the base alone. Grants layer on per turn, not assembled here."""
    grants = GrantStore(fernet=credentials.fernet) if credentials is not None else None
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
