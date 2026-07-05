import base64
from uuid import uuid4

import pytest

from selfhost.sandbox.session import RunToken


def _basic(username: str) -> str:
    """The Proxy-Authorization header an HTTP client sends for `http://<username>:@host` — Basic
    base64 of `username:` (empty password), exactly what the container's proxy URL produces."""
    return "Basic " + base64.b64encode(f"{username}:".encode()).decode()


def test_run_token_round_trips_encode_then_proxy_auth() -> None:
    token = RunToken(workspace_id=uuid4(), turn_id=uuid4())
    assert RunToken.from_proxy_auth(_basic(token.encode())) == token


def test_encoded_token_is_url_safe_userinfo() -> None:
    encoded = RunToken(workspace_id=uuid4(), turn_id=uuid4()).encode()
    assert all(char.isalnum() or char in "-_" for char in encoded)


def test_from_proxy_auth_rejects_non_basic_scheme() -> None:
    with pytest.raises(ValueError, match="basic"):
        RunToken.from_proxy_auth("Bearer " + RunToken(uuid4(), uuid4()).encode())


def test_from_proxy_auth_rejects_missing_header() -> None:
    with pytest.raises(ValueError, match="basic"):
        RunToken.from_proxy_auth("")


def test_from_proxy_auth_rejects_a_malformed_run_token() -> None:
    with pytest.raises(ValueError):
        RunToken.from_proxy_auth(_basic("not-a-run-token"))
