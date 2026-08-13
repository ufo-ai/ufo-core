from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fake_workos import MAGIC_CODE, FakeVerifier
from ufo.db import dispose_db, init_db

from ufo_control.gateway import (
    INVITE_REQUIRED_ENV,
    STAMPED_SCRIPT,
    Onboarding,
    _invite_required,
    _stamp_script,
)
from ufo_control.gateway_claim import ClaimWorkflow
from ufo_control.gateway_directives import CLIENT_VERSION_ENV, client_install
from ufo_control.gateway_email import WorkEmailPolicy
from ufo_control.gateway_invite import InviteAccepted, InviteCodes
from ufo_control.gateway_shared import SharedWorkspaces
from ufo_control.gateway_store import OnboardClaim, OnboardStore

RAW_SCRIPT = 'UFO_URL="${UFO_URL:-https://flyingobject.ai}"\n'
WORKSPACE_URL = "https://app.flyingobject.ai"
TOKEN_SECRET = "test-secret"


def test_stamp_substitutes_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UFO_PUBLIC_BASE_URL", "https://testing.flyingobject.ai")
    assert 'UFO_URL="${UFO_URL:-https://testing.flyingobject.ai}"' in _stamp_script(RAW_SCRIPT)


def test_shipped_script_installs_the_platform_binary() -> None:
    assert STAMPED_SCRIPT.startswith("#!/bin/sh")
    assert "/ufo/bin/$TARGET" in STAMPED_SCRIPT
    for target in (
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
        "x86_64-pc-windows-msvc",
    ):
        assert target in STAMPED_SCRIPT, target
    assert 'exec "$BIN" "$@"' in STAMPED_SCRIPT


def test_client_install_covers_first_run_and_stale_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CLIENT_VERSION_ENV, raising=False)
    assert client_install({}) == b"install\n"
    assert client_install({"x-ufo-installed": "0"}) == b"install\n"
    assert client_install({"x-ufo-installed": "1"}) == b""
    monkeypatch.setenv(CLIENT_VERSION_ENV, "0.2.0")
    assert client_install({"x-ufo-installed": "1", "x-ufo-script": "1a2b3c4d5e6f"}) == b"install\n"
    assert client_install({"x-ufo-installed": "1", "x-ufo-script": "0.2.0"}) == b""
    assert client_install({"X-UFO-Installed": "1", "X-UFO-Script": "0.2.0"}) == b""


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
    await store.insert_claim(
        OnboardClaim(
            claim_id=uuid4(),
            email=email,
            email_domain=domain,
            surface="ufo",
            surface_ref="sess",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            verified_at=None,
            invite_id=None,
        )
    )
    stale = await store.live_claim("ufo", "sess")
    assert stale is not None
    verifier = FakeVerifier(codes={email: MAGIC_CODE})
    claims = ClaimWorkflow(store=store, email_policy=WorkEmailPolicy(), verifier=verifier)
    invites = InviteCodes(pool=store.pool)
    await invites.mint(1, email)
    await claims.verify(stale, MAGIC_CODE)
    flow = Onboarding(
        claims=claims,
        store=store,
        workspaces=SharedWorkspaces(workspace_url=WORKSPACE_URL, pool=store.pool),
        invites=invites,
        verifier=verifier,
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
    assert len(await flow.workspaces.choices(domain, email)) == 1
