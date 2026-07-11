"""Every workspace is one craft aloft: `/fleet`'s count reads the same `workspace` table both
onboarding tiers write, so the landing page's sky can never drift from the real fleet. The proof
boots the real app — lifespan, owner pool, kube client, route — against the throwaway Postgres."""

import asyncio
import uuid
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

from ufo_control.gateway import gateway_app
from ufo_control.gateway_shared import SERVE_DSN_ENV


async def _add_workspaces(dsn: str, count: int) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        for _ in range(count):
            await connection.execute("insert into workspace (id) values ($1)", uuid.uuid4())
    finally:
        await connection.close()


def test_fleet_answers_the_workspace_count(
    gateway_postgres: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        SERVE_DSN_ENV, gateway_postgres.replace("postgresql://", "postgresql+asyncpg://")
    )
    token_file = tmp_path / "token"
    token_file.write_text("test-token")
    monkeypatch.setenv("UFO_CONTROL_KUBE_TOKEN_FILE", str(token_file))
    with TestClient(gateway_app()) as client:
        before = client.get("/fleet").json()["craft"]
        asyncio.run(_add_workspaces(gateway_postgres, 2))
        assert client.get("/fleet").json()["craft"] == before + 2
