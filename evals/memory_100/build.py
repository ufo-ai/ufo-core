import argparse
import hashlib
import heapq
import json
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, Field

from evals.memory_100.assets import (
    ENTERPRISE_DOCUMENTS,
    ENTERPRISE_OVERVIEW,
    ENTERPRISE_QUESTIONS,
    LONGMEM_CLEANED,
    UPSTREAMS,
)
from evals.memory_100.models import (
    SnapshotCase,
    SnapshotManifest,
    SnapshotMemory,
    SnapshotPage,
    UpstreamAsset,
)
from evals.memory_100.snapshot import (
    CORPUS_COUNTS,
    NULL_CHARACTER,
    canonical_json,
    content_digest,
    write_snapshot,
)

DATA_DIR = Path(__file__).parent / "data"
SELECTION_FILE = DATA_DIR / "selection.json"
UFO_CASES_FILE = DATA_DIR / "ufo_cases.json"
THIRD_PARTY_NOTICES_FILE = DATA_DIR / "THIRD_PARTY_NOTICES.md"
DOCUMENT_ID = re.compile(r"(dsid_[0-9a-f]{32})")
TOKEN = re.compile(r"[a-z0-9]{3,}")
SOURCE_TYPES = (
    "confluence",
    "fireflies",
    "github",
    "gmail",
    "google_drive",
    "hubspot",
    "jira",
    "linear",
    "slack",
)
STOPWORDS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "has",
        "have",
        "how",
        "its",
        "that",
        "the",
        "their",
        "this",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)
LEXICAL_NEGATIVES_PER_CASE = 75
METADATA_NEGATIVES_PER_CASE = 75
BACKGROUND_PER_SOURCE = 500
HASH_CHUNK_BYTES = 1024 * 1024
ENTERPRISE_OVERVIEW_SOURCE_REF = "enterprise/company_overview.md"
NULL_REPLACEMENT = "\ufffd"


class EnterpriseQuestion(BaseModel):
    question_id: str
    question_type: str
    source_types: tuple[str, ...]
    question: str
    expected_doc_ids: tuple[str, ...]
    gold_answer: str
    answer_facts: tuple[str, ...]


class LongMemTurn(BaseModel):
    role: str
    content: str


class LongMemQuestion(BaseModel):
    question_id: str
    question_type: str
    question: str
    answer: str | int | float | bool
    question_date: str = Field(min_length=1)
    haystack_dates: tuple[str, ...]
    haystack_session_ids: tuple[str, ...]
    haystack_sessions: tuple[tuple[LongMemTurn, ...], ...]
    answer_session_ids: tuple[str, ...]


class EnterpriseSelection(BaseModel):
    basic: tuple[str, ...] = Field(min_length=4, max_length=4)
    semantic: tuple[str, ...] = Field(min_length=8, max_length=8)
    intra_document_reasoning: tuple[str, ...] = Field(min_length=6, max_length=6)
    project_related: tuple[str, ...] = Field(min_length=8, max_length=8)
    constrained: tuple[str, ...] = Field(min_length=7, max_length=7)
    conflicting_info: tuple[str, ...] = Field(min_length=7, max_length=7)
    completeness: tuple[str, ...] = Field(min_length=7, max_length=7)
    miscellaneous: tuple[str, ...] = Field(min_length=4, max_length=4)
    high_level: tuple[str, ...] = Field(min_length=4, max_length=4)
    info_not_found: tuple[str, ...] = Field(min_length=5, max_length=5)


class LongMemSelection(BaseModel):
    information_extraction: tuple[str, ...] = Field(min_length=6, max_length=6)
    multi_session: tuple[str, ...] = Field(min_length=6, max_length=6)
    knowledge_update: tuple[str, ...] = Field(min_length=6, max_length=6)
    temporal_reasoning: tuple[str, ...] = Field(min_length=6, max_length=6)
    abstention: tuple[str, ...] = Field(min_length=6, max_length=6)


class Selection(BaseModel):
    enterprise: EnterpriseSelection
    longmem: LongMemSelection


class PageSeed(BaseModel):
    source_ref: str
    audience: str
    body: str
    origin: str


class MemorySeed(BaseModel):
    source_ref: str
    audience: str
    body: str
    item_class: str = "fact"
    memory_kind: str = "fact"
    confidence: int = 5


class UfoCaseSeed(BaseModel):
    case: SnapshotCase
    pages: tuple[PageSeed, ...]
    memories: tuple[MemorySeed, ...]


@dataclass(frozen=True)
class Document:
    document_id: str
    source_ref: str
    source_type: str
    parent: str


@dataclass(frozen=True)
class EnterpriseCorpus:
    archive: Path
    overview: Path
    scratch: Path

    def pages(
        self,
        questions: tuple[EnterpriseQuestion, ...],
        *,
        lexical_per_case: int = LEXICAL_NEGATIVES_PER_CASE,
        metadata_per_case: int = METADATA_NEGATIVES_PER_CASE,
        background_per_source: int = BACKGROUND_PER_SOURCE,
    ) -> tuple[SnapshotPage, ...]:
        database = self.scratch / "enterprise.sqlite3"
        documents = self._index(database)
        by_ref = {document.source_ref: document for document in documents}
        by_id: dict[str, list[Document]] = {}
        for document in documents:
            by_id.setdefault(document.document_id, []).append(document)
        evidence_ids = {
            source_ref for question in questions for source_ref in question.expected_doc_ids
        }
        missing = sorted(evidence_ids - by_id.keys())
        if missing:
            raise ValueError(f"enterprise evidence is missing from archive: {', '.join(missing)}")
        selected = {
            document.source_ref for document_id in evidence_ids for document in by_id[document_id]
        }
        with sqlite3.connect(database) as connection:
            for question in questions:
                evidence = {
                    document.source_ref
                    for document_id in question.expected_doc_ids
                    for document in by_id[document_id]
                }
                selected.update(
                    self._lexical(
                        connection,
                        question,
                        lexical_per_case,
                        evidence,
                    )
                )
                selected.update(self._metadata(documents, by_id, question, metadata_per_case))
        for source_type in SOURCE_TYPES:
            candidates = (document for document in documents if document.source_type == source_type)
            selected.update(
                document.source_ref
                for document in heapq.nsmallest(
                    background_per_source,
                    candidates,
                    key=lambda document: _rank("background", document.source_ref),
                )
            )
        pages = list(self._read_pages(tuple(by_ref[source_ref] for source_ref in sorted(selected))))
        if any(question.question_type == "high_level" for question in questions):
            body = _portable_text(self.overview.read_text(encoding="utf-8"))
            pages.append(
                SnapshotPage(
                    source_ref=ENTERPRISE_OVERVIEW_SOURCE_REF,
                    audience="shared",
                    body=body,
                    digest=content_digest(body),
                    origin="enterprise:overview",
                )
            )
        return tuple(pages)

    def _index(self, database: Path) -> tuple[Document, ...]:
        database.unlink(missing_ok=True)
        documents: list[Document] = []
        try:
            archive = ZipFile(self.archive)
        except BadZipFile as error:
            raise ValueError(f"invalid enterprise document archive: {self.archive}") from error
        with archive, sqlite3.connect(database) as connection:
            connection.execute("create virtual table search using fts5(source_ref unindexed, body)")
            for member in sorted(archive.infolist(), key=lambda item: item.filename):
                if member.is_dir():
                    continue
                document = _document(member.filename)
                if document is None:
                    continue
                body = _portable_text(archive.read(member).decode("utf-8"))
                connection.execute("insert into search values (?, ?)", (document.source_ref, body))
                documents.append(document)
        refs = [document.source_ref for document in documents]
        if len(set(refs)) != len(refs):
            raise ValueError("enterprise archive contains duplicate document paths")
        return tuple(documents)

    @staticmethod
    def _lexical(
        connection: sqlite3.Connection,
        question: EnterpriseQuestion,
        limit: int,
        evidence: set[str],
    ) -> tuple[str, ...]:
        terms = sorted(set(TOKEN.findall(question.question.lower())) - STOPWORDS)
        if not terms or limit == 0:
            return ()
        query = " OR ".join(f'"{term}"' for term in terms)
        rows = connection.execute(
            "select source_ref from search where search match ? "
            "order by bm25(search), source_ref limit ?",
            (query, max(limit * 4, limit)),
        )
        return tuple(row[0] for row in rows if row[0] not in evidence)[:limit]

    @staticmethod
    def _metadata(
        documents: tuple[Document, ...],
        by_id: dict[str, list[Document]],
        question: EnterpriseQuestion,
        limit: int,
    ) -> tuple[str, ...]:
        evidence = {
            document.source_ref
            for document_id in question.expected_doc_ids
            for document in by_id[document_id]
        }
        parents = {
            document.parent
            for document_id in question.expected_doc_ids
            for document in by_id[document_id]
        }
        candidates = (
            document
            for document in documents
            if document.source_ref not in evidence and document.source_type in question.source_types
        )
        return tuple(
            document.source_ref
            for document in heapq.nsmallest(
                limit,
                candidates,
                key=lambda document: (
                    document.parent not in parents,
                    _rank(question.question_id, document.source_ref),
                ),
            )
        )

    def _read_pages(self, documents: tuple[Document, ...]) -> tuple[SnapshotPage, ...]:
        with ZipFile(self.archive) as archive:
            pages = []
            for document in documents:
                content = _portable_text(archive.read(document.source_ref).decode("utf-8"))
                body = f"source: {document.source_type}\npath: {document.source_ref}\n\n{content}"
                pages.append(
                    SnapshotPage(
                        source_ref=f"enterprise/{document.source_ref}",
                        audience="shared",
                        body=body,
                        digest=content_digest(body),
                        origin=f"enterprise:{document.source_type}",
                    )
                )
        return tuple(pages)


@dataclass(frozen=True)
class Memory100Builder:
    enterprise_questions: Path
    enterprise_documents: Path
    enterprise_overview: Path
    longmem: Path
    output: Path
    scratch: Path

    def build(self) -> SnapshotManifest:
        verify_asset(self.enterprise_questions, ENTERPRISE_QUESTIONS)
        verify_asset(self.enterprise_documents, ENTERPRISE_DOCUMENTS)
        verify_asset(self.enterprise_overview, ENTERPRISE_OVERVIEW)
        verify_asset(self.longmem, LONGMEM_CLEANED)
        selection = Selection.model_validate_json(SELECTION_FILE.read_bytes())
        enterprise = _enterprise_questions(self.enterprise_questions, selection.enterprise)
        longmem_cases, longmem_memories = _longmem(self.longmem, selection.longmem)
        ufo_cases, ufo_pages, ufo_memories = _ufo()
        enterprise_pages = EnterpriseCorpus(
            self.enterprise_documents, self.enterprise_overview, self.scratch
        ).pages(enterprise)
        enterprise_cases = tuple(
            SnapshotCase(
                id=f"enterprise/{question.question_id}",
                corpus="enterprise",
                category=question.question_type,
                audience="shared",
                question=question.question,
                expected_answer=question.gold_answer,
                evidence_refs=_enterprise_evidence_refs(question, enterprise_pages),
                answer_facts=question.answer_facts,
            )
            for question in enterprise
        )
        builder_digest = _builder_digest(selection)
        manifest = write_snapshot(
            self.output,
            upstreams=UPSTREAMS,
            builder_digest=builder_digest,
            cases=tuple(
                sorted(
                    (*enterprise_cases, *longmem_cases, *ufo_cases),
                    key=lambda item: item.id,
                )
            ),
            pages=tuple(sorted((*enterprise_pages, *ufo_pages), key=lambda item: item.source_ref)),
            memories=tuple(
                sorted((*longmem_memories, *ufo_memories), key=lambda item: item.source_ref)
            ),
        )
        (self.output / THIRD_PARTY_NOTICES_FILE.name).write_bytes(
            THIRD_PARTY_NOTICES_FILE.read_bytes()
        )
        return manifest


def verify_asset(path: Path, asset: UpstreamAsset) -> None:
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


def _enterprise_questions(
    path: Path, selection: EnterpriseSelection
) -> tuple[EnterpriseQuestion, ...]:
    questions = tuple(
        EnterpriseQuestion.model_validate_json(line) for line in path.read_bytes().splitlines()
    )
    by_id = {question.question_id: question for question in questions}
    chosen: list[EnterpriseQuestion] = []
    for category, ids in selection:
        candidates = tuple(question for question in questions if question.question_type == category)
        expected = tuple(
            question.question_id
            for question in sorted(
                candidates,
                key=lambda question: _rank("memory_100/enterprise", question.question_id),
            )[: len(ids)]
        )
        if set(ids) != set(expected):
            raise ValueError(f"enterprise selection for {category} is not deterministic")
        chosen.extend(by_id[question_id] for question_id in ids)
    if len(chosen) != 60 or len({question.question_id for question in chosen}) != 60:
        raise ValueError("enterprise selection must contain 60 unique questions")
    if set(SOURCE_TYPES) - {source for question in chosen for source in question.source_types}:
        raise ValueError("enterprise selection does not cover every source type")
    return tuple(chosen)


def _longmem(
    path: Path, selection: LongMemSelection
) -> tuple[tuple[SnapshotCase, ...], tuple[SnapshotMemory, ...]]:
    raw = json.loads(path.read_bytes())
    questions = tuple(LongMemQuestion.model_validate(item) for item in raw)
    by_id = {question.question_id: question for question in questions}
    selected_ids = tuple(question_id for _category, ids in selection for question_id in ids)
    if len(selected_ids) != 30 or len(set(selected_ids)) != 30:
        raise ValueError("LongMem selection must contain 30 unique questions")
    _validate_longmem_selection(questions, selection)
    category_by_id = {question_id: category for category, ids in selection for question_id in ids}
    cases: list[SnapshotCase] = []
    memories: list[SnapshotMemory] = []
    for question_id in selected_ids:
        question = by_id[question_id]
        if not (
            len(question.haystack_dates)
            == len(question.haystack_session_ids)
            == len(question.haystack_sessions)
        ):
            raise ValueError(f"LongMem case {question_id} has misaligned session fields")
        evidence_refs = tuple(
            f"longmem/{question_id}/session/{session_id}"
            for session_id in question.answer_session_ids
        )
        cases.append(
            SnapshotCase(
                id=f"longmem/{question_id}",
                corpus="longmem",
                category=category_by_id[question_id],
                audience=f"longmem-{question_id}",
                question=f"Question date: {question.question_date}\n\n{question.question}",
                expected_answer=str(question.answer),
                evidence_refs=evidence_refs,
            )
        )
        for date, session_id, turns in zip(
            question.haystack_dates,
            question.haystack_session_ids,
            question.haystack_sessions,
            strict=True,
        ):
            body = "\n".join(
                (f"Session date: {date}", *(f"{turn.role}: {turn.content}" for turn in turns))
            )
            memories.append(
                SnapshotMemory(
                    source_ref=f"longmem/{question_id}/session/{session_id}",
                    audience=f"longmem-{question_id}",
                    body=body,
                    digest=content_digest(body),
                    item_class="fact",
                    memory_kind="event",
                    confidence=8,
                )
            )
    return tuple(cases), tuple(memories)


def _validate_longmem_selection(
    questions: tuple[LongMemQuestion, ...], selection: LongMemSelection
) -> None:
    by_type = {
        question_type: tuple(
            question
            for question in questions
            if question.question_type == question_type and not question.question_id.endswith("_abs")
        )
        for question_type in (
            "single-session-user",
            "single-session-assistant",
            "single-session-preference",
            "multi-session",
            "knowledge-update",
            "temporal-reasoning",
        )
    }
    chosen_info: list[str] = []
    for question_type in (
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
    ):
        chosen_info.extend(_ranked_ids(by_type[question_type], 2))
    expected = {
        "information_extraction": tuple(chosen_info),
        "multi_session": _ranked_ids(by_type["multi-session"], 6),
        "knowledge_update": _ranked_ids(by_type["knowledge-update"], 6),
        "temporal_reasoning": _ranked_ids(by_type["temporal-reasoning"], 6),
        "abstention": _ranked_ids(
            tuple(question for question in questions if question.question_id.endswith("_abs")), 6
        ),
    }
    for category, ids in selection:
        if set(ids) != set(expected[category]):
            raise ValueError(f"LongMem selection for {category} is not deterministic")


def _ranked_ids(questions: tuple[LongMemQuestion, ...], limit: int) -> tuple[str, ...]:
    return tuple(
        question.question_id
        for question in sorted(
            questions,
            key=lambda question: _rank("memory_100/longmem", question.question_id),
        )[:limit]
    )


def _enterprise_evidence_refs(
    question: EnterpriseQuestion, pages: tuple[SnapshotPage, ...]
) -> tuple[str, ...]:
    if question.question_type == "high_level":
        return (ENTERPRISE_OVERVIEW_SOURCE_REF,)
    by_id: dict[str, list[str]] = {}
    for page in pages:
        matched = DOCUMENT_ID.search(PurePosixPath(page.source_ref).name)
        if matched is not None:
            by_id.setdefault(matched.group(1), []).append(page.source_ref)
    missing = sorted(set(question.expected_doc_ids) - by_id.keys())
    if missing:
        raise ValueError(f"enterprise evidence is missing from pages: {', '.join(missing)}")
    return tuple(
        source_ref
        for document_id in dict.fromkeys(question.expected_doc_ids)
        for source_ref in by_id[document_id]
    )


def _ufo() -> tuple[tuple[SnapshotCase, ...], tuple[SnapshotPage, ...], tuple[SnapshotMemory, ...]]:
    seeds = tuple(
        UfoCaseSeed.model_validate(item) for item in json.loads(UFO_CASES_FILE.read_bytes())
    )
    expected = CORPUS_COUNTS["ufo"]
    if len(seeds) != expected or len({seed.case.id for seed in seeds}) != expected:
        raise ValueError(f"UFO dataset must contain {expected} unique cases")
    pages = tuple(
        SnapshotPage(
            **page.model_dump(),
            digest=content_digest(page.body),
        )
        for seed in seeds
        for page in seed.pages
    )
    memories = tuple(
        SnapshotMemory(
            **memory.model_dump(),
            digest=content_digest(memory.body),
        )
        for seed in seeds
        for memory in seed.memories
    )
    return tuple(seed.case for seed in seeds), pages, memories


def _document(member: str) -> Document | None:
    path = PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"enterprise archive member escapes its root: {member!r}")
    matched = DOCUMENT_ID.search(path.name)
    if matched is None:
        return None
    source_type = next((part for part in path.parts if part in SOURCE_TYPES), None)
    if source_type is None:
        raise ValueError(f"enterprise document has no recognized source type: {member!r}")
    return Document(matched.group(1), member, source_type, str(path.parent))


def _rank(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}/{value}".encode()).hexdigest()


def _portable_text(value: str) -> str:
    return value.replace(NULL_CHARACTER, NULL_REPLACEMENT)


def _builder_digest(selection: Selection) -> str:
    policy = (
        Path(__file__).read_bytes()
        + canonical_json(selection)
        + UFO_CASES_FILE.read_bytes()
        + THIRD_PARTY_NOTICES_FILE.read_bytes()
        + (
            f"{LEXICAL_NEGATIVES_PER_CASE}:{METADATA_NEGATIVES_PER_CASE}:{BACKGROUND_PER_SOURCE}"
        ).encode()
    )
    return f"sha256:{hashlib.sha256(policy).hexdigest()}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.memory_100.build")
    parser.add_argument("--enterprise-questions", type=Path, required=True)
    parser.add_argument("--enterprise-documents", type=Path, required=True)
    parser.add_argument("--enterprise-overview", type=Path, required=True)
    parser.add_argument("--longmem", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.scratch is not None:
        args.scratch.mkdir(parents=True, exist_ok=True)
        manifest = Memory100Builder(
            args.enterprise_questions,
            args.enterprise_documents,
            args.enterprise_overview,
            args.longmem,
            args.out,
            args.scratch,
        ).build()
    else:
        with tempfile.TemporaryDirectory(prefix="memory-100-") as scratch:
            manifest = Memory100Builder(
                args.enterprise_questions,
                args.enterprise_documents,
                args.enterprise_overview,
                args.longmem,
                args.out,
                Path(scratch),
            ).build()
    print(manifest.digest)


if __name__ == "__main__":
    main()
