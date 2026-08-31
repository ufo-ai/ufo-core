"""Signing in with OpenAI: the leg that puts a member's own provider key in the workspace store.

The portal will not open until the member holds a key. The door asks OpenAI for a one-time code
before it draws anything (`OpenAiDeviceLogin.request_code`), so the member reads the walkthrough
with their code already in it; `claim` then polls until they approve and exchanges the grant for an
API key. The value lands under the member's own credential slot, which the turn loop resolves
before it falls back to an admin's.

`openai_client_id` names OpenAI's own published public client for this grant, and the grant
is refused outright until the account enables device-code login — which is why that is step one of
three, and why the page still draws when there is no code to show.
"""

import os
from dataclasses import dataclass
from typing import Literal

import httpx

from ufo.sdk.models import (
    OPENAI_TOKEN_URL,
    chatgpt_account_id,
    granted,
)

AUTH_BASE = os.environ.get("UFO_OPENAI_AUTH_BASE", "https://auth.openai.com").rstrip("/")
USER_CODE_URL = f"{AUTH_BASE}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE}/api/accounts/deviceauth/token"
REDIRECT_URI = f"{AUTH_BASE}/deviceauth/callback"
VERIFICATION_URL = f"{AUTH_BASE}/codex/device"

AUTHORIZATION_CODE_GRANT = "authorization_code"
PENDING_STATUSES = frozenset({403, 404})
PENDING_ERRORS = frozenset({"deviceauth_authorization_pending", "slow_down"})

DEVICE_COOKIE = "ufo_openai_device"
SIGN_IN_PATH = "openai"
DEVICE_PATH = "openai/device"
POLL_PATH = "openai/device/poll"

HTTP_TIMEOUT_SECONDS = 15.0
DEFAULT_POLL_INTERVAL = 5

DEVICE_UNAVAILABLE = "OpenAI would not start a device sign-in. Start again."
EXCHANGE_REFUSED = "OpenAI did not return an API key for that account."


@dataclass(frozen=True)
class DeviceAuthorization:
    """What OpenAI hands back to open a device sign-in. The poll is keyed on both ids, so the pair
    travels together; only `user_code` is drawn, and the member types it at `verification_uri`."""

    device_auth_id: str
    user_code: str
    verification_uri: str
    interval: int


@dataclass(frozen=True)
class DeviceClaim:
    """One poll's answer: `pending` while the member is still at OpenAI, `granted` with the key
    once they approved and the grant bought one, `refused` for an end the member has to read."""

    status: Literal["pending", "granted", "refused"]
    key: str = ""
    refusal: str = ""


@dataclass(frozen=True)
class OpenAiDeviceLogin:
    """OpenAI's device sign-in, which is its own shape rather than RFC 8628: `request_code` asks
    for a user code, `claim` polls until the member approves and then redeems the authorization
    code the approval hands back — together with the PKCE verifier the server minted for it.

    What comes back is a ChatGPT account token, not a platform API key: it is spent against the
    Codex backend under the account id its own JWT carries, which is why nothing here asks
    `api.openai.com` to vouch for it."""

    client_id: str
    user_code_url: str = USER_CODE_URL
    device_token_url: str = DEVICE_TOKEN_URL
    token_url: str = OPENAI_TOKEN_URL
    redirect_uri: str = REDIRECT_URI

    async def request_code(self) -> DeviceAuthorization | None:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                opened = await client.post(self.user_code_url, json={"client_id": self.client_id})
            except httpx.HTTPError:
                return None
        if opened.status_code != 200:
            return None
        answer = opened.json()
        device_auth_id = answer.get("device_auth_id")
        user_code = answer.get("user_code")
        if not isinstance(device_auth_id, str) or not isinstance(user_code, str):
            return None
        return DeviceAuthorization(
            device_auth_id=device_auth_id,
            user_code=user_code,
            verification_uri=VERIFICATION_URL,
            interval=_interval(answer.get("interval")),
        )

    async def claim(self, device_auth_id: str, user_code: str) -> DeviceClaim:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                polled = await client.post(
                    self.device_token_url,
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                )
            except httpx.HTTPError:
                return DeviceClaim(status="pending")
            if polled.status_code != 200:
                return _unapproved(polled)
            approved = polled.json() if polled.content else {}
            code = approved.get("authorization_code")
            verifier = approved.get("code_verifier")
            if (
                not isinstance(code, str)
                or not isinstance(verifier, str)
                or not code
                or not verifier
            ):
                return DeviceClaim(status="refused", refusal=EXCHANGE_REFUSED)
            return await self._redeem(client, code, verifier)

    async def _redeem(self, client: httpx.AsyncClient, code: str, verifier: str) -> DeviceClaim:
        """The approved grant, spent at the ordinary token endpoint. The form is urlencoded — the
        device legs above are the JSON ones — and the redirect it names is the device callback the
        approval was issued against."""
        try:
            answered = await client.post(
                self.token_url,
                data={
                    "grant_type": AUTHORIZATION_CODE_GRANT,
                    "client_id": self.client_id,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": self.redirect_uri,
                },
            )
        except httpx.HTTPError:
            return DeviceClaim(status="refused", refusal=EXCHANGE_REFUSED)
        if answered.status_code != 200:
            return DeviceClaim(status="refused", refusal=EXCHANGE_REFUSED)
        try:
            bought = granted(answered.json())
        except ValueError:
            return DeviceClaim(status="refused", refusal=EXCHANGE_REFUSED)
        if bought is None or chatgpt_account_id(bought.access) is None:
            return DeviceClaim(status="refused", refusal=EXCHANGE_REFUSED)
        return DeviceClaim(status="granted", key=bought.stored())


def _interval(raw: object) -> int:
    """OpenAI answers the poll interval as a string; a missing or unreadable one takes the
    specified default rather than polling as fast as the loop can."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_POLL_INTERVAL


def _unapproved(polled: httpx.Response) -> DeviceClaim:
    """A non-200 poll that means the member is still deciding, or an ending they must read. The
    two statuses OpenAI answers before approval are refusals of the poll, not of the sign-in."""
    if polled.status_code in PENDING_STATUSES:
        return DeviceClaim(status="pending")
    try:
        error = polled.json().get("error")
    except ValueError:
        error = None
    code = error.get("code") if isinstance(error, dict) else error
    if code in PENDING_ERRORS:
        return DeviceClaim(status="pending")
    return DeviceClaim(status="refused", refusal=DEVICE_UNAVAILABLE)
