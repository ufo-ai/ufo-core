"""The s3fs mount recipe a carrier runs to bring a conversation's workspace S3 prefix up at
`/workspace`: the private refresh token, local ECS metadata relay, s3fs command, mount steps, and
the health probe that decides skip-vs-remount. Pure string builders shared by Docker and E2B."""

from __future__ import annotations

import shlex

from ufo.sandbox.session import SANDBOX_GID, SANDBOX_UID

FUSE_DEVICE = "/dev/fuse"
FUSE_CONF = "/etc/fuse.conf"
MOUNT_TIMEOUT_SECONDS = 30
MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS = 10
SANDBOX_FS_TOKEN_PATH = "/run/ufo-sandbox-fs-token"
SANDBOX_FS_TOKEN_STAGING_DIR = "/run/ufo-sandbox-fs"
SANDBOX_FS_TOKEN_STAGING_PATH = f"{SANDBOX_FS_TOKEN_STAGING_DIR}/token.next"
SANDBOX_FS_RELAY_PORT = 8791
SANDBOX_FS_RELAY_LOG = "/run/ufo-sandbox-fs-relay.log"
SANDBOX_FS_RELAY_PID = "/run/ufo-sandbox-fs-relay.pid"
SANDBOX_FS_RELAY_SECRET_PATH = f"{SANDBOX_FS_TOKEN_STAGING_DIR}/relay-secret"
SANDBOX_FS_RELAY_SECRET_STAGING_PATH = f"{SANDBOX_FS_TOKEN_STAGING_DIR}/relay-secret.next"
SANDBOX_FS_MOUNT_USER = "nobody"


def s3fs_command(
    bucket: str, key_prefix: str, mountpoint: str, s3_url: str, region: str, path_style: bool
) -> str:
    # compat_dir treats keys created by boto3 (the framework's writes, which leave no directory
    # markers) as a navigable tree. It does not rescue a prefix with no objects at all — s3fs's
    # mount-time directory check aborts on one — so `_workspace_mount` (queue.py) and the deploy
    # gate write the workspace directory marker once before s3fs dials.
    # allow_other lets a user other than the mounting agent (uid 1000) reach the mount — the file
    # tools' helper stats and serves paths under /workspace, so without it those ops get EACCES.
    # Requires `user_allow_other` in /etc/fuse.conf, set in the mount prepare step.
    # hard_remove really removes a file unlinked while still open — its open handles are served by
    # pseudo fd (the image's pinned s3fs) instead of the default's server-side copy to a
    # `.fuse_hidden*` object per unlink, which litters the workspace prefix and doubles the S3
    # traffic of extract/build workloads. Writes made through a handle after the unlink are
    # discarded at close — POSIX for a zero-link file, and no less durable than the default, whose
    # hidden object libfuse likewise deletes at the last close.
    # The command runs in a shell at a privileged mount seam, so every interpolated value is
    # shell-quoted even though the inputs are framework-controlled (bucket/region/url from config,
    # prefix from ids).
    options = [
        "-o ecs",
        f"-o url={shlex.quote(s3_url)}",
        f"-o endpoint={shlex.quote(region)}",
        "-o compat_dir",
        "-o allow_other",
        "-o hard_remove",
        f"-o uid={SANDBOX_UID}",
        f"-o gid={SANDBOX_GID}",
    ]
    if path_style:
        options.append("-o use_path_request_style")
    target = shlex.quote(f"{bucket}:/{key_prefix}")
    return f"s3fs {target} {shlex.quote(mountpoint)} " + " ".join(options)


def prepare_token_staging_command() -> str:
    directory = shlex.quote(SANDBOX_FS_TOKEN_STAGING_DIR)
    return f"install -d -m 700 -o root -g root {directory}"


def install_token_command() -> str:
    staging = shlex.quote(SANDBOX_FS_TOKEN_STAGING_PATH)
    target = shlex.quote(SANDBOX_FS_TOKEN_PATH)
    return f"chown root {staging} && chmod 600 {staging} && mv -f {staging} {target}"


def mount_scripts(mountpoint: str, s3fs: str, credential_url: str) -> tuple[str, str]:
    """The two root shell commands a carrier runs to mount: `prepare` and `mount`. Both
    are idempotent, so a carrier re-runs them on every create — and skips them when the mount is
    already healthy, so a later turn that attaches to a still-running container skips a redundant
    teardown+remount (same-conversation turns serialize under the queue's per-partition
    concurrency, so no dispatch races another's in-flight file reads).

    prepare: open /dev/fuse to the agent user (root-only in the image); enable `user_allow_other` so
    a non-root mount may pass `allow_other` (needed so a root daemon can serve the agent's file
    tools over the mount); and lazily detach whatever is at the mountpoint — a reused sandbox may
    still
    hold a prior mount, and mounting s3fs over an existing one is undefined; `umount -l` is a no-op
    on a fresh sandbox.

    mount: create the mountpoint, keep one local metadata relay serving the proxy credential
    endpoint behind a root-owned bearer path, and launch s3fs as the dedicated unprivileged mount
    user. The relay reads the current root-owned token file for every fetch, so a later attach can
    rotate the bounded token without remounting. s3fs owns credential refresh timing."""
    mp = shlex.quote(mountpoint)
    endpoint = shlex.quote(credential_url)
    token_path = shlex.quote(SANDBOX_FS_TOKEN_PATH)
    relay_secret_path = shlex.quote(SANDBOX_FS_RELAY_SECRET_PATH)
    relay_secret_staging_path = shlex.quote(SANDBOX_FS_RELAY_SECRET_STAGING_PATH)
    prepare = (
        f"chmod 666 {FUSE_DEVICE}; "
        f"grep -qxF user_allow_other {FUSE_CONF} 2>/dev/null "
        f"|| echo user_allow_other >> {FUSE_CONF}; "
        f"umount -l {mp} 2>/dev/null || true"
    )
    s3fs_mount = (
        f"IFS= read -r relay_secret; "
        f'export AWS_CONTAINER_CREDENTIALS_RELATIVE_URI="'
        f'@127.0.0.1:{SANDBOX_FS_RELAY_PORT}/$relay_secret"; '
        f"exec {s3fs}"
    )
    mount = (
        f"mkdir -p {mp} && chown {SANDBOX_FS_MOUNT_USER} {mp} && chown root {token_path} && "
        f"chmod 600 {token_path} && "
        f"if ! test -s {relay_secret_path}; then umask 077 && "
        f"{{ od -An -N32 -tx1 /dev/urandom | tr -d ' \\n'; printf '\\n'; }} "
        f"> {relay_secret_staging_path} && "
        f"mv -f {relay_secret_staging_path} {relay_secret_path}; fi && "
        f"if ! pgrep -f '^python3 /usr/local/bin/sbxcred ' >/dev/null; "
        f"then sbxcred {endpoint} {token_path} {SANDBOX_FS_RELAY_PORT} "
        f"{relay_secret_path} {SANDBOX_FS_RELAY_PID} "
        f">{SANDBOX_FS_RELAY_LOG} 2>&1; fi && "
        f"cat {relay_secret_path} | "
        f"runuser -u {SANDBOX_FS_MOUNT_USER} -- sh -c {shlex.quote(s3fs_mount)}"
    )
    return prepare, mount


def mount_health_check(mountpoint: str) -> str:
    """The attach-time probe: the relay can redeem the token and a readdir answers through s3fs."""
    mp = shlex.quote(mountpoint)
    relay_secret_path = shlex.quote(SANDBOX_FS_RELAY_SECRET_PATH)
    return (
        f"mountpoint -q {mp} && "
        f"{{ IFS= read -r relay_secret < {relay_secret_path}; "
        f"printf 'url = \"http://127.0.0.1:{SANDBOX_FS_RELAY_PORT}/%s\"\\n' "
        f'"$relay_secret"; }} | '
        f"curl --fail --silent --max-time {MOUNT_HEALTH_CHECK_TIMEOUT_SECONDS} "
        f"--config - >/dev/null && "
        f"ls {mp} >/dev/null 2>&1"
    )
