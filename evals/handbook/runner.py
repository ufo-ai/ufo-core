"""Each pinned HANDBOOK.md task runs as one capability case: the handbook and its working files
are staged into the conversation workspace, upstream's own preamble and request are preserved ahead
of a fixed envelope, and upstream's rubric verifier scores the finished workspace and the mutated
services in the environment container.

The case passes only when every rubric passes — upstream's own bar. The scorecard rides the verdict
evidence, so the run archive shows which rubrics failed and why."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

from evals.handbook.corpus import HandbookTask, UpstreamPin, load_corpus, load_pin
from evals.handbook.environment import SERVER_NAME, TaskEnvironment, VerifierResults
from evals.handbook.ingest import DOCUMENT_SUFFIXES, DocumentIngest
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    WorkspaceFile,
)
from evals.harness.harness import Json, JsonObject
from evals.harness.registry import EvalTask, capability_task, rewrapped
from ufo.blob import WorkspaceBlobStore
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.indexing import EmbedClient, IndexBackend
from ufo.runtime.workspace import ws_current

HANDBOOK_PACKS = ("assistant", "assistant_eval")
HANDBOOK_BACKENDS = ("local", "docker")
ENVELOPE_REVISION = "workspace-only-scratch-4"
WORKSPACE_ROOT = "/workspace"
WORKFLOW_WAIT_SECONDS = 3_600.0


@dataclass(frozen=True)
class IngestDeps:
    """What indexing a task's policy documents needs. Present only for the ingested arm — the
    default arm leaves the documents in the workspace for the agent to read, as upstream does."""

    staging_root: Path
    blob: WorkspaceBlobStore
    index: IndexBackend
    embed: EmbedClient
    manifests: tuple[Manifest, ...]
    postgres: bool


@dataclass(frozen=True)
class CasePrepare:
    """The case's prepare hook, in order: index the policy documents when the arm calls for it, then
    bring the services up over the conversation's own workspace."""

    environment: TaskEnvironment
    ingest: DocumentIngest | None

    async def __call__(self, workspace_id: UUID, workspace_dir: Path) -> None:
        if self.ingest is not None:
            await self.ingest.run(workspace_id)
        await self.environment.start(workspace_id, workspace_dir)


def load_handbook(
    checkout: Path,
    credentials: CredentialStore,
    task_ids: tuple[str, ...] = (),
    ingest: IngestDeps | None = None,
) -> tuple[EvalTask, ...]:
    """Verify the checkout against the pin and build one exclusive task per case. Cases are
    exclusive because the workspace's `mcp_servers` slot points at exactly one task's services at a
    time."""
    pin = load_pin()
    tasks = load_corpus(checkout, task_ids)
    if ingest is not None:
        tasks = _scorable_ingested(tasks, requested=bool(task_ids))
    return tuple(
        rewrapped(
            capability_task(
                f"handbook.{task.task_id}",
                (_capability_case(task, pin, checkout, credentials, ingest),),
            ),
            lambda built: replace(built, pin_runtime=True, exclusive=True),
        )
        for task in tasks
    )


def _scorable_ingested(
    tasks: tuple[HandbookTask, ...], requested: bool
) -> tuple[HandbookTask, ...]:
    """The ingested arm withholds the policy documents, so a task whose rubrics assert on one — or
    whose workspace holds nothing else — cannot be scored in it. Naming such a task is an error
    worth raising; sweeping the corpus is not, so those tasks are dropped — and named, because a
    silently smaller corpus reads as a real result."""
    scorable: list[HandbookTask] = []
    skipped: list[str] = []
    for task in tasks:
        if _rubrics_need_the_documents(task):
            skipped.append(f"{task.task_id} (its rubrics assert on a policy document)")
        elif _stages_only_documents(task):
            skipped.append(f"{task.task_id} (it stages nothing but documents)")
        else:
            scorable.append(task)
    if skipped and requested:
        raise ValueError(
            "these HANDBOOK.md tasks cannot be scored in the ingested arm, which withholds the "
            f"policy documents: {', '.join(skipped)}"
        )
    if skipped:
        print(
            f"handbook: ingested arm skips {len(skipped)} of {len(tasks)} tasks it cannot score: "
            f"{', '.join(skipped)}"
        )
    if not scorable:
        raise ValueError("no HANDBOOK.md task can be scored in the ingested arm")
    return tuple(scorable)


def _capability_case(
    task: HandbookTask,
    pin: UpstreamPin,
    checkout: Path,
    credentials: CredentialStore,
    ingest: IngestDeps | None = None,
) -> CapabilityCase:
    environment = TaskEnvironment(task, pin, checkout, credentials)
    files = _workspace_files(task, ingested=ingest is not None)
    documents = (
        None
        if ingest is None
        else DocumentIngest(
            task=task,
            staging_root=ingest.staging_root,
            blob=ingest.blob,
            index=ingest.index,
            embed=ingest.embed,
            manifests=ingest.manifests,
            postgres=ingest.postgres,
        )
    )
    arm = "ingested" if ingest is not None else "in-workspace"
    return CapabilityCase(
        name=task.task_id,
        message=f"{task.system_prompt}\n\n{task.instruction}"
        + _envelope(tuple(file.path for file in files), ingested=ingest is not None),
        grader=RubricVerifier(environment, task, documents),
        digest_tag=(
            f"{pin.revision}:{ENVELOPE_REVISION}:{arm}:wait-{WORKFLOW_WAIT_SECONDS:g}:"
            f"{task.task_id}:{task.tree_sha256}"
        ),
        workspace_files=files,
        prepare=CasePrepare(environment, documents),
    )


def _workspace_files(task: HandbookTask, ingested: bool = False) -> tuple[WorkspaceFile, ...]:
    """Upstream stages its working files at the root of `/workdir`; they land at the root of the
    conversation workspace so the handbook and its spreadsheets sit where the request expects.

    The ingested arm withholds the policy documents. Left on disk they are simply read: the agent
    searches once, decides snippets are not the authority, and opens the file anyway — so the arm
    would measure retrieval plus reading rather than retrieval instead of it. The knowledge base is
    the only route to policy in a deployment that has ingested it."""
    root = task.workspace_root
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    if ingested:
        paths = [path for path in paths if path.suffix.lower() not in DOCUMENT_SUFFIXES]
    if not paths:
        raise ValueError(f"HANDBOOK.md task {task.task_id!r} stages no workspace files")
    return tuple(
        WorkspaceFile(path=str(path.relative_to(root)), content=path.read_bytes()) for path in paths
    )


def _rubrics_need_the_documents(task: HandbookTask) -> tuple[str, ...]:
    """Policy document filenames some rubric checks for in the workspace. Withholding a document a
    rubric asserts on would fail the case for a harness reason wearing a capability's clothes, so
    the ingested arm refuses such a task rather than scoring it."""
    rubrics = (task.tests_root / "rubrics.json").read_text()
    return tuple(
        path.name
        for path in sorted(task.workspace_root.rglob("*"))
        if path.suffix.lower() in DOCUMENT_SUFFIXES and path.name in rubrics
    )


def _stages_only_documents(task: HandbookTask) -> bool:
    """`load_handbook` builds every case up front, so a task the ingested arm cannot stage raises
    before any case in its lane runs and takes the innocent ones with it."""
    files = [path for path in task.workspace_root.rglob("*") if path.is_file()]
    return bool(files) and all(path.suffix.lower() in DOCUMENT_SUFFIXES for path in files)


def _envelope(paths: tuple[str, ...], ingested: bool = False) -> str:
    """Upstream's preamble names `/workdir` as the filesystem, which is where its own harness mounts
    the task's files. Here they are in the sandbox at `WORKSPACE_ROOT`, so the envelope says so and
    lists them by absolute path — otherwise every case burns its opening rounds on a directory that
    does not exist, and a handbook read under `/workdir` is refused outright as a path escape."""
    files = "\n".join(f"- {WORKSPACE_ROOT}/{path}" for path in paths)
    searchable = (
        "The company's policy documents are not on disk — they are in the knowledge base. Use "
        "`memory_search` to find the rules that govern this task and `object_get` on a page ref to "
        "read a section in full; search again whenever you need a threshold or an exact wording. "
        if ingested
        else ""
    )
    return (
        "\n\n---\n"
        f"Your workspace is {WORKSPACE_ROOT} — where the task text says /workdir, it means "
        f"{WORKSPACE_ROOT}. It already contains:\n"
        f"{files}\n"
        f"{searchable}"
        f"The company's mail, Slack, calendar, Jira, and storefront are tools on the "
        f"{SERVER_NAME!r} MCP server; call `list_mcp_tools` first and use each tool's own argument "
        "schema rather than the real product's API parameter names. Edit the workspace files in "
        f"place where the task calls for it, and keep anything you generate under {WORKSPACE_ROOT} "
        "— nothing outside it is readable, including /tmp."
    )


@dataclass(frozen=True)
class RubricVerifier:
    """Runs upstream's rubric verifier against the finished environment and reports its scorecard.
    The case passes only on a clean sweep, which is upstream's own bar; the per-rubric verdicts ride
    the evidence either way so a partial score is legible in the archive."""

    environment: TaskEnvironment
    task: HandbookTask
    documents: DocumentIngest | None = None

    @property
    def grading(self) -> str:
        return (
            f"All {self.task.rubrics} upstream HANDBOOK.md rubrics pass, scored by "
            f"tests/sop_verifier.py over the finished workspace and the mutated services."
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        """The grader runs inside the eval's workspace scope, which is how it names the same
        container `prepare` raised — the workspace is not known when the case is built."""
        workspace_id = ws_current().workspace_id
        if output.workspace_dir is None:
            await self.environment.teardown(workspace_id)
            return CapabilityVerdict(False, "the turn exposed no workspace directory to grade")
        if self.documents is not None:
            derived = await self.documents.derived_facts(workspace_id)
            if derived:
                await self.environment.teardown(workspace_id)
                return CapabilityVerdict(
                    False,
                    f"{len(derived)} memory items were derived from this task's policy documents "
                    "during the turn; the ingested arm scores verbatim retrieval, so this case is "
                    "excluded rather than scored",
                    excluded=True,
                )
        results = await self.environment.verify(workspace_id, Path(output.workspace_dir))
        evidence = _evidence(results)
        if results.rubrics_total != self.task.rubrics:
            return CapabilityVerdict(
                False,
                f"verifier scored {results.rubrics_total} rubrics, pin declares "
                f"{self.task.rubrics}",
                evidence,
            )
        reason = (
            f"{results.rubrics_passed}/{results.rubrics_total} rubrics passed; "
            f"score={results.score:.2f}"
        )
        if not results.passed:
            failed = [item.id for item in results.rubric_results if not item.passed]
            reason += f"; failed: {', '.join(failed[:5])}"
        return CapabilityVerdict(results.passed, reason, evidence)


def _evidence(results: VerifierResults) -> JsonObject:
    rubrics: list[Json] = [
        {
            "id": item.id,
            "passed": item.passed,
            "score": item.score,
            "feedback": item.feedback[:1_000],
        }
        for item in results.rubric_results
    ]
    return {
        "rubricsPassed": results.rubrics_passed,
        "rubricsTotal": results.rubrics_total,
        "score": results.score,
        "rubrics": rubrics,
    }
