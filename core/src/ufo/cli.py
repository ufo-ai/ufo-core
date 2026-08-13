"""The ufoctl CLI: init, serve, portal, chat, ext, bundle."""

import asyncio
import os
import secrets
import subprocess
import sys
import threading
import tomllib
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TextIO
from uuid import UUID, uuid4

import asyncpg
import click
import httpx
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.accounting import SpendReport, SpendRollup
from ufo.bearer import UFO_TOKEN_SECRET_ENV, mint_token
from ufo.bundle import Bundle, wheel_name
from ufo.cancellation import cancel_one_turn
from ufo.config import Config, config_path, load_config
from ufo.credentials import CredentialStore
from ufo.db import apply_migrations, dispose_db, init_db, init_owner_db, owner_tx, workspace_tx
from ufo.durability import replay_safe_client
from ufo.ext.loader import load_manifests, lockfile_path
from ufo.ext.store import ExtensionStore, read_catalog
from ufo.grants import GrantSummary, workspace_grant_summaries
from ufo.ingress_serve import run as ingress_run
from ufo.onboarding import DEFAULT_AGENT_MODEL, AlreadyInitialized, Onboarded, Onboarding
from ufo.proxy_serve import OWNER_DSN_ENV
from ufo.proxy_serve import run as proxy_run
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME
from ufo.seats import email_domain
from ufo.serve import home_surface
from ufo.serve import run as serve_run
from ufo.workspace import ws

UFOCTL_DIR_ENV = "UFOCTL_DIR"
TURN_REQUEST_TIMEOUT_SECONDS = 90.0
PORTAL_REACH_TIMEOUT_SECONDS = 5.0
HANDOFF_PATH_BYTES = 24
LOOPBACK = "127.0.0.1"
ERASE_LINE = "\r\x1b[K"
MICRO_USD_PER_USD = 1_000_000
CLI_TOKEN_TTL = timedelta(days=3650)
UFO_CHANNEL_PREFIX = "/surface/ufo"
SECRET_HEADER = "x-ufo-secret"
SECRET_SLOT_HEADER = "x-ufo-slot"
SINCE_HEADER = "x-ufo-since"
POLL_FALLBACK_SECONDS = 1.0
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
search_provider = "exa"

[connect]
public_base_url = "http://localhost:8710"
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
    """Load the `.env` beside the config file into the environment before any verb reads a key, so
    exported secrets and `ufoctl init`'s minted dev secrets both arrive with no manual `export` —
    the smooth local default. An already-set var wins (an explicit `export` overrides the file), so
    this only fills what is unset."""
    dotenv = _dotenv_path()
    if not dotenv.exists():
        return
    for name, value in _dotenv_pairs(dotenv.read_text()):
        os.environ.setdefault(name, value)


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
def init(email: str, model: str) -> None:
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
        onboarded = asyncio.run(_onboard(config, email, model))
    except (AlreadyInitialized, ValueError, RuntimeError) as error:
        raise click.ClickException(str(error)) from error
    token = mint_token(secret, str(onboarded.workspace_id), email, CLI_TOKEN_TTL)
    ufoctl_dir = _ufoctl_dir()
    ufoctl_dir.mkdir(mode=0o700, exist_ok=True)
    token_path = ufoctl_dir / "token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    click.echo(f"workspace ready — owner {email}, agent {DEFAULT_AGENT_NAME!r} ({model})")
    click.echo(f"cli token written to {token_path}")


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


async def _onboard(config: Config, email: str, model: str) -> Onboarded:
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
    cluster migrate Job injects the owner DSN as `UFO_OWNER_DSN` — mirroring `ufoctl proxy` — while
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


@main.command()
def serve() -> None:
    """Run surfaces, workers, and jobs; embed the egress proxy for single-node config."""
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
def proxy() -> None:
    """Run the shared egress proxy: one service fronting every workspace sandbox."""
    proxy_run()


@main.command()
def ingress() -> None:
    """Run the sandbox ingress: a token-gated reverse proxy to conversations' sandbox ports."""
    ingress_run()


@main.command()
@click.argument("message", required=False)
@click.option("--new", is_flag=True, help="Start a fresh conversation session.")
def chat(message: str | None, new: bool) -> None:
    """Talk to the agent; the session continues across invocations."""
    config = load_config()
    token_path = _ufoctl_dir() / "token"
    if not token_path.exists():
        raise click.ClickException("no CLI token — run `ufoctl init` first")
    token = token_path.read_text().strip()
    channel = _session(new)
    if message is not None:
        _run_turn(config, token, channel, message)
        return
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            click.echo()
            return
        if line.strip():
            _run_turn(config, token, channel, line)


def _session(new: bool) -> str:
    ufoctl_dir = _ufoctl_dir()
    path = ufoctl_dir / "session"
    if new or not path.exists():
        ufoctl_dir.mkdir(mode=0o700, exist_ok=True)
        path.write_text(uuid4().hex)
    return path.read_text().strip()


def _run_turn(config: Config, token: str, channel: str, message: str) -> None:
    """One turn against the shared `ufo` surface. The surface has no cancel, so a Ctrl-C or a
    dropped connection ends the client cleanly while the turn finishes on the fleet — the next
    message (an empty body first) resumes tailing the conversation's latest turn to catch up."""
    base = f"http://{config.serve.host}:{config.serve.port}"
    try:
        asyncio.run(_stream_turn(base, token, channel, message))
    except KeyboardInterrupt:
        click.echo(
            "\n(stopped — the turn finishes in the background; send another message to catch up)"
        )
    except httpx.HTTPError as error:
        raise click.ClickException(
            f"lost connection to serve ({error}) — retry to catch up"
        ) from error


@dataclass
class _Pending:
    """What a held stream ended on: a `poll` reconnect after its seconds, credential prompts to
    fulfill, or neither — the turn is done. `since` is where the stream got to, carried into the
    reconnect so the tail resumes after the last frame rendered rather than replaying the turn."""

    poll_seconds: float | None = None
    since: str = ""
    secrets: list[tuple[str, str, str]] = field(default_factory=list)


async def _stream_turn(base: str, token: str, channel: str, message: str) -> None:
    """Open the client and drive one turn on the shared `ufo` surface through `_ChatStream`."""
    display = _TurnDisplay(
        out=sys.stdout, err=sys.stderr, tty=sys.stdout.isatty() and sys.stderr.isatty()
    )
    headers = {"authorization": f"Bearer {token}", "content-type": "text/plain"}
    path = f"{UFO_CHANNEL_PREFIX}/{channel}"
    async with httpx.AsyncClient(base_url=base, timeout=TURN_REQUEST_TIMEOUT_SECONDS) as client:
        await _ChatStream(client=client, path=path, headers=headers, display=display).run(message)


@dataclass(frozen=True)
class _ChatStream:
    """One turn on the shared `ufo` surface: POST the message to the member's channel and render the
    held directive stream, reconnecting with an empty body on `poll` until the turn caps
    (`ask`/`exit`), then fulfilling any credential prompts it asked for."""

    client: httpx.AsyncClient
    path: str
    headers: dict[str, str]
    display: "_TurnDisplay"

    async def run(self, message: str) -> None:
        body = message
        since = ""
        while True:
            pending = await self._drain(body, since)
            if pending.poll_seconds is not None:
                await asyncio.sleep(pending.poll_seconds)
                body = ""
                since = pending.since
                continue
            self.display.close()
            for sealed, slot, prompt in pending.secrets:
                await self._fulfill_secret(sealed, slot, prompt)
            return

    async def _drain(self, body: str, since: str = "") -> _Pending:
        """Render one held stream and report how it ended. `txt`/`say`/`note`/`status`/`file`
        render; `secret` collects a prompt to fulfill after the turn; `since` names where this
        stream got to, for the reconnect to resume from; `poll` asks for an empty-body reconnect;
        `ask` and `exit` are the terminal directives the stream closes on; any other verb fails
        loud."""
        pending = _Pending()
        headers = {**self.headers, **({SINCE_HEADER: since} if since else {})}
        async with self.client.stream(
            "POST", self.path, content=body.encode(), headers=headers
        ) as stream:
            if stream.status_code != 200:
                detail = (await stream.aread()).decode().strip()
                raise click.ClickException(f"chat failed ({stream.status_code}): {detail}")
            async for raw in stream.aiter_lines():
                if not raw:
                    continue
                verb, *fields = (_unescape(part) for part in raw.split("\t"))
                match verb:
                    case "txt":
                        self.display.text(fields[0] if fields else "")
                    case "say":
                        self.display.line(fields[0] if fields else "")
                    case "you":
                        self.display.line(f"\u203a {fields[0] if fields else ''}")
                    case "note":
                        self.display.activity(fields[0] if fields else "")
                    case "status":
                        self.display.meter(fields[0] if fields else "")
                    case "file" if len(fields) == 3:
                        self.display.shared_file(fields[0], fields[1], fields[2])
                    case "secret" if len(fields) == 3:
                        pending.secrets.append((fields[0], fields[1], fields[2]))
                    case "since" if len(fields) == 2:
                        pending.since = f"{fields[0]}:{fields[1]}"
                    case "poll":
                        try:
                            pending.poll_seconds = (
                                float(fields[0]) if fields else POLL_FALLBACK_SECONDS
                            )
                        except ValueError:
                            raise click.ClickException(
                                f"unexpected directive from serve: {raw!r}"
                            ) from None
                    case "ask" | "exit":
                        pass
                    case _:
                        raise click.ClickException(f"unexpected directive from serve: {raw!r}")
        return pending

    async def _fulfill_secret(self, sealed: str, slot: str, prompt: str) -> None:
        """Enter one credential value privately and hand it to the surface out of band — never a
        message, so nothing reaches the transcript. The surface answers with a `say`
        acknowledgement."""
        value = click.prompt(prompt, hide_input=True)
        response = await self.client.post(
            self.path,
            content=value.encode(),
            headers={**self.headers, SECRET_HEADER: sealed, SECRET_SLOT_HEADER: slot},
        )
        if response.status_code != 200:
            raise click.ClickException(
                f"could not store {slot} ({response.status_code}): {response.text.strip()}"
            )
        for raw in response.text.splitlines():
            verb, *fields = (_unescape(part) for part in raw.split("\t"))
            if verb == "say" and fields:
                self.display.line(fields[0])


_DIRECTIVE_ESCAPES = {"\\": "\\", "t": "\t", "n": "\n"}


def _unescape(text: str) -> str:
    r"""Reverse the `ufo` surface's directive escaping: `\t`→tab, `\n`→newline, `\\`→backslash."""
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(_DIRECTIVE_ESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


@dataclass
class _TurnDisplay:
    """One turn's terminal rendering, driven by `ufo` surface directives: `txt` deltas stream to
    stdout, `say` and `note` land finished lines, and the `status` meter is a transient stderr line
    that only ever occupies a line of its own and is erased before anything else prints, so it can
    never overwrite streamed text. Its line bookkeeping assumes both streams land on one terminal,
    so tty is true only when stdout and stderr are both ttys."""

    out: TextIO
    err: TextIO
    tty: bool
    line_open: bool = False
    meter_shown: bool = False

    def text(self, delta: str) -> None:
        self._erase_meter()
        click.echo(delta, nl=False, file=self.out)
        if delta:
            self.line_open = not delta.endswith("\n")

    def line(self, text: str) -> None:
        """A finished line the surface `say`s — the answer when it did not stream, a failure, a
        connect link, or a stored-credential acknowledgement."""
        self._close_line()
        click.echo(text, file=self.out)

    def activity(self, note: str) -> None:
        """A mid-turn `note` (a tool call, a skill load) on its own dim line — the meter is erased
        first so streamed text is never corrupted, and the stream continues after it."""
        self._close_line()
        click.echo(click.style(note, dim=True), file=self.out)

    def shared_file(self, name: str, size_bytes: str, url: str) -> None:
        """A file the turn shared. The name and size are dim context; the link stays undimmed
        because it is the one part the member acts on. A deploy that mints no link names the file
        alone, so the member learns it exists rather than nothing at all."""
        self._close_line()
        label = click.style(f"shared {name} ({size_bytes} bytes)", dim=True)
        click.echo(f"{label} {url}" if url else label, file=self.out)

    def meter(self, text: str) -> None:
        if not self.tty:
            return
        if self.line_open:
            click.echo(file=self.err)
            self.line_open = False
        click.echo(
            f"{ERASE_LINE}{click.style(text, dim=True)}", nl=False, file=self.err, color=True
        )
        self.meter_shown = True

    def close(self) -> None:
        self._close_line()

    def _erase_meter(self) -> None:
        if not self.meter_shown:
            return
        click.echo(ERASE_LINE, nl=False, file=self.err, color=True)
        self.meter_shown = False

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
def bundle(out: Path) -> None:
    """Freeze this deploy into a runnable artifact: OCI image recipe, pinned config, lockfile."""
    config = load_config()
    catalog = read_catalog(config.ext.store) if config.ext.store is not None else None
    result = Bundle(config_path=config_path(), catalog=catalog, out=out).build()
    subprocess.run(
        ("uv", "build", "--wheel", str(_ufo_project_dir()), "--out-dir", str(result.out)),
        check=True,
    )
    wheel = result.out / wheel_name()
    if not wheel.exists():
        raise click.ClickException(f"wheel build produced no {wheel}")
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
            return await cancel_one_turn(client, turn_id)
    finally:
        await dispose_db()
