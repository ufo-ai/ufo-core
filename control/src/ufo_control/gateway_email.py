"""Work-email policy and the verification-code sender.

`WorkEmailPolicy` rejects free, personal, and disposable domains so a tenant maps to a real
organization — the denylist fails CLOSED and a malformed address is rejected up front. The sender
delivers the 6-digit code: `logging` for dev/tests, `ses` for the hosted apex. The SES backend
speaks SESv2 `SendEmail` over `httpx` with a local SigV4 signer — signing is pure CPU (hmac/sha256)
so it runs inline, and the send itself is async: no boto3 network client, no sync HTTP on the loop.
The backend is selected by `UFO_GATEWAY_EMAIL_BACKEND`, failing loud on a missing SES field.
Copy-adapted from metalcraft's `onboard/email_domain.py` and `onboard/email_sender.py`."""

import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

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

CODE_SUBJECT = "Your flyingobject.ai verification code"
CODE_BODY = "Your flyingobject.ai verification code is {code}. It expires shortly."
SES_SERVICE = "ses"
SES_PATH = "/v2/email/outbound-emails"
SES_TIMEOUT_SECONDS = 10.0

EMAIL_BACKEND_ENV = "UFO_GATEWAY_EMAIL_BACKEND"
SES_SENDER_ENV = "UFO_SES_SENDER"
SES_REGION_ENV = "UFO_SES_REGION"
AWS_ACCESS_KEY_ENV = "AWS_ACCESS_KEY_ID"
AWS_SECRET_KEY_ENV = "AWS_SECRET_ACCESS_KEY"
AWS_SESSION_TOKEN_ENV = "AWS_SESSION_TOKEN"
DEFAULT_SES_REGION = "us-east-1"


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


@dataclass(frozen=True)
class SentCode:
    email: str
    code: str


class EmailSender(Protocol):
    async def send(self, email: str, code: str) -> None: ...


@dataclass
class LoggingEmailSender:
    """Dev/test sender: logs the code and keeps every send in `sent` so tests read the minted code
    without a real mailbox. Never used in a hosted deployment."""

    sent: list[SentCode] = field(default_factory=list)

    async def send(self, email: str, code: str) -> None:
        self.sent.append(SentCode(email, code))
        logger.info("onboard verification code for %s: %s", email, code)

    def last_code(self, email: str) -> str:
        for record in reversed(self.sent):
            if record.email == email:
                return record.code
        raise LookupError(f"no code sent to {email}")


@dataclass(frozen=True)
class SesCredentials:
    access_key: str
    secret_key: str
    session_token: str | None


@dataclass(frozen=True)
class SesEmailSender:
    """SESv2 `SendEmail` via `httpx` with a local SigV4 signer. `source` is the verified From
    address; `region` selects the SES endpoint."""

    source: str
    region: str
    credentials: SesCredentials

    async def send(self, email: str, code: str) -> None:
        body = json.dumps(
            {
                "FromEmailAddress": self.source,
                "Destination": {"ToAddresses": [email]},
                "Content": {
                    "Simple": {
                        "Subject": {"Data": CODE_SUBJECT},
                        "Body": {"Text": {"Data": CODE_BODY.format(code=code)}},
                    }
                },
            }
        ).encode()
        host = f"email.{self.region}.amazonaws.com"
        headers = _sigv4_headers(host, body, self.region, self.credentials, datetime.now(UTC))
        async with httpx.AsyncClient(timeout=SES_TIMEOUT_SECONDS) as client:
            response = await client.post(f"https://{host}{SES_PATH}", content=body, headers=headers)
            response.raise_for_status()


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
    }
    if credentials.session_token:
        headers["x-amz-security-token"] = credentials.session_token
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


def email_sender_from_env() -> EmailSender:
    backend = os.environ.get(EMAIL_BACKEND_ENV, "logging")
    if backend == "logging":
        return LoggingEmailSender()
    if backend == "ses":
        return SesEmailSender(
            source=_require_env(SES_SENDER_ENV),
            region=os.environ.get(SES_REGION_ENV, DEFAULT_SES_REGION),
            credentials=SesCredentials(
                access_key=_require_env(AWS_ACCESS_KEY_ENV),
                secret_key=_require_env(AWS_SECRET_KEY_ENV),
                session_token=os.environ.get(AWS_SESSION_TOKEN_ENV),
            ),
        )
    raise RuntimeError(f"{EMAIL_BACKEND_ENV}={backend!r} is not a known backend (ses|logging)")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset — required for the ses email backend")
    return value
