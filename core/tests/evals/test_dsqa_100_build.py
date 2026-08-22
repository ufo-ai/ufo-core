import csv
import gzip
import hashlib
from collections import Counter
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import evals.dsqa_100.build as dsqa_build
from evals.dsqa_100.build import DSQA100Builder, SourceRow, _features, _select
from evals.dsqa_100.models import Selection, SnapshotCase, UpstreamAsset
from evals.dsqa_100.snapshot import (
    CASES_FILE,
    EXPECTED_CATEGORIES,
    content_digest,
    load_snapshot,
    write_snapshot,
)

DIGEST = "sha256:" + "a" * 64


def _source_rows() -> tuple[SourceRow, ...]:
    rows: list[SourceRow] = []
    example_id = 0
    for category, selected_count in EXPECTED_CATEGORIES.items():
        for _ in range(selected_count * 9):
            rows.append(
                SourceRow(
                    example_id=example_id,
                    problem=f"List the result for {category} record {example_id} in 2025.",
                    problem_category=category,
                    answer=f"secret answer {example_id}",
                    answer_type="Single Answer",
                )
            )
            example_id += 1
    selected = _select(tuple(rows))
    set_ids = {item.example_id for item in selected[:66]}
    return tuple(
        row.model_copy(
            update={"answer_type": "Set Answer" if row.example_id in set_ids else "Single Answer"}
        )
        for row in rows
    )


def _csv_body(rows: tuple[SourceRow, ...]) -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=["example_id", "problem", "problem_category", "answer", "answer_type"],
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row.model_dump(mode="json"))
    return output.getvalue().encode()


def _archive(body: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("DSQA-full.csv", body)
    return output.getvalue()


def _case(item, answer_type: str) -> SnapshotCase:
    problem = f"problem {item.example_id}"
    return SnapshotCase(
        example_id=item.example_id,
        problem=problem,
        problem_sha256=content_digest(problem),
        category=item.category,
        answer=f"answer {item.example_id}",
        answer_type=answer_type,
        pressure_band=item.pressure_band,
        tool_pressure_score=item.tool_pressure_score,
        prompt_word_count=item.prompt_word_count,
        signals=item.signals,
    )


def test_dsqa_builder_verifies_selects_and_writes_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _source_rows()
    selected = _select(rows)
    selection = Selection(items=selected)
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(selection.model_dump_json(indent=2))
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    notices.write_text("DeepSearchQA test notice\n")
    csv_body = _csv_body(rows)
    archive_body = _archive(csv_body)
    archive_path = tmp_path / "deepsearchqa.zip"
    archive_path.write_bytes(archive_body)
    upstream = UpstreamAsset(
        url="https://example.invalid/deepsearchqa.zip",
        archive_size_bytes=len(archive_body),
        archive_sha256=f"sha256:{hashlib.sha256(archive_body).hexdigest()}",
        csv_size_bytes=len(csv_body),
        csv_sha256=f"sha256:{hashlib.sha256(csv_body).hexdigest()}",
    )
    monkeypatch.setattr(dsqa_build, "SELECTION_FILE", selection_path)
    monkeypatch.setattr(dsqa_build, "THIRD_PARTY_NOTICES_FILE", notices)
    monkeypatch.setattr(dsqa_build, "UPSTREAM", upstream)

    first = DSQA100Builder(archive_path, tmp_path / "first").build()
    second = DSQA100Builder(archive_path, tmp_path / "second").build()

    assert first == second
    assert (tmp_path / "first" / CASES_FILE).read_bytes() == (
        tmp_path / "second" / CASES_FILE
    ).read_bytes()
    snapshot = load_snapshot(tmp_path / "first")
    assert len(snapshot.cases) == 100
    assert Counter(case.answer_type for case in snapshot.cases) == {
        "Set Answer": 66,
        "Single Answer": 34,
    }


def test_dsqa_selection_ignores_answers_and_answer_types() -> None:
    rows = _source_rows()
    changed = tuple(
        row.model_copy(
            update={
                "answer": f"different {row.example_id}",
                "answer_type": "Set Answer"
                if row.answer_type == "Single Answer"
                else "Single Answer",
            }
        )
        for row in rows
    )

    assert _select(rows) == _select(changed)


def test_dsqa_prompt_pressure_features_are_explicit() -> None:
    signals, score, words = _features(
        "According to the 2024 report, list every top 10 result, excluding examples, then format "
        "the answer in chronological order."
    )

    assert words == 19
    assert signals.source_anchor_count == 1
    assert signals.stage_marker_count == 1
    assert signals.temporal_anchor_count == 1
    assert signals.exclusion_signal_count == 1
    assert signals.ranking_signal_count == 1
    assert signals.enumeration_signal_count == 2
    assert signals.format_signal_count == 2
    assert score == 10


def test_dsqa_snapshot_rejects_tampered_cases(tmp_path: Path) -> None:
    selection = Selection.model_validate_json(dsqa_build.SELECTION_FILE.read_bytes())
    cases = tuple(
        _case(item, "Set Answer" if index < 66 else "Single Answer")
        for index, item in enumerate(selection.items)
    )
    write_snapshot(tmp_path, upstream=dsqa_build.UPSTREAM, builder_digest=DIGEST, cases=cases)
    payload = gzip.decompress((tmp_path / CASES_FILE).read_bytes()) + b"\n"
    (tmp_path / CASES_FILE).write_bytes(gzip.compress(payload, mtime=0))

    with pytest.raises(ValueError, match="digest does not match"):
        load_snapshot(tmp_path)
