from datetime import UTC, datetime
from pathlib import Path

import pytest

from ufo_control.gateway_email import (
    AWS_ROLE_ARN_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
    DEFAULT_SES_REGION,
    SES_REGION_ENV,
    SES_SENDER_ENV,
    SesCredentials,
    SesEmailSender,
    _parse_assume_role_credentials,
    _sigv4_headers,
    email_sender_from_env,
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
