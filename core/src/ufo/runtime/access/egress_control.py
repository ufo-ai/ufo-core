"""The internal routes core `serve` answers for sandbox egress: the tool bridge and the cache
daemon's git credential.

`/internal/egress/tool-bridge` dispatches the sandbox's bounded JSON tool interface for a live run,
gated by `Authorization: Bearer <control_token>` and refused before any work; the run token rides
each body as the raw `Proxy-Authorization` value, verified with the deploy's token codec, and every
read is scoped to its workspace under the normal RLS-scoped role. `/internal/git-credential` answers
the cache daemon behind its own token, so that credential reaches nothing else."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ufo.harness.sandbox.session import ProbeToken, ProbeTokenCodec, RunToken, RunTokenCodec
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.tools.bridge import ToolBridgeRequest, ToolBridgeRequester, ToolBridgeResponse
from ufo.runtime.workspace import ws

CACHE_CONTROL_TOKEN_ENV = "UFO_CACHE_CONTROL_TOKEN"

EgressPrincipal = RunToken | ProbeToken


class ToolBridgeControlRequest(BaseModel):
    proxy_auth: str
    request: ToolBridgeRequest


class GitCredentialRequest(BaseModel):
    proxy_auth: str | None = None
    host: str | None = None


@dataclass(frozen=True)
class EgressControl:
    """The internal egress routes, mounted on core `serve`. `resolver` is the real `PerAgentRules`,
    whose liveness check gates the bridge and whose grant read answers the git credential;
    `run_tokens` verifies the deploy-signed token each body carries, and probe tokens verify against
    the same secret. `bridge` dispatches the bounded JSON interface under a live run.

    Two secrets, two routers: `control_token` gates `/internal/egress/*` and `cache_control_token`
    gates `/internal/git-credential` alone, the route the cache daemon calls. Each credential
    reaches exactly one surface."""

    control_token: str
    cache_control_token: str
    resolver: PerAgentRules
    run_tokens: RunTokenCodec
    bridge: ToolBridgeRequester | None = None

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/internal/egress", dependencies=[Depends(self._guard)])
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
