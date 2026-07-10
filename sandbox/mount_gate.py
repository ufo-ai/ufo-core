#!/usr/bin/env python3
"""Deploy gate: prove the deployed environment serves a sandbox workspace mount end-to-end.

Boots a sandbox from the published template, mints a prefix-scoped credential through the same
minter production uses (STS AssumeRole on the sandbox-fs role), brings /workspace up with the same
prepare/mount recipe the carriers run, then exercises the mount the way an agent does — create,
list, read back, chmod, delete — and proves the carriers' health probe passes on the fresh mount.
Any failure exits non-zero, so a deploy onto a broken storage chain (template FUSE/s3fs, IAM trust
or policy, bucket) goes red in the pipeline instead of surfacing later as a wedged agent. The
workspace prefix is a throwaway conversation id, emptied again by the exercise's own delete; the
sandbox is killed either way."""

from __future__ import annotations

import argparse
import asyncio
from uuid import uuid4

from e2b import Sandbox

from ufo.sandbox.fs_creds import AwsStsClient, SandboxFsCredentialMinter, workspace_key_prefix
from ufo.sandbox.fs_mount import (
    AWS_CREDENTIALS_PATH,
    MOUNT_TIMEOUT_SECONDS,
    aws_credentials_file,
    mount_health_check,
    mount_scripts,
    s3fs_command,
)
from ufo.sandbox.session import WORKSPACE_DIR

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
""".strip()


def main() -> None:
    parser = argparse.ArgumentParser(prog="mount-gate")
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--template", default="ufo-sbx")
    args = parser.parse_args()
    s3_url = f"https://s3.{args.region}.amazonaws.com"
    conversation = uuid4()
    minter = SandboxFsCredentialMinter(
        sts=AwsStsClient(endpoint_url=None, region=args.region),
        role_arn=args.role_arn,
        bucket=args.bucket,
        s3_url=s3_url,
        region=args.region,
        path_style=False,
    )
    credentials = asyncio.run(minter.mint(conversation))
    s3fs = s3fs_command(
        args.bucket, workspace_key_prefix(conversation), WORKSPACE_DIR, s3_url, args.region, False
    )
    prepare, mount = mount_scripts(WORKSPACE_DIR, s3fs)
    sandbox = Sandbox.create(template=args.template, timeout=SANDBOX_TIMEOUT_SECONDS)
    try:
        sandbox.files.make_dir(WORKSPACE_DIR)
        sandbox.files.write(AWS_CREDENTIALS_PATH, aws_credentials_file(credentials))
        sandbox.commands.run(prepare, user="root", timeout=MOUNT_TIMEOUT_SECONDS)
        sandbox.commands.run(mount, timeout=MOUNT_TIMEOUT_SECONDS)
        sandbox.commands.run(mount_health_check(WORKSPACE_DIR), timeout=MOUNT_TIMEOUT_SECONDS)
        exercised = sandbox.commands.run(EXERCISE, timeout=EXERCISE_TIMEOUT_SECONDS)
        print(exercised.stdout)
    finally:
        sandbox.kill()
    print(f"workspace mount gate passed (conversation {conversation})")


if __name__ == "__main__":
    main()
