"""A throwaway Postgres for the gateway tests — the platform `onboard_claim` ledger and the
member write both hit a real database. Spun once per session on 5548 (never the shared 5541 or a
sibling module's port), schema prepared, owner DSN exported; the store fixture hands each test a
clean table."""

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest

from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_store import OnboardStore
from ufo_control.rls import POSTGRES_OWNER_DSN_ENV

CONTAINER = "ufo-gateway-test-pg"
PORT = 5548
OWNER_DSN = f"postgresql://ufo:ufo@127.0.0.1:{PORT}/ufo"
IMAGE = "pgvector/pgvector:pg17"
WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"

RUNTIME_SCHEMA = (
    "create table if not exists workspace ("
    "  id uuid primary key,"
    "  created_at timestamptz not null default now(),"
    "  updated_at timestamptz not null default now())",
    "create table if not exists member ("
    "  id uuid primary key,"
    "  workspace_id uuid not null references workspace(id) on delete cascade,"
    "  email text not null,"
    "  created_at timestamptz not null,"
    "  updated_at timestamptz not null,"
    "  unique (workspace_id, email))",
    "create table if not exists agent ("
    "  id uuid primary key,"
    "  workspace_id uuid not null references workspace(id) on delete cascade,"
    "  name text not null,"
    "  prompt text not null,"
    "  model text not null,"
    "  created_at timestamptz not null,"
    "  updated_at timestamptz not null,"
    "  unique (workspace_id, name))",
)


async def _prepare_schema() -> None:
    pool = await asyncpg.create_pool(OWNER_DSN)
    try:
        async with pool.acquire() as connection:
            for statement in RUNTIME_SCHEMA:
                await connection.execute(statement)
            await connection.execute(
                "insert into workspace (id) values ($1) on conflict do nothing",
                uuid.UUID(WORKSPACE_ID),
            )
        await OnboardStore(pool=pool).ensure_table()
        await InviteCodes(pool=pool).ensure_table()
    finally:
        await pool.close()


@pytest.fixture(scope="session")
def gateway_postgres(monkeypatch_session: pytest.MonkeyPatch) -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker is required to spin the gateway-onboarding Postgres")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_USER=ufo",
            "-e",
            "POSTGRES_PASSWORD=ufo",
            "-e",
            "POSTGRES_DB=ufo",
            "-p",
            f"127.0.0.1:{PORT}:5432",
            IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start Postgres container: {started.stderr.strip()}")
    try:
        _await_ready()
        asyncio.run(_prepare_schema())
        monkeypatch_session.setenv(POSTGRES_OWNER_DSN_ENV, OWNER_DSN)
        yield OWNER_DSN
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)


@pytest.fixture
async def store(gateway_postgres: str) -> AsyncIterator[OnboardStore]:
    pool = await asyncpg.create_pool(gateway_postgres)
    async with pool.acquire() as connection:
        await connection.execute("truncate ufo_control.onboard_claim")
        await connection.execute("truncate ufo_control.invite_code")
        await connection.execute("delete from member")
        await connection.execute("delete from agent")
    try:
        yield OnboardStore(pool=pool)
    finally:
        await pool.close()


def _await_ready(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            asyncio.run(_ping())
            return
        except Exception as error:
            last = error
            time.sleep(1.0)
    raise RuntimeError(f"Postgres on {PORT} never became ready: {last}")


async def _ping() -> None:
    connection = await asyncpg.connect(OWNER_DSN)
    await connection.close()


@pytest.fixture(scope="session")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()
