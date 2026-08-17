import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.agent_scope import agent
from ufo.connectors import CliCredential, ForwardedResponse
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.egress_control import EgressControl, rule_json
from ufo.egress_resolver import PerAgentRules
from ufo.egress_rules import (
    ForwardRule,
    InjectionRule,
    InternetRule,
    MeterRule,
    ScopeRule,
    ServiceRule,
)
from ufo.ext.manifest import CredentialSlot, InjectionTarget
from ufo.grants import GrantStore, grant_sentinel
from ufo.models.catalog import CORE_PRICING
from ufo.sandbox.session import (
    SENTINEL_MODEL_KEY,
    ProbeToken,
    ProbeTokenCodec,
    RunToken,
    RunTokenCodec,
)
from ufo.schema import tables
from ufo.workspace import ws

CONTROL_TOKEN = "egress-control-secret"
CACHE_TOKEN = "egress-cache-secret"
RUN_TOKENS = RunTokenCodec(b"egress-control-test-token-secret")
PROBE_TOKENS = ProbeTokenCodec(secret=RUN_TOKENS.secret)
PROVIDER = "sampleprov"
HOST = "api.sample.test"
ACCOUNT = "acct-9f3c"
CLI_HEADER = "authorization"
CONTRACT = Path(__file__).parent.parent.parent / "egress" / "tests" / "rule_contract.json"


def _basic(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {CONTROL_TOKEN}"}


def _cache_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {CACHE_TOKEN}"}


@dataclass
class _FakeForwarder:
    """A real `RequestForwarder` standing in for the broker: it records the request the forward
    route hands it and answers a fixed provider response the test reads back — never a mock of the
    thing asserted, the received call and the reconstructed response are."""

    received: list[tuple[str, str, str, dict[str, str], bytes]] = field(default_factory=list)

    async def forward(
        self, account_id: str, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> ForwardedResponse:
        self.received.append((account_id, method, url, dict(headers), body))
        return ForwardedResponse(status=201, headers={"x-echo": "pong"}, body=b"broker body")


def _control(
    resolver: PerAgentRules, clis: dict[str, CliCredential] | None = None
) -> EgressControl:
    return EgressControl(
        control_token=CONTROL_TOKEN,
        cache_control_token=CACHE_TOKEN,
        resolver=resolver,
        clis=clis or {},
        pricing=CORE_PRICING,
        run_tokens=RUN_TOKENS,
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


async def _seed_turn(
    connection: AsyncConnection,
    status: str = "running",
    internet_access_allowed: bool = True,
) -> _Seeded:
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
            internet_access_allowed=internet_access_allowed,
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
    terminal = None if status in ("queued", "running", "parked") else {"status": status}
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status=status,
            inbound="hi",
            terminal=terminal,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return _Seeded(workspace_id, turn_id, agent_id, member_id, conversation_id)


def test_rule_json_matches_the_golden_contract() -> None:
    """The Rust proxy deserializes `rule_contract.json` to pin the wire shape; re-deriving it here
    from real Rule dataclasses proves the committed fixture is exactly what `rule_json` emits, so
    the Rust contract cannot drift from what serve serializes."""
    rules = (
        ScopeRule(allowed_hosts=frozenset({"api.openai.com", "api.anthropic.com"})),
        InternetRule(),
        InjectionRule(
            host="api.anthropic.com",
            header="x-api-key",
            sentinel=SENTINEL_MODEL_KEY,
            real="sk-ant-real-key",
        ),
        MeterRule(host="api.anthropic.com", dimension="tokens"),
        ForwardRule(
            host="api.github.com",
            header="authorization",
            sentinel="UFO_SENTINEL_GRANT_acct-9f3c",
            account_id="acct-9f3c",
            forward=_FakeForwarder(),
        ),
        ServiceRule(host="registry.npmjs.org", daemon_prefix="/pkg/registry.npmjs.org"),
    )
    assert json.loads(CONTRACT.read_text()) == {"rules": [rule_json(rule) for rule in rules]}


async def test_authorize_admits_a_running_turn_and_refuses_an_ended_one(db: None) -> None:
    async with workspace_tx() as connection:
        running = await _seed_turn(connection)
        ended = await _seed_turn(connection, status="done")
    resolver = PerAgentRules(base=(), grants=None)
    async with _client(_control(resolver)) as client:
        live = await client.post(
            "/internal/egress/authorize",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(RunToken(running.workspace_id, running.turn_id))
                )
            },
        )
        dead = await client.post(
            "/internal/egress/authorize",
            headers=_auth(),
            json={
                "proxy_auth": _basic(RUN_TOKENS.encode(RunToken(ended.workspace_id, ended.turn_id)))
            },
        )
        forged = await client.post(
            "/internal/egress/authorize",
            headers=_auth(),
            json={"proxy_auth": "Basic bm90LWEtdG9rZW4="},
        )
    assert live.json() == {"authorized": True, "generation": 0}
    assert dead.json() == {"authorized": False, "generation": None}
    assert forged.json() == {"authorized": False, "generation": None}


async def test_authorize_admits_a_probe_until_its_deadline(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    now = int(datetime.now(UTC).timestamp())
    resolver = PerAgentRules(base=(), grants=None)
    live = PROBE_TOKENS.encode(
        ProbeToken(seeded.workspace_id, seeded.conversation_id, uuid4(), now + 300)
    )
    expired = PROBE_TOKENS.encode(
        ProbeToken(seeded.workspace_id, seeded.conversation_id, uuid4(), now - 1)
    )
    async with _client(_control(resolver)) as client:
        admitted = await client.post(
            "/internal/egress/authorize", headers=_auth(), json={"proxy_auth": _basic(live)}
        )
        refused = await client.post(
            "/internal/egress/authorize", headers=_auth(), json={"proxy_auth": _basic(expired)}
        )
    assert admitted.json() == {"authorized": True, "generation": 0}
    assert refused.json() == {"authorized": False, "generation": None}


async def test_resolve_returns_the_seeded_grant_and_forward_rules(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            conversation_id=seeded.conversation_id,
            shared=True,
        )
    clis = {
        PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, forward=_FakeForwarder())
    }
    resolver = PerAgentRules(base=(), grants=GrantStore(), clis=clis)
    async with _client(_control(resolver, clis)) as client:
        response = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
                )
            },
        )
    rules = response.json()["rules"]
    assert {"kind": "scope", "hosts": [HOST]} in rules
    assert {"kind": "meter", "host": HOST, "dimension": "requests"} in rules
    (forward,) = [rule for rule in rules if rule["kind"] == "forward"]
    assert forward == {
        "kind": "forward",
        "host": HOST,
        "header": CLI_HEADER,
        "sentinel": grant_sentinel(ACCOUNT),
        "account_id": ACCOUNT,
    }


async def test_resolve_forged_principal_yields_the_base(db: None) -> None:
    resolver = PerAgentRules(base=(ScopeRule(allowed_hosts=frozenset({HOST})),), grants=None)
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={"proxy_auth": "Basic bm90LWEtdG9rZW4="},
        )
    assert response.json() == {"rules": [{"kind": "scope", "hosts": [HOST]}]}


async def test_the_bearer_gate_refuses_a_request_with_no_control_token(db: None) -> None:
    resolver = PerAgentRules(base=(), grants=None)
    async with _client(_control(resolver)) as client:
        missing = await client.post("/internal/egress/resolve", json={"proxy_auth": ""})
        wrong = await client.post(
            "/internal/egress/resolve",
            headers={"Authorization": "Bearer wrong"},
            json={"proxy_auth": ""},
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_meter_writes_the_egress_probe_and_token_ledger_rows(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    workspace_id, turn_id = seeded.workspace_id, seeded.turn_id
    usage = {
        "input_tokens": 1000,
        "output_tokens": 2000,
        "cache_read_tokens": 3000,
        "cache_write_5m_tokens": 0,
        "cache_write_1h_tokens": 4000,
    }
    resolver = PerAgentRules(base=(), grants=None)
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/egress/meter",
            headers=_auth(),
            json={
                "records": [
                    {"kind": "egress", "workspace_id": str(workspace_id), "turn_id": str(turn_id)},
                    {"kind": "egress", "workspace_id": str(workspace_id), "turn_id": str(turn_id)},
                    {"kind": "egress", "workspace_id": str(workspace_id), "turn_id": None},
                    {
                        "kind": "tokens",
                        "workspace_id": str(workspace_id),
                        "turn_id": str(turn_id),
                        "model": "claude-opus-4-8",
                        "usage": usage,
                    },
                    {
                        "kind": "tokens",
                        "workspace_id": str(workspace_id),
                        "turn_id": str(turn_id),
                        "model": "claude-opus-4-8",
                        "usage": usage,
                    },
                ]
            },
        )
    assert response.json() == {}
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension, tables.ledger.c.turn_id, tables.ledger.c.amount
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
    by_key = {(row.dimension, row.turn_id): int(row.amount) for row in rows}
    assert by_key[("egress", turn_id)] == 2
    assert by_key[("egress", None)] == 1
    assert by_key[("sandbox_tokens", turn_id)] == 2 * (1000 + 2000 + 3000 + 4000)


async def test_meter_folds_unpriced_cache_write_30m_into_input(monkeypatch, db: None) -> None:
    """The proxy carries OpenAI's cache-write share on cache_write_30m ungated; a model that prices
    a 30m write tier keeps it, one that does not bills it as input. Two records, one of each, prove
    the split is decided here from the pricing rather than in the data plane."""
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    workspace_id, turn_id = seeded.workspace_id, seeded.turn_id
    captured: dict[str, object] = {}

    async def _capture(_connection, _ws, _turn, model, usage, _pricing):
        captured[model] = usage

    monkeypatch.setattr("ufo.egress_control.record_sandbox_tokens", _capture)
    usage = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0,
        "cache_write_30m_tokens": 5000,
        "cache_write_1h_tokens": 0,
    }
    resolver = PerAgentRules(base=(), grants=None)
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/egress/meter",
            headers=_auth(),
            json={
                "records": [
                    {
                        "kind": "tokens",
                        "workspace_id": str(workspace_id),
                        "turn_id": str(turn_id),
                        "model": "gpt-5.6-terra",
                        "usage": usage,
                    },
                    {
                        "kind": "tokens",
                        "workspace_id": str(workspace_id),
                        "turn_id": str(turn_id),
                        "model": "claude-opus-4-8",
                        "usage": usage,
                    },
                ]
            },
        )
    assert response.json() == {}
    assert captured["gpt-5.6-terra"].cache_write_30m_tokens == 5000
    assert captured["gpt-5.6-terra"].input_tokens == 1000
    assert captured["claude-opus-4-8"].cache_write_30m_tokens == 0
    assert captured["claude-opus-4-8"].input_tokens == 6000


async def test_meter_emits_the_sandbox_egress_counter_per_host_and_dimension(monkeypatch) -> None:
    calls: list[tuple[str, int, str, str]] = []
    monkeypatch.setattr(
        "ufo.egress_control.emit_metric",
        lambda name, amount, **dims: calls.append((name, amount, dims["host"], dims["dimension"])),
    )
    resolver = PerAgentRules(base=(), grants=None)
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/egress/meter",
            headers=_auth(),
            json={
                "records": [
                    {"kind": "metric", "host": "api.anthropic.com", "dimension": "tokens"},
                    {"kind": "metric", "host": "api.anthropic.com", "dimension": "tokens"},
                    {"kind": "metric", "host": "github.com", "dimension": "requests"},
                ]
            },
        )
    assert response.json() == {}
    assert set(calls) == {
        ("sandbox_egress_total", 2, "api.anthropic.com", "tokens"),
        ("sandbox_egress_total", 1, "github.com", "requests"),
    }


async def test_forward_routes_through_the_broker_forwarder(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            conversation_id=seeded.conversation_id,
            shared=True,
        )
    forwarder = _FakeForwarder()
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, forward=forwarder)}
    resolver = PerAgentRules(base=(), grants=GrantStore(), clis=clis)
    async with _client(_control(resolver, clis)) as client:
        response = await client.post(
            "/internal/egress/forward",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id))
                ),
                "account_id": ACCOUNT,
                "method": "POST",
                "url": f"https://{HOST}/v1/thing",
                "headers": [["content-type", "application/json"]],
                "body_b64": base64.b64encode(b"payload").decode(),
            },
        )
    body = response.json()
    assert body["status"] == 201
    assert ["x-echo", "pong"] in body["headers"]
    assert base64.b64decode(body["body_b64"]) == b"broker body"
    assert forwarder.received == [
        (
            ACCOUNT,
            "POST",
            f"https://{HOST}/v1/thing",
            {"content-type": "application/json"},
            b"payload",
        )
    ]


async def test_forward_refuses_a_foreign_private_grant(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            conversation_id=seeded.conversation_id,
            shared=False,
        )
    clis = {
        PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, forward=_FakeForwarder())
    }
    resolver = PerAgentRules(base=(), grants=GrantStore(), clis=clis)
    stranger = RunToken(seeded.workspace_id, seeded.turn_id, acting_member_id=uuid4())
    async with _client(_control(resolver, clis)) as client:
        response = await client.post(
            "/internal/egress/forward",
            headers=_auth(),
            json={
                "proxy_auth": _basic(RUN_TOKENS.encode(stranger)),
                "account_id": ACCOUNT,
                "method": "GET",
                "url": f"https://{HOST}/v1/thing",
                "headers": [],
                "body_b64": "",
            },
        )
    assert response.status_code == 403


def _git_slot() -> CredentialSlot:
    return CredentialSlot(
        name="github_git_token",
        description="git token",
        injection=InjectionTarget(
            host="github.com",
            header="authorization",
            sentinel="SENTINEL_GIT",
            git_basic_user="x-access-token",
        ),
    )


async def test_git_credential_resolves_the_matching_git_slot(db: None) -> None:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    await store.put(workspace_id, "github_git_token", "ghs-installation-token")
    resolver = PerAgentRules(base=(), grants=None, credentials=store, slots=(_git_slot(),))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"workspace_id": str(workspace_id), "host": "github.com"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "username": "x-access-token",
        "token": "ghs-installation-token",
        "principal": f"w{workspace_id}",
    }


async def test_git_credential_is_public_without_a_matching_slot(db: None) -> None:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    resolver = PerAgentRules(base=(), grants=None, credentials=store, slots=(_git_slot(),))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"workspace_id": str(uuid4()), "host": "gitlab.com"},
        )
    assert response.status_code == 200
    assert response.json() == {"credential": None, "principal": "public"}


async def test_git_credential_is_gated_by_the_cache_token_not_the_egress_token() -> None:
    """Least privilege: the cache credential reaches only git-credential, and the egress control
    token — the key to the secrets tier — is not accepted here. Neither token crosses to the other's
    route."""
    resolver = PerAgentRules(base=(), grants=None)
    body = {"workspace_id": str(uuid4()), "host": "github.com"}
    async with _client(_control(resolver)) as client:
        none = await client.post("/internal/git-credential", json=body)
        egress = await client.post("/internal/git-credential", headers=_auth(), json=body)
        cross = await client.post(
            "/internal/egress/resolve", headers=_cache_auth(), json={"proxy_auth": ""}
        )
    assert none.status_code == 401
    assert egress.status_code == 401
    assert cross.status_code == 401
