"""One workspace's member profiles: the name a member is drawn under and the picture beside it.

A profile has three writers and they rank. The member's own edit outranks what the Slack install
reports, which outranks what gravatar answers for their address, so a prefill job fills what is
empty or derived and never overwrites a member's own choice. The rank is enforced at the one write
rather than by each writer checking before it acts.

Every stored picture is normalized on the way in — centre-cropped square, bounded to
`PROFILE_PHOTO_DIMENSION`, re-encoded as WebP — so what a member uploads, what Slack hosts, and what
gravatar answers all land as the same shape, carry no EXIF, and are served with no media type
recorded beside them. The digest is the ETag the portal's photo route answers with, and a replaced
picture is a new digest, so a member who changes their photo sees it change.

Reading a profile needs the database alone and every screen does it; storing one needs the blob
store too. The reads are therefore free functions and the writes are `MemberProfiles`.
"""

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from PIL import Image
from PIL.Image import Resampling
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.blob import WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.schema import tables

ProfileSource = Literal["member", "slack", "gravatar"]

PROFILE_SOURCE_RANK: dict[ProfileSource, int] = {"gravatar": 1, "slack": 2, "member": 3}

PROFILE_PHOTO_DIMENSION = 256
PROFILE_PHOTO_MEDIA_TYPE = "image/webp"
PROFILE_PHOTO_MAX_BYTES = 4 * 1024 * 1024
PROFILE_NAME_MAX_CHARS = 64

_PHOTO_QUALITY = 82
_PHOTO_EFFORT = 4
_DECODED_PIXEL_CEILING = 64_000_000


class InvalidProfilePhoto(ValueError):
    pass


@dataclass(frozen=True)
class MemberProfile:
    """What one member is called and whether a picture stands for them. `name` is None until
    somebody names them, so a reader falls through `profile_name` rather than inventing one;
    `photo_digest` is None until a picture lands, which is what tells a face from initials."""

    id: UUID
    email: str
    name: str | None
    name_source: ProfileSource | None
    photo_digest: str | None
    photo_source: ProfileSource | None
    created_at: datetime
    updated_at: datetime


def profile_name(profile: MemberProfile) -> str:
    """The name to draw this member under: what they are called, else the local part of their
    address. Never the bare address — a header reading `alex@simplecasual.com` beside a face reads
    as a machine, and the local part is what the product already shortens an unnamed member to."""
    if profile.name:
        return profile.name
    return profile.email.split("@", 1)[0] or profile.email


def photo_blob_key(member_id: UUID) -> str:
    """Where this member's picture lives, workspace-relative. One key per member, replaced in
    place, so a changed photo leaves nothing behind to collect."""
    return f"members/{member_id}/photo.webp"


async def read_profiles(
    workspace_id: UUID, member_ids: frozenset[UUID] | None = None
) -> tuple[MemberProfile, ...]:
    """The profiles of these members, or of the whole workspace when none are named, ordered by
    email so a roster's rows are stable."""
    query = (
        sa.select(
            tables.member.c.id,
            tables.member.c.email,
            tables.member.c.display_name,
            tables.member.c.display_name_source,
            tables.member.c.photo_digest,
            tables.member.c.photo_source,
            tables.member.c.created_at,
            tables.member.c.updated_at,
        )
        .where(tables.member.c.workspace_id == workspace_id)
        .order_by(tables.member.c.email)
    )
    if member_ids is not None:
        if not member_ids:
            return ()
        query = query.where(tables.member.c.id.in_(member_ids))
    async with workspace_tx() as connection:
        rows = (await connection.execute(query)).all()
    return tuple(_profile(row) for row in rows)


async def read_profile(workspace_id: UUID, member_id: UUID) -> MemberProfile | None:
    return next(iter(await read_profiles(workspace_id, frozenset({member_id}))), None)


def next_window(
    profiles: Sequence[MemberProfile], after: str | None, limit: int
) -> tuple[MemberProfile, ...]:
    """The `limit` profiles following the one `after` names, wrapping past the end.

    A prefill job asks an outside host about members it holds no answer for, and a host that has
    none leaves the member exactly as undrawn as it found them. Taking the head of a stable order
    every tick would then ask about the same members forever and never reach the rest, so a tick
    starts where the last one stopped and the roster is covered in turn — and comes round again
    later, which is what lets a picture appear after the member uploads one."""
    if not profiles:
        return ()
    start = 0
    if after is not None:
        found = next(
            (index for index, profile in enumerate(profiles) if profile.email == after), None
        )
        if found is not None:
            start = found + 1
    rotated = (*profiles[start:], *profiles[:start])
    return rotated[:limit]


class ProfilePhoto:
    """The Pillow work one stored picture costs, off the loop. Decoding and re-encoding a
    2048x1536 JPEG measured 12ms median and 34ms worst here, past the budget a turn's loop keeps,
    so `MemberProfiles` hands `normalized` to a worker thread rather than paying it inline."""

    @staticmethod
    def normalized(data: bytes) -> tuple[bytes, str]:
        """The bytes to store for a picture somebody supplied, and their digest. Raises
        `InvalidProfilePhoto` for anything past the byte ceiling, anything Pillow cannot open as a
        raster, and anything whose pixels exceed what a decompression bomb would need."""
        if not data:
            raise InvalidProfilePhoto("a profile photo has no bytes")
        if len(data) > PROFILE_PHOTO_MAX_BYTES:
            raise InvalidProfilePhoto(
                f"a profile photo is capped at {PROFILE_PHOTO_MAX_BYTES} bytes"
            )
        try:
            with Image.open(BytesIO(data)) as opened:
                width, height = opened.size
                if width <= 0 or height <= 0:
                    raise InvalidProfilePhoto("a profile photo has no pixels")
                if width * height > _DECODED_PIXEL_CEILING:
                    raise InvalidProfilePhoto("a profile photo exceeds the decoded pixel ceiling")
                square = ProfilePhoto._squared(opened.convert("RGB"), width, height)
            written = BytesIO()
            square.save(written, format="WEBP", quality=_PHOTO_QUALITY, method=_PHOTO_EFFORT)
        except InvalidProfilePhoto:
            raise
        except Exception as error:
            raise InvalidProfilePhoto("a profile photo is not a readable picture") from error
        stored = written.getvalue()
        return stored, hashlib.sha256(stored).hexdigest()

    @staticmethod
    def _squared(image: Image.Image, width: int, height: int) -> Image.Image:
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        cropped = image.crop((left, top, left + side, top + side))
        return cropped.resize(
            (PROFILE_PHOTO_DIMENSION, PROFILE_PHOTO_DIMENSION), Resampling.LANCZOS
        )


@dataclass(frozen=True)
class MemberProfiles:
    """The writes over one workspace's profiles, and the picture read that needs the store. Every
    write states the source it speaks for and is refused where a stronger source already answered,
    so the member, the Slack install, and gravatar need no order between them."""

    workspace_id: UUID
    blob: WorkspaceBlobStore

    async def photo(self, member_id: UUID) -> bytes | None:
        """The stored picture's bytes, or None where the row names no digest. A row naming a digest
        whose blob is gone raises rather than answering an empty face: the member set a picture and
        a missing one is a fault, not initials."""
        profile = await read_profile(self.workspace_id, member_id)
        if profile is None or profile.photo_digest is None:
            return None
        return await self.blob.get(photo_blob_key(member_id))

    async def set_name(self, member_id: UUID, name: str | None, source: ProfileSource) -> bool:
        """Name this member, or clear the name so the derived one answers again. Answers whether
        the write landed — False where a stronger source already named them."""
        cleaned = _cleaned_name(name)
        async with workspace_tx() as connection:
            if not await self._outranks(
                connection, member_id, tables.member.c.display_name_source, source
            ):
                return False
            await connection.execute(
                self._row(member_id).values(
                    display_name=cleaned,
                    display_name_source=None if cleaned is None else source,
                    updated_at=sa.func.now(),
                )
            )
        return True

    async def set_photo(self, member_id: UUID, data: bytes, source: ProfileSource) -> str | None:
        """Store a picture for this member and answer its digest, or None where a stronger source
        already gave them one. The bytes are normalized before the row moves, so a row never names
        a digest the store does not hold."""
        stored, digest = await asyncio.to_thread(ProfilePhoto.normalized, data)
        async with workspace_tx() as connection:
            if not await self._outranks(
                connection, member_id, tables.member.c.photo_source, source
            ):
                return None
            await self.blob.put(photo_blob_key(member_id), stored)
            await connection.execute(
                self._row(member_id).values(
                    photo_digest=digest, photo_source=source, updated_at=sa.func.now()
                )
            )
        return digest

    async def clear_photo(self, member_id: UUID) -> None:
        """Drop this member's picture, whatever set it, so the derived sources may fill it again.
        The row moves first: a blob outliving its row draws nothing, while a row outliving its blob
        is the fault `photo` raises on."""
        async with workspace_tx() as connection:
            await connection.execute(
                self._row(member_id).values(
                    photo_digest=None, photo_source=None, updated_at=sa.func.now()
                )
            )
        await self.blob.delete(photo_blob_key(member_id))

    def _row(self, member_id: UUID) -> sa.Update:
        return sa.update(tables.member).where(
            tables.member.c.workspace_id == self.workspace_id,
            tables.member.c.id == member_id,
        )

    async def _outranks(
        self,
        connection: AsyncConnection,
        member_id: UUID,
        column: sa.Column,
        source: ProfileSource,
    ) -> bool:
        held = (
            await connection.execute(
                sa.select(column).where(
                    tables.member.c.workspace_id == self.workspace_id,
                    tables.member.c.id == member_id,
                )
            )
        ).scalar_one_or_none()
        if held is None:
            return True
        return PROFILE_SOURCE_RANK[source] >= PROFILE_SOURCE_RANK[held]


def _cleaned_name(name: str | None) -> str | None:
    if name is None:
        return None
    collapsed = " ".join(name.split())[:PROFILE_NAME_MAX_CHARS].strip()
    return collapsed or None


def _profile(row: sa.Row) -> MemberProfile:
    return MemberProfile(
        id=row.id,
        email=row.email,
        name=row.display_name,
        name_source=row.display_name_source,
        photo_digest=row.photo_digest,
        photo_source=row.photo_source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
