import asyncio
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.access.connectors import CliCredential, ForwardedResponse
from ufo.access.credentials import CredentialStore
from ufo.access.egress_resolver import PerAgentRules
from ufo.access.egress_rules import (
    REQUEST_METER_DIMENSION,
    ConnectorTransferHosts,
    ForwardRule,
    InjectionRule,
    InternetRule,
    MeterRule,
    ScopeRule,
    derive_grant_rules,
)
from ufo.access.grants import (
    CONNECT_MEMO_SECONDS,
    ConnectFlow,
    ConnectHandoff,
    ConnectionOwnedByAnotherMember,
    ConnectionPermissionDenied,
    ConnectionRecorded,
    ConnectRequestInvalid,
    ConnectStateInvalid,
    ConnectUnavailable,
    Grant,
    GrantStore,
    GrantSummary,
    OAuthAccount,
    UnknownProvider,
    grant_summaries,
    install_connect_flow,
)
from ufo.agent_scope import AgentUnbound, agent
from ufo.db import workspace_tx
from ufo.ext.loader import connection_hooks
from ufo.ext.manifest import HookContext, HookOutcome, HookSpec, Manifest
from ufo.sandbox.session import RunToken
from ufo.schema import tables
from ufo.schema.records import Agent, ConnectRequest, TerminalFrame, Turn
from ufo.sdk.callback_page import (
    CALLBACK_PAGE_MAX_BYTES,
    CLOSE_THIS_PAGE,
    CONNECT_LOGO_PATH,
    CONVERSATION_CONTINUES,
    PageLink,
    callback_page,
)
from ufo.surfaces.admission import Admission, ConnectResume
from ufo.surfaces.cli import CONNECT_LOGO_CACHE, CONNECT_LOGO_FILE, callback_router
from ufo.tools.builtins import ConnectAccountInput, connect_account_handler
from ufo.tools.context import ToolContext
from ufo.turns.audience import conversation_audience
from ufo.workspace import ws

GRANTED_HOST = "api.granted.test"
UNGRANTED_HOST = "api.ungranted.test"
HOST_A = "api.aaa.test"
HOST_B = "api.bbb.test"
REDIRECT_URI = "http://surface/v1/connect/callback"


@pytest.fixture(autouse=True)
def _reset_connect_flow() -> Iterator[None]:
    yield
    install_connect_flow(None)


@dataclass(frozen=True)
class StubProvider:
    """The injected OAuth descriptor for the proof — stands in for a connector extension's provider,
    never the thing asserted. `authorize_url` echoes the sealed state so the test can drive the
    callback; `exchange` yields a fixed connected-account id (the broker holds the token)."""

    provider: str = "stub"
    host: str = GRANTED_HOST
    account_id: str = "acct-42"
    account_label: str | None = "Work account"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://stub.test/oauth?state={state}&redirect_uri={redirect_uri}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id=self.account_id, account_label=self.account_label)


class _ForbiddenForwarder:
    async def forward(
        self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> ForwardedResponse:
        raise AssertionError("cross-agent forwarding")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _member_agent(workspace_id: UUID) -> tuple[UUID, UUID]:
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
    return member_id, agent_id


async def _record(
    store: GrantStore,
    workspace_id: UUID,
    agent_id: UUID,
    provider: str,
    account_id: str,
    host: str,
    grantor_member_id: UUID,
    conversation_id: UUID,
    shared: bool,
) -> None:
    with ws(workspace_id), agent(agent_id):
        await store.record(
            provider=provider,
            account_id=account_id,
            host=host,
            grantor_member_id=grantor_member_id,
            conversation_id=conversation_id,
            shared=shared,
        )


async def _active(store: GrantStore, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]:
    with ws(workspace_id), agent(agent_id):
        return await store.active_grants()


async def _set_shared(
    store: GrantStore,
    workspace_id: UUID,
    agent_id: UUID,
    actor_member_id: UUID,
    grant_id: UUID,
    shared: bool,
) -> bool:
    with ws(workspace_id), agent(agent_id):
        return await store.set_shared(
            grant_id,
            shared,
            actor_member_id=actor_member_id,
        )


async def _revoke(
    store: GrantStore,
    workspace_id: UUID,
    agent_id: UUID,
    actor_member_id: UUID,
    grant_id: UUID,
) -> bool:
    with ws(workspace_id), agent(agent_id):
        return await store.revoke(
            grant_id,
            actor_member_id=actor_member_id,
        )


async def _summaries(workspace_id: UUID, agent_id: UUID) -> tuple[GrantSummary, ...]:
    with ws(workspace_id), agent(agent_id):
        return await grant_summaries()


async def test_grant_store_requires_an_agent_boundary(db: None) -> None:
    with ws(await _workspace()), pytest.raises(AgentUnbound):
        await GrantStore().active_grants()


def test_derive_admits_and_meters_the_granted_host_without_injecting() -> None:
    """A grant admits and meters its host but injects nothing — the broker holds the account's token
    and runs connector tools server-side, so no secret is on the wire."""
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        owner_member_id=uuid4(),
        owner_email="owner@x.test",
        connection_shared=False,
    )
    rules = derive_grant_rules((grant,))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST})
    assert MeterRule(host=GRANTED_HOST, dimension=REQUEST_METER_DIMENSION) in rules
    assert not any(isinstance(r, InjectionRule) for r in rules)


def test_no_grants_derive_no_rules() -> None:
    assert derive_grant_rules(()) == ()


TRANSFER_HOST = "stash.broker.test"


def test_derive_admits_the_providers_transfer_hosts_with_the_grant() -> None:
    """A grant also admits and meters its broker's declared file-store hosts — where the sandbox
    fetches a tool's presigned file outputs and stages its file inputs — still injecting nothing."""
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        owner_member_id=uuid4(),
        owner_email="owner@x.test",
        connection_shared=False,
    )
    rules = derive_grant_rules((grant,), ConnectorTransferHosts({"stub": (TRANSFER_HOST,)}))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST, TRANSFER_HOST})
    assert MeterRule(host=TRANSFER_HOST, dimension=REQUEST_METER_DIMENSION) in rules
    assert not any(isinstance(r, InjectionRule) for r in rules)


def test_derive_ignores_another_providers_transfer_hosts() -> None:
    grant = Grant(
        id=uuid4(),
        connection_id=uuid4(),
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        owner_member_id=uuid4(),
        owner_email="owner@x.test",
        connection_shared=False,
    )
    rules = derive_grant_rules((grant,), ConnectorTransferHosts({"other": (TRANSFER_HOST,)}))
    scope = next(r for r in rules if isinstance(r, ScopeRule))
    assert scope.allowed_hosts == frozenset({GRANTED_HOST})


async def test_resolver_folds_the_transfer_hosts_into_the_turns_rules(db: None) -> None:
    """The per-turn resolver carries the manifests' provider→transfer-hosts map, so a granted
    provider's broker file store is reachable for exactly the turns its grant covers."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_id = await _turn(workspace_id, agent_id, conversation_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    resolver = PerAgentRules(
        base=(), grants=store, transfer_hosts=ConnectorTransferHosts({"stub": (TRANSFER_HOST,)})
    )
    rules = await resolver.resolve(RunToken(workspace_id, turn_id))
    assert any(isinstance(r, ScopeRule) and TRANSFER_HOST in r.allowed_hosts for r in rules)
    assert not any(isinstance(r, InjectionRule) for r in rules)


async def test_grant_round_trips_carrying_only_the_account_id(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    grants = await _active(store, workspace_id, agent_id)
    assert grants == (
        Grant(
            id=grants[0].id,
            connection_id=grants[0].connection_id,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            owner_member_id=member_id,
            owner_email=f"{member_id.hex[:8]}@x.test",
            connection_shared=False,
        ),
    )


async def test_record_rejects_a_control_char_account_id(db: None) -> None:
    """The account_id is external (the provider's OAuth exchange) and a connector tool sends it to
    the broker, so a CR/LF that could forge a broker request is refused at record."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    with pytest.raises(ValueError, match="control character"):
        await _record(
            GrantStore(),
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42\r\nX-Injected: 1",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )


async def test_reconnecting_the_same_account_updates_not_duplicates(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for host in (HOST_A, HOST_B):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42",
            host=host,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    async with workspace_tx() as connection:
        connection_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connection))
        ).scalar_one()
        grant_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connector_grant))
        ).scalar_one()
    assert (connection_count, grant_count) == (1, 1)
    grants = await _active(store, workspace_id, agent_id)
    assert grants[0].host == HOST_B


async def test_reconnecting_an_owned_account_cannot_reassign_it(db: None) -> None:
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    other_id = uuid4()
    conversation_id = await _conversation(workspace_id, owner_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_id,
                workspace_id=workspace_id,
                email="other@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    with pytest.raises(ConnectionOwnedByAnotherMember):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42",
            host=HOST_B,
            grantor_member_id=other_id,
            conversation_id=conversation_id,
            shared=True,
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.connection.c.owner_member_id,
                    tables.connection.c.host,
                    tables.connection.c.shared,
                ).select_from(
                    tables.connection.join(
                        tables.connector_grant,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                )
            )
        ).one()
    assert (row.owner_member_id, row.host, row.shared) == (owner_id, HOST_A, False)


async def test_reconnect_never_narrows_a_shared_connection(db: None) -> None:
    """The owner reconnects a workspace-shared account for another agent with shared=False —
    the connect tool's default — and the connection stays shared: reconnect widens only."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=True,
    )
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    async with workspace_tx() as connection:
        row = (await connection.execute(sa.select(tables.connection.c.shared))).one()
    assert bool(row.shared) is True


async def test_connect_flow_records_a_durable_grant_with_account_label(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    url = flow.authorize(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    recorded = await flow.complete(state=state, code="the-code")
    assert (recorded.provider, recorded.account_id, recorded.agent_id) == (
        "stub",
        "acct-42",
        agent_id,
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.connection.c.account_id,
                    tables.connection.c.account_label,
                    tables.connection.c.host,
                    tables.connection.c.owner_member_id,
                ).where(tables.connection.c.workspace_id == workspace_id)
            )
        ).one()
    assert (row.account_id, row.account_label, row.host, row.owner_member_id) == (
        "acct-42",
        "Work account",
        GRANTED_HOST,
        member_id,
    )


async def test_a_landed_connection_reaches_every_extension_that_derives_from_it(db: None) -> None:
    """The control-plane seam: completing the handoff publishes the committed connection to each
    declared `connection_recorded` hook, so an extension creates what the connection implies inside
    the callback. A handler that raises is swallowed — the connection is recorded and the next
    handler still runs — because the extension's own job retries what it did not finish."""
    published: list[ConnectionRecorded] = []

    async def _explode(ctx: HookContext) -> HookOutcome:
        raise RuntimeError("connection consumer exploded")

    async def _publish(ctx: HookContext) -> HookOutcome:
        assert isinstance(ctx.payload, ConnectionRecorded)
        published.append(ctx.payload)
        return None

    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    manifests = (
        Manifest(
            name="boom_ext",
            version="0",
            hooks=(HookSpec(event="connection_recorded", handler=_explode),),
        ),
        Manifest(
            name="probe_ext",
            version="0",
            hooks=(HookSpec(event="connection_recorded", handler=_publish),),
        ),
    )
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
        connections=connection_hooks(
            manifests, CredentialStore(fernet=Fernet(Fernet.generate_key()))
        ),
    )
    state = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_id,
                provider="stub",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
        ).query
    )["state"][0]
    recorded = await flow.complete(state=state, code="the-code")
    assert recorded.account_id == "acct-42"
    [landed] = published
    assert (landed.provider, landed.account_id, landed.owner_member_id, landed.agent_id) == (
        "stub",
        "acct-42",
        member_id,
        agent_id,
    )
    with ws(workspace_id):
        async with workspace_tx() as connection:
            assert (
                await connection.execute(
                    sa.select(tables.connection.c.id).where(
                        tables.connection.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one() == landed.connection_id


async def test_connect_flow_records_null_account_label(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider(account_label=None)},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    state = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_id,
                provider="stub",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
        ).query
    )["state"][0]
    await flow.complete(state=state, code="the-code")
    async with workspace_tx() as connection:
        label = (
            await connection.execute(
                sa.select(tables.connection.c.account_label).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert label is None


async def test_reconnect_refreshes_account_label(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)

    async def complete(label: str) -> None:
        flow = ConnectFlow(
            providers={"stub": StubProvider(account_label=label)},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
        state = parse_qs(
            urlparse(
                flow.authorize(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    provider="stub",
                    grantor_member_id=member_id,
                    conversation_id=conversation_id,
                    shared=False,
                )
            ).query
        )["state"][0]
        await flow.complete(state=state, code="the-code")

    await complete("Work account")
    await complete("Personal account")
    async with workspace_tx() as connection:
        label = (
            await connection.execute(
                sa.select(tables.connection.c.account_label).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert label == "Personal account"


async def test_tampered_connect_state_is_refused() -> None:
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    with pytest.raises(ConnectStateInvalid):
        await flow.complete(state="not-a-sealed-token", code="x")


def test_unknown_provider_is_rejected() -> None:
    flow = ConnectFlow(
        providers={},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    with pytest.raises(UnknownProvider) as caught:
        flow.authorize(
            workspace_id=uuid4(),
            agent_id=uuid4(),
            provider="nope",
            grantor_member_id=uuid4(),
            conversation_id=uuid4(),
            shared=False,
        )
    assert str(caught.value) == "No connector is available for 'nope'."


async def test_validating_an_unclaimed_provider_states_the_refusal() -> None:
    flow = ConnectFlow(
        providers={},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    with pytest.raises(UnknownProvider) as caught:
        await flow.validate_provider("linear")
    assert str(caught.value) == "No connector is available for 'linear'."


async def test_grant_summaries_expose_the_audit_view(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    await _record(
        GrantStore(),
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    summaries = await _summaries(workspace_id, agent_id)
    assert len(summaries) == 1
    summary = summaries[0]
    assert (summary.agent, summary.provider, summary.account_id) == ("assistant", "stub", "acct-42")
    assert (summary.owner_member_id, summary.conversation_id) == (member_id, conversation_id)


async def test_agent_a_authenticates_only_to_its_own_granted_host(db: None) -> None:
    """Per-agent authentication isolation: agent A's resolved rules carry an exact rule for A's
    provider only — B's host is neither scoped nor forwardable in A's set, so A cannot inject or
    forward through B's account. A's internet rule may reach B's host opaquely, but with no
    credential."""
    workspace_id = await _workspace()
    member_id, agent_a = await _member_agent(workspace_id)
    agent_b = await _agent(workspace_id, "assistant-b")
    conversation_id = await _conversation(workspace_id, member_id)
    turn_a = await _turn(workspace_id, agent_a, conversation_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_a,
        provider="stub",
        account_id="acct-a",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    await _record(
        store,
        workspace_id,
        agent_b,
        provider="stub",
        account_id="acct-b",
        host=HOST_B,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    resolver = PerAgentRules(
        base=(),
        grants=store,
        internet=(InternetRule(),),
        clis={
            "stub": CliCredential(
                env="STUB_TOKEN", header="authorization", forward=_ForbiddenForwarder()
            )
        },
    )
    rules_a = await resolver.resolve(RunToken(workspace_id, turn_a))
    assert any(isinstance(r, ScopeRule) and HOST_A in r.allowed_hosts for r in rules_a)
    assert not any(isinstance(r, ScopeRule) and HOST_B in r.allowed_hosts for r in rules_a)
    assert not any(isinstance(r, ForwardRule) and r.host == HOST_B for r in rules_a)
    assert InternetRule() in rules_a


async def test_a_grant_recorded_after_start_is_live_for_the_next_turn(db: None) -> None:
    """The resolver derives each call, so a grant recorded after it was built is live on the next
    turn's resolve — nothing pins a snapshot at boot."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    turn_1 = await _turn(workspace_id, agent_id, conversation_id, seq=1)
    turn_2 = await _turn(workspace_id, agent_id, conversation_id, seq=2)
    store = GrantStore()
    resolver = PerAgentRules(base=(), grants=store)

    rules_1 = await resolver.resolve(RunToken(workspace_id, turn_1))
    assert not any(isinstance(r, ScopeRule) and HOST_A in r.allowed_hosts for r in rules_1)
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-a",
        host=HOST_A,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    rules_2 = await resolver.resolve(RunToken(workspace_id, turn_2))
    assert any(isinstance(r, ScopeRule) and HOST_A in r.allowed_hosts for r in rules_2)


def _turn_context(
    workspace_id: UUID, agent_id: UUID, conversation_id: UUID, member_id: UUID | None
) -> ToolContext:
    """A tool context whose only live fields the connect tool reads are the turn (workspace, agent,
    conversation) and the speaking member; the sandbox and other capabilities the tool never touches
    stay unset."""
    turn = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="running",
        inbound="connect my gmail",
        created_at=datetime.now(UTC),
    )
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
    )


@dataclass(frozen=True)
class _ResumeCall:
    conversation_id: UUID
    message: str
    speaker_member_id: UUID
    idempotency_key: str


@dataclass
class _RecordingResumption:
    """Stands in for the admitter the callback resumes through — the seam, not the queue. What is
    asserted is which conversation the flow tells, as whom, and under which key."""

    calls: list[_ResumeCall] = field(default_factory=list)

    async def resume(
        self,
        conversation_id: UUID,
        message: str,
        *,
        speaker_member_id: UUID,
        idempotency_key: str,
    ) -> bool:
        self.calls.append(
            _ResumeCall(
                conversation_id=conversation_id,
                message=message,
                speaker_member_id=speaker_member_id,
                idempotency_key=idempotency_key,
            )
        )
        return True


@dataclass
class _QueueDbos:
    """Stands in for the DBOS client at the admission seam alone — the rows admission writes are
    real, and they are what this asserts."""

    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: dict[str, str], workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def test_a_completed_connect_lands_a_real_turn_on_the_conversations_queue(db: None) -> None:
    """The seam proven against the queue itself rather than a recorder: the callback runs the real
    `ConnectResume` over the real `Admission`, and what is read back is the durable turn in the
    conversation the member left to authorize, carrying the words the agent will act on.

    A recorder can only show that the flow called something. It cannot show that admission accepted
    the message, that the turn landed in the right conversation, or that it reached the queue — and
    those are the failure the member sees."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    asking_turn = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=asking_turn,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect stub",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=TerminalFrame(
                    status="done",
                    connect_request=ConnectRequest(provider="stub", requester_member_id=member_id),
                ).model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = _QueueDbos()
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
        resumption=ConnectResume(
            Admission(dbos=dbos, durable_surfaces=frozenset())  # type: ignore[arg-type]
        ),
    )
    state = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_id,
                provider="stub",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
                turn_id=asking_turn,
            )
        ).query
    )["state"][0]

    recorded = await flow.complete(state=state, code="the-code")

    # The conversation carries on: the landing grant tells it, and the stamp the surfaces draw a
    # settled control from rides the same write the grant did, so neither can cost the other.
    assert recorded.resumed is True
    with ws(workspace_id):
        async with workspace_tx() as connection:
            stamped = (
                await connection.execute(
                    sa.select(tables.turn.c.connect_landed_at).where(
                        tables.turn.c.id == asking_turn
                    )
                )
            ).scalar_one()
    assert stamped is not None
    with ws(workspace_id):
        async with workspace_tx() as connection:
            turns = (
                (
                    await connection.execute(
                        sa.select(tables.turn.c.id, tables.turn.c.inbound).where(
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.id != asking_turn,
                        )
                    )
                )
                .mappings()
                .all()
            )
    assert len(turns) == 1
    # The member-facing name, not the slug and not the broker's id.
    assert turns[0]["inbound"] == "Connected Stub: Work account."
    assert dbos.enqueued == [str(turns[0]["id"])]

    # The member refreshes the callback. The same state carries the same key, so they rejoin the
    # turn already running rather than founding a second one on the same account.
    await flow.complete(state=state, code="the-code")

    with ws(workspace_id):
        async with workspace_tx() as connection:
            after = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.turn)
                    .where(
                        tables.turn.c.conversation_id == conversation_id,
                        tables.turn.c.id != asking_turn,
                    )
                )
            ).scalar_one()
    assert after == 1


def test_the_callback_page_is_small_and_fetches_only_its_own_mark() -> None:
    """A member waits on this page in a browser they opened for one moment, often on a phone off a
    Slack thread, and no session or cache stands behind it. So the document carries its own styling
    and asks for exactly one thing more: the mark, from this same origin on the connection already
    open. A stylesheet or a font added later would each cost another round trip on that connection,
    and the cap plus these assertions are what keep them out.

    Inlining the mark instead would put 19 KB on a 700-byte page, and nothing in front of this
    deploy compresses a response — so it is served once and cached for good."""
    page = callback_page(headline="GitHub connected.", detail=CLOSE_THIS_PAGE).body.decode()

    assert len(page.encode()) < CALLBACK_PAGE_MAX_BYTES
    # Same origin, so no host to resolve and no second connection to open.
    assert "://" not in page
    assert page.count("src=") == 1 and CONNECT_LOGO_PATH in page
    # It answers in the reader's own theme without a media query, which is the cheapest way to.
    assert "color-scheme:light dark" in page
    # A page that still asks the member for something must stay put, so it carries no script.
    assert "<script" not in page


def test_a_return_leg_with_nothing_left_to_say_closes_its_own_tab() -> None:
    """The member is finished here and the agent has the work, so the tab goes away on its own
    rather than being left for them to deal with. The close runs from the document itself, and the
    detail line stays the instruction for a browser that refuses to close a tab it did not open."""
    finished = callback_page(
        headline="GitHub is connected.", detail=CLOSE_THIS_PAGE, close=True
    ).body.decode()

    assert "window.close()" in finished and CLOSE_THIS_PAGE in finished
    # Still one document, one fetch: the close costs no round trip.
    assert "://" not in finished
    assert finished.count("src=") == 1
    assert len(finished.encode()) < CALLBACK_PAGE_MAX_BYTES


def test_a_finished_return_leg_links_back_to_the_conversation() -> None:
    """The close is refused for any tab a script did not open, which is most of them: the member
    reached this page from a chat link through the provider. A link needs no permission, so it is
    the half that always works — and it replaces the detail line, because it says the same thing
    and acts on it. It is styled in `currentColor`, so it needs no colour of its own to hold in
    either theme, and the rule ships only on a page that has a link."""
    back = "https://slack.com/app_redirect?app=A1&team=T1"
    linked = callback_page(
        headline="ufo is installed.", link=PageLink(label="Continue in Slack", url=back), close=True
    ).body.decode()
    plain = callback_page(headline="GitHub connected.", detail=CLOSE_THIS_PAGE).body.decode()

    assert f'<a href="{back}">Continue in Slack</a>' in unescape(linked)
    assert "<p>" not in linked
    assert "color:inherit" in linked and "a{" not in plain
    assert len(linked.encode()) < CALLBACK_PAGE_MAX_BYTES


def test_the_connect_mark_is_the_portals_own_file_byte_for_byte() -> None:
    """A logo is drawn artwork, so core copies it rather than rewriting it. Hand-minifying this one
    drew it wrongly: the counters in the letters are cut by the `fill-rule: evenodd` that its
    `<defs>` stylesheet carries, and rounding the coordinates moved the shapes against each other.
    Nothing about a rendered glyph fails a size assertion, so this compares the bytes instead — and
    it is also what keeps the two copies from drifting apart as the brand changes."""
    portal = (
        Path(__file__).resolve().parents[3]
        / "extensions"
        / "web"
        / "frontend"
        / "src"
        / "assets"
        / "ufo-logo.svg"
    )

    assert portal.is_file(), "the portal's logo moved — core's copy is now unanchored"
    assert CONNECT_LOGO_FILE.read_bytes() == portal.read_bytes()


def test_the_connect_mark_is_served_immutably_from_core() -> None:
    """The page is reached with no session and no frontend build behind it, so the mark cannot be
    the portal's copy: that one is fingerprinted by its bundler and its name changes every build.
    Core serves its own, and says it never changes, so a member who connects a second account pays
    for it once."""
    assert CONNECT_LOGO_FILE.is_file()
    assert b"<svg" in CONNECT_LOGO_FILE.read_bytes()[:200]
    assert "immutable" in CONNECT_LOGO_CACHE


async def test_a_connect_from_a_portal_panel_leaves_the_intent_lane_alone(db: None) -> None:
    """A connect begun from a portal panel seals that panel's own conversation, and that lane
    dispatches one typed verb per turn with no model round. A free-text message admitted there
    would run a whole turn nothing reads — the surface declares no writeback and the chat index
    skips the lane — while holding the lane's single partition until it ended, so the member's next
    panel submit would wait behind it and time out.

    The grant still lands. Only the resume is declined, and the page then states what landed and
    that the member may close it — there is no conversation here to send them back to."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound="{}",
                admission_source="intent",
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = _QueueDbos()
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
        resumption=ConnectResume(
            Admission(dbos=dbos, durable_surfaces=frozenset())  # type: ignore[arg-type]
        ),
    )
    state = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_id,
                provider="stub",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
        ).query
    )["state"][0]

    recorded = await flow.complete(state=state, code="the-code")

    assert recorded.resumed is False
    assert dbos.enqueued == []
    with ws(workspace_id):
        async with workspace_tx() as connection:
            turns = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.turn)
                    .where(tables.turn.c.conversation_id == conversation_id)
                )
            ).scalar_one()
            granted = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.connection))
            ).scalar_one()
    # The seeded intent turn and nothing else, and the grant landed all the same.
    assert turns == 1
    assert granted == 1

    install_connect_flow(flow)
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        page = (
            await client.get("/v1/connect/callback", params={"state": state, "code": "the-code"})
        ).text
    # Nothing waits on this connect, so the page names no conversation and no agent — it states the
    # outcome and the one generic thing left to do, and takes the window away itself.
    assert CLOSE_THIS_PAGE in page and CONVERSATION_CONTINUES not in page
    assert "conversation" not in page and "agent" not in page
    assert "window.close()" in page


async def test_connect_account_handoff_is_private_memoized_and_binds_the_speaker(
    db: None,
) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    resumed = _RecordingResumption()
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
        resumption=resumed,
    )
    install_connect_flow(flow)
    ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id)
    result = await connect_account_handler(
        ctx, ConnectAccountInput(provider="stub", user_description="connecting their account")
    )
    assert result.is_error is False
    tool_text = result.content[0].text
    assert "https://" not in tool_text
    request = ConnectRequest.model_validate_json(tool_text.splitlines()[1])
    terminal = TerminalFrame(status="done", connect_request=request)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=ctx.turn.id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect my gmail",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with pytest.raises(ConnectRequestInvalid, match="another member"):
        await ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, uuid4())
    urls = await asyncio.gather(
        ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, member_id),
        ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, member_id),
    )
    assert urls[0] == urls[1]
    url = urls[0]
    assert url.startswith("https://stub.test/oauth")
    assert await ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, member_id) == url
    state = parse_qs(urlparse(url).query)["state"][0]
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        for _ in range(2):
            done = await client.get(
                "/v1/connect/callback", params={"state": state, "code": "the-code"}
            )
            assert done.status_code == 200
            # No session stands behind this page — a Slack member finishes consent in a browser
            # that has never signed in — so it addresses them by what just happened and names the
            # one thing left to do. The turn already resumed, so this is the one branch that may
            # name the conversation: the page closes the window the portal opened for it, and the
            # line stays for the tab no script owns.
            assert CONVERSATION_CONTINUES in done.text
            assert "window.close()" in done.text

    # The conversation that asked for the account is told, as the granting member, so the turn
    # waiting there carries on without them asking it to. Both callbacks carry the one key the
    # connection settled on, so a refreshed browser rejoins that message rather than sending a
    # second one.
    assert [(call.conversation_id, call.speaker_member_id) for call in resumed.calls] == [
        (conversation_id, member_id),
        (conversation_id, member_id),
    ]
    assert len({call.idempotency_key for call in resumed.calls}) == 1
    assert resumed.calls[0].message == "Connected Stub: Work account."

    # A later connect of the same account is a second act and must reach the conversation too — a
    # re-consent after the provider revoked the token, or a connect for another agent. The handoff
    # memoizes one state per asking turn, so a second act is a second authorization.
    # `GrantStore.record` settles it onto the connection row the first one wrote, so a key built
    # from the connection would repeat here — and admission, which matches on (workspace, key),
    # would drop this message while the page told the member the agent was carrying on.
    later = parse_qs(
        urlparse(
            flow.authorize(
                workspace_id=workspace_id,
                agent_id=agent_id,
                provider="stub",
                grantor_member_id=member_id,
                conversation_id=conversation_id,
                shared=False,
            )
        ).query
    )["state"][0]
    await flow.complete(state=later, code="the-code")

    assert len(resumed.calls) == 3
    assert resumed.calls[2].idempotency_key != resumed.calls[0].idempotency_key
    assert resumed.calls[2].conversation_id == conversation_id
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.connection.c.account_id,
                    tables.connector_grant.c.agent_id,
                    tables.connection.c.owner_member_id,
                    tables.connector_grant.c.conversation_id,
                )
                .select_from(
                    tables.connector_grant.join(
                        tables.connection,
                        tables.connector_grant.c.connection_id == tables.connection.c.id,
                    )
                )
                .where(tables.connector_grant.c.workspace_id == workspace_id)
            )
        ).all()
    assert len(rows) == 1
    assert (rows[0].account_id, rows[0].agent_id) == ("acct-42", agent_id)
    assert (rows[0].owner_member_id, rows[0].conversation_id) == (member_id, conversation_id)


async def test_connect_handoff_refuses_a_request_whose_agent_is_gone(db: None) -> None:
    """A request outliving its own signing window may outlive the agent it was to grant. Sealing a
    state for one the workspace no longer holds moves the failure into the callback, where the
    member waits on a page that can only apologise, so the press is refused where they are still
    looking at the control."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=TerminalFrame(
                    status="done",
                    connect_request=ConnectRequest(
                        provider="stub",
                        requester_member_id=member_id,
                        grantee_agent_id=uuid4(),
                    ),
                ).model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with pytest.raises(ConnectRequestInvalid, match="agent that is gone"):
        await ConnectHandoff(flow).authorize(workspace_id, turn_id, member_id)


async def test_connect_handoff_mints_for_a_request_older_than_one_signing_window(db: None) -> None:
    """A request outlives the window any one state is signed for — a member reads the reply, leaves,
    and presses the control later — so an old request mints rather than refusing the only act its
    reply names."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    turn_id = uuid4()
    terminal = TerminalFrame(
        status="done",
        connect_request=ConnectRequest(provider="stub", requester_member_id=member_id),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=datetime.now(UTC) - timedelta(minutes=11),
            )
        )
    minted = await ConnectHandoff(flow).authorize(workspace_id, turn_id, member_id)
    assert minted.startswith("https://stub.test/oauth?state=")
    async with workspace_tx() as connection:
        stamped = (
            await connection.execute(
                sa.select(
                    tables.turn.c.connect_authorization_url,
                    tables.turn.c.connect_authorized_at,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one()
    assert stamped.connect_authorization_url == minted
    assert stamped.connect_authorized_at is not None


async def test_connect_handoff_replays_one_authorization_then_remints_past_its_window(
    db: None,
) -> None:
    """Every read of one request names one control while the state it carries is live, so a reload
    and the stream draw the same URL. Once that state has aged past the window the callback would
    refuse it, the next read mints a fresh one in its place — the control the member presses is
    always a live consent page."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    turn_id = uuid4()
    terminal = TerminalFrame(
        status="done",
        connect_request=ConnectRequest(provider="stub", requester_member_id=member_id),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="connect",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    handoff = ConnectHandoff(flow)
    url = await handoff.authorize(workspace_id, turn_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=11))
            .where(tables.turn.c.id == turn_id)
        )
    assert await handoff.authorize(workspace_id, turn_id, member_id) == url
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(connect_authorized_at=datetime.now(UTC) - timedelta(minutes=11))
            .where(tables.turn.c.id == turn_id)
        )
    reminted = await handoff.authorize(workspace_id, turn_id, member_id)
    assert reminted.startswith("https://stub.test/oauth?state=")
    assert reminted != url
    assert await handoff.authorize(workspace_id, turn_id, member_id) == reminted
    # One handed back late in its own signing window would send the member to a consent page the
    # callback then refuses, so the memo is spent well before the state it holds is.
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(
                connect_authorized_at=datetime.now(UTC)
                - timedelta(seconds=CONNECT_MEMO_SECONDS + 1)
            )
            .where(tables.turn.c.id == turn_id)
        )
    fresher = await handoff.authorize(workspace_id, turn_id, member_id)
    assert fresher not in (url, reminted)
    # Age it by the store's own clock, which is what stamped it: a stamp read back in whatever
    # precision that store keeps names no row, so the write that replaces an aged authorization
    # names the URL it read instead.
    async with workspace_tx() as connection:
        clock = (
            "datetime('now','-11 minutes')"
            if connection.dialect.name == "sqlite"
            else "now() - interval '11 minutes'"
        )
        await connection.execute(
            sa.update(tables.turn)
            .values(connect_authorized_at=sa.text(clock))
            .where(tables.turn.c.id == turn_id)
        )
    again = await handoff.authorize(workspace_id, turn_id, member_id)
    assert again.startswith("https://stub.test/oauth?state=")
    assert again not in (url, reminted)


async def test_connect_account_without_a_speaker_is_refused() -> None:
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), None)
    with pytest.raises(ValueError, match="speaking member"):
        await connect_account_handler(
            ctx, ConnectAccountInput(provider="stub", user_description="connecting their account")
        )


async def test_connect_account_without_an_installed_flow_raises() -> None:
    install_connect_flow(None)
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), uuid4())
    with pytest.raises(ConnectUnavailable):
        await connect_account_handler(
            ctx, ConnectAccountInput(provider="stub", user_description="connecting their account")
        )


async def test_the_begin_route_is_gone_and_the_callback_reports_unavailable_without_a_flow() -> (
    None
):
    install_connect_flow(None)
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        begun = await client.post("/v1/connect", params={"provider": "stub"})
        callback = await client.get("/v1/connect/callback", params={"state": "s", "code": "c"})
    assert begun.status_code == 404
    assert callback.status_code == 503


async def test_connect_flow_records_the_models_shared_decision(db: None) -> None:
    """The model decides disclosure at connect time: shared=True lands a workspace-shared grant,
    the default lands one private to its grantor."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=store,
        redirect_uri=REDIRECT_URI,
    )
    url = flow.authorize(
        workspace_id=workspace_id,
        agent_id=agent_id,
        provider="stub",
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=True,
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    await flow.complete(state=state, code="c")
    (grant,) = await _active(store, workspace_id, agent_id)
    assert grant.connection_shared is True
    assert grant.owner_member_id == member_id


async def test_a_grant_defaults_private_and_reconnect_updates_shared(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for shared in (False, True):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=shared,
        )
    (grant,) = await _active(store, workspace_id, agent_id)
    assert grant.connection_shared is True
    (summary,) = await _summaries(workspace_id, agent_id)
    assert summary.shared is True


async def test_set_shared_flips_the_connection_for_every_attached_agent(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    second_agent = await _agent(workspace_id, "reviewer")
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for target in (agent_id, second_agent):
        await _record(
            store,
            workspace_id,
            target,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    (initial,) = await _active(store, workspace_id, agent_id)
    assert await _set_shared(store, workspace_id, agent_id, member_id, initial.id, True) is True
    (flipped,) = await _active(store, workspace_id, agent_id)
    assert flipped.connection_shared is True
    (untouched,) = await _active(store, workspace_id, second_agent)
    assert untouched.connection_shared is True
    assert await _set_shared(store, workspace_id, agent_id, member_id, uuid4(), True) is False


async def test_attach_honors_ownership_and_sharing_and_never_widens(db: None) -> None:
    """The owner attaches their private connection to a second agent; another member is refused
    until the connection is shared, and an admin holds no escape past that refusal. A `shared`
    claim that would widen the connection refuses instead of silently dropping."""
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    second_agent = await _agent(workspace_id, "reviewer")
    other_id, admin_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member),
            (
                {
                    "id": other_id,
                    "workspace_id": workspace_id,
                    "email": "other@x.test",
                    "is_admin": False,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": admin_id,
                    "workspace_id": workspace_id,
                    "email": "admin@x.test",
                    "is_admin": True,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
    conversation_id = await _conversation(workspace_id, owner_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    with ws(workspace_id), agent(second_agent):
        for actor in (other_id, admin_id):
            with pytest.raises(ConnectionPermissionDenied):
                await store.attach(
                    provider="stub",
                    account_id="acct-42",
                    conversation_id=conversation_id,
                    actor_member_id=actor,
                    shared=False,
                )
        with pytest.raises(ValueError, match="attach cannot share"):
            await store.attach(
                provider="stub",
                account_id="acct-42",
                conversation_id=conversation_id,
                actor_member_id=owner_id,
                shared=True,
            )
        assert (
            await store.attach(
                provider="stub",
                account_id="acct-42",
                conversation_id=conversation_id,
                actor_member_id=owner_id,
                shared=False,
            )
            is True
        )
        assert (
            await store.attach(
                provider="stub",
                account_id="missing",
                conversation_id=conversation_id,
                actor_member_id=owner_id,
                shared=False,
            )
            is False
        )
    (attached,) = await _active(store, workspace_id, second_agent)
    assert attached.account_id == "acct-42"
    assert attached.connection_shared is False
    (initial,) = await _active(store, workspace_id, agent_id)
    assert await _set_shared(store, workspace_id, agent_id, owner_id, initial.id, True) is True
    with ws(workspace_id), agent(second_agent):
        assert (
            await store.attach(
                provider="stub",
                account_id="acct-42",
                conversation_id=conversation_id,
                actor_member_id=other_id,
                shared=True,
            )
            is True
        )


async def test_revoke_removes_only_the_named_agents_binding(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    second_agent = await _agent(workspace_id, "reviewer")
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for target in (agent_id, second_agent):
        await _record(
            store,
            workspace_id,
            target,
            provider="stub",
            account_id="acct-42",
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
        )
    (initial,) = await _active(store, workspace_id, agent_id)
    assert await _revoke(store, workspace_id, agent_id, member_id, initial.id) is True
    assert await _active(store, workspace_id, agent_id) == ()
    (kept,) = await _active(store, workspace_id, second_agent)
    assert kept.account_id == "acct-42"
    assert await _revoke(store, workspace_id, agent_id, member_id, initial.id) is False


async def test_grant_mutations_bump_the_egress_rules_generation(db: None) -> None:
    """The proxy's rule cache pins this counter, so every act that changes what the wire may do
    reaches the next CONNECT instead of waiting out the cache TTL."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    second_agent = await _agent(workspace_id, "reviewer")
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()

    async def generation() -> int:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.workspace.c.egress_rules_generation).where(
                        tables.workspace.c.id == workspace_id
                    )
                )
            ).scalar_one()

    start = await generation()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=True,
    )
    recorded = await generation()
    with ws(workspace_id), agent(second_agent):
        assert await store.attach(
            provider="stub",
            account_id="acct-42",
            conversation_id=conversation_id,
            actor_member_id=member_id,
            shared=False,
        )
    attached = await generation()
    (grant,) = await _active(store, workspace_id, agent_id)
    assert await _set_shared(store, workspace_id, agent_id, member_id, grant.id, False)
    unshared = await generation()
    assert await _revoke(store, workspace_id, agent_id, member_id, grant.id)
    revoked = await generation()
    with ws(workspace_id), agent(second_agent):
        assert await store.disconnect(grant.connection_id, actor_member_id=member_id)
    disconnected = await generation()

    assert start == 0
    assert start < recorded < attached < unshared < revoked < disconnected


async def test_same_owner_replacements_refuse_stale_generations(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    stale_connection = (await _active(store, workspace_id, agent_id))[0]
    with ws(workspace_id), agent(agent_id):
        assert (
            await store.disconnect(
                stale_connection.connection_id,
                actor_member_id=member_id,
            )
            is True
        )
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    reconnected = (await _active(store, workspace_id, agent_id))[0]
    assert reconnected.connection_id != stale_connection.connection_id
    assert reconnected.id != stale_connection.id
    with ws(workspace_id), agent(agent_id):
        assert (
            await store.disconnect(
                stale_connection.connection_id,
                actor_member_id=member_id,
            )
            is False
        )
        assert await store.revoke(reconnected.id, actor_member_id=member_id) is True
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=True,
    )
    regranted = (await _active(store, workspace_id, agent_id))[0]
    assert regranted.connection_id == reconnected.connection_id
    assert regranted.id != reconnected.id
    with ws(workspace_id), agent(agent_id):
        assert await store.set_shared(reconnected.id, False, actor_member_id=member_id) is False
        assert await store.revoke(reconnected.id, actor_member_id=member_id) is False
    assert (await _active(store, workspace_id, agent_id)) == (regranted,)


async def test_admin_may_narrow_and_revoke_but_not_widen(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    admin_id = uuid4()
    conversation_id = await _conversation(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=admin_id,
                workspace_id=workspace_id,
                email="admin@x.test",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=member_id,
        conversation_id=conversation_id,
        shared=False,
    )
    private = (await _active(store, workspace_id, agent_id))[0]
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(ConnectionPermissionDenied):
            await store.set_shared(private.id, True, actor_member_id=admin_id)
        assert await store.set_shared(private.id, False, actor_member_id=admin_id) is True
        assert await store.set_shared(private.id, True, actor_member_id=member_id) is True
        assert await store.revoke(private.id, actor_member_id=admin_id) is True
    assert await _active(store, workspace_id, agent_id) == ()


async def test_stale_owner_mutation_cannot_touch_a_reconnected_account(db: None) -> None:
    workspace_id = await _workspace()
    alice, agent_id = await _member_agent(workspace_id)
    bob, admin = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member),
            (
                {
                    "id": bob,
                    "workspace_id": workspace_id,
                    "email": "bob@x.test",
                    "is_admin": False,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": admin,
                    "workspace_id": workspace_id,
                    "email": "admin@x.test",
                    "is_admin": True,
                    "created_at": now,
                    "updated_at": now,
                },
            ),
        )
    alice_conversation = await _conversation(workspace_id, alice)
    bob_conversation = await _conversation(workspace_id, bob)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=alice,
        conversation_id=alice_conversation,
        shared=False,
    )
    stale = (await _active(store, workspace_id, agent_id))[0]
    assert (await _summaries(workspace_id, agent_id))[0].owner_member_id == alice
    with ws(workspace_id), agent(agent_id):
        assert await store.disconnect(stale.connection_id, actor_member_id=admin) is True
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-42",
        host=GRANTED_HOST,
        grantor_member_id=bob,
        conversation_id=bob_conversation,
        shared=False,
    )
    current = (await _active(store, workspace_id, agent_id))[0]
    with ws(workspace_id), agent(agent_id):
        assert await store.set_shared(stale.id, True, actor_member_id=alice) is False
        assert await store.revoke(stale.id, actor_member_id=alice) is False
        assert await store.disconnect(stale.connection_id, actor_member_id=alice) is False
        with pytest.raises(ConnectionPermissionDenied):
            await store.set_shared(
                current.id,
                True,
                actor_member_id=alice,
            )
        with pytest.raises(ConnectionPermissionDenied):
            await store.revoke(current.id, actor_member_id=alice)
        with pytest.raises(ConnectionPermissionDenied):
            await store.disconnect(current.connection_id, actor_member_id=alice)
    assert (current.owner_member_id, current.connection_shared) == (bob, False)


async def test_connect_account_carries_the_shared_intent(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    install_connect_flow(
        ConnectFlow(
            providers={"stub": StubProvider()},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
    )
    ctx = _turn_context(workspace_id, agent_id, conversation_id, member_id)
    result = await connect_account_handler(
        ctx,
        ConnectAccountInput(
            provider="stub", shared=True, user_description="connecting the team account"
        ),
    )
    payload = json.loads(result.content[0].text.splitlines()[-1])
    assert payload == {
        "provider": "stub",
        "requester_member_id": str(member_id),
        "shared": True,
        "grantee_agent_id": None,
    }


async def _agent(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _turn(workspace_id: UUID, agent_id: UUID, conversation_id: UUID, seq: int = 1) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status="running",
                inbound="hi",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _conversation(workspace_id: UUID, member_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=sa.select(tables.agent.c.id)
                .where(tables.agent.c.workspace_id == workspace_id)
                .order_by(tables.agent.c.created_at, tables.agent.c.id)
                .limit(1)
                .scalar_subquery(),
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def test_connector_accounts_admit_only_the_speakers_own_and_shared_grants(db: None) -> None:
    """The runtime check: a private grant resolves only for its grantor's turns; a shared grant
    for anyone's; a speakerless (scheduled/internal) turn sees only shared grants."""
    workspace_id = await _workspace()
    grantor_id, agent_id = await _member_agent(workspace_id)
    other_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_id,
                workspace_id=workspace_id,
                email="other@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id = await _conversation(workspace_id, grantor_id)
    store = GrantStore()
    for account_id, member, shared in (
        ("acct-private", grantor_id, False),
        ("acct-shared", other_id, True),
        ("acct-other-private", other_id, False),
    ):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id=account_id,
            host=GRANTED_HOST,
            grantor_member_id=member,
            conversation_id=conversation_id,
            shared=shared,
        )
    ctx = _turn_context(workspace_id, agent_id, conversation_id, grantor_id)
    ctx = replace(ctx, grants=store)
    with ws(workspace_id), agent(agent_id):
        assert await ctx.connector_accounts("stub") == ("acct-private", "acct-shared")
        speakerless = replace(
            _turn_context(workspace_id, agent_id, conversation_id, None), grants=store
        )
        assert await speakerless.connector_accounts("stub") == ("acct-shared",)
        with pytest.raises(ValueError, match="acct-other-private"):
            await ctx.connector_account("stub", "acct-other-private")


async def test_connector_accounts_resolve_the_on_behalf_of_member_for_speakerless_turns(
    db: None,
) -> None:
    """A scheduled fire or subagent carries the initiating member as on_behalf_of, so it keeps that
    member's private connections even with no live speaker — a turn with no member at all sees only
    shared grants."""
    workspace_id = await _workspace()
    initiator_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, initiator_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-initiator-private",
        host=GRANTED_HOST,
        grantor_member_id=initiator_id,
        conversation_id=conversation_id,
        shared=False,
    )
    on_behalf = replace(
        _turn_context(workspace_id, agent_id, conversation_id, None),
        grants=store,
        on_behalf_of_member_id=initiator_id,
    )
    assert on_behalf.speaker_member_id is None
    with ws(workspace_id), agent(agent_id):
        assert await on_behalf.connector_accounts("stub") == ("acct-initiator-private",)
    anonymous = replace(_turn_context(workspace_id, agent_id, conversation_id, None), grants=store)
    with ws(workspace_id), agent(agent_id):
        assert await anonymous.connector_accounts("stub") == ()


async def test_the_main_agent_connects_an_account_for_another_agent(db: None) -> None:
    """A member on a surface bound only to the main agent finishes another agent's setup by asking.
    The named agent is resolved and gated when the request is made, so the durable request already
    carries the agent the seal will bind."""
    workspace_id = await _workspace()
    member_id, main_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).values(is_main=True).where(tables.agent.c.id == main_id)
        )
    shipped = await _agent(workspace_id, "shipped")
    install_connect_flow(
        ConnectFlow(
            providers={"stub": StubProvider()},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
    )
    ctx = _turn_context(workspace_id, main_id, conversation_id, member_id)
    result = await connect_account_handler(
        ctx,
        ConnectAccountInput(
            provider="stub", agent="shipped", user_description="connecting their account"
        ),
    )
    assert result.is_error is False
    request = ConnectRequest.model_validate_json(result.content[0].text.splitlines()[1])
    assert request.grantee_agent_id == shipped


async def test_the_grant_lands_on_the_named_agent_not_the_asking_one(db: None) -> None:
    """The whole point of the target: the member answers on the main agent's turn, and the
    connection must still belong to the agent that needs it. The seal carries the grantee, so the
    callback records the grant against that agent."""
    workspace_id = await _workspace()
    member_id, main_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).values(is_main=True).where(tables.agent.c.id == main_id)
        )
    shipped = await _agent(workspace_id, "shipped")
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    install_connect_flow(flow)
    ctx = _turn_context(workspace_id, main_id, conversation_id, member_id)
    result = await connect_account_handler(
        ctx,
        ConnectAccountInput(
            provider="stub", agent="shipped", user_description="connecting their account"
        ),
    )
    request = ConnectRequest.model_validate_json(result.content[0].text.splitlines()[1])
    terminal = TerminalFrame(status="done", connect_request=request)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=ctx.turn.id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=main_id,
                seq=1,
                status="done",
                inbound="set up shipped",
                admission_source="member",
                speaker_member_id=member_id,
                terminal=terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    url = await ConnectHandoff(flow).authorize(workspace_id, ctx.turn.id, member_id)
    state = parse_qs(urlparse(url).query)["state"][0]
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        done = await client.get("/v1/connect/callback", params={"state": state, "code": "the-code"})
        assert done.status_code == 200
    async with workspace_tx() as connection:
        granted = (
            await connection.execute(
                sa.select(tables.connector_grant.c.agent_id).where(
                    tables.connector_grant.c.workspace_id == workspace_id
                )
            )
        ).all()
    assert [row.agent_id for row in granted] == [shipped]


async def test_an_agent_that_is_not_main_cannot_connect_for_another(db: None) -> None:
    """A grant changes one agent's authority, so naming a different agent is the main agent's act
    alone — the same rule the object verbs hold. Any other agent is refused where it asks."""
    workspace_id = await _workspace()
    member_id, asking = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    await _agent(workspace_id, "shipped")
    install_connect_flow(
        ConnectFlow(
            providers={"stub": StubProvider()},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
    )
    ctx = _turn_context(workspace_id, asking, conversation_id, member_id)
    with pytest.raises(ValueError, match="only the workspace main agent"):
        await connect_account_handler(
            ctx,
            ConnectAccountInput(
                provider="stub", agent="shipped", user_description="connecting their account"
            ),
        )


async def test_connecting_for_an_unknown_agent_is_refused(db: None) -> None:
    workspace_id = await _workspace()
    member_id, main_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).values(is_main=True).where(tables.agent.c.id == main_id)
        )
    install_connect_flow(
        ConnectFlow(
            providers={"stub": StubProvider()},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
    )
    ctx = _turn_context(workspace_id, main_id, conversation_id, member_id)
    with pytest.raises(ValueError, match="no agent named"):
        await connect_account_handler(
            ctx,
            ConnectAccountInput(
                provider="stub", agent="absent", user_description="connecting their account"
            ),
        )


async def test_a_grant_refuses_an_archived_app_name(db: None) -> None:
    workspace_id = await _workspace()
    member_id, main_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).values(is_main=True).where(tables.agent.c.id == main_id)
        )
    retired = await _agent(workspace_id, "notes")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(
                name=f"~archived-{retired}",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
            .where(tables.agent.c.id == retired)
        )
    install_connect_flow(
        ConnectFlow(
            providers={"stub": StubProvider()},
            fernet=Fernet(Fernet.generate_key()),
            store=GrantStore(),
            redirect_uri=REDIRECT_URI,
        )
    )
    ctx = _turn_context(workspace_id, main_id, conversation_id, member_id)

    with pytest.raises(ValueError, match="no agent named"):
        await connect_account_handler(
            ctx,
            ConnectAccountInput(
                provider="stub", agent="notes", user_description="connecting their account"
            ),
        )
