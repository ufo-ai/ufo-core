"""The claim state machine against the real `onboard_claim` table (Postgres): the work-email
denylist, the claim's time-to-live, what each of the verifier's three answers does to the row, and
the conditional writes that decide a race. WorkOS stands behind `FakeVerifier` — every assertion
here is about the claim, never about the fake."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from fake_workos import MAGIC_CODE, FakeVerifier

from ufo_control.gateway_claim import ClaimError, ClaimWorkflow
from ufo_control.gateway_email import WorkEmailError, WorkEmailPolicy
from ufo_control.gateway_invite import (
    InviteAccepted,
    InviteCodes,
    InviteConsumed,
    InviteError,
    InviteExpired,
    InviteUnknown,
)
from ufo_control.gateway_store import OnboardStore

WRONG_CODE = "000000"


def _workflow(
    store: OnboardStore, verifier: FakeVerifier, claim_ttl: timedelta | None = None
) -> ClaimWorkflow:
    if claim_ttl is None:
        return ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), verifier=verifier)
    return ClaimWorkflow(
        store=store, email_policy=WorkEmailPolicy(), verifier=verifier, claim_ttl=claim_ttl
    )


async def test_start_records_the_claim_and_asks_workos_for_one_code(store: OnboardStore) -> None:
    verifier = FakeVerifier()
    domain = await _workflow(store, verifier).start("Me@Acme.com", "ufo", "sess-1")
    assert domain == "acme.com"
    assert verifier.begun == ["me@acme.com"]
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    assert claim.email == "me@acme.com"
    assert claim.verified_at is None


async def test_denylist_rejects_a_free_email_before_any_write_or_send(store: OnboardStore) -> None:
    verifier = FakeVerifier()
    with pytest.raises(WorkEmailError):
        await _workflow(store, verifier).start("someone@gmail.com", "ufo", "sess-1")
    assert await store.live_claim("ufo", "sess-1") is None
    assert verifier.begun == []


async def test_a_code_workos_could_not_send_leaves_no_active_claim(store: OnboardStore) -> None:
    verifier = FakeVerifier(begin_fails=True)
    with pytest.raises(ClaimError, match=r"Could not send the verification code\. Try again\."):
        await _workflow(store, verifier).start("me@acme.com", "ufo", "sess-1")
    assert await store.live_claim("ufo", "sess-1") is None


async def test_verify_accepts_the_right_code_and_marks_verified(store: OnboardStore) -> None:
    workflow = _workflow(store, FakeVerifier())
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    await workflow.verify(claim, MAGIC_CODE)
    verified = await store.live_claim("ufo", "sess-1")
    assert verified is not None and verified.verified_at is not None


async def test_verify_rejects_a_wrong_code_and_keeps_the_claim(store: OnboardStore) -> None:
    """WorkOS caps the attempts, so a wrong code costs the member nothing but the retype: the row
    stays exactly as it was and the same prompt comes back."""
    workflow = _workflow(store, FakeVerifier())
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    with pytest.raises(ClaimError, match=r"The verification code is incorrect\."):
        await workflow.verify(claim, WRONG_CODE)
    retryable = await store.live_claim("ufo", "sess-1")
    assert retryable is not None and retryable.verified_at is None
    await workflow.verify(retryable, MAGIC_CODE)
    verified = await store.live_claim("ufo", "sess-1")
    assert verified is not None and verified.verified_at is not None


async def test_a_verifier_that_could_not_grade_the_code_ends_the_claim(
    store: OnboardStore,
) -> None:
    """A WorkOS fault is not a verdict on the digits, so the claim cannot be left standing for a
    retype it has no answer for: it is deleted, and the member reads the verifier's own sentence."""
    verifier = FakeVerifier(faults={"me@acme.com"})
    workflow = _workflow(store, verifier)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    with pytest.raises(ClaimError, match=r"Sign-in failed\. Try again\."):
        await workflow.verify(claim, MAGIC_CODE)
    assert await store.live_claim("ufo", "sess-1") is None


async def test_an_expired_claim_is_refused_without_asking_workos(store: OnboardStore) -> None:
    """The verifier's own answer for this code is dropped first, so a consulted verifier would
    grade the code wrong rather than expired — the sentence names which end refused."""
    verifier = FakeVerifier()
    workflow = _workflow(store, verifier, claim_ttl=timedelta(minutes=-1))
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    verifier.codes.clear()
    with pytest.raises(ClaimError, match=r"expired\. Start onboarding again\."):
        await workflow.verify(claim, MAGIC_CODE)
    assert await store.live_claim("ufo", "sess-1") is None


async def test_a_stale_expiry_cannot_delete_a_verified_claim(store: OnboardStore) -> None:
    """Two attempts on one session: the one holding the older row must not undo the newer state.
    Every write is conditional on the row still being unverified, so the loser says so."""
    workflow = _workflow(store, FakeVerifier())
    await workflow.start("me@acme.com", "ufo", "sess-1")
    stale = await store.live_claim("ufo", "sess-1")
    assert stale is not None
    await workflow.verify(stale, MAGIC_CODE)
    expired = replace(stale, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(ClaimError, match="changed this session"):
        await workflow.verify(expired, MAGIC_CODE)
    verified = await store.live_claim("ufo", "sess-1")
    assert verified is not None and verified.verified_at is not None
    assert not await store.delete_unverified_claim(verified.claim_id)


async def test_only_the_first_of_two_verifications_stamps_the_claim(store: OnboardStore) -> None:
    workflow = _workflow(store, FakeVerifier())
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    assert await store.mark_verified(claim.claim_id)
    assert not await store.mark_verified(claim.claim_id)
    with pytest.raises(ClaimError, match="changed this session"):
        await workflow.verify(claim, MAGIC_CODE)


async def test_admit_verified_writes_a_claim_that_needs_no_code(store: OnboardStore) -> None:
    workflow = _workflow(store, FakeVerifier())
    claim = await workflow.admit_verified("Me@Acme.com", "web", "sess-1")
    assert claim.email == "me@acme.com"
    assert claim.email_domain == "acme.com"
    assert claim.verified_at is not None
    stored = await store.live_claim("web", "sess-1")
    assert stored is not None and stored.claim_id == claim.claim_id
    assert stored.verified_at is not None


async def test_admit_verified_answers_a_repeated_callback_with_the_first_claim(
    store: OnboardStore,
) -> None:
    workflow = _workflow(store, FakeVerifier())
    first = await workflow.admit_verified("me@acme.com", "web", "sess-1")
    second = await workflow.admit_verified("someone.else@acme.com", "web", "sess-1")
    assert second.claim_id == first.claim_id
    assert second.email == "me@acme.com"


async def test_admit_verified_refuses_a_denylisted_email_before_any_write(
    store: OnboardStore,
) -> None:
    workflow = _workflow(store, FakeVerifier())
    with pytest.raises(WorkEmailError):
        await workflow.admit_verified("someone@gmail.com", "web", "sess-1")
    assert await store.live_claim("web", "sess-1") is None


async def test_a_web_claim_and_a_terminal_claim_never_share_a_session(store: OnboardStore) -> None:
    workflow = _workflow(store, FakeVerifier())
    await workflow.admit_verified("me@acme.com", "web", "shared")
    await workflow.start("me@acme.com", "ufo", "shared")
    web = await store.live_claim("web", "shared")
    terminal = await store.live_claim("ufo", "shared")
    assert web is not None and web.verified_at is not None
    assert terminal is not None and terminal.verified_at is None


async def test_invite_redeems_once_and_stamps_the_claim(store: OnboardStore) -> None:
    workflow = _workflow(store, FakeVerifier())
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    accepted = await invites.redeem(claim.email_domain, claim.claim_id)
    assert isinstance(accepted, InviteAccepted)
    assert accepted.object_number == 7
    assert accepted.consumed_at.tzinfo is not None
    repeated = await invites.redeem(claim.email_domain, claim.claim_id)
    assert isinstance(repeated, InviteAccepted)
    assert repeated.invite_id == accepted.invite_id
    stamped = await store.live_claim("ufo", "sess-1")
    assert stamped is not None and stamped.invite_id == accepted.invite_id


async def test_a_grant_is_redeemed_by_any_address_at_the_granted_domain(
    store: OnboardStore,
) -> None:
    """The invitation goes to whoever asked; the colleague who runs the installer is identified by
    the same grant, so a company is never asked to pass a secret between two inboxes."""
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    accepted = await invites.redeem("acme.com", uuid4())
    assert isinstance(accepted, InviteAccepted)


async def test_a_grant_refuses_every_other_domain(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    assert isinstance(await invites.redeem("elsewhere.com", uuid4()), InviteUnknown)


async def test_consumed_invite_is_refused_on_second_redeem(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    assert isinstance(await invites.redeem("acme.com", uuid4()), InviteAccepted)
    assert isinstance(await invites.redeem("acme.com", uuid4()), InviteConsumed)


async def test_mint_refuses_a_second_live_grant_for_an_object(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    with pytest.raises(InviteError, match="object #7 already holds a live invite"):
        await invites.mint(7, "other@second.com")


async def test_mint_refuses_a_second_live_grant_for_a_domain(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    with pytest.raises(InviteError, match=r"acme\.com already holds a live invite"):
        await invites.mint(8, "someone.else@acme.com")


async def test_mint_refuses_an_address_that_could_never_sign_in(store: OnboardStore) -> None:
    """The claim flow rejects free and disposable domains, so granting one would mint a grant no
    member could ever redeem."""
    invites = InviteCodes(pool=store.pool)
    with pytest.raises(WorkEmailError, match="not a work email domain"):
        await invites.mint(7, "someone@gmail.com")
    with pytest.raises(WorkEmailError, match="malformed"):
        await invites.mint(7, "not-an-email")


async def test_live_grant_uniqueness_is_database_enforced(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool)
    await invites.mint(8, "founder@acme.com")
    with pytest.raises(asyncpg.UniqueViolationError):
        await store.pool.execute(
            "insert into ufo_control.invite_code"
            " (id, object_number, email, email_domain, expires_at)"
            " values ($1, 9, 'other@acme.com', 'acme.com', now() + interval '1 day')",
            uuid4(),
        )


async def test_mint_reissues_after_expiry_but_not_after_identification(
    store: OnboardStore,
) -> None:
    await InviteCodes(pool=store.pool, ttl=timedelta(days=-1)).mint(7, "founder@acme.com")
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    assert isinstance(await invites.redeem("acme.com", uuid4()), InviteAccepted)
    with pytest.raises(InviteError, match="already identified"):
        await invites.mint(7, "founder@acme.com")


async def test_expired_grant_reports_its_expiry(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool, ttl=timedelta(days=-1))
    minted = await invites.mint(7, "founder@acme.com")
    outcome = await invites.redeem("acme.com", uuid4())
    assert isinstance(outcome, InviteExpired)
    assert outcome.expires_at == minted.expires_at


async def test_an_ungranted_domain_is_not_recognized(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool)
    assert isinstance(await invites.redeem("nobody.com", uuid4()), InviteUnknown)


async def test_a_consumed_grant_reports_consumed_even_after_expiry(store: OnboardStore) -> None:
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    assert isinstance(await invites.redeem("acme.com", uuid4()), InviteAccepted)
    await store.pool.execute(
        "update ufo_control.invite_code set expires_at = now() - interval '1 day'"
    )
    assert isinstance(await invites.redeem("acme.com", uuid4()), InviteConsumed)
