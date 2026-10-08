"""`ufoctl bundle`: freeze a deploy into a runnable artifact — OCI image recipe, sandbox client,
pinned config, lockfile.

The bundle pins every extension the deploy already runs (the current lockfile, or every discovered
extension when none is pinned yet) plus every catalog entry marked bundle-only — those disabled in
the store install here, at bundle time, and never at runtime. Each pin hashes the built wheels, so
the lock names the bytes the image installs even when the local environment holds older source.
The output directory is a `docker build` context: the Dockerfile installs every wheel the build
produced — the `ufo` distribution with the harness, runtime, first-party extensions and packs, and
a deploy's own distribution beside it — at the versions the project's lockfile resolved, installs
the sandbox client, names the distributions the
loader trusts with privileged capabilities, and copies the pinned config and lockfile, whose pins
narrow the active set and verify each digest at boot — so the same artifact boots identically on
any machine."""

from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from ufo.host.ext.loader import (
    FIRST_PARTY_ENV,
    ExtensionPin,
    Lockfile,
    discovered,
    extension_content_digest,
    first_party_distributions,
    lockfile_path,
    read_lockfile,
)
from ufo.host.ext.store import Catalog, ufo_version

DOCKERFILE_BASE = "python:3.12-slim-trixie"
PCRE2_MINIMUM_VERSION = "10.46-1~deb13u3"
BUNDLE_CONFIG_NAME = "ufo.toml"
BUNDLE_LOCKFILE_NAME = "ufo.lock"
BUNDLE_CONSTRAINTS_NAME = "constraints.txt"
BUNDLE_DOCKERFILE_NAME = "Dockerfile"
BUNDLE_CLIENT_BINARY_NAME = "ufo-sandbox-client"
BUNDLE_CLIENT_INSTALL_PATH = f"/usr/local/bin/{BUNDLE_CLIENT_BINARY_NAME}"
BUNDLE_WHEELS_DIR = "/tmp/wheels"


def wheel_name() -> str:
    """The tenant runtime wheel the CLI builds into the closed bundle."""
    return f"ufo-{ufo_version()}-py3-none-any.whl"


@dataclass(frozen=True)
class BundleResult:
    out: Path
    dockerfile: Path
    config: Path
    lockfile: Path
    client_binary: Path
    pins: tuple[ExtensionPin, ...]


@dataclass(frozen=True)
class Bundle:
    """Produce the deploy artifact into `out`. `catalog` is None when the store is off — then the
    bundle pins only what the deploy already runs, with no bundle-only additions. `constraints`
    is the project lockfile exported as pip constraints: every dependency of every wheel installs
    at the version the lock resolved, never at what the index serves the day the image builds."""

    config_path: Path
    catalog: Catalog | None
    out: Path
    wheels: tuple[Path, ...]
    client_binary: Path
    constraints: str

    def build(self) -> BundleResult:
        pins = self._pins()
        self.out.mkdir(parents=True, exist_ok=True)
        config = self.out / BUNDLE_CONFIG_NAME
        config.write_text(self.config_path.read_text())
        lockfile = self.out / BUNDLE_LOCKFILE_NAME
        lockfile.write_text(
            Lockfile(ufo_version=ufo_version(), extensions=pins).model_dump_json(indent=2) + "\n"
        )
        (self.out / BUNDLE_CONSTRAINTS_NAME).write_text(self.constraints)
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
            files = self._package_files(top)
            if not files:
                built = ", ".join(wheel.name for wheel in self.wheels)
                raise RuntimeError(f"extension package {top!r} is absent from {built}")
            pins.append(
                ExtensionPin(
                    name=name,
                    version=manifest.version,
                    digest=extension_content_digest(files),
                )
            )
        return tuple(pins)

    def _package_files(self, top: str) -> dict[str, bytes]:
        prefix = f"{top}/"
        module = f"{top}.py"
        for path in self.wheels:
            with ZipFile(path) as wheel:
                names = wheel.namelist()
                package = {
                    name.removeprefix(prefix): wheel.read(name)
                    for name in names
                    if name.startswith(prefix)
                    and not name.endswith("/")
                    and "__pycache__" not in Path(name).parts
                    and Path(name).suffix != ".pyc"
                }
                if package:
                    return package
                if module in names:
                    return {module: wheel.read(module)}
        return {}

    def _dockerfile(self) -> str:
        wheels = " ".join(wheel.name for wheel in self.wheels)
        installed = " ".join(f"{BUNDLE_WHEELS_DIR}/{wheel.name}" for wheel in self.wheels)
        first_party = ",".join(sorted(first_party_distributions()))
        return "\n".join(
            (
                f"FROM {DOCKERFILE_BASE}",
                "RUN apt-get update "
                "&& apt-get install -y --no-install-recommends libpcre2-8-0 "
                "&& dpkg --compare-versions "
                "\"$(dpkg-query -W -f='${Version}' libpcre2-8-0)\" "
                f"ge {PCRE2_MINIMUM_VERSION} "
                "&& rm -rf /var/lib/apt/lists/*",
                "WORKDIR /app",
                f"ENV UFO_CONFIG=/app/{BUNDLE_CONFIG_NAME} "
                f"UFO_LOCKFILE=/app/{BUNDLE_LOCKFILE_NAME} "
                f"{FIRST_PARTY_ENV}={first_party}",
                f"COPY {wheels} {BUNDLE_CONSTRAINTS_NAME} {BUNDLE_WHEELS_DIR}/",
                f"RUN pip install --no-cache-dir -c {BUNDLE_WHEELS_DIR}/{BUNDLE_CONSTRAINTS_NAME} "
                f"{installed} && rm -r {BUNDLE_WHEELS_DIR}",
                f"COPY {BUNDLE_CONFIG_NAME} {BUNDLE_LOCKFILE_NAME} /app/",
                f"COPY --chmod=0555 {BUNDLE_CLIENT_BINARY_NAME} {BUNDLE_CLIENT_INSTALL_PATH}",
                f"ENV UFO_CLIENT_BINARY={BUNDLE_CLIENT_INSTALL_PATH}",
                'ENTRYPOINT ["ufoctl"]',
                'CMD ["serve"]',
                "",
            )
        )
