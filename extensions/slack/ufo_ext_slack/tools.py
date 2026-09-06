"""Slack setup as chat actions: an admin connects Slack in conversation and the agent drives it.

The three tools are instance actions on core's `surface/slack` object — a read of that object
lists them, and `object_action` dispatches them under `action:surface:<name>` with this extension's
own context. Two install paths converge on the same per-workspace bot token and identity record.
`slack_connect` defaults to `method="oauth"`: when the deploy has its own Slack app configured, it
seals an install handoff to the admin and the bot-token slot and returns an "Add to Slack" link;
the OAuth callback lands the token, team binding, and identity. `method="manifest"` is the
bring-your-own-app path — `slack_app_manifest` renders the exact app YAML the member creates the
app from, the two secrets (bot token, signing secret) travel through `request_credentials`
fulfillment into per-workspace slots (never through chat), and `slack_connect` derives the identity
with `auth.test`. Both paths report the same downstream states: `pending` once identity is proven,
`connected` once a
signature-verified request writes the url-verified marker. The manifest template below is pinned to
the skill's YAML by a test, so the scopes and events never drift apart.

`slack_channels` is the one runtime tool here: it spends the manifest's `*:read` scopes, paging
conversations.list on the bot token so the agent can discover a channel by name — or a DM by who is
in it — instead of only acting on an id it was handed."""

import json
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.objects import SURFACE_KIND
from ufo.sdk.surfaces import CredentialSlotUnset, SurfaceInstallationConflict
from ufo.sdk.tools import (
    ActionPresentation,
    ObjectBinding,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
)
from ufo_ext_slack.surface import (
    MALFORMED_IDENTITY_ERROR,
    SLACK_BOT_TOKEN_SLOT,
    SLACK_CLIENT_ID_ENV,
    SLACK_CLIENT_SECRET_ENV,
    SLACK_INSTALL_PAYLOAD,
    SLACK_SIGNING_SECRET_SLOT,
    SURFACE_SLACK,
    URL_VERIFIED_BLOB_KEY,
    SlackConversationSearch,
    SlackIdentity,
    SlackIdentityError,
    SlackIdentityResolver,
    mirror_url_verified,
    read_identity,
    slack_authorize_url,
    slack_client_id,
    slack_installation_id,
    slack_oauth_redirect_uri,
    verifying_fingerprint,
)

SLACK_SECRET_SLOTS = (SLACK_BOT_TOKEN_SLOT, SLACK_SIGNING_SECRET_SLOT)
SLACK_CONNECT_ACTION = "slack_connect"
SLACK_APP_MANIFEST_ACTION = "slack_app_manifest"
SLACK_CHANNELS_ACTION = "slack_channels"
CONNECT_ADMIN_ONLY = "only a workspace admin can connect Slack"

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
      - channels:read
      - chat:write
      - files:read
      - files:write
      - groups:history
      - groups:read
      - im:history
      - im:read
      - mpim:history
      - mpim:read
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
    model_config = ConfigDict(extra="forbid")
    method: Literal["oauth", "manifest"] = Field(
        default="oauth",
        description=(
            "How to install Slack: 'oauth' (preferred) returns a one-click Add to Slack link for "
            "the deploy's own app; 'manifest' is the bring-your-own-app path (create an app from "
            "slack_app_manifest and enter its secrets privately)."
        ),
    )


class SlackManifestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_name: str = Field(
        default="ufo", description="The bot's display name shown in Slack, 1-35 plain characters."
    )


class SlackChannelsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        default="",
        description="Case-insensitive text matched against each conversation's name, purpose, "
        "topic, and — for DMs and group DMs — the people in it. Leave empty to list from the top.",
    )


def _events_url(public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/surface/slack"


def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult:
    payload = {"state": state, "hint": hint, "events_url": events_url, **extra}
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult:
    """One idempotent walk of the install state machine. Once identity exists (either path) it
    reports `pending`, or `connected` once a signature-verified request has marked the deploy
    reachable. Otherwise it installs by `method`: `oauth` mints the admin an Add to Slack link;
    `manifest` reports `not_configured` until the secrets are entered, then derives the identity
    with `auth.test`. Minting the link and deriving identity are admin-only — the bot is shared."""
    assert ctx.ext is not None
    events_url = None if ctx.public_base_url is None else _events_url(ctx.public_base_url)
    try:
        bot_token = await ctx.ext.credentials.get(SLACK_BOT_TOKEN_SLOT)
    except CredentialSlotUnset:
        bot_token = None
    identity = await read_identity(ctx.blob, bot_token) if bot_token is not None else None
    if identity is None:
        if args.method == "oauth":
            return await _oauth_link(ctx, events_url)
        derived = await _derive_manifest_identity(ctx, events_url)
        if isinstance(derived, ToolResult):
            return derived
        identity = derived
    try:
        await ctx.ext.installations.bind(SURFACE_SLACK, slack_installation_id(identity.team_id))
    except SurfaceInstallationConflict:
        return _state(
            "not_configured",
            "This Slack workspace is already connected to another UFO workspace.",
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


async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult:
    """Mint the admin an Add to Slack link for the deploy's own app; the OAuth callback completes
    the install. Falls back to the manifest path when the deploy has no app configured."""
    if not (os.environ.get(SLACK_CLIENT_ID_ENV) and os.environ.get(SLACK_CLIENT_SECRET_ENV)):
        return _state(
            "not_configured",
            "This deploy has no Slack app for one-click install — connect Slack with "
            "method='manifest' to bring your own app.",
            events_url,
        )
    if not await ctx.speaker_is_admin():
        return _state(
            "not_installed",
            "Ask a workspace admin to connect Slack — only they can install it.",
            events_url,
        )
    if not ctx.public_base_url:
        raise ValueError(
            "no public base URL — set [connect] public_base_url and restart the deploy"
        )
    sealed = await ctx.begin_credential_authorization(SLACK_BOT_TOKEN_SLOT, SLACK_INSTALL_PAYLOAD)
    authorize_url = slack_authorize_url(
        slack_client_id(), slack_oauth_redirect_uri(ctx.public_base_url), sealed
    )
    return _state(
        "not_installed",
        "Open this Add to Slack link to install ufo in your Slack workspace; the link expires "
        "shortly, so ask again for a fresh one if it lapses.",
        events_url,
        authorize_url=authorize_url,
    )


async def _derive_manifest_identity(
    ctx: ToolContext, events_url: str | None
) -> SlackIdentity | ToolResult:
    """The bring-your-own-app path: report `not_configured` until both secret slots are filled, then
    prove and persist the identity with `auth.test` (admin-only), returning it for the common tail —
    or a `not_configured` diagnosis when Slack rejects the token."""
    assert ctx.ext is not None
    missing = []
    for slot in SLACK_SECRET_SLOTS:
        try:
            await ctx.ext.credentials.get(slot)
        except CredentialSlotUnset:
            missing.append(slot)
    if missing:
        return _state(
            "not_configured",
            "use slack_app_manifest to create the app, then the credential collection's "
            f"request_credentials action for {', '.join(missing)}, then slack_connect again.",
            events_url,
            missing=missing,
        )
    bot_token = await ctx.ext.credentials.get(SLACK_BOT_TOKEN_SLOT)
    if not await ctx.require_speaking_admin(CONNECT_ADMIN_ONLY):
        raise ValueError(CONNECT_ADMIN_ONLY)
    try:
        return await SlackIdentityResolver(ctx.blob, bot_token).resolve()
    except SlackIdentityError as error:
        if error.error == MALFORMED_IDENTITY_ERROR:
            return _state(
                "not_configured",
                "Slack auth.test did not return a usable team/bot id — re-copy the Bot User "
                "OAuth Token from OAuth & Permissions and collect it again.",
                events_url,
            )
        return _state("not_configured", _token_diagnosis(error.error), events_url)


async def _verified(ctx: ToolContext) -> bool:
    """Whether Slack has reached this deploy with the signing secret currently in force — the
    marker the slack surface writes on a signature-verified request, trusted only while its
    fingerprint matches the workspace's verifying secret (its own slot, else the deploy env), so a
    rotation reads as pending again.

    A proof read here re-stamps the scoped-store mirror the workspace fact reads. This is the only
    reader that holds both, so it is where an install proved before that mirror existed — the
    marker written by an earlier image, no `ext_store` row — stops reading live to this tool and
    absent to the fact."""
    assert ctx.ext is not None
    fingerprint = await verifying_fingerprint(ctx.ext.credentials)
    if fingerprint is None:
        return False
    key = URL_VERIFIED_BLOB_KEY
    if not await ctx.blob.exists(key):
        return False
    try:
        marker = json.loads(await ctx.blob.get(key))
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(marker, dict) or marker.get("fingerprint") != fingerprint:
        return False
    await mirror_url_verified(ctx.ext.workspace_id, fingerprint)
    return True


async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult:
    """The ready-to-paste Slack app manifest for this deploy: the member creates the app from it at
    api.slack.com (Create New App → From a manifest), so the scopes, events, and request URLs are
    right by construction — never hand-assembled."""
    if not re.match(BOT_NAME_PATTERN, args.bot_name):
        raise ValueError("bot display name must be 1-35 plain characters")
    base = ctx.public_base_url
    if not base:
        raise ValueError(
            "no public base URL — set [connect] public_base_url and restart the deploy"
        )
    events_url = _events_url(base)
    manifest = SLACK_APP_MANIFEST_TEMPLATE.format(
        name=args.bot_name, request_url=events_url, interactivity_url=f"{events_url}/interactive"
    )
    return ToolResult(content=(TextContent(text=manifest),))


async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult:
    """List and search the workspace's Slack conversations — channels, group DMs, and DMs — by
    name, purpose, topic, or the people in a DM, so the agent can act on one it discovered rather
    than only an id it was handed. Reads the app's own bot token — the surface's Slack credential —
    and pages conversations.list host-side, resolving each DM's members through the bot's own
    identity; the text it returns is member-authored, so the result is walled untrusted."""
    assert ctx.ext is not None
    try:
        bot_token = await ctx.ext.credentials.get(SLACK_BOT_TOKEN_SLOT)
    except CredentialSlotUnset as unset:
        raise ValueError(
            "Slack is not connected — set the bot token with slack_connect first."
        ) from unset
    identity = await read_identity(ctx.blob, bot_token)
    if identity is None:
        raise ValueError("Slack identity is not resolved yet — run slack_connect first.")
    found = await SlackConversationSearch(
        bot_token=bot_token, bot_user_id=identity.bot_user_id, query=args.query
    ).run()
    payload = {
        "conversations": [conversation.model_dump() for conversation in found.conversations],
        "truncated": found.truncated,
    }
    return ToolResult(content=(TextContent(text=json.dumps(payload)),), untrusted=True)


def _token_diagnosis(error: str) -> str:
    if error in TOKEN_REJECTED_ERRORS:
        return (
            f"Slack rejected the bot token ({error}) — re-copy the Bot User OAuth Token from "
            "OAuth & Permissions and collect it again."
        )
    return f"Slack auth.test failed: {error or 'no error given'}"


TOOLS = (
    ToolDef(
        name=SLACK_CONNECT_ACTION,
        description=(
            "Walk the Slack install state machine and report where it stands (not_configured / "
            "not_installed / pending / connected). Idempotent — call it before, during, and after "
            "setup. Defaults to the one-click OAuth install (returns an 'Add to Slack' link for "
            "an admin); pass method='manifest' for the bring-your-own-app path. Minting the link "
            "and deriving identity are admin-only."
        ),
        input_model=SlackConnectInput,
        handler=slack_connect_handler,
        bound=ObjectBinding(kind=SURFACE_KIND, binding="instance", name=SURFACE_SLACK),
        untrusted=True,
        side_effecting=True,
        presentation=ActionPresentation(label="Connect Slack"),
    ),
    ToolDef(
        name=SLACK_APP_MANIFEST_ACTION,
        description=(
            "The exact Slack app manifest for this deploy, ready to paste at api.slack.com "
            "(Create New App → From a manifest). Show it to the member verbatim in a code block "
            "when they are connecting Slack with the bring-your-own-app (manifest) path."
        ),
        input_model=SlackManifestInput,
        handler=slack_manifest_handler,
        bound=ObjectBinding(kind=SURFACE_KIND, binding="instance", name=SURFACE_SLACK),
    ),
    ToolDef(
        name=SLACK_CHANNELS_ACTION,
        description=(
            "List and search the connected workspace's Slack conversations — public and private "
            "channels, group DMs, and 1:1 DMs — by name, purpose, topic, or (for DMs) the people "
            "in them. Use it to find the conversation to post in or read from when you were given "
            "a name or a person rather than an id. Returns each match's id, kind, name, people, "
            "purpose, topic, and membership; `truncated` is true when the workspace has more than "
            "one scan covers — search again with a more specific query."
        ),
        input_model=SlackChannelsInput,
        handler=slack_channels_handler,
        bound=ObjectBinding(kind=SURFACE_KIND, binding="instance", name=SURFACE_SLACK),
        untrusted=True,
    ),
)
