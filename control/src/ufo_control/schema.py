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

CLAIM_KEYED_DELIVERY = (
    "select exists (select from information_schema.columns"
    "  where table_schema = $1 and table_name = $2 and column_name = 'onboard_claim_id')"
)

STEP_ASIDE = (
    f"alter table {gateway_slack_connect.TABLE} rename to slack_connect_delivery_by_claim",
    "alter index ufo_control.slack_connect_delivery_pkey"
    "  rename to slack_connect_delivery_by_claim_pkey",
    "alter index ufo_control.slack_connect_delivery_channel_name_key"
    "  rename to slack_connect_delivery_by_claim_channel_name_key",
    f"alter index ufo_control.{gateway_slack_connect.DUE_INDEX}"
    "  rename to slack_connect_delivery_by_claim_due",
)

TRANSLATE_DELIVERY = (
    f"insert into {gateway_slack_connect.TABLE}"
    "  (email_domain, email, state, channel_name, channel_id, slack_invitation_id,"
    "   invite_attempted_at, attempts, last_error, created_at, updated_at, delivered_at)"
    "  select distinct on (d.channel_name)"
    "    k.email_domain, k.email,"
    f"    case when d.state in ('{gateway_slack_connect.STATE_DELIVERED}',"
    f"                          '{gateway_slack_connect.STATE_FAILED}')"
    f"      then d.state else '{gateway_slack_connect.STATE_PENDING}' end,"
    "    d.channel_name, d.channel_id, d.slack_invitation_id,"
    "    d.invite_attempted_at, d.attempts, d.last_error, d.created_at, d.updated_at,"
    "    d.delivered_at"
    "  from ufo_control.slack_connect_delivery_by_claim d"
    f"  join {gateway_store.TABLE} k on k.id = d.onboard_claim_id"
    "  order by d.channel_name, d.created_at"
    " on conflict do nothing",
    "drop table ufo_control.slack_connect_delivery_by_claim",
)

RESHAPE = (
    f"alter table {gateway_store.TABLE} drop column if exists code_hash,"
    " drop column if exists attempts",
    f"alter table {gateway_invite.TABLE} alter column object_number drop not null",
    f"alter table {gateway_invite.TABLE} add column if not exists business text,"
    " add column if not exists goals text,"
    " drop column if exists role,"
    " drop column if exists member_name,"
    " drop column if exists company,"
    " drop column if exists use_case",
    f"alter table {gateway_store.TABLE} add column if not exists created_workspace boolean"
    " not null default false",
)


async def shape_control_schema(dsn: str) -> None:
    """Bring the whole schema to head in one transaction, one writer at a time. An invite ledger
    that cannot name the domain each grant opens is rebuilt and its rows are void: a grant is
    redeemed by the domain it names, so an unbound row could never be honored. A claim ledger
    carrying the columns a verification code needed loses them, voiding nothing: WorkOS holds the
    code, and a claim is ten minutes of state a member reruns.

    A Slack Connect ledger keyed by the claim that completed signup is translated to the granted
    domain rather than rebuilt. Its rows are the only record of which customers Slack has already
    invited, so dropping them would open a second channel and send a second invitation to everyone
    already delivered. The old table and its three indexes step aside under names nothing reads,
    the head shape is created beside it, and each row moves across on its claim's domain.

    A row mid-delivery lands `pending`, never `claimed`: the worker holding it does not survive the
    deploy, and a `claimed` row arriving without its lease could never be claimed again — `CLAIM`
    takes a claimed row only where `claim_expires_at <= now()`, and a null never satisfies that, so
    the row would strand with no verb able to recover it. Its `channel_id` and `invite_attempted_at`
    cross with it, which is what lets the next worker reconcile against Slack rather than invite a
    second time.

    The index renames are not cosmetic: an index name is schema-scoped, and while Postgres
    uniquifies the names it generates for a primary key and a unique column, `create index if not
    exists` on the explicitly named due index would find the old table's index holding that name
    and silently create nothing — leaving the head table to scan for due rows."""
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
            claim_keyed = await connection.fetchval(
                CLAIM_KEYED_DELIVERY,
                gateway_store.SCHEMA,
                gateway_slack_connect.TABLE.split(".", maxsplit=1)[1],
            )
            if claim_keyed:
                for statement in STEP_ASIDE:
                    await connection.execute(statement)
            for statement in DDL:
                await connection.execute(statement)
            if claim_keyed:
                for statement in TRANSLATE_DELIVERY:
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
