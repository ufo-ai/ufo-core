import importlib.util
import json
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from evals.memory_ingestion.materialize import (
    DERIVATION_MODEL,
    DerivedCorpus,
    DerivedEvidence,
    DerivedFact,
    IngestionReadiness,
)
from evals.memory_ingestion.models import (
    IngestionCase,
    IngestionPage,
    content_digest,
    write_snapshot,
)
from evals.stack import Matrix

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "nightly_memory_ingestion_test", SCRIPTS / "nightly_memory_ingestion.py"
)
assert SPEC is not None and SPEC.loader is not None
NIGHTLY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = NIGHTLY
SPEC.loader.exec_module(NIGHTLY)


def _snapshot(root: Path) -> Path:
    body = "The project codename is Polaris."
    write_snapshot(
        root,
        upstreams=(),
        builder_digest="sha256:" + "1" * 64,
        cases=(
            IngestionCase(
                id="longmem/polaris",
                corpus="longmem",
                category="information_extraction",
                question="What is the codename?",
                expected_answer="Polaris",
                evidence_refs=("longmem/polaris/session/answer",),
            ),
        ),
        pages=(
            IngestionPage(
                source_ref="longmem/polaris/answer/00/00.txt",
                evidence_ref="longmem/polaris/session/answer",
                body=body,
                digest=content_digest(body),
                origin="longmem:polaris",
            ),
        ),
    )
    return root


def _corpus(snapshot: Path) -> DerivedCorpus:
    manifest = json.loads((snapshot / "snapshot.json").read_text())
    return DerivedCorpus.build(
        manifest["digest"],
        DERIVATION_MODEL,
        (
            DerivedFact(
                source_ref="longmem/polaris/answer/00/00.txt",
                body="The project codename is Polaris.",
                memory_kind="fact",
                confidence=8,
                as_of=datetime(2026, 8, 17, tzinfo=UTC),
            ),
        ),
    )


def test_paired_nightly_targets_share_one_derived_corpus(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "snapshot")
    corpus_path = tmp_path / "derived-corpus.json"
    corpus_path.write_text(_corpus(snapshot).model_dump_json())

    NIGHTLY.write_inputs(tmp_path / "targets", snapshot, corpus_path, "auto")

    matrix = Matrix.model_validate(
        tomllib.loads((tmp_path / "targets" / "matrix.toml").read_text())
    )
    assert {row.memory_ingestion_corpus for row in matrix.run} == {corpus_path}
    assert {row.label for row in matrix.run} == {
        "memory-ingestion-claude-opus-5",
        "memory-ingestion-glm-5-3-flash",
    }


def test_producer_gaps_remain_scored_artifact_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = _snapshot(tmp_path / "snapshot")
    corpus = _corpus(snapshot)
    corpus_path = tmp_path / "derived-corpus.json"
    corpus_path.write_text(corpus.model_dump_json())
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(
        IngestionReadiness(
            snapshot_digest=corpus.snapshot_digest,
            corpus_digest="sha256:" + "2" * 64,
            derived_corpus_digest=corpus.digest,
            derivation_model=DERIVATION_MODEL,
            workspace_id=uuid4(),
            source_id=uuid4(),
            pages_root=tmp_path / "pages",
            page_count=1,
            memory_count=1,
            chunk_count=1,
            asker_email="memory-ingestion+asker@eval.invalid",
            evidence=(
                DerivedEvidence(
                    source_ref="longmem/polaris/session/answer",
                    memory_ids=(),
                ),
            ),
        ).model_dump_json()
    )
    output = tmp_path / "producer-state" / "readiness.json"

    NIGHTLY.verify_producer(snapshot, corpus_path, readiness_path, output)

    assert output.read_bytes() == readiness_path.read_bytes()
    assert output.with_name("derived-corpus.json").read_bytes() == corpus_path.read_bytes()
    assert capsys.readouterr().out == "memory ingestion producer evidence coverage 0/1\n"
