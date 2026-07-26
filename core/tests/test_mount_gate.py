import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from ufo_ext_e2b import CA_INSTALL_TIMEOUT_SECONDS, CA_STAGING_PATH, INSTALL_CA_COMMAND

from sandbox import mount_gate
from sandbox.mount_gate import (
    EXERCISE,
    WORKSPACE_DIR,
    _mount_gate_recipe,
)
from ufo.sandbox.fs_creds import SANDBOX_FS_TOKEN_SECRET_ENV
from ufo.sandbox.fs_mount import (
    SANDBOX_FS_TOKEN_STAGING_PATH,
    install_token_command,
    mount_health_check,
    prepare_token_staging_command,
)
from ufo.sandbox.session import EGRESS_CA_CERT_ENV
from ufo.token_signing import verify_token

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class _Files:
    events: list[tuple[object, ...]]

    def make_dir(self, path: str) -> None:
        self.events.append(("make_dir", path))

    def write(self, path: str, data: str, *, user: str | None = None) -> None:
        self.events.append(("write", path, data, user))


@dataclass
class _Commands:
    events: list[tuple[object, ...]]

    def run(
        self, command: str, *, user: str | None = None, timeout: float | None = None
    ) -> SimpleNamespace:
        self.events.append(("run", command, user, timeout))
        return SimpleNamespace(stdout="exercised", stderr="", exit_code=0)


@dataclass
class _Sandbox:
    events: list[tuple[object, ...]] = field(default_factory=list)
    files: _Files = field(init=False)
    commands: _Commands = field(init=False)
    killed: bool = False

    def __post_init__(self) -> None:
        self.files = _Files(self.events)
        self.commands = _Commands(self.events)

    def kill(self) -> None:
        self.killed = True


def test_mount_gate_installs_proxy_ca_before_mounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox()
    monkeypatch.setattr(
        mount_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mount-gate",
            "--bucket",
            "bucket",
            "--region",
            "us-east-1",
            "--proxy-url",
            "https://proxy.test",
        ],
    )
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, "ca-pem")
    monkeypatch.setenv(SANDBOX_FS_TOKEN_SECRET_ENV, "secret")

    mount_gate.main()

    assert sandbox.events[0] == ("write", CA_STAGING_PATH, "ca-pem", "root")
    assert sandbox.events[1] == (
        "run",
        INSTALL_CA_COMMAND,
        "root",
        CA_INSTALL_TIMEOUT_SECONDS,
    )
    assert sandbox.events[2] == ("make_dir", WORKSPACE_DIR)
    assert sandbox.killed


def test_mount_gate_recipe_wires_the_expiring_token_proxy_and_root_mount_steps() -> None:
    conversation = uuid4()
    recipe = _mount_gate_recipe(
        bucket="bucket",
        region="us-east-1",
        proxy_url="https://proxy.test/",
        ca_cert="ca-pem",
        token_secret="secret",
        conversation=conversation,
        now=NOW,
    )
    claims = json.loads(verify_token(recipe.token, b"secret"))

    assert claims["kind"] == "deploy_gate"
    assert claims["conversation_id"] == str(conversation)
    assert claims["expires_at"] > int(NOW.timestamp())
    assert recipe.ca_cert == "ca-pem"
    assert recipe.prepare_token_staging == prepare_token_staging_command()
    assert recipe.root_commands[0] == install_token_command()
    assert SANDBOX_FS_TOKEN_STAGING_PATH in recipe.root_commands[0]
    assert "https://proxy.test/sandbox-fs-credentials" in recipe.root_commands[2]
    assert recipe.root_commands[3] == mount_health_check(WORKSPACE_DIR)
    assert EXERCISE.startswith("set -ex")


def test_mount_gate_exercise_writes_to_a_file_unlinked_while_open() -> None:
    """The write/fsync/close after os.remove are the file-handle requests libfuse delivers with a
    null path — the case that segfaulted every packaged s3fs (s3fs-fuse#2903) — and the trailing
    read-back proves the daemon survived them. They only flow because the recipe mounts with
    `hard_remove` (asserted here against the gate's own mount command): without it the unlink hides
    the open file as a `.fuse_hidden*` copy and the probe exercises nothing."""
    recipe = _mount_gate_recipe(
        bucket="bucket",
        region="us-east-1",
        proxy_url="https://proxy.test/",
        ca_cert="ca-pem",
        token_secret="secret",
        conversation=uuid4(),
        now=NOW,
    )
    assert "-o hard_remove" in recipe.root_commands[2]
    assert "os.remove('unlinked.bin')" in EXERCISE
    unlink = EXERCISE.index("os.remove")
    assert EXERCISE.index("os.write(fd, b'b'", unlink) > unlink
    assert EXERCISE.index("os.fsync(fd)", unlink) > unlink
    assert EXERCISE.index("os.close(fd)", unlink) > unlink
    assert EXERCISE.index("alive.txt") > unlink


def test_mount_gate_recipe_requires_the_proxy_ca() -> None:
    with pytest.raises(RuntimeError, match=EGRESS_CA_CERT_ENV):
        _mount_gate_recipe(
            bucket="bucket",
            region="us-east-1",
            proxy_url="https://proxy.test",
            ca_cert=None,
            token_secret="secret",
            conversation=uuid4(),
            now=NOW,
        )


def test_mount_gate_recipe_requires_the_deploy_token_secret() -> None:
    with pytest.raises(RuntimeError, match=SANDBOX_FS_TOKEN_SECRET_ENV):
        _mount_gate_recipe(
            bucket="bucket",
            region="us-east-1",
            proxy_url="https://proxy.test",
            ca_cert="ca-pem",
            token_secret=None,
            conversation=uuid4(),
            now=NOW,
        )
