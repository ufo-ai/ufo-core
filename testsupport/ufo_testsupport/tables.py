"""The shared workspace-data reset the `db` fixture and the CLI e2e's bootstrap both run through.
It lives in the shared testsupport package, so every test directory imports the same one from a
stable, unambiguous path."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

FTS_SHADOW_SUFFIXES = ("data", "idx", "content", "docsize", "config")
POSTGRES_TABLES = sa.text(
    "select tablename from pg_tables where schemaname = 'public' and tablename <> 'alembic_version'"
)
POSTGRES_FOREIGN_KEYS = sa.text(
    "select child.relname as child, parent.relname as parent from pg_constraint "
    "join pg_class child on child.oid = pg_constraint.conrelid "
    "join pg_class parent on parent.oid = pg_constraint.confrelid "
    "join pg_namespace schemas on schemas.oid = child.relnamespace "
    "where pg_constraint.contype = 'f' and schemas.nspname = 'public'"
)
_delete_order: dict[tuple[str, ...], tuple[str, ...]] = {}


async def reset_workspace_data(connection: AsyncConnection) -> None:
    """Wipe every application table in the live database — core and extension alike, enumerated
    from the schema itself so a new extension table can never leak rows across tests. Protected:
    the alembic stamp, and FTS5 shadow tables (their virtual table is deleted from instead, which
    maintains them). DBOS system tables live in the `_dbos` sibling database and are never here."""
    if connection.dialect.name == "postgresql":
        await _delete_dirty_tables(connection)
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


async def _delete_dirty_tables(connection: AsyncConnection) -> None:
    """`truncate table … cascade` over 41 application tables rewrites 41 relfilenodes per test, and
    a session's median cost climbs with position in it — 59 ms at the first test to 614 ms by the
    945th, sawtoothing as autovacuum catches up. One probe finds the two or three tables a test
    actually dirtied and deletes those, in child-first order so a NO ACTION key is never tripped;
    a delete of live rows rewrites nothing else, so a small test pays for its own tables alone. The
    dead pages a heavy test leaves behind are still its successors' cost until autovacuum catches
    up, so what ends is the whole-schema rewrite, not every way cost can follow position.

    Every table is still enumerated and probed on every call — what narrows is which of them a
    statement is spent on, never which of them the wipe is answerable for. The order is cached
    against the table set it was derived from, so a migration that adds a table derives a new one.

    The lock is what `truncate` gave for free and a probe plus a delete do not: both see only their
    own snapshot, so a transaction that had written and not yet committed would be invisible, its
    table judged clean, and its rows would land in the next test. Taking ACCESS EXCLUSIVE on every
    table first — in this transaction, before the probe — puts every prior writer behind the
    snapshot the probe then takes, and a writer that never ends blocks the wipe loudly instead.

    Probe and delete also obey row security, which `truncate` ignored. Core's migrations enable RLS
    on no table and the test role owns them all as superuser, so nothing is hidden from either; a
    migration that enables it would need this wipe to bypass it explicitly.
    """
    names = tuple(sorted((await connection.execute(POSTGRES_TABLES)).scalars().all()))
    if not names:
        return
    order = _delete_order.get(names)
    if order is None:
        order = await _child_first_order(connection, names)
        _delete_order[names] = order
    locked = ", ".join(f'"{name}"' for name in order)
    await connection.execute(sa.text(f"lock table {locked} in access exclusive mode"))
    probe = " union all ".join(
        f"(select '{name}' as name from \"{name}\" limit 1)" for name in order
    )
    dirty = set((await connection.execute(sa.text(probe))).scalars().all())
    for name in order:
        if name in dirty:
            await connection.execute(sa.text(f'delete from "{name}"'))


async def _child_first_order(
    connection: AsyncConnection, names: tuple[str, ...]
) -> tuple[str, ...]:
    parents: dict[str, set[str]] = {}
    for row in await connection.execute(POSTGRES_FOREIGN_KEYS):
        if row.child != row.parent and row.child in names and row.parent in names:
            parents.setdefault(row.child, set()).add(row.parent)
    ordered: list[str] = []
    placed: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in placed:
            return
        if name in path:
            cycle = " -> ".join((*path[path.index(name) :], name))
            raise RuntimeError(f"a foreign key cycle has no delete order: {cycle}")
        for parent in sorted(parents.get(name, ())):
            visit(parent, (*path, name))
        placed.add(name)
        ordered.append(name)

    for name in names:
        visit(name, ())
    return tuple(reversed(ordered))
