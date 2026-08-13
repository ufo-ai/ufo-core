"""Work-email verification through WorkOS, under a claim time-to-live.

The time-to-live is the Magic Auth code's own ten minutes. WorkOS answers a code it will not
redeem the same way whether the digits are wrong or the code has died, so the claim's window is
what tells the member which happened: inside it a refused code is one to retype, and at its edge
the claim ends and says so.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from ufo_control.gateway_email import WorkEmailPolicy
from ufo_control.gateway_store import OnboardClaim, OnboardStore
from ufo_control.gateway_workos import VerificationError

logger = logging.getLogger(__name__)

CLAIM_TTL = timedelta(minutes=10)
VERIFICATION_CHANGED = "Another verification attempt changed this session."


class ClaimError(RuntimeError):
    """The onboarding flow renders the message."""


class Verifier(Protocol):
    def authorization_url(self, state: str) -> str: ...
    async def exchange(self, code: str) -> str: ...
    async def begin(self, email: str) -> None: ...
    async def confirm(self, email: str, code: str) -> bool: ...


@dataclass(frozen=True)
class ClaimWorkflow:
    """The claim is the machine's state and the seam the verifier sits behind: the terminal earns
    its verified stamp by confirming a code, the browser arrives already verified, and every
    write that could race another attempt is a conditional one whose loser says so."""

    store: OnboardStore
    email_policy: WorkEmailPolicy
    verifier: Verifier
    claim_ttl: timedelta = CLAIM_TTL

    async def start(self, email: str, surface: str, surface_ref: str) -> str:
        domain = self.email_policy.validate(email)
        claim = self._claim(email, domain, surface, surface_ref, verified_at=None)
        await self.store.insert_claim(claim)
        try:
            await self.verifier.begin(claim.email)
        except VerificationError as error:
            await self.store.delete_claim(claim.claim_id)
            logger.exception("onboard.verify.begin_failed domain=%s surface=%s", domain, surface)
            raise ClaimError(str(error)) from error
        return domain

    async def verify(self, claim: OnboardClaim, code: str) -> None:
        if datetime.now(UTC) >= claim.expires_at:
            if await self.store.delete_unverified_claim(claim.claim_id):
                raise ClaimError("The verification code expired. Start onboarding again.")
            raise ClaimError(VERIFICATION_CHANGED)
        try:
            confirmed = await self.verifier.confirm(claim.email, code)
        except VerificationError as error:
            if await self.store.delete_unverified_claim(claim.claim_id):
                raise ClaimError(str(error)) from error
            raise ClaimError(VERIFICATION_CHANGED) from error
        if not confirmed:
            raise ClaimError("The verification code is incorrect.")
        if not await self.store.mark_verified(claim.claim_id):
            raise ClaimError(VERIFICATION_CHANGED)

    async def admit_verified(self, email: str, surface: str, surface_ref: str) -> OnboardClaim:
        """The browser's claim: WorkOS has already answered, so the claim is stamped as it is
        written. A second callback for a session that already holds a live claim resolves that
        claim rather than opening a second one, so the member lands where the first one left off."""
        domain = self.email_policy.validate(email)
        existing = await self.store.live_claim(surface, surface_ref)
        if existing is not None:
            return existing
        claim = self._claim(email, domain, surface, surface_ref, verified_at=datetime.now(UTC))
        await self.store.insert_claim(claim)
        return claim

    def _claim(
        self, email: str, domain: str, surface: str, surface_ref: str, verified_at: datetime | None
    ) -> OnboardClaim:
        return OnboardClaim(
            claim_id=uuid4(),
            email=email.strip().lower(),
            email_domain=domain,
            surface=surface,
            surface_ref=surface_ref,
            expires_at=datetime.now(UTC) + self.claim_ttl,
            verified_at=verified_at,
            invite_id=None,
        )
