"""Connections and their per-agent connector grants as workspace objects, read through one owner
gate in a turn and in the portal.

A connection is either one member's provider account or the workspace's own — a keyed provider, a
configured feed — which no member owns and every agent's grant reaches. The member-owned one is its
owner's or an admin's to edit; the workspace's own is an admin's, and it cannot be made private,
because a connection nobody owns has nobody to be private to."""

from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, JsonValue
from ufo.sdk.grants import (
    MAX_BACKFILL_DAYS,
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
from ufo.sdk.tools import SpeakerRequired, ToolContext

CONNECTION_KIND = "connection"
CONNECTOR_GRANT_KIND = "connector_grant"
CONNECT_REFUSAL = "connecting an account involves a third party — use connect_account"
SHARE_GATE = (
    "only the connection owner may share; the owner or a workspace admin may make it private or "
    "change what it reads"
)
DISCONNECT_GATE = "only the connection owner or a workspace admin may disconnect an account"
WORKSPACE_DISCONNECT_REFUSAL = (
    "this is the workspace's own connection — a provider key or a configured feed, not an account "
    "anyone consented to — so its feed stops when the credential slot is cleared or the configured "
    "entry removed, and the registrar removes the connection on its next tick; deleting it here "
    "would only be undone by the same tick"
)
GRANT_GATE = "only the connection owner or a workspace admin may change an agent's grant"
REVOKE_GATE = "only the connection owner or a workspace admin may revoke an agent's grant"
UNOWNED_PRIVATE_REFUSAL = (
    "this connection belongs to the workspace rather than to a member, so it cannot be made private"
)


class ConnectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    account_id: str
    shared: bool = Field(
        default=False,
        description="Let every agent in the workspace use this account and read what it syncs, "
        "rather than only the member who connected it.",
    )
    base_url: str = Field(
        default="",
        description="Tenant API URL, for a provider that has one; empty dials the provider's own "
        "host.",
    )
    backfill_days: int | None = Field(
        default=None,
        ge=1,
        le=MAX_BACKFILL_DAYS,
        description="How far back this account's content is read, in days, at most "
        f"{MAX_BACKFILL_DAYS}; unset reaches back as far as each stream declares.",
    )


class ConnectorGrantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    account_id: str


class _AccountSummary(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def account_id(self) -> str: ...

    @property
    def owner_email(self) -> str | None: ...

    @property
    def shared(self) -> bool: ...


def _named[SummaryT: _AccountSummary](rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]:
    return {account_object_name(row.provider, row.account_id): row for row in rows}


def _summary(row: _AccountSummary) -> str:
    """The one-line row both kinds render: which account, whose it is, and whether the workspace
    shares it. The workspace's own connection holds no account handle and no owner address, so it
    reads as the workspace's."""
    account = f"account {row.account_id}" if row.account_id else "workspace connection"
    holder = row.owner_email or "no owner"
    return f"{row.provider} {account} ({holder}, {'shared' if row.shared else 'private'})"


@dataclass(frozen=True)
class ConnectionObjects(MemberReadableObjects[ConnectionSpec, GeneratedObjectOwner]):
    kind_name: ClassVar[str] = CONNECTION_KIND
    mutate_gate: ClassVar[str] = SHARE_GATE
    delete_gate: ClassVar[str] = DISCONNECT_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    def _admin_can_apply(self, old: ConnectionSpec, spec: ConnectionSpec) -> bool:
        """What an admin who does not own the connection may submit: narrowing it to private, and
        changing what it reads. Widening is the owner's alone, because it hands their account to
        every agent in the workspace."""
        identical = (spec.provider, spec.account_id) == (old.provider, old.account_id)
        return identical and (not spec.shared or spec.shared == old.shared)

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        return tuple(
            OwnedRow(
                name=name,
                summary=_summary(row),
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
            spec=ConnectionSpec(
                provider=row.provider,
                account_id=row.account_id,
                shared=row.shared,
                base_url=row.base_url or "",
                backfill_days=row.backfill_days,
            ),
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
        if ctx.ext is None:
            raise RuntimeError(
                "a connection's status reads its streams through the extension context"
            )
        streams: list[JsonValue] = [
            {
                "stream": str(record.config.get("stream", record.backend)),
                "next_sync_at": record.next_sync_at.isoformat(),
                "errors": record.consecutive_errors,
                "parked": record.parked_reason,
            }
            for record in sorted(
                (
                    record
                    for record in await ctx.ext.sources(row.provider)
                    if record.connection_id == owner.generation
                ),
                key=lambda record: str(record.config.get("stream", record.backend)),
            )
        ]
        return {
            "owner_member_id": None if row.owner_member_id is None else str(row.owner_member_id),
            "owner": row.owner_email,
            "shared": row.shared,
            "host": row.host,
            "agents": list(row.agents),
            "streams": streams,
        }

    async def _apply_owned(
        self,
        ctx: ToolContext,
        name: str,
        spec: ConnectionSpec,
        old: ConnectionSpec | None,
        owner: GeneratedObjectOwner | None,
    ) -> None:
        """Change who may use this connection and what its streams read: `shared` through
        `set_shared`, which restamps the disclosure of every page already synced, and the tenant URL
        and backfill window through `set_feed`. Provider and account are the connection's identity,
        so an apply that moves either is refused whole: only connect_account creates a connection,
        and only disconnecting retires one."""
        if old is None or owner is None:
            raise VerbNotSupported(CONNECT_REFUSAL)
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        speaker = ctx.speaker_member_id
        if speaker is None:
            raise SpeakerRequired("changing a connection requires a speaking member")
        row = next(
            (summary for summary in await connection_summaries() if summary.id == owner.generation),
            None,
        )
        if row is None:
            raise ValueError(f"connection {name!r} changed while editing")
        if (spec.provider, spec.account_id) != (row.provider, row.account_id):
            raise VerbNotSupported(CONNECT_REFUSAL)
        if spec.shared != row.shared:
            if not spec.shared and row.owner_member_id is None:
                raise VerbNotSupported(UNOWNED_PRIVATE_REFUSAL)
            if not await ctx.grants.set_shared(
                owner.generation, spec.shared, actor_member_id=speaker
            ):
                raise ValueError(f"connection {name!r} changed while editing")
        if (spec.base_url, spec.backfill_days) == (row.base_url or "", row.backfill_days):
            return
        if not await ctx.grants.set_feed(
            owner.generation,
            base_url=spec.base_url or None,
            backfill_days=spec.backfill_days,
            actor_member_id=speaker,
        ):
            raise ValueError(f"connection {name!r} changed while editing")

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        if ctx.speaker_member_id is None:
            raise SpeakerRequired("disconnect requires a speaking member")
        if owner.member_id is None:
            raise VerbNotSupported(WORKSPACE_DISCONNECT_REFUSAL)
        disconnected = await ctx.grants.disconnect(
            owner.generation,
            actor_member_id=ctx.speaker_member_id,
        )
        if not disconnected:
            raise ValueError(f"connection {name!r} changed while disconnecting")


@dataclass(frozen=True)
class ConnectorGrantObjects(MemberReadableObjects[ConnectorGrantSpec, GeneratedObjectOwner]):
    kind_name: ClassVar[str] = CONNECTOR_GRANT_KIND
    mutate_gate: ClassVar[str] = GRANT_GATE
    delete_gate: ClassVar[str] = REVOKE_GATE
    mutate_requires_speaker: ClassVar[bool] = True
    delete_requires_speaker: ClassVar[bool] = True

    async def _member_rows(
        self, ext: ExtensionContext | None, *, member_id: UUID | None
    ) -> tuple[OwnedRow[GeneratedObjectOwner], ...]:
        return tuple(
            OwnedRow(
                name=name,
                summary=_summary(row),
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
            spec=ConnectorGrantSpec(provider=row.provider, account_id=row.account_id),
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
            "owner_member_id": None if row.owner_member_id is None else str(row.owner_member_id),
            "owner": row.owner_email,
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
        """Attach a connection the workspace already holds to this agent. A grant edge holds
        nothing else — which account it opens is its identity and sharing is the connection's — so
        an apply naming a different account is a different object, and one naming this same account
        has already landed."""
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        if ctx.speaker_member_id is None:
            raise SpeakerRequired("attaching requires a speaking member")
        if old is None and owner is None:
            attached = await ctx.grants.attach(
                provider=spec.provider,
                account_id=spec.account_id,
                actor_member_id=ctx.speaker_member_id,
                shared=False,
            )
            if not attached:
                raise ValueError(f"connection {name!r} is not available")
            return
        if old is None or owner is None:
            raise VerbNotSupported(CONNECT_REFUSAL)
        row = next(
            (summary for summary in await grant_summaries() if summary.id == owner.generation),
            None,
        )
        if row is None:
            raise ValueError(f"connector grant {name!r} changed while editing")
        if (spec.provider, spec.account_id) != (row.provider, row.account_id):
            raise VerbNotSupported(CONNECT_REFUSAL)

    async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None:
        if ctx.grants is None:
            raise RuntimeError("grants unavailable: no credential key configured")
        if ctx.speaker_member_id is None:
            raise SpeakerRequired("revoking access requires a speaking member")
        revoked = await ctx.grants.revoke(
            owner.generation,
            actor_member_id=ctx.speaker_member_id,
        )
        if not revoked:
            raise ValueError(f"connector grant {name!r} changed while revoking")


CONNECTION_OBJECT = ObjectKind(
    name=CONNECTION_KIND,
    description=(
        "A provider connection: who may use it and what its content sync reads. Created through "
        "connect_account; delete disconnects it from every agent."
    ),
    guidance=(
        "Use this kind to inspect a provider account, share it with the workspace, change what "
        "its content sync reads, or disconnect it. A connection belongs to the member who "
        "completed consent, or to the workspace where a keyed provider or a configured feed "
        "authenticates without one. Apply changes `shared`, `base_url` and `backfill_days` and "
        "nothing else. `shared` hands the account to every agent in the workspace and restamps "
        "everything it has synced as workspace-readable; only its owner may set it, the owner or "
        "a workspace admin may unset it, and a connection nobody owns is always shared. "
        "`base_url` is the tenant API URL a per-tenant provider needs, and such a provider syncs "
        "nothing until it is set. `backfill_days` is how far back the first sync of each stream "
        f"reaches, at most {MAX_BACKFILL_DAYS}, and unset reaches back as far as each stream "
        "declares. Status lists the streams the connection syncs, each with its next sync, its "
        "error count and the reason the provider parked it, if it did. An apply that moves "
        "`provider` or `account_id` is refused — connect_account is the only way to create a "
        "connection. Delete is the owner's or an admin's and disconnects "
        "it, removing every connector_grant edge and everything its streams synced. The "
        "workspace's own connection has no owner and is not deleted here: clearing its credential "
        "slot or removing its configured entry is what stops that feed."
    ),
    spec_model=ConnectionSpec,
    store=ConnectionObjects(),
)

CONNECTOR_GRANT_OBJECT = ObjectKind(
    name=CONNECTOR_GRANT_KIND,
    description=(
        "One agent's access to a connected provider account. Apply attaches an existing "
        "connection; delete revokes only this agent's access."
    ),
    guidance=(
        "Use this kind to give one agent access to a connection the workspace already holds. "
        "Apply with a new name attaches it; connect_account is the only way to create a "
        "connection, and sharing one with the whole workspace is the `connection` kind's own "
        "`shared` field, not this one. The workspace main agent may apply with `agent:` to attach "
        "a held connection to another agent with no new authorization — the speaker must own the "
        "connection or it must be shared. Its connection owner or a workspace admin may delete "
        "it, revoking only this agent's edge while leaving the connection and other agents' edges "
        "intact. Its `scoped_to` link names the agent holding the edge; while the connection is "
        "private its `access_to` link names the connection the edge opens — pass that target "
        "unchanged to object_get."
    ),
    spec_model=ConnectorGrantSpec,
    store=ConnectorGrantObjects(),
    agent_target_verbs=frozenset({"create", "update"}),
)
