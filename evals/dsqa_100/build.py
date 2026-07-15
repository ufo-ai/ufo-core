import argparse
import csv
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field

from evals.dsqa_100.models import (
    SOURCE_ROWS,
    SUBSET_SIZE,
    AnswerType,
    Category,
    PressureBand,
    PromptSignals,
    Selection,
    SelectionItem,
    SnapshotCase,
    SnapshotManifest,
    UpstreamAsset,
)
from evals.dsqa_100.snapshot import canonical_json, content_digest, write_snapshot

DATA_DIR = Path(__file__).parent / "data"
SELECTION_FILE = DATA_DIR / "selection.json"
THIRD_PARTY_NOTICES_FILE = DATA_DIR / "THIRD_PARTY_NOTICES.md"
CSV_MEMBER = "DSQA-full.csv"
SOURCE_ANCHOR_CAP = 2
STAGE_MARKER_CAP = 3
REPEATED_SIGNAL_WEIGHT = 2
LONG_PROMPT_WORDS = 45
VERY_LONG_PROMPT_WORDS = 75

UPSTREAM = UpstreamAsset(
    url=(
        "https://www.kaggle.com/api/v1/datasets/download/deepmind/"
        "deepsearchqa?datasetVersionNumber=4"
    ),
    archive_size_bytes=133_197,
    archive_sha256="sha256:e33ad7532a141152138fc5a37fcb35db604acd9c8d195339075123b6375e790f",
    csv_size_bytes=358_607,
    csv_sha256="sha256:cc4394663f2fa9af042327d9c6d53767df1ed85c9aaef9ed11fe6458b6133368",
)

SOURCE_ANCHOR = re.compile(
    r"\b(?:according to|based on|refer(?:ring)? to|as (?:reported|listed|"
    r"recorded|published|provided) (?:by|in|on)|data (?:from|published by)|"
    r"statistics from)\b",
    re.IGNORECASE,
)
STAGE_MARKER = re.compile(
    r"\b(?:of those|out of (?:these|those)|among (?:these|those)|which of "
    r"(?:these|those)|then|finally|filter(?:ing|ed)?|for each|from (?:that|"
    r"this|these|those) (?:list|set|group|result|year|country|countries))\b",
    re.IGNORECASE,
)
TEMPORAL_YEAR = re.compile(r"\b(?:18|19|20)\d{2}\b", re.IGNORECASE)
TEMPORAL_RANGE = re.compile(r"\b(?:as of|up to and including|between .{0,30} and)\b", re.IGNORECASE)
NUMERIC_SIGNAL = re.compile(
    r"(?:\b\d+(?:\.\d+)?\b|%|\$|£|€|\bpercent(?:age)?\b|\bper\s+\d)", re.IGNORECASE
)
EXCLUSION_SIGNAL = re.compile(
    r"\b(?:exclude|excluding|except|without|do not include|don't include|"
    r"not including|discard|did not|does not|no longer|other than)\b",
    re.IGNORECASE,
)
RANKING_SIGNAL = re.compile(
    r"\b(?:top\s+\d+|bottom\s+\d+|highest|lowest|largest|smallest|least|"
    r"most|rank(?:ed|ing)?|average|median|maximum|minimum)\b",
    re.IGNORECASE,
)
ENUMERATION_SIGNAL = re.compile(
    r"\b(?:list|all|every|each|complete list|names?|years?|countries|which\s+\d+)\b",
    re.IGNORECASE,
)
FORMAT_SIGNAL = re.compile(
    r"\b(?:alphabetical order|chronological order|comma[- ]separated|"
    r"no additional text|just give|provide only|format)\b",
    re.IGNORECASE,
)


class SourceRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    example_id: int = Field(ge=0, lt=SOURCE_ROWS)
    problem: str = Field(min_length=1)
    problem_category: Category
    answer: str = Field(min_length=1)
    answer_type: AnswerType


@dataclass(frozen=True)
class Candidate:
    row: SourceRow
    problem_sha256: str
    signals: PromptSignals
    tool_pressure_score: int
    prompt_word_count: int


@dataclass(frozen=True)
class DSQA100Builder:
    archive: Path
    output: Path

    def build(self) -> SnapshotManifest:
        rows = _read_source(self.archive)
        selected = _select(rows)
        expected = Selection.model_validate_json(SELECTION_FILE.read_bytes())
        actual = Selection(items=selected)
        if actual != expected:
            raise ValueError("computed dsqa_100 selection does not match data/selection.json")
        by_id = {row.example_id: row for row in rows}
        cases = tuple(
            SnapshotCase(
                example_id=item.example_id,
                problem=by_id[item.example_id].problem,
                problem_sha256=item.problem_sha256,
                category=item.category,
                answer=by_id[item.example_id].answer,
                answer_type=by_id[item.example_id].answer_type,
                pressure_band=item.pressure_band,
                tool_pressure_score=item.tool_pressure_score,
                prompt_word_count=item.prompt_word_count,
                signals=item.signals,
            )
            for item in selected
        )
        return write_snapshot(
            self.output,
            upstream=UPSTREAM,
            builder_digest=_builder_digest(expected),
            cases=cases,
        )


def _read_source(archive: Path) -> tuple[SourceRow, ...]:
    body = archive.read_bytes()
    _verify_bytes(
        "DeepSearchQA archive",
        body,
        UPSTREAM.archive_size_bytes,
        UPSTREAM.archive_sha256,
    )
    try:
        with ZipFile(archive) as bundle:
            if bundle.namelist() != [CSV_MEMBER]:
                raise ValueError("DeepSearchQA archive must contain only DSQA-full.csv")
            csv_body = bundle.read(CSV_MEMBER)
    except BadZipFile as error:
        raise ValueError("DeepSearchQA archive is not a valid zip file") from error
    _verify_bytes("DSQA-full.csv", csv_body, UPSTREAM.csv_size_bytes, UPSTREAM.csv_sha256)
    reader = csv.DictReader(StringIO(csv_body.decode("utf-8-sig")), strict=True)
    expected_fields = ["example_id", "problem", "problem_category", "answer", "answer_type"]
    if reader.fieldnames != expected_fields:
        raise ValueError(f"DSQA-full.csv fields are invalid: {reader.fieldnames}")
    rows = tuple(SourceRow.model_validate(row) for row in reader)
    if len(rows) != SOURCE_ROWS:
        raise ValueError(f"DSQA-full.csv requires {SOURCE_ROWS} rows, found {len(rows)}")
    ids = [row.example_id for row in rows]
    if ids != list(range(SOURCE_ROWS)):
        raise ValueError("DSQA-full.csv example ids must be ordered 0 through 899")
    return rows


def _verify_bytes(name: str, body: bytes, size: int, digest: str) -> None:
    if len(body) != size:
        raise ValueError(f"{name} has {len(body)} bytes, expected {size}")
    actual = f"sha256:{hashlib.sha256(body).hexdigest()}"
    if actual != digest:
        raise ValueError(f"{name} digest {actual} does not match {digest}")


def _features(problem: str) -> tuple[PromptSignals, int, int]:
    signals = PromptSignals(
        source_anchor_count=len(SOURCE_ANCHOR.findall(problem)),
        stage_marker_count=len(STAGE_MARKER.findall(problem)),
        temporal_anchor_count=len(TEMPORAL_YEAR.findall(problem))
        + len(TEMPORAL_RANGE.findall(problem)),
        numeric_signal_count=len(NUMERIC_SIGNAL.findall(problem)),
        exclusion_signal_count=len(EXCLUSION_SIGNAL.findall(problem)),
        ranking_signal_count=len(RANKING_SIGNAL.findall(problem)),
        enumeration_signal_count=len(ENUMERATION_SIGNAL.findall(problem)),
        format_signal_count=len(FORMAT_SIGNAL.findall(problem)),
    )
    words = len(problem.split())
    score = (
        min(signals.source_anchor_count, SOURCE_ANCHOR_CAP) * REPEATED_SIGNAL_WEIGHT
        + min(signals.stage_marker_count, STAGE_MARKER_CAP) * REPEATED_SIGNAL_WEIGHT
        + int(signals.temporal_anchor_count > 0)
        + int(signals.numeric_signal_count > 0)
        + int(signals.exclusion_signal_count > 0)
        + int(signals.ranking_signal_count > 0)
        + int(signals.enumeration_signal_count > 0)
        + int(signals.format_signal_count > 0)
        + int(words >= LONG_PROMPT_WORDS)
        + int(words >= VERY_LONG_PROMPT_WORDS)
    )
    return signals, score, words


def _quotas(category_counts: Counter[Category]) -> dict[Category, int]:
    total = sum(category_counts.values())
    exact = {category: SUBSET_SIZE * count / total for category, count in category_counts.items()}
    quotas = {category: max(1, math.floor(value)) for category, value in exact.items()}
    while sum(quotas.values()) < SUBSET_SIZE:
        category = max(
            category_counts,
            key=lambda name: (exact[name] - quotas[name], category_counts[name], name),
        )
        quotas[category] += 1
    while sum(quotas.values()) > SUBSET_SIZE:
        eligible = [name for name, quota in quotas.items() if quota > 1]
        category = min(
            eligible,
            key=lambda name: (exact[name] - quotas[name], -category_counts[name], name),
        )
        quotas[category] -= 1
    return quotas


def _select(rows: tuple[SourceRow, ...]) -> tuple[SelectionItem, ...]:
    category_counts: Counter[Category] = Counter(row.problem_category for row in rows)
    quotas = _quotas(category_counts)
    candidates = tuple(_candidate(row) for row in rows)
    odd = sorted(
        (category for category, quota in quotas.items() if quota % 2),
        key=lambda category: (-category_counts[category], category),
    )
    higher_extra = set(odd[::2])
    lower_extra = set(odd[1::2])
    if len(higher_extra) != len(lower_extra):
        raise ValueError("DSQA category quotas cannot split into equal pressure bands")
    selected: list[SelectionItem] = []
    for category in sorted(category_counts):
        quota = quotas[category]
        category_rows = [item for item in candidates if item.row.problem_category == category]
        lower_count = quota // 2 + int(category in lower_extra)
        higher_count = quota // 2 + int(category in higher_extra)
        ascending = sorted(
            category_rows,
            key=lambda item: (
                item.tool_pressure_score,
                item.prompt_word_count,
                item.problem_sha256,
            ),
        )
        lower = ascending[:lower_count]
        lower_ids = {item.row.example_id for item in lower}
        higher = sorted(
            (item for item in category_rows if item.row.example_id not in lower_ids),
            key=lambda item: (
                -item.tool_pressure_score,
                -item.prompt_word_count,
                item.problem_sha256,
            ),
        )[:higher_count]
        selected.extend(_selection_item(item, "lower") for item in lower)
        selected.extend(_selection_item(item, "higher") for item in higher)
    selected.sort(key=lambda item: item.example_id)
    if len(selected) != SUBSET_SIZE or len({item.example_id for item in selected}) != SUBSET_SIZE:
        raise ValueError(f"DSQA selection must contain {SUBSET_SIZE} unique ids")
    bands = Counter(item.pressure_band for item in selected)
    if bands != {"lower": 50, "higher": 50}:
        raise ValueError(f"DSQA pressure band counts are invalid: {dict(bands)}")
    return tuple(selected)


def _candidate(row: SourceRow) -> Candidate:
    signals, score, words = _features(row.problem)
    return Candidate(
        row=row,
        problem_sha256=content_digest(row.problem),
        signals=signals,
        tool_pressure_score=score,
        prompt_word_count=words,
    )


def _selection_item(candidate: Candidate, band: PressureBand) -> SelectionItem:
    return SelectionItem(
        example_id=candidate.row.example_id,
        pressure_band=band,
        category=candidate.row.problem_category,
        problem_sha256=candidate.problem_sha256,
        tool_pressure_score=candidate.tool_pressure_score,
        prompt_word_count=candidate.prompt_word_count,
        signals=candidate.signals,
    )


def _builder_digest(selection: Selection) -> str:
    policy = (
        Path(__file__).read_bytes()
        + canonical_json(selection)
        + THIRD_PARTY_NOTICES_FILE.read_bytes()
    )
    return f"sha256:{hashlib.sha256(policy).hexdigest()}"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.dsqa_100.build")
    parser.add_argument("--dataset-archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = DSQA100Builder(args.dataset_archive, args.out).build()
    print(manifest.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
