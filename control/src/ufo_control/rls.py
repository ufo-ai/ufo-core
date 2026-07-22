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
LOCK_TIMEOUT = "10s"
IDLE_IN_TRANSACTION_TIMEOUT = "60s"
EXPECTED_POLICY_EXPR = "({column} = (current_setting('{guc}'::text))::uuid)"


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
        await connection.execute(f"set lock_timeout = '{LOCK_TIMEOUT}'")
        exists = await connection.fetchval("select 1 from pg_roles where rolname = $1", SERVE_ROLE)
        if exists is None:
            await connection.execute(f"create role \"{SERVE_ROLE}\" login password '{password}'")
        else:
            await connection.execute(
                f"alter role \"{SERVE_ROLE}\" with login password '{password}'"
            )
        await connection.execute(f'grant set on parameter {WORKSPACE_GUC} to "{SERVE_ROLE}"')
        for role in (SERVE_ROLE, OWNER_ROLE):
            await connection.execute(
                f'alter role "{role}" set idle_in_transaction_session_timeout = '
                f"'{IDLE_IN_TRANSACTION_TIMEOUT}'"
            )
        await _grant_serve_role(connection)
        app_database = await connection.fetchval("select current_database()")
        await _ensure_database(connection, f"{app_database}_dbos", owner=SERVE_ROLE)
    finally:
        await connection.close()


async def bootstrap_policies(dsn: str) -> None:
    """Enable and refresh the workspace policy on every public table. A table whose policy already
    matches costs only the ACCESS SHARE lock `_conformant`'s catalog read takes; a table that
    genuinely needs DDL takes it in one short transaction. Either way, a table wedged behind a
    holder's ACCESS EXCLUSIVE fails after LOCK_TIMEOUT naming its lock holders instead of wedging
    the whole bootstrap behind it."""
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f"set lock_timeout = '{LOCK_TIMEOUT}'")
        tables = await connection.fetch(
            "select tablename from pg_tables where schemaname = 'public' order by tablename"
        )
        for record in tables:
            table = record["tablename"]
            if table == ALEMBIC_VERSION_TABLE:
                continue
            try:
                if await _conformant(connection, table):
                    continue
                async with connection.transaction():
                    await _policy_for(connection, table)
            except asyncpg.exceptions.LockNotAvailableError as error:
                holders = await _lock_holders(connection, table)
                raise RuntimeError(
                    f"lock on table {table!r} timed out after {LOCK_TIMEOUT}; held by: {holders}"
                ) from error
    finally:
        await connection.close()


async def _conformant(connection: asyncpg.Connection, table: str) -> bool:
    """RLS enabled and the managed policy already carrying this table's exact predicate. Reads
    catalogs only, so it never takes the ACCESS EXCLUSIVE the DDL path needs — but `pg_policies`
    resolves `qual`/`with_check` via `pg_get_expr`, which takes an ACCESS SHARE lock on the table,
    so this call still waits (and can time out) behind a holder's ACCESS EXCLUSIVE. The caller
    wraps this in the same LOCK_TIMEOUT handling as the DDL path for that reason. The expected
    expression is Postgres's own normalization of the predicate `_policy_for` creates; a drift (a
    version upgrade changing normalization, a template change) reads as non-conformant and costs
    one re-create — degradation is an extra DDL pass, never a skipped policy."""
    row = await connection.fetchrow(
        "select rel.relrowsecurity, pol.qual, pol.with_check, pol.permissive, pol.roles, pol.cmd "
        "from pg_class rel join pg_namespace ns on ns.oid = rel.relnamespace "
        "left join pg_policies pol on pol.schemaname = ns.nspname "
        "and pol.tablename = rel.relname and pol.policyname = $2 "
        "where ns.nspname = 'public' and rel.relname = $1",
        table,
        POLICY_NAME,
    )
    if row is None or not row["relrowsecurity"] or row["qual"] is None:
        return False
    column = await _scope_column(connection, table)
    expected = EXPECTED_POLICY_EXPR.format(column=column, guc=WORKSPACE_GUC)
    return (
        row["qual"] == expected
        and row["with_check"] == expected
        and row["permissive"] == "PERMISSIVE"
        and row["cmd"] == "ALL"
        and list(row["roles"]) == ["public"]
    )


async def _lock_holders(connection: asyncpg.Connection, table: str) -> str:
    rows = await connection.fetch(
        "select stat.pid, stat.usename, stat.state, now() - stat.xact_start as xact_age, "
        "left(stat.query, 200) as query "
        "from pg_locks locks join pg_stat_activity stat on stat.pid = locks.pid "
        "where locks.relation = $1::regclass and locks.granted and locks.pid <> pg_backend_pid()",
        table,
    )
    return (
        "; ".join(
            f"pid={row['pid']} role={row['usename']} state={row['state']!r} "
            f"xact_age={row['xact_age']} query={row['query']!r}"
            for row in rows
        )
        or "(no holder visible)"
    )


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
