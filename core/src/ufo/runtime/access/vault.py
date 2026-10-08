"""A bound secret's value by its name, released only on the host its declaration admits.

A session policy binds each secret by name and carries no value (`egress_rules`): a workspace
credential slot by its own name, a connection as `connection:<id>`, the deploy's model key as
`ufo/models`. This resolves such a name to its value, and each name answers only for the hosts its
declaration names — an injecting slot's host, a connection's own host and its CLI's git host, a
model provider's API host — so a policy binding a name on any other host is handed nothing. A
member's private connection answers only a session created for that member. It is
core's because the values sit where only core reaches: the encrypted credential store, the
connection rows, each connector's broker read, and the deploy's environment."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import (
    CredentialSlotUnset,
    CredentialStore,
    credential_host,
    deploy_env,
)
from ufo.runtime.access.egress_rules import CONNECTION_SECRET_PREFIX, UFO_MODELS_SECRET
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.workspace import ws
from ufo.schema import tables


@dataclass(frozen=True, slots=True)
class SecretValue:
    """A bound secret's value, and when it stops being valid if its holder says."""

    value: str = field(repr=False)
    expires_at: datetime | None


class SecretUnbound(LookupError):
    """No secret of that name rides to that host for the bound workspace."""


@dataclass(frozen=True)
class VaultReads:
    """The one reach from a name to a value, released only for the host the name's declaration
    admits."""

    credentials: CredentialStore
    slots: WorkspaceSlots
    clis: Mapping[str, CliCredential]
    model_key_envs: Mapping[str, str]

    async def resolve(
        self, workspace_id: UUID, name: str, host: str, member_id: UUID | None
    ) -> SecretValue:
        """The value `name` takes on the wire to `host` for the workspace, or `SecretUnbound` when
        no declaration binds it there or nothing is stored. `ufo/models` is the deploy's key for a
        model provider's host, `connection:<id>` the token its connector's broker holds for that
        connection's own account, released only when the connection is shared or `member_id` owns
        it, and any other name a workspace credential slot, read only on the host its injection
        resolves to whoever asks. No value here carries an expiry, and a broker's fault
        propagates."""
        with ws(workspace_id):
            if name == UFO_MODELS_SECRET:
                env = self.model_key_envs.get(host)
                value = None if env is None else deploy_env(env)
            elif name.startswith(CONNECTION_SECRET_PREFIX):
                value = await self._connection_token(workspace_id, name, host, member_id)
            else:
                value = await self._slot_value(workspace_id, name, host)
        if value is None:
            raise SecretUnbound(
                f"No secret named {name!r} is bound on {host!r} for this workspace."
            )
        return SecretValue(value=value, expires_at=None)

    async def _connection_token(
        self, workspace_id: UUID, name: str, host: str, member_id: UUID | None
    ) -> str | None:
        try:
            connection_id = UUID(name.removeprefix(CONNECTION_SECRET_PREFIX))
        except ValueError:
            return None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.connection.c.provider,
                        tables.connection.c.account_id,
                        tables.connection.c.host,
                        tables.connection.c.shared,
                        tables.connection.c.owner_member_id,
                    ).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.id == connection_id,
                    )
                )
            ).one_or_none()
        if row is None or not (row.shared or row.owner_member_id == member_id):
            return None
        cli = self.clis.get(row.provider)
        git_host = None if cli is None or cli.git is None else cli.git.host
        if cli is None or host not in (row.host, git_host):
            return None
        return await cli.secret.secret(workspace_id, row.account_id)

    async def _slot_value(self, workspace_id: UUID, name: str, host: str) -> str | None:
        slot = next(
            (slot for slot in await self.slots.all(workspace_id) if slot.name == name), None
        )
        if slot is None or slot.injection is None:
            return None
        if await credential_host(self.credentials, workspace_id, slot.injection.host) != host:
            return None
        try:
            return await self.credentials.get(workspace_id, name)
        except CredentialSlotUnset:
            return None
