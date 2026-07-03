"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import os
import threading
from uuid import UUID

import sqlalchemy as sa
import uvicorn
from cryptography.fernet import Fernet
from dbos import DBOS, DBOSClient
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from selfhost.blob import blob_store_for
from selfhost.config import Config, load_config
from selfhost.credentials import CredentialStore
from selfhost.db import init_db, workspace_tx
from selfhost.ext.context import context_for
from selfhost.ext.loader import load_manifests, validate_ext_tools
from selfhost.ext.manifest import Manifest
from selfhost.hub import InProcessHub
from selfhost.jobs import JobRunner, bindings_from, core_jobs
from selfhost.loop.queue import Runtime, init_runtime
from selfhost.loop.subagents import SubagentRegistry
from selfhost.memory.chunk import TextChunker
from selfhost.memory.embed import OpenAIEmbedClient
from selfhost.memory.index import index_backend_for
from selfhost.memory.indexer import MemoryIndexer
from selfhost.memory.service import MemoryService
from selfhost.models.openai import openai_sdk_client
from selfhost.o11y import init_o11y, log
from selfhost.sandbox.carrier import DockerCarrier
from selfhost.sandbox.proxy.rules import (
    Rule,
    ScopeRule,
    derive_credential_rules,
    derive_model_rules,
)
from selfhost.sandbox.proxy.server import EgressProxy, generate_ca
from selfhost.sandbox.session import ProxyEndpoint
from selfhost.schema import tables
from selfhost.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from selfhost.surfaces.cli import router

PROXY_STARTUP_TIMEOUT_SECONDS = 30


def run() -> None:
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
    init_db(config.database.url)
    asyncio.run(_require_bootstrap())
    manifests = load_manifests()
    workspace_id = asyncio.run(_sole_workspace_id())
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    validate_ext_tools(manifests, workspace_id, credentials)
    embed = OpenAIEmbedClient(
        client=openai_sdk_client(os.environ.get(config.models.openai_api_key_env, ""))
    )
    index = index_backend_for(config.database.url, embed)
    memory = MemoryService(index=index, embed=embed)
    indexer = MemoryIndexer(index=index, embed=embed, chunker=TextChunker())
    hub = InProcessHub()
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    init_runtime(
        Runtime(
            config=config,
            blob=blob_store_for(config.blob),
            hub=hub,
            carrier=DockerCarrier(),
            proxy=_egress_proxy(asyncio.run(_assemble_rules(config))),
            dbos=dbos_client,
            subagents=SubagentRegistry(()),
            manifests=manifests,
            credentials=credentials,
            memory=memory,
        )
    )
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": config.database.system_url,
            "run_admin_server": False,
        }
    )
    DBOS.launch()
    _launch_jobs(config, indexer, memory)
    app = FastAPI()
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.include_router(router)
    _mount_ext_routes(app, manifests, workspace_id, credentials, memory)
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


def _launch_jobs(config: Config, indexer: MemoryIndexer, memory: MemoryService) -> None:
    """Register this workspace's jobs — core's own (the memory index derivation) plus every
    installed extension's — as DBOS schedules and one-shot enqueues, after launch so the system
    store is live. Registration is the synchronous DBOS API (off the loop, at startup); a handler
    may read a declared credential, so once any job is registered the credential key must be set."""
    bindings = bindings_from(load_manifests(), core_jobs(indexer))
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


def _egress_proxy(rules: tuple[Rule, ...]) -> ProxyEndpoint:
    """The sandbox's sole route out runs on its own event loop: a standalone network service, not
    part of the turn loop, that outlives every turn for the life of the process."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def _boot() -> ProxyEndpoint:
        cert, key = await generate_ca()
        return await EgressProxy(rules=rules, ca_cert=cert, ca_key=key).start()

    return asyncio.run_coroutine_threadsafe(_boot(), loop).result(PROXY_STARTUP_TIMEOUT_SECONDS)


async def _assemble_rules(config: Config) -> tuple[Rule, ...]:
    """The proxy's full rule set: the model providers the deploy holds keys for, plus every
    extension credential slot whose secret is stored — derived, never registered."""
    model = _model_rules(config)
    credential = await _credential_rules(config)
    return (*model, *credential)


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
