"""The ufoctl CLI: init, serve, portal, debugger, ext, bundle, and each active extension's
commands."""

import asyncio
import json
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
from pydantic import BaseModel, ValidationError

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
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.interface import (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.harness.models.pricing import MICRO_USD_PER_USD
from ufo.harness.models.registry import model_registry
from ufo.harness.sandbox.ingress_serve import run as ingress_run
from ufo.host.ext.loader import load_manifests, lockfile_path
from ufo.host.ext.store import ExtensionStore, read_catalog
from ufo.onboard.onboarding import AlreadyInitialized, Onboarding
from ufo.proxy_serve import OWNER_DSN_ENV
from ufo.runtime.access.credentials import CredentialStore, deploy_env, member_slot
from ufo.runtime.access.grants import GrantSummary, workspace_grant_summaries
from ufo.runtime.billing.accounting import SpendReport, SpendRollup
from ufo.runtime.ext.deploy import CommandRefused, CommandSpec, DeployContext
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.ext.operator import DEBUGGER_SURFACE
from ufo.runtime.ext.surface import TurnStep
from ufo.runtime.provisioning import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_AGENT_REASONING,
    Provisioned,
    Provisioning,
)
from ufo.runtime.seats import email_domain
from ufo.runtime.steps import DurableTurnSteps
from ufo.runtime.turns.cancellation import cancel_one_turn
from ufo.runtime.workspace import (
    MEMBER_ROUTED_SLOTS,
    NoWorkspace,
    SeveralWorkspaces,
    init_workspace_credentials,
    sole_workspace,
    ws,
)
from ufo.schema import tables
from ufo.schema.records import (
    DEFAULT_AGENT_NAME,
    ReasoningEffort,
)
from ufo.serve import FLEETS, WHOLE_FLEET, declared_flags, deploy_spend, home_surface
from ufo.serve import run as serve_run

UFOCTL_DIR_ENV = "UFOCTL_DIR"
RESERVED_DOTENV_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
PORTAL_REACH_TIMEOUT_SECONDS = 5.0
HANDOFF_PATH_BYTES = 24
REASONING_EFFORTS = ("auto", "off", "low", "medium", "high")
LOOPBACK = "127.0.0.1"
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

[flags]
backend = "open"
"""
CORE_VERSIONS_DIR = MIGRATIONS_DIR / "versions"
MIGRATION_HEAD_FILENAME = "HEAD"
MIGRATION_STAMP = "%Y%m%d%H%M%S"
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
    """Other tools reading `.env` pick up the bare provider-key names, so ufo's keys live under
    their `UFO_`-prefixed names."""
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


class Ufoctl(click.Group):
    """`ufoctl`'s own verbs, then `ufoctl <extension> <name>` for each command an active extension
    declares. `.env` loads before any verb resolves: resolving an extension's command loads the
    manifests, and a manifest may read the environment."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        _load_dotenv()
        return super().parse_args(ctx, args)

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted({*super().list_commands(ctx), *self._extension_groups(ctx)})

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        return super().get_command(ctx, cmd_name) or self._extension_groups(ctx).get(cmd_name)

    def _extension_groups(self, ctx: click.Context) -> dict[str, click.Group]:
        if not config_path().exists():
            return {}
        own = set(super().list_commands(ctx))
        config = load_config()
        try:
            manifests = load_manifests(config.pack.name)
        except RuntimeError as error:
            raise click.ClickException(str(error)) from error
        groups: dict[str, click.Group] = {}
        for manifest in manifests:
            if not manifest.commands:
                continue
            if manifest.name in own:
                raise click.ClickException(
                    f"extension {manifest.name!r} declares commands under a ufoctl verb's name"
                )
            groups[manifest.name] = click.Group(
                name=manifest.name,
                help=f"Commands of the {manifest.name} extension.",
                commands=[
                    _command(config, manifests, manifest, spec) for spec in manifest.commands
                ],
            )
        return groups


def _command(
    config: Config, manifests: tuple[Manifest, ...], manifest: Manifest, spec: CommandSpec
) -> click.Command:
    def run(**given: str | None) -> None:
        try:
            params = spec.params.model_validate(
                {field: value for field, value in given.items() if value is not None}
            )
        except ValidationError as error:
            raise click.UsageError(
                "; ".join(
                    f"--{'.'.join(str(part) for part in failure['loc']).replace('_', '-')}: "
                    f"{failure['msg']}"
                    for failure in error.errors()
                )
            ) from error
        click.echo(asyncio.run(_run_command(config, manifests, manifest, spec, params)))

    return click.Command(
        name=spec.name,
        help=spec.help,
        callback=run,
        params=[
            click.Option(
                [f"--{field.replace('_', '-')}"],
                required=info.is_required(),
                help=info.description,
            )
            for field, info in spec.params.model_fields.items()
        ],
    )


async def _run_command(
    config: Config,
    manifests: tuple[Manifest, ...],
    manifest: Manifest,
    spec: CommandSpec,
    params: BaseModel,
) -> str:
    init_db(config.database.url)
    try:
        owner_dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
        if owner_dsn:
            init_owner_db(owner_dsn)
        key = os.environ.get(config.credentials.key_env)
        if key:
            init_workspace_credentials(CredentialStore(fernet=Fernet(key.encode())))
        context = DeployContext(
            extension=manifest.name,
            route=f"ufoctl {manifest.name} {spec.name}",
            blob=WorkspaceBlobStore(backend=blob_store_for(config.blob)),
            provisioning=Provisioning(
                founded=tuple(
                    founded for active in manifests for founded in active.workspace_founded
                )
            ),
            flag_backend=config.flags.backend,
            flag_keys=frozenset(declared_flags(manifests)),
        )
        return await spec.run(context, params)
    except CommandRefused as refusal:
        raise click.ClickException(str(refusal)) from refusal
    finally:
        await dispose_db()


@click.group(cls=Ufoctl)
def main() -> None:
    """An agent runtime you can run, read, and extend."""


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
    default=DEFAULT_AGENT_REASONING,
    show_default=True,
)
@click.option(
    "--member-model-provider",
    type=click.Choice((PROVIDER_ANTHROPIC, PROVIDER_OPENAI)),
    help="Store this provider's environment key for the initial member.",
)
def init(
    email: str,
    model: str,
    reasoning: ReasoningEffort,
    member_model_provider: str | None,
) -> None:
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
        onboarded = asyncio.run(_onboard(config, email, model, reasoning, member_model_provider))
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


async def _onboard(
    config: Config,
    email: str,
    model: str,
    reasoning: ReasoningEffort,
    member_model_provider: str | None,
) -> Provisioned:
    init_db(config.database.url)
    try:
        key = os.environ.get(config.credentials.key_env)
        credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
        manifests = load_manifests(config.pack.name)
        spend, ledger = deploy_spend(
            manifests, model_registry(config, manifests), config.connect.public_base_url
        )
        onboarding = Onboarding(
            config=config,
            email=email,
            model=model,
            reasoning=reasoning,
            credentials=credentials,
            manifests=manifests,
            spend=spend,
            ledger=ledger,
        )
        member_model_key: tuple[str, str] | None = None
        if member_model_provider is not None:
            env_name = (
                config.models.anthropic_api_key_env
                if member_model_provider == PROVIDER_ANTHROPIC
                else config.models.openai_api_key_env
            )
            value = deploy_env(env_name)
            if not value:
                raise RuntimeError(
                    f"member model provider {member_model_provider!r} needs UFO_{env_name} "
                    f"(or {env_name})"
                )
            slot = next(
                slot
                for slot, provider in MEMBER_ROUTED_SLOTS.items()
                if provider == member_model_provider
            )
            member_model_key = slot, value
        onboarded = await onboarding.create()
        if member_model_key is not None:
            if credentials is None:
                raise RuntimeError(
                    f"storing a member model key requires {config.credentials.key_env}"
                )
            slot, value = member_model_key
            await credentials.put(
                onboarded.workspace_id, member_slot(slot, onboarded.member_id), value
            )
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
    target = CORE_VERSIONS_DIR / f"{stamp}_{slug}.py"
    target.write_text(MIGRATION_TEMPLATE.format(revision=stamp, down_revision=head))
    click.echo(f"wrote {target} — revision {stamp}, down_revision {head}")
    (CORE_VERSIONS_DIR / MIGRATION_HEAD_FILENAME).write_text(f"{stamp}\n")


@main.command()
@click.option(
    "--fleet",
    type=click.Choice(sorted(FLEETS)),
    default=WHOLE_FLEET.name,
    show_default=True,
    help=(
        "The durable work this process claims: turns and their surfaces, background jobs, routes "
        "and recurring job ticks (api), or all."
    ),
)
def serve(fleet: str) -> None:
    """Run surfaces, workers, jobs, and the egress-control RPC the Rust proxy calls."""
    config = load_config()
    surface = home_surface(load_manifests(config.pack.name))
    if surface is not None:
        click.echo(
            f"portal {_serve_base(config)}/surface/{surface} — "
            "run `ufoctl portal` to open a session in your browser"
        )
    serve_run(FLEETS[fleet])


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


@main.command()
def debugger() -> None:
    """Open the session debugger in a browser, signed in with this machine's CLI token."""
    config = load_config()
    surfaces = {
        spec.name for manifest in load_manifests(config.pack.name) for spec in manifest.surfaces
    }
    if DEBUGGER_SURFACE not in surfaces:
        raise click.ClickException(f"pack {config.pack.name!r} installs no debugger")
    token_path = _ufoctl_dir() / "token"
    if not token_path.exists():
        raise click.ClickException("no CLI token — run `ufoctl init` first")
    token = token_path.read_text().strip()
    base = _serve_base(config)
    debugger_url = f"{base}/surface/{DEBUGGER_SURFACE}"
    try:
        page = httpx.get(
            debugger_url,
            headers={"authorization": f"Bearer {token}"},
            follow_redirects=False,
            timeout=PORTAL_REACH_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        raise click.ClickException(
            f"serve is not answering at {base} ({error}) — run `ufoctl serve` first"
        ) from error
    if page.status_code != httpx.codes.OK:
        raise click.ClickException(f"{debugger_url} answered {page.status_code}: {page.text}")
    BrowserHandoff(portal_url=debugger_url, token=token).open()
    click.echo(f"opened {debugger_url}")


def _serve_base(config: Config) -> str:
    """A cookie is host-only and every absolute link the deploy mints names `public_base_url`, so
    a session opened on the bind address cannot follow them."""
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


WORKSPACE_ID_OPTION = click.option(
    "--workspace-id", default="", help="The workspace to act on; omit on a single-workspace deploy."
)


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
@WORKSPACE_ID_OPTION
def spend_cap_set(
    scope: str,
    subject_id: str,
    window_seconds: int,
    limit_micro_usd: int,
    on_breach: str,
    workspace_id: str,
) -> None:
    """Create or update a spend cap; raising a cap lets the sweep re-admit its parked turns."""
    if scope == "workspace" and subject_id:
        raise click.ClickException("workspace scope takes no --subject-id")
    if scope != "workspace" and not subject_id:
        raise click.ClickException(f"{scope} scope requires --subject-id")
    subject = UUID(subject_id) if subject_id else None
    config = load_config()
    cap_id = asyncio.run(
        _write_spend_cap(
            config, workspace_id, scope, subject, window_seconds, limit_micro_usd, on_breach
        )
    )
    dollars = limit_micro_usd / MICRO_USD_PER_USD
    click.echo(f"spend cap {cap_id} — {scope} ${dollars:,.2f} / {window_seconds}s ({on_breach})")


@spend_cap.command(name="list")
@WORKSPACE_ID_OPTION
def spend_cap_list(workspace_id: str) -> None:
    """Show the workspace's spend caps."""
    config = load_config()
    caps = asyncio.run(_read_spend_caps(config, workspace_id))
    if not caps:
        click.echo("no spend caps set")
        return
    for cap_id, scope, subject, window_seconds, limit_micro_usd, on_breach in caps:
        dollars = limit_micro_usd / MICRO_USD_PER_USD
        target = f" {subject}" if subject is not None else ""
        click.echo(f"{cap_id}  {scope}{target}  ${dollars:,.2f} / {window_seconds}s  {on_breach}")


async def _write_spend_cap(
    config: Config,
    named: str,
    scope: str,
    subject: UUID | None,
    window_seconds: int,
    limit_micro_usd: int,
    on_breach: str,
) -> UUID:
    async with _workspace_scope(config, named) as workspace_id:
        async with workspace_tx() as connection:
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
                    .where(
                        tables.spend_cap.c.workspace_id == workspace_id,
                        tables.spend_cap.c.id == existing.id,
                    )
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


async def _read_spend_caps(
    config: Config, named: str
) -> list[tuple[UUID, str, UUID | None, int, int, str]]:
    async with _workspace_scope(config, named) as workspace_id:
        async with workspace_tx() as connection:
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


async def _target_workspace(named: str) -> UUID:
    """The workspace a command acts on: the one named, else the deploy's only one. A deploy
    serving several takes the option that names one rather than a guess."""
    if not named:
        try:
            return await sole_workspace()
        except NoWorkspace as error:
            raise click.ClickException(str(error)) from error
        except SeveralWorkspaces as error:
            raise click.ClickException(f"{error}; name one with --workspace-id") from error
    wanted = UUID(named)
    async with owner_tx() as connection:
        found = (
            await connection.execute(
                sa.select(tables.workspace.c.id).where(tables.workspace.c.id == wanted)
            )
        ).one_or_none()
    if found is None:
        raise click.ClickException(f"no workspace {wanted}")
    return wanted


@asynccontextmanager
async def _workspace_scope(config: Config, named: str) -> AsyncIterator[UUID]:
    """Without `init_db` first, `owner_tx` falls back to the app pool, which on an RLS deploy pins
    no workspace, and the cross-workspace `workspace` read raises."""
    init_db(config.database.url)
    owner_dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    if owner_dsn:
        init_owner_db(owner_dsn)
    try:
        workspace_id = await _target_workspace(named)
        with ws(workspace_id):
            yield workspace_id
    finally:
        await dispose_db()


SPEND_WINDOW_DEFAULT_SECONDS = 86_400


@main.command()
@click.option("--window-seconds", type=int, default=SPEND_WINDOW_DEFAULT_SECONDS, show_default=True)
@WORKSPACE_ID_OPTION
def spend(window_seconds: int, workspace_id: str) -> None:
    """Sum the ledger over a window: the workspace total, then a per-dimension, per-service,
    per-member, per-agent, and per-price-digest breakdown — the rollups that match the ledger, the
    last attributing each burn to the rate version that priced it."""
    config = load_config()
    report = asyncio.run(_read_spend(config, workspace_id, window_seconds))
    total = report.total_micro_usd / MICRO_USD_PER_USD
    click.echo(f"spend · last {window_seconds / 3600:g}h · ${total:,.6f}")
    for dim in report.by_dimension:
        priced = dim.priced_micro_usd / MICRO_USD_PER_USD
        click.echo(f"  {dim.dimension:<10}{dim.amount:>14,}  ${priced:,.6f}")
    click.echo("by service:")
    for service in report.by_service:
        click.echo(f"  {service.service:<32}${service.priced_micro_usd / MICRO_USD_PER_USD:,.6f}")
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


async def _read_spend(config: Config, named: str, window_seconds: int) -> SpendReport:
    async with _workspace_scope(config, named) as workspace_id, workspace_tx() as connection:
        return await SpendRollup(workspace_id).read(connection, window_seconds)


TRANSCRIPT_READS_LIMIT = 100


@main.command(name="transcript-reads")
@click.option("--limit", type=int, default=TRANSCRIPT_READS_LIMIT, show_default=True)
@WORKSPACE_ID_OPTION
def transcript_reads(limit: int, workspace_id: str) -> None:
    """List the disclosures an admin recorded to read another member's private transcript, newest
    first. This is the operator's read of that record; no member surface lists it, and every
    disclosure also emits `surface.transcript_disclosed`."""
    if limit < 1:
        raise click.ClickException("--limit must be at least 1")
    config = load_config()
    reads = asyncio.run(_read_transcript_accesses(config, workspace_id, limit))
    if not reads:
        click.echo("no transcript reads recorded")
        return
    for reader_email, subject_email, conversation_id, created_at in reads:
        when = created_at.strftime("%Y-%m-%d %H:%M")
        click.echo(f"{when}  {reader_email:<32}{subject_email:<32}{conversation_id}")


async def _read_transcript_accesses(
    config: Config, named: str, limit: int
) -> list[tuple[str, str, UUID, datetime]]:
    reader = tables.member.alias("reader")
    subject = tables.member.alias("subject")
    async with _workspace_scope(config, named) as workspace_id:
        async with workspace_tx() as connection:
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


@main.command()
@WORKSPACE_ID_OPTION
def grants(workspace_id: str) -> None:
    """List the OAuth accounts granted to each agent (granted in chat via connect_account)."""
    config = load_config()
    summaries = asyncio.run(_read_grants(config, workspace_id))
    if not summaries:
        click.echo("no grants")
        return
    for summary in summaries:
        granted = summary.granted_at.strftime("%Y-%m-%d")
        scope = "shared" if summary.shared else "private"
        click.echo(
            f"{summary.agent:<20}{summary.provider:<16}{summary.account_id:<28}{scope:<8}{granted}"
        )


async def _read_grants(config: Config, named: str) -> tuple[GrantSummary, ...]:
    async with _workspace_scope(config, named) as workspace_id:
        return await workspace_grant_summaries(workspace_id)


@main.group()
def credential() -> None:
    """Fill and inspect the BYOK credential slots installed extensions declare, encrypted at
    rest."""


@credential.command(name="set")
@click.argument("slot")
@WORKSPACE_ID_OPTION
def credential_set(slot: str, workspace_id: str) -> None:
    """Store one slot's secret — prompted hidden on a terminal, read from stdin when piped, never
    argv, never echoed."""
    config = load_config()
    declared = _declared_slots(config)
    owner = declared.get(slot)
    if owner is None:
        available = ", ".join(sorted(declared)) or "none"
        raise click.ClickException(f"unknown credential slot {slot!r} (declared: {available})")
    key = os.environ.get(config.credentials.key_env)
    if not key:
        raise click.ClickException(f"{config.credentials.key_env} must be set to store credentials")
    value = (
        click.prompt(slot, hide_input=True) if sys.stdin.isatty() else sys.stdin.readline()
    ).strip()
    if not value:
        raise click.ClickException("empty credential value")
    asyncio.run(_write_credential(config, workspace_id, key, slot, value))
    click.echo(f"credential {slot} set ({owner})")


@credential.command(name="list")
@WORKSPACE_ID_OPTION
def credential_list(workspace_id: str) -> None:
    """Each declared slot, its extension, and set/unset — values are never read or printed."""
    config = load_config()
    declared = _declared_slots(config)
    if not declared:
        click.echo("no credential slots declared")
        return
    stored = asyncio.run(_read_stored_slots(config, workspace_id))
    for name, extension in sorted(declared.items()):
        status = "set" if name in stored else "unset"
        click.echo(f"{name:<28}{extension:<20}{status}")


def _declared_slots(config: Config) -> dict[str, str]:
    try:
        manifests = load_manifests(config.pack.name)
    except RuntimeError as error:
        raise click.ClickException(str(error)) from error
    return {slot.name: manifest.name for manifest in manifests for slot in manifest.credentials}


async def _write_credential(config: Config, named: str, key: str, slot: str, value: str) -> None:
    async with _workspace_scope(config, named) as workspace_id:
        await CredentialStore(fernet=Fernet(key.encode())).put(workspace_id, slot, value)


async def _read_stored_slots(config: Config, named: str) -> frozenset[str]:
    async with _workspace_scope(config, named) as workspace_id:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.credential.c.slot).where(
                        tables.credential.c.workspace_id == workspace_id
                    )
                )
            ).all()
        return frozenset(row.slot for row in rows)


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
    """A wheel-only install carries no source project and fails here. `uv build --all-packages`
    walks up to the workspace root from this project."""
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
    required=True,
)
def bundle(out: Path, client_binary: Path) -> None:
    """Freeze this deploy into a runnable artifact: image recipe, client, config, lockfile."""
    config = load_config()
    catalog = read_catalog(config.ext.store) if config.ext.store is not None else None
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.whl"):
        stale.unlink()
    subprocess.run(
        (
            "uv",
            "build",
            "--wheel",
            "--all-packages",
            str(_ufo_project_dir()),
            "--out-dir",
            str(out),
        ),
        check=True,
    )
    wheels = tuple(sorted(out.glob("*.whl")))
    if out / wheel_name() not in wheels:
        raise click.ClickException(f"wheel build produced no {out / wheel_name()}")
    constraints = subprocess.run(
        (
            "uv",
            "export",
            "--frozen",
            "--all-packages",
            "--no-dev",
            "--no-hashes",
            "--no-emit-workspace",
            "--project",
            str(_ufo_project_dir()),
        ),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result = Bundle(
        config_path=config_path(),
        catalog=catalog,
        out=out,
        wheels=wheels,
        client_binary=client_binary,
        constraints=constraints,
    ).build()
    names = ", ".join(wheel.name for wheel in wheels)
    click.echo(f"bundle at {result.out} — {len(result.pins)} extension(s) pinned, wheels {names}")
    for pin in result.pins:
        click.echo(f"  {pin.name} {pin.version} {pin.digest}")


@main.group(name="turn")
def turn() -> None:
    """Act on a single turn."""


@turn.command(name="cancel")
@click.argument("turn_id")
@click.option("--workspace-id", default="")
def turn_cancel(turn_id: str, workspace_id: str) -> None:
    """Cancel one turn: cancel its durable workflow, then commit its cancelled terminal.

    The operator's only end for a turn no member can end — one waiting in-turn on work that will not
    finish, or one a rollout keeps recovering without ever completing, which leaves its conversation
    silent and its queue partition held. A turn that already reached its own terminal is untouched.
    Descendants are the cancel reconciler's, as they are for every other cancel path.
    """
    config = load_config()
    cancelled = asyncio.run(_cancel_turn(config, UUID(turn_id), workspace_id))
    click.echo(f"cancelled {turn_id}" if cancelled else f"{turn_id} was already terminal")


async def _cancel_turn(config: Config, turn_id: UUID, named_workspace: str) -> bool:
    init_db(config.database.url)
    owner_dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    try:
        if named_workspace:
            workspace_id = UUID(named_workspace)
        else:
            if owner_dsn is None:
                raise click.ClickException(
                    "owner database is unavailable; name the turn's workspace with --workspace-id"
                )
            init_owner_db(owner_dsn)
            async with owner_tx() as connection:
                resolved_workspace = (
                    await connection.execute(
                        sa.select(tables.turn.c.workspace_id).where(tables.turn.c.id == turn_id)
                    )
                ).scalar_one_or_none()
            if resolved_workspace is None:
                raise click.ClickException(f"no turn {turn_id}")
            workspace_id = resolved_workspace
        with ws(workspace_id):
            if named_workspace:
                async with workspace_tx() as connection:
                    found = (
                        await connection.execute(
                            sa.select(tables.turn.c.id).where(
                                tables.turn.c.workspace_id == workspace_id,
                                tables.turn.c.id == turn_id,
                            )
                        )
                    ).scalar_one_or_none()
                if found is None:
                    raise click.ClickException(f"no turn {turn_id} in workspace {workspace_id}")
            client = replay_safe_client(config.database.system_url)
            return await cancel_one_turn(client, turn_id) is not None
    finally:
        await dispose_db()


@turn.command(name="steps")
@click.argument("turn_id")
@click.option("--workspace-id", default="")
def turn_steps(turn_id: str, workspace_id: str) -> None:
    """Print one turn's durable trajectory from the DBOS step log.

    The recorded record survives however the turn ended — a turn cancelled or wedged with no
    written answer still holds every model round and tool result it completed, uncompacted, which
    the conversation transcript (the compacted window a later turn reads) no longer shows. This is
    the operator's read of what a turn actually did."""
    config = load_config()
    asyncio.run(_print_turn_steps(config, UUID(turn_id), workspace_id))


async def _print_turn_steps(config: Config, turn_id: UUID, named_workspace: str) -> None:
    init_db(config.database.url)
    owner_dsn = os.environ.get(OWNER_DSN_ENV) or config.database.owner_url
    try:
        if named_workspace:
            workspace_id = UUID(named_workspace)
        else:
            if owner_dsn is None:
                raise click.ClickException(
                    "owner database is unavailable; name the turn's workspace with --workspace-id"
                )
            init_owner_db(owner_dsn)
            async with owner_tx() as connection:
                resolved = (
                    await connection.execute(
                        sa.select(tables.turn.c.workspace_id).where(tables.turn.c.id == turn_id)
                    )
                ).scalar_one_or_none()
            if resolved is None:
                raise click.ClickException(f"no turn {turn_id}")
            workspace_id = resolved
        with ws(workspace_id):
            async with workspace_tx() as connection:
                found = (
                    await connection.execute(
                        sa.select(tables.turn.c.running_attempt).where(
                            tables.turn.c.workspace_id == workspace_id,
                            tables.turn.c.id == turn_id,
                        )
                    )
                ).one_or_none()
            if found is None:
                raise click.ClickException(f"no turn {turn_id} in workspace {workspace_id}")
            client = replay_safe_client(config.database.system_url)
            steps = await DurableTurnSteps(client=client).read(
                found.running_attempt or str(turn_id)
            )
        _echo_turn_steps(steps)
    finally:
        await dispose_db()


def _echo_turn_steps(steps: tuple[TurnStep, ...]) -> None:
    if not steps:
        click.echo("no recorded steps")
        return
    for step in steps:
        span = "" if step.duration_ms is None else f"  {step.duration_ms}ms"
        click.echo(f"{step.number:>3}. [{step.kind}] {step.name}  {step.function_name}{span}")
        for message in step.messages:
            content = message.content
            if isinstance(content, str):
                click.echo(f"       {message.role}: {content}")
                continue
            for block in content:
                click.echo(f"       {message.role}: {_echo_block(block)}")


def _echo_block(block: object) -> str:
    match block:
        case TextBlock(text=text):
            return text
        case ToolUseBlock(name=name, input=arguments):
            return f"call {name} {json.dumps(arguments, separators=(',', ':'))}"
        case ToolResultBlock(content=result, is_error=is_error):
            body = result if isinstance(result, str) else "[non-text result]"
            return f"result{' (error)' if is_error else ''}: {body}"
        case _:
            return type(block).__name__
