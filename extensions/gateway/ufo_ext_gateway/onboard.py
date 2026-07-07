"""The apex onboarding backend: `GET /ufo` serves the client, `POST /onboard/{channel}` drives the
sign-in screen as directives.

The state is the `onboard_claim` row keyed by the shell's session, so each POST advances the same
claim: no email → ask; code pending → verify; verified → resolve the tenant (join or provision) and
sign in. Provisioning is streamed across the client's polls — a `status` + `poll` each pass until
the tenant reports Ready — never one blocked request. Copy-adapted from metalcraft's
`gateway_plugin.handle_onboard_ufo`, with the join-or-provision moved onto the control-plane API."""

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ufo.sdk.context import ExtensionContext
from ufo.sdk.http import PlainTextResponse, Request, Response
from ufo_ext_gateway.claim import ClaimError, ClaimWorkflow
from ufo_ext_gateway.directives import PROMPT, directive, first_run_install, render
from ufo_ext_gateway.email_domain import WorkEmailError, WorkEmailPolicy
from ufo_ext_gateway.email_sender import email_sender_from_env
from ufo_ext_gateway.provision import (
    DeployTarget,
    JoinOrProvision,
    TooManyTenantsForDomain,
)
from ufo_ext_gateway.store import OnboardClaim, OnboardStore
from ufo_ext_gateway.token import TOKEN_SECRET_ENV, mint_token

logger = logging.getLogger(__name__)

CONTROL_API_URL_ENV = "UFO_CONTROL_API_URL"
BASE_DOMAIN_ENV = "UFO_BASE_DOMAIN"
BUNDLE_IMAGE_ENV = "UFO_BUNDLE_IMAGE"
SANDBOX_IMAGE_ENV = "UFO_SANDBOX_IMAGE"
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


async def serve_script(ctx: ExtensionContext, request: Request) -> Response:
    return PlainTextResponse(STAMPED_SCRIPT, media_type=SHELLSCRIPT_MEDIA_TYPE)


async def onboard(ctx: ExtensionContext, request: Request) -> Response:
    channel = request.path_params["channel"]
    session = request.headers.get("x-ufo-session")
    install = first_run_install(request.headers)
    if not session:
        return _directives_response(
            render(install, directive("say", "x-ufo-session header is required"), _exit())
        )
    body = (await request.body()).decode("utf-8", "replace").strip()
    store = OnboardStore(ctx)
    base_domain = _require_env(BASE_DOMAIN_ENV)
    async with httpx.AsyncClient(base_url=_require_env(CONTROL_API_URL_ENV)) as http:
        flow = Onboarding(
            claims=ClaimWorkflow(
                store=store,
                email_policy=WorkEmailPolicy(),
                email_sender=email_sender_from_env(),
            ),
            store=store,
            join=JoinOrProvision(
                http=http,
                target=DeployTarget(
                    base_domain=base_domain,
                    bundle_image=_require_env(BUNDLE_IMAGE_ENV),
                    sandbox_image=_require_env(SANDBOX_IMAGE_ENV),
                ),
            ),
            token_secret=_require_env(TOKEN_SECRET_ENV),
            base_domain=base_domain,
        )
        try:
            payload = await flow.advance(channel, session, body, install)
        except Exception as error:
            logger.exception("onboard.failed channel=%s", channel)
            payload = render(install, directive("say", f"error: {error}"), _exit())
    return _directives_response(payload)


@dataclass(frozen=True)
class Onboarding:
    claims: ClaimWorkflow
    store: OnboardStore
    join: JoinOrProvision
    token_secret: str
    base_domain: str

    async def advance(self, channel: str, session: str, body: str, install: bytes) -> bytes:
        claim = await self.store.live_claim(channel, session)
        if claim is None:
            return await self._collect_email(channel, session, body, install)
        if claim.verified_at is None:
            return await self._verify_code(claim, body, install)
        return await self._resolve_tenant(claim, install)

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
        return await self._resolve_tenant(claim, install)

    async def _resolve_tenant(self, claim: OnboardClaim, install: bytes) -> bytes:
        if claim.tenant_name is None:
            return await self._decide(claim, install)
        return await self._poll_provisioning(claim, install)

    async def _decide(self, claim: OnboardClaim, install: bytes) -> bytes:
        tenants = await self.join.tenants_for_domain(claim.email_domain)
        if len(tenants) > 1:
            raise TooManyTenantsForDomain(
                f"domain {claim.email_domain} maps to {len(tenants)} workspaces — contact support"
            )
        if len(tenants) == 1:
            name = tenants[0].name
            workspace_id = await self.join.join(name, claim.email)
            await self.store.complete(claim.claim_id, name, workspace_id, datetime.now(UTC))
            return self._signed_in(name, claim.email, workspace_id, install)
        name = await self.join.provision(claim.email_domain, claim.email)
        await self.store.start_provisioning(claim.claim_id, name, datetime.now(UTC))
        return render(
            install,
            directive("say", "provisioning your workspace…"),
            directive("status", "creating tenant"),
            directive("poll", POLL_SECONDS),
        )

    async def _poll_provisioning(self, claim: OnboardClaim, install: bytes) -> bytes:
        assert claim.tenant_name is not None
        view = await self.join.tenant_status(claim.tenant_name)
        if view.phase == "Ready" and view.workspace_id:
            await self.store.complete(
                claim.claim_id, claim.tenant_name, view.workspace_id, datetime.now(UTC)
            )
            return self._signed_in(claim.tenant_name, claim.email, view.workspace_id, install)
        if view.phase == "Failed":
            return render(
                install,
                directive("say", "provisioning failed — please try again later"),
                _exit(),
            )
        return render(
            install,
            directive("status", f"provisioning ({view.phase.lower()})…"),
            directive("poll", POLL_SECONDS),
        )

    def _signed_in(self, name: str, email: str, workspace_id: str, install: bytes) -> bytes:
        token = mint_token(self.token_secret, workspace_id, email)
        return render(
            install,
            directive("token", token),
            directive("workspace", f"https://{name}.{self.base_domain}"),
            directive("say", f"✓ signed in as {email}"),
            directive("ask", PROMPT),
        )


def _exit(code: str = "1") -> bytes:
    return directive("exit", code)


def _directives_response(body: bytes) -> Response:
    return PlainTextResponse(body, media_type="text/plain")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset — required by the gateway onboarding backend")
    return value
