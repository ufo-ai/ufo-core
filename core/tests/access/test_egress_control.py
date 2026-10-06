import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.harness.sandbox.session import ProbeToken, ProbeTokenCodec, RunToken, RunTokenCodec
from ufo.runtime.access.connectors import CliCredential, GitWire, GrantUnusable
from ufo.runtime.access.egress_control import EgressControl
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import CONNECTION_SECRET_PREFIX, PolicyScope
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.tools.bridge import ToolBridgePrincipal, ToolBridgeRequest, ToolBridgeSuccess
from ufo.runtime.workspace import ws
from ufo.schema import tables

CONTROL_TOKEN = "egress-control-secret"
CACHE_TOKEN = "egress-cache-secret"
RUN_TOKENS = RunTokenCodec(b"egress-control-test-token-secret")
PROBE_TOKENS = ProbeTokenCodec(secret=RUN_TOKENS.secret)
PROVIDER = "sampleprov"
HOST = "api.sample.test"
ACCOUNT = "acct-9f3c"
CLI_HEADER = "authorization"
BRIDGE_REQUEST = {
    "request_id": "00000000-0000-4000-8000-000000000001",
    "action": "get_schema",
    "tool_name": "object_list",
    "arguments": {},
}


def _basic(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {CONTROL_TOKEN}"}


def _cache_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {CACHE_TOKEN}"}


@dataclass(frozen=True)
class _Tokens:
    fault: Exception | None = None
    asked: list[tuple[UUID, str]] = field(default_factory=list)

    async def secret(self, workspace_id: UUID, account_id: str) -> str:
        self.asked.append((workspace_id, account_id))
        if self.fault is not None:
            raise self.fault
        return f"token-{account_id}"


GIT = GitWire(host="github.com", basic_user="x-access-token", helper="!gh auth git-credential")


@dataclass
class _Bridge:
    received: list[tuple[ToolBridgePrincipal, ToolBridgeRequest]] = field(default_factory=list)

    async def request(
        self, run: ToolBridgePrincipal, request: ToolBridgeRequest
    ) -> ToolBridgeSuccess:
        self.received.append((run, request))
        return ToolBridgeSuccess(result={"name": request.tool_name})


def _control(
    resolver: PerAgentRules,
    bridge: object | None = None,
) -> EgressControl:
    return EgressControl(
        control_token=CONTROL_TOKEN,
        cache_control_token=CACHE_TOKEN,
        resolver=resolver,
        run_tokens=RUN_TOKENS,
        bridge=bridge,
    )


def _client(control: EgressControl) -> AsyncClient:
    app = FastAPI()
    app.include_router(control.router())
    app.include_router(control.git_credential_router())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://serve")


@dataclass(frozen=True)
class _Seeded:
    workspace_id: UUID
    turn_id: UUID
    agent_id: UUID
    member_id: UUID
    conversation_id: UUID


async def _seed_turn(connection: AsyncConnection) -> _Seeded:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    await connection.execute(
        sa.insert(tables.member).values(
            id=member_id,
            workspace_id=workspace_id,
            email="a@b.c",
            seated_at=sa.func.now(),
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
            internet_access_allowed=True,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="cli",
            queue_key=uuid4().hex,
            member_id=member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return _Seeded(workspace_id, turn_id, agent_id, member_id, conversation_id)


async def test_tool_bridge_passes_only_a_run_principal_to_the_bridge(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    bridge = _Bridge()
    resolver = PerAgentRules()
    run = RunToken(seeded.workspace_id, seeded.turn_id, acts_for=seeded.member_id)
    probe = ProbeToken(
        seeded.workspace_id,
        seeded.conversation_id,
        uuid4(),
        int(datetime.now(UTC).timestamp()) + 300,
        member_id=seeded.member_id,
    )
    async with _client(_control(resolver, bridge=bridge)) as client:
        admitted = await client.post(
            "/internal/egress/tool-bridge",
            headers=_auth(),
            json={"proxy_auth": _basic(RUN_TOKENS.encode(run)), "request": BRIDGE_REQUEST},
        )
        refused = await client.post(
            "/internal/egress/tool-bridge",
            headers=_auth(),
            json={"proxy_auth": _basic(PROBE_TOKENS.encode(probe)), "request": BRIDGE_REQUEST},
        )
    assert admitted.json() == {"ok": True, "result": {"name": "object_list"}}
    assert refused.status_code == 403
    assert bridge.received == [
        (
            ToolBridgePrincipal(run.workspace_id, run.turn_id, seeded.member_id),
            ToolBridgeRequest.model_validate(BRIDGE_REQUEST),
        )
    ]


async def test_the_bearer_gate_refuses_a_request_with_no_control_token(db: None) -> None:
    resolver = PerAgentRules()
    body = {"proxy_auth": "", "request": BRIDGE_REQUEST}
    async with _client(_control(resolver, bridge=_Bridge())) as client:
        missing = await client.post("/internal/egress/tool-bridge", json=body)
        wrong = await client.post(
            "/internal/egress/tool-bridge", headers={"Authorization": "Bearer wrong"}, json=body
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401


async def _seed_git_cli(
    shared: bool = True, fault: Exception | None = None
) -> tuple[_Seeded, PerAgentRules, _Tokens]:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=shared,
        )
    tokens = _Tokens(fault=fault)
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, secret=tokens, git=GIT)}
    return seeded, PerAgentRules(grants=GrantStore(), clis=clis), tokens


async def test_git_credential_answers_the_granted_accounts_token_for_the_wired_host(
    db: None,
) -> None:
    seeded, resolver, tokens = await _seed_git_cli()
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"proxy_auth": _basic(token), "host": GIT.host},
        )
    assert response.status_code == 200
    assert response.json() == {
        "username": "x-access-token",
        "token": f"token-{ACCOUNT}",
        "principal": f"w{seeded.workspace_id}-{ACCOUNT}",
    }
    assert tokens.asked == [(seeded.workspace_id, ACCOUNT)]


async def test_git_credential_is_public_without_a_principal(db: None) -> None:
    _, resolver, tokens = await _seed_git_cli()
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential", headers=_cache_auth(), json={"host": GIT.host}
        )
    assert response.status_code == 200
    assert response.json() == {"principal": "public"}
    assert tokens.asked == []


async def test_git_credential_is_public_for_a_host_no_cli_clones_through(db: None) -> None:
    seeded, resolver, tokens = await _seed_git_cli()
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"proxy_auth": _basic(token), "host": "gitlab.com"},
        )
    assert response.status_code == 200
    assert response.json() == {"credential": None, "principal": "public"}
    assert tokens.asked == []


async def test_git_credential_is_public_for_a_private_grant_the_principal_cannot_use(
    db: None,
) -> None:
    seeded, resolver, tokens = await _seed_git_cli(shared=False)
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"proxy_auth": _basic(token), "host": GIT.host},
        )
    assert response.json() == {"credential": None, "principal": "public"}
    assert tokens.asked == []


@pytest.mark.parametrize(
    "fault",
    [
        GrantUnusable("reconnect the account", awaits_grant=True),
        RuntimeError("broker 503"),
    ],
    ids=["unhealthy-account", "unreachable-broker"],
)
async def test_git_credential_is_public_when_the_broker_will_not_answer(
    db: None, fault: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """Reading the account's token is a call to the broker, so it is uncertain the way every
    broker call is."""
    seeded, resolver, tokens = await _seed_git_cli(fault=fault)
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
    with caplog.at_level(logging.WARNING, logger="ufo"):
        async with _client(_control(resolver)) as client:
            response = await client.post(
                "/internal/git-credential",
                headers=_cache_auth(),
                json={"proxy_auth": _basic(token), "host": GIT.host},
            )

    assert response.status_code == 200
    assert response.json() == {"credential": None, "principal": "public"}
    assert tokens.asked == [(seeded.workspace_id, ACCOUNT)]
    withheld = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "egress.git_credential_failed"
    ]
    assert [(entry["provider"], entry["account_id"]) for entry in withheld] == [(PROVIDER, ACCOUNT)]
    assert withheld[0]["error_class"] == type(fault).__name__


async def test_git_credential_is_gated_by_the_cache_token_not_the_egress_token() -> None:
    resolver = PerAgentRules()
    body = {"host": GIT.host}
    async with _client(_control(resolver)) as client:
        none = await client.post("/internal/git-credential", json=body)
        egress = await client.post("/internal/git-credential", headers=_auth(), json=body)
        cross = await client.post(
            "/internal/egress/tool-bridge",
            headers=_cache_auth(),
            json={"proxy_auth": "", "request": BRIDGE_REQUEST},
        )
    assert none.status_code == 401
    assert egress.status_code == 401
    assert cross.status_code == 401


async def _bound(resolver: PerAgentRules, seeded: _Seeded, member_id: UUID | None) -> set[str]:
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        policy = await resolver.session_policy(
            PolicyScope(seeded.workspace_id, member_id, True, False, None)
        )
    return {bind.secret for bind in policy.bind}


async def test_session_binds_and_git_credentials_prefer_the_members_private_account(
    db: None,
) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        private = await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=False,
        )
        shared = await GrantStore().record(
            provider=PROVIDER,
            account_id="acct-other",
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=True,
        )
    tokens = _Tokens()
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, secret=tokens, git=GIT)}
    resolver = PerAgentRules(grants=GrantStore(), clis=clis)
    acting = RunToken(seeded.workspace_id, seeded.turn_id, acts_for=seeded.member_id)
    nobody = RunToken(seeded.workspace_id, seeded.turn_id)

    acting_credential = await resolver.git_credential(acting, GIT.host)
    nobody_credential = await resolver.git_credential(nobody, GIT.host)

    assert await _bound(resolver, seeded, seeded.member_id) == {
        f"{CONNECTION_SECRET_PREFIX}{private}"
    }
    assert acting_credential == (GIT, f"token-{ACCOUNT}", ACCOUNT)
    assert await _bound(resolver, seeded, None) == {f"{CONNECTION_SECRET_PREFIX}{shared}"}
    assert nobody_credential == (GIT, "token-acct-other", "acct-other")


async def test_a_probe_acting_for_a_member_loses_a_disconnected_grant(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    store = GrantStore()
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        connection_id = await store.record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=False,
        )
    probe = ProbeToken(
        seeded.workspace_id,
        seeded.conversation_id,
        uuid4(),
        int(datetime.now(UTC).timestamp()) + 300,
        member_id=seeded.member_id,
    )
    tokens = _Tokens()
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, secret=tokens, git=GIT)}
    resolver = PerAgentRules(grants=store, clis=clis)

    granted = await _bound(resolver, seeded, seeded.member_id)
    granted_credential = await resolver.git_credential(probe, GIT.host)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        assert await store.disconnect(connection_id, actor_member_id=seeded.member_id) is True
    removed = await _bound(resolver, seeded, seeded.member_id)
    removed_credential = await resolver.git_credential(probe, GIT.host)

    assert granted == {f"{CONNECTION_SECRET_PREFIX}{connection_id}"}
    assert granted_credential == (GIT, f"token-{ACCOUNT}", ACCOUNT)
    assert removed == set()
    assert removed_credential is None
