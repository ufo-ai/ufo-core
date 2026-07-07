"""The claim state machine against the real store (SQLite + Postgres via the shared `db` fixture):
the work-email denylist, hash-only storage, the TTL, the attempt cap, and the logging email backend
that lets a test read the minted code back."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_gateway.claim import ClaimError, ClaimWorkflow, hash_code
from ufo_ext_gateway.email_domain import WorkEmailError, WorkEmailPolicy
from ufo_ext_gateway.email_sender import LoggingEmailSender
from ufo_ext_gateway.store import OnboardStore

from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _store() -> OnboardStore:
    return OnboardStore(context_for(await _workspace(), "gateway", frozenset(), None))


async def test_start_persists_only_the_hash_and_emails_the_code() -> None:
    store = await _store()
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


async def test_denylist_rejects_a_free_email_before_any_write() -> None:
    store = await _store()
    workflow = ClaimWorkflow(
        store=store, email_policy=WorkEmailPolicy(), email_sender=LoggingEmailSender()
    )
    with pytest.raises(WorkEmailError):
        await workflow.start("someone@gmail.com", "ufo", "sess-1")
    assert await store.live_claim("ufo", "sess-1") is None


async def test_verify_accepts_the_right_code_and_marks_verified() -> None:
    store = await _store()
    sender = LoggingEmailSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    await workflow.verify(claim, sender.last_code("me@acme.com"))
    verified = await store.live_claim("ufo", "sess-1")
    assert verified is not None and verified.verified_at is not None


async def test_verify_rejects_a_wrong_code() -> None:
    store = await _store()
    sender = LoggingEmailSender()
    workflow = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=sender)
    await workflow.start("me@acme.com", "ufo", "sess-1")
    claim = await store.live_claim("ufo", "sess-1")
    assert claim is not None
    with pytest.raises(ClaimError, match="incorrect"):
        await workflow.verify(claim, "000000")


async def test_attempt_cap_exhausts() -> None:
    store = await _store()
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


async def test_expired_code_is_rejected() -> None:
    store = await _store()
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
