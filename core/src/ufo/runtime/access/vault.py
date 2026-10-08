"""A bound secret's value by its name, released only on the host its declaration admits.

A session policy binds each secret by name and carries no value (`egress_rules`): a workspace
credential slot by its own name, a connection as `connection:<id>`, the deploy's model key as
`ufo/models`. This resolves such a name to its value, and each name answers only for the hosts its
declaration names — an injecting slot's host, a feed slot's provider hosts and its keyed
connection's admitted tenant host, a connection's own host and its CLI's git host, a model
provider's API host — so a policy binding a name on any other host is handed nothing. `describe`
answers what a name is without its value, for a service deciding whether to bind it. It is core's
because the values sit where only core reaches: the encrypted credential store, the connection
rows, each connector's broker read, and the deploy's environment."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.runtime.access.connectors import CliCredential, ConnectorRegistry, brokered_account
from ufo.runtime.access.credentials import (
    CredentialSlotUnset,
    CredentialStore,
    credential_host,
    deploy_env,
)
from ufo.runtime.access.egress_rules import CONNECTION_SECRET_PREFIX, UFO_MODELS_SECRET
from ufo.runtime.access.grants import tenant_url
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.ext.context import SelfUserIdResolver
from ufo.runtime.ext.manifest import CredentialSlot, FeedRelease
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
class SecretDescription:
    """What a vault name is, never its value: `static` for a key the workspace holds and `oauth`
    for an account a broker holds; `usable` when it can be bound now and `released` when `resolve`
    answers it on some host; the broker extension and its account for a brokered connection; the
    connection's tenant URL, disclosure and owner; and `self_user_id`, the product's own speaker on
    the provider's surface where that surface resolves one."""

    name: str
    kind: Literal["static", "oauth"]
    provider: str | None
    usable: bool
    released: bool
    broker: str | None
    account: str | None
    base_url: str | None
    shared: bool
    owner_member_id: UUID | None
    self_user_id: str | None


@dataclass(frozen=True)
class VaultReads:
    """The one reach from a name to a value, released only for the host the name's declaration
    admits. `declared` is every slot the deploy's manifests declare, by name; `connectors` names the
    broker extension of a brokered connection; `self_user_ids` resolves a provider's surface
    speaker."""

    credentials: CredentialStore
    slots: WorkspaceSlots
    clis: Mapping[str, CliCredential]
    model_key_envs: Mapping[str, str]
    declared: Mapping[str, CredentialSlot]
    connectors: ConnectorRegistry
    self_user_ids: Mapping[str, SelfUserIdResolver]

    async def resolve(self, workspace_id: UUID, name: str, host: str) -> SecretValue:
        """The value `name` takes on the wire to `host` for the workspace, or `SecretUnbound` when
        no declaration binds it there or nothing is stored. `ufo/models` is the deploy's key for a
        model provider's host, `connection:<id>` the token its connector's broker holds for that
        connection's own account, and any other name a workspace credential slot, read only on
        the host its injection resolves to or, for a feed slot, its provider's hosts and the
        admitted tenant host of the workspace's keyed connection to that provider. No value here
        carries an expiry, and a broker's fault propagates."""
        with ws(workspace_id):
            if name == UFO_MODELS_SECRET:
                env = self.model_key_envs.get(host)
                value = None if env is None else deploy_env(env)
            elif name.startswith(CONNECTION_SECRET_PREFIX):
                value = await self._connection_token(workspace_id, name, host)
            else:
                value = await self._slot_value(workspace_id, name, host)
        if value is None:
            raise SecretUnbound(
                f"No secret named {name!r} is bound on {host!r} for this workspace."
            )
        return SecretValue(value=value, expires_at=None)

    async def _connection_token(self, workspace_id: UUID, name: str, host: str) -> str | None:
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
                    ).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.id == connection_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        cli = self.clis.get(row.provider)
        git_host = None if cli is None or cli.git is None else cli.git.host
        if cli is None or host not in (row.host, git_host):
            return None
        return await cli.secret.secret(workspace_id, row.account_id)

    async def describe(self, workspace_id: UUID, name: str) -> SecretDescription:
        """What `name` is for the workspace, or `SecretUnbound` for `ufo/models`, a name no slot
        declares, and a connection the workspace does not hold. A connection is usable while its
        row stands: a broker account's health is the broker's to answer, read where it syncs."""
        with ws(workspace_id):
            if name.startswith(CONNECTION_SECRET_PREFIX):
                described = await self._connection_description(workspace_id, name)
            elif name == UFO_MODELS_SECRET:
                described = None
            else:
                described = await self._slot_description(workspace_id, name)
        if described is None:
            raise SecretUnbound(f"No secret named {name!r} is held by this workspace.")
        return described

    async def _connection_description(
        self, workspace_id: UUID, name: str
    ) -> SecretDescription | None:
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
                        tables.connection.c.base_url,
                        tables.connection.c.shared,
                        tables.connection.c.owner_member_id,
                    ).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.id == connection_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        account = brokered_account(row.account_id)
        return SecretDescription(
            name=name,
            kind="static" if account is None else "oauth",
            provider=row.provider,
            usable=True,
            released=row.provider in self.clis,
            broker=None if account is None else self.connectors.entry(row.provider).broker_name,
            account=account,
            base_url=row.base_url,
            shared=row.shared,
            owner_member_id=row.owner_member_id,
            self_user_id=await self._self_user_id(workspace_id, row.provider),
        )

    async def _slot_description(self, workspace_id: UUID, name: str) -> SecretDescription | None:
        slot = await self._slot(workspace_id, name)
        if slot is None:
            return None
        filled = name in await self.credentials.stored_slots(workspace_id)
        provider = None if slot.feed is None else slot.feed.provider
        return SecretDescription(
            name=name,
            kind="static",
            provider=provider,
            usable=filled,
            released=filled and (slot.injection is not None or slot.feed is not None),
            broker=None,
            account=None,
            base_url=None,
            shared=True,
            owner_member_id=None,
            self_user_id=None
            if provider is None
            else await self._self_user_id(workspace_id, provider),
        )

    async def _self_user_id(self, workspace_id: UUID, provider: str) -> str | None:
        resolver = self.self_user_ids.get(provider)
        return None if resolver is None else await resolver(workspace_id)

    async def _slot_value(self, workspace_id: UUID, name: str, host: str) -> str | None:
        slot = await self._slot(workspace_id, name)
        if slot is None:
            return None
        if slot.feed is not None:
            released = host in slot.feed.hosts or (
                slot.feed.tenant and host in await self._tenant_hosts(workspace_id, slot.feed)
            )
        elif slot.injection is not None:
            released = (
                await credential_host(self.credentials, workspace_id, slot.injection.host) == host
            )
        else:
            released = False
        if not released:
            return None
        try:
            return await self.credentials.get(workspace_id, name)
        except CredentialSlotUnset:
            return None

    async def _slot(self, workspace_id: UUID, name: str) -> CredentialSlot | None:
        declared = self.declared.get(name)
        if declared is not None:
            return declared
        return next(
            (slot for slot in await self.slots.all(workspace_id) if slot.name == name), None
        )

    async def _tenant_hosts(self, workspace_id: UUID, feed: FeedRelease) -> frozenset[str]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.connection.c.account_id, tables.connection.c.base_url).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.provider == feed.provider,
                        tables.connection.c.base_url.is_not(None),
                    )
                )
            ).all()
        hosts: set[str] = set()
        for row in rows:
            if brokered_account(row.account_id) is not None:
                continue
            try:
                admitted = tenant_url(feed.provider, row.base_url)
            except ValueError:
                continue
            if admitted is not None:
                hosts.add(urlsplit(admitted).netloc)
        return frozenset(hosts)
