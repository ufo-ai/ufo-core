"""What Slack knows a member as, offered to their ufo profile.

A workspace that installed Slack already told this deploy who its members are there — the surface
links a member to their Slack user id on first contact — so the name and picture they set once, in
the place they work, fill the portal without anyone typing them again. Neither is ever imposed: the
offer is refused for a member who set their own, and it stops being made once every linked member
is drawn.

The picture is fetched here and stored by core, so the portal serves it from its own origin and no
page a member opens reaches Slack's CDN.
"""

from dataclasses import dataclass

import httpx

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec, undrawn_surface_member_workspaces
from ufo.sdk.member_profiles import PROFILE_PHOTO_MAX_BYTES, next_window
from ufo.sdk.o11y import log
from ufo_ext_slack.surface import SLACK_BOT_TOKEN_SLOT, SURFACE_SLACK, slack_user

SLACK_PROFILES_JOB = "slack_profiles"
SLACK_PROFILES_SCHEDULE = "0 */10 * * * *"
SLACK_PROFILE_FETCH_TIMEOUT_SECONDS = 10.0
SLACK_PROFILES_PER_TICK = 20
SLACK_PROFILES_CURSOR_KEY = "profiles/cursor"


@dataclass(frozen=True)
class SlackProfiles:
    """Offer every linked member the name and picture their Slack account carries, bounded to
    `SLACK_PROFILES_PER_TICK` members a tick so a large workspace spreads its reads over several
    rather than holding the job open through all of them.

    A member whose Slack account carries no picture of their own stays undrawn, so the window
    rotates on a stored cursor rather than taking the head of the order every tick — otherwise the
    same members would be asked about forever and the rest of the roster never reached.

    The linked rows outlive the token: an admin who empties the slot leaves a workspace the
    candidate query still names every tick, so the token is checked before it is read."""

    async def run(self, ctx: ExtensionContext) -> None:
        if ctx.profiles is None:
            raise RuntimeError("the slack profile job requires workspace blob storage")
        if not await ctx.credentials.stored(SLACK_BOT_TOKEN_SLOT):
            return
        linked = await ctx.installations.linked_members(SURFACE_SLACK)
        undrawn = tuple(
            profile
            for profile in await ctx.profiles.undrawn()
            if profile.id in linked and (profile.name is None or profile.photo_digest is None)
        )
        if not undrawn:
            return
        held = await ctx.store.get(SLACK_PROFILES_CURSOR_KEY)
        asked = next_window(
            undrawn, held if isinstance(held, str) else None, SLACK_PROFILES_PER_TICK
        )
        bot_token = await ctx.credentials.get(SLACK_BOT_TOKEN_SLOT)
        async with httpx.AsyncClient(timeout=SLACK_PROFILE_FETCH_TIMEOUT_SECONDS) as client:
            for profile in asked:
                found = await slack_user(bot_token, linked[profile.id])
                if found is None:
                    continue
                await ctx.profiles.suggest(
                    profile.id,
                    source="slack",
                    name=found.name if profile.name is None else None,
                    photo=(
                        await self._picture(client, found.image_url)
                        if profile.photo_digest is None
                        else None
                    ),
                )
        await ctx.store.put(SLACK_PROFILES_CURSOR_KEY, asked[-1].email)

    async def _picture(self, client: httpx.AsyncClient, url: str | None) -> bytes | None:
        """Slack serves generated initials for an account with no avatar; `image_url` maps that
        to None."""
        if url is None:
            return None
        try:
            answer = await client.get(url)
        except httpx.HTTPError as error:
            log("slack.avatar_unreachable", error=str(error))
            return None
        if answer.status_code != httpx.codes.OK:
            return None
        return answer.content if len(answer.content) <= PROFILE_PHOTO_MAX_BYTES else None


SLACK_PROFILES_JOB_SPEC = JobSpec(
    name=SLACK_PROFILES_JOB,
    schedule=SLACK_PROFILES_SCHEDULE,
    handler=SlackProfiles().run,
    candidates=undrawn_surface_member_workspaces(SURFACE_SLACK),
)
