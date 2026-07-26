#!/usr/bin/env python3
"""Deploy gate: prove the deployed environment serves a sandbox workspace mount end-to-end.

Boots a sandbox from the published template, issues a short-lived operator probe token, brings
/workspace up through the production credential endpoint and carrier recipe, then exercises the
mount the way an agent does — create, list, read back, chmod, delete, and the write/fsync/close of
a file unlinked while open, the file-handle requests libfuse delivers with no path (which
segfaulted every packaged s3fs, s3fs-fuse#2903) — and proves the health probe passes. Any failure
exits non-zero, so a deploy onto a broken storage chain (template FUSE/s3fs, credential endpoint,
IAM trust or policy, bucket) goes red in the pipeline. The gate stands in for serve as the mount
preparer, so like `_workspace_mount` it writes the workspace directory marker before mounting —
s3fs refuses an object-less prefix. The workspace prefix is a throwaway conversation id, emptied
again by the gate's own deletes; the sandbox is killed either way."""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from e2b import Sandbox
from ufo_ext_e2b import (
    CA_INSTALL_TIMEOUT_SECONDS,
    CA_STAGING_PATH,
    E2B_TEMPLATE_NAME,
    INSTALL_CA_COMMAND,
)

from ufo.blob import S3BlobStore
from ufo.sandbox.fs_creds import (
    SANDBOX_FS_CREDENTIAL_PATH,
    SANDBOX_FS_GATE_TOKEN_TTL_SECONDS,
    SANDBOX_FS_TOKEN_SECRET_ENV,
    ensure_workspace_marker,
    issue_sandbox_fs_gate_token,
    workspace_key_prefix,
)
from ufo.sandbox.fs_mount import (
    MOUNT_TIMEOUT_SECONDS,
    SANDBOX_FS_TOKEN_STAGING_PATH,
    install_token_command,
    mount_health_check,
    mount_scripts,
    prepare_token_staging_command,
    s3fs_command,
)
from ufo.sandbox.session import EGRESS_CA_CERT_ENV, WORKSPACE_DIR

SANDBOX_TIMEOUT_SECONDS = 180
EXERCISE_TIMEOUT_SECONDS = 60
EXERCISE = f"""
set -ex
cd {WORKSPACE_DIR}
echo gate-probe > gate.txt
ls -l gate.txt
[ "$(cat gate.txt)" = gate-probe ]
chmod 644 gate.txt
rm gate.txt
python3 -c "
import os
fd = os.open('unlinked.bin', os.O_CREAT | os.O_RDWR, 0o644)
os.write(fd, b'a' * 65536)
os.remove('unlinked.bin')
os.write(fd, b'b' * 65536)
os.fsync(fd)
os.close(fd)
"
echo gate-probe > alive.txt
[ "$(cat alive.txt)" = gate-probe ]
rm alive.txt
""".strip()


@dataclass(frozen=True)
class _MountGateRecipe:
    ca_cert: str
    token: str
    prepare_token_staging: str
    root_commands: tuple[str, ...]


def _mount_gate_recipe(
    *,
    bucket: str,
    region: str,
    proxy_url: str,
    ca_cert: str | None,
    token_secret: str | None,
    conversation: UUID,
    now: datetime,
) -> _MountGateRecipe:
    if not ca_cert:
        raise RuntimeError(f"{EGRESS_CA_CERT_ENV} is required")
    if not token_secret:
        raise RuntimeError(f"{SANDBOX_FS_TOKEN_SECRET_ENV} is required")
    token = issue_sandbox_fs_gate_token(
        conversation,
        token_secret.encode(),
        now + timedelta(seconds=SANDBOX_FS_GATE_TOKEN_TTL_SECONDS),
    )
    s3fs = s3fs_command(
        bucket,
        workspace_key_prefix(conversation),
        WORKSPACE_DIR,
        f"https://s3.{region}.amazonaws.com",
        region,
        False,
    )
    credential_url = f"{proxy_url.rstrip('/')}{SANDBOX_FS_CREDENTIAL_PATH.rstrip('/')}"
    prepare, mount = mount_scripts(WORKSPACE_DIR, s3fs, credential_url)
    return _MountGateRecipe(
        ca_cert=ca_cert,
        token=token,
        prepare_token_staging=prepare_token_staging_command(),
        root_commands=(
            install_token_command(),
            prepare,
            mount,
            mount_health_check(WORKSPACE_DIR),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="mount-gate")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--proxy-url", required=True)
    args = parser.parse_args()
    conversation = uuid4()
    recipe = _mount_gate_recipe(
        bucket=args.bucket,
        region=args.region,
        proxy_url=args.proxy_url,
        ca_cert=os.environ.get(EGRESS_CA_CERT_ENV),
        token_secret=os.environ.get(SANDBOX_FS_TOKEN_SECRET_ENV),
        conversation=conversation,
        now=datetime.now(UTC),
    )
    store = S3BlobStore(bucket=args.bucket, region=args.region)
    marker_key: str | None = None
    sandbox: Sandbox | None = None
    try:
        marker_key = asyncio.run(ensure_workspace_marker(store, conversation))
        sandbox = Sandbox.create(template=E2B_TEMPLATE_NAME, timeout=SANDBOX_TIMEOUT_SECONDS)
        sandbox.files.write(CA_STAGING_PATH, recipe.ca_cert, user="root")
        sandbox.commands.run(
            INSTALL_CA_COMMAND,
            user="root",
            timeout=CA_INSTALL_TIMEOUT_SECONDS,
        )
        sandbox.files.make_dir(WORKSPACE_DIR)
        sandbox.commands.run(
            recipe.prepare_token_staging,
            user="root",
            timeout=MOUNT_TIMEOUT_SECONDS,
        )
        sandbox.files.write(SANDBOX_FS_TOKEN_STAGING_PATH, recipe.token, user="root")
        for command in recipe.root_commands:
            sandbox.commands.run(command, user="root", timeout=MOUNT_TIMEOUT_SECONDS)
        exercised = sandbox.commands.run(EXERCISE, timeout=EXERCISE_TIMEOUT_SECONDS)
        print(exercised.stdout)
    except BaseException:
        # The exception that surfaces from a red gate must be the deploy defect itself, so on this
        # path cleanup failures only print. On the success path below they raise — a leaked
        # sandbox or marker is then the failure.
        try:
            if sandbox is not None:
                sandbox.kill()
        except Exception as error:
            print(f"sandbox cleanup failed: {error}")
        finally:
            if marker_key is not None:
                try:
                    asyncio.run(store.delete(marker_key))
                except Exception as error:
                    print(f"marker cleanup failed, {marker_key} leaked: {error}")
        raise
    try:
        sandbox.kill()
    finally:
        asyncio.run(store.delete(marker_key))
    print(f"workspace mount gate passed (conversation {conversation})")


if __name__ == "__main__":
    main()
