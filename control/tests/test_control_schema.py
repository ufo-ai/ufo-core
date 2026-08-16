"""`ufo-control migrate` against a real Postgres that holds none of the schema yet.

The deploy shapes `ufo_control` once, before any gateway replica starts, so the proof is that shape:
an empty database brought to head by the verb the Job runs, the same verb repeated changing nothing,
concurrent writers agreeing on one schema rather than racing, and a gateway that refuses to boot
against a database the verb never ran on — never re-creating what it found missing.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from click.testing import CliRunner

from ufo_control.gateway import gateway_app
from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    SES_SENDER_ENV,
)
from ufo_control.gateway_invite import (
    LIVE_DOMAIN_INDEX,
    LIVE_OBJECT_INDEX,
    InviteAccepted,
    InviteCodes,
)
from ufo_control.gateway_shared import SERVE_DSN_ENV
from ufo_control.gateway_slack_connect import DUE_INDEX
from ufo_control.gateway_store import ACTIVE_INDEX, SCHEMA, OnboardClaim, OnboardStore
from ufo_control.gateway_token import TOKEN_SECRET_ENV
from ufo_control.gateway_workos import (
    AUTH_CALLBACK_PATH,
    WORKOS_API_KEY_ENV,
    WORKOS_CLIENT_ID_ENV,
    WORKOS_REDIRECT_URI_ENV,
)
from ufo_control.main import main
from ufo_control.rls import POSTGRES_OWNER_DSN_ENV
from ufo_control.schema import shape_control_schema

REPO = Path(__file__).resolve().parents[2]
HOSTED_TEMPLATE = REPO / "infra/templates/hosted.yaml.tpl"
MIGRATE_JOB = (
    HOSTED_TEMPLATE.read_text()
    .split("name: ufo-migrate-${image_tag}", maxsplit=1)[1]
    .split("\n---\n", maxsplit=1)[0]
)
MIGRATE_STEP = "\n        - name: migrate\n"
CONTROL_SCHEMA_STEP = "\n        - name: control-schema\n"
RLS_BOOTSTRAP_STEP = "\n        - name: rls-bootstrap\n"
INIT_CONTAINERS = "\n      initContainers:\n"
CONTAINERS = "\n      containers:\n"
CONTROL_STEP = MIGRATE_JOB.split(CONTROL_SCHEMA_STEP, maxsplit=1)[1].split(CONTAINERS, maxsplit=1)[
    0
]

RELATIONS = (
    "select rel.relkind::text as relkind, rel.relname, rel.oid::bigint as oid from pg_class rel"
    " join pg_namespace ns on ns.oid = rel.relnamespace"
    " where ns.nspname = $1 and rel.relkind in ('r', 'i')"
    " order by rel.relkind, rel.relname"
)
TABLE_KIND = "r"
INDEX_KIND = "i"

WRITERS = 8
SHAPED_DATABASE = "ufo_control_schema"
EXPECTED_TABLES = frozenset({"onboard_claim", "invite_code", "slack_connect_delivery"})
EXPECTED_INDEXES = frozenset({ACTIVE_INDEX, LIVE_OBJECT_INDEX, LIVE_DOMAIN_INDEX, DUE_INDEX})
WORKSPACE_URL = "https://app.testing.flyingobject.ai"


@pytest.fixture
def empty_database(gateway_postgres: str) -> Iterator[str]:
    """A database of its own, so first-time shaping is genuinely first-time and the session's
    prepared schema is never dropped out from under the other tests."""
    dsn = f"{gateway_postgres.rsplit('/', maxsplit=1)[0]}/{SHAPED_DATABASE}"
    asyncio.run(_recreate_database(gateway_postgres))
    try:
        yield dsn
    finally:
        asyncio.run(_drop_database(gateway_postgres))


@pytest.fixture
def migrate(empty_database: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[CliRunner]:
    monkeypatch.setenv(POSTGRES_OWNER_DSN_ENV, empty_database)
    yield CliRunner()


def test_the_verb_shapes_an_empty_database_and_repeats_as_a_no_op(
    migrate: CliRunner, empty_database: str
) -> None:
    first = migrate.invoke(main, ["migrate"])
    assert first.exit_code == 0, first.output
    shaped = asyncio.run(_catalog(empty_database))
    assert {name for kind, name, _ in shaped if kind == TABLE_KIND} == EXPECTED_TABLES
    assert EXPECTED_INDEXES <= {name for kind, name, _ in shaped if kind == INDEX_KIND}

    second = migrate.invoke(main, ["migrate"])
    assert second.exit_code == 0, second.output
    assert asyncio.run(_catalog(empty_database)) == shaped


async def test_concurrent_shaping_agrees_on_one_schema(empty_database: str) -> None:
    """The deploy runs one writer, but a duplicated Job pod or an operator running the verb by hand
    mid-deploy is a second one, and `backoffLimit: 0` gives a raced loser no second chance. So the
    proof is N genuinely-first-time writers at once: all succeed, and the schema they agree on is
    the same one a single run produces."""
    await asyncio.gather(*(shape_control_schema(empty_database) for _ in range(WRITERS)))

    shaped = await _catalog(empty_database)
    assert {name for kind, name, _ in shaped if kind == TABLE_KIND} == EXPECTED_TABLES
    assert EXPECTED_INDEXES <= {name for kind, name, _ in shaped if kind == INDEX_KIND}


async def test_the_verb_rebuilds_an_unbound_invite_ledger(empty_database: str) -> None:
    """A ledger whose rows name no domain can honor no grant; the reshape that voids it lives with
    the shaping verb, so the grant it rebuilds for is mintable and redeemable afterwards."""
    connection = await asyncpg.connect(empty_database)
    try:
        await connection.execute(f"create schema {SCHEMA}")
        await connection.execute(
            f"create table {SCHEMA}.invite_code ("
            "  id uuid primary key,"
            "  code_hash text not null unique,"
            "  object_number integer not null,"
            "  created_at timestamptz not null default now())"
        )
    finally:
        await connection.close()

    await shape_control_schema(empty_database)

    pool = await asyncpg.create_pool(empty_database, min_size=1, max_size=2)
    try:
        invites = InviteCodes(pool=pool)
        await invites.mint(3, "founder@rebuilt.io")
        assert isinstance(await invites.redeem("rebuilt.io", uuid4()), InviteAccepted)
    finally:
        await pool.close()


async def test_the_verb_drops_the_columns_a_verification_code_needed(empty_database: str) -> None:
    """WorkOS holds the code now, so the claim ledger keeps no hash and counts no attempts, and it
    gains the mark that says whether a claim opened its workspace or joined one. The reshape lives
    with the shaping verb, so a claim written afterwards reads back through the store — and
    completes through it, which is the write every signup ends on."""
    connection = await asyncpg.connect(empty_database)
    try:
        await connection.execute(f"create schema {SCHEMA}")
        await connection.execute(
            f"create table {SCHEMA}.onboard_claim ("
            "  id uuid primary key,"
            "  email text not null,"
            "  email_domain text not null,"
            "  code_hash text not null,"
            "  surface text not null,"
            "  surface_ref text not null,"
            "  attempts integer not null default 0,"
            "  expires_at timestamptz not null,"
            "  verified_at timestamptz,"
            "  resulting_workspace_id text,"
            "  invite_id uuid,"
            "  created_at timestamptz not null default now())"
        )
    finally:
        await connection.close()

    await shape_control_schema(empty_database)

    pool = await asyncpg.create_pool(empty_database, min_size=1, max_size=2)
    try:
        columns = {
            row["column_name"]
            for row in await pool.fetch(
                "select column_name from information_schema.columns"
                " where table_schema = $1 and table_name = 'onboard_claim'",
                SCHEMA,
            )
        }
        assert "code_hash" not in columns
        assert "attempts" not in columns
        store = OnboardStore(pool=pool)
        await store.insert_claim(
            OnboardClaim(
                claim_id=uuid4(),
                email="pilot@reshaped.io",
                email_domain="reshaped.io",
                surface="ufo",
                surface_ref="reshape-proof",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                verified_at=None,
                invite_id=None,
            )
        )
        claim = await store.live_claim("ufo", "reshape-proof")
        assert claim is not None and claim.verified_at is None
        await store.complete(claim.claim_id, str(uuid4()), created_workspace=True)
        assert (
            await pool.fetchval(
                f"select created_workspace from {SCHEMA}.onboard_claim where id = $1",
                claim.claim_id,
            )
            is True
        ), (
            "a live claim ledger gains created_workspace only through RESHAPE; without it every "
            "signup raises after the workspace, the member and the agent are already written"
        )
    finally:
        await pool.close()


async def test_the_verb_carries_a_claim_keyed_delivery_across_to_its_domain(
    empty_database: str,
) -> None:
    """Delivery rows are the only record of which customers Slack has already invited, so the shape
    that re-keys them to the granted domain must translate rather than rebuild: a rebuilt ledger
    would re-materialize every delivered customer as pending and send each a second invitation. The
    proof is the delivered row surviving with its channel, its invitation, and its state — and the
    head shape's own due index surviving the name the old table's index held."""
    connection = await asyncpg.connect(empty_database)
    try:
        await connection.execute(f"create schema {SCHEMA}")
        await connection.execute(
            f"create table {SCHEMA}.onboard_claim ("
            "  id uuid primary key,"
            "  email text not null,"
            "  email_domain text not null,"
            "  surface text not null,"
            "  surface_ref text not null,"
            "  expires_at timestamptz not null,"
            "  verified_at timestamptz,"
            "  resulting_workspace_id text,"
            "  invite_id uuid,"
            "  created_at timestamptz not null default now())"
        )
        await connection.execute(
            f"create table {SCHEMA}.slack_connect_delivery ("
            "  onboard_claim_id uuid primary key"
            f"    references {SCHEMA}.onboard_claim (id) on delete cascade,"
            "  state text not null,"
            "  channel_name text not null unique,"
            "  channel_id text,"
            "  slack_invitation_id text,"
            "  invite_attempted_at timestamptz,"
            "  worker_id text,"
            "  claim_expires_at timestamptz,"
            "  next_attempt_at timestamptz,"
            "  attempts integer not null default 0,"
            "  last_error text,"
            "  created_at timestamptz not null default now(),"
            "  updated_at timestamptz not null default now(),"
            "  delivered_at timestamptz)"
        )
        await connection.execute(
            f"create index {DUE_INDEX} on {SCHEMA}.slack_connect_delivery (state, next_attempt_at)"
        )
        claim_id = uuid4()
        await connection.execute(
            f"insert into {SCHEMA}.onboard_claim (id, email, email_domain, surface, surface_ref,"
            "  expires_at, verified_at, resulting_workspace_id, invite_id)"
            " values ($1, 'founder@carried.io', 'carried.io', 'ufo', $2,"
            "  now() + interval '1 hour', now(), $3, $4)",
            claim_id,
            str(claim_id),
            str(uuid4()),
            uuid4(),
        )
        await connection.execute(
            f"insert into {SCHEMA}.slack_connect_delivery (onboard_claim_id, state, channel_name,"
            "  channel_id, slack_invitation_id, invite_attempted_at, delivered_at)"
            " values ($1, 'delivered', 'ext-carried-flyingobject', 'C0CARRIED', 'I0CARRIED',"
            "  now(), now())",
            claim_id,
        )
        flight_id = uuid4()
        await connection.execute(
            f"insert into {SCHEMA}.onboard_claim (id, email, email_domain, surface, surface_ref,"
            "  expires_at, verified_at, resulting_workspace_id, invite_id)"
            " values ($1, 'founder@inflight.io', 'inflight.io', 'ufo', $2,"
            "  now() + interval '1 hour', now(), $3, $4)",
            flight_id,
            str(flight_id),
            str(uuid4()),
            uuid4(),
        )
        await connection.execute(
            f"insert into {SCHEMA}.slack_connect_delivery (onboard_claim_id, state, channel_name,"
            "  channel_id, invite_attempted_at, worker_id, claim_expires_at)"
            " values ($1, 'claimed', 'ext-inflight-flyingobject', 'C0FLIGHT', now(),"
            "  'pod-that-is-about-to-die', now() + interval '2 minutes')",
            flight_id,
        )
    finally:
        await connection.close()

    await shape_control_schema(empty_database)

    pool = await asyncpg.create_pool(empty_database, min_size=1, max_size=2)
    try:
        row = await pool.fetchrow(
            f"select * from {SCHEMA}.slack_connect_delivery where email_domain = $1", "carried.io"
        )
        assert row is not None, "the delivered customer was dropped and will be invited again"
        assert row["email"] == "founder@carried.io"
        assert row["state"] == "delivered"
        assert row["channel_id"] == "C0CARRIED"
        assert row["slack_invitation_id"] == "I0CARRIED"
        assert await pool.fetchval(
            "select indexdef from pg_indexes where schemaname = $1 and indexname = $2",
            SCHEMA,
            DUE_INDEX,
        ), "the head shape lost its due index to the old table's index name"
        assert (
            await pool.fetchval(
                "select to_regclass($1)", f"{SCHEMA}.slack_connect_delivery_by_claim"
            )
            is None
        )

        flight = await pool.fetchrow(
            f"select * from {SCHEMA}.slack_connect_delivery where email_domain = $1", "inflight.io"
        )
        assert flight is not None
        assert flight["state"] == "pending", (
            "a row claimed by a worker the deploy killed must come back claimable; "
            "'claimed' with no lease can never be claimed again"
        )
        assert flight["worker_id"] is None
        assert flight["claim_expires_at"] is None
        assert flight["channel_id"] == "C0FLIGHT"
        assert flight["invite_attempted_at"] is not None, (
            "the marker that makes the next worker reconcile instead of inviting twice"
        )
    finally:
        await pool.close()


async def test_the_verb_frees_an_object_number_a_live_ledger_still_requires(
    empty_database: str,
) -> None:
    """A grant approved from the intake form names no waitlist object, so the column has to be
    nullable on a database that already holds the ledger — `create table if not exists` is a no-op
    there, so only RESHAPE can free it. The proof mints an unnumbered grant through the store."""
    connection = await asyncpg.connect(empty_database)
    try:
        await connection.execute(f"create schema {SCHEMA}")
        await connection.execute(
            f"create table {SCHEMA}.invite_code ("
            "  id uuid primary key,"
            "  object_number integer not null check (object_number > 0),"
            "  email text not null,"
            "  email_domain text not null,"
            "  expires_at timestamptz not null,"
            "  consumed_at timestamptz,"
            "  created_at timestamptz not null default now())"
        )
    finally:
        await connection.close()

    await shape_control_schema(empty_database)

    pool = await asyncpg.create_pool(empty_database, min_size=1, max_size=2)
    try:
        minted = await InviteCodes(pool=pool).mint(None, "founder@formco.io")
        assert minted.object_number is None
        assert isinstance(await InviteCodes(pool=pool).redeem("formco.io", uuid4()), InviteAccepted)
    finally:
        await pool.close()


def test_the_gateway_refuses_to_boot_an_unshaped_schema(
    empty_database: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The consumer end of the tear-out: a replica names the verb and dies instead of shaping the
    schema itself, so a silent re-create can never come back."""
    token_file = tmp_path / "web-identity"
    token_file.write_text("token")
    monkeypatch.setenv(POSTGRES_OWNER_DSN_ENV, empty_database)
    monkeypatch.setenv(
        SERVE_DSN_ENV, empty_database.replace("postgresql://", "postgresql+asyncpg://")
    )
    monkeypatch.setenv(TOKEN_SECRET_ENV, "test-token-secret")
    monkeypatch.setenv("UFO_WORKSPACE_BASE_URL", WORKSPACE_URL)
    monkeypatch.setenv(SES_SENDER_ENV, "no-reply@flyingobject.ai")
    monkeypatch.setenv(AWS_ROLE_ARN_ENV, "arn:aws:iam::123456789012:role/gateway-ses")
    monkeypatch.setenv(AWS_WEB_IDENTITY_TOKEN_FILE_ENV, str(token_file))
    monkeypatch.setenv(WORKOS_API_KEY_ENV, "sk_test_gateway")
    monkeypatch.setenv(WORKOS_CLIENT_ID_ENV, "client_01GATEWAY")
    monkeypatch.setenv(WORKOS_REDIRECT_URI_ENV, f"{WORKSPACE_URL}{AUTH_CALLBACK_PATH}")
    app = gateway_app()

    async def boot() -> None:
        async with app.router.lifespan_context(app):
            pass

    with pytest.raises(RuntimeError, match=r"onboard_claim is absent — run `ufo-control migrate`"):
        asyncio.run(boot())

    assert asyncio.run(_catalog(empty_database)) == []


def test_operator_verbs_refuse_an_unshaped_schema(migrate: CliRunner) -> None:
    """Every verb that reads a ledger states the same precondition the same way, so an operator who
    reaches for one before the deploy has shaped the schema is told which verb to run instead of
    reading a raw `UndefinedTableError`."""
    for argv in (
        ["invite", "cli@mintco.io", "--object", "1"],
        ["slack-connect-retry", "unshaped.io"],
    ):
        result = migrate.invoke(main, argv)
        assert result.exit_code != 0, result.output
        assert "run `ufo-control migrate`" in str(result.exception)


def test_the_deploy_shapes_the_control_schema_before_rls_bootstrap() -> None:
    """The Job both terraform environments already gate on carries the step, so the ordering is
    structural: initContainers run to completion in order (`migrate`, then this), `rls-bootstrap`
    runs after both, and the gateway Deployment waits on the Job's success."""
    assert "image: ${registry}/ufo-control:${image_tag}" in CONTROL_STEP
    assert "args: [migrate]" in CONTROL_STEP
    assert "name: UFO_CONTROL_POSTGRES_OWNER_DSN" in CONTROL_STEP
    assert "secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}" in CONTROL_STEP
    assert (
        MIGRATE_JOB.index(INIT_CONTAINERS)
        < MIGRATE_JOB.index(MIGRATE_STEP)
        < MIGRATE_JOB.index(CONTROL_SCHEMA_STEP)
        < MIGRATE_JOB.index(CONTAINERS)
        < MIGRATE_JOB.index(RLS_BOOTSTRAP_STEP)
    )
    for environment in ("testing", "prod"):
        config = (REPO / f"infra/envs/{environment}/ufo.tf").read_text()
        assert 'strcontains(path, "/jobs/ufo-migrate-")' in config
        job = config.split('resource "kubectl_manifest" "ufo_migrate" {', maxsplit=1)[1].split(
            "\n}\n", maxsplit=1
        )[0]
        assert 'key   = "status.succeeded"' in job
        assert 'value = "1"' in job
        assert "depends_on = [kubectl_manifest.ufo_migrate, module.platform]" in config


async def _catalog(dsn: str) -> list[tuple[str, str, int]]:
    """Every table and index of the schema by kind, name *and* oid: a second shaping that dropped
    and re-created anything shows up as a new oid, so `==` proves untouched rather than
    equivalent."""
    connection = await asyncpg.connect(dsn)
    try:
        rows = await connection.fetch(RELATIONS, SCHEMA)
    finally:
        await connection.close()
    return [(row["relkind"], row["relname"], row["oid"]) for row in rows]


async def _recreate_database(admin_dsn: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'drop database if exists "{SHAPED_DATABASE}" with (force)')
        await connection.execute(f'create database "{SHAPED_DATABASE}"')
    finally:
        await connection.close()


async def _drop_database(admin_dsn: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'drop database if exists "{SHAPED_DATABASE}" with (force)')
    finally:
        await connection.close()
