"""The S3-backed sandbox workspace mount, offline: the STS inline policy that confines the mount
credential to the conversation's `workspace/` prefix (and so denies its transcript), the s3fs
command the carrier runs, and `_workspace_mount` minting on the S3 backend instead of raising.

The live mount (real MinIO/S3 + STS + FUSE in a container) is infra-gated; these prove the policy,
the command construction, and the wiring that a live mount then executes. The STS client is a
stand-in dependency — every assertion is the minter's and the mount-builder's own output, never the
stand-in's canned response."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ufo.blob import S3BlobStore
from ufo.config import BlobConfig
from ufo.loop.queue import _workspace_mount
from ufo.sandbox.fs_creds import (
    SANDBOX_FS_CRED_TTL_SECONDS,
    SANDBOX_FS_CREDENTIAL_PATH,
    SANDBOX_FS_GATE_TOKEN_TTL_SECONDS,
    SANDBOX_FS_TOKEN_SECRET_ENV,
    InvalidSandboxFsToken,
    SandboxFsCredentialMinter,
    SandboxFsCredentials,
    issue_sandbox_fs_gate_token,
    sandbox_fs_minter,
    workspace_key_prefix,
    workspace_prefix_policy,
)
from ufo.sandbox.fs_mount import (
    SANDBOX_FS_RELAY_PID,
    SANDBOX_FS_RELAY_PORT,
    SANDBOX_FS_RELAY_SECRET_PATH,
    SANDBOX_FS_RELAY_SECRET_STAGING_PATH,
    SANDBOX_FS_TOKEN_PATH,
    SANDBOX_FS_TOKEN_STAGING_PATH,
    install_token_command,
    mount_health_check,
    mount_scripts,
    prepare_token_staging_command,
    s3fs_command,
)
from ufo.sandbox.session import MountSpec, RunToken
from ufo.transcript import transcript_key

BUCKET = "ufo-blobs"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
RUN = RunToken(workspace_id=uuid4(), turn_id=uuid4())


async def _live_turn(run: RunToken) -> bool:
    return run == RUN


class _StubSts:
    """Stands in for STS: records the inline policy it was handed and returns canned credentials in
    the AssumeRole response shape, so a test can assert the minter scoped the session correctly
    without reaching a real STS."""

    def __init__(self) -> None:
        self.seen_policy: str | None = None
        self.calls = 0

    async def assume_role(
        self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int
    ) -> dict[str, dict[str, str]]:
        self.calls += 1
        self.seen_policy = Policy
        return {
            "Credentials": {
                "AccessKeyId": "AKIASBX",
                "SecretAccessKey": "sbx-secret",
                "SessionToken": "sbx-token",
                "Expiration": datetime(2026, 1, 2, tzinfo=UTC),
            }
        }


@dataclass(frozen=True)
class _RecordingS3BlobStore(S3BlobStore):
    """The S3 store with its network put recorded: the workspace directory marker is asserted as
    `_workspace_mount`'s own act, and nothing asserts the store's behavior."""

    marker_puts: list[tuple[str, bytes]] = field(default_factory=list, compare=False)

    async def put(self, key: str, data: bytes) -> None:
        self.marker_puts.append((key, data))


def _object_resource(policy: dict) -> str:
    for statement in policy["Statement"]:
        if "s3:GetObject" in statement["Action"]:
            return statement["Resource"]
    raise AssertionError("policy has no object-access statement")


def test_policy_confines_object_access_to_the_workspace_and_denies_the_transcript() -> None:
    conversation = uuid4()
    policy = json.loads(workspace_prefix_policy(BUCKET, workspace_key_prefix(conversation)))
    resource = _object_resource(policy).removeprefix("arn:aws:s3:::")

    assert resource == f"{BUCKET}/conversations/{conversation}/workspace/*"
    # A workspace file (any depth) is reachable; the transcript sibling and another conversation's
    # workspace are outside the granted prefix, so the Allow-only policy leaves them denied.
    assert fnmatchcase(f"{BUCKET}/conversations/{conversation}/workspace/report.md", resource)
    assert fnmatchcase(f"{BUCKET}/conversations/{conversation}/workspace/a/b/c.txt", resource)
    assert not fnmatchcase(f"{BUCKET}/{transcript_key(conversation)}", resource)
    assert not fnmatchcase(f"{BUCKET}/conversations/{conversation}/compactions/0.lz4", resource)
    assert not fnmatchcase(f"{BUCKET}/conversations/{uuid4()}/workspace/x", resource)


def test_policy_list_condition_scopes_enumeration_to_the_workspace_prefix() -> None:
    conversation = uuid4()
    prefix = workspace_key_prefix(conversation)
    policy = json.loads(workspace_prefix_policy(BUCKET, prefix))
    statement = next(s for s in policy["Statement"] if s["Action"] == "s3:ListBucket")

    assert statement["Resource"] == f"arn:aws:s3:::{BUCKET}"
    prefixes = statement["Condition"]["StringLike"]["s3:prefix"]
    assert prefixes == [f"{prefix}/*", prefix]
    # Listing the conversation dir (which would reveal messages.json.lz4) matches no allowed prefix.
    assert not any(fnmatchcase(f"conversations/{conversation}/", pattern) for pattern in prefixes)
    assert not any(fnmatchcase(transcript_key(conversation), pattern) for pattern in prefixes)


def test_s3fs_command_construction() -> None:
    command = s3fs_command(
        BUCKET,
        "conversations/c1/workspace",
        "/workspace",
        "https://s3.example:9000",
        "us-east-1",
        False,
    )
    assert command == (
        "s3fs ufo-blobs:/conversations/c1/workspace /workspace "
        "-o ecs -o url=https://s3.example:9000 -o endpoint=us-east-1 "
        "-o compat_dir -o allow_other -o hard_remove -o uid=1000 -o gid=1000"
    )


def test_s3fs_command_adds_path_style_for_minio() -> None:
    command = s3fs_command(
        BUCKET, "conversations/c1/workspace", "/workspace", "https://minio:9000", "us-east-1", True
    )
    assert command.endswith(
        "-o compat_dir -o allow_other -o hard_remove -o uid=1000 -o gid=1000 "
        "-o use_path_request_style"
    )


def test_mount_scripts_prepare_and_mount() -> None:
    prepare, mount = mount_scripts(
        "/workspace", "s3fs bucket:/p /workspace -o x", "https://proxy.test/credentials"
    )
    assert "chmod 666 /dev/fuse" in prepare
    assert "user_allow_other" in prepare
    assert "umount -l /workspace" in prepare
    assert "chown nobody /workspace" in mount
    assert f"chown root {SANDBOX_FS_TOKEN_PATH}" in mount
    assert f"chmod 600 {SANDBOX_FS_TOKEN_PATH}" in mount
    assert "od -An -N32 -tx1 /dev/urandom" in mount
    assert f"> {SANDBOX_FS_RELAY_SECRET_STAGING_PATH}" in mount
    assert f"mv -f {SANDBOX_FS_RELAY_SECRET_STAGING_PATH} {SANDBOX_FS_RELAY_SECRET_PATH}" in mount
    assert (
        f"sbxcred https://proxy.test/credentials {SANDBOX_FS_TOKEN_PATH} "
        f"{SANDBOX_FS_RELAY_PORT} {SANDBOX_FS_RELAY_SECRET_PATH} {SANDBOX_FS_RELAY_PID}"
    ) in mount
    assert (
        f'export AWS_CONTAINER_CREDENTIALS_RELATIVE_URI="@127.0.0.1:'
        f'{SANDBOX_FS_RELAY_PORT}/$relay_secret"'
    ) in mount
    assert f"cat {SANDBOX_FS_RELAY_SECRET_PATH} | runuser -u nobody -- sh -c" in mount
    assert "relay_secret=$(cat" not in mount
    assert "s3fs bucket:/p /workspace -o x" in mount


def test_token_install_replaces_the_root_owned_file_atomically() -> None:
    prepare = prepare_token_staging_command()
    command = install_token_command()

    assert "install -d -m 700 -o root -g root" in prepare
    assert f"chown root {SANDBOX_FS_TOKEN_STAGING_PATH}" in command
    assert f"chmod 600 {SANDBOX_FS_TOKEN_STAGING_PATH}" in command
    assert f"mv -f {SANDBOX_FS_TOKEN_STAGING_PATH} {SANDBOX_FS_TOKEN_PATH}" in command


def test_mount_health_check_probes_the_relay_and_readdir() -> None:
    assert (
        mount_health_check("/workspace") == "mountpoint -q /workspace && "
        f"{{ IFS= read -r relay_secret < {SANDBOX_FS_RELAY_SECRET_PATH}; "
        'printf \'url = "http://127.0.0.1:8791/%s"\\n\' "$relay_secret"; } | '
        "curl --fail --silent --max-time 10 "
        "--config - >/dev/null && "
        "ls /workspace >/dev/null 2>&1"
    )


async def test_workspace_mount_issues_a_scoped_s3_mount_token() -> None:
    sts = _StubSts()
    conversation = uuid4()
    minter = SandboxFsCredentialMinter(
        sts=sts,
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket=BUCKET,
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=True,
        token_secret=b"mount-secret",
        now=lambda: NOW,
    )
    blob = _RecordingS3BlobStore(bucket=BUCKET)
    mount = await _workspace_mount(blob, minter, conversation, RUN, fresh_sandbox=True)

    assert blob.marker_puts == [(f"conversations/{conversation}/workspace/", b"")]
    resumed = await _workspace_mount(blob, minter, conversation, RUN, fresh_sandbox=False)
    assert resumed == mount
    assert len(blob.marker_puts) == 1
    assert mount == MountSpec(
        kind="s3",
        bucket=BUCKET,
        key_prefix=f"conversations/{conversation}/workspace",
        credential_token=minter.issue(conversation, RUN),
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=True,
    )
    assert sts.seen_policy is None


async def test_mount_token_redeems_repeatedly_to_scoped_ecs_credentials() -> None:
    sts = _StubSts()
    conversation = uuid4()
    minter = SandboxFsCredentialMinter(
        sts=sts,
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket=BUCKET,
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=False,
        token_secret=b"mount-secret",
        now=lambda: NOW,
    )
    token = minter.issue(conversation, RUN)

    first = await minter.refresh(token, _live_turn)
    second = await minter.refresh(token, _live_turn)

    assert (
        first
        == second
        == SandboxFsCredentials(
            access_key_id="AKIASBX",
            secret_access_key="sbx-secret",
            session_token="sbx-token",
            expiration=datetime(2026, 1, 2, tzinfo=UTC),
            role_arn="arn:aws:iam::0:role/sbxfs",
        )
    )
    assert json.loads(first.ecs_json()) == {
        "AccessKeyId": "AKIASBX",
        "SecretAccessKey": "sbx-secret",
        "Token": "sbx-token",
        "Expiration": "2026-01-02T00:00:00Z",
        "RoleArn": "arn:aws:iam::0:role/sbxfs",
    }
    assert sts.seen_policy == workspace_prefix_policy(BUCKET, workspace_key_prefix(conversation))
    assert "." in token
    with pytest.raises(InvalidSandboxFsToken, match="invalid sandbox-fs token"):
        await minter.refresh(token + "x", _live_turn)


async def test_mount_token_refresh_requires_a_live_turn_without_a_wall_clock_deadline() -> None:
    sts = _StubSts()
    current_time = [NOW]
    minter = SandboxFsCredentialMinter(
        sts=sts,
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket=BUCKET,
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=False,
        token_secret=b"mount-secret",
        now=lambda: current_time[0],
    )
    token = minter.issue(uuid4(), RUN)
    current_time[0] += timedelta(days=365)

    assert await minter.refresh(token, _live_turn)
    assert sts.calls == 1

    async def ended_turn(run: RunToken) -> bool:
        assert run == RUN
        return False

    with pytest.raises(InvalidSandboxFsToken, match="invalid sandbox-fs token"):
        await minter.refresh(token, ended_turn)
    assert sts.calls == 1


def test_sandbox_fs_credential_path_is_stable_for_the_relay() -> None:
    assert SANDBOX_FS_CREDENTIAL_PATH == "/sandbox-fs-credentials/"
    assert SANDBOX_FS_CRED_TTL_SECONDS == 3600
    assert SANDBOX_FS_GATE_TOKEN_TTL_SECONDS == 600


async def test_expired_deploy_gate_token_is_rejected() -> None:
    sts = _StubSts()
    minter = SandboxFsCredentialMinter(
        sts=sts,
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket=BUCKET,
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=False,
        token_secret=b"mount-secret",
        now=lambda: NOW,
    )
    token = issue_sandbox_fs_gate_token(uuid4(), minter.token_secret, NOW - timedelta(seconds=1))

    with pytest.raises(InvalidSandboxFsToken, match="invalid sandbox-fs token"):
        await minter.refresh(token, _live_turn)
    assert sts.seen_policy is None


def test_sandbox_fs_credentials_reject_extra_wire_fields() -> None:
    with pytest.raises(ValidationError):
        SandboxFsCredentials.model_validate(
            {
                "access_key_id": "AKIA",
                "secret_access_key": "secret",
                "session_token": "token",
                "expiration": datetime(2026, 1, 2, tzinfo=UTC),
                "role_arn": "arn:aws:iam::0:role/sbxfs",
                "unexpected": True,
            }
        )


def test_s3_minter_requires_the_shared_mount_token_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = BlobConfig(
        backend="s3",
        bucket=BUCKET,
        s3_url="https://s3.example",
        sts_role_arn="arn:aws:iam::0:role/sbxfs",
    )
    monkeypatch.delenv(SANDBOX_FS_TOKEN_SECRET_ENV, raising=False)
    with pytest.raises(RuntimeError, match=SANDBOX_FS_TOKEN_SECRET_ENV):
        sandbox_fs_minter(blob)

    monkeypatch.setenv(SANDBOX_FS_TOKEN_SECRET_ENV, "shared-secret")
    minter = sandbox_fs_minter(blob)
    assert minter is not None
    assert minter.token_secret == b"shared-secret"


async def test_workspace_mount_on_s3_without_a_minter_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="minter"):
        await _workspace_mount(S3BlobStore(bucket=BUCKET), None, uuid4(), RUN, fresh_sandbox=True)
