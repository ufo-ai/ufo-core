"""The pinned HANDBOOK.md corpus, read from a local checkout of the upstream repository.

The repository holds only the pin (`data/upstream.json`) — never upstream prompts, handbooks,
rubrics, or working files. `load_corpus` verifies a checkout against the pin and returns the tasks
it declares, each carrying the digest of its own task tree so editing a task moves the suite digest
by itself."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DATA_ROOT = Path(__file__).parent / "data"
UPSTREAM_PIN = DATA_ROOT / "upstream.json"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
TASK_ID_PATTERN = r"^(finance|medical|insurance|logistics|hr)_[a-z0-9_]+_[0-9a-f]{8}$"
WORKSPACE_DIR = "environment/initial_workspace"
SERVICES_DIR = "environment/initial_external_services"
INSTRUCTION = "instruction.md"
SYSTEM_PROMPT = "system_prompt.md"
RUBRICS = "tests/rubrics.json"
VERIFIER = "tests/sop_verifier.py"
TASK_DOCKERFILE = "environment/Dockerfile"
DIGESTED_FILES = (INSTRUCTION, SYSTEM_PROMPT, RUBRICS, VERIFIER, TASK_DOCKERFILE)


class CorpusModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PinnedTask(CorpusModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    rubrics: int = Field(gt=0)
    tree_sha256: str = Field(pattern=DIGEST_PATTERN)


class UpstreamPin(CorpusModel):
    """Everything needed to prove a checkout is the corpus this suite was written against. The tool
    sets are identical across all upstream tasks, so they pin once here rather than per task."""

    repository: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(min_length=1)
    tool_sets: tuple[str, ...] = Field(min_length=1)
    file_tool_sets: tuple[str, ...] = Field(min_length=1)
    verifier_sha256: str = Field(pattern=DIGEST_PATTERN)
    tasks: tuple[PinnedTask, ...] = Field(min_length=1)

    @property
    def service_tool_sets(self) -> tuple[str, ...]:
        """The tool sets the environment container serves: upstream's set minus the file tool sets.
        The agent's file surface is ufo's own sandbox, so upstream's bash/read/write tools stay off
        and only the business services reach the model."""
        excluded = frozenset(self.file_tool_sets)
        return tuple(name for name in self.tool_sets if name not in excluded)


class HandbookTask(CorpusModel):
    """One verified task: the member's request, upstream's own preamble, and the resolved paths the
    environment and the case read from."""

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    instruction: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    rubrics: int = Field(gt=0)
    tree_sha256: str = Field(pattern=DIGEST_PATTERN)
    task_root: str = Field(min_length=1)

    @property
    def root(self) -> Path:
        return Path(self.task_root)

    @property
    def workspace_root(self) -> Path:
        return self.root / WORKSPACE_DIR

    @property
    def tests_root(self) -> Path:
        return self.root / "tests"

    @property
    def environment_root(self) -> Path:
        return self.root / "environment"


def load_pin() -> UpstreamPin:
    return UpstreamPin.model_validate_json(UPSTREAM_PIN.read_text())


def tree_digest(task_root: Path) -> str:
    """The task's identity: the digest of its instruction, preamble, rubrics, verifier, and
    Dockerfile, plus every seeded workspace and service file, name and bytes, in sorted order."""
    digest = sha256()
    for relative in DIGESTED_FILES:
        digest.update(relative.encode())
        digest.update(sha256((task_root / relative).read_bytes()).digest())
    for seeded in (WORKSPACE_DIR, SERVICES_DIR):
        root = task_root / seeded
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            digest.update(str(path.relative_to(task_root)).encode())
            digest.update(sha256(path.read_bytes()).digest())
    return f"sha256:{digest.hexdigest()}"


def load_corpus(checkout: Path, task_ids: tuple[str, ...] = ()) -> tuple[HandbookTask, ...]:
    """Verify a checkout against the pin and return the requested tasks (all of them when none are
    named). Every drift — a missing task, an edited handbook, a changed rubric, a patched verifier —
    fails loud here rather than producing a score against an unknown corpus."""
    pin = load_pin()
    tasks_root = checkout / "tasks"
    if not tasks_root.is_dir():
        raise ValueError(f"{checkout} is not a HANDBOOK.md checkout: no tasks/ directory")
    requested = frozenset(task_ids)
    unknown = sorted(requested - {task.task_id for task in pin.tasks})
    if unknown:
        raise ValueError(f"unknown HANDBOOK.md task ids: {', '.join(unknown)}")
    selected = tuple(task for task in pin.tasks if not requested or task.task_id in requested)
    loaded: list[HandbookTask] = []
    for pinned in selected:
        task_root = tasks_root / pinned.task_id
        if not task_root.is_dir():
            raise ValueError(f"HANDBOOK.md task {pinned.task_id!r} is absent from {checkout}")
        verifier = f"sha256:{sha256((task_root / VERIFIER).read_bytes()).hexdigest()}"
        if verifier != pin.verifier_sha256:
            raise ValueError(f"HANDBOOK.md verifier drifted in task {pinned.task_id!r}")
        digest = tree_digest(task_root)
        if digest != pinned.tree_sha256:
            raise ValueError(
                f"HANDBOOK.md task {pinned.task_id!r} drifted from the pin: "
                f"{digest} != {pinned.tree_sha256}"
            )
        loaded.append(
            HandbookTask(
                task_id=pinned.task_id,
                instruction=(task_root / INSTRUCTION).read_text().strip(),
                system_prompt=(task_root / SYSTEM_PROMPT).read_text().strip(),
                rubrics=pinned.rubrics,
                tree_sha256=pinned.tree_sha256,
                task_root=str(task_root),
            )
        )
    return tuple(loaded)
