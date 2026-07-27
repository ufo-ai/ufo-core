"""The published GitHub App as the git credential: an installation token minted for the turn.

A workspace that installed the App holds only its installation id — not a secret, which is why it
lives in an ordinary credential slot the member fills in chat like any other. The deploy holds the
App's private key, signs a short-lived JWT with it, and exchanges that for an installation token
scoped to what the org granted at install time. The token expires in an hour and is minted at rule
derivation, so it lives about as long as the turn that uses it and never reaches the sandbox.

A workspace with no installation mints nothing and the slot's stored value answers instead — the
member's own token, for a repository outside any org that installed the App.

The slot holds a seal over `(workspace, installation)` that only the install callback can produce,
never a bare id. An id is a small integer anyone could type into the slot, and this deploy's App key
can mint against any installation of it — so trusting a typed one would let a workspace mint tokens
for another organization's repositories. A value that does not open raises rather than falling back
to the member's own token, because authenticating as a different identity than the one the
organization granted is worse than failing the clone."""

import asyncio
import os
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from datetime import datetime
from json import dumps
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ufo.sdk.context import CredentialSlotUnset
from ufo.sdk.credentials import (
    CredentialMintFailed,
    CredentialRequestInvalid,
    CredentialStore,
    open_installation,
)
from ufo.sdk.o11y import warn

GITHUB_API = "https://api.github.com"
JWT_LIFETIME_SECONDS = 540
TOKEN_REFRESH_MARGIN_SECONDS = 300
MINT_TIMEOUT_SECONDS = 10


def _segment(payload: dict[str, object]) -> bytes:
    return urlsafe_b64encode(dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")


@dataclass(frozen=True)
class GitHubAppTokens:
    """Mints this workspace's installation token, cached until shortly before it expires so a
    conversation's turns share one rather than minting per turn. `app_id` and `private_key` are the
    deploy's own App registration; `installation_slot` is where the member's install id is read.

    The cache is keyed by the installation as well as the workspace, because a workspace can rebind
    to a different one: keyed by workspace alone, a rebind would keep authenticating as the previous
    installation for the rest of the token's hour, which is exactly the mismatch the seal exists to
    prevent."""

    app_id: str
    private_key: rsa.RSAPrivateKey
    installation_slot: str
    transport: httpx.AsyncBaseTransport | None = None
    minted: dict[tuple[UUID, str], tuple[str, float]] = field(default_factory=dict)
    _minting: dict[tuple[UUID, str], asyncio.Task[tuple[str, float]]] = field(
        default_factory=dict, init=False, compare=False
    )

    async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool:
        """Whether this workspace bound an installation — the stored seal's presence, no mint. A
        seal this deploy cannot open is not a binding: it opens no egress and says so, rather than
        reporting a credential the wire would then fail to produce."""
        try:
            sealed = await store.get(workspace_id, self.installation_slot)
        except CredentialSlotUnset:
            return False
        try:
            open_installation(store.fernet, workspace_id, self.installation_slot, sealed)
        except CredentialRequestInvalid:
            warn("github_app.installation_binding_unreadable", slot=self.installation_slot)
            return False
        return True

    async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None:
        try:
            bound = await store.get(workspace_id, self.installation_slot)
        except CredentialSlotUnset:
            return None
        installation = open_installation(store.fernet, workspace_id, self.installation_slot, bound)
        key = (workspace_id, installation)
        now = time.time()
        cached = self.minted.get(key)
        if cached is not None and cached[1] - TOKEN_REFRESH_MARGIN_SECONDS > now:
            return cached[0]
        pending = self._minting.get(key)
        if pending is None:
            pending = asyncio.create_task(self._mint(key, installation))
            self._minting[key] = pending
        token, _ = await asyncio.shield(pending)
        return token

    async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]:
        task = asyncio.current_task()
        try:
            minted = await self._installation_token(installation)
            self.minted[key] = minted
            return minted
        finally:
            if self._minting.get(key) is task:
                del self._minting[key]

    async def _installation_token(self, installation: str) -> tuple[str, float]:
        """Exchange an App JWT for the installation's own token, and answer GitHub's stated expiry
        for it — the JWT that fetched it lives minutes and the token an hour, so caching on the
        wrong one would re-mint fifteen times as often as it needs to. A failure raises: a workspace
        that installed the App and then cannot mint must not silently fall back to a stored token,
        which would authenticate as a different identity than the one the organization granted."""
        async with httpx.AsyncClient(
            timeout=MINT_TIMEOUT_SECONDS, transport=self.transport
        ) as client:
            try:
                response = await client.post(
                    f"{GITHUB_API}/app/installations/{installation}/access_tokens",
                    headers={
                        "Authorization": f"Bearer {self._jwt()}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            except httpx.HTTPError as error:
                raise CredentialMintFailed(
                    f"github app installation {installation} unreachable: {error}"
                ) from error
        if response.status_code != 201:
            raise CredentialMintFailed(
                f"github app installation {installation} minted no token: "
                f"{response.status_code} {response.text[:200]}"
            )
        try:
            payload = response.json()
            return payload["token"], datetime.fromisoformat(payload["expires_at"]).timestamp()
        except (KeyError, TypeError, ValueError) as error:
            raise CredentialMintFailed(
                f"github app installation {installation} answered an unreadable token: {error}"
            ) from error

    def _jwt(self) -> str:
        now = int(time.time())
        head = _segment({"alg": "RS256", "typ": "JWT"})
        body = _segment({"iat": now - 60, "exp": now + JWT_LIFETIME_SECONDS, "iss": self.app_id})
        signature = self.private_key.sign(
            b".".join((head, body)), padding.PKCS1v15(), hashes.SHA256()
        )
        return b".".join((head, body, urlsafe_b64encode(signature).rstrip(b"="))).decode()


def app_tokens(installation_slot: str) -> GitHubAppTokens:
    """Read the deploy's App registration loud: a configured id with no readable key is a deploy
    that would answer every git request with the member's fallback token and no signal why. The key
    is the PEM itself, not a path — one secret value the deploy sets beside every other, rather than
    a file the process must also have mounted."""
    app_id = os.environ["GITHUB_APP_ID"]
    key = serialization.load_pem_private_key(
        os.environ["GITHUB_APP_PRIVATE_KEY"].encode(), password=None
    )
    if not isinstance(key, rsa.RSAPrivateKey):
        raise RuntimeError("GITHUB_APP_PRIVATE_KEY is not an RSA private key")
    return GitHubAppTokens(app_id=app_id, private_key=key, installation_slot=installation_slot)
