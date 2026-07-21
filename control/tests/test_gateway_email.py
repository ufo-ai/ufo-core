import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    CONSOLE_EMAIL_MODE,
    DEFAULT_SES_REGION,
    EMAIL_MODE_ENV,
    PUBLIC_BASE_URL_ENV,
    SES_REGION_ENV,
    SES_SENDER_ENV,
    ConsoleEmailSender,
    SesCredentials,
    SesEmailSender,
    _parse_assume_role_credentials,
    _sigv4_headers,
    email_sender_from_env,
    invite_email,
    public_apex_host,
    verification_email,
)

STS_RESPONSE = """\
<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
  <AssumeRoleWithWebIdentityResult>
    <SubjectFromWebIdentityToken>system:serviceaccount:ufo-system:ufo-gateway</SubjectFromWebIdentityToken>
    <AssumedRoleUser>
      <Arn>arn:aws:sts::111122223333:assumed-role/ufo-testing-gateway-ses/ufo-gateway-email</Arn>
      <AssumedRoleId>AROAEXAMPLE:ufo-gateway-email</AssumedRoleId>
    </AssumedRoleUser>
    <Credentials>
      <SessionToken>IQoJb3JpZ2luX2VjEXAMPLETOKEN</SessionToken>
      <SecretAccessKey>wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY</SecretAccessKey>
      <Expiration>2026-07-10T18:00:00Z</Expiration>
      <AccessKeyId>ASIAEXAMPLE</AccessKeyId>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
  <ResponseMetadata>
    <RequestId>c6104cbe-af31-11e0-8154-cbc7ccf896c7</RequestId>
  </ResponseMetadata>
</AssumeRoleWithWebIdentityResponse>
"""


def test_public_apex_host_strips_scheme_and_trailing_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PUBLIC_BASE_URL_ENV, "https://testing.flyingobject.ai/")
    assert public_apex_host() == "testing.flyingobject.ai"
    monkeypatch.delenv(PUBLIC_BASE_URL_ENV)
    assert public_apex_host() == "flyingobject.ai"


def test_public_apex_host_rejects_a_schemeless_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PUBLIC_BASE_URL_ENV, "flyingobject.ai")
    with pytest.raises(RuntimeError, match=PUBLIC_BASE_URL_ENV):
        public_apex_host()


def test_verification_email_states_the_exact_expiry() -> None:
    subject, body = verification_email(
        "042042", datetime(2026, 7, 12, 18, 45, tzinfo=UTC), timedelta(minutes=15)
    )
    assert subject == "Your flyingobject.ai verification code"
    assert body == "Your code: 042042. Expires 18:45 UTC (15 minutes)."


def test_invite_email_renders_the_ledger() -> None:
    subject, body = invite_email(
        object_number=7,
        code="abcd-efgh-jkmn",
        expires_at=datetime(2026, 7, 26, 18, 45, tzinfo=UTC),
        apex_host="flyingobject.ai",
    )
    assert subject == "identification granted"
    assert body == (
        "  object:   #7 → identified\n"
        "  code:     abcd-efgh-jkmn\n"
        "  expires:  2026-07-26 18:45 UTC\n"
        "\n"
        "  curl -fsSL https://flyingobject.ai/ufo | sh\n"
        "\n"
        "  Your code identifies one company. You'll receive\n"
        "  3 more when your fleet is live.\n"
    )


def test_parse_assume_role_credentials() -> None:
    credentials = _parse_assume_role_credentials(STS_RESPONSE)
    assert credentials == SesCredentials(
        access_key="ASIAEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        session_token="IQoJb3JpZ2luX2VjEXAMPLETOKEN",
    )


def test_parse_assume_role_credentials_missing_field_raises() -> None:
    truncated = STS_RESPONSE.replace("<AccessKeyId>ASIAEXAMPLE</AccessKeyId>", "")
    with pytest.raises(RuntimeError, match="missing AccessKeyId"):
        _parse_assume_role_credentials(truncated)


def test_sigv4_headers_sign_the_session_token() -> None:
    credentials = SesCredentials(
        access_key="ASIAEXAMPLE", secret_key="secret", session_token="token"
    )
    headers = _sigv4_headers(
        "email.us-east-1.amazonaws.com",
        b'{"FromEmailAddress": "no-reply@flyingobject.ai"}',
        "us-east-1",
        credentials,
        datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC),
    )
    assert headers["x-amz-security-token"] == "token"
    assert headers["x-amz-date"] == "20260710T120000Z"
    assert headers["authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=ASIAEXAMPLE/20260710/us-east-1/ses/aws4_request, "
    )
    assert "x-amz-security-token" in headers["authorization"].split("SignedHeaders=")[1]


async def test_send_surfaces_the_ses_denial_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("sts."):
            return httpx.Response(200, text=STS_RESPONSE)
        return httpx.Response(
            403, json={"Message": "not authorized on identity/no-reply@flyingobject.ai"}
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    token_file = tmp_path / "token"
    token_file.write_text("projected-token\n")
    sender = SesEmailSender(
        source="no-reply@flyingobject.ai",
        region="us-east-1",
        role_arn="arn:aws:iam::111122223333:role/ufo-testing-gateway-ses",
        token_file=token_file,
    )
    with pytest.raises(RuntimeError, match="identity/no-reply@flyingobject"):
        await sender.send(
            "member@example.com",
            "042042",
            datetime(2026, 7, 16, 16, 10, tzinfo=UTC),
            timedelta(minutes=15),
        )


async def test_assume_role_surfaces_the_sts_error_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<Error><Code>ExpiredTokenException</Code></Error>")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    token_file = tmp_path / "token"
    token_file.write_text("projected-token\n")
    sender = SesEmailSender(
        source="no-reply@flyingobject.ai",
        region="us-east-1",
        role_arn="arn:aws:iam::111122223333:role/ufo-testing-gateway-ses",
        token_file=token_file,
    )
    with pytest.raises(RuntimeError, match="ExpiredTokenException"):
        await sender.send(
            "member@example.com",
            "042042",
            datetime(2026, 7, 16, 16, 10, tzinfo=UTC),
            timedelta(minutes=15),
        )


def test_email_sender_from_env_builds_ses_from_irsa(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("projected-token\n")
    monkeypatch.setenv(SES_SENDER_ENV, "no-reply@flyingobject.ai")
    monkeypatch.delenv(SES_REGION_ENV, raising=False)
    monkeypatch.setenv(AWS_ROLE_ARN_ENV, "arn:aws:iam::111122223333:role/ufo-testing-gateway-ses")
    monkeypatch.setenv(AWS_WEB_IDENTITY_TOKEN_FILE_ENV, str(token_file))
    sender = email_sender_from_env()
    assert sender == SesEmailSender(
        source="no-reply@flyingobject.ai",
        region=DEFAULT_SES_REGION,
        role_arn="arn:aws:iam::111122223333:role/ufo-testing-gateway-ses",
        token_file=token_file,
    )


def test_email_sender_from_env_fails_loud_without_irsa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SES_SENDER_ENV, "no-reply@flyingobject.ai")
    monkeypatch.delenv(AWS_ROLE_ARN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=AWS_ROLE_ARN_ENV):
        email_sender_from_env()


async def test_console_mode_logs_the_code_and_needs_no_ses(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(EMAIL_MODE_ENV, CONSOLE_EMAIL_MODE)
    monkeypatch.delenv(SES_SENDER_ENV, raising=False)
    monkeypatch.delenv(AWS_ROLE_ARN_ENV, raising=False)
    sender = email_sender_from_env()
    assert isinstance(sender, ConsoleEmailSender)
    with caplog.at_level(logging.INFO):
        await sender.send(
            "boss@webco.io",
            "424242",
            datetime(2026, 7, 16, 16, 10, tzinfo=UTC),
            timedelta(minutes=15),
        )
    assert "424242" in caplog.text
    assert "boss@webco.io" in caplog.text


def test_email_sender_from_env_rejects_an_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_MODE_ENV, "carrier-pigeon")
    with pytest.raises(RuntimeError, match=EMAIL_MODE_ENV):
        email_sender_from_env()
