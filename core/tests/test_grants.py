import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from selfhost.db import workspace_tx
from selfhost.grants import (
    ConnectFlow,
    ConnectStateInvalid,
    Grant,
    GrantStore,
    OAuthAccount,
    UnknownProvider,
    grant_summaries,
)
from selfhost.sandbox.proxy.rules import InjectionRule, ScopeRule, derive_grant_rules
from selfhost.sandbox.proxy.server import EgressProxy, generate_ca
from selfhost.schema import tables
from selfhost.surfaces.cli import router

GRANTED_HOST = "api.granted.test"
UNGRANTED_HOST = "api.ungranted.test"


@dataclass(frozen=True)
class StubProvider:
    """The injected OAuth descriptor for the proof — stands in for a connector extension's provider,
    never the thing asserted. `authorize_url` echoes the sealed state so the test can drive the
    callback; `exchange` yields a fixed account and token."""

    provider: str = "stub"
    host: str = GRANTED_HOST
    account_id: str = "acct-42"
    token: str = "tok-secret-abc"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://stub.test/oauth?state={state}&redirect_uri={redirect_uri}"

    async def exchange(self, code: str, redirect_uri: str) -> OAuthAccount:
        return OAuthAccount(account_id=self.account_id, token=self.token)


def _store() -> GrantStore:
    return GrantStore(fernet=Fernet(Fernet.generate_key()))


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member_agent(workspace_id: UUID, token: str | None = None) -> tuple[UUID, UUID]:
    member_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if token is not None:
            await connection.execute(
                sa.insert(tables.surface_identity).values(
                    workspace_id=workspace_id,
                    member_id=member_id,
                    surface="cli",
                    external_id=hashlib.sha256(token.encode()).hexdigest(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return member_id, agent_id


def test_derive_admits_the_granted_host_and_injects_its_token() -> None:
    grant = Grant(provider="stub", account_id="acct-42", host=GRANTED_HOST, token="tok-abc")
    rules = derive_grant_rules((grant,))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    injection = next(r for r in rules if isinstance(r, InjectionRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST})
    assert injection.host == GRANTED_HOST
    assert injection.header == "authorization"
    assert injection.sentinel == "Bearer SELFHOST_SENTINEL_GRANT_stub_acct-42"
    assert injection.real == "Bearer tok-abc"


def test_no_grants_derive_no_rules() -> None:
    assert derive_grant_rules(()) == ()


async def test_grant_round_trips_and_encrypts_the_token(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = _store()
    await store.record(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        token="tok-secret-abc",
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    grants = await store.active_grants(workspace_id)
    assert grants == (
        Grant(provider="stub", account_id="acct-42", host=GRANTED_HOST, token="tok-secret-abc"),
    )
    async with workspace_tx() as connection:
        ciphertext = (
            await connection.execute(
                sa.select(tables.grant.c.ciphertext).where(
                    tables.grant.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert b"tok-secret-abc" not in ciphertext


async def test_reconnecting_the_same_account_updates_not_duplicates(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = _store()
    for token in ("tok-one", "tok-two"):
        await store.record(
            workspace_id=workspace_id,
            agent_id=agent_id,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            token=token,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
        )
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.grant)
            )
        ).scalar_one()
    assert count == 1
    grants = await store.active_grants(workspace_id)
    assert grants[0].token == "tok-two"


async def test_connect_flow_records_a_durable_grant_with_account_id(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    fernet = Fernet(Fernet.generate_key())
    flow = ConnectFlow(
        providers={"stub": StubProvider()}, fernet=fernet, store=GrantStore(fernet=fernet)
    )
    url = flow.authorize(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        redirect_uri="http://surface/v1/connect/callback",
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    recorded = await flow.complete(
        state=state, code="the-code", redirect_uri="http://surface/v1/connect/callback"
    )
    assert (recorded.provider, recorded.account_id, recorded.agent_id) == (
        "stub",
        "acct-42",
        agent_id,
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.grant.c.account_id,
                    tables.grant.c.host,
                    tables.grant.c.grantor_member_id,
                ).where(tables.grant.c.workspace_id == workspace_id)
            )
        ).one()
    assert (row.account_id, row.host, row.grantor_member_id) == (
        "acct-42",
        GRANTED_HOST,
        member_id,
    )


async def test_tampered_connect_state_is_refused() -> None:
    fernet = Fernet(Fernet.generate_key())
    flow = ConnectFlow(
        providers={"stub": StubProvider()}, fernet=fernet, store=GrantStore(fernet=fernet)
    )
    with pytest.raises(ConnectStateInvalid):
        await flow.complete(state="not-a-sealed-token", code="x", redirect_uri="y")


def test_unknown_provider_is_rejected() -> None:
    fernet = Fernet(Fernet.generate_key())
    flow = ConnectFlow(providers={}, fernet=fernet, store=GrantStore(fernet=fernet))
    with pytest.raises(UnknownProvider):
        flow.authorize(
            workspace_id=uuid4(),
            agent_id=uuid4(),
            provider="nope",
            grantor_member_id=uuid4(),
            conversation_id=uuid4(),
            redirect_uri="http://surface/v1/connect/callback",
        )


async def test_grant_summaries_expose_the_audit_view(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    await _store().record(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        token="tok",
        grantor_member_id=member_id,
        conversation_id=conversation_id,
    )
    summaries = await grant_summaries(workspace_id)
    assert len(summaries) == 1
    summary = summaries[0]
    assert (summary.agent, summary.provider, summary.account_id) == ("assistant", "stub", "acct-42")
    assert (summary.grantor_member_id, summary.conversation_id) == (member_id, conversation_id)


async def test_proxy_admits_the_granted_host_and_blocks_the_ungranted() -> None:
    grant = Grant(provider="stub", account_id="acct-42", host=GRANTED_HOST, token="tok-abc")
    cert, key = await generate_ca()
    proxy = EgressProxy(rules=derive_grant_rules((grant,)), ca_cert=cert, ca_key=key)
    endpoint = await proxy.start(bind_host="127.0.0.1")
    try:
        assert await _connect_status(endpoint.port, UNGRANTED_HOST) == 403
        assert await _connect_status(endpoint.port, GRANTED_HOST) == 200
    finally:
        await proxy.stop()


async def test_connect_routes_land_a_grant(db: None) -> None:
    workspace_id = await _workspace()
    token = secrets.token_hex(16)
    _member_id, agent_id = await _member_agent(workspace_id, token=token)
    fernet = Fernet(Fernet.generate_key())
    app = FastAPI()
    app.state.connect_flow = ConnectFlow(
        providers={"stub": StubProvider()}, fernet=fernet, store=GrantStore(fernet=fernet)
    )
    app.include_router(router)
    headers = {"authorization": f"Bearer {token}", "x-selfhost-session": uuid4().hex}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://surface"
    ) as client:
        begun = await client.post("/v1/connect", params={"provider": "stub"}, headers=headers)
        assert begun.status_code == 200
        state = parse_qs(urlparse(begun.json()["authorize_url"]).query)["state"][0]
        done = await client.get(
            "/v1/connect/callback", params={"state": state, "code": "the-code"}
        )
    assert done.status_code == 200
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.grant.c.account_id, tables.grant.c.agent_id).where(
                    tables.grant.c.workspace_id == workspace_id
                )
            )
        ).one()
    assert (row.account_id, row.agent_id) == ("acct-42", agent_id)


async def _conversation(workspace_id: UUID, member_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _connect_status(port: int, host: str) -> int:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    await writer.drain()
    status_line = await reader.readline()
    writer.close()
    return int(status_line.split()[1])
