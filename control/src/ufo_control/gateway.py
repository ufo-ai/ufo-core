"""The apex onboarding server — a control-plane role, not a tenant workspace.

`GET /ufo` serves the version-stamped POSIX client; `POST /v1/onboard/{channel}` drives the sign-in
screen as directives. `GET /` and `POST /v1/onboard/web` present the identical machine as a
self-contained web portal (the same directives returned as JSON; see `gateway_web`). The state is
the `onboard_claim` row keyed by the client's session, so each POST advances the same claim: no
email → ask; code pending → verify; verified → resolve to a workspace and sign in. The claim ledger
is written as the Postgres owner (`onboard_claim` is a platform record, no RLS).

Resolution has two tiers, picked by `UFO_ONBOARD_TIER`:

    shared (default)  → `SharedWorkspaces` ensures the org's workspace ROW in the shared database
                        and adds the member as owner, under `ws(workspace_id)` as the RLS-subject
                        serve role — no Tenant CR, no poll, no subdomain; signs in immediately.
    enterprise        → `JoinOrProvision` applies a `Tenant` CR through the same `KubeClient` the
                        operator uses, streaming provisioning across the client's polls (a `status`
                        + `poll` each pass until Ready) before joining the member.

Copy-adapted from metalcraft's `gateway_plugin.handle_onboard_ufo`."""

import hashlib
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from ufo.db import dispose_db, init_db

from ufo_control.gateway_claim import ClaimError, ClaimWorkflow
from ufo_control.gateway_directives import PROMPT, directive, first_run_install, render
from ufo_control.gateway_email import WorkEmailError, WorkEmailPolicy, email_sender_from_env
from ufo_control.gateway_provision import (
    DeployTarget,
    JoinOrProvision,
    TooManyTenantsForDomain,
)
from ufo_control.gateway_shared import SharedWorkspaces, serve_dsn
from ufo_control.gateway_store import OnboardClaim, OnboardStore
from ufo_control.gateway_token import TOKEN_SECRET_ENV, mint_token
from ufo_control.gateway_web import PORTAL_PAGE, WEB_CHANNEL, parse_directives
from ufo_control.kube import KubeClient
from ufo_control.members import owner_dsn

logger = logging.getLogger(__name__)

ONBOARD_TIER_ENV = "UFO_ONBOARD_TIER"
SHARED_TIER = "shared"
ENTERPRISE_TIER = "enterprise"
BASE_DOMAIN_ENV = "UFO_BASE_DOMAIN"
BUNDLE_IMAGE_ENV = "UFO_BUNDLE_IMAGE"
PUBLIC_BASE_URL_ENV = "UFO_PUBLIC_BASE_URL"
DEFAULT_PUBLIC_BASE_URL = "https://flyingobject.ai"
SCRIPT_URL_DEFAULT = 'UFO_URL="${UFO_URL:-https://flyingobject.ai}"'

POLL_SECONDS = "2"
SHELLSCRIPT_MEDIA_TYPE = "text/x-shellscript"

# The client is read once at import (off the event loop); the version stamp and the apex URL default
# are substituted then, since both are process-level, never per request.
_CLIENT_SCRIPT = Path(__file__).parent / "client" / "ufo"


def _stamp_script(text: str) -> str:
    """Pin the served script to the running backend: substitute the sha1 version and the apex URL
    default (from UFO_PUBLIC_BASE_URL) so a curl of testing vs prod boards its own backend, not a
    hardcoded apex."""
    version = hashlib.sha1(text.encode()).hexdigest()[:12]
    base_url = os.environ.get(PUBLIC_BASE_URL_ENV, DEFAULT_PUBLIC_BASE_URL)
    stamped = text.replace("UFO_SCRIPT_VERSION=dev", f"UFO_SCRIPT_VERSION={version}", 1)
    return stamped.replace(SCRIPT_URL_DEFAULT, f'UFO_URL="${{UFO_URL:-{base_url}}}"', 1)


STAMPED_SCRIPT = _stamp_script(_CLIENT_SCRIPT.read_text())


@dataclass(frozen=True)
class Onboarding:
    """The claim state machine, tier-agnostic through verification, then resolving through whichever
    tier the `resolver` is: a `SharedWorkspaces` signs in on the spot; a `JoinOrProvision` may
    stream provisioning across polls first. The resolver's type IS the tier — the composition root
    builds exactly one."""

    claims: ClaimWorkflow
    store: OnboardStore
    resolver: SharedWorkspaces | JoinOrProvision
    token_secret: str

    async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes:
        claim = await self.store.live_claim(channel, session)
        if claim is None:
            return await self._collect_email(channel, session, body, install)
        if claim.verified_at is None:
            return await self._verify_code(claim, body, install)
        return await self._resolve(claim, install)

    async def _collect_email(self, channel: str, session: str, body: str, install: bytes) -> bytes:
        if not body:
            return render(
                install,
                directive("say", "u f o · flyingobject.ai"),
                directive("say", "sign in to board"),
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
        return await self._resolve(claim, install)

    async def _resolve(self, claim: OnboardClaim, install: bytes) -> bytes:
        match self.resolver:
            case SharedWorkspaces() as shared:
                workspace_id = await shared.ensure(claim.email_domain, claim.email)
                await self.store.complete(claim.claim_id, None, workspace_id)
                return self._signed_in(claim.email, workspace_id, shared.workspace_url, install)
            case JoinOrProvision() as enterprise:
                if claim.tenant_name is None:
                    return await self._decide(enterprise, claim, install)
                return await self._poll_provisioning(enterprise, claim, install)

    async def _decide(
        self, enterprise: JoinOrProvision, claim: OnboardClaim, install: bytes
    ) -> bytes:
        tenants = await enterprise.tenants_for_domain(claim.email_domain)
        if len(tenants) > 1:
            raise TooManyTenantsForDomain(
                f"domain {claim.email_domain} maps to {len(tenants)} workspaces — contact support"
            )
        if len(tenants) == 1:
            name = tenants[0].tenant
            workspace_id = await enterprise.join(name, claim.email)
            await self.store.complete(claim.claim_id, name, workspace_id)
            url = enterprise.workspace_url(name)
            return self._signed_in(claim.email, workspace_id, url, install)
        name = await enterprise.provision(claim.email_domain, claim.email)
        await self.store.start_provisioning(claim.claim_id, name)
        return render(
            install,
            directive("say", "provisioning your workspace…"),
            directive("status", "creating tenant"),
            directive("poll", POLL_SECONDS),
        )

    async def _poll_provisioning(
        self, enterprise: JoinOrProvision, claim: OnboardClaim, install: bytes
    ) -> bytes:
        assert claim.tenant_name is not None
        view = await enterprise.tenant_status(claim.tenant_name)
        if view.phase == "Ready" and view.workspace_id:
            await self.store.complete(claim.claim_id, claim.tenant_name, view.workspace_id)
            url = enterprise.workspace_url(claim.tenant_name)
            return self._signed_in(claim.email, view.workspace_id, url, install)
        if view.phase == "Failed":
            return render(
                install,
                directive("say", "provisioning failed — please try again later"),
                directive("exit", "1"),
            )
        return render(
            install,
            directive("status", f"provisioning ({view.phase.lower()})…"),
            directive("poll", POLL_SECONDS),
        )

    def _signed_in(
        self, email: str, workspace_id: str, workspace_url: str, install: bytes
    ) -> bytes:
        # The token travels only in the machine-consumed `token` directive (the client writes it to
        # a chmod-600 credentials file, never printing it). The web portal builds the tokened
        # /surface/setup handoff link from this token client-side; the terminal never echoes it.
        token = mint_token(self.token_secret, workspace_id, email)
        return render(
            install,
            directive("token", token),
            directive("workspace", workspace_url),
            directive("say", f"✓ signed in as {email}"),
            directive("ask", PROMPT),
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset — required by the gateway onboarding server")
    return value


def _tier() -> str:
    tier = os.environ.get(ONBOARD_TIER_ENV, SHARED_TIER)
    if tier not in (SHARED_TIER, ENTERPRISE_TIER):
        raise RuntimeError(
            f"{ONBOARD_TIER_ENV}={tier!r} is not a known tier (set it to {SHARED_TIER!r} or "
            f"{ENTERPRISE_TIER!r})"
        )
    return tier


def _resolver(tier: str, state: dict[str, Any]) -> SharedWorkspaces | JoinOrProvision:
    if tier == SHARED_TIER:
        return SharedWorkspaces(
            workspace_url=os.environ.get(PUBLIC_BASE_URL_ENV, DEFAULT_PUBLIC_BASE_URL)
        )
    return JoinOrProvision(
        kube=state["kube"],
        target=DeployTarget(
            base_domain=_require_env(BASE_DOMAIN_ENV),
            bundle_image=_require_env(BUNDLE_IMAGE_ENV),
        ),
    )


def _onboarding(state: dict[str, Any]) -> Onboarding:
    """One request's flow over the lifespan's store and the tier's resolver — assembled per request
    so a misconfigured env fails the request loudly, never the boot."""
    return Onboarding(
        claims=ClaimWorkflow(
            store=state["store"],
            email_policy=WorkEmailPolicy(),
            email_sender=email_sender_from_env(),
        ),
        store=state["store"],
        resolver=_resolver(state["tier"], state),
        token_secret=_require_env(TOKEN_SECRET_ENV),
    )


def gateway_app() -> FastAPI:
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        tier = _tier()
        pool = await asyncpg.create_pool(dsn=owner_dsn())
        store = OnboardStore(pool=pool)
        await store.ensure_table()
        state["store"] = store
        state["tier"] = tier
        if tier == SHARED_TIER:
            init_db(serve_dsn())
        else:
            state["kube"] = KubeClient.from_env()
        try:
            yield
        finally:
            if tier == SHARED_TIER:
                await dispose_db()
            else:
                await state["kube"].close()
            await pool.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def portal() -> Response:
        return HTMLResponse(PORTAL_PAGE)

    @app.get("/ufo")
    async def serve_script() -> Response:
        return PlainTextResponse(STAMPED_SCRIPT, media_type=SHELLSCRIPT_MEDIA_TYPE)

    @app.post("/v1/onboard/web")
    async def onboard_web(request: Request) -> Response:
        """The portal page's wire: the same machine as the terminal channel, its directives
        returned as JSON for the page's renderer instead of tab-separated lines for the shell."""
        session = request.headers.get("x-ufo-session")
        if not session:
            return JSONResponse({"error": "x-ufo-session header is required"}, status_code=400)
        body = (await request.body()).decode("utf-8", "replace").strip()
        flow = _onboarding(state)  # env misconfig raises here → 500, distinct from a flow error
        try:
            payload = await flow.advance(WEB_CHANNEL, session, body, b"")
        except Exception as error:
            logger.exception("onboard.failed channel=%s", WEB_CHANNEL)
            payload = render(directive("say", f"error: {error}"), directive("exit", "1"))
        return JSONResponse({"directives": parse_directives(payload)})

    @app.post("/v1/onboard/{channel}")
    async def onboard(channel: str, request: Request) -> Response:
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
        body = (await request.body()).decode("utf-8", "replace").strip()
        flow = _onboarding(state)  # env misconfig raises here → 500, distinct from a flow error
        try:
            payload = await flow.advance(channel, session, body, install)
        except Exception as error:
            logger.exception("onboard.failed channel=%s", channel)
            payload = render(install, directive("say", f"error: {error}"), directive("exit", "1"))
        return PlainTextResponse(payload, media_type="text/plain")

    return app


app = gateway_app()
