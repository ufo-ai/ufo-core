"""The `connector` object kind: granted provider accounts projected as workspace objects.

A grant is created only through the `connect_account` chat flow — a third party and a secret are
involved, so create and update refuse naming that path. This kind carries the read and revoke
halves: list shows every granted account with its grantor and fill state, get renders the
identifying spec beside the grant's audit status, and delete revokes — admitted to the grantor or
an owner. The broker holds the account's token and exposes no revoke surface, so revocation is
the grant-row delete: the account stops resolving for tools, syncs, and proxy rules at once.

Names derive from the grant: `<provider>-<account-slug>`, each part canonicalized into the object
name grammar, with a short digest suffix when two accounts collapse to one slug."""

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ufo.sdk.context import JsonValue
from ufo.sdk.grants import GrantSummary, grant_summaries
from ufo.sdk.objects import (
    OBJECT_LIST_PAGE,
    ObjectKind,
    ObjectPage,
    ObjectRow,
    OwnerRequired,
    VerbNotSupported,
)
from ufo.sdk.tools import ToolContext

CONNECTOR_KIND = "connector"
CONNECT_REFUSAL = "connecting an account involves a third party and a secret — use connect_account"
REVOKE_GATE = "only the grantor or the workspace owner may revoke a connected account"
NAME_DIGEST_LENGTH = 8


class ConnectorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    account_id: str


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


@dataclass(frozen=True)
class ConnectorObjects:
    """The kind's handlers over the workspace's grant rows: list and get read the audit view,
    delete revokes through the grant store on the turn's context. Grants for the same provider
    account across agents collapse to one object; revoking it withdraws them all."""

    async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage:
        rows = [
            ObjectRow(name=name, summary=f"{grant.provider} account {grant.account_id}")
            for name, grant in sorted((await self._named(ctx)).items())
        ]
        matched = [row for row in rows if query in row.name or query in row.summary]
        remaining = [row for row in matched if row.name > cursor] if cursor else matched
        page, rest = remaining[:OBJECT_LIST_PAGE], remaining[OBJECT_LIST_PAGE:]
        return ObjectPage(rows=tuple(page), next_cursor=page[-1].name if rest else None)

    async def get(self, ctx: ToolContext, name: str) -> ConnectorSpec | None:
        grant = (await self._named(ctx)).get(name)
        if grant is None:
            return None
        return ConnectorSpec(provider=grant.provider, account_id=grant.account_id)

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        grant = (await self._named(ctx)).get(name)
        if grant is None:
            return None
        return {
            "grantor_member_id": str(grant.grantor_member_id),
            "granted_at": grant.granted_at.isoformat(),
            "host": grant.host,
            "agent": grant.agent,
        }

    async def apply(
        self, ctx: ToolContext, name: str, spec: ConnectorSpec, old: ConnectorSpec | None
    ) -> None:
        raise VerbNotSupported(CONNECT_REFUSAL)

    async def delete(self, ctx: ToolContext, name: str) -> None:
        grant = (await self._named(ctx))[name]
        if not await self._may_revoke(ctx, grant.grantor_member_id):
            raise OwnerRequired(REVOKE_GATE)
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        await ctx.grants.revoke(ctx.turn.workspace_id, grant.provider, grant.account_id)

    async def _may_revoke(self, ctx: ToolContext, grantor_member_id: UUID) -> bool:
        if ctx.speaker_member_id == grantor_member_id:
            return True
        return await ctx.speaker_is_owner()

    async def _named(self, ctx: ToolContext) -> dict[str, GrantSummary]:
        accounts: dict[tuple[str, str], GrantSummary] = {}
        for grant in await grant_summaries(ctx.turn.workspace_id):
            accounts.setdefault((grant.provider, grant.account_id), grant)
        grouped: dict[str, list[GrantSummary]] = {}
        for grant in accounts.values():
            grouped.setdefault(f"{_slug(grant.provider)}-{_slug(grant.account_id)}", []).append(
                grant
            )
        named: dict[str, GrantSummary] = {}
        for plain, group in grouped.items():
            if len(group) == 1:
                named[plain] = group[0]
                continue
            for grant in group:
                qualifier = hashlib.sha256(grant.account_id.encode()).hexdigest()
                named[f"{plain}-{qualifier[:NAME_DIGEST_LENGTH]}"] = grant
        return named


CONNECTOR_OBJECT = ObjectKind(
    name=CONNECTOR_KIND,
    description=(
        "A connected provider account (an OAuth grant). Created only through connect_account; "
        "delete revokes it, admitted to the grantor or the workspace owner."
    ),
    guidance=(
        "Connected accounts granted to this agent, one object per provider account. Create and "
        "update are refused — connecting an account involves a third party and a secret, so it "
        "stays the connect_account chat flow. Delete revokes: the grantor or a workspace owner "
        "removes the grant and the agent loses the account's tools and syncs."
    ),
    spec_model=ConnectorSpec,
    store=ConnectorObjects(),
)
