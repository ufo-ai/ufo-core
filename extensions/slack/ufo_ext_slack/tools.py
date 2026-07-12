"""Slack setup as chat tools: the member connects Slack in conversation and the agent drives it.

`slack_connect` is the idempotent state machine — missing secrets read as `not_configured`; stored
secrets with an absent or rotation-staled identity run `auth.test` (Slack's authentication-and-
identity check) and persist the derived team/bot ids as the surface's own identity record; a valid
identity awaiting Slack's first signed event reads `pending`; a fingerprint-matching url-verified
marker reads `connected`. `slack_app_manifest` renders the exact app manifest for this deploy so
the member creates the app with the right scopes and request URLs. The two secrets (bot token,
signing secret) travel through `request_credentials` fulfillment — the member's terminal prompts
privately — and never through chat. The manifest template below is pinned to the skill's YAML by a
test, so the scopes and events can never drift apart."""

import json
import re
from uuid import UUID

from pydantic import BaseModel, Field

from ufo.sdk.surfaces import CredentialSlotUnset
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_slack.surface import (
    MALFORMED_IDENTITY_ERROR,
    SLACK_BOT_TOKEN_SLOT,
    SLACK_SIGNING_SECRET_SLOT,
    SlackIdentityError,
    SlackIdentityResolver,
    read_identity,
    signing_secret_fingerprint,
    url_verified_blob_key,
)

SLACK_SECRET_SLOTS = (SLACK_BOT_TOKEN_SLOT, SLACK_SIGNING_SECRET_SLOT)

BOT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,34}$"

TOKEN_REJECTED_ERRORS = ("invalid_auth", "token_revoked", "account_inactive", "not_authed")

SLACK_APP_MANIFEST_TEMPLATE = """\
display_information:
  name: {name}
features:
  agent_view:
    agent_description: Answers @mentions and direct messages, and follows the threads it joins.
  app_home:
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: {name}
oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - assistant:write
      - channels:history
      - chat:write
      - files:read
      - files:write
      - groups:history
      - im:history
      - mpim:history
      - users:read
      - users:read.email
settings:
  event_subscriptions:
    request_url: {request_url}
    bot_events:
      - app_home_opened
      - app_mention
      - message.channels
      - message.groups
      - message.im
      - message.mpim
  interactivity:
    is_enabled: true
    request_url: {interactivity_url}
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
"""


class SlackConnectInput(BaseModel):
    pass


class SlackManifestInput(BaseModel):
    name: str = Field(
        default="ufo", description="The bot's display name shown in Slack, 1-35 plain characters."
    )


def _events_url(public_base_url: str, workspace_id: UUID) -> str:
    return f"{public_base_url.rstrip('/')}/surface/slack/{workspace_id}"


def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult:
    payload = {"state": state, "hint": hint, "events_url": events_url, **extra}
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult:
    """One idempotent walk of the connection state machine, reporting where it stopped:
    `not_configured` (a secret is missing or the token is rejected), `pending` (identity proven,
    awaiting Slack's first signed event), or `connected`. Deriving identity here is owner-gated —
    the bot is shared by every member; a signed Slack request proves the same record itself, the
    signature gating what the owner gates here."""
    assert ctx.ext is not None
    events_url = (
        None
        if ctx.public_base_url is None
        else _events_url(ctx.public_base_url, ctx.turn.workspace_id)
    )
    missing = []
    for slot in SLACK_SECRET_SLOTS:
        try:
            await ctx.ext.credentials.get(slot)
        except CredentialSlotUnset:
            missing.append(slot)
    if missing:
        return _state(
            "not_configured",
            "use slack_app_manifest to create the app, then request_credentials for "
            f"{', '.join(missing)}, then slack_connect again.",
            events_url,
            missing=missing,
        )
    bot_token = await ctx.ext.credentials.get(SLACK_BOT_TOKEN_SLOT)
    identity = await read_identity(ctx.blob, ctx.turn.workspace_id, bot_token)
    if identity is None:
        if not await ctx.speaker_is_owner():
            raise ValueError("only the workspace owner can connect Slack")
        try:
            identity = await SlackIdentityResolver(
                ctx.blob, ctx.turn.workspace_id, bot_token
            ).resolve()
        except SlackIdentityError as error:
            if error.error == MALFORMED_IDENTITY_ERROR:
                return _state(
                    "not_configured",
                    "Slack auth.test did not return a usable team/bot id — re-copy the Bot User "
                    "OAuth Token from OAuth & Permissions and collect it again.",
                    events_url,
                )
            return _state(
                "not_configured",
                _token_diagnosis(error.error),
                events_url,
            )
    if await _verified(ctx):
        return _state(
            "connected",
            "Slack reached this deploy and the credentials check out — talk to the bot.",
            events_url,
            team_id=identity.team_id,
        )
    return _state(
        "pending",
        "identity proven — invite the bot to a channel and @mention it, or DM it; its first "
        "message flips this to connected.",
        events_url,
        team_id=identity.team_id,
    )


async def _verified(ctx: ToolContext) -> bool:
    """Whether Slack has reached this deploy with the signing secret currently stored — the marker
    the slack surface writes on a signature-verified request, trusted only while its fingerprint
    matches the stored secret, so a rotation reads as pending again."""
    assert ctx.ext is not None
    key = url_verified_blob_key(ctx.turn.workspace_id)
    if not await ctx.blob.exists(key):
        return False
    try:
        marker = json.loads(await ctx.blob.get(key))
    except (ValueError, json.JSONDecodeError):
        return False
    try:
        secret = await ctx.ext.credentials.get(SLACK_SIGNING_SECRET_SLOT)
    except CredentialSlotUnset:
        return False
    return isinstance(marker, dict) and marker.get("fingerprint") == signing_secret_fingerprint(
        secret
    )


async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult:
    """The ready-to-paste Slack app manifest for this deploy: the member creates the app from it at
    api.slack.com (Create New App → From a manifest), so the scopes, events, and request URLs are
    right by construction — never hand-assembled."""
    if not re.match(BOT_NAME_PATTERN, args.name):
        raise ValueError("bot display name must be 1-35 plain characters")
    base = ctx.public_base_url
    if not base:
        raise ValueError(
            "no public base URL — set [connect] public_base_url and restart the deploy"
        )
    events_url = _events_url(base, ctx.turn.workspace_id)
    manifest = SLACK_APP_MANIFEST_TEMPLATE.format(
        name=args.name, request_url=events_url, interactivity_url=f"{events_url}/interactive"
    )
    return ToolResult(content=(TextContent(text=manifest),))


def _token_diagnosis(error: str) -> str:
    if error in TOKEN_REJECTED_ERRORS:
        return (
            f"Slack rejected the bot token ({error}) — re-copy the Bot User OAuth Token from "
            "OAuth & Permissions and collect it again."
        )
    return f"Slack auth.test failed: {error or 'no error given'}"


TOOLS = (
    ToolDef(
        name="slack_connect",
        description=(
            "Walk the Slack connection state machine and report where it stands "
            "(not_configured / pending / connected, plus this deploy's Events request URL). "
            "Idempotent — call it before, during, and after setup. When the secrets are stored "
            "it verifies the bot token live and derives the app's identity itself; that step is "
            "owner-only."
        ),
        input_model=SlackConnectInput,
        handler=slack_connect_handler,
        untrusted=True,
    ),
    ToolDef(
        name="slack_app_manifest",
        description=(
            "The exact Slack app manifest for this deploy, ready to paste at api.slack.com "
            "(Create New App → From a manifest). Show it to the member verbatim in a code block "
            "when they are connecting Slack."
        ),
        input_model=SlackManifestInput,
        handler=slack_manifest_handler,
    ),
)
