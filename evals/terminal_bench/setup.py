import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.request import urlopen
from uuid import uuid4

from evals.terminal_bench.models import DatasetManifest, ReleaseAsset, UpstreamMetadata

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_FILE = Path(__file__).parent / "data" / "upstream.json"
DEFAULT_ROOT = Path(".local/terminal_bench")
DOWNLOAD_TIMEOUT_SECONDS = 120
COPY_BUFFER_BYTES = 1024 * 1024
CLIENT_FOLDER = "x86_64"
CLIENT_PLATFORM = "linux/amd64"
TASK_MANIFEST = "task.toml"
AGENT_TABLE_HEADER = "[agent]"
AGENT_NETWORK_LINE = 'network_mode = "no-network"'
NETWORK_POLICY_FIELDS = ("network_mode", "allowed_hosts")


def load_upstream(path: Path = UPSTREAM_FILE) -> UpstreamMetadata:
    """Load the pinned Terminal-Bench release and selected-task metadata."""
    return UpstreamMetadata.model_validate(json.loads(path.read_bytes()))


@dataclass(frozen=True)
class TerminalBenchSetup:
    """Verify and materialize the pinned Terminal-Bench inputs and remote client."""

    root: Path = DEFAULT_ROOT
    asset: Path | None = None
    repository_root: Path = REPOSITORY_ROOT
    upstream_file: Path = UPSTREAM_FILE

    def run(self) -> None:
        """Materialize the pinned tasks and Linux client."""
        upstream = load_upstream(self.upstream_file)
        archive = self.asset if self.asset is not None else self._download_asset(upstream.asset)
        self._verify_asset(archive, upstream.asset)
        tasks_root = self._materialize_tasks(archive, upstream)
        client = self._build_client(CLIENT_FOLDER, CLIENT_PLATFORM, self._client_source_digest())

        print(f"verified {upstream.asset.sha256} {archive}")
        for task in upstream.tasks:
            print(tasks_root / task.name)
        print(client)

    def _download_asset(self, asset: ReleaseAsset) -> Path:
        asset_root = self.root / "assets"
        asset_root.mkdir(parents=True, exist_ok=True)
        destination = asset_root / f"{asset.sha256.removeprefix('sha256:')}.tar.gz"
        if destination.is_file():
            return destination
        descriptor, temporary_name = tempfile.mkstemp(prefix=".tasks-", dir=asset_root)
        os.close(descriptor)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with urlopen(asset.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                with temporary.open("wb") as output:
                    while chunk := response.read(COPY_BUFFER_BYTES):
                        size_bytes += len(chunk)
                        if size_bytes > asset.size_bytes:
                            raise ValueError(
                                f"Terminal-Bench archive exceeds pinned size {asset.size_bytes}"
                            )
                        digest.update(chunk)
                        output.write(chunk)
            actual_digest = f"sha256:{digest.hexdigest()}"
            if size_bytes != asset.size_bytes:
                raise ValueError(
                    f"Terminal-Bench archive size {size_bytes} does not match {asset.size_bytes}"
                )
            if actual_digest != asset.sha256:
                raise ValueError(
                    f"Terminal-Bench archive digest {actual_digest} does not match {asset.sha256}"
                )
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination

    def _verify_asset(self, archive: Path, asset: ReleaseAsset) -> None:
        size_bytes = archive.stat().st_size
        if size_bytes != asset.size_bytes:
            raise ValueError(
                f"Terminal-Bench archive size {size_bytes} does not match {asset.size_bytes}"
            )
        with archive.open("rb") as source:
            digest = f"sha256:{hashlib.file_digest(source, 'sha256').hexdigest()}"
        if digest != asset.sha256:
            raise ValueError(
                f"Terminal-Bench archive digest {digest} does not match {asset.sha256}"
            )

    def _materialize_tasks(self, archive_path: Path, upstream: UpstreamMetadata) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".tasks-", dir=self.root))
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                members = self._safe_members(archive)
                dataset_member = next(
                    (
                        member
                        for path, member in members
                        if path == PurePosixPath("dataset.toml") and member.isfile()
                    ),
                    None,
                )
                if dataset_member is None:
                    raise ValueError("Terminal-Bench archive is missing dataset.toml")
                dataset_file = archive.extractfile(dataset_member)
                if dataset_file is None:
                    raise ValueError("Terminal-Bench archive dataset.toml is unreadable")
                manifest = DatasetManifest.model_validate(
                    tomllib.loads(dataset_file.read().decode())
                )
                self._validate_manifest(manifest, upstream)

                selected = {task.name for task in upstream.tasks}
                observed_roots = {
                    path.name
                    for path, member in members
                    if len(path.parts) == 1 and path.name in selected and member.isdir()
                }
                missing_roots = sorted(selected - observed_roots)
                if missing_roots:
                    raise ValueError(
                        "Terminal-Bench archive is missing selected task roots: "
                        + ", ".join(missing_roots)
                    )
                for path, member in members:
                    if path != PurePosixPath("dataset.toml") and (
                        not path.parts or path.parts[0] not in selected
                    ):
                        continue
                    destination = staging.joinpath(*path.parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                        destination.chmod(member.mode & 0o777)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(f"Terminal-Bench archive member is unreadable: {path}")
                    with destination.open("wb") as output:
                        shutil.copyfileobj(source, output, COPY_BUFFER_BYTES)
                    destination.chmod(member.mode & 0o777)
            for task in upstream.tasks:
                if task.restrict_agent_network:
                    self._restrict_agent_network(staging / task.name / TASK_MANIFEST)
            return self._replace_tasks(staging)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _safe_members(
        self, archive: tarfile.TarFile
    ) -> tuple[tuple[PurePosixPath, tarfile.TarInfo], ...]:
        result = []
        observed: set[PurePosixPath] = set()
        for member in archive.getmembers():
            raw_path = PurePosixPath(member.name)
            if raw_path.is_absolute() or ".." in raw_path.parts:
                raise ValueError(f"unsafe Terminal-Bench archive path: {member.name!r}")
            parts = tuple(part for part in raw_path.parts if part not in ("", "."))
            if not parts:
                if member.isdir():
                    continue
                raise ValueError(f"unsafe Terminal-Bench archive path: {member.name!r}")
            path = PurePosixPath(*parts)
            if path in observed:
                raise ValueError(f"duplicate archive member: {path}")
            observed.add(path)
            if member.issym() or member.islnk():
                raise ValueError(f"Terminal-Bench archive links are forbidden: {path}")
            if not member.isfile() and not member.isdir():
                raise ValueError(f"Terminal-Bench archive member type is forbidden: {path}")
            result.append((path, member))
        result = [
            (path, member)
            for path, member in result
            if not any(part.startswith("._") for part in path.parts)
        ]
        wrapper = PurePosixPath("tasks")
        wrapped_dataset = wrapper / "dataset.toml"
        if not (
            any(path == wrapped_dataset and member.isfile() for path, member in result)
            and all(path.parts[0] == wrapper.name for path, _ in result)
        ):
            return tuple(result)
        normalized = []
        normalized_paths: set[PurePosixPath] = set()
        for path, member in result:
            if path == wrapper:
                if not member.isdir():
                    raise ValueError("Terminal-Bench archive tasks wrapper is not a directory")
                continue
            normalized_path = PurePosixPath(*path.parts[1:])
            if normalized_path in normalized_paths:
                raise ValueError(f"duplicate archive member: {normalized_path}")
            normalized_paths.add(normalized_path)
            normalized.append((normalized_path, member))
        return tuple(normalized)

    def _restrict_agent_network(self, manifest: Path) -> None:
        if not manifest.is_file():
            raise FileNotFoundError(f"Terminal-Bench task manifest is missing: {manifest}")
        text = manifest.read_text()
        config = tomllib.loads(text)
        for table in ("agent", "environment"):
            section = config.get(table, {})
            declared = sorted(set(NETWORK_POLICY_FIELDS) & set(section))
            if declared:
                raise ValueError(
                    f"Terminal-Bench task declares its own network policy in [{table}]: "
                    f"{', '.join(declared)} in {manifest}"
                )
        if "agent" not in config:
            raise ValueError(f"Terminal-Bench task manifest has no [agent] table: {manifest}")
        lines = text.splitlines(keepends=True)
        headers = [index for index, line in enumerate(lines) if line.strip() == AGENT_TABLE_HEADER]
        if len(headers) != 1:
            raise ValueError(
                f"Terminal-Bench task manifest has {len(headers)} {AGENT_TABLE_HEADER} "
                f"header lines: {manifest}"
            )
        lines.insert(headers[0] + 1, f"{AGENT_NETWORK_LINE}\n")
        patched = "".join(lines)
        if tomllib.loads(patched)["agent"].get("network_mode") != "no-network":
            raise ValueError(f"Terminal-Bench agent network patch failed: {manifest}")
        manifest.write_text(patched)

    def _validate_manifest(self, manifest: DatasetManifest, upstream: UpstreamMetadata) -> None:
        by_id = {task.task_id: task for task in manifest.tasks}
        selected = {task.name: task for task in upstream.tasks}
        missing = sorted(set(selected) - set(by_id))
        if missing:
            raise ValueError(
                "Terminal-Bench dataset manifest is missing selected tasks: " + ", ".join(missing)
            )
        mismatched = sorted(
            name for name, task in selected.items() if by_id[name].digest != task.digest
        )
        if mismatched:
            raise ValueError(
                "Terminal-Bench dataset digests do not match the pins: " + ", ".join(mismatched)
            )

    def _replace_tasks(self, staging: Path) -> Path:
        destination = self.root / "tasks"
        backup = self.root / f".tasks-backup-{uuid4().hex}"
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise ValueError(f"Terminal-Bench tasks destination is not a directory: {destination}")
        if destination.exists():
            destination.replace(backup)
        try:
            staging.replace(destination)
        except BaseException:
            if backup.exists():
                backup.replace(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return destination

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
    parser.add_argument("--asset", type=Path)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    TerminalBenchSetup(root=args.root, asset=args.asset).run()


if __name__ == "__main__":
    main()
