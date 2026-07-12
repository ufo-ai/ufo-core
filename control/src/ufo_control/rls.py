"""Provision and enforce the shared workspace database boundary."""

import hashlib
import os

import asyncpg

POSTGRES_OWNER_DSN_ENV = "UFO_CONTROL_POSTGRES_OWNER_DSN"
PG_ROLE_SEED_ENV = "UFO_CONTROL_PG_ROLE_SEED"
ALEMBIC_VERSION_TABLE = "alembic_version"
WORKSPACE_TABLE = "workspace"
WORKSPACE_COLUMN = "workspace_id"
POLICY_NAME = "ufo_workspace_rls"
WORKSPACE_GUC = "app.workspace_id"
OWNER_ROLE = "ufo_owner"
SERVE_ROLE = "ufo_serve"


def owner_dsn() -> str:
    dsn = os.environ.get(POSTGRES_OWNER_DSN_ENV)
    if not dsn:
        raise RuntimeError(f"{POSTGRES_OWNER_DSN_ENV} is unset — no Postgres owner DSN for RLS")
    return dsn


def serve_password() -> str:
    seed = os.environ.get(PG_ROLE_SEED_ENV)
    if not seed:
        raise RuntimeError(f"{PG_ROLE_SEED_ENV} is unset — cannot derive the serve Postgres role")
    return hashlib.sha256(f"{seed}:{SERVE_ROLE}".encode()).hexdigest()


def serve_dsn(postgres_host: str, app_database: str) -> str:
    return f"postgresql+asyncpg://{SERVE_ROLE}:{serve_password()}@{postgres_host}/{app_database}"


async def ensure_serve_role(admin_dsn: str) -> None:
    password = serve_password()
    connection = await asyncpg.connect(admin_dsn)
    try:
        exists = await connection.fetchval("select 1 from pg_roles where rolname = $1", SERVE_ROLE)
        if exists is None:
            await connection.execute(f"create role \"{SERVE_ROLE}\" login password '{password}'")
        else:
            await connection.execute(
                f"alter role \"{SERVE_ROLE}\" with login password '{password}'"
            )
        await connection.execute(f'grant set on parameter {WORKSPACE_GUC} to "{SERVE_ROLE}"')
        await _grant_serve_role(connection)
        app_database = await connection.fetchval("select current_database()")
        await _ensure_database(connection, f"{app_database}_dbos", owner=SERVE_ROLE)
    finally:
        await connection.close()


async def bootstrap_policies(dsn: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction():
            tables = await connection.fetch(
                "select tablename from pg_tables where schemaname = 'public' order by tablename"
            )
            for record in tables:
                table = record["tablename"]
                if table != ALEMBIC_VERSION_TABLE:
                    await _policy_for(connection, table)
    finally:
        await connection.close()


async def _grant_serve_role(connection: asyncpg.Connection) -> None:
    await connection.execute(
        f'alter default privileges for role "{OWNER_ROLE}" in schema public '
        f'revoke select, insert, update, delete on tables from "{SERVE_ROLE}"'
    )
    await connection.execute(
        f'alter default privileges for role "{OWNER_ROLE}" in schema public '
        f'revoke usage on sequences from "{SERVE_ROLE}"'
    )
    await connection.execute(f'grant usage on schema public to "{SERVE_ROLE}"')
    await connection.execute(
        f'grant select, insert, update, delete on all tables in schema public to "{SERVE_ROLE}"'
    )
    await connection.execute(f'grant usage on all sequences in schema public to "{SERVE_ROLE}"')


async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None:
    exists = await connection.fetchval("select 1 from pg_database where datname = $1", name)
    if exists is None:
        await connection.execute(f'create database "{name}" owner "{owner}"')


async def _policy_for(connection: asyncpg.Connection, table: str) -> None:
    column = await _scope_column(connection, table)
    predicate = f"{column} = current_setting('{WORKSPACE_GUC}')::uuid"
    await connection.execute(f'alter table "{table}" enable row level security')
    await connection.execute(f'drop policy if exists {POLICY_NAME} on "{table}"')
    await connection.execute(
        f'create policy {POLICY_NAME} on "{table}" using ({predicate}) with check ({predicate})'
    )


async def _scope_column(connection: asyncpg.Connection, table: str) -> str:
    if table == WORKSPACE_TABLE:
        return "id"
    has_column = await connection.fetchval(
        "select 1 from information_schema.columns "
        "where table_schema = 'public' and table_name = $1 and column_name = $2",
        table,
        WORKSPACE_COLUMN,
    )
    if has_column is None:
        raise RuntimeError(
            f"table {table!r} is neither the workspace table nor workspace-scoped "
            f"(no {WORKSPACE_COLUMN} column)"
        )
    return WORKSPACE_COLUMN
