import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evals.terminal_bench.models import UpstreamMetadata

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_FILE = Path(__file__).parent / "data" / "upstream.json"
DEFAULT_ROOT = Path(".local/terminal_bench")
COPY_BUFFER_BYTES = 1024 * 1024
CLIENT_FOLDER = "x86_64"
CLIENT_PLATFORM = "linux/amd64"


def load_upstream(path: Path = UPSTREAM_FILE) -> UpstreamMetadata:
    """Load the pinned Terminal-Bench dataset and roster metadata."""
    return UpstreamMetadata.model_validate(json.loads(path.read_bytes()))


@dataclass(frozen=True)
class TerminalBenchSetup:
    """Build the native client uploaded into Terminal-Bench environments."""

    root: Path = DEFAULT_ROOT
    repository_root: Path = REPOSITORY_ROOT
    upstream_file: Path = UPSTREAM_FILE

    def run(self) -> None:
        """Build the Linux client and print the immutable benchmark identity."""
        upstream = load_upstream(self.upstream_file)
        client = self._build_client(CLIENT_FOLDER, CLIENT_PLATFORM, self._client_source_digest())

        print(f"{upstream.dataset}@{upstream.digest} revision {upstream.revision}")
        print(f"{len(upstream.tasks)} tasks")
        print(client)

    def _client_source_digest(self) -> str:
        files = [
            Path(__file__).parent / "client.Dockerfile",
            self.repository_root / ".dockerignore",
        ]
        files.extend(
            path
            for path in (self.repository_root / "client").rglob("*")
            if path.is_file()
            and "target" not in path.relative_to(self.repository_root / "client").parts
        )
        digest = hashlib.sha256()
        for path in sorted(files):
            relative = path.relative_to(self.repository_root)
            digest.update(relative.as_posix().encode())
            digest.update(b"\0")
            with path.open("rb") as source:
                while chunk := source.read(COPY_BUFFER_BYTES):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def _build_client(self, folder: str, platform: str, source_digest: str) -> Path:
        cache = self.root / "builds" / source_digest / folder
        executable = cache / "ufo"
        if not executable.is_file():
            cache.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{folder}-", dir=cache.parent))
            try:
                subprocess.run(
                    (
                        "docker",
                        "buildx",
                        "build",
                        "--file",
                        str(Path(__file__).parent / "client.Dockerfile"),
                        "--platform",
                        platform,
                        "--target",
                        "export",
                        "--output",
                        f"type=local,dest={staging}",
                        str(self.repository_root),
                    ),
                    check=True,
                )
                built = staging / "ufo"
                if not built.is_file():
                    raise RuntimeError(f"Docker Buildx did not export the {folder} ufo client")
                built.chmod(0o755)
                staging.replace(cache)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        if not os.access(executable, os.X_OK):
            raise RuntimeError(f"cached Terminal-Bench client is not executable: {executable}")
        destination = self.root / "bin" / folder / "ufo"
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".ufo-", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(executable, temporary)
            temporary.chmod(0o755)
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.terminal_bench.setup")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    TerminalBenchSetup(root=args.root).run()


if __name__ == "__main__":
    main()
