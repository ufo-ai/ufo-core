import base64
from pathlib import Path
from uuid import uuid4

import pytest

from selfhost.sandbox import session as session_module
from selfhost.sandbox.session import SANDBOX_GID, SANDBOX_UID, RunToken


def test_dockerfile_pins_the_sandbox_uid_to_the_constant() -> None:
    """serve chowns the workspace mount to SANDBOX_UID/GID so the non-root sandbox user can write
    it; that only holds if the image's sandbox user really has those ids. This gate keeps the
    Dockerfile's build args and the constant from drifting apart."""
    dockerfile = (Path(session_module.__file__).parent / "image" / "Dockerfile").read_text()
    assert f"ARG SANDBOX_UID={SANDBOX_UID}" in dockerfile
    assert f"ARG SANDBOX_GID={SANDBOX_GID}" in dockerfile


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
