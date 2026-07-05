"""The s3fs mount recipe a carrier runs to bring a conversation's workspace S3 prefix up at
`/workspace`: the AWS credentials file the minted scoped cred becomes, the s3fs command, and the
root `prepare` + user `mount` shell steps. Pure string builders — each carrier (Docker, E2B) writes
the cred, runs the two steps through its own exec-with-user, and health-checks with `mountpoint` —
so this backend-neutral recipe is re-exported through `selfhost.sdk.sandbox` for the carriers."""

from __future__ import annotations

import shlex

from selfhost.sandbox.fs_creds import SandboxFsCredentials

AWS_CREDENTIALS_PATH = "/home/user/.aws/credentials"
FUSE_DEVICE = "/dev/fuse"
FUSE_CONF = "/etc/fuse.conf"
MOUNT_TIMEOUT_SECONDS = 30
MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS = 10


def aws_credentials_file(credentials: SandboxFsCredentials) -> str:
    return (
        "[default]\n"
        f"aws_access_key_id={credentials.access_key_id}\n"
        f"aws_secret_access_key={credentials.secret_access_key}\n"
        f"aws_session_token={credentials.session_token}\n"
    )


def s3fs_command(
    bucket: str, key_prefix: str, mountpoint: str, s3_url: str, region: str, path_style: bool
) -> str:
    # compat_dir lets s3fs mount a prefix that has no explicit directory-marker object — a fresh
    # conversation's prefix is empty at first mount, and without it s3fs's mount-time directory
    # check 404s and aborts. It also treats keys created by boto3 (the framework's writes, which
    # leave no directory markers) as a navigable tree.
    # allow_other lets a user other than the mounting agent (uid 1000) reach the mount — the file
    # tools' helper stats and serves paths under /workspace, so without it those ops get EACCES.
    # Requires `user_allow_other` in /etc/fuse.conf, set in the mount prepare step.
    # The command runs in a shell at a privileged mount seam, so every interpolated value is
    # shell-quoted even though the inputs are framework-controlled (bucket/region/url from config,
    # prefix from ids).
    options = [
        "-o profile=default",
        f"-o url={shlex.quote(s3_url)}",
        f"-o endpoint={shlex.quote(region)}",
        "-o compat_dir",
        "-o allow_other",
    ]
    if path_style:
        options.append("-o use_path_request_style")
    target = shlex.quote(f"{bucket}:/{key_prefix}")
    return f"s3fs {target} {shlex.quote(mountpoint)} " + " ".join(options)


def mount_scripts(mountpoint: str, s3fs: str) -> tuple[str, str]:
    """The two shell commands a carrier runs to mount: a root `prepare` and the agent `mount`. Both
    are idempotent, so a carrier re-runs them on every create — and skips them when the mount is
    already healthy, so subagents sharing one sandbox never yank the mount out from under an
    in-flight dispatch.

    prepare: open /dev/fuse to the agent user (root-only in the image); enable `user_allow_other` so
    a non-root mount may pass `allow_other` (needed so a root daemon can serve the agent's file
    tools over the mount); and lazily detach whatever is at the mountpoint — a reused sandbox may
    still
    hold a prior mount, and mounting s3fs over an existing one is undefined; `umount -l` is a no-op
    on a fresh sandbox.

    mount: create the mountpoint — the image ships WITHOUT it and s3fs refuses to mount a path that
    does not exist — lock down the credentials file, then mount with this bring-up's credential."""
    mp = shlex.quote(mountpoint)
    prepare = (
        f"chmod 666 {FUSE_DEVICE}; "
        f"grep -qxF user_allow_other {FUSE_CONF} 2>/dev/null "
        f"|| echo user_allow_other >> {FUSE_CONF}; "
        f"umount -l {mp} 2>/dev/null || true"
    )
    mount = f"mkdir -p {mp} && chmod 600 {AWS_CREDENTIALS_PATH} && {s3fs}"
    return prepare, mount
