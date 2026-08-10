"""This extension's turn-lifecycle hook: the connector Slack send, footered with a mention of the
bot user this workspace's own install proved.

The footer belongs to whoever knows the id, and that is this extension — the connectors tool marks
the send generically because it has no Slack identity to name. So the marking is rewritten here, in
the arguments, before the tool dispatches: `pre_tool_use` on `call_external_tool` returns
`ModifyInput`, and the tool's own append then finds the attribution line already present and adds
nothing.

`pre_tool_use` is a gating event: a handler that raises or outruns the loader's per-handler timeout
is a Deny, and the member's Slack send never leaves. A footer is cosmetic and must never hold that
power, so every await here is inside a timeout of its own and every failure resolves to `None` —
no rewrite, and the generic attribution the tool appends on its own. The read is the extension's
scoped store, which the surface mirrors the id into, so the send path asks Slack nothing."""

import asyncio
import re

from ufo_ext_connectors.tools import CallExternalToolInput

from ufo.sdk.manifest import HookContext, HookOutcome, ModifyInput, PreToolUse
from ufo.sdk.o11y import log
from ufo_ext_slack.attribution import is_slack_send, mention_attributed
from ufo_ext_slack.surface import BOT_USER_ID_PATTERN, SELF_USER_ID_STORE_KEY

CONNECTOR_CALL_TOOL = "call_external_tool"
SELF_USER_ID_READ_SECONDS = 1.0


async def attribute_connector_send(ctx: HookContext) -> HookOutcome:
    """Rewrite a connector Slack send's message text to carry the mentioning footer. Any other
    connector call, and any send whose bot user this workspace has not proved, is left exactly as
    the model wrote it."""
    match ctx.payload:
        case PreToolUse(tool_input=CallExternalToolInput() as call) if is_slack_send(
            call.source_id, call.tool_name
        ):
            bot_user_id = await _mirrored_self_user_id(ctx)
            if bot_user_id is None:
                return None
            arguments = mention_attributed(call.arguments, bot_user_id)
            return ModifyInput(tool_input=call.model_copy(update={"arguments": arguments}))
        case _:
            return None


async def _mirrored_self_user_id(ctx: HookContext) -> str | None:
    """The bot-user id the Slack surface mirrored for this workspace, or None when it never proved
    one (no install yet, or a deploy running the connector without the Slack surface installed).
    One scoped-store read per send, uncached, so a reinstall under a new bot user is picked up
    without a restart; a read that fails or outruns its own budget returns None rather than letting
    the gating hook deny the send."""
    try:
        async with asyncio.timeout(SELF_USER_ID_READ_SECONDS):
            mirrored = await ctx.ext.store.get(SELF_USER_ID_STORE_KEY)
    except Exception as error:
        log("slack.attribution.self_user_id_unread", error_class=type(error).__name__)
        return None
    if isinstance(mirrored, str) and re.match(BOT_USER_ID_PATTERN, mirrored):
        return mirrored
    return None
