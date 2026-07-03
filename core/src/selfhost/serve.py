"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import asyncio

import sqlalchemy as sa
import uvicorn
from dbos import DBOS, DBOSClient
from fastapi import FastAPI

from selfhost.blob import blob_store_for
from selfhost.config import load_config
from selfhost.db import init_db, workspace_tx
from selfhost.hub import InProcessHub
from selfhost.loop.queue import Runtime, init_runtime
from selfhost.o11y import init_o11y, log
from selfhost.schema import tables
from selfhost.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from selfhost.surfaces.cli import router


def run() -> None:
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
    init_db(config.database.url)
    asyncio.run(_require_bootstrap())
    hub = InProcessHub()
    init_runtime(Runtime(config=config, blob=blob_store_for(config.blob), hub=hub))
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
