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
    kill_error: Exception | None = None
    files: _Files = field(init=False)
    commands: _Commands = field(init=False)
    killed: bool = False

    def __post_init__(self) -> None:
        self.files = _Files(self.events)
        self.commands = _Commands(self.events)

    def kill(self) -> None:
        self.killed = True
        self.events.append(("kill",))
        if self.kill_error is not None:
            raise self.kill_error


@dataclass
class _Store:
    """Stands in for the S3 blob store, recording into the same event list as the sandbox fake so
    the marker's ordering against the mount commands is assertable."""

    events: list[tuple[object, ...]]
    delete_error: Exception | None = None

    async def put(self, key: str, data: bytes) -> None:
        self.events.append(("marker_put", key, data))

    async def delete(self, key: str) -> None:
        self.events.append(("marker_delete", key))
        if self.delete_error is not None:
            raise self.delete_error


def test_mount_gate_installs_proxy_ca_before_mounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox()
    store = _Store(sandbox.events)
    monkeypatch.setattr(
        mount_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(mount_gate, "S3BlobStore", lambda **kwargs: store)
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

    kind, marker_key, marker_body = sandbox.events[0]
    assert kind == "marker_put"
    assert marker_key.startswith("conversations/") and marker_key.endswith("/workspace/")
    assert marker_body == b""
    assert sandbox.events[1] == ("write", CA_STAGING_PATH, "ca-pem", "root")
    assert sandbox.events[2] == (
        "run",
        INSTALL_CA_COMMAND,
        "root",
        CA_INSTALL_TIMEOUT_SECONDS,
    )
    assert sandbox.events[3] == ("make_dir", WORKSPACE_DIR)
    assert sandbox.killed
    assert sandbox.events[-2:] == [("kill",), ("marker_delete", marker_key)]
    assert [e for e in sandbox.events if e[0] == "marker_put"] == [sandbox.events[0]]


def test_mount_gate_deletes_the_marker_when_sandbox_create_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed Sandbox.create — the fault class the gate exists to catch — must not leak the
    marker: the delete runs unconditionally, and no kill fires for a sandbox never assigned."""
    events: list[tuple[object, ...]] = []
    store = _Store(events)

    def refuse(**kwargs: object) -> object:
        raise RuntimeError("no sandbox capacity")

    monkeypatch.setattr(mount_gate, "Sandbox", SimpleNamespace(create=refuse))
    monkeypatch.setattr(mount_gate, "S3BlobStore", lambda **kwargs: store)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mount-gate", "--bucket", "bucket", "--region", "us-east-1", "--proxy-url", "https://p"],
    )
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, "ca-pem")
    monkeypatch.setenv(SANDBOX_FS_TOKEN_SECRET_ENV, "secret")

    with pytest.raises(RuntimeError, match="no sandbox capacity"):
        mount_gate.main()

    assert [event[0] for event in events] == ["marker_put", "marker_delete"]
    assert events[0][1] == events[1][1]


def test_mount_gate_cleanup_failure_does_not_mask_the_deploy_defect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failing marker delete during cleanup prints instead of raising, so the exception that
    surfaces from a red gate is the actual deploy defect, never the cleanup's own."""
    events: list[tuple[object, ...]] = []
    store = _Store(events, delete_error=RuntimeError("s3 blip"))

    def refuse(**kwargs: object) -> object:
        raise RuntimeError("no sandbox capacity")

    monkeypatch.setattr(mount_gate, "Sandbox", SimpleNamespace(create=refuse))
    monkeypatch.setattr(mount_gate, "S3BlobStore", lambda **kwargs: store)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mount-gate", "--bucket", "bucket", "--region", "us-east-1", "--proxy-url", "https://p"],
    )
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, "ca-pem")
    monkeypatch.setenv(SANDBOX_FS_TOKEN_SECRET_ENV, "secret")

    with pytest.raises(RuntimeError, match="no sandbox capacity"):
        mount_gate.main()

    assert "marker cleanup failed" in capsys.readouterr().out


def test_mount_gate_goes_red_when_cleanup_fails_on_a_passing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the success path cleanup is load-bearing: a failing marker delete after a healthy
    exercise must fail the gate, not print-and-pass — a leak on a green run is the failure."""
    sandbox = _Sandbox()
    store = _Store(sandbox.events, delete_error=RuntimeError("s3 blip"))
    monkeypatch.setattr(
        mount_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(mount_gate, "S3BlobStore", lambda **kwargs: store)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mount-gate", "--bucket", "bucket", "--region", "us-east-1", "--proxy-url", "https://p"],
    )
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, "ca-pem")
    monkeypatch.setenv(SANDBOX_FS_TOKEN_SECRET_ENV, "secret")

    with pytest.raises(RuntimeError, match="s3 blip"):
        mount_gate.main()
    assert sandbox.killed


def test_mount_gate_deletes_the_marker_when_the_success_path_kill_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kill failure after a healthy exercise must not skip the marker delete: the delete runs in
    a finally, the gate still goes red on the kill's own exception."""
    sandbox = _Sandbox(kill_error=RuntimeError("kill refused"))
    store = _Store(sandbox.events)
    monkeypatch.setattr(
        mount_gate,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: sandbox),
    )
    monkeypatch.setattr(mount_gate, "S3BlobStore", lambda **kwargs: store)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mount-gate", "--bucket", "bucket", "--region", "us-east-1", "--proxy-url", "https://p"],
    )
    monkeypatch.setenv(EGRESS_CA_CERT_ENV, "ca-pem")
    monkeypatch.setenv(SANDBOX_FS_TOKEN_SECRET_ENV, "secret")

    with pytest.raises(RuntimeError, match="kill refused"):
        mount_gate.main()
    assert sandbox.events[-2:] == [("kill",), ("marker_delete", sandbox.events[0][1])]


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
