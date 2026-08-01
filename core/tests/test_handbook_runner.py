"""HANDBOOK.md tasks from a pinned checkout: corpus drift detection, the case's staged workspace and
envelope, and the rubric verifier's verdicts over upstream's scorecard."""

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from handbook_corpus import (
    HANDBOOK,
    INSTRUCTION,
    REVISION,
    SYSTEM_PROMPT,
    TASK_ID,
    VERIFIER_SOURCE,
    fabricate_checkout,
    fabricate_pin,
    install_pin,
)
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory import store as memory_store

from evals.handbook.build import EXPECTED_TASKS, build_pin
from evals.handbook.build import main as build_main
from evals.handbook.corpus import PinnedTask, UpstreamPin, load_corpus, load_pin, tree_digest
from evals.handbook.environment import (
    ABANDON_TIMEOUT_SECONDS,
    DockerError,
    RubricResult,
    TaskEnvironment,
    VerifierResults,
)
from evals.handbook.ingest import (
    DERIVE_CONSUMER,
    INDEX_CONSUMER,
    MEMORY_EXTENSION,
    DocumentIngest,
    document_text,
)
from evals.handbook.runner import (
    IngestDeps,
    RubricVerifier,
    _capability_case,
    load_handbook,
)
from evals.harness.capability import CapabilityOutput
from ufo.blob import BlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore
from ufo.indexing import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    Chunk,
    EmbedClient,
    IndexBackend,
    IndexScope,
)
from ufo.jobs import PAGE_CHANGE_CURSOR_KEY
from ufo.schema import tables
from ufo.workspace import ws

WORKSPACE = UUID("11111111-2222-3333-4444-555555555555")
CANCELLED_ABANDON_DEADLINE = 5.0


@pytest.fixture
def credentials() -> CredentialStore:
    return CredentialStore(fernet=Fernet(Fernet.generate_key()))


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = fabricate_checkout(tmp_path / "handbook")
    install_pin(fabricate_pin(root), monkeypatch)
    return root


def test_load_corpus_returns_the_pinned_task(checkout: Path) -> None:
    tasks = load_corpus(checkout)
    assert [task.task_id for task in tasks] == [TASK_ID]
    assert tasks[0].instruction == INSTRUCTION
    assert tasks[0].system_prompt == SYSTEM_PROMPT
    assert tasks[0].rubrics == 2


def test_load_corpus_rejects_an_edited_handbook(checkout: Path) -> None:
    handbook = checkout / "tasks" / TASK_ID / "environment" / "initial_workspace" / "SOP.html"
    handbook.write_text(HANDBOOK.replace("2%", "5%"))
    with pytest.raises(ValueError, match="drifted from the pin"):
        load_corpus(checkout)


def test_load_corpus_rejects_a_patched_verifier(checkout: Path) -> None:
    verifier = checkout / "tasks" / TASK_ID / "tests" / "sop_verifier.py"
    verifier.write_text("print('always pass')\n")
    with pytest.raises(ValueError, match="verifier drifted"):
        load_corpus(checkout)


def test_load_corpus_rejects_an_unknown_task(checkout: Path) -> None:
    with pytest.raises(ValueError, match=r"unknown HANDBOOK\.md task ids"):
        load_corpus(checkout, ("hr_nowhere_00000000",))


def test_load_handbook_builds_one_exclusive_task_per_case(
    checkout: Path, credentials: CredentialStore
) -> None:
    tasks = load_handbook(checkout, credentials)
    assert [task.name for task in tasks] == [f"handbook.{TASK_ID}"]
    assert tasks[0].exclusive
    assert tasks[0].pin_runtime
    assert tasks[0].cases == (TASK_ID,)


def test_case_stages_the_workspace_and_preserves_upstream_text(
    checkout: Path, credentials: CredentialStore
) -> None:
    task = load_corpus(checkout)[0]
    case = _capability_case(task, fabricate_pin(checkout), checkout, credentials)
    assert sorted(file.path for file in case.workspace_files) == ["SOP.html", "ap_ledger.xlsx"]
    handbook = next(file for file in case.workspace_files if file.path == "SOP.html")
    assert handbook.content.decode() == HANDBOOK
    assert case.message.startswith(SYSTEM_PROMPT)
    assert INSTRUCTION in case.message
    assert "'workplace' MCP server" in case.message
    assert case.prepare is not None
    assert case.seed is None


@dataclass
class StubEnvironment:
    results: VerifierResults
    verified: list[Path]
    torn_down: list[UUID]

    async def verify(self, workspace_id: UUID, workspace_dir: Path) -> VerifierResults:
        self.verified.append(workspace_dir)
        return self.results

    async def teardown(self, workspace_id: UUID) -> None:
        self.torn_down.append(workspace_id)


def _results(passed: int, total: int) -> VerifierResults:
    return VerifierResults.model_validate(
        {
            "passed": passed == total,
            "rubrics_passed": passed,
            "rubrics_total": total,
            "score": passed / total,
            "rubric_results": [
                {
                    "id": f"rubric-{index}",
                    "pass": index < passed,
                    "score": 1.0 if index < passed else 0.0,
                    "feedback": "graded",
                }
                for index in range(total)
            ],
        }
    )


async def test_verifier_passes_only_on_a_clean_sweep(checkout: Path) -> None:
    task = load_corpus(checkout)[0]
    environment = StubEnvironment(_results(2, 2), [], [])
    with ws(WORKSPACE):
        verdict = await RubricVerifier(environment, task)(  # type: ignore[arg-type]
            CapabilityOutput("done", (), workspace_dir=Path("/tmp/conv"))
        )
    assert verdict.passed
    assert verdict.evidence["rubricsPassed"] == 2
    assert environment.verified == [Path("/tmp/conv")]


async def test_verifier_reports_the_failed_rubrics(checkout: Path) -> None:
    task = load_corpus(checkout)[0]
    with ws(WORKSPACE):
        verdict = await RubricVerifier(StubEnvironment(_results(1, 2), [], []), task)(  # type: ignore[arg-type]
            CapabilityOutput("done", (), workspace_dir=Path("/tmp/conv"))
        )
    assert not verdict.passed
    assert "1/2 rubrics passed" in verdict.reason
    assert "rubric-1" in verdict.reason
    assert verdict.evidence["score"] == 0.5


async def test_verifier_rejects_a_scorecard_that_disagrees_with_the_pin(checkout: Path) -> None:
    task = load_corpus(checkout)[0]
    with ws(WORKSPACE):
        verdict = await RubricVerifier(StubEnvironment(_results(3, 3), [], []), task)(  # type: ignore[arg-type]
            CapabilityOutput("done", (), workspace_dir=Path("/tmp/conv"))
        )
    assert not verdict.passed
    assert "pin declares 2" in verdict.reason


async def test_verifier_fails_when_no_workspace_was_exposed(checkout: Path) -> None:
    task = load_corpus(checkout)[0]
    environment = StubEnvironment(_results(2, 2), [], [])
    with ws(WORKSPACE):
        verdict = await RubricVerifier(environment, task)(CapabilityOutput("done", ()))  # type: ignore[arg-type]
    assert not verdict.passed
    assert environment.verified == []
    assert environment.torn_down == [WORKSPACE], "a case that never graded must drop its services"


def test_rubric_result_reads_upstreams_pass_field() -> None:
    result = RubricResult.model_validate({"id": "r", "pass": True, "score": 1.0})
    assert result.passed


def test_each_task_owns_its_container_name(checkout: Path, credentials: CredentialStore) -> None:
    """Concurrent lanes tear their environments down by name. A name shared between tasks would let
    one lane kill another lane's services mid-case, and the finished workspace would then be graded
    against seed state rather than what the turn did."""
    task = load_corpus(checkout)[0]
    pin = fabricate_pin(checkout)
    other = task.model_copy(update={"task_id": "hr_ridgeline_gear_co_6950ff2b"})
    mine = TaskEnvironment(task, pin, checkout, credentials)
    theirs = TaskEnvironment(other, pin, checkout, credentials)
    assert mine.container_for(WORKSPACE) != theirs.container_for(WORKSPACE)
    assert mine.container_for(WORKSPACE) != mine.container_for(uuid4())
    assert mine.image != theirs.image
    assert task.task_id in mine.container_for(WORKSPACE)
    assert mine.container_for(WORKSPACE).startswith(mine.lane_filter(WORKSPACE))


def test_the_services_see_the_conversation_workspace_at_the_sandbox_path(
    checkout: Path, credentials: CredentialStore, tmp_path: Path
) -> None:
    """Attachment paths the agent writes are resolved by the mail service inside its own container,
    so the workspace must be mounted there at the same absolute path the sandbox serves it from —
    read-only, since the services must not edit what the turn is graded on."""
    from evals.handbook.environment import SANDBOX_WORKSPACE

    task = load_corpus(checkout)[0]
    env = TaskEnvironment(task, fabricate_pin(checkout), checkout, credentials)
    seen: list[tuple[str, ...]] = []

    async def record(*args: str, **kwargs: object) -> str:
        seen.append(args)
        return ""

    object.__setattr__(env, "_docker", record)
    asyncio.run(env._run_container(env.container_for(WORKSPACE), tmp_path / "conv"))
    argv = seen[0]
    assert "--volume" in argv
    assert f"{tmp_path / 'conv'}:{SANDBOX_WORKSPACE}:ro" in argv


def test_document_text_reads_each_policy_format(tmp_path: Path) -> None:
    """The folder backend decodes UTF-8 only, so every handbook format is converted before it can be
    ingested — html, docx and pdf all appear across the corpus."""
    html = tmp_path / "SOP.html"
    html.write_text("<html><body><h1>Hold</h1><p>variance over 2%.</p></body></html>")
    text = document_text(html)
    assert "variance over 2%" in text
    assert "<p>" not in text
    with pytest.raises(ValueError, match="not a policy document"):
        document_text(tmp_path / "ledger.xlsx")


def test_ingest_stages_documents_but_not_spreadsheets(
    checkout: Path, tmp_path: Path, credentials: CredentialStore
) -> None:
    """Policy documents are indexed; the working spreadsheets stay live task data the agent reads
    itself. Staging is also what strips the formats the folder backend cannot decode."""
    task = load_corpus(checkout)[0]
    ingest = DocumentIngest(
        task=task,
        staging_root=tmp_path,
        blob=cast("BlobStore", None),
        index=cast("IndexBackend", None),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )
    staged = ingest._stage()
    assert staged == ("SOP.html",)
    assert [p.name for p in sorted(ingest.folder.iterdir())] == ["SOP.txt"]
    assert "variance" in (ingest.folder / "SOP.txt").read_text()


def test_ingest_restages_without_the_previous_tasks_documents(
    checkout: Path, tmp_path: Path
) -> None:
    """Cases share a workspace and the corpus rewrites its thresholds per task, so a document left
    behind would answer this task's search with the last task's numbers."""
    task = load_corpus(checkout)[0]
    ingest = DocumentIngest(
        task=task,
        staging_root=tmp_path,
        blob=cast("BlobStore", None),
        index=cast("IndexBackend", None),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )
    ingest.folder.mkdir(parents=True)
    (ingest.folder / "Previous_Task_SOP.txt").write_text("variance over 5%")
    ingest._stage()
    assert [p.name for p in sorted(ingest.folder.iterdir())] == ["SOP.txt"]


def test_the_ingested_arm_tells_the_agent_the_policy_is_searchable(
    checkout: Path, credentials: CredentialStore, tmp_path: Path
) -> None:
    """Both arms exist to be compared, so their prompts and their digests must differ: one arm reads
    the handbook off disk, the other searches it."""
    task = load_corpus(checkout)[0]
    pin = fabricate_pin(checkout)
    deps = IngestDeps(
        staging_root=tmp_path,
        blob=cast("BlobStore", None),
        index=cast("IndexBackend", None),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )
    plain = _capability_case(task, pin, checkout, credentials)
    ingested = _capability_case(task, pin, checkout, credentials, deps)
    assert "memory_search" not in plain.message
    assert "memory_search" in ingested.message
    assert plain.digest_tag != ingested.digest_tag


def test_the_ingested_arm_withholds_the_policy_documents(
    checkout: Path, credentials: CredentialStore, tmp_path: Path
) -> None:
    """Left on disk the handbook is simply read, so the arm would measure retrieval plus reading
    rather than retrieval instead of it. The spreadsheets stay — they are live task data."""
    task = load_corpus(checkout)[0]
    pin = fabricate_pin(checkout)
    deps = IngestDeps(
        staging_root=tmp_path,
        blob=cast("BlobStore", None),
        index=cast("IndexBackend", None),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )
    plain = _capability_case(task, pin, checkout, credentials)
    ingested = _capability_case(task, pin, checkout, credentials, deps)
    assert sorted(f.path for f in plain.workspace_files) == ["SOP.html", "ap_ledger.xlsx"]
    assert sorted(f.path for f in ingested.workspace_files) == ["ap_ledger.xlsx"]


def test_the_ingested_arm_refuses_a_task_whose_rubrics_assert_on_the_document(
    checkout: Path, credentials: CredentialStore, tmp_path: Path
) -> None:
    """Withholding a document a rubric checks for would fail the case for a harness reason dressed
    as a capability one. Asked for by name, the arm refuses rather than scoring it."""
    rubrics = checkout / "tasks" / TASK_ID / "tests" / "rubrics.json"
    rubrics.write_text(
        json.dumps([{"id": "r", "verifier_code": "assert (workspace/'SOP.html').exists()"}])
    )
    install_pin(fabricate_pin(checkout, rubrics=1), pytest.MonkeyPatch())
    deps = IngestDeps(
        staging_root=tmp_path,
        blob=cast("BlobStore", None),
        index=cast("IndexBackend", None),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )
    with pytest.raises(ValueError, match=r"assert on a policy document"):
        load_handbook(checkout, credentials, (TASK_ID,), deps)


def test_build_refuses_a_checkout_that_is_not_the_pinned_corpus(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each refusal is what stops a pin being written against a corpus nobody verified. A wrong task
    count, a patched verifier, and drifted tool sets each have to fail rather than pin."""
    monkeypatch.setattr(
        "evals.handbook.build.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout=REVISION + "\n", returncode=0),
    )
    with pytest.raises(ValueError, match=rf"expected {EXPECTED_TASKS} HANDBOOK\.md tasks"):
        build_pin(checkout)

    monkeypatch.setattr("evals.handbook.build.EXPECTED_TASKS", 2)
    second = checkout / "tasks" / "hr_ridgeline_gear_co_6950ff2b"
    shutil.copytree(checkout / "tasks" / TASK_ID, second)
    (second / "tests" / "sop_verifier.py").write_text("print('different')\n")
    with pytest.raises(ValueError, match="do not share one verifier"):
        build_pin(checkout)

    (second / "tests" / "sop_verifier.py").write_text(VERIFIER_SOURCE)
    (second / "task.toml").write_text(
        '[environment]\nenv = { WORLDBENCH_TOOL_SETS = "slack_core" }\n'
    )
    with pytest.raises(ValueError, match="tool sets are not uniform"):
        build_pin(checkout)

    (second / "task.toml").write_text('[environment]\nenv = { INPUTDIR = "/data" }\n')
    with pytest.raises(ValueError, match="declares no WORLDBENCH_TOOL_SETS"):
        build_pin(checkout)


def test_build_pin_round_trips_a_checkout(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pin is what makes a score attributable to a known corpus, so the module that writes it is
    exercised against a checkout and read back through the loader that verifies runs."""
    written = checkout / "pin.json"
    monkeypatch.setattr(
        "evals.handbook.build.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout=REVISION + "\n", returncode=0),
    )
    monkeypatch.setattr("evals.handbook.build.EXPECTED_TASKS", 1)
    build_main(["--checkout", str(checkout), "--out", str(written)])
    pin = UpstreamPin.model_validate_json(written.read_text())
    assert pin.revision == REVISION
    assert [t.task_id for t in pin.tasks] == [TASK_ID]
    assert pin.tasks[0].rubrics == 2
    assert pin.tasks[0].tree_sha256 == tree_digest(checkout / "tasks" / TASK_ID)
    monkeypatch.setattr("evals.handbook.corpus.load_pin", lambda: pin)
    assert [t.task_id for t in load_corpus(checkout)] == [TASK_ID]


def test_the_committed_pin_loads_and_covers_the_corpus() -> None:
    """The pin shipped in this repository is the one runs verify against; a malformed or truncated
    file would only surface at run time, after a container was already built."""
    pin = load_pin()
    assert pin.repository == "surge-ai/handbook"
    assert len(pin.tasks) == EXPECTED_TASKS
    assert len({t.task_id for t in pin.tasks}) == EXPECTED_TASKS
    assert "syntara_ds_all" not in pin.service_tool_sets


def _ingest(checkout: Path, staging: Path, index: IndexBackend | None = None) -> DocumentIngest:
    return DocumentIngest(
        task=load_corpus(checkout)[0],
        staging_root=staging,
        blob=cast("BlobStore", None),
        index=index or UncalledIndex(),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )


def _fact_scope(item_id: UUID) -> IndexScope:
    return IndexScope(OWNER_KIND_MEMORY_ITEM, str(item_id))


def _page_scope(page_id: UUID) -> IndexScope:
    return IndexScope(OWNER_KIND_PAGE, str(page_id))


async def _memory_item_ids() -> list[UUID]:
    async with workspace_tx() as connection:
        rows = await connection.execute(
            sa.select(memory_store.memory_item.c.id).order_by(memory_store.memory_item.c.id)
        )
    return list(rows.scalars())


async def _index_page_chunks(index: IndexBackend, page_ids: list[UUID]) -> None:
    await index.upsert(
        tuple(
            Chunk(
                chunk_digest=f"page-{page_id}",
                owner_kind=OWNER_KIND_PAGE,
                owner_id=str(page_id),
                subject="shared",
                ordinal=0,
                text="hold any invoice whose variance exceeds 2%",
            )
            for page_id in page_ids
        )
    )


async def _index_facts(index: IndexBackend, item_ids: list[UUID]) -> None:
    await index.upsert(
        tuple(
            Chunk(
                chunk_digest=str(item_id),
                owner_kind=OWNER_KIND_MEMORY_ITEM,
                owner_id=str(item_id),
                subject="shared",
                ordinal=0,
                text="a fact derived from that page",
            )
            for item_id in item_ids
        )
    )


class UncalledIndex:
    """The stand-in for the tests that reach no index at all, so a call is the test drifting from
    what it claims to exercise."""

    async def delete(self, scope: IndexScope) -> None:
        raise AssertionError("this test reaches no index")


def test_ingest_owns_only_the_sources_it_staged(checkout: Path, tmp_path: Path) -> None:
    """A deploy's own `[[sources]]` folder entries are somebody else's rows. Selecting folder
    sources by backend alone would take them, and their derived facts with them."""
    ingest = _ingest(checkout, tmp_path / "staging")
    assert ingest._is_mine({"root": str(tmp_path / "staging" / TASK_ID)})
    assert ingest._is_mine({"root": str(tmp_path / "staging")})
    assert not ingest._is_mine({"root": str(tmp_path / "company-notes")})
    assert not ingest._is_mine({"root": ""})
    assert not ingest._is_mine(None)


def test_ingest_matches_its_own_rows_when_the_staging_root_is_relative(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source rows carry absolute roots. A relative `--handbook-ingest` comparing raw strings would
    match none of its own rows and purge nothing, leaving the last task's policy searchable."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "staging").mkdir()
    ingest = _ingest(checkout, Path("staging"))
    assert ingest._is_mine({"root": str((tmp_path / "staging" / TASK_ID).resolve())})


def test_ingest_does_not_match_a_sibling_sharing_its_prefix(checkout: Path, tmp_path: Path) -> None:
    """`<root>-archive` starts with `<root>`; a string prefix would sweep it."""
    ingest = _ingest(checkout, tmp_path / "staging")
    assert not ingest._is_mine({"root": str(tmp_path / "staging-archive" / TASK_ID)})


async def _make_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _register_folder_source(workspace_id: UUID, root: str) -> tuple[UUID, UUID]:
    source_id, page_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend="folder",
                config={"root": root},
                next_sync_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest=f"sha256:{'0' * 64}",
                body_ref=f"{root}/doc.txt",
                stream="files",
                title="doc",
                subject="shared",
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(memory_store.memory_item).values(
                id=uuid4(),
                workspace_id=workspace_id,
                subject="shared",
                body="a fact derived from that page",
                item_class="fact",
                source_ref="page",
                created_from_page_id=page_id,
                created_from_page_revision=1,
                source_id=source_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_id, page_id


async def test_purge_leaves_a_deploys_own_folder_sources_alone(
    db: None, checkout: Path, tmp_path: Path
) -> None:
    """The rows a deploy registered are somebody else's. Selecting folder sources by backend alone
    takes them — and their derived facts, irrecoverably, because the derive cursor has just moved
    past those pages."""
    workspace_id = await _make_workspace()
    index = DefaultIndex(transaction=workspace_tx)
    with ws(workspace_id):
        theirs, their_page = await _register_folder_source(workspace_id, str(tmp_path / "company"))
        mine, my_page = await _register_folder_source(
            workspace_id, str(tmp_path / "staging" / TASK_ID)
        )
        await _index_page_chunks(index, [their_page, my_page])
        ingest = _ingest(checkout, tmp_path / "staging", index)
        await ingest._purge(workspace_id)
        async with workspace_tx() as connection:
            sources = set((await connection.execute(sa.select(tables.source.c.id))).scalars())
            pages = set((await connection.execute(sa.select(tables.page.c.id))).scalars())
        vectors = (
            await index.has_chunks(_page_scope(their_page)),
            await index.has_chunks(_page_scope(my_page)),
        )
    assert theirs in sources and their_page in pages, "a deploy's own source must survive"
    assert mine not in sources and my_page not in pages, "the last case's documents must go"
    assert vectors == (True, False), "the last case's page vectors go, the deploy's stay"


async def test_derived_facts_and_their_removal_are_scoped_to_this_arm(
    db: None, checkout: Path, tmp_path: Path
) -> None:
    """`derived_facts` is what the grader reads to void a contaminated case, so it must see this
    arm's facts and only this arm's, and a fact's vectors go with its row."""
    workspace_id = await _make_workspace()
    index = DefaultIndex(transaction=workspace_tx)
    with ws(workspace_id):
        await _register_folder_source(workspace_id, str(tmp_path / "company"))
        await _register_folder_source(workspace_id, str(tmp_path / "staging" / TASK_ID))
        ingest = _ingest(checkout, tmp_path / "staging", index)
        derived = await ingest.derived_facts(workspace_id)
        assert len(derived) == 1
        items = await _memory_item_ids()
        await _index_facts(index, items)
        await ingest._drop_derived_facts(workspace_id)
        assert await ingest.derived_facts(workspace_id) == []
        remaining = await _memory_item_ids()
        gone = _fact_scope(derived[0])
        survivor = _fact_scope(next(item for item in items if item != derived[0]))
        indexed = (await index.has_chunks(gone), await index.has_chunks(survivor))
    assert remaining == [item for item in items if item != derived[0]]
    assert indexed == (False, True), "the removed fact's vectors go with it, nobody else's"


@dataclass
class StubIngest:
    derived: list[UUID]

    async def derived_facts(self, workspace_id: UUID) -> list[UUID]:
        return self.derived


async def test_a_contaminated_case_is_excluded_rather_than_failed(
    checkout: Path, tmp_path: Path
) -> None:
    """A policy paraphrased into memory during the turn is the harness failing to hold the
    environment the case describes, not the model failing the task. Scoring it as a failure charges
    the model for our defect and moves the reported rate."""
    task = load_corpus(checkout)[0]
    environment = StubEnvironment(_results(2, 2), [], [])
    verifier = RubricVerifier(environment, task, StubIngest([uuid4(), uuid4()]))  # type: ignore[arg-type]
    with ws(WORKSPACE):
        verdict = await verifier(CapabilityOutput("done", (), workspace_dir=tmp_path))
    assert verdict.excluded is True
    assert not verdict.passed
    assert "2 memory items" in verdict.reason
    assert environment.verified == [], "a contaminated case must not be graded"
    assert environment.torn_down == [WORKSPACE]


async def test_an_uncontaminated_case_is_graded_normally(checkout: Path, tmp_path: Path) -> None:
    """The exclusion has an edge: with no derived facts the case grades as any other."""
    task = load_corpus(checkout)[0]
    environment = StubEnvironment(_results(2, 2), [], [])
    verifier = RubricVerifier(environment, task, StubIngest([]))  # type: ignore[arg-type]
    with ws(WORKSPACE):
        verdict = await verifier(CapabilityOutput("done", (), workspace_dir=tmp_path))
    assert verdict.passed
    assert verdict.excluded is False
    assert environment.verified == [tmp_path]


async def test_start_drops_the_container_when_bringing_it_up_fails(
    checkout: Path, credentials: CredentialStore, tmp_path: Path
) -> None:
    """A start that raises past the container's creation leaves services running under a name only
    this lane will ever use again, so the failure path has to drop it."""
    task = load_corpus(checkout)[0]
    env = TaskEnvironment(task, fabricate_pin(checkout), checkout, credentials)
    seen: list[tuple[tuple[str, ...], dict[str, object]]] = []

    async def record(*args: str, **kwargs: object) -> str:
        seen.append((args, kwargs))
        if args[0] == "port":
            raise DockerError("no port published")
        return ""

    object.__setattr__(env, "_docker", record)
    with pytest.raises(DockerError):
        await env.start(WORKSPACE, tmp_path)
    # The retry loop removes before each attempt, so a removal existing proves nothing. What the
    # failure path owes is a removal *after* the last attempt raised, which is the final call.
    args, kwargs = seen[-1]
    assert args[0] == "rm", args
    assert env.container_for(WORKSPACE) in args
    assert kwargs["deadline_seconds"] == ABANDON_TIMEOUT_SECONDS
    assert kwargs["check"] is False


async def test_teardown_drops_this_cases_container_by_name(
    checkout: Path, credentials: CredentialStore
) -> None:
    """The grader calls `teardown` on every path that never reaches verification, and only that
    path releases the services — a container left up holds its published port against the next
    case in the lane."""
    task = load_corpus(checkout)[0]
    env = TaskEnvironment(task, fabricate_pin(checkout), checkout, credentials)
    seen: list[tuple[str, ...]] = []

    async def record(*args: str, **kwargs: object) -> str:
        seen.append(args)
        return ""

    object.__setattr__(env, "_docker", record)
    await env.teardown(WORKSPACE)
    assert seen == [("rm", "--force", env.container_for(WORKSPACE))]


async def test_abandon_outlives_a_cancelled_case_and_keeps_the_real_error(
    checkout: Path, credentials: CredentialStore
) -> None:
    """A cancelled case is when services are most likely to be stranded, so the removal is shielded
    and runs to completion after the cancellation. It also swallows a failing daemon, because the
    caller is already raising the error that matters."""
    task = load_corpus(checkout)[0]
    env = TaskEnvironment(task, fabricate_pin(checkout), checkout, credentials)
    released, finished = asyncio.Event(), asyncio.Event()
    removed: list[tuple[str, ...]] = []

    async def record(*args: str, **kwargs: object) -> str:
        await released.wait()
        removed.append(args)
        finished.set()
        return ""

    object.__setattr__(env, "_docker", record)
    running = asyncio.create_task(env._abandon("ufo-handbook-cancelled"))
    await asyncio.sleep(0)
    running.cancel()
    await running
    assert removed == [], "the removal must still be in flight"
    released.set()
    await asyncio.wait_for(finished.wait(), CANCELLED_ABANDON_DEADLINE)
    assert removed == [("rm", "--force", "ufo-handbook-cancelled")]

    async def refuse(*args: str, **kwargs: object) -> str:
        raise DockerError("daemon is gone")

    object.__setattr__(env, "_docker", refuse)
    await env._abandon("ufo-handbook-cancelled")


async def test_reclaim_lane_removes_only_this_lanes_containers(
    checkout: Path, credentials: CredentialStore
) -> None:
    """A turn that ended uncleanly never reaches grading, so its container is still running with
    nothing to do. Only this lane's workspace can know that; another lane's services are live."""
    task = load_corpus(checkout)[0]
    env = TaskEnvironment(task, fabricate_pin(checkout), checkout, credentials)
    seen: list[tuple[str, ...]] = []

    async def record(*args: str, **kwargs: object) -> str:
        seen.append(args)
        return "abandoned-one\nabandoned-two\n" if args[0] == "ps" else ""

    object.__setattr__(env, "_docker", record)
    await env._reclaim_lane(WORKSPACE)
    listing = next(a for a in seen if a[0] == "ps")
    assert f"name={env.lane_filter(WORKSPACE)}" in listing
    assert WORKSPACE.hex[:8] in env.lane_filter(WORKSPACE)
    removed = next(a for a in seen if a[0] == "rm")
    assert "abandoned-one" in removed and "abandoned-two" in removed


def test_the_ingested_arm_skips_what_it_cannot_score_when_sweeping(
    checkout: Path, credentials: CredentialStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sweeping the corpus must not abort on a task the arm cannot score, and must not shrink the
    corpus silently either — a smaller denominator reads as a real result."""
    rubrics = checkout / "tasks" / TASK_ID / "tests" / "rubrics.json"
    rubrics.write_text(json.dumps([{"id": "r", "verifier_code": "open('SOP.html')"}]))
    install_pin(fabricate_pin(checkout, rubrics=1), pytest.MonkeyPatch())
    deps = IngestDeps(
        staging_root=tmp_path,
        blob=cast("BlobStore", None),
        index=cast("IndexBackend", None),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )
    with pytest.raises(ValueError, match=r"no HANDBOOK\.md task can be scored"):
        load_handbook(checkout, credentials, (), deps)
    assert TASK_ID in capsys.readouterr().out


def test_the_ingested_arm_returns_the_tasks_it_can_still_score(
    checkout: Path,
    credentials: CredentialStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of the sweep: one unscorable task must not take the rest of the corpus with
    it. Dropping every task instead would leave a run reporting a clean sweep of nothing."""
    second_id = "hr_ridgeline_gear_co_6950ff2b"
    second = checkout / "tasks" / second_id
    shutil.copytree(checkout / "tasks" / TASK_ID, second)
    rubrics = checkout / "tasks" / TASK_ID / "tests" / "rubrics.json"
    rubrics.write_text(json.dumps([{"id": "r", "verifier_code": "open('SOP.html')"}]))
    pinned = fabricate_pin(checkout).model_copy(
        update={
            "tasks": (
                PinnedTask(
                    task_id=TASK_ID,
                    rubrics=1,
                    tree_sha256=tree_digest(checkout / "tasks" / TASK_ID),
                ),
                PinnedTask(task_id=second_id, rubrics=2, tree_sha256=tree_digest(second)),
            )
        }
    )
    install_pin(pinned, monkeypatch)
    deps = IngestDeps(
        staging_root=tmp_path,
        blob=cast("BlobStore", None),
        index=cast("IndexBackend", None),
        embed=cast("EmbedClient", None),
        manifests=(),
        postgres=False,
    )
    assert [task.name for task in load_handbook(checkout, credentials, (), deps)] == [
        f"handbook.{second_id}"
    ]
    assert TASK_ID in capsys.readouterr().out


async def test_a_case_starts_without_the_previous_cases_notes(
    db: None, checkout: Path, tmp_path: Path
) -> None:
    """A fact the agent wrote with `memory_update` carries no page provenance, so the derived-fact
    purge never reached it and the previous task's notes came back in this task's searches — in one
    case, another tenant's persona on all thirty of them."""
    workspace_id = await _make_workspace()
    note = uuid4()
    index = DefaultIndex(transaction=workspace_tx)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(memory_store.memory_item).values(
                    id=note,
                    workspace_id=workspace_id,
                    subject="shared",
                    body="I coordinate appeals at Mojave Crest Assurance",
                    item_class="fact",
                    source_ref="agent",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await _index_facts(index, [note])
        ingest = _ingest(checkout, tmp_path / "staging", index)
        await ingest._clear_memory(workspace_id)
        left = await _memory_item_ids()
        indexed = await index.has_chunks(_fact_scope(note))
    assert left == [], "the previous case's notes must not reach this one"
    assert indexed is False, "its vector must go too"


async def test_settling_the_deriver_carries_it_to_the_indexers_high_water(
    db: None, checkout: Path, tmp_path: Path
) -> None:
    """Serve's page-change job runs every minute, and a derived paraphrase of a policy is what this
    arm exists to prevent, so the deriver's cursor is moved past every page the indexer just read.
    Left where it was, the next tick would walk the staged pages and summarise the handbook."""
    workspace_id = await _make_workspace()
    store = ScopedStore(extension=MEMORY_EXTENSION)
    indexed = f"{PAGE_CHANGE_CURSOR_KEY}:{INDEX_CONSUMER}"
    derived = f"{PAGE_CHANGE_CURSOR_KEY}:{DERIVE_CONSUMER}"
    ingest = _ingest(checkout, tmp_path / "staging")
    with ws(workspace_id):
        await ingest._settle_deriver()
        assert await store.get(derived) is None, "no high water leaves the cursor alone"
        await store.put(indexed, 47)
        await store.put(derived, 3)
        await ingest._settle_deriver()
        assert await store.get(derived) == 47


async def test_run_performs_every_step_in_the_order_it_depends_on(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each step exists because a previous run was wrong without it, and each depends on the last:
    purge before staging or the old documents survive; clear memory before the turn or the previous
    case's notes answer this one's searches; index before settling the deriver's cursor, because the
    cursor is carried from the indexer's high water; drop derived facts last, after the window in
    which serve's job could have derived. A step that exists but is never called reads as fixed."""
    order: list[str] = []
    ingest = _ingest(checkout, tmp_path / "staging")

    def record(name: str):
        async def step(*args: object, **kwargs: object) -> None:
            order.append(name)

        return step

    for name in (
        "_purge",
        "_clear_memory",
        "_index_pages",
        "_settle_deriver",
        "_drop_derived_facts",
    ):
        object.__setattr__(ingest, name, record(name))
    monkeypatch.setattr("evals.handbook.ingest.register_sources", record("register"))

    class _Driver:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def run(self) -> None:
            order.append("sync")

    monkeypatch.setattr("evals.handbook.ingest.SyncDriver", _Driver)
    staged = await ingest.run(WORKSPACE)

    assert staged == ("SOP.html",)
    assert order == [
        "_purge",
        "_clear_memory",
        "register",
        "sync",
        "_index_pages",
        "_settle_deriver",
        "_drop_derived_facts",
    ]


def test_service_tool_sets_drop_upstreams_file_tools(checkout: Path) -> None:
    """The agent's file surface is ufo's sandbox, so upstream's bash/read/write tools stay off."""
    sets = fabricate_pin(checkout).service_tool_sets
    assert "syntara_ds_all" not in sets
    assert "slack_core" in sets
