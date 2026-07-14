"""Persist eval runs, render their offline comparison viewer, and publish selected runs to S3."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from uuid import UUID, uuid4

from aiobotocore.session import get_session
from botocore.config import Config
from pydantic import BaseModel, ConfigDict

from evals.harness.harness import EvalReport

RUNS_DIR = "runs"
VIEWER_FILENAME = "index.html"
SHARE_TOKEN_BYTES = 24
MAX_SHARE_PAGE_BYTES = 32 * 1024 * 1024
MIN_SHARE_EXPIRY_SECONDS = 60
MAX_SHARE_EXPIRY_SECONDS = 7 * 24 * 60 * 60
AWS_S3_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
S3_COMPAT_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"})


class EvalRun(BaseModel):
    """One immutable runner invocation persisted for later comparison."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    created_at: datetime
    label: str
    agent: str
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
    """Persist one run and rebuild the archive viewer around every recorded run."""
    path = root / RUNS_DIR / f"{run.id}.json"
    write_atomic(path, run.model_dump_json(indent=2).encode())
    write_viewer(root, load_runs(root))
    return path


def write_viewer(root: Path, runs: tuple[EvalRun, ...]) -> Path:
    """Write the self-contained local viewer for the supplied archive."""
    path = root / VIEWER_FILENAME
    write_atomic(path, render_viewer(runs))
    return path


def render_viewer(
    runs: tuple[EvalRun, ...], current: UUID | None = None, baseline: UUID | None = None
) -> bytes:
    """Render an offline dashboard whose data and interface live in one shareable HTML asset."""
    payload = json.dumps(
        {
            "runs": [run.model_dump(mode="json") for run in runs],
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
