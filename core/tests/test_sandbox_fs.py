"""The S3-backed sandbox workspace mount, offline: the STS inline policy that confines the mount
credential to the conversation's `workspace/` prefix (and so denies its transcript), the s3fs
command the carrier runs, and `_workspace_mount` minting on the S3 backend instead of raising.

The live mount (real MinIO/S3 + STS + FUSE in a container) is infra-gated; these prove the policy,
the command construction, and the wiring that a live mount then executes. The STS client is a
stand-in dependency — every assertion is the minter's and the mount-builder's own output, never the
stand-in's canned response."""

import json
from fnmatch import fnmatchcase
from uuid import uuid4

import pytest

from ufo.blob import S3BlobStore
from ufo.loop.queue import _workspace_mount
from ufo.sandbox.fs_creds import (
    SANDBOX_FS_CRED_TTL_SECONDS,
    SandboxFsCredentialMinter,
    SandboxFsCredentials,
    workspace_key_prefix,
    workspace_prefix_policy,
)
from ufo.sandbox.fs_mount import (
    AWS_CREDENTIALS_PATH,
    MOUNT_CREDENTIAL_MAX_AGE_SECONDS,
    aws_credentials_file,
    mount_health_check,
    mount_scripts,
    s3fs_command,
)
from ufo.sandbox.session import MountSpec
from ufo.transcript import transcript_key

BUCKET = "ufo-blobs"


class _StubSts:
    """Stands in for STS: records the inline policy it was handed and returns canned credentials in
    the AssumeRole response shape, so a test can assert the minter scoped the session correctly
    without reaching a real STS."""

    def __init__(self) -> None:
        self.seen_policy: str | None = None

    async def assume_role(
        self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int
    ) -> dict[str, dict[str, str]]:
        self.seen_policy = Policy
        return {
            "Credentials": {
                "AccessKeyId": "AKIASBX",
                "SecretAccessKey": "sbx-secret",
                "SessionToken": "sbx-token",
            }
        }


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
        "-o profile=default -o url=https://s3.example:9000 -o endpoint=us-east-1 "
        "-o compat_dir -o allow_other"
    )


def test_s3fs_command_adds_path_style_for_minio() -> None:
    command = s3fs_command(
        BUCKET, "conversations/c1/workspace", "/workspace", "https://minio:9000", "us-east-1", True
    )
    assert command.endswith("-o compat_dir -o allow_other -o use_path_request_style")


def test_aws_credentials_file_is_the_default_profile() -> None:
    rendered = aws_credentials_file(SandboxFsCredentials("AKIA", "secret", "token"))
    assert rendered == (
        "[default]\naws_access_key_id=AKIA\naws_secret_access_key=secret\naws_session_token=token\n"
    )


def test_mount_scripts_prepare_and_mount() -> None:
    """The mountpoint is born in the root `prepare` (the image ships without /workspace, `/` is
    root-owned, and there is no sudo) and handed to the agent user, whose non-root `mount` then
    only locks the credential file and runs s3fs."""
    prepare, mount = mount_scripts("/workspace", "s3fs bucket:/p /workspace -o x")
    assert "chmod 666 /dev/fuse" in prepare
    assert "user_allow_other" in prepare
    assert "umount -l /workspace" in prepare
    assert prepare.endswith("mkdir -p /workspace && chown user /workspace")
    assert mount == (f"chmod 600 {AWS_CREDENTIALS_PATH} && s3fs bucket:/p /workspace -o x")


def test_mount_health_check_probes_readdir_and_bounds_credential_age() -> None:
    """The skip-vs-remount probe is a real proof of service, not a bare `mountpoint`: readdir
    forces a ListObjects through s3fs (an expired credential or wedged daemon fails it with EIO
    while the mountpoint alone stays green), and the credential-age bound remounts before the
    minted session can expire under a turn — the wedge a pause/resume carrier otherwise pins
    forever, since the daemon survives the pause but its credential does not."""
    probe = mount_health_check("/workspace")
    assert probe == (
        "mountpoint -q /workspace && ls /workspace >/dev/null 2>&1 && "
        f"[ $(( $(date +%s) - $(stat -c %Y {AWS_CREDENTIALS_PATH} 2>/dev/null || echo 0) )) "
        f"-lt {MOUNT_CREDENTIAL_MAX_AGE_SECONDS} ]"
    )
    # Refresh strictly inside the TTL: a mount that passes the probe holds a credential with at
    # least half its lifetime left for the session it serves.
    assert MOUNT_CREDENTIAL_MAX_AGE_SECONDS <= SANDBOX_FS_CRED_TTL_SECONDS // 2


async def test_workspace_mount_mints_a_scoped_s3_mount() -> None:
    sts = _StubSts()
    conversation = uuid4()
    minter = SandboxFsCredentialMinter(
        sts=sts,
        role_arn="arn:aws:iam::0:role/sbxfs",
        bucket=BUCKET,
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=True,
    )
    mount = await _workspace_mount(S3BlobStore(bucket=BUCKET), minter, conversation)

    assert mount == MountSpec(
        kind="s3",
        bucket=BUCKET,
        key_prefix=f"conversations/{conversation}/workspace",
        credentials=SandboxFsCredentials("AKIASBX", "sbx-secret", "sbx-token"),
        s3_url="https://s3.example:9000",
        region="us-east-1",
        path_style=True,
    )
    # The session it minted was scoped to this conversation's workspace prefix, nothing wider.
    assert sts.seen_policy == workspace_prefix_policy(BUCKET, workspace_key_prefix(conversation))


async def test_workspace_mount_on_s3_without_a_minter_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="minter"):
        await _workspace_mount(S3BlobStore(bucket=BUCKET), None, uuid4())
