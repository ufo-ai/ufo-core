"""The jobs role: discovered JobSpecs (core + extensions) become live DBOS work at boot.

Registration is dynamic — `DBOS.apply_schedules` for cron jobs, an enqueue for one-shots — not the
import-time `@DBOS.scheduled` decorator, because jobs are discovered from the installed extensions
at boot, not known when this module is imported; `apply_schedules` upserts, so re-registration on
every restart is idempotent. Registration is the synchronous DBOS API: the async variants repoint
the running loop's default executor at DBOS's shared pool, so a short-lived boot loop closing would
shut that pool down. One durable workflow fires each handler with the extension's scoped
ExtensionContext, so a core job and an extension job run the identical path."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from dbos import DBOS, Queue, ScheduleInput

from selfhost.credentials import CredentialStore
from selfhost.ext.context import ExtensionContext, context_for
from selfhost.ext.manifest import JobSpec, Manifest
from selfhost.memory.indexer import MemoryIndexer
from selfhost.memory.service import MemoryService
from selfhost.o11y import log

JOB_QUEUE_NAME = "jobs"
JOB_WORKFLOW_NAME = "job"
CORE_EXTENSION = "core"
MEMORY_INDEX_JOB = "memory_index"
MEMORY_INDEX_SCHEDULE = "*/10 * * * * *"
JOB_QUEUE = Queue(JOB_QUEUE_NAME)


def core_jobs(indexer: MemoryIndexer) -> tuple[JobSpec, ...]:
    """The jobs a deploy always runs, before any extension's. The memory index job is core because
    recall is core: it derives — batch-at-interval, off the write path — the chunks recall reads."""

    async def _index(context: ExtensionContext) -> None:
        await indexer.run()

    return (JobSpec(name=MEMORY_INDEX_JOB, schedule=MEMORY_INDEX_SCHEDULE, handler=_index),)


@dataclass(frozen=True)
class _Binding:
    key: str
    extension: str
    declared: frozenset[str]
    spec: JobSpec


def bindings_from(
    manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]
) -> tuple[_Binding, ...]:
    """Core jobs live in the `core` namespace with no declared slots; each extension's jobs live in
    its own namespace with exactly the credential slots that extension declared."""
    bindings = [
        _Binding(
            key=f"{CORE_EXTENSION}:{spec.name}",
            extension=CORE_EXTENSION,
            declared=frozenset(),
            spec=spec,
        )
        for spec in core_jobs
    ]
    for manifest in manifests:
        declared = frozenset(slot.name for slot in manifest.credentials)
        bindings.extend(
            _Binding(
                key=f"{manifest.name}:{spec.name}",
                extension=manifest.name,
                declared=declared,
                spec=spec,
            )
            for spec in manifest.jobs
        )
    return tuple(bindings)


@dataclass(frozen=True)
class JobRunner:
    """Boot registration for one workspace: publish the firing table, then register each cron job
    and enqueue each one-shot. `fire` is the per-execution dispatch the durable workflow calls."""

    workspace_id: UUID
    credential_store: CredentialStore
    bindings: tuple[_Binding, ...]
    memory: MemoryService | None = None

    def launch(self) -> None:
        global _firing
        _firing = self
        schedules: list[ScheduleInput] = []
        for binding in self.bindings:
            if binding.spec.schedule is None:
                JOB_QUEUE.enqueue(job_workflow, datetime.now(UTC), binding.key)
                log("jobs.enqueued", key=binding.key)
            else:
                schedules.append(
                    ScheduleInput(
                        schedule_name=binding.key,
                        workflow_fn=job_workflow,
                        schedule=binding.spec.schedule,
                        context=binding.key,
                        queue_name=JOB_QUEUE_NAME,
                    )
                )
                log("jobs.scheduled", key=binding.key, schedule=binding.spec.schedule)
        if schedules:
            DBOS.apply_schedules(schedules)

    async def fire(self, key: str) -> None:
        binding = next((b for b in self.bindings if b.key == key), None)
        if binding is None:
            raise RuntimeError(f"no job registered for key {key!r}")
        context = context_for(
            self.workspace_id,
            binding.extension,
            binding.declared,
            self.credential_store,
            self.memory,
        )
        await binding.spec.handler(context)


_firing: JobRunner | None = None


@DBOS.workflow(name=JOB_WORKFLOW_NAME)
async def job_workflow(scheduled_time: datetime, key: str) -> None:
    runner = _firing
    if runner is None:
        raise RuntimeError("jobs not registered (JobRunner.launch runs in serve)")
    await runner.fire(key)
