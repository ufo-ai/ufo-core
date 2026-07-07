"""Reject free, personal, and disposable email domains so a tenant maps to a real organization.

Copy-adapted from metalcraft's `onboard/email_domain.py`. The denylist always fails CLOSED; a
malformed address is rejected up front."""

import re
from dataclasses import dataclass

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
