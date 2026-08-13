"""The verifier's two halves: the state codec that carries an onboarding session through the Google
hop, and how WorkOS's own answers are graded. The grading runs against the real SDK — its request
builder, its status-to-exception mapping, its models — with only the network scripted, because the
distinction the flow turns on (a code the member may retype versus a refusal) is read off the
error payload WorkOS actually sends, and the Google hop's provider string is the one the SDK builds
into the authorization URL WorkOS receives."""

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from workos import AsyncWorkOSClient

from ufo_control.gateway_workos import (
    AUTH_CALLBACK_PATH,
    AUTH_CONSOLE_PATH,
    CONSOLE_CODE,
    STATE_SEPARATOR,
    WORKOS_API_KEY_ENV,
    WORKOS_CLIENT_ID_ENV,
    WORKOS_MODE_ENV,
    WORKOS_REDIRECT_URI_ENV,
    AuthCarry,
    ConsoleVerifier,
    VerificationError,
    WorkosVerifier,
    console_signin_page,
    open_session,
    pack_state,
    seal_session,
    unpack_state,
    workos_console_mode,
    workos_verifier_from_env,
)

REPO = Path(__file__).parents[2]
REDIRECT_URI = f"https://app.test{AUTH_CALLBACK_PATH}"
CLIENT_ID = "client_01TEST"
EMAIL = "member@acme.com"
GATEWAY_SECRET = "ufo-gateway-workos"
STATE_SECRET = "state-signing-secret"
WORKOS_ENV_NAMES = (WORKOS_API_KEY_ENV, WORKOS_CLIENT_ID_ENV, WORKOS_REDIRECT_URI_ENV)
PROJECTION_ROOTS = frozenset({"core", "extensions", "packs", "evals"})

GRANT_REFUSED_BODY = {
    "error": "invalid_grant",
    "error_description": "The code '000000' has expired or is invalid.",
}

AUTHENTICATED_BODY = {
    "user": {
        "object": "user",
        "id": "user_01TEST",
        "first_name": None,
        "last_name": None,
        "profile_picture_url": None,
        "email": " Member@Acme.com ",
        "email_verified": True,
        "external_id": None,
        "last_sign_in_at": None,
        "created_at": "2026-08-12T00:00:00.000Z",
        "updated_at": "2026-08-12T00:00:00.000Z",
    },
    "access_token": "access",
    "refresh_token": "refresh",
}

MAGIC_AUTH_BODY = {
    "object": "magic_auth",
    "id": "magic_auth_01TEST",
    "user_id": "user_01TEST",
    "email": EMAIL,
    "expires_at": "2026-08-12T00:10:00.000Z",
    "created_at": "2026-08-12T00:00:00.000Z",
    "updated_at": "2026-08-12T00:00:00.000Z",
    "code": "654321",
}


def _verifier(status: int, body: Mapping[str, object]) -> WorkosVerifier:
    """The real client, answered by a scripted transport. `max_retries=0` keeps a scripted 5xx from
    spending the SDK's own backoff, which is the gateway's bounded-retry behaviour and not this
    test's subject."""
    client = AsyncWorkOSClient(api_key="sk_test_x", client_id=CLIENT_ID, max_retries=0)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    return WorkosVerifier(client=client, redirect_uri=REDIRECT_URI)


def test_state_survives_the_round_trip() -> None:
    carry = AuthCarry(
        session="0" * 36,
        conversation="123e4567-e89b-12d3-a456-426614174000",
        artifact="/artifacts/abc/report.html",
    )
    assert unpack_state(pack_state(carry, STATE_SECRET), STATE_SECRET) == carry


def test_state_drops_a_carry_that_is_not_its_shape() -> None:
    packed = pack_state(
        AuthCarry(session="s1", conversation="../../etc", artifact="https://evil.example/"),
        STATE_SECRET,
    )
    carry = unpack_state(packed, STATE_SECRET)
    assert carry.conversation is None
    assert carry.artifact is None


def test_state_refuses_a_missing_or_oversized_session() -> None:
    for session in ("", "x" * 129):
        carry = AuthCarry(session=session, conversation=None, artifact=None)
        with pytest.raises(ValueError):
            unpack_state(pack_state(carry, STATE_SECRET), STATE_SECRET)


def test_state_refuses_garbage() -> None:
    with pytest.raises(ValueError):
        unpack_state("not-base64-json", STATE_SECRET)


def test_state_refuses_a_carry_this_gateway_did_not_sign() -> None:
    """The session the state names is what a verified email is written under, so the state has to be
    one this gateway packed: an unsigned carry, a body swapped under a signature that was ours, and
    a signature from another secret each name nothing."""
    carry = AuthCarry(session="minted", conversation=None, artifact=None)
    packed = pack_state(carry, STATE_SECRET)
    body, _, signature = packed.partition(STATE_SEPARATOR)
    chosen = pack_state(AuthCarry(session="chosen", conversation=None, artifact=None), STATE_SECRET)
    swapped = f"{chosen.partition(STATE_SEPARATOR)[0]}{STATE_SEPARATOR}{signature}"
    for raw in (body, swapped, pack_state(carry, "another-secret")):
        with pytest.raises(ValueError):
            unpack_state(raw, STATE_SECRET)
    assert unpack_state(packed, STATE_SECRET) == carry


def test_a_sealed_cookie_opens_only_under_our_signature() -> None:
    """The `__Host-ufo_onboard` cookie carries the minted session under a signature, so a value the
    gateway did not seal opens to nothing and keys no claim: the sealed value round-trips, but a
    raw id a caller chose, a session swapped under a signature that was ours, and a seal from
    another secret each open to None."""
    sealed = seal_session("minted", STATE_SECRET)
    assert open_session(sealed, STATE_SECRET) == "minted"
    swapped = f"chosen{STATE_SEPARATOR}{sealed.partition(STATE_SEPARATOR)[2]}"
    for value in ("chosen", swapped, seal_session("minted", "another-secret")):
        assert open_session(value, STATE_SECRET) is None


def test_verifier_from_env_fails_loud_without_each_of_the_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The redirect URI is stated, never derived: the origin that routes the callback here is the
    app host in a hosted deploy and the gateway's own port locally, so a half-configured deploy
    dies naming the value it lacks rather than sending members to a URI WorkOS refuses."""
    for name in WORKOS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in (
        (WORKOS_API_KEY_ENV, "sk_test_x"),
        (WORKOS_CLIENT_ID_ENV, CLIENT_ID),
        (WORKOS_REDIRECT_URI_ENV, REDIRECT_URI),
    ):
        with pytest.raises(RuntimeError, match=name):
            workos_verifier_from_env()
        monkeypatch.setenv(name, value)
    verifier = workos_verifier_from_env()
    assert isinstance(verifier, WorkosVerifier)
    assert verifier.redirect_uri == REDIRECT_URI


def test_console_mode_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local dev: `WORKOS_MODE=console` boots the gateway with no WorkOS keys at all, so
    `docker compose up` needs nothing to sign a member in."""
    for name in WORKOS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(WORKOS_MODE_ENV, "console")
    assert workos_console_mode() is True
    assert isinstance(workos_verifier_from_env(), ConsoleVerifier)


async def test_the_console_verifier_fakes_only_the_identity_proof() -> None:
    """The browser hop lands on the local page, the terminal code is the fixed dev code, and the
    exchanged address is whatever the dev typed, lowered — everything past the proof is real."""
    verifier = ConsoleVerifier()
    assert verifier.authorization_url("st.sig").startswith(f"{AUTH_CONSOLE_PATH}?state=")
    assert await verifier.exchange(" Founder@Dev.IO ") == "founder@dev.io"
    await verifier.begin("someone@dev.io")
    assert await verifier.confirm("someone@dev.io", CONSOLE_CODE) is True
    assert await verifier.confirm("someone@dev.io", "999999") is False


def test_the_console_page_posts_the_state_and_email_to_the_callback() -> None:
    page = console_signin_page("st.sig")
    assert f'action="{AUTH_CALLBACK_PATH}"' in page
    assert 'name="state" value="st.sig"' in page
    assert 'name="code"' in page


def test_an_unknown_workos_mode_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKOS_MODE_ENV, "banana")
    with pytest.raises(RuntimeError, match=WORKOS_MODE_ENV):
        workos_verifier_from_env()


def test_the_authorization_url_names_google_the_callback_and_the_state() -> None:
    """The `Continue with Google` hop names `provider=GoogleOAuth`, so WorkOS routes straight to
    Google with no hosted page in between. The real SDK builds the URL, so the provider string is
    the one WorkOS actually receives."""
    url = _verifier(200, AUTHENTICATED_BODY).authorization_url("packed-state")
    assert "provider=GoogleOAuth" in url
    assert "state=packed-state" in url
    assert f"client_id={CLIENT_ID}" in url
    assert "response_type=code" in url
    assert f"redirect_uri=https%3A%2F%2Fapp.test{AUTH_CALLBACK_PATH.replace('/', '%2F')}" in url


async def test_a_code_workos_will_not_redeem_is_a_wrong_code() -> None:
    """WorkOS answers `invalid_grant` whether the digits are wrong, expired, or already spent, so
    the flow reads it as a code the member may retype. Reading it as a dead claim instead would
    destroy the session on a typo, which is the common case and expiry the rare one."""
    assert await _verifier(400, GRANT_REFUSED_BODY).confirm(EMAIL, "000000") is False


async def test_a_confirmed_code_verifies_the_email() -> None:
    assert await _verifier(200, AUTHENTICATED_BODY).confirm(EMAIL, "654321") is True


async def test_a_workos_fault_refuses_rather_than_grading_the_code() -> None:
    with pytest.raises(VerificationError, match="Sign-in failed"):
        await _verifier(500, {"message": "unavailable"}).confirm(EMAIL, "654321")


async def test_a_malformed_request_refuses_rather_than_grading_the_code() -> None:
    with pytest.raises(VerificationError, match="Sign-in failed"):
        await _verifier(
            400, {"error": "invalid_request", "error_description": "Missing email."}
        ).confirm(EMAIL, "654321")


async def test_exchange_answers_the_lowered_email() -> None:
    assert await _verifier(200, AUTHENTICATED_BODY).exchange("code") == EMAIL


async def test_exchange_refuses_a_code_workos_does_not_know() -> None:
    with pytest.raises(VerificationError, match="Sign-in failed"):
        await _verifier(400, GRANT_REFUSED_BODY).exchange("code")


async def test_begin_sends_the_code() -> None:
    await _verifier(200, MAGIC_AUTH_BODY).begin(EMAIL)


async def test_begin_refuses_when_workos_cannot_send() -> None:
    with pytest.raises(VerificationError, match="Could not send the verification code"):
        await _verifier(422, {"message": "unprocessable"}).begin(EMAIL)


def test_only_the_gateway_deployment_receives_the_workos_credentials() -> None:
    """WorkOS is reached from the one pod that signs members in. The serve fleet never holds the
    key, so a workspace's own containers cannot mint a verified email, and no projection under
    core, extensions, packs, or evals names the variables at all."""
    hosted = (REPO / "infra/templates/hosted.yaml.tpl").read_text()
    carrying = [
        document
        for document in hosted.split("\n---\n")
        if any(name in document for name in WORKOS_ENV_NAMES)
    ]
    assert len(carrying) == 1
    gateway_deployment = carrying[0]
    assert "kind: Deployment" in gateway_deployment
    assert "name: ufo-gateway" in gateway_deployment
    assert "envFrom" not in gateway_deployment
    for name in (WORKOS_API_KEY_ENV, WORKOS_CLIENT_ID_ENV):
        assert f"secretKeyRef: {{name: {GATEWAY_SECRET}, key: {name}}}" in gateway_deployment

    services = (REPO / "infra/templates/cluster-services.yaml.tpl").read_text()
    platform_secrets = [
        document
        for document in services.split("\n---\n")
        if "name: ufo-platform-secrets" in document
    ]
    assert len(platform_secrets) == 1
    assert "workos" not in platform_secrets[0].lower()

    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    projections = [
        name
        for name in tracked.stdout.split("\0")
        if name
        and name.split("/")[0] in PROJECTION_ROOTS
        and any(
            variable in (REPO / name).read_text(errors="ignore") for variable in WORKOS_ENV_NAMES
        )
    ]
    assert projections == []


def test_the_hosted_redirect_uri_is_the_app_host_and_the_local_one_is_not() -> None:
    """The callback has to arrive at the process that holds the claim. Hosted, the ingress routes
    `/v1/onboard` from the app host to the gateway, so the redirect is that host's own callback;
    locally nothing routes it off serve's port, so compose states the gateway's origin instead.
    Both are the same env var, which is why neither can be derived from the app host."""
    hosted = (REPO / "infra/templates/hosted.yaml.tpl").read_text()
    gateway_deployment = next(
        document for document in hosted.split("\n---\n") if WORKOS_REDIRECT_URI_ENV in document
    )
    app_host = _plain_env(gateway_deployment, "UFO_WORKSPACE_BASE_URL")
    assert (
        _plain_env(gateway_deployment, WORKOS_REDIRECT_URI_ENV) == f"{app_host}{AUTH_CALLBACK_PATH}"
    )

    compose = (REPO / "compose.yaml").read_text()
    gateway_service = next(
        block
        for block in re.split(r"\n(?=  [A-Za-z][\w.-]*:)", compose)
        if block.lstrip().startswith("gateway:")
    )
    for name in WORKOS_ENV_NAMES:
        assert f"{name}:" in gateway_service, name
    local_redirect = gateway_service.split(f"{WORKOS_REDIRECT_URI_ENV}:", maxsplit=1)[1]
    assert local_redirect.splitlines()[0].strip().endswith(AUTH_CALLBACK_PATH)


def _plain_env(document: str, name: str) -> str:
    entry = next(line for line in document.splitlines() if f"name: {name}," in line)
    return entry.split('value: "', maxsplit=1)[1].rsplit('"', maxsplit=1)[0]
