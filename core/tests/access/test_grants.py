import asyncio
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import sqlalchemy.exc as sa_exc
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.db import workspace_tx
from ufo.host.ext.loader import connection_hooks
from ufo.host.tools.builtins import ConnectAccountInput, connect_account_handler
from ufo.runtime.access.connectors import CliCredential
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    ConnectorTransferHosts,
    HostEntry,
    PolicyScope,
    SessionPolicy,
    policy_hosts,
)
from ufo.runtime.access.grants import (
    CommitIdentity,
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
    cli_accounts,
    connection_summaries,
    grant_sentinel,
    grant_summaries,
    install_connect_flow,
    tenant_url,
)
from ufo.runtime.agent_scope import AgentUnbound, agent
from ufo.runtime.billing.accounting import UNGATED_LEDGER
from ufo.runtime.billing.spend import NO_SPEND_GATES
from ufo.runtime.ext.manifest import HookContext, HookOutcome, HookSpec, Manifest
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.surfaces.admission import Admission, ConnectResume
from ufo.runtime.surfaces.cli import CONNECTED_PARAM, callback_router, portal_url
from ufo.runtime.tools.context import SpeakerRequired, ToolContext
from ufo.runtime.turns.audience import (
    SHARED_AUDIENCE,
    conversation_audience,
    foreign_room_audience,
)
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.schema.records import Agent, ConnectRequest, TerminalFrame, Turn
from ufo.schema.tables import MAX_BACKFILL_DAYS
from ufo.sdk.callback_page import (
    CLOSE_THIS_PAGE,
    CONSENT_WINDOW_MARK,
    CONVERSATION_CONTINUES,
    forward_script,
)

GRANTED_HOST = "api.granted.test"
HOST_A = "api.aaa.test"
HOST_B = "api.bbb.test"
REDIRECT_URI = "http://surface/v1/connect/callback"
PORTAL_URL = "https://ufo.example.com/surface/web"
LOCK_WAIT_TIMEOUT_SECONDS = 5
STREAM_SYNCED_AT = datetime(2026, 8, 3, tzinfo=UTC)
GITHUB_SUBJECT = "583231"


@pytest.fixture(autouse=True)
def _reset_connect_flow() -> Iterator[None]:
    yield
    install_connect_flow(None)


@dataclass(frozen=True)
class StubProvider:
    """The injected OAuth descriptor for the proof — stands in for a connector extension's
    provider, never the thing asserted."""

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


class _UnaskedSecret:
    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        raise AssertionError(f"cross-agent token read for {account_id}")


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
    commit: CommitIdentity | None = None,
    identity: str | None = None,
) -> UUID:
    with ws(workspace_id), agent(agent_id):
        return await store.record(
            provider=provider,
            account_id=account_id,
            host=host,
            grantor_member_id=grantor_member_id,
            shared=shared,
            commit=commit,
            identity=identity,
        )


async def _active(store: GrantStore, workspace_id: UUID, agent_id: UUID) -> tuple[Grant, ...]:
    with ws(workspace_id), agent(agent_id):
        return await store.active_grants()


async def _set_shared(
    store: GrantStore,
    workspace_id: UUID,
    agent_id: UUID,
    actor_member_id: UUID,
    connection_id: UUID,
    shared: bool,
) -> bool:
    with ws(workspace_id), agent(agent_id):
        return await store.set_shared(
            connection_id,
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


async def _policy(
    rules: PerAgentRules, workspace_id: UUID, agent_id: UUID, member_id: UUID
) -> SessionPolicy:
    with ws(workspace_id), agent(agent_id):
        return await rules.session_policy(PolicyScope(workspace_id, member_id, True, True, None))


def test_grant_sentinel_is_deterministic_per_account() -> None:
    assert grant_sentinel("acct-1") == grant_sentinel("acct-1")
    assert grant_sentinel("acct-1") != grant_sentinel("acct-2")
    assert "acct-1" in grant_sentinel("acct-1")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_grant_store_requires_an_agent_boundary(db: None) -> None:
    with ws(await _workspace()), pytest.raises(AgentUnbound):
        await GrantStore().active_grants()


TRANSFER_HOST = "stash.broker.test"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_resolver_folds_the_transfer_hosts_into_the_session_policy(db: None) -> None:
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
    resolver = PerAgentRules(
        grants=store, transfer_hosts=ConnectorTransferHosts({"stub": (TRANSFER_HOST,)})
    )
    policy = await _policy(resolver, workspace_id, agent_id, member_id)
    assert policy.hosts == policy_hosts(GRANTED_HOST, TRANSFER_HOST)
    assert policy.bind == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_reconnecting_under_a_fresh_broker_account_settles_on_one_connection(
    db: None,
) -> None:
    """A broker mints a new account id for every consent, so a member reauthorizing to widen
    scope lands an id this workspace has never seen."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for account_id in ("apn_first", "apn_second"):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id=account_id,
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
            identity=GITHUB_SUBJECT,
        )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.connection.c.account_id, tables.connection.c.identity)
            )
        ).all()
    assert [(row.account_id, row.identity) for row in rows] == [("apn_second", GITHUB_SUBJECT)]
    grants = await _active(store, workspace_id, agent_id)
    assert cli_accounts(grants, "stub", member_id) == ("apn_second",)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_two_provider_subjects_stay_two_connections(db: None) -> None:
    """Settling on the subject is not collapsing the provider: a member who connects two real
    accounts holds two, and the CLI tier is ambiguous exactly as it was."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    for account_id, subject in (("apn_work", GITHUB_SUBJECT), ("apn_personal", "778899")):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id=account_id,
            host=GRANTED_HOST,
            grantor_member_id=member_id,
            conversation_id=conversation_id,
            shared=False,
            identity=subject,
        )
    grants = await _active(store, workspace_id, agent_id)
    assert cli_accounts(grants, "stub", member_id) == ("apn_personal", "apn_work")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_reconnecting_refreshes_the_commit_identity_on_the_reused_row(db: None) -> None:
    """Every connection recorded before this deploy read an identity holds none, and a display
    name changes on the provider."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    identities = (
        None,
        CommitIdentity(name="Alex Graveley", email="12345+alexg@users.noreply.github.test"),
        CommitIdentity(name="Alex G", email="12345+alexg@users.noreply.github.test"),
    )
    seen: list[CommitIdentity | None] = []
    for commit in identities:
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
            commit=commit,
        )
        seen.append((await _active(store, workspace_id, agent_id))[0].commit)
    assert seen == list(identities)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connect_flow_cannot_land_after_the_grantor_loses_access(db: None) -> None:
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
        shared=True,
    )
    state = parse_qs(urlparse(url).query)["state"][0]
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == member_id)
            .values(seated_at=None, updated_at=sa.func.now())
        )

    with pytest.raises(ConnectionPermissionDenied, match="no longer has workspace access"):
        await flow.complete(state=state, code="the-code")

    async with workspace_tx() as connection:
        connections = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connection))
        ).scalar_one()
        grants = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.connector_grant))
        ).scalar_one()
    assert (connections, grants) == (0, 0)


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_record_takes_no_lock_that_conflicts_with_a_concurrent_workspace_write(
    db: None,
    database_url: str,
) -> None:
    """`record` completes a callback whose provider code is already spent, so it must never be
    the side Postgres aborts to break a lock cycle."""
    if not database_url.startswith("postgresql"):
        pytest.skip("row-lock modes require PostgreSQL")
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    with ws(workspace_id):
        async with workspace_tx() as other:
            for held in (
                sa.select(tables.workspace.c.id).where(tables.workspace.c.id == workspace_id),
                sa.select(tables.member.c.id).where(tables.member.c.id == member_id),
                sa.select(tables.conversation.c.id).where(
                    tables.conversation.c.id == conversation_id
                ),
            ):
                await other.execute(held.with_for_update(read=True, key_share=True))
            try:
                async with asyncio.timeout(LOCK_WAIT_TIMEOUT_SECONDS):
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
            except TimeoutError:
                pytest.fail("record waited on a row a concurrent workspace write holds KEY SHARE")
    async with workspace_tx() as connection:
        owner = (
            await connection.execute(
                sa.select(tables.connection.c.owner_member_id).where(
                    tables.connection.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert owner == member_id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_landed_connection_reaches_every_extension_that_derives_from_it(db: None) -> None:
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
            manifests,
            CredentialStore(fernet=Fernet(Fernet.generate_key())),
            spend=NO_SPEND_GATES,
            ledger=UNGATED_LEDGER,
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


async def test_tampered_connect_state_is_refused() -> None:
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
    )
    with pytest.raises(ConnectStateInvalid):
        await flow.complete(state="not-a-sealed-token", code="x")


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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert (summary.owner_member_id, summary.host, summary.shared) == (
        member_id,
        GRANTED_HOST,
        False,
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_agent_a_authenticates_only_to_its_own_granted_host(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_a = await _member_agent(workspace_id)
    agent_b = await _agent(workspace_id, "assistant-b")
    conversation_id = await _conversation(workspace_id, member_id)
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
        grants=store,
        internet=True,
        clis={
            "stub": CliCredential(env="STUB_TOKEN", header="authorization", secret=_UnaskedSecret())
        },
    )
    policy_a = await _policy(resolver, workspace_id, agent_a, member_id)
    assert HostEntry(host=HOST_A) in policy_a.hosts
    assert HostEntry(host=HOST_B) not in policy_a.hosts
    assert not any(bind.host == HOST_B for bind in policy_a.bind)
    assert policy_a.internet


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_grant_recorded_after_start_is_live_for_the_next_compile(db: None) -> None:
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    store = GrantStore()
    resolver = PerAgentRules(grants=store)

    before = await _policy(resolver, workspace_id, agent_id, member_id)
    assert HostEntry(host=HOST_A) not in before.hosts
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
    after = await _policy(resolver, workspace_id, agent_id, member_id)
    assert HostEntry(host=HOST_A) in after.hosts


def _turn_context(
    workspace_id: UUID,
    agent_id: UUID,
    conversation_id: UUID,
    member_id: UUID | None,
    *,
    main: bool = False,
) -> ToolContext:
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
        agent=Agent(prompt="p", model="claude-opus-4-8", is_main=main),
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_completed_connect_lands_a_real_turn_on_the_conversations_queue(db: None) -> None:
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
    assert turns[0]["inbound"] == "Connected Stub: Work account."
    assert dbos.enqueued == [str(turns[0]["id"])]

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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_connect_from_a_portal_panel_leaves_the_intent_lane_alone(db: None) -> None:
    """A connect begun from a portal panel seals that panel's own conversation, and that lane
    dispatches one typed verb per turn with no model round."""
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
    assert turns == 1
    assert granted == 1

    install_connect_flow(flow)
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        page = (
            await client.get("/v1/connect/callback", params={"state": state, "code": "the-code"})
        ).text
    assert CLOSE_THIS_PAGE in page and CONVERSATION_CONTINUES not in page
    assert "conversation" not in page and "agent" not in page
    assert "window.close()" in page


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_callback_carries_a_link_arrived_tab_to_the_connectors_screen(db: None) -> None:
    """A deploy with a browser surface takes the member to the screen the account now stands on,
    rather than telling them to close a tab the browser will not close."""
    workspace_id = await _workspace()
    member_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, member_id)
    flow = ConnectFlow(
        providers={"stub": StubProvider()},
        fernet=Fernet(Fernet.generate_key()),
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
        portal_url=PORTAL_URL,
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

    install_connect_flow(flow)
    app = FastAPI()
    app.include_router(callback_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        done = await client.get(
            "/v1/connect/callback",
            params={"state": state, "code": "the-code"},
        )

    assert done.status_code == 200
    assert "Stub · Work account connected." in done.text
    assert f'<a href="{PORTAL_URL}#/connectors">Go to your connectors</a>' in done.text
    assert f'sessionStorage.getItem("{CONSENT_WINDOW_MARK}")' in done.text
    assert "window.close()" in done.text
    assert (
        f'location.replace("{PORTAL_URL}?connected=Stub+%C2%B7+Work+account#/connectors")'
        in done.text
    )
    assert done.text.count(f"{CONNECTED_PARAM}=") == 1
    assert CLOSE_THIS_PAGE not in done.text and CONVERSATION_CONTINUES not in done.text


def test_a_deploy_with_no_browser_surface_has_no_screen_to_forward_to() -> None:
    assert portal_url("https://ufo.example.com/", "web") == PORTAL_URL
    assert portal_url(None, "web") is None
    assert portal_url("", "web") is None
    assert portal_url("https://ufo.example.com", None) is None


def test_the_forwarding_script_reads_the_mark_before_it_takes_a_window_away() -> None:
    """Which window this is decides which half runs, and a browser that refuses storage answers no
    mark — which carries the member, the one direction that is never a dead end."""
    script = forward_script("https://ufo.example.com/surface/web#/connectors")
    marked = script.index(f'sessionStorage.getItem("{CONSENT_WINDOW_MARK}")')
    assert marked < script.index("window.close()") < script.index("location.replace")
    assert "catch" in script[:marked] or "catch" in script[marked:]


def test_the_forwarding_script_cannot_be_left_by_the_url_it_carries() -> None:
    """The URL is deploy-configured, so this is the seal on the page rather than a live threat."""
    script = forward_script('https://ufo.example.com/#/"</script><script>alert(1)')
    assert script.count("</script>") == 1
    assert script.endswith("</script>")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    result = await connect_account_handler(ctx, ConnectAccountInput(provider="stub"))
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
            assert CONVERSATION_CONTINUES in done.text
            assert "window.close()" in done.text

    assert [(call.conversation_id, call.speaker_member_id) for call in resumed.calls] == [
        (conversation_id, member_id),
        (conversation_id, member_id),
    ]
    assert len({call.idempotency_key for call in resumed.calls}) == 1
    assert resumed.calls[0].message == "Connected Stub: Work account."

    # A key built from the connection would repeat here, and admission matches on (workspace, key),
    # so this message would be dropped while the page said the agent was carrying on.
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
    assert rows[0].owner_member_id == member_id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connect_handoff_refuses_a_request_whose_agent_is_gone(db: None) -> None:
    """A request outliving its own signing window may outlive the agent it was to grant."""
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


async def test_connect_account_without_a_speaker_is_refused() -> None:
    """The grant belongs to the member who asked, so the tool asks the shared helper for one. The
    class is `SpeakerRequired` and the refusal names `requested_by`, which is what a retry sets."""
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), None)
    with pytest.raises(SpeakerRequired, match="requested_by"):
        await connect_account_handler(ctx, ConnectAccountInput(provider="stub"))


async def test_connect_account_without_an_installed_flow_raises() -> None:
    install_connect_flow(None)
    ctx = _turn_context(uuid4(), uuid4(), uuid4(), uuid4())
    with pytest.raises(ConnectUnavailable):
        await connect_account_handler(ctx, ConnectAccountInput(provider="stub"))


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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_attach_honors_ownership_and_sharing_and_never_widens(db: None) -> None:
    """The owner attaches their private connection to a second agent; another member is refused
    until the connection is shared, and an admin holds no escape past that refusal."""
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
                    actor_member_id=actor,
                    shared=False,
                )
        with pytest.raises(ValueError, match="attach cannot share"):
            await store.attach(
                provider="stub",
                account_id="acct-42",
                actor_member_id=owner_id,
                shared=True,
            )
        assert (
            await store.attach(
                provider="stub",
                account_id="acct-42",
                actor_member_id=owner_id,
                shared=False,
            )
            is True
        )
        assert (
            await store.attach(
                provider="stub",
                account_id="missing",
                actor_member_id=owner_id,
                shared=False,
            )
            is False
        )
    (attached,) = await _active(store, workspace_id, second_agent)
    assert attached.account_id == "acct-42"
    assert attached.connection_shared is False
    (initial,) = await _active(store, workspace_id, agent_id)
    assert (
        await _set_shared(store, workspace_id, agent_id, owner_id, initial.connection_id, True)
        is True
    )
    with ws(workspace_id), agent(second_agent):
        assert (
            await store.attach(
                provider="stub",
                account_id="acct-42",
                actor_member_id=other_id,
                shared=True,
            )
            is True
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        assert (
            await store.set_shared(regranted.connection_id, False, actor_member_id=member_id)
            is True
        )
        assert await store.revoke(reconnected.id, actor_member_id=member_id) is False
    assert (await _active(store, workspace_id, agent_id))[0].id == regranted.id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            await store.set_shared(private.connection_id, True, actor_member_id=admin_id)
        assert (
            await store.set_shared(private.connection_id, False, actor_member_id=admin_id) is True
        )
        assert (
            await store.set_shared(private.connection_id, True, actor_member_id=member_id) is True
        )
        assert await store.revoke(private.id, actor_member_id=admin_id) is True
    assert await _active(store, workspace_id, agent_id) == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        assert await store.set_shared(stale.connection_id, True, actor_member_id=alice) is False
        assert await store.revoke(stale.id, actor_member_id=alice) is False
        assert await store.disconnect(stale.connection_id, actor_member_id=alice) is False
        with pytest.raises(ConnectionPermissionDenied):
            await store.set_shared(current.connection_id, True, actor_member_id=alice)
        with pytest.raises(ConnectionPermissionDenied):
            await store.revoke(current.id, actor_member_id=alice)
        with pytest.raises(ConnectionPermissionDenied):
            await store.disconnect(current.connection_id, actor_member_id=alice)
    assert (current.owner_member_id, current.connection_shared) == (bob, False)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        ConnectAccountInput(provider="stub", shared=True),
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_shipped_agent_acting_for_nobody_reaches_shared_connections_alone(
    db: None,
) -> None:
    workspace_id = await _workspace()
    grantor_id, agent_id = await _member_agent(workspace_id)
    speaker_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=speaker_id,
                workspace_id=workspace_id,
                email="speaker@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id = await _conversation(workspace_id, grantor_id)
    store = GrantStore()
    for account_id, shared in (("acct-attached", False), ("acct-shared", True)):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider="stub",
            account_id=account_id,
            host=GRANTED_HOST,
            grantor_member_id=grantor_id,
            conversation_id=conversation_id,
            shared=shared,
        )
    with ws(workspace_id), agent(agent_id):
        shipped = replace(
            _turn_context(workspace_id, agent_id, conversation_id, None), grants=store
        )
        assert await shipped.connector_accounts("stub") == ("acct-shared",)
        with pytest.raises(SpeakerRequired, match="acct-attached"):
            await shipped.connector_account("stub", "acct-attached")

        shared_turn = replace(shipped, other_members_active=True)
        assert await shared_turn.connector_accounts("stub") == ("acct-shared",)
        with pytest.raises(SpeakerRequired, match="acct-attached"):
            await shared_turn.connector_account("stub", "acct-attached")

        unattributed_turn = replace(shipped, member_messages_active=True)
        assert await unattributed_turn.connector_accounts("stub") == ("acct-shared",)
        with pytest.raises(SpeakerRequired, match="acct-attached"):
            await unattributed_turn.connector_account("stub", "acct-attached")

        selected_owner = replace(
            _turn_context(workspace_id, agent_id, conversation_id, grantor_id),
            grants=store,
            other_members_active=True,
        )
        assert await selected_owner.connector_account("stub", "acct-attached") == "acct-attached"

        as_main = replace(
            _turn_context(workspace_id, agent_id, conversation_id, None, main=True), grants=store
        )
        assert await as_main.connector_accounts("stub") == ("acct-shared",)

        spoken_to = replace(
            _turn_context(workspace_id, agent_id, conversation_id, speaker_id), grants=store
        )
        assert await spoken_to.connector_accounts("stub") == ("acct-shared",)


async def test_connector_accounts_admit_only_the_speakers_own_and_shared_grants(db: None) -> None:
    """A private grant resolves only for its grantor's turns; a shared grant for anyone's; a turn
    acting for nobody sees only shared grants."""
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
    for provider, account_id, member, shared in (
        ("stub", "acct-private", grantor_id, False),
        ("stub", "acct-shared", other_id, True),
        ("stub", "acct-other-private", other_id, False),
        ("solo", "acct-solo", grantor_id, False),
    ):
        await _record(
            store,
            workspace_id,
            agent_id,
            provider=provider,
            account_id=account_id,
            host=GRANTED_HOST,
            grantor_member_id=member,
            conversation_id=conversation_id,
            shared=shared,
        )
    ctx = _turn_context(workspace_id, agent_id, conversation_id, grantor_id, main=True)
    ctx = replace(ctx, grants=store)
    with ws(workspace_id), agent(agent_id):
        assert await ctx.connector_accounts("stub") == ("acct-private", "acct-shared")
        speakerless = replace(
            _turn_context(workspace_id, agent_id, conversation_id, None, main=True), grants=store
        )
        assert await speakerless.connector_accounts("stub") == ("acct-shared",)
        with pytest.raises(ValueError, match="acct-other-private"):
            await ctx.connector_account("stub", "acct-other-private")
        with pytest.raises(SpeakerRequired, match="acct-private"):
            await speakerless.connector_account("stub", "acct-private")
        with pytest.raises(SpeakerRequired, match="every 'solo' account"):
            await speakerless.connector_account("solo")
        with pytest.raises(ValueError, match="connect one with connect_account"):
            await speakerless.connector_account("nobody")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_colleagues_private_account_is_named_only_while_they_hold_a_message(
    db: None,
) -> None:
    workspace_id = await _workspace()
    speaker_id, agent_id = await _member_agent(workspace_id)
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
    conversation_id = await _conversation(workspace_id, speaker_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-other-private",
        host=GRANTED_HOST,
        grantor_member_id=other_id,
        conversation_id=conversation_id,
        shared=False,
    )
    alone = replace(
        _turn_context(workspace_id, agent_id, conversation_id, speaker_id, main=True),
        grants=store,
        audience=SHARED_AUDIENCE,
    )
    accompanied = replace(alone, other_members_active=True)
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(ValueError, match="connect one with connect_account") as plain:
            await alone.connector_account("stub")
        assert not isinstance(plain.value, SpeakerRequired)
        assert "other@x.test" not in str(plain.value)
        with pytest.raises(ValueError, match="no active 'stub' account 'acct-other-private'"):
            await alone.connector_account("stub", "acct-other-private")
        with pytest.raises(SpeakerRequired, match=r"other@x\.test"):
            await accompanied.connector_account("stub")
        with pytest.raises(SpeakerRequired, match="acct-other-private"):
            await accompanied.connector_account("stub", "acct-other-private")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_connector_accounts_follow_the_member_a_speakerless_turn_acts_for(
    db: None,
) -> None:
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
    speakerless = _turn_context(workspace_id, agent_id, conversation_id, None)
    fired = replace(
        speakerless,
        grants=store,
        turn=speakerless.turn.model_copy(update={"member_id": initiator_id}),
    )
    assert fired.speaker_member_id is None
    assert fired.acting_member_id == initiator_id
    with ws(workspace_id), agent(agent_id):
        assert await fired.connector_accounts("stub") == ("acct-initiator-private",)
    anonymous = replace(
        _turn_context(workspace_id, agent_id, conversation_id, None, main=True),
        grants=store,
    )
    with ws(workspace_id), agent(agent_id):
        assert await anonymous.connector_accounts("stub") == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_channel_call_asks_for_the_member_a_private_connector_needs(db: None) -> None:
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    asker_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=asker_id,
                workspace_id=workspace_id,
                email="asker@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id = await _conversation(workspace_id, owner_id)
    store = GrantStore()
    await _record(
        store,
        workspace_id,
        agent_id,
        provider="drive",
        account_id="acct-drive",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    owner_email = f"{owner_id.hex[:8]}@x.test"
    channel = replace(
        _turn_context(workspace_id, agent_id, conversation_id, None, main=True),
        grants=store,
        audience=SHARED_AUDIENCE,
        other_members_active=True,
    )
    channel_asker = replace(
        _turn_context(workspace_id, agent_id, conversation_id, asker_id),
        grants=store,
        audience=SHARED_AUDIENCE,
        other_members_active=True,
    )
    channel_owner = replace(
        _turn_context(workspace_id, agent_id, conversation_id, owner_id),
        grants=store,
        audience=SHARED_AUDIENCE,
        other_members_active=True,
    )
    direct_asker = replace(
        _turn_context(workspace_id, agent_id, conversation_id, asker_id), grants=store
    )
    foreign = replace(channel, audience=foreign_room_audience("slack", "C0FOREIGN"))
    with ws(workspace_id), agent(agent_id):
        for account_id in (None, "acct-drive"):
            for missing in (channel, channel_asker):
                with pytest.raises(SpeakerRequired, match=owner_email):
                    await missing.connector_account("drive", account_id)
            with pytest.raises(SpeakerRequired) as outside:
                await foreign.connector_account("drive", account_id)
            assert "@" not in str(outside.value)
            assert "private account, and this call" in str(outside.value)
        assert await channel_owner.connector_account("drive") == "acct-drive"
        selected = await channel_owner.connector_connection("drive", "acct-drive")
        assert (selected.account_id, selected.owner_member_id) == ("acct-drive", owner_id)
        await channel_owner.require_connector_connection(selected)
        with pytest.raises(ValueError) as refused:
            await direct_asker.connector_account("drive")
        assert not isinstance(refused.value, SpeakerRequired)
        with pytest.raises(ValueError, match="connect one with connect_account") as unconnected:
            await channel.connector_account("nobody")
        assert not isinstance(unconnected.value, SpeakerRequired)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_main_agent_connects_an_account_for_another_agent(db: None) -> None:
    """A member on a surface bound only to the main agent finishes another agent's setup by
    asking."""
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
        ConnectAccountInput(provider="stub", agent="shipped"),
    )
    assert result.is_error is False
    request = ConnectRequest.model_validate_json(result.content[0].text.splitlines()[1])
    assert request.grantee_agent_id == shipped


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_grant_lands_on_the_named_agent_not_the_asking_one(db: None) -> None:
    """The whole point of the target: the member answers on the main agent's turn, and the
    connection must still belong to the agent that needs it."""
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
        ConnectAccountInput(provider="stub", agent="shipped"),
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            ConnectAccountInput(provider="stub", agent="shipped"),
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            ConnectAccountInput(provider="stub", agent="absent"),
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
            ConnectAccountInput(provider="stub", agent="notes"),
        )


async def _seed_streams(
    workspace_id: UUID, connection_id: UUID, streams: tuple[str, ...], subject: str
) -> dict[str, tuple[UUID, UUID]]:
    """One source row per stream of `connection_id`, each holding a live page and a tombstoned
    one, with every page stamped `subject` — the state a first sync leaves."""
    seeded: dict[str, tuple[UUID, UUID]] = {}
    async with workspace_tx() as connection:
        for stream in streams:
            source_id, source_uid = uuid4(), uuid7()
            live_id, tombstoned_id = uuid4(), uuid4()
            await connection.execute(
                sa.insert(tables.source).values(
                    uid=source_uid,
                    workspace_id=workspace_id,
                    backend="stub",
                    config={"stream": stream},
                    feed_handle=feed_handle_for({"stream": stream}, frozenset()),
                    connection_id=connection_id,
                    next_sync_at=STREAM_SYNCED_AT,
                    created_at=STREAM_SYNCED_AT,
                    updated_at=STREAM_SYNCED_AT,
                )
            )
            await connection.execute(
                sa.insert(tables.page),
                [
                    {
                        "uid": page_id,
                        "workspace_id": workspace_id,
                        "source_uid": source_uid,
                        "digest": f"sha256:{stream}-{page_id.hex[:8]}",
                        "body_ref": f"sources/{source_id}/{page_id}",
                        "stream": stream,
                        "title": stream.title(),
                        "subject": subject,
                        "tombstone": tombstone,
                        "created_at": STREAM_SYNCED_AT,
                        "updated_at": STREAM_SYNCED_AT,
                    }
                    for page_id, tombstone in ((live_id, False), (tombstoned_id, True))
                ],
            )
            seeded[stream] = (source_id, live_id)
    return seeded


async def _page_subjects(workspace_id: UUID) -> dict[UUID, tuple[str, bool, datetime]]:
    async with workspace_tx() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(
                        tables.page.c.uid,
                        tables.page.c.subject,
                        tables.page.c.tombstone,
                        tables.page.c.updated_at,
                    ).where(tables.page.c.workspace_id == workspace_id)
                )
            )
            .mappings()
            .all()
        )
    return {
        row["uid"]: (
            row["subject"],
            bool(row["tombstone"]),
            row["updated_at"]
            if row["updated_at"].tzinfo is not None
            else row["updated_at"].replace(tzinfo=UTC),
        )
        for row in rows
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_disconnecting_takes_the_streams_and_their_pages_with_it(db: None) -> None:
    """Disconnecting is one `DELETE` on the connection; its source rows, their pages, and its
    grant edges follow by cascade."""
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    peer_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=peer_id,
                workspace_id=workspace_id,
                email="peer@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id = await _conversation(workspace_id, owner_id)
    store = GrantStore()
    connection_id = await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-owned",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    peer_connection_id = await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-peer",
        host=GRANTED_HOST,
        grantor_member_id=peer_id,
        conversation_id=conversation_id,
        shared=False,
    )
    await _seed_streams(workspace_id, connection_id, ("messages", "files"), f"member:{owner_id}")
    await _seed_streams(workspace_id, peer_connection_id, ("messages",), f"member:{peer_id}")

    with ws(workspace_id), agent(agent_id):
        assert await store.disconnect(connection_id, actor_member_id=owner_id)

    async with workspace_tx() as connection:
        connections = (
            (
                await connection.execute(
                    sa.select(tables.connection.c.id).where(
                        tables.connection.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        sources = (
            (
                await connection.execute(
                    sa.select(tables.source.c.connection_id).where(
                        tables.source.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        grants = (
            (
                await connection.execute(
                    sa.select(tables.connector_grant.c.connection_id).where(
                        tables.connector_grant.c.workspace_id == workspace_id
                    )
                )
            )
            .scalars()
            .all()
        )
        pages = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.page)
                .where(tables.page.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert list(connections) == [peer_connection_id]
    assert list(sources) == [peer_connection_id]
    assert list(grants) == [peer_connection_id]
    assert pages == 2  # the peer's one live page and its one tombstone


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sharing_a_connection_restamps_every_page_its_streams_already_synced(
    db: None,
) -> None:
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, owner_id)
    store = GrantStore()
    connection_id = await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-owned",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    private = f"member:{owner_id}"
    seeded = await _seed_streams(workspace_id, connection_id, ("messages", "files"), private)
    live = {seeded[stream][1] for stream in ("messages", "files")}

    assert await _set_shared(store, workspace_id, agent_id, owner_id, connection_id, True)
    shared_now = await _page_subjects(workspace_id)
    assert {shared_now[page_id][0] for page_id in live} == {SHARED_SUBJECT}
    assert all(shared_now[page_id][2] > STREAM_SYNCED_AT for page_id in live)
    tombstoned = {
        page_id: value for page_id, value in shared_now.items() if value[1] and page_id not in live
    }
    assert len(tombstoned) == 2
    assert {value[0] for value in tombstoned.values()} == {private}
    assert {value[2] for value in tombstoned.values()} == {STREAM_SYNCED_AT}

    assert await _set_shared(store, workspace_id, agent_id, owner_id, connection_id, False)
    private_again = await _page_subjects(workspace_id)
    assert {private_again[page_id][0] for page_id in live} == {private}
    assert all(private_again[page_id][2] >= shared_now[page_id][2] for page_id in live)


REFUSED_TENANT_URLS = (
    "http://acme.zendesk.com",  # not https: the token would cross the wire in clear
    "https://user:pass@acme.zendesk.com",  # credentials in a column, logged with every dial
    "https://acme.zendesk.com:8443",  # a port names a service this is not
    "https://acme.zendesk.com?tenant=acme",  # a query the connector would carry onto every path
    "https://acme.zendesk.com#frag",  # a fragment no request ever sends
    "https://",  # no host at all
    "https://acme.zendesk.com:notaport",  # unparseable port
)


def test_a_tenant_url_is_admitted_only_under_its_providers_rule() -> None:
    assert tenant_url("zendesk", "https://acme.zendesk.com") == "https://acme.zendesk.com"
    assert tenant_url("zendesk", "https://acme.zendesk.com/") == "https://acme.zendesk.com"
    assert tenant_url("zendesk", "  https://acme.zendesk.com/  ") == "https://acme.zendesk.com"
    assert (
        tenant_url("quickbooks", "https://quickbooks.api.intuit.com/v3/company/4620816365/")
        == "https://quickbooks.api.intuit.com/v3/company/4620816365"
    )

    for provider in ("github", "zendesk"):
        assert tenant_url(provider, None) is None
        assert tenant_url(provider, "") is None
        assert tenant_url(provider, "   ") is None

    for exfiltrating in (
        "https://api.github.com",
        "https://acme.zendesk.com",
        "https://evil.example.com",
    ):
        with pytest.raises(ValueError, match="'github' has a fixed API host"):
            tenant_url("github", exfiltrating)

    for foreign in (
        "https://evil.example.com",
        "https://acme.zendesk.com.evil.example.com",
        "https://acme.zendesk.com/api/v2",
    ):
        with pytest.raises(ValueError, match=re.escape("https://<subdomain>.zendesk.com")):
            tenant_url("zendesk", foreign)

    with pytest.raises(ValueError, match=re.escape("/v3/company/<realmId>")):
        tenant_url("quickbooks", "https://quickbooks.api.intuit.com")

    for site in ("api.datadoghq.com", "api.us5.datadoghq.com", "api.datadoghq.eu"):
        assert tenant_url("datadog", f"https://{site}/") == f"https://{site}"
    for wrong_site in (
        "https://api.us9.datadoghq.com",
        "https://api.datadoghq.com.evil.example.com",
        "https://api.datadoghq.com/api/v1",
    ):
        with pytest.raises(ValueError, match=re.escape("https://api.datadoghq.com")):
            tenant_url("datadog", wrong_site)

    for refused in REFUSED_TENANT_URLS:
        with pytest.raises(ValueError, match="base_url"):
            tenant_url("zendesk", refused)


async def test_set_feed_stores_what_the_streams_dial_and_how_far_back_they_reach(db: None) -> None:
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    stranger_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=stranger_id,
                workspace_id=workspace_id,
                email="stranger@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    conversation_id = await _conversation(workspace_id, owner_id)
    store = GrantStore()
    connection_id = await _record(
        store,
        workspace_id,
        agent_id,
        provider="zendesk",
        account_id="acct-owned",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )
    fixed_host_id = await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-fixed",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )

    with ws(workspace_id), agent(agent_id):
        assert await store.set_feed(
            connection_id,
            base_url="https://acme.zendesk.com/",
            backfill_days=90,
            actor_member_id=owner_id,
        )
        held = {row.id: row for row in await connection_summaries()}[connection_id]
        assert (held.base_url, held.backfill_days) == ("https://acme.zendesk.com", 90)

        with pytest.raises(ValueError, match="base_url"):
            await store.set_feed(
                connection_id,
                base_url="http://acme.zendesk.com",
                backfill_days=90,
                actor_member_id=owner_id,
            )
        with pytest.raises(ValueError, match="'stub' has a fixed API host"):
            await store.set_feed(
                fixed_host_id,
                base_url="https://acme.zendesk.com",
                backfill_days=90,
                actor_member_id=owner_id,
            )
        with pytest.raises(ConnectionPermissionDenied):
            await store.set_feed(
                connection_id,
                base_url="https://elsewhere.zendesk.com",
                backfill_days=1,
                actor_member_id=stranger_id,
            )
        unchanged = {row.id: row for row in await connection_summaries()}[connection_id]
        assert (unchanged.base_url, unchanged.backfill_days) == ("https://acme.zendesk.com", 90)

        assert await store.set_feed(
            connection_id, base_url=None, backfill_days=None, actor_member_id=owner_id
        )
        cleared = {row.id: row for row in await connection_summaries()}[connection_id]
        assert (cleared.base_url, cleared.backfill_days) == (None, None)

        assert not await store.set_feed(
            uuid4(), base_url=None, backfill_days=None, actor_member_id=owner_id
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_backfill_window_outside_the_admitted_range_is_refused_by_the_database(
    db: None,
) -> None:
    """`backfill_days` is a number of days, and the check bounds it at one and at all history."""
    workspace_id = await _workspace()
    owner_id, agent_id = await _member_agent(workspace_id)
    conversation_id = await _conversation(workspace_id, owner_id)
    store = GrantStore()
    connection_id = await _record(
        store,
        workspace_id,
        agent_id,
        provider="stub",
        account_id="acct-owned",
        host=GRANTED_HOST,
        grantor_member_id=owner_id,
        conversation_id=conversation_id,
        shared=False,
    )

    with ws(workspace_id), agent(agent_id):
        assert await store.set_feed(
            connection_id,
            base_url=None,
            backfill_days=MAX_BACKFILL_DAYS,
            actor_member_id=owner_id,
        )
        for refused in (0, -1, MAX_BACKFILL_DAYS + 1):
            with pytest.raises(sa_exc.IntegrityError):
                await store.set_feed(
                    connection_id,
                    base_url=None,
                    backfill_days=refused,
                    actor_member_id=owner_id,
                )
        (held,) = await connection_summaries()
    assert held.backfill_days == MAX_BACKFILL_DAYS
