"""The ufoctl CLI: init, serve, portal, ext, bundle."""

import asyncio
import os
import re
import secrets
import subprocess
import sys
import threading
import tomllib
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import click
import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.access.credentials import CredentialStore
from ufo.access.grants import GrantSummary, workspace_grant_summaries
from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.billing.accounting import SpendReport, SpendRollup
from ufo.billing.balance import Balance, credit, read_balance, set_reserve
from ufo.blob import WorkspaceBlobStore, blob_store_for
from ufo.bundle import Bundle, wheel_name
from ufo.config import Config, config_path, load_config
from ufo.db import (
    MIGRATIONS_DIR,
    apply_migrations,
    core_migration_head,
    dispose_db,
    init_db,
    init_owner_db,
    owner_tx,
    workspace_tx,
)
from ufo.durability import replay_safe_client
from ufo.ext.loader import load_manifests, lockfile_path
from ufo.ext.store import ExtensionStore, read_catalog
from ufo.onboard.onboarding import DEFAULT_AGENT_MODEL, AlreadyInitialized, Onboarded, Onboarding
from ufo.onboard.seed import KitchenSink
from ufo.proxy_serve import OWNER_DSN_ENV
from ufo.sandbox.containment import contained_file
from ufo.sandbox.ingress_serve import run as ingress_run
from ufo.schema import tables
from ufo.schema.records import (
    DEFAULT_AGENT_NAME,
    DEFAULT_REASONING_EFFORT,
    ReasoningEffort,
)
from ufo.seats import email_domain
from ufo.serve import home_surface
from ufo.serve import run as serve_run
from ufo.turns.cancellation import cancel_one_turn
from ufo.workspace import ws

UFOCTL_DIR_ENV = "UFOCTL_DIR"
RESERVED_DOTENV_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
PORTAL_REACH_TIMEOUT_SECONDS = 5.0
HANDOFF_PATH_BYTES = 24
REASONING_EFFORTS = ("auto", "off", "low", "medium", "high")
LOOPBACK = "127.0.0.1"
MICRO_USD_PER_USD = 1_000_000
CLI_TOKEN_TTL = timedelta(days=3650)
DEFAULT_CONFIG = """\
[database]
url = "sqlite+aiosqlite:///ufo.db"
owner_url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"

[pack]
name = "assistant"

[research]
search_provider = "perplexity"

[connect]
public_base_url = "http://ufo.localhost:8710"

[sandbox]
ingress_public_url = "http://ufo.localhost:8100"
"""
CORE_VERSIONS_DIR = MIGRATIONS_DIR / "versions"
MIGRATION_HEAD_FILENAME = "HEAD"
MIGRATION_STAMP = "%Y%m%d%H%M%S"
MIGRATION_FILE_MODE = 0o644
MIGRATION_SLUG = re.compile(r"[a-z][a-z0-9_]*")
MIGRATION_TEMPLATE = """\
revision: str = "{revision}"
down_revision: str | None = "{down_revision}"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def _ufoctl_dir() -> Path:
    override = os.environ.get(UFOCTL_DIR_ENV)
    return Path(override) if override else Path.home() / ".ufoctl"


def _dotenv_path() -> Path:
    return config_path().parent / ".env"


def _dotenv_pairs(text: str) -> list[tuple[str, str]]:
    """Parse `.env` text into (key, value) pairs — the whole format: one `KEY=VALUE` per line, blank
    lines and `#` comments skipped, a leading `export` and matching surrounding quotes stripped. A
    quoted value whose closing quote lands on a later line carries those lines verbatim, so a PEM
    private key is one entry like every other secret; an unclosed quote is malformed and raises."""
    pairs: list[tuple[str, str]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        name = key.strip().removeprefix("export ").strip()
        value = value.strip()
        quote = value[:1] if value[:1] in ('"', "'") else ""
        if not quote:
            pairs.append((name, value))
            continue
        if len(value) >= 2 and value.endswith(quote):
            pairs.append((name, value[1:-1]))
            continue
        body = [value[1:]]
        while index < len(lines) and not lines[index].rstrip().endswith(quote):
            body.append(lines[index])
            index += 1
        if index == len(lines):
            raise RuntimeError(f"{name} opens a {quote} in .env that never closes")
        body.append(lines[index].rstrip()[:-1])
        index += 1
        pairs.append((name, "\n".join(body)))
    return pairs


def _load_dotenv() -> None:
    """Load `.env` into the process, refusing the bare provider-key names first: every tool
    reading `.env` picks those up, so a key meant for ufo lives under its `UFO_`-prefixed name and
    a bare name in ufo's own config surface is always a mistake — refused before any of it enters
    the environment."""
    dotenv = _dotenv_path()
    if not dotenv.exists():
        return
    pairs = _dotenv_pairs(dotenv.read_text())
    for name, _ in pairs:
        if name in RESERVED_DOTENV_NAMES:
            raise click.ClickException(
                f"{dotenv} sets {name}, which every tool reading .env picks up — rename it to "
                f"UFO_{name} to scope it to ufo"
            )
    for name, value in pairs:
        os.environ[name] = value


@click.group()
def main() -> None:
    """An agent runtime you can run, read, and extend."""
    _load_dotenv()


def _one_address(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    """The owner's address answers the same shape rule the member write enforces, so a typo is
    refused here with a readable line instead of raising out of the workspace insert."""
    if not email_domain(value):
        raise click.BadParameter(f"{value!r} is not one local@domain address.")
    return value


@main.command()
@click.option("--email", required=True, callback=_one_address)
@click.option("--model", default=DEFAULT_AGENT_MODEL, show_default=True)
@click.option(
    "--reasoning",
    type=click.Choice(REASONING_EFFORTS),
    default=DEFAULT_REASONING_EFFORT,
    show_default=True,
)
def init(email: str, model: str, reasoning: ReasoningEffort) -> None:
    """Write ufo.toml if absent, apply the schema, then onboard the workspace, owner, default
    agent and model key (plus any extension onboarding steps) and bind this machine's CLI token."""
    path = config_path()
    if not path.exists():
        path.write_text(DEFAULT_CONFIG)
        click.echo(f"wrote {path} (SQLite, filesystem blobs — zero services)")
    config = load_config()
    added = _write_dev_secrets(config)
    if added:
        click.echo(f"wrote {', '.join(added)} to {_dotenv_path()} — serve auto-loads it")
    if config.database.url.startswith("postgresql"):
        asyncio.run(_create_postgres_system_database(config))
    apply_migrations(config.database.url, config.pack.name)
    secret = os.environ.get(UFO_TOKEN_SECRET_ENV)
    if not secret:
        raise click.ClickException(f"{UFO_TOKEN_SECRET_ENV} is unset — cannot mint a CLI token")
    try:
        onboarded = asyncio.run(_onboard(config, email, model, reasoning))
    except (AlreadyInitialized, ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from error
    token = mint_token(secret, str(onboarded.workspace_id), email, CLI_TOKEN_TTL)
    ufoctl_dir = _ufoctl_dir()
    ufoctl_dir.mkdir(mode=0o700, exist_ok=True)
    token_path = ufoctl_dir / "token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    click.echo(
        f"workspace ready — owner {email}, agent {DEFAULT_AGENT_NAME!r} "
        f"({model}, {reasoning} reasoning)"
    )
    click.echo(f"cli token written to {token_path}")
    for missing in _missing_deploy_keys(config):
        click.echo(
            f"{missing} is unset — add it to {_dotenv_path()} before the features that need it"
        )


def _missing_deploy_keys(config: Config) -> tuple[str, ...]:
    """The provider keys this pack's extensions declared and the environment does not carry —
    reported by their `UFO_`-prefixed names, the form `.env.template` scaffolds and `deploy_env`
    resolves first (the bare upstream name also satisfies a declaration). Nobody can mint one, so
    `init` names them where the developer is already configuring rather than leaving the first job
    that needs one to raise into a log hours later. It reports rather than refuses: a serve with
    no key still boots, which is what makes a zero-config checkout worth having."""
    declared = {
        key for manifest in load_manifests(config.pack.name) for key in manifest.deploy_keys
    }
    present = (
        {name for name, value in _dotenv_pairs(_dotenv_path().read_text()) if value}
        if _dotenv_path().exists()
        else set()
    )
    present |= {name for name in os.environ if os.environ[name]}
    return tuple(sorted(f"UFO_{name}" for name in declared if not present & {f"UFO_{name}", name}))


def _write_dev_secrets(config: Config) -> tuple[str, ...]:
    """Mint the dev secrets a zero-config `serve` needs and merge them into the `.env` beside the
    config without clobbering: the Fernet credential key the store seals BYOK secrets with, the HMAC
    secret that signs artifact-delivery tokens, and the HMAC secret that signs member bearers — the
    same one `init` mints this machine's CLI token with and `serve` verifies it against. `.env`
    auto-loads on the next verb, so `serve` boots with no manual export; a name already in `.env` or
    exported is left untouched. Returns the names newly written."""
    minted = {
        config.credentials.key_env: Fernet.generate_key().decode(),
        config.artifacts.token_secret_env: secrets.token_urlsafe(32),
        UFO_TOKEN_SECRET_ENV: secrets.token_urlsafe(32),
    }
    dotenv = _dotenv_path()
    existing = dotenv.read_text() if dotenv.exists() else ""
    present = {name for name, _ in _dotenv_pairs(existing)} | os.environ.keys()
    added = {name: value for name, value in minted.items() if name not in present}
    if not added:
        return ()
    prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
    dotenv.write_text(prefix + "".join(f"{name}={value}\n" for name, value in added.items()))
    for name, value in added.items():
        os.environ[name] = value
    return tuple(added)


async def _onboard(config: Config, email: str, model: str, reasoning: ReasoningEffort) -> Onboarded:
    """Open the db boundary once: create the core workspace and owner member, THEN run the
    extension onboarding steps — so core access lands before any add-on step that could fail. The
    CLI's bearer names this owner by email; the `ufo` surface links the member on first contact."""
    init_db(config.database.url)
    try:
        key = os.environ.get(config.credentials.key_env)
        credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
        onboarding = Onboarding(
            config=config,
            email=email,
            model=model,
            reasoning=reasoning,
            credentials=credentials,
            manifests=load_manifests(config.pack.name),
        )
        onboarded = await onboarding.create()
        await onboarding.run_steps(onboarded)
        return onboarded
    finally:
        await dispose_db()


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
def migrate() -> None:
    """Bring the database to head: apply core's schema plus every active extension's migration
    branch. `init` runs this once at onboarding; run it again after `ext install` adds a
    table-owning extension, before `serve`, so the extension's tables exist. Idempotent.

    The shared-schema deploy runs this once as the RLS-bypassing owner (the tables' owner), so the
    cluster migrate Job injects the owner DSN as `UFO_OWNER_DSN` — mirroring `ingress` — while
    the baked config still supplies `[pack]`. Without it, the config's own `database.url` is used
    for local development or a dedicated server."""
    config = load_config()
    owner = os.environ.get(OWNER_DSN_ENV)
    if owner:
        owner = owner.replace("postgres://", "postgresql://", 1)
        url = owner.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        url = config.database.url
    apply_migrations(url, config.pack.name)
    click.echo("schema at head")


def _one_slug(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    if not MIGRATION_SLUG.fullmatch(value):
        raise click.BadParameter(f"{value!r} is not a snake_case name.")
    return value


@main.command(name="new-migration")
@click.argument("slug", callback=_one_slug)
def new_migration(slug: str) -> None:
    """Write core's next migration file, named and revisioned by the current UTC timestamp.

    The stamp is the id, so two branches open the same day never claim the same one and neither has
    to be renumbered to land. The id carries no ordering: `down_revision` does, and this writes
    core's head right now. When another branch's migration merges first, repoint `down_revision` at
    the new head and keep the stamp — the single-head gate is what catches a fork. The rewrite of
    `versions/HEAD` is what makes two racing migration branches conflict at merge instead of
    forking main."""
    head = core_migration_head()
    stamp = datetime.now(UTC).strftime(MIGRATION_STAMP)
    with contained_file(f"{stamp}_{slug}.py", CORE_VERSIONS_DIR) as target:
        target.replace_text(
            MIGRATION_TEMPLATE.format(revision=stamp, down_revision=head), MIGRATION_FILE_MODE
        )
        click.echo(f"wrote {target.path} — revision {stamp}, down_revision {head}")
    with contained_file(MIGRATION_HEAD_FILENAME, CORE_VERSIONS_DIR) as head_file:
        head_file.replace_text(f"{stamp}\n", MIGRATION_FILE_MODE)


@main.command()
def serve() -> None:
    """Run surfaces, workers, jobs, and the egress-control RPC the Rust proxy calls."""
    config = load_config()
    surface = home_surface(load_manifests(config.pack.name))
    if surface is not None:
        click.echo(
            f"portal {_serve_base(config)}/surface/{surface} — "
            "run `ufoctl portal` to open a session in your browser"
        )
    serve_run()


@main.command()
def portal() -> None:
    """Open the portal in a browser, signed in with this machine's CLI token."""
    config = load_config()
    surface = home_surface(load_manifests(config.pack.name))
    if surface is None:
        raise click.ClickException(f"pack {config.pack.name!r} installs no browser portal")
    token_path = _ufoctl_dir() / "token"
    if not token_path.exists():
        raise click.ClickException("no CLI token — run `ufoctl init` first")
    base = _serve_base(config)
    portal_url = f"{base}/surface/{surface}"
    try:
        httpx.get(portal_url, follow_redirects=False, timeout=PORTAL_REACH_TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise click.ClickException(
            f"serve is not answering at {base} ({error}) — run `ufoctl serve` first"
        ) from error
    BrowserHandoff(portal_url=portal_url, token=token_path.read_text().strip()).open()
    click.echo(f"opened {portal_url}")


def _serve_base(config: Config) -> str:
    """The host the browser session lands on. `public_base_url` when set — every absolute link the
    deploy mints (a homepage frame, a connect callback) names that host, and a cookie is host-only,
    so a session opened anywhere else cannot follow those links. The bind address is the fallback
    for a config that mints none."""
    if config.connect.public_base_url is not None:
        return config.connect.public_base_url
    return f"http://{config.serve.host}:{config.serve.port}"


@dataclass(frozen=True)
class BrowserHandoff:
    """Hand this machine's bearer to a browser as a portal session. The browser opens a page this
    process serves and that page posts the bearer to the portal — the same POST the hosted sign-in
    card makes, so a node with no sign-in page reaches the portal through the one door every deploy
    uses, with nothing for anyone to type or paste. The bearer rides the form body, never a URL.
    The page answers at an unguessable path and the listener closes behind the one request that
    took it, so another account on the host cannot read the bearer off the port."""

    portal_url: str
    token: str

    def open(self) -> None:
        path = f"/{secrets.token_urlsafe(HANDOFF_PATH_BYTES)}"
        delivered = threading.Event()
        with HTTPServer((LOOPBACK, 0), self._responder(path, delivered)) as listener:
            url = f"http://{LOOPBACK}:{listener.server_port}{path}"
            if not webbrowser.open(url):
                click.echo(f"open {url} to finish signing in")
            while not delivered.is_set():
                listener.handle_request()

    def _responder(self, path: str, delivered: threading.Event) -> type[BaseHTTPRequestHandler]:
        page = self._page().encode()

        class Responder(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != path:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                delivered.set()

            def log_message(self, *args: object) -> None: ...

        return Responder

    def _page(self) -> str:
        return (
            '<!doctype html>\n<meta charset="utf-8">\n<title>ufo</title>\n'
            f'<form id="open" method="post" action="{escape(self.portal_url, quote=True)}">\n'
            f'<input type="hidden" name="token" value="{escape(self.token, quote=True)}">\n'
            '<button type="submit">Open your workspace</button>\n</form>\n'
            "<script>document.getElementById('open').submit()</script>\n"
        )


@main.command()
def ingress() -> None:
    """Run the sandbox ingress: a token-gated reverse proxy to conversations' sandbox ports."""
    ingress_run()


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


@main.group(name="balance")
def balance() -> None:
    """Read and credit the workspace's prepaid balance."""


@balance.command(name="show")
@click.option("--workspace-id", default="", help="omit on a single-workspace deploy")
def balance_show(workspace_id: str) -> None:
    """Show what is left, the headroom a turn needs to begin, and the lifetime totals behind it."""
    config = load_config()
    current = asyncio.run(_read_balance(config, workspace_id))
    if current is None:
        click.echo("no balance")
        return
    click.echo(
        f"balance ${current.balance_micro_usd / MICRO_USD_PER_USD:,.2f}  "
        f"reserve ${current.reserve_micro_usd / MICRO_USD_PER_USD:,.2f}"
    )
    click.echo(
        f"granted ${current.granted_micro_usd / MICRO_USD_PER_USD:,.2f}  "
        f"charged ${current.charged_micro_usd / MICRO_USD_PER_USD:,.2f}  "
        f"last {current.last_purchase_at or 'never'}"
    )


@balance.command(name="credit")
@click.option("--granted-micro-usd", type=int, required=True)
@click.option("--charged-micro-usd", type=int, default=0, show_default=True)
@click.option("--reference", required=True, help="idempotency key; one credit per workspace")
@click.option("--workspace-id", default="")
def balance_credit(
    granted_micro_usd: int, charged_micro_usd: int, reference: str, workspace_id: str
) -> None:
    """Add to the balance once per reference. Both amounts may be negative, which is how a refund or
    a corrected credit is taken back off."""
    if granted_micro_usd == 0:
        raise click.ClickException("--granted-micro-usd must not be zero")
    config = load_config()
    if asyncio.run(
        _credit_balance(config, workspace_id, granted_micro_usd, charged_micro_usd, reference)
    ):
        click.echo(f"credited ${granted_micro_usd / MICRO_USD_PER_USD:,.2f} ({reference})")
        return
    click.echo(f"already credited ({reference})")


@balance.command(name="reserve")
@click.option("--micro-usd", type=int, required=True)
@click.option("--workspace-id", default="")
def balance_reserve(micro_usd: int, workspace_id: str) -> None:
    """Set the headroom a turn needs before it may begin."""
    if micro_usd < 0:
        raise click.ClickException("--micro-usd must not be negative")
    config = load_config()
    if asyncio.run(_set_reserve(config, workspace_id, micro_usd)):
        click.echo(f"reserve ${micro_usd / MICRO_USD_PER_USD:,.2f}")
        return
    raise click.ClickException("no balance to set a reserve on; credit it first")


async def _target_workspace(named: str) -> UUID:
    """The workspace a command acts on: the one named, else the deploy's only one. A hosted deploy
    serves many, so a bare command there names the count and the option that fixes it rather than
    picking one."""
    async with owner_tx() as connection:
        if named:
            wanted = UUID(named)
            found = (
                await connection.execute(
                    sa.select(tables.workspace.c.id).where(tables.workspace.c.id == wanted)
                )
            ).one_or_none()
            if found is None:
                raise click.ClickException(f"no workspace {wanted}")
            return wanted
        rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
    if not rows:
        raise click.ClickException("no workspace — run `ufoctl init` first")
    if len(rows) != 1:
        raise click.ClickException(
            f"this deploy serves {len(rows)} workspaces; name one with --workspace-id"
        )
    return rows[0].id


@asynccontextmanager
async def _balance_scope(config: Config, named: str) -> AsyncIterator[tuple[AsyncConnection, UUID]]:
    """Open the one workspace a balance verb acts on, bound to it.

    The owner database is initialized first because `_target_workspace` reads across workspaces to
    find that one. Without it `owner_tx` falls back to the app pool, which on a hosted deploy is the
    RLS-subject role with no workspace pinned, and the `workspace` read raises before the verb does
    anything — the same shape `turn cancel` initializes against."""
    init_db(config.database.url)
    owner_dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if owner_dsn:
        init_owner_db(owner_dsn)
    try:
        workspace_id = await _target_workspace(named)
        with ws(workspace_id):
            async with workspace_tx() as connection:
                yield connection, workspace_id
    finally:
        await dispose_db()


async def _read_balance(config: Config, named: str) -> Balance | None:
    async with _balance_scope(config, named) as (connection, workspace_id):
        return await read_balance(connection, workspace_id)


async def _credit_balance(
    config: Config, named: str, granted_micro_usd: int, charged_micro_usd: int, reference: str
) -> bool:
    async with _balance_scope(config, named) as (connection, workspace_id):
        return await credit(
            connection, workspace_id, granted_micro_usd, charged_micro_usd, reference
        )


async def _set_reserve(config: Config, named: str, reserve_micro_usd: int) -> bool:
    async with _balance_scope(config, named) as (connection, workspace_id):
        return await set_reserve(connection, workspace_id, reserve_micro_usd)


SPEND_WINDOW_DEFAULT_SECONDS = 86_400


@main.command()
@click.option("--window-seconds", type=int, default=SPEND_WINDOW_DEFAULT_SECONDS, show_default=True)
def spend(window_seconds: int) -> None:
    """Sum the ledger over a window: the workspace total, then a per-dimension, per-member,
    per-agent, and per-price-digest breakdown — the rollups that match the ledger, the last
    attributing each burn to the rate version that priced it."""
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
    click.echo("by origin:")
    for origin in report.by_origin:
        click.echo(f"  {origin.label:<32}${origin.priced_micro_usd / MICRO_USD_PER_USD:,.6f}")
    click.echo("by price digest:")
    for entry in report.by_price_digest:
        priced = entry.priced_micro_usd / MICRO_USD_PER_USD
        click.echo(f"  {entry.price_digest}  ${priced:,.6f}")


async def _read_spend(config: Config, window_seconds: int) -> SpendReport:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            return await SpendRollup(workspace_id).read(connection, window_seconds)
    finally:
        await dispose_db()


TRANSCRIPT_READS_LIMIT = 100


@main.command(name="transcript-reads")
@click.option("--limit", type=int, default=TRANSCRIPT_READS_LIMIT, show_default=True)
def transcript_reads(limit: int) -> None:
    """List the disclosures an admin recorded to read another member's private transcript, newest
    first. This is the operator's read of that record; no member surface lists it, and every
    disclosure also emits `surface.transcript_disclosed`."""
    if limit < 1:
        raise click.ClickException("--limit must be at least 1")
    config = load_config()
    reads = asyncio.run(_read_transcript_accesses(config, limit))
    if not reads:
        click.echo("no transcript reads recorded")
        return
    for reader_email, subject_email, conversation_id, created_at in reads:
        when = created_at.strftime("%Y-%m-%d %H:%M")
        click.echo(f"{when}  {reader_email:<32}{subject_email:<32}{conversation_id}")


async def _read_transcript_accesses(
    config: Config, limit: int
) -> list[tuple[str, str, UUID, datetime]]:
    init_db(config.database.url)
    try:
        reader = tables.member.alias("reader")
        subject = tables.member.alias("subject")
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            rows = (
                await connection.execute(
                    sa.select(
                        reader.c.email.label("reader_email"),
                        subject.c.email.label("subject_email"),
                        tables.transcript_access.c.conversation_id,
                        tables.transcript_access.c.created_at,
                    )
                    .select_from(
                        tables.transcript_access.join(
                            reader, reader.c.id == tables.transcript_access.c.reader_member_id
                        ).join(
                            subject, subject.c.id == tables.transcript_access.c.subject_member_id
                        )
                    )
                    .where(tables.transcript_access.c.workspace_id == workspace_id)
                    .order_by(
                        tables.transcript_access.c.created_at.desc(),
                        tables.transcript_access.c.id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        return [(r.reader_email, r.subject_email, r.conversation_id, r.created_at) for r in rows]
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
        scope = "shared" if summary.shared else "private"
        click.echo(
            f"{summary.agent:<20}{summary.provider:<16}{summary.account_id:<28}{scope:<8}{granted}"
        )


async def _read_grants(config: Config) -> tuple[GrantSummary, ...]:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        return await workspace_grant_summaries(workspace_id)
    finally:
        await dispose_db()


@main.group()
def credential() -> None:
    """Fill and inspect the BYOK credential slots installed extensions declare, encrypted at
    rest."""


@credential.command(name="set")
@click.argument("slot")
def credential_set(slot: str) -> None:
    """Store one slot's secret — prompted hidden on a terminal, read from stdin when piped, never
    argv, never echoed."""
    config = load_config()
    declared = _declared_slots(config)
    owner = declared.get(slot)
    if owner is None:
        available = ", ".join(sorted(declared)) or "none"
        raise click.ClickException(f"unknown credential slot {slot!r} (declared: {available})")
    if slot not in _fillable_slots(config):
        raise click.ClickException(
            f"credential slot {slot!r} is written by this deploy, never entered — its value is a "
            "seal, and a typed one only refuses when the wire reads it"
        )
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise click.ClickException(f"{config.credentials.key_env} must be set to store credentials")
    value = (
        click.prompt(slot, hide_input=True) if sys.stdin.isatty() else sys.stdin.readline()
    ).strip()
    if not value:
        raise click.ClickException("empty credential value")
    asyncio.run(_write_credential(config, key, slot, value))
    click.echo(f"credential {slot} set ({owner})")


@credential.command(name="list")
def credential_list() -> None:
    """Each declared slot, its extension, and set/unset — values are never read or printed."""
    config = load_config()
    declared = _declared_slots(config)
    if not declared:
        click.echo("no credential slots declared")
        return
    stored = asyncio.run(_read_stored_slots(config))
    for name, extension in sorted(declared.items()):
        status = "set" if name in stored else "unset"
        click.echo(f"{name:<28}{extension:<20}{status}")


def _declared_slots(config: Config) -> dict[str, str]:
    try:
        manifests = load_manifests(config.pack.name)
    except RuntimeError as error:
        raise click.ClickException(str(error)) from error
    return {slot.name: manifest.name for manifest in manifests for slot in manifest.credentials}


def _fillable_slots(config: Config) -> frozenset[str]:
    """The slots an operator may type a value into — the same subset a member's private prompt is
    limited to, read from the one declaration rather than a second list to keep in step."""
    try:
        manifests = load_manifests(config.pack.name)
    except RuntimeError as error:
        raise click.ClickException(str(error)) from error
    return frozenset(
        slot.name for manifest in manifests for slot in manifest.credentials if slot.member_filled
    )


async def _write_credential(config: Config, key: str, slot: str, value: str) -> None:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        await CredentialStore(fernet=Fernet(key.encode())).put(workspace_id, slot, value)
    finally:
        await dispose_db()


async def _read_stored_slots(config: Config) -> frozenset[str]:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            rows = (
                await connection.execute(
                    sa.select(tables.credential.c.slot).where(
                        tables.credential.c.workspace_id == workspace_id
                    )
                )
            ).all()
        return frozenset(row.slot for row in rows)
    finally:
        await dispose_db()


@main.group()
def ext() -> None:
    """Search the extension store and pin installs into the deploy's lockfile."""


def _store(config: Config) -> ExtensionStore:
    if config.ext.store is None:
        raise click.ClickException("extension store not enabled (set [ext].store in ufo.toml)")
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


def _ufo_project_dir() -> Path:
    """The ufo source project `uv build` packages into the bundle's wheel — the nearest ancestor
    of this module whose ``pyproject.toml`` declares the ``ufo`` distribution. Deriving it (not
    the cwd) lets ``ufoctl bundle`` build the same wheel from any directory; a wheel-only install
    carries no source project and fails loud here."""
    for ancestor in Path(__file__).resolve().parents:
        pyproject = ancestor / "pyproject.toml"
        if not pyproject.exists():
            continue
        if tomllib.loads(pyproject.read_text()).get("project", {}).get("name") == "ufo":
            return ancestor
    raise click.ClickException("ufoctl bundle needs the ufo source project; none found upward")


@main.command()
@click.option(
    "--out", type=click.Path(path_type=Path), default=DEFAULT_BUNDLE_DIR, show_default=True
)
@click.option(
    "--client-binary",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
)
def bundle(out: Path, client_binary: Path | None) -> None:
    """Freeze this deploy into a runnable artifact: OCI image recipe, pinned config, lockfile."""
    config = load_config()
    catalog = read_catalog(config.ext.store) if config.ext.store is not None else None
    subprocess.run(
        ("uv", "build", "--wheel", str(_ufo_project_dir()), "--out-dir", str(out)),
        check=True,
    )
    wheel = out / wheel_name()
    if not wheel.exists():
        raise click.ClickException(f"wheel build produced no {wheel}")
    result = Bundle(
        config_path=config_path(),
        catalog=catalog,
        out=out,
        wheel=wheel,
        client_binary=client_binary,
    ).build()
    click.echo(
        f"bundle at {result.out} — {len(result.pins)} extension(s) pinned, wheel {wheel.name}"
    )
    for pin in result.pins:
        click.echo(f"  {pin.name} {pin.version} {pin.digest}")


@main.group(name="turn")
def turn() -> None:
    """Act on a single turn."""


@turn.command(name="cancel")
@click.argument("turn_id")
def turn_cancel(turn_id: str) -> None:
    """Cancel one turn: cancel its durable workflow, then commit its cancelled terminal.

    The operator's only end for a turn no member can end — one waiting in-turn on work that will not
    finish, or one a rollout keeps recovering without ever completing, which leaves its conversation
    silent and its queue partition held. A turn that already reached its own terminal is untouched.
    Descendants are the cancel reconciler's, as they are for every other cancel path.
    """
    config = load_config()
    cancelled = asyncio.run(_cancel_turn(config, UUID(turn_id)))
    click.echo(f"cancelled {turn_id}" if cancelled else f"{turn_id} was already terminal")


async def _cancel_turn(config: Config, turn_id: UUID) -> bool:
    """The turn's workspace is read through `owner_tx` and nothing else is: a cancel names one turn
    by id, and finding which tenant owns it is exactly the identifier that path exists to yield.
    The cancel itself runs bound to that workspace, so it goes through the same RLS every other
    write does."""
    init_db(config.database.url)
    owner_dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if owner_dsn:
        init_owner_db(owner_dsn)
    try:
        async with owner_tx() as connection:
            workspace_id = (
                await connection.execute(
                    sa.select(tables.turn.c.workspace_id).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one_or_none()
        if workspace_id is None:
            raise click.ClickException(f"no turn {turn_id}")
        client = replay_safe_client(config.database.system_url)
        with ws(workspace_id):
            return await cancel_one_turn(client, turn_id) is not None
    finally:
        await dispose_db()


@main.group()
def seed() -> None:
    """Write demonstration content into the workspace."""


@seed.command(name="kitchen-sink")
@click.option("--workspace-id", "workspace_id", default="", help="The workspace to seed.")
def seed_kitchen_sink(workspace_id: str) -> None:
    """Write one conversation holding every shape the portal draws, and print where to read it.

    A design change to the conversation surface is checked against a conversation, not a mock, and
    the same content has to come back on every run or two reviews are looking at different things.
    Each run replaces the last one.
    """
    config = load_config()
    conversation_id = asyncio.run(_seed_kitchen_sink(config, workspace_id))
    click.echo(f"/surface/web#/c/{conversation_id}")


async def _seed_kitchen_sink(config: Config, named: str) -> UUID:
    """The engine lifecycle around one seed: the owner pool first, because `_target_workspace`
    reads across workspaces to resolve the one named — through the app pool alone a hosted deploy's
    RLS-subject role has no workspace pinned and the read raises before the verb does anything."""
    init_db(config.database.url)
    owner_dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if owner_dsn:
        init_owner_db(owner_dsn)
    try:
        return await _seed_target(WorkspaceBlobStore(backend=blob_store_for(config.blob)), named)
    finally:
        await dispose_db()


async def _seed_target(blob: WorkspaceBlobStore, named: str) -> UUID:
    workspace_id = await _target_workspace(named)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            agent_id = (
                (
                    await connection.execute(
                        sa.select(tables.agent.c.id).where(
                            tables.agent.c.workspace_id == workspace_id,
                            tables.agent.c.is_main,
                        )
                    )
                )
                .scalars()
                .one()
            )
            member = (
                await connection.execute(
                    sa.select(tables.member.c.id, tables.member.c.email)
                    .where(tables.member.c.workspace_id == workspace_id)
                    .order_by(tables.member.c.created_at)
                )
            ).first()
        if member is None:
            raise click.ClickException("no member — run `ufoctl init` first")
        return await KitchenSink(
            blob=blob,
            workspace_id=workspace_id,
            agent_id=agent_id,
            member_id=member.id,
            email=member.email,
        ).write()
