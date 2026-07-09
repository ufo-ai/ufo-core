"""Shared-tier onboarding: a verified org domain resolves to a workspace ROW in the shared database.

The default tier (RFC 0011): one shared serve fleet presents each workspace by RLS, so onboarding a
member is a workspace row plus that member's owner row — no `Tenant` CR, no dedicated deploy, no
subdomain. The write runs under the ambient `ws(workspace_id)` scope as the RLS-subject serve role,
so both rows land scoped to exactly that workspace or the policy rejects them. The workspace uuid is
derived from the domain (`uuid5`), the shared-tier analogue of the enterprise `mint_tenant_name`, so
a concurrent second onboard for a fresh domain computes the same id and the insert is idempotent —
never a duplicate workspace — and a later member of the same organization joins the one workspace it
already has.
"""

import os
from dataclasses import dataclass
from uuid import NAMESPACE_DNS, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.workspace import ws

SERVE_DSN_ENV = "UFO_CONTROL_SERVE_DSN"


def serve_dsn() -> str:
    """The RLS-subject serve-role DSN the shared onboarding writes through — the same role the
    shared serve fleet dials, scoping every transaction by the workspace `ws(...)` binds."""
    dsn = os.environ.get(SERVE_DSN_ENV)
    if not dsn:
        raise RuntimeError(
            f"{SERVE_DSN_ENV} is unset — shared-tier onboarding writes the workspace row as the "
            "RLS-subject serve role"
        )
    return dsn


@dataclass(frozen=True)
class SharedWorkspaces:
    """Resolve a verified org domain to its workspace row in the shared database, adding the member
    as owner. Holds no Kubernetes client — the shared tier applies no `Tenant`. `workspace_url` is
    the shared serve host the member's `ufo` surface talks to (`app.<apex>`, one fleet for every
    workspace — no per-workspace subdomain), distinct from the onboarding apex."""

    workspace_url: str

    async def ensure(self, domain: str, email: str) -> str:
        workspace_id = uuid5(NAMESPACE_DNS, domain.lower())
        member = email.strip().lower()
        with ws(workspace_id):
            async with workspace_tx() as connection:
                await connection.execute(
                    insert(tables.workspace)
                    .values(id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now())
                    .on_conflict_do_nothing(index_elements=[tables.workspace.c.id])
                )
                await connection.execute(
                    insert(tables.member)
                    .values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        email=member,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_nothing(
                        index_elements=[tables.member.c.workspace_id, tables.member.c.email]
                    )
                )
        return str(workspace_id)
