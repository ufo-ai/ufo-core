"""The hosted workspace boundary against a real Postgres database."""

import asyncio
import logging
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import asyncpg
import pytest
import sqlalchemy as sa
from click.testing import CliRunner
from fake_workos import MAGIC_CODE, FakeVerifier
from ufo.balance import read_balance
from ufo.bearer import verify_token
from ufo.config import DatabaseConfig
from ufo.db import (
    apply_migrations,
    current_workspace,
    dispose_db,
    init_db,
    init_owner_db,
    owner_tx,
    workspace_tx,
)
from ufo.onboarding import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_PROMPT
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME
from ufo.seats import create_member
from ufo.workspace import ws

from ufo_control import gateway, rls
from ufo_control.gateway import Onboarding
from ufo_control.gateway_claim import ClaimWorkflow
from ufo_control.gateway_directives import PROMPT
from ufo_control.gateway_email import CONSOLE_EMAIL_MODE, EMAIL_MODE_ENV, WorkEmailPolicy
from ufo_control.gateway_invite import InviteAccepted, InviteCodes
from ufo_control.gateway_shared import (
    SIGNUP_GRANT_MICRO_USD,
    SIGNUP_RESERVE_MICRO_USD,
    SharedWorkspaces,
)
from ufo_control.gateway_store import OnboardStore
from ufo_control.main import main
from ufo_control.rls import (
    PG_ROLE_SEED_ENV,
    POSTGRES_OWNER_DSN_ENV,
    SERVE_ROLE,
    WORKSPACE_GUC,
    bootstrap_policies,
    ensure_serve_role,
    serve_dsn,
)
from ufo_control.schema import shape_control_schema

SHARED_TOKEN_SECRET = "shared-service-secret"
SHARED_WORKSPACE_URL = "https://app.flyingobject.ai"
PG_HOST = "127.0.0.1"
PG_PORT = 5544
POSTGRES_HOST = f"{PG_HOST}:{PG_PORT}"
ADMIN_DSN = f"postgresql://admin:admin@{POSTGRES_HOST}/postgres"
APP_DATABASE = "ufo_rls_test"
ADMIN_APP_DSN = f"postgresql://admin:admin@{POSTGRES_HOST}/{APP_DATABASE}"
FAILLOUD_DATABASE = "ufo_rls_failloud"
LOCKWEDGE_DATABASE = "ufo_rls_lockwedge"
DRIFT_DATABASE = "ufo_rls_drift"
CONFORMANT_LOCK_DATABASE = "ufo_rls_conformant_lock"
OWNER_ROLE = "ufo_owner"
OWNER_PASSWORD = "ownerpw"
SEED = "rls-test-seed"
PACK = "assistant_hosted"
RLS_REQUIRED_ENV = "UFO_RLS_REQUIRED"


def _reachable() -> bool:
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=0.5):
            return True
    except OSError:
        if os.environ.get(RLS_REQUIRED_ENV):
            raise RuntimeError(
                f"{RLS_REQUIRED_ENV} is set but Postgres on {POSTGRES_HOST} is unreachable"
            ) from None
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"postgres on :{PG_PORT} not reachable")


def _libpq(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _owner_app_dsn(scheme: str, database: str) -> str:
    return f"{scheme}://{OWNER_ROLE}:{OWNER_PASSWORD}@{POSTGRES_HOST}/{database}"


async def _reset(database: str) -> None:
    connection = await asyncpg.connect(ADMIN_DSN)
    try:
        for name in (database, f"{database}_dbos"):
            await connection.execute(f'drop database if exists "{name}" with (force)')
        for role in ("ufo_serve", OWNER_ROLE):
            if await connection.fetchval("select 1 from pg_roles where rolname = $1", role):
                await connection.execute(f'drop owned by "{role}"')
                await connection.execute(f'drop role "{role}"')
        await connection.execute(
            f"create role \"{OWNER_ROLE}\" login password '{OWNER_PASSWORD}' createrole createdb"
        )
        await connection.execute(f'create database "{database}" owner "{OWNER_ROLE}"')
    finally:
        await connection.close()
    app = await asyncpg.connect(ADMIN_DSN, database=database)
    try:
        await app.execute(f'grant all on schema public to "{OWNER_ROLE}"')
    finally:
        await app.close()


async def _seed_workspaces(dsn: str, workspace_ids: tuple[str, ...]) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        for index, workspace_id in enumerate(workspace_ids):
            parsed = UUID(workspace_id)
            await connection.execute(
                "insert into workspace (id, created_at, updated_at) values ($1, now(), now())",
                parsed,
            )
            await connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values ($1, $2, $3, now(), now())",
                uuid4(),
                parsed,
                f"owner{index}@example.test",
            )
    finally:
        await connection.close()


@dataclass(frozen=True)
class SharedRoleEnv:
    workspaces: tuple[str, ...]
    owner_dsn: str


@pytest.fixture(scope="module")
def shared_role_env() -> Iterator[SharedRoleEnv]:
    previous_seed = os.environ.get(PG_ROLE_SEED_ENV)
    os.environ[PG_ROLE_SEED_ENV] = SEED
    asyncio.run(_reset(APP_DATABASE))
    apply_migrations(_owner_app_dsn("postgresql+asyncpg", APP_DATABASE), PACK)
    owner_dsn = _owner_app_dsn("postgresql", APP_DATABASE)
    asyncio.run(shape_control_schema(owner_dsn))
    asyncio.run(bootstrap_policies(owner_dsn))
    asyncio.run(ensure_serve_role(owner_dsn))
    workspaces = (str(uuid4()), str(uuid4()))
    asyncio.run(_seed_workspaces(owner_dsn, workspaces))
    init_db(serve_dsn(POSTGRES_HOST, APP_DATABASE))
    init_owner_db(_owner_app_dsn("postgresql+asyncpg", APP_DATABASE))
    try:
        yield SharedRoleEnv(workspaces=workspaces, owner_dsn=owner_dsn)
    finally:
        asyncio.run(dispose_db())
        asyncio.run(_reset(APP_DATABASE))
        if previous_seed is None:
            os.environ.pop(PG_ROLE_SEED_ENV, None)
        else:
            os.environ[PG_ROLE_SEED_ENV] = previous_seed


def test_rls_bootstrap_cli_is_idempotent(shared_role_env: SharedRoleEnv) -> None:
    previous = os.environ.get(POSTGRES_OWNER_DSN_ENV)
    os.environ[POSTGRES_OWNER_DSN_ENV] = shared_role_env.owner_dsn
    try:
        result = CliRunner().invoke(main, ["rls-bootstrap"])
    finally:
        if previous is None:
            os.environ.pop(POSTGRES_OWNER_DSN_ENV, None)
        else:
            os.environ[POSTGRES_OWNER_DSN_ENV] = previous
    assert result.exit_code == 0, result.output
    assert "rls policies at head" in result.output


async def test_rds_style_owner_bootstraps_serve_role(shared_role_env: SharedRoleEnv) -> None:
    owner = await asyncpg.connect(shared_role_env.owner_dsn)
    try:
        role = await owner.fetchrow(
            "select rolsuper, rolcreaterole, rolcreatedb, "
            "pg_has_role(current_user, $1, 'SET'), pg_has_role(current_user, $1, 'USAGE') "
            "from pg_roles where rolname = current_user",
            SERVE_ROLE,
        )
        assert role == (False, True, True, True, False)
        assert (
            await owner.fetchval(
                "select pg_get_userbyid(datdba) from pg_database where datname = $1",
                f"{APP_DATABASE}_dbos",
            )
            == SERVE_ROLE
        )
    finally:
        await owner.close()
    await ensure_serve_role(shared_role_env.owner_dsn)
    serve = await asyncpg.connect(_libpq(serve_dsn(POSTGRES_HOST, APP_DATABASE)))
    try:
        workspace_id = str(uuid4())
        assert (
            await serve.fetchval(f"select set_config('{WORKSPACE_GUC}', $1, false)", workspace_id)
            == workspace_id
        )
    finally:
        await serve.close()


async def test_bootstrap_fails_loud_on_an_unpoliced_table() -> None:
    reset = await asyncpg.connect(ADMIN_DSN)
    try:
        await reset.execute(f'drop database if exists "{FAILLOUD_DATABASE}" with (force)')
        await reset.execute(f'create database "{FAILLOUD_DATABASE}"')
    finally:
        await reset.close()
    dsn = f"postgresql://admin:admin@{POSTGRES_HOST}/{FAILLOUD_DATABASE}"
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("create table workspace (id uuid primary key)")
        await connection.execute("create table z_rogue_widget (id int primary key)")
    finally:
        await connection.close()
    try:
        with pytest.raises(RuntimeError, match="z_rogue_widget"):
            await bootstrap_policies(dsn)
        inspection = await asyncpg.connect(dsn)
        try:
            assert await inspection.fetchval(
                "select relrowsecurity from pg_class where relname = 'workspace'"
            )
            assert await inspection.fetchval(
                "select 1 from pg_policies where tablename = 'workspace'"
            )
        finally:
            await inspection.close()
    finally:
        cleanup = await asyncpg.connect(ADMIN_DSN)
        try:
            await cleanup.execute(f'drop database if exists "{FAILLOUD_DATABASE}" with (force)')
        finally:
            await cleanup.close()


async def test_bootstrap_fails_fast_when_a_table_lock_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rls, "LOCK_TIMEOUT", "200ms")
    reset = await asyncpg.connect(ADMIN_DSN)
    try:
        await reset.execute(f'drop database if exists "{LOCKWEDGE_DATABASE}" with (force)')
        await reset.execute(f'create database "{LOCKWEDGE_DATABASE}"')
    finally:
        await reset.close()
    dsn = f"postgresql://admin:admin@{POSTGRES_HOST}/{LOCKWEDGE_DATABASE}"
    setup = await asyncpg.connect(dsn)
    try:
        await setup.execute("create table workspace (id uuid primary key)")
    finally:
        await setup.close()
    holder = await asyncpg.connect(dsn)
    transaction = holder.transaction()
    await transaction.start()
    try:
        await holder.execute("lock table workspace in access share mode")
        with pytest.raises(RuntimeError, match="lock on table 'workspace' timed out") as caught:
            await bootstrap_policies(dsn)
        assert "idle in transaction" in str(caught.value)
        assert "lock table workspace" in str(caught.value)
    finally:
        await transaction.rollback()
        await holder.close()
        cleanup = await asyncpg.connect(ADMIN_DSN)
        try:
            await cleanup.execute(f'drop database if exists "{LOCKWEDGE_DATABASE}" with (force)')
        finally:
            await cleanup.close()


async def test_shared_bootstrap_provisions_the_dbos_database(
    shared_role_env: SharedRoleEnv,
) -> None:
    system_url = DatabaseConfig(url=serve_dsn(POSTGRES_HOST, APP_DATABASE)).system_url
    connection = await asyncpg.connect(_libpq(system_url.replace("+psycopg", "+asyncpg", 1)))
    try:
        assert await connection.fetchval("select current_database()") == f"{APP_DATABASE}_dbos"
        await connection.execute("create schema dbos")
        await connection.execute("drop schema dbos")
    finally:
        await connection.close()


async def test_new_tables_receive_no_serve_grant_before_policy_bootstrap(
    shared_role_env: SharedRoleEnv,
) -> None:
    table = "late_scoped_record"
    workspace_id = shared_role_env.workspaces[0]
    owner = await asyncpg.connect(shared_role_env.owner_dsn)
    try:
        await owner.execute(
            f'create table "{table}" (id uuid primary key, workspace_id uuid not null)'
        )
        serve = await asyncpg.connect(_libpq(serve_dsn(POSTGRES_HOST, APP_DATABASE)))
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await serve.fetch(f'select id from "{table}"')
        finally:
            await serve.close()

        await bootstrap_policies(shared_role_env.owner_dsn)
        await ensure_serve_role(shared_role_env.owner_dsn)

        serve = await asyncpg.connect(_libpq(serve_dsn(POSTGRES_HOST, APP_DATABASE)))
        try:
            await serve.execute(f"select set_config('{WORKSPACE_GUC}', '{workspace_id}', false)")
            assert await serve.fetch(f'select id from "{table}"') == []
        finally:
            await serve.close()
    finally:
        await owner.execute(f'drop table if exists "{table}"')
        await owner.close()


async def test_shared_role_scopes_each_transaction(shared_role_env: SharedRoleEnv) -> None:
    for workspace_id in shared_role_env.workspaces:
        with ws(UUID(workspace_id)):
            async with workspace_tx() as connection:
                rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
                assert [str(row.id) for row in rows] == [workspace_id]
                members = (await connection.execute(sa.select(tables.member.c.workspace_id))).all()
                assert {str(row.workspace_id) for row in members} == {workspace_id}


async def test_shared_role_rejects_a_foreign_workspace_write(
    shared_role_env: SharedRoleEnv,
) -> None:
    own, foreign = shared_role_env.workspaces
    connection = await asyncpg.connect(_libpq(serve_dsn(POSTGRES_HOST, APP_DATABASE)))
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute(f"set local {WORKSPACE_GUC} = '{own}'")
        with pytest.raises(asyncpg.PostgresError, match="row-level security"):
            await connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values ($1, $2, $3, now(), now())",
                uuid4(),
                UUID(foreign),
                "intruder@example.test",
            )
    finally:
        await transaction.rollback()
        await connection.close()


async def test_shared_role_without_workspace_fails_closed(
    shared_role_env: SharedRoleEnv,
) -> None:
    assert current_workspace.get() is None
    with pytest.raises(Exception) as caught:
        async with workspace_tx() as connection:
            await connection.execute(sa.select(tables.workspace.c.id))
    assert WORKSPACE_GUC in str(caught.value)


async def _members_in(workspace_id: str) -> list[str]:
    """Emails of the workspace's members, asserting each holds a seat — the hosted join's
    auto-seat invariant while no limit binds."""
    with ws(UUID(workspace_id)):
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.member.c.email, tables.member.c.seated_at).order_by(
                        tables.member.c.email
                    )
                )
            ).all()
    assert all(row.seated_at is not None for row in rows)
    return [row.email for row in rows]


async def test_shared_create_and_join_write_workspace_and_members(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        assert await shared.choices("sharedco.io", "founder@sharedco.io") == ()
        founder = await shared.create("sharedco.io", "Founder@Sharedco.io")
        workspace_id = founder.workspace_id
        assert workspace_id == str(uuid5(NAMESPACE_DNS, "sharedco.io"))
        assert founder.admin
        founder_choice = await shared.choices("sharedco.io", "founder@sharedco.io")
        assert [(str(choice.workspace_id), choice.label) for choice in founder_choice] == [
            (workspace_id, "sharedco.io")
        ]
        assert await shared.join(founder_choice[0], "sharedco.io", "founder@sharedco.io") == founder
        colleague_choice = await shared.choices("sharedco.io", "colleague@sharedco.io")
        colleague = await shared.join(colleague_choice[0], "sharedco.io", "colleague@sharedco.io")
        assert colleague.workspace_id == workspace_id
        assert not colleague.admin
        assert await _members_in(workspace_id) == ["colleague@sharedco.io", "founder@sharedco.io"]
        third_choice = await shared.choices("sharedco.io", "third@sharedco.io")
        third = await shared.join(third_choice[0], "sharedco.io", "third@sharedco.io")
        assert third.workspace_id == workspace_id
        with ws(UUID(workspace_id)):
            async with workspace_tx() as connection:
                seated = (
                    await connection.execute(
                        sa.select(tables.member.c.email, tables.member.c.seated_at).order_by(
                            tables.member.c.email
                        )
                    )
                ).all()
        assert [(row.email, row.seated_at is not None) for row in seated] == [
            ("colleague@sharedco.io", True),
            ("founder@sharedco.io", True),
            ("third@sharedco.io", True),
        ]
    finally:
        await pool.close()


async def _default_agents_in(workspace_id: str) -> list[tuple[str, str, str]]:
    with ws(UUID(workspace_id)):
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.agent.c.name, tables.agent.c.prompt, tables.agent.c.model)
                    .where(tables.agent.c.name == DEFAULT_AGENT_NAME)
                    .order_by(tables.agent.c.name)
                )
            ).all()
    return [(row.name, row.prompt, row.model) for row in rows]


async def _balance_in(workspace_id: str) -> tuple[int, int, int]:
    with ws(UUID(workspace_id)):
        async with workspace_tx() as connection:
            current = await read_balance(connection, UUID(workspace_id))
            purchases = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.balance_purchase)
                )
            ).scalar_one()
    assert current is not None
    return current.balance_micro_usd, current.reserve_micro_usd, int(purchases)


async def test_shared_create_grants_the_signup_balance(shared_role_env: SharedRoleEnv) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        workspace_id = (
            await shared.create("balancegrant.io", "founder@balancegrant.io")
        ).workspace_id
        assert await _balance_in(workspace_id) == (
            SIGNUP_GRANT_MICRO_USD,
            SIGNUP_RESERVE_MICRO_USD,
            1,
        )
    finally:
        await pool.close()


async def test_a_second_join_never_re_grants(shared_role_env: SharedRoleEnv) -> None:
    """The reference is derived from the workspace, so every later entry through `_ensure` — a
    second address on the domain, or the founder returning — finds the grant already delivered."""
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        workspace_id = (await shared.create("topupgrant.io", "founder@topupgrant.io")).workspace_id
        choice = (await shared.choices("topupgrant.io", "second@topupgrant.io"))[0]
        await shared.join(choice, "topupgrant.io", "second@topupgrant.io")
        await shared.create("topupgrant.io", "third@topupgrant.io")
        assert await _balance_in(workspace_id) == (
            SIGNUP_GRANT_MICRO_USD,
            SIGNUP_RESERVE_MICRO_USD,
            1,
        )
    finally:
        await pool.close()


async def test_a_workspace_that_predates_the_balance_is_never_funded_by_a_join(
    shared_role_env: SharedRoleEnv,
) -> None:
    """A workspace created before this feature has no balance row, which means unrestricted. The
    grant must not reach it: `join` routes a member who is not yet seated through `_ensure`, so the
    next new colleague to sign in would otherwise hand a long-running workspace a starting balance
    and, with it, a ceiling and a reserve it never had."""
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        workspace_id = uuid5(NAMESPACE_DNS, "predates.io")
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.workspace).values(
                        id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                    )
                )
        await shared.create("predates.io", "founder@predates.io")
        with ws(workspace_id):
            async with workspace_tx() as connection:
                assert await read_balance(connection, workspace_id) is None
    finally:
        await pool.close()


async def test_shared_create_seeds_the_default_agent(shared_role_env: SharedRoleEnv) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        workspace_id = (await shared.create("agentco.io", "founder@agentco.io")).workspace_id
        expected = [(DEFAULT_AGENT_NAME, DEFAULT_AGENT_PROMPT, DEFAULT_AGENT_MODEL)]
        assert await _default_agents_in(workspace_id) == expected
        choice = (await shared.choices("agentco.io", "founder@agentco.io"))[0]
        assert (await shared.join(choice, "agentco.io", "founder@agentco.io")).workspace_id == (
            workspace_id
        )
        assert await _default_agents_in(workspace_id) == expected
    finally:
        await pool.close()


async def test_shared_domain_lookup_rejects_ambiguous_workspaces(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    workspace_ids = (uuid4(), uuid4())
    async with pool.acquire() as connection:
        for workspace_id in workspace_ids:
            await connection.execute(
                "insert into workspace (id, created_at, updated_at) values ($1, now(), now())",
                workspace_id,
            )
            await connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values ($1, $2, $3, now(), now())",
                uuid4(),
                workspace_id,
                "owner@ambiguousco.io",
            )
    try:
        shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
        with pytest.raises(RuntimeError, match=r"domain ambiguousco\.io maps to 2 workspaces"):
            await shared.choices("ambiguousco.io", "owner@ambiguousco.io")
        assert not await pool.fetchval(
            "select 1 from workspace where id = $1", uuid5(NAMESPACE_DNS, "ambiguousco.io")
        )
    finally:
        await pool.close()


async def test_shared_resolution_finds_the_workspace_an_outside_address_was_added_to(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        host = await shared.create("hostco.io", "founder@hostco.io")
        with ws(UUID(host.workspace_id)):
            async with workspace_tx() as connection:
                await create_member(connection, UUID(host.workspace_id), "Contractor@Outside.dev")
        choices = await shared.choices("outside.dev", "contractor@outside.dev")
        assert [(str(choice.workspace_id), choice.label) for choice in choices] == [
            (host.workspace_id, "hostco.io")
        ]
        joined = await shared.join(choices[0], "outside.dev", "contractor@outside.dev")
        assert joined.workspace_id == host.workspace_id
        assert not joined.admin
        assert await _members_in(host.workspace_id) == [
            "contractor@outside.dev",
            "founder@hostco.io",
        ]
        assert not await pool.fetchval(
            "select 1 from workspace where id = $1", uuid5(NAMESPACE_DNS, "outside.dev")
        )
    finally:
        await pool.close()


async def test_shared_join_does_not_recreate_a_removed_exact_membership(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        host = await shared.create("removedco.io", "founder@removedco.io")
        with ws(UUID(host.workspace_id)):
            async with workspace_tx() as connection:
                await create_member(connection, UUID(host.workspace_id), "gone@outside.dev")
        choice = (await shared.choices("outside.dev", "gone@outside.dev"))[0]
        with ws(UUID(host.workspace_id)):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.delete(tables.member).where(tables.member.c.email == "gone@outside.dev")
                )
        with pytest.raises(RuntimeError, match="is no longer a member"):
            await shared.join(choice, "outside.dev", "gone@outside.dev")
        assert await _members_in(host.workspace_id) == ["founder@removedco.io"]
    finally:
        await pool.close()


async def test_shared_resolution_offers_the_domain_workspace_and_exact_membership(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        host = await shared.create("addedco.io", "founder@addedco.io")
        with ws(UUID(host.workspace_id)):
            async with workspace_tx() as connection:
                await create_member(connection, UUID(host.workspace_id), "Alice@Bigco.io")
        own = await shared.create("bigco.io", "founder@bigco.io")
        assert own.workspace_id != host.workspace_id
        choices = await shared.choices("bigco.io", "alice@bigco.io")
        assert [(str(choice.workspace_id), choice.label) for choice in choices] == [
            (host.workspace_id, "addedco.io"),
            (own.workspace_id, "bigco.io"),
        ]
        joined = await shared.join(choices[1], "bigco.io", "alice@bigco.io")
        assert joined.workspace_id == own.workspace_id
        assert not joined.admin
        assert await _members_in(own.workspace_id) == ["alice@bigco.io", "founder@bigco.io"]
        assert await _members_in(host.workspace_id) == ["alice@bigco.io", "founder@addedco.io"]
    finally:
        await pool.close()


async def test_shared_create_founds_a_domain_over_a_roster_holding_the_address(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        host = await shared.create("grantedhost.io", "founder@grantedhost.io")
        with ws(UUID(host.workspace_id)):
            async with workspace_tx() as connection:
                await create_member(connection, UUID(host.workspace_id), "boss@granted.io")
        founded = await shared.create("granted.io", "boss@granted.io")
        assert founded.workspace_id == str(uuid5(NAMESPACE_DNS, "granted.io"))
        assert founded.admin
    finally:
        await pool.close()


async def test_shared_resolution_offers_every_exact_membership(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        first = await shared.create("firstco.io", "founder@firstco.io")
        second = await shared.create("secondco.io", "founder@secondco.io")
        for workspace_id in (first.workspace_id, second.workspace_id):
            with ws(UUID(workspace_id)):
                async with workspace_tx() as connection:
                    await create_member(connection, UUID(workspace_id), "contractor@twice.dev")
        choices = await shared.choices("twice.dev", "contractor@twice.dev")
        assert [(str(choice.workspace_id), choice.label) for choice in choices] == [
            (first.workspace_id, "firstco.io"),
            (second.workspace_id, "secondco.io"),
        ]
    finally:
        await pool.close()


async def test_shared_onboard_creates_then_joins_a_workspace(
    shared_role_env: SharedRoleEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SHARED_TOKEN_SECRET)
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    store = OnboardStore(pool=pool)
    invites = InviteCodes(pool=pool)
    async with pool.acquire() as connection:
        await connection.execute("truncate ufo_control.onboard_claim cascade")
        await connection.execute("truncate ufo_control.invite_code")
    verifier = FakeVerifier()
    flow = Onboarding(
        claims=ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), verifier=verifier),
        store=store,
        workspaces=SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool),
        invites=invites,
        verifier=verifier,
        token_secret=SHARED_TOKEN_SECRET,
        apex_host="flyingobject.ai",
        invite_required=True,
    )
    try:
        await invites.mint(1, "boss@sharedtwo.io")
        await flow.advance("ufo", "sess", "", b"")
        await flow.advance("ufo", "sess", "boss@sharedtwo.io", b"")
        signed_in = await flow.advance("ufo", "sess", MAGIC_CODE, b"")
        await flow.advance("ufo", "sess2", "", b"")
        await flow.advance("ufo", "sess2", "mate@sharedtwo.io", b"")
        joined = await flow.advance("ufo", "sess2", MAGIC_CODE, b"")
    finally:
        await pool.close()
    workspace_id = str(uuid5(NAMESPACE_DNS, "sharedtwo.io"))
    directives = dict(
        line.split("\t", 1) for line in signed_in.decode().splitlines() if "\t" in line
    )
    assert verify_token(directives["token"], UUID(workspace_id)) == "boss@sharedtwo.io"
    assert directives["workspace"] == SHARED_WORKSPACE_URL
    joined_directives = dict(
        line.split("\t", 1) for line in joined.decode().splitlines() if "\t" in line
    )
    assert "invite" not in joined.decode()
    assert verify_token(joined_directives["token"], UUID(workspace_id)) == "mate@sharedtwo.io"
    assert await _members_in(workspace_id) == ["boss@sharedtwo.io", "mate@sharedtwo.io"]
    assert directives["choose"] == "\t".join(
        (
            gateway.FIRST_MOVE_PROMPT,
            gateway.SLACK_CHOICE,
            gateway.BILLING_CHOICE,
            gateway.TOUR_CHOICE,
        )
    ), "the admin's first move leads with Slack"
    assert directives["slack"] == gateway.SLACK_CHOICE
    assert "ask" not in directives
    assert joined_directives["ask"] == PROMPT
    assert "choose" not in joined_directives
    assert "slack" not in joined_directives, "a teammate cannot install, so is never told to"


def test_invite_cli_rejects_a_nonpositive_object_number() -> None:
    result = CliRunner().invoke(main, ["invite", "cli@mintco.io", "--object", "0"])
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_invite_cli_grants_a_redeemable_domain(
    shared_role_env: SharedRoleEnv,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The verb delivers the invitation rather than printing it, so its own output names only the
    grant and the message is read where the sender put it."""
    monkeypatch.setenv(EMAIL_MODE_ENV, CONSOLE_EMAIL_MODE)
    previous = os.environ.get(POSTGRES_OWNER_DSN_ENV)
    os.environ[POSTGRES_OWNER_DSN_ENV] = shared_role_env.owner_dsn
    try:
        with caplog.at_level(logging.INFO):
            granted = CliRunner().invoke(main, ["invite", "cli@mintco.io", "--object", "42"])
        refused = CliRunner().invoke(main, ["invite", "someone@gmail.com", "--object", "43"])
    finally:
        if previous is None:
            os.environ.pop(POSTGRES_OWNER_DSN_ENV, None)
        else:
            os.environ[POSTGRES_OWNER_DSN_ENV] = previous
    assert granted.exit_code == 0, granted.output
    assert "object #42 granted to cli@mintco.io" in granted.output
    assert "Your ufo invite" in caplog.text
    assert "Sign in as cli@mintco.io." in caplog.text
    assert "Anyone at mintco.io can sign in with the same invite." in caplog.text
    assert "code:" not in caplog.text
    assert asyncio.run(_redeems(shared_role_env.owner_dsn, "mintco.io"))

    assert refused.exit_code != 0, refused.output
    assert "gmail.com is not a work email domain" in refused.output


async def _redeems(dsn: str, domain: str) -> bool:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        claim_id = uuid4()
        await pool.execute(
            "insert into ufo_control.onboard_claim "
            "(id, email, email_domain, surface, surface_ref, expires_at) "
            "values ($1, $2, $3, $4, $5, now() + interval '15 minutes')",
            claim_id,
            f"cli@{domain}",
            domain,
            "ufo",
            "cli-mint-proof",
        )
        outcome = await InviteCodes(pool=pool).redeem(domain, claim_id)
        return isinstance(outcome, InviteAccepted)
    finally:
        await pool.close()


async def test_owner_tx_enumerates_every_workspace(shared_role_env: SharedRoleEnv) -> None:
    assert current_workspace.get() is None
    async with owner_tx() as connection:
        rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
    assert set(shared_role_env.workspaces) <= {str(row.id) for row in rows}


async def test_bootstrap_skips_conformant_tables_without_locking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A table whose policy already matches is never locked: the second bootstrap succeeds while a
    held ACCESS SHARE lock would block any DDL — the routine no-delta deploy stops racing the live
    fleet entirely."""
    monkeypatch.setattr(rls, "LOCK_TIMEOUT", "200ms")
    reset = await asyncpg.connect(ADMIN_DSN)
    try:
        await reset.execute(f'drop database if exists "{LOCKWEDGE_DATABASE}" with (force)')
        await reset.execute(f'create database "{LOCKWEDGE_DATABASE}"')
    finally:
        await reset.close()
    dsn = f"postgresql://admin:admin@{POSTGRES_HOST}/{LOCKWEDGE_DATABASE}"
    setup = await asyncpg.connect(dsn)
    try:
        await setup.execute("create table workspace (id uuid primary key)")
    finally:
        await setup.close()
    await bootstrap_policies(dsn)
    holder = await asyncpg.connect(dsn)
    transaction = holder.transaction()
    await transaction.start()
    try:
        await holder.execute("lock table workspace in access share mode")
        await bootstrap_policies(dsn)
    finally:
        await transaction.rollback()
        await holder.close()
        cleanup = await asyncpg.connect(ADMIN_DSN)
        try:
            await cleanup.execute(f'drop database if exists "{LOCKWEDGE_DATABASE}" with (force)')
        finally:
            await cleanup.close()


async def test_bootstrap_fails_fast_when_the_conformant_check_itself_is_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_conformant`'s catalog read resolves `pg_policies.qual`/`with_check` via `pg_get_expr`,
    which takes an ACCESS SHARE lock on the table — so a conformant table blocked behind a
    concurrent ACCESS EXCLUSIVE (a second in-flight bootstrap, a live migration) must surface the
    same `_lock_holders`-backed RuntimeError as the DDL path, not a raw LockNotAvailableError."""
    monkeypatch.setattr(rls, "LOCK_TIMEOUT", "200ms")
    reset = await asyncpg.connect(ADMIN_DSN)
    try:
        await reset.execute(f'drop database if exists "{CONFORMANT_LOCK_DATABASE}" with (force)')
        await reset.execute(f'create database "{CONFORMANT_LOCK_DATABASE}"')
    finally:
        await reset.close()
    dsn = f"postgresql://admin:admin@{POSTGRES_HOST}/{CONFORMANT_LOCK_DATABASE}"
    setup = await asyncpg.connect(dsn)
    try:
        await setup.execute("create table workspace (id uuid primary key)")
    finally:
        await setup.close()
    await bootstrap_policies(dsn)
    holder = await asyncpg.connect(dsn)
    transaction = holder.transaction()
    await transaction.start()
    try:
        await holder.execute("lock table workspace in access exclusive mode")
        with pytest.raises(RuntimeError, match="lock on table 'workspace' timed out") as caught:
            await bootstrap_policies(dsn)
        assert "idle in transaction" in str(caught.value)
    finally:
        await transaction.rollback()
        await holder.close()
        cleanup = await asyncpg.connect(ADMIN_DSN)
        try:
            await cleanup.execute(
                f'drop database if exists "{CONFORMANT_LOCK_DATABASE}" with (force)'
            )
        finally:
            await cleanup.close()


async def test_bootstrap_recreates_a_drifted_policy() -> None:
    """A policy present with the right predicate but the wrong `cmd` (drift, not absence) is not
    conformant and gets re-created — proving `_conformant`'s False branch actually fires instead of
    silently leaving a stale predicate in place forever."""
    reset = await asyncpg.connect(ADMIN_DSN)
    try:
        await reset.execute(f'drop database if exists "{DRIFT_DATABASE}" with (force)')
        await reset.execute(f'create database "{DRIFT_DATABASE}"')
    finally:
        await reset.close()
    dsn = f"postgresql://admin:admin@{POSTGRES_HOST}/{DRIFT_DATABASE}"
    predicate = f"id = current_setting('{rls.WORKSPACE_GUC}')::uuid"
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("create table workspace (id uuid primary key)")
        await connection.execute("alter table workspace enable row level security")
        await connection.execute(
            f"create policy {rls.POLICY_NAME} on workspace for update "
            f"using ({predicate}) with check ({predicate})"
        )
        assert not await rls._conformant(connection, "workspace")
    finally:
        await connection.close()
    try:
        await bootstrap_policies(dsn)
        inspection = await asyncpg.connect(dsn)
        try:
            row = await inspection.fetchrow(
                "select cmd from pg_policies where tablename = 'workspace'"
            )
            assert row["cmd"] == "ALL"
        finally:
            await inspection.close()
    finally:
        cleanup = await asyncpg.connect(ADMIN_DSN)
        try:
            await cleanup.execute(f'drop database if exists "{DRIFT_DATABASE}" with (force)')
        finally:
            await cleanup.close()


def test_roles_carry_the_idle_in_transaction_timeout(shared_role_env: SharedRoleEnv) -> None:
    """`ensure_serve_role` stamps both fleet roles so Postgres itself terminates a session holding
    a transaction idle past the bound — no convoy head can hold table locks for minutes again."""

    async def _settings() -> dict[str, list[str]]:
        connection = await asyncpg.connect(ADMIN_APP_DSN)
        try:
            rows = await connection.fetch(
                "select rol.rolname, setting.setconfig from pg_db_role_setting setting "
                "join pg_roles rol on rol.oid = setting.setrole"
            )
            return {row["rolname"]: list(row["setconfig"] or []) for row in rows}
        finally:
            await connection.close()

    settings = asyncio.run(_settings())
    expected = f"idle_in_transaction_session_timeout={rls.IDLE_IN_TRANSACTION_TIMEOUT}"
    assert expected in settings.get(rls.SERVE_ROLE, [])
    assert expected in settings.get(OWNER_ROLE, [])
