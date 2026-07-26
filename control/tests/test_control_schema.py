"""`ufo-control migrate` against a real Postgres that holds none of the schema yet.

The deploy shapes `ufo_control` once, before any gateway replica starts, so the proof is that shape:
an empty database brought to head by the verb the Job runs, the same verb repeated changing nothing,
concurrent writers agreeing on one schema rather than racing, and a gateway that refuses to boot
against a database the verb never ran on — never re-creating what it found missing.
"""

import asyncio
from collections.abc import Iterator
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
from ufo_control.gateway_invite import InviteAccepted, InviteCodes
from ufo_control.gateway_shared import SERVE_DSN_ENV
from ufo_control.gateway_slack_connect import DUE_INDEX
from ufo_control.gateway_store import ACTIVE_INDEX, SCHEMA
from ufo_control.gateway_token import TOKEN_SECRET_ENV
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
LIVE_INVITE_INDEX = "invite_code_live_object"
EXPECTED_TABLES = frozenset({"onboard_claim", "invite_code", "slack_connect_delivery"})
EXPECTED_INDEXES = frozenset({ACTIVE_INDEX, LIVE_INVITE_INDEX, DUE_INDEX})
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


async def test_the_verb_rebuilds_an_unnumbered_invite_ledger(empty_database: str) -> None:
    """A ledger that cannot name objects predates `object_number`; the reshape that voids it lives
    with the shaping verb, so the code it rebuilds for is mintable and redeemable afterwards."""
    connection = await asyncpg.connect(empty_database)
    try:
        await connection.execute(f"create schema {SCHEMA}")
        await connection.execute(
            f"create table {SCHEMA}.invite_code ("
            "  id uuid primary key,"
            "  code_hash text not null unique,"
            "  used_at timestamptz,"
            "  created_at timestamptz not null default now())"
        )
    finally:
        await connection.close()

    await shape_control_schema(empty_database)

    pool = await asyncpg.create_pool(empty_database, min_size=1, max_size=2)
    try:
        invites = InviteCodes(pool=pool)
        minted = await invites.mint(3)
        assert isinstance(await invites.redeem(minted.code, uuid4()), InviteAccepted)
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
    for argv in (["invite", "1"], ["slack-connect-retry", str(uuid4())]):
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
