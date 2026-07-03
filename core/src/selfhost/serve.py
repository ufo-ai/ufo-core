"""Composition root: one process — surfaces, DBOS workers, shared channels."""

import psycopg
import uvicorn
from dbos import DBOS, DBOSClient
from fastapi import FastAPI

from selfhost.blob import blob_store_for
from selfhost.config import Config, load_config
from selfhost.db import init_db
from selfhost.hub import InProcessHub
from selfhost.loop.queue import Runtime, init_runtime
from selfhost.o11y import init_o11y, log
from selfhost.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from selfhost.surfaces.cli import router


def run() -> None:
    config = load_config()
    init_o11y(config.o11y.otlp_endpoint)
    _require_bootstrap(config)
    init_db(config.postgres.url)
    hub = InProcessHub()
    init_runtime(Runtime(config=config, blob=blob_store_for(config.blob), hub=hub))
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "application_database_url": config.postgres.url,
            "system_database_url": config.postgres.system_url,
            "run_admin_server": False,
        }
    )
    DBOS.launch()
    app = FastAPI()
    app.state.hub = hub
    app.state.dbos = DBOSClient(system_database_url=config.postgres.system_url)
    app.include_router(router)
    log("serve.started", host=config.serve.host, port=config.serve.port)
    try:
        uvicorn.run(app, host=config.serve.host, port=config.serve.port, log_level="warning")
    finally:
        DBOS.destroy()


def _require_bootstrap(config: Config) -> None:
    plain = config.postgres.url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(plain) as connection:
            row = connection.execute("select id from workspace").fetchone()
    except psycopg.errors.UndefinedTable as error:
        raise RuntimeError("schema missing — run `selfhost init` first") from error
    if row is None:
        raise RuntimeError("workspace missing — run `selfhost init` first")
