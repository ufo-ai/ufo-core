"""The RLS tier against real Postgres (its own container on :5544, never core's :5541).

The whole chain: create the owner role + shared app database, migrate the runtime's real
``assistant_hosted`` schema as the owner, bootstrap the workspace policies, provision two tenants
through the rls arm, seed each workspace's rows as the owner, then connect as each tenant role and
prove the shared database presents a single-workspace view — disjoint, and write-guarded by the
policy's WITH CHECK. Setup is a sync fixture because ``apply_migrations`` drives alembic on its own
event loop (``asyncio.run``), which cannot nest inside a running one.
"""

import asyncio
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import asyncpg
import httpx
import pytest
import sqlalchemy as sa
from click.testing import CliRunner
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

from ufo_control.gateway import Onboarding
from ufo_control.gateway_claim import ClaimWorkflow
from ufo_control.gateway_email import LoggingEmailSender, WorkEmailPolicy
from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_provision import TenantJoin
from ufo_control.gateway_shared import SharedWorkspaces
from ufo_control.gateway_store import OnboardStore
from ufo_control.gateway_token import verify_token
from ufo_control.kube import KubeClient
from ufo_control.main import main
from ufo_control.postgres import (
    PG_ROLE_SEED_ENV,
    WORKSPACE_GUC,
    TenantPostgres,
    ensure_serve_role,
    ensure_tenant_postgres,
    serve_dsn,
)
from ufo_control.rls import POSTGRES_OWNER_DSN_ENV, bootstrap_policies

SHARED_TOKEN_SECRET = "shared-tier-secret"
SHARED_WORKSPACE_URL = "https://flyingobject.ai"

PG_HOST = "127.0.0.1"
PG_PORT = 5544
ADMIN_DSN = f"postgresql://admin:admin@{PG_HOST}:{PG_PORT}/postgres"
POSTGRES_HOST = f"{PG_HOST}:{PG_PORT}"
APP_DATABASE = "ufo_rls_test"
# The admin, connected to the shared app database (not the maintenance db) — what prod's rollout
# owner DSN is, so ensure_serve_role derives the DBOS sibling from the app database it targets.
ADMIN_APP_DSN = f"postgresql://admin:admin@{POSTGRES_HOST}/{APP_DATABASE}"
FAILLOUD_DATABASE = "ufo_rls_failloud"
OWNER_ROLE = "ufo_owner"
OWNER_PASSWORD = "ownerpw"
SEED = "rls-test-seed"
PACK = "assistant_hosted"
TENANT_ROLES = ("ufo_t_acme", "ufo_t_globex")
TENANT_DBOS = ("ufo_dbos_acme", "ufo_dbos_globex")
# CI sets this so an unreachable superuser Postgres is a loud collection error, never a silent skip:
# the RLS tier's isolation, serve-role fail-closed, and shared bootstrap must be exercised on every
# run — this suite going quiet is exactly how the serve-role gaps once reached the live cluster.
RLS_REQUIRED_ENV = "UFO_RLS_REQUIRED"


def _reachable() -> bool:
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=0.5):
            return True
    except OSError:
        if os.environ.get(RLS_REQUIRED_ENV):
            raise RuntimeError(
                f"{RLS_REQUIRED_ENV} is set but the superuser Postgres on {PG_HOST}:{PG_PORT} is "
                "unreachable — the RLS tier must run here, not skip"
            ) from None
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=f"postgres on :{PG_PORT} not reachable")


def _libpq(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _owner_app_dsn(scheme: str, database: str) -> str:
    return f"{scheme}://{OWNER_ROLE}:{OWNER_PASSWORD}@{POSTGRES_HOST}/{database}"


@dataclass(frozen=True)
class TenantRecord:
    name: str
    workspace_id: str
    postgres: TenantPostgres


@dataclass(frozen=True)
class RlsEnv:
    owner_libpq_dsn: str
    tenants: tuple[TenantRecord, ...]


async def _reset(database: str) -> None:
    connection = await asyncpg.connect(ADMIN_DSN)
    try:
        for name in (database, f"{database}_dbos", *TENANT_DBOS):
            await connection.execute(f'drop database if exists "{name}" with (force)')
        for role in (*TENANT_ROLES, "ufo_serve", "ufo_serve_shared", "ufo_app", OWNER_ROLE):
            if await connection.fetchval("select 1 from pg_roles where rolname = $1", role):
                # drop_owned first: a role holding SET on the app.workspace_id parameter (or the
                # owner's default privileges) can't be dropped while those grants stand.
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


async def _seed_workspace_rows(dsn: str, tenants: tuple[TenantRecord, ...]) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        for tenant in tenants:
            workspace_id = UUID(tenant.workspace_id)
            await connection.execute(
                "insert into workspace (id, created_at, updated_at) values ($1, now(), now())",
                workspace_id,
            )
            await connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values ($1, $2, $3, now(), now())",
                uuid4(),
                workspace_id,
                f"owner@{tenant.name}.test",
            )
    finally:
        await connection.close()


async def _provision(name: str, workspace_id: str) -> TenantPostgres:
    return await ensure_tenant_postgres(
        "rls", ADMIN_DSN, name, POSTGRES_HOST, APP_DATABASE, workspace_id
    )


@pytest.fixture(scope="module")
def rls_env() -> Iterator[RlsEnv]:
    previous_seed = os.environ.get(PG_ROLE_SEED_ENV)
    os.environ[PG_ROLE_SEED_ENV] = SEED
    asyncio.run(_reset(APP_DATABASE))
    apply_migrations(_owner_app_dsn("postgresql+asyncpg", APP_DATABASE), PACK)
    owner_libpq = _owner_app_dsn("postgresql", APP_DATABASE)
    asyncio.run(bootstrap_policies(owner_libpq))
    tenants: list[TenantRecord] = []
    for name in ("acme", "globex"):
        workspace_id = str(uuid4())
        postgres = asyncio.run(_provision(name, workspace_id))
        tenants.append(TenantRecord(name=name, workspace_id=workspace_id, postgres=postgres))
    tenants_tuple = tuple(tenants)
    asyncio.run(_seed_workspace_rows(owner_libpq, tenants_tuple))
    try:
        yield RlsEnv(owner_libpq_dsn=owner_libpq, tenants=tenants_tuple)
    finally:
        asyncio.run(_reset(APP_DATABASE))
        if previous_seed is None:
            os.environ.pop(PG_ROLE_SEED_ENV, None)
        else:
            os.environ[PG_ROLE_SEED_ENV] = previous_seed


async def test_reprovisioning_an_existing_tenant_is_idempotent(rls_env: RlsEnv) -> None:
    acme = rls_env.tenants[0]
    result = await _provision(acme.name, acme.workspace_id)
    assert result.workspace_id == acme.workspace_id
    connection = await asyncpg.connect(_libpq(result.url))
    try:
        pinned = await connection.fetchval("select current_setting('app.workspace_id')")
        assert pinned == acme.workspace_id
    finally:
        await connection.close()


async def test_each_tenant_role_sees_only_its_own_workspace(rls_env: RlsEnv) -> None:
    for tenant in rls_env.tenants:
        connection = await asyncpg.connect(_libpq(tenant.postgres.url))
        try:
            pinned = await connection.fetchval("select current_setting('app.workspace_id')")
            assert pinned == tenant.workspace_id
            workspaces = await connection.fetch("select id from workspace")
            assert [str(row["id"]) for row in workspaces] == [tenant.workspace_id]
            members = await connection.fetch("select workspace_id from member")
            assert {str(row["workspace_id"]) for row in members} == {tenant.workspace_id}
        finally:
            await connection.close()


async def test_no_cross_tenant_visibility(rls_env: RlsEnv) -> None:
    acme, globex = rls_env.tenants
    connection = await asyncpg.connect(_libpq(acme.postgres.url))
    try:
        seen = await connection.fetch("select id from workspace")
        ids = {str(row["id"]) for row in seen}
        assert globex.workspace_id not in ids
        assert ids == {acme.workspace_id}
    finally:
        await connection.close()


async def test_with_check_rejects_a_foreign_workspace_insert(rls_env: RlsEnv) -> None:
    acme, globex = rls_env.tenants
    connection = await asyncpg.connect(_libpq(acme.postgres.url))
    try:
        # Its own workspace: the WITH CHECK passes.
        await connection.execute(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values ($1, $2, $3, now(), now())",
            uuid4(),
            UUID(acme.workspace_id),
            "second@acme.test",
        )
        # A foreign workspace uuid: the WITH CHECK rejects it as an RLS violation.
        with pytest.raises(asyncpg.PostgresError, match="row-level security"):
            await connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values ($1, $2, $3, now(), now())",
                uuid4(),
                UUID(globex.workspace_id),
                "intruder@acme.test",
            )
    finally:
        await connection.close()


def test_rls_bootstrap_cli_is_idempotent(rls_env: RlsEnv) -> None:
    # A sync test: the CLI command drives ``asyncio.run`` itself, which cannot nest in a loop. The
    # bootstrap now also creates the ufo_serve role, so it runs as the cluster admin (prod's
    # postgres-admin-dsn), not the non-superuser tenant owner.
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
    dsn = f"postgresql://admin:admin@{PG_HOST}:{PG_PORT}/{FAILLOUD_DATABASE}"
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("create table workspace (id uuid primary key)")
        await connection.execute("create table rogue_widget (id int primary key)")
    finally:
        await connection.close()
    try:
        with pytest.raises(RuntimeError, match="rogue_widget"):
            await bootstrap_policies(dsn)
    finally:
        cleanup = await asyncpg.connect(ADMIN_DSN)
        try:
            await cleanup.execute(f'drop database if exists "{FAILLOUD_DATABASE}" with (force)')
        finally:
            await cleanup.close()


@dataclass(frozen=True)
class SharedRoleEnv:
    workspaces: tuple[str, ...]
    dsn_libpq: str


@pytest.fixture(scope="module")
def shared_role_env(rls_env: RlsEnv) -> Iterator[SharedRoleEnv]:
    """The real ``ufo_serve`` role the shared fleet connects as, created by the rollout bootstrap
    (``ensure_serve_role``): an RLS-subject login role in ufo_app, GRANTed SET on the workspace GUC,
    with NO pinned default — so it scopes per transaction from current_workspace and an unset
    workspace fails loud. The shared fleet also opens the RLS-bypassing ``ufo_owner`` engine
    (``init_owner_db``) that ``owner_tx`` enumerates through, so both the subject and owner ends the
    fleet uses are live here. Built on rls_env's owner + policies + two seeded workspaces; _reset
    drops the role and the DBOS sibling database the bootstrap provisions."""
    asyncio.run(ensure_serve_role(ADMIN_APP_DSN))
    dsn = serve_dsn(POSTGRES_HOST, APP_DATABASE)
    init_db(dsn)
    init_owner_db(_owner_app_dsn("postgresql+asyncpg", APP_DATABASE))
    try:
        yield SharedRoleEnv(
            workspaces=tuple(tenant.workspace_id for tenant in rls_env.tenants),
            dsn_libpq=_libpq(dsn),
        )
    finally:
        asyncio.run(dispose_db())


async def test_shared_bootstrap_provisions_the_dbos_system_database(
    shared_role_env: SharedRoleEnv,
) -> None:
    """Core derives the fleet's DBOS system store as the ``<app>_dbos`` sibling of the serve DSN and
    ``DBOS.launch`` connects there, but the RLS-subject serve role has no CREATEDB to mint it — so
    the rollout bootstrap creates it, owned by the role. Without it the fleet crash-loops on
    ``database "<app>_dbos" does not exist``. Connect as the serve role to core's derived system
    database and create the schema DBOS bootstraps — proving it exists and the role owns it."""
    system_url = DatabaseConfig(url=serve_dsn(POSTGRES_HOST, APP_DATABASE)).system_url
    connection = await asyncpg.connect(_libpq(system_url.replace("+psycopg", "+asyncpg", 1)))
    try:
        assert await connection.fetchval("select current_database()") == f"{APP_DATABASE}_dbos"
        await connection.execute("create schema dbos")
        await connection.execute("drop schema dbos")
    finally:
        await connection.close()


async def test_shared_role_scopes_each_transaction_via_contextvar(
    shared_role_env: SharedRoleEnv,
) -> None:
    """One shared connection pool, many workspaces: setting current_workspace pins app.workspace_id
    for the transaction, so RLS presents only that workspace — the shared serve fleet's isolation.
    The same role, two workspaces, two disjoint views."""
    for workspace_id in shared_role_env.workspaces:
        reset = current_workspace.set(UUID(workspace_id))
        try:
            async with workspace_tx() as connection:
                rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
                assert [str(row.id) for row in rows] == [workspace_id]
                members = (await connection.execute(sa.select(tables.member.c.workspace_id))).all()
                assert {str(row.workspace_id) for row in members} == {workspace_id}
        finally:
            current_workspace.reset(reset)


async def test_shared_role_without_workspace_fails_closed(
    shared_role_env: SharedRoleEnv,
) -> None:
    """No workspace set → workspace_tx pins no GUC; under the RLS-subject role the policy's strict
    current_setting errors on the unset custom GUC — fail-loud, never a cross-workspace read."""
    assert current_workspace.get() is None
    with pytest.raises(Exception) as caught:
        async with workspace_tx() as connection:
            await connection.execute(sa.select(tables.workspace.c.id))
    assert WORKSPACE_GUC in str(caught.value)


async def _members_in(workspace_id: str) -> list[str]:
    with ws(UUID(workspace_id)):
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.member.c.email).order_by(tables.member.c.email)
                )
            ).all()
    return [row.email for row in rows]


def _tenantless_shared() -> SharedWorkspaces:
    """A `SharedWorkspaces` over an empty cluster: the domain has no dedicated tenant, so
    resolution falls through to the shared row path these tests prove."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []}))
    kube = KubeClient(http=httpx.AsyncClient(transport=transport, base_url="https://kube.test"))
    return SharedWorkspaces(
        workspace_url=SHARED_WORKSPACE_URL,
        joins=TenantJoin(kube=kube, base_domain="flyingobject.ai"),
    )


async def test_shared_ensure_writes_workspace_and_owner_under_rls(
    shared_role_env: SharedRoleEnv,
) -> None:
    """The shared tier's core act: a verified org domain resolves to its workspace row and the
    member's owner row, written as the RLS-subject serve role under `ws(workspace_id)`. The uuid is
    derived from the domain, so the write is idempotent and a colleague joins the one workspace."""
    shared = _tenantless_shared()
    assert not await shared.exists("sharedco.io")
    workspace_id = await shared.ensure("sharedco.io", "Founder@Sharedco.io")
    assert await shared.exists("sharedco.io")
    assert workspace_id == str(uuid5(NAMESPACE_DNS, "sharedco.io"))
    with ws(UUID(workspace_id)):
        async with workspace_tx() as connection:
            rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
            assert [str(row.id) for row in rows] == [workspace_id]
    assert await _members_in(workspace_id) == ["founder@sharedco.io"]
    # Re-onboard is a no-op; a colleague of the same domain joins the one workspace.
    assert await shared.ensure("sharedco.io", "founder@sharedco.io") == workspace_id
    assert await shared.ensure("sharedco.io", "colleague@sharedco.io") == workspace_id
    assert await _members_in(workspace_id) == ["colleague@sharedco.io", "founder@sharedco.io"]


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
    """The shared tier seeds the same default `assistant` agent `ufoctl init` seeds per tenant, so
    the first turn's `default_agent()` resolves a row. Seeded from core's own defaults — identical
    to a per-tenant agent — and idempotent: a re-onboard neither duplicates the row nor errors."""
    shared = _tenantless_shared()
    workspace_id = await shared.ensure("agentco.io", "founder@agentco.io")
    seeded = [(DEFAULT_AGENT_NAME, DEFAULT_AGENT_PROMPT, DEFAULT_AGENT_MODEL)]
    assert await _default_agents_in(workspace_id) == seeded
    assert await shared.ensure("agentco.io", "founder@agentco.io") == workspace_id
    assert await _default_agents_in(workspace_id) == seeded


async def test_shared_onboard_signs_in_without_a_tenant_cr(
    shared_role_env: SharedRoleEnv, rls_env: RlsEnv
) -> None:
    """The full shared flow over the real claim ledger + RLS database: email → code → verify → the
    invite gate (a fresh domain creates a workspace, so a one-time code is burned) → a signed-in
    bearer carrying the domain's workspace uuid, surfacing the apex (no subdomain). A colleague of
    the now-existing domain then joins codeless. The cluster holds no tenant for the domain, so
    resolution falls through the tenant check to the shared row path."""
    pool = await asyncpg.create_pool(rls_env.owner_libpq_dsn)
    store = OnboardStore(pool=pool)
    await store.ensure_table()
    invites = InviteCodes(pool=pool)
    await invites.ensure_table()
    async with pool.acquire() as connection:
        await connection.execute("truncate ufo_control.onboard_claim")
        await connection.execute("truncate ufo_control.invite_code")
    sender = LoggingEmailSender()
    flow = Onboarding(
        claims=ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender),
        store=store,
        resolver=_tenantless_shared(),
        invites=invites,
        token_secret=SHARED_TOKEN_SECRET,
        apex_host="flyingobject.ai",
    )
    try:
        await flow.advance("ufo", "sess", "", b"")
        await flow.advance("ufo", "sess", "boss@sharedtwo.io", b"")
        code = sender.last_code("boss@sharedtwo.io")
        gated = await flow.advance("ufo", "sess", code, b"")
        assert "invite code" in gated.decode()
        signed_in = await flow.advance("ufo", "sess", await invites.mint(), b"")
        await flow.advance("ufo", "sess2", "", b"")
        await flow.advance("ufo", "sess2", "mate@sharedtwo.io", b"")
        joined = await flow.advance("ufo", "sess2", sender.last_code("mate@sharedtwo.io"), b"")
    finally:
        await pool.close()
    workspace_id = str(uuid5(NAMESPACE_DNS, "sharedtwo.io"))
    directives = dict(
        line.split("\t", 1) for line in signed_in.decode().splitlines() if "\t" in line
    )
    assert verify_token(directives["token"], SHARED_TOKEN_SECRET)["ws"] == workspace_id
    assert directives["workspace"] == SHARED_WORKSPACE_URL
    joined_directives = dict(
        line.split("\t", 1) for line in joined.decode().splitlines() if "\t" in line
    )
    assert "invite" not in joined.decode()  # an existing workspace joins codeless
    assert verify_token(joined_directives["token"], SHARED_TOKEN_SECRET)["ws"] == workspace_id
    assert await _members_in(workspace_id) == ["boss@sharedtwo.io", "mate@sharedtwo.io"]
    assert isinstance(flow.resolver, SharedWorkspaces)


def test_invite_mints_a_one_time_code_over_the_cli(rls_env: RlsEnv) -> None:
    """``ufo-control invite`` prints the plaintext once; the ledger holds only its hash, and the
    printed code redeems — the whole chain a workspace creation consumes."""
    previous = os.environ.get(POSTGRES_OWNER_DSN_ENV)
    os.environ[POSTGRES_OWNER_DSN_ENV] = rls_env.owner_libpq_dsn
    try:
        result = CliRunner().invoke(main, ["invite"])
    finally:
        if previous is None:
            os.environ.pop(POSTGRES_OWNER_DSN_ENV, None)
        else:
            os.environ[POSTGRES_OWNER_DSN_ENV] = previous
    assert result.exit_code == 0, result.output
    code = result.output.strip()
    assert code
    assert code not in result.output.replace(code, "", 1)  # printed exactly once
    assert asyncio.run(_redeems(rls_env.owner_libpq_dsn, code))


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
        return await InviteCodes(pool=pool).redeem(code, claim_id) is not None
    finally:
        await pool.close()


async def test_owner_tx_enumerates_every_workspace_where_the_subject_fails_closed(
    shared_role_env: SharedRoleEnv,
) -> None:
    """The cross-workspace sweeps enumerate through ``owner_tx``: the RLS-bypassing ``ufo_owner``
    engine reads across every workspace with no GUC set — exactly where the shared subject role
    fails closed (prior test). A sweep re-binds each row under ``with ws(...)`` afterward; here we
    prove only the enumeration half. Superset, not equality: the shared app database accumulates
    workspaces from the other shared-tier tests, so the proof is that owner_tx sees every seeded
    tenant at once — which the subject role, with no workspace bound, never can."""
    assert current_workspace.get() is None
    async with owner_tx() as connection:
        rows = (await connection.execute(sa.select(tables.workspace.c.id))).all()
    assert set(shared_role_env.workspaces) <= {str(row.id) for row in rows}
