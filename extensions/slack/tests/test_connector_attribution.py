"""The connector Slack send's mentioning footer, end to end across the two extensions it joins: the
surface mirrors the bot-user id it proved, the declared `pre_tool_use` hook reads that mirror and
rewrites the send's arguments, and the connectors tool's own attribution then finds its line already
present and adds nothing.

Every failure on this path resolves to the plain footer, never to a refused send: a gating hook that
raised or timed out would deny the member's Slack message, so the unresolvable cases are asserted
through the real `HookChain`, where a denial would show.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_connectors.tools as connector_tools
import ufo_ext_slack.surface as slack
from cryptography.fernet import Fernet
from ufo_ext_connectors.tools import CallExternalToolInput
from ufo_ext_slack.attribution import addressing_mention, mention_attribution
from ufo_ext_slack.hooks import CONNECTOR_CALL_TOOL, attribute_connector_send
from ufo_ext_slack.manifest import manifest as slack_manifest

from ufo.blob import BlobStore, FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import CredentialAccess, ExtensionContext, JsonValue, ScopedStore
from ufo.ext.loader import BoundHook, HookChain, HookResolution, turn_hooks
from ufo.ext.manifest import PreToolUse
from ufo.ext.surface import AMBIENT_CONTEXT_ELEMENT, SurfaceContext
from ufo.schema import tables
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.workspace import ws

BOT_USER_ID = "U0BOTUFO"
SLACK_SEND_SLUG = "SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL"
SLACK_HISTORY_SLUG = "SLACK_FETCH_CONVERSATION_HISTORY"
MARK = "beef"
SENT_TEXT = "the plan is posted"


class _UnreadableStore(ScopedStore):
    """A scoped store whose read fails — the transient database fault the footer must survive."""

    async def get(self, key: str) -> JsonValue | None:
        raise RuntimeError("ext_store unavailable")


@dataclass
class _IdentityReadContext:
    """The three members `_identity` touches on a surface context: the workspace the route bound,
    the blob holding the identity record, and the bot-token slot it is fingerprinted against."""

    workspace_id: UUID
    blob: BlobStore
    bot_token: str

    async def credential(self, slot: str) -> str:
        assert slot == slack.SLACK_BOT_TOKEN_SLOT
        return self.bot_token


def _send(arguments: dict[str, JsonValue], slug: str = SLACK_SEND_SLUG) -> CallExternalToolInput:
    return CallExternalToolInput(
        tool_name=slug,
        source_id=connector_tools.SLACK_PROVIDER,
        arguments=arguments,
        user_description="posting the plan in the launch channel",
    )


def _declared_chain() -> HookChain:
    """The hook the Slack manifest declares, bound the way a turn binds it."""
    return turn_hooks(
        (slack_manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=SHARED_AUDIENCE,
    )


def _chain_over(store: ScopedStore) -> HookChain:
    (spec,) = slack_manifest().hooks
    ext = ExtensionContext(store=store, credentials=CredentialAccess(declared=frozenset()))
    return HookChain(hooks={"pre_tool_use": (BoundHook(spec=spec, ext=ext),)})


async def _fire(chain: HookChain, call: CallExternalToolInput) -> HookResolution:
    return await chain.fire(
        "pre_tool_use",
        PreToolUse(tool_name=CONNECTOR_CALL_TOOL, tool_input=call),
        None,
        None,
        None,
    )


async def _seed_workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def test_the_manifest_declares_the_hook_on_the_connector_call() -> None:
    (spec,) = slack_manifest().hooks
    assert (spec.event, spec.tools) == ("pre_tool_use", (CONNECTOR_CALL_TOOL,))
    assert spec.handler is attribute_connector_send


async def test_the_surfaces_own_identity_read_mirrors_the_id_into_the_store(
    db: None, tmp_path: Path
) -> None:
    """The writer, exercised where it lives: every inbound event resolves the workspace's identity
    through `_identity`, and that read is what lands the id the hook later reads — so a workspace
    installed before the mirror existed is covered by its next Slack event, not by a reinstall."""
    workspace_id = await _seed_workspace()
    bot_token = "xoxb-mirrored"
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put(
        slack.identity_blob_key(workspace_id),
        slack.SlackIdentity(
            bot_token_fingerprint=slack.bot_token_fingerprint(bot_token),
            team_id="T01234567",
            bot_user_id=BOT_USER_ID,
        )
        .model_dump_json()
        .encode(),
    )
    ctx = _IdentityReadContext(workspace_id=workspace_id, blob=blob, bot_token=bot_token)

    with ws(workspace_id):
        identity = await slack._identity(cast(SurfaceContext, ctx))
        assert identity is not None and identity.bot_user_id == BOT_USER_ID
        assert (
            await ScopedStore(extension=slack.SLACK_EXTENSION).get(slack.SELF_USER_ID_STORE_KEY)
            == BOT_USER_ID
        )


async def test_the_hook_footers_a_send_with_the_id_the_surface_mirrored(db: None) -> None:
    """The join that makes the mention reachable from a hook: the surface writes the proved id into
    the extension's own scoped store, and the hook — which holds a ScopedStore and no BlobStore —
    reads it back with no call to Slack."""
    workspace_id = await _seed_workspace()
    with ws(workspace_id):
        await slack._mirror_self_user_id(workspace_id, BOT_USER_ID)
        resolution = await _fire(
            _declared_chain(),
            _send({"channel": "C1", "text": SENT_TEXT, "markdown_text": "the *plan* is posted"}),
        )

    assert (resolution.denied, resolution.failed_closed) == (None, None)
    assert isinstance(resolution.tool_input, CallExternalToolInput)
    footer = mention_attribution(BOT_USER_ID)
    assert resolution.tool_input.arguments == {
        "channel": "C1",
        "text": f"{SENT_TEXT}\n\n{footer}",
        "markdown_text": f"the *plan* is posted\n\n{footer}",
    }
    assert footer == f"Sent using <@{BOT_USER_ID}>"


async def test_the_hooks_footer_leaves_the_connector_tool_nothing_to_append(db: None) -> None:
    """No double footer: the mentioning line is one of the two forms the tool's never-stack guard
    matches, so its own append is suppressed — the plain line rides only when the hook wrote
    none."""
    workspace_id = await _seed_workspace()
    with ws(workspace_id):
        await slack._mirror_self_user_id(workspace_id, BOT_USER_ID)
        resolution = await _fire(_declared_chain(), _send({"channel": "C1", "text": SENT_TEXT}))

    assert isinstance(resolution.tool_input, CallExternalToolInput)
    rewritten = resolution.tool_input.arguments
    assert (
        connector_tools.slack_attributed(connector_tools.SLACK_PROVIDER, SLACK_SEND_SLUG, rewritten)
        == rewritten
    )
    text = str(rewritten["text"])
    assert text.count(mention_attribution(BOT_USER_ID)) == 1
    assert connector_tools.UFO_ATTRIBUTION not in text


async def test_a_workspace_with_no_proved_id_keeps_the_plain_attribution(db: None) -> None:
    """A deploy running the connector without a Slack install — no mirror row — degrades to the
    footer the tool writes on its own, and the send still dispatches."""
    workspace_id = await _seed_workspace()
    arguments: dict[str, JsonValue] = {"channel": "C1", "text": SENT_TEXT}
    with ws(workspace_id):
        resolution = await _fire(_declared_chain(), _send(arguments))

    assert (resolution.denied, resolution.failed_closed) == (None, None)
    assert resolution.tool_input is not None
    assert isinstance(resolution.tool_input, CallExternalToolInput)
    assert resolution.tool_input.arguments == arguments
    assert connector_tools.slack_attributed(
        connector_tools.SLACK_PROVIDER, SLACK_SEND_SLUG, arguments
    ) == {"channel": "C1", "text": f"{SENT_TEXT}\n\n{connector_tools.UFO_ATTRIBUTION}"}


async def test_an_unreadable_store_never_denies_the_members_send() -> None:
    """The gating hazard, asserted where it would show: a read that raises leaves the resolution
    undenied and the arguments as the model wrote them."""
    call = _send({"channel": "C1", "text": SENT_TEXT})
    resolution = await _fire(_chain_over(_UnreadableStore(extension=slack.SLACK_EXTENSION)), call)

    assert (resolution.denied, resolution.failed_closed) == (None, None)
    assert resolution.tool_input is call


@pytest.mark.parametrize(
    "call",
    [
        _send({"channel": "C1"}, slug=SLACK_HISTORY_SLUG),
        _send({"channel": "C1", "text": "  "}),
        CallExternalToolInput(
            tool_name=SLACK_SEND_SLUG,
            source_id="gmail",
            arguments={"to": "a@b.test", "text": SENT_TEXT},
            user_description="mailing the plan",
        ),
    ],
    ids=["read", "empty_text", "other_provider"],
)
async def test_only_a_slack_send_is_rewritten(call: CallExternalToolInput) -> None:
    """A read, an empty body, and another provider's send are left alone — and the two that are not
    Slack sends at all are decided before the id is read, over a store that would raise."""
    resolution = await _fire(_chain_over(_UnreadableStore(extension=slack.SLACK_EXTENSION)), call)

    assert (resolution.denied, resolution.failed_closed) == (None, None)
    assert resolution.tool_input is not None
    assert isinstance(resolution.tool_input, CallExternalToolInput)
    assert resolution.tool_input.arguments == call.arguments


def test_a_footered_message_is_not_a_message_addressed_to_the_agent() -> None:
    """A connector send is authored by a member's own connected account, so no inbound bot-author
    drop catches it. Read naively, its footer's mention would open a turn about the deploy's own
    outbound message; the footer-aware test keeps a real mention addressing and the footer's not."""
    footered = f"{SENT_TEXT}\n\n{mention_attribution(BOT_USER_ID)}"
    event = {"type": "message", "text": footered}
    assert slack.slack_message_addressed(event, BOT_USER_ID, is_dm=False) is False
    assert (
        slack.slack_message_addressed(
            {"type": "message", "text": f"<@{BOT_USER_ID}> what happened here?"},
            BOT_USER_ID,
            is_dm=False,
        )
        is True
    )
    assert addressing_mention(f"<@{BOT_USER_ID}> and {footered}", BOT_USER_ID) is True


def test_a_footered_message_stays_visible_in_the_ambient_digest() -> None:
    """The second half of the same defect: a mention-bearing message is dropped from the digest
    because that mention already became its own turn. The deploy's own connector send never did, so
    dropping it would hide this product's messages from the agent reading the channel later."""
    footered = f"{SENT_TEXT}\n\n{mention_attribution(BOT_USER_ID)}"
    messages: list[object] = [
        {"user": "U_MEMBER", "ts": "1700000000.000100", "text": footered},
        {"user": "U_OTHER", "ts": "1700000060.000200", "text": f"<@{BOT_USER_ID}> already a turn"},
    ]
    digest = slack.ambient_digest(messages, BOT_USER_ID, slack.AMBIENT_THREAD_NOTE, MARK)

    assert AMBIENT_CONTEXT_ELEMENT in digest
    assert SENT_TEXT in digest
    assert "already a turn" not in digest
