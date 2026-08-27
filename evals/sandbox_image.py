"""Prepare the content-addressed Docker sandbox image an application eval runs."""

import argparse
import fcntl
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sandbox.build_template import (
    BUILD_DIGEST_PATH,
    build_definition_digest,
    stage_client_binary,
    stage_system_skills,
)

IMAGE_REPOSITORY = "ufo-sandbox-eval"
IMAGE_KEY_PATTERN = re.compile(r"[0-9a-f]{16}")
PROTOCOL_PROBE = f"""set -eu
test "$(cat {BUILD_DIGEST_PATH})" = "$1"
base=/var/tmp/ufo-eval-task-contract
rm -f "$base.pid" "$base.log" "$base.exit" "$base.lock"
ufo run --task "$base" --detach -- sh -c 'exit 0' >/dev/null
ufo run --task "$base" -- sh -c 'exit 0'
test "$(cat "$base.exit")" = 0
"""


@dataclass(frozen=True)
class SandboxImagePlan:
    """One pinned Dockerfile, image reference, and baked definition digest."""

    dockerfile: Path
    reference: str
    definition_digest: str

    def prepare(self) -> None:
        lock = Path(tempfile.gettempdir()) / f"{self.reference.replace(':', '-')}.lock"
        with lock.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            if self._compatible():
                return
            stage_client_binary()
            stage_system_skills()
            built = subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    self.reference,
                    "-f",
                    str(self.dockerfile),
                    str(Path.cwd()),
                ],
                check=False,
            )
            if built.returncode != 0 or not self._compatible():
                raise RuntimeError(f"sandbox image {self.reference} failed its task protocol")

    def _compatible(self) -> bool:
        inspected = subprocess.run(
            ["docker", "image", "inspect", self.reference],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if inspected.returncode != 0:
            return False
        probed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                self.reference,
                "-c",
                PROTOCOL_PROBE,
                "sh",
                self.definition_digest,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return probed.returncode == 0


def sandbox_image_plan(repo_root: Path, dockerfile: Path) -> SandboxImagePlan:
    """Derive the image reference from the repository's pinned-definition cache key."""

    keyed = subprocess.run(
        ["bash", str(repo_root / ".github/scripts/sandbox_image_key.sh"), str(dockerfile)],
        cwd=repo_root,
        env=os.environ,
        stdout=subprocess.PIPE,
        check=False,
    )
    key = keyed.stdout.decode().strip()
    if (
        keyed.returncode != 0
        or IMAGE_KEY_PATTERN.fullmatch(key) is None
        or not dockerfile.is_file()
    ):
        raise RuntimeError("sandbox image definition key could not be produced")
    return SandboxImagePlan(
        dockerfile=dockerfile,
        reference=f"{IMAGE_REPOSITORY}:{key}",
        definition_digest=build_definition_digest(None),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.sandbox_image")
    parser.add_argument("dockerfile", type=Path)
    parser.add_argument("reference")
    parser.add_argument("definition_digest")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    SandboxImagePlan(args.dockerfile, args.reference, args.definition_digest).prepare()


if __name__ == "__main__":
    main()
