"""The attribution footer a connector-sent Slack message carries, rendered as a mention of the bot
user this workspace's own Slack install proved.

The connectors tool marks every Slack send it dispatches with `UFO_ATTRIBUTION` — the one connector
call that publishes text this deploy wrote into someone else's surface, marked there because a
connector send passes through no renderer of ours. This module writes that attribution with the
deploy's own bot user mentioned in place of its plain name, so a reader can reach the agent from the
message that mentions it.

The footer's shapes are imported, never restated — only the subject changes here. The tool
attributes a body only when it carries no footer of its own, so a footer written here is what
suppresses that append: the never-stack guard and this footer are one join, not two copies of a
string that could drift apart.

`addressing_mention` is the other half of the same join, read by the surface's inbound tests. A
connector send is authored by a member's own connected Slack account, not by the deploy's bot, so
neither inbound bot-author drop catches it; a footer naming the bot would otherwise read as a
message addressed to the agent and, in the ambient digest, as traffic to hide."""

from collections.abc import Iterator, Mapping

from ufo_ext_connectors.tools import (
    SLACK_BLOCKS_ARGUMENT,
    SLACK_MESSAGE_NOUN,
    SLACK_PROVIDER,
    SLACK_SEND_VERBS,
    SLACK_TEXT_ARGUMENT,
    UFO_ATTRIBUTION_MENTION,
    UFO_ATTRIBUTION_MENTION_SUBJECT,
    attributed_arguments,
    attribution_stripped,
)

from ufo.sdk.context import JsonValue


def mention_attribution(bot_user_id: str) -> str:
    """The attribution footer mentioning this deploy's own Slack bot user, which Slack renders as
    the app's handle, in the plain shape a send's `text` carries. It is one of the forms
    `ATTRIBUTION_LINE` matches, so the connectors tool's own append finds an attribution line
    already present and adds nothing."""
    return UFO_ATTRIBUTION_MENTION.format(bot_user_id=bot_user_id)


def is_slack_send(provider: str, slug: str) -> bool:
    """Whether this connector call publishes a Slack message — the same provider and slug test the
    connectors tool makes before attributing arguments, so the two never disagree about which call
    carries a footer."""
    name = slug.lower()
    return (
        provider == SLACK_PROVIDER
        and SLACK_MESSAGE_NOUN in name
        and any(verb in name for verb in SLACK_SEND_VERBS)
    )


def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]:
    """`arguments` with the mentioning footer attached to a Slack send's body, in whichever shapes
    that body renders in — the same shaping the connectors tool applies to its own plain footer,
    with the bot mentioned in place of the product's name. A body already carrying an attribution
    line of its own is returned untouched — the resend of a marked message, and the reason a second
    pass over these arguments adds nothing. A body that merely opens the way a footer does is not
    one, and is footered like any other."""
    subject = UFO_ATTRIBUTION_MENTION_SUBJECT.format(bot_user_id=bot_user_id)
    return attributed_arguments(arguments, subject)


def addressing_mention(text: str, bot_user_id: str) -> bool:
    """Whether `text` mentions the bot user anywhere other than on an attribution this deploy wrote.
    Every attribution is removed before the test, so a member who addresses the agent in a message
    that also carries a footer is still addressing it.

    A footer is stripped here in any shape the send can arrive in, not only the whole-line shape
    the outbound guard reads: the broker keeps no separator on the fallback `text` it stores, so the
    footer comes back flattened onto the body's own line. Tolerating that is what keeps this
    deploy's own published message from reading as the member addressing the agent."""
    return f"<@{bot_user_id}>" in attribution_stripped(text)


def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]:
    """Every string a Slack message could carry a mention in: its `text` and the text of each block
    and rich-text element under it. A `blocks`-authored send names no `text` at all, so the footer
    this deploy appended lives only in a context element — read `text` alone and the mention it
    carries is invisible to the address decision while Slack still delivers `app_mention` for it."""
    blocks = event.get(SLACK_BLOCKS_ARGUMENT)
    return (str(event.get(SLACK_TEXT_ARGUMENT) or ""), *_nested_strings(blocks))


def _nested_strings(value: object) -> Iterator[str]:
    match value:
        case str():
            yield value
        case Mapping():
            for item in value.values():
                yield from _nested_strings(item)
        case list():
            for item in value:
                yield from _nested_strings(item)
