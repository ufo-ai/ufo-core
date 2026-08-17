"""WorkOS custody of email verification: Magic Auth for the email and code both the browser and the
terminal collect on our own pages, and a Google OAuth hop (`provider=GoogleOAuth`) for the browser's
`Continue with Google`. WorkOS answers one question — does this person control this email — and no
hosted WorkOS page collects the address: our gateway does, so the work-email policy runs before any
code is mailed.

The client is the SDK's own async client, so every call is bounded and retried by it — external
uncertainty, the one legitimate case. `confirm` grades the member's code: WorkOS answers a code it
will not redeem with the OAuth `invalid_grant`, whether the digits are wrong, expired, or already
spent, so that answer is a wrong code the member may retype and the claim's own time-to-live is
what ends the session. Every other failure is a refusal the member cannot retype past.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from html import escape
from urllib.parse import quote

from workos import AsyncWorkOSClient, AuthenticationError, BadRequestError, WorkOSError
from workos.user_management import UserManagementAuthenticationProvider

logger = logging.getLogger(__name__)

WORKOS_API_KEY_ENV = "WORKOS_API_KEY"
WORKOS_CLIENT_ID_ENV = "WORKOS_CLIENT_ID"
WORKOS_REDIRECT_URI_ENV = "WORKOS_REDIRECT_URI"
WORKOS_MODE_ENV = "WORKOS_MODE"
WORKOS_MODE = "workos"
CONSOLE_MODE = "console"
AUTH_START_PATH = "/v1/onboard/auth/start"
AUTH_CALLBACK_PATH = "/v1/onboard/auth/callback"
AUTH_CONSOLE_PATH = "/v1/onboard/auth/console"
CONSOLE_CODE = "000000"
GOOGLE_PROVIDER = UserManagementAuthenticationProvider.GOOGLE_OAUTH
GRANT_REFUSED = "invalid_grant"
SIGN_IN_FAILED = "Sign-in failed. Try again."
MAX_STATE_SESSION_BYTES = 128
STATE_SEPARATOR = "."
STATE_KEY_LABEL = b"onboard-auth-state"
COOKIE_KEY_LABEL = b"onboard-session-cookie"
ARTIFACT_CARRY_PREFIX = "/artifacts/"
CONVERSATION_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class VerificationError(RuntimeError):
    """The onboarding flow renders the message to the member."""


@dataclass(frozen=True)
class AuthCarry:
    session: str
    conversation: str | None
    artifact: str | None


def pack_state(carry: AuthCarry, secret: str) -> str:
    """The OAuth `state`: the onboarding session and what the member clicked to reach sign-in, under
    a signature. The session it names is the one the callback writes a verified email under, so only
    a state this gateway packed may name one."""
    payload = {"s": carry.session, "c": carry.conversation, "a": carry.artifact}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{body}{STATE_SEPARATOR}{_state_signature(body, secret)}"


def unpack_state(raw: str, secret: str) -> AuthCarry:
    """The carry the state names, or `ValueError` when the signature is not this gateway's or the
    state names no usable session. A conversation or artifact that is not its own shape is dropped
    rather than refused: the member still signs in, landing on a new conversation instead of
    somewhere the query invented."""
    body, _, signature = raw.partition(STATE_SEPARATOR)
    if not hmac.compare_digest(signature.encode(), _state_signature(body, secret).encode()):
        raise ValueError("state carries no signature of ours")
    padded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("state is not the packed carry") from error
    if not isinstance(payload, dict):
        raise ValueError("state is not the packed carry")
    session = payload.get("s")
    if not isinstance(session, str) or not session:
        raise ValueError("state carries no session")
    if len(session.encode()) > MAX_STATE_SESSION_BYTES:
        raise ValueError("state carries an oversized session")
    conversation = payload.get("c")
    if not isinstance(conversation, str) or not CONVERSATION_SHAPE.fullmatch(conversation):
        conversation = None
    artifact = payload.get("a")
    if not isinstance(artifact, str) or not artifact.startswith(ARTIFACT_CARRY_PREFIX):
        artifact = None
    return AuthCarry(session=session, conversation=conversation, artifact=artifact)


def _state_signature(body: str, secret: str) -> str:
    """Signed under a subkey of the gateway's token secret, so a state and a member's bearer never
    come out of one key."""
    key = hmac.new(secret.encode(), STATE_KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def seal_session(session: str, secret: str) -> str:
    """The `__Host-ufo_onboard` cookie value: a minted session under a signature, so only a value
    this gateway sealed can key a claim. The sealed string is the session everywhere it is compared
    — cookie, state, and claim — never the raw id, and a value carrying no signature of ours opens
    to nothing."""
    return f"{session}{STATE_SEPARATOR}{_cookie_signature(session, secret)}"


def open_session(value: str, secret: str) -> str | None:
    """The minted id a sealed cookie names, or None when the value carries no signature of ours — so
    a planted or forged cookie is read as absent and a fresh session minted in its place, never
    trusted to key a claim."""
    session, _, signature = value.partition(STATE_SEPARATOR)
    if not session or not hmac.compare_digest(
        signature.encode(), _cookie_signature(session, secret).encode()
    ):
        return None
    return session


def _cookie_signature(session: str, secret: str) -> str:
    """Under a different subkey than the state signature, so the cookie seal and the state seal
    never come out of one key."""
    key = hmac.new(secret.encode(), COOKIE_KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(key, session.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class WorkosVerifier:
    """The one question WorkOS answers: does this person control this email."""

    client: AsyncWorkOSClient
    redirect_uri: str

    def authorization_url(self, state: str) -> str:
        """The `Continue with Google` hop: WorkOS routes `provider=GoogleOAuth` straight to Google,
        with no hosted WorkOS page in between, and returns to the callback with the code."""
        return self.client.user_management.get_authorization_url(
            provider=GOOGLE_PROVIDER, redirect_uri=self.redirect_uri, state=state
        )

    async def exchange(self, code: str) -> str:
        try:
            response = await self.client.user_management.authenticate_with_code(code=code)
        except WorkOSError as error:
            raise VerificationError(SIGN_IN_FAILED) from error
        return response.user.email.strip().lower()

    async def begin(self, email: str) -> None:
        try:
            await self.client.user_management.create_magic_auth(email=email)
        except WorkOSError as error:
            raise VerificationError("Could not send the verification code. Try again.") from error

    async def confirm(self, email: str, code: str) -> bool:
        try:
            await self.client.user_management.authenticate_with_magic_auth(code=code, email=email)
        except (BadRequestError, AuthenticationError) as error:
            if GRANT_REFUSED in (error.error, error.code):
                return False
            raise VerificationError(SIGN_IN_FAILED) from error
        except WorkOSError as error:
            raise VerificationError(SIGN_IN_FAILED) from error
        return True


@dataclass(frozen=True)
class ConsoleVerifier:
    """The local-dev verifier: no WorkOS, no credentials, selected by `WORKOS_MODE=console` and
    never inferred from missing keys. The `Continue with Google` hop lands on a local stand-in for
    the WorkOS page (`AUTH_CONSOLE_PATH`) that takes any work email, and the code the email step
    would mail is logged rather than emailed — so `docker compose up` signs a member in without
    leaving localhost. The claim, the cookie binding, and the signed state are the deploy's own;
    only the identity proof is faked, so the code paths a member reaches are the same ones a real
    sign-in exercises."""

    def authorization_url(self, state: str) -> str:
        return f"{AUTH_CONSOLE_PATH}?state={quote(state)}"

    async def exchange(self, code: str) -> str:
        return code.strip().lower()

    async def begin(self, email: str) -> None:
        logger.info("onboard.console.code email=%s code=%s", email, CONSOLE_CODE)

    async def confirm(self, email: str, code: str) -> bool:
        return code.strip() == CONSOLE_CODE


def console_signin_page(state: str) -> str:
    """The stand-in the Google hop lands on in `WORKOS_MODE=console`: a plain email form whose GET
    reaches the same callback a real return does, carrying the state it was handed and the typed
    address as the code the console verifier reads straight back."""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8"><title>Dev sign-in · ufo</title></head>\n'
        "<body>\n"
        "<h1>Dev sign-in</h1>\n"
        "<p>WorkOS is off (WORKOS_MODE=console). Enter a work email to sign in.</p>\n"
        f'<form action="{AUTH_CALLBACK_PATH}" method="get">\n'
        f'<input type="hidden" name="state" value="{escape(state)}">\n'
        '<input name="code" type="email" placeholder="email@work.com" autofocus required>\n'
        '<button type="submit">Sign in</button>\n'
        "</form>\n"
        "</body></html>\n"
    )


def workos_console_mode() -> bool:
    return _workos_mode() == CONSOLE_MODE


def workos_verifier_from_env() -> WorkosVerifier | ConsoleVerifier:
    """The gateway's verifier. `WORKOS_MODE=console` runs the credential-free dev verifier; the
    default (`workos`) requires all three values, and the redirect URI is stated rather than derived
    from a host: it is the origin that routes the callback to this process, which is the app host in
    a hosted deploy and the gateway's own port locally. It has to match the URI registered in the
    same WorkOS environment, so a deploy states the one string both ends hold."""
    if _workos_mode() == CONSOLE_MODE:
        logger.warning("gateway.workos.console_mode")
        return ConsoleVerifier()
    api_key = _require_env(WORKOS_API_KEY_ENV)
    client_id = _require_env(WORKOS_CLIENT_ID_ENV)
    redirect_uri = _require_env(WORKOS_REDIRECT_URI_ENV)
    return WorkosVerifier(
        client=AsyncWorkOSClient(api_key=api_key, client_id=client_id), redirect_uri=redirect_uri
    )


def _workos_mode() -> str:
    mode = os.environ.get(WORKOS_MODE_ENV, WORKOS_MODE).strip().lower()
    if mode not in (WORKOS_MODE, CONSOLE_MODE):
        raise RuntimeError(f"{WORKOS_MODE_ENV}={mode!r} is not {WORKOS_MODE}|{CONSOLE_MODE}")
    return mode


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset — required by the gateway sign-in")
    return value
