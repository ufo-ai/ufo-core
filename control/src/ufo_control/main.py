"""The hosted gateway and database bootstrap entry point."""

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
from ufo_control.rls import bootstrap_policies, ensure_serve_role, owner_dsn

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
GATEWAY_PORT_ENV = "UFO_GATEWAY_PORT"
OTLP_ENDPOINT_ENV = "UFO_CONTROL_OTLP_ENDPOINT"
OTLP_LOGS_PATH = "v1/logs"
SERVICE_NAME = "ufo-control"


@click.group()
def main() -> None:
    """Operate the hosted shared-workspace service."""
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
def gateway() -> None:
    """Serve onboarding, fleet count, and the terminal client."""
    port = int(os.environ.get(GATEWAY_PORT_ENV, str(DEFAULT_PORT)))
    uvicorn.run(
        "ufo_control.gateway:app", host=DEFAULT_HOST, port=port, log_level="info", log_config=None
    )


@main.command()
def invite() -> None:
    """Mint a one-time new-workspace invite."""
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
    """Create the shared serve role and workspace policies."""
    asyncio.run(_bootstrap())
    click.echo("rls policies at head")


async def _bootstrap() -> None:
    dsn = owner_dsn()
    await bootstrap_policies(dsn)
    await ensure_serve_role(dsn)
