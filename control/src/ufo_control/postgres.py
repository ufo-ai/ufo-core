"""Provision a tenant's Postgres — the two tiers, both transparent to core (RFC 0011 §2).

``rls`` is the hosted default: one shared application database presents a single-workspace view to
each tenant through row-level security keyed on ``workspace_id``. Each tenant gets an RLS-subject
LOGIN role ``ufo_t_<name>`` (member of the ``ufo_app`` group that anchors the CRUD grant), its
session pinned to its workspace uuid by ``ALTER ROLE … IN DATABASE … SET app.workspace_id`` so the
DSN alone scopes every connection and core needs no session-variable code. DBOS stays per-tenant —
a private ``ufo_dbos_<name>`` the role owns — so no tenant recovers another's workflows. The
policies that make RLS an isolation boundary are bootstrapped as the owner (``rls`` module); this
module provisions only roles, the GUC pin, the CRUD grant, and the DBOS database.

``database`` is the hard-isolation tier for self-hosters: a whole database per tenant with a
CREATEDB role that lets core's ``init`` create the DBOS sibling unchanged.

Role passwords are derived deterministically from a cluster seed + the tenant name, so every
reconcile reproduces the same DSN without persisting a secret — idempotent, no drift on the running
pod. Password is hex and identifiers are ``[a-z0-9_]``, so inline DDL literals are injection-safe;
asyncpg does not parameterize DDL.
"""

import hashlib
import os
from dataclasses import dataclass
from uuid import UUID

import asyncpg

PG_ROLE_SEED_ENV = "UFO_CONTROL_PG_ROLE_SEED"
APP_GROUP_ROLE = "ufo_app"
OWNER_ROLE = "ufo_owner"
WORKSPACE_GUC = "app.workspace_id"


@dataclass(frozen=True)
class TenantPostgres:
    """What provisioning hands ``render``. ``url`` is the asyncpg DSN the serve pods dial. In the
    ``rls`` tier ``system_url`` is the explicit DBOS-sibling DSN (deriving one from the shared app
    db would collide every tenant on one system db) and ``workspace_id`` is the uuid the GUC is
    pinned to, passed to ``init --workspace-id`` so the workspace row equals the GUC or RLS rejects
    it. In the ``database`` tier both are ``None``: core derives the ``_dbos`` sibling and ``init``
    mints the workspace uuid itself."""

    url: str
    system_url: str | None = None
    workspace_id: str | None = None


def tenant_suffix(tenant_name: str) -> str:
    """The ``[a-z0-9_]`` role/database suffix for a tenant — validated DNS-label in (``-`` → ``_``),
    no quoting hazards out. ``TenantIdentity.name`` already guaranteed alnum + hyphen, starting
    alpha."""
    suffix = tenant_name.replace("-", "_")
    if not suffix.replace("_", "").isalnum():
        raise ValueError(f"unsafe tenant suffix {suffix!r}")
    return suffix


def tenant_identifier(tenant_name: str) -> str:
    """The database-tier per-tenant role/database name (``ufo_<name>``)."""
    return "ufo_" + tenant_suffix(tenant_name)


def tenant_role(tenant_name: str) -> str:
    """The rls-tier per-tenant LOGIN role (``ufo_t_<name>``, member of ``ufo_app``)."""
    return "ufo_t_" + tenant_suffix(tenant_name)


def tenant_dbos_database(tenant_name: str) -> str:
    """The rls-tier per-tenant DBOS sibling database (``ufo_dbos_<name>``)."""
    return "ufo_dbos_" + tenant_suffix(tenant_name)


def tenant_password(tenant_name: str) -> str:
    seed = os.environ.get(PG_ROLE_SEED_ENV)
    if not seed:
        raise RuntimeError(f"{PG_ROLE_SEED_ENV} is unset — cannot derive the tenant Postgres role")
    return hashlib.sha256(f"{seed}:{tenant_name}".encode()).hexdigest()


def tenant_dsn(tenant_name: str, postgres_host: str) -> str:
    identifier = tenant_identifier(tenant_name)
    return f"postgresql+asyncpg://{identifier}:{tenant_password(tenant_name)}@{postgres_host}/{identifier}"


async def ensure_tenant_postgres(
    model: str,
    admin_dsn: str,
    tenant_name: str,
    postgres_host: str,
    app_database: str,
    workspace_id: str,
) -> TenantPostgres:
    match model:
        case "rls":
            return await _ensure_rls_tenant(
                admin_dsn, tenant_name, postgres_host, app_database, workspace_id
            )
        case "database":
            url = await ensure_tenant_database(admin_dsn, tenant_name, postgres_host)
            return TenantPostgres(url=url)
        case _:
            raise ValueError(f"unknown postgres model {model!r}")


async def _ensure_rls_tenant(
    admin_dsn: str, tenant_name: str, postgres_host: str, app_database: str, workspace_id: str
) -> TenantPostgres:
    role = tenant_role(tenant_name)
    password = tenant_password(tenant_name)
    dbos_database = tenant_dbos_database(tenant_name)
    UUID(workspace_id)
    cluster = await asyncpg.connect(admin_dsn)
    try:
        await _ensure_group_role(cluster)
        await _ensure_tenant_role(cluster, role, password)
        await cluster.execute(f'grant set on parameter {WORKSPACE_GUC} to "{OWNER_ROLE}"')
        await cluster.execute(
            f'alter role "{role}" in database "{app_database}" '
            f"set {WORKSPACE_GUC} = '{workspace_id}'"
        )
        await _ensure_database(cluster, dbos_database, owner=role)
    finally:
        await cluster.close()
    app = await asyncpg.connect(admin_dsn, database=app_database)
    try:
        await _grant_app_group(app)
    finally:
        await app.close()
    return TenantPostgres(
        url=f"postgresql+asyncpg://{role}:{password}@{postgres_host}/{app_database}",
        system_url=f"postgresql+psycopg://{role}:{password}@{postgres_host}/{dbos_database}",
        workspace_id=workspace_id,
    )


async def _ensure_group_role(connection: asyncpg.Connection) -> None:
    exists = await connection.fetchval("select 1 from pg_roles where rolname = $1", APP_GROUP_ROLE)
    if exists is None:
        await connection.execute(f'create role "{APP_GROUP_ROLE}" nologin')


async def _ensure_tenant_role(connection: asyncpg.Connection, role: str, password: str) -> None:
    exists = await connection.fetchval("select 1 from pg_roles where rolname = $1", role)
    if exists is None:
        await connection.execute(
            f'create role "{role}" login in role "{APP_GROUP_ROLE}" password \'{password}\''
        )
    else:
        await connection.execute(f"alter role \"{role}\" with login password '{password}'")
    await connection.execute(f'grant "{APP_GROUP_ROLE}" to "{role}"')


async def _grant_app_group(connection: asyncpg.Connection) -> None:
    """Anchor the tenant roles' rights on the ``ufo_app`` group, in the shared app database. CRUD on
    every current table (which includes ``alembic_version`` — SELECT there is what the per-tenant
    ``init`` no-op reads) plus default privileges on the owner's future tables, so a later migration
    never leaves a tenant unable to write. USAGE on schema + sequences covers identity columns."""
    await connection.execute(f'grant usage on schema public to "{APP_GROUP_ROLE}"')
    await connection.execute(
        f'grant select, insert, update, delete on all tables in schema public to "{APP_GROUP_ROLE}"'
    )
    await connection.execute(f'grant usage on all sequences in schema public to "{APP_GROUP_ROLE}"')
    await connection.execute(
        f'alter default privileges for role "{OWNER_ROLE}" in schema public '
        f'grant select, insert, update, delete on tables to "{APP_GROUP_ROLE}"'
    )
    await connection.execute(
        f'alter default privileges for role "{OWNER_ROLE}" in schema public '
        f'grant usage on sequences to "{APP_GROUP_ROLE}"'
    )


async def _ensure_database(connection: asyncpg.Connection, name: str, owner: str) -> None:
    exists = await connection.fetchval("select 1 from pg_database where datname = $1", name)
    if exists is None:
        await connection.execute(f'create database "{name}" owner "{owner}"')


async def ensure_tenant_database(admin_dsn: str, tenant_name: str, postgres_host: str) -> str:
    """The ``database`` tier: create (idempotently) the tenant's LOGIN+CREATEDB role and owned
    database; return its DSN. CREATEDB lets core's ``init`` create the ``_dbos`` sibling
    unchanged."""
    identifier = tenant_identifier(tenant_name)
    password = tenant_password(tenant_name)
    connection = await asyncpg.connect(admin_dsn)
    try:
        role_exists = await connection.fetchval(
            "select 1 from pg_roles where rolname = $1", identifier
        )
        if role_exists is None:
            await connection.execute(
                f"create role \"{identifier}\" login createdb password '{password}'"
            )
        else:
            await connection.execute(
                f"alter role \"{identifier}\" with login createdb password '{password}'"
            )
        db_exists = await connection.fetchval(
            "select 1 from pg_database where datname = $1", identifier
        )
        if db_exists is None:
            await connection.execute(f'create database "{identifier}" owner "{identifier}"')
    finally:
        await connection.close()
    return tenant_dsn(tenant_name, postgres_host)
