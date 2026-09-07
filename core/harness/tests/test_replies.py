"""What a round's span tags mean: which spans a member is owed, what the window keeps, what a
closing answer carries as a file, and what a live stream is allowed to publish while a tag is
still arriving."""

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
CARRIED = f'{ANSWER}\n\n<artifact path="{REPORT_PATH}" text="Open detailed report"/>\n'


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


def test_reply_markup_leaves_an_artifact_tag_in_the_window() -> None:
    replies, window = marked_replies(f"{_span(str(MESSAGE), 'hi')}{CARRIED}")
    assert [reply.text for reply in replies] == ["hi"]
    assert window == f"\nhi\n{CARRIED}"


def test_a_closing_answer_yields_its_artifact_and_the_words_without_it() -> None:
    artifacts, delivered = marked_artifacts(CARRIED)
    assert artifacts == (
        MarkedArtifact("nightly-runner-queue.md", REPORT_PATH, "Open detailed report"),
    )
    assert delivered == ANSWER + "\n"


def test_the_link_text_is_one_bounded_line_or_none() -> None:
    def text(tag: str) -> str | None:
        return marked_artifacts(tag)[0][0].text

    assert text('<artifact path="/workspace/a.md"/>') is None
    assert text('<artifact path="/workspace/a.md" text=""/>') is None
    assert text('<artifact path="/workspace/a.md" text=" Open  the\nplan "/>') == "Open the plan"
    assert text(f'<artifact path="/workspace/a.md" text="{"x" * 81}"/>') is None
    assert text(f'<artifact path="/workspace/a.md" text="{"x" * 80}"/>') == "x" * 80


def test_artifacts_keep_their_order_and_an_empty_one_carries_nothing() -> None:
    text = (
        '<artifact path="/workspace/a.md"/>'
        '<artifact path="  "/>'
        '<artifact path="/workspace/out/c.csv"/> done'
    )
    artifacts, delivered = marked_artifacts(text)
    assert [(artifact.name, artifact.path) for artifact in artifacts] == [
        ("a.md", "/workspace/a.md"),
        ("c.csv", "/workspace/out/c.csv"),
    ]
    assert delivered == " done"


def test_an_artifact_name_is_its_leaf_and_defaults_to_markdown() -> None:
    text = (
        '<artifact path="/workspace/reports/q3.md"/>'
        '<artifact path="/workspace/plan"/>'
        '<artifact path=".."/>'
    )
    artifacts, _ = marked_artifacts(text)
    assert [artifact.name for artifact in artifacts] == ["q3.md", "plan.md", "artifact.md"]


def test_a_tag_that_does_not_close_itself_is_stripped_and_carries_nothing() -> None:
    artifacts, delivered = marked_artifacts('Done. <artifact path="/workspace/x.md"> half a report')
    assert artifacts == ()
    assert delivered == "Done.  half a report"


def test_a_stream_publishes_narration_at_every_chunk_size_and_never_the_span() -> None:
    text = f"before {_span(str(MESSAGE), 'the reply')} after"
    for size in (1, 3, 7, 512):
        redaction = SpanRedaction()
        published = "".join(
            redaction.feed(text[start : start + size]) for start in range(0, len(text), size)
        )
        assert published == "before  after"


def test_a_stream_withholds_an_artifact_span_at_every_chunk_size() -> None:
    for size in (1, 5, 7, 512):
        redaction = SpanRedaction()
        published = "".join(
            redaction.feed(CARRIED[start : start + size]) for start in range(0, len(CARRIED), size)
        )
        assert published == ANSWER + "\n\n\n"
        assert "artifact" not in published
        assert "Nightly" not in published


def test_a_stream_redacts_a_reply_and_an_artifact_in_one_round() -> None:
    text = f'Working. {_span(str(MESSAGE), "spoken")} then <artifact path="/workspace/x.md"/> end'
    redaction = SpanRedaction()
    published = "".join(redaction.feed(char) for char in text)
    assert published == "Working.  then  end"


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
    assert SpanRedaction().feed("done <artifact") == "done "
    assert SpanRedaction().feed("text</artifact>more") == "textmore"


def test_the_earlier_body_span_is_stripped_and_carries_nothing() -> None:
    earlier = f'{ANSWER}\n\n<artifact name="x.md">\n# Nightly\n\nMove the jobs.\n</artifact>\n'
    assert marked_artifacts(earlier) == ((), ANSWER + "\n")
    both = f'{CARRIED}<artifact name="x.md">body</artifact>\n'
    assert marked_artifacts(both) == (
        (MarkedArtifact("nightly-runner-queue.md", REPORT_PATH, "Open detailed report"),),
        ANSWER + "\n",
    )
    for size in (1, 5, 512):
        redaction = SpanRedaction()
        published = "".join(
            redaction.feed(earlier[start : start + size]) for start in range(0, len(earlier), size)
        )
        assert published == ANSWER + "\n\n\n"
        assert "Nightly" not in published
