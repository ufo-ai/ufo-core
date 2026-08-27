"""Deterministic artifact graders inspect delivered bytes, not filenames or claims."""

import asyncio
import subprocess
import sys
from dataclasses import replace
from gzip import compress
from hashlib import sha256
from io import BytesIO
from json import dumps, loads
from pathlib import Path
from shutil import copy as copy_file
from tarfile import TarInfo
from tarfile import open as open_tar
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import sqlalchemy as sa
from ufo_ext_eval_env.manifest import (
    APP_FIXTURE_PREFIX,
    DRIVE_PROVIDER,
    GITHUB_PROVIDER,
    eval_env_email,
    eval_env_event,
)
from ufo_ext_eval_env.manifest import (
    NAME as EVAL_ENV_NAME,
)
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_REQUEST_CONTRACT_KEY,
    ApplicationAuditContract,
    ApplicationAuditRegion,
    ApplicationAuditReport,
    application_region_relation,
)
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_DELEGATION_TOOL,
    APPLICATION_BUILDER_DEPLOY_TOOL,
    APPLICATION_BUILDER_DESIGN_TOOL,
    APPLICATION_BUILDER_QA_TOOL,
    APPLICATION_BUILDER_WRITE_TOOL,
    ApplicationBuilderResult,
)

from evals.ablate import EGRESS_BINARY, Ablation, ArmSpec, load_experiment
from evals.harness.artifact_checks import (
    JPEG_MAGIC,
    JPEG_TRAILER,
    MAX_ARCHIVE_ENTRIES,
    MAX_EXPANDED_BYTES,
    MAX_PART_BYTES,
    OOXML_PARTS,
    PNG_MAGIC,
    PNG_TRAILER,
    office_document,
    valid_image,
    valid_pdf,
    valid_png,
)
from evals.harness.capability import (
    CapabilityOutput,
    ProbeCommandResult,
    SharedArtifact,
    ToolInvocation,
)
from evals.harness.harness import EvalCaseResult, EvalReport
from evals.harness.scorers import (
    board_presentation_scorer,
    forecast_workbook_scorer,
    office_document_scorer,
    pdf_document_scorer,
    png_image_scorer,
    rendered_pages_scorer,
    shared_artifact_scorer,
    site_archive_scorer,
)
from evals.suites.ufo_app_bench import (
    ACTION_CASES,
    ACTION_CONTRACTS,
    APP_DATA_CONTENT,
    APP_DATA_DIGEST,
    APP_PREVIEW,
    APP_UNIVERSE_EMAILS,
    APP_UNIVERSE_EVENTS,
    APP_UNIVERSE_TOOLS,
    APP_WORKSPACE_FILES,
    AUDIT_CONTENT,
    AUDIT_DIGEST,
    CONNECTED_APPS,
    CONNECTED_CASES,
    CONTROL_CASES,
    COPY_CAPTURE_CONTENT,
    COPY_CAPTURE_DIGEST,
    COPY_CASES,
    DESKTOP_HEIGHT,
    DESKTOP_WIDTH,
    HOUSE_CRITERIA,
    INTERACTION_MIN_CONTROLS,
    INTERACTION_MIN_SUCCESSES,
    MAX_BROWSER_QA_CALLS,
    MAX_PRODUCT_QA_CALLS,
    MEASURED_VIEWS,
    MEMBER_QUERIES,
    NARROW_HEIGHT,
    NARROW_WIDTH,
    PROBE_PORT,
    SCHEMES,
    SETUP_CASES,
    SETUP_CONTRACTS,
    TASTE_CRITERIA,
    AppBenchWorkspaceProbe,
    _above_fold_scorer,
    _action_scorer,
    _AppActionProbe,
    _AppBenchProbe,
    _AppCopyProbe,
    _application_audit_contract,
    _application_builder_scorer,
    _AppSetupProbe,
    _browser_probe_slot,
    _ConnectedApp,
    _ConnectedAppSeed,
    _copy_scorer,
    _delivery_scorer,
    _design_region_scorer,
    _interaction_screen,
    _json_contains,
    _measured_screen,
    _missing_setup_terms,
    _qa_efficiency_scorer,
    _requirement_scorer,
    _rewrite_source_call,
    _score_app_report,
    _setup_scorer,
    _skill_scorer,
    _source_copy,
)
from evals.suites.ufo_app_bench import CASES as BENCH_CASES
from evals.suites.ufo_app_bench import (
    WORKFLOW_WAIT_SECONDS as UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS,
)
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.sdk.context import ScopedStore
from ufo.workspace import ws

REVENUE = (120, 135, 142, 160)
AUDIT_DESIGN_REGIONS = (
    {
        "name": "queue",
        "left": 0.0,
        "top": 0.0,
        "width": 0.6,
        "height": 1.0,
        "aboveFold": True,
    },
    {
        "name": "detail",
        "left": 0.6,
        "top": 0.0,
        "width": 0.4,
        "height": 1.0,
        "aboveFold": True,
    },
)
TESTING_APP_SOURCES = {
    "pre-meeting-briefs": ("meeting-briefs",),
    "meeting-tasks": ("meeting-scribe-home",),
    "issue-owner": ("intake-watcher-homepage", "issue-fixer-home"),
    "issue-planner": ("issue-fixer-home",),
    "code-review-queue": ("code-review-home", "ufo-review-homepage"),
    "engineering-metrics": ("investor-update-home", "pr-babysitter-homepage"),
    "pr-babysitter": ("pr-babysitter-homepage",),
    "startup-metrics": ("investor-update-home",),
    "account-health": (),
    "candidate-review": (),
}


def _stub_egress_binary_copy(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    def copy_with_placeholder(
        source: str | Path,
        destination: str | Path,
        *,
        follow_symlinks: bool = True,
    ) -> str:
        if Path(source) == repo / EGRESS_BINARY:
            Path(destination).write_bytes(b"test egress")
            return str(destination)
        return copy_file(source, str(destination), follow_symlinks=follow_symlinks)

    monkeypatch.setattr("evals.ablate.shutil.copy", copy_with_placeholder)


def test_secondary_text_regression_materializes_one_matched_token_difference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[3]
    reference = load_experiment(repo / "evals/app-builder-filled-control-contrast.toml")
    spec = load_experiment(repo / "evals/app-builder-secondary-text-contrast.toml")
    builder_path = "extensions/sites/ufo_ext_sites/application_builder.py"
    prompt_path = "extensions/sites/ufo_ext_sites/prompts/subagent_ufo_application_builder.md"
    theme_path = "extensions/web/frontend/src/theme.css"
    kit_path = "extensions/sites/ufo_ext_sites/page/kit/kit.css"
    source = {
        path: (repo / path).read_text()
        for path in (builder_path, prompt_path, theme_path, kit_path)
    }
    assert tuple(replacement.path for replacement in spec.arm[0].replacements) == (
        theme_path,
        kit_path,
    )
    base = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    arms = (
        ArmSpec.model_construct(
            name="control",
            files={},
            replacements=(),
        ),
        spec.arm[0],
    )
    patches = {arm.name: arm for arm in arms}
    materialized = {}
    roots: list[Path] = []
    monkeypatch.setattr(Ablation, "_sync", lambda self, root: None)
    _stub_egress_binary_copy(monkeypatch, repo)
    ablation = Ablation(repo=repo, spec=spec, out=tmp_path / "out")

    try:
        tracked = subprocess.run(
            ("git", "-C", str(repo), "ls-tree", "-r", "--name-only", base),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert kit_path not in tracked
        for name, patch in patches.items():
            assert patch.files == {}
            root = tmp_path / name
            roots.append(root)
            ablation._materialize(patch, base, root, None)
            assert (root / "ablate-matrix.toml").is_file()
            files = {path: (root / path).read_text() for path in source}
            compile(files[builder_path], builder_path, "exec")
            materialized[name] = files
    finally:
        for root in roots:
            ablation._remove_worktree(root)

    control = materialized["control"]
    regression = materialized["low-secondary-text"]
    assert control == source
    assert {path for path in source if regression[path] != control[path]} == {
        theme_path,
        kit_path,
    }
    for replacement in spec.arm[0].replacements:
        assert (
            regression[replacement.path].replace(replacement.new, replacement.old)
            == control[replacement.path]
        )
    assert "--text-secondary: light-dark(#676767, #A7A9A9);" in control[theme_path]
    assert "--text-secondary:light-dark(#676767,#a7a9a9)" in control[kit_path]
    assert "--text-secondary: light-dark(#919090, #A7A9A9);" in regression[theme_path]
    assert "--text-secondary:light-dark(#919090,#a7a9a9)" in regression[kit_path]

    assert 'APPLICATION_BUILDER_MODEL = "google/gemini-3.7-flash"' in control[builder_path]
    assert 'APPLICATION_BUILDER_REASONING: Literal["medium"] = "medium"' in control[builder_path]

    def luminance(value: str) -> float:
        channels = tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))
        red, green, blue = (
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        )
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    surface = luminance("#FAF9F7")
    safe_ratio = (surface + 0.05) / (luminance("#676767") + 0.05)
    regression_ratio = (surface + 0.05) / (luminance("#919090") + 0.05)
    assert regression_ratio < 4.5 <= safe_ratio
    assert safe_ratio == pytest.approx(5.4, abs=0.1)
    assert spec.cases == reference.cases
    assert spec.suites == reference.suites
    assert spec.repeats == reference.repeats == 1
    assert spec.concurrency == reference.concurrency == 3
    assert spec.max_stacks == len(patches) == 2
    assert spec.template == reference.template
    assert spec.model is reference.model is None
    assert spec.reasoning is reference.reasoning is None


def test_filled_control_regression_materializes_the_application_runtime_prompt_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[3]
    reference = load_experiment(repo / "evals/app-builder-secondary-text-contrast.toml")
    spec = load_experiment(repo / "evals/app-builder-filled-control-contrast.toml")
    builder_path = "extensions/sites/ufo_ext_sites/application_builder.py"
    prompt_path = "extensions/sites/ufo_ext_sites/prompts/subagent_ufo_application_builder.md"
    theme_path = "extensions/web/frontend/src/theme.css"
    kit_path = "extensions/sites/ufo_ext_sites/page/kit/kit.css"
    paths = (builder_path, prompt_path, theme_path, kit_path)
    source = {path: (repo / path).read_text() for path in paths}
    replacements = spec.arm[0].replacements
    assert tuple(replacement.path for replacement in replacements) == (
        theme_path,
        kit_path,
        prompt_path,
    )
    base = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    arms = (
        ArmSpec.model_construct(name="control", files={}, replacements=()),
        spec.arm[0],
    )
    materialized: dict[str, dict[str, str]] = {}
    roots: list[Path] = []
    monkeypatch.setattr(Ablation, "_sync", lambda self, root: None)
    _stub_egress_binary_copy(monkeypatch, repo)
    ablation = Ablation(repo=repo, spec=spec, out=tmp_path / "out")

    try:
        tracked = subprocess.run(
            ("git", "-C", str(repo), "ls-tree", "-r", "--name-only", base),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert kit_path not in tracked
        for arm in arms:
            root = tmp_path / arm.name
            roots.append(root)
            ablation._materialize(arm, base, root, None)
            assert (root / "ablate-matrix.toml").is_file()
            files = {path: (root / path).read_text() for path in paths}
            compile(files[builder_path], builder_path, "exec")
            materialized[arm.name] = files
    finally:
        for root in roots:
            ablation._remove_worktree(root)

    control = materialized["control"]
    regression = materialized["missing-fill-ink"]
    assert control == source
    assert {path for path in paths if regression[path] != control[path]} == {
        theme_path,
        kit_path,
        prompt_path,
    }
    for replacement in replacements:
        assert (
            regression[replacement.path].replace(replacement.new, replacement.old)
            == control[replacement.path]
        )
    assert control[theme_path].count("--color-fill-ink: #191A1A;") == 1
    assert control[kit_path].count("--color-fill-ink:#191a1a;") == 1
    assert control[prompt_path].count("--accent-primary") == 2
    assert control[prompt_path].count("--color-fill-ink") == 2
    assert control[prompt_path].count("`--color-link` is text, not a fill") == 1
    assert "--color-fill-ink: #191A1A;" not in regression[theme_path]
    assert "--color-fill-ink:#191a1a;" not in regression[kit_path]
    assert "--color-fill-ink" not in regression[prompt_path]
    assert "--text-secondary: light-dark(#676767, #A7A9A9);" in control[theme_path]
    assert "--text-secondary:light-dark(#676767,#a7a9a9)" in control[kit_path]
    assert 'APPLICATION_BUILDER_MODEL = "google/gemini-3.7-flash"' in control[builder_path]
    assert 'APPLICATION_BUILDER_REASONING: Literal["medium"] = "medium"' in control[builder_path]

    def luminance(value: str) -> float:
        channels = tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))
        red, green, blue = (
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        )
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    fill = luminance("#0095FF")
    ink = luminance("#191A1A")
    ratio = (max(fill, ink) + 0.05) / (min(fill, ink) + 0.05)
    assert ratio >= 4.5
    assert ratio == pytest.approx(5.6, abs=0.1)
    assert spec.cases == reference.cases
    assert spec.suites == reference.suites
    assert spec.repeats == reference.repeats == 1
    assert spec.concurrency == reference.concurrency == 3
    assert spec.max_stacks == len(arms) == 2
    assert spec.template == reference.template
    assert spec.model is reference.model is None
    assert spec.reasoning is reference.reasoning is None


def test_style_divergence_regression_materializes_one_wording_difference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[3]
    reference = load_experiment(repo / "evals/app-builder-secondary-text-contrast.toml")
    spec = load_experiment(repo / "evals/app-builder-style-divergence-wording.toml")
    skill_path = "core/src/ufo/skills/ufo-style/SKILL.md"
    tokens_path = "core/src/ufo/skills/ufo-style/references/tokens.css"
    paths = (skill_path, tokens_path)
    source = {path: (repo / path).read_text() for path in paths}
    base = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    arms = (
        ArmSpec.model_construct(name="control", files={}, replacements=()),
        spec.arm[0],
    )
    monkeypatch.setattr(Ablation, "_sync", lambda self, root: None)
    _stub_egress_binary_copy(monkeypatch, repo)
    ablation = Ablation(repo=repo, spec=spec, out=tmp_path / "out")
    roots: dict[str, Path] = {}
    materialized: dict[str, dict[str, str]] = {}

    try:
        for arm in arms:
            root = tmp_path / arm.name
            roots[arm.name] = root
            ablation._materialize(arm, base, root, None)
            changed = subprocess.run(
                ("git", "-C", str(root), "diff", "--name-only"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            assert changed == ([] if arm.name == "control" else list(paths))
            materialized[arm.name] = {path: (root / path).read_text() for path in paths}
    finally:
        for root in roots.values():
            ablation._remove_worktree(root)

    control = materialized["control"]
    regression = materialized["false-divergence"]
    assert control == source
    assert {path for path in paths if regression[path] != control[path]} == set(paths)
    for replacement in spec.arm[0].replacements:
        assert (
            regression[replacement.path].replace(replacement.new, replacement.old)
            == control[replacement.path]
        )
    assert tuple(replacement.path for replacement in spec.arm[0].replacements) == paths
    assert "description:" in control[skill_path]
    stale_claim = "portal's own `theme.css` keeps `#919090`"
    assert stale_claim not in " ".join(control[skill_path].split())
    assert stale_claim in " ".join(regression[skill_path].split())
    assert "diverges from the portal's `theme.css`" not in control[tokens_path]
    assert "diverges from the portal's `theme.css`" in regression[tokens_path]
    assert "--color-mark-soft` carries `#919090`" in control[skill_path]
    assert "`--mark-secondary` stays `#919090`" in control[tokens_path]
    assert "--text-secondary: light-dark(#676767, #A7A9A9);" in control[tokens_path]
    assert "--mark-secondary: light-dark(#919090, #A7A9A9);" in control[tokens_path]
    assert (
        spec.cases
        == reference.cases
        == (
            "pre-meeting-briefs",
            "meeting-tasks",
            "issue-owner",
        )
    )
    assert spec.suites == reference.suites == ("ufo-app-bench",)
    assert spec.repeats == reference.repeats == 1
    assert spec.concurrency == reference.concurrency == 3
    assert spec.max_stacks == len(arms) == 2
    assert spec.budget_usd == 40.0
    assert spec.template == reference.template
    assert spec.model is reference.model is None
    assert spec.reasoning is reference.reasoning is None


READER_REWRITES = {
    "pre-meeting-briefs": "Confirm the SSO date before the renewal call.",
    "meeting-tasks": "Create the agreed tasks and confirm their owners and dates.",
    "issue-planner": "Show the Stripe failure code and a clear explanation.",
    "engineering-metrics": "Pull requests merged 10 hours faster this month.",
    "startup-metrics": "Enterprise supplies 57% of monthly revenue.",
    "account-health": "Resolve the invoice export before Beacon Health renews.",
    "candidate-review": "Noor's scorecard is missing. Sam gave no rollback plan.",
}


async def test_app_bench_workspace_probe_runs_in_the_conversation_container(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"captured", b""

    async def create(*args, **kwargs) -> Process:
        calls.append(args)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    conversation_id = UUID("11111111-1111-1111-1111-111111111111")

    result = await AppBenchWorkspaceProbe(conversation_id).run("capture app", 17)

    assert result.exit_code == 0
    assert result.stdout == "captured"
    assert calls == [
        (
            "docker",
            "exec",
            f"ufo-sbx-{conversation_id}",
            "bash",
            "-lc",
            "capture app",
        )
    ]


async def test_app_bench_workspace_probe_serializes_browser_work(monkeypatch, tmp_path) -> None:
    calls: list[tuple[object, ...]] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class Process:
        returncode = 0

        def __init__(self, position: int) -> None:
            self.position = position

        async def communicate(self) -> tuple[bytes, bytes]:
            if self.position == 0:
                first_started.set()
                await release_first.wait()
            return b"captured", b""

    async def create(*args, **kwargs) -> Process:
        calls.append(args)
        return Process(len(calls) - 1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(
        "evals.suites.ufo_app_bench.BROWSER_PROBE_LOCK", tmp_path / "browser-probe.lock"
    )
    first = asyncio.create_task(AppBenchWorkspaceProbe(UUID(int=1)).run("first"))
    await first_started.wait()
    second = asyncio.create_task(AppBenchWorkspaceProbe(UUID(int=2)).run("second"))
    await asyncio.sleep(0.1)

    assert len(calls) == 1

    release_first.set()
    results = await asyncio.gather(first, second)

    assert [result.stdout for result in results] == ["captured", "captured"]
    assert len(calls) == 2


async def test_app_bench_browser_probe_lock_spans_processes(monkeypatch, tmp_path) -> None:
    lock_path = tmp_path / "browser-probe.lock"
    monkeypatch.setattr("evals.suites.ufo_app_bench.BROWSER_PROBE_LOCK", lock_path)
    holder = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        (
            "import fcntl,sys; "
            "lock=open(sys.argv[1], 'a'); "
            "fcntl.flock(lock, fcntl.LOCK_EX); "
            "print('held', flush=True); "
            "sys.stdin.readline()"
        ),
        str(lock_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert holder.stdout is not None
    assert holder.stdin is not None
    assert await holder.stdout.readline() == b"held\n"
    entered = asyncio.Event()

    async def enter() -> None:
        async with _browser_probe_slot():
            entered.set()

    task = asyncio.create_task(enter())
    try:
        await asyncio.sleep(0.1)
        assert not entered.is_set()
    finally:
        holder.stdin.write(b"\n")
        await holder.stdin.drain()
        await holder.wait()

    await asyncio.wait_for(task, 1)
    assert entered.is_set()


def test_app_bench_audit_builds_interactive_and_static_html() -> None:
    source = AUDIT_CONTENT.decode()

    assert "document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)" in source
    assert "document.caretRangeFromPoint" in source
    assert "renderedText,\n    renderedParts,\n    aboveFoldText," in source
    assert "aboveFoldText: aboveFold.join" not in source
    assert "visuallyHidden(element, style, box)" not in source
    assert "script.setAttribute('src', await asDataUrl(resource))" in source
    assert "style.textContent = await inlineCssResources(css, resource.href)" in source
    assert "sheets.push(await inlineCssResources(css, sheet.href || location.href))" in source
    assert "link.replaceWith(style)" in source
    assert "image.setAttribute('src', await asDataUrl(resource))" in source
    assert "fs.writeFileSync(interactivePath, await interactiveDocument(frame))" in source
    assert "page.$('iframe[name=\"ufo-app\"]')" in source
    assert "if (!element) return page;" in source
    assert "await element.contentFrame()" in source
    assert "frame.evaluate(measure, AA_FLOOR)" in source
    assert "designRegionAudit(browser, acceptedDesignUrl.href)" in source
    assert "document.querySelectorAll('[data-app-region]')" in source
    assert "}).slice(0, 20);" in source
    assert "designRegions, views, interaction" in source
    assert "fs.writeFileSync(staticPath, await frame.content())" in source
    assert "window.__ufoCalls || []" in source
    assert "window.__ufoNavigations || []" in source
    assert "paths.unshift([...path, child])" in source
    assert "reloadStates.push" in source

    preview = APP_PREVIEW.decode()
    assert "window.__ufoCalls=[]" in preview
    assert "window.__ufoNavigations=[]" in preview
    assert 'message.ufo==="navigate"' in preview
    assert 'path==="objects/eval_app_action"' in preview
    assert 'typeof message.body==="string"?JSON.parse(message.body):message.body' in preview
    assert "localStorage.setItem(actionKey" in preview


def test_app_copy_capture_renders_one_static_dom_without_screenshots() -> None:
    source = COPY_CAPTURE_CONTENT.decode()

    assert "await page.goto(url, { waitUntil: 'load' })" in source
    assert "fs.writeFileSync(staticPath, await page.content())" in source
    assert "renderedText" in source
    assert "page.screenshot" not in source
    assert "interactionAudit" not in source


@pytest.mark.docker
def test_app_bench_audit_reads_the_page_chromium_paints(
    sandbox_container: tuple[str, Path],
) -> None:
    container, workspace = sandbox_container
    fixture_html = b"""
        <style>
          body { overflow-x: hidden }
          .flex { display: flex }
          .clip { overflow: hidden; width: 100px; height: 20px }
          .off { display: inline-block; transform: translateX(200px) }
          .fixed { position: fixed; bottom: 20px; left: 20px }
          .below { margin-top: 1000px }
          .card { position: relative }
          .overlay { position: absolute; inset: 0 }
        </style>
        <main>
          <p>$<span>48,000</span> and <strong>78</strong>% at <code>2.4</code>x.</p>
          <div class="flex"><span>2</span><span>open</span></div>
          <table><tr><td>Product design</td><td>sync</td></tr></table>
          <div class="clip"><span class="off">off-canvas fact</span></div>
          <div class="clip"><span class="fixed">fixed bar fact</span></div>
          <div class="card"><span>covered fact</span><a class="overlay" href="#"></a></div>
          <span style="display: none">responsive fact</span><span>responsive fact</span>
          <details><summary>More</summary><p>closed fact</p></details>
          <p class="below">below-fold fact</p>
        </main>
        """ + b"".join(
        f'<section data-app-region="region-{index}">Region {index}</section>'.encode()
        for index in range(21)
    )
    subprocess.run(
        ("docker", "exec", "-i", container, "tee", "/workspace/app-audit.cjs"),
        input=AUDIT_CONTENT,
        check=True,
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        ("docker", "exec", "-i", container, "tee", "/workspace/fixture.html"),
        input=fixture_html,
        check=True,
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        ("docker", "exec", "-i", container, "tee", "/workspace/accepted-design.svg"),
        input=(
            b'<svg viewBox="0 0 1280 800">'
            b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
            b'<g data-app-region="detail"><rect x="680" width="600" height="800" /></g>'
            b"</svg>"
        ),
        check=True,
        capture_output=True,
        timeout=120,
    )
    command = """
python3 -m http.server 8765 --bind 127.0.0.1 --directory /workspace >/tmp/audit-http.log 2>&1 &
server=$!
trap 'kill "$server"' EXIT
for attempt in $(seq 1 50); do
  curl -fsS http://127.0.0.1:8765/fixture.html >/dev/null && break
done
node /workspace/app-audit.cjs http://127.0.0.1:8765/fixture.html \
  /workspace/report.json /workspace/light.png /workspace/dark.png \
  /workspace/interactive.html /workspace/static.html \
  http://127.0.0.1:8765/accepted-design.svg
"""

    subprocess.run(
        ("docker", "exec", container, "bash", "-lc", command),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = loads((workspace / "report.json").read_bytes())
    validated = ApplicationAuditReport.model_validate(report)
    assert all(len(view.regions) == 20 for view in validated.views)
    view = next(
        item
        for item in report["views"]
        if item["scheme"] == "light" and item["width"] == DESKTOP_WIDTH
    )

    assert "$48,000 and 78% at 2.4x." in view["renderedText"]
    assert "2 open" in view["renderedText"]
    assert "Product design sync" in view["renderedText"]
    assert "$48,000 and 78% at 2.4x." in view["aboveFoldText"]
    assert "2 open" in view["aboveFoldText"]
    assert "Product design sync" in view["aboveFoldText"]
    assert "fixed bar fact" in view["aboveFoldText"]
    assert "covered fact" in view["aboveFoldText"]
    assert "responsive fact" in view["aboveFoldText"]
    assert "off-canvas fact" not in view["aboveFoldText"]
    assert "closed fact" not in view["aboveFoldText"]
    assert "below-fold fact" not in view["aboveFoldText"]


@pytest.mark.docker
def test_app_bench_design_measurement_uses_painted_pixels(
    sandbox_container: tuple[str, Path],
) -> None:
    container, workspace = sandbox_container
    subprocess.run(
        ("docker", "exec", "-i", container, "tee", "/workspace/app-audit.cjs"),
        input=AUDIT_CONTENT,
        check=True,
        capture_output=True,
        timeout=120,
    )

    def invoke(svg: bytes) -> subprocess.CompletedProcess[str]:
        subprocess.run(
            ("docker", "exec", "-i", container, "tee", "/workspace/application-design.svg"),
            input=svg,
            check=True,
            capture_output=True,
            timeout=120,
        )
        return subprocess.run(
            (
                "docker",
                "exec",
                container,
                "node",
                "/workspace/app-audit.cjs",
                "--design",
                "/workspace/application-design.svg",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def native(svg: bytes) -> bytes:
        return svg.replace(b"<svg ", b'<svg xmlns="http://www.w3.org/2000/svg" ', 1)

    def render(svg: bytes) -> list[dict[str, object]]:
        result = invoke(native(svg))
        assert result.returncode == 0, result.stderr
        value = loads(result.stdout)
        assert isinstance(value, list)
        return value

    transformed = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue"><path d="M0 0H600V800H0Z" /></g>'
        b'<g transform="translate(680 0)"><g data-app-region="detail">'
        b'<rect width="600px" height="100%" /></g></g></svg>'
    )
    assert [region["name"] for region in transformed] == ["queue", "detail"]
    assert float(transformed[0]["left"]) + float(transformed[0]["width"]) < float(
        transformed[1]["left"]
    )

    structural_css = render(
        b'<svg viewBox="0 0 1280 800"><style>'
        b"g:nth-of-type(2){transform:translateX(680px)}</style>"
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><rect width="600" height="800" /></g></svg>'
    )
    assert [region["name"] for region in structural_css] == ["queue", "detail"]
    assert float(structural_css[0]["left"]) + float(structural_css[0]["width"]) < float(
        structural_css[1]["left"]
    )

    structural_overlap = render(
        b'<svg viewBox="0 0 1280 800"><style>'
        b"g:nth-of-type(2){transform:translateX(-680px)}</style>"
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><rect x="680" width="600" height="800" /></g></svg>'
    )
    assert [region["name"] for region in structural_overlap] == ["queue", "detail"]
    assert float(structural_overlap[0]["left"]) < float(structural_overlap[1]["left"]) + float(
        structural_overlap[1]["width"]
    )
    assert float(structural_overlap[1]["left"]) < float(structural_overlap[0]["left"]) + float(
        structural_overlap[0]["width"]
    )

    first_child = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><rect width="600" height="800" /></g>'
        b"<style>g:first-child{transform:translateX(680px)}</style></svg>"
    )
    assert float(first_child[1]["left"]) + float(first_child[1]["width"]) < float(
        first_child[0]["left"]
    )

    style_attribute_selector = render(
        b'<svg viewBox="0 0 1280 800"><style>'
        b"g:has(+ g[style]){transform:translateX(680px)}</style>"
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><rect width="600" height="800" /></g></svg>'
    )
    assert float(style_attribute_selector[0]["left"]) == float(style_attribute_selector[1]["left"])

    variables_and_shorthands = render(
        b'<svg viewBox="0 0 1280 800"><style>'
        b':root{--detail-x:900px}g[data-app-region="detail"]{'
        b"transform:translateX(var(--detail-x));font:italic 700 60px/1.2 sans-serif;"
        b"text-decoration:underline 4px}</style>"
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><text x="0" y="100">Detail</text></g></svg>'
    )
    assert float(variables_and_shorthands[1]["left"]) > 0.7
    assert float(variables_and_shorthands[1]["height"]) > 0.05

    smil_transform = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><animateTransform attributeName="transform" '
        b'type="translate" from="680 0" to="680 0" dur="1s" />'
        b'<rect width="600" height="800" /></g></svg>'
    )
    assert float(smil_transform[0]["left"]) + float(smil_transform[0]["width"]) < float(
        smil_transform[1]["left"]
    )

    smil_overlap = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><animateTransform attributeName="transform" '
        b'type="translate" from="-680 0" to="-680 0" dur="1s" />'
        b'<rect x="680" width="600" height="800" /></g></svg>'
    )
    assert float(smil_overlap[0]["left"]) == float(smil_overlap[1]["left"])

    smil_opacity = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue" opacity="0">'
        b'<animate attributeName="opacity" from="0.2" to="0.2" dur="1s" />'
        b'<rect width="600" height="800" /></g></svg>'
    )
    assert [region["name"] for region in smil_opacity] == ["queue"]

    smil_points = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><polygon points="0,0 600,0 600,800 0,800">'
        b'<animate attributeName="points" '
        b'values="680,0 1280,0 1280,800 680,800;680,0 1280,0 1280,800 680,800" '
        b'dur="1s" /></polygon></g></svg>'
    )
    assert float(smil_points[0]["left"]) + float(smil_points[0]["width"]) < float(
        smil_points[1]["left"]
    )

    smil_view_box = render(
        b'<svg viewBox="680 0 1280 800">'
        b'<animate attributeName="viewBox" '
        b'values="0 0 1280 800;0 0 1280 800" dur="1s" />'
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><rect x="680" width="600" height="800" /></g></svg>'
    )
    assert [region["name"] for region in smil_view_box] == ["queue", "detail"]

    smil_path_length = render(
        b'<svg viewBox="0 0 1280 800"><g data-app-region="queue">'
        b'<path d="M0 100H600" pathLength="1000" fill="none" stroke="black" '
        b'stroke-width="20" stroke-dasharray="0.1 2">'
        b'<animate attributeName="pathLength" from="1" to="1" dur="1s" />'
        b"</path></g></svg>"
    )
    assert [region["name"] for region in smil_path_length] == ["queue"]
    assert float(smil_path_length[0]["width"]) < 0.1

    low_alpha = render(
        b'<svg viewBox="0 0 1280 800"><rect width="1280" height="800" fill="white" />'
        b'<g data-app-region="queue" opacity="0.1">'
        b'<rect width="600" height="800" /></g></svg>'
    )
    assert low_alpha == []
    visible_alpha = render(
        b'<svg viewBox="0 0 1280 800"><rect width="1280" height="800" fill="white" />'
        b'<g data-app-region="queue" opacity="0.2">'
        b'<rect width="600" height="800" /></g></svg>'
    )
    assert [region["name"] for region in visible_alpha] == ["queue"]

    retained_references = render(
        b'<svg viewBox="0 0 1280 800"><defs>'
        b'<symbol id="card"><rect width="600" height="800" /></symbol>'
        b'<clipPath id="clip"><rect width="300" height="800" /></clipPath>'
        b'<mask id="mask" maskUnits="userSpaceOnUse" x="0" y="0" width="600" height="800">'
        b'<rect width="300" height="800" fill="white" /></mask></defs>'
        b'<rect width="1280" height="800" fill="white" />'
        b'<g data-app-region="queue" opacity="0.2" clip-path="url(#clip)">'
        b'<use href="#card" /></g>'
        b'<g data-app-region="detail" opacity="0.2" transform="translate(900 0)" '
        b'mask="url(#mask)"><use href="#card" /></g></svg>'
    )
    assert [region["name"] for region in retained_references] == ["queue", "detail"]
    assert all(0.2 < float(region["width"]) < 0.25 for region in retained_references)

    visible_reference = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g id="shape" data-app-region="queue"><rect width="200" height="200" /></g>'
        b'<g data-app-region="detail" transform="translate(900 0)">'
        b'<use href="#shape" /></g></svg>'
    )
    assert float(visible_reference[1]["left"]) > 0.7
    assert float(visible_reference[1]["width"]) < 0.2

    colour_token = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g id="fff" data-app-region="queue"><rect width="200" height="200" /></g>'
        b'<g data-app-region="detail"><rect x="900" width="200" height="200" '
        b'fill="#fff" /></g></svg>'
    )
    assert float(colour_token[1]["left"]) > 0.7
    assert float(colour_token[1]["width"]) < 0.2

    duplicate_ids = invoke(
        native(
            b'<svg viewBox="0 0 1280 800">'
            b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
            b'<g id="shape" transform="translate(-900 0)">'
            b'<rect width="200" height="200" /></g>'
            b'<g id="shape" transform="translate(-1800 0)">'
            b'<rect width="600" height="800" /></g>'
            b'<g data-app-region="detail" transform="translate(1800 0)">'
            b'<use href="#shape" /></g></svg>'
        )
    )
    assert duplicate_ids.returncode != 0
    assert "application design SVG ids must be unique" in duplicate_ids.stderr

    within_slop = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><rect x="575.68" width="600" height="800" /></g></svg>'
    )
    beyond_slop = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="queue"><rect width="600" height="800" /></g>'
        b'<g data-app-region="detail"><rect x="573.12" width="600" height="800" /></g></svg>'
    )
    assert application_region_relation(
        ApplicationAuditRegion.model_validate(within_slop[0]),
        ApplicationAuditRegion.model_validate(within_slop[1]),
    ) == ("horizontal", -1)
    assert (
        application_region_relation(
            ApplicationAuditRegion.model_validate(beyond_slop[0]),
            ApplicationAuditRegion.model_validate(beyond_slop[1]),
        )
        is None
    )

    reset = invoke(
        native(
            b'<svg viewBox="0 0 1280 800" style="white-space:pre"><style>'
            b'g{all:initial}</style><g data-app-region="queue">'
            b'<text x="0" y="100">A B</text></g></svg>'
        )
    )
    assert reset.returncode != 0
    assert "application design uses too many style properties" in reset.stderr

    inherited = b"x" * 100_000
    bounded = invoke(
        native(
            b'<svg viewBox="0 0 1280 800"><style>:root{--payload:'
            + inherited
            + b'}</style><g data-app-region="queue"><rect width="1" height="1"/></g>'
            + b"<g/>" * 32
            + b"</svg>"
        )
    )
    assert bounded.returncode != 0
    assert "application design rendered form is too large" in bounded.stderr

    non_rect = render(
        b'<svg viewBox="0 0 1280 800"><defs><symbol id="card">'
        b'<rect width="200" height="200" /></symbol></defs>'
        b'<g data-app-region="queue"><text x="20" y="100" font-size="60">Queue</text></g>'
        b'<g data-app-region="detail"><use href="#card" x="900" y="100" /></g></svg>'
    )
    assert [region["name"] for region in non_rect] == ["queue", "detail"]

    hidden = render(
        b'<svg viewBox="0 0 1280 800">'
        b'<g data-app-region="visible"><rect width="200" height="200" /></g>'
        b'<g data-app-region="hidden" style="display:none">'
        b'<rect width="200" height="200" /></g>'
        b'<g data-app-region="transparent" opacity="0.1">'
        b'<rect width="200" height="200" /></g>'
        b'<g data-app-region="empty"><rect width="0" height="0" /></g></svg>'
    )
    assert [region["name"] for region in hidden] == ["visible"]

    definitions = render(
        b'<svg viewBox="0 0 1280 800"><defs>'
        b'<clipPath id="clip"><rect width="300" height="800" /></clipPath>'
        b'<mask id="mask"><rect width="300" height="800" fill="white" /></mask></defs>'
        b'<g data-app-region="queue"><rect width="600" height="800" clip-path="url(#clip)" /></g>'
        b'<g data-app-region="detail"><rect x="900" width="380" height="800" '
        b'mask="url(#mask)" /></g></svg>'
    )
    assert [region["name"] for region in definitions] == ["queue"]
    assert 0.2 < float(definitions[0]["width"]) < 0.25

    subprocess.run(
        ("docker", "exec", "-i", container, "tee", "/workspace/fixture.html"),
        input=b"<style>main{display:block}</style><main>Rendered app</main>",
        check=True,
        capture_output=True,
        timeout=120,
    )
    command = """
cp /workspace/application-design.svg /workspace/accepted-design.svg
python3 -m http.server 8766 --bind 127.0.0.1 --directory /workspace >/tmp/design-http.log 2>&1 &
server=$!
trap 'kill "$server"' EXIT
for attempt in $(seq 1 50); do
  curl -fsS http://127.0.0.1:8766/fixture.html >/dev/null && break
done
node /workspace/app-audit.cjs http://127.0.0.1:8766/fixture.html \
  /workspace/design-report.json /workspace/design-light.png /workspace/design-dark.png \
  /workspace/design-interactive.html /workspace/design-static.html \
  http://127.0.0.1:8766/accepted-design.svg
"""
    audit = subprocess.run(
        ("docker", "exec", container, "bash", "-lc", command),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert audit.returncode == 0, audit.stderr
    report = loads((workspace / "design-report.json").read_bytes())
    assert report["designRegions"] == definitions


async def test_app_copy_probe_returns_the_browser_rendered_dom_and_text(tmp_path: Path) -> None:
    class Probe:
        async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
            assert "ufo-app-copy-capture.cjs" in command
            assert timeout_s == 120
            directory = tmp_path / ".eval-output" / "meeting-tasks"
            directory.mkdir(parents=True)
            (directory / "meeting-tasks-static.html").write_bytes(b"<main>Rendered</main>")
            (directory / "meeting-tasks-audit.json").write_bytes(b'{"views": []}')
            return ProbeCommandResult(0, "", "")

    result = await _AppCopyProbe("meeting-tasks")(
        CapabilityOutput("", (), workspace_dir=tmp_path), Probe()
    )

    assert result.error == ""
    assert result.artifacts == (
        SharedArtifact("meeting-tasks-static.html", b"<main>Rendered</main>"),
        SharedArtifact("meeting-tasks-audit.json", b'{"views": []}'),
    )


def _output(name: str, content: bytes) -> CapabilityOutput:
    return CapabilityOutput(
        "",
        (
            ToolInvocation(
                "share_file",
                {"files": [{"file_path": f"/workspace/{name}"}]},
                f'[{{"name":"{name}"}}]',
                has_result=True,
            ),
        ),
        artifacts=(SharedArtifact(name, content),),
    )


def _site(
    heading: str,
    *,
    extra_index: bool = False,
    unsafe_path: bool = False,
    extra_entries: int = 0,
) -> bytes:
    result = BytesIO()
    with open_tar(fileobj=result, mode="w:gz") as archive:
        body = f"<!doctype html><html><body><h1>{heading}</h1></body></html>".encode()
        info = TarInfo("index.html")
        info.size = len(body)
        archive.addfile(info, BytesIO(body))
        if extra_index:
            info = TarInfo("nested/index.html")
            info.size = len(body)
            archive.addfile(info, BytesIO(body))
        if unsafe_path:
            archive.addfile(TarInfo("../escape"), BytesIO())
        for index in range(extra_entries):
            archive.addfile(TarInfo(f"assets/{index}"), BytesIO())
    return result.getvalue()


def _zip(parts: dict[str, str | bytes]) -> bytes:
    result = BytesIO()
    with ZipFile(result, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return result.getvalue()


def _oversized_tar_header() -> bytes:
    info = TarInfo("assets/compression-bomb")
    info.size = MAX_EXPANDED_BYTES + 1
    return compress(info.tobuf())


def _workbook(*, formulas: bool = True, chart: bool = True, recalculated: bool = True) -> bytes:
    values = ("0.125", "0.0518519", "0.1267606") if recalculated else ("9", "9", "9")
    formula_cells = (
        f"""
        <c r="B2"><f>(Inputs!B3/Inputs!B2)-1</f><v>{values[0]}</v></c>
        <c r="B3"><f>(Inputs!B4/Inputs!B3)-1</f><v>{values[1]}</v></c>
        <c r="B4"><f>(Inputs!B5/Inputs!B4)-1</f><v>{values[2]}</v></c>
        """
        if formulas
        else '<c r="B2"><v>0.125</v></c>'
    )
    parts: dict[str, str | bytes] = {
        "xl/workbook.xml": """
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>
                <sheet name="Inputs" sheetId="1" r:id="rId1"/>
                <sheet name="Forecast" sheetId="2" r:id="rId2"/>
              </sheets>
            </workbook>
        """,
        "xl/_rels/workbook.xml.rels": """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
              <Relationship Id="rId2" Target="worksheets/sheet2.xml"/>
            </Relationships>
        """,
        "xl/worksheets/sheet1.xml": """
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData><row>
                <c r="B2"><v>120</v></c><c r="B3"><v>135</v></c>
                <c r="B4"><v>142</v></c><c r="B5"><v>160</v></c>
              </row></sheetData>
            </worksheet>
        """,
        "xl/worksheets/sheet2.xml": f"""
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData><row>{formula_cells}</row></sheetData>
            </worksheet>
        """,
    }
    if chart:
        parts["xl/charts/chart1.xml"] = """
            <c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
              <c:chart><c:plotArea><c:lineChart><c:ser>
                <c:val><c:numRef><c:f>Inputs!$B$2:$B$5</c:f></c:numRef></c:val>
              </c:ser></c:lineChart></c:plotArea></c:chart>
            </c:chartSpace>
        """
    return _zip(parts)


def _presentation(
    *,
    slides: int = 6,
    noted: int = 6,
    note_offset: int = 0,
    embedding: bool = True,
    valid_embedding: bool = True,
) -> bytes:
    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
    presentation = "http://schemas.openxmlformats.org/presentationml/2006/main"
    parts: dict[str, str | bytes] = {}
    for index in range(1, slides + 1):
        parts[f"ppt/slides/slide{index}.xml"] = f'<p:sld xmlns:p="{presentation}"/>'
    for index in range(1 + note_offset, noted + 1 + note_offset):
        parts[f"ppt/notesSlides/notesSlide{index}.xml"] = f"""
            <p:notes xmlns:p="{presentation}" xmlns:a="{drawing}">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
                <a:t>Discuss the revenue implication for quarter {index}.</a:t>
              </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:notes>
        """
    points = "".join(
        f'<c:pt idx="{index}"><c:v>{value}</c:v></c:pt>' for index, value in enumerate(REVENUE)
    )
    parts["ppt/charts/chart1.xml"] = f"""
        <c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
          <c:chart><c:plotArea><c:barChart><c:ser>
            <c:val><c:numRef><c:f>Sheet1!$B$2:$B$5</c:f><c:numCache>{points}</c:numCache>
            </c:numRef></c:val>
          </c:ser></c:barChart></c:plotArea></c:chart>
        </c:chartSpace>
    """
    if embedding:
        parts["ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx"] = (
            _workbook() if valid_embedding else b"not a workbook"
        )
    return _zip(parts)


async def test_site_archive_scorer_reads_the_shared_tar_contents() -> None:
    grader = site_archive_scorer("Ufo Status: Operational")

    assert (await grader(_output("site.tar.gz", _site("Ufo Status: Operational")))).passed
    assert not (await grader(_output("site.tar.gz", _site("All systems nominal")))).passed
    assert not (
        await grader(_output("site.tar.gz", _site("Ufo Status: Operational", extra_index=True)))
    ).passed
    assert not (
        await grader(_output("site.tar.gz", _site("Ufo Status: Operational", unsafe_path=True)))
    ).passed
    assert not (
        await grader(
            _output(
                "site.tar.gz",
                _site("Ufo Status: Operational", extra_entries=MAX_ARCHIVE_ENTRIES),
            )
        )
    ).passed


async def test_site_archive_rejects_an_oversized_member_before_reading_its_payload() -> None:
    verdict = await site_archive_scorer("unused")(_output("site.tar.gz", _oversized_tar_header()))

    assert not verdict.passed
    assert verdict.reason.endswith(
        "invalid site archive: expanded content exceeds the inspection limit"
    )


async def test_workbook_scorer_requires_formulas_recalculation_and_chart() -> None:
    grader = forecast_workbook_scorer(REVENUE)

    assert (await grader(_output("forecast.xlsx", _workbook()))).passed
    assert not (await grader(_output("forecast.xlsx", _workbook(formulas=False)))).passed
    assert not (await grader(_output("forecast.xlsx", _workbook(chart=False)))).passed
    assert not (await grader(_output("forecast.xlsx", _workbook(recalculated=False)))).passed
    assert not (
        await grader(
            _output(
                "forecast.xlsx",
                _zip({"xl/workbook.xml": " " * (MAX_PART_BYTES + 1)}),
            )
        )
    ).passed


async def test_presentation_scorer_requires_six_noted_slides_and_editable_chart() -> None:
    grader = board_presentation_scorer(6, REVENUE)

    assert (await grader(_output("board.pptx", _presentation()))).passed
    assert not (await grader(_output("board.pptx", _presentation(slides=5, noted=5)))).passed
    assert not (await grader(_output("board.pptx", _presentation(noted=5)))).passed
    assert not (await grader(_output("board.pptx", _presentation(note_offset=1)))).passed
    assert not (await grader(_output("board.pptx", _presentation(embedding=False)))).passed
    assert not (await grader(_output("board.pptx", _presentation(valid_embedding=False)))).passed


def _png(body: bytes = b"pixels") -> bytes:
    return PNG_MAGIC + body + PNG_TRAILER


def _pdf(body: bytes = b"1 0 obj\n<<>>\nendobj\n", *, eof: bool = True) -> bytes:
    return b"%PDF-1.7\n" + body + (b"%%EOF\n" if eof else b"")


def _ooxml(suffix: str, *, content_types: bool = True, root: bool = True) -> bytes:
    parts: dict[str, str | bytes] = {}
    if content_types:
        parts["[Content_Types].xml"] = "<Types/>"
    if root:
        parts[OOXML_PARTS[suffix]] = "<root/>"
    parts["docProps/core.xml"] = "<props/>"
    return _zip(parts)


def _delivery(files: dict[str, bytes]) -> CapabilityOutput:
    return CapabilityOutput(
        "",
        tuple(
            ToolInvocation(
                "share_file",
                {"files": [{"file_path": f"/workspace/{name}"}]},
                f'[{{"name":"{name}"}}]',
                has_result=True,
            )
            for name in files
        ),
        artifacts=tuple(SharedArtifact(name, content) for name, content in files.items()),
    )


def test_valid_pdf_requires_header_and_trailer() -> None:
    assert valid_pdf(_pdf()).passed
    assert not valid_pdf(b"%PDF- but truncated").passed
    assert not valid_pdf(_pdf(eof=False)).passed
    assert not valid_pdf(b"not a pdf").passed


def test_valid_png_requires_the_signature() -> None:
    assert valid_png(_png()).passed
    assert not valid_png(b"\x89PNGnope").passed
    assert not valid_png(_pdf()).passed
    truncated = valid_png(PNG_MAGIC + b"partial pixel data")
    assert not truncated.passed
    assert "truncated" in truncated.reason


def test_valid_image_accepts_png_and_jpeg_and_rejects_truncation() -> None:
    assert valid_image(_png()).passed
    assert valid_image(JPEG_MAGIC + b"pixels" + JPEG_TRAILER).passed
    assert not valid_image(JPEG_MAGIC + b"pixels").passed
    assert not valid_image(PNG_MAGIC + b"partial").passed
    assert not valid_image(b"GIF89a not supported").passed


def test_office_document_requires_content_types_and_root_part() -> None:
    assert office_document(_ooxml(".docx"), OOXML_PARTS[".docx"]).passed
    assert not office_document(_ooxml(".docx", content_types=False), OOXML_PARTS[".docx"]).passed
    assert not office_document(_ooxml(".docx", root=False), OOXML_PARTS[".docx"]).passed
    assert not office_document(b"not a zip", OOXML_PARTS[".docx"]).passed
    assert not office_document(_ooxml(".pptx"), OOXML_PARTS[".xlsx"]).passed


async def test_office_document_scorer_reads_the_shared_package() -> None:
    grader = office_document_scorer(".docx")

    assert (await grader(_output("memo.docx", _ooxml(".docx")))).passed
    assert not (await grader(_output("memo.docx", _ooxml(".docx", root=False)))).passed
    assert not (await grader(_output("memo.pdf", _pdf()))).passed


async def test_pdf_and_png_scorers_read_shared_bytes() -> None:
    assert (await pdf_document_scorer()(_output("invoice.pdf", _pdf()))).passed
    assert not (await pdf_document_scorer()(_output("invoice.pdf", b"junk"))).passed
    assert (await png_image_scorer()(_output("chart.png", _png()))).passed
    assert not (await png_image_scorer()(_output("chart.png", b"junk"))).passed


async def test_rendered_pages_scorer_counts_shared_page_images() -> None:
    grader = rendered_pages_scorer(2)

    delivery = _delivery({"memo.docx": _ooxml(".docx"), "page-1.png": _png(), "page-2.png": _png()})
    assert (await grader(delivery)).passed
    one_page = _delivery({"memo.docx": _ooxml(".docx"), "page-1.png": _png()})
    assert not (await grader(one_page)).passed
    assert not (await rendered_pages_scorer(1)(_output("memo.docx", _ooxml(".docx")))).passed


async def test_rendered_pages_scorer_rejects_overflow_past_max_pages() -> None:
    grader = rendered_pages_scorer(1, 1)

    assert (await grader(_delivery({"memo.docx": _ooxml(".docx"), "page-1.png": _png()}))).passed
    overflow = _delivery({"memo.docx": _ooxml(".docx"), "page-1.png": _png(), "page-2.png": _png()})
    verdict = await grader(overflow)
    assert not verdict.passed
    assert "more than the 1" in verdict.reason


async def test_rendered_pages_scorer_rejects_a_corrupt_page_image() -> None:
    grader = rendered_pages_scorer(1, 1)

    corrupt = _delivery({"memo.docx": _ooxml(".docx"), "page-1.png": PNG_MAGIC + b"partial"})
    verdict = await grader(corrupt)

    assert not verdict.passed
    assert "not a valid image" in verdict.reason


def _measured(**overrides: object) -> bytes:
    """An audit report over four clean views, with one view's measurements overridden."""
    heights = {DESKTOP_WIDTH: DESKTOP_HEIGHT, NARROW_WIDTH: NARROW_HEIGHT}
    views = [
        {
            "scheme": scheme,
            "width": width,
            "documentWidth": width,
            "viewportWidth": width,
            "documentHeight": heights[width],
            "viewportHeight": heights[width],
            "textChecked": 40,
            "textUnderFloor": 0,
            "text": [],
            "aboveFoldText": "",
            "pastViewport": [],
            "clipped": [],
            "console": [],
            "regions": AUDIT_DESIGN_REGIONS,
        }
        for scheme, width in MEASURED_VIEWS
    ]
    views[0].update(overrides)
    return dumps(
        {
            "designRegions": AUDIT_DESIGN_REGIONS,
            "views": views,
            "interaction": {
                "controls": [
                    {"selector": "#first", "name": "First action"},
                    {"selector": "#second", "name": "Second action"},
                ],
                "successes": [
                    {"selector": "#first", "name": "First action"},
                    {"selector": "#second", "name": "Second action"},
                ],
                "calls": [],
                "reloadStates": [],
                "console": [],
            },
        }
    ).encode()


async def test_measured_screen_scorer_recomputes_the_aa_threshold_per_string() -> None:
    """The audit reports every string under the strict AA floor with its own size and weight, and
    the grader decides which threshold each string owed: body text at 3.03:1 fails, a 32px title at
    3.4:1 clears the large-text threshold and passes. The script cannot soften the verdict, because
    it reports measurements and no thresholds."""
    grader = shared_artifact_scorer(".json", _measured_screen)

    clean = await grader(_output("audit.json", _measured()))
    assert clean.passed, clean.reason

    body = await grader(
        _output(
            "audit.json",
            _measured(
                text=[
                    {
                        "text": "UFO-820",
                        "selector": "span.ref",
                        "px": 11,
                        "weight": 400,
                        "ratio": 3.03,
                    }
                ]
            ),
        )
    )
    assert not body.passed
    assert '"UFO-820" at span.ref is 3.03:1; needs 4.5:1' in body.reason

    large = await grader(
        _output(
            "audit.json",
            _measured(
                text=[
                    {"text": "Sprint 24", "selector": "h1", "px": 32, "weight": 400, "ratio": 3.4}
                ]
            ),
        )
    )
    assert large.passed, large.reason


async def test_measured_screen_scorer_fails_an_unmeasured_view_and_a_wide_document() -> None:
    """A report that skipped a scheme or a width fails as unmeasured rather than passing on the
    views that ran, and a document wider than its viewport at the narrow width fails on its own."""
    grader = shared_artifact_scorer(".json", _measured_screen)

    partial = loads(_measured())
    partial["views"] = [view for view in partial["views"] if view["width"] != 390]
    short = await grader(_output("audit.json", dumps(partial).encode()))
    assert not short.passed
    assert "measures no light at 390px, dark at 390px" in short.reason

    narrow = loads(_measured())
    narrow["views"][2]["documentWidth"] = 402
    overflowing = await grader(_output("audit.json", dumps(narrow).encode()))
    assert not overflowing.passed
    assert "390px document is 402px" in overflowing.reason

    tall = loads(_measured())
    tall["views"][0]["documentHeight"] = DESKTOP_HEIGHT + 500
    scrolls = await grader(_output("audit.json", dumps(tall).encode()))
    assert scrolls.passed, scrolls.reason

    narrow_tall = loads(_measured())
    narrow_tall["views"][2]["documentHeight"] = NARROW_HEIGHT + 500
    narrow_scrolls = await grader(_output("audit.json", dumps(narrow_tall).encode()))
    assert narrow_scrolls.passed, narrow_scrolls.reason

    empty = await grader(_output("audit.json", _measured(textChecked=0)))
    assert not empty.passed
    assert "read no text" in empty.reason

    assert not (await grader(_output("audit.json", b"{"))).passed


async def test_interaction_screen_requires_two_accessible_visible_state_changes() -> None:
    clean = _interaction_screen("kanban-board", _measured())
    assert clean.passed, clean.reason

    too_few_controls = loads(_measured())
    too_few_controls["interaction"]["controls"] = too_few_controls["interaction"]["controls"][:1]
    controls = _interaction_screen("kanban-board", dumps(too_few_controls).encode())
    assert not controls.passed
    assert f"needs {INTERACTION_MIN_CONTROLS}" in controls.reason

    one_change = loads(_measured())
    one_change["interaction"]["successes"] = one_change["interaction"]["successes"][:1]
    changes = _interaction_screen("kanban-board", dumps(one_change).encode())
    assert not changes.passed
    assert f"needs {INTERACTION_MIN_SUCCESSES}" in changes.reason

    repeated = loads(_measured())
    repeated["interaction"]["successes"][1]["selector"] = "#first"
    distinct = _interaction_screen("kanban-board", dumps(repeated).encode())
    assert not distinct.passed
    assert "distinct" in distinct.reason

    noisy = loads(_measured())
    noisy["interaction"]["console"] = ["pageerror: broken"]
    console = _interaction_screen("kanban-board", dumps(noisy).encode())
    assert not console.passed
    assert "pageerror: broken" in console.reason


def _application_qa_calls() -> tuple[ToolInvocation, ...]:
    if APPLICATION_BUILDER_QA_TOOL == "js_repl":
        return (
            ToolInvocation("start_server", {}, "started", has_result=True),
            ToolInvocation("js_repl", {}, "checked", has_result=True),
            ToolInvocation("js_repl", {}, "reviewed", has_result=True),
        )
    return (ToolInvocation(APPLICATION_BUILDER_QA_TOOL, {}, "passed", has_result=True),)


def _built_screen(files: dict[str, bytes]) -> CapabilityOutput:
    result = ApplicationBuilderResult(
        status="deployed",
        source_path="/workspace/ufo-app/app.tsx",
        site_name="built-app",
        site_url="https://ufo.test/built-app",
        browser_batches=2 if APPLICATION_BUILDER_QA_TOOL == "js_repl" else 1,
        controls_checked=("Filter", "Select"),
    ).model_dump_json()
    own_calls = (
        ToolInvocation("load_skill", {"name": "website-building"}, "loaded", has_result=True),
        ToolInvocation(
            APPLICATION_BUILDER_DELEGATION_TOOL,
            {},
            result,
            has_result=True,
        ),
    )
    worker_calls = (
        ToolInvocation("call_external_tool", {}, "facts", has_result=True),
        ToolInvocation(
            APPLICATION_BUILDER_DESIGN_TOOL,
            {"content": "<svg viewBox='0 0 1 1' />"},
            "written",
            has_result=True,
        ),
        ToolInvocation(
            APPLICATION_BUILDER_WRITE_TOOL,
            {"content": "const page = true;"},
            "written",
            has_result=True,
        ),
        *_application_qa_calls(),
        ToolInvocation(APPLICATION_BUILDER_DEPLOY_TOOL, {}, "deployed", has_result=True),
    )
    return CapabilityOutput(
        "Built the app.",
        (*own_calls, *worker_calls),
        artifacts=tuple(SharedArtifact(name, content) for name, content in files.items()),
        own_calls=own_calls,
        own_tools=tuple(call.name for call in own_calls),
    )


async def test_ufo_app_bench_rework_pulls_the_source_between_deploys() -> None:
    case = BENCH_CASES[3]
    assert "pull-before-redeploy" in case.digest_tag
    page = b"<main>Built app</main>"
    files = {
        "daily-brief-rework-interactive.html": page,
        "daily-brief-rework-static.html": page,
        "daily-brief-rework-audit.json": _measured(),
        **{f"daily-brief-rework-{scheme}.png": _png() for scheme in SCHEMES},
    }
    base = _built_screen(files)
    reworked = replace(
        base,
        calls=(
            *base.calls,
            ToolInvocation("object_get", {"kind": "site"}, "read", has_result=True),
            ToolInvocation("js_repl", {}, "checked repair", has_result=True),
            ToolInvocation("js_repl", {}, "reviewed repair", has_result=True),
            ToolInvocation("deploy_website", {}, "redeployed", has_result=True),
        ),
    )
    unpulled = replace(
        base,
        calls=(
            *base.calls,
            ToolInvocation("deploy_website", {}, "redeployed", has_result=True),
        ),
    )

    pulled = await case.grader(reworked)
    once = await case.grader(base)
    skipped = await case.grader(unpulled)

    assert pulled.passed, pulled.reason
    assert not once.passed
    assert "second" in once.reason
    assert not skipped.passed
    assert "object_get" in skipped.reason


async def test_ufo_app_bench_requires_one_end_to_end_worker() -> None:
    base = _built_screen({})
    direct = replace(
        base,
        calls=tuple(
            call for call in base.calls if call.name != APPLICATION_BUILDER_DELEGATION_TOOL
        ),
        own_calls=tuple(
            call for call in base.own_calls if call.name != APPLICATION_BUILDER_DELEGATION_TOOL
        ),
    )
    wrong_lane_call = ToolInvocation(
        "spawn",
        {"target": "website_building", "payload": {}},
        "built",
        has_result=True,
    )
    wrong_lane = replace(
        base,
        calls=(*base.calls, wrong_lane_call),
        own_calls=(*base.own_calls, wrong_lane_call),
    )
    parent_write_call = ToolInvocation(
        "write",
        {"file_path": "/workspace/ufo-app/app.tsx", "content": "bad"},
        "written",
        has_result=True,
    )
    parent_edit_call = ToolInvocation(
        "edit",
        {"file_path": "/workspace/ufo-app/app.tsx", "old_string": "a", "new_string": "b"},
        "edited",
        has_result=True,
    )
    parent_read_call = ToolInvocation(
        "bash",
        {"command": "head -40 /workspace/ufo-app/app.tsx"},
        "source",
        has_result=True,
    )
    parent_write = replace(
        base,
        calls=(*base.calls, parent_write_call),
        own_calls=(*base.own_calls, parent_write_call),
    )
    parent_edit = replace(
        base,
        calls=(*base.calls, parent_edit_call),
        own_calls=(*base.own_calls, parent_edit_call),
    )
    parent_read = replace(
        base,
        calls=(*base.calls, parent_read_call),
        own_calls=(*base.own_calls, parent_read_call),
    )
    no_child_write = replace(
        base,
        calls=tuple(call for call in base.calls if call.name != APPLICATION_BUILDER_WRITE_TOOL),
    )

    accepted = await _application_builder_scorer()(base)
    missing = await _application_builder_scorer()(direct)
    lane = await _application_builder_scorer()(wrong_lane)
    authored = await _application_builder_scorer()(parent_write)
    edited = await _application_builder_scorer()(parent_edit)
    inspected = await _application_builder_scorer()(parent_read)
    unwritten = await _application_builder_scorer()(no_child_write)

    assert accepted.passed, accepted.reason
    assert not missing.passed
    assert f"did not call {APPLICATION_BUILDER_DELEGATION_TOOL}" in missing.reason
    assert not lane.passed
    assert "parent entered the worker loop" in lane.reason
    assert not authored.passed
    assert "parent entered the worker loop" in authored.reason
    assert not edited.passed
    assert "parent entered the worker loop" in edited.reason
    assert not inspected.passed
    assert "parent entered the worker loop" in inspected.reason
    assert not unwritten.passed
    assert APPLICATION_BUILDER_WRITE_TOOL in unwritten.reason

    self_certified = replace(
        base,
        calls=(*base.calls, ToolInvocation("set_homepage", {}, "bound", has_result=True)),
    )
    certified = await _application_builder_scorer()(self_certified)
    assert not certified.passed
    assert "certify its own homepage" in certified.reason


async def test_ufo_app_bench_accepts_the_worker_preloaded_skill() -> None:
    base = _built_screen({})
    without_parent_load = replace(
        base,
        calls=tuple(call for call in base.calls if call.name != "load_skill"),
        own_calls=tuple(call for call in base.own_calls if call.name != "load_skill"),
    )

    verdict = await _skill_scorer()(without_parent_load)

    assert verdict.passed, verdict.reason
    assert "preloads 'website-building'" in verdict.reason


async def test_ufo_app_bench_rejects_a_routine_second_delegation() -> None:
    base = _built_screen({})
    second = ToolInvocation(
        APPLICATION_BUILDER_DELEGATION_TOOL,
        {},
        "repaired",
        has_result=True,
    )
    repeated = replace(
        base,
        calls=(*base.calls, second),
        own_calls=(*base.own_calls, second),
    )
    verdict = await _application_builder_scorer()(repeated)

    assert not verdict.passed
    assert "delegated 2 times" in verdict.reason


async def test_ufo_app_bench_rejects_a_failed_delegation_before_a_success() -> None:
    base = _built_screen({})
    failed = ToolInvocation(
        APPLICATION_BUILDER_DELEGATION_TOOL,
        {},
        "invalid input",
        has_result=True,
        is_error=True,
    )
    repeated = replace(
        base,
        calls=(failed, *base.calls),
        own_calls=(failed, *base.own_calls),
    )
    verdict = await _application_builder_scorer()(repeated)

    assert not verdict.passed
    assert "delegated 2 times" in verdict.reason


async def test_ufo_app_bench_bounds_product_qa_calls() -> None:
    grader = _qa_efficiency_scorer()
    clean = await grader(_built_screen({}))
    assert clean.passed, clean.reason
    max_calls = (
        MAX_BROWSER_QA_CALLS if APPLICATION_BUILDER_QA_TOOL == "js_repl" else MAX_PRODUCT_QA_CALLS
    )

    repeated_qa = replace(
        _built_screen({}),
        calls=(
            *_built_screen({}).calls[:4],
            *(
                ToolInvocation(APPLICATION_BUILDER_QA_TOOL, {}, "extra check", has_result=True)
                for _ in range(max_calls)
            ),
            *_built_screen({}).calls[4:],
        ),
    )
    too_many_calls = await grader(repeated_qa)
    assert not too_many_calls.passed
    assert f"at most {max_calls}" in too_many_calls.reason

    failed_qa = replace(
        _built_screen({}),
        calls=(
            *_built_screen({}).calls,
            ToolInvocation(APPLICATION_BUILDER_QA_TOOL, {}, "failed", has_result=False),
        ),
    )
    failed_call = await grader(failed_qa)
    assert not failed_call.passed
    assert "final QA call failed" in failed_call.reason

    base = _built_screen({})
    first_qa = next(
        index for index, call in enumerate(base.calls) if call.name == APPLICATION_BUILDER_QA_TOOL
    )
    recovered_qa = replace(
        base,
        calls=(
            *base.calls[:first_qa],
            ToolInvocation(APPLICATION_BUILDER_QA_TOOL, {}, "found a defect", has_result=False),
            *base.calls[first_qa:],
        ),
    )
    recovered_call = await grader(recovered_qa)
    assert recovered_call.passed, recovered_call.reason

    qa_after_deploy = replace(
        _built_screen({}),
        calls=(
            *_built_screen({}).calls,
            ToolInvocation(APPLICATION_BUILDER_QA_TOOL, {}, "late check", has_result=True),
        ),
    )
    wrong_order = await grader(qa_after_deploy)
    assert not wrong_order.passed
    assert f"before {APPLICATION_BUILDER_DEPLOY_TOOL}" in wrong_order.reason


async def test_ufo_app_bench_accepts_static_deploy_or_published_application() -> None:
    static = await _delivery_scorer()(_built_screen({}))
    published = replace(
        _built_screen({}),
        calls=(
            ToolInvocation("load_skill", {"name": "website-building"}, "loaded", has_result=True),
            *_application_qa_calls(),
            ToolInvocation("publish_website", {}, "published", has_result=True),
        ),
    )
    app = await _delivery_scorer()(published)
    qa = await _qa_efficiency_scorer()(published)

    assert static.passed, static.reason
    assert app.passed, app.reason
    assert qa.passed, qa.reason
    assert app.evidence == {"appDeliveryPassed": 2, "appDeliveryTotal": 2}


def test_ufo_app_bench_report_keeps_binary_verdict_and_adds_continuous_layers() -> None:
    case = EvalCaseResult(
        name="meeting-tasks",
        passed=False,
        reason="one hard gate failed",
        evidence={
            "selectedAttempt": 0,
            "visualRubric": ["one", "two"],
            "attempts": [
                {
                    "grader": {
                        "appDeliveryPassed": 2,
                        "appDeliveryTotal": 2,
                        "appDesignPassed": 3,
                        "appDesignTotal": 4,
                        "appSourcePassed": 7,
                        "appSourceTotal": 10,
                        "appDensityPassed": 8,
                        "appDensityTotal": 10,
                        "appPagePassed": 4,
                        "appPageTotal": 4,
                        "appInteractionPassed": 1,
                        "appInteractionTotal": 1,
                        "processSkillPassed": 1,
                        "processSkillTotal": 1,
                        "processBuilderPassed": 1,
                        "processBuilderTotal": 1,
                        "processQaPassed": 0,
                        "processQaTotal": 1,
                    },
                    "judge": [
                        {"criterion": "one", "passed": True, "reason": "yes"},
                        {"criterion": "two", "passed": False, "reason": "no"},
                    ],
                }
            ],
        },
    )
    scored = _score_app_report(
        EvalReport(name="ufo-app-bench", suite="capability", digest="sha256:test", cases=(case,))
    )

    result = scored.cases[0]
    assert not result.passed
    assert result.tier == 3
    assert result.evidence["appScoreLayers"] == {
        "delivery": 1.0,
        "design": 0.75,
        "source": 0.7,
        "density": 0.8,
        "page": 1.0,
        "interaction": 1.0,
        "action": 1.0,
        "visual": 0.5,
    }
    assert result.evidence["appScore"] == pytest.approx(6.75 / 8)
    assert result.evidence["processScore"] == pytest.approx(2 / 3)
    assert {metric.name: metric.value for metric in scored.metrics} == {
        "app_score": pytest.approx(6.75 / 8),
        "delivery_score": 1.0,
        "design_score": 0.75,
        "source_score": 0.7,
        "density_score": 0.8,
        "page_score": 1.0,
        "interaction_score": 1.0,
        "action_score": 1.0,
        "visual_score": 0.5,
        "process_score": pytest.approx(2 / 3),
    }


def test_setup_case_action_layer_requires_action_and_setup_proof() -> None:
    case = EvalCaseResult(
        name="setup-issue-owner",
        passed=False,
        reason="setup failed",
        evidence={
            "selectedAttempt": 0,
            "visualRubric": [],
            "attempts": [
                {
                    "grader": {
                        "appActionPassed": 1,
                        "appActionTotal": 1,
                        "appSetupPassed": 0,
                        "appSetupTotal": 1,
                    }
                }
            ],
        },
    )

    scored = _score_app_report(
        EvalReport(name="ufo-app-bench", suite="capability", digest="sha256:test", cases=(case,))
    )

    assert scored.cases[0].evidence["appScoreLayers"]["action"] == 0.5


async def test_ufo_app_bench_grades_every_screen_on_both_schemes() -> None:
    page = b"<main>Built app</main>"

    assert [case.name for case in CONTROL_CASES] == [
        "kanban-board",
        "call-notes",
        "daily-brief",
        "daily-brief-rework",
    ]
    assert [case.name for case in CONNECTED_CASES] == [case.name for case in CONNECTED_APPS]
    assert [case.name for case in ACTION_CASES] == [
        "action-meeting-tasks",
        "action-issue-owner",
        "action-pr-babysitter",
    ]
    assert [case.name for case in SETUP_CASES] == [
        "setup-meeting-tasks",
        "setup-issue-owner",
        "setup-pr-babysitter",
    ]
    assert len(BENCH_CASES) == 20
    assert UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS == 900.0
    for case in CONTROL_CASES[:3]:
        assert f"wait-{UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS:g}" in case.digest_tag
        assert "interactive-homepage" in case.digest_tag
        assert "qa-bounded-product" in case.digest_tag
        assert AUDIT_DIGEST[:12] in case.digest_tag
        shots = {f"{case.name}-{scheme}.png": _png() for scheme in SCHEMES}
        report = {f"{case.name}-audit.json": _measured()}
        pages = {
            f"{case.name}-interactive.html": page,
            f"{case.name}-static.html": page,
        }
        built = await case.grader(_built_screen({**pages, **report, **shots}))
        assert built.passed, case.name

        blocked = ApplicationBuilderResult(
            status="blocked",
            source_path="/workspace/ufo-app/app.tsx",
            browser_batches=0,
            blocker="The source did not compile.",
        ).model_dump_json()
        own_calls = (
            ToolInvocation("load_skill", {"name": "website-building"}, "loaded", has_result=True),
            ToolInvocation(
                APPLICATION_BUILDER_DELEGATION_TOOL,
                {},
                blocked,
                has_result=True,
            ),
        )
        unbound = replace(
            _built_screen({**pages, **report, **shots}),
            calls=(*own_calls, ToolInvocation("deploy_website", {}, "deployed", has_result=True)),
            own_calls=own_calls,
        )
        not_homepage = await case.grader(unbound)
        assert not not_homepage.passed, case.name
        assert "deterministic acceptance" in not_homepage.reason

        one_scheme = await case.grader(
            _built_screen({**pages, **report, f"{case.name}-light.png": _png()})
        )
        assert not one_scheme.passed, case.name
        assert "need 2" in one_scheme.reason

        no_interactive = await case.grader(
            _built_screen({f"{case.name}-static.html": page, **report, **shots})
        )
        assert not no_interactive.passed, case.name
        assert "probe captured 0 -interactive.html artifacts" in no_interactive.reason

        unmeasured = await case.grader(_built_screen({**pages, **shots}))
        assert not unmeasured.passed, case.name
        assert "probe captured 0 -audit.json artifacts" in unmeasured.reason

        assert {item.path for item in case.workspace_files} == {
            "ufo-app/app.tsx",
            "ufo-app/index.html",
            "ufo-app/preview.html",
        }
        assert case.prepare is None
        assert case.artifact_probe is not None
        assert isinstance(case.artifact_probe, _AppBenchProbe)
        probe_command = case.artifact_probe._command()
        assert "/tmp/ufo-app-bench-server.py /workspace/ufo-app" in probe_command
        assert "test -s /workspace/ufo-app/application-design.svg" in probe_command
        assert f'"$capture/{case.name}-design.html"' in probe_command
        assert f'"$capture/{case.name}-design.svg"' in probe_command
        assert (
            f'"$capture/{case.name}-design.svg" "$health_token" '
            ">/tmp/ufo-app-bench-server.log" in probe_command
        )
        assert f"http://localhost:{PROBE_PORT}/preview.html" in probe_command
        assert f"http://localhost:{PROBE_PORT}/accepted-design.svg" in probe_command
        assert "rglob('*.html')" not in probe_command
        assert f'"$capture/{case.name}-interactive.html"' in probe_command
        assert f'"$capture/{case.name}-static.html"' in probe_command
        assert "artifactProbe" in case.payload()
        assert case.visual_rubric[: len(HOUSE_CRITERIA)] == HOUSE_CRITERIA
        assert len(case.visual_rubric) == len(HOUSE_CRITERIA) + 1
        information = case.visual_rubric[-1]
        assert f"{DESKTOP_WIDTH} x {DESKTOP_HEIGHT}" in information
        assert "above the fold" in information
        assert "oversized title" in information
        assert "not an exact fact count" in information
        assert case.message == MEMBER_QUERIES[case.name]
        assert len(case.message.split()) <= 12
        assert "interactive" in case.message.lower()
        assert "homepage" in case.message.lower()
        assert "Evaluation delivery" not in case.message
        leaked = {"column", "attendee", "metric", "token", "viewport"}
        assert not leaked & set(case.message.lower().split())


def test_connected_app_prompts_have_one_to_one_proof_without_staged_data() -> None:
    for spec, case in zip(CONNECTED_APPS, CONNECTED_CASES, strict=True):
        assert case.message == MEMBER_QUERIES[case.name]
        assert case.message.startswith(spec.request)
        assert "interactive" in case.message.lower()
        assert "homepage" in case.message.lower()
        assert "Evaluation delivery" not in case.message
        assert {item.path for item in case.workspace_files} == {
            "ufo-app/app.tsx",
            "ufo-app/index.html",
            "ufo-app/preview.html",
        }
        assert case.prepare is None
        assert case.seed is not None
        assert APP_DATA_DIGEST[:12] in case.digest_tag
        assert case.visual_rubric[: len(HOUSE_CRITERIA)] == HOUSE_CRITERIA
        assert case.judge_on_deterministic_failure
        taste_start = len(HOUSE_CRITERIA)
        taste_end = taste_start + len(TASTE_CRITERIA)
        assert case.visual_rubric[taste_start:taste_end] == TASTE_CRITERIA
        assert taste_end == len(case.visual_rubric) - 1
        assert len(case.visual_rubric) <= 12
        assert all(item.prompt in case.message for item in spec.requirements)
        assert all(
            item.calls or item.visible or item.visible_any or item.rewrite_sources or item.absent
            for item in spec.requirements
        )
        hidden = {
            fact.casefold()
            for item in spec.requirements
            for fact in (*item.visible, *(group[0] for group in item.visible_any))
            if fact.casefold() not in case.message.casefold()
        }
        assert hidden, spec.name


def test_action_cases_add_only_the_connected_action_contract() -> None:
    for contract, case in zip(ACTION_CONTRACTS, ACTION_CASES, strict=True):
        source = CONNECTED_APPS[[item.name for item in CONNECTED_APPS].index(contract.source_case)]

        assert case.name == f"action-{contract.source_case}"
        assert case.message.startswith(MEMBER_QUERIES[contract.source_case])
        assert "application action contract" in case.message
        assert case.seed == _ConnectedAppSeed(source, case.message)
        assert case.artifact_probe == _AppActionProbe(case.name, contract)
        assert contract.kind == "eval_app_action"
        assert contract.connector_value() == {
            "label": contract.label,
            "write": {
                "function": "ufoWrite",
                "arguments": ["eval_app_action", contract.name, contract.spec],
            },
            "read": {
                "function": "ufoRead",
                "arguments": [f"objects/eval_app_action/{contract.name}"],
            },
            "success_text": contract.expected_result,
            "render": {"component": "ApplicationAction", "prop": "action"},
        }


def test_setup_cases_add_connected_state_and_keep_the_action_proof() -> None:
    for action, setup, case in zip(ACTION_CONTRACTS, SETUP_CONTRACTS, SETUP_CASES, strict=True):
        source = CONNECTED_APPS[[item.name for item in CONNECTED_APPS].index(action.source_case)]

        assert case.name == f"setup-{action.source_case}"
        assert case.message.startswith(MEMBER_QUERIES[action.source_case])
        assert "application action contract" in case.message
        assert "connector states" in case.message
        assert "notification surfaces" in case.message
        assert "Open chat when I review setup changes" in case.message
        assert case.seed == _ConnectedAppSeed(source, case.message)
        assert case.artifact_probe == _AppSetupProbe(case.name, action, setup)
        assert setup.connector_value()["review"] == {
            "label": "Review setup in chat",
            "navigate": {"function": "ufoNavigate", "arguments": ["#/new/eval-agent"]},
        }
        assert "Chat as selected" not in case.message
        assert "Slack as available" not in case.message
        assert "iMessage as not connected" not in case.message


async def test_action_scorer_requires_every_deterministic_acceptance_check() -> None:
    proof = {
        "checks": {
            "browser": True,
            "reload": True,
            "applied": True,
            "idempotent": True,
            "refused": True,
            "scoped": True,
            "result": True,
            "fixture": True,
        }
    }
    accepted = await _action_scorer()(
        _output("action-issue-owner-action-proof.json", dumps(proof).encode())
    )
    proof["checks"]["idempotent"] = False
    repeated = await _action_scorer()(
        _output("action-issue-owner-action-proof.json", dumps(proof).encode())
    )

    assert accepted.passed, accepted.reason
    assert accepted.evidence == {"appActionPassed": 1, "appActionTotal": 1}
    assert not repeated.passed
    assert "idempotent" in repeated.reason


async def test_setup_scorer_requires_visible_state_chat_and_no_direct_write() -> None:
    proof = {
        "checks": {
            "visible": True,
            "chat": True,
            "no_direct_setup_write": True,
        }
    }
    accepted = await _setup_scorer()(
        _output("setup-issue-owner-setup-proof.json", dumps(proof).encode())
    )
    proof["checks"]["chat"] = False
    direct = await _setup_scorer()(
        _output("setup-issue-owner-setup-proof.json", dumps(proof).encode())
    )

    assert accepted.passed, accepted.reason
    assert accepted.evidence == {"appSetupPassed": 1, "appSetupTotal": 1}
    assert not direct.passed
    assert "chat" in direct.reason


def test_setup_state_accepts_reader_facing_selection_and_connection_terms() -> None:
    setup = SETUP_CONTRACTS[2]
    visible = (
        "GitHub connected. Chat Active. Slack Available. iMessage Disconnected. "
        "Review setup in chat."
    ).casefold()

    assert _missing_setup_terms(visible, setup) == ()
    assert _missing_setup_terms(visible.replace("available", "pending"), setup) == ("available",)


def test_action_fixture_match_is_a_recursive_subset() -> None:
    actual = {
        "issues": [
            {"number": 520, "owner": "sam"},
            {"number": 521, "owner": "alex", "project_status": "Assigned"},
        ]
    }

    assert _json_contains(actual, {"issues": [{"number": 521, "owner": "alex"}]})
    assert not _json_contains(actual, {"issues": [{"number": 521, "owner": "priya"}]})


def test_connected_app_fixtures_name_their_testing_source_without_live_identifiers() -> None:
    assert {spec.name: spec.source_apps for spec in CONNECTED_APPS} == TESTING_APP_SOURCES
    fixture = APP_DATA_CONTENT.decode().casefold()
    assert all(
        identifier not in fixture
        for identifier in (
            "metalcraftai",
            "flyingobject.ai",
            "marshall-ufo",
            "alexg-ufo",
            "marshall@metalcraft.ai",
        )
    )


def test_issue_owner_proves_the_prepared_action_and_chat_boundary_separately() -> None:
    spec = next(item for item in CONNECTED_APPS if item.name == "issue-owner")
    requirement = next(item for item in spec.requirements if "prepared assignment" in item.prompt)

    assert requirement.visible_any == (
        (
            "Prepare assignment",
            "Prepare an assignment",
            "Prepared Assignment",
            "Prepare note for review",
        ),
        ("Review in chat", "Open chat to review", "Assignments are made in chat"),
    )


def test_copy_cases_reuse_connected_prompts_fixtures_and_browser_rendering() -> None:
    copied_specs = tuple(
        spec
        for spec in CONNECTED_APPS
        if any(requirement.rewrite_sources for requirement in spec.requirements)
    )

    assert [case.name for case in COPY_CASES] == [f"copy-{spec.name}" for spec in copied_specs]
    for spec, copy_case in zip(copied_specs, COPY_CASES, strict=True):
        connected_case = next(case for case in CONNECTED_CASES if case.name == spec.name)
        assert copy_case.message == connected_case.message == MEMBER_QUERIES[spec.name]
        assert (
            copy_case.seed
            == connected_case.seed
            == _ConnectedAppSeed(spec, MEMBER_QUERIES[spec.name])
        )
        assert copy_case.workspace_files == connected_case.workspace_files == APP_WORKSPACE_FILES
        assert connected_case.artifact_probe == _AppBenchProbe(spec.name)
        assert copy_case.artifact_probe == _AppCopyProbe(spec.name)
        assert "ufo-app-copy-capture.cjs" in copy_case.artifact_probe._command()
        assert ".png" not in copy_case.artifact_probe._command()
        assert copy_case.visual_rubric == ()
        assert copy_case.artifact_rubric == ()
        assert f"wait-{UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS:g}" in copy_case.digest_tag
        assert APP_DATA_DIGEST[:12] in copy_case.digest_tag
        assert COPY_CAPTURE_DIGEST[:12] in copy_case.digest_tag


def _rendered_artifacts(name: str, text: str) -> tuple[SharedArtifact, ...]:
    report = loads(_measured(renderedText=text, renderedParts=[text]))
    return (
        SharedArtifact(f"{name}-static.html", f"<main>{text}</main>".encode()),
        SharedArtifact(f"{name}-audit.json", dumps(report).encode()),
    )


def _replace_rendered_text(output: CapabilityOutput, old: bytes, new: bytes) -> CapabilityOutput:
    artifacts = []
    for artifact in output.artifacts:
        if artifact.name.endswith("-static.html"):
            artifacts.append(replace(artifact, content=artifact.content.replace(old, new)))
            continue
        report = loads(artifact.content)
        for view in report["views"]:
            if view["scheme"] == "light" and view["width"] == DESKTOP_WIDTH:
                view["renderedText"] = view["renderedText"].replace(old.decode(), new.decode())
                view["renderedParts"] = [
                    part.replace(old.decode(), new.decode()) for part in view["renderedParts"]
                ]
        artifacts.append(replace(artifact, content=dumps(report).encode()))
    return replace(output, artifacts=tuple(artifacts))


def _append_rendered_text(output: CapabilityOutput, text: str) -> CapabilityOutput:
    artifacts = []
    for artifact in output.artifacts:
        if artifact.name.endswith("-static.html"):
            content = artifact.content.replace(b"</main>", f" {text}</main>".encode())
            artifacts.append(replace(artifact, content=content))
            continue
        report = loads(artifact.content)
        for view in report["views"]:
            if view["scheme"] == "light" and view["width"] == DESKTOP_WIDTH:
                view["renderedText"] = f"{view['renderedText']} {text}"
                view["renderedParts"].append(text)
        artifacts.append(replace(artifact, content=dumps(report).encode()))
    return replace(output, artifacts=tuple(artifacts))


def _copy_output(
    spec: _ConnectedApp,
    content: str,
    *,
    include_source_call: bool = True,
) -> CapabilityOutput:
    requirement = next(item for item in spec.requirements if item.rewrite_sources)
    expected = _rewrite_source_call(requirement.rewrite_sources[0])
    visible = " ".join(
        (*requirement.visible, *(alternatives[0] for alternatives in requirement.visible_any))
    )
    rendered = _rendered_artifacts(spec.name, f"{visible} {content}")
    built = _built_screen({artifact.name: artifact.content for artifact in rendered})
    connector_calls = (
        ToolInvocation("list_external_tools", {}, "listed", has_result=True),
        ToolInvocation(
            "describe_external_tools",
            {"source_id": expected.provider},
            "described",
            has_result=True,
        ),
        *(
            (
                ToolInvocation(
                    "call_external_tool",
                    {"source_id": expected.provider, "tool_name": expected.tool},
                    "called",
                    has_result=True,
                ),
            )
            if include_source_call
            else ()
        ),
    )
    worker_start = next(
        index for index, call in enumerate(built.calls) if call.name == "call_external_tool"
    )
    return replace(
        built,
        calls=(*built.calls[:worker_start], *connector_calls, *built.calls[worker_start + 1 :]),
    )


async def test_copy_grader_requires_source_use_and_rejects_source_copy() -> None:
    spec = CONNECTED_APPS[0]
    requirement = next(item for item in spec.requirements if item.rewrite_sources)
    source = spec._source_text(requirement.rewrite_sources[0])
    grader = _copy_scorer(spec)

    rewritten = await grader(_copy_output(spec, READER_REWRITES[spec.name]))
    missing_source = await grader(
        _copy_output(spec, READER_REWRITES[spec.name], include_source_call=False)
    )
    copied = await grader(_copy_output(spec, source))
    lightly_edited = await grader(
        _copy_output(
            spec,
            "Please leverage cross functional alignment to support the renewal motion.",
        )
    )

    assert rewritten.passed, rewritten.reason
    assert not missing_source.passed
    assert "eval_email.list_emails proof" in missing_source.reason
    assert not copied.passed
    assert "copies source text" in copied.reason
    assert not lightly_edited.passed
    assert "keeps 9/10 source words" in lightly_edited.reason


async def test_copy_case_uses_the_worker_without_visual_or_qa_efficiency_grading() -> None:
    spec = CONNECTED_APPS[0]
    case = COPY_CASES[0]

    verdict = await case.grader(_copy_output(spec, READER_REWRITES[spec.name]))

    assert verdict.passed, verdict.reason


def _connected_requirement_output(
    spec: _ConnectedApp, *, omit_call: bool = False, copy_source: bool = False
) -> CapabilityOutput:
    required = tuple(call for item in spec.requirements for call in item.calls)
    calls = [ToolInvocation("list_external_tools", {}, "listed", has_result=True)]
    for expected in required:
        calls.append(
            ToolInvocation(
                "describe_external_tools",
                {"source_id": expected.provider},
                "described",
                has_result=True,
            )
        )
        calls.append(
            ToolInvocation(
                "call_external_tool",
                {"source_id": expected.provider, "tool_name": expected.tool},
                "called",
                has_result=True,
            )
        )
    if omit_call:
        calls.pop()
    visible = " ".join(
        fact
        for item in spec.requirements
        for fact in (*item.visible, *(group[0] for group in item.visible_any))
    )
    if copy_source:
        visible = f"{visible} {next(item for req in spec.requirements for item in req.absent)}"
    return CapabilityOutput(
        "Built app",
        tuple(calls),
        artifacts=_rendered_artifacts(spec.name, visible),
    )


async def test_connected_app_requirement_grader_checks_calls_facts_and_copy() -> None:
    spec = CONNECTED_APPS[0]
    grader = _requirement_scorer(spec)

    passed = await grader(_connected_requirement_output(spec))
    missing_call = await grader(_connected_requirement_output(spec, omit_call=True))
    copied = await grader(_connected_requirement_output(spec, copy_source=True))
    combined = await grader(_connected_requirement_output(spec, omit_call=True, copy_source=True))

    assert passed.passed, passed.reason
    assert passed.evidence["appSourcePassed"] == passed.evidence["appSourceTotal"]
    assert not missing_call.passed
    assert missing_call.evidence["appSourcePassed"] < missing_call.evidence["appSourceTotal"]
    assert "proof" in missing_call.reason
    assert not copied.passed
    assert "copies source text" in copied.reason
    assert not combined.passed
    assert "proof" in combined.reason
    assert "copies source text" in combined.reason


async def test_connected_app_density_grader_checks_exact_facts_in_both_desktop_views() -> None:
    spec = CONNECTED_APPS[0]
    facts = tuple(
        fact
        for requirement in spec.requirements
        for fact in (*requirement.visible, *(group[0] for group in requirement.visible_any))
    )
    report = loads(_measured())
    for view in report["views"]:
        if view["width"] == DESKTOP_WIDTH:
            view["aboveFoldText"] = " ".join(facts)
    output = CapabilityOutput(
        "Built app",
        (),
        artifacts=(SharedArtifact(f"{spec.name}-audit.json", dumps(report).encode()),),
    )

    passed = await _above_fold_scorer(spec)(output)
    assert passed.passed, passed.reason
    assert passed.evidence["appDensityPassed"] == passed.evidence["appDensityTotal"]

    missing_fact = facts[0]
    report["views"][1]["aboveFoldText"] = report["views"][1]["aboveFoldText"].replace(
        missing_fact, ""
    )
    failed = await _above_fold_scorer(spec)(
        replace(
            output,
            artifacts=(SharedArtifact(f"{spec.name}-audit.json", dumps(report).encode()),),
        )
    )
    assert not failed.passed
    assert f"dark desktop lacks {missing_fact}" in failed.reason
    assert failed.evidence["appDensityPassed"] < failed.evidence["appDensityTotal"]


async def test_app_design_region_grader_measures_names_fold_and_relative_order() -> None:
    report = loads(_measured())
    report["designRegions"] = [
        {
            "name": "queue",
            "left": 0.05,
            "top": 0.1,
            "width": 0.55,
            "height": 0.8,
            "aboveFold": True,
        },
        {
            "name": "detail",
            "left": 0.65,
            "top": 0.1,
            "width": 0.3,
            "height": 0.8,
            "aboveFold": True,
        },
    ]
    for view in report["views"]:
        if view["width"] == DESKTOP_WIDTH:
            view["regions"] = [dict(region) for region in report["designRegions"]]
    output = CapabilityOutput(
        "Built app",
        (),
        artifacts=(SharedArtifact("queue-audit.json", dumps(report).encode()),),
    )

    matched = await _design_region_scorer()(output)

    assert matched.passed
    assert matched.evidence["appDesignPassed"] == matched.evidence["appDesignTotal"]

    dark = next(
        view
        for view in report["views"]
        if view["width"] == DESKTOP_WIDTH and view["scheme"] == "dark"
    )
    dark["regions"][0]["left"] = 0.7
    dark["regions"][1]["left"] = 0.05
    changed = await _design_region_scorer()(
        replace(
            output,
            artifacts=(SharedArtifact("queue-audit.json", dumps(report).encode()),),
        )
    )

    assert not changed.passed
    assert changed.evidence["appDesignPassed"] < changed.evidence["appDesignTotal"]
    assert "changes horizontal order" in changed.reason


def test_source_copy_proof_catches_a_light_edit_and_allows_a_reader_rewrite() -> None:
    source = "Please leverage cross-functional alignment to operationalize the renewal motion."
    markers = ("leverage cross-functional alignment to operationalize the renewal motion",)

    copied = _source_copy(
        source,
        markers,
        ("Please leverage cross functional alignment to support the renewal motion.",),
    )
    rewritten = _source_copy(
        source,
        markers,
        ("Confirm the SSO date before the renewal call.",),
    )

    assert copied == (source, 9, 10)
    assert rewritten is None


def test_every_copy_source_fails_and_every_reader_rewrite_passes() -> None:
    checked = set()
    for spec in CONNECTED_APPS:
        for requirement in spec.requirements:
            for path in requirement.rewrite_sources:
                source = spec._source_text(path)
                assert _source_copy(source, requirement.absent, (source,)) is not None
                assert (
                    _source_copy(source, requirement.absent, (READER_REWRITES[spec.name],)) is None
                )
                checked.add(spec.name)

    assert checked == set(READER_REWRITES)


async def test_connected_app_requirement_grader_rejects_a_light_source_edit() -> None:
    spec = CONNECTED_APPS[0]
    output = _connected_requirement_output(spec)
    verdict = await _requirement_scorer(spec)(
        _append_rendered_text(
            output,
            "Please leverage cross functional alignment to support the renewal motion.",
        )
    )

    assert not verdict.passed
    assert "keeps 9/10 source words" in verdict.reason


async def test_connected_app_requirement_grader_accepts_one_visible_format() -> None:
    spec = CONNECTED_APPS[0]
    output = _connected_requirement_output(spec)
    content = _replace_rendered_text(output, b"12 August", b"Aug 12")
    content = _replace_rendered_text(content, b"21 August", b"Aug 21")

    verdict = await _requirement_scorer(spec)(content)

    assert verdict.passed, verdict.reason

    missing_output = _replace_rendered_text(output, b"12 August", b"")
    missing_output = _replace_rendered_text(missing_output, b"21 August", b"")
    missing = await _requirement_scorer(spec)(missing_output)
    assert not missing.passed
    assert "12 August or Aug 12" in missing.reason


async def test_code_review_requirement_accepts_reader_safe_thread_count_copy() -> None:
    spec = next(item for item in CONNECTED_APPS if item.name == "code-review-queue")
    output = _connected_requirement_output(spec)
    verdict = await _requirement_scorer(spec)(
        _replace_rendered_text(output, b"2 unresolved", b"2 open threads")
    )

    assert verdict.passed, verdict.reason


async def test_code_review_requirement_accepts_failed_check_and_labeled_thread_count() -> None:
    spec = next(item for item in CONNECTED_APPS if item.name == "code-review-queue")
    output = _connected_requirement_output(spec)
    content = _replace_rendered_text(output, b"failing", b"Failed")
    content = _replace_rendered_text(
        content, b"2 unresolved", b"Threads \xc2\xb7 Issue 2 \xc2\xb7 #602"
    )

    verdict = await _requirement_scorer(spec)(content)

    assert verdict.passed, verdict.reason


async def test_issue_planner_requirement_accepts_reader_safe_intent_copy() -> None:
    spec = next(item for item in CONNECTED_APPS if item.name == "issue-planner")
    output = _connected_requirement_output(spec)
    content = _replace_rendered_text(output, b"Needs product", b"Product Decisions")
    content = _replace_rendered_text(content, b"Review plan in chat", b"Prepare Chat Intent")

    verdict = await _requirement_scorer(spec)(content)

    assert verdict.passed, verdict.reason


async def test_startup_metrics_requirement_accepts_stated_customer_churn() -> None:
    spec = next(item for item in CONNECTED_APPS if item.name == "startup-metrics")
    output = _connected_requirement_output(spec)
    content = _replace_rendered_text(output, b"16%", b"25.0%")
    content = _replace_rendered_text(content, b"4 subscriptions", b"4 total")

    verdict = await _requirement_scorer(spec)(content)

    assert verdict.passed, verdict.reason


async def test_engineering_metrics_requirement_accepts_compact_hour_copy() -> None:
    spec = next(item for item in CONNECTED_APPS if item.name == "engineering-metrics")
    output = _connected_requirement_output(spec)
    verdict = await _requirement_scorer(spec)(_replace_rendered_text(output, b"10 hours", b"10h"))

    assert verdict.passed, verdict.reason


async def test_account_health_requirement_accepts_direct_action_copy() -> None:
    spec = next(item for item in CONNECTED_APPS if item.name == "account-health")
    output = _connected_requirement_output(spec)
    content = _replace_rendered_text(output, b"Resolve invoice export", b"Own the invoice mismatch")
    content = _replace_rendered_text(content, b"Contact Dana", b"Call Dana")

    verdict = await _requirement_scorer(spec)(content)

    assert verdict.passed, verdict.reason


async def test_connected_app_seed_grants_sources_and_keeps_one_fixed_data_universe(
    db: None, tmp_path: Path
) -> None:
    workspace_id = uuid4()
    member_id = uuid4()
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@evalco.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="google/gemini-3.7-flash",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    spec = CONNECTED_APPS[0]
    with ws(workspace_id):
        await _ConnectedAppSeed(spec, MEMBER_QUERIES[spec.name])(
            workspace_id,
            agent_id,
            WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
        )
        second = CONNECTED_APPS[1]
        await _ConnectedAppSeed(second, MEMBER_QUERIES[second.name])(
            workspace_id,
            agent_id,
            WorkspaceBlobStore(FilesystemBlobStore(tmp_path)),
        )
        action_contract = ACTION_CONTRACTS[1]
        stored = await ScopedStore(extension=EVAL_ENV_NAME).get(
            f"{APP_FIXTURE_PREFIX}{DRIVE_PROVIDER}:list_documents"
        )
        action_fixture = await ScopedStore(extension=EVAL_ENV_NAME).get(
            f"{APP_FIXTURE_PREFIX}{GITHUB_PROVIDER}:{action_contract.fixture_tool}"
        )
        first_contract = await ScopedStore(extension="sites").get(
            APPLICATION_AUDIT_REQUEST_CONTRACT_KEY.format(
                request_sha256=sha256(MEMBER_QUERIES[spec.name].encode()).hexdigest()
            )
        )
        second_contract = await ScopedStore(extension="sites").get(
            APPLICATION_AUDIT_REQUEST_CONTRACT_KEY.format(
                request_sha256=sha256(MEMBER_QUERIES[second.name].encode()).hexdigest()
            )
        )
        async with workspace_tx() as connection:
            providers = (
                (
                    await connection.execute(
                        sa.select(tables.connection.c.provider).where(
                            tables.connection.c.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            fixture_rows = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.ext_store)
                    .where(
                        tables.ext_store.c.workspace_id == workspace_id,
                        tables.ext_store.c.extension == EVAL_ENV_NAME,
                    )
                )
            ).scalar_one()
            email_rows = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(eval_env_email)
                    .where(eval_env_email.c.workspace_id == workspace_id)
                )
            ).scalar_one()
            event_rows = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(eval_env_event)
                    .where(eval_env_event.c.workspace_id == workspace_id)
                )
            ).scalar_one()

    assert sorted(providers) == [
        "eval_calendar",
        "eval_email",
        GITHUB_PROVIDER,
        "google_drive",
    ]
    assert stored == APP_UNIVERSE_TOOLS[DRIVE_PROVIDER]["list_documents"]
    assert isinstance(action_fixture, dict)
    assert ApplicationAuditContract.model_validate(first_contract) == _application_audit_contract(
        spec
    )
    assert ApplicationAuditContract.model_validate(second_contract) == _application_audit_contract(
        second
    )
    assert action_fixture["application_actions"] == [
        contract.connector_value()
        for contract in ACTION_CONTRACTS
        if contract.fixture_tool == action_contract.fixture_tool
    ]
    assert action_fixture["application_setups"] == [
        contract.connector_value()
        for contract in SETUP_CONTRACTS
        if contract.fixture_tool == action_contract.fixture_tool
    ]
    assert isinstance(stored, dict)
    documents = stored["documents"]
    assert isinstance(documents, list)
    assert len(documents) == sum(
        len(case.tools.get(DRIVE_PROVIDER, {}).get("list_documents", {}).get("documents", []))
        for case in CONNECTED_APPS
    )
    assert fixture_rows == sum(
        len(APP_UNIVERSE_TOOLS[provider]) for provider in (DRIVE_PROVIDER, GITHUB_PROVIDER)
    )
    assert email_rows == len(APP_UNIVERSE_EMAILS)
    assert event_rows == len(APP_UNIVERSE_EVENTS)


def test_ufo_app_bench_rubric_asks_only_for_visible_design_judgments() -> None:
    assert all("legible" not in criterion for criterion in HOUSE_CRITERIA)
    assert all("clipped" not in criterion for criterion in HOUSE_CRITERIA)
    assert all("tokens.css" not in criterion for criterion in HOUSE_CRITERIA)
    assert all("licensed" not in criterion for criterion in HOUSE_CRITERIA)
    assert any("Judge each image independently" in criterion for criterion in HOUSE_CRITERIA)
    assert any("minor radius differences" in criterion for criterion in HOUSE_CRITERIA)
