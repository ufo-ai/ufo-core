"""What a round's reply tags mean: which spans a member is owed, what the window keeps, and what a
live stream is allowed to publish while the tag is still arriving."""

from uuid import UUID

from ufo.harness.replies import MarkedReply, ReplyRedaction, marked_replies

MESSAGE = UUID("a532d68a-6724-5bd3-b34f-3ec90a57db80")
OTHER = UUID("b6f0c2de-0d7a-5a2f-9f5e-8f6b2ad1c4e7")


def _span(message: str, body: str) -> str:
    return f'<reply-to message="{message}">\n{body}\n</reply-to>'


def test_spans_are_ordered_replies_and_their_words_stay_in_the_window() -> None:
    text = (
        f"Checked the queue.\n\n{_span(str(MESSAGE), 'Filed it as #1801.')}"
        f"working{_span(str(OTHER), 'second')}\n\nNow the tests."
    )
    replies, window = marked_replies(text)
    assert replies == (
        MarkedReply(message_ref=MESSAGE, text="Filed it as #1801."),
        MarkedReply(message_ref=OTHER, text="second"),
    )
    assert "reply-to" not in window
    assert window == (
        "Checked the queue.\n\n\nFiled it as #1801.\nworking\nsecond\n\n\nNow the tests."
    )


def test_malformed_or_empty_spans_never_create_a_reply() -> None:
    cases = (
        ("Reading the diff now.", ((), "Reading the diff now.")),
        (f'<reply-to message="{MESSAGE}">half a thought', ((), "half a thought")),
        (_span(str(MESSAGE), "   "), ((), "\n   \n")),
    )
    for text, expected in cases:
        assert marked_replies(text) == expected


def test_nested_markup_is_stripped_and_invalid_refs_remain_deliverable() -> None:
    text = f'<reply-to message="{MESSAGE}">outer <reply-to message="{OTHER}">inner</reply-to>'
    replies, window = marked_replies(f"{text}</reply-to> tail</reply-to>")
    assert replies == (MarkedReply(message_ref=MESSAGE, text="outer inner"),)
    assert window == "outer inner tail"

    replies, _window = marked_replies(_span("the first one", "here it is"))
    assert replies == (MarkedReply(message_ref=None, text="here it is"),)


def test_a_stream_publishes_narration_at_every_chunk_size_and_never_the_span() -> None:
    text = f"before {_span(str(MESSAGE), 'the reply')} after"
    for size in (1, 3, 7, 512):
        redaction = ReplyRedaction()
        published = "".join(
            redaction.feed(text[start : start + size]) for start in range(0, len(text), size)
        )
        assert published == "before  after"


def test_stream_redaction_handles_partial_unclosed_and_stray_markup() -> None:
    partial = ReplyRedaction()
    assert partial.feed("compare <re") == "compare "
    assert partial.feed("play> to <reply") == "<replay> to "
    assert partial.feed(" it") == "<reply it"

    unclosed = ReplyRedaction()
    assert unclosed.feed(f'here <reply-to message="{MESSAGE}">unfinished') == "here "
    assert unclosed.feed(" still going") == ""

    assert ReplyRedaction().feed("done <reply-to") == "done "
    assert ReplyRedaction().feed("text</reply-to>more") == "textmore"
