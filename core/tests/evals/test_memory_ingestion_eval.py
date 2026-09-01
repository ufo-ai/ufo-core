import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory import manifest as memory_manifest
from ufo_ext_memory.events import MEMORY_RECALL_EVENT
from ufo_ext_sources import manifest as sources_manifest
from ufo_testsupport.migrations import apply_cached_migrations

from evals.__main__ import _tasks as selected_eval_tasks
from evals.budget import EvalRunBudget
from evals.harness.capability import CapabilityOutput, ToolInvocation, TurnLog
from evals.memory_100.build import SELECTION_FILE, Selection
from evals.memory_ingestion.build import MemoryIngestionBuilder
from evals.memory_ingestion.materialize import (
    DERIVATION_MODEL,
    DerivedEvidence,
    IngestionReadiness,
    MemoryIngestionMaterializer,
)
from evals.memory_ingestion.models import (
    IngestionCase,
    IngestionPage,
    UpstreamAsset,
    content_digest,
    load_snapshot,
    write_snapshot,
)
from evals.memory_ingestion.runner import (
    ExpectedDerivedEvidence,
    MemoryIngestionGrader,
    load_memory_ingestion,
)
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import ModelEvent, ModelRequest, ToolCallDelta, ToolCallStart
from ufo.harness.models.registry import ModelRegistry
from ufo.schema import tables
from ufo.schema.records import Usage


class DeterministicEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0, 0.0) for _ in texts)


@dataclass
class ExtractionClient:
    requests: list[ModelRequest] = field(default_factory=list)
    run_budget: EvalRunBudget | None = None
    observed_budget: bool = False

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if self.run_budget is not None:
            async with workspace_tx() as connection:
                purchase = (
                    await connection.execute(
                        sa.select(
                            tables.balance_purchase.c.reference,
                            tables.balance_purchase.c.granted_micro_usd,
                        )
                    )
                ).one()
            assert purchase == (f"eval/{self.run_budget.run_id}", self.run_budget.micro_usd)
            self.observed_budget = True
        self.requests.append(request)
        message = request.messages[0].content
        assert isinstance(message, str)
        payload = json.loads(message)
        facts = [
            {
                "page_id": page["page_id"],
                "notability": "high",
                "body": "The project codename is Polaris.",
                "memory_kind": "fact",
                "confidence": 8,
            }
            for page in payload["pages"]
        ]
        call_id = f"call-{len(self.requests)}"
        yield ToolCallStart(id=call_id, name="record_facts")
        yield ToolCallDelta(
            id=call_id,
            partial_json=json.dumps({"facts": facts}, separators=(",", ":")),
        )
        yield Usage(input_tokens=10, output_tokens=5)


@pytest.fixture
def memory_ingestion_database_url(database_url: str, tmp_path: Path) -> str:
    if database_url.startswith("postgresql"):
        pytest.skip("memory_ingestion materialization proof uses the real SQLite default index")
    url = f"sqlite+aiosqlite:///{tmp_path / 'memory_ingestion.db'}"
    apply_cached_migrations(url)
    return url


@pytest.fixture
async def memory_ingestion_db(
    memory_ingestion_database_url: str,
) -> AsyncIterator[None]:
    init_db(memory_ingestion_database_url)
    try:
        yield
    finally:
        await dispose_db()


def _asset(path: Path, name: str) -> UpstreamAsset:
    body = path.read_bytes()
    return UpstreamAsset(
        name=name,
        url=f"https://example.test/{name}",
        revision="fixture",
        size_bytes=len(body),
        sha256=f"sha256:{hashlib.sha256(body).hexdigest()}",
        license="MIT",
    )


def _builder_inputs(tmp_path: Path) -> tuple[Path, Path, UpstreamAsset, UpstreamAsset]:
    selection = Selection.model_validate_json(SELECTION_FILE.read_bytes()).longmem
    category_by_id = {question_id: category for category, ids in selection for question_id in ids}
    longmem = []
    for question_id, category in category_by_id.items():
        abstention = category == "abstention"
        session_id = f"answer_{question_id}"
        longmem.append(
            {
                "question_id": question_id,
                "question_type": "single-session-user",
                "question": f"Question for {question_id}?",
                "answer": "Polaris",
                "question_date": "2026-08-18",
                "haystack_dates": ["2026-08-17"],
                "haystack_session_ids": [session_id],
                "haystack_sessions": [
                    [
                        {
                            "role": "user",
                            "content": "The project codename is Polaris.",
                            "has_answer": not abstention,
                        }
                    ]
                ],
                "answer_session_ids": [session_id],
            }
        )
    longmem_path = tmp_path / "longmem.json"
    longmem_path.write_text(json.dumps(longmem))
    qa = []
    conversation: dict[str, object] = {"speaker_a": "A", "speaker_b": "B"}
    question_index = 0
    for category, count in ((1, 18), (2, 18), (3, 17), (4, 17)):
        for _ in range(count):
            question_index += 1
            dialog_id = f"D{question_index}:1"
            qa.append(
                {
                    "question": f"What is fact {question_index}?",
                    "answer": f"fact {question_index}",
                    "evidence": [dialog_id],
                    "category": category,
                }
            )
            conversation[f"session_{question_index}_date_time"] = "2026-08-17"
            conversation[f"session_{question_index}"] = [
                {
                    "speaker": "A",
                    "dia_id": dialog_id,
                    "text": f"The durable value is fact {question_index}.",
                }
            ]
    locomo_path = tmp_path / "locomo.json"
    locomo_path.write_text(
        json.dumps(
            [
                {
                    "sample_id": "fixture",
                    "qa": qa,
                    "conversation": conversation,
                    "event_summary": {},
                    "observation": {},
                    "session_summary": {},
                }
            ]
        )
    )
    return (
        longmem_path,
        locomo_path,
        _asset(longmem_path, "longmem"),
        _asset(locomo_path, "locomo"),
    )


def test_builder_matches_memory_100_and_builds_deterministic_locomo_cases(
    tmp_path: Path,
) -> None:
    longmem, locomo, longmem_asset, locomo_asset = _builder_inputs(tmp_path)
    notices = tmp_path / "notices.md"
    notices.write_text("notices\n")
    output = tmp_path / "snapshot"

    manifest = MemoryIngestionBuilder(
        longmem,
        locomo,
        output,
        longmem_asset=longmem_asset,
        locomo_asset=locomo_asset,
        notices_file=notices,
    ).run()

    snapshot = load_snapshot(output)
    matched = {
        f"longmem/{question_id}"
        for _category, ids in Selection.model_validate_json(SELECTION_FILE.read_bytes()).longmem
        for question_id in ids
    }
    assert manifest.cases.records == 100
    assert {case.id for case in snapshot.cases if case.corpus == "longmem"} == matched
    assert sum(case.corpus == "locomo" for case in snapshot.cases) == 70
    assert all(len(page.body) < 8_000 for page in snapshot.pages)
    assert not any(case.evidence_refs for case in snapshot.cases if case.category == "abstention")
    second = tmp_path / "second"
    MemoryIngestionBuilder(
        longmem,
        locomo,
        second,
        longmem_asset=longmem_asset,
        locomo_asset=locomo_asset,
        notices_file=notices,
    ).run()
    assert {path.name: path.read_bytes() for path in output.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    assert all(case.samples == 1 for case in snapshot.cases)
    smoke_ids = tuple(sorted(case.id for case in snapshot.cases))[:3]
    smoke_output = tmp_path / "smoke"
    MemoryIngestionBuilder(
        longmem,
        locomo,
        smoke_output,
        longmem_asset=longmem_asset,
        locomo_asset=locomo_asset,
        notices_file=notices,
    ).run(smoke_ids, samples=3)
    smoke_snapshot = load_snapshot(smoke_output)
    assert {case.id for case in smoke_snapshot.cases} == set(smoke_ids)
    assert all(case.samples == 3 for case in smoke_snapshot.cases)
    smoke_report_key = (smoke_snapshot.cases[0].corpus, smoke_snapshot.cases[0].category)
    smoke_report_output = tmp_path / "smoke-report"
    MemoryIngestionBuilder(
        longmem,
        locomo,
        smoke_report_output,
        longmem_asset=longmem_asset,
        locomo_asset=locomo_asset,
        notices_file=notices,
    ).run(smoke_ids, samples=3, report=smoke_report_key)
    smoke_report = load_snapshot(smoke_report_output)
    assert smoke_report.pages == smoke_snapshot.pages
    assert {(case.corpus, case.category) for case in smoke_report.cases} == {smoke_report_key}
    locomo_output = tmp_path / "locomo-report"
    MemoryIngestionBuilder(
        longmem,
        locomo,
        locomo_output,
        longmem_asset=longmem_asset,
        locomo_asset=locomo_asset,
        notices_file=notices,
    ).run(report=("locomo", "multi_hop"))
    locomo_report = load_snapshot(locomo_output)
    assert len(locomo_report.cases) == 18
    assert {(case.corpus, case.category) for case in locomo_report.cases} == {
        ("locomo", "multi_hop")
    }
    assert locomo_report.pages == snapshot.pages
    locomo_readiness = tmp_path / "locomo-readiness.json"
    locomo_readiness.write_text(
        IngestionReadiness(
            snapshot_digest=locomo_report.manifest.digest,
            corpus_digest="sha256:" + "1" * 64,
            derivation_model=DERIVATION_MODEL,
            workspace_id=uuid4(),
            source_id=uuid4(),
            pages_root=tmp_path / "locomo-pages",
            page_count=len(locomo_report.pages),
            memory_count=0,
            chunk_count=0,
            asker_email="asker@eval.invalid",
            evidence=tuple(
                DerivedEvidence(source_ref=source_ref, memory_ids=())
                for source_ref in sorted(
                    {ref for case in locomo_report.cases for ref in case.evidence_refs}
                )
            ),
        ).model_dump_json()
    )
    locomo_run = load_memory_ingestion(locomo_output, locomo_readiness)
    assert tuple(task.name for task in locomo_run.tasks) == ("memory_ingestion.locomo.multi_hop",)
    abstention_output = tmp_path / "abstention-report"
    MemoryIngestionBuilder(
        longmem,
        locomo,
        abstention_output,
        longmem_asset=longmem_asset,
        locomo_asset=locomo_asset,
        notices_file=notices,
    ).run(report=("longmem", "abstention"))
    abstention_report = load_snapshot(abstention_output)
    assert len(abstention_report.cases) == 6
    assert not any(case.evidence_refs for case in abstention_report.cases)
    assert abstention_report.pages == snapshot.pages
    with pytest.raises(ValueError, match=r"report longmem\.unknown has no cases"):
        MemoryIngestionBuilder(
            longmem,
            locomo,
            tmp_path / "unknown-report",
            longmem_asset=longmem_asset,
            locomo_asset=locomo_asset,
            notices_file=notices,
        ).run(report=("longmem", "unknown"))


def _snapshot(root: Path, samples: int = 1) -> None:
    body = "The project codename is Polaris and it remains active for the launch."
    write_snapshot(
        root,
        upstreams=(),
        builder_digest="sha256:" + "0" * 64,
        cases=(
            IngestionCase(
                id="longmem/polaris",
                corpus="longmem",
                category="information_extraction",
                question="What is the project codename?",
                expected_answer="Polaris",
                evidence_refs=("longmem/polaris/session/answer",),
                samples=samples,
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


def _registry(client: ExtractionClient) -> ModelRegistry:
    return ModelRegistry(
        specs={
            spec.id: replace(spec, client=lambda spec, key: client, key_slot="", key_env="")
            for spec in CORE_MODEL_SPECS
        },
        pricing=CORE_PRICING,
        auto_model="claude-opus-5",
    )


async def test_materializer_runs_luna_derivation_and_indexes_only_derived_memory(
    memory_ingestion_db: None, tmp_path: Path
) -> None:
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    run_budget = EvalRunBudget(uuid4(), 1_250_000)
    client = ExtractionClient(run_budget=run_budget)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blobs"))
    materializer = MemoryIngestionMaterializer.from_snapshot(
        snapshot_root,
        tmp_path / "state",
        blob=blob,
        index=DefaultIndex(transaction=workspace_tx),
        embed=DeterministicEmbed(),
        manifests=(sources_manifest.manifest(), memory_manifest.manifest()),
        registry=_registry(client),
        background_model=DERIVATION_MODEL,
        agent_reasoning="medium",
        run_budget=run_budget,
    )

    readiness = await materializer.run()

    assert readiness.derivation_model == DERIVATION_MODEL
    assert readiness.page_count == 1
    assert readiness.memory_count == 1
    assert readiness.evidence[0].memory_ids
    assert client.requests
    assert client.observed_budget
    assert {request.model for request in client.requests} == {DERIVATION_MODEL}
    async with workspace_tx() as connection:
        agent_reasoning = (
            await connection.execute(sa.select(tables.agent.c.reasoning))
        ).scalar_one()
        owners = (
            await connection.execute(sa.text("select distinct owner_kind from chunk"))
        ).scalars()
        assert agent_reasoning == "medium"
        assert set(owners) == {"memory_item"}


async def test_runner_requires_derived_evidence_from_recall_or_search(tmp_path: Path) -> None:
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    snapshot = load_snapshot(snapshot_root)
    memory_id = uuid4()
    readiness = IngestionReadiness(
        snapshot_digest=snapshot.manifest.digest,
        corpus_digest="sha256:" + "1" * 64,
        derivation_model=DERIVATION_MODEL,
        workspace_id=uuid4(),
        source_id=uuid4(),
        pages_root=tmp_path / "pages",
        page_count=1,
        memory_count=1,
        chunk_count=1,
        asker_email="asker@eval.invalid",
        evidence=(
            DerivedEvidence(
                source_ref="longmem/polaris/session/answer",
                memory_ids=(memory_id,),
            ),
        ),
    )
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(readiness.model_dump_json())

    run = load_memory_ingestion(snapshot_root, readiness_path)
    sampled_root = tmp_path / "sampled"
    _snapshot(sampled_root, samples=3)
    sampled_readiness = tmp_path / "sampled-readiness.json"
    sampled_readiness.write_text(
        readiness.model_copy(
            update={"snapshot_digest": load_snapshot(sampled_root).manifest.digest}
        ).model_dump_json()
    )
    sampled = load_memory_ingestion(sampled_root, sampled_readiness)
    assert sampled.tasks[0].digest != run.tasks[0].digest
    grader = MemoryIngestionGrader(
        (ExpectedDerivedEvidence("longmem/polaris/session/answer", frozenset({memory_id})),)
    )
    output = CapabilityOutput(
        "Polaris",
        (
            ToolInvocation(
                name="memory_search",
                input={},
                result=f"- [fact] Polaris (memory/{memory_id}, 2026-08-18)",
                has_result=True,
            ),
        ),
        log=TurnLog(
            event=MEMORY_RECALL_EVENT,
            turn_id=uuid4(),
            attributes={"memory_ids": []},
        ),
    )

    verdict = await grader(output)

    assert verdict.passed
    assert verdict.evidence["coverage"] == 1.0
    assert verdict.evidence["derivationModel"] == "gpt-5.6-luna"
    assert selected_eval_tasks((), None, run) == run.tasks
    missing = await grader(replace(output, calls=()))
    assert not missing.passed

    empty_readiness = readiness.model_copy(
        update={
            "evidence": (
                DerivedEvidence(
                    source_ref="longmem/polaris/session/answer",
                    memory_ids=(),
                ),
            )
        }
    )
    readiness_path.write_text(empty_readiness.model_dump_json())
    empty_run = load_memory_ingestion(snapshot_root, readiness_path)
    assert empty_run.tasks[0].cases == ("longmem/polaris",)
    empty_verdict = await MemoryIngestionGrader(
        (ExpectedDerivedEvidence("longmem/polaris/session/answer", frozenset()),)
    )(output)
    assert not empty_verdict.passed
    assert empty_verdict.evidence["coverage"] == 0.0
