"""The `credential` object kind: declared BYOK slots projected as workspace objects.

Instances are the slots installed extensions declare, filled or not — the declaration lives in
the manifest, the row holds only the sealed value, and an empty slot has no row (its envelope
timestamps are null). The value appears in no read: spec is the declaration (slot, description,
injection host), status says filled or empty — no value field, no value digest. Fill and rotate
stay `request_credentials` (a secret
and a private handoff, the speaker gating the act), so create and update refuse naming it;
delete clears the stored value, admin-gated in the handler, and the slot stays listed as empty.
A signed-in member reads the same index and declaration in the portal: a declaration carries no
member scope and no read discloses a value, so the workspace is the whole audience on both paths.

Core-registered: the loader builds the kind from every active manifest's declared slots and binds
it with no extension context — the handlers read the ambient workspace directly, exactly as
`ScopedStore` does."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import workspace_tx
from ufo.runtime.access.credentials import (
    CredentialStore,
    DeclaredSlot,
    HostChoice,
    credential_host,
    named_slots,
)
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.objects import (
    AdminRequired,
    MemberObject,
    ObjectDetail,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

CREDENTIAL_KIND = "credential"
FILL_REFUSAL = (
    "filling or rotating a credential involves a secret and a private handoff — run the "
    "collection's request_credentials action"
)
UNSET_GATE = "only a workspace admin can clear a credential slot"


class CredentialSpec(BaseModel):
    """The declaration a read renders. `host` is the fixed injection host; a slot whose provider
    pins its host per account instead carries `host_slot` and the `host_options` a member may
    select, so the agent asks with the real answers rather than inviting a hostname."""

    model_config = ConfigDict(extra="forbid")
    slot: str
    description: str = ""
    extension: str = ""
    host: str = ""
    host_slot: str = ""
    host_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class CredentialObjects:
    """The kind's handlers over the declared slots and the sealed `credential` table: list shows
    every slot with its fill state, get renders the declaration beside filled-or-empty status,
    delete clears the stored value. No handler reads the ciphertext column."""

    slots: tuple[DeclaredSlot, ...]
    credentials: CredentialStore | None = None

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        return object_page(await self._rows(), query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The slot index a signed-in member reads — every declared slot with its fill state, the
        rows `list` produces."""
        return object_page(await self._rows(), query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None:
        return await self._detail(name)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[CredentialSpec] | None:
        """One slot as the portal reads it: the row `list` renders beside the declaration `get`
        reads, on the same workspace-wide read both answer. A slot no manifest declares is absent
        for every member."""
        detail = await self._detail(name)
        if detail is None:
            return None
        row = next(row for row in await self._rows() if row.name == name)
        return MemberObject(row=row, detail=detail)

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        slot = self._named().get(name)
        if slot is None:
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.credential.c.slot).where(
                        tables.credential.c.workspace_id == ws_current().workspace_id,
                        tables.credential.c.slot == slot.name,
                    )
                )
            ).one_or_none()
        status: dict[str, JsonValue] = {"filled": row is not None}
        if slot.host is not None and self.credentials is not None:
            status["host"] = await credential_host(
                self.credentials, ws_current().workspace_id, slot.host
            )
        return status

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: CredentialSpec,
        old: CredentialSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(FILL_REFUSAL)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        if not await ctx.speaker_is_admin():
            raise AdminRequired(UNSET_GATE)
        slot = self._named()[name]
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.credential).where(
                    tables.credential.c.workspace_id == ws_current().workspace_id,
                    tables.credential.c.slot == slot.name,
                )
            )

    async def _rows(self) -> tuple[ObjectRow, ...]:
        filled = await self._filled_slots()
        return tuple(
            ObjectRow(
                name=name,
                summary=(
                    f"{slot.extension}: {slot.description or slot.name} — "
                    f"{'filled' if slot.name in filled else 'empty'}"
                ),
                fields={
                    "slot": slot.name,
                    "extension": slot.extension,
                    "filled": slot.name in filled,
                },
            )
            for name, slot in sorted(self._named().items())
        )

    async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None:
        slot = self._named().get(name)
        if slot is None:
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.credential.c.created_at, tables.credential.c.updated_at).where(
                        tables.credential.c.workspace_id == ws_current().workspace_id,
                        tables.credential.c.slot == slot.name,
                    )
                )
            ).one_or_none()
        return ObjectDetail(
            spec=CredentialSpec(
                slot=slot.name,
                description=slot.description,
                extension=slot.extension,
                host=slot.host if isinstance(slot.host, str) else "",
                host_slot=slot.host.slot if isinstance(slot.host, HostChoice) else "",
                host_options=slot.host.hosts if isinstance(slot.host, HostChoice) else (),
            ),
            created_at=None if row is None else row.created_at,
            updated_at=None if row is None else row.updated_at,
        )

    def _named(self) -> dict[str, DeclaredSlot]:
        return named_slots(self.slots)

    async def _filled_slots(self) -> frozenset[str]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.credential.c.slot).where(
                        tables.credential.c.workspace_id == ws_current().workspace_id
                    )
                )
            ).all()
        return frozenset(row.slot for row in rows)


CREDENTIAL_DESCRIPTION = (
    "A BYOK credential slot an extension declares, filled or empty. The stored value is never "
    "shown."
)
CREDENTIAL_GUIDANCE = (
    "The BYOK secret slots installed extensions declare, filled or empty; values never appear "
    "in any read. Listings filter and order on `extension` and `filled` — filter "
    "`filled: false` for the slots still to fill, or `extension` for one extension's. "
    "Create and update are refused — filling or rotating a secret goes through the collection's "
    "request_credentials action, a private handoff a workspace admin authorizes. Delete clears a "
    "stored value (workspace admin only); the slot stays listed as empty because its "
    "declaration lives in the extension, not the row."
)
