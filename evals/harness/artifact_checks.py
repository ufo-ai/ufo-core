"""Bounded deterministic inspection of eval-produced site and Office artifacts."""

from __future__ import annotations

import re
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from itertools import pairwise
from pathlib import PurePosixPath
from posixpath import normpath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_PART_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4096
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")
NOTES_RE = re.compile(r"ppt/notesSlides/notesSlide(\d+)\.xml$")


@dataclass(frozen=True)
class ArtifactCheck:
    passed: bool
    reason: str


class ArtifactInvalid(ValueError):
    pass


class _Headings(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self._parts: list[str] = []
        self.headings: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "h1":
            if self._depth == 0:
                self._parts = []
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "h1" or self._depth == 0:
            return
        self._depth -= 1
        if self._depth == 0:
            self.headings.append(" ".join("".join(self._parts).split()))

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)


def site_archive(content: bytes, expected_heading: str) -> ArtifactCheck:
    """Require a safe gzip tar with one index containing exactly the expected h1."""
    try:
        with tarfile.open(fileobj=BytesIO(content), mode="r:gz") as archive:
            members: list[tarfile.TarInfo] = []
            expanded_bytes = 0
            while (member := archive.next()) is not None:
                if len(members) >= MAX_ARCHIVE_ENTRIES:
                    raise ArtifactInvalid("archive has too many entries")
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ArtifactInvalid(f"unsafe path {member.name!r}")
                if member.size < 0:
                    raise ArtifactInvalid(f"entry {member.name!r} has a negative size")
                expanded_bytes += member.size
                if expanded_bytes > MAX_EXPANDED_BYTES:
                    raise ArtifactInvalid("expanded content exceeds the inspection limit")
                members.append(member)
            indexes = [
                member
                for member in members
                if member.isreg() and PurePosixPath(member.name).name == "index.html"
            ]
            if len(indexes) != 1:
                return ArtifactCheck(False, f"archive has {len(indexes)} regular index.html files")
            extracted = archive.extractfile(indexes[0])
            if extracted is None:
                return ArtifactCheck(False, "archive index.html cannot be read")
            body = extracted.read(MAX_PART_BYTES + 1)
    except (ArtifactInvalid, tarfile.TarError) as error:
        return ArtifactCheck(False, f"invalid site archive: {error}")
    if len(body) > MAX_PART_BYTES:
        return ArtifactCheck(False, "archive index.html exceeds the inspection limit")
    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        return ArtifactCheck(False, "archive index.html is not UTF-8")
    parser = _Headings()
    parser.feed(html)
    if parser.headings != [expected_heading]:
        return ArtifactCheck(False, f"index.html h1 headings are {parser.headings!r}")
    return ArtifactCheck(True, f"archive contains index.html with h1 {expected_heading!r}")


def forecast_workbook(content: bytes, expected_revenue: tuple[int, ...]) -> ArtifactCheck:
    """Require Inputs/Forecast sheets, recalculated growth formulas, and a linked chart."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            _bounded_zip(archive)
            sheets = _sheet_paths(archive)
            missing = [name for name in ("Inputs", "Forecast") if name not in sheets]
            if missing:
                return ArtifactCheck(False, f"workbook is missing sheet(s): {', '.join(missing)}")
            inputs = _xml(archive, sheets["Inputs"])
            forecast = _xml(archive, sheets["Forecast"])
            if not _contains_numbers(_sheet_numbers(inputs), expected_revenue):
                return ArtifactCheck(False, "Inputs does not contain all requested revenue values")
            growth = tuple(
                (formula, value)
                for formula, value in _growth_formula_cells(forecast)
                if "inputs" in formula.lower()
            )
            if len(growth) < len(expected_revenue) - 1:
                return ArtifactCheck(False, "Forecast has fewer than three Inputs growth formulas")
            if any(value is None for _, value in growth):
                return ArtifactCheck(False, "growth formulas have no recalculated numeric values")
            expected_growth = tuple(
                (current / previous) - 1 for previous, current in pairwise(expected_revenue)
            )
            cached_growth = tuple(value for _, value in growth if value is not None)
            if not _contains_numbers(cached_growth, expected_growth):
                return ArtifactCheck(False, "growth formula values are not recalculated correctly")
            if forecast.findall(f".//{{{SPREADSHEET_NS}}}c[@t='e']"):
                return ArtifactCheck(False, "Forecast contains a formula error")
            chart_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("xl/charts/chart") and name.endswith(".xml")
            )
            if not chart_names:
                return ArtifactCheck(False, "workbook contains no chart")
            linked_chart = any(_linked_chart(archive, name) for name in chart_names)
            if not linked_chart:
                return ArtifactCheck(False, "workbook chart is not linked to Inputs or Forecast")
    except (ArtifactInvalid, BadZipFile, KeyError, ElementTree.ParseError) as error:
        return ArtifactCheck(False, f"invalid workbook: {error}")
    return ArtifactCheck(True, "workbook has inputs, recalculated growth formulas, and a chart")


def board_presentation(
    content: bytes, expected_slides: int, expected_revenue: tuple[int, ...]
) -> ArtifactCheck:
    """Require the requested slide count, notes on every slide, and an editable revenue chart."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            _bounded_zip(archive)
            slide_numbers = sorted(
                int(match.group(1))
                for name in archive.namelist()
                if (match := SLIDE_RE.fullmatch(name))
            )
            if slide_numbers != list(range(1, expected_slides + 1)):
                return ArtifactCheck(False, f"presentation slides are {slide_numbers!r}")
            note_numbers = sorted(
                int(match.group(1))
                for name in archive.namelist()
                if (match := NOTES_RE.fullmatch(name))
            )
            if note_numbers != slide_numbers:
                return ArtifactCheck(False, f"presentation notes are for slides {note_numbers!r}")
            notes = [f"ppt/notesSlides/notesSlide{number}.xml" for number in note_numbers]
            empty_notes = [name for name in notes if len(_part_text(archive, name)) < 10]
            if empty_notes:
                return ArtifactCheck(False, f"speaker notes are empty: {', '.join(empty_notes)}")
            charts = sorted(
                name
                for name in archive.namelist()
                if name.startswith("ppt/charts/chart") and name.endswith(".xml")
            )
            if not charts:
                return ArtifactCheck(False, "presentation contains no chart")
            chart_roots = [_xml(archive, name) for name in charts]
            chart_values = tuple(
                value
                for root in chart_roots
                for node in root.findall(f".//{{{CHART_NS}}}v")
                if (value := _number(node.text)) is not None
            )
            if not _contains_numbers(chart_values, expected_revenue):
                return ArtifactCheck(False, "presentation chart lacks the requested revenue data")
            if not any(root.findall(f".//{{{CHART_NS}}}ser") for root in chart_roots):
                return ArtifactCheck(False, "presentation chart has no editable series")
            embeddings = [
                name
                for name in archive.namelist()
                if name.startswith("ppt/embeddings/") and name.endswith(".xlsx")
            ]
            if not embeddings:
                return ArtifactCheck(False, "presentation chart has no embedded workbook")
            if not any(
                _embedded_workbook_has_revenue(archive, name, expected_revenue)
                for name in embeddings
            ):
                return ArtifactCheck(
                    False, "presentation chart has no valid embedded revenue workbook"
                )
    except (ArtifactInvalid, BadZipFile, KeyError, ElementTree.ParseError) as error:
        return ArtifactCheck(False, f"invalid presentation: {error}")
    return ArtifactCheck(
        True, f"presentation has {expected_slides} noted slides and editable chart"
    )


def _bounded_paths(entries: Iterable[tuple[str, int]]) -> None:
    total = 0
    for count, (raw_name, size) in enumerate(entries, 1):
        if count > MAX_ARCHIVE_ENTRIES:
            raise ArtifactInvalid("archive has too many entries")
        path = PurePosixPath(raw_name)
        if path.is_absolute() or ".." in path.parts:
            raise ArtifactInvalid(f"unsafe path {raw_name!r}")
        if size < 0:
            raise ArtifactInvalid(f"entry {raw_name!r} has a negative size")
        total += size
    if total > MAX_EXPANDED_BYTES:
        raise ArtifactInvalid("expanded content exceeds the inspection limit")


def _bounded_zip(archive: ZipFile) -> None:
    infos = archive.infolist()
    _bounded_paths((info.filename, info.file_size) for info in infos)
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ArtifactInvalid("archive has duplicate part names")


def _xml(archive: ZipFile, name: str) -> ElementTree.Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_PART_BYTES:
        raise ArtifactInvalid(f"part {name!r} exceeds the inspection limit")
    return ElementTree.fromstring(archive.read(info))


def _sheet_paths(archive: ZipFile) -> dict[str, str]:
    workbook = _xml(archive, "xl/workbook.xml")
    relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }
    paths: dict[str, str] = {}
    for sheet in workbook.findall(f".//{{{SPREADSHEET_NS}}}sheet"):
        relation = sheet.attrib.get(f"{{{OFFICE_REL_NS}}}id")
        if relation is not None and relation in targets:
            target = targets[relation]
            paths[sheet.attrib["name"]] = (
                target.lstrip("/") if target.startswith("/") else normpath(f"xl/{target}")
            )
    return paths


def _sheet_numbers(root: ElementTree.Element) -> tuple[float, ...]:
    values: list[float] = []
    for cell in root.findall(f".//{{{SPREADSHEET_NS}}}c"):
        if cell.attrib.get("t") in {"e", "inlineStr", "s", "str"}:
            continue
        value = cell.find(f"{{{SPREADSHEET_NS}}}v")
        parsed = _number(value.text if value is not None else None)
        if parsed is not None:
            values.append(parsed)
    return tuple(values)


def _growth_formula_cells(root: ElementTree.Element) -> tuple[tuple[str, float | None], ...]:
    formulas: list[tuple[str, float | None]] = []
    for cell in root.findall(f".//{{{SPREADSHEET_NS}}}c"):
        formula = cell.find(f"{{{SPREADSHEET_NS}}}f")
        if formula is None or formula.text is None or "/" not in formula.text:
            continue
        value = cell.find(f"{{{SPREADSHEET_NS}}}v")
        formulas.append((formula.text, _number(value.text if value is not None else None)))
    return tuple(formulas)


def _linked_chart(archive: ZipFile, name: str) -> bool:
    root = _xml(archive, name)
    if not root.findall(f".//{{{CHART_NS}}}ser"):
        return False
    references = [
        node.text.lower() for node in root.findall(f".//{{{CHART_NS}}}f") if node.text is not None
    ]
    return any("inputs" in reference or "forecast" in reference for reference in references)


def _part_text(archive: ZipFile, name: str) -> str:
    root = _xml(archive, name)
    return " ".join(
        node.text.strip()
        for node in root.findall(f".//{{{DRAWING_NS}}}t")
        if node.text and node.text.strip()
    )


def _embedded_workbook_has_revenue(
    presentation: ZipFile, name: str, expected_revenue: tuple[int, ...]
) -> bool:
    info = presentation.getinfo(name)
    if info.file_size > MAX_PART_BYTES:
        return False
    try:
        with ZipFile(BytesIO(presentation.read(info))) as workbook:
            _bounded_zip(workbook)
            sheets = [
                part
                for part in workbook.namelist()
                if part.startswith("xl/worksheets/") and part.endswith(".xml")
            ]
            numbers = tuple(
                number for sheet in sheets for number in _sheet_numbers(_xml(workbook, sheet))
            )
    except (ArtifactInvalid, BadZipFile, KeyError, ElementTree.ParseError):
        return False
    return _contains_numbers(numbers, expected_revenue)


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _contains_numbers(actual: tuple[float, ...], expected: tuple[int | float, ...]) -> bool:
    return all(any(abs(value - target) < 0.0001 for value in actual) for target in expected)
