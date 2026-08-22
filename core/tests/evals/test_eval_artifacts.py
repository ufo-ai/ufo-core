"""Deterministic artifact graders inspect delivered bytes, not filenames or claims."""

import asyncio
from dataclasses import replace
from gzip import compress
from io import BytesIO
from json import dumps, loads
from tarfile import TarInfo
from tarfile import open as open_tar
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

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
from evals.harness.capability import CapabilityOutput, SharedArtifact, ToolInvocation
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
    AUDIT_CONTENT,
    DESKTOP_HEIGHT,
    DESKTOP_WIDTH,
    HOUSE_CRITERIA,
    INFORMATION_FACT_COUNTS,
    INTERACTION_MIN_CONTROLS,
    INTERACTION_MIN_SUCCESSES,
    MEASURED_VIEWS,
    MEMBER_QUERIES,
    NARROW_HEIGHT,
    NARROW_WIDTH,
    PALETTE_STEPS,
    SCHEMES,
    AppBenchWorkspaceProbe,
    _AppBenchProbe,
    _declarations,
    _interaction_screen,
    _measured_screen,
)
from evals.suites.ufo_app_bench import CASES as BENCH_CASES
from evals.suites.ufo_app_bench import (
    WORKFLOW_WAIT_SECONDS as UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS,
)
from ufo.skills.runtime import CORE_SKILLS_BY_NAME

REVENUE = (120, 135, 142, 160)


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


def test_app_bench_audit_builds_interactive_and_static_html() -> None:
    source = AUDIT_CONTENT.decode()

    assert "script.setAttribute('src', await asDataUrl(resource))" in source
    assert "link.replaceWith(style)" in source
    assert "image.setAttribute('src', await asDataUrl(resource))" in source
    assert "fs.writeFileSync(interactivePath, await interactiveDocument(page))" in source
    assert "fs.writeFileSync(staticPath, await page.content())" in source


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
            ToolInvocation("deploy_website", {}, "deployed", has_result=True),
            ToolInvocation("set_homepage", {}, "bound", has_result=True),
        ),
        artifacts=tuple(SharedArtifact(name, content) for name, content in files.items()),
    )


async def test_ufo_app_bench_grades_every_screen_on_both_schemes() -> None:
    page = b"<main>Built app</main>"

    assert [case.name for case in BENCH_CASES] == ["kanban-board", "call-notes", "daily-brief"]
    assert UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS == 900.0
    for case in BENCH_CASES:
        assert f"wait-{UFO_APP_BENCH_WORKFLOW_WAIT_SECONDS:g}" in case.digest_tag
        assert "interactive-homepage" in case.digest_tag
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
        facts = INFORMATION_FACT_COUNTS[case.name]
        assert f"at least {facts} distinct requested facts" in information
        assert f"{DESKTOP_WIDTH} x {DESKTOP_HEIGHT}" in information
        assert "above the fold" in information
        assert "oversized title" in information
        assert case.message == MEMBER_QUERIES[case.name]
        assert len(case.message.split()) <= 12
        assert "interactive" in case.message.lower()
        assert "homepage" in case.message.lower()
        assert "Evaluation delivery" not in case.message
        leaked = {"column", "attendee", "metric", "token", "viewport"}
        assert not leaked & set(case.message.lower().split())


def test_ufo_app_bench_rubric_reads_the_shipped_tokens() -> None:
    """The visual rubric quotes the tokens rather than restating them, so the judge grades the
    screenshot against the look the product ships; a token the skill no longer declares raises."""
    tokens = dict(CORE_SKILLS_BY_NAME["ufo-style"].files)["references/tokens.css"].decode()
    palette = HOUSE_CRITERIA[0]
    assert all("legible" not in criterion for criterion in HOUSE_CRITERIA)
    assert all("clipped" not in criterion for criterion in HOUSE_CRITERIA)

    for step in PALETTE_STEPS:
        declared = _declarations(step)
        assert declared in palette
        assert declared.partition(": ")[2] in tokens

    assert _declarations("--radius") == "--radius: 0.25rem"
    with pytest.raises(KeyError):
        _declarations("--bkgd-400")
