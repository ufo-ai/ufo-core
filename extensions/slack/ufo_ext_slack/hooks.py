"""This extension's hooks: the connector Slack send names the bot user this workspace's own install
proved, and the connect button a landed connection settles.

The footer identity belongs to whoever knows it, and that is this extension. The connectors tool
owns the audience check because it holds the account that sends the message, and writes the generic
footer without this extension. This hook supplies the optional bot-user id before dispatch, so the
tool can name that bot after it proves the destination internal.

`pre_tool_use` is a gating event: a handler that raises or outruns the loader's per-handler timeout
is a Deny, and the member's Slack send never leaves. A footer is cosmetic and must never hold that
power, so the store read is inside a timeout of its own and every failure resolves to `None` — no
identity rewrite, and the connector can still write its generic footer. The surface mirrors the id
into this extension's scoped store."""

import asyncio
import re

from ufo_ext_connectors.tools import CallExternalToolInput, is_slack_send

from ufo.sdk.grants import ConnectionRecorded
from ufo.sdk.manifest import HookContext, HookOutcome, ModifyInput, PreToolUse
from ufo.sdk.o11y import log
from ufo_ext_slack.surface import (
    BOT_USER_ID_PATTERN,
    SELF_USER_ID_STORE_KEY,
    SLACK_BOT_TOKEN_SLOT,
    ConnectMessage,
    connect_message_key,
    settle_connect_message,
)

CONNECTOR_CALL_TOOL = "call_external_tool"
SELF_USER_ID_READ_SECONDS = 1.0


async def attribute_connector_send(ctx: HookContext) -> HookOutcome:
    """Supply the bot user for a connector Slack send's optional mentioning footer."""
    match ctx.payload:
        case PreToolUse(tool_input=CallExternalToolInput() as call) if is_slack_send(
            call.source_id, call.tool_name
        ):
            bot_user_id = await _mirrored_self_user_id(ctx)
            if bot_user_id is None:
                return None
            return ModifyInput(
                tool_input=call.model_copy(update={"attribution_bot_user_id": bot_user_id})
            )
        case _:
            return None


async def _mirrored_self_user_id(ctx: HookContext) -> str | None:
    """Uncached, so a reinstall under a new bot user is picked up without a restart."""
    try:
        async with asyncio.timeout(SELF_USER_ID_READ_SECONDS):
            mirrored = await ctx.ext.store.get(SELF_USER_ID_STORE_KEY)
    except Exception as error:
        log("slack.attribution.self_user_id_unread", error_class=type(error).__name__)
        return None
    if isinstance(mirrored, str) and re.match(BOT_USER_ID_PATTERN, mirrored):
        return mirrored
    return None


async def settle_connect_button(ctx: HookContext) -> HookOutcome:
    """Rewrite the Slack button a landed connection answers into the account it made.

    The member pressed the button, authorized on the provider's pages, and never came back to the
    thread — so the thread is where the button would sit offering an act already done. The
    connection names who it belongs to and what for, which is what the button was held under; a
    workspace whose Slack never posted one has nothing held and nothing to rewrite.

    Slack is told after the grant stands, so a failure here costs the member nothing they did: the
    account is connected, the agent has been told, and the button is stale rather than wrong."""
    match ctx.payload:
        case ConnectionRecorded(
            provider=provider,
            account_id=account_id,
            account_label=account_label,
            owner_member_id=owner,
        ):
            held = await ctx.ext.store.get(connect_message_key(owner, provider))
            if held is None:
                return None
            await settle_connect_message(
                await ctx.ext.credentials.get(SLACK_BOT_TOKEN_SLOT),
                ConnectMessage.model_validate(held),
                provider,
                account_label or account_id,
            )
            await ctx.ext.store.delete(connect_message_key(owner, provider))
        case _:
            raise RuntimeError("slack connect hook fired on a non-connection_recorded payload")
    return None
