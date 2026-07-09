"""The Slack extension's manifest: one surface on the core seam plus the credential slots its
handlers read in-process — the bot token and signing secret (secrets), and the bot user id and team
id (the app's identity used to gate and verify events). None carry a wire-injection target: a
surface authenticates to Slack itself, never through the sandbox egress proxy. The `slack-app-setup`
skill walks a member through creating the Slack app and filling these four slots."""

from pathlib import Path

from ufo.sdk.manifest import CredentialSlot, Manifest, SkillSpec
from ufo.sdk.surfaces import SurfaceRoute, SurfaceSpec
from ufo_ext_slack.surface import (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_BOT_USER_ID_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SLACK_TEAM_ID_SLOT,
    SURFACE_SLACK,
    attach,
    ingest,
    interactive,
    post,
)

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
            CredentialSlot(name=SLACK_BOT_USER_ID_SLOT, description="The bot's own Slack user id."),
            CredentialSlot(name=SLACK_TEAM_ID_SLOT, description="The Slack workspace (team) id."),
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
            ),
        ),
        skills=(SkillSpec(path=SKILL_DIR),),
    )
