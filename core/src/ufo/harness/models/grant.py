"""A member's connected provider account, as it is stored and as it is spent.

An account grant is not an API key: it expires, and it carries the refresh token that buys the next
one. Storing only the access token leaves a slot that reads as connected long after it stops
answering, so the whole grant is what lands in the slot and `live` is what a call gets — refreshed
in place when it has gone stale, so a member connects once and keeps working.

The client ids and token endpoints are here rather than beside the sign-in flows because both ends
spend them: the flow that first redeems a grant, and every later call that refreshes one.
"""

import json
import os
import time

import httpx
from pydantic import BaseModel

from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT, OPENAI_KEY_SLOT

OPENAI_CLIENT_ID_ENV = "UFO_OPENAI_OAUTH_CLIENT_ID"
OPENAI_PUBLIC_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_AUTH_BASE = os.environ.get("UFO_OPENAI_AUTH_BASE", "https://auth.openai.com").rstrip("/")
OPENAI_TOKEN_URL = f"{OPENAI_AUTH_BASE}/oauth/token"

ANTHROPIC_CLIENT_ID_ENV = "UFO_ANTHROPIC_OAUTH_CLIENT_ID"
ANTHROPIC_PUBLIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_TOKEN_URL = os.environ.get(
    "UFO_ANTHROPIC_TOKEN_URL", "https://platform.claude.com/v1/oauth/token"
)

REFRESH_GRANT = "refresh_token"
HTTP_TIMEOUT_SECONDS = 15.0


def openai_client_id() -> str:
    """The public client this deploy presents to OpenAI — its own if it names one, else OpenAI's
    published Codex client."""
    return os.environ.get(OPENAI_CLIENT_ID_ENV) or OPENAI_PUBLIC_CLIENT_ID


def anthropic_client_id() -> str:
    """The public client this deploy presents to Anthropic — its own if it names one, else
    Claude's published client."""
    return os.environ.get(ANTHROPIC_CLIENT_ID_ENV) or ANTHROPIC_PUBLIC_CLIENT_ID


"""Where each slot's grant is exchanged, and who it is exchanged as. Read per call rather than at
import, because a refresh has to name the same client the sign-in did — a deploy that presents its
own client would otherwise mint grants no refresh could spend."""
GRANT_CLIENTS = {
    OPENAI_KEY_SLOT: (OPENAI_TOKEN_URL, openai_client_id),
    ANTHROPIC_KEY_SLOT: (ANTHROPIC_TOKEN_URL, anthropic_client_id),
}

"""How long a claimed refresh is left to finish before another caller may take it. Longer than the
request the claimer makes, so a live refresh is never raced; short enough that a caller killed
mid-refresh does not strand the account for long."""
REFRESH_LEASE_SECONDS = 30.0

"""How long before expiry a grant is treated as spent. A turn holds its client for the length of
the turn, so a token that is merely valid *now* is not good enough — this is the margin that keeps
a long turn from dying halfway through on a token that expired under it."""
REFRESH_MARGIN_SECONDS = 300


class GrantRefusedRefresh(Exception):
    """The provider would not exchange a refresh token. The member has to connect again — nothing
    else in the deploy can buy them a working account."""

    def __init__(self, slot: str) -> None:
        super().__init__(f"the connected account for {slot!r} could not be refreshed")
        self.slot = slot


class Grant(BaseModel):
    """A provider account as stored: what a call spends, what buys the next one, and when the first
    stops working. `expires_at` is epoch seconds."""

    access: str
    refresh: str
    expires_at: float
    """When a caller claimed the refresh of this grant, and until when. A refresh token is
    one-time, so the claim is what keeps two callers from both spending it."""
    refreshing_until: float = 0.0

    @property
    def spent(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGIN_SECONDS

    @property
    def claimed(self) -> bool:
        return time.time() < self.refreshing_until

    def stored(self) -> str:
        return self.model_dump_json()


def granted(payload: dict[str, object]) -> Grant | None:
    """The grant a token endpoint answered with, or None if it answered without one. Every field is
    required: a response missing the refresh token buys one hour and then nothing, which is worse
    than refusing the sign-in outright."""
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access, str) or not access:
        return None
    if not isinstance(refresh, str) or not refresh:
        return None
    if not isinstance(expires_in, int | float):
        return None
    return Grant(access=access, refresh=refresh, expires_at=time.time() + float(expires_in))


def read_grant(stored: str) -> Grant | None:
    """The grant a slot holds, or None when the slot holds a plain API key — which is what the
    workspace row and the deploy's own environment hold, and those never expire."""
    try:
        payload = json.loads(stored)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return Grant.model_validate(payload)
    except ValueError:
        return None


async def refreshed(grant: Grant, slot: str) -> Grant:
    """The grant, current. Spends the refresh token for a new pair, which providers rotate, so the
    answer replaces the stored grant rather than updating one field of it."""
    endpoint, client_of = GRANT_CLIENTS[slot]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        try:
            answered = await client.post(
                endpoint,
                json={
                    "grant_type": REFRESH_GRANT,
                    "refresh_token": grant.refresh,
                    "client_id": client_of(),
                },
                headers={"content-type": "application/json", "accept": "application/json"},
            )
        except httpx.HTTPError as unreachable:
            raise GrantRefusedRefresh(slot) from unreachable
    if answered.status_code != 200:
        raise GrantRefusedRefresh(slot)
    try:
        bought = granted(answered.json())
    except ValueError as unreadable:
        raise GrantRefusedRefresh(slot) from unreadable
    if bought is None:
        raise GrantRefusedRefresh(slot)
    return bought
