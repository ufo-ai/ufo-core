"""Grants: an OAuth account bound to an agent through `/connect`, the account-token analog of a
credential slot — a secret won in chat rather than a BYOK value set at deploy. The proxy's egress
scope is derived from the workspace's grants, so a granted host is reachable with its token swapped
onto the wire and every ungranted host is refused at CONNECT.

`ConnectFlow` runs the two-legged OAuth handoff: `authorize` opens a provider's link carrying sealed
state; `complete` verifies that state, exchanges the code for the account and token, and records the
grant. The token is Fernet-encrypted at rest (the deploy's credential key) and leaves the process
only as the real secret the proxy injects. The OAuth mechanics are an injected `OAuthProvider` and
never known to core; the account id is frozen onto the grant row at record time so later derivation
needs no live provider."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from selfhost.db import workspace_tx
from selfhost.o11y import log
from selfhost.schema import tables

CONNECT_STATE_TTL_SECONDS = 600


class UnknownProvider(KeyError):
    """No OAuth provider is installed under this name — no connectors extension declares it."""


class ConnectStateInvalid(ValueError):
    """The sealed OAuth state is tampered, expired, or unreadable — the callback refuses it."""


@dataclass(frozen=True)
class OAuthAccount:
    """What a completed handoff yields: the provider's stable account id and the access token the
    proxy swaps onto the wire for the sentinel the sandbox sees."""

    account_id: str
    token: str


class OAuthProvider(Protocol):
    """A connector's OAuth descriptor, injected by the extension that declares it. `authorize_url`
    builds the link the member opens; `exchange` turns the returned code into the account and token.
    `host` is the provider's own host the grant admits — direct-provider-host, provider-agnostic."""

    provider: str
    host: str

    def authorize_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange(self, code: str, redirect_uri: str) -> OAuthAccount: ...


@dataclass(frozen=True)
class Grant:
    """A decrypted grant as the proxy-rule derivation reads it: the host it admits, the provider and
    account that identify its sentinel, and the real token."""

    provider: str
    account_id: str
    host: str
    token: str


@dataclass(frozen=True)
class GrantSummary:
    """The audit view of a grant for `selfhost grants` — no secret, only who granted which provider
    account to which agent, and when."""

    agent: str
    provider: str
    account_id: str
    grantor_member_id: UUID
    conversation_id: UUID
    granted_at: datetime


@dataclass(frozen=True)
class GrantRecorded:
    """What `complete` returns once the handoff lands durably: the provider account now bound."""

    provider: str
    account_id: str
    agent_id: UUID


class ConnectState(BaseModel):
    """The state carried across the OAuth redirect: who is granting, to which agent, for which
    provider, in which conversation. Fernet-sealed into the `state` param so the callback trusts it
    without a server-side pending row, and TTL-bounded so a stale handoff is refused."""

    workspace_id: UUID
    agent_id: UUID
    provider: str
    grantor_member_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class GrantStore:
    """Persists and reads OAuth grants, encrypting the token at rest with the deploy's credential
    key. The raw token leaves the process only as the real secret the egress proxy injects."""

    fernet: Fernet

    async def record(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        provider: str,
        account_id: str,
        host: str,
        token: str,
        grantor_member_id: UUID,
        conversation_id: UUID,
    ) -> None:
        """Upsert on (workspace, agent, provider, account): re-connecting the same account refreshes
        its token and audit fields rather than duplicating the grant. One atomic insert-on-conflict,
        so two near-simultaneous first connects of the same account settle on one row instead of
        colliding on the unique identity — the loser updates, never raises."""
        ciphertext = self.fernet.encrypt(token.encode())
        async with workspace_tx() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.grant)
                .values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    provider=provider,
                    account_id=account_id,
                    host=host,
                    ciphertext=ciphertext,
                    grantor_member_id=grantor_member_id,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        tables.grant.c.workspace_id,
                        tables.grant.c.agent_id,
                        tables.grant.c.provider,
                        tables.grant.c.account_id,
                    ],
                    set_={
                        "host": host,
                        "ciphertext": ciphertext,
                        "grantor_member_id": grantor_member_id,
                        "conversation_id": conversation_id,
                        "updated_at": sa.func.now(),
                    },
                )
            )

    async def active_grants(self, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]:
        """One agent's grants in this workspace, decrypted into the shape the proxy-rule derivation
        reads. Agent-scoped: the per-turn resolver admits and injects only the turn's agent's own
        grants, so agent A's rule set never carries agent B's host or token. A grant whose
        ciphertext will not decrypt is logged and skipped — its host stays ungranted, so one corrupt
        row denies only itself, never the agent's other grants or the proxy loop."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.grant.c.provider,
                        tables.grant.c.account_id,
                        tables.grant.c.host,
                        tables.grant.c.ciphertext,
                    ).where(
                        tables.grant.c.workspace_id == workspace_id,
                        tables.grant.c.agent_id == agent_id,
                    )
                )
            ).all()
        grants: list[Grant] = []
        for row in rows:
            try:
                token = self.fernet.decrypt(row.ciphertext).decode()
            except InvalidToken:
                log("grant.undecryptable", provider=row.provider, account_id=row.account_id)
                continue
            grants.append(
                Grant(
                    provider=row.provider, account_id=row.account_id, host=row.host, token=token
                )
            )
        return tuple(grants)


@dataclass(frozen=True)
class ConnectFlow:
    """The `/connect` workflow, twinned across a browser redirect: `authorize` opens a provider's
    link carrying sealed state; `complete` verifies that state, exchanges the code, and records the
    grant. Deps: the providers the deploy installs (empty until a connectors extension declares
    any), the Fernet that seals the state, and the grant store."""

    providers: Mapping[str, OAuthProvider]
    fernet: Fernet
    store: GrantStore

    def authorize(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        provider: str,
        grantor_member_id: UUID,
        conversation_id: UUID,
        redirect_uri: str,
    ) -> str:
        descriptor = self._provider(provider)
        state = ConnectState(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider=provider,
            grantor_member_id=grantor_member_id,
            conversation_id=conversation_id,
        )
        sealed = self.fernet.encrypt(state.model_dump_json().encode()).decode()
        return descriptor.authorize_url(sealed, redirect_uri)

    async def complete(self, *, state: str, code: str, redirect_uri: str) -> GrantRecorded:
        claims = self._open(state)
        descriptor = self._provider(claims.provider)
        account = await descriptor.exchange(code, redirect_uri)
        await self.store.record(
            workspace_id=claims.workspace_id,
            agent_id=claims.agent_id,
            provider=descriptor.provider,
            account_id=account.account_id,
            host=descriptor.host,
            token=account.token,
            grantor_member_id=claims.grantor_member_id,
            conversation_id=claims.conversation_id,
        )
        return GrantRecorded(
            provider=descriptor.provider, account_id=account.account_id, agent_id=claims.agent_id
        )

    def _provider(self, name: str) -> OAuthProvider:
        descriptor = self.providers.get(name)
        if descriptor is None:
            raise UnknownProvider(name)
        return descriptor

    def _open(self, state: str) -> ConnectState:
        try:
            raw = self.fernet.decrypt(state.encode(), ttl=CONNECT_STATE_TTL_SECONDS)
        except InvalidToken as error:
            raise ConnectStateInvalid("connect state is tampered or expired") from error
        return ConnectState.model_validate_json(raw)


async def grant_summaries(workspace_id: UUID) -> tuple[GrantSummary, ...]:
    """The workspace's grants as audit rows, provider-ordered, joined to the granted agent's name —
    the read behind `selfhost grants`. Reads no secret, so it needs no encryption key."""
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.agent.c.name,
                    tables.grant.c.provider,
                    tables.grant.c.account_id,
                    tables.grant.c.grantor_member_id,
                    tables.grant.c.conversation_id,
                    tables.grant.c.created_at,
                )
                .select_from(
                    tables.grant.join(tables.agent, tables.grant.c.agent_id == tables.agent.c.id)
                )
                .where(tables.grant.c.workspace_id == workspace_id)
                .order_by(tables.grant.c.provider)
            )
        ).all()
    return tuple(
        GrantSummary(
            agent=row.name,
            provider=row.provider,
            account_id=row.account_id,
            grantor_member_id=row.grantor_member_id,
            conversation_id=row.conversation_id,
            granted_at=row.created_at,
        )
        for row in rows
    )
