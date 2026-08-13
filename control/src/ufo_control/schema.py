"""The `ufo_control` schema, shaped once per deploy.

`create schema | table | index if not exists` is not atomic against a concurrent creator: two
callers issuing it together both find the object absent, both issue it, and the loser raises a
`pg_class`/`pg_namespace` unique violation. So the schema is shaped by one process before any
replica starts — `ufo-control migrate`, an initContainer of the `ufo-migrate` Job the gateway
Deployment waits on — and no replica issues DDL. `require_control_schema` is the other end: a
gateway that finds a ledger absent says which verb shapes it and refuses to serve, never creating
it under a request.

The verb does not lean on that topology for correctness. A duplicated Job pod or an operator
running it by hand mid-deploy is a second caller, and the Job's `backoffLimit: 0` gives a raced
loser no second chance, so the statements run under `SHAPE_LOCK` — the writer that waits then finds
everything present and writes nothing. Every statement is `if not exists` and the reshape is
conditional, so the verb is a no-op on a database already at head.
"""

import asyncpg

from ufo_control import gateway_invite, gateway_slack_connect, gateway_store

LEDGERS = (gateway_store.TABLE, gateway_invite.TABLE, gateway_slack_connect.TABLE)

SHAPE_LOCK = f"select pg_advisory_xact_lock(hashtext('{gateway_store.SCHEMA} schema'))"

DDL = (
    f"create schema if not exists {gateway_store.SCHEMA}",
    *gateway_store.DDL,
    *gateway_invite.DDL,
    *gateway_slack_connect.DDL,
)

UNBOUND_INVITE_CODE = (
    "select exists (select from information_schema.tables"
    "  where table_schema = $1 and table_name = $2)"
    " and not exists (select from information_schema.columns"
    "  where table_schema = $1 and table_name = $2 and column_name = 'email_domain')"
)

RESHAPE = (
    f"alter table {gateway_store.TABLE} drop column if exists code_hash,"
    " drop column if exists attempts",
)


async def shape_control_schema(dsn: str) -> None:
    """Bring the whole schema to head in one transaction, one writer at a time. An invite ledger
    that cannot name the domain each grant opens is rebuilt and its rows are void: a grant is
    redeemed by the domain it names, so an unbound row could never be honored. A claim ledger
    carrying the columns a verification code needed loses them, voiding nothing: WorkOS holds the
    code, and a claim is ten minutes of state a member reruns."""
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction():
            await connection.execute(SHAPE_LOCK)
            unbound = await connection.fetchval(
                UNBOUND_INVITE_CODE,
                gateway_store.SCHEMA,
                gateway_invite.TABLE.split(".", maxsplit=1)[1],
            )
            if unbound:
                await connection.execute(f"drop table if exists {gateway_invite.TABLE}")
            for statement in DDL:
                await connection.execute(statement)
            for statement in RESHAPE:
                await connection.execute(statement)
    finally:
        await connection.close()


async def require_control_schema(dsn: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        for table in LEDGERS:
            if await connection.fetchval("select to_regclass($1)", table) is None:
                raise RuntimeError(
                    f"{table} is absent — run `ufo-control migrate` before starting the gateway"
                )
    finally:
        await connection.close()
