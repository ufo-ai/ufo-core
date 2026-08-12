"""Slack's message markup, rendered as the words a reader reads.

Slack delivers a message with its entities encoded: a member is `<@U0BG8632NDS>`, a channel is
`<#C0271QK6M|ufo-eng>`, a link is `<https://example.com|the docs>`, and `&`, `<` and `>` arrive
escaped. Those forms are Slack's wire and nobody's words. A member reading a conversation in the
portal sees the id where the name should be, and a model reading the turn cannot tell one id from
another — neither can act on `<@U0BG8632NDS>`.

`render_markup` is the one rewrite, applied to a message as it is admitted, so the stored turn is
the readable one and every reader downstream — the title, the transcript, the model — reads the
same message. An id the caller could not resolve keeps its encoded form: an unresolved mention is
a name this workspace does not know, and inventing one would be worse than showing the wire. Only
the four shapes Slack encodes are rewritten — a mention, a channel, a broadcast, and a link with a
scheme — so a member who typed angle brackets around anything else reads them back as they wrote
them.

`unescape` is deliberately not part of it. Slack escapes `<`, `>` and `&` wherever a member typed
them, and that escaping is what keeps another principal's words from closing the prompt element
they are quoted inside — the ambient digest carries bystanders' messages and relies on it. So
entity rendering, which is safe for anyone's words, is separate from unescaping, which is only
safe for the words of the member whose own turn this is."""

import re
from collections.abc import Mapping

SLACK_ENTITY = re.compile(
    r"<(?:(?P<kind>[@#!])(?P<id>[^<>|]*)|(?P<url>[a-z][a-z0-9+.\-]*:[^<>|]*))"
    r"(?:\|(?P<label>[^<>]*))?>"
)
BROADCASTS = frozenset({"here", "channel", "everyone"})
SLACK_ESCAPES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))


def mentioned_users(text: str) -> frozenset[str]:
    """The member ids this text mentions. A caller resolves these against `users.info`, so the
    kind is split from `mentioned_channels` here rather than guessed from the id's letter."""
    return _mentioned(text, "@")


def mentioned_channels(text: str) -> frozenset[str]:
    """The channel ids this text mentions, which a caller resolves against `conversations.info`."""
    return _mentioned(text, "#")


def _mentioned(text: str, kind: str) -> frozenset[str]:
    return frozenset(
        match.group("id")
        for match in SLACK_ENTITY.finditer(text)
        if match.group("kind") == kind and match.group("id")
    )


def render_markup(text: str, names: Mapping[str, str]) -> str:
    """`text` with every Slack entity replaced by what it names.

    A mention becomes `@Real Name` and a channel `#ufo-eng`, from `names` — else from the label
    Slack packed beside the id, else left as it arrived. A broadcast becomes `@here`. A link keeps
    its URL and carries the label beside it, since the label is what the member sees and the URL is
    the only part the agent can open; the URL alone where the two are the same string.

    Escapes are left standing, so a `&lt;` a member typed is never read as an entity here and
    never becomes a `<` in somebody else's element downstream."""
    return SLACK_ENTITY.sub(lambda match: _entity(match, names), text)


def unescape(text: str) -> str:
    """`text` with Slack's `&amp;`, `&lt;` and `&gt;` returned to the characters the member typed.

    Only for the words of the member whose turn this is: their own message is already their own,
    so nothing is forged by giving the characters back. Another principal's words — anything the
    ambient digest carries — keep their escapes, which is what stops a bystander from closing the
    element they are quoted inside."""
    for escape, character in SLACK_ESCAPES:
        text = text.replace(escape, character)
    return text


def _entity(match: re.Match[str], names: Mapping[str, str]) -> str:
    body, label = match.group("id"), match.group("label")
    match match.group("kind"):
        case "@":
            named = names.get(body) or label
            return f"@{named}" if named else match.group(0)
        case "#":
            named = names.get(body) or label
            return f"#{named}" if named else match.group(0)
        case "!":
            name = body.partition("^")[0]
            if name in BROADCASTS:
                return f"@{name}"
            return label or f"@{name}"
        case _:
            url = match.group("url")
            return f"{label} ({url})" if label and label != url else url
