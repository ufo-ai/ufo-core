"""The hosted gateway and database bootstrap entry point."""

import asyncio
import logging
import os
from datetime import UTC, datetime

import asyncpg
import click
import uvicorn
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from ufo_control.gateway_email import (
    WorkEmailError,
    email_sender_from_env,
    invite_email,
    public_apex_host,
)
from ufo_control.gateway_invite import InviteCodes, InviteError, MintedInvite
from ufo_control.gateway_slack_connect import rearm_failed_delivery
from ufo_control.rls import bootstrap_policies, ensure_serve_role, owner_dsn
from ufo_control.schema import require_control_schema, shape_control_schema

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
def migrate() -> None:
    """Shape the platform control schema — every gateway ledger, as the database owner."""
    asyncio.run(shape_control_schema(owner_dsn()))
    click.echo("control schema at head")


@main.command()
@click.argument("object_number", type=click.IntRange(min=1))
@click.argument("email")
def invite(object_number: int, email: str) -> None:
    """Grant a waitlist object's email domain one new workspace and email it the invitation."""
    try:
        minted = asyncio.run(_mint_invite(object_number, email))
    except (InviteError, WorkEmailError) as error:
        raise click.ClickException(str(error)) from error
    expires = minted.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    click.echo(f"object #{minted.object_number} granted to {minted.email}, expires {expires} UTC")


async def _mint_invite(object_number: int, email: str) -> MintedInvite:
    """The SES sender is built before the grant lands, so a deploy missing its mail configuration
    refuses without spending the object's one live grant. A grant that outlives its own invitation
    still opens the workspace — the member proves it by verifying the granted address — so a failed
    send is reported against a standing grant rather than withdrawing it."""
    apex_host = public_apex_host()
    dsn = owner_dsn()
    await require_control_schema(dsn)
    sender = email_sender_from_env()
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=1)
    try:
        minted = await InviteCodes(pool=pool).mint(object_number, email)
    finally:
        await pool.close()
    subject, body = invite_email(minted.email, minted.expires_at, apex_host)
    try:
        await sender.send(minted.email, subject, body)
    except Exception as error:
        raise click.ClickException(
            f"object #{minted.object_number} is granted to {minted.email}, but the invitation "
            f"could not be emailed ({error}); the grant stands — tell them to run the installer"
        ) from error
    return minted


@main.command(name="slack-connect-retry")
@click.argument("email_domain")
def slack_connect_retry(email_domain: str) -> None:
    """Re-arm one failed signup Slack Connect delivery once its cause is corrected."""
    domain = email_domain.strip().lower()
    failed_at = asyncio.run(_rearm_slack_connect(domain))
    if failed_at is None:
        raise click.ClickException(f"no failed slack connect delivery for {domain}")
    stamp = failed_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    click.echo(f"slack connect delivery for {domain} re-armed, failed since {stamp} UTC")


async def _rearm_slack_connect(email_domain: str) -> datetime | None:
    dsn = owner_dsn()
    await require_control_schema(dsn)
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=1)
    try:
        return await rearm_failed_delivery(pool, email_domain)
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
