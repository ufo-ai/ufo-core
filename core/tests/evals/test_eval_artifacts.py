"""Deterministic artifact graders inspect delivered bytes, not filenames or claims."""

import asyncio
import subprocess
import sys
from dataclasses import replace
from gzip import compress
from io import BytesIO
from json import dumps, loads
from pathlib import Path
from tarfile import TarInfo
from tarfile import open as open_tar
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import sqlalchemy as sa
from ufo_ext_eval_env.manifest import (
    APP_FIXTURE_PREFIX,
    CALENDAR_PROVIDER,
    DRIVE_PROVIDER,
    EMAIL_PROVIDER,
    GITHUB_PROVIDER,
    eval_env_email,
    eval_env_event,
)
from ufo_ext_eval_env.manifest import (
    NAME as EVAL_ENV_NAME,
)

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
    APP_DATA_CONTENT,
    APP_DATA_DIGEST,
    APP_UNIVERSE_EMAILS,
    APP_UNIVERSE_EVENTS,
    APP_UNIVERSE_TOOLS,
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
    MAX_PREVIEW_SERVER_CALLS,
    MEASURED_VIEWS,
    MEMBER_QUERIES,
    NARROW_HEIGHT,
    NARROW_WIDTH,
    SCHEMES,
    TASTE_CRITERIA,
    AppBenchWorkspaceProbe,
    _above_fold_scorer,
    _AppBenchProbe,
    _AppCopyProbe,
    _browser_probe_slot,
    _ConnectedApp,
    _ConnectedAppSeed,
    _copy_scorer,
    _delivery_scorer,
    _interaction_screen,
    _measured_screen,
    _qa_efficiency_scorer,
    _requirement_scorer,
    _rewrite_source_call,
    _score_app_report,
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
    assert "renderedParts" in source
    assert "script.setAttribute('src', await asDataUrl(resource))" in source
    assert "link.replaceWith(style)" in source
    assert "image.setAttribute('src', await asDataUrl(resource))" in source
    assert "fs.writeFileSync(interactivePath, await interactiveDocument(page))" in source
    assert "fs.writeFileSync(staticPath, await page.content())" in source


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
    (workspace / "app-audit.cjs").write_bytes(AUDIT_CONTENT)
    (workspace / "fixture.html").write_text(
        """
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
        """
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
  /workspace/interactive.html /workspace/static.html
"""

    subprocess.run(
        ("docker", "exec", container, "bash", "-lc", command),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = loads((workspace / "report.json").read_bytes())
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
            "renderedText": "",
            "renderedParts": [],
            "aboveFoldText": "",
            "pastViewport": [],
            "clipped": [],
            "console": [],
        }
        for scheme, width in MEASURED_VIEWS
    ]
    views[0].update(overrides)
    return dumps(
        {
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
    assert "3.03:1 needs 4.5:1" in body.reason

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


def _built_screen(files: dict[str, bytes]) -> CapabilityOutput:
    return CapabilityOutput(
        "Built the app.",
        (
            ToolInvocation("load_skill", {"name": "website-building"}, "loaded", has_result=True),
            ToolInvocation("start_server", {}, "started", has_result=True),
            ToolInvocation("js_repl", {}, "checked", has_result=True),
            ToolInvocation("js_repl", {}, "reviewed", has_result=True),
            ToolInvocation("deploy_website", {}, "deployed", has_result=True),
            ToolInvocation("set_homepage", {}, "bound", has_result=True),
        ),
        artifacts=tuple(SharedArtifact(name, content) for name, content in files.items()),
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


async def test_ufo_app_bench_bounds_preview_setup_and_browser_batches() -> None:
    grader = _qa_efficiency_scorer()
    clean = await grader(_built_screen({}))
    assert clean.passed, clean.reason

    repeated_server = replace(
        _built_screen({}),
        calls=(
            *_built_screen({}).calls,
            ToolInvocation("start_server", {}, "started again", has_result=True),
        ),
    )
    too_many_starts = await grader(repeated_server)
    assert not too_many_starts.passed
    assert f"needs {MAX_PREVIEW_SERVER_CALLS}" in too_many_starts.reason

    repeated_browser = replace(
        _built_screen({}),
        calls=(
            *_built_screen({}).calls[:4],
            *(
                ToolInvocation("js_repl", {}, "extra check", has_result=True)
                for _ in range(MAX_BROWSER_QA_CALLS - 1)
            ),
            *_built_screen({}).calls[4:],
        ),
    )
    too_many_batches = await grader(repeated_browser)
    assert not too_many_batches.passed
    assert f"at most {MAX_BROWSER_QA_CALLS}" in too_many_batches.reason

    failed_browser = replace(
        _built_screen({}),
        calls=(
            *_built_screen({}).calls[:4],
            ToolInvocation("js_repl", {}, "timed out", has_result=False),
            *_built_screen({}).calls[4:],
        ),
    )
    failed_batch = await grader(failed_browser)
    assert not failed_batch.passed
    assert "final browser QA batch failed" in failed_batch.reason

    base = _built_screen({})
    recovered_browser = replace(
        base,
        calls=(
            *base.calls[:4],
            ToolInvocation("js_repl", {}, "found a defect", has_result=False),
            ToolInvocation("js_repl", {}, "repair passed", has_result=True),
            *base.calls[4:],
        ),
    )
    recovered_batch = await grader(recovered_browser)
    assert recovered_batch.passed, recovered_batch.reason

    browser_after_deploy = replace(
        _built_screen({}),
        calls=(
            *_built_screen({}).calls,
            ToolInvocation("js_repl", {}, "late check", has_result=True),
        ),
    )
    wrong_order = await grader(browser_after_deploy)
    assert not wrong_order.passed
    assert "before deploy_website" in wrong_order.reason

    untested_first_deploy = replace(
        base,
        calls=(
            base.calls[0],
            base.calls[1],
            base.calls[4],
            base.calls[5],
            base.calls[2],
            base.calls[3],
            ToolInvocation("deploy_website", {}, "redeployed", has_result=True),
        ),
    )
    missing_initial_qa = await grader(untested_first_deploy)
    assert not missing_initial_qa.passed
    assert "0 successful browser QA batch(es) before deploy_website" in missing_initial_qa.reason

    failed_redeploy = replace(
        base,
        calls=(
            *base.calls,
            ToolInvocation("js_repl", {}, "checked repair", has_result=True),
            ToolInvocation("js_repl", {}, "reviewed repair", has_result=True),
            ToolInvocation("deploy_website", {}, "failed", has_result=True, is_error=True),
        ),
    )
    deployment_failure = await grader(failed_redeploy)
    assert not deployment_failure.passed
    assert "final application deployment failed" in deployment_failure.reason

    repaired_then_reworked = replace(
        base,
        calls=(
            *base.calls[:4],
            ToolInvocation("js_repl", {}, "found a defect", has_result=False),
            ToolInvocation("js_repl", {}, "repair passed", has_result=True),
            *base.calls[4:],
            ToolInvocation("js_repl", {}, "checked rework", has_result=True),
            ToolInvocation("js_repl", {}, "reviewed rework", has_result=True),
            ToolInvocation("deploy_website", {}, "redeployed", has_result=True),
        ),
    )
    over_budget = await grader(repaired_then_reworked)
    assert not over_budget.passed
    assert f"at most {MAX_BROWSER_QA_CALLS}" in over_budget.reason

    reworked = replace(
        base,
        calls=(
            *base.calls,
            ToolInvocation("js_repl", {}, "reviewed rework", has_result=True),
            ToolInvocation("deploy_website", {}, "redeployed", has_result=True),
        ),
    )
    rework_proof = await grader(reworked)
    assert rework_proof.passed, rework_proof.reason

    premature_homepage = replace(
        base,
        calls=(
            *base.calls[:4],
            base.calls[5],
            base.calls[4],
        ),
    )
    homepage_order = await grader(premature_homepage)
    assert not homepage_order.passed
    assert "set_homepage must run after the first deploy_website" in homepage_order.reason

    late_homepage = replace(
        base,
        calls=(
            *base.calls[:5],
            ToolInvocation("js_repl", {}, "checked rework", has_result=True),
            ToolInvocation("js_repl", {}, "reviewed rework", has_result=True),
            ToolInvocation("deploy_website", {}, "redeployed", has_result=True),
            base.calls[5],
        ),
    )
    late_binding = await grader(late_homepage)
    assert not late_binding.passed
    assert "before a redeploy" in late_binding.reason


async def test_ufo_app_bench_accepts_static_deploy_or_published_application() -> None:
    static = await _delivery_scorer()(_built_screen({}))
    published = replace(
        _built_screen({}),
        calls=(
            ToolInvocation("load_skill", {"name": "website-building"}, "loaded", has_result=True),
            ToolInvocation("start_server", {}, "started", has_result=True),
            ToolInvocation("js_repl", {}, "checked", has_result=True),
            ToolInvocation("js_repl", {}, "reviewed", has_result=True),
            ToolInvocation("publish_website", {}, "published", has_result=True),
            ToolInvocation("set_homepage", {}, "bound", has_result=True),
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
        "source": 0.7,
        "density": 0.8,
        "page": 1.0,
        "interaction": 1.0,
        "visual": 0.5,
    }
    assert result.evidence["appScore"] == pytest.approx(5 / 6)
    assert result.evidence["processScore"] == pytest.approx(1 / 2)
    assert {metric.name: metric.value for metric in scored.metrics} == {
        "app_score": pytest.approx(5 / 6),
        "delivery_score": 1.0,
        "source_score": 0.7,
        "density_score": 0.8,
        "page_score": 1.0,
        "interaction_score": 1.0,
        "visual_score": 0.5,
        "process_score": pytest.approx(1 / 2),
    }


async def test_ufo_app_bench_grades_every_screen_on_both_schemes() -> None:
    page = b"<main>Built app</main>"

    assert [case.name for case in CONTROL_CASES] == [
        "kanban-board",
        "call-notes",
        "daily-brief",
        "daily-brief-rework",
    ]
    assert [case.name for case in CONNECTED_CASES] == [case.name for case in CONNECTED_APPS]
    assert len(BENCH_CASES) == 14
    assert all(case.judge_on_deterministic_failure for case in BENCH_CASES)
    assert UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS == 900.0
    for case in CONTROL_CASES[:3]:
        assert f"wait-{UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS:g}" in case.digest_tag
        assert "interactive-homepage" in case.digest_tag
        assert f"qa-total-{MAX_BROWSER_QA_CALLS}:redeploy-1" in case.digest_tag
        assert AUDIT_DIGEST[:12] in case.digest_tag
        shots = {f"{case.name}-{scheme}.png": _png() for scheme in SCHEMES}
        report = {f"{case.name}-audit.json": _measured()}
        pages = {
            f"{case.name}-interactive.html": page,
            f"{case.name}-static.html": page,
        }
        built = await case.grader(_built_screen({**pages, **report, **shots}))
        assert built.passed, case.name

        unbound = replace(
            _built_screen({**pages, **report, **shots}),
            calls=(
                ToolInvocation(
                    "load_skill", {"name": "website-building"}, "loaded", has_result=True
                ),
                ToolInvocation("deploy_website", {}, "deployed", has_result=True),
            ),
        )
        not_homepage = await case.grader(unbound)
        assert not not_homepage.passed, case.name
        assert "set_homepage" in not_homepage.reason

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
        assert "probe captured 0 .json artifacts" in unmeasured.reason

        assert not case.workspace_files
        assert case.artifact_probe is not None
        assert isinstance(case.artifact_probe, _AppBenchProbe)
        probe_command = case.artifact_probe._command()
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
        assert not case.workspace_files
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
        assert copy_case.seed == connected_case.seed == _ConnectedAppSeed(spec)
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
    calls = [
        ToolInvocation("load_skill", {"name": "website-building"}, "loaded", has_result=True),
        ToolInvocation("list_external_tools", {}, "listed", has_result=True),
        ToolInvocation(
            "describe_external_tools",
            {"source_id": expected.provider},
            "described",
            has_result=True,
        ),
    ]
    if include_source_call:
        calls.append(
            ToolInvocation(
                "call_external_tool",
                {"source_id": expected.provider, "tool_name": expected.tool},
                "called",
                has_result=True,
            )
        )
    calls.extend(
        (
            ToolInvocation("deploy_website", {}, "deployed", has_result=True),
            ToolInvocation("set_homepage", {}, "bound", has_result=True),
        )
    )
    visible = " ".join(
        (*requirement.visible, *(alternatives[0] for alternatives in requirement.visible_any))
    )
    return CapabilityOutput(
        "Built app",
        tuple(calls),
        artifacts=_rendered_artifacts(spec.name, f"{visible} {content}"),
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


async def test_copy_case_ignores_visual_contrast_and_browser_qa_gates() -> None:
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
        blob = WorkspaceBlobStore(FilesystemBlobStore(tmp_path))
        await asyncio.gather(
            _ConnectedAppSeed(spec)(workspace_id, agent_id, blob),
            _ConnectedAppSeed(CONNECTED_APPS[1])(workspace_id, agent_id, blob),
        )
        stored = await ScopedStore(extension=EVAL_ENV_NAME).get(
            f"{APP_FIXTURE_PREFIX}{DRIVE_PROVIDER}:list_documents"
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
        CALENDAR_PROVIDER,
        EMAIL_PROVIDER,
        GITHUB_PROVIDER,
        DRIVE_PROVIDER,
    ]
    assert stored == APP_UNIVERSE_TOOLS[DRIVE_PROVIDER]["list_documents"]
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
