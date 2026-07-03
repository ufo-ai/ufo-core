"""The selfhost CLI: init, serve, chat, ext, bundle."""

import asyncio
import hashlib
import json
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from uuid import UUID, uuid4

import asyncpg
import click
import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.accounting import SpendReport, SpendRollup
from selfhost.bundle import Bundle
from selfhost.config import Config, config_path, load_config
from selfhost.credentials import CredentialStore
from selfhost.db import apply_migrations, dispose_db, init_db, workspace_tx
from selfhost.ext.loader import load_manifests, lockfile_path
from selfhost.ext.store import ExtensionStore, read_catalog
from selfhost.grants import GrantSummary, grant_summaries
from selfhost.onboarding import AlreadyInitialized, Onboarded, Onboarding
from selfhost.schema import tables
from selfhost.schema.records import DEFAULT_AGENT_NAME
from selfhost.serve import run as serve_run

SELFHOST_DIR = Path.home() / ".selfhost"
DEFAULT_AGENT_MODEL = "claude-opus-4-8"
RECONNECT_SECONDS = 1.0
TURN_REQUEST_TIMEOUT_SECONDS = 90.0
ERASE_LINE = "\r\x1b[K"
MICRO_USD_PER_USD = 1_000_000
DEFAULT_CONFIG = """\
[database]
url = "sqlite+aiosqlite:///selfhost.db"

[blob]
backend = "filesystem"
root = "./blobs"
"""


@click.group()
def main() -> None:
    """An agent runtime you can run, read, and extend."""


@main.command()
@click.option("--email", required=True)
@click.option("--model", default=DEFAULT_AGENT_MODEL, show_default=True)
def init(email: str, model: str) -> None:
    """Write selfhost.toml if absent, apply the schema, then onboard the workspace, owner, default
    agent and model key (plus any extension onboarding steps) and bind this machine's CLI token."""
    config_path = Path("selfhost.toml")
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG)
        click.echo(f"wrote {config_path} (SQLite, filesystem blobs — zero services)")
    config = load_config()
    if config.database.url.startswith("postgresql"):
        asyncio.run(_create_postgres_system_database(config))
    apply_migrations(config.database.url)
    token = secrets.token_hex(32)
    try:
        asyncio.run(_onboard(config, email, model, token))
    except (AlreadyInitialized, ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from error
    SELFHOST_DIR.mkdir(mode=0o700, exist_ok=True)
    token_path = SELFHOST_DIR / "token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    click.echo(f"workspace ready — owner {email}, agent {DEFAULT_AGENT_NAME!r} ({model})")
    click.echo(f"cli token written to {token_path}")


async def _onboard(config: Config, email: str, model: str, token: str) -> None:
    """Open the db boundary once: create the core workspace, bind the CLI token to the new owner
    (the CLI surface's own identity, issued here not in the surface-agnostic engine), THEN run the
    extension onboarding steps — so core access lands before any add-on step that could fail."""
    init_db(config.database.url)
    try:
        key = os.environ.get(config.credentials.key_env)
        credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
        onboarding = Onboarding(
            config=config,
            email=email,
            model=model,
            credentials=credentials,
            manifests=load_manifests(),
        )
        onboarded = await onboarding.create()
        await _bind_cli_token(onboarded, token)
        await onboarding.run_steps(onboarded)
    finally:
        await dispose_db()


async def _bind_cli_token(onboarded: Onboarded, token: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=onboarded.workspace_id,
                member_id=onboarded.member_id,
                surface="cli",
                external_id=hashlib.sha256(token.encode()).hexdigest(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _create_postgres_system_database(config: Config) -> None:
    app_dsn = config.database.url.replace("postgresql+asyncpg://", "postgresql://", 1)
    _, _, system_name = config.database.system_url.rpartition("/")
    connection = await asyncpg.connect(app_dsn)
    try:
        exists = await connection.fetchrow(
            "select 1 from pg_database where datname = $1", system_name
        )
        if exists is None:
            await connection.execute(f'create database "{system_name}"')
    finally:
        await connection.close()


@main.command()
def serve() -> None:
    """Run the workspace: surfaces + workers, one process."""
    serve_run()


@main.command()
@click.argument("message", required=False)
@click.option("--new", is_flag=True, help="Start a fresh conversation session.")
def chat(message: str | None, new: bool) -> None:
    """Talk to the agent; the session continues across invocations."""
    config = load_config()
    token_path = SELFHOST_DIR / "token"
    if not token_path.exists():
        raise click.ClickException("no CLI token — run `selfhost init` first")
    headers = {
        "authorization": f"Bearer {token_path.read_text().strip()}",
        "x-selfhost-session": _session(new),
    }
    if message is not None:
        _run_turn(config, headers, message)
        return
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            click.echo()
            return
        if line.strip():
            _run_turn(config, headers, line)


def _session(new: bool) -> str:
    path = SELFHOST_DIR / "session"
    if new or not path.exists():
        SELFHOST_DIR.mkdir(mode=0o700, exist_ok=True)
        path.write_text(uuid4().hex)
    return path.read_text().strip()


def _run_turn(config: Config, headers: dict[str, str], message: str) -> None:
    base = f"http://{config.serve.host}:{config.serve.port}"
    current: dict[str, str] = {}
    try:
        asyncio.run(_stream_turn(base, headers, message, current))
    except KeyboardInterrupt:
        turn_id = current.get("turn_id")
        if turn_id is not None:
            asyncio.run(_cancel_turn(base, headers, turn_id))
        click.echo("\n(cancelled)")


async def _stream_turn(
    base: str, headers: dict[str, str], message: str, current: dict[str, str]
) -> None:
    display = _TurnDisplay(
        out=sys.stdout, err=sys.stderr, tty=sys.stdout.isatty() and sys.stderr.isatty()
    )
    async with httpx.AsyncClient(base_url=base, timeout=TURN_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post("/v1/chat", content=message.encode(), headers=headers)
        response.raise_for_status()
        turn_id = response.json()["turn_id"]
        current["turn_id"] = turn_id
        while True:
            try:
                async with client.stream(
                    "GET", f"/v1/turns/{turn_id}/stream", headers=headers
                ) as stream:
                    if stream.status_code != 200:
                        raise click.ClickException(f"stream failed with {stream.status_code}")
                    async for line in stream.aiter_lines():
                        if not line:
                            continue
                        frame = json.loads(line)
                        if "frame" in frame:
                            display.terminal(frame["frame"])
                            return
                        if "message" in frame:
                            display.message(frame["message"])
                            return
                        if "cost_micro_usd" in frame:
                            display.tick(frame["tokens"], frame["cost_micro_usd"])
                            continue
                        display.text(frame["text"])
            except httpx.TransportError:
                await asyncio.sleep(RECONNECT_SECONDS)


async def _cancel_turn(base: str, headers: dict[str, str], turn_id: str) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
        await client.post(f"/v1/turns/{turn_id}/cancel", headers=headers)


@dataclass
class _TurnDisplay:
    """One turn's terminal rendering: text deltas stream to stdout; the live cost meter is a
    transient stderr line that only ever occupies a line of its own and is erased before anything
    else prints, so it can never overwrite streamed text. Its line bookkeeping assumes both
    streams land on one terminal, so tty is true only when stdout and stderr are both ttys —
    the terminal frame prints the authoritative cost either way."""

    out: TextIO
    err: TextIO
    tty: bool
    streamed: bool = False
    line_open: bool = False
    meter: bool = False

    def text(self, delta: str) -> None:
        self._erase_meter()
        click.echo(delta, nl=False, file=self.out)
        self.streamed = True
        if delta:
            self.line_open = not delta.endswith("\n")

    def tick(self, tokens: int, cost_micro_usd: int) -> None:
        if not self.tty:
            return
        if self.line_open:
            click.echo(file=self.err)
            self.line_open = False
        dollars = cost_micro_usd / MICRO_USD_PER_USD
        meter = click.style(f"{tokens} tok · ${dollars:.6f}", dim=True)
        click.echo(f"{ERASE_LINE}{meter}", nl=False, file=self.err, color=True)
        self.meter = True

    def message(self, note: str) -> None:
        self._close_line()
        click.echo(click.style(note, dim=True), file=self.out)

    def terminal(self, frame: dict[str, object]) -> None:
        self._close_line()
        match frame["status"]:
            case "done":
                if not self.streamed:
                    click.echo(frame["text"], file=self.out)
                dollars = int(frame["cost_micro_usd"]) / MICRO_USD_PER_USD
                cost = f"{frame['model']} · {frame['tokens']} tok · ${dollars:.6f}"
                click.echo(click.style(cost, dim=True), file=self.out)
            case "cancelled":
                click.echo(frame.get("text") or "(cancelled)", file=self.out)
            case _:
                raise click.ClickException(f"turn failed: {frame['error_class']}")

    def _erase_meter(self) -> None:
        if not self.meter:
            return
        click.echo(ERASE_LINE, nl=False, file=self.err, color=True)
        self.meter = False

    def _close_line(self) -> None:
        self._erase_meter()
        if self.line_open:
            click.echo(file=self.out)
            self.line_open = False


@main.group(name="spend-cap")
def spend_cap() -> None:
    """Read and set the workspace spend caps enforced at turn admission and per model round."""


@spend_cap.command(name="set")
@click.option("--scope", type=click.Choice(["workspace", "member", "agent"]), required=True)
@click.option("--subject-id", default="", help="member or agent id; omit for workspace scope")
@click.option("--window-seconds", type=int, required=True)
@click.option("--limit-micro-usd", type=int, required=True)
@click.option(
    "--on-breach", type=click.Choice(["park", "reject"]), default="park", show_default=True
)
def spend_cap_set(
    scope: str,
    subject_id: str,
    window_seconds: int,
    limit_micro_usd: int,
    on_breach: str,
) -> None:
    """Create or update a spend cap; raising a cap lets the sweep re-admit its parked turns."""
    if scope == "workspace" and subject_id:
        raise click.ClickException("workspace scope takes no --subject-id")
    if scope != "workspace" and not subject_id:
        raise click.ClickException(f"{scope} scope requires --subject-id")
    subject = UUID(subject_id) if subject_id else None
    config = load_config()
    cap_id = asyncio.run(
        _write_spend_cap(config, scope, subject, window_seconds, limit_micro_usd, on_breach)
    )
    dollars = limit_micro_usd / MICRO_USD_PER_USD
    click.echo(f"spend cap {cap_id} — {scope} ${dollars:,.2f} / {window_seconds}s ({on_breach})")


@spend_cap.command(name="list")
def spend_cap_list() -> None:
    """Show the workspace's spend caps."""
    config = load_config()
    caps = asyncio.run(_read_spend_caps(config))
    if not caps:
        click.echo("no spend caps set")
        return
    for cap_id, scope, subject, window_seconds, limit_micro_usd, on_breach in caps:
        dollars = limit_micro_usd / MICRO_USD_PER_USD
        target = f" {subject}" if subject is not None else ""
        click.echo(f"{cap_id}  {scope}{target}  ${dollars:,.2f} / {window_seconds}s  {on_breach}")


async def _write_spend_cap(
    config: Config,
    scope: str,
    subject: UUID | None,
    window_seconds: int,
    limit_micro_usd: int,
    on_breach: str,
) -> UUID:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            subject_match = (
                tables.spend_cap.c.subject_id.is_(None)
                if subject is None
                else tables.spend_cap.c.subject_id == subject
            )
            existing = (
                await connection.execute(
                    sa.select(tables.spend_cap.c.id).where(
                        tables.spend_cap.c.workspace_id == workspace_id,
                        tables.spend_cap.c.scope == scope,
                        subject_match,
                        tables.spend_cap.c.window_seconds == window_seconds,
                    )
                )
            ).one_or_none()
            if existing is not None:
                await connection.execute(
                    sa.update(tables.spend_cap)
                    .values(
                        limit_micro_usd=limit_micro_usd,
                        on_breach=on_breach,
                        updated_at=sa.func.now(),
                    )
                    .where(tables.spend_cap.c.id == existing.id)
                )
                return existing.id
            cap_id = uuid4()
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=cap_id,
                    workspace_id=workspace_id,
                    scope=scope,
                    subject_id=subject,
                    window_seconds=window_seconds,
                    limit_micro_usd=limit_micro_usd,
                    on_breach=on_breach,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            return cap_id
    finally:
        await dispose_db()


async def _read_spend_caps(
    config: Config,
) -> list[tuple[UUID, str, UUID | None, int, int, str]]:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            rows = (
                await connection.execute(
                    sa.select(
                        tables.spend_cap.c.id,
                        tables.spend_cap.c.scope,
                        tables.spend_cap.c.subject_id,
                        tables.spend_cap.c.window_seconds,
                        tables.spend_cap.c.limit_micro_usd,
                        tables.spend_cap.c.on_breach,
                    )
                    .where(tables.spend_cap.c.workspace_id == workspace_id)
                    .order_by(tables.spend_cap.c.scope)
                )
            ).all()
        return [
            (r.id, r.scope, r.subject_id, r.window_seconds, r.limit_micro_usd, r.on_breach)
            for r in rows
        ]
    finally:
        await dispose_db()


SPEND_WINDOW_DEFAULT_SECONDS = 86_400


@main.command()
@click.option("--window-seconds", type=int, default=SPEND_WINDOW_DEFAULT_SECONDS, show_default=True)
def spend(window_seconds: int) -> None:
    """Sum the ledger over a window: the workspace total, then a per-dimension, per-member, and
    per-agent breakdown — the rollups that match the ledger."""
    config = load_config()
    report = asyncio.run(_read_spend(config, window_seconds))
    total = report.total_micro_usd / MICRO_USD_PER_USD
    click.echo(f"spend · last {window_seconds / 3600:g}h · ${total:,.6f}")
    for dim in report.by_dimension:
        priced = dim.priced_micro_usd / MICRO_USD_PER_USD
        click.echo(f"  {dim.dimension:<10}{dim.amount:>14,}  ${priced:,.6f}")
    click.echo("by member:")
    for member in report.by_member:
        click.echo(f"  {member.label:<32}${member.priced_micro_usd / MICRO_USD_PER_USD:,.6f}")
    click.echo("by agent:")
    for agent in report.by_agent:
        click.echo(f"  {agent.label:<32}${agent.priced_micro_usd / MICRO_USD_PER_USD:,.6f}")


async def _read_spend(config: Config, window_seconds: int) -> SpendReport:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            return await SpendRollup(workspace_id).read(connection, window_seconds)
    finally:
        await dispose_db()


@main.command()
def grants() -> None:
    """List the OAuth accounts granted to each agent (granted in chat via connect_account)."""
    config = load_config()
    summaries = asyncio.run(_read_grants(config))
    if not summaries:
        click.echo("no grants")
        return
    for summary in summaries:
        granted = summary.granted_at.strftime("%Y-%m-%d")
        click.echo(f"{summary.agent:<20}{summary.provider:<16}{summary.account_id:<28}{granted}")


async def _read_grants(config: Config) -> tuple[GrantSummary, ...]:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        return await grant_summaries(workspace_id)
    finally:
        await dispose_db()


@main.group()
def ext() -> None:
    """Search the extension store and pin installs into the deploy's lockfile."""


def _store(config: Config) -> ExtensionStore:
    if config.ext.store is None:
        raise click.ClickException("extension store not enabled (set [ext].store in selfhost.toml)")
    return ExtensionStore(catalog=read_catalog(config.ext.store), lockfile=lockfile_path())


@ext.command(name="search")
@click.argument("query", default="")
def ext_search(query: str) -> None:
    """List the extensions the store offers, marking installed and bundle-only ones."""
    listings = _store(load_config()).search(query)
    if not listings:
        click.echo("no matching extensions")
        return
    for listing in listings:
        if listing.installed:
            state = "installed"
        elif listing.disabled:
            state = "bundle-only"
        else:
            state = "available"
        click.echo(f"{listing.name:<24}{listing.version:<12}{state}")


@ext.command(name="install")
@click.argument("name")
def ext_install(name: str) -> None:
    """Pin an extension from the store into the lockfile; the next serve loads it."""
    try:
        pin = _store(load_config()).install(name)
    except (ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"installed {pin.name} {pin.version} ({pin.digest})")


@ext.command(name="remove")
@click.argument("name")
def ext_remove(name: str) -> None:
    """Drop an extension from the lockfile; the next serve stops loading it."""
    try:
        _store(load_config()).remove(name)
    except (ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"removed {name}")


DEFAULT_BUNDLE_DIR = Path("bundle")


@main.command()
@click.option(
    "--out", type=click.Path(path_type=Path), default=DEFAULT_BUNDLE_DIR, show_default=True
)
def bundle(out: Path) -> None:
    """Freeze this deploy into a runnable artifact: OCI image recipe, pinned config, lockfile."""
    config = load_config()
    catalog = read_catalog(config.ext.store) if config.ext.store is not None else None
    result = Bundle(config_path=config_path(), catalog=catalog, out=out).build()
    click.echo(f"bundle at {result.out} — {len(result.pins)} extension(s) pinned")
    for pin in result.pins:
        click.echo(f"  {pin.name} {pin.version} {pin.digest}")
