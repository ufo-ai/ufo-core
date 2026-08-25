"""Drift guard for the single sandbox build definition (`sandbox/build_template.py`).

The ported office/pdf/media skills assume the exact toolchain the image bakes; a dropped or altered
package silently breaks a skill at runtime, so the three package tuples are pinned here against the
expected sets and the rendered Dockerfile is asserted to carry the whole install sequence. The two
render targets (E2B template, Docker image) share `apply_layers`, so this offline check over the
Dockerfile render also covers what the E2B template bakes."""

import json
import re
import sys
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from e2b.template.types import BuildInfo
from ufo_ext_daytona import snapshot_map

import sandbox.build_template as build_template
from sandbox.build_template import (
    APT_PACKAGES,
    CLIENT_ROOT_FILES,
    CLIENT_SOURCE_DIRS,
    CLIENT_STAGE_PATH,
    DAYTONA_TIERS,
    GH_INSTALL_COMMAND,
    NPM_PACKAGES,
    PIP_PACKAGES,
    ROOT,
    RUNTIME_USER,
    SANDBOX_ENV,
    SANDBOX_MODULES,
    SANDBOX_TEMPLATE_READY_COMMAND,
    SANDBOX_TIERS,
    SYSTEM_SKILLS_STAGE_PATH,
    DaytonaSizing,
    Sizing,
    build_definition_digest,
    client_definition,
    daytona_definition_digest,
    daytona_image,
    daytona_refs,
    daytona_snapshot_name,
    pod_dockerfile,
    stage_system_skills,
    system_skill_bundle,
    template_name,
)
from ufo.sdk.sandbox import PLAYWRIGHT_VERSION, SANDBOX_SIZES, SYSTEM_SKILLS_ROOT


@pytest.fixture(autouse=True)
def staged_client() -> Iterator[None]:
    """Every render COPYs the staged client from one path, and Daytona's builder refuses a COPY
    source that is not there, so these offline renders need a file at that path — never a real
    build, whose bytes no render reads and whose absence is no drift."""
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
    "vite",
    "react",
    "react-dom",
    "react-icons",
    "sharp",
    "docx",
    "pdf-lib",
    f"playwright@{PLAYWRIGHT_VERSION}",
)


def test_apt_packages_match_the_expected_toolchain() -> None:
    assert APT_PACKAGES == EXPECTED_APT


def test_pip_packages_match_the_expected_toolchain() -> None:
    """markitdown[pptx] carries the pptx extra the office skills need — the bare package would drop
    it silently."""
    assert PIP_PACKAGES == EXPECTED_PIP


def test_npm_packages_match_the_expected_toolchain() -> None:
    assert NPM_PACKAGES == EXPECTED_NPM


def test_the_playwright_pin_matches_the_edge_suite() -> None:
    edge = json.loads((ROOT / "infra/modules/edge/package.json").read_text())
    assert edge["devDependencies"]["playwright"] == PLAYWRIGHT_VERSION


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
        "ufo",
        "rg",
        "pdftotext",
        "pdftoppm",
        "soffice",
        "gh",
    ):
        assert f"command -v {tool}" in SANDBOX_TEMPLATE_READY_COMMAND
    assert "chromium" in SANDBOX_TEMPLATE_READY_COMMAND


def test_the_baked_in_sandbox_cli_is_the_compiled_client() -> None:
    """One binary where the image used to bake two Python scripts: `ufo fs` serves the file ops and
    `ufo llm` the egress CLI. It is COPY'd from a staged path inside the repository, because the
    build context is the repository root and `.dockerignore` keeps the crate's own `target/` out."""
    dockerfile = pod_dockerfile()
    assert str(CLIENT_STAGE_PATH.relative_to(ROOT)) in dockerfile
    assert "/usr/local/bin/ufo" in dockerfile
    for gone in ("/usr/local/bin/sbx", "/usr/local/bin/sbxfs"):
        assert gone not in dockerfile


def test_the_containment_guard_is_baked_beside_the_client() -> None:
    """An in-sandbox python program imports the guard as the sibling module `containment`, and the
    bin dir is `sys.path[0]` for a program run from there — so the image carries the same file the
    serve process imports as `ufo.sandbox.containment`, never a second copy of the checks."""
    assert tuple(name for name, _ in SANDBOX_MODULES) == ("containment.py",)
    assert "/usr/local/bin/containment.py" in pod_dockerfile()


def test_system_skills_are_baked_into_each_sandbox_image() -> None:
    dockerfile = pod_dockerfile()
    assert str(SYSTEM_SKILLS_STAGE_PATH.relative_to(ROOT)) in dockerfile
    assert SYSTEM_SKILLS_ROOT in dockerfile
    assert system_skill_bundle().digest.removeprefix("sha256:") in dockerfile
    assert system_skill_bundle().archive == SYSTEM_SKILLS_STAGE_PATH.read_bytes()
    assert f'test -f "{SYSTEM_SKILLS_ROOT}/current"' in SANDBOX_TEMPLATE_READY_COMMAND


def test_the_baked_client_moves_the_drift_digest_with_its_source(monkeypatch, tmp_path) -> None:
    """A live template baked from older client source must fail --check rather than keep serving
    file ops from a binary the host no longer ships. The binary's own bytes are not hashed — a
    release build is not reproducible — so the crate's sources stand in for it."""
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


def test_the_client_definition_names_the_target_it_is_built_for() -> None:
    assert client_definition()["target"] == build_template.SANDBOX_CLIENT_TARGET


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
    browser install, and the client binary copied to the bin dir."""
    dockerfile = pod_dockerfile()
    assert "--no-install-recommends" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "apt-get remove -y sudo" in dockerfile
    assert "pip install --no-cache-dir" in dockerfile
    assert "npm install -g --prefix /usr/local --no-fund --no-audit" in dockerfile
    assert "playwright install chromium" in dockerfile
    for package in EXPECTED_APT + EXPECTED_PIP + EXPECTED_NPM:
        assert package in dockerfile
    assert "/usr/local/bin/ufo" in dockerfile
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


def test_daytona_snapshot_name_carries_size_and_digest() -> None:
    name = daytona_snapshot_name("small")
    digest = daytona_definition_digest("small")
    assert name == f"ufo-sbx-small-{digest[:12]}"
    assert re.fullmatch(r"ufo-sbx-small-[0-9a-f]{12}", name)


def test_daytona_digest_moves_with_the_docker_definition(monkeypatch) -> None:
    before = daytona_definition_digest("small")
    monkeypatch.setattr(build_template, "GH_INSTALL_COMMAND", "changed")
    assert daytona_definition_digest("small") != before


def test_daytona_digest_moves_with_tier_resources_and_the_e2b_digest_does_not(
    monkeypatch,
) -> None:
    daytona_before = daytona_definition_digest("small")
    e2b_before = build_definition_digest(SANDBOX_TIERS["small"])
    monkeypatch.setitem(
        build_template.DAYTONA_TIERS, "small", DaytonaSizing(cpu=2, memory_gb=2, disk_gb=20)
    )
    assert daytona_definition_digest("small") != daytona_before
    assert build_definition_digest(SANDBOX_TIERS["small"]) == e2b_before


def test_daytona_refs_round_trip_the_carriers_snapshot_map() -> None:
    assert snapshot_map(daytona_refs()) == {
        size: daytona_snapshot_name(size) for size in SANDBOX_SIZES
    }


def test_daytona_tiers_cover_exactly_the_declared_sizes() -> None:
    assert tuple(DAYTONA_TIERS) == SANDBOX_SIZES


def test_daytona_image_bakes_the_same_layers_as_the_dockerfile() -> None:
    image = daytona_image("small")
    rendered = image.dockerfile()
    for command in (
        "apt-get update",
        GH_INSTALL_COMMAND.split(" && ")[0],
        "python3 -m pip install",
        "npm install -g",
        f"mkdir -p {build_template.WORKSPACE_DIR}",
    ):
        assert command in rendered
    assert f"USER {RUNTIME_USER}" in rendered


def test_daytona_publish_boots_nothing_for_a_standing_active_snapshot(monkeypatch) -> None:
    """The verify sandbox spends the organization's one memory budget, which the live fleet is
    also spending, so a republish with nothing changed must create nothing and boot nothing — an
    active digest-named snapshot is current by construction."""
    from daytona_api_client import SnapshotState

    acts: list[tuple[str, str]] = []
    states = {
        build_template.daytona_snapshot_name("small"): SnapshotState.ACTIVE,
        build_template.daytona_snapshot_name("medium"): SnapshotState.INACTIVE,
    }

    class Snapshots:
        def get(self, name):
            if name in states:
                return SimpleNamespace(state=states[name])
            raise build_template.DaytonaNotFoundError(f"no snapshot {name}")

        def create(self, params, on_logs):
            acts.append(("create", params.name))

        def activate(self, snapshot):
            acts.append(("activate", "medium"))

    monkeypatch.setattr(build_template, "stage_client_binary", lambda: CLIENT_STAGE_PATH)
    monkeypatch.setattr(build_template, "Daytona", lambda: SimpleNamespace(snapshot=Snapshots()))
    monkeypatch.setattr(
        build_template,
        "verify_daytona_snapshot",
        lambda daytona, name: acts.append(("verify", name)),
    )

    build_template.build_daytona_snapshots()

    large = build_template.daytona_snapshot_name("large")
    assert acts == [
        ("activate", "medium"),
        ("verify", build_template.daytona_snapshot_name("medium")),
        ("create", large),
        ("verify", large),
    ]
