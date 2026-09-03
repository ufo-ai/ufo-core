"""Registered chat surfaces as read-only workspace objects.

Core owns the kind because no extension sees every manifest or the shared installation table.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ufo.db import workspace_tx
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.object_name import InvalidName, validate_object_name
from ufo.runtime.objects import (
    MemberObject,
    ObjectDetail,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import FOREIGN_AUDIENCE_PREFIX
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

SURFACE_KIND = "surface"
SETUP_IS_THE_SURFACE_OWN_FLOW = (
    "a surface carries nothing authored — its extension declares it, and its own connect flow "
    "binds the installation"
)
REGISTRATION_LEAVES_WITH_THE_EXTENSION = (
    "a surface is registered by its extension's manifest and leaves with the extension, never "
    "through an object"
)


@dataclass(frozen=True)
class RegisteredSurface:
    """One `SurfaceSpec` registration as this kind projects it: the declaring extension and the
    declaration's routing and delivery shape."""

    extension: str
    addressed: bool
    durable: bool
    home: bool


def registered_surfaces(manifests: tuple[Manifest, ...]) -> dict[str, RegisteredSurface]:
    """The `surface` object for each registration the active manifests declare, keyed by the
    surface's own name — the object address is the surface name, so the name is held to the
    object-name grammar, and two extensions registering one name fail loud here, at boot, rather
    than silently hiding one surface behind the other. Both refusals name the extension."""
    registered: dict[str, RegisteredSurface] = {}
    for manifest in manifests:
        for spec in manifest.surfaces:
            try:
                validate_object_name(spec.name)
            except InvalidName as error:
                raise InvalidName(
                    f"extension {manifest.name!r} registers surface {spec.name!r}: {error}"
                ) from error
            if spec.name in registered:
                raise ValueError(
                    f"extensions {registered[spec.name].extension!r} and {manifest.name!r} both "
                    f"register surface {spec.name!r}"
                )
            registered[spec.name] = RegisteredSurface(
                extension=manifest.name,
                addressed=spec.addressed,
                durable=spec.post is not None,
                home=spec.home,
            )
    return registered


class SurfaceObjectSpec(BaseModel):
    """One surface registration as a read renders it: the declaring extension and how the surface
    routes and delivers. Nothing here is authored — the declaration lives in the extension's
    manifest."""

    model_config = ConfigDict(extra="forbid")
    surface: str
    extension: str
    addressed: bool = False
    durable: bool = False
    home: bool = False


@dataclass(frozen=True)
class _BoundInstallation:
    agent: str
    archived: bool
    routes_ingress: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SurfaceObjects:
    """Read-only handlers over the registered surfaces and the workspace's `surface_installation`
    rows: list names every registered surface with its binding state, get renders the declaration
    beside the binding's timestamps, status carries the binding itself, and every mutation refuses.
    Each verb reads the workspace's installations once. No handler reads the installation id
    column."""

    surfaces: Mapping[str, RegisteredSurface]

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return object_page((), query)
        return object_page(self._rows(await self._installations()), query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The surface index a signed-in member reads — workspace transport state is an admin
        concern, so a non-admin member reads no rows."""
        if not admin:
            return object_page((), query)
        return object_page(self._rows(await self._installations()), query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SurfaceObjectSpec] | None:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return None
        if name not in self.surfaces:
            return None
        return self._detail(name, await self._installations())

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[SurfaceObjectSpec] | None:
        """One surface as the portal reads it: the row `list` renders beside the declaration `get`
        reads, answered for an admin alone."""
        if not admin or name not in self.surfaces:
            return None
        installed = await self._installations()
        return MemberObject(
            row=self._row(name, self.surfaces[name], installed),
            detail=self._detail(name, installed),
        )

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        if ctx.audience.startswith(FOREIGN_AUDIENCE_PREFIX):
            return None
        if name not in self.surfaces:
            return None
        bound = (await self._installations()).get(name)
        if bound is None:
            return {"bound": False}
        return {
            "bound": True,
            "agent": bound.agent,
            "archived": bound.archived,
            "routes_ingress": bound.routes_ingress,
        }

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: SurfaceObjectSpec,
        old: SurfaceObjectSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(SETUP_IS_THE_SURFACE_OWN_FLOW)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(REGISTRATION_LEAVES_WITH_THE_EXTENSION)

    def _rows(self, installed: Mapping[str, _BoundInstallation]) -> tuple[ObjectRow, ...]:
        return tuple(
            self._row(name, registered, installed)
            for name, registered in sorted(self.surfaces.items())
        )

    def _row(
        self,
        name: str,
        registered: RegisteredSurface,
        installed: Mapping[str, _BoundInstallation],
    ) -> ObjectRow:
        bound = installed.get(name)
        if bound is None:
            summary = f"{registered.extension}: not bound"
        else:
            summary = f"{registered.extension}: bound to {bound.agent}"
            if bound.archived:
                summary += " (archived)"
        return ObjectRow(
            name=name,
            summary=summary,
            fields={
                "extension": registered.extension,
                "addressed": registered.addressed,
                "durable": registered.durable,
                "home": registered.home,
                "bound": bound is not None,
            },
        )

    def _detail(
        self, name: str, installed: Mapping[str, _BoundInstallation]
    ) -> ObjectDetail[SurfaceObjectSpec]:
        registered = self.surfaces[name]
        bound = installed.get(name)
        return ObjectDetail(
            spec=SurfaceObjectSpec(
                surface=name,
                extension=registered.extension,
                addressed=registered.addressed,
                durable=registered.durable,
                home=registered.home,
            ),
            created_at=None if bound is None else bound.created_at,
            updated_at=None if bound is None else bound.updated_at,
        )

    async def _installations(self) -> dict[str, _BoundInstallation]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.surface_installation.c.surface,
                        tables.surface_installation.c.routes_ingress,
                        tables.surface_installation.c.created_at,
                        tables.surface_installation.c.updated_at,
                        sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name).label(
                            "agent_name"
                        ),
                        tables.agent.c.archived_at,
                    )
                    .join_from(
                        tables.surface_installation,
                        tables.agent,
                        tables.surface_installation.c.agent_id == tables.agent.c.id,
                    )
                    .where(tables.surface_installation.c.workspace_id == ws_current().workspace_id)
                )
            ).all()
        return {
            row.surface: _BoundInstallation(
                agent=row.agent_name,
                archived=row.archived_at is not None,
                routes_ingress=row.routes_ingress,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        }


SURFACE_DESCRIPTION = (
    "A chat surface an installed extension registers, with the installation this workspace holds "
    "for it. Read-only: a surface is set up by its own connect flow."
)
SURFACE_GUIDANCE = (
    "The chat surfaces this deploy registers, one object each, named by the surface name and "
    "listed whether installed or not. The spec is the declaration: `extension` names the "
    "declaring extension; `addressed` means inbound traffic names its member by the sender's "
    "address; `durable` means the turn's replies are written back to the provider, where a live "
    "surface delivers by streaming from its own connection; `home` means a browser at the "
    "deploy's root lands here. Status carries `bound` — whether this workspace holds a "
    "`surface_installation` row for the surface, the binding a connect flow writes when a surface "
    "routes by an external installation identity (a Slack team) or by a shared provider (an "
    "iMessage line) — and, when bound, `agent` for the agent its conversations run as, `archived` "
    "when that agent has since been archived, and `routes_ingress` for whether the installation "
    "identity selects this workspace for inbound traffic. A surface that resolves its workspace "
    "from the request itself — a signed-in browser session, a bearer — never holds an "
    "installation and is ready as registered, so `bound: false` on it is not a setup gap; what a "
    "surface still needs is the answer of its own actions, listed on its object. An addressed "
    "surface with no binding still routes: its conversations run as the workspace's main agent. "
    "Listings filter and order on `extension`, `addressed`, `durable`, `home`, and `bound` — "
    "filter `bound: true` for the surfaces holding an installation. No read carries an "
    "installation id or a provider secret. Create, update, and delete are all refused: an "
    "extension declares its surface, and the surface's own connect flow binds the installation. "
    "Reads answer an internal conversation only — an externally shared channel lists nothing."
)
