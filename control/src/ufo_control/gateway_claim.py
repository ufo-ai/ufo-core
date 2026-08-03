"""Work-email verification under a time-to-live and attempt cap."""

import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from ufo_control.gateway_email import EmailSender, WorkEmailPolicy, verification_email
from ufo_control.gateway_store import OnboardClaim, OnboardStore

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5
VERIFICATION_CHANGED = "Another verification attempt changed this session."


class ClaimError(RuntimeError):
    """The onboarding flow renders the message; verification codes remain hashed."""


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
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        claim = OnboardClaim(
            claim_id=uuid4(),
            email=email.strip().lower(),
            email_domain=domain,
            code_hash=hash_code(code),
            surface=surface,
            surface_ref=surface_ref,
            expires_at=datetime.now(UTC) + self.code_ttl,
            attempts=0,
            verified_at=None,
            invite_id=None,
        )
        await self.store.insert_claim(claim)
        subject, text = verification_email(code, claim.expires_at, self.code_ttl)
        try:
            await self.email_sender.send(claim.email, subject, text)
        except Exception as exc:
            await self.store.delete_claim(claim.claim_id)
            logger.exception("onboard.email.send_failed domain=%s surface=%s", domain, surface)
            raise ClaimError("Could not send the verification email. Try again.") from exc
        return domain

    async def verify(self, claim: OnboardClaim, code: str) -> None:
        if datetime.now(UTC) >= claim.expires_at:
            if await self.store.delete_unverified_claim(claim.claim_id, claim.attempts):
                raise ClaimError("The verification code expired. Start onboarding again.")
            raise ClaimError(VERIFICATION_CHANGED)
        attempts = claim.attempts + 1
        if hmac.compare_digest(claim.code_hash, hash_code(code)):
            if await self.store.record_verification(claim.claim_id, claim.attempts):
                return
            raise ClaimError(VERIFICATION_CHANGED)
        if attempts >= self.max_attempts:
            if await self.store.delete_unverified_claim(claim.claim_id, claim.attempts):
                raise ClaimError("Too many attempts. Start onboarding again.")
            raise ClaimError(VERIFICATION_CHANGED)
        if await self.store.record_attempt(claim.claim_id, claim.attempts):
            raise ClaimError("The verification code is incorrect.")
        raise ClaimError(VERIFICATION_CHANGED)
