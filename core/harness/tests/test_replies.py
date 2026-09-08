"""Reply spans and Markdown links that carry workspace files, batch and streamed."""

from uuid import UUID

from ufo.harness.replies import (
    MarkedArtifact,
    MarkedReply,
    SpanRedaction,
    marked_artifacts,
    marked_replies,
)

MESSAGE = UUID("a532d68a-6724-5bd3-b34f-3ec90a57db80")
OTHER = UUID("b6f0c2de-0d7a-5a2f-9f5e-8f6b2ad1c4e7")
REPORT_PATH = "/workspace/nightly-runner-queue.md"
ANSWER = "Move the event-driven jobs onto a queue and keep cron for the clock."
CARRIED = f"{ANSWER}\n\n[nightly-runner-queue.md]({REPORT_PATH})\n"


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


def test_reply_markup_leaves_a_file_link_in_the_window() -> None:
    replies, window = marked_replies(f"{_span(str(MESSAGE), 'hi')}{CARRIED}")
    assert [reply.text for reply in replies] == ["hi"]
    assert window == f"\nhi\n{CARRIED}"


def test_a_closing_answer_yields_its_file_and_the_link_label() -> None:
    artifacts, delivered = marked_artifacts(CARRIED)
    assert artifacts == (MarkedArtifact(name="nightly-runner-queue.md", path=REPORT_PATH),)
    assert delivered == ANSWER + "\n\nnightly-runner-queue.md\n"


def test_file_links_keep_their_order_and_names() -> None:
    text = "[first](/workspace/a.md) [second](out/c.csv) [third](plan)"
    artifacts, delivered = marked_artifacts(text)
    assert [(artifact.name, artifact.path) for artifact in artifacts] == [
        ("a.md", "/workspace/a.md"),
        ("c.csv", "out/c.csv"),
        ("plan.md", "plan"),
    ]
    assert delivered == "first second third"


def test_non_file_links_images_code_and_call_syntax_stay_prose() -> None:
    prose = (
        "[docs](https://example.test/plan.md) [mail](mailto:a@b.test) [top](#top) "
        "[cdn](//cdn.test/x.md) ![chart](chart.png) handlers[name](event) "
        "`[read](plan.md)` and [x] (y)\n"
    )
    assert marked_artifacts(prose) == ((), prose)


def test_a_stream_publishes_narration_at_every_chunk_size_and_never_the_span() -> None:
    text = f"before {_span(str(MESSAGE), 'the reply')} after"
    for size in (1, 3, 7, 512):
        redaction = SpanRedaction()
        published = "".join(
            redaction.feed(text[start : start + size]) for start in range(0, len(text), size)
        )
        assert published == "before  after"


def test_a_stream_turns_a_file_link_into_its_label_at_every_chunk_size() -> None:
    for size in range(1, len(CARRIED) + 1):
        redaction = SpanRedaction()
        published = "".join(
            redaction.feed(CARRIED[start : start + size]) for start in range(0, len(CARRIED), size)
        )
        published += redaction.finish()
        assert published == ANSWER + "\n\nnightly-runner-queue.md\n"


def test_a_stream_redacts_a_reply_and_projects_a_file_link_in_one_round() -> None:
    text = f"Working. {_span(str(MESSAGE), 'spoken')} then [details](/workspace/x.md) end"
    redaction = SpanRedaction()
    published = "".join(redaction.feed(char) for char in text)
    assert published + redaction.finish() == "Working.  then details end"


def test_stream_redaction_handles_partial_unclosed_and_stray_markup() -> None:
    partial = SpanRedaction()
    assert partial.feed("compare <re") == "compare "
    assert partial.feed("play> to <reply") == "<replay> to "
    assert partial.feed(" it") == "<reply it"

    unclosed = SpanRedaction()
    assert unclosed.feed(f'here <reply-to message="{MESSAGE}">unfinished') == "here "
    assert unclosed.feed(" still going") == ""

    assert SpanRedaction().feed("done <reply-to") == "done "
    assert SpanRedaction().feed("text</reply-to>more") == "textmore"
    assert SpanRedaction().feed("a <b> c <artifice> d") == "a <b> c <artifice> d"


def test_stream_images_call_syntax_and_trailing_citations_stay_prose() -> None:
    prose = "![chart](chart.png) handlers[name](event) [2]"
    for size in range(1, len(prose) + 1):
        redaction = SpanRedaction()
        published = "".join(
            redaction.feed(prose[start : start + size]) for start in range(0, len(prose), size)
        )
        assert published + redaction.finish() == prose
