"""HMAC-signed opaque tokens with a base64url payload."""

import base64
import hashlib
import hmac


class SignedTokenError(ValueError):
    """A signed token is malformed or its signature does not match."""


def sign_detached(secret: bytes, message: bytes) -> str:
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def verify_detached(secret: bytes, message: bytes, signature: str) -> bool:
    return hmac.compare_digest(signature, sign_detached(secret, message))


def sign_token(secret: bytes, payload: bytes) -> str:
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{sign_detached(secret, body.encode())}"


def verify_token(token: str, secret: bytes) -> bytes:
    body, separator, signature = token.partition(".")
    if not separator or not signature:
        raise SignedTokenError("signed token is malformed")
    if not verify_detached(secret, body.encode(), signature):
        raise SignedTokenError("signed token signature does not match")
    try:
        return base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
    except ValueError as error:
        raise SignedTokenError("signed token payload is unreadable") from error
