"""The core-registered `artifact` object kind: files shared out of turns as workspace objects.

`share_file` is the sole producer — each share lands a `shared_artifact` row and its bytes in the
blob store — and the kind is the read/delete surface over those rows, one object per conversation
and filename: re-sharing a filename in the same conversation is a new version of the same object
(get, status, and the workspace copy reflect the latest share), while the same filename shared
from another conversation is a different file and a different object. Names carry both halves of
that identity — `<conversation-prefix>-<filename-slug>` (`3f2a9c1b-report-txt`, carried in the
share result), so one session's artifacts cluster together in a listing; a short digest suffix
appears only when distinct shares still collide on one name. Get renders the latest share and,
through status, copies its bytes back into the conversation workspace so a turn can reuse a file
an earlier turn produced; status also mints a fresh TTL download link. Create and update raise
`VerbNotSupported` naming `share_file`; delete removes every version's row and blob, after which
already-minted links stop serving (the download route 404s an absent blob)."""

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.agent_scope import agent_current
from ufo.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ARTIFACT_TOKEN_TTL_SECONDS,
    mint_artifact_token,
)
from ufo.blob import BlobNotFound
from ufo.conversations import CONVERSATION_KIND
from ufo.db import workspace_tx
from ufo.ext.context import JsonValue
from ufo.objects import (
    MATERIALIZE_MAX_BYTES,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRef,
    ObjectRow,
    VerbNotSupported,
    object_page,
)
from ufo.schema import tables
from ufo.tools.context import ToolContext
from ufo.workspace import ws_current

ARTIFACT_KIND = "artifact"
ARTIFACTS_ARE_SHARED = (
    "artifacts exist only by sharing — write the file in the workspace and share_file it"
)
ARTIFACT_WORKSPACE_DIR = "artifacts"
NAME_SLUG_MAX = 40
SUMMARY_MAX = 100
NAME_FALLBACK_SLUG = "artifact"
CONVERSATION_PREFIX_HEX = 8
COLLISION_DIGEST_HEX = 8
_SLUG_RUN = re.compile(r"[^a-z0-9]+")


def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]:
    """The object name for each distinct (conversation, filename) share identity: the
    conversation's hex prefix plus the filename's slug — one session's artifacts share a prefix,
    and the same filename from different sessions stays distinct. A short digest of the full
    identity is appended only where distinct identities still collide on one name."""
    identities = set(shares)
    base = {
        (conversation_id, filename): (
            f"{conversation_id.hex[:CONVERSATION_PREFIX_HEX]}-{_slug(filename)}"
        )
        for conversation_id, filename in identities
    }
    counts = Counter(base.values())
    return {
        identity: (
            name
            if counts[name] == 1
            else f"{name}-{_identity_digest(identity)[:COLLISION_DIGEST_HEX]}"
        )
        for identity, name in base.items()
    }


def _slug(filename: str) -> str:
    slug = _SLUG_RUN.sub("-", filename.lower()).strip("-")[:NAME_SLUG_MAX].rstrip("-")
    return slug or NAME_FALLBACK_SLUG


def _identity_digest(identity: tuple[UUID, str]) -> str:
    conversation_id, filename = identity
    return hashlib.sha256(f"{conversation_id}/{filename}".encode()).hexdigest()


class ArtifactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(description="The shared file's name, as share_file recorded it.")
    media_type: str = Field(description="The MIME type guessed from the filename at share time.")
    subject: str = Field(default="", description="The caption the latest share carried, if any.")


@dataclass(frozen=True)
class ArtifactObjects:
    """Read/delete handlers over the agent's `shared_artifact` rows, grouped by the sharing
    conversation and filename — each group's newest share is the object's current version.
    Another agent's artifacts are not found. `status` is where get's workspace copy happens: the
    seam calls `status` only on `object_get`, so `apply` and `delete` fetching the current spec
    never write into the workspace as a side effect."""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = tuple(
            ObjectRow(
                name=name,
                summary=_summary(shares),
                fields={
                    "filename": shares[0].filename,
                    "subject": shares[0].subject or "",
                },
            )
            for name, shares in await self._groups()
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None:
        shares = await self._find(name)
        if shares is None:
            return None
        latest = shares[0]
        return ObjectDetail(
            spec=ArtifactSpec(
                filename=latest.filename,
                media_type=latest.media_type,
                subject=latest.subject or "",
            ),
            created_at=shares[-1].created_at,
            updated_at=latest.created_at,
            links=(
                ObjectLink(
                    relation="created_in",
                    target=ObjectRef(kind=CONVERSATION_KIND, name=str(latest.conversation_id)),
                ),
            ),
        )

    async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None:
        shares = await self._find(name)
        if shares is None:
            return None
        latest = shares[0]
        url: str | None = None
        if ctx.artifact_token_secret:
            expires_at = int(datetime.now(UTC).timestamp()) + ARTIFACT_TOKEN_TTL_SECONDS
            token = mint_artifact_token(
                ctx.artifact_token_secret, latest.blob_key, latest.filename, expires_at
            )
            url = f"{ARTIFACT_DOWNLOAD_PATH}?token={token}"
        return {
            "size_bytes": latest.size_bytes,
            "shared_at": latest.created_at.isoformat(),
            "turn_id": str(latest.turn_id),
            "versions": len(shares),
            "download_url": url,
            "workspace_path": await self._materialize(ctx, name, latest),
        }

    async def apply(
        self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None
    ) -> None:
        raise VerbNotSupported(ARTIFACTS_ARE_SHARED)

    async def delete(self, ctx: ToolContext, name: str) -> None:
        shares = await self._find(name)
        if shares is None:
            raise ValueError(f"no artifact named {name!r}")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.shared_artifact).where(
                    tables.shared_artifact.c.workspace_id == ws_current().workspace_id,
                    tables.shared_artifact.c.blob_key.in_([share.blob_key for share in shares]),
                )
            )
        for share in shares:
            await ctx.blob.delete(share.blob_key)

    async def _materialize(self, ctx: ToolContext, name: str, latest: sa.Row) -> str | None:
        if latest.size_bytes > MATERIALIZE_MAX_BYTES:
            return None
        try:
            data = await ctx.blob.get(latest.blob_key)
        except BlobNotFound as error:
            raise ValueError(
                f"artifact {name!r} has no stored bytes under {latest.blob_key!r}"
            ) from error
        path = f"{ARTIFACT_WORKSPACE_DIR}/{name}/{latest.filename}"
        await ctx.sandbox.write_file(path, data)
        return path

    async def _find(self, name: str) -> tuple[sa.Row, ...] | None:
        matched = [shares for candidate, shares in await self._groups() if candidate == name]
        return matched[0] if matched else None

    async def _groups(self) -> Sequence[tuple[str, tuple[sa.Row, ...]]]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.turn_id,
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.size_bytes,
                        tables.shared_artifact.c.created_at,
                        tables.turn.c.conversation_id,
                    )
                    .select_from(
                        tables.shared_artifact.join(
                            tables.turn, tables.shared_artifact.c.turn_id == tables.turn.c.id
                        )
                    )
                    .where(
                        tables.shared_artifact.c.workspace_id == ws_current().workspace_id,
                        tables.turn.c.agent_id == agent_current().agent_id,
                    )
                )
            ).all()
        by_identity: dict[tuple[UUID, str], list[sa.Row]] = {}
        for row in rows:
            by_identity.setdefault((row.conversation_id, row.filename), []).append(row)
        names = artifact_object_names(by_identity)
        groups = [
            (
                names[identity],
                tuple(sorted(shares, key=lambda r: (r.created_at, r.blob_key), reverse=True)),
            )
            for identity, shares in by_identity.items()
        ]
        return sorted(groups, key=lambda pair: pair[0])


def _summary(shares: tuple[sa.Row, ...]) -> str:
    latest = shares[0]
    versions = f", {len(shares)} versions" if len(shares) > 1 else ""
    return (
        f"{latest.filename} ({latest.media_type}, {latest.size_bytes} bytes), "
        f"shared {latest.created_at.date().isoformat()}{versions}"
    )[:SUMMARY_MAX]


ARTIFACT_OBJECT = ObjectKind(
    name=ARTIFACT_KIND,
    description=(
        "A file shared out of a turn by share_file, one object per conversation and filename — "
        "re-shares in the same conversation are versions: list this agent's shared files, "
        "get one to copy its latest bytes back into the workspace, delete to remove every "
        "stored version. Create and update are refused — share_file is the producer."
    ),
    guidance=(
        "Files shared with members by share_file, one object per conversation and filename, "
        "named <conversation-prefix>-<filename-slug> (3f2a9c1b-report-txt; the share result "
        "carries the name), so one session's artifacts share a prefix and the same filename "
        "from different sessions stays distinct. Re-sharing a filename in the same conversation "
        "adds a version — get, status, and the workspace copy reflect the latest share. Another "
        "agent's artifacts are not found. "
        "object_get copies the latest bytes back into the conversation workspace at "
        "artifacts/<name>/<filename> — the way to reuse a file an earlier turn produced — its "
        "status carries a fresh member download link (valid one hour), the share time, the "
        "sharing turn, and the version count, and its `created_in` link names the sharing "
        "conversation; a file over 32 MiB is not copied "
        "(workspace_path is null) and is fetched via the link instead. Create and update are "
        "refused: an artifact exists by sharing a produced file, so write the file in the "
        "workspace and share_file it. Delete removes the record and stored bytes of every "
        "version; existing download links stop serving."
    ),
    spec_model=ArtifactSpec,
    store=ArtifactObjects(),
    list_fields=frozenset({"filename", "subject"}),
)
