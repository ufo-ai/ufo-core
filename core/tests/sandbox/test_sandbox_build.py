"""Drift guard for the single sandbox build definition (`sandbox/build_template.py`).

The ported office/pdf/media skills assume the exact toolchain the image bakes; a dropped or altered
package silently breaks a skill at runtime, so the three package tuples are pinned here against the
expected sets and the rendered Dockerfile is asserted to carry the whole install sequence. The two
render targets (E2B template, Docker image) share `apply_layers`, so this offline check over the
Dockerfile render also covers what the E2B template bakes."""

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from e2b.exceptions import BuildException, SandboxException, TimeoutException
from e2b.template.types import BuildInfo

import sandbox.build_template as build_template
from sandbox.build_template import (
    APT_HTTPS_COMMAND,
    APT_PACKAGES,
    BUNDLE_TRUST_PROBE,
    CLIENT_ROOT_FILES,
    CLIENT_SOURCE_DIRS,
    CLIENT_STAGE_PATH,
    GH_INSTALL_COMMAND,
    NODE_INSTALL_COMMAND,
    NODE_TRUST_PROBE,
    NPM_PACKAGES,
    PIP_PACKAGES,
    PNPM_VERSION,
    ROOT,
    RUNTIME_USER,
    RUST_INSTALL_COMMAND,
    RUST_VERSION,
    SANDBOX_ENV,
    SANDBOX_TEMPLATE_READY_COMMAND,
    SANDBOX_TIERS,
    SANDBOX_TMPDIR,
    SYSTEM_SKILLS_STAGE_PATH,
    Sizing,
    build_definition_digest,
    client_definition,
    pod_dockerfile,
    stage_system_skills,
    system_skill_bundle,
    template_name,
)
from ufo.harness.sandbox.session import SYSTEM_SKILLS_BAKED_ENV
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY
from ufo.sdk.sandbox import (
    DISTRUSTED_ROOT_CAS,
    PLAYWRIGHT_VERSION,
    SANDBOX_GID,
    SANDBOX_RUNS_ROOT,
    SANDBOX_SIZES,
    SANDBOX_UID,
    SYSTEM_CA_BUNDLE,
    SYSTEM_SKILLS_ROOT,
)


@pytest.fixture(autouse=True)
def staged_client() -> Iterator[None]:
    """Every render COPYs the staged client from one path, so these offline renders need a file at
    that path — never a real build, whose bytes no render reads and whose absence is no drift."""
    created_client = not CLIENT_STAGE_PATH.exists()
    created_skills = not SYSTEM_SKILLS_STAGE_PATH.exists()
    if created_client:
        CLIENT_STAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CLIENT_STAGE_PATH.write_bytes(b"")
    if created_skills:
        stage_system_skills()
    try:
        yield
    finally:
        if created_client:
            CLIENT_STAGE_PATH.unlink()
        if created_skills:
            SYSTEM_SKILLS_STAGE_PATH.unlink()


EXPECTED_APT = (
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
EXPECTED_PIP = (
    "urllib3",
    "brotli==1.2.1",
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
EXPECTED_NPM = (
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


def _check_apt_packages_match_the_expected_toolchain() -> None:
    assert APT_PACKAGES == EXPECTED_APT


def _check_runtime_apt_sources_use_https_proxy_tunnels() -> None:
    dockerfile = pod_dockerfile()
    assert APT_HTTPS_COMMAND in dockerfile
    for host in (
        "archive.ubuntu.com",
        "security.ubuntu.com",
        "ports.ubuntu.com",
        "deb.debian.org",
        "security.debian.org",
        "cdn-fastly.deb.debian.org",
    ):
        assert f"http://{host}" in APT_HTTPS_COMMAND
        assert f"https://{host}" in APT_HTTPS_COMMAND


def _check_pip_packages_match_the_expected_toolchain() -> None:
    """markitdown[pptx] carries the pptx extra the office skills need — the bare package would drop
    it silently."""
    assert PIP_PACKAGES == EXPECTED_PIP


def _check_ready_probe_imports_the_document_python_libraries() -> None:
    assert (
        "python3 -c 'import brotli, docx, fontTools, reportlab'" in SANDBOX_TEMPLATE_READY_COMMAND
    )


def _check_npm_packages_match_the_expected_toolchain() -> None:
    assert NPM_PACKAGES == EXPECTED_NPM


def _check_the_sandbox_installs_the_pinned_rust_toolchain() -> None:
    """The client crate is Rust, so a sandbox without cargo cannot build or test it."""
    dockerfile = pod_dockerfile()
    assert RUST_INSTALL_COMMAND in dockerfile
    assert f"https://static.rust-lang.org/dist/$name-{RUST_VERSION}-$triple.tar.xz" in dockerfile
    assert (
        "test \"$(rustc --version | cut -d' ' -f2)\" = "
        f'"{RUST_VERSION}"' in SANDBOX_TEMPLATE_READY_COMMAND
    )


def _check_sandbox_tiers_scale_cpu_and_memory_together() -> None:
    """large sits on E2B's build ceiling (8 vCPU / 8192 MiB); small is the pre-tier template's exact
    size, so an agent that never picks a size runs the sandbox it always ran."""
    assert SANDBOX_TIERS == {
        "small": Sizing(cpu_count=2, memory_mb=2048),
        "medium": Sizing(cpu_count=4, memory_mb=4096),
        "large": Sizing(cpu_count=8, memory_mb=8192),
    }
    assert tuple(SANDBOX_TIERS) == SANDBOX_SIZES


def _check_template_names_carry_the_size() -> None:
    assert [template_name(size) for size in SANDBOX_TIERS] == [
        "ufo-sbx-small",
        "ufo-sbx-medium",
        "ufo-sbx-large",
    ]


def _check_no_kubernetes_toolchain_baked() -> None:
    """The k8s bits (kubectl, kubeconfig, KUBECONFIG) are dropped — ufo's sandbox has no
    control-plane egress, so a kubectl reappearing is drift."""
    for name in ("kubectl", "ufo-tool", "kubeconfig"):
        assert name not in SANDBOX_TEMPLATE_READY_COMMAND
    assert "KUBECONFIG" not in SANDBOX_ENV
    dockerfile = pod_dockerfile()
    for token in ("kubectl", "kubeconfig", "ufo-tool", "KUBECONFIG"):
        assert token not in dockerfile


def _check_ready_probe_checks_every_baked_entrypoint() -> None:
    assert SANDBOX_TEMPLATE_READY_COMMAND.startswith("set -ex\n")
    for tool in (
        "python3",
        "node",
        "ufo",
        "rg",
        "uv",
        "cargo",
        "pdftotext",
        "pdftoppm",
        "soffice",
        "gh",
    ):
        assert f"command -v {tool}" in SANDBOX_TEMPLATE_READY_COMMAND
    assert "chromium" in SANDBOX_TEMPLATE_READY_COMMAND
    assert f'python3 -c "{BUNDLE_TRUST_PROBE}" {SYSTEM_CA_BUNDLE}' in SANDBOX_TEMPLATE_READY_COMMAND
    assert f'node -e "{NODE_TRUST_PROBE}"' in SANDBOX_TEMPLATE_READY_COMMAND


def _check_scratch_resolves_onto_the_disk_not_the_memory_backed_tmpfs() -> None:
    """The guest mounts `/tmp` as a tmpfs sized to half its memory, so scratch there is spent
    RAM."""
    assert SANDBOX_ENV["TMPDIR"] == SANDBOX_TMPDIR
    assert not SANDBOX_TMPDIR.startswith("/tmp")
    assert f"TMPDIR={SANDBOX_TMPDIR} python3 -c" in SANDBOX_TEMPLATE_READY_COMMAND


def _check_the_scratch_dir_is_one_no_container_has_to_be_built_with() -> None:
    assert SANDBOX_TMPDIR == "/var/tmp"
    assert f"mkdir -p {SANDBOX_TMPDIR}" not in pod_dockerfile()


def _check_the_baked_in_sandbox_cli_is_the_compiled_client() -> None:
    """One binary where the image used to bake two Python scripts: `ufo fs` serves the file ops
    and `ufo llm` the egress CLI."""
    dockerfile = pod_dockerfile()
    assert str(CLIENT_STAGE_PATH.relative_to(ROOT)) in dockerfile
    assert "/usr/local/bin/ufo" in dockerfile
    for gone in ("/usr/local/bin/sbx", "/usr/local/bin/sbxfs"):
        assert gone not in dockerfile


def _check_system_skills_are_baked_into_each_sandbox_image() -> None:
    dockerfile = pod_dockerfile()
    bundle = system_skill_bundle()
    manifest = json.loads(bundle.manifest)
    assert SANDBOX_ENV[SYSTEM_SKILLS_BAKED_ENV] == "1"
    assert f"{SYSTEM_SKILLS_BAKED_ENV}=1" in dockerfile
    assert str(SYSTEM_SKILLS_STAGE_PATH.relative_to(ROOT)) in dockerfile
    assert SYSTEM_SKILLS_ROOT in dockerfile
    assert f"{SYSTEM_SKILLS_ROOT}/.system-manifest.json" in dockerfile
    assert f"chmod -R a-w {SYSTEM_SKILLS_ROOT}" in dockerfile
    assert f"chmod 1777 {SYSTEM_SKILLS_ROOT}" in dockerfile
    assert f"chown {RUNTIME_USER}:{RUNTIME_USER} {SYSTEM_SKILLS_ROOT}" not in dockerfile
    assert "sandbox" in manifest["skills"]
    assert (
        manifest["skills"]["sandbox"]["digest"]
        == CORE_SKILL_REGISTRY.named("sandbox").content_digest()
    )
    assert "website-building" in manifest["skills"]
    assert "core/src/ufo/runtime/skills/sandbox" not in manifest["skills"]
    assert bundle.archive == SYSTEM_SKILLS_STAGE_PATH.read_bytes()
    assert f'test -f "{SYSTEM_SKILLS_ROOT}/.system-manifest.json"' in SANDBOX_TEMPLATE_READY_COMMAND


def test_system_skill_bundle_excludes_dependency_skills(monkeypatch, tmp_path) -> None:
    skill = tmp_path / "extensions" / "sample" / "skills" / "expected"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: expected\ndescription: Load when the sample is required.\n---\n\nUse it.\n"
    )
    dependency = (
        tmp_path
        / "extensions"
        / "web"
        / "frontend"
        / "node_modules"
        / "package"
        / "skills"
        / "dependency"
    )
    dependency.mkdir(parents=True)
    (dependency / "SKILL.md").write_text(
        "---\nname: package/dependency\ndescription: Load when dependency text is required.\n---\n"
    )
    monkeypatch.setattr(build_template, "ROOT", tmp_path)
    system_skill_bundle.cache_clear()
    try:
        manifest = json.loads(system_skill_bundle().manifest)
    finally:
        system_skill_bundle.cache_clear()
    assert tuple(manifest["skills"]) == ("expected",)


def test_the_baked_client_moves_the_drift_digest_with_its_source(monkeypatch, tmp_path) -> None:
    """A live template baked from older client source must fail --check rather than keep serving
    file ops from a binary the host no longer ships."""
    crate = tmp_path / "client"
    for directory in CLIENT_SOURCE_DIRS:
        (crate / directory).mkdir(parents=True)
    for name in CLIENT_ROOT_FILES:
        (crate / name).write_text('version = "0.1.30"\n')
    (crate / "src" / "main.rs").write_text("fn main() {}\n")
    (crate / "licenses" / "github-cli.txt").write_text("MIT\n")
    (crate / "scripts" / "build-gh.sh").write_text("go build\n")
    monkeypatch.setattr(build_template, "CLIENT_SOURCE_DIR", crate)
    for path in (
        crate / "src" / "main.rs",
        crate / "build.rs",
        crate / "licenses" / "github-cli.txt",
        crate / "scripts" / "build-gh.sh",
    ):
        before = build_definition_digest(None)
        path.write_text(path.read_text() + "changed\n")
        assert build_definition_digest(None) != before


def test_the_baked_client_target_is_covered_by_the_drift_digest(monkeypatch) -> None:
    """The target belongs to the definition, not to the builder's own architecture: an image baked
    for another triple carries a binary this fleet's sandboxes cannot run."""
    before = build_definition_digest(None)
    monkeypatch.setattr(build_template, "SANDBOX_CLIENT_TARGET", "aarch64-unknown-linux-musl")
    assert build_definition_digest(None) != before


def _check_the_client_definition_names_the_target_it_is_built_for() -> None:
    assert client_definition()["target"] == build_template.SANDBOX_CLIENT_TARGET


def _check_rendered_dockerfile_carries_the_full_install_sequence() -> None:
    dockerfile = pod_dockerfile()
    assert "--no-install-recommends" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "apt-get remove -y sudo" in dockerfile
    assert NODE_INSTALL_COMMAND in dockerfile
    assert "pip install --no-cache-dir" in dockerfile
    assert "npm install -g --prefix /usr/local --no-fund --no-audit" in dockerfile
    assert "playwright install chromium" in dockerfile
    for package in EXPECTED_APT + EXPECTED_PIP + EXPECTED_NPM:
        assert package in dockerfile
    assert "/usr/local/bin/ufo" in dockerfile
    assert "s3fs" not in dockerfile
    assert "sbxcred" not in dockerfile


def _check_rendered_dockerfile_runs_as_the_non_root_user() -> None:
    """A sandbox that ran as root after sudo is stripped would be a regression; the render must end
    switched to the non-root runtime user, and the carrier chowns the workspace to that user."""
    dockerfile = pod_dockerfile()
    assert f"USER {RUNTIME_USER}" in dockerfile
    assert dockerfile.rstrip().rfind(f"USER {RUNTIME_USER}") > dockerfile.rfind("USER root")


def _check_runtime_root_is_writable_by_the_sandbox_user() -> None:
    dockerfile = pod_dockerfile()
    assert f"install -d -o {SANDBOX_UID} -g {SANDBOX_GID} -m 0700 {SANDBOX_RUNS_ROOT}" in dockerfile


def _check_build_definition_digest_is_stable_and_prefixed() -> None:
    digest = build_definition_digest(None)
    assert digest.startswith("sha256:")
    assert digest == build_definition_digest(None)


def _check_gh_installs_from_the_official_cli_repo() -> None:
    dockerfile = pod_dockerfile()
    assert "cli.github.com/packages" in dockerfile
    assert GH_INSTALL_COMMAND in dockerfile


def test_gh_install_is_covered_by_the_drift_digest(monkeypatch) -> None:
    before = build_definition_digest(None)
    monkeypatch.setattr(build_template, "GH_INSTALL_COMMAND", "changed")
    assert build_definition_digest(None) != before


def test_chart_defaults_are_installed_for_both_carriers() -> None:
    dockerfile = pod_dockerfile()
    assert (
        "COPY extensions/repl/ufo_ext_repl/assets/matplotlibrc "
        f"{build_template.MATPLOTLIB_CONFIG_PATH}" in dockerfile
    )
    assert f"MATPLOTLIBRC={build_template.MATPLOTLIB_CONFIG_PATH}" in dockerfile


def test_chart_defaults_change_the_build_digest(monkeypatch, tmp_path) -> None:
    before = build_definition_digest(None)
    config = tmp_path / "matplotlibrc"
    config.write_text(build_template.MATPLOTLIB_CONFIG.read_text() + "\nsavefig.pad_inches: 0.2\n")
    monkeypatch.setattr(build_template, "MATPLOTLIB_CONFIG", config)
    assert build_definition_digest(None) != before


def _check_sizing_is_covered_by_the_drift_digest() -> None:
    """Sizing is fixed at build time and carried by no layer, so without it in the digest a live
    template built at another tier's size would pass --check and keep serving turns mis-sized."""
    digests = {build_definition_digest(sizing) for sizing in SANDBOX_TIERS.values()}
    assert len(digests) == len(SANDBOX_TIERS)
    assert build_definition_digest(None) not in digests


def test_publish_builds_every_tier_and_prints_the_size_map(monkeypatch, capsys) -> None:
    created = []
    built = []
    sandbox = SimpleNamespace(
        commands=SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(exit_code=0)),
        kill=lambda: None,
    )
    monkeypatch.setattr(sys, "argv", ["build-sandbox-template"])
    monkeypatch.setattr(build_template, "stage_client_binary", lambda: CLIENT_STAGE_PATH)
    monkeypatch.setattr(build_template, "e2b_template", lambda size: object())
    monkeypatch.setattr(
        build_template.Template,
        "build",
        lambda template, name, **kwargs: (
            built.append(kwargs)
            or BuildInfo(
                template_id="template-1",
                build_id=f"build-{name.removeprefix('ufo-sbx-')}",
                name=name,
                alias=name,
            )
        ),
    )
    monkeypatch.setattr(
        build_template,
        "Sandbox",
        SimpleNamespace(create=lambda **kwargs: created.append(kwargs) or sandbox),
    )

    build_template.main()

    assert created == [
        {
            "template": f"ufo-sbx-{size}:build-{size}",
            "timeout": build_template.READY_VERIFY_TIMEOUT_SECONDS,
        }
        for size in SANDBOX_TIERS
    ]

    assert built == [
        {"cpu_count": sizing.cpu_count, "memory_mb": sizing.memory_mb, "skip_cache": True}
        for sizing in SANDBOX_TIERS.values()
    ]
    assert capsys.readouterr().out == (
        "small=ufo-sbx-small:build-small,medium=ufo-sbx-medium:build-medium,"
        "large=ufo-sbx-large:build-large\n"
    )


def _boxed(sandbox: SimpleNamespace, **_: object) -> SimpleNamespace:
    return sandbox


def test_a_verification_box_that_cannot_be_reaped_leaves_the_gate_s_verdict(
    monkeypatch, capsys
) -> None:
    """Handing the box back is the E2B API's to answer, and it fails three ways: the transport
    times out, the service already reaped the box, or the kill itself answers 500."""
    ready = SimpleNamespace(exit_code=0)
    faults = [
        httpx.ReadTimeout("The read operation timed out"),
        TimeoutException("The sandbox was not found"),
        SandboxException("500: Error killing sandbox: sandbox operation failed"),
    ]
    for fault in faults:
        unreachable = SimpleNamespace(
            commands=SimpleNamespace(run=lambda *args, **kwargs: ready),
            kill=lambda held=fault: (_ for _ in ()).throw(held),
        )
        monkeypatch.setattr(
            build_template,
            "Sandbox",
            SimpleNamespace(create=partial(_boxed, unreachable)),
        )

        build_template.verify_published_template("ufo-sbx-small:build-1")

        assert "ufo-sbx-small:build-1: sandbox left to expire" in capsys.readouterr().err

    ready.exit_code = 1
    with pytest.raises(RuntimeError, match="missing baked runtime tools"):
        build_template.verify_published_template("ufo-sbx-small:build-1")


def test_a_build_that_cannot_reach_e2b_is_retried_and_a_rejected_one_is_not(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(build_template, "sleep", lambda _: None)
    attempts = []

    def flaky(template, name, **kwargs):
        attempts.append(name)
        if len(attempts) < build_template.BUILD_ATTEMPTS:
            raise httpx.ReadTimeout("The read operation timed out")
        return BuildInfo(template_id="template-1", build_id="build-1", name=name, alias=name)

    monkeypatch.setattr(build_template, "e2b_template", lambda size: object())
    monkeypatch.setattr(build_template.Template, "build", flaky)

    info = build_template._built("small", SANDBOX_TIERS["small"])

    assert len(attempts) == build_template.BUILD_ATTEMPTS
    assert info.build_id == "build-1"
    assert "did not reach E2B" in capsys.readouterr().err

    def rejected(template, name, **kwargs):
        raise BuildException("template definition rejected")

    monkeypatch.setattr(build_template.Template, "build", rejected)
    with pytest.raises(BuildException):
        build_template._built("small", SANDBOX_TIERS["small"])


def test_a_verification_box_that_will_not_boot_is_retried_then_raises(monkeypatch, capsys) -> None:
    """Booting the box is a call to E2B, and its 500s say nothing about the template under test —
    a reserve script that could not reach their redis is not a drifted image."""
    monkeypatch.setattr(build_template, "sleep", lambda _: None)
    boots = []
    ready = SimpleNamespace(
        commands=SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(exit_code=0)),
        kill=lambda: None,
    )

    def flaky(**kwargs):
        boots.append(kwargs)
        if len(boots) < build_template.BUILD_ATTEMPTS:
            raise SandboxException("500: Failed to create sandbox: redis: connection pool timeout")
        return ready

    monkeypatch.setattr(build_template, "Sandbox", SimpleNamespace(create=flaky))
    build_template.verify_published_template("ufo-sbx-small:build-1")
    assert len(boots) == build_template.BUILD_ATTEMPTS
    assert "did not reach E2B" in capsys.readouterr().err

    def never(**kwargs):
        raise SandboxException("500: Failed to create sandbox: redis: connection pool timeout")

    monkeypatch.setattr(build_template, "Sandbox", SimpleNamespace(create=never))
    with pytest.raises(SandboxException):
        build_template.verify_published_template("ufo-sbx-small:build-1")


def test_check_reads_every_tier_against_its_own_digest(monkeypatch) -> None:
    checked = []
    monkeypatch.setattr(sys, "argv", ["build-sandbox-template", "--check"])
    monkeypatch.setattr(
        build_template,
        "check_published_template",
        lambda name, expected: checked.append((name, expected)),
    )

    build_template.main()

    assert checked == [
        (template_name(size), build_definition_digest(sizing))
        for size, sizing in SANDBOX_TIERS.items()
    ]


def _root_pem(common_name: str) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


@pytest.mark.parametrize("distrusted", DISTRUSTED_ROOT_CAS)
def test_the_bundle_probe_fails_only_a_bundle_holding_a_distrusted_root(
    tmp_path, distrusted: str
) -> None:
    clean = tmp_path / "clean.pem"
    clean.write_bytes(_root_pem("ufo test root"))
    held = tmp_path / "held.pem"
    held.write_bytes(_root_pem("ufo test root") + _root_pem(distrusted))
    probe = [sys.executable, "-c", BUNDLE_TRUST_PROBE]
    assert subprocess.run([*probe, str(clean)], check=False).returncode == 0
    assert subprocess.run([*probe, str(held)], check=False).returncode == 1


def test_sandbox_build_contract() -> None:
    checks = tuple(value for name, value in globals().items() if name.startswith("_check_"))
    assert len(checks) == 21
    for check in checks:
        check()
