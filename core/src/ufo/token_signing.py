"""HMAC-signed opaque tokens with a base64url payload."""

import base64
import hashlib
import hmac


class SignedTokenError(ValueError):
    """A signed token is malformed or its signature does not match."""


def sign_token(secret: bytes, payload: bytes) -> str:
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    digest = hmac.new(secret, body.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"{body}.{signature}"


def verify_token(token: str, secret: bytes) -> bytes:
    body, separator, signature = token.partition(".")
    if not separator or not signature:
        raise SignedTokenError("signed token is malformed")
    digest = hmac.new(secret, body.encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if not hmac.compare_digest(signature, expected):
        raise SignedTokenError("signed token signature does not match")
    try:
        return base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise SignedTokenError("signed token payload is unreadable") from error
