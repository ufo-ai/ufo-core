"""The selfhost CLI: init, serve, chat."""

import asyncio
import hashlib
import json
import secrets
from pathlib import Path
from uuid import uuid4

import asyncpg
import click
import httpx
import sqlalchemy as sa

from selfhost.config import Config, load_config
from selfhost.db import apply_migrations, dispose_db, init_db, workspace_tx
from selfhost.schema import tables
from selfhost.serve import run as serve_run

SELFHOST_DIR = Path.home() / ".selfhost"
DEFAULT_AGENT_MODEL = "claude-opus-4-8"
DEFAULT_AGENT_PROMPT = "You are a helpful assistant."
RECONNECT_SECONDS = 1.0
TURN_REQUEST_TIMEOUT_SECONDS = 90.0
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
    """Write selfhost.toml if absent, then create the schema, workspace, owner,
    default agent, and CLI token."""
    config_path = Path("selfhost.toml")
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG)
        click.echo(f"wrote {config_path} (SQLite, filesystem blobs — zero services)")
    config = load_config()
    if config.database.url.startswith("postgresql"):
        asyncio.run(_create_postgres_system_database(config))
    apply_migrations(config.database.url)
    token = secrets.token_hex(32)
    asyncio.run(_bootstrap_workspace(config, email, model, token))
    SELFHOST_DIR.mkdir(mode=0o700, exist_ok=True)
    token_path = SELFHOST_DIR / "token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    click.echo(f"workspace ready — owner {email}, agent 'assistant' ({model})")
    click.echo(f"cli token written to {token_path}")


async def _bootstrap_workspace(config: Config, email: str, model: str, token: str) -> None:
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            owner = (await connection.execute(sa.select(tables.member.c.email))).first()
            if owner is not None:
                raise click.ClickException(f"already initialized (owner {owner.email})")
            workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name="assistant",
                    prompt=DEFAULT_AGENT_PROMPT,
                    model=model,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.surface_identity).values(
                    workspace_id=workspace_id,
                    member_id=member_id,
                    surface="cli",
                    external_id=hashlib.sha256(token.encode()).hexdigest(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
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
    async with httpx.AsyncClient(base_url=base, timeout=TURN_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post("/v1/chat", content=message.encode(), headers=headers)
        response.raise_for_status()
        turn_id = response.json()["turn_id"]
        current["turn_id"] = turn_id
        streamed = False
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
                            _render_terminal(frame["frame"], streamed)
                            return
                        click.echo(frame["text"], nl=False)
                        streamed = True
            except httpx.TransportError:
                await asyncio.sleep(RECONNECT_SECONDS)


async def _cancel_turn(base: str, headers: dict[str, str], turn_id: str) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
        await client.post(f"/v1/turns/{turn_id}/cancel", headers=headers)


def _render_terminal(terminal: dict[str, object], streamed: bool) -> None:
    match terminal["status"]:
        case "done":
            if not streamed:
                click.echo(terminal["text"], nl=False)
            click.echo()
            dollars = int(terminal["cost_micro_usd"]) / 1_000_000
            cost = f"{terminal['model']} · {terminal['tokens']} tok · ${dollars:.6f}"
            click.echo(click.style(cost, dim=True))
        case "cancelled":
            click.echo("\n(cancelled)")
        case _:
            raise click.ClickException(f"turn failed: {terminal['error_class']}")
