"""What a round's reply tags mean: which spans a member is owed, what the window keeps, and what a
live stream is allowed to publish while the tag is still arriving."""

from uuid import UUID

import pytest

from ufo.harness.replies import MarkedReply, ReplyRedaction, marked_replies

MESSAGE = UUID("a532d68a-6724-5bd3-b34f-3ec90a57db80")
OTHER = UUID("b6f0c2de-0d7a-5a2f-9f5e-8f6b2ad1c4e7")


def _span(message: str, body: str) -> str:
    return f'<reply-to message="{message}">\n{body}\n</reply-to>'


def test_a_tagged_span_is_a_reply_and_its_words_stay_in_the_window() -> None:
    text = f"Checked the queue.\n\n{_span(str(MESSAGE), 'Filed it as #1801.')}\n\nNow the tests."
    replies, window = marked_replies(text)
    assert replies == (MarkedReply(message_ref=MESSAGE, text="Filed it as #1801."),)
    assert "reply-to" not in window
    assert window == "Checked the queue.\n\n\nFiled it as #1801.\n\n\nNow the tests."


def test_spans_come_back_in_the_order_the_model_wrote_them() -> None:
    text = _span(str(MESSAGE), "first") + "working" + _span(str(OTHER), "second")
    replies, _window = marked_replies(text)
    assert [(reply.message_ref, reply.text) for reply in replies] == [
        (MESSAGE, "first"),
        (OTHER, "second"),
    ]


def test_untagged_text_is_working_notes_and_owes_no_reply() -> None:
    assert marked_replies("Reading the diff now.") == ((), "Reading the diff now.")


def test_an_unclosed_span_delivers_nothing_and_leaks_no_markup() -> None:
    replies, window = marked_replies(f'<reply-to message="{MESSAGE}">half a thought')
    assert replies == ()
    assert window == "half a thought"


def test_a_nested_or_stray_tag_delivers_one_span_without_markup() -> None:
    text = f'<reply-to message="{MESSAGE}">outer <reply-to message="{OTHER}">inner</reply-to>'
    replies, window = marked_replies(f"{text}</reply-to> tail</reply-to>")
    assert replies == (MarkedReply(message_ref=MESSAGE, text="outer inner"),)
    assert window == "outer inner tail"


def test_a_tag_naming_something_that_is_not_a_message_still_reaches_the_member() -> None:
    """The span is content the model addressed to a member; a ref we cannot read makes it
    unattributable, never undeliverable."""
    replies, _window = marked_replies(_span("the first one", "here it is"))
    assert replies == (MarkedReply(message_ref=None, text="here it is"),)


def test_an_empty_span_is_no_reply() -> None:
    assert marked_replies(_span(str(MESSAGE), "   ")) == ((), "\n   \n")


@pytest.mark.parametrize("size", [1, 3, 7, 512])
def test_a_stream_publishes_the_narration_and_never_the_span(size: int) -> None:
    """Chunk boundaries fall anywhere, so the redaction is asserted at every split of one text: the
    published stream is the same whatever the provider's framing."""
    text = f"before {_span(str(MESSAGE), 'the reply')} after"
    redaction = ReplyRedaction()
    published = "".join(
        redaction.feed(text[start : start + size]) for start in range(0, len(text), size)
    )
    assert published == "before  after"


def test_a_stream_releases_prose_that_only_looked_like_a_tag() -> None:
    redaction = ReplyRedaction()
    assert redaction.feed("compare <re") == "compare "
    assert redaction.feed("play> to <reply") == "<replay> to "
    assert redaction.feed(" it") == "<reply it"


def test_a_stream_publishes_nothing_of_an_unclosed_span() -> None:
    redaction = ReplyRedaction()
    assert redaction.feed(f'here <reply-to message="{MESSAGE}">unfinished') == "here "
    assert redaction.feed(" still going") == ""


def test_a_stream_withholds_a_tag_head_the_round_never_finishes() -> None:
    redaction = ReplyRedaction()
    assert redaction.feed("done <reply-to") == "done "


def test_a_stream_strips_a_stray_closer() -> None:
    redaction = ReplyRedaction()
    assert redaction.feed("text</reply-to>more") == "textmore"
