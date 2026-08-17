"""The web extension's manifest: one surface on the core seam, live mode, the admin-only chat
verbs the portal's own acts ride (#645 — the web surface is its audience authority), and the
title job that rewrites each new chat's rail label from its opening exchange. No
credential slots (its session cookie carries the member's own token, not a bot secret) and no
config knob — installed means mounted, like Slack. The surface admits without writeback and tails
the hub in its own stream route, and claims the browser home, so the deploy's bare host opens the
portal."""

from ufo.sdk.jobs import JobSpec, store_key_workspaces, unseeded_agent_workspaces
from ufo.sdk.manifest import Manifest
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_web.audience import EXTENSION_WEB, WEB_ACCESS_TOOLS
from ufo_ext_web.surface import (
    ARTIFACTS_SLOT,
    CHANGES_SLOT,
    CHAT_PENDING_PREFIX,
    HOMEPAGE_SEED_PREFIX,
    ROUTES,
    SEED_JOB_NAME,
    SEED_JOB_SCHEDULE,
    SURFACE_WEB,
    TITLE_JOB_NAME,
    TITLE_JOB_SCHEDULE,
    resolve_workspace,
    seed_homepages,
    summarize_chat_titles,
)

NAME = EXTENSION_WEB
VERSION = "0.1.0"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=WEB_ACCESS_TOOLS,
        conversation_slots=(CHANGES_SLOT, ARTIFACTS_SLOT),
        surfaces=(
            SurfaceSpec(name=SURFACE_WEB, routes=ROUTES, identify=resolve_workspace, home=True),
        ),
        jobs=(
            JobSpec(
                name=TITLE_JOB_NAME,
                schedule=TITLE_JOB_SCHEDULE,
                handler=summarize_chat_titles,
                candidates=store_key_workspaces(EXTENSION_WEB, CHAT_PENDING_PREFIX),
            ),
            JobSpec(
                name=SEED_JOB_NAME,
                schedule=SEED_JOB_SCHEDULE,
                handler=seed_homepages,
                candidates=unseeded_agent_workspaces(EXTENSION_WEB, HOMEPAGE_SEED_PREFIX),
            ),
        ),
        member_context_read=True,
    )
