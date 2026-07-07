"""`selfhost bundle`: freeze a deploy into a runnable artifact — OCI image recipe, pinned config,
lockfile.

The bundle pins every extension the deploy already runs (the current lockfile, or every discovered
extension when none is pinned yet) plus every catalog entry marked bundle-only — those disabled in
the store install here, at bundle time, and never at runtime. Each pin re-checks the installed
digest, so a bundle cannot freeze an extension the environment lacks. The output directory is a
`docker build` context: the Dockerfile installs the one `selfhost` distribution (core and every
first-party extension and pack ship in it) and copies the pinned config and lockfile, whose pins
narrow the active set and verify each digest at boot — so the same artifact boots identically on
any machine."""

from dataclasses import dataclass
from pathlib import Path

from selfhost.ext.loader import ExtensionPin, Lockfile, discovered, lockfile_path, read_lockfile
from selfhost.ext.store import Catalog, pin_for, selfhost_version

DOCKERFILE_BASE = "python:3.12-slim"
BUNDLE_CONFIG_NAME = "selfhost.toml"
BUNDLE_LOCKFILE_NAME = "selfhost.lock"
BUNDLE_DOCKERFILE_NAME = "Dockerfile"


@dataclass(frozen=True)
class BundleResult:
    out: Path
    dockerfile: Path
    config: Path
    lockfile: Path
    pins: tuple[ExtensionPin, ...]


@dataclass(frozen=True)
class Bundle:
    """Produce the deploy artifact into `out`. `catalog` is None when the store is off — then the
    bundle pins only what the deploy already runs, with no bundle-only additions."""

    config_path: Path
    catalog: Catalog | None
    out: Path

    def build(self) -> BundleResult:
        pins = self._pins()
        self.out.mkdir(parents=True, exist_ok=True)
        config = self.out / BUNDLE_CONFIG_NAME
        config.write_text(self.config_path.read_text())
        lockfile = self.out / BUNDLE_LOCKFILE_NAME
        lockfile.write_text(
            Lockfile(selfhost_version=selfhost_version(), extensions=pins).model_dump_json(indent=2)
            + "\n"
        )
        dockerfile = self.out / BUNDLE_DOCKERFILE_NAME
        dockerfile.write_text(self._dockerfile())
        return BundleResult(
            out=self.out, dockerfile=dockerfile, config=config, lockfile=lockfile, pins=pins
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
        return tuple(pin_for(name) for name in names)

    def _dockerfile(self) -> str:
        return "\n".join(
            (
                f"FROM {DOCKERFILE_BASE}",
                "WORKDIR /app",
                f"ENV SELFHOST_CONFIG=/app/{BUNDLE_CONFIG_NAME} "
                f"SELFHOST_LOCKFILE=/app/{BUNDLE_LOCKFILE_NAME}",
                f'RUN pip install --no-cache-dir "selfhost=={selfhost_version()}"',
                f"COPY {BUNDLE_CONFIG_NAME} {BUNDLE_LOCKFILE_NAME} /app/",
                'ENTRYPOINT ["selfhost"]',
                'CMD ["serve"]',
                "",
            )
        )
