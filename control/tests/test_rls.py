"""The RLS tier against real Postgres (its own container on :5544, never core's :5541).

The whole chain: create the owner role + shared app database, migrate the runtime's real
``assistant_hosted`` schema as the owner, bootstrap the workspace policies, provision two tenants
through the rls arm, seed each workspace's rows as the owner, then connect as each tenant role and
prove the shared database presents a single-workspace view — disjoint, and write-guarded by the
policy's WITH CHECK. Setup is a sync fixture because ``apply_migrations`` drives alembic on its own
event loop (``asyncio.run``), which cannot nest inside a running one.
"""

import asyncio
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg
import pytest
from click.testing import CliRunner
from ufo.db import apply_migrations

from ufo_control.main import main
from ufo_control.postgres import (
    PG_ROLE_SEED_ENV,
    TenantPostgres,
    ensure_tenant_postgres,
)
from ufo_control.rls import POSTGRES_OWNER_DSN_ENV, bootstrap_policies

PG_HOST = "127.0.0.1"
PG_PORT = 5544
ADMIN_DSN = f"postgresql://admin:admin@{PG_HOST}:{PG_PORT}/postgres"
POSTGRES_HOST = f"{PG_HOST}:{PG_PORT}"
APP_DATABASE = "ufo_rls_test"
FAILLOUD_DATABASE = "ufo_rls_failloud"
OWNER_ROLE = "ufo_owner"
OWNER_PASSWORD = "ownerpw"
SEED = "rls-test-seed"
PACK = "assistant_hosted"
TENANT_ROLES = ("ufo_t_acme", "ufo_t_globex")
TENANT_DBOS = ("ufo_dbos_acme", "ufo_dbos_globex")


def _reachable() -> bool:
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"postgres on :{PG_PORT} not reachable")


def _libpq(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _owner_app_dsn(scheme: str, database: str) -> str:
    return f"{scheme}://{OWNER_ROLE}:{OWNER_PASSWORD}@{POSTGRES_HOST}/{database}"


@dataclass(frozen=True)
class TenantRecord:
    name: str
    workspace_id: str
    postgres: TenantPostgres


@dataclass(frozen=True)
class RlsEnv:
    owner_libpq_dsn: str
    tenants: tuple[TenantRecord, ...]


async def _reset(database: str) -> None:
    connection = await asyncpg.connect(ADMIN_DSN)
    try:
        for name in (database, *TENANT_DBOS):
            await connection.execute(f'drop database if exists "{name}" with (force)')
        for role in (*TENANT_ROLES, "ufo_app", OWNER_ROLE):
            await connection.execute(f'drop role if exists "{role}"')
        await connection.execute(f"create role \"{OWNER_ROLE}\" login password '{OWNER_PASSWORD}'")
        await connection.execute(f'create database "{database}" owner "{OWNER_ROLE}"')
    finally:
        await connection.close()
    app = await asyncpg.connect(ADMIN_DSN, database=database)
    try:
        await app.execute(f'grant all on schema public to "{OWNER_ROLE}"')
    finally:
        await app.close()


async def _seed_workspace_rows(dsn: str, tenants: tuple[TenantRecord, ...]) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        for tenant in tenants:
            workspace_id = UUID(tenant.workspace_id)
            await connection.execute(
                "insert into workspace (id, created_at, updated_at) values ($1, now(), now())",
                workspace_id,
            )
            await connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values ($1, $2, $3, now(), now())",
                uuid4(),
                workspace_id,
                f"owner@{tenant.name}.test",
            )
    finally:
        await connection.close()


async def _provision(name: str, workspace_id: str) -> TenantPostgres:
    return await ensure_tenant_postgres(
        "rls", ADMIN_DSN, name, POSTGRES_HOST, APP_DATABASE, workspace_id
    )


@pytest.fixture(scope="module")
def rls_env() -> Iterator[RlsEnv]:
    previous_seed = os.environ.get(PG_ROLE_SEED_ENV)
    os.environ[PG_ROLE_SEED_ENV] = SEED
    asyncio.run(_reset(APP_DATABASE))
    apply_migrations(_owner_app_dsn("postgresql+asyncpg", APP_DATABASE), PACK)
    owner_libpq = _owner_app_dsn("postgresql", APP_DATABASE)
    asyncio.run(bootstrap_policies(owner_libpq))
    tenants: list[TenantRecord] = []
    for name in ("acme", "globex"):
        workspace_id = str(uuid4())
        postgres = asyncio.run(_provision(name, workspace_id))
        tenants.append(TenantRecord(name=name, workspace_id=workspace_id, postgres=postgres))
    tenants_tuple = tuple(tenants)
    asyncio.run(_seed_workspace_rows(owner_libpq, tenants_tuple))
    try:
        yield RlsEnv(owner_libpq_dsn=owner_libpq, tenants=tenants_tuple)
    finally:
        asyncio.run(_reset(APP_DATABASE))
        if previous_seed is None:
            os.environ.pop(PG_ROLE_SEED_ENV, None)
        else:
            os.environ[PG_ROLE_SEED_ENV] = previous_seed


async def test_reprovisioning_an_existing_tenant_is_idempotent(rls_env: RlsEnv) -> None:
    acme = rls_env.tenants[0]
    result = await _provision(acme.name, acme.workspace_id)
    assert result.workspace_id == acme.workspace_id
    connection = await asyncpg.connect(_libpq(result.url))
    try:
        pinned = await connection.fetchval("select current_setting('app.workspace_id')")
        assert pinned == acme.workspace_id
    finally:
        await connection.close()


async def test_each_tenant_role_sees_only_its_own_workspace(rls_env: RlsEnv) -> None:
    for tenant in rls_env.tenants:
        connection = await asyncpg.connect(_libpq(tenant.postgres.url))
        try:
            pinned = await connection.fetchval("select current_setting('app.workspace_id')")
            assert pinned == tenant.workspace_id
            workspaces = await connection.fetch("select id from workspace")
            assert [str(row["id"]) for row in workspaces] == [tenant.workspace_id]
            members = await connection.fetch("select workspace_id from member")
            assert [str(row["workspace_id"]) for row in members] == [tenant.workspace_id]
        finally:
            await connection.close()


async def test_no_cross_tenant_visibility(rls_env: RlsEnv) -> None:
    acme, globex = rls_env.tenants
    connection = await asyncpg.connect(_libpq(acme.postgres.url))
    try:
        seen = await connection.fetch("select id from workspace")
        ids = {str(row["id"]) for row in seen}
        assert globex.workspace_id not in ids
        assert ids == {acme.workspace_id}
    finally:
        await connection.close()


async def test_with_check_rejects_a_foreign_workspace_insert(rls_env: RlsEnv) -> None:
    acme, globex = rls_env.tenants
    connection = await asyncpg.connect(_libpq(acme.postgres.url))
    try:
        # Its own workspace: the WITH CHECK passes.
        await connection.execute(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values ($1, $2, $3, now(), now())",
            uuid4(),
            UUID(acme.workspace_id),
            "second@acme.test",
        )
        # A foreign workspace uuid: the WITH CHECK rejects it as an RLS violation.
        with pytest.raises(asyncpg.PostgresError, match="row-level security"):
            await connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values ($1, $2, $3, now(), now())",
                uuid4(),
                UUID(globex.workspace_id),
                "intruder@acme.test",
            )
    finally:
        await connection.close()


def test_rls_bootstrap_cli_is_idempotent(rls_env: RlsEnv) -> None:
    # A sync test: the CLI command drives ``asyncio.run`` itself, which cannot nest in a loop.
    previous = os.environ.get(POSTGRES_OWNER_DSN_ENV)
    os.environ[POSTGRES_OWNER_DSN_ENV] = rls_env.owner_libpq_dsn
    try:
        result = CliRunner().invoke(main, ["rls-bootstrap"])
    finally:
        if previous is None:
            os.environ.pop(POSTGRES_OWNER_DSN_ENV, None)
        else:
            os.environ[POSTGRES_OWNER_DSN_ENV] = previous
    assert result.exit_code == 0, result.output
    assert "rls policies at head" in result.output


async def test_bootstrap_fails_loud_on_an_unpoliced_table() -> None:
    reset = await asyncpg.connect(ADMIN_DSN)
    try:
        await reset.execute(f'drop database if exists "{FAILLOUD_DATABASE}" with (force)')
        await reset.execute(f'create database "{FAILLOUD_DATABASE}"')
    finally:
        await reset.close()
    dsn = f"postgresql://admin:admin@{PG_HOST}:{PG_PORT}/{FAILLOUD_DATABASE}"
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("create table workspace (id uuid primary key)")
        await connection.execute("create table rogue_widget (id int primary key)")
    finally:
        await connection.close()
    try:
        with pytest.raises(RuntimeError, match="rogue_widget"):
            await bootstrap_policies(dsn)
    finally:
        cleanup = await asyncpg.connect(ADMIN_DSN)
        try:
            await cleanup.execute(f'drop database if exists "{FAILLOUD_DATABASE}" with (force)')
        finally:
            await cleanup.close()
