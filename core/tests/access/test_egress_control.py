import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.harness.models.catalog import CORE_PRICING
from ufo.harness.sandbox.cache import CACHE_HOST
from ufo.harness.sandbox.preview import PREVIEW_AUTH_HEADER, PREVIEW_HOST, PREVIEW_SENTINEL
from ufo.harness.sandbox.session import (
    SENTINEL_MODEL_KEY,
    ProbeToken,
    ProbeTokenCodec,
    RunToken,
    RunTokenCodec,
)
from ufo.runtime.access.connectors import CliCredential, GitWire, GrantUnusable
from ufo.runtime.access.egress_control import EgressControl, rule_json
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    InjectionRule,
    InternetRule,
    MeterRule,
    ScopeRule,
    ServiceRule,
)
from ufo.runtime.access.grants import GrantStore, grant_sentinel
from ufo.runtime.agent_scope import agent
from ufo.runtime.authority import WORKSPACE_AUTHORITY, MemberAuthority
from ufo.runtime.tools.bridge import (
    TOOL_BRIDGE_HOST,
    ToolBridgeRequest,
    ToolBridgeSuccess,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import TurnRuntimeConfig

CONTROL_TOKEN = "egress-control-secret"
CACHE_TOKEN = "egress-cache-secret"
RUN_TOKENS = RunTokenCodec(b"egress-control-test-token-secret")
PROBE_TOKENS = ProbeTokenCodec(secret=RUN_TOKENS.secret)
PROVIDER = "sampleprov"
HOST = "api.sample.test"
ACCOUNT = "acct-9f3c"
CLI_HEADER = "authorization"
CONTRACT = Path(__file__).parents[3] / "servers" / "egress" / "tests" / "rule_contract.json"


def _basic(token: str) -> str:
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {CONTROL_TOKEN}"}


def _cache_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {CACHE_TOKEN}"}


@dataclass(frozen=True)
class _Tokens:
    """The broker's token read behind a `CliCredential`: deterministic per account and recorded per
    call, so a test asserts the token the route answered and which account it was read for. `fault`
    raises instead, standing for the broker faults this read actually meets — an account the broker
    will not authenticate, and a broker that cannot be reached."""

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
    received: list[tuple[RunToken, ToolBridgeRequest]] = field(default_factory=list)

    async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeSuccess:
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
        pricing=CORE_PRICING,
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


async def _seed_turn(
    connection: AsyncConnection,
    status: str = "running",
    internet_access_allowed: bool = True,
    runtime_config: TurnRuntimeConfig | None = None,
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
            runtime_config=(
                None if runtime_config is None else runtime_config.model_dump(mode="json")
            ),
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return _Seeded(workspace_id, turn_id, agent_id, member_id, conversation_id)


async def test_turn_runtime_config_can_disable_but_not_enable_public_egress(db: None) -> None:
    runtime_config = TurnRuntimeConfig(model="claude-opus-4-8", internet_access=False)
    async with workspace_tx() as connection:
        narrowed = await _seed_turn(connection, runtime_config=runtime_config)
    resolver = PerAgentRules(base=(), grants=None, internet=(InternetRule(),))

    rules = await resolver.resolve(
        RunToken(narrowed.workspace_id, narrowed.turn_id, WORKSPACE_AUTHORITY)
    )

    assert InternetRule() not in rules
    with pytest.raises(ValueError, match="False"):
        TurnRuntimeConfig.model_validate({"model": "claude-opus-4-8", "internet_access": True})


def test_rule_json_matches_the_golden_contract() -> None:
    """The Rust proxy deserializes `rule_contract.json` to pin the wire shape; re-deriving it here
    from real Rule dataclasses proves the committed fixture is exactly what `rule_json` emits, so
    the Rust contract cannot drift from what serve serializes. Both scope shapes ride the fixture:
    the deploy's own hosts admitted as they are, and a workspace-declared host admitted pinned, so
    the proxy still resolves it and refuses a private answer."""
    rules = (
        ScopeRule(allowed_hosts=frozenset({"api.openai.com", "api.anthropic.com"})),
        ScopeRule(allowed_hosts=frozenset({"api.acmekeys.com"}), pinned=True),
        InternetRule(),
        InjectionRule(
            host="api.anthropic.com",
            header="x-api-key",
            sentinel=SENTINEL_MODEL_KEY,
            real="sk-ant-real-key",
        ),
        MeterRule(host="api.anthropic.com", dimension="tokens"),
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
                    RUN_TOKENS.encode(
                        RunToken(running.workspace_id, running.turn_id, WORKSPACE_AUTHORITY)
                    )
                )
            },
        )
        dead = await client.post(
            "/internal/egress/authorize",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(
                        RunToken(ended.workspace_id, ended.turn_id, WORKSPACE_AUTHORITY)
                    )
                )
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
        ProbeToken(
            seeded.workspace_id,
            seeded.conversation_id,
            uuid4(),
            now + 300,
            WORKSPACE_AUTHORITY,
        )
    )
    expired = PROBE_TOKENS.encode(
        ProbeToken(
            seeded.workspace_id,
            seeded.conversation_id,
            uuid4(),
            now - 1,
            WORKSPACE_AUTHORITY,
        )
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


async def test_authorize_refuses_run_and_probe_authority_after_seat_revocation(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    run = RUN_TOKENS.encode(
        RunToken(seeded.workspace_id, seeded.turn_id, MemberAuthority(seeded.member_id))
    )
    probe = PROBE_TOKENS.encode(
        ProbeToken(
            seeded.workspace_id,
            seeded.conversation_id,
            uuid4(),
            int(datetime.now(UTC).timestamp()) + 300,
            MemberAuthority(seeded.member_id),
        )
    )
    resolver = PerAgentRules(base=(), grants=None)
    async with _client(_control(resolver)) as client:
        for token in (run, probe):
            response = await client.post(
                "/internal/egress/authorize",
                headers=_auth(),
                json={"proxy_auth": _basic(token)},
            )
            assert response.json() == {"authorized": True, "generation": 0}
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .values(seated_at=None, updated_at=sa.func.now())
                .where(tables.member.c.id == seeded.member_id)
            )
        for token in (run, probe):
            response = await client.post(
                "/internal/egress/authorize",
                headers=_auth(),
                json={"proxy_auth": _basic(token)},
            )
            assert response.json() == {"authorized": False, "generation": None}


async def test_tool_bridge_passes_only_a_run_principal_to_the_bridge(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    bridge = _Bridge()
    resolver = PerAgentRules(base=(), grants=None)
    request_id = uuid4()
    request = {
        "request_id": str(request_id),
        "action": "get_schema",
        "tool_name": "object_list",
        "arguments": {},
    }
    run = RunToken(seeded.workspace_id, seeded.turn_id, MemberAuthority(seeded.member_id))
    probe = ProbeToken(
        seeded.workspace_id,
        seeded.conversation_id,
        uuid4(),
        int(datetime.now(UTC).timestamp()) + 300,
        MemberAuthority(seeded.member_id),
    )
    async with _client(_control(resolver, bridge=bridge)) as client:
        admitted = await client.post(
            "/internal/egress/tool-bridge",
            headers=_auth(),
            json={"proxy_auth": _basic(RUN_TOKENS.encode(run)), "request": request},
        )
        refused = await client.post(
            "/internal/egress/tool-bridge",
            headers=_auth(),
            json={"proxy_auth": _basic(PROBE_TOKENS.encode(probe)), "request": request},
        )
    assert admitted.json() == {"ok": True, "result": {"name": "object_list"}}
    assert refused.status_code == 403
    assert bridge.received == [
        (
            run,
            ToolBridgeRequest(
                request_id=request_id,
                action="get_schema",
                tool_name="object_list",
            ),
        )
    ]


async def test_resolve_returns_the_seeded_grant_and_injection_rules(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    with ws(seeded.workspace_id), agent(seeded.agent_id):
        await GrantStore().record(
            provider=PROVIDER,
            account_id=ACCOUNT,
            host=HOST,
            grantor_member_id=seeded.member_id,
            shared=True,
        )
    tokens = _Tokens()
    clis = {PROVIDER: CliCredential(env="SAMPLE_TOKEN", header=CLI_HEADER, secret=tokens)}
    resolver = PerAgentRules(base=(), grants=GrantStore(), clis=clis)
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(
                        RunToken(seeded.workspace_id, seeded.turn_id, WORKSPACE_AUTHORITY)
                    )
                )
            },
        )
    rules = response.json()["rules"]
    assert {"kind": "scope", "hosts": [HOST], "pinned": False} in rules
    assert {"kind": "meter", "host": HOST, "dimension": "requests"} in rules
    assert [rule for rule in rules if rule["kind"] == "injection"] == [
        {
            "kind": "injection",
            "host": HOST,
            "header": CLI_HEADER,
            "sentinel": grant_sentinel(ACCOUNT),
            "real": f"token-{ACCOUNT}",
        }
    ]
    assert tokens.asked == [(seeded.workspace_id, ACCOUNT)]


async def test_resolve_rechecks_turn_and_probe_liveness(db: None) -> None:
    async with workspace_tx() as connection:
        seeded = await _seed_turn(connection)
    run = RUN_TOKENS.encode(
        RunToken(seeded.workspace_id, seeded.turn_id, MemberAuthority(seeded.member_id))
    )
    expired_probe = PROBE_TOKENS.encode(
        ProbeToken(
            seeded.workspace_id,
            seeded.conversation_id,
            uuid4(),
            int(datetime.now(UTC).timestamp()) - 1,
            MemberAuthority(seeded.member_id),
        )
    )
    resolver = PerAgentRules(
        base=(
            ScopeRule(allowed_hosts=frozenset({HOST})),
            InjectionRule(
                host=HOST,
                header="authorization",
                sentinel=SENTINEL_MODEL_KEY,
                real="deployment-model-key",
            ),
        ),
        grants=None,
        preview_token="preview-real",
    )
    async with _client(_control(resolver)) as client:
        authorized = await client.post(
            "/internal/egress/authorize",
            headers=_auth(),
            json={"proxy_auth": _basic(run)},
        )
        assert authorized.json() == {"authorized": True, "generation": 0}
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(status="done", terminal={"status": "done"}, updated_at=sa.func.now())
                .where(tables.turn.c.id == seeded.turn_id)
            )
        ended = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={"proxy_auth": _basic(run)},
        )
        expired = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={"proxy_auth": _basic(expired_probe)},
        )

    assert ended.json() == {"rules": []}
    assert expired.json() == {"rules": []}


async def test_resolve_admits_the_preview_host_whatever_the_agents_internet_policy(
    db: None,
) -> None:
    """The cache fronts public hosts, so it is admitted only for an agent that already holds the
    internet; the preview service fronts nothing, and rendering a file the sandbox already holds is
    not reaching the internet. An agent narrowed off the internet therefore resolves the preview
    service rule and nothing else — no internet, no cache — while an unnarrowed one gets both."""
    async with workspace_tx() as connection:
        offline = await _seed_turn(connection, internet_access_allowed=False)
        online = await _seed_turn(connection)
    resolver = PerAgentRules(
        base=(),
        grants=None,
        internet=(InternetRule(),),
        cache_host=CACHE_HOST,
        cache_pkg_hosts=("registry.npmjs.org",),
        preview_token="preview-real",
    )
    async with _client(_control(resolver)) as client:
        narrowed = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(
                        RunToken(offline.workspace_id, offline.turn_id, WORKSPACE_AUTHORITY)
                    )
                )
            },
        )
        unnarrowed = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(
                        RunToken(online.workspace_id, online.turn_id, WORKSPACE_AUTHORITY)
                    )
                )
            },
        )
    preview = {"kind": "service", "host": PREVIEW_HOST, "daemon_prefix": None}
    tool_bridge = {"kind": "service", "host": TOOL_BRIDGE_HOST, "daemon_prefix": None}
    injection = {
        "kind": "injection",
        "host": PREVIEW_HOST,
        "header": PREVIEW_AUTH_HEADER,
        "sentinel": PREVIEW_SENTINEL,
        "real": "preview-real",
    }
    assert narrowed.json() == {"rules": [tool_bridge, preview, injection]}
    assert unnarrowed.json() == {
        "rules": [
            {"kind": "internet"},
            tool_bridge,
            {"kind": "service", "host": CACHE_HOST, "daemon_prefix": None},
            {
                "kind": "service",
                "host": "registry.npmjs.org",
                "daemon_prefix": "/pkg/registry.npmjs.org",
            },
            preview,
            injection,
        ]
    }


async def test_cache_routes_require_the_deploy_internet_capability(db: None) -> None:
    async with workspace_tx() as connection:
        online = await _seed_turn(connection)
    resolver = PerAgentRules(
        base=(),
        grants=None,
        cache_host=CACHE_HOST,
        cache_pkg_hosts=("registry.npmjs.org",),
    )
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={
                "proxy_auth": _basic(
                    RUN_TOKENS.encode(
                        RunToken(online.workspace_id, online.turn_id, WORKSPACE_AUTHORITY)
                    )
                )
            },
        )
    assert response.json() == {
        "rules": [{"kind": "service", "host": TOOL_BRIDGE_HOST, "daemon_prefix": None}]
    }


async def test_resolve_forged_principal_yields_the_base(db: None) -> None:
    resolver = PerAgentRules(base=(ScopeRule(allowed_hosts=frozenset({HOST})),), grants=None)
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/egress/resolve",
            headers=_auth(),
            json={"proxy_auth": "Basic bm90LWEtdG9rZW4="},
        )
    assert response.json() == {"rules": [{"kind": "scope", "hosts": [HOST], "pinned": False}]}


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

    monkeypatch.setattr("ufo.runtime.access.egress_control.record_sandbox_tokens", _capture)
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
        "ufo.runtime.access.egress_control.emit_metric",
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
    return seeded, PerAgentRules(base=(), grants=GrantStore(), clis=clis), tokens


async def test_git_credential_answers_the_granted_accounts_token_for_the_wired_host(
    db: None,
) -> None:
    """The cache daemon presents the run token the proxy stamped on the relay and gets exactly the
    credential the proxy would inject for that principal on the host: the wire's Basic username,
    the granted account's token, and the account as the mirror principal."""
    seeded, resolver, tokens = await _seed_git_cli()
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id, WORKSPACE_AUTHORITY))
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
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id, WORKSPACE_AUTHORITY))
    async with _client(_control(resolver)) as client:
        response = await client.post(
            "/internal/git-credential",
            headers=_cache_auth(),
            json={"proxy_auth": _basic(token), "host": "gitlab.com"},
        )
    assert response.status_code == 200
    assert response.json() == {"credential": None, "principal": "public"}
    assert tokens.asked == []


async def test_git_credential_is_public_for_a_private_grant_under_workspace_authority(
    db: None,
) -> None:
    """A memberless principal reaches only what is shared with the workspace: a member's private
    account answers nothing, so the daemon fetches anonymously rather than as that member."""
    seeded, resolver, tokens = await _seed_git_cli(shared=False)
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id, WORKSPACE_AUTHORITY))
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
    """Reading the account's token is a call to the broker, so it is uncertain the way every broker
    call is. Escaping answers 500 here, which the cache daemon turns into a 502 to the sandbox —
    every clone in the workspace fails, including a public one that needs no credential at all. The
    fault costs the account its authentication and nothing else: the daemon fetches anonymously,
    and the warn names the account so the withholding is visible."""
    seeded, resolver, tokens = await _seed_git_cli(fault=fault)
    token = RUN_TOKENS.encode(RunToken(seeded.workspace_id, seeded.turn_id, WORKSPACE_AUTHORITY))
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
    """Least privilege: the cache credential reaches only git-credential, and the egress control
    token — the key to the secrets tier — is not accepted here. Neither token crosses to the other's
    route."""
    resolver = PerAgentRules(base=(), grants=None)
    body = {"host": GIT.host}
    async with _client(_control(resolver)) as client:
        none = await client.post("/internal/git-credential", json=body)
        egress = await client.post("/internal/git-credential", headers=_auth(), json=body)
        cross = await client.post(
            "/internal/egress/resolve", headers=_cache_auth(), json={"proxy_auth": ""}
        )
    assert none.status_code == 401
    assert egress.status_code == 401
    assert cross.status_code == 401
