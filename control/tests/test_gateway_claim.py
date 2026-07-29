"""The claim state machine against the real `onboard_claim` table (Postgres): the work-email
denylist, hash-only storage, the TTL, the attempt cap, and the recording email sender fake that lets
a test read the minted code back."""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from ufo_control.gateway_claim import ClaimError, ClaimWorkflow, hash_code
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

CODE_IN_BODY = re.compile(r"\d{6}")


@dataclass
class RecordingSender:
    sent: dict[str, str] = field(default_factory=dict)

    async def send(self, email: str, subject: str, text: str) -> None:
        """The sender is handed a rendered message, never a code, so the code is read back out of
        the body the way a member reads it."""
        found = CODE_IN_BODY.search(text)
        assert found is not None
        self.sent[email] = found.group()

    def last_code(self, email: str) -> str:
        return self.sent[email]


class FailingSender:
    async def send(self, email: str, subject: str, text: str) -> None:
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
    with pytest.raises(ClaimError, match="Too many attempts"):
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
    with pytest.raises(ClaimError, match="Could not send"):
        await workflow.start("me@acme.com", "ufo", "sess-1")
    assert await store.live_claim("ufo", "sess-1") is None


async def test_invite_redeems_once_and_stamps_the_claim(store: OnboardStore) -> None:
    sender = RecordingSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    invites = InviteCodes(pool=store.pool)
    await invites.mint(7, "founder@acme.com")
    accepted = await invites.redeem(claim.email_domain, claim.claim_id)
    assert isinstance(accepted, InviteAccepted)
    assert accepted.object_number == 7
    assert accepted.consumed_at.tzinfo is not None
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
