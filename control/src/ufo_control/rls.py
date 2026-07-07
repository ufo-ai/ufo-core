"""The RLS policy bootstrap — the enforcement gate, run once per bundle rollout as the owner.

The tenants share one application database; the group grant (``postgres`` module) lets their roles
read and write, and these policies are what make that database present each connection a
single-workspace view. Run as ``ufo_owner`` (which owns the tables and bypasses RLS by design —
platform cross-tenant reads), after ``ufoctl migrate``, over the shared app database the owner DSN
targets. For every table in schema ``public`` except ``alembic_version``:

    workspace table  → policy on ``id``            = the session's pinned ``app.workspace_id``
    has workspace_id → policy on ``workspace_id``  = the session's pinned ``app.workspace_id``
    neither          → FAIL LOUD, naming the table — a new unpoliced table breaks the rollout, not
                       the isolation (enforce, don't document)

Policies are dropped and recreated so a redefinition converges; RLS is enabled per table. The owner
is never FORCEd RLS, so it keeps its cross-tenant bypass.
"""

import os

import asyncpg

POSTGRES_OWNER_DSN_ENV = "UFO_CONTROL_POSTGRES_OWNER_DSN"
ALEMBIC_VERSION_TABLE = "alembic_version"
WORKSPACE_TABLE = "workspace"
WORKSPACE_COLUMN = "workspace_id"
POLICY_NAME = "ufo_workspace_rls"
WORKSPACE_GUC = "app.workspace_id"


def owner_dsn() -> str:
    dsn = os.environ.get(POSTGRES_OWNER_DSN_ENV)
    if not dsn:
        raise RuntimeError(f"{POSTGRES_OWNER_DSN_ENV} is unset — no Postgres owner DSN for RLS")
    return dsn


async def bootstrap_policies(dsn: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        tables = await connection.fetch(
            "select tablename from pg_tables where schemaname = 'public' order by tablename"
        )
        for record in tables:
            table = record["tablename"]
            if table == ALEMBIC_VERSION_TABLE:
                continue
            await _policy_for(connection, table)
    finally:
        await connection.close()


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
            f"(no {WORKSPACE_COLUMN} column) — it cannot be isolated by RLS; every app table must "
            "carry the workspace key before the RLS tier can deploy"
        )
    return WORKSPACE_COLUMN
