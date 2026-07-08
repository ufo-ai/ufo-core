"""The claim state machine against the real `onboard_claim` table (Postgres): the work-email
denylist, hash-only storage, the TTL, the attempt cap, and the logging email backend that lets a
test read the minted code back."""

from datetime import timedelta

import pytest

from ufo_control.gateway_claim import ClaimError, ClaimWorkflow, hash_code
from ufo_control.gateway_email import LoggingEmailSender, WorkEmailError, WorkEmailPolicy
from ufo_control.gateway_store import OnboardStore


async def test_start_persists_only_the_hash_and_emails_the_code(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
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
        store=store, email_policy=WorkEmailPolicy(), email_sender=LoggingEmailSender()
    )
    with pytest.raises(WorkEmailError):
        await workflow.start("someone@gmail.com", "ufo", "sess-1")
    assert await store.live_claim("ufo", "sess-1") is None


async def test_verify_accepts_the_right_code_and_marks_verified(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    await workflow.verify(claim, sender.last_code("me@acme.com"))
    verified = await store.live_claim("ufo", "sess-1")
    assert verified is not None and verified.verified_at is not None


async def test_verify_rejects_a_wrong_code(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    with pytest.raises(ClaimError, match="incorrect"):
        await workflow.verify(claim, "000000")


async def test_attempt_cap_exhausts(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
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


async def test_expired_code_is_rejected(store: OnboardStore) -> None:
    sender = LoggingEmailSender()
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
