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
already-minted links stop serving (the download route 404s an absent blob). A signed-in member
reads shares made after a member entered their conversation in the portal, under the same two
scopes — the selected agent and the subjects their own conversation carries — while agent reads
stay whole and the bytes stay behind the turn."""

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.blob import BlobNotFound
from ufo.db import workspace_tx
from ufo.host.kinds.conversations import CONVERSATION_KIND
from ufo.runtime.authority import authority_member_id
from ufo.runtime.ext.context import ExtensionContext, JsonValue
from ufo.runtime.media.artifact_url import (
    TEXT_APPLICATION_MEDIA_TYPES,
    TEXT_MEDIA_PREFIX,
    artifact_url_expiry,
    is_text_media,
    mint_artifact_url,
    mint_image_preview_url,
)
from ufo.runtime.media.image_previews import raster_image_media_type
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.object_scope import object_agent_id
from ufo.runtime.objects import (
    MATERIALIZE_MAX_BYTES,
    MemberObject,
    ObjectDetail,
    ObjectKind,
    ObjectLink,
    ObjectListQuery,
    ObjectPage,
    ObjectRow,
    UnknownObject,
    VerbNotSupported,
    object_page,
)
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.turns.audience import audience_subjects, conversation_audience
from ufo.runtime.workspace import ws_current
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, TurnContext

ARTIFACT_KIND = "artifact"
ARTIFACTS_ARE_SHARED = (
    "artifacts exist only by sharing — write the file in the workspace and share_file it"
)
ARTIFACT_WORKSPACE_DIR = "artifacts"
ARTIFACT_SCAN_LIMIT = 500
NAME_SLUG_MAX = 40
SUMMARY_MAX = 100
NAME_FALLBACK_SLUG = "artifact"
CONVERSATION_PREFIX_HEX = 8
COLLISION_DIGEST_HEX = 8
WEB_SURFACE = "web"
MEDIA_IMAGE_PREFIX = "image/"
MEDIA_OFFICE_PREFIX = "application/vnd.openxmlformats-officedocument"
MEDIA_DOCUMENT_TYPES = frozenset({"application/pdf", "application/msword"})
_SLUG_RUN = re.compile(r"[^a-z0-9]+")


def artifact_media(media_type: str) -> str:
    """The coarse category a listing filters files by — `image`, `document`, or `other` — so
    `media=image` is an exact match over a declared field rather than a bespoke query grammar. A
    document is anything readable as text, an office file, a pdf, or a Word file; `_document_media`
    is the same answer as a where clause."""
    lowered = media_type.lower()
    if lowered.startswith(MEDIA_IMAGE_PREFIX):
        return "image"
    if (
        is_text_media(lowered)
        or lowered.startswith(MEDIA_OFFICE_PREFIX)
        or lowered in MEDIA_DOCUMENT_TYPES
    ):
        return "document"
    return "other"


def _document_media() -> sa.ColumnElement[bool]:
    lowered = sa.func.lower(tables.shared_artifact.c.media_type)
    return sa.or_(
        lowered.like(TEXT_MEDIA_PREFIX + "%"),
        lowered.like(MEDIA_OFFICE_PREFIX + "%"),
        lowered.in_(TEXT_APPLICATION_MEDIA_TYPES | MEDIA_DOCUMENT_TYPES),
    )


def _member_participated() -> sa.ColumnElement[bool]:
    member_turn = tables.turn.alias("artifact_member_turn")
    return (
        sa.select(sa.literal(1))
        .where(
            member_turn.c.workspace_id == tables.turn.c.workspace_id,
            member_turn.c.conversation_id == tables.turn.c.conversation_id,
            member_turn.c.admission_source == MEMBER_ADMISSION,
            member_turn.c.seq <= tables.turn.c.seq,
        )
        .correlate(tables.turn)
        .exists()
    )


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
    """Read/delete handlers over the selected agent's audience-visible `shared_artifact` rows,
    grouped by the sharing conversation and filename — each group's newest share is the object's
    current version. `status` is where get's workspace copy happens: the seam calls `status` only
    on `object_get`, so `apply` and `delete` fetching the current spec never write into the
    workspace as a side effect. The deploy's link minting rides construction — a listing row
    publishes the signed download and preview links the portal draws, and a deploy that mints no
    artifact links lists the same rows with null links. The member projection is the Artifacts
    index: it includes only shares made after a member entered that conversation. Agent reads stay
    whole so a machine-lane file remains reusable by the agent that produced it."""

    public_base_url: str | None = None
    artifact_token_secret: str = ""

    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        rows = await self._rows(
            ctx.read_subjects,
            authority_member_id(ctx.authority),
            query,
            self._shares(ctx.read_subjects),
        )
        return object_page(rows, query)

    async def member_page(
        self,
        ext: ExtensionContext | None,
        *,
        member_id: UUID,
        admin: bool,
        query: ObjectListQuery,
    ) -> ObjectPage:
        """The selected agent's Artifacts index for one signed-in member: files shared after a
        member entered their conversation, inside the subjects this reader's own conversation
        carries. Every reader — an admin included — stays behind that audience fence, so another
        member's private file and every room file remain absent."""
        subjects = audience_subjects(conversation_audience(member_id))
        rows = await self._rows(
            subjects,
            member_id,
            query,
            self._member_shares(subjects),
        )
        return object_page(rows, query)

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None:
        shares = await self._find(ctx.read_subjects, name, self._shares(ctx.read_subjects))
        return None if shares is None else _detail(shares)

    async def member_detail(
        self,
        ext: ExtensionContext | None,
        name: str,
        *,
        member_id: UUID,
        admin: bool,
    ) -> MemberObject[ArtifactSpec] | None:
        """One file from the Artifacts index: the shares after a member entered its conversation,
        on the same agent and audience reach `member_page` lists under. Bytes stay behind the turn:
        the workspace copy `status` writes is not this read, though the row carries the same signed
        links the listing publishes."""
        subjects = audience_subjects(conversation_audience(member_id))
        shares = await self._find(subjects, name, self._member_shares(subjects))
        if shares is None:
            return None
        sources = await self._sources((shares[0].conversation_id,))
        return MemberObject(row=self._row(name, shares, member_id, sources), detail=_detail(shares))

    async def status(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> dict[str, JsonValue] | None:
        shares = await self._find(ctx.read_subjects, name, self._shares(ctx.read_subjects))
        if shares is None:
            return None
        latest = shares[0]
        data: bytes | None = None
        if latest.size_bytes <= MATERIALIZE_MAX_BYTES:
            try:
                data = await ctx.blob.get(latest.blob_key)
            except BlobNotFound as error:
                raise ValueError(
                    f"artifact {name!r} has no stored bytes under {latest.blob_key!r}"
                ) from error
        async with workspace_tx() as connection:
            if (
                await connection.execute(self._unchanged_visible(ctx, latest))
            ).scalar_one_or_none() is None:
                raise UnknownObject(f"no artifact object named {name!r}")
        path: str | None = None
        if data is not None:
            path = f"{ARTIFACT_WORKSPACE_DIR}/{name}/{latest.filename}"
            await ctx.sandbox.write_file(path, data)
        url: str | None = None
        if ctx.artifact_token_secret:
            expires_at = artifact_url_expiry(datetime.now(UTC))
            url = mint_artifact_url(
                ctx.artifact_token_secret,
                latest.blob_key,
                expires_at,
                workspace_id=ws_current().workspace_id,
            )
        return {
            "size_bytes": latest.size_bytes,
            "shared_at": latest.created_at.isoformat(),
            "turn_id": str(latest.turn_id),
            "versions": len(shares),
            "download_url": url,
            "workspace_path": path,
        }

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: ArtifactSpec,
        old: ArtifactSpec | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise VerbNotSupported(ARTIFACTS_ARE_SHARED)

    async def delete(
        self,
        ctx: ToolContext,
        name: str,
        *,
        expected_generation: UUID | None,
    ) -> None:
        shares = await self._find(ctx.read_subjects, name, self._shares(ctx.read_subjects))
        if shares is None:
            raise ValueError(f"no artifact named {name!r}")
        async with workspace_tx() as connection:
            if (
                await connection.execute(self._unchanged_visible(ctx, shares[0]).with_for_update())
            ).scalar_one_or_none() is None:
                raise ValueError(f"artifact {name!r} changed while deleting")
            deleted = await connection.execute(
                sa.delete(tables.shared_artifact).where(
                    tables.shared_artifact.c.workspace_id == ws_current().workspace_id,
                    tables.shared_artifact.c.blob_key.in_([share.blob_key for share in shares]),
                )
            )
            if deleted.rowcount != len(shares):
                raise ValueError(f"artifact {name!r} lost a version while deleting")
        for share in shares:
            await ctx.blob.delete(share.blob_key)
            if share.preview_blob_key is not None:
                await ctx.blob.delete(share.preview_blob_key)

    def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select:
        return sa.select(tables.conversation.c.id).where(
            tables.conversation.c.workspace_id == ws_current().workspace_id,
            tables.conversation.c.id == latest.conversation_id,
            tables.conversation.c.agent_id == object_agent_id(),
            tables.conversation.c.audience == latest.audience,
            tables.conversation.c.audience.in_(ctx.read_subjects),
        )

    async def _rows(
        self,
        subjects: frozenset[str],
        viewer: UUID | None,
        query: ObjectListQuery,
        shares: sa.Select,
    ) -> tuple[ObjectRow, ...]:
        groups = await self._groups(subjects, viewer, query, shares)
        sources = await self._sources(
            tuple({shares[0].conversation_id for _name, shares in groups})
        )
        return tuple(self._row(name, shares, viewer, sources) for name, shares in groups)

    async def _find(
        self, subjects: frozenset[str], name: str, shares: sa.Select
    ) -> tuple[sa.Row, ...] | None:
        """One named group whole inside the supplied projection. The name resolves against every
        identity the audience fence admits — bounded by the workspace's distinct files rather than
        its shares — and the projected rows arrive with no window, so a name read answers even when
        its newest matching share fell past the listing's scan."""
        names = await self._identities(subjects)
        identity = next((candidate for candidate, held in names.items() if held == name), None)
        if identity is None:
            return None
        conversation_id, filename = identity
        query = shares.where(
            tables.turn.c.conversation_id == conversation_id,
            tables.shared_artifact.c.filename == filename,
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        if not rows:
            return None
        return tuple(sorted(rows, key=lambda r: (r.created_at, r.blob_key), reverse=True))

    async def _groups(
        self,
        subjects: frozenset[str],
        viewer: UUID | None,
        query: ObjectListQuery,
        shares: sa.Select,
    ) -> Sequence[tuple[str, tuple[sa.Row, ...]]]:
        """The reader's shares grouped per file, off the newest bounded scan after column filters.
        `_find` answers a named identity whole, and names come off the whole identity set."""
        narrowed = shares
        if query.query:
            like = f"%{query.query}%"
            narrowed = narrowed.where(
                sa.or_(
                    tables.shared_artifact.c.filename.ilike(like),
                    tables.shared_artifact.c.subject.ilike(like),
                )
            )
        conversation = query.filters.get("conversation")
        if isinstance(conversation, str):
            try:
                narrowed = narrowed.where(tables.turn.c.conversation_id == UUID(conversation))
            except ValueError:
                narrowed = narrowed.where(sa.false())
        if query.filters.get("mine") is True and viewer is not None:
            narrowed = narrowed.where(tables.conversation.c.member_id == viewer)
        surface = query.filters.get("surface")
        if isinstance(surface, str):
            narrowed = narrowed.where(tables.conversation.c.surface == surface)
        match query.filters.get("media"):
            case "image":
                narrowed = narrowed.where(
                    sa.func.lower(tables.shared_artifact.c.media_type).like(
                        MEDIA_IMAGE_PREFIX + "%"
                    )
                )
            case "document":
                narrowed = narrowed.where(_document_media())
            case "other":
                narrowed = narrowed.where(
                    sa.not_(
                        sa.func.lower(tables.shared_artifact.c.media_type).like(
                            MEDIA_IMAGE_PREFIX + "%"
                        )
                    ),
                    sa.not_(_document_media()),
                )
            case _:
                pass
        ordered = narrowed.order_by(
            tables.shared_artifact.c.created_at.desc(), tables.shared_artifact.c.id.desc()
        ).limit(ARTIFACT_SCAN_LIMIT)
        async with workspace_tx() as connection:
            rows = (await connection.execute(ordered)).all()
        by_identity: dict[tuple[UUID, str], list[sa.Row]] = {}
        for row in rows:
            by_identity.setdefault((row.conversation_id, row.filename), []).append(row)
        names = await self._identities(subjects)
        groups = [
            (
                names[identity],
                tuple(sorted(shares, key=lambda r: (r.created_at, r.blob_key), reverse=True)),
            )
            for identity, shares in by_identity.items()
        ]
        return sorted(groups, key=lambda pair: pair[0])

    async def _identities(self, subjects: frozenset[str]) -> dict[tuple[UUID, str], str]:
        """Every distinct (conversation, filename) the fence admits, named. A name disambiguates
        collisions against the whole set, never a window of it, so the same file answers one name
        however it was reached."""
        query = (
            sa.select(tables.turn.c.conversation_id, tables.shared_artifact.c.filename)
            .distinct()
            .select_from(
                tables.shared_artifact.join(
                    tables.turn, tables.shared_artifact.c.turn_id == tables.turn.c.id
                ).join(
                    tables.conversation,
                    tables.turn.c.conversation_id == tables.conversation.c.id,
                )
            )
            .where(
                tables.shared_artifact.c.workspace_id == ws_current().workspace_id,
                tables.turn.c.agent_id == object_agent_id(),
                tables.conversation.c.audience.in_(subjects),
            )
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return artifact_object_names((row.conversation_id, row.filename) for row in rows)

    def _shares(self, subjects: frozenset[str]) -> sa.Select:
        return (
            sa.select(
                tables.shared_artifact.c.turn_id,
                tables.shared_artifact.c.blob_key,
                tables.shared_artifact.c.filename,
                tables.shared_artifact.c.subject,
                tables.shared_artifact.c.media_type,
                tables.shared_artifact.c.size_bytes,
                tables.shared_artifact.c.preview_blob_key,
                tables.shared_artifact.c.preview_media_type,
                tables.shared_artifact.c.preview_size_bytes,
                tables.shared_artifact.c.created_at,
                tables.turn.c.conversation_id,
                tables.conversation.c.audience,
                tables.conversation.c.surface,
                tables.conversation.c.surface_label,
                tables.conversation.c.member_id.label("owner_member_id"),
                tables.member.c.email.label("owner_email"),
            )
            .select_from(
                tables.shared_artifact.join(
                    tables.turn, tables.shared_artifact.c.turn_id == tables.turn.c.id
                )
                .join(
                    tables.conversation,
                    tables.turn.c.conversation_id == tables.conversation.c.id,
                )
                .outerjoin(tables.member, tables.conversation.c.member_id == tables.member.c.id)
            )
            .where(
                tables.shared_artifact.c.workspace_id == ws_current().workspace_id,
                tables.turn.c.agent_id == object_agent_id(),
                tables.conversation.c.audience.in_(subjects),
            )
        )

    def _member_shares(self, subjects: frozenset[str]) -> sa.Select:
        return self._shares(subjects).where(_member_participated())

    async def _sources(self, conversation_ids: Sequence[UUID]) -> dict[UUID, str | None]:
        """Each conversation's opening source — the permalink its first turn arrived from — read
        the way the surface reads it: the minimum-seq turn's context, None when it carried none."""
        if not conversation_ids:
            return {}
        opening = (
            sa.select(
                tables.turn.c.conversation_id,
                sa.func.min(tables.turn.c.seq).label("seq"),
            )
            .where(
                tables.turn.c.workspace_id == ws_current().workspace_id,
                tables.turn.c.conversation_id.in_(conversation_ids),
            )
            .group_by(tables.turn.c.conversation_id)
            .subquery()
        )
        query = (
            sa.select(tables.turn.c.conversation_id, tables.turn.c.context)
            .select_from(
                tables.turn.join(
                    opening,
                    sa.and_(
                        tables.turn.c.conversation_id == opening.c.conversation_id,
                        tables.turn.c.seq == opening.c.seq,
                    ),
                )
            )
            .where(tables.turn.c.workspace_id == ws_current().workspace_id)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return {
            row.conversation_id: (
                None if row.context is None else TurnContext.model_validate(row.context).source
            )
            for row in rows
        }

    def _row(
        self,
        name: str,
        shares: tuple[sa.Row, ...],
        viewer: UUID | None,
        sources: dict[UUID, str | None],
    ) -> ObjectRow:
        latest = shares[0]
        return ObjectRow(
            name=name,
            summary=_summary(shares),
            fields={
                "filename": latest.filename,
                "subject": latest.subject or "",
                "conversation": str(latest.conversation_id),
                "shared_at": latest.created_at.isoformat(),
                "media": artifact_media(latest.media_type),
                "media_type": latest.media_type,
                "size_bytes": latest.size_bytes,
                "owner_email": latest.owner_email,
                "origin": latest.surface_label
                or (latest.surface if latest.surface != WEB_SURFACE else None),
                "surface": latest.surface,
                "source": sources.get(latest.conversation_id),
                "mine": viewer is not None and latest.owner_member_id == viewer,
                "url": self._download_url(latest),
                "preview_url": self._preview_url(latest),
            },
        )

    def _download_url(self, latest: sa.Row) -> str | None:
        if not self.artifact_token_secret or not self.public_base_url:
            return None
        path = mint_artifact_url(
            self.artifact_token_secret,
            latest.blob_key,
            artifact_url_expiry(datetime.now(UTC)),
            workspace_id=ws_current().workspace_id,
        )
        return f"{self.public_base_url.rstrip('/')}{path}"

    def _preview_url(self, latest: sa.Row) -> str | None:
        """A signed raster-preview link, or None when its type, size, or delivery is ineligible —
        the picture blob a share rasterized when it has one, the file's own bytes when it is
        already an image, and the declared type must agree with the key it names."""
        if latest.preview_blob_key is not None:
            blob_key = latest.preview_blob_key
            declared = latest.preview_media_type
            size_bytes = latest.preview_size_bytes
        else:
            blob_key = latest.blob_key
            declared = latest.media_type
            size_bytes = latest.size_bytes
        if raster_image_media_type(blob_key) != declared:
            return None
        return mint_image_preview_url(
            self.artifact_token_secret,
            self.public_base_url,
            blob_key,
            size_bytes,
            workspace_id=ws_current().workspace_id,
        )


def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]:
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


def _summary(shares: tuple[sa.Row, ...]) -> str:
    latest = shares[0]
    versions = f", {len(shares)} versions" if len(shares) > 1 else ""
    return (
        f"{latest.filename} ({latest.media_type}, {latest.size_bytes} bytes), "
        f"shared {latest.created_at.date().isoformat()}{versions}"
    )[:SUMMARY_MAX]


def artifact_object(
    *, public_base_url: str | None = None, artifact_token_secret: str = ""
) -> ObjectKind:
    """The artifact kind bound to this deploy's link minting: listing rows publish the signed
    download and preview links the portal draws, so the kind is constructed where the deploy's
    public base and artifact secret are known — a deploy that mints no links lists the same rows
    with null links."""
    return ObjectKind(
        name=ARTIFACT_KIND,
        description=(
            "A file share_file sent out of a turn, one object per conversation and filename, "
            "each re-share a new version. share_file is the only producer."
        ),
        guidance=(
            "Files shared with members by share_file, one object per conversation and filename, "
            "named <conversation-prefix>-<filename-slug> (3f2a9c1b-report-txt; the share result "
            "carries the name), so one session's artifacts share a prefix and the same filename "
            "from different sessions stays distinct. Re-sharing a filename in the same "
            "conversation "
            "adds a version — get, status, and the workspace copy reflect the latest share. Reads "
            "stay "
            "inside the selected agent and acting audience. Listings filter and order on "
            "`filename`, `subject`, `conversation`, `shared_at`, `media` (image, document, or "
            "other), `media_type`, `size_bytes`, `owner_email`, `origin`, `surface`, `source`, "
            "and `mine` — filter on a conversation id for that "
            "session's files, media=image for pictures, mine=true for files from your own "
            "conversations, or order by `shared_at` desc for the most recent; each row also "
            "carries signed `url` and `preview_url` links when the deploy mints them. "
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
        store=ArtifactObjects(
            public_base_url=public_base_url, artifact_token_secret=artifact_token_secret
        ),
        list_fields=frozenset(
            {
                "filename",
                "subject",
                "conversation",
                "shared_at",
                "media",
                "media_type",
                "size_bytes",
                "owner_email",
                "origin",
                "surface",
                "source",
                "mine",
                "url",
                "preview_url",
            }
        ),
        agent_target_verbs=frozenset({"list", "get", "delete"}),
    )
