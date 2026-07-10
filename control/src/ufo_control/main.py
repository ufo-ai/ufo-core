"""The ``ufo-control`` entry point — one image, one role per Deployment, selected by argument.

``ufo-control api`` runs the deploy endpoint; ``ufo-control operator`` runs the reconcile loop;
``ufo-control gateway`` runs the apex onboarding server. This is metalcraft's one-image/console-
script-args deployment shape (``[metalcraft-operator]``), stripped to the processes this control
plane needs. All serve ``/healthz`` for probes.
"""

import asyncio
import logging
import os

import asyncpg
import click
import uvicorn
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from ufo_control.gateway_invite import InviteCodes
from ufo_control.postgres import ensure_serve_role
from ufo_control.rls import bootstrap_policies, owner_dsn

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
GATEWAY_PORT_ENV = "UFO_GATEWAY_PORT"
OTLP_ENDPOINT_ENV = "UFO_CONTROL_OTLP_ENDPOINT"
OTLP_LOGS_PATH = "v1/logs"
SERVICE_NAME = "ufo-control"


@click.group()
def main() -> None:
    """The Kubernetes control plane that runs ufo as its per-workspace backend."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _export_logs(os.environ.get(OTLP_ENDPOINT_ENV))


def _export_logs(otlp_endpoint: str | None) -> None:
    """Ship every record the process logs to the platform OTLP collector; None keeps stdout only."""
    if otlp_endpoint is None:
        return
    logger_provider = LoggerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/{OTLP_LOGS_PATH}")
        )
    )
    _install_root_handler(logger_provider)


def _install_root_handler(logger_provider: LoggerProvider) -> None:
    """Bridge stdlib logging to OTel at the root logger, excluding the `opentelemetry` loggers so
    an export failure can never feed the pipeline that reports it."""
    handler = LoggingHandler(logger_provider=logger_provider)
    handler.addFilter(lambda record: not record.name.startswith("opentelemetry"))
    logging.getLogger().addHandler(handler)


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True)
def api(host: str, port: int) -> None:
    """Serve the deploy endpoint (POST /v1/deploy, GET /v1/tenants/{name})."""
    uvicorn.run("ufo_control.api:app", host=host, port=port, log_level="info", log_config=None)


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True)
def operator(host: str, port: int) -> None:
    """Run the level-triggered Tenant reconcile loop behind a /healthz probe."""
    uvicorn.run(
        "ufo_control.operator:operator_app",
        host=host,
        port=port,
        factory=True,
        log_level="info",
        log_config=None,
    )


@main.command()
def gateway() -> None:
    """Serve the apex onboarding backend (GET /ufo, POST /v1/onboard/{channel})."""
    port = int(os.environ.get(GATEWAY_PORT_ENV, str(DEFAULT_PORT)))
    uvicorn.run(
        "ufo_control.gateway:app", host=DEFAULT_HOST, port=port, log_level="info", log_config=None
    )


@main.command()
def invite() -> None:
    """Mint a one-time invite code that lets onboarding create a new workspace. The plaintext
    prints once and is never stored."""
    click.echo(asyncio.run(_mint_invite()))


async def _mint_invite() -> str:
    pool = await asyncpg.create_pool(dsn=owner_dsn(), min_size=1, max_size=1)
    try:
        invites = InviteCodes(pool=pool)
        await invites.ensure_table()
        return await invites.mint()
    finally:
        await pool.close()


@main.command(name="rls-bootstrap")
def rls_bootstrap() -> None:
    """Bring the shared app database to the RLS tier, as the owner: create the shared ``ufo_serve``
    role the fleet connects as, then enable RLS + the workspace policies. The cluster migrate Job
    runs this after ``ufoctl migrate``, once per bundle rollout."""
    asyncio.run(_bootstrap())
    click.echo("rls policies at head")


async def _bootstrap() -> None:
    await ensure_serve_role(owner_dsn())
    await bootstrap_policies(owner_dsn())
