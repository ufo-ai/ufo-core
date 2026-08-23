#!/usr/bin/env python3
"""Build the ufo sandbox image — one definition, two targets that stay in sync.

The E2B sandbox template and the Docker carrier's container image share the same layers
(``apply_layers``: apt packages, the pip/npm toolchain, the sandbox scripts, the start
command, the baked env). Only the base differs:

- E2B builds from the ``code-interpreter-v1`` template, which carries E2B's own provisioning layers,
  so the E2B path keeps a ``from_template`` base.
- The Docker image builds from the public ``e2bdev/code-interpreter`` image that the E2B template
  is itself based on, via the SDK's ``Template.to_dockerfile`` (which requires a ``from_image`` base
  and drops COPY modes and per-step run_cmd/copy user — hence the explicit ``chmod`` and the
  ``set_user`` bracketing: build as root, run as the non-root user). Rendering the Dockerfile is
  offline: it needs the ``e2b`` package installed but no E2B account, so a Docker-only deploy builds
  its image with ``--build-docker`` without any E2B key.

``sandbox/build_template.py`` (no args) builds, publishes, and verifies the E2B template — it boots
the published image and runs the baked-tool readiness probe, so a drifted build fails instead of
publishing silently. ``--check`` does the inverse and never publishes. ``--dockerfile`` emits the
Docker image's Dockerfile to stdout (build it with the repository root as the context).
``--build-docker`` renders that Dockerfile and runs ``docker build`` locally, tagging the image the
Docker carrier runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from daytona import (
    CreateSandboxFromSnapshotParams,
    CreateSnapshotParams,
    Daytona,
    DaytonaNotFoundError,
    Resources,
)
from daytona import Image as DaytonaImage
from daytona_api_client import SnapshotState
from e2b import Sandbox, Template
from e2b.sandbox.commands.command_handle import CommandExitException

from ufo.sdk.sandbox import PLAYWRIGHT_BROWSERS_DIR, SANDBOX_ENV, SANDBOX_SIZES, WORKSPACE_DIR

ROOT = Path(__file__).resolve().parents[1]
E2B_TEMPLATE_NAME = "ufo-sbx"
SBX_BIN_DIR = "/usr/local/bin"
UFO_DIR = "/etc/ufo"
# The in-sandbox binaries live in core beside the local carrier, which installs them onto its
# command PATH; the image bakes the same files, so a script behaves identically under every carrier.
IMAGE_SOURCE_DIR = ROOT / "core" / "src" / "ufo" / "sandbox" / "image"
# The path-containment guard the serve process imports as `ufo.sandbox.containment`, baked beside
# the scripts so the in-sandbox file ops confine paths with the same module, not a second copy.
MODULE_SOURCE_DIR = ROOT / "core" / "src" / "ufo" / "sandbox"

E2B_BASE_TEMPLATE = "code-interpreter-v1"


@dataclass(frozen=True)
class Sizing:
    cpu_count: int
    memory_mb: int


# The resources every E2B sandbox built from a tier's template gets. E2B fixes them at build time —
# the SDK's `Sandbox.create` takes no sizing argument at all — so an E2B deploy sizes its boxes here
# or nowhere: one template per size, and the agent's `sandbox_size` picks which one a fresh sandbox
# is created from. A size applies to every turn in that sandbox: a subagent runs in the sandbox of
# the turn that spawned it, so coding work takes the same size. `small` is the floor a repository
# checkout plus a toolchain build needs (the SDK default of 2 vCPU / 1024 MB outgrows on the
# memory axis); `large` is E2B's build ceiling of 8 vCPU / 8192 MiB.
SANDBOX_TIERS: dict[str, Sizing] = {
    "small": Sizing(cpu_count=2, memory_mb=2048),
    "medium": Sizing(cpu_count=4, memory_mb=4096),
    "large": Sizing(cpu_count=8, memory_mb=8192),
}
if tuple(SANDBOX_TIERS) != SANDBOX_SIZES:
    raise RuntimeError("SANDBOX_TIERS must define exactly the sizes SANDBOX_SIZES declares")
DOCKER_BASE_IMAGE = "e2bdev/code-interpreter:latest"
DOCKER_IMAGE_TAG = "ufo-sandbox:latest"
DAYTONA_SNAPSHOT_PREFIX = "ufo-sbx"
DAYTONA_DIGEST_CHARS = 12


@dataclass(frozen=True)
class DaytonaSizing:
    cpu: int
    memory_gb: int
    disk_gb: int


# The resources a tier's Daytona snapshot fixes — cpu and memory mirror the E2B tiers, and disk is
# the axis Daytona adds (its default 3 GiB outgrows on a repository checkout plus a toolchain).
DAYTONA_TIERS: dict[str, DaytonaSizing] = {
    "small": DaytonaSizing(cpu=2, memory_gb=2, disk_gb=10),
    "medium": DaytonaSizing(cpu=4, memory_gb=4, disk_gb=10),
    "large": DaytonaSizing(cpu=8, memory_gb=8, disk_gb=10),
}
if tuple(DAYTONA_TIERS) != SANDBOX_SIZES:
    raise RuntimeError("DAYTONA_TIERS must define exactly the sizes SANDBOX_SIZES declares")
START_COMMAND = "tail -f /dev/null"
# Build as root, run as the base image's non-root user. set_user brackets the layers because
# to_dockerfile drops the per-step run_cmd/copy user, and a sandbox that runs as root after sudo is
# stripped would be a regression.
BUILD_USER = "root"
RUNTIME_USER = "user"
# The template's readiness probe is the baked-tool check the publish gate enforces: a build that
# fails to bake a tool never goes READY, so the publish fails instead of drifting silently.
READY_VERIFY_TIMEOUT_SECONDS = 120
# The build-definition digest is baked here so --check can read it off the live template and compare
# to source — the drift gate that keeps publishing opt-in without letting a stale template pass.
BUILD_DIGEST_PATH = f"{UFO_DIR}/template-digest"

# poppler-utils → pdftotext/pdftoppm/pdfimages (pdf + media skills); chromium → the
# headless browser skills; libreoffice-{writer,calc,impress} → soffice for the office convert/recalc
# paths; pandoc → docx↔markdown text extraction; qpdf → pdf CLI merge/split/encrypt/repair;
# tesseract-ocr → the pytesseract OCR path for scanned PDFs; ffmpeg → the video/GIF encoder the
# media paths shell out to (imageio-ffmpeg wraps the same binary).
APT_PACKAGES = (
    "python3",
    "ca-certificates",
    "git",
    "curl",
    "jq",
    "ripgrep",
    "media-types",
    "poppler-utils",
    "chromium",
    "libreoffice-writer",
    "libreoffice-calc",
    "libreoffice-impress",
    "pandoc",
    "qpdf",
    "tesseract-ocr",
    "ffmpeg",
)
# gh is the sandboxed GitHub CLI behind the grant-sentinel GH_TOKEN (the egress proxy forwards its
# sentinel-carrying requests through the connector broker). It installs from GitHub's own apt repo —
# the distro archives lag years behind.
GH_INSTALL_COMMAND = (
    "mkdir -p -m 755 /etc/apt/keyrings && "
    "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg "
    "-o /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
    "chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
    'echo "deb [arch=$(dpkg --print-architecture) '
    "signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
    'https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list && '
    "apt-get update && apt-get install -y --no-install-recommends gh && "
    "rm -rf /var/lib/apt/lists/*"
)
# Skill runtimes the office/pdf/media/document-review scripts assume pre-installed.
PIP_PACKAGES = (
    "urllib3",
    "markitdown[pptx]",
    "openpyxl",
    "lxml",
    "PyMuPDF",
    "Pillow",
    "reportlab",
    "pdfplumber",
    "pypdfium2",
    "pypdf",
    "pdf2image",
    "pdf2docx",
    "pytesseract",
    "imageio-ffmpeg",
)
# Globally installed under the npm --prefix (/usr/local) so the pptx/docx/pdf/website/game scripts
# `require()` them from any cwd; SANDBOX_ENV exports NODE_PATH so resolution is base-independent.
# playwright drives the website-building game test client; its browser binary installs separately
# into PLAYWRIGHT_BROWSERS_DIR (the npm package alone can't launch).
NPM_PACKAGES = (
    "pptxgenjs",
    "react",
    "react-dom",
    "react-icons",
    "sharp",
    "docx",
    "pdf-lib",
    "playwright",
)
# Runtime env the image needs beyond the base — SANDBOX_ENV, defined in core beside its
# run-boundary consumer: NODE_PATH so node resolves the globally installed skill modules from any
# cwd, PLAYWRIGHT_BROWSERS_PATH so scripts find the Chromium baked at build time. The Docker
# carrier inherits it from the image ENV (docker exec keeps it); the E2B carrier merges it into
# every exec's envs, since e2b commands do not inherit the template ENV.
# The scripts baked into the image, with a version bumped on any content change so the digest moves.
SANDBOX_SCRIPTS: tuple[tuple[str, int], ...] = (("sbx", 2), ("sbxfs", 5))
# Importable modules baked beside them: a script's own directory is `sys.path[0]`, so a sibling here
# is what `sbxfs` imports, under every carrier, with no installed package inside the sandbox.
SANDBOX_MODULES: tuple[tuple[str, int], ...] = (("containment.py", 4),)
SANDBOX_TEMPLATE_READY_COMMAND = """
set -ex
command -v python3 >/dev/null
command -v node >/dev/null
command -v sbx >/dev/null
command -v sbxfs >/dev/null
command -v rg >/dev/null
command -v pdftotext >/dev/null
command -v pdftoppm >/dev/null
command -v soffice >/dev/null
command -v gh >/dev/null
browser="$(command -v chromium || command -v chromium-browser \\
  || command -v google-chrome || command -v google-chrome-stable || true)"
test -n "$browser"
""".strip()


def template_name(size: str) -> str:
    return f"{E2B_TEMPLATE_NAME}-{size}"


def daytona_definition_digest(size: str) -> str:
    """Content digest of a tier's Daytona snapshot: the Docker-image definition (the snapshot IS
    that Dockerfile, built by Daytona's own builder) plus the cpu, memory and disk the snapshot
    fixes — which no layer carries and only a new snapshot can change."""
    sizing = DAYTONA_TIERS[size]
    payload = {
        "docker": build_definition_digest(None),
        "cpu": sizing.cpu,
        "memory_gb": sizing.memory_gb,
        "disk_gb": sizing.disk_gb,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def daytona_snapshot_name(size: str) -> str:
    """`ufo-sbx-<size>-<digest12>` — the digest in the name is the drift gate: a snapshot exists
    under exactly the definition that built it, so `--check-daytona` is a name lookup and a changed
    definition simply names a snapshot that does not exist yet."""
    digest = daytona_definition_digest(size)[:DAYTONA_DIGEST_CHARS]
    return f"{DAYTONA_SNAPSHOT_PREFIX}-{size}-{digest}"


def daytona_refs() -> str:
    return ",".join(f"{size}={daytona_snapshot_name(size)}" for size in DAYTONA_TIERS)


def build_definition_digest(sizing: Sizing | None) -> str:
    """Content digest of everything apply_layers bakes — base, users, the start/ready commands, the
    apt/pip/npm package sets, the env, and each script (version + content hash) — plus the cpu and
    memory the build allocates, which no layer carries but which only a republish can change. The
    Docker image carries no sizing (the daemon imposes none), so its digest takes None. Baked into
    the image at BUILD_DIGEST_PATH and re-derived by --check, so any change to a tier's build
    definition is detectable as drift from that tier's live template."""
    payload = {
        "base": E2B_BASE_TEMPLATE,
        "workspace": WORKSPACE_DIR,
        "sizing": None
        if sizing is None
        else {"cpu_count": sizing.cpu_count, "memory_mb": sizing.memory_mb},
        "users": [BUILD_USER, RUNTIME_USER],
        "start": START_COMMAND,
        "ready": SANDBOX_TEMPLATE_READY_COMMAND,
        "apt": list(APT_PACKAGES),
        "gh": GH_INSTALL_COMMAND,
        "pip": list(PIP_PACKAGES),
        "npm": list(NPM_PACKAGES),
        "env": SANDBOX_ENV,
        "scripts": [
            {
                "name": name,
                "version": version,
                "sha256": hashlib.sha256((IMAGE_SOURCE_DIR / name).read_bytes()).hexdigest(),
            }
            for name, version in SANDBOX_SCRIPTS
        ],
        "modules": [
            {
                "name": name,
                "version": version,
                "sha256": hashlib.sha256((MODULE_SOURCE_DIR / name).read_bytes()).hexdigest(),
            }
            for name, version in SANDBOX_MODULES
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def apply_layers(builder: object, digest: str) -> object:
    builder.set_user(BUILD_USER)
    builder.run_cmd(
        "apt-get update && apt-get install -y --no-install-recommends "
        + " ".join(APT_PACKAGES)
        + " && rm -rf /var/lib/apt/lists/*"
    )
    builder.run_cmd("apt-get remove -y sudo || true; rm -rf /etc/sudoers /etc/sudoers.d")
    builder.run_cmd(GH_INSTALL_COMMAND)
    builder.run_cmd("python3 -m pip install --no-cache-dir " + " ".join(PIP_PACKAGES))
    builder.run_cmd(
        f"npm install -g --prefix /usr/local --no-fund --no-audit {' '.join(NPM_PACKAGES)}"
    )
    builder.run_cmd(
        f"PLAYWRIGHT_BROWSERS_PATH={PLAYWRIGHT_BROWSERS_DIR} playwright install chromium"
    )
    builder.run_cmd(f"mkdir -p {UFO_DIR} && chmod 0777 {UFO_DIR}")
    builder.run_cmd(
        f"mkdir -p {WORKSPACE_DIR} && chown {RUNTIME_USER}:{RUNTIME_USER} {WORKSPACE_DIR}"
    )
    builder.run_cmd(f"printf '%s' '{digest}' > {BUILD_DIGEST_PATH}")
    builder.set_envs(SANDBOX_ENV)
    targets = []
    for name, _ in SANDBOX_SCRIPTS:
        target = f"{SBX_BIN_DIR}/{name}"
        builder.copy((IMAGE_SOURCE_DIR / name).relative_to(ROOT), target, mode=0o755)
        targets.append(target)
    builder.run_cmd(f"chmod 0755 {' '.join(targets)}")
    modules = []
    for name, _ in SANDBOX_MODULES:
        target = f"{SBX_BIN_DIR}/{name}"
        builder.copy((MODULE_SOURCE_DIR / name).relative_to(ROOT), target, mode=0o644)
        modules.append(target)
    builder.run_cmd(f"chmod 0644 {' '.join(modules)}")
    builder.set_user(RUNTIME_USER)
    return builder.set_start_cmd(START_COMMAND, SANDBOX_TEMPLATE_READY_COMMAND)


class _DaytonaImageBuilder:
    """`apply_layers`' builder over the Daytona `Image` — the third render of the one definition.
    Daytona's builder runs server-side and uploads each copied file as build context, so no
    registry sits between the definition and the snapshot."""

    def __init__(self) -> None:
        self.image = DaytonaImage.base(DOCKER_BASE_IMAGE)

    def set_user(self, user: str) -> _DaytonaImageBuilder:
        self.image = self.image.dockerfile_commands([f"USER {user}"])
        return self

    def run_cmd(self, command: str) -> _DaytonaImageBuilder:
        self.image = self.image.run_commands(command)
        return self

    def copy(self, source: Path, target: str, mode: int) -> _DaytonaImageBuilder:
        self.image = self.image.add_local_file(ROOT / source, target)
        return self

    def set_envs(self, envs: dict[str, str]) -> _DaytonaImageBuilder:
        self.image = self.image.env(envs)
        return self

    def set_start_cmd(self, start: str, ready: str) -> DaytonaImage:
        return self.image.entrypoint(["sh", "-c", start])


def daytona_image(size: str) -> DaytonaImage:
    builder = _DaytonaImageBuilder()
    return apply_layers(builder, daytona_definition_digest(size))


def build_daytona_snapshots() -> None:
    """Publish one snapshot per tier from the shared definition, verify each by booting it and
    running the baked-tool readiness probe, and print the DAYTONA_SNAPSHOTS wire line. Digest-named
    means an existing active snapshot is current by construction, so a republish with nothing
    changed creates nothing; one that fell inactive (two weeks unused) is reactivated."""
    daytona = Daytona()
    references = []
    for size, sizing in DAYTONA_TIERS.items():
        name = daytona_snapshot_name(size)
        snapshot = _daytona_snapshot(daytona, name)
        if snapshot is None:
            daytona.snapshot.create(
                CreateSnapshotParams(
                    name=name,
                    image=daytona_image(size),
                    resources=Resources(
                        cpu=sizing.cpu, memory=sizing.memory_gb, disk=sizing.disk_gb
                    ),
                ),
                on_logs=print,
            )
        elif snapshot.state != SnapshotState.ACTIVE:
            daytona.snapshot.activate(snapshot)
        verify_daytona_snapshot(daytona, name)
        references.append(f"{size}={name}")
    print(",".join(references))


def _daytona_snapshot(daytona: Daytona, name: str) -> object | None:
    try:
        return daytona.snapshot.get(name)
    except DaytonaNotFoundError:
        return None


def verify_daytona_snapshot(daytona: Daytona, name: str) -> None:
    """Publish gate: boot a sandbox from the snapshot and run the baked-tool readiness probe — the
    same command the E2B template bakes as its ready cmd, run through `exec` because Daytona has no
    ready hook."""
    sandbox = daytona.create(CreateSandboxFromSnapshotParams(snapshot=name, auto_stop_interval=5))
    try:
        result = sandbox.process.exec(
            f"sh -c {shlex.quote(SANDBOX_TEMPLATE_READY_COMMAND)}",
            timeout=READY_VERIFY_TIMEOUT_SECONDS,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"published snapshot {name} is missing baked runtime tools: {result.result}"
            )
    finally:
        sandbox.delete()


def check_daytona_snapshots() -> None:
    """Drift gate (never publishes): every tier's digest-named snapshot exists and is active, else
    exit red until someone republishes."""
    daytona = Daytona()
    for size in DAYTONA_TIERS:
        name = daytona_snapshot_name(size)
        snapshot = _daytona_snapshot(daytona, name)
        if snapshot is None:
            raise SystemExit(
                f"daytona snapshot {name} is missing; "
                "republish with sandbox/build_template.py --daytona"
            )
        if snapshot.state != SnapshotState.ACTIVE:
            raise SystemExit(
                f"daytona snapshot {name} is {snapshot.state}, not active; "
                "republish with sandbox/build_template.py --daytona"
            )
        print(f"{name} active")


def e2b_template(size: str) -> object:
    builder = Template(file_context_path=ROOT).from_template(E2B_BASE_TEMPLATE)
    return apply_layers(builder, build_definition_digest(SANDBOX_TIERS[size]))


def pod_dockerfile() -> str:
    builder = Template(file_context_path=ROOT).from_image(DOCKER_BASE_IMAGE)
    return Template.to_dockerfile(apply_layers(builder, build_definition_digest(None)))


def build_docker_image() -> None:
    """Render the Dockerfile from the single definition and build the image the Docker carrier runs.
    Offline — no E2B account, only the local Docker daemon — so a Docker-only deploy builds with no
    E2B key. The context is the repository root, matching the COPY paths apply_layers emits."""
    process = subprocess.run(
        ["docker", "build", "-t", DOCKER_IMAGE_TAG, "-f", "-", str(ROOT)],
        input=pod_dockerfile().encode(),
        check=False,
    )
    if process.returncode != 0:
        raise SystemExit(f"docker build failed (rc {process.returncode})")
    print(DOCKER_IMAGE_TAG)


def verify_published_template(name: str) -> None:
    """Publish gate: boot a sandbox from the freshly built template and run the baked-tool readiness
    probe. A template missing a baked tool fails here, so the build cannot report success on a
    drifted image — the same SANDBOX_TEMPLATE_READY_COMMAND the publish path bakes as the ready
    cmd."""
    sandbox = Sandbox.create(template=name, timeout=READY_VERIFY_TIMEOUT_SECONDS)
    try:
        result = sandbox.commands.run(
            SANDBOX_TEMPLATE_READY_COMMAND, timeout=READY_VERIFY_TIMEOUT_SECONDS
        )
    except CommandExitException as error:
        raise RuntimeError(f"published template {name} is missing baked runtime tools") from error
    finally:
        sandbox.kill()
    if result.exit_code != 0:
        raise RuntimeError(f"published template {name} is missing baked runtime tools")


def check_published_template(name: str, expected: str) -> None:
    """Drift gate (never publishes): boot the live template, read its baked build digest, and
    compare to the current source definition. A stale template — built from older inputs, or
    predating the digest — fails here, so CI stays red until someone republishes."""
    sandbox = Sandbox.create(template=name, timeout=READY_VERIFY_TIMEOUT_SECONDS)
    try:
        result = sandbox.commands.run(
            f"cat {BUILD_DIGEST_PATH}", timeout=READY_VERIFY_TIMEOUT_SECONDS
        )
    except CommandExitException as error:
        raise RuntimeError(
            f"published template {name} predates the build digest; republish the sandbox template"
        ) from error
    finally:
        sandbox.kill()
    actual = result.stdout.strip()
    if actual != expected:
        raise RuntimeError(
            f"published template {name} is stale (live {actual or '<none>'} != source {expected}); "
            "republish the sandbox template"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="build-sandbox-template")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dockerfile",
        action="store_true",
        help="emit the Docker carrier's Dockerfile instead of building the E2B template",
    )
    group.add_argument(
        "--build-docker",
        action="store_true",
        help="render the Dockerfile and docker build the Docker carrier's image (no E2B key)",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="fail if the live E2B template no longer matches the source definition (drift gate)",
    )
    group.add_argument(
        "--daytona",
        action="store_true",
        help="build, verify, and print the Daytona snapshots (skips digest-named ones that exist)",
    )
    group.add_argument(
        "--daytona-refs",
        action="store_true",
        help="print the DAYTONA_SNAPSHOTS wire line from the source definition (no API)",
    )
    group.add_argument(
        "--check-daytona",
        action="store_true",
        help="fail unless every tier's digest-named Daytona snapshot exists and is active",
    )
    args = parser.parse_args()
    if args.dockerfile:
        sys.stdout.write(pod_dockerfile())
        return
    if args.build_docker:
        build_docker_image()
        return
    if args.check:
        for size, sizing in SANDBOX_TIERS.items():
            check_published_template(template_name(size), build_definition_digest(sizing))
            print(f"{template_name(size)} up to date")
        return
    if args.daytona:
        build_daytona_snapshots()
        return
    if args.daytona_refs:
        print(daytona_refs())
        return
    if args.check_daytona:
        check_daytona_snapshots()
        return
    references = []
    for size, sizing in SANDBOX_TIERS.items():
        info = Template.build(
            e2b_template(size),
            name=template_name(size),
            cpu_count=sizing.cpu_count,
            memory_mb=sizing.memory_mb,
        )
        reference = f"{info.name}:{info.build_id}"
        verify_published_template(reference)
        references.append(f"{size}={reference}")
    print(",".join(references))


if __name__ == "__main__":
    main()
