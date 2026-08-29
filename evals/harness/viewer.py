"""Persist eval runs, render their offline comparison viewer, and publish selected runs to S3."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from uuid import UUID, uuid4

from aiobotocore.session import get_session
from botocore.config import Config
from pydantic import BaseModel, ConfigDict

from evals.harness.capability import recorded_evidence_missing
from evals.harness.harness import EvalReport
from ufo.schema.records import RuntimeAttestation

RUNS_DIR = "runs"
VIEWER_FILENAME = "index.html"
SHARE_TOKEN_BYTES = 24
MAX_SHARE_PAGE_BYTES = 32 * 1024 * 1024
MIN_SHARE_EXPIRY_SECONDS = 60
MAX_SHARE_EXPIRY_SECONDS = 7 * 24 * 60 * 60
AWS_S3_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
S3_COMPAT_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"})


class EvalRun(BaseModel):
    """One immutable runner invocation persisted for later comparison. `agent_prompt` is the
    target agent's configured base prompt — the fixed half of every case's system prompt — so a
    reviewer reads the setup the agent answered under without reaching for the workspace.
    `workspace_id` names the durable workspace whose state and remote turns belong to the run."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    created_at: datetime
    label: str
    agent: str
    agent_prompt: str = ""
    workspace_id: UUID | None = None
    runtime: RuntimeAttestation | None = None
    ufo_version: str
    revision: str
    reports: tuple[EvalReport, ...]


def load_runs(root: Path) -> tuple[EvalRun, ...]:
    """Read every persisted run newest-first; a corrupt run makes the archive fail loud."""
    directory = root / RUNS_DIR
    if not directory.exists():
        return ()
    runs: list[EvalRun] = []
    for path in sorted(directory.glob("*.json")):
        try:
            runs.append(EvalRun.model_validate_json(path.read_text()))
        except Exception as error:
            raise RuntimeError(f"invalid eval run {path}: {error}") from error
    return tuple(sorted(runs, key=lambda run: run.created_at, reverse=True))


def write_atomic(path: Path, data: bytes) -> None:
    """Replace one archive file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def record_run(root: Path, run: EvalRun) -> Path:
    """Persist one run and rebuild the archive viewer around every recorded run. The archive is
    the debugging record, so a case arriving without its evidence — a null prompt or response —
    is refused here, never stored as a case that looks validly empty."""
    for report in run.reports:
        for case in report.cases:
            missing = recorded_evidence_missing(case)
            if missing:
                raise ValueError(f"case {case.name!r} was recorded without evidence: {missing}")
    path = root / RUNS_DIR / f"{run.id}.json"
    write_atomic(path, run.model_dump_json(indent=2, by_alias=True, exclude_none=True).encode())
    write_viewer(root, load_runs(root))
    return path


@dataclass
class RunRecorder:
    """One run's record, rewritten every time a suite finishes. A record written once at the end is
    a record the next raise deletes: a shard runs its suites in one process, so one suite raising —
    or the workflow step's own deadline killing the process — used to discard every finished
    suite's report with it. Recording per suite keeps the reports already in hand on disk under the
    run's own id, and the run rewrites in place as the rest arrive, so a salvaged shard reads as one
    partial run rather than a pile of fragments.

    A report is recorded finished, digest and all, so a partial record compares against a trend line
    exactly as the complete one would."""

    root: Path
    id: UUID
    created_at: datetime
    label: str
    agent: str
    ufo_version: str
    revision: str
    agent_prompt: str = ""
    workspace_id: UUID | None = None
    runtime: RuntimeAttestation | None = None
    reports: dict[int, EvalReport] = field(default_factory=dict)

    def record(self, index: int, report: EvalReport) -> Path:
        """Add one finished suite's report, keyed by its position in the run's task list, and
        rewrite the run around every report recorded so far."""
        self.reports[index] = report
        return record_run(self.root, self.run())

    def run(self) -> EvalRun:
        return EvalRun(
            id=self.id,
            created_at=self.created_at,
            label=self.label,
            agent=self.agent,
            agent_prompt=self.agent_prompt,
            workspace_id=self.workspace_id,
            runtime=self.runtime,
            ufo_version=self.ufo_version,
            revision=self.revision,
            reports=tuple(self.reports[index] for index in sorted(self.reports)),
        )


def write_viewer(root: Path, runs: tuple[EvalRun, ...]) -> Path:
    """Write the self-contained local viewer for the supplied archive."""
    path = root / VIEWER_FILENAME
    write_atomic(path, render_viewer(runs))
    return path


def render_viewer(
    runs: tuple[EvalRun, ...], current: UUID | None = None, baseline: UUID | None = None
) -> bytes:
    """Render the dashboard whose data and interface live in one offline HTML asset — the same
    page locally and behind a `--share` URL: shares are private, expiring, and carry the whole
    record."""
    payload = json.dumps(
        {
            "runs": [run.model_dump(mode="json", by_alias=True, exclude_none=True) for run in runs],
            "current": str(current) if current is not None else "",
            "baseline": str(baseline) if baseline is not None else "",
        },
        separators=(",", ":"),
    )
    safe_payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return VIEWER_PAGE.replace("__EVAL_PAYLOAD__", safe_payload).encode()


@dataclass(frozen=True)
class S3ViewerShare:
    """Publish one viewer asset to a private S3 bucket and mint its expiring read URL."""

    bucket: str
    region: str | None = None
    endpoint_url: str | None = None

    async def publish(self, page: bytes, expiry_seconds: int) -> str:
        if len(page) > MAX_SHARE_PAGE_BYTES:
            raise ValueError(f"share page exceeds {MAX_SHARE_PAGE_BYTES} bytes")
        if not MIN_SHARE_EXPIRY_SECONDS <= expiry_seconds <= MAX_SHARE_EXPIRY_SECONDS:
            raise ValueError(
                f"share expiry must be {MIN_SHARE_EXPIRY_SECONDS}.."
                f"{MAX_SHARE_EXPIRY_SECONDS} seconds"
            )
        key = f"eval-viewers/{token_urlsafe(SHARE_TOKEN_BYTES)}.html"
        expires_at = datetime.now(UTC) + timedelta(seconds=expiry_seconds)
        async with get_session().create_client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            config=S3_COMPAT_CONFIG if self.endpoint_url is not None else AWS_S3_CONFIG,
        ) as client:
            await client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=page,
                ContentType="text/html; charset=utf-8",
                CacheControl="private, no-store",
                Expires=expires_at,
            )
            return await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiry_seconds,
            )


VIEWER_PAGE = Path(__file__).with_name("viewer.html").read_text()
