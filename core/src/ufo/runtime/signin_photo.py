"""The picture the identity provider that verified a member's address hosts for them, fetched once
the seat has recorded where it is.

The seat writes only the address of the picture: fetching the bytes is derived work, so it runs
here on the next tick rather than inside the sign-in. The picture is offered under `signin`, so a
member's own upload stands and a later clear lets it fill again. An address the host refuses, or
one that is not an address at all, is dropped after one attempt, so a dead link costs one request
rather than one every minute; a host unreachable this tick is asked again on the next.
"""

import re
from dataclasses import dataclass
from uuid import UUID

import httpx
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import warn
from ufo.runtime.candidates import WorkspaceCandidates, owner_candidates
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.member_profiles import (
    PROFILE_PHOTO_DIMENSION,
    PROFILE_PHOTO_MAX_BYTES,
    InvalidProfilePhoto,
)
from ufo.runtime.workspace import ws_current
from ufo.schema import tables

SIGNIN_PHOTO_JOB = "member_signin_photo"
SIGNIN_PHOTO_SCHEDULE = "0 * * * * *"
SIGNIN_PHOTO_TIMEOUT_SECONDS = 10.0
SIGNIN_PHOTOS_PER_TICK = 20
SECURE_SCHEME = "https://"
GOOGLE_SIZE_SUFFIX = re.compile(r"=s\d+-c$")


def unfetched_signin_photo_workspaces() -> WorkspaceCandidates:
    """Workspaces holding a seated member whose sign-in named a picture nobody has fetched. The
    set empties as pictures land or dead links are dropped."""

    def with_an_unfetched_picture() -> sa.Select[tuple[UUID]]:
        return (
            sa.select(tables.member.c.workspace_id)
            .where(
                tables.member.c.seated_at.is_not(None),
                tables.member.c.signin_photo_url.is_not(None),
                tables.member.c.photo_digest.is_(None),
            )
            .distinct()
        )

    return owner_candidates(with_an_unfetched_picture)


@dataclass(frozen=True)
class SigninPhotos:
    """Fetch the picture each unpictured member's sign-in named, bounded to
    `SIGNIN_PHOTOS_PER_TICK` members a tick. The set shrinks on its own — a fetched picture or a
    dropped link leaves it — so no cursor is needed to reach everyone."""

    async def run(self, ctx: ExtensionContext) -> None:
        if ctx.profiles is None:
            raise RuntimeError("the sign-in photo job requires workspace blob storage")
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.member.c.id, tables.member.c.signin_photo_url)
                    .where(
                        tables.member.c.workspace_id == ws_current().workspace_id,
                        tables.member.c.seated_at.is_not(None),
                        tables.member.c.signin_photo_url.is_not(None),
                        tables.member.c.photo_digest.is_(None),
                    )
                    .order_by(tables.member.c.email)
                    .limit(SIGNIN_PHOTOS_PER_TICK)
                )
            ).all()
        async with httpx.AsyncClient(
            timeout=SIGNIN_PHOTO_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            for row in rows:
                try:
                    picture = await self._picture(client, row.signin_photo_url)
                except httpx.HTTPError as error:
                    warn("signin_photo.unreachable", error=str(error))
                    continue
                if picture is not None:
                    try:
                        await ctx.profiles.suggest(row.id, source="signin", photo=picture)
                        continue
                    except InvalidProfilePhoto:
                        pass
                await self._drop(row.id)

    async def _picture(self, client: httpx.AsyncClient, url: str) -> bytes | None:
        """The bytes the host serves at `url`, or None where it serves anything but a picture of
        usable size, the address is not one to fetch over TLS, or it is not an address httpx can
        parse. Raises where the host cannot be reached, which the caller reads as "ask again next
        tick" rather than "drop the link"."""
        if not url.startswith(SECURE_SCHEME):
            return None
        # WorkOS relays Google's 96px variant (`=s96-c`); the same asset serves any size the
        # suffix names, and the store keeps PROFILE_PHOTO_DIMENSION.
        try:
            answer = await client.get(GOOGLE_SIZE_SUFFIX.sub(f"=s{PROFILE_PHOTO_DIMENSION}-c", url))
        except httpx.InvalidURL:
            return None
        if answer.status_code != httpx.codes.OK:
            return None
        return answer.content if len(answer.content) <= PROFILE_PHOTO_MAX_BYTES else None

    async def _drop(self, member_id: UUID) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .where(
                    tables.member.c.workspace_id == ws_current().workspace_id,
                    tables.member.c.id == member_id,
                )
                .values(signin_photo_url=None, updated_at=sa.func.now())
            )
