"""A boot-time regression for connection-budget exhaustion: several gateway replicas starting
concurrently must not eagerly claim more of the shared Postgres connection ceiling than the
deploy actually has, or every replica's startup connect races and loses."""

import asyncio
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest

from ufo_control.gateway import GATEWAY_POOL_MAX_SIZE, gateway_app
from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    SES_SENDER_ENV,
)
from ufo_control.gateway_shared import SERVE_DSN_ENV
from ufo_control.gateway_token import TOKEN_SECRET_ENV
from ufo_control.rls import POSTGRES_OWNER_DSN_ENV
from ufo_control.schema import shape_control_schema

CONTAINER = "ufo-gateway-pool-budget-test-pg"
PORT = 5549
OWNER_DSN = f"postgresql://ufo:ufo@127.0.0.1:{PORT}/ufo"
IMAGE = "pgvector/pgvector:pg17"
WORKSPACE_URL = "https://app.testing.flyingobject.ai"
TOKEN_SECRET = "test-token-secret"
REPLICAS = 3
MAX_CONNECTIONS = 3 + REPLICAS * (GATEWAY_POOL_MAX_SIZE + 1)


@pytest.fixture(scope="module")
def constrained_postgres() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker is required to spin the connection-constrained Postgres")
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
            "-c",
            f"max_connections={MAX_CONNECTIONS}",
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
        yield OWNER_DSN
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)


async def _prepare_schema() -> None:
    await shape_control_schema(OWNER_DSN)


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


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "web-identity"
    token_file.write_text("token")
    monkeypatch.setenv(POSTGRES_OWNER_DSN_ENV, OWNER_DSN)
    monkeypatch.setenv(SERVE_DSN_ENV, OWNER_DSN.replace("postgresql://", "postgresql+asyncpg://"))
    monkeypatch.setenv(TOKEN_SECRET_ENV, TOKEN_SECRET)
    monkeypatch.setenv("UFO_WORKSPACE_BASE_URL", WORKSPACE_URL)
    monkeypatch.setenv(SES_SENDER_ENV, "no-reply@flyingobject.ai")
    monkeypatch.setenv(AWS_ROLE_ARN_ENV, "arn:aws:iam::123456789012:role/gateway-ses")
    monkeypatch.setenv(AWS_WEB_IDENTITY_TOKEN_FILE_ENV, str(token_file))


async def _boot(app) -> None:
    async with app.router.lifespan_context(app):
        pass


def test_replica_fleet_boots_under_the_shared_connection_ceiling(
    constrained_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path)

    async def boot_fleet() -> None:
        await asyncio.gather(*(_boot(gateway_app()) for _ in range(REPLICAS)))

    asyncio.run(boot_fleet())
