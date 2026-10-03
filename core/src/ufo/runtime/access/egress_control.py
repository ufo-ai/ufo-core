"""The internal egress-control RPC the Rust egress proxy calls.

The Rust proxy holds no customer key, no owner DSN, and no policy logic: it verifies the
deployment-signed run/probe token, then asks these routes what egress is allowed (and for the
resolved secrets), enforces it on the wire, and posts metering back. Every policy decision, every
key, and every ledger write stay here, where `PerAgentRules` and the `accounting` writers already
own them.

The routes live under `/internal/egress/`, gated by `Authorization: Bearer <control_token>` — a
shared secret the proxy holds, refused before any work. The run or probe token rides each body as
the raw `Proxy-Authorization` value; `EgressControl` verifies its signature with the deploy's token
codec and scopes every read to its workspace under the normal RLS-scoped role. The tool bridge
accepts run tokens only and reuses the same principal for its host-side dispatch."""

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ufo.db import current_workspace, workspace_tx
from ufo.harness.models.pricing import Pricing
from ufo.harness.o11y import emit_metric
from ufo.harness.sandbox.session import ProbeToken, ProbeTokenCodec, RunToken, RunTokenCodec
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    InjectionRule,
    InternetRule,
    MeterRule,
    ResidentialRule,
    Rule,
    ScopeRule,
    ServiceRule,
)
from ufo.runtime.billing.accounting import (
    UNGATED_LEDGER,
    Ledger,
    record_egress_request,
    record_probe_egress_request,
)
from ufo.runtime.tools.bridge import ToolBridgeRequest, ToolBridgeRequester, ToolBridgeResponse
from ufo.runtime.workspace import ws
from ufo.schema.records import Usage

EgressPrincipal = RunToken | ProbeToken


def rule_json(rule: Rule) -> dict[str, object]:
    """The one JSON shape core serializes and the Rust proxy deserializes for each policy rule.
    `kind` tags the variant and the `hosts`/`daemon_prefix` field renames are load-bearing: a Rust
    `#[serde(tag="kind", rename_all="snake_case")]` enum reads exactly these names."""
    match rule:
        case ScopeRule(allowed_hosts=allowed_hosts, pinned=pinned):
            return {"kind": "scope", "hosts": sorted(allowed_hosts), "pinned": pinned}
        case InternetRule():
            return {"kind": "internet"}
        case InjectionRule(host=host, header=header, sentinel=sentinel, real=real):
            return {
                "kind": "injection",
                "host": host,
                "header": header,
                "sentinel": sentinel,
                "real": real,
            }
        case MeterRule(host=host, dimension=dimension):
            return {"kind": "meter", "host": host, "dimension": dimension}
        case ServiceRule(host=host, daemon_prefix=daemon_prefix):
            return {"kind": "service", "host": host, "daemon_prefix": daemon_prefix or None}
        case ResidentialRule(host=host):
            return {"kind": "residential", "host": host}


class AuthorizeRequest(BaseModel):
    proxy_auth: str


class AuthorizeResponse(BaseModel):
    authorized: bool
    generation: int | None = None


class ResolveRequest(BaseModel):
    proxy_auth: str


class EgressMeterRecord(BaseModel):
    kind: Literal["egress"]
    workspace_id: UUID
    turn_id: UUID | None = None


class TokenMeterRecord(BaseModel):
    kind: Literal["tokens"]
    workspace_id: UUID
    turn_id: UUID
    model: str
    usage: Usage


class MetricMeterRecord(BaseModel):
    kind: Literal["metric"]
    host: str
    dimension: str


class MeterRequest(BaseModel):
    records: list[
        Annotated[
            EgressMeterRecord | TokenMeterRecord | MetricMeterRecord,
            Field(discriminator="kind"),
        ]
    ]


class ToolBridgeControlRequest(BaseModel):
    proxy_auth: str
    request: ToolBridgeRequest


class GitCredentialRequest(BaseModel):
    proxy_auth: str | None = None
    host: str | None = None


@dataclass(frozen=True)
class EgressControl:
    """The control-plane RPC over the egress policy code, mounted on core `serve`. `resolver` is the
    real `PerAgentRules` — its liveness gate authorizes each CONNECT, its resolution answers the
    rule set, and its generation reader keys the proxy's rule cache. `pricing` prices the model
    usage the proxy tees off the wire, and `ledger` books it. `run_tokens` verifies the
    deploy-signed token each body carries; probe tokens verify against the same secret. `bridge`
    dispatches the bounded JSON interface under a live run.

    Two secrets, two routers: `control_token` gates `/internal/egress/*`, the secrets-and-metering
    tier the proxy holds; `cache_control_token` gates `/internal/git-credential` alone, the route
    the cache daemon calls. Each credential reaches exactly one surface — the cache token never
    admits it to the secrets tier, restoring the separation the loopback callback had before this
    route moved onto serve."""

    control_token: str
    cache_control_token: str
    resolver: PerAgentRules
    pricing: Pricing
    run_tokens: RunTokenCodec
    bridge: ToolBridgeRequester | None = None
    ledger: Ledger = UNGATED_LEDGER

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/internal/egress", dependencies=[Depends(self._guard)])
        router.add_api_route("/authorize", self._authorize, methods=["POST"])
        router.add_api_route("/resolve", self._resolve, methods=["POST"])
        router.add_api_route("/meter", self._meter, methods=["POST"])
        router.add_api_route("/tool-bridge", self._tool_bridge, methods=["POST"])
        return router

    def git_credential_router(self) -> APIRouter:
        """The cache daemon's git-credential callback, on its own route behind its own token so the
        cache credential reaches nothing but this endpoint."""
        router = APIRouter(prefix="/internal", dependencies=[Depends(self._cache_guard)])
        router.add_api_route("/git-credential", self._git_credential, methods=["POST"])
        return router

    async def _guard(self, authorization: Annotated[str, Header()] = "") -> None:
        if authorization != f"Bearer {self.control_token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    async def _cache_guard(self, authorization: Annotated[str, Header()] = "") -> None:
        if authorization != f"Bearer {self.cache_control_token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse:
        principal = self._principal(body.proxy_auth)
        generation = None if principal is None else await self._live_generation(principal)
        return AuthorizeResponse(authorized=generation is not None, generation=generation)

    async def _live_generation(self, principal: EgressPrincipal) -> int | None:
        match principal:
            case RunToken() as run:
                return await self.resolver.turn_live(run)
            case ProbeToken() as probe:
                return await self.resolver.probe_live(probe)

    async def _resolve(self, body: ResolveRequest) -> dict[str, object]:
        rules = await self.resolver.resolve(self._principal(body.proxy_auth))
        return {"rules": [rule_json(rule) for rule in rules]}

    async def _meter(self, body: MeterRequest) -> dict[str, object]:
        egress: dict[tuple[UUID, UUID | None], int] = {}
        tokens: dict[tuple[UUID, UUID | None], dict[str, Usage]] = {}
        counter: dict[tuple[str, str, UUID | None], int] = {}
        # The proxy posts from outside any turn — the loop global is the only workspace this batch
        # can name, and the point reads unattributed when none is bound.
        loop_ws = current_workspace.get()
        for record in body.records:
            match record:
                case MetricMeterRecord(host=host, dimension=dimension):
                    key = (host, dimension, loop_ws)
                    counter[key] = counter.get(key, 0) + 1
                    continue
                case _:
                    billed = (record.workspace_id, record.turn_id)
            match record:
                case EgressMeterRecord():
                    egress[billed] = egress.get(billed, 0) + 1
                case TokenMeterRecord():
                    by_model = tokens.setdefault(billed, {})
                    previous = by_model.get(record.model, Usage())
                    added = record.usage
                    by_model[record.model] = Usage(
                        input_tokens=previous.input_tokens + added.input_tokens,
                        output_tokens=previous.output_tokens + added.output_tokens,
                        cache_read_tokens=previous.cache_read_tokens + added.cache_read_tokens,
                        cache_write_5m_tokens=(
                            previous.cache_write_5m_tokens + added.cache_write_5m_tokens
                        ),
                        cache_write_30m_tokens=(
                            previous.cache_write_30m_tokens + added.cache_write_30m_tokens
                        ),
                        cache_write_1h_tokens=(
                            previous.cache_write_1h_tokens + added.cache_write_1h_tokens
                        ),
                    )
        for (host, dimension, ws_id), amount in counter.items():
            if ws_id is None:
                emit_metric("sandbox_egress_total", amount, host=host, dimension=dimension)
            else:
                with ws(ws_id):
                    emit_metric("sandbox_egress_total", amount, host=host, dimension=dimension)
        for workspace_id, turn_id in {*egress, *tokens}:
            with ws(workspace_id):
                async with workspace_tx() as connection:
                    count = egress.get((workspace_id, turn_id))
                    if turn_id is None:
                        if count is not None:
                            await record_probe_egress_request(
                                connection, workspace_id, amount=count
                            )
                        continue
                    if count is not None:
                        await record_egress_request(connection, workspace_id, turn_id, amount=count)
                    for model, usage in tokens.get((workspace_id, turn_id), {}).items():
                        await self.ledger.record_sandbox_tokens(
                            connection,
                            workspace_id,
                            turn_id,
                            model,
                            self._priced_cache_write(model, usage),
                            self.pricing,
                        )
        return {}

    def _priced_cache_write(self, model: str, usage: Usage) -> Usage:
        """OpenAI reports a cache-write share on `cache_write_30m_tokens`; a model that does not
        price a 30m tier bills it as plain input, the split the host adapters apply."""
        price = self.pricing.prices.get(model)
        if (price is not None and price.cache_write_30m) or not usage.cache_write_30m_tokens:
            return usage
        return usage.model_copy(
            update={
                "input_tokens": usage.input_tokens + usage.cache_write_30m_tokens,
                "cache_write_30m_tokens": 0,
            }
        )

    async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse:
        principal = self._principal(body.proxy_auth)
        if not isinstance(principal, RunToken) or self.bridge is None:
            raise HTTPException(status_code=403, detail="forbidden")
        live = await self.resolver.live_bridge_principal(principal)
        if live is None:
            raise HTTPException(status_code=403, detail="forbidden")
        with ws(live.workspace_id):
            return await self.bridge.request(live, body.request)

    async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]:
        """The daemon holds no key: it presents the run or probe token the proxy stamped on the
        relay, so it can never obtain another workspace's or another connection's token."""
        principal = None if not body.proxy_auth else self._principal(body.proxy_auth)
        if principal is None or body.host is None:
            return {"principal": "public"}
        resolved = await self.resolver.git_credential(principal, body.host)
        if resolved is None:
            return {"credential": None, "principal": "public"}
        wire, token, account_id = resolved
        return {
            "username": wire.basic_user,
            "token": token,
            "principal": f"w{principal.workspace_id}-{account_id}",
        }

    def _principal(self, proxy_auth: str) -> EgressPrincipal | None:
        if not proxy_auth:
            return None
        try:
            return self.run_tokens.from_proxy_auth(proxy_auth)
        except ValueError:
            pass
        try:
            return ProbeTokenCodec(secret=self.run_tokens.secret).from_proxy_auth(proxy_auth)
        except ValueError:
            return None
