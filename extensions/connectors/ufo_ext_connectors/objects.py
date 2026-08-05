"""Member-owned connections and their per-agent connector grants as workspace objects, read
through one owner gate in a turn and in the portal."""

from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.grants import (
    account_object_name,
    connection_summaries,
    grant_summaries,
)
from ufo.sdk.objects import (
    AGENT_KIND,
    GeneratedObjectOwner,
    MemberReadableObjects,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectRef,
    OwnedRow,
    VerbNotSupported,
)
from ufo.sdk.tools import ToolContext

CONNECTION_KIND = "connection"
CONNECTOR_GRANT_KIND = "connector_grant"
CONNECT_REFUSAL = "connecting an account involves a third party — use connect_account"
DISCONNECT_GATE = "only the connection owner or a workspace admin may disconnect an account"
REVOKE_GATE = "only the connection owner or a workspace admin may revoke an agent's grant"
SHARE_GATE = (
    "only the connection owner may share; the owner or a workspace admin may make it private"
)


class ConnectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    account_id: str


class ConnectorGrantSpec(ConnectionSpec):
    shared: bool = False


class _AccountSummary(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def account_id(self) -> str: ...


def _named[SummaryT: _AccountSummary](rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]:
    return {account_object_name(row.provider, row.account_id): row for row in rows}


@dataclass(frozen=True)
class ConnectionObjects(MemberReadableObjects[ConnectionSpec, GeneratedObjectOwner]):
    kind_name: ClassVar[str] = CONNECTION_KIND
    mutate_gate: ClassVar[str] = DISCONNECT_GATE
    delete_gate: ClassVar[str] = DISCONNECT_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        return tuple(
            OwnedRow(
                name=name,
                summary=f"{row.provider} account {row.account_id}",
                owner=GeneratedObjectOwner(
                    member_id=row.owner_member_id,
                    shared=False,
                    generation=row.id,
                ),
            )
            for name, row in _named(await connection_summaries()).items()
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: GeneratedObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[ConnectionSpec] | None:
        row = next(
            (summary for summary in await connection_summaries() if summary.id == owner.generation),
            None,
        )
        if row is None:
            return None
        return ObjectDetail(
            spec=ConnectionSpec(provider=row.provider, account_id=row.account_id),
            created_at=row.connected_at,
            updated_at=row.updated_at,
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner
    ) -> dict[str, JsonValue] | None:
        row = next(
            (summary for summary in await connection_summaries() if summary.id == owner.generation),
            None,
        )
        if row is None:
            return None
        return {
            "owner_member_id": str(row.owner_member_id),
            "host": row.host,
            "agents": list(row.agents),
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: ConnectionSpec,
        old: ConnectionSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        raise VerbNotSupported(CONNECT_REFUSAL)

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        if ctx.speaker_member_id is None:
            raise RuntimeError("disconnect requires a speaking member")
        disconnected = await ctx.grants.disconnect(
            owner.generation,
            actor_member_id=ctx.speaker_member_id,
        )
        if not disconnected:
            raise ValueError(f"connection {name!r} changed while disconnecting")


@dataclass(frozen=True)
class ConnectorGrantObjects(MemberReadableObjects[ConnectorGrantSpec, GeneratedObjectOwner]):
    kind_name: ClassVar[str] = CONNECTOR_GRANT_KIND
    mutate_gate: ClassVar[str] = SHARE_GATE
    delete_gate: ClassVar[str] = REVOKE_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool:
        return old.shared and spec == old.model_copy(update={"shared": False})

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        return tuple(
            OwnedRow(
                name=name,
                summary=(
                    f"{row.provider} account {row.account_id} "
                    f"({'shared' if row.shared else 'private'})"
                ),
                owner=GeneratedObjectOwner(
                    member_id=row.owner_member_id,
                    shared=row.shared,
                    generation=row.id,
                ),
            )
            for name, row in _named(await grant_summaries()).items()
        )

    async def _member_object(
        self,
        ext: ExtensionContext | None,
        name: str,
        owner: GeneratedObjectOwner,
        *,
        member_id: UUID | None,
    ) -> ObjectDetail[ConnectorGrantSpec] | None:
        row = next(
            (summary for summary in await grant_summaries() if summary.id == owner.generation),
            None,
        )
        if row is None:
            return None
        return ObjectDetail(
            spec=ConnectorGrantSpec(
                provider=row.provider,
                account_id=row.account_id,
                shared=row.shared,
            ),
            created_at=row.granted_at,
            updated_at=row.updated_at,
            links=(
                ObjectLink(
                    relation="scoped_to",
                    target=ObjectRef(kind=AGENT_KIND, name=row.agent),
                ),
                *(
                    ()
                    if row.shared
                    else (
                        ObjectLink(
                            relation="access_to",
                            target=ObjectRef(
                                kind=CONNECTION_KIND,
                                name=account_object_name(row.provider, row.account_id),
                            ),
                        ),
                    )
                ),
            ),
        )

    async def _status(
        self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner
    ) -> dict[str, JsonValue] | None:
        row = next(
            (summary for summary in await grant_summaries() if summary.id == owner.generation),
            None,
        )
        if row is None:
            return None
        return {
            "owner_member_id": str(row.owner_member_id),
            "host": row.host,
            "agent": row.agent,
            "shared": row.shared,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: ConnectorGrantSpec,
        old: ConnectorGrantSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        if old is None or owner is None:
            raise VerbNotSupported(CONNECT_REFUSAL)
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        if ctx.speaker_member_id is None:
            raise RuntimeError("changing disclosure requires a speaking member")
        row = next(
            (summary for summary in await grant_summaries() if summary.id == owner.generation),
            None,
        )
        if row is None:
            raise ValueError(f"connector grant {name!r} changed while editing")
        current = ConnectorGrantSpec(
            provider=row.provider,
            account_id=row.account_id,
            shared=row.shared,
        )
        if spec.model_copy(update={"shared": current.shared}) != current:
            raise VerbNotSupported(CONNECT_REFUSAL)
        if spec.shared == current.shared:
            return
        updated = await ctx.grants.set_shared(
            owner.generation,
            spec.shared,
            actor_member_id=ctx.speaker_member_id,
        )
        if not updated:
            raise ValueError(f"connector grant {name!r} changed while editing")

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        if ctx.speaker_member_id is None:
            raise RuntimeError("revoking access requires a speaking member")
        revoked = await ctx.grants.revoke(
            owner.generation,
            actor_member_id=ctx.speaker_member_id,
        )
        if not revoked:
            raise ValueError(f"connector grant {name!r} changed while revoking")


CONNECTION_OBJECT = ObjectKind(
    name=CONNECTION_KIND,
    description=(
        "A member-owned provider connection. Created through connect_account; delete disconnects "
        "it from every agent."
    ),
    guidance=(
        "Use this kind to inspect or disconnect a provider account. A connection belongs to the "
        "member who completed consent and is independent of agents. Its owner or a workspace "
        "admin may delete it, disconnecting every connector_grant edge."
    ),
    spec_model=ConnectionSpec,
    store=ConnectionObjects(),
)

CONNECTOR_GRANT_OBJECT = ObjectKind(
    name=CONNECTOR_GRANT_KIND,
    description=(
        "One agent's access to a connected provider account. Apply flips `shared`; delete revokes "
        "only this agent's access."
    ),
    guidance=(
        "Use this kind to manage the current agent's connection access. Create is refused; "
        "connect_account creates the edge. Its connection owner may share or make it private; a "
        "workspace admin may only make it private. Its owner or an admin may delete it, revoking "
        "only this agent's edge while leaving the connection and other agents' edges intact. Its "
        "`scoped_to` link names the agent holding the edge; while it is private its `access_to` "
        "link names the connection the edge opens — object_get that for the account's owner and "
        "every agent holding it."
    ),
    spec_model=ConnectorGrantSpec,
    store=ConnectorGrantObjects(),
)
