"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio
import os
import threading

import sqlalchemy as sa
import uvicorn
from dbos import DBOS, DBOSClient
from fastapi import FastAPI

from selfhost.blob import blob_store_for
from selfhost.config import Config, load_config
from selfhost.db import init_db, workspace_tx
from selfhost.hub import InProcessHub
from selfhost.loop.queue import Runtime, init_runtime
from selfhost.o11y import init_o11y, log
from selfhost.sandbox.carrier import DockerCarrier
from selfhost.sandbox.proxy.rules import Rule, ScopeRule, derive_model_rules
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
    hub = InProcessHub()
    init_runtime(
        Runtime(
            config=config,
            blob=blob_store_for(config.blob),
            hub=hub,
            carrier=DockerCarrier(),
            proxy=_egress_proxy(config),
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
    app = FastAPI()
    app.state.hub = hub
    app.state.dbos = DBOSClient(system_database_url=config.database.system_url)
    app.include_router(router)
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


def _egress_proxy(config: Config) -> ProxyEndpoint:
    """The sandbox's sole route out runs on its own event loop: a standalone network service, not
    part of the turn loop, that outlives every turn for the life of the process."""
    rules = _proxy_rules(config)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def _boot() -> ProxyEndpoint:
        cert, key = await generate_ca()
        return await EgressProxy(rules=rules, ca_cert=cert, ca_key=key).start()

    return asyncio.run_coroutine_threadsafe(_boot(), loop).result(PROXY_STARTUP_TIMEOUT_SECONDS)


def _proxy_rules(config: Config) -> tuple[Rule, ...]:
    """The egress allowlist + credential injection, derived from the provider keys the deploy holds:
    each configured provider host is reachable and its sentinel swaps to the real key on the wire;
    every other host is refused at CONNECT."""
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
