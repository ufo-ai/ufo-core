"""Standing member permissions projected as member-owned objects."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import workspace_tx
from ufo.runtime.access.member_authorization import AuthorizationScope
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.objects import (
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

MEMBER_PERMISSION_KIND = "permission"
MEMBER_PERMISSION_APPLY = "permissions are granted in conversation, not through object manifests"


class MemberPermissionSpec(BaseModel):
    """The account operation and agent a standing member permission covers."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    call: str
    scope: AuthorizationScope
    basis: Literal["selected_message", "pending_answer"] | None
    evidence: str | None


@dataclass(frozen=True)
class MemberPermissionObjects:
    """Read and revoke active standing permissions for exactly the acting member."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return object_page(await self._rows(ctx.require_speaker()), query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        return object_page(await self._rows(member_id), query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberPermissionSpec] | None:
        return await self._detail(ctx.require_speaker(), name)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[MemberPermissionSpec] | None:
        detail = await self._detail(member_id, name)
        if detail is None:
            return None
        row = next(row for row in await self._rows(member_id) if row.name == name)
        return MemberObject(row=row, detail=detail)

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        row = await self._row(ctx.require_speaker(), name)
        return None if row is None else {"active": True, "call": row.call}

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: MemberPermissionSpec,
        old: MemberPermissionSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(MEMBER_PERMISSION_APPLY)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        member_id = ctx.require_speaker()
        try:
            permission_id = UUID(name)
        except ValueError:
            return
        now = sa.func.now()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member_permission)
                .values(revoked_at=now, updated_at=now)
                .where(
                    tables.member_permission.c.workspace_id == ws_current().workspace_id,
                    tables.member_permission.c.member_id == member_id,
                    tables.member_permission.c.id == permission_id,
                    tables.member_permission.c.revoked_at.is_(None),
                )
            )

    async def _rows(self, member_id: UUID) -> tuple[ObjectRow, ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    self._query(member_id).order_by(tables.member_permission.c.created_at.desc())
                )
            ).all()
        return tuple(
            ObjectRow(
                name=str(row.id),
                summary=f"{row.agent}: {row.call}",
                fields={"agent": row.agent, "call": row.call},
            )
            for row in rows
        )

    async def _detail(
        self, member_id: UUID, name: str
    ) -> ObjectDetail[MemberPermissionSpec] | None:
        row = await self._row(member_id, name)
        if row is None:
            return None
        return ObjectDetail(
            spec=MemberPermissionSpec(
                agent=row.agent,
                call=row.call,
                scope=AuthorizationScope.model_validate(row.scope),
                basis=row.basis,
                evidence=row.evidence,
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def _row(self, member_id: UUID, name: str) -> sa.Row | None:
        try:
            permission_id = UUID(name)
        except ValueError:
            return None
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    self._query(member_id).where(tables.member_permission.c.id == permission_id)
                )
            ).one_or_none()

    def _query(self, member_id: UUID) -> sa.Select:
        decision = tables.member_authorization.alias("permission_decision")
        grant_decision = (
            decision.c.workspace_id == tables.member_permission.c.workspace_id,
            decision.c.member_id == tables.member_permission.c.member_id,
            decision.c.agent_id == tables.member_permission.c.agent_id,
            decision.c.call == tables.member_permission.c.call,
            decision.c.effect_digest == tables.member_permission.c.effect_digest,
            decision.c.scope_digest == tables.member_permission.c.scope_digest,
            decision.c.decided_by == tables.member_permission.c.granted_by,
            decision.c.decision == "always",
        )
        return (
            sa.select(
                tables.member_permission.c.id,
                tables.member_permission.c.call,
                tables.member_permission.c.scope,
                tables.member_permission.c.created_at,
                tables.member_permission.c.updated_at,
                tables.agent.c.name.label("agent"),
                sa.select(decision.c.basis)
                .where(*grant_decision)
                .order_by(decision.c.updated_at.desc())
                .limit(1)
                .scalar_subquery()
                .label("basis"),
                sa.select(decision.c.evidence)
                .where(*grant_decision)
                .order_by(decision.c.updated_at.desc())
                .limit(1)
                .scalar_subquery()
                .label("evidence"),
            )
            .join(
                tables.agent,
                (tables.agent.c.workspace_id == tables.member_permission.c.workspace_id)
                & (tables.agent.c.id == tables.member_permission.c.agent_id),
            )
            .where(
                tables.member_permission.c.workspace_id == ws_current().workspace_id,
                tables.member_permission.c.member_id == member_id,
                tables.member_permission.c.scope_digest.is_not(None),
                tables.member_permission.c.scope.is_not(None),
                tables.member_permission.c.revoked_at.is_(None),
            )
        )


MEMBER_PERMISSION_OBJECT = ObjectKind(
    name=MEMBER_PERMISSION_KIND,
    description="A permission record.",
    guidance="This kind has no additional guidance.",
    spec_model=MemberPermissionSpec,
    store=MemberPermissionObjects(),
    list_fields=frozenset({"agent", "call"}),
)
