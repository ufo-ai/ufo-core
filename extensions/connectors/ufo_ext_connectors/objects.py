"""The `connector` object kind: granted provider accounts projected as workspace objects.

A grant is created only through the `connect_account` chat flow — a third party and a secret are
involved, so create refuses naming that path. A grant is private to its grantor by default; apply
admits exactly one mutation, flipping `shared`, gated to the grantor or an owner — the only way a
connected account's disclosure changes after connect time. Delete revokes, gated the same way. The
broker holds the account's token and exposes no revoke surface, so revocation is the grant-row
delete: the account stops resolving for tools, syncs, and proxy rules at once.

Names derive from the grant: `<provider>-<account-slug>`, each part canonicalized into the object
name grammar, with a short digest suffix when two accounts collapse to one slug."""

import hashlib
import re
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ufo.sdk.context import JsonValue
from ufo.sdk.grants import GrantSummary, grant_summaries
from ufo.sdk.objects import (
    MemberOwnedObjects,
    ObjectKind,
    ObjectOwner,
    OwnedRow,
    VerbNotSupported,
)
from ufo.sdk.tools import ToolContext

CONNECTOR_KIND = "connector"
CONNECT_REFUSAL = "connecting an account involves a third party and a secret — use connect_account"
REVOKE_GATE = "only the grantor or the workspace owner may revoke a connected account"
SHARE_GATE = "only the grantor or the workspace owner may change an account's sharing"
NAME_DIGEST_LENGTH = 8


class ConnectorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    account_id: str
    shared: bool = False


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


@dataclass(frozen=True)
class ConnectorObjects(MemberOwnedObjects[ConnectorSpec]):
    """The kind's handlers over the workspace's grant rows: list and get read the audit view,
    apply flips sharing, delete revokes — both through the grant store on the turn's context. The
    per-member visibility and grantor-or-owner gate is the base's; this kind supplies the grant
    rows, their specs, and the flip/revoke domain acts. Grants for the same provider account across
    agents collapse to one object; revoking or resharing it acts on them all."""

    kind_name: ClassVar[str] = CONNECTOR_KIND
    mutate_gate: ClassVar[str] = SHARE_GATE
    delete_gate: ClassVar[str] = REVOKE_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]:
        return tuple(
            OwnedRow(
                name=name,
                summary=(
                    f"{grant.provider} account {grant.account_id} "
                    f"({'shared' if grant.shared else 'private'})"
                ),
                owner=ObjectOwner(member_id=grant.grantor_member_id, shared=grant.shared),
            )
            for name, grant in (await self._named(ctx)).items()
        )

    async def _spec(self, ctx: ToolContext, name: str) -> ConnectorSpec | None:
        grant = (await self._named(ctx)).get(name)
        if grant is None:
            return None
        return ConnectorSpec(
            provider=grant.provider, account_id=grant.account_id, shared=grant.shared
        )

    async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        grant = (await self._named(ctx)).get(name)
        if grant is None:
            return None
        return {
            "grantor_member_id": str(grant.grantor_member_id),
            "granted_at": grant.granted_at.isoformat(),
            "host": grant.host,
            "agent": grant.agent,
            "shared": grant.shared,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: ConnectorSpec,
        old: ConnectorSpec | None,
        owner: ObjectOwner | None,
    ) -> None:
        if old is None or spec.model_copy(update={"shared": old.shared}) != old:
            raise VerbNotSupported(CONNECT_REFUSAL)
        if spec.shared == old.shared:
            return
        grant = (await self._named(ctx))[name]
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        await ctx.grants.set_shared(
            ctx.turn.workspace_id, grant.provider, grant.account_id, spec.shared
        )

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None:
        grant = (await self._named(ctx))[name]
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        await ctx.grants.revoke(ctx.turn.workspace_id, grant.provider, grant.account_id)

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
        "A connected provider account (an OAuth grant), private to its grantor by default. "
        "Created only through connect_account; apply flips `shared`; delete revokes — both "
        "admitted to the grantor or the workspace owner."
    ),
    guidance=(
        "Connected accounts granted to this agent, one object per provider account. Create is "
        "refused — connecting an account involves a third party and a secret, so it stays the "
        "connect_account chat flow. Apply admits exactly one change: flipping `shared` — the "
        "grantor (or a workspace owner) shares a private account with every member's turns, or "
        "makes a shared one private again. Delete revokes: the grantor or a workspace owner "
        "removes the grant and the agent loses the account's tools and syncs. Reads show shared "
        "accounts plus the member's own — a workspace owner sees all."
    ),
    spec_model=ConnectorSpec,
    store=ConnectorObjects(),
)
