import gzip
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from evals.memory_100.build import (
    ENTERPRISE_OVERVIEW_SOURCE_REF,
    SELECTION_FILE,
    EnterpriseCorpus,
    EnterpriseQuestion,
    Selection,
    _document,
    _enterprise_evidence_refs,
    _longmem,
    _ufo,
    verify_asset,
)
from evals.memory_100.models import (
    SnapshotCase,
    SnapshotMemory,
    SnapshotPage,
    UpstreamAsset,
)
from evals.memory_100.snapshot import content_digest, load_snapshot, write_snapshot

DIGEST = "sha256:" + "0" * 64


def _cases() -> tuple[SnapshotCase, ...]:
    return (
        tuple(
            SnapshotCase(
                id=f"enterprise/{index}",
                corpus="enterprise",
                category="semantic",
                audience="shared",
                question=f"enterprise question {index}",
                expected_answer="answer",
            )
            for index in range(60)
        )
        + tuple(
            SnapshotCase(
                id=f"longmem/{index}",
                corpus="longmem",
                category="temporal_reasoning",
                audience=f"member-{index}",
                question=f"longmem question {index}",
                expected_answer="answer",
            )
            for index in range(30)
        )
        + tuple(
            SnapshotCase(
                id=f"ufo/{index}",
                corpus="ufo",
                category="isolation",
                audience="shared",
                question=f"ufo question {index}",
                expected_answer="answer",
            )
            for index in range(10)
        )
    )


def test_snapshot_bytes_are_deterministic_and_round_trip(tmp_path: Path) -> None:
    page_body = "Company handbook evidence"
    memory_body = "I prefer concise answers"
    page = SnapshotPage(
        source_ref="enterprise/dsid_abc",
        audience="shared",
        body=page_body,
        digest=content_digest(page_body),
        origin="enterprise:confluence",
    )
    memory = SnapshotMemory(
        source_ref="longmem/session-1",
        audience="member-1",
        body=memory_body,
        digest=content_digest(memory_body),
    )
    upstream = UpstreamAsset(
        name="fixture",
        url="https://example.test/fixture",
        revision="abc",
        size_bytes=1,
        sha256=DIGEST,
        license="MIT",
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    manifest = write_snapshot(
        first,
        upstreams=(upstream,),
        builder_digest=DIGEST,
        cases=_cases(),
        pages=(page,),
        memories=(memory,),
    )
    write_snapshot(
        second,
        upstreams=(upstream,),
        builder_digest=DIGEST,
        cases=_cases(),
        pages=(page,),
        memories=(memory,),
    )
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    loaded = load_snapshot(first)
    assert loaded.manifest == manifest
    assert loaded.pages == (page,)
    assert loaded.memories == (memory,)
    assert gzip.decompress((first / "pages.jsonl.gz").read_bytes()).endswith(b"\n")


def test_snapshot_rejects_a_tampered_record_file(tmp_path: Path) -> None:
    write_snapshot(
        tmp_path,
        upstreams=(),
        builder_digest=DIGEST,
        cases=_cases(),
        pages=(),
        memories=(),
    )
    (tmp_path / "cases.jsonl.gz").write_bytes(gzip.compress(b"{}\n", mtime=0))
    with pytest.raises(ValueError, match="digest does not match"):
        load_snapshot(tmp_path)


def test_snapshot_rejects_invalid_corpus_counts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly 100 cases"):
        write_snapshot(
            tmp_path,
            upstreams=(),
            builder_digest=DIGEST,
            cases=_cases()[:-1],
            pages=(),
            memories=(),
        )


def test_snapshot_rejects_missing_evidence(tmp_path: Path) -> None:
    cases = list(_cases())
    cases[0] = cases[0].model_copy(update={"evidence_refs": ("missing/page",)})
    with pytest.raises(ValueError, match="missing evidence refs"):
        write_snapshot(
            tmp_path,
            upstreams=(),
            builder_digest=DIGEST,
            cases=tuple(cases),
            pages=(),
            memories=(),
        )


def test_snapshot_rejects_duplicate_case_evidence(tmp_path: Path) -> None:
    body = "Company handbook evidence"
    cases = list(_cases())
    cases[0] = cases[0].model_copy(update={"evidence_refs": ("memory/handbook", "memory/handbook")})
    with pytest.raises(ValueError, match="duplicate evidence refs"):
        write_snapshot(
            tmp_path,
            upstreams=(),
            builder_digest=DIGEST,
            cases=tuple(cases),
            pages=(),
            memories=(
                SnapshotMemory(
                    source_ref="memory/handbook",
                    audience="shared",
                    body=body,
                    digest=content_digest(body),
                ),
            ),
        )


def test_snapshot_rejects_a_source_ref_shared_by_page_and_memory(tmp_path: Path) -> None:
    _, pages, memories = _ufo()
    memory = memories[0].model_copy(update={"source_ref": pages[0].source_ref})
    with pytest.raises(ValueError, match="source refs overlap"):
        write_snapshot(
            tmp_path,
            upstreams=(),
            builder_digest=DIGEST,
            cases=_cases(),
            pages=(pages[0],),
            memories=(memory,),
        )


def test_asset_verification_checks_size_and_digest(tmp_path: Path) -> None:
    path = tmp_path / "asset"
    path.write_bytes(b"pinned")
    digest = "sha256:" + hashlib.sha256(b"pinned").hexdigest()
    asset = UpstreamAsset(
        name="fixture",
        url="https://example.test/fixture",
        revision="abc",
        size_bytes=6,
        sha256=digest,
        license="MIT",
    )
    verify_asset(path, asset)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="size mismatch"):
        verify_asset(path, asset)


def test_checked_in_selections_and_ufo_cases_have_exact_counts() -> None:
    selection = Selection.model_validate_json(SELECTION_FILE.read_bytes())
    enterprise = [question_id for _category, ids in selection.enterprise for question_id in ids]
    longmem = [question_id for _category, ids in selection.longmem for question_id in ids]
    cases, pages, memories = _ufo()
    assert len(enterprise) == len(set(enterprise)) == 60
    assert len(longmem) == len(set(longmem)) == 30
    assert len(cases) == 10
    assert {case.corpus for case in cases} == {"ufo"}
    assert {ref for case in cases for ref in case.evidence_refs} <= {
        *(page.source_ref for page in pages),
        *(memory.source_ref for memory in memories),
    }


def test_enterprise_corpus_selects_evidence_and_deterministic_negatives(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "documents.zip"
    identifiers = (
        "dsid_00000000000000000000000000000001",
        "dsid_00000000000000000000000000000002",
        "dsid_00000000000000000000000000000003",
    )
    with ZipFile(archive, "w") as output:
        output.writestr(f"confluence/team/{identifiers[0]}-policy.txt", "refund window thirty days")
        output.writestr(f"confluence/team/{identifiers[1]}-notes.txt", "refund workflow notes")
        output.writestr(f"confluence/other/{identifiers[2]}-roadmap.txt", "unrelated roadmap")
    question = EnterpriseQuestion(
        question_id="qst_test",
        question_type="semantic",
        source_types=("confluence",),
        question="What is the refund window?",
        expected_doc_ids=(identifiers[0],),
        gold_answer="Thirty days",
        answer_facts=("The refund window is thirty days.",),
    )
    overview = tmp_path / "company_overview.md"
    overview.write_text("The company mission is reliable inference.")
    high_level = EnterpriseQuestion(
        question_id="qst_high_level",
        question_type="high_level",
        source_types=(),
        question="What is the company mission?",
        expected_doc_ids=(),
        gold_answer="Reliable inference",
        answer_facts=("The company mission is reliable inference.",),
    )
    first = EnterpriseCorpus(archive, overview, tmp_path).pages(
        (question, high_level), lexical_per_case=1, metadata_per_case=1, background_per_source=0
    )
    second = EnterpriseCorpus(archive, overview, tmp_path).pages(
        (question, high_level), lexical_per_case=1, metadata_per_case=1, background_per_source=0
    )
    assert first == second
    assert f"enterprise/{identifiers[0]}" in {page.source_ref for page in first}
    assert _enterprise_evidence_refs(high_level) == (ENTERPRISE_OVERVIEW_SOURCE_REF,)
    overview_page = next(
        page for page in first if page.source_ref == ENTERPRISE_OVERVIEW_SOURCE_REF
    )
    assert overview_page.body == "The company mission is reliable inference."
    assert len(first) >= 2


def test_longmem_cases_keep_dates_and_recallable_session_bodies(tmp_path: Path) -> None:
    selection = Selection.model_validate_json(SELECTION_FILE.read_bytes()).longmem
    question_types = {
        **{
            question_id: question_type
            for ids, question_type in zip(
                (
                    selection.information_extraction[:2],
                    selection.information_extraction[2:4],
                    selection.information_extraction[4:],
                ),
                (
                    "single-session-user",
                    "single-session-assistant",
                    "single-session-preference",
                ),
                strict=True,
            )
            for question_id in ids
        },
        **{question_id: "multi-session" for question_id in selection.multi_session},
        **{question_id: "knowledge-update" for question_id in selection.knowledge_update},
        **{question_id: "temporal-reasoning" for question_id in selection.temporal_reasoning},
        **{question_id: "single-session-user" for question_id in selection.abstention},
    }
    rows = []
    for question_id, question_type in question_types.items():
        session_id = f"session-{question_id}"
        rows.append(
            {
                "question_id": question_id,
                "question_type": question_type,
                "question": "What was current at the time?",
                "answer": "The dated answer.",
                "question_date": "2025/02/03 (Mon) 12:00",
                "haystack_dates": ["2025/01/02 (Thu) 09:00"],
                "haystack_session_ids": [session_id],
                "haystack_sessions": [
                    [{"role": "user", "content": "The dated answer was recorded."}]
                ],
                "answer_session_ids": [] if question_id.endswith("_abs") else [session_id],
            }
        )
    source = tmp_path / "longmem.json"
    source.write_text(json.dumps(rows))

    cases, memories = _longmem(source, selection)

    assert len(cases) == 30
    assert all(case.question.startswith("Question date: 2025/02/03") for case in cases)
    assert all(memory.item_class == "fact" for memory in memories)
    assert all(memory.body.startswith("Session date: 2025/01/02") for memory in memories)


def test_enterprise_archive_rejects_an_escaping_member() -> None:
    with pytest.raises(ValueError, match="escapes"):
        _document("../confluence/dsid_00000000000000000000000000000001.txt")
