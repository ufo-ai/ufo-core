"""`ufo-control invite` against the real ledger: the grant lands and its invitation goes out through
the same sender a deploy uses, selected by `UFO_CONTROL_EMAIL_MODE`. The two orderings the verb
promises are proven here — mail configuration is read before the object spends its one live grant,
and a grant that outlives a failed send still stands."""

import logging

import click
import pytest

from ufo_control import main
from ufo_control.gateway_email import (
    CONSOLE_EMAIL_MODE,
    EMAIL_MODE_ENV,
    PUBLIC_BASE_URL_ENV,
    SES_SENDER_ENV,
)
from ufo_control.gateway_store import OnboardStore

APEX = "https://testing.flyingobject.ai"


class FailingSender:
    async def send(self, email: str, subject: str, text: str) -> None:
        raise RuntimeError("mail unavailable")


@pytest.fixture
def console_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_MODE_ENV, CONSOLE_EMAIL_MODE)
    monkeypatch.setenv(PUBLIC_BASE_URL_ENV, APEX)


async def test_invite_emails_the_invitation_to_the_granted_address(
    store: OnboardStore, console_email: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        minted = await main._mint_invite(2, "Founder@Acme.com")
    assert minted.email == "founder@acme.com"
    assert "identification granted" in caplog.text
    assert "founder@acme.com" in caplog.text
    assert f"curl -fsSL {APEX}/ufo | sh" in caplog.text
    row = await store.pool.fetchrow(
        "select object_number, email, email_domain, consumed_at from ufo_control.invite_code"
    )
    assert row is not None
    assert (row["object_number"], row["email"], row["email_domain"]) == (
        2,
        "founder@acme.com",
        "acme.com",
    )
    assert row["consumed_at"] is None


async def test_unconfigured_mail_refuses_before_the_grant_lands(
    store: OnboardStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(EMAIL_MODE_ENV, raising=False)
    monkeypatch.delenv(SES_SENDER_ENV, raising=False)
    with pytest.raises(RuntimeError, match=SES_SENDER_ENV):
        await main._mint_invite(3, "founder@acme.com")
    assert await store.pool.fetchval("select count(*) from ufo_control.invite_code") == 0


async def test_a_failed_send_leaves_the_grant_standing(
    store: OnboardStore, console_email: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "email_sender_from_env", FailingSender)
    with pytest.raises(click.ClickException, match="the grant stands"):
        await main._mint_invite(4, "founder@acme.com")
    row = await store.pool.fetchrow("select email_domain, consumed_at from ufo_control.invite_code")
    assert row is not None
    assert row["email_domain"] == "acme.com"
    assert row["consumed_at"] is None
