"""The attribution footer a connector-sent Slack message carries, rendered as a mention of the bot
user this workspace's own Slack install proved.

The connectors tool marks every Slack send it dispatches with `UFO_ATTRIBUTION` — the one connector
call that publishes text this deploy wrote into someone else's surface, marked there because a
connector send passes through no renderer of ours. This module writes that line with the deploy's
own bot user mentioned in place of its plain name, so a reader can reach the agent from the message
that mentions it.

The line is imported, never restated. The tool appends its own footer only to a text that carries
neither form as a line of its own, so a footer written here is what suppresses that append: the
never-stack guard and this footer are one join, not two copies of a string that could drift apart.

`addressing_mention` is the other half of the same join, read by the surface's inbound tests. A
connector send is authored by a member's own connected Slack account, not by the deploy's bot, so
neither inbound bot-author drop catches it; a footer naming the bot would otherwise read as a
message addressed to the agent and, in the ambient digest, as traffic to hide."""

from ufo_ext_connectors.tools import (
    ATTRIBUTION_LINE,
    SLACK_MESSAGE_NOUN,
    SLACK_MESSAGE_TEXT_ARGUMENTS,
    SLACK_PROVIDER,
    SLACK_SEND_VERBS,
    UFO_ATTRIBUTION_MENTION,
)

from ufo.sdk.context import JsonValue


def mention_attribution(bot_user_id: str) -> str:
    """The attribution footer mentioning this deploy's own Slack bot user, which Slack renders as
    the app's handle. It is one of the two forms `ATTRIBUTION_LINE` matches, so the connectors
    tool's own append finds an attribution line already present and adds nothing."""
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
    """`arguments` with the mentioning footer appended to a Slack send's message text. A text
    already carrying an attribution line of its own is returned untouched — the resend of a marked
    message, and the reason a second pass over these arguments adds nothing. A body that merely
    opens the way a footer does is not one, and is footered like any other."""
    footer = mention_attribution(bot_user_id)
    attributed = dict(arguments)
    for key in SLACK_MESSAGE_TEXT_ARGUMENTS:
        text = arguments.get(key)
        if isinstance(text, str) and text.strip() and not ATTRIBUTION_LINE.search(text):
            attributed[key] = f"{text}\n\n{footer}"
    return attributed


def addressing_mention(text: str, bot_user_id: str) -> bool:
    """Whether `text` mentions the bot user anywhere other than on an attribution line this deploy
    wrote. Every such line is removed before the test, so a member who addresses the agent in a
    message that also carries a footer is still addressing it."""
    return f"<@{bot_user_id}>" in ATTRIBUTION_LINE.sub("", text)
