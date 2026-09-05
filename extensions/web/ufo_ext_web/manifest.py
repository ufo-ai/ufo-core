"""The web extension's manifest: one surface on the core seam, live mode, the admin-only chat
verbs the portal's own acts ride (#645 — the web surface is its audience authority), and the
title job that rewrites each conversation's rail label from its opening exchange — every surface's,
since the rail lists a Slack thread and a CLI session beside a portal chat and names them all the
same way, so the job's candidates are core's conversations awaiting a summary rather than a key
space of this extension's own. No credential slots (its session cookie carries the member's own
token, not a bot secret) and no config knob — installed means mounted, like Slack. What the deploy
does decide, in the environment the pod reads, is the Datadog RUM application the portal records
browser sessions into; naming none records nothing. The surface admits without writeback and tails
the hub in its own stream route, and claims the browser home, so the deploy's bare host opens the
portal."""

from ufo.sdk.jobs import JobSpec, unseeded_agent_workspaces, untitled_conversation_workspaces
from ufo.sdk.manifest import FlagSpec, Manifest
from ufo.sdk.surfaces import SurfaceSpec
from ufo_ext_web.audience import EXTENSION_WEB, WEB_ACCESS_TOOLS
from ufo_ext_web.surface import (
    APP_FLAGS,
    ARTIFACTS_SLOT,
    CHANGES_SLOT,
    HOMEPAGE_SEED_PREFIX,
    LANES_SHELL_FLAG,
    MAIN_AGENT_FLAG,
    PORTAL_SURFACES,
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
# What the portal reads a flag to decide, stated for the operator turning one on — they are reading
# this line, not the boot read. Every key the portal reads is declared here, and a gate holds each
# environment's `infra/envs/*/flags.tf` to exactly this set, so no key the portal reads is one the
# flag service was never told about.
FLAGS = (
    FlagSpec(key=APP_FLAGS["code"], what="The Code app is listed in the portal."),
    FlagSpec(key=APP_FLAGS["issues"], what="The Issues app is listed in the portal."),
    FlagSpec(key=APP_FLAGS["meetings"], what="The Meetings app is listed in the portal."),
    FlagSpec(key=APP_FLAGS["metrics"], what="The Metrics app is listed in the portal."),
    FlagSpec(key=APP_FLAGS["notification"], what="The Notification app is listed in the portal."),
    FlagSpec(key=APP_FLAGS["radar"], what="The Radar app is listed in the portal."),
    FlagSpec(key=APP_FLAGS["wiki"], what="The Wiki app is listed in the portal."),
    FlagSpec(key=MAIN_AGENT_FLAG, what="The workspace's main agent is listed in the portal."),
    FlagSpec(key=PORTAL_SURFACES["memory"], what="The workspace Memory tab is drawn."),
    FlagSpec(
        key=PORTAL_SURFACES["community-skills"],
        what="The Skills tab offers the community catalogue.",
    ),
    FlagSpec(
        key=PORTAL_SURFACES["installed-skills"],
        what="The Skills tab offers the workspace's own skills.",
    ),
    FlagSpec(
        key=LANES_SHELL_FLAG,
        what="The portal serves the lanes shell rather than the sidebar shell.",
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        connects_member_accounts=True,
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
                candidates=untitled_conversation_workspaces(),
            ),
            JobSpec(
                name=SEED_JOB_NAME,
                schedule=SEED_JOB_SCHEDULE,
                handler=seed_homepages,
                candidates=unseeded_agent_workspaces(EXTENSION_WEB, HOMEPAGE_SEED_PREFIX),
            ),
        ),
        flags=FLAGS,
        member_context_read=True,
    )
