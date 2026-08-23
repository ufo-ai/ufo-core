"""Three natural app requests, inspected after the evaluated turn without delivery instructions."""

import asyncio
import base64
import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from evals.harness.artifact_checks import ArtifactCheck, valid_png
from evals.harness.capability import (
    ArtifactProbeResult,
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ProbeCommandResult,
    SharedArtifact,
    WorkspaceProbe,
)
from evals.harness.scorers import combine, required_tools_scorer, skill_scorer
from ufo.skills.runtime import CORE_SKILLS_BY_NAME

HOUSE_STYLE_SKILL = "ufo-style"
HOUSE_TOKENS = "references/tokens.css"
SITE_SKILL = "website-building"
PALETTE_STEPS = (
    "--bkgd-100",
    "--bkgd-200",
    "--bkgd-300",
    "--text-primary",
    "--text-secondary",
    "--mark-secondary",
    "--accent-primary",
    "--accent-secondary",
)
AUDIT_CONTENT = Path(__file__).with_name("ufo_app_bench_audit.cjs").read_bytes()
PROBE_OUTPUT = ".eval-output"
PROBE_PORT = 8137
PROBE_TIMEOUT_SECONDS = 120
DESKTOP_WIDTH = 1440
DESKTOP_HEIGHT = 900
NARROW_WIDTH = 390
NARROW_HEIGHT = 844
SCHEMES = ("light", "dark")
# The four views `app-audit.cjs` measures. A report missing one fails the case as an unmeasured view
# rather than passing on the three that ran.
MEASURED_VIEWS = tuple(
    (scheme, width) for width in (DESKTOP_WIDTH, NARROW_WIDTH) for scheme in SCHEMES
)
AA_BODY = 4.5
AA_LARGE = 3.0
LARGE_PX = 24.0
LARGE_BOLD_PX = 18.66
BOLD_WEIGHT = 700
REPORTED_FAILURES = 4
WORKFLOW_WAIT_SECONDS = 900.0
SUPPORTED_BACKENDS = ("docker",)
INTERACTION_MIN_CONTROLS = 2
INTERACTION_MIN_SUCCESSES = 2
MAX_PREVIEW_SERVER_CALLS = 1
MAX_BROWSER_QA_CALLS = 4
INFORMATION_FACT_COUNTS = {
    "kanban-board": 18,
    "call-notes": 14,
    "daily-brief": 15,
    "daily-brief-rework": 15,
}
MEMBER_QUERIES = {
    "kanban-board": "Build your interactive project board homepage for the ops team.",
    "call-notes": "Build your interactive internal notes homepage for one customer call.",
    "daily-brief": "Build your interactive daily brief homepage for the team.",
    "daily-brief-rework": (
        "Build your interactive daily brief homepage for the team. Once it is live, make the "
        "headline row bolder and redeploy the page."
    ),
}


@dataclass(frozen=True)
class AppBenchWorkspaceProbe(WorkspaceProbe):
    """Run a bounded app-bench command in a local Docker conversation sandbox."""

    conversation_id: UUID

    async def run(self, command: str, timeout_s: int = 60) -> ProbeCommandResult:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            f"ufo-sbx-{self.conversation_id}",
            "bash",
            "-lc",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            return ProbeCommandResult(124, "", "probe timed out", timeout_s)
        return ProbeCommandResult(
            process.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )


@dataclass(frozen=True)
class _AppBenchProbe:
    name: str

    async def __call__(
        self, output: CapabilityOutput, probe: WorkspaceProbe
    ) -> ArtifactProbeResult:
        if output.workspace_dir is None:
            return ArtifactProbeResult(error="app probe has no workspace")
        directory = output.workspace_dir / PROBE_OUTPUT / self.name
        result = await probe.run(self._command(), PROBE_TIMEOUT_SECONDS)
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no command output"
            return ArtifactProbeResult(error=f"app probe failed: {detail[:500]}")
        paths = (
            directory / f"{self.name}-interactive.html",
            directory / f"{self.name}-static.html",
            directory / f"{self.name}-audit.json",
            *(directory / f"{self.name}-{scheme}.png" for scheme in SCHEMES),
        )
        missing = [path.name for path in paths if not path.is_file()]
        if missing:
            return ArtifactProbeResult(error=f"app probe produced no {', '.join(missing)}")
        contents = await asyncio.gather(*(asyncio.to_thread(path.read_bytes) for path in paths))
        return ArtifactProbeResult(
            artifacts=tuple(
                SharedArtifact(path.name, content)
                for path, content in zip(paths, contents, strict=True)
            )
        )

    def _command(self) -> str:
        directory = f"/workspace/{PROBE_OUTPUT}/{self.name}"
        audit = base64.b64encode(AUDIT_CONTENT).decode()
        find_page = """python3 - <<'PY'
from pathlib import Path

root = Path('/workspace')
excluded = {'.skills', '.eval-output', 'node_modules'}
pages = [
    path
    for path in root.rglob('*.html')
    if not excluded.intersection(path.parts) and path.is_file()
]
pages.sort(key=lambda path: (path.name == 'index.html', path.stat().st_mtime), reverse=True)
if pages:
    print(pages[0])
PY
"""
        readiness = f"""python3 - <<'PY'
import socket
import time

deadline = time.time() + 15
while time.time() < deadline:
    try:
        with socket.create_connection(('127.0.0.1', {PROBE_PORT}), timeout=1):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.2)
raise SystemExit(1)
PY"""
        return (
            "set -eu\n"
            f"capture={shlex.quote(directory)}\n"
            'rm -rf "$capture"\n'
            'mkdir -p "$capture"\n'
            f"printf %s {shlex.quote(audit)} | base64 -d > /tmp/ufo-app-bench-audit.cjs\n"
            f"page=$({find_page})\n"
            'if [ -z "$page" ]; then printf %s "no generated HTML page" >&2; exit 2; fi\n'
            f"(fuser -k {PROBE_PORT}/tcp 2>/dev/null || true)\n"
            f"nohup python3 -m http.server {PROBE_PORT} --bind 127.0.0.1 "
            ' --directory "$(dirname "$page")" >/tmp/ufo-app-bench-server.log 2>&1 &\n'
            f"{readiness}\n"
            "node /tmp/ufo-app-bench-audit.cjs "
            f'http://localhost:{PROBE_PORT}/$(basename "$page") '
            f'"$capture/{self.name}-audit.json" "$capture/{self.name}-light.png" '
            f'"$capture/{self.name}-dark.png" "$capture/{self.name}-interactive.html" '
            f'"$capture/{self.name}-static.html"'
        )


def _tokens() -> str:
    return dict(CORE_SKILLS_BY_NAME[HOUSE_STYLE_SKILL].files)[HOUSE_TOKENS].decode()


def _declarations(*names: str) -> str:
    """The named one-line token declarations, verbatim from the shipped `tokens.css`, in the order
    asked for. The rubric is written out of these instead of restating them, so a palette, face or
    radius the product changes changes the judge's words with it, and a name the tokens no longer
    declare raises here, at import, rather than grading a screen against a look that is gone."""
    declared = {}
    for line in _tokens().splitlines():
        name, separator, value = line.strip().rstrip(";").partition(": ")
        if separator and name in names:
            declared[name] = f"{name}: {value}"
    return "; ".join(declared[name] for name in names)


HOUSE_CRITERIA = (
    "Every colour on the screen is one of the house palette steps as `tokens.css` declares them "
    f"({_declarations(*PALETTE_STEPS)}), or a mix of two of those steps. Each screenshot runs one "
    "scheme throughout — the light value of every pair, or the dark value of every pair — and no "
    "hue from outside the palette appears.",
    "The screen is neutral with the accent carried by a few small elements: an accent is a fill, a "
    "marker or a link, never the colour of body text or of a whole pane, and the second accent "
    "appears only where something wants attention.",
    "Text is set in the house faces "
    f"({_declarations('--font-sans', '--font-mono', '--font-display')}): a grotesque for text, a "
    "monospace for code and for any value read as data, a serif only for display type. No "
    "decorative, novelty or mismatched face appears, and no licensed portal face is loaded.",
    "Type stays on the house scale for an app screen "
    f"({_declarations('--text-mono', '--text-title', '--text-display')}, so nothing is smaller "
    "than the first, the screen's own title may take the display step once, and nothing else is "
    "larger than the second), and the sizes on screen read as a few deliberate steps rather than a "
    "size per element.",
    f"Corners are the one house radius ({_declarations('--radius')}, about 4px) on every panel, "
    f"card, control and menu, and only a row or a chip is the full pill "
    f"({_declarations('--radius-row')}). No large soft-rounded cards, and no mix of rounded and "
    "square corners.",
    "Gaps, padding and alignment are even and deliberate: elements sit on a shared grid, "
    "comparable gaps match, columns and baselines line up, and related controls use consistent "
    "interior padding.",
)


def _information_criterion(name: str, inventory: str) -> str:
    facts = INFORMATION_FACT_COUNTS[name]
    return (
        f"Each {DESKTOP_WIDTH} x {DESKTOP_HEIGHT} screenshot shows at least {facts} distinct "
        f"requested facts above the fold: {inventory}. Count each label and its value separately, "
        "and count each title, name, state, date, timestamp, action, decision, summary paragraph, "
        "or source once. The facts use the working area as compact grouped content; no oversized "
        "title, empty decorative panel, or unused region takes space from the requested "
        "information."
    )


def _needed_ratio(px: float, weight: int) -> float:
    """WCAG AA for one rendered string: 4.5:1, or 3:1 where the text is large — 24px, or 18.66px at
    bold weight. The audit reports every string under the strict floor with its own size and weight,
    so the threshold each string owed is decided here and not by the script that measured it."""
    if px >= LARGE_PX or (px >= LARGE_BOLD_PX and weight >= BOLD_WEIGHT):
        return AA_LARGE
    return AA_BODY


def _measured_screen(content: bytes) -> ArtifactCheck:
    """The measured half: every view `app-audit.cjs` shot, with contrast reported first because it
    is the check a first bench run showed a static token check cannot make. Contrast fails the
    screen, then a document wider than its viewport, then clipped text, then a console error."""
    try:
        report = json.loads(content)
        views = {(str(view["scheme"]), int(view["width"])): view for view in report["views"]}
        missing = [
            f"{scheme} at {width}px"
            for scheme, width in MEASURED_VIEWS
            if (scheme, width) not in views
        ]
        if missing:
            return ArtifactCheck(False, f"measures no {', '.join(missing)}")
        unread = [
            f"{scheme} at {width}px"
            for scheme, width in MEASURED_VIEWS
            if int(views[(scheme, width)]["textChecked"]) == 0
        ]
        if unread:
            return ArtifactCheck(False, f"read no text in {', '.join(unread)} — the page is empty")
        below = [
            f"{scheme} {width}px {item['selector']} at {float(item['ratio'])}:1 needs {needed}:1"
            for scheme, width in MEASURED_VIEWS
            for item in views[(scheme, width)]["text"]
            if (needed := _needed_ratio(float(item["px"]), int(item["weight"])))
            > float(item["ratio"])
        ]
        wide = [
            f"{scheme} {width}px document is {int(views[(scheme, width)]['documentWidth'])}px"
            for scheme, width in MEASURED_VIEWS
            if int(views[(scheme, width)]["documentWidth"]) > width
        ]
        clipped = [
            f"{scheme} {width}px clips {clip}"
            for scheme, width in MEASURED_VIEWS
            for clip in views[(scheme, width)]["clipped"]
        ]
        noisy = [
            f"{scheme} {width}px logs {problem}"
            for scheme, width in MEASURED_VIEWS
            for problem in views[(scheme, width)]["console"]
        ]
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError) as error:
        return ArtifactCheck(False, f"is not a bench audit report: {error}")
    if below:
        return ArtifactCheck(
            False,
            f"{len(below)} string(s) of text below AA: {'; '.join(below[:REPORTED_FAILURES])}",
        )
    for reported in (wide, clipped, noisy):
        if reported:
            return ArtifactCheck(False, "; ".join(reported[:REPORTED_FAILURES]))
    return ArtifactCheck(
        True, "every string clears AA in both schemes, and nothing clips or overflows horizontally"
    )


def _interaction_screen(name: str, content: bytes) -> ArtifactCheck:
    try:
        interaction = json.loads(content)["interaction"]
        controls = interaction["controls"]
        successes = interaction["successes"]
        problems = interaction["console"]
        selectors = {str(success["selector"]) for success in successes}
        names = [str(success["name"]) for success in successes]
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError) as error:
        return ArtifactCheck(False, f"is not an interaction audit: {error}")
    if problems:
        return ArtifactCheck(False, f"interaction logs {str(problems[0])[:300]}")
    if len(controls) < INTERACTION_MIN_CONTROLS:
        return ArtifactCheck(
            False,
            f"{name} exposes {len(controls)} accessible control(s), needs "
            f"{INTERACTION_MIN_CONTROLS}",
        )
    if len(selectors) < INTERACTION_MIN_SUCCESSES:
        return ArtifactCheck(
            False,
            f"{name} has {len(selectors)} distinct visible state change(s), needs "
            f"{INTERACTION_MIN_SUCCESSES}",
        )
    return ArtifactCheck(
        True,
        f"{name} has {len(selectors)} browser-proved visible state changes: "
        f"{', '.join(names[:INTERACTION_MIN_SUCCESSES])}",
    )


def _captured_artifact_scorer(
    suffix: str, validate: Callable[[bytes], ArtifactCheck] | None = None
) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.artifact_error:
            return CapabilityVerdict(False, f"artifact inspection failed: {output.artifact_error}")
        artifacts = [
            artifact for artifact in output.artifacts if artifact.name.lower().endswith(suffix)
        ]
        if len(artifacts) != 1:
            return CapabilityVerdict(False, f"probe captured {len(artifacts)} {suffix} artifacts")
        if validate is None:
            return CapabilityVerdict(True, f"captured {artifacts[0].name}")
        checked = validate(artifacts[0].content)
        return CapabilityVerdict(checked.passed, f"{artifacts[0].name}: {checked.reason}")

    return DescribedGrader(f"the harness probe captures one valid {suffix} artifact", grade)


def _screen_images_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        images = [artifact for artifact in output.artifacts if artifact.name.endswith(".png")]
        if len(images) != len(SCHEMES):
            return CapabilityVerdict(
                False, f"probe captured {len(images)} screen image(s), need {len(SCHEMES)}"
            )
        invalid = next((image for image in images if not valid_png(image.content).passed), None)
        if invalid is not None:
            return CapabilityVerdict(False, f"{invalid.name} is not a valid PNG")
        return CapabilityVerdict(True, f"captured {len(images)} screen image(s)")

    return DescribedGrader("the harness probe captures both valid screen images", grade)


def _direct_application_build_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        delegated = tuple(call for call in output.calls if call.name == "build_website")
        if delegated:
            return CapabilityVerdict(
                False,
                f"used build_website {len(delegated)} time(s); build the application directly",
            )
        return CapabilityVerdict(True, "built the application without whole-site delegation")

    return DescribedGrader(
        "the application build does not delegate a second whole-site build", grade
    )


def _qa_efficiency_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        starts = tuple(call for call in output.calls if call.name == "start_server")
        if len(starts) != MAX_PREVIEW_SERVER_CALLS:
            return CapabilityVerdict(
                False,
                f"used start_server {len(starts)} time(s), needs {MAX_PREVIEW_SERVER_CALLS}",
            )
        if not starts[0].succeeded:
            return CapabilityVerdict(False, "the preview server did not start successfully")
        browser = tuple(call for call in output.calls if call.name == "js_repl")
        if not browser:
            return CapabilityVerdict(False, "used no js_repl browser QA batch")
        if len(browser) > MAX_BROWSER_QA_CALLS:
            return CapabilityVerdict(
                False,
                f"used {len(browser)} browser QA batches, needs at most {MAX_BROWSER_QA_CALLS}",
            )
        successful = tuple(call for call in browser if call.succeeded)
        if len(successful) < 2:
            return CapabilityVerdict(
                False, f"used {len(successful)} successful browser QA batch(es), needs at least 2"
            )
        if not browser[-1].succeeded:
            return CapabilityVerdict(False, "the final browser QA batch failed")
        names = tuple(call.name for call in output.calls)
        if "deploy_website" not in names or "set_homepage" not in names:
            return CapabilityVerdict(
                False, "browser QA must precede deploy_website and set_homepage"
            )
        start_index = names.index("start_server")
        browser_indexes = tuple(index for index, name in enumerate(names) if name == "js_repl")
        deploy_index = names.index("deploy_website")
        homepage_index = names.index("set_homepage")
        if not start_index < min(browser_indexes):
            return CapabilityVerdict(False, "browser QA must run after start_server")
        if not max(browser_indexes) < deploy_index:
            return CapabilityVerdict(False, "browser QA must finish before deploy_website")
        if not deploy_index < homepage_index:
            return CapabilityVerdict(False, "set_homepage must run after deploy_website")
        return CapabilityVerdict(
            True,
            f"started one preview server and used {len(browser)} browser QA batch(es)",
        )

    return DescribedGrader(
        "one preview start and two to four browser QA batches ending in success", grade
    )


def _pull_before_redeploy_scorer() -> Grader:
    """The edit half of the home skills: a change to an already-deployed page pulls the deployed
    source before it deploys again, never reworking whatever the sandbox already holds."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        names = tuple(call.name for call in output.calls)
        deploys = tuple(index for index, name in enumerate(names) if name == "deploy_website")
        if len(deploys) < 2:
            return CapabilityVerdict(
                False, f"used deploy_website {len(deploys)} time(s); the rework needs a second"
            )
        pulls = tuple(
            index
            for index, call in enumerate(output.calls)
            if call.name == "object_get" and call.succeeded
        )
        if not any(deploys[0] < pull < deploys[-1] for pull in pulls):
            return CapabilityVerdict(
                False, "the rework must object_get the site between the deploys, before editing"
            )
        return CapabilityVerdict(True, "pulled the deployed source before the rework's deploy")

    return DescribedGrader("the rework pulls the deployed source before deploying again", grade)


def _screen(name: str, information_inventory: str) -> CapabilityCase:
    return CapabilityCase(
        name,
        MEMBER_QUERIES[name],
        combine(
            skill_scorer(SITE_SKILL, HOUSE_STYLE_SKILL),
            required_tools_scorer(
                ("deploy_website", "set_homepage"),
                (("deploy_website", "set_homepage"),),
            ),
            _direct_application_build_scorer(),
            _qa_efficiency_scorer(),
            _captured_artifact_scorer("-interactive.html"),
            _captured_artifact_scorer("-static.html"),
            _captured_artifact_scorer(".json", _measured_screen),
            _captured_artifact_scorer(".json", lambda content: _interaction_screen(name, content)),
            _screen_images_scorer(),
        ),
        visual_rubric=(*HOUSE_CRITERIA, _information_criterion(name, information_inventory)),
        digest_tag=(
            f"ufo-app-bench:{name}:interactive-homepage:qa-1x{MAX_BROWSER_QA_CALLS}:"
            f"wait-{WORKFLOW_WAIT_SECONDS:g}"
        ),
        artifact_probe=_AppBenchProbe(name),
    )


CASES = (
    _screen(
        "kanban-board",
        "the board context, useful workflow groups, enough work items to scan, and ownership or "
        "current state on each item; the exact layout, labels, and item count are the builder's "
        "choice",
    ),
    _screen(
        "call-notes",
        "the call identity and context, participants, the outcome or summary, decisions or key "
        "points, and follow-up work with responsibility or timing where useful; the exact layout "
        "and labels are the builder's choice",
    ),
    _screen(
        "daily-brief",
        "the brief's date or scope, current status or metrics, material changes, priorities or "
        "follow-ups, and source context; the exact layout, labels, and item count are the "
        "builder's choice",
    ),
    CapabilityCase(
        "daily-brief-rework",
        MEMBER_QUERIES["daily-brief-rework"],
        combine(
            skill_scorer(SITE_SKILL, HOUSE_STYLE_SKILL),
            required_tools_scorer(
                ("deploy_website", "set_homepage", "object_get"),
                (("deploy_website", "set_homepage"),),
            ),
            _direct_application_build_scorer(),
            _pull_before_redeploy_scorer(),
            _captured_artifact_scorer("-interactive.html"),
            _captured_artifact_scorer("-static.html"),
            _captured_artifact_scorer(".json", _measured_screen),
            _captured_artifact_scorer(
                ".json", lambda content: _interaction_screen("daily-brief-rework", content)
            ),
            _screen_images_scorer(),
        ),
        visual_rubric=(
            *HOUSE_CRITERIA,
            _information_criterion(
                "daily-brief-rework",
                "the brief's date or scope, current status or metrics, material changes, "
                "priorities or follow-ups, and source context; the exact layout, labels, and "
                "item count are the builder's choice",
            ),
        ),
        digest_tag=(
            f"ufo-app-bench:daily-brief-rework:pull-before-redeploy:wait-{WORKFLOW_WAIT_SECONDS:g}"
        ),
        artifact_probe=_AppBenchProbe("daily-brief-rework"),
    ),
)
