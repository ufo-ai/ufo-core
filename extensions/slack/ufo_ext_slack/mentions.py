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

`mention_markup` is the other direction, for the one text this deploy writes itself: the agent's
reply. The agent reads `@Real Name` and writes it back, so the reply must carry `<@U0BG8632NDS>`
before it reaches the wire or Slack notifies nobody. Every store still holds the readable name,
because the rewrite happens on the send path and nowhere else. It is deliberately not the inverse
of everything `render_markup` renders: a name is mapped only against the ids a caller vouches for,
a broadcast word is never mapped at all, and a name that resolves to nobody — or to two people —
stays the plain text the agent wrote. The worst failure it can have is a notification that does not
fire.

`unescape` is deliberately not part of it. Slack escapes `<`, `>` and `&` wherever a member typed
them, and that escaping is what keeps another principal's words from closing the prompt element
they are quoted inside — the ambient digest carries bystanders' messages and relies on it. So
entity rendering, which is safe for anyone's words, is separate from unescaping, which is only
safe for the words of the member whose own turn this is.

`as_markdown` is what a reader outside Slack draws the same message with. Slack renders its own
emphasis markup, so the member who wrote `*ship it*` saw bold and typed no markdown; a portal
bubble states their words as markdown, and this is the rewrite that makes the two agree. Slack
draws an emoji from the shortcode the wire carries too, so `:smile:` is a face there and a run of
colons everywhere else, and the same rewrite states the character the member saw."""

import itertools
import re
from collections.abc import Iterable, Mapping

import emoji

SLACK_ENTITY = re.compile(
    r"<(?:(?P<kind>[@#!])(?P<id>[^<>|]*)|(?P<url>[a-z][a-z0-9+.\-]*:[^<>|]*))"
    r"(?:\|(?P<label>[^<>]*))?>"
)
BROADCASTS = frozenset({"here", "channel", "everyone"})
SLACK_ESCAPES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))

MENTION_MARKUP_MAX = 8
"""How many mentions one reply may map. The transcript the agent reads is full of `@Name` strings it
never chose — ingest renders every inbound mention that way, and the ambient digest does it for
bystanders — so a reply that recaps a thread carries them back. A cap is what keeps a quoted crowd
from being paged; past it the names stay as written."""
MENTION_NAME_WORDS = 4
MENTION_NAME_CHARS = 80
MENTION_NAME_TRAILING = "\"'`.,;:!?*_~)]}>"
MENTION_NOT_AFTER = "@._-+/:%&=?#"
"""What an `@` must not follow to be a mention: a character that makes it part of an address, a
handle or a query string instead. An email is the case this is for — `alex@example.test` is not a
mention of `@example`."""
MENTION_SKIP = re.compile(
    r"```(?s:.*?)(?:```|\Z)"  # a fenced block, closed or running to the end
    r"|`[^`\n]*`"  # an inline code span
    r"|\]\([^)\n]*\)"  # a Markdown link's target
    r"|<[^\s<>]*>"  # an angle-bracketed link, and any wire form already spelled out
    r"|[a-zA-Z][\w+.\-]*://\S*"  # a bare URL
)
"""The spans an outbound mention is never written into. Slack applies no other formatting inside
backticks, so a mention mapped in a code span prints the raw wire text in the channel — the bug
this work is about, in the other direction — and an `@` inside a URL is part of the address."""


MRKDWN_BOLD = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]*[^\s*])\*(?![\w*])")
MRKDWN_STRIKE = re.compile(r"(?<![\w~])~(?=\S)([^~\n]*[^\s~])~(?![\w~])")
MRKDWN_EMOJI = re.compile(r":([a-z0-9_+'\-]+):(?::skin-tone-(?P<tone>[2-6]):)?")
SKIN_TONES = ("\U0001f3fb", "\U0001f3fc", "\U0001f3fd", "\U0001f3fe", "\U0001f3ff")
"""The five modifiers Slack spells `:skin-tone-2:` through `:skin-tone-6:`, in that order."""


def as_markdown(text: str) -> str:
    """A member's own Slack message as the markdown a reader outside Slack renders.

    Slack's emphasis is markup the member never typed: one asterisk is bold there and one tilde is
    strikethrough, which markdown reads as italic and as plain text. Both are doubled, so a bubble
    drawn from a Slack message states the emphasis the member saw when they sent it. Everything
    else Slack spells the way markdown does — a quote, a bullet, an inline code span, a fence — and
    crosses untouched, and no emphasis is written inside a span `MENTION_SKIP` names, where Slack
    renders none either.

    A shortcode is the other markup the member never typed: Slack drew `:smile:` as the face, so
    the bubble carries the character. A name no table knows — a workspace's own custom emoji — and
    a name inside a span `MENTION_SKIP` names stay the colons a reader sees in Slack."""
    written: list[str] = []
    read = 0
    for skipped in MENTION_SKIP.finditer(text):
        written.extend((_rendered(text[read : skipped.start()]), skipped.group(0)))
        read = skipped.end()
    written.append(_rendered(text[read:]))
    return "".join(written)


def _rendered(text: str) -> str:
    return MRKDWN_EMOJI.sub(
        _emoji_character, MRKDWN_STRIKE.sub(r"~~\1~~", MRKDWN_BOLD.sub(r"**\1**", text))
    )


def _emoji_character(match: re.Match[str]) -> str:
    name = match.group(1)
    for spelled in (name, name.replace("-", "_")):
        character = emoji.emojize(f":{spelled}:", language="alias")
        if character != f":{spelled}:":
            return _toned(character, match.group("tone"))
    return match.group(0)


def _toned(character: str, tone: str | None) -> str:
    if tone is None:
        return character
    # A modifier tones the face it follows, so a joined sequence takes it after the first code
    # point, in place of the variation selector standing there.
    rest = character[1:].removeprefix("\ufe0f")
    return f"{character[0]}{SKIN_TONES[int(tone) - 2]}{rest}"


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


def mention_key(name: str) -> str:
    """The key a name is mapped under: case-folded, with every run of whitespace collapsed onto one
    space. A cached name is already collapsed onto one line, so `@alex graveley` has to find the
    member Slack calls `Alex  Graveley`."""
    return " ".join(name.split()).casefold()


def mention_index(named: Iterable[tuple[str, str]]) -> dict[str, str]:
    """`named` — id and name pairs — turned into the map an outbound mention resolves through: the
    key `mention_key` reads, the id Slack notifies. One id carries every name its vouching caller
    knows it by, since the workspace's own name for a member and Slack's are both names the agent
    reads that member under.

    A name two ids share resolves to neither. A display name is free text its owner sets, so a
    colleague's name is a name anyone can take, and picking the first id pages a person by cache age
    with nothing to show for it; the plain text is the better answer. A name spelling a broadcast
    word is dropped for the same reason, and unconditionally: inbound, `@here` is a fact about what
    a member wrote, but outbound it is an act, and no member's own name may commit it."""
    claimed: dict[str, set[str]] = {}
    for id_, name in named:
        key = mention_key(name)
        if key and key not in BROADCASTS:
            claimed.setdefault(key, set()).add(id_)
    return {key: next(iter(ids)) for key, ids in claimed.items() if len(ids) == 1}


def mention_markup(text: str, ids: Mapping[str, str], limit: int = MENTION_MARKUP_MAX) -> str:
    """`text` with each `@name` it spells for an id in `ids` replaced by the mention Slack notifies
    on, and every other character left as the agent wrote it.

    `ids` is a `mention_index` — its keys are the only names that can be mapped, so the caller's
    allowlist is also the parser: a display name carries spaces, which no word pattern can bound, so
    at each `@` the longest name the map holds wins. A name nobody in the map carries stays plain
    text, as does anything inside `MENTION_SKIP`, an `@` that continues an address, and every
    mention past `limit`."""
    if not ids:
        return text
    skipped = [(match.start(), match.end()) for match in MENTION_SKIP.finditer(text)]
    mapped: list[str] = []
    written = 0
    count = 0
    for match in re.finditer("@", text):
        start = match.start()
        if start < written or count == limit:
            continue
        if any(span_start <= start < span_end for span_start, span_end in skipped):
            continue
        if start and (text[start - 1].isalnum() or text[start - 1] in MENTION_NOT_AFTER):
            continue
        named = _mention_at(text, start + 1, ids)
        if named is None:
            continue
        end, id_ = named
        mapped.extend((text[written:start], f"<@{id_}>"))
        written = end
        count += 1
    mapped.append(text[written:])
    return "".join(mapped)


def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None:
    line = text[start : start + MENTION_NAME_CHARS].split("\n", 1)[0]
    if not line or line[0].isspace():
        return None
    words = itertools.islice(re.finditer(r"\S+", line), MENTION_NAME_WORDS)
    for end in reversed([word.end() for word in words]):
        candidate = line[:end].rstrip(MENTION_NAME_TRAILING)
        id_ = ids.get(mention_key(candidate))
        if id_ is not None:
            return start + len(candidate), id_
    return None


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
