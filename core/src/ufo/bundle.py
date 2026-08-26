"""`ufoctl bundle`: freeze a deploy into a runnable artifact — OCI image recipe, pinned config,
lockfile.

The bundle pins every extension the deploy already runs (the current lockfile, or every discovered
extension when none is pinned yet) plus every catalog entry marked bundle-only — those disabled in
the store install here, at bundle time, and never at runtime. Each pin hashes the built wheel, so
the lock names the bytes the image installs even when the local environment holds older source.
The output directory is a `docker build` context: the Dockerfile installs the one `ufo`
distribution (core and every first-party extension and pack ship in it) and copies the pinned
config and lockfile, whose pins narrow the active set and verify each digest at boot — so the same
artifact boots identically on any machine."""

from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from ufo.ext.loader import (
    ExtensionPin,
    Lockfile,
    discovered,
    extension_content_digest,
    lockfile_path,
    read_lockfile,
)
from ufo.ext.store import Catalog, ufo_version

DOCKERFILE_BASE = "python:3.12-slim"
BUNDLE_CONFIG_NAME = "ufo.toml"
BUNDLE_LOCKFILE_NAME = "ufo.lock"
BUNDLE_DOCKERFILE_NAME = "Dockerfile"
BUNDLE_CLIENT_BINARY_NAME = "ufo-sandbox-client"
BUNDLE_CLIENT_INSTALL_PATH = f"/usr/local/bin/{BUNDLE_CLIENT_BINARY_NAME}"


def wheel_name() -> str:
    """The wheel the CLI verb builds beside this context — the closed distribution the Dockerfile
    installs, since no index carries `ufo`."""
    return f"ufo-{ufo_version()}-py3-none-any.whl"


@dataclass(frozen=True)
class BundleResult:
    out: Path
    dockerfile: Path
    config: Path
    lockfile: Path
    client_binary: Path | None
    pins: tuple[ExtensionPin, ...]


@dataclass(frozen=True)
class Bundle:
    """Produce the deploy artifact into `out`. `catalog` is None when the store is off — then the
    bundle pins only what the deploy already runs, with no bundle-only additions."""

    config_path: Path
    catalog: Catalog | None
    out: Path
    wheel: Path
    client_binary: Path | None = None

    def build(self) -> BundleResult:
        pins = self._pins()
        self.out.mkdir(parents=True, exist_ok=True)
        config = self.out / BUNDLE_CONFIG_NAME
        config.write_text(self.config_path.read_text())
        lockfile = self.out / BUNDLE_LOCKFILE_NAME
        lockfile.write_text(
            Lockfile(ufo_version=ufo_version(), extensions=pins).model_dump_json(indent=2) + "\n"
        )
        bundled_client = None
        if self.client_binary is not None:
            bundled_client = self.out / BUNDLE_CLIENT_BINARY_NAME
            bundled_client.write_bytes(self.client_binary.read_bytes())
        dockerfile = self.out / BUNDLE_DOCKERFILE_NAME
        dockerfile.write_text(self._dockerfile())
        return BundleResult(
            out=self.out,
            dockerfile=dockerfile,
            config=config,
            lockfile=lockfile,
            client_binary=bundled_client,
            pins=pins,
        )

    def _pins(self) -> tuple[ExtensionPin, ...]:
        installed = discovered()
        path = lockfile_path()
        base = (
            tuple(pin.name for pin in read_lockfile(path).extensions)
            if path.exists()
            else tuple(installed)
        )
        bundle_only = (
            tuple(entry.name for entry in self.catalog.extensions if entry.disabled)
            if self.catalog is not None
            else ()
        )
        names = list(dict.fromkeys((*base, *bundle_only)))
        pins: list[ExtensionPin] = []
        for name in names:
            found = installed.get(name)
            if found is None:
                raise RuntimeError(f"extension {name!r} is not installed in this environment")
            manifest, entry = found
            top = entry.module.split(".", 1)[0]
            prefix = f"{top}/"
            with ZipFile(self.wheel) as wheel:
                package = {
                    path.removeprefix(prefix): wheel.read(path)
                    for path in wheel.namelist()
                    if path.startswith(prefix)
                    and not path.endswith("/")
                    and "__pycache__" not in Path(path).parts
                    and Path(path).suffix != ".pyc"
                }
                module = f"{top}.py"
                files = package or (
                    {Path(module).name: wheel.read(module)} if module in wheel.namelist() else {}
                )
            if not files:
                raise RuntimeError(f"extension package {top!r} is absent from {self.wheel.name}")
            pins.append(
                ExtensionPin(
                    name=name,
                    version=manifest.version,
                    digest=extension_content_digest(files),
                )
            )
        return tuple(pins)

    def _dockerfile(self) -> str:
        sandbox_client = (
            (
                f"COPY --chmod=0555 {BUNDLE_CLIENT_BINARY_NAME} {BUNDLE_CLIENT_INSTALL_PATH}",
                f"ENV UFO_CLIENT_BINARY={BUNDLE_CLIENT_INSTALL_PATH}",
            )
            if self.client_binary is not None
            else ()
        )
        return "\n".join(
            (
                f"FROM {DOCKERFILE_BASE}",
                "WORKDIR /app",
                f"ENV UFO_CONFIG=/app/{BUNDLE_CONFIG_NAME} "
                f"UFO_LOCKFILE=/app/{BUNDLE_LOCKFILE_NAME}",
                f"COPY {wheel_name()} /tmp/{wheel_name()}",
                f"RUN pip install --no-cache-dir /tmp/{wheel_name()} && rm /tmp/{wheel_name()}",
                f"COPY {BUNDLE_CONFIG_NAME} {BUNDLE_LOCKFILE_NAME} /app/",
                *sandbox_client,
                'ENTRYPOINT ["ufoctl"]',
                'CMD ["serve"]',
                "",
            )
        )
