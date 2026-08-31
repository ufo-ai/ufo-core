"""Signing in with Anthropic: the leg that puts a member's own Anthropic credential in the store.

Two shapes of the same walkthrough, chosen by whether the deploy registered an OAuth client.
With one, `AnthropicCodeLogin` sends the member to Anthropic's authorization page and takes back the
code shown at the end — the redirect lands on Anthropic's own display callback, so nothing has to
come back to this host — and exchanges it for an access token. With none, the walkthrough opens the
console and takes the key they create there. Either way `verified_key` refuses what Anthropic will
not answer for, and the value lands under the member's own credential slot.
"""

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from ufo.sdk.models import (
    ANTHROPIC_TOKEN_URL,
    Grant,
    granted,
)

AUTHORIZE_URL = os.environ.get(
    "UFO_ANTHROPIC_AUTHORIZE_URL", "https://claude.com/cai/oauth/authorize"
)
REDIRECT_URI = os.environ.get(
    "UFO_ANTHROPIC_REDIRECT_URI", "https://platform.claude.com/oauth/code/callback"
)
MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"
OAUTH_BETA = "oauth-2025-04-20"
OAUTH_SCOPE = "user:inference"
OAUTH_ACCESS_TOKEN_PREFIX = "sk-ant-oat"

STATE_COOKIE = "ufo_anthropic_oauth"
SIGN_IN_PATH = "anthropic"
AUTHORIZE_PATH = "anthropic/authorize"
CODE_PATH = "anthropic/code"
CODE_FIELD = "code"

HTTP_TIMEOUT_SECONDS = 15.0
MAX_CODE_BYTES = 4096

CODE_MISSING = "Paste the code Anthropic showed you."
CODE_REFUSED = "Anthropic did not accept that code."


@dataclass(frozen=True)
class PendingAuthorization:
    """What the browser is sent to, and the verifier the paste-back is redeemed with. `cookie`
    rides the member's browser; `url` is the link they open."""

    url: str
    cookie: str


@dataclass(frozen=True)
class AnthropicCodeLogin:
    """The authorization-code sign-in against Anthropic. `authorize` opens one; `claim` spends the
    code the member pastes back. The redirect lands on Anthropic's own page, which displays the
    code rather than returning it here, so the member is the transport and there is no callback
    route to hold open."""

    client_id: str
    authorize_url: str = AUTHORIZE_URL
    token_url: str = ANTHROPIC_TOKEN_URL
    redirect_uri: str = REDIRECT_URI

    def authorize(self) -> PendingAuthorization:
        """`state` is the verifier itself, which is what Anthropic's authorize accepts — an
        independent shorter state is refused as an invalid request format before the member ever
        sees a consent screen. It also means the code and the state the member pastes back carry
        everything the redemption needs."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        query = urlencode(
            {
                "code": "true",
                "client_id": self.client_id,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "scope": OAUTH_SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": verifier,
            }
        )
        return PendingAuthorization(url=f"{self.authorize_url}?{query}", cookie=verifier)

    async def claim(self, pasted: str, verifier: str) -> Grant | None:
        """The access token the pasted code buys, or None. The member may paste the bare code, the
        `code#state` pair the page shows, or the whole callback URL — all three are what Anthropic
        puts in front of them, so all three are read rather than one being the right one. A state
        that came back is the verifier, so it is compared against the one this browser holds."""
        if not verifier:
            return None
        code, returned = _split_pasted(pasted)
        if not code or (returned is not None and not secrets.compare_digest(returned, verifier)):
            return None
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                answered = await client.post(
                    self.token_url,
                    content=json.dumps(
                        {
                            "grant_type": "authorization_code",
                            "code": code,
                            "redirect_uri": self.redirect_uri,
                            "client_id": self.client_id,
                            "code_verifier": verifier,
                            "state": returned or verifier,
                        }
                    ).encode(),
                    headers={"content-type": "application/json", "accept": "application/json"},
                )
            except httpx.HTTPError:
                return None
        if answered.status_code != 200:
            return None
        try:
            bought = granted(answered.json())
        except ValueError:
            return None
        return bought


def _split_pasted(pasted: str) -> tuple[str, str | None]:
    """The code and the state Anthropic handed back, out of whichever of the three forms the member
    pasted."""
    trimmed = pasted.strip()
    if "://" in trimmed:
        parsed = urlsplit(trimmed)
        values = parse_qs(parsed.query)
        values.update(parse_qs(parsed.fragment))
        return values.get("code", [""])[0], values.get("state", [None])[0]
    if "#" in trimmed:
        code, _, state = trimmed.partition("#")
        return code, state
    return trimmed, None


async def verified_key(credential: str) -> bool:
    """Whether Anthropic answers for this credential. An access token from the grant authenticates
    as a bearer under the OAuth beta; an API key authenticates as `x-api-key`. Signing in has to
    mean something, so a value that cannot list models never reaches the store."""
    headers = {"anthropic-version": ANTHROPIC_VERSION}
    if credential.startswith(OAUTH_ACCESS_TOKEN_PREFIX):
        headers |= {"authorization": f"Bearer {credential}", "anthropic-beta": OAUTH_BETA}
    else:
        headers |= {"x-api-key": credential}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        try:
            answered = await client.get(MODELS_URL, headers=headers)
        except httpx.HTTPError:
            return False
    return answered.status_code == 200
