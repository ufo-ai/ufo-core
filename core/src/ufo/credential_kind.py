"""The `credential` object kind: declared BYOK slots projected as workspace objects.

Instances are the slots installed extensions declare, filled or not — the declaration lives in
the manifest, the row holds only the sealed value. The value appears in no read: spec is the
declaration (slot, description, injection host), status says filled or empty and when that
changed — no value field, no value digest. Fill and rotate stay `request_credentials` (a secret
and a private handoff, the speaker gating the act), so create and update refuse naming it;
delete clears the stored value, owner-gated in the handler, and the slot stays listed as empty.

Core-registered: the loader builds the kind from every active manifest's declared slots and binds
it with no extension context — the handlers read the ambient workspace directly, exactly as
`ScopedStore` does."""

import hashlib
import re
from dataclasses import dataclass

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.objects import (
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    OwnerRequired,
    VerbNotSupported,
    object_page,
)
from ufo.schema import tables
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

CREDENTIAL_KIND = "credential"
FILL_REFUSAL = (
    "filling or rotating a credential involves a secret and a private handoff — use "
    "request_credentials"
)
UNSET_GATE = "only the workspace owner can clear a credential slot"
NAME_DIGEST_LENGTH = 8


@dataclass(frozen=True)
class DeclaredSlot:
    """One declared BYOK slot as the kind projects it: the slot name, its documentation, the
    extension that declares it, and the injection host when the slot carries a wire target."""

    name: str
    description: str
    extension: str
    injection_host: str = ""


class CredentialSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: str
    description: str = ""
    extension: str = ""
    injection_host: str = ""


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


@dataclass(frozen=True)
class CredentialObjects:
    """The kind's handlers over the declared slots and the sealed `credential` table: list shows
    every slot with its fill state, get renders the declaration beside filled-or-empty status,
    delete clears the stored value. No handler reads the ciphertext column."""

    slots: tuple[DeclaredSlot, ...]

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        named = self._named()
        filled = await self._filled_slots()
        rows = [
            ObjectRow(
                name=name,
                summary=(
                    f"{slot.extension}: {slot.description or slot.name} — "
                    f"{'filled' if slot.name in filled else 'empty'}"
                ),
            )
            for name, slot in sorted(named.items())
        ]
        return object_page(tuple(rows), query)

    async def get(self, ctx: ToolContext, name: str) -> CredentialSpec | None:
        slot = self._named().get(name)
        if slot is None:
            return None
        return CredentialSpec(
            slot=slot.name,
            description=slot.description,
            extension=slot.extension,
            injection_host=slot.injection_host,
        )

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        slot = self._named().get(name)
        if slot is None:
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.credential.c.updated_at).where(
                        tables.credential.c.workspace_id == ws_current().workspace_id,
                        tables.credential.c.slot == slot.name,
                    )
                )
            ).one_or_none()
        return {
            "filled": row is not None,
            "updated_at": None if row is None else row.updated_at.isoformat(),
        }

    async def apply(
        self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None
    ) -> None:
        raise VerbNotSupported(FILL_REFUSAL)

    async def delete(self, ctx: ToolContext, name: str) -> None:
        if not await ctx.speaker_is_owner():
            raise OwnerRequired(UNSET_GATE)
        slot = self._named()[name]
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.credential).where(
                    tables.credential.c.workspace_id == ws_current().workspace_id,
                    tables.credential.c.slot == slot.name,
                )
            )

    def _named(self) -> dict[str, DeclaredSlot]:
        grouped: dict[str, list[DeclaredSlot]] = {}
        for slot in self.slots:
            grouped.setdefault(_slug(slot.name), []).append(slot)
        named: dict[str, DeclaredSlot] = {}
        for plain, group in grouped.items():
            if len(group) == 1:
                named[plain] = group[0]
                continue
            for slot in group:
                qualifier = hashlib.sha256(f"{slot.extension}:{slot.name}".encode()).hexdigest()
                named[f"{plain}-{qualifier[:NAME_DIGEST_LENGTH]}"] = slot
        return named

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
    "A declared BYOK credential slot, filled or empty — the value itself is never shown. Fill "
    "or rotate through request_credentials; delete (owner-only) clears the stored value while "
    "the slot stays declared."
)
CREDENTIAL_GUIDANCE = (
    "The BYOK secret slots installed extensions declare, filled or empty; values never appear "
    "in any read. Create and update are refused — filling or rotating a secret goes through "
    "request_credentials, a private handoff the workspace owner authorizes. Delete clears a "
    "stored value (workspace owner only); the slot stays listed as empty because its "
    "declaration lives in the extension, not the row."
)
