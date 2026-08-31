import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from evals.memory_100.build import SELECTION_FILE, Selection
from evals.memory_ingestion.assets import LOCOMO, LONGMEM_CLEANED
from evals.memory_ingestion.models import (
    IngestionCase,
    IngestionManifest,
    IngestionPage,
    UpstreamAsset,
    content_digest,
    write_snapshot,
)
from ufo.sdk.context import JsonValue

DATA_DIR = Path(__file__).parent / "data"
THIRD_PARTY_NOTICES_FILE = DATA_DIR / "THIRD_PARTY_NOTICES.md"
HASH_CHUNK_BYTES = 1024 * 1024
PAGE_CONTENT_CHARS = 7_000
LOCOMO_COUNTS = {1: 18, 2: 18, 3: 17, 4: 17}
LOCOMO_CATEGORIES = {
    1: "multi_hop",
    2: "temporal_reasoning",
    3: "open_domain",
    4: "information_extraction",
}
DIALOG_ID = re.compile(r"^D([1-9][0-9]*):([1-9][0-9]*)$")


class LongMemTurn(BaseModel):
    role: str = Field(min_length=1)
    content: str
    has_answer: bool = False


class LongMemQuestion(BaseModel):
    question_id: str = Field(min_length=1)
    question_type: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str | int | float | bool
    question_date: str = Field(min_length=1)
    haystack_dates: tuple[str, ...]
    haystack_session_ids: tuple[str, ...]
    haystack_sessions: tuple[tuple[LongMemTurn, ...], ...]
    answer_session_ids: tuple[str, ...]


class LoCoMoQuestion(BaseModel):
    question: str = Field(min_length=1)
    answer: str | int | float | None = None
    evidence: tuple[str, ...]
    category: int = Field(ge=1, le=5)


class LoCoMoTurn(BaseModel):
    speaker: str = Field(min_length=1)
    dia_id: str = Field(min_length=1)
    text: str = ""
    blip_caption: str = ""


class LoCoMoSample(BaseModel):
    sample_id: str = Field(min_length=1)
    qa: tuple[LoCoMoQuestion, ...]
    conversation: dict[str, JsonValue]


@dataclass(frozen=True)
class _LoCoMoCandidate:
    id: str
    sample: LoCoMoSample
    index: int
    question: LoCoMoQuestion


@dataclass(frozen=True)
class MemoryIngestionBuilder:
    longmem: Path
    locomo: Path
    output: Path
    longmem_asset: UpstreamAsset = field(default_factory=lambda: LONGMEM_CLEANED)
    locomo_asset: UpstreamAsset = field(default_factory=lambda: LOCOMO)
    selection_file: Path = SELECTION_FILE
    notices_file: Path = THIRD_PARTY_NOTICES_FILE

    def run(self, case_ids: tuple[str, ...] = (), samples: int = 1) -> IngestionManifest:
        """Build the deterministic 100-case corpus or an exact named subset, each case scored over
        `samples` recall attempts."""
        verify_asset(self.longmem, self.longmem_asset)
        verify_asset(self.locomo, self.locomo_asset)
        selection = Selection.model_validate_json(self.selection_file.read_bytes()).longmem
        longmem_cases, longmem_pages = _longmem(self.longmem, dict(selection))
        locomo_cases, locomo_pages = _locomo(self.locomo, LOCOMO_COUNTS)
        cases = tuple(sorted((*longmem_cases, *locomo_cases), key=lambda item: item.id))
        pages = tuple(sorted((*longmem_pages, *locomo_pages), key=lambda item: item.source_ref))
        if len(cases) != 100:
            raise ValueError(f"memory_ingestion selection requires 100 cases, found {len(cases)}")
        if case_ids:
            requested = set(case_ids)
            unknown = sorted(requested - {case.id for case in cases})
            if unknown:
                raise ValueError(f"unknown memory_ingestion cases: {', '.join(unknown)}")
            cases = tuple(case for case in cases if case.id in requested)
            evidence = {ref for case in cases for ref in case.evidence_refs}
            pages = tuple(page for page in pages if page.evidence_ref in evidence)
        if samples != 1:
            cases = tuple(case.model_copy(update={"samples": samples}) for case in cases)
        manifest = write_snapshot(
            self.output,
            upstreams=(self.longmem_asset, self.locomo_asset),
            builder_digest=_builder_digest(self.selection_file, self.notices_file),
            cases=cases,
            pages=pages,
        )
        shutil.copyfile(self.notices_file, self.output / THIRD_PARTY_NOTICES_FILE.name)
        return manifest


def verify_asset(path: Path, asset: UpstreamAsset) -> None:
    """Verify one pinned upstream file before parsing it."""
    stat = path.stat()
    if stat.st_size != asset.size_bytes:
        raise ValueError(
            f"{asset.name} size mismatch: expected {asset.size_bytes}, found {stat.st_size}"
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    found = f"sha256:{digest.hexdigest()}"
    if found != asset.sha256:
        raise ValueError(f"{asset.name} digest mismatch: expected {asset.sha256}, found {found}")


def _longmem(
    path: Path, selected: dict[str, tuple[str, ...]]
) -> tuple[tuple[IngestionCase, ...], tuple[IngestionPage, ...]]:
    questions = tuple(
        LongMemQuestion.model_validate(item) for item in json.loads(path.read_bytes())
    )
    by_id = {question.question_id: question for question in questions}
    category_by_id = {
        question_id: category for category, ids in selected.items() for question_id in ids
    }
    selected_ids = tuple(question_id for ids in selected.values() for question_id in ids)
    if len(selected_ids) != 30 or len(set(selected_ids)) != 30:
        raise ValueError("matched LongMem selection must contain 30 unique questions")
    missing = sorted(set(selected_ids) - by_id.keys())
    if missing:
        raise ValueError(f"matched LongMem selection is missing questions: {', '.join(missing)}")
    cases: list[IngestionCase] = []
    pages: list[IngestionPage] = []
    for question_id in selected_ids:
        question = by_id[question_id]
        if not (
            len(question.haystack_dates)
            == len(question.haystack_session_ids)
            == len(question.haystack_sessions)
        ):
            raise ValueError(f"LongMem case {question_id} has misaligned session fields")
        sessions = {
            session_id: (date, turns)
            for date, session_id, turns in zip(
                question.haystack_dates,
                question.haystack_session_ids,
                question.haystack_sessions,
                strict=True,
            )
        }
        answer_session_ids = (
            () if question.question_id.endswith("_abs") else question.answer_session_ids
        )
        evidence_refs = tuple(
            f"longmem/{question_id}/session/{session_id}" for session_id in answer_session_ids
        )
        cases.append(
            IngestionCase(
                id=f"longmem/{question_id}",
                corpus="longmem",
                category=category_by_id[question_id],
                question=f"Question date: {question.question_date}\n\n{question.question}",
                expected_answer=str(question.answer),
                evidence_refs=evidence_refs,
            )
        )
        for session_id, evidence_ref in zip(answer_session_ids, evidence_refs, strict=True):
            if session_id not in sessions:
                raise ValueError(f"LongMem case {question_id} is missing session {session_id}")
            date, turns = sessions[session_id]
            answer_turns = tuple(
                (index, turn) for index, turn in enumerate(turns) if turn.has_answer
            )
            if not answer_turns:
                raise ValueError(
                    f"LongMem evidence session {question_id}/{session_id} has no answer turn"
                )
            for turn_index, turn in answer_turns:
                pages.extend(
                    _turn_pages(
                        source_prefix=f"longmem/{question_id}/{session_id}/{turn_index:02d}",
                        evidence_ref=evidence_ref,
                        date=date,
                        speaker=turn.role,
                        content=turn.content,
                        origin=f"longmem:{question_id}",
                    )
                )
    return tuple(cases), tuple(pages)


def _locomo(
    path: Path, counts: dict[int, int]
) -> tuple[tuple[IngestionCase, ...], tuple[IngestionPage, ...]]:
    samples = tuple(LoCoMoSample.model_validate(item) for item in json.loads(path.read_bytes()))
    candidates: dict[int, list[_LoCoMoCandidate]] = {category: [] for category in counts}
    for sample in samples:
        for index, question in enumerate(sample.qa):
            if question.category not in counts:
                continue
            case_id = f"locomo/{sample.sample_id}/{index:03d}"
            candidates[question.category].append(_LoCoMoCandidate(case_id, sample, index, question))
    chosen = tuple(
        candidate
        for category, count in counts.items()
        for candidate in sorted(
            candidates[category], key=lambda item: _rank("memory_ingestion/locomo", item.id)
        )[:count]
    )
    if len(chosen) != sum(counts.values()):
        raise ValueError("LoCoMo selection does not satisfy its category counts")
    cases: list[IngestionCase] = []
    pages: dict[str, IngestionPage] = {}
    for candidate in chosen:
        if candidate.question.answer is None:
            raise ValueError(f"LoCoMo case {candidate.id} has no answer")
        evidence_refs = tuple(
            f"locomo/{candidate.sample.sample_id}/dialog/{dialog_id}"
            for dialog_id in candidate.question.evidence
        )
        cases.append(
            IngestionCase(
                id=candidate.id,
                corpus="locomo",
                category=LOCOMO_CATEGORIES[candidate.question.category],
                question=candidate.question.question,
                expected_answer=str(candidate.question.answer),
                evidence_refs=evidence_refs,
            )
        )
        for dialog_id, evidence_ref in zip(candidate.question.evidence, evidence_refs, strict=True):
            date, turn = _locomo_turn(candidate.sample, dialog_id)
            content = turn.text
            if turn.blip_caption and turn.blip_caption not in content:
                content = f"{content}\nImage: {turn.blip_caption}".strip()
            derived = _turn_pages(
                source_prefix=(
                    f"locomo/{candidate.sample.sample_id}/{dialog_id.replace(':', '-')}"
                ),
                evidence_ref=evidence_ref,
                date=date,
                speaker=turn.speaker,
                content=content,
                origin=f"locomo:{candidate.sample.sample_id}",
            )
            for page in derived:
                previous = pages.setdefault(page.source_ref, page)
                if previous != page:
                    raise ValueError(f"LoCoMo page {page.source_ref!r} has conflicting bodies")
    return tuple(cases), tuple(pages.values())


def _locomo_turn(sample: LoCoMoSample, dialog_id: str) -> tuple[str, LoCoMoTurn]:
    matched = DIALOG_ID.fullmatch(dialog_id)
    if matched is None:
        raise ValueError(f"LoCoMo sample {sample.sample_id} has invalid dialog id {dialog_id!r}")
    session_number = matched.group(1)
    raw_turns = sample.conversation.get(f"session_{session_number}")
    raw_date = sample.conversation.get(f"session_{session_number}_date_time")
    if not isinstance(raw_turns, list) or not isinstance(raw_date, str):
        raise ValueError(f"LoCoMo sample {sample.sample_id} is missing session {session_number}")
    turns = tuple(LoCoMoTurn.model_validate(turn) for turn in raw_turns)
    found = tuple(turn for turn in turns if turn.dia_id == dialog_id)
    if len(found) != 1:
        raise ValueError(
            f"LoCoMo sample {sample.sample_id} dialog {dialog_id!r} matched {len(found)} turns"
        )
    return raw_date, found[0]


def _turn_pages(
    *,
    source_prefix: str,
    evidence_ref: str,
    date: str,
    speaker: str,
    content: str,
    origin: str,
) -> tuple[IngestionPage, ...]:
    if not content:
        raise ValueError(f"memory_ingestion evidence {evidence_ref!r} has no text")
    parts = tuple(
        content[offset : offset + PAGE_CONTENT_CHARS]
        for offset in range(0, len(content), PAGE_CONTENT_CHARS)
    )
    return tuple(
        IngestionPage(
            source_ref=f"{source_prefix}/{index:02d}.txt",
            evidence_ref=evidence_ref,
            body=(body := f"Session date: {date}\n{speaker}: {part}"),
            digest=content_digest(body),
            origin=origin,
        )
        for index, part in enumerate(parts)
    )


def _rank(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()


def _builder_digest(selection_file: Path, notices_file: Path) -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        Path(__file__).with_name("assets.py"),
        Path(__file__).with_name("models.py"),
        selection_file,
        notices_file,
    ):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.memory_ingestion.build")
    parser.add_argument("--longmem", type=Path, required=True)
    parser.add_argument("--locomo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[], metavar="CASE_ID")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    manifest = MemoryIngestionBuilder(args.longmem, args.locomo, args.output).run(tuple(args.case))
    print(f"{manifest.digest} {manifest.cases.records} cases {manifest.pages.records} pages")


if __name__ == "__main__":
    main()
