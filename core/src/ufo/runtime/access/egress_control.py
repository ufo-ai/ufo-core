"""The internal routes core `serve` answers for sandbox egress.

`POST /internal/egress/tool-bridge/request` and `POST /internal/egress/preview/render` are the
upstreams of the two routes core compiles into a turn's proxy session; the proxy service forwards a
sandbox's request to them. Two headers must verify before any work, and either failing answers 403.
`x-ufo-session` is the proxy service's Ed25519-signed stamp: it forwarded the request for a live
session of that workspace, with those labels, within `SESSION_STAMP_MAX_AGE_SECONDS`. `x-ufo-run`
is the run token core placed as the route's header when it compiled the policy, HMAC-signed with
the deploy's token secret: core compiled this route for this turn and acting member, and a sandbox
cannot present it, since the proxy strips every `x-ufo-*` header a sandbox sends. The stamp's
workspace and `turn` label must equal the run token's, and the turn must be running.

The bridge then dispatches the bounded JSON interface for the member the run token acts for; the
preview relay streams the body to the deploy's preview service under its real bearer.
`/internal/git-credential` answers the cache daemon behind its own token, so that credential
reaches nothing else."""

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.background import BackgroundTask

from ufo.harness.document_renderer import DOCUMENT_INPUT_MAX_BYTES, DOCUMENT_RENDER_TIMEOUT_SECONDS
from ufo.harness.sandbox.session import RunToken, RunTokenCodec
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.access.egress_rules import RUN_HEADER
from ufo.runtime.billing.accounting import TURN_LABEL
from ufo.runtime.tools.bridge import (
    ToolBridgePrincipal,
    ToolBridgeRequest,
    ToolBridgeRequester,
    ToolBridgeResponse,
)
from ufo.runtime.workspace import ws

PROXY_PUBLIC_KEY_ENV = "UFO_PROXY_PUBLIC_KEY"
SESSION_STAMP_HEADER = "x-ufo-session"
SESSION_STAMP_MAX_AGE_SECONDS = 300
PREVIEW_RELAY_TIMEOUT_SECONDS = DOCUMENT_RENDER_TIMEOUT_SECONDS
CACHE_CONTROL_TOKEN_ENV = "UFO_CACHE_CONTROL_TOKEN"


class SessionStamp(BaseModel):
    """What the proxy service vouches for on a request it forwards: the session, its workspace and
    labels, and when it signed, in epoch seconds."""

    model_config = ConfigDict(extra="forbid")

    issued_at: int
    labels: dict[str, str]
    session_id: UUID
    workspace_id: UUID


class StampInvalid(ValueError):
    """A presented stamp is malformed, signed by another key, or outside the skew bound."""


def _b64url(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)


def verify_stamp(key: Ed25519PublicKey, header: str, now: datetime) -> SessionStamp:
    """The stamp `header` carries — `<base64url payload>.<base64url signature>`, the signature over
    the payload's base64url text — when `key` signed it within `SESSION_STAMP_MAX_AGE_SECONDS` of
    `now` either way."""
    body, _, signature = header.partition(".")
    try:
        key.verify(_b64url(signature), body.encode())
        stamp = SessionStamp.model_validate_json(_b64url(body))
    except (InvalidSignature, ValueError) as error:
        raise StampInvalid("the session stamp does not verify") from error
    if abs(now.timestamp() - stamp.issued_at) > SESSION_STAMP_MAX_AGE_SECONDS:
        raise StampInvalid("the session stamp is outside the skew bound")
    return stamp


def load_proxy_public_key(raw: str) -> Ed25519PublicKey:
    """The proxy service's stamp key from its standard base64 of the 32 raw key bytes."""
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(raw, validate=True))
    except ValueError as error:
        raise RuntimeError(
            f"{PROXY_PUBLIC_KEY_ENV} must hold the proxy service's Ed25519 public key as the "
            "standard base64 of its 32 raw bytes, so the routes it relays can be verified."
        ) from error


class GitCredentialRequest(BaseModel):
    proxy_auth: str | None = None
    host: str | None = None


@dataclass(frozen=True)
class PreviewRelay:
    """The deploy's preview service: its address, its real bearer, and the client reaching it."""

    service: tuple[str, int]
    token: str
    http: httpx.AsyncClient


@dataclass(frozen=True)
class EgressControl:
    """The internal egress routes, mounted on core `serve`. `resolver` is the real `PerAgentRules`,
    whose liveness check gates both stamp routes and whose grant read answers the git credential;
    `run_tokens` verifies the run token core placed on each route; `stamp_key` verifies the proxy
    service's stamp. `bridge` dispatches the bounded JSON interface under a live run; `preview`
    relays a render to the preview service.

    `router` serves the stamp routes and `git_credential_router` the cache daemon's route behind
    `cache_control_token`, so each credential reaches exactly one surface."""

    cache_control_token: str | None
    resolver: PerAgentRules
    run_tokens: RunTokenCodec
    bridge: ToolBridgeRequester | None = None
    stamp_key: Ed25519PublicKey | None = None
    preview: PreviewRelay | None = None

    def router(self) -> APIRouter:
        if self.stamp_key is None:
            raise RuntimeError("The stamp routes need the proxy service's public key.")
        router = APIRouter(prefix="/internal/egress")
        router.add_api_route("/tool-bridge/request", self._tool_bridge, methods=["POST"])
        if self.preview is not None:
            router.add_api_route(
                "/preview/render", self._preview_render(self.preview), methods=["POST"]
            )
        return router

    def git_credential_router(self) -> APIRouter:
        """The cache daemon's git-credential callback, on its own route behind its own token so the
        cache credential reaches nothing but this endpoint."""
        if self.cache_control_token is None:
            raise RuntimeError("The git-credential route needs the cache control token.")
        router = APIRouter(prefix="/internal", dependencies=[Depends(self._cache_guard)])
        router.add_api_route("/git-credential", self._git_credential, methods=["POST"])
        return router

    async def _tool_bridge(self, request: Request) -> ToolBridgeResponse:
        live = await self._route_principal(request)
        if self.bridge is None:
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            body = ToolBridgeRequest.model_validate_json(await request.body())
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
        with ws(live.workspace_id):
            return await self.bridge.request(live, body)

    def _preview_render(
        self, relay: PreviewRelay
    ) -> Callable[[Request], Awaitable[StreamingResponse]]:
        async def render(request: Request) -> StreamingResponse:
            await self._route_principal(request)
            length = request.headers.get("content-length", "")
            if not length.isdigit() or int(length) > DOCUMENT_INPUT_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"A preview body must state its length and be at most "
                    f"{DOCUMENT_INPUT_MAX_BYTES} bytes.",
                )
            host, port = relay.service
            headers = {"authorization": f"Bearer {relay.token}", "content-length": length}
            if "content-type" in request.headers:
                headers["content-type"] = request.headers["content-type"]
            try:
                upstream = await relay.http.send(
                    relay.http.build_request(
                        "POST",
                        f"http://{host}:{port}/render",
                        headers=headers,
                        content=request.stream(),
                    ),
                    stream=True,
                )
            except httpx.TransportError as error:
                raise HTTPException(
                    status_code=502, detail="The preview service did not answer."
                ) from error
            return StreamingResponse(
                upstream.aiter_raw(),
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
                background=BackgroundTask(upstream.aclose),
            )

        return render

    async def _route_principal(self, request: Request) -> ToolBridgePrincipal:
        stamp_header = request.headers.get(SESSION_STAMP_HEADER)
        run_header = request.headers.get(RUN_HEADER)
        if self.stamp_key is None or stamp_header is None or run_header is None:
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            stamp = verify_stamp(self.stamp_key, stamp_header, datetime.now(UTC))
            run = self.run_tokens.decode(run_header)
        except ValueError as error:
            raise HTTPException(status_code=403, detail="forbidden") from error
        if stamp.workspace_id != run.workspace_id or stamp.labels.get(TURN_LABEL) != str(
            run.turn_id
        ):
            raise HTTPException(status_code=403, detail="forbidden")
        live = await self.resolver.live_bridge_principal(run)
        if live is None:
            raise HTTPException(status_code=403, detail="forbidden")
        return live

    async def _cache_guard(self, authorization: Annotated[str, Header()] = "") -> None:
        if authorization != f"Bearer {self.cache_control_token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]:
        """The daemon holds no key: it presents the run token the proxy stamped on the relay, so it
        can never obtain another workspace's or another connection's token."""
        principal = self._git_principal(body.proxy_auth)
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

    def _git_principal(self, proxy_auth: str | None) -> RunToken | None:
        scheme, _, encoded = (proxy_auth or "").partition(" ")
        if scheme.lower() != "basic":
            return None
        try:
            username = base64.b64decode(encoded, validate=True).decode().split(":", 1)[0]
            return self.run_tokens.decode(username)
        except ValueError:
            return None
