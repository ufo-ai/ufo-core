"""The shared workspace-data reset the `db` fixture and the CLI e2e's bootstrap both run through.
It lives in the shared testsupport package, so every test directory imports the same one from a
stable, unambiguous path."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

FTS_SHADOW_SUFFIXES = ("data", "idx", "content", "docsize", "config")


async def reset_workspace_data(connection: AsyncConnection) -> None:
    """Wipe every application table in the live database — core and extension alike, enumerated
    from the schema itself so a new extension table can never leak rows across tests. Protected:
    the alembic stamp, and FTS5 shadow tables (their virtual table is deleted from instead, which
    maintains them). DBOS system tables live in the `_dbos` sibling database and are never here."""
    if connection.dialect.name == "postgresql":
        names = (
            (
                await connection.execute(
                    sa.text(
                        "select tablename from pg_tables "
                        "where schemaname = 'public' and tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        if names:
            quoted = ", ".join(f'"{name}"' for name in names)
            await connection.execute(sa.text(f"truncate table {quoted} cascade"))
        return
    rows = (
        await connection.execute(
            sa.text(
                "select name, sql from sqlite_master where type = 'table' "
                "and name <> 'alembic_version' and name not like 'sqlite_%'"
            )
        )
    ).all()
    virtual = {row.name for row in rows if "virtual table" in (row.sql or "").lower()}
    shadows = {f"{name}_{suffix}" for name in virtual for suffix in FTS_SHADOW_SUFFIXES}
    await connection.execute(sa.text("pragma defer_foreign_keys = on"))
    for row in rows:
        if row.name not in shadows:
            await connection.execute(sa.text(f'delete from "{row.name}"'))
