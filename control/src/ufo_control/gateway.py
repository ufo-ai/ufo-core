"""The hosted onboarding server and shared-workspace resolver."""

import asyncio
import hashlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from urllib.parse import urlencode

import asyncpg
import sqlalchemy as sa
from fastapi import FastAPI, Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.surface import OPERATOR_EMAIL_DOMAIN
from ufo.sandbox.terminal import client_program_bundle
from ufo.sdk.http import set_session_cookie

from ufo_control.gateway_claim import ClaimError, ClaimWorkflow, Verifier
from ufo_control.gateway_directives import PROMPT, directive, first_run_install, render
from ufo_control.gateway_email import (
    DEFAULT_PUBLIC_BASE_URL,
    PUBLIC_BASE_URL_ENV,
    WorkEmailError,
    WorkEmailPolicy,
    public_apex_host,
)
from ufo_control.gateway_invite import (
    InviteAccepted,
    InviteCodes,
    InviteConsumed,
    InviteExpired,
)
from ufo_control.gateway_shared import EnsuredWorkspace, SharedWorkspaces, serve_dsn
from ufo_control.gateway_slack_connect import slack_connect_from_env
from ufo_control.gateway_store import OnboardClaim, OnboardStore
from ufo_control.gateway_token import TOKEN_SECRET_ENV, mint_token
from ufo_control.gateway_web import (
    LOGIN_PAGE,
    ONBOARD_SESSION_COOKIE,
    WEB_CHANNEL,
    parse_directives,
)
from ufo_control.gateway_workos import (
    AUTH_CALLBACK_PATH,
    AUTH_CONSOLE_PATH,
    AUTH_START_PATH,
    SIGN_IN_FAILED,
    AuthCarry,
    VerificationError,
    console_signin_page,
    open_session,
    pack_state,
    seal_session,
    unpack_state,
    workos_console_mode,
    workos_verifier_from_env,
)
from ufo_control.rls import owner_dsn
from ufo_control.schema import require_control_schema

logger = logging.getLogger(__name__)

WORKSPACE_BASE_URL_ENV = "UFO_WORKSPACE_BASE_URL"
INVITE_REQUIRED_ENV = "UFO_INVITE_REQUIRED"
DEBUG_SURFACE_PATH = "/surface/debug"
FIRST_MOVE_PROMPT = "What first?"
BILLING_CHOICE = "Set up billing"
TOUR_CHOICE = "Show me what you can do"
WORKSPACE_PROMPT = "Choose a workspace:"
SCRIPT_URL_DEFAULT = 'UFO_URL="${UFO_URL:-https://flyingobject.ai}"'
SHELLSCRIPT_MEDIA_TYPE = "text/x-shellscript"
CLIENT_BIN_DIR_ENV = "UFO_CLIENT_BIN_DIR"
CLIENT_TARGETS = frozenset(
    {
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-unknown-linux-musl",
        "x86_64-pc-windows-msvc",
    }
)
MAX_CHANNEL_BYTES = 64
MAX_SESSION_BYTES = 128
ONBOARD_SESSION_BYTES = 32
MAX_BODY_BYTES = 4096
GATEWAY_POOL_MIN_SIZE = 1
GATEWAY_POOL_MAX_SIZE = 4

_CLIENT_SCRIPT = Path(__file__).parent / "client" / "ufo"
_PROGRAM_MARKER = "# ufo:programs"


class _RequestInputError(ValueError):
    pass


def _stamp_script(text: str) -> str:
    bundled = text.replace(_PROGRAM_MARKER, client_program_bundle(), 1)
    version = hashlib.sha1(bundled.encode()).hexdigest()[:12]
    base_url = os.environ.get(PUBLIC_BASE_URL_ENV, DEFAULT_PUBLIC_BASE_URL)
    stamped = bundled.replace("UFO_SCRIPT_VERSION=dev", f"UFO_SCRIPT_VERSION={version}", 1)
    return stamped.replace(SCRIPT_URL_DEFAULT, f'UFO_URL="${{UFO_URL:-{base_url}}}"', 1)


STAMPED_SCRIPT = _stamp_script(_CLIENT_SCRIPT.read_text())


@dataclass(frozen=True)
class Onboarding:
    claims: ClaimWorkflow
    store: OnboardStore
    workspaces: SharedWorkspaces
    invites: InviteCodes
    verifier: Verifier
    token_secret: str
    apex_host: str
    invite_required: bool

    async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes:
        """One machine for every surface. A session with no claim collects the work email and the
        code WorkOS mailed for it — the browser on our own `/login` page, exactly as the terminal
        does — a verified claim resolves a workspace and mints the bearer. The browser reaches a
        verified claim either that way or from the Google hop the callback stamps; nothing outside
        this machine ever collects the address, so the work-email policy runs before any code."""
        claim = await self.store.live_claim(channel, session)
        if claim is None:
            return await self._collect_email(channel, session, body, install)
        if claim.verified_at is None:
            return await self._verify_code(claim, body, install)
        return await self._resolve(claim, body, install)

    async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes:
        """The email step both surfaces share: an empty turn asks for the address, a submitted one
        validates the work-email policy through `ClaimWorkflow.start` and, only once it passes, has
        WorkOS mail the code — so a denylisted address is refused with no code sent. Home-realm SSO
        discovery (routing a domain that has a WorkOS connection to its organization's authorize
        instead of Magic Auth) branches here, before the code is sent; it is not built yet."""
        if not body:
            return render(
                install,
                directive("say", "ufo · flyingobject.ai"),
                directive("ask", "Enter your work email:"),
            )
        try:
            await self.claims.start(body, channel, session)
        except (WorkEmailError, ClaimError, ValueError) as error:
            return render(
                install, directive("say", str(error)), directive("ask", "Enter your work email:")
            )
        return render(
            install,
            directive("say", f"We emailed a code to {body.strip().lower()}"),
            directive("ask", "Enter the code:"),
        )

    async def _verify_code(self, claim: OnboardClaim, body: str, install: bytes) -> bytes:
        try:
            await self.claims.verify(claim, body)
        except ClaimError as error:
            current = await self.store.live_claim(claim.surface, claim.surface_ref)
            if current is not None and current.verified_at is not None:
                return await self._resolve(current, "", install)
            prompt = "Enter the code:" if current is not None else "Enter your work email:"
            return render(install, directive("say", str(error)), directive("ask", prompt))
        return await self._resolve(claim, "", install)

    async def _resolve(self, claim: OnboardClaim, body: str, install: bytes) -> bytes:
        choices = await self.workspaces.choices(claim.email_domain, claim.email)
        if not choices:
            if self.invite_required:
                refusal = await self._invite_gate(claim, install)
                if refusal is not None:
                    return refusal
            ensured = await self.workspaces.create(claim.email_domain, claim.email)
        else:
            create_available = claim.invite_id is not None or await self.invites.available(
                claim.email_domain
            )
            if len(choices) == 1 and not create_available:
                ensured = await self.workspaces.join(choices[0], claim.email_domain, claim.email)
            else:
                create_label = f"Create {claim.email_domain} workspace"
                options = [choice.label for choice in choices]
                if create_available:
                    options.append(create_label)
                if body == create_label and create_available:
                    refusal = await self._invite_gate(claim, install)
                    if refusal is not None:
                        return refusal
                    ensured = await self.workspaces.create(claim.email_domain, claim.email)
                else:
                    selected = next((choice for choice in choices if choice.label == body), None)
                    if selected is None:
                        return render(
                            install,
                            directive("say", "Choose a listed workspace.") if body else b"",
                            directive("choose", WORKSPACE_PROMPT, *options),
                        )
                    ensured = await self.workspaces.join(selected, claim.email_domain, claim.email)
        await self.store.complete(claim.claim_id, ensured.workspace_id)
        return self._signed_in(claim, ensured, install)

    async def _invite_gate(self, claim: OnboardClaim, install: bytes) -> bytes | None:
        """A refusal screen, or `None` when the flow may open the workspace. The verified email
        domain is the whole answer: a live grant for it opens the workspace with nothing to type,
        and every refusal ends the session rather than prompting, because the member holds no
        secret that could change the outcome. The claim keeps its verified email, so re-running the
        installer once a grant lands resolves the same claim."""
        if claim.invite_id is not None:
            return None
        domain = claim.email_domain
        match await self.invites.redeem(domain, claim.claim_id):
            case InviteAccepted():
                return None
            case InviteExpired(expires_at=expires_at):
                expired = expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
                return render(
                    install,
                    directive("say", f"The invite for {domain} expired {expired} UTC."),
                    directive("say", "Reply to your invite email for a new one."),
                    directive("exit", "0"),
                )
            case InviteConsumed():
                return render(
                    install,
                    directive("say", f"The invite for {domain} was already used."),
                    directive("say", "Contact us if you cannot sign in."),
                    directive("exit", "0"),
                )
            case _:
                return render(
                    install,
                    directive("say", f"{domain} has no invite."),
                    directive(
                        "say",
                        "Join the waitlist: "
                        f"curl https://{self.apex_host}/waitlist -d email={claim.email}",
                    ),
                    directive("exit", "0"),
                )

    def _signed_in(self, claim: OnboardClaim, ensured: EnsuredWorkspace, install: bytes) -> bytes:
        """The signed-in cap: token and workspace for every member, plus the `debugger` directive
        — the operator session debugger's base URL — only when the claim's channel-verified email
        domain is the operator's. The gate is server-side policy; every renderer (the terminal
        client drops unknown verbs) simply carries or ignores the extra line.

        A terminal owner caps on `choose` rather than `ask`, so setting up billing costs one
        selection: the workspace directive has already landed, so whichever option they pick
        posts to `/surface/ufo` as their first message and the agent drives it from there. A
        joined teammate caps on the ordinary prompt — billing is not theirs to set up. The web
        renderer ends on its signed-in card rather than a prompt, so it is never handed a menu
        it cannot drive."""
        token = mint_token(self.token_secret, ensured.workspace_id, claim.email)
        operator = claim.email_domain == OPERATOR_EMAIL_DOMAIN
        return render(
            install,
            directive("token", token),
            directive("workspace", self.workspaces.workspace_url),
            directive("debugger", f"{self.workspaces.workspace_url}{DEBUG_SURFACE_PATH}")
            if operator
            else b"",
            directive("say", f"Signed in: {claim.email}"),
            directive("choose", FIRST_MOVE_PROMPT, BILLING_CHOICE, TOUR_CHOICE)
            if ensured.admin and claim.surface != WEB_CHANNEL
            else directive("ask", PROMPT),
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
            raise _RequestInputError("Request body is too large.")
    return bytes(body).decode("utf-8", "replace").strip()


def _onboard_session(request: Request, secret: str) -> str | None:
    """The sealed `__Host-ufo_onboard` session the request carries. The `__Host-` prefix makes the
    cookie host-only and un-plantable across hosts, so the browser holds at most one; reading every
    cookie of that name and honoring the one that opens under our signature is belt against a
    forged or duplicate value. The sealed value is the id the claim is keyed by, returned as-is;
    only a value that opens under our signature is trusted, and a request carrying none is answered
    with a freshly minted session instead."""
    header = request.headers.get("cookie", "")
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == ONBOARD_SESSION_COOKIE and open_session(value, secret) is not None:
            return value
    return None


def gateway_app() -> FastAPI:
    state: GatewayState | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Every environment read precedes `init_db`, so a misconfigured deploy fails startup on the
        environment rather than on the first request. The schema the deploy shaped is a precondition
        of the same kind — a replica issues no DDL, so an absent
        ledger fails startup naming the verb that shapes it. The Slack Connect inviter
        then runs as a task beside the request path, never inside it: onboarding resolves a
        workspace and signs the member in whether or not Slack is reachable."""
        nonlocal state
        owner_url = owner_dsn()
        serve_url = serve_dsn()
        verifier = workos_verifier_from_env()
        await require_control_schema(owner_url)
        pool = await asyncpg.create_pool(
            dsn=owner_url, min_size=GATEWAY_POOL_MIN_SIZE, max_size=GATEWAY_POOL_MAX_SIZE
        )
        store = OnboardStore(pool=pool)
        invites = InviteCodes(pool=pool)
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
                    store=store, email_policy=WorkEmailPolicy(), verifier=verifier
                ),
                store=store,
                workspaces=SharedWorkspaces(
                    workspace_url=_require_env(WORKSPACE_BASE_URL_ENV), pool=pool
                ),
                invites=invites,
                verifier=verifier,
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

    @app.get("/ufo/bin/{target}")
    async def client_binary(target: str) -> Response:
        """The native terminal client for one build target, from the deploy's binary directory —
        an unconfigured deploy answers the same 404 an unknown target does."""
        directory = os.environ.get(CLIENT_BIN_DIR_ENV, "")
        refusal = PlainTextResponse(f"no client binary for {target}", status_code=404)
        if target not in CLIENT_TARGETS or not directory:
            return refusal
        name = "ufo.exe" if target == "x86_64-pc-windows-msvc" else "ufo"
        binary = Path(directory) / target / name
        if not binary.is_file():
            return refusal
        return FileResponse(binary, media_type="application/octet-stream", filename=name)

    @app.get("/fleet")
    async def fleet() -> Response:
        assert state is not None
        craft = await state.pool.fetchval("select count(*) from workspace")
        return JSONResponse({"craft": craft})

    @app.get("/login")
    async def login() -> Response:
        return HTMLResponse(LOGIN_PAGE)

    @app.get(AUTH_START_PATH)
    async def auth_start(request: Request) -> Response:
        """The `Continue with Google` button's target: it mints the onboarding session, binds it to
        this browser as the cookie the page cannot read (never taken from the query), and 302s to
        WorkOS with `provider=GoogleOAuth`, so WorkOS goes straight to Google with no hosted page.
        The id that keys the claim the callback verifies reaches nothing but the browser that signed
        in, so no one else can name a session to have a verified email written under. Packing the
        carry and unpacking it again is the validation: a conversation or artifact the query
        invented is dropped here, under the same rules the callback reads it back by, so the state
        carries only what will be honored."""
        assert state is not None
        secret = state.onboarding.token_secret
        session = seal_session(secrets.token_urlsafe(ONBOARD_SESSION_BYTES), secret)
        carry = unpack_state(
            pack_state(
                AuthCarry(
                    session=session,
                    conversation=request.query_params.get("c"),
                    artifact=request.query_params.get("a"),
                ),
                secret,
            ),
            secret,
        )
        response = RedirectResponse(
            state.onboarding.verifier.authorization_url(pack_state(carry, secret)), status_code=302
        )
        set_session_cookie(response, ONBOARD_SESSION_COOKIE, session, samesite="lax")
        return response

    if workos_console_mode():

        @app.get(AUTH_CONSOLE_PATH)
        async def auth_console(request: Request) -> Response:
            """The local stand-in for the Google hop, mounted only under `WORKOS_MODE=console`: the
            dev enters a work email that the callback reads as the code. The cookie the start path
            set still binds the return, so the walk past this page is a real return's own."""
            return HTMLResponse(console_signin_page(request.query_params.get("state", "")))

    @app.get(AUTH_CALLBACK_PATH)
    async def auth_callback(request: Request) -> Response:
        """The Google hop's return, honored only in the browser that left: the state has to be one
        this gateway signed, and the session it names has to be the one the start path bound as the
        cookie, so a state a caller wrote — or one of ours replayed anywhere else — verifies nothing
        and writes no claim under a session someone chose. The email the Google account carries
        passes the same work-email policy a typed address does, so a personal `@gmail.com` Google
        account is refused; the refusal rides back to the page as a sentence rather than a status,
        the page where the member reads it. A callback for a session that already holds a claim
        resolves that claim, so a repeated return signs the same member in."""
        assert state is not None
        code = request.query_params.get("code", "")
        try:
            carry = unpack_state(
                request.query_params.get("state", ""), state.onboarding.token_secret
            )
        except ValueError:
            return PlainTextResponse("The sign-in link is not valid.", status_code=400)
        query: list[tuple[str, str]] = []
        if carry.conversation:
            query.append(("c", carry.conversation))
        if carry.artifact:
            query.append(("a", carry.artifact))
        bound = _onboard_session(request, state.onboarding.token_secret)
        try:
            if (
                not code
                or bound is None
                or not secrets.compare_digest(bound.encode(), carry.session.encode())
            ):
                raise VerificationError(SIGN_IN_FAILED)
            email = await state.onboarding.verifier.exchange(code)
            await state.onboarding.claims.admit_verified(email, WEB_CHANNEL, carry.session)
        except (VerificationError, WorkEmailError, ClaimError) as error:
            query.insert(0, ("error", str(error)))
        landing = f"/login?{urlencode(query)}" if query else "/login"
        return RedirectResponse(landing, status_code=303)

    @app.post("/v1/onboard/web")
    async def onboard_web(request: Request) -> Response:
        """The page's session is the `__Host-ufo_onboard` cookie the gateway mints and seals
        server-side. Two things keep a verified email bound to the browser that earned it, so a
        planted value can never key its claim. The `__Host-` prefix is host-only by the cookie
        the browser enforces: it refuses to set such a cookie with a `Domain`, and no sibling
        `<label>.flyingobject.ai` can write the app host's copy — so the value cannot be planted
        across hosts at all. And a presented cookie is trusted only to *continue* a claim it already
        keys: a claim is only ever started under a session freshly minted here, so even a validly
        sealed value a caller obtained by asking (the `Set-Cookie` of a bare turn) is discarded and
        re-minted unless it already stands behind a live claim. The cookie is `HttpOnly`, `Secure`,
        `SameSite=lax`, `Path=/`, no `max_age`; a Google return has already bound its claim at the
        start path. Nothing a client sends can name the session a verified email is written
        under."""
        assert state is not None
        secret = state.onboarding.token_secret
        store = state.onboarding.store
        session = _onboard_session(request, secret)
        minted = ""
        if session is None or await store.live_claim(WEB_CHANNEL, session) is None:
            session = seal_session(secrets.token_urlsafe(ONBOARD_SESSION_BYTES), secret)
            minted = session
        try:
            body = await _request_body(request)
            payload = await state.onboarding.advance(WEB_CHANNEL, session, body, b"")
        except _RequestInputError as error:
            payload = render(directive("say", str(error)), directive("exit", "1"))
        except Exception:
            logger.exception("onboard.failed channel=%s", WEB_CHANNEL)
            payload = render(directive("say", "Onboarding failed."), directive("exit", "1"))
        response = JSONResponse({"directives": parse_directives(payload)})
        if minted:
            set_session_cookie(response, ONBOARD_SESSION_COOKIE, minted, samesite="lax")
        return response

    @app.post("/v1/onboard/{channel}")
    async def onboard(channel: str, request: Request) -> Response:
        assert state is not None
        session = request.headers.get("x-ufo-session")
        install = first_run_install(request.headers)
        if not session:
            return PlainTextResponse(
                render(
                    install,
                    directive("say", "x-ufo-session header is required."),
                    directive("exit", "1"),
                ),
                media_type="text/plain",
            )
        try:
            if len(channel.encode()) > MAX_CHANNEL_BYTES:
                raise _RequestInputError("Channel is too long.")
            if len(session.encode()) > MAX_SESSION_BYTES:
                raise _RequestInputError("Session is too long.")
            body = await _request_body(request)
            payload = await state.onboarding.advance(channel, session, body, install)
        except _RequestInputError as error:
            payload = render(install, directive("say", str(error)), directive("exit", "1"))
        except Exception:
            logger.exception("onboard.failed channel=%s", channel)
            payload = render(
                install, directive("say", "Onboarding failed."), directive("exit", "1")
            )
        return PlainTextResponse(payload, media_type="text/plain")

    return app


app = gateway_app()
