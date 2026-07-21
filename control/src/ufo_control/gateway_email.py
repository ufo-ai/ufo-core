"""Work-email policy and the verification-code sender.

`WorkEmailPolicy` rejects free, personal, and disposable domains so a workspace maps to a real
organization — the denylist fails CLOSED and a malformed address is rejected up front. The sender
delivers the 6-digit code through SESv2. The sender
speaks SESv2 `SendEmail` over `httpx` with a local SigV4 signer — signing is pure CPU (hmac/sha256)
so it runs inline, and every network call is async: no boto3 network client, no sync HTTP on the
loop. Credentials are the pod's IRSA web identity (`AWS_ROLE_ARN` + `AWS_WEB_IDENTITY_TOKEN_FILE`,
injected by the EKS pod identity webhook from the gateway ServiceAccount's annotation), exchanged
at STS per send. Missing SES configuration fails loud.

`UFO_CONTROL_EMAIL_MODE` picks the sender: `ses` (the default) is the SES delivery above; `console`
logs the code instead of sending it, so a local stack reads the code from the process log with no
SES account. An unrecognized mode fails loud."""

import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

EMAIL_PATTERN = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")

FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "ymail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "pm.me",
        "gmx.com",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "fastmail.com",
        "hey.com",
    }
)

DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "10minutemail.com",
        "tempmail.com",
        "temp-mail.org",
        "throwawaymail.com",
        "yopmail.com",
        "trashmail.com",
        "getnada.com",
        "dispostable.com",
        "sharklasers.com",
        "maildrop.cc",
    }
)

PUBLIC_BASE_URL_ENV = "UFO_PUBLIC_BASE_URL"
DEFAULT_PUBLIC_BASE_URL = "https://flyingobject.ai"

CODE_SUBJECT = "Your flyingobject.ai verification code"
CODE_BODY = "Your code: {code}. Expires {expires} UTC ({minutes} minutes)."

INVITE_SUBJECT = "identification granted"
INVITE_BODY = """\
  object:   #{object_number} → identified
  code:     {code}
  expires:  {expires} UTC

  curl -fsSL https://{apex_host}/ufo | sh

  Your code identifies one company. You'll receive
  3 more when your fleet is live.
"""

SES_SERVICE = "ses"
SES_PATH = "/v2/email/outbound-emails"
SES_TIMEOUT_SECONDS = 10.0
ERROR_BODY_CHARS = 1000

SES_SENDER_ENV = "UFO_SES_SENDER"
SES_REGION_ENV = "UFO_SES_REGION"
AWS_ROLE_ARN_ENV = "AWS_ROLE_ARN"
AWS_WEB_IDENTITY_TOKEN_FILE_ENV = "AWS_WEB_IDENTITY_TOKEN_FILE"
DEFAULT_SES_REGION = "us-east-1"

EMAIL_MODE_ENV = "UFO_CONTROL_EMAIL_MODE"
SES_EMAIL_MODE = "ses"
CONSOLE_EMAIL_MODE = "console"

logger = logging.getLogger(__name__)

STS_VERSION = "2011-06-15"
STS_NS = "{https://sts.amazonaws.com/doc/2011-06-15/}"
STS_SESSION_NAME = "ufo-gateway-email"
STS_SESSION_SECONDS = 900
STS_TIMEOUT_SECONDS = 10.0


class WorkEmailError(ValueError):
    """The email is not an acceptable work email — bad format or a denylisted domain."""


def normalize_email(email: str) -> tuple[str, str]:
    """Lowercased (address, domain). Raises WorkEmailError on a malformed address."""
    candidate = email.strip().lower()
    match = EMAIL_PATTERN.match(candidate)
    if match is None:
        raise WorkEmailError("email address is malformed")
    return candidate, match.group(1)


@dataclass(frozen=True)
class WorkEmailPolicy:
    denylist: frozenset[str] = FREE_EMAIL_DOMAINS | DISPOSABLE_EMAIL_DOMAINS

    def validate(self, email: str) -> str:
        _, domain = normalize_email(email)
        if domain in self.denylist:
            raise WorkEmailError(f"{domain} is not a work email domain")
        return domain


def public_apex_host() -> str:
    """The public front-door host from ``UFO_PUBLIC_BASE_URL``, path and scheme stripped."""
    base_url = os.environ.get(PUBLIC_BASE_URL_ENV, DEFAULT_PUBLIC_BASE_URL)
    host = urlsplit(base_url).netloc
    if not host:
        raise RuntimeError(
            f"{PUBLIC_BASE_URL_ENV} must be a base URL like {DEFAULT_PUBLIC_BASE_URL}"
        )
    return host


def verification_email(code: str, expires_at: datetime, ttl: timedelta) -> tuple[str, str]:
    """Subject and body for a verification-code delivery."""
    return CODE_SUBJECT, CODE_BODY.format(
        code=code,
        expires=expires_at.strftime("%H:%M"),
        minutes=int(ttl.total_seconds() // 60),
    )


def invite_email(
    object_number: int, code: str, expires_at: datetime, apex_host: str
) -> tuple[str, str]:
    """Subject and body for an invite-code delivery; ``ufo-control invite`` prints it once for
    the operator to send by hand."""
    return INVITE_SUBJECT, INVITE_BODY.format(
        object_number=object_number,
        code=code,
        expires=expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M"),
        apex_host=apex_host,
    )


class EmailSender(Protocol):
    async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None: ...


@dataclass(frozen=True)
class SesCredentials:
    access_key: str
    secret_key: str
    session_token: str


@dataclass(frozen=True)
class SesEmailSender:
    """SESv2 `SendEmail` via `httpx` with a local SigV4 signer. `source` is the verified From
    address; `region` selects the STS and SES endpoints. Credentials are the pod's IRSA web
    identity: the projected token at `token_file` is exchanged for `role_arn` at STS on every send
    (`AssumeRoleWithWebIdentity` is unsigned, so no bootstrap credential exists) — onboarding email
    is rare enough that a credential cache would be dead weight."""

    source: str
    region: str
    role_arn: str
    token_file: Path

    async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None:
        subject, text = verification_email(code, expires_at, ttl)
        credentials = await self._assume_role()
        body = json.dumps(
            {
                "FromEmailAddress": self.source,
                "Destination": {"ToAddresses": [email]},
                "Content": {
                    "Simple": {
                        "Subject": {"Data": subject},
                        "Body": {"Text": {"Data": text}},
                    }
                },
            }
        ).encode()
        host = f"email.{self.region}.amazonaws.com"
        headers = _sigv4_headers(host, body, self.region, credentials, datetime.now(UTC))
        async with httpx.AsyncClient(timeout=SES_TIMEOUT_SECONDS) as client:
            response = await client.post(f"https://{host}{SES_PATH}", content=body, headers=headers)
        if response.is_error:
            raise RuntimeError(
                f"SES SendEmail returned {response.status_code}: {response.text[:ERROR_BODY_CHARS]}"
            )

    async def _assume_role(self) -> SesCredentials:
        token = self.token_file.read_text().strip()
        async with httpx.AsyncClient(timeout=STS_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"https://sts.{self.region}.amazonaws.com/",
                data={
                    "Action": "AssumeRoleWithWebIdentity",
                    "Version": STS_VERSION,
                    "RoleArn": self.role_arn,
                    "RoleSessionName": STS_SESSION_NAME,
                    "WebIdentityToken": token,
                    "DurationSeconds": str(STS_SESSION_SECONDS),
                },
            )
        if response.is_error:
            raise RuntimeError(
                f"STS AssumeRoleWithWebIdentity returned {response.status_code}: "
                f"{response.text[:ERROR_BODY_CHARS]}"
            )
        return _parse_assume_role_credentials(response.text)


def _parse_assume_role_credentials(payload: str) -> SesCredentials:
    root = ElementTree.fromstring(payload)

    def credential(name: str) -> str:
        value = root.findtext(f".//{STS_NS}Credentials/{STS_NS}{name}")
        if not value:
            raise RuntimeError(f"STS AssumeRoleWithWebIdentity response is missing {name}")
        return value

    return SesCredentials(
        access_key=credential("AccessKeyId"),
        secret_key=credential("SecretAccessKey"),
        session_token=credential("SessionToken"),
    )


def _sigv4_headers(
    host: str, body: bytes, region: str, credentials: SesCredentials, now: datetime
) -> dict[str, str]:
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "content-type": "application/json",
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-security-token": credentials.session_token,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
    canonical_request = "\n".join(
        ["POST", SES_PATH, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{region}/{SES_SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signing_key = _signing_key(credentials.secret_key, date_stamp, region)
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def _signing_key(secret_key: str, date_stamp: str, region: str) -> bytes:
    key = f"AWS4{secret_key}".encode()
    for message in (date_stamp, region, SES_SERVICE, "aws4_request"):
        key = hmac.new(key, message.encode(), hashlib.sha256).digest()
    return key


@dataclass(frozen=True)
class ConsoleEmailSender:
    """A local `EmailSender` that logs the verification code instead of delivering it — the code the
    SES sender would email is read straight from the process log, so a local stack needs no SES
    account. Selected by `UFO_CONTROL_EMAIL_MODE=console`; a deploy that delivers real mail never
    sets it."""

    async def send(self, email: str, code: str, expires_at: datetime, ttl: timedelta) -> None:
        subject, text = verification_email(code, expires_at, ttl)
        logger.info("email (console mode) → %s | %s | %s", email, subject, text)


def email_sender_from_env() -> EmailSender:
    mode = os.environ.get(EMAIL_MODE_ENV, SES_EMAIL_MODE)
    if mode == CONSOLE_EMAIL_MODE:
        return ConsoleEmailSender()
    if mode == SES_EMAIL_MODE:
        return SesEmailSender(
            source=_require_env(SES_SENDER_ENV),
            region=os.environ.get(SES_REGION_ENV, DEFAULT_SES_REGION),
            role_arn=_require_env(AWS_ROLE_ARN_ENV),
            token_file=Path(_require_env(AWS_WEB_IDENTITY_TOKEN_FILE_ENV)),
        )
    raise RuntimeError(
        f"{EMAIL_MODE_ENV}={mode!r} is not a valid email mode "
        f"(expected {SES_EMAIL_MODE!r} or {CONSOLE_EMAIL_MODE!r})"
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset — required by the email sender")
    return value
