import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from ufo.db import dispose_db, init_db

from ufo_control.gateway import (
    INVITE_REQUIRED_ENV,
    STAMPED_SCRIPT,
    Onboarding,
    _invite_required,
    _stamp_script,
)
from ufo_control.gateway_claim import ClaimWorkflow, hash_code
from ufo_control.gateway_email import WorkEmailPolicy
from ufo_control.gateway_invite import InviteAccepted, InviteCodes
from ufo_control.gateway_shared import SharedWorkspaces
from ufo_control.gateway_store import OnboardClaim, OnboardStore

RAW_SCRIPT = 'UFO_SCRIPT_VERSION=dev\nUFO_URL="${UFO_URL:-https://flyingobject.ai}"\n'
WORKSPACE_URL = "https://app.flyingobject.ai"
TOKEN_SECRET = "test-secret"


class UnusedSender:
    async def send(self, email: str, subject: str, text: str) -> None:
        raise AssertionError


def test_stamp_substitutes_version_and_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_PUBLIC_BASE_URL", "https://testing.flyingobject.ai")
    stamped = _stamp_script(RAW_SCRIPT)
    assert "UFO_SCRIPT_VERSION=dev" not in stamped
    assert re.search(r"UFO_SCRIPT_VERSION=[0-9a-f]{12}", stamped) is not None
    assert 'UFO_URL="${UFO_URL:-https://testing.flyingobject.ai}"' in stamped


def test_shipped_script_is_stamped_and_targets_chat() -> None:
    assert "UFO_SCRIPT_VERSION=dev" not in STAMPED_SCRIPT
    assert re.search(r"UFO_SCRIPT_VERSION=[0-9a-f]{12}", STAMPED_SCRIPT) is not None
    assert "surface/ufo/" in STAMPED_SCRIPT


def test_invite_required_defaults_on_and_fails_loud_on_non_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(INVITE_REQUIRED_ENV, raising=False)
    assert _invite_required() is True
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "1")
    assert _invite_required() is True
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "false")
    assert _invite_required() is False
    monkeypatch.setenv(INVITE_REQUIRED_ENV, "yes")
    with pytest.raises(RuntimeError, match="not a boolean"):
        _invite_required()


async def test_onboarding_resolves_claim_verified_by_concurrent_turn(
    store: OnboardStore,
    gateway_postgres: str,
) -> None:
    email = "pilot@concurrent.io"
    domain = "concurrent.io"
    code = "123456"
    await store.insert_claim(
        OnboardClaim(
            claim_id=uuid4(),
            email=email,
            email_domain=domain,
            code_hash=hash_code(code),
            surface="ufo",
            surface_ref="sess",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            attempts=0,
            verified_at=None,
            invite_id=None,
        )
    )
    stale = await store.live_claim("ufo", "sess")
    assert stale is not None
    claims = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), email_sender=UnusedSender())
    invites = InviteCodes(pool=store.pool)
    await invites.mint(1, email)
    await claims.verify(stale, code)
    flow = Onboarding(
        claims=claims,
        store=store,
        workspaces=SharedWorkspaces(workspace_url=WORKSPACE_URL, pool=store.pool),
        invites=invites,
        token_secret=TOKEN_SECRET,
        apex_host="flyingobject.ai",
        invite_required=True,
    )
    assert isinstance(await invites.redeem(domain, stale.claim_id), InviteAccepted)
    assert await flow._invite_gate(stale, b"") is None
    init_db(gateway_postgres.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        signed_in = await flow._verify_code(stale, "000000", b"")
    finally:
        await dispose_db()
    directives = dict(
        line.split("\t", 1) for line in signed_in.decode().splitlines() if "\t" in line
    )
    assert directives["workspace"] == WORKSPACE_URL
    assert "token" in directives
    assert await flow.workspaces.exists(domain)
