"""The hosted workspace boundary against a real Postgres database."""

import asyncio
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import asyncpg
import pytest
import sqlalchemy as sa
from click.testing import CliRunner
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
from ufo.workspace import ws

from ufo_control import rls
from ufo_control.gateway import Onboarding
from ufo_control.gateway_claim import ClaimWorkflow
from ufo_control.gateway_email import WorkEmailPolicy
from ufo_control.gateway_invite import InviteAccepted, InviteCodes
from ufo_control.gateway_shared import SharedWorkspaces
from ufo_control.gateway_store import OnboardStore
from ufo_control.main import main
from ufo_control.rls import (
    PG_ROLE_SEED_ENV,
    POSTGRES_OWNER_DSN_ENV,
    WORKSPACE_GUC,
    bootstrap_policies,
    ensure_serve_role,
    serve_dsn,
)

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
        await connection.execute(f"create role \"{OWNER_ROLE}\" login password '{OWNER_PASSWORD}'")
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


@dataclass
class RecordingSender:
    sent: dict[str, str] = field(default_factory=dict)

    async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None:
        self.sent[email] = code

    def last_code(self, email: str) -> str:
        return self.sent[email]


@pytest.fixture(scope="module")
def shared_role_env() -> Iterator[SharedRoleEnv]:
    previous_seed = os.environ.get(PG_ROLE_SEED_ENV)
    os.environ[PG_ROLE_SEED_ENV] = SEED
    asyncio.run(_reset(APP_DATABASE))
    apply_migrations(_owner_app_dsn("postgresql+asyncpg", APP_DATABASE), PACK)
    owner_dsn = _owner_app_dsn("postgresql", APP_DATABASE)
    asyncio.run(bootstrap_policies(owner_dsn))
    asyncio.run(ensure_serve_role(ADMIN_APP_DSN))
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
    os.environ[POSTGRES_OWNER_DSN_ENV] = ADMIN_APP_DSN
    try:
        result = CliRunner().invoke(main, ["rls-bootstrap"])
    finally:
        if previous is None:
            os.environ.pop(POSTGRES_OWNER_DSN_ENV, None)
        else:
            os.environ[POSTGRES_OWNER_DSN_ENV] = previous
    assert result.exit_code == 0, result.output
    assert "rls policies at head" in result.output


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
        await ensure_serve_role(ADMIN_APP_DSN)

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


async def test_shared_ensure_writes_workspace_and_members(
    shared_role_env: SharedRoleEnv,
) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        assert not await shared.exists("sharedco.io")
        workspace_id = await shared.ensure("sharedco.io", "Founder@Sharedco.io")
        assert await shared.exists("sharedco.io")
        assert workspace_id == str(uuid5(NAMESPACE_DNS, "sharedco.io"))
        assert await shared.ensure("sharedco.io", "founder@sharedco.io") == workspace_id
        assert await shared.ensure("sharedco.io", "colleague@sharedco.io") == workspace_id
        assert await _members_in(workspace_id) == ["colleague@sharedco.io", "founder@sharedco.io"]
        with ws(UUID(workspace_id)):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.workspace)
                    .values(seat_limit=2, updated_at=sa.func.now())
                    .where(tables.workspace.c.id == UUID(workspace_id))
                )
        assert await shared.ensure("sharedco.io", "third@sharedco.io") == workspace_id
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
            ("third@sharedco.io", False),
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


async def test_shared_ensure_seeds_the_default_agent(shared_role_env: SharedRoleEnv) -> None:
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    shared = SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool)
    try:
        workspace_id = await shared.ensure("agentco.io", "founder@agentco.io")
        expected = [(DEFAULT_AGENT_NAME, DEFAULT_AGENT_PROMPT, DEFAULT_AGENT_MODEL)]
        assert await _default_agents_in(workspace_id) == expected
        assert await shared.ensure("agentco.io", "founder@agentco.io") == workspace_id
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
            await shared.exists("ambiguousco.io")
    finally:
        await pool.close()


async def test_shared_onboard_creates_then_joins_a_workspace(
    shared_role_env: SharedRoleEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UFO_TOKEN_SECRET", SHARED_TOKEN_SECRET)
    pool = await asyncpg.create_pool(shared_role_env.owner_dsn)
    store = OnboardStore(pool=pool)
    await store.ensure_table()
    invites = InviteCodes(pool=pool)
    await invites.ensure_table()
    async with pool.acquire() as connection:
        await connection.execute("truncate ufo_control.onboard_claim")
        await connection.execute("truncate ufo_control.invite_code")
    sender = RecordingSender()
    flow = Onboarding(
        claims=ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender),
        store=store,
        workspaces=SharedWorkspaces(workspace_url=SHARED_WORKSPACE_URL, pool=pool),
        invites=invites,
        token_secret=SHARED_TOKEN_SECRET,
        apex_host="flyingobject.ai",
    )
    try:
        await flow.advance("ufo", "sess", "", b"")
        await flow.advance("ufo", "sess", "boss@sharedtwo.io", b"")
        gated = await flow.advance("ufo", "sess", sender.last_code("boss@sharedtwo.io"), b"")
        assert "invite code" in gated.decode()
        signed_in = await flow.advance("ufo", "sess", (await invites.mint(1)).code, b"")
        await flow.advance("ufo", "sess2", "", b"")
        await flow.advance("ufo", "sess2", "mate@sharedtwo.io", b"")
        joined = await flow.advance("ufo", "sess2", sender.last_code("mate@sharedtwo.io"), b"")
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


def test_invite_cli_rejects_a_nonpositive_object_number() -> None:
    result = CliRunner().invoke(main, ["invite", "0"])
    assert result.exit_code != 0
    assert "not in the range" in result.output


def test_invite_cli_mints_a_redeemable_code(shared_role_env: SharedRoleEnv) -> None:
    previous = os.environ.get(POSTGRES_OWNER_DSN_ENV)
    os.environ[POSTGRES_OWNER_DSN_ENV] = shared_role_env.owner_dsn
    try:
        result = CliRunner().invoke(main, ["invite", "42"])
    finally:
        if previous is None:
            os.environ.pop(POSTGRES_OWNER_DSN_ENV, None)
        else:
            os.environ[POSTGRES_OWNER_DSN_ENV] = previous
    assert result.exit_code == 0, result.output
    assert "Subject: identification granted" in result.output
    assert "  object:   #42 → identified" in result.output
    code_line = next(
        line for line in result.output.splitlines() if line.strip().startswith("code:")
    )
    code = code_line.split()[-1]
    assert asyncio.run(_redeems(shared_role_env.owner_dsn, code))


async def _redeems(dsn: str, code: str) -> bool:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        claim_id = uuid4()
        await pool.execute(
            "insert into ufo_control.onboard_claim "
            "(id, email, email_domain, code_hash, surface, surface_ref, expires_at) "
            "values ($1, $2, $3, $4, $5, $6, now() + interval '15 minutes')",
            claim_id,
            "cli@mintco.io",
            "mintco.io",
            "x",
            "ufo",
            "cli-mint-proof",
        )
        outcome = await InviteCodes(pool=pool).redeem(code, claim_id)
        return isinstance(outcome, InviteAccepted)
    finally:
        await pool.close()


async def test_owner_tx_enumerates_every_workspace(shared_role_env: SharedRoleEnv) -> None:
    assert current_workspace.get() is None
    async with owner_tx() as connection:
        rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
    assert set(shared_role_env.workspaces) <= {str(row.id) for row in rows}
