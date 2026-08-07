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
import subprocess
import sys
from pathlib import Path

from e2b import Sandbox, Template
from e2b.sandbox.commands.command_handle import CommandExitException
from ufo_ext_e2b import (
    PLAYWRIGHT_BROWSERS_DIR,
    SANDBOX_ENV,
)

ROOT = Path(__file__).resolve().parents[1]
E2B_TEMPLATE_NAME = "ufo-sbx"
SBX_BIN_DIR = "/usr/local/bin"
UFO_DIR = "/etc/ufo"
# The in-sandbox binaries live in core beside the local carrier, which installs them onto its
# command PATH; the image bakes the same files, so a script behaves identically under every carrier.
IMAGE_SOURCE_DIR = ROOT / "core" / "src" / "ufo" / "sandbox" / "image"

E2B_BASE_TEMPLATE = "code-interpreter-v1"
# RAM every E2B sandbox built from this template gets. E2B fixes memory at build time — the SDK's
# `Sandbox.create` takes no sizing argument at all — so an E2B deploy sizes its boxes here or
# nowhere. It applies to every turn on that carrier: a subagent runs in the sandbox of the turn
# that spawned it, so coding work takes this size too. The SDK's default is 1024, which a
# repository checkout plus a toolchain build inside one box outgrows.
TEMPLATE_MEMORY_MB = 2048
DOCKER_BASE_IMAGE = "e2bdev/code-interpreter:latest"
DOCKER_IMAGE_TAG = "ufo-sandbox:latest"
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
# Runtime env the image needs beyond the base — SANDBOX_ENV, defined in ufo_ext_e2b beside its
# run-boundary consumer: NODE_PATH so node resolves the globally installed skill modules from any
# cwd, PLAYWRIGHT_BROWSERS_PATH so scripts find the Chromium baked at build time. The Docker
# carrier inherits it from the image ENV (docker exec keeps it); the E2B carrier merges it into
# every exec's envs, since e2b commands do not inherit the template ENV.
# The scripts baked into the image, with a version bumped on any content change so the digest moves.
SANDBOX_SCRIPTS: tuple[tuple[str, int], ...] = (("sbx", 2), ("sbxfs", 2))
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


def build_definition_digest() -> str:
    """Content digest of everything apply_layers bakes — base, users, the start/ready commands, the
    apt/pip/npm package sets, the env, and each script (version + content hash) — plus the memory
    the build allocates, which no layer carries but which only a republish can change. Baked into
    the image at BUILD_DIGEST_PATH and re-derived by --check, so any change to the build definition
    is detectable as drift from the live template."""
    payload = {
        "base": E2B_BASE_TEMPLATE,
        "memory_mb": TEMPLATE_MEMORY_MB,
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
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def apply_layers(builder: object) -> object:
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
    builder.run_cmd(f"printf '%s' '{build_definition_digest()}' > {BUILD_DIGEST_PATH}")
    builder.set_envs(SANDBOX_ENV)
    targets = []
    for name, _ in SANDBOX_SCRIPTS:
        target = f"{SBX_BIN_DIR}/{name}"
        builder.copy((IMAGE_SOURCE_DIR / name).relative_to(ROOT), target, mode=0o755)
        targets.append(target)
    builder.run_cmd(f"chmod 0755 {' '.join(targets)}")
    builder.set_user(RUNTIME_USER)
    return builder.set_start_cmd(START_COMMAND, SANDBOX_TEMPLATE_READY_COMMAND)


def e2b_template() -> object:
    builder = Template(file_context_path=ROOT).from_template(E2B_BASE_TEMPLATE)
    return apply_layers(builder)


def pod_dockerfile() -> str:
    builder = Template(file_context_path=ROOT).from_image(DOCKER_BASE_IMAGE)
    return Template.to_dockerfile(apply_layers(builder))


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


def check_published_template(name: str) -> None:
    """Drift gate (never publishes): boot the live template, read its baked build digest, and
    compare to the current source definition. A stale template — built from older inputs, or
    predating the digest — fails here, so CI stays red until someone republishes."""
    expected = build_definition_digest()
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
    args = parser.parse_args()
    if args.dockerfile:
        sys.stdout.write(pod_dockerfile())
        return
    if args.build_docker:
        build_docker_image()
        return
    if args.check:
        check_published_template(E2B_TEMPLATE_NAME)
        print(f"{E2B_TEMPLATE_NAME} up to date")
        return
    info = Template.build(e2b_template(), name=E2B_TEMPLATE_NAME, memory_mb=TEMPLATE_MEMORY_MB)
    reference = f"{info.name}:{info.build_id}"
    verify_published_template(reference)
    print(reference)


if __name__ == "__main__":
    main()
