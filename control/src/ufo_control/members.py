"""Add a member to an existing tenant — cross-tenant authority the closed control plane owns.

The onboarding backend (the ``gateway`` extension) calls this when a verified user's org domain
already has a tenant: it joins them rather than provisioning a second one. The write runs as the
Postgres OWNER role (which bypasses RLS by design — the platform-worker precedent), inserting the
``member`` row idempotently. The tenant's workspace uuid is read from its Tenant CR's
``status.workspaceId``; a tenant not yet reporting one is a 409 the caller retries.
"""

import os
from uuid import UUID, uuid4

import asyncpg
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from ufo_control.kube import KubeClient

OWNER_DSN_ENV = "UFO_CONTROL_POSTGRES_OWNER_DSN"


class MemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str


class MemberResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    email: str


def owner_dsn() -> str:
    dsn = os.environ.get(OWNER_DSN_ENV)
    if not dsn:
        raise RuntimeError(f"{OWNER_DSN_ENV} is unset — no Postgres owner DSN for member writes")
    return dsn


async def add_member(kube: KubeClient, name: str, request: MemberRequest) -> MemberResult:
    obj = await kube.get_tenant(name)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"no tenant {name!r}")
    workspace_id = (obj.get("status") or {}).get("workspaceId")
    if not workspace_id:
        raise HTTPException(
            status_code=409,
            detail=f"tenant {name!r} has no workspace yet; retry once it is Ready",
        )
    email = request.email.strip().lower()
    connection = await asyncpg.connect(owner_dsn())
    try:
        await connection.execute(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values ($1, $2, $3, now(), now()) "
            "on conflict (workspace_id, email) do nothing",
            uuid4(),
            UUID(str(workspace_id)),
            email,
        )
    finally:
        await connection.close()
    return MemberResult(workspace_id=str(workspace_id), email=email)
