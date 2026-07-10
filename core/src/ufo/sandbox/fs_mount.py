"""The s3fs mount recipe a carrier runs to bring a conversation's workspace S3 prefix up at
`/workspace`: the AWS credentials file the minted scoped cred becomes, the s3fs command, the
root `prepare` + user `mount` shell steps, and the `mount_health_check` probe that decides
skip-vs-remount. Pure string builders — each carrier (Docker, E2B) writes the cred and runs the
steps through its own exec-with-user — so this backend-neutral recipe is re-exported through
`ufo.sdk.sandbox` for the carriers."""

from __future__ import annotations

import shlex

from ufo.sandbox.fs_creds import SANDBOX_FS_CRED_TTL_SECONDS, SandboxFsCredentials

AWS_CREDENTIALS_PATH = "/home/user/.aws/credentials"
FUSE_DEVICE = "/dev/fuse"
FUSE_CONF = "/etc/fuse.conf"
AGENT_USER = "user"
MOUNT_TIMEOUT_SECONDS = 30
MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS = 10
MOUNT_CREDENTIAL_MAX_AGE_SECONDS = SANDBOX_FS_CRED_TTL_SECONDS // 2


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
    already healthy, so a later turn that attaches to a still-running container skips a redundant
    teardown+remount (same-conversation turns serialize under the queue's per-partition
    concurrency, so no dispatch races another's in-flight file reads).

    prepare: open /dev/fuse to the agent user (root-only in the image); enable `user_allow_other` so
    a non-root mount may pass `allow_other` (needed so a root daemon can serve the agent's file
    tools over the mount); lazily detach whatever is at the mountpoint — a reused sandbox may still
    hold a prior mount, and mounting s3fs over an existing one is undefined, while `umount -l` is a
    no-op on a fresh sandbox; then create the mountpoint and hand it to the agent user — the image
    ships WITHOUT it, `/` is root-owned, and the image carries no sudo, so only this root step can
    bring it into being.

    mount: lock down the credentials file, then mount with this bring-up's credential."""
    mp = shlex.quote(mountpoint)
    prepare = (
        f"chmod 666 {FUSE_DEVICE}; "
        f"grep -qxF user_allow_other {FUSE_CONF} 2>/dev/null "
        f"|| echo user_allow_other >> {FUSE_CONF}; "
        f"umount -l {mp} 2>/dev/null || true; "
        f"mkdir -p {mp} && chown {AGENT_USER} {mp}"
    )
    mount = f"chmod 600 {AWS_CREDENTIALS_PATH} && {s3fs}"
    return prepare, mount


def mount_health_check(mountpoint: str) -> str:
    """The probe that decides skip-vs-remount, true only for a genuinely serviceable mount: the
    path is a mountpoint, a readdir answers through s3fs (a real ListObjects — an expired
    credential or wedged daemon fails it with EIO, while `mountpoint` alone stays green because
    s3fs synthesizes the root inode locally), and the mounted credential is under half its TTL.
    Every attach re-mints, so refreshing at half-life means no attach proceeds onto a credential
    that expires mid-session — and a pause/resume carrier (e2b) can park a sandbox for days, so
    wall-clock credential age, not daemon liveness, is what invalidates a mount. A missing
    credentials file reads as age-since-epoch, so a never-mounted sandbox probes unhealthy."""
    mp = shlex.quote(mountpoint)
    minted = f"$(stat -c %Y {shlex.quote(AWS_CREDENTIALS_PATH)} 2>/dev/null || echo 0)"
    return (
        f"mountpoint -q {mp} && ls {mp} >/dev/null 2>&1 && "
        f"[ $(( $(date +%s) - {minted} )) -lt {MOUNT_CREDENTIAL_MAX_AGE_SECONDS} ]"
    )
