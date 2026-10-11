"""The inbound read for a connector-sent Slack message's attribution footer.

The connectors tool writes the footer after its sending account proves the destination internal.
The Slack hook supplies the optional bot user this workspace's own install proved, so the connector
can name that bot instead of the product.

`addressing_mention` is the other half of that write, read by the Slack surface. A
connector send is authored by a member's own connected Slack account, not by the deploy's bot, so
neither inbound bot-author drop catches it; a footer naming the bot would otherwise read as a
message addressed to the agent and, in the ambient digest, as traffic to hide."""

from collections.abc import Iterator, Mapping

from ufo_ext_connectors.tools import (
    SLACK_BLOCKS_ARGUMENT,
    SLACK_TEXT_ARGUMENT,
    attribution_stripped,
)


def addressing_mention(text: str, bot_user_id: str) -> bool:
    """Whether `text` mentions the bot user anywhere other than on an attribution this deploy wrote.
    Every attribution is removed before the test, so a member who addresses the agent in a message
    that also carries a footer is still addressing it.

    A footer is stripped here wherever a message can carry it, not only on the whole line the
    outbound guard reads: a body quoting a marked message keeps text on both sides of the footer,
    and a fallback `text` Slack stores comes back with it flattened onto the body's own line.
    Tolerating that is what keeps this deploy's own published message from reading as the member
    addressing the agent."""
    return f"<@{bot_user_id}>" in attribution_stripped(text)


def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]:
    """Every string a Slack message could carry a mention in: its `text` and the text of each block
    and rich-text element under it. The footer this deploy appends lives in a context element and
    never in `text` — read `text` alone and the mention it carries is invisible to the address
    decision while Slack still delivers `app_mention` for it."""
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
