"""The Slack extension's manifest: one surface on the core seam, its two secret slots — the bot
token and signing secret, read in-process — and the setup tools the agent drives in chat. The
app's identity (team and bot-user ids) is derived metadata, not a credential: `slack_connect`
proves it with auth.test and custodies it as the surface's own record. Neither slot carries a
wire-injection target: a surface authenticates to Slack itself, never through the sandbox egress
proxy. The `slack-app-setup` skill walks a member through the whole connection."""

from pathlib import Path

from ufo.sdk.manifest import CredentialSlot, Manifest, SkillSpec
from ufo.sdk.surfaces import SurfaceRoute, SurfaceSpec
from ufo_ext_slack.surface import (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SURFACE_SLACK,
    attach,
    ingest,
    interactive,
    post,
    resolve_workspace,
)
from ufo_ext_slack.tools import TOOLS

NAME = "slack"
VERSION = "0.1.0"
SKILL_DIR = Path(__file__).parent / "skills" / "slack-app-setup"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=SLACK_BOT_TOKEN_SLOT, description="Slack bot OAuth token (xoxb-...)."
            ),
            CredentialSlot(name=SLACK_SIGNING_SECRET_SLOT, description="Slack app signing secret."),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_SLACK,
                routes=(
                    SurfaceRoute(method="POST", path="", handler=ingest),
                    SurfaceRoute(method="POST", path="interactive", handler=interactive),
                ),
                post=post,
                attach=attach,
                identify=resolve_workspace,
            ),
        ),
        tools=TOOLS,
        skills=(SkillSpec(path=SKILL_DIR),),
    )
