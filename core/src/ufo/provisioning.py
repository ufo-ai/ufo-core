"""Agents an extension ships, applied to a workspace.

An active extension declares `AgentProvision`s; this turns each into an ordinary `agent` row and
then stops owning it. The workspace's copy is the live configuration from that moment.

A shipped row is created once and never written again. A later version of the extension reaches new
workspaces only: the row records the extension version that made it, so an operator reads which
spec a workspace actually runs, and the member's own edits are never overwritten.

A shipped agent is identified by the extension that ships it and the name that extension declared,
never by the row's own name. A name already in use — by a member's agent or by a second extension's
— sends the shipped agent to a free variant. Nothing is overwritten and nothing is stuck.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.ext.manifest import AgentProvision, Manifest
from ufo.schema import tables
from ufo.schema.records import auto_agent_icon
from ufo.workspace import ws

CREATED = "created"
ADOPTED = "adopted"
PRESENT = "present"
FREE_NAME_LIMIT = 50


@dataclass(frozen=True)
class ProvisionOutcome:
    extension: str
    name: str
    result: str


@dataclass(frozen=True)
class AgentProvisioning:
    """The provisions the active manifests declare, applied to one workspace."""

    manifests: tuple[Manifest, ...]

    async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]:
        with ws(workspace_id):
            return tuple(
                [
                    await self._one(workspace_id, manifest, provision)
                    for manifest in self.manifests
                    for provision in manifest.agents
                ]
            )

    async def _one(
        self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision
    ) -> ProvisionOutcome:
        extension = manifest.name
        async with workspace_tx() as connection:
            shipped = (
                await connection.execute(
                    sa.select(tables.agent.c.name).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.provisioned_by == extension,
                        tables.agent.c.provisioned_name == provision.name,
                    )
                )
            ).one_or_none()
            if shipped is not None:
                return ProvisionOutcome(extension, shipped.name, PRESENT)

            standing = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.reasoning,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.sandbox_size,
                        tables.agent.c.tools,
                    ).where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.name == provision.name,
                        tables.agent.c.provisioned_by.is_(None),
                    )
                )
            ).one_or_none()
            if standing is not None and self._identical(standing, provision):
                await connection.execute(
                    sa.update(tables.agent)
                    .values(
                        provisioned_by=extension,
                        provisioned_name=provision.name,
                        provisioned_version=manifest.version,
                        setup=provision.setup.model_dump(mode="json"),
                        updated_at=sa.func.now(),
                    )
                    .where(tables.agent.c.id == standing.id)
                )
                return ProvisionOutcome(extension, provision.name, ADOPTED)

            name = await self._free_name(connection, workspace_id, extension, provision.name)
            await self._create(connection, workspace_id, manifest, provision, name)
        return ProvisionOutcome(extension, name, CREATED)

    async def _free_name(
        self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str
    ) -> str:
        """The declared name, or the first free variant of it. Whoever holds the name keeps it — a
        member's own agent, or a second extension that declared the same name and applied first. The
        shipped agent takes `<name>-<extension>`, then a numbered one; the member can tell them
        apart, and neither is overwritten. The suffix spells the extension in the object grammar,
        which has no underscore, so the minted name is addressable by construction."""
        taken = {
            row.name
            for row in await connection.execute(
                sa.select(tables.agent.c.name).where(tables.agent.c.workspace_id == workspace_id)
            )
        }
        suffix = extension.replace("_", "-")
        for candidate in (
            declared,
            f"{declared}-{suffix}",
            *(f"{declared}-{suffix}-{count}" for count in range(2, FREE_NAME_LIMIT)),
        ):
            if candidate not in taken:
                return candidate
        raise ValueError(f"no free name for the {extension!r} agent {declared!r}")

    async def _create(
        self,
        connection: AsyncConnection,
        workspace_id: UUID,
        manifest: Manifest,
        provision: AgentProvision,
        name: str,
    ) -> None:
        """The ordinary row, carrying no grant, credential, source, or memory of the installer's:
        an agent an extension shipped starts with the authority a member gives it in chat. The row
        records what that authority is (`setup`), which is the only part of it an extension may
        state — a member still makes every edge, and the status read tells them which are missing.

        The insert yields to a row already there rather than raising. Two first turns of one
        workspace can read the absent row together — in one process or in two replicas — and both
        reach here; the unique provision identity settles which one writes. An `IntegrityError`
        here would leave the turn path, and fail a member's turn for a write that was not theirs."""
        spec = provision.spec
        taken = (
            (
                await connection.execute(
                    sa.select(tables.agent.c.icon).where(
                        tables.agent.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        await connection.execute(
            insert(tables.agent)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                name=name,
                icon=auto_agent_icon(name, taken),
                prompt=spec.prompt,
                model=spec.model,
                reasoning=spec.reasoning,
                is_main=False,
                internet_access_allowed=spec.internet_access_allowed,
                sandbox_size=spec.sandbox_size,
                tools=list(provision.tools) if provision.tools is not None else None,
                provisioned_by=manifest.name,
                provisioned_name=provision.name,
                provisioned_version=manifest.version,
                setup=provision.setup.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing()
        )

    def _identical(self, row: sa.Row, provision: AgentProvision) -> bool:
        """Whether a row the workspace already holds is the agent this provision would create. An
        identical row is adopted rather than duplicated under a second name."""
        spec = provision.spec
        stored = None if row.tools is None else tuple(row.tools)
        return (
            row.prompt == spec.prompt
            and row.model == spec.model
            and row.reasoning == spec.reasoning
            and row.internet_access_allowed == spec.internet_access_allowed
            and row.sandbox_size == spec.sandbox_size
            and stored == provision.tools
        )
