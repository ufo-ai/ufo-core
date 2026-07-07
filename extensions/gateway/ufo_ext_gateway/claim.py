"""The code-verify journey: work-email → 6-digit code → constant-time verify under a TTL and an
attempt cap.

Copy-adapted from metalcraft's `onboard/claim.py`. `start` validates the work-email domain, mints a
one-time code, persists only its hash, and sends it; `verify` compares in constant time under the
expiry and attempt cap, then marks the claim verified. The join-or-provision decision that
metalcraft folded into `verify_code` moves to the route handler, because provisioning is streamed
across the client's polls rather than blocking one request."""

import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from ufo_ext_gateway.email_domain import WorkEmailPolicy
from ufo_ext_gateway.email_sender import EmailSender
from ufo_ext_gateway.store import OnboardClaim, OnboardStore

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5


class ClaimError(RuntimeError):
    """A claim could not start or verify: expired, attempts exhausted, or a code mismatch. The
    surface turns this into an in-channel message; the code never leaves as anything but a hash."""


def hash_code(code: str) -> str:
    return sha256(code.encode()).hexdigest()


@dataclass(frozen=True)
class ClaimWorkflow:
    store: OnboardStore
    email_policy: WorkEmailPolicy
    email_sender: EmailSender
    code_ttl: timedelta = CODE_TTL
    max_attempts: int = MAX_ATTEMPTS

    async def start(self, email: str, surface: str, surface_ref: str) -> str:
        domain = self.email_policy.validate(email)
        now = datetime.now(UTC)
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        claim = OnboardClaim(
            claim_id=uuid4(),
            email=email.strip().lower(),
            email_domain=domain,
            code_hash=hash_code(code),
            surface=surface,
            surface_ref=surface_ref,
            expires_at=now + self.code_ttl,
            attempts=0,
            max_attempts=self.max_attempts,
            verified_at=None,
            tenant_name=None,
            resulting_workspace_id=None,
            completed_at=None,
        )
        await self.store.insert_claim(claim, now)
        try:
            await self.email_sender.send(claim.email, code)
        except Exception as exc:
            logger.exception("onboard.email.send_failed domain=%s surface=%s", domain, surface)
            raise ClaimError("could not send the verification email; please try again") from exc
        return domain

    async def verify(self, claim: OnboardClaim, code: str) -> None:
        now = datetime.now(UTC)
        if claim.attempts >= claim.max_attempts:
            raise ClaimError("too many attempts; start onboarding again")
        if now >= claim.expires_at:
            raise ClaimError("verification code expired; start onboarding again")
        await self.store.record_attempt(claim.claim_id, claim.attempts + 1, now)
        if not hmac.compare_digest(claim.code_hash, hash_code(code)):
            raise ClaimError("verification code is incorrect")
        await self.store.mark_verified(claim.claim_id, now)
