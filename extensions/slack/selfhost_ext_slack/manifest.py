"""The Slack extension's manifest: one surface on the core seam plus the credential slots its
handlers read in-process — the bot token and signing secret (secrets), and the bot user id and team
id (the app's identity used to gate and verify events). None carry a wire-injection target: a
surface authenticates to Slack itself, never through the sandbox egress proxy."""

from selfhost.sdk.manifest import CredentialSlot, Manifest
from selfhost.sdk.surfaces import SurfaceRoute, SurfaceSpec
from selfhost_ext_slack.surface import (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_BOT_USER_ID_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SLACK_TEAM_ID_SLOT,
    SURFACE_SLACK,
    attach,
    ingest,
    post,
)

NAME = "slack"
VERSION = "0.1.0"


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
                routes=(SurfaceRoute(method="POST", path="", handler=ingest),),
                post=post,
                attach=attach,
            ),
        ),
    )
