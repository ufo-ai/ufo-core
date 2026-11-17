"""The picture gravatar hosts for a member's address, where they have put one there.

Gravatar is keyed on the address a member already gave this workspace and needs no install, no
credential and no state, so there is nothing for an extension to hold on its behalf — the deploy
either asks gravatar about an address or it does not. `d=404` is what makes it an answer rather
than a decoration: without it gravatar draws a generated pattern for every address alive, and every
member would end up wearing one they never chose.

The ask discloses to gravatar's host that some deploy holds an address with this digest. That is
inherent to gravatar and is why this runs against members who have no picture at all, once, rather
than against the whole roster on every tick.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import sqlalchemy as sa

from ufo.harness.o11y import warn
from ufo.runtime.candidates import WorkspaceCandidates, owner_candidates
from ufo.runtime.ext.context import CORE_EXTENSION, ExtensionContext
from ufo.runtime.member_profiles import PROFILE_PHOTO_MAX_BYTES, next_window
from ufo.schema import tables

GRAVATAR_JOB = "member_gravatar"
GRAVATAR_SCHEDULE = "0 */10 * * * *"

GRAVATAR_URL = "https://gravatar.com/avatar/{digest}"
GRAVATAR_SIZE = 256
GRAVATAR_TIMEOUT_SECONDS = 10.0
GRAVATAR_MEMBERS_PER_TICK = 20
GRAVATAR_CURSOR_KEY = "gravatar/cursor"
GRAVATAR_CHECKED_KEY = "gravatar/checked"
GRAVATAR_RETRY_AFTER = timedelta(days=7)


def gravatar_digest(email: str) -> str:
    """What gravatar keys a person by: the sha256 of their address, trimmed and lower-cased, which
    is the normalization gravatar's own clients apply before hashing."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def unpictured_member_workspaces() -> WorkspaceCandidates:
    """Workspaces with an unpictured member whose address changed since the last full pass, or
    whose unsuccessful gravatar checks are due for a retry."""

    def with_an_unpictured_member_due() -> sa.Select[tuple[UUID]]:
        checked = tables.ext_store.alias("gravatar_checked")
        return (
            sa.select(tables.member.c.workspace_id)
            .outerjoin(
                checked,
                (checked.c.workspace_id == tables.member.c.workspace_id)
                & (checked.c.extension == CORE_EXTENSION)
                & (checked.c.key == GRAVATAR_CHECKED_KEY),
            )
            .where(
                tables.member.c.seated_at.is_not(None),
                tables.member.c.photo_digest.is_(None),
                sa.or_(
                    checked.c.workspace_id.is_(None),
                    tables.member.c.updated_at > checked.c.updated_at,
                    checked.c.updated_at <= datetime.now(UTC) - GRAVATAR_RETRY_AFTER,
                ),
            )
            .distinct()
        )

    return owner_candidates(with_an_unpictured_member_due)


@dataclass(frozen=True)
class GravatarPrefill:
    """Fill the picture of members who have none from gravatar, bounded to
    `GRAVATAR_MEMBERS_PER_TICK` addresses a tick.

    A member who set their own picture is never asked about, because `undrawn` does not list them
    and the offer would be refused anyway. A member gravatar holds nothing for stays undrawn, so the
    window rotates on a stored cursor rather than taking the head of the order every tick — the
    whole roster is covered in turn, and each member is asked again on a later pass, which is what
    lets a picture appear after they upload one there."""

    async def run(self, ctx: ExtensionContext) -> None:
        if ctx.profiles is None:
            raise RuntimeError("the gravatar job requires workspace blob storage")
        unpictured = tuple(
            profile for profile in await ctx.profiles.undrawn() if profile.photo_digest is None
        )
        if not unpictured:
            return
        held = await ctx.store.get(GRAVATAR_CURSOR_KEY)
        cursor = held if isinstance(held, str) else None
        asked = next_window(unpictured, cursor, GRAVATAR_MEMBERS_PER_TICK)
        async with httpx.AsyncClient(timeout=GRAVATAR_TIMEOUT_SECONDS) as client:
            for profile in asked:
                picture = await self._picture(client, profile.email)
                if picture is not None:
                    await ctx.profiles.suggest(profile.id, source="gravatar", photo=picture)
        await ctx.store.put(GRAVATAR_CURSOR_KEY, asked[-1].email)
        cursor_index = next(
            (index for index, profile in enumerate(unpictured) if profile.email == cursor), None
        )
        if len(unpictured) <= GRAVATAR_MEMBERS_PER_TICK or (
            cursor_index is not None
            and len(unpictured) - cursor_index - 1 <= GRAVATAR_MEMBERS_PER_TICK
        ):
            await ctx.store.put(GRAVATAR_CHECKED_KEY, True)

    async def _picture(self, client: httpx.AsyncClient, email: str) -> bytes | None:
        try:
            answer = await client.get(
                GRAVATAR_URL.format(digest=gravatar_digest(email)),
                params={"s": GRAVATAR_SIZE, "d": "404"},
            )
        except httpx.HTTPError as error:
            warn("gravatar.unreachable", error=str(error))
            return None
        if answer.status_code == httpx.codes.NOT_FOUND:
            return None
        if answer.status_code != httpx.codes.OK:
            warn("gravatar.refused", http_status=answer.status_code)
            return None
        return answer.content if len(answer.content) <= PROFILE_PHOTO_MAX_BYTES else None
