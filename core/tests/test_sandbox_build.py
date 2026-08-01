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
    SANDBOX_SCRIPTS,
    SANDBOX_TEMPLATE_READY_COMMAND,
    build_definition_digest,
    pod_dockerfile,
)

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
    digest = build_definition_digest()
    assert digest.startswith("sha256:")
    assert digest == build_definition_digest()


def test_gh_installs_from_the_official_cli_repo() -> None:
    """`gh` is the sandboxed GitHub CLI behind the grant-sentinel GH_TOKEN; it installs from
    GitHub's own apt repo (the distro archives lag years behind), and pytest also imports this via
    the drift digest so a dropped layer fails the publish gate."""
    dockerfile = pod_dockerfile()
    assert "cli.github.com/packages" in dockerfile
    assert GH_INSTALL_COMMAND in dockerfile


def test_gh_install_is_covered_by_the_drift_digest(monkeypatch) -> None:
    before = build_definition_digest()
    monkeypatch.setattr(build_template, "GH_INSTALL_COMMAND", "changed")
    assert build_definition_digest() != before


def test_publish_returns_the_exact_build_reference(monkeypatch, capsys) -> None:
    created = []
    sandbox = SimpleNamespace(
        commands=SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(exit_code=0)),
        kill=lambda: None,
    )
    monkeypatch.setattr(sys, "argv", ["build-sandbox-template"])
    monkeypatch.setattr(build_template, "e2b_template", lambda: object())
    monkeypatch.setattr(
        build_template.Template,
        "build",
        lambda template, name: BuildInfo(
            template_id="template-1",
            build_id="build-1",
            name=name,
            alias=name,
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
            "template": "ufo-sbx:build-1",
            "timeout": build_template.READY_VERIFY_TIMEOUT_SECONDS,
        }
    ]
    assert capsys.readouterr().out == "ufo-sbx:build-1\n"
