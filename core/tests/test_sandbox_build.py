"""Drift guard for the single sandbox build definition (`sandbox/build_template.py`).

The ported office/pdf/media skills assume the exact toolchain the image bakes; a dropped or altered
package silently breaks a skill at runtime, so the three package tuples are pinned here against the
expected sets and the rendered Dockerfile is asserted to carry the whole install sequence. The two
render targets (E2B template, Docker image) share `apply_layers`, so this offline check over the
Dockerfile render also covers what the E2B template bakes."""

import sys
from types import SimpleNamespace

from e2b.template.types import BuildInfo

import sandbox.build_template as build_template
from sandbox.build_template import (
    APT_PACKAGES,
    GH_INSTALL_COMMAND,
    NPM_PACKAGES,
    PIP_PACKAGES,
    RUNTIME_USER,
    SANDBOX_ENV,
    SANDBOX_MODULES,
    SANDBOX_SCRIPTS,
    SANDBOX_TEMPLATE_READY_COMMAND,
    SANDBOX_TIERS,
    Sizing,
    build_definition_digest,
    pod_dockerfile,
    template_name,
)
from ufo.sdk.sandbox import SANDBOX_SIZES

EXPECTED_APT = (
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
EXPECTED_PIP = (
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
EXPECTED_NPM = (
    "pptxgenjs",
    "react",
    "react-dom",
    "react-icons",
    "sharp",
    "docx",
    "pdf-lib",
    "playwright",
)


def test_apt_packages_match_the_expected_toolchain() -> None:
    assert APT_PACKAGES == EXPECTED_APT


def test_pip_packages_match_the_expected_toolchain() -> None:
    """markitdown[pptx] carries the pptx extra the office skills need — the bare package would drop
    it silently."""
    assert PIP_PACKAGES == EXPECTED_PIP


def test_npm_packages_match_the_expected_toolchain() -> None:
    assert NPM_PACKAGES == EXPECTED_NPM


def test_sandbox_tiers_scale_cpu_and_memory_together() -> None:
    """large sits on E2B's build ceiling (8 vCPU / 8192 MiB); small is the pre-tier template's exact
    size, so an agent that never picks a size runs the sandbox it always ran."""
    assert SANDBOX_TIERS == {
        "small": Sizing(cpu_count=2, memory_mb=2048),
        "medium": Sizing(cpu_count=4, memory_mb=4096),
        "large": Sizing(cpu_count=8, memory_mb=8192),
    }
    assert tuple(SANDBOX_TIERS) == SANDBOX_SIZES


def test_template_names_carry_the_size() -> None:
    assert [template_name(size) for size in SANDBOX_TIERS] == [
        "ufo-sbx-small",
        "ufo-sbx-medium",
        "ufo-sbx-large",
    ]


def test_no_kubernetes_toolchain_baked() -> None:
    """The k8s bits (kubectl, kubeconfig, KUBECONFIG) are dropped — ufo's sandbox has no
    control-plane egress, so a kubectl reappearing is drift."""
    for name in ("kubectl", "ufo-tool", "kubeconfig"):
        assert name not in SANDBOX_TEMPLATE_READY_COMMAND
    assert "KUBECONFIG" not in SANDBOX_ENV
    dockerfile = pod_dockerfile()
    for token in ("kubectl", "kubeconfig", "ufo-tool", "KUBECONFIG"):
        assert token not in dockerfile


def test_ready_probe_checks_every_baked_entrypoint() -> None:
    """`set -e` is what makes the probe a gate: a multi-line script's exit status is otherwise the
    last command's alone, so every earlier check could fail while the template still reports
    READY."""
    assert SANDBOX_TEMPLATE_READY_COMMAND.startswith("set -ex\n")
    for tool in (
        "python3",
        "node",
        "sbx",
        "sbxfs",
        "rg",
        "pdftotext",
        "pdftoppm",
        "soffice",
        "gh",
    ):
        assert f"command -v {tool}" in SANDBOX_TEMPLATE_READY_COMMAND
    assert "chromium" in SANDBOX_TEMPLATE_READY_COMMAND


def test_scripts_are_the_exec_and_workspace_helpers() -> None:
    assert tuple(name for name, _ in SANDBOX_SCRIPTS) == ("sbx", "sbxfs")


def test_the_containment_guard_is_baked_beside_the_scripts() -> None:
    """`sbxfs` confines a caller-supplied path with the module the serve process imports as
    `ufo.sandbox.containment`, reached as a sibling of the script on `sys.path[0]` — so the image
    has to carry that file beside the script, or the file ops lose their guard at import time."""
    assert tuple(name for name, _ in SANDBOX_MODULES) == ("containment.py",)
    assert "/usr/local/bin/containment.py" in pod_dockerfile()


def test_baked_modules_are_covered_by_the_drift_digest(monkeypatch) -> None:
    """A live template baked from an older guard must fail --check, not keep serving file ops with
    checks the host no longer has."""
    before = build_definition_digest(None)
    monkeypatch.setattr(build_template, "SANDBOX_MODULES", (("containment.py", 99),))
    assert build_definition_digest(None) != before


def test_rendered_dockerfile_carries_the_full_install_sequence() -> None:
    """The Docker image and the E2B template render from one `apply_layers`, so this over the
    Dockerfile covers both: the apt line with --no-install-recommends and the lists cleanup, the
    sudo strip, the pip --no-cache-dir install, the npm global install, the separate playwright
    browser install, and both scripts copied to the bin dir."""
    dockerfile = pod_dockerfile()
    assert "--no-install-recommends" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "apt-get remove -y sudo" in dockerfile
    assert "pip install --no-cache-dir" in dockerfile
    assert "npm install -g --prefix /usr/local --no-fund --no-audit" in dockerfile
    assert "playwright install chromium" in dockerfile
    for package in EXPECTED_APT + EXPECTED_PIP + EXPECTED_NPM:
        assert package in dockerfile
    assert "/usr/local/bin/sbx" in dockerfile
    assert "/usr/local/bin/sbxfs" in dockerfile
    assert "s3fs" not in dockerfile
    assert "sbxcred" not in dockerfile


def test_rendered_dockerfile_runs_as_the_non_root_user() -> None:
    """A sandbox that ran as root after sudo is stripped would be a regression; the render must end
    switched to the non-root runtime user, and the carrier chowns the workspace to that user."""
    dockerfile = pod_dockerfile()
    assert f"USER {RUNTIME_USER}" in dockerfile
    assert dockerfile.rstrip().rfind(f"USER {RUNTIME_USER}") > dockerfile.rfind("USER root")


def test_build_definition_digest_is_stable_and_prefixed() -> None:
    digest = build_definition_digest(None)
    assert digest.startswith("sha256:")
    assert digest == build_definition_digest(None)


def test_gh_installs_from_the_official_cli_repo() -> None:
    """`gh` is the sandboxed GitHub CLI behind the grant-sentinel GH_TOKEN; it installs from
    GitHub's own apt repo (the distro archives lag years behind), and pytest also imports this via
    the drift digest so a dropped layer fails the publish gate."""
    dockerfile = pod_dockerfile()
    assert "cli.github.com/packages" in dockerfile
    assert GH_INSTALL_COMMAND in dockerfile


def test_gh_install_is_covered_by_the_drift_digest(monkeypatch) -> None:
    before = build_definition_digest(None)
    monkeypatch.setattr(build_template, "GH_INSTALL_COMMAND", "changed")
    assert build_definition_digest(None) != before


def test_sizing_is_covered_by_the_drift_digest() -> None:
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
        {"cpu_count": sizing.cpu_count, "memory_mb": sizing.memory_mb}
        for sizing in SANDBOX_TIERS.values()
    ]
    assert capsys.readouterr().out == (
        "small=ufo-sbx-small:build-small,medium=ufo-sbx-medium:build-medium,"
        "large=ufo-sbx-large:build-large\n"
    )


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
