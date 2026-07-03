import base64
import shutil
import subprocess
from collections.abc import Iterator
from uuid import uuid4

import pytest

from selfhost.sandbox.carrier import DockerCarrier
from selfhost.sandbox.session import (
    MAX_READ_BYTES,
    RunToken,
    SandboxHandle,
    SandboxSession,
)

SANDBOX_TEST_IMAGE = "python:3.12-slim"


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


@pytest.fixture(scope="module")
def sandbox_session() -> Iterator[SandboxSession]:
    """A live DockerCarrier-backed session so the read/write path is exercised end to end — the
    real `docker exec`, the real in-container base64, the real decode — never a fake."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", SANDBOX_TEST_IMAGE, "sleep", "infinity"],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"docker cannot run the sandbox image: {started.stderr.strip()}")
    container = started.stdout.strip()
    handle = SandboxHandle(conversation_id=uuid4(), container_id=container)
    try:
        yield SandboxSession(carrier=DockerCarrier(), handle=handle)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)


async def test_read_file_round_trips_binary_byte_for_byte(sandbox_session: SandboxSession) -> None:
    blob = bytes(range(256)) * 64
    await sandbox_session.write_file("bin.dat", blob)
    assert await sandbox_session.read_file("bin.dat") == blob


async def test_read_file_round_trips_multibyte_text(sandbox_session: SandboxSession) -> None:
    content = "héllo\nwörld\n".encode()
    await sandbox_session.write_file("notes.txt", content)
    assert await sandbox_session.read_file("notes.txt") == content


async def test_read_file_over_the_cap_is_refused(sandbox_session: SandboxSession) -> None:
    await sandbox_session.bash(f"truncate -s {MAX_READ_BYTES + 1} /workspace/big.dat")
    with pytest.raises(ValueError, match="exceeds max read size"):
        await sandbox_session.read_file("big.dat")


async def test_read_file_missing_raises_file_not_found(sandbox_session: SandboxSession) -> None:
    with pytest.raises(FileNotFoundError):
        await sandbox_session.read_file("absent.dat")
