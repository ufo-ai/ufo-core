#!/usr/bin/env python3
"""Build the ufo sandbox image — one definition, two targets that stay in sync.

The E2B sandbox template and the Docker carrier's container image share the same layers
(``apply_layers``: apt packages, the pip/npm toolchain, the ``ufo`` client, the start
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

Every mode that actually builds stages the compiled ``ufo`` client and system skill bundle into the
build context first; rendering the Dockerfile alone needs neither artifact, so the CI job that only
wants the image's cache key derives it without a Rust toolchain or build-context writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from time import sleep

import httpx
from e2b import Sandbox, Template
from e2b.exceptions import SandboxException, TimeoutException
from e2b.sandbox.commands.command_handle import CommandExitException
from e2b.template.main import TemplateBuilder, TemplateFinal
from e2b.template.types import BuildInfo

from ufo.harness.sandbox.client_binary import CLIENT_BINARY_NAME, client_binary
from ufo.runtime.skills.runtime import SystemSkillBundle, discover_skills
from ufo.sdk.sandbox import (
    PLAYWRIGHT_BROWSERS_DIR,
    PLAYWRIGHT_VERSION,
    SANDBOX_ENV,
    SANDBOX_GID,
    SANDBOX_RUNS_ROOT,
    SANDBOX_SIZES,
    SANDBOX_TMPDIR,
    SANDBOX_UID,
    SYSTEM_SKILLS_ROOT,
    WORKSPACE_DIR,
)

ROOT = Path(__file__).resolve().parents[1]
E2B_TEMPLATE_NAME = "ufo-sbx"
SBX_BIN_DIR = "/usr/local/bin"
UFO_DIR = "/etc/ufo"
MODULE_SOURCE_DIR = ROOT / "core" / "src" / "ufo" / "harness"
CLIENT_SOURCE_DIR = ROOT / "client"
SANDBOX_CLIENT_TARGET = "x86_64-unknown-linux-musl"
CLIENT_STAGE_DIR = ROOT / "sandbox" / "artifacts"
CLIENT_STAGE_PATH = CLIENT_STAGE_DIR / CLIENT_BINARY_NAME
SYSTEM_SKILLS_STAGE_PATH = CLIENT_STAGE_DIR / "system-skills.zip"
SYSTEM_SKILLS_ARCHIVE_PATH = f"{UFO_DIR}/system-skills.zip"
CLIENT_ROOT_FILES = ("Cargo.toml", "Cargo.lock", "build.rs")
CLIENT_SOURCE_DIRS = ("src", "licenses", "scripts")

E2B_BASE_TEMPLATE = "code-interpreter-v1"


@dataclass(frozen=True)
class Sizing:
    cpu_count: int
    memory_mb: int


SANDBOX_TIERS: dict[str, Sizing] = {
    "small": Sizing(cpu_count=2, memory_mb=2048),
    "medium": Sizing(cpu_count=4, memory_mb=4096),
    "large": Sizing(cpu_count=8, memory_mb=8192),
}
if tuple(SANDBOX_TIERS) != SANDBOX_SIZES:
    raise RuntimeError("SANDBOX_TIERS must define exactly the sizes SANDBOX_SIZES declares")
DOCKER_BASE_IMAGE = "e2bdev/code-interpreter:latest"
DOCKER_IMAGE_TAG = "ufo-sandbox:latest"
START_COMMAND = "tail -f /dev/null"
BUILD_USER = "root"
RUNTIME_USER = "user"
READY_VERIFY_TIMEOUT_SECONDS = 120
BUILD_ATTEMPTS = 3
BUILD_RETRY_BACKOFF_SECONDS = 15
BUILD_DIGEST_PATH = f"{UFO_DIR}/template-digest"

APT_PACKAGES = (
    "python3",
    "ca-certificates",
    "git",
    "curl",
    "jq",
    "bc",
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
APT_HTTPS_COMMAND = (
    "find /etc/apt -type f \\( -name '*.list' -o -name '*.sources' \\) -exec sed -i '"
    "s|http://archive.ubuntu.com|https://archive.ubuntu.com|g; "
    "s|http://security.ubuntu.com|https://security.ubuntu.com|g; "
    "s|http://ports.ubuntu.com|https://ports.ubuntu.com|g; "
    "s|http://deb.debian.org|https://deb.debian.org|g; "
    "s|http://security.debian.org|https://security.debian.org|g; "
    "s|http://cdn-fastly.deb.debian.org|https://cdn-fastly.deb.debian.org|g' {} +"
)
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
NODE_VERSION = "24.19.0"
PNPM_VERSION = "11.24.0"
NODE_INSTALL_COMMAND = (
    'case "$(dpkg --print-architecture)" in '
    "amd64) node_arch=x64; node_sha="
    "f625d97cd707df4ff96254916fbc5ff014f09c09effe5a1e0ca8f6d41a8789d4 ;; "
    "arm64) node_arch=arm64; node_sha="
    "d28c8a5bf0a808f0ed434a1dce8c54ae98f0371c0bd86ac58abc613f73e6643f ;; "
    '*) echo "unsupported Node architecture" >&2; exit 1 ;; esac && '
    f"archive=/tmp/node-v{NODE_VERSION}-linux-${{node_arch}}.tar.gz && "
    f"curl -fsSL https://nodejs.org/dist/v{NODE_VERSION}/"
    f"node-v{NODE_VERSION}-linux-${{node_arch}}.tar.gz "
    '-o "$archive" && '
    'echo "$node_sha  $archive" | sha256sum -c - && '
    'tar -xzf "$archive" -C /usr/local --strip-components=1 --no-same-owner && '
    'rm "$archive"'
)
RUST_VERSION = "1.98.1"
RUST_INSTALL_COMMAND = (
    'case "$(dpkg --print-architecture)" in '
    "amd64) rust_arch=x86_64; rust_sha="
    "24ba1338a2d35c5a3247936546429e163fa674d726102af18bdf624582c57aea ;; "
    "arm64) rust_arch=aarch64; rust_sha="
    "f00ba576645cef658e1deed96fab8f707958e9d58808b16343448b5d1c4f7407 ;; "
    '*) echo "unsupported Rust architecture" >&2; exit 1 ;; esac && '
    'triple="${rust_arch}-unknown-linux-gnu" && '
    f'archive=/tmp/rust-{RUST_VERSION}-"$triple".tar.gz && '
    f"curl -fsSL https://static.rust-lang.org/dist/rust-{RUST_VERSION}-"
    '"$triple".tar.gz -o "$archive" && '
    'echo "$rust_sha  $archive" | sha256sum -c - && '
    "mkdir /tmp/rust && "
    'tar -xzf "$archive" -C /tmp/rust --strip-components=1 --no-same-owner && '
    "/tmp/rust/install.sh --prefix=/usr/local --disable-ldconfig "
    '--components=rustc,cargo,rust-std-"$triple" && '
    'rm -rf "$archive" /tmp/rust'
)
PIP_PACKAGES = (
    "urllib3",
    "brotli",
    "fonttools",
    "markitdown[pptx]",
    "openpyxl",
    "lxml",
    "python-docx",
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
    "uv",
)
NPM_PACKAGES = (
    f"pnpm@{PNPM_VERSION}",
    "pptxgenjs",
    "vite",
    "react",
    "react-dom",
    "react-icons",
    "sharp",
    "docx",
    "pdf-lib",
    f"playwright@{PLAYWRIGHT_VERSION}",
)
SANDBOX_MODULES: tuple[tuple[str, int], ...] = (("containment.py", 4),)
SANDBOX_TEMPLATE_READY_COMMAND = f"""
set -ex
command -v python3 >/dev/null
command -v node >/dev/null
command -v pnpm >/dev/null
command -v ufo >/dev/null
command -v vite >/dev/null
command -v rg >/dev/null
command -v uv >/dev/null
command -v cargo >/dev/null
command -v bc >/dev/null
command -v pdftotext >/dev/null
command -v pdftoppm >/dev/null
command -v soffice >/dev/null
command -v gh >/dev/null
python3 -c 'import brotli, docx, fontTools, reportlab'
test "$(node --version)" = "v{NODE_VERSION}"
test "$(pnpm --version)" = "{PNPM_VERSION}"
test "$(rustc --version | cut -d' ' -f2)" = "{RUST_VERSION}"
test "$(TMPDIR={SANDBOX_TMPDIR} python3 -c 'import tempfile; print(tempfile.gettempdir())')" \\
  = "{SANDBOX_TMPDIR}"
test -f "{SYSTEM_SKILLS_ROOT}/.system-manifest.json"
browser="$(command -v chromium || command -v chromium-browser \\
  || command -v google-chrome || command -v google-chrome-stable || true)"
test -n "$browser"
""".strip()


def template_name(size: str) -> str:
    return f"{E2B_TEMPLATE_NAME}-{size}"


def client_definition() -> dict[str, str]:
    """What the baked `ufo` client is, for the definition digest: its target and a digest of the
    crate sources it is built from.

    The binary's own bytes are deliberately not hashed. A release build is not reproducible byte for
    byte, so two machines building one commit would name two images and every `--check` would read
    as drift. Hashing the source instead keeps the promise the digest is for — the image moves when
    what goes into it moves — and `client/Cargo.lock` is in the set, so a dependency bump moves it
    too."""
    digest = hashlib.sha256()
    code = (
        path
        for directory in CLIENT_SOURCE_DIRS
        for path in (CLIENT_SOURCE_DIR / directory).rglob("*")
    )
    sources = sorted(
        [CLIENT_SOURCE_DIR / name for name in CLIENT_ROOT_FILES]
        + [path for path in code if path.is_file()]
    )
    for path in sources:
        digest.update(str(path.relative_to(CLIENT_SOURCE_DIR)).encode())
        digest.update(path.read_bytes())
    return {
        "name": CLIENT_BINARY_NAME,
        "target": SANDBOX_CLIENT_TARGET,
        "sha256": digest.hexdigest(),
    }


def stage_client_binary() -> Path:
    """Put the compiled `ufo` client where the build context can COPY it, and answer that path.

    Every render names this one path, so a build that forgets to stage fails on a missing COPY
    source rather than baking a stale binary. Which build produced it is `client_binary`'s question:
    a CI job hands over the artifact its client pipeline already built, and a developer's checkout
    uses its own `cargo build`."""
    source = client_binary(target=SANDBOX_CLIENT_TARGET)
    CLIENT_STAGE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, CLIENT_STAGE_PATH)
    CLIENT_STAGE_PATH.chmod(0o755)
    return CLIENT_STAGE_PATH


@cache
def system_skill_bundle() -> SystemSkillBundle:
    paths = sorted(
        path
        for path in {
            *ROOT.glob("core/src/ufo/runtime/skills/**/SKILL.md"),
            *ROOT.glob("extensions/**/skills/**/SKILL.md"),
            *ROOT.glob("packs/**/skills/**/SKILL.md"),
        }
        if "node_modules" not in path.parts
    )
    directories = {path.parent for path in paths}
    roots = sorted(
        directory
        for directory in directories
        if not any(parent in directories for parent in directory.parents)
    )
    skills = tuple(skill for root in roots for skill in discover_skills(root).values())
    return SystemSkillBundle.from_skills(skills)


def stage_system_skills() -> Path:
    SYSTEM_SKILLS_STAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYSTEM_SKILLS_STAGE_PATH.write_bytes(system_skill_bundle().archive)
    return SYSTEM_SKILLS_STAGE_PATH


def build_definition_digest(sizing: Sizing | None) -> str:
    """Content digest of everything apply_layers bakes — base, users, the start/ready commands, the
    apt/pip/npm package sets, the env, the baked client and each module (version + content hash) —
    plus the cpu and memory the build allocates, which no layer carries but which only a republish
    can change. The
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
        "apt_https": APT_HTTPS_COMMAND,
        "gh": GH_INSTALL_COMMAND,
        "node": NODE_INSTALL_COMMAND,
        "rust": RUST_INSTALL_COMMAND,
        "pip": list(PIP_PACKAGES),
        "npm": list(NPM_PACKAGES),
        "env": SANDBOX_ENV,
        "runtime_root": {
            "path": SANDBOX_RUNS_ROOT,
            "uid": SANDBOX_UID,
            "gid": SANDBOX_GID,
            "mode": "0700",
        },
        "client": client_definition(),
        "system_skills": system_skill_bundle().digest,
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


def apply_layers(builder: TemplateBuilder, digest: str) -> TemplateFinal:
    builder.set_user(BUILD_USER)
    builder.run_cmd(
        "apt-get update && apt-get install -y --no-install-recommends "
        + " ".join(APT_PACKAGES)
        + " && rm -rf /var/lib/apt/lists/*"
    )
    builder.run_cmd(APT_HTTPS_COMMAND)
    builder.run_cmd("apt-get remove -y sudo || true; rm -rf /etc/sudoers /etc/sudoers.d")
    builder.run_cmd(GH_INSTALL_COMMAND)
    builder.run_cmd(NODE_INSTALL_COMMAND)
    builder.run_cmd(RUST_INSTALL_COMMAND)
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
    builder.run_cmd(f"install -d -o {SANDBOX_UID} -g {SANDBOX_GID} -m 0700 {SANDBOX_RUNS_ROOT}")
    builder.run_cmd(f"printf '%s' '{digest}' > {BUILD_DIGEST_PATH}")
    builder.set_envs(SANDBOX_ENV)
    builder.copy(SYSTEM_SKILLS_STAGE_PATH.relative_to(ROOT), SYSTEM_SKILLS_ARCHIVE_PATH, mode=0o644)
    builder.run_cmd(
        f"mkdir -p {SYSTEM_SKILLS_ROOT} && "
        f"python3 -m zipfile -e {SYSTEM_SKILLS_ARCHIVE_PATH} {SYSTEM_SKILLS_ROOT} && "
        f"mv {SYSTEM_SKILLS_ROOT}/manifest.json "
        f"{SYSTEM_SKILLS_ROOT}/.system-manifest.json && "
        f"rm {SYSTEM_SKILLS_ARCHIVE_PATH} && "
        f"chmod -R a-w {SYSTEM_SKILLS_ROOT} && "
        f"chmod 1777 {SYSTEM_SKILLS_ROOT}"
    )
    client = f"{SBX_BIN_DIR}/{CLIENT_BINARY_NAME}"
    builder.copy(CLIENT_STAGE_PATH.relative_to(ROOT), client, mode=0o755)
    builder.run_cmd(f"chmod 0755 {client}")
    modules = []
    for name, _ in SANDBOX_MODULES:
        target = f"{SBX_BIN_DIR}/{name}"
        builder.copy((MODULE_SOURCE_DIR / name).relative_to(ROOT), target, mode=0o644)
        modules.append(target)
    builder.run_cmd(f"chmod 0644 {' '.join(modules)}")
    builder.set_user(RUNTIME_USER)
    return builder.set_start_cmd(START_COMMAND, SANDBOX_TEMPLATE_READY_COMMAND)


def e2b_template(size: str) -> TemplateFinal:
    builder = Template(file_context_path=ROOT).from_template(E2B_BASE_TEMPLATE)
    return apply_layers(builder, build_definition_digest(SANDBOX_TIERS[size]))


def pod_dockerfile() -> str:
    builder = Template(file_context_path=ROOT).from_image(DOCKER_BASE_IMAGE)
    return Template.to_dockerfile(apply_layers(builder, build_definition_digest(None)))


def build_docker_image() -> None:
    """Render the Dockerfile from the single definition and build the image the Docker carrier runs.
    Offline — no E2B account, only the local Docker daemon — so a Docker-only deploy builds with no
    E2B key. The context is the repository root, matching the COPY paths apply_layers emits."""
    stage_client_binary()
    stage_system_skills()
    process = subprocess.run(
        ["docker", "build", "-t", DOCKER_IMAGE_TAG, "-f", "-", str(ROOT)],
        input=pod_dockerfile().encode(),
        check=False,
    )
    if process.returncode != 0:
        raise SystemExit(f"docker build failed (rc {process.returncode})")
    print(DOCKER_IMAGE_TAG)


def _booted(name: str) -> Sandbox:
    """Boot the box a gate checks, standing through the service's own bad minutes. Creating a
    sandbox is a call to E2B, and its 500s and timeouts — a reserve script that could not reach
    their redis, an API that did not answer — say nothing about the template under test. A box that
    will not boot after every attempt raises, because a gate that cannot run must not report a
    pass."""
    for attempt in range(1, BUILD_ATTEMPTS + 1):
        try:
            return Sandbox.create(template=name, timeout=READY_VERIFY_TIMEOUT_SECONDS)
        except (httpx.TransportError, SandboxException) as unreachable:
            if attempt == BUILD_ATTEMPTS:
                raise
            print(
                f"{name}: boot attempt {attempt} did not reach E2B ({unreachable}); retrying",
                file=sys.stderr,
            )
            sleep(BUILD_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError("unreachable")


def _reap(sandbox: Sandbox, name: str) -> None:
    """Hand the verification box back. Every sandbox here is created with a `timeout`, so E2B reaps
    it on its own and this only returns it sooner — which is why a failure closing it cannot be
    allowed to replace the verdict the gate just computed. The delete times out at the transport or
    comes back as the SDK's own fault, a `TimeoutException` for a box the service already reaped or
    a `SandboxException` for a 500 on the kill, so both end here. It is said on stderr instead,
    because a box that outlives its check is worth knowing about even though nothing waits on it."""
    try:
        sandbox.kill()
    except (httpx.TransportError, SandboxException) as unreachable:
        print(f"{name}: sandbox left to expire ({unreachable})", file=sys.stderr)


def verify_published_template(name: str) -> None:
    """Publish gate: boot a sandbox from the freshly built template and run the baked-tool readiness
    probe. A template missing a baked tool fails here, so the build cannot report success on a
    drifted image — the same SANDBOX_TEMPLATE_READY_COMMAND the publish path bakes as the ready
    cmd."""
    sandbox = _booted(name)
    try:
        result = sandbox.commands.run(
            SANDBOX_TEMPLATE_READY_COMMAND, timeout=READY_VERIFY_TIMEOUT_SECONDS
        )
    except CommandExitException as error:
        raise RuntimeError(f"published template {name} is missing baked runtime tools") from error
    finally:
        _reap(sandbox, name)
    if result.exit_code != 0:
        raise RuntimeError(f"published template {name} is missing baked runtime tools")


def check_published_template(name: str, expected: str) -> None:
    """Drift gate (never publishes): boot the live template, read its baked build digest, and
    compare to the current source definition. A stale template — built from older inputs, or
    predating the digest — fails here, so CI stays red until someone republishes."""
    sandbox = _booted(name)
    try:
        result = sandbox.commands.run(
            f"cat {BUILD_DIGEST_PATH}", timeout=READY_VERIFY_TIMEOUT_SECONDS
        )
    except CommandExitException as error:
        raise RuntimeError(
            f"published template {name} predates the build digest; republish the sandbox template"
        ) from error
    finally:
        _reap(sandbox, name)
    actual = result.stdout.strip()
    if actual != expected:
        raise RuntimeError(
            f"published template {name} is stale (live {actual or '<none>'} != source {expected}); "
            "republish the sandbox template"
        )


def _built(size: str, sizing: Sizing) -> BuildInfo:
    """Publish one tier's template, standing through the build API's own bad minutes. Requesting a
    build and staging its layers are calls to E2B, and a timeout there says nothing about the
    definition — the same inputs answer on the next attempt, and a whole deploy rides on this step.
    A definition E2B rejects raises on the first attempt like any other fault."""
    for attempt in range(1, BUILD_ATTEMPTS + 1):
        try:
            return Template.build(
                e2b_template(size),
                name=template_name(size),
                cpu_count=sizing.cpu_count,
                memory_mb=sizing.memory_mb,
            )
        except (httpx.TransportError, TimeoutException) as unreachable:
            if attempt == BUILD_ATTEMPTS:
                raise
            print(
                f"{template_name(size)}: build attempt {attempt} did not reach E2B "
                f"({unreachable}); retrying",
                file=sys.stderr,
            )
            sleep(BUILD_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError("unreachable")


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
        for size, sizing in SANDBOX_TIERS.items():
            check_published_template(template_name(size), build_definition_digest(sizing))
            print(f"{template_name(size)} up to date")
        return
    stage_client_binary()
    stage_system_skills()
    references = []
    for size, sizing in SANDBOX_TIERS.items():
        info = _built(size, sizing)
        reference = f"{info.name}:{info.build_id}"
        verify_published_template(reference)
        references.append(f"{size}={reference}")
    print(",".join(references))


if __name__ == "__main__":
    main()
