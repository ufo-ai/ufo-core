"""The Slack extension's manifest: one surface on the core seam — its ingest, interactivity, and
OAuth-install routes — the per-workspace secret slots, and the setup tools the agent drives in chat.

Install has two paths. The preferred one is OAuth on the deploy's own Slack app, whose client id,
client secret, and signing secret are read from the deploy's env in-process (`SLACK_CLIENT_ID`,
`SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`) — never per-workspace, never the sandbox, so they are
not credential slots; the OAuth callback mints the per-workspace bot token. The alternative is a
bring-your-own Slack app (the `slack-app-setup` skill): the member creates an app from
`slack_app_manifest` and fills the two per-workspace slots — the bot token and the app's own signing
secret — privately. A surface authenticates to Slack directly, never through the sandbox egress
proxy, so neither slot carries a wire-injection target.

The surface's thread followers are side-channel tasks in the process that runs the turn, so the
status follower and the progress reporter are armed on `user_prompt_submit` — the one event that
fires once per execution of a turn, which is what puts them back on a run this fleet resumed."""

from pathlib import Path

from ufo.sdk.manifest import CredentialSlot, HookSpec, Manifest, SkillSpec
from ufo.sdk.surfaces import SurfaceRoute, SurfaceSpec
from ufo_ext_slack.hooks import CONNECTOR_CALL_TOOL, attribute_connector_send
from ufo_ext_slack.surface import (
    SLACK_BOT_TOKEN_SLOT,
    SLACK_EXTENSION,
    SLACK_OAUTH_CALLBACK_PATH,
    SLACK_SIGNING_SECRET_SLOT,
    SURFACE_SLACK,
    attach,
    follow_turn,
    ingest,
    interactive,
    oauth_callback,
    post,
    resolve_self_user_id,
    resolve_workspace,
)
from ufo_ext_slack.tools import TOOLS

NAME = SLACK_EXTENSION
VERSION = "0.1.0"
SKILL_DIR = Path(__file__).parent / "skills" / "slack-app-setup"


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=SLACK_BOT_TOKEN_SLOT,
                description="Slack bot token (xoxb-...): OAuth-minted, or pasted for a BYO app.",
            ),
            CredentialSlot(
                name=SLACK_SIGNING_SECRET_SLOT,
                description="A bring-your-own Slack app's signing secret (unused for OAuth).",
            ),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_SLACK,
                routes=(
                    SurfaceRoute(method="POST", path="", handler=ingest),
                    SurfaceRoute(method="POST", path="interactive", handler=interactive),
                    SurfaceRoute(
                        method="GET", path=SLACK_OAUTH_CALLBACK_PATH, handler=oauth_callback
                    ),
                ),
                post=post,
                attach=attach,
                identify=resolve_workspace,
                self_user_id=resolve_self_user_id,
            ),
        ),
        tools=TOOLS,
        hooks=(
            HookSpec(
                event="pre_tool_use",
                handler=attribute_connector_send,
                tools=(CONNECTOR_CALL_TOOL,),
            ),
            HookSpec(event="user_prompt_submit", handler=follow_turn),
        ),
        skills=(SkillSpec(path=SKILL_DIR),),
    )
