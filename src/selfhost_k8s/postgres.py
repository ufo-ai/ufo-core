"""Provision a tenant's Postgres — the database-per-tenant model (RFC 0003 §5, decision 2 default).

The re-derivation of metalcraft's ``platform-migrate`` Job, which minted the app role on shared
Postgres. Here the control plane mints a per-tenant LOGIN role and a database it owns, from the
shared-cluster admin DSN. The role carries ``CREATEDB`` so core's own ``selfhost init`` can create
the DBOS ``_dbos`` sibling database unchanged (RFC 0004 step 5: ``init`` runs verbatim).

The role password is derived deterministically from a cluster seed + the tenant name, so every
reconcile reproduces the same DSN without persisting a secret — idempotent, no drift on the running
pod. The dense alternative — an RLS-scoped role on one shared database — is transparent to core but
not provisioned in this cut; a deploy that wants it selects ``postgres = "rls"`` and this raises
until the RLS role path lands.
"""

import hashlib
import os

import asyncpg

from selfhost_k8s.contract import PostgresModel

PG_ROLE_SEED_ENV = "SELFHOST_K8S_PG_ROLE_SEED"


def tenant_identifier(tenant_name: str) -> str:
    """A Postgres identifier for the tenant — validated DNS-label in, ``[a-z0-9_]`` out (no quoting
    hazards). ``DeployRequest.tenant.name`` already guaranteed alnum + hyphen, starting alpha."""
    identifier = "selfhost_" + tenant_name.replace("-", "_")
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"unsafe tenant identifier {identifier!r}")
    return identifier


def tenant_password(tenant_name: str) -> str:
    seed = os.environ.get(PG_ROLE_SEED_ENV)
    if not seed:
        raise RuntimeError(f"{PG_ROLE_SEED_ENV} is unset — cannot derive the tenant Postgres role")
    return hashlib.sha256(f"{seed}:{tenant_name}".encode()).hexdigest()


def tenant_dsn(tenant_name: str, postgres_host: str) -> str:
    identifier = tenant_identifier(tenant_name)
    return f"postgresql+asyncpg://{identifier}:{tenant_password(tenant_name)}@{postgres_host}/{identifier}"


async def ensure_tenant_database(admin_dsn: str, tenant_name: str, postgres_host: str) -> str:
    """Create (idempotently) the tenant's LOGIN+CREATEDB role and owned database; return its DSN.
    Password is hex and identifiers are ``[a-z0-9_]``, so inline DDL literals are injection-safe;
    asyncpg does not parameterize DDL."""
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


async def ensure_tenant_postgres(
    model: PostgresModel, admin_dsn: str, tenant_name: str, postgres_host: str
) -> str:
    if model is PostgresModel.RLS:
        raise NotImplementedError(
            "postgres = 'rls' (dense, shared-database) is not provisioned in this cut — "
            "select postgres = 'database' (hard isolation). RLS-role-per-tenant is the documented "
            "tier alternative (RFC 0003 §5)."
        )
    return await ensure_tenant_database(admin_dsn, tenant_name, postgres_host)
