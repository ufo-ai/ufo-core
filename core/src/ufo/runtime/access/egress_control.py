"""The internal egress-control RPC the Rust egress proxy calls.

The Rust proxy holds no customer key, no owner DSN, and no policy logic: it verifies the
deployment-signed run/probe token, then asks these routes what egress is allowed (and for the
resolved secrets), enforces it on the wire, and posts metering back. Every policy decision, every
key, and every ledger write stay here, where `PerAgentRules`, the connector forwarders, and the
`accounting` writers already own them.

The routes live under `/internal/egress/`, gated by `Authorization: Bearer <control_token>` — a
shared secret the proxy holds, refused before any work. The run or probe token rides each body as
the raw `Proxy-Authorization` value; `EgressControl` verifies its signature with the deploy's token
codec and scopes every read to its workspace under the normal RLS-scoped role. The tool bridge
accepts run tokens only and reuses the same authority for its host-side dispatch."""

from base64 import b64decode, b64encode
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ufo.db import workspace_tx
from ufo.harness.models.pricing import Pricing
from ufo.harness.o11y import emit_metric, warn
from ufo.harness.sandbox.session import ProbeToken, ProbeTokenCodec, RunToken, RunTokenCodec
from ufo.runtime.access.credentials import credential_host, slot_is_set, slot_secret
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import (
    ForwardRule,
    InjectionRule,
    InternetRule,
    MeterRule,
    Rule,
    ScopeRule,
    ServiceRule,
)
from ufo.runtime.billing.accounting import (
    record_egress_request,
    record_probe_egress_request,
    record_sandbox_tokens,
)
from ufo.runtime.tools.bridge import ToolBridgeRequest, ToolBridgeRequester, ToolBridgeResponse
from ufo.runtime.workspace import ws
from ufo.schema.records import Usage

EgressPrincipal = RunToken | ProbeToken


def rule_json(rule: Rule) -> dict[str, object]:
    """The one JSON shape core serializes and the Rust proxy deserializes for each policy rule.
    `kind` tags the variant and the `hosts`/`daemon_prefix` field renames are load-bearing: a Rust
    `#[serde(tag="kind", rename_all="snake_case")]` enum reads exactly these names. A `ForwardRule`
    drops its broker callable — the proxy needs only the sentinel it matches, not the forwarder."""
    match rule:
        case ScopeRule(allowed_hosts=allowed_hosts):
            return {"kind": "scope", "hosts": sorted(allowed_hosts)}
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
        case ForwardRule(host=host, header=header, sentinel=sentinel, account_id=account_id):
            return {
                "kind": "forward",
                "host": host,
                "header": header,
                "sentinel": sentinel,
                "account_id": account_id,
            }
        case ServiceRule(host=host, daemon_prefix=daemon_prefix):
            return {"kind": "service", "host": host, "daemon_prefix": daemon_prefix or None}


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


class ForwardRequest(BaseModel):
    proxy_auth: str
    account_id: str
    method: str
    url: str
    headers: list[tuple[str, str]]
    body_b64: str


class ForwardResponse(BaseModel):
    status: int
    headers: list[tuple[str, str]]
    body_b64: str


class ToolBridgeControlRequest(BaseModel):
    proxy_auth: str
    request: ToolBridgeRequest


class GitCredentialRequest(BaseModel):
    workspace_id: UUID | None = None
    host: str | None = None


@dataclass(frozen=True)
class EgressControl:
    """The control-plane RPC over the egress policy code, mounted on core `serve`. `resolver` is the
    real `PerAgentRules` — its liveness gate authorizes each CONNECT, its resolution answers the
    rule set, and its generation reader keys the proxy's rule cache. A forwarded request resolves
    the current `ForwardRule` again before it reaches the broker. `pricing` prices the model usage
    the proxy tees off the wire. `run_tokens` verifies the deploy-signed token each body carries;
    probe tokens verify against the same secret. `bridge` dispatches the bounded JSON interface
    under a live run.

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

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/internal/egress", dependencies=[Depends(self._guard)])
        router.add_api_route("/authorize", self._authorize, methods=["POST"])
        router.add_api_route("/resolve", self._resolve, methods=["POST"])
        router.add_api_route("/meter", self._meter, methods=["POST"])
        router.add_api_route("/forward", self._forward, methods=["POST"])
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
        counter: dict[tuple[str, str], int] = {}
        for record in body.records:
            match record:
                case MetricMeterRecord(host=host, dimension=dimension):
                    counter[(host, dimension)] = counter.get((host, dimension), 0) + 1
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
        for (host, dimension), amount in counter.items():
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
                        await record_sandbox_tokens(
                            connection,
                            workspace_id,
                            turn_id,
                            model,
                            self._priced_cache_write(model, usage),
                            self.pricing,
                        )
        return {}

    def _priced_cache_write(self, model: str, usage: Usage) -> Usage:
        """OpenAI reports a cache-write share the proxy carries on `cache_write_30m_tokens` ungated,
        because whether the model prices a 30m write tier is a pricing fact the data plane does not
        hold. A model that does not price it bills that share as plain input — the same split the
        host adapters apply to a call the loop makes — so it is folded back here."""
        price = self.pricing.prices.get(model)
        if (price is not None and price.cache_write_30m) or not usage.cache_write_30m_tokens:
            return usage
        return usage.model_copy(
            update={
                "input_tokens": usage.input_tokens + usage.cache_write_30m_tokens,
                "cache_write_30m_tokens": 0,
            }
        )

    async def _forward(self, body: ForwardRequest) -> ForwardResponse:
        principal = self._principal(body.proxy_auth)
        target = urlsplit(body.url)
        if (
            principal is None
            or target.scheme != "https"
            or target.hostname is None
            or await self._live_generation(principal) is None
        ):
            raise HTTPException(status_code=403, detail="forbidden")
        forward = next(
            (
                rule
                for rule in await self.resolver.resolve(principal)
                if isinstance(rule, ForwardRule)
                and rule.host == target.hostname
                and rule.account_id == body.account_id
            ),
            None,
        )
        if forward is None:
            raise HTTPException(status_code=403, detail="forbidden")
        response = await forward.forward.forward(
            body.account_id,
            body.method,
            body.url,
            dict(body.headers),
            b64decode(body.body_b64),
        )
        return ForwardResponse(
            status=response.status,
            headers=list(response.headers.items()),
            body_b64=b64encode(response.body).decode(),
        )

    async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse:
        principal = self._principal(body.proxy_auth)
        if not isinstance(principal, RunToken) or self.bridge is None:
            raise HTTPException(status_code=403, detail="forbidden")
        with ws(principal.workspace_id):
            return await self.bridge.request(principal, body.request)

    async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]:
        """The cache daemon's git-credential callback, moved off the proxy pod onto core `serve`.
        The daemon holds no key; it asks for the credential the proxy would inject directly, scoped
        to the `(workspace, host)` it was stamped with, so it can never obtain another workspace's
        token. A slot that does not resolve contributes nothing — the daemon fetches anonymously —
        never another slot's identity."""
        credentials = self.resolver.credentials
        if body.workspace_id is None or body.host is None or credentials is None:
            return {"principal": "public"}
        with ws(body.workspace_id):
            resolved = await self._git_credential_for(body.workspace_id, body.host)
        if resolved is None:
            return {"credential": None, "principal": "public"}
        username, secret = resolved
        return {"username": username, "token": secret, "principal": f"w{body.workspace_id}"}

    async def _git_credential_for(self, workspace_id: UUID, host: str) -> tuple[str, str] | None:
        """This workspace's git secret for `host`, resolved exactly as the proxy's injection does:
        the `git_basic_user` slot whose stored host matches. A slot that fails to resolve is skipped
        rather than falling through to another slot's identity."""
        credentials = self.resolver.credentials
        assert credentials is not None
        for slot in self.resolver.slots:
            target = slot.injection
            if target is None or target.git_basic_user is None:
                continue
            try:
                if not await slot_is_set(slot.name, slot.source, workspace_id, credentials):
                    continue
                if await credential_host(credentials, workspace_id, target.host) != host:
                    continue
                secret = await slot_secret(slot.name, slot.source, workspace_id, credentials)
            except Exception as error:
                warn(
                    "egress.git_credential_slot_failed",
                    slot=slot.name,
                    error_class=type(error).__name__,
                )
                continue
            if secret is None:
                continue
            return target.git_basic_user, secret
        return None

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
