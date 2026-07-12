"""The claim state machine against the real `onboard_claim` table (Postgres): the work-email
denylist, hash-only storage, the TTL, the attempt cap, and the recording email sender fake that lets
a test read the minted code back."""

from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from ufo_control.gateway_claim import ClaimError, ClaimWorkflow, hash_code
from ufo_control.gateway_email import WorkEmailError, WorkEmailPolicy
from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_store import OnboardStore


@dataclass
class RecordingSender:
    sent: dict[str, str] = field(default_factory=dict)

    async def send(self, email: str, code: str) -> None:
        self.sent[email] = code

    def last_code(self, email: str) -> str:
        return self.sent[email]


class FailingSender:
    async def send(self, email: str, code: str) -> None:
        raise RuntimeError("mail unavailable")


async def test_start_persists_only_the_hash_and_emails_the_code(store: OnboardStore) -> None:
    sender = RecordingSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    domain = await workflow.start("Me@Acme.com", "ufo", "sess-1")
    assert domain == "acme.com"
    code = sender.last_code("me@acme.com")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    assert claim.email == "me@acme.com"
    assert claim.code_hash == hash_code(code)
    assert code not in claim.code_hash


async def test_denylist_rejects_a_free_email_before_any_write(store: OnboardStore) -> None:
    workflow = ClaimWorkflow(
        store=store, email_policy=WorkEmailPolicy(), email_sender=RecordingSender()
    )
    with pytest.raises(WorkEmailError):
        await workflow.start("someone@gmail.com", "ufo", "sess-1")
    assert await store.live_claim("ufo", "sess-1") is None


async def test_verify_accepts_the_right_code_and_marks_verified(store: OnboardStore) -> None:
    sender = RecordingSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    await workflow.verify(claim, sender.last_code("me@acme.com"))
    verified = await store.live_claim("ufo", "sess-1")
    assert verified is not None and verified.verified_at is not None


async def test_verify_rejects_a_wrong_code(store: OnboardStore) -> None:
    sender = RecordingSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    with pytest.raises(ClaimError, match="incorrect"):
        await workflow.verify(claim, "000000")


async def test_attempt_cap_exhausts(store: OnboardStore) -> None:
    sender = RecordingSender()
    workflow = ClaimWorkflow(
        store=store, email_policy=WorkEmailPolicy(), email_sender=sender, max_attempts=2
    )
    await workflow.start("me@acme.com", "ufo", "sess-1")
    for _ in range(2):
        claim = await store.live_claim("ufo", "sess-1")
        assert claim is not None
        with pytest.raises(ClaimError, match="incorrect"):
            await workflow.verify(claim, "000000")
    exhausted = await store.live_claim("ufo", "sess-1")
    assert exhausted is not None
    with pytest.raises(ClaimError, match="too many attempts"):
        await workflow.verify(exhausted, sender.last_code("me@acme.com"))
    assert await store.live_claim("ufo", "sess-1") is None


async def test_expired_code_is_rejected(store: OnboardStore) -> None:
    sender = RecordingSender()
    workflow = ClaimWorkflow(
        store=store,
        email_policy=WorkEmailPolicy(),
        email_sender=sender,
        code_ttl=timedelta(minutes=-1),
    )
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    with pytest.raises(ClaimError, match="expired"):
        await workflow.verify(claim, sender.last_code("me@acme.com"))
    assert await store.live_claim("ufo", "sess-1") is None


async def test_failed_email_leaves_no_active_claim(store: OnboardStore) -> None:
    workflow = ClaimWorkflow(
        store=store, email_policy=WorkEmailPolicy(), email_sender=FailingSender()
    )
    with pytest.raises(ClaimError, match="could not send"):
        await workflow.start("me@acme.com", "ufo", "sess-1")
    assert await store.live_claim("ufo", "sess-1") is None


async def test_invite_redeems_once_and_stamps_the_claim(store: OnboardStore) -> None:
    sender = RecordingSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    invites = InviteCodes(pool=store.pool)
    code = await invites.mint()
    invite_id = await invites.redeem(code, claim.claim_id)
    assert invite_id is not None
    assert await invites.redeem(code, claim.claim_id) is None
    stamped = await store.live_claim("ufo", "sess-1")
    assert stamped is not None and stamped.invite_id == invite_id
