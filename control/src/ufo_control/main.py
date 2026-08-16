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
from ufo_control.gateway_invite import (
    InviteCodes,
    InviteError,
    MintedInvite,
    SignupProfile,
)
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
@click.argument("email")
@click.option("--business", help="What they said their company does.")
@click.option("--goals", help="What they said they want an agent to do.")
@click.option(
    "--object",
    "object_number",
    type=click.IntRange(min=1),
    help="The waitlist object this approves, when it approves one.",
)
def invite(email: str, business: str | None, goals: str | None, object_number: int | None) -> None:
    """Grant an email domain one new workspace and email it the invitation.

    Both intake answers open the new workspace's main agent prompt. They travel together: a grant
    describes this customer completely or not at all. `--object` names a waitlist object where one
    exists; a grant approved from the intake form answers a form response, which is no waitlist
    object, so it carries no number.
    """
    if bool(business) != bool(goals):
        raise click.ClickException("--business and --goals are given together or not")
    profile = SignupProfile(business=business, goals=goals) if business and goals else None
    try:
        minted = asyncio.run(_mint_invite(object_number, email, profile))
    except (InviteError, WorkEmailError) as error:
        raise click.ClickException(str(error)) from error
    expires = minted.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    approved = (
        f"object #{minted.object_number}" if minted.object_number is not None else minted.email
    )
    click.echo(f"{approved} granted to {minted.email}, expires {expires} UTC")


async def _mint_invite(
    object_number: int | None, email: str, profile: SignupProfile | None
) -> MintedInvite:
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
        minted = await InviteCodes(pool=pool).mint(object_number, email, profile)
    finally:
        await pool.close()
    subject, body = invite_email(minted.email, minted.expires_at, apex_host)
    try:
        await sender.send(minted.email, subject, body)
    except Exception as error:
        raise click.ClickException(
            f"{minted.email} is granted, but the invitation "
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
