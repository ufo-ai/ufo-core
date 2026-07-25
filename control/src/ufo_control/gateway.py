"""The hosted onboarding server and shared-workspace resolver."""

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

import asyncpg
import sqlalchemy as sa
from fastapi import FastAPI, Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.surface import OPERATOR_EMAIL_DOMAIN

from ufo_control.gateway_claim import ClaimError, ClaimWorkflow
from ufo_control.gateway_directives import PROMPT, directive, first_run_install, render
from ufo_control.gateway_email import (
    DEFAULT_PUBLIC_BASE_URL,
    PUBLIC_BASE_URL_ENV,
    WorkEmailError,
    WorkEmailPolicy,
    email_sender_from_env,
    public_apex_host,
)
from ufo_control.gateway_invite import (
    InviteAccepted,
    InviteCodes,
    InviteConsumed,
    InviteExpired,
)
from ufo_control.gateway_shared import SharedWorkspaces, serve_dsn
from ufo_control.gateway_slack_connect import ensure_delivery_table, slack_connect_from_env
from ufo_control.gateway_store import OnboardClaim, OnboardStore
from ufo_control.gateway_token import TOKEN_SECRET_ENV, mint_token
from ufo_control.gateway_web import LOGIN_PAGE, WEB_CHANNEL, parse_directives
from ufo_control.rls import owner_dsn

logger = logging.getLogger(__name__)

WORKSPACE_BASE_URL_ENV = "UFO_WORKSPACE_BASE_URL"
INVITE_REQUIRED_ENV = "UFO_INVITE_REQUIRED"
DEBUG_SURFACE_PATH = "/surface/debug"
SCRIPT_URL_DEFAULT = 'UFO_URL="${UFO_URL:-https://flyingobject.ai}"'
SHELLSCRIPT_MEDIA_TYPE = "text/x-shellscript"
MAX_CHANNEL_BYTES = 64
MAX_SESSION_BYTES = 128
MAX_BODY_BYTES = 4096
GATEWAY_POOL_MIN_SIZE = 1
GATEWAY_POOL_MAX_SIZE = 4

_CLIENT_SCRIPT = Path(__file__).parent / "client" / "ufo"


class _RequestInputError(ValueError):
    pass


def _stamp_script(text: str) -> str:
    version = hashlib.sha1(text.encode()).hexdigest()[:12]
    base_url = os.environ.get(PUBLIC_BASE_URL_ENV, DEFAULT_PUBLIC_BASE_URL)
    stamped = text.replace("UFO_SCRIPT_VERSION=dev", f"UFO_SCRIPT_VERSION={version}", 1)
    return stamped.replace(SCRIPT_URL_DEFAULT, f'UFO_URL="${{UFO_URL:-{base_url}}}"', 1)


STAMPED_SCRIPT = _stamp_script(_CLIENT_SCRIPT.read_text())


@dataclass(frozen=True)
class Onboarding:
    claims: ClaimWorkflow
    store: OnboardStore
    workspaces: SharedWorkspaces
    invites: InviteCodes
    token_secret: str
    apex_host: str
    invite_required: bool

    async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes:
        claim = await self.store.live_claim(channel, session)
        if claim is None:
            return await self._collect_email(channel, session, body, install)
        if claim.verified_at is None:
            return await self._verify_code(claim, body, install)
        return await self._resolve(claim, body, install)

    async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes:
        if not body:
            return render(
                install,
                directive("say", "u f o · flyingobject.ai"),
                directive("say", "begin identification"),
                directive("ask", "enter your work email:"),
            )
        try:
            await self.claims.start(body, channel, session)
        except (WorkEmailError, ClaimError, ValueError) as error:
            return render(
                install, directive("say", str(error)), directive("ask", "enter your work email:")
            )
        return render(
            install,
            directive("say", f"we emailed a code to {body.strip().lower()}"),
            directive("ask", "enter the code:"),
        )

    async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes:
        try:
            await self.claims.verify(claim, body)
        except ClaimError as error:
            return render(
                install, directive("say", str(error)), directive("ask", "enter the code:")
            )
        return await self._resolve(claim, None, install)

    async def _resolve(self, claim: OnboardClaim, answer: str | None, install: bytes) -> bytes:
        accepted = b""
        if self.invite_required and not await self.workspaces.exists(claim.email_domain):
            gate = await self._invite_gate(claim, answer, install)
            match gate:
                case bytes():
                    return gate
                case InviteAccepted(object_number=number, consumed_at=consumed_at):
                    stamp = consumed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    code = (answer or "").strip().lower()
                    accepted = directive(
                        "say", f"code {code} accepted. object #{number} identified. {stamp}"
                    )
        workspace_id = await self.workspaces.ensure(claim.email_domain, claim.email)
        await self.store.complete(claim.claim_id, workspace_id)
        return self._signed_in(claim, workspace_id, install, accepted)

    async def _invite_gate(
        self, claim: OnboardClaim, answer: str | None, install: bytes
    ) -> bytes | InviteAccepted | None:
        if claim.invite_id is not None:
            return None
        if not answer:
            return render(
                install,
                directive("say", "a new workspace needs an invite code."),
                directive("ask", "enter your invite:"),
            )
        code = answer.strip().lower()
        match await self.invites.redeem(code, claim.claim_id):
            case InviteAccepted() as accepted:
                return accepted
            case InviteExpired(expires_at=expires_at):
                expired = expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
                return render(
                    install,
                    directive("say", f"code {code} expired {expired} UTC."),
                    directive("say", "reply to your invite email for a new one."),
                    directive("ask", "enter your invite:"),
                )
            case InviteConsumed():
                return render(
                    install,
                    directive(
                        "say", f"code {code} already used. contact us if that wasn't your team."
                    ),
                    directive("ask", "enter your invite:"),
                )
            case _:
                return render(
                    install,
                    directive("say", "code not recognized."),
                    directive(
                        "say",
                        "request identification: "
                        f"curl https://{self.apex_host}/waitlist -d email=you@yourco.com",
                    ),
                    directive("ask", "enter your invite:"),
                )

    def _signed_in(
        self, claim: OnboardClaim, workspace_id: str, install: bytes, accepted: bytes
    ) -> bytes:
        """The signed-in cap: token and workspace for every member, plus the `debugger` directive
        — the operator session debugger's base URL — only when the claim's channel-verified email
        domain is the operator's. The gate is server-side policy; every renderer (the terminal
        client drops unknown verbs) simply carries or ignores the extra line."""
        token = mint_token(self.token_secret, workspace_id, claim.email)
        operator = claim.email_domain == OPERATOR_EMAIL_DOMAIN
        return render(
            install,
            accepted,
            directive("token", token),
            directive("workspace", self.workspaces.workspace_url),
            directive("debugger", f"{self.workspaces.workspace_url}{DEBUG_SURFACE_PATH}")
            if operator
            else b"",
            directive("say", f"signed in: {claim.email}"),
            directive("ask", PROMPT),
        )


@dataclass(frozen=True)
class GatewayState:
    pool: asyncpg.Pool
    onboarding: Onboarding
    owner_role: str
    serve_role: str

    async def healthy(self) -> bool:
        try:
            owner_role = await self.pool.fetchval("select current_user")
            async with workspace_tx() as connection:
                serve_role = (await connection.execute(sa.text("select current_user"))).scalar_one()
        except Exception:
            logger.exception("gateway.health.failed")
            return False
        return owner_role == self.owner_role and serve_role == self.serve_role


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset — required by the gateway onboarding server")
    return value


def _invite_required() -> bool:
    """New-workspace invites gate signup unless a deploy explicitly opts out (local dev). Unset =
    required, so forgetting the knob never opens signup; garbage fails loud, never defaults."""
    match os.environ.get(INVITE_REQUIRED_ENV, "true").strip().lower():
        case "true" | "1":
            return True
        case "false" | "0":
            return False
        case other:
            raise RuntimeError(f"{INVITE_REQUIRED_ENV}={other!r} is not a boolean (true/false)")


def _dsn_role(dsn: str) -> str:
    role = sa.engine.make_url(dsn).username
    if role is None:
        raise RuntimeError("database DSN has no role")
    return role


async def _request_body(request: Request) -> str:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk[: MAX_BODY_BYTES + 1 - len(body)])
        if len(body) > MAX_BODY_BYTES:
            raise _RequestInputError("request body is too large")
    return bytes(body).decode("utf-8", "replace").strip()


def gateway_app() -> FastAPI:
    state: GatewayState | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Every environment read precedes `init_db`, so a misconfigured deploy fails startup before
        the shared engine exists and never leaves one half-initialized. The Slack Connect inviter
        then runs as a task beside the request path, never inside it: onboarding resolves a
        workspace and signs the member in whether or not Slack is reachable."""
        nonlocal state
        owner_url = owner_dsn()
        serve_url = serve_dsn()
        pool = await asyncpg.create_pool(
            dsn=owner_url, min_size=GATEWAY_POOL_MIN_SIZE, max_size=GATEWAY_POOL_MAX_SIZE
        )
        store = OnboardStore(pool=pool)
        await store.ensure_table()
        invites = InviteCodes(pool=pool)
        await invites.ensure_table()
        await ensure_delivery_table(pool)
        invite_required = _invite_required()
        if not invite_required:
            logger.warning("gateway.invite_gate.disabled")
        inviter = slack_connect_from_env(pool)
        if inviter is None:
            logger.info("gateway.slack_connect.disabled")
        state = GatewayState(
            pool=pool,
            onboarding=Onboarding(
                claims=ClaimWorkflow(
                    store=store,
                    email_policy=WorkEmailPolicy(),
                    email_sender=email_sender_from_env(),
                ),
                store=store,
                workspaces=SharedWorkspaces(
                    workspace_url=_require_env(WORKSPACE_BASE_URL_ENV), pool=pool
                ),
                invites=invites,
                token_secret=_require_env(TOKEN_SECRET_ENV),
                apex_host=public_apex_host(),
                invite_required=invite_required,
            ),
            owner_role=_dsn_role(owner_url),
            serve_role=_dsn_role(serve_url),
        )
        init_db(serve_url)
        deliveries = None if inviter is None else asyncio.create_task(inviter.run())
        try:
            yield
        finally:
            if deliveries is not None:
                deliveries.cancel()
                await asyncio.gather(deliveries, return_exceptions=True)
            state = None
            await dispose_db()
            await pool.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> Response:
        if state is None or not await state.healthy():
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/ufo")
    async def serve_script() -> Response:
        return PlainTextResponse(STAMPED_SCRIPT, media_type=SHELLSCRIPT_MEDIA_TYPE)

    @app.get("/fleet")
    async def fleet() -> Response:
        assert state is not None
        craft = await state.pool.fetchval("select count(*) from workspace")
        return JSONResponse({"craft": craft})

    @app.get("/login")
    async def login() -> Response:
        return HTMLResponse(LOGIN_PAGE)

    @app.post("/v1/onboard/web")
    async def onboard_web(request: Request) -> Response:
        assert state is not None
        session = request.headers.get("x-ufo-session")
        if not session:
            return JSONResponse({"error": "x-ufo-session header is required"}, status_code=400)
        try:
            if len(session.encode()) > MAX_SESSION_BYTES:
                raise _RequestInputError("session is too long")
            body = await _request_body(request)
            payload = await state.onboarding.advance(WEB_CHANNEL, session, body, b"")
        except _RequestInputError as error:
            payload = render(directive("say", str(error)), directive("exit", "1"))
        except Exception:
            logger.exception("onboard.failed channel=%s", WEB_CHANNEL)
            payload = render(directive("say", "onboarding failed"), directive("exit", "1"))
        return JSONResponse({"directives": parse_directives(payload)})

    @app.post("/v1/onboard/{channel}")
    async def onboard(channel: str, request: Request) -> Response:
        assert state is not None
        session = request.headers.get("x-ufo-session")
        install = first_run_install(request.headers)
        if not session:
            return PlainTextResponse(
                render(
                    install,
                    directive("say", "x-ufo-session header is required"),
                    directive("exit", "1"),
                ),
                media_type="text/plain",
            )
        try:
            if len(channel.encode()) > MAX_CHANNEL_BYTES:
                raise _RequestInputError("channel is too long")
            if len(session.encode()) > MAX_SESSION_BYTES:
                raise _RequestInputError("session is too long")
            body = await _request_body(request)
            payload = await state.onboarding.advance(channel, session, body, install)
        except _RequestInputError as error:
            payload = render(install, directive("say", str(error)), directive("exit", "1"))
        except Exception:
            logger.exception("onboard.failed channel=%s", channel)
            payload = render(install, directive("say", "onboarding failed"), directive("exit", "1"))
        return PlainTextResponse(payload, media_type="text/plain")

    return app


app = gateway_app()
