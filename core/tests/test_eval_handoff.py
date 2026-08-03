"""Reading a delegated child's handoff: the prose it left standing before finishing, the payload it
returned, and whether the payload is a restatement of that prose. The measurement has to separate a
regenerated report from ordinary working narration, because only the first is waste."""

from uuid import uuid4

from evals.harness.handoff import handoff_record, shingle_overlap
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock

CONVERSATION = uuid4()
REPORT = (
    "I fixed the journal mode by sealing the file at the end of apply_migrations, because alembic "
    "builds its own engine and the connect hook never runs against a migrated file. I added a test "
    "that holds a write transaction open on a byte copy and asks a second connection for the mode."
)
NARRATION = "Running the focused db tests now."


def assistant(*text: str, tool: str = "") -> Message:
    blocks: list[TextBlock | ToolUseBlock] = [TextBlock(text=item) for item in text]
    if tool:
        blocks.append(ToolUseBlock(id="call-1", name=tool, input={}))
    return Message(role="assistant", content=tuple(blocks))


def test_a_regenerated_report_is_read_as_duplication() -> None:
    messages = (assistant(NARRATION, tool="bash"), assistant(REPORT))
    record = handoff_record(CONVERSATION, messages, REPORT)
    assert record.closing_chars == len(REPORT)
    assert record.result_chars == len(REPORT)
    assert record.duplication == 1.0


def test_working_narration_is_not_read_as_duplication() -> None:
    record = handoff_record(CONVERSATION, (assistant(NARRATION, tool="bash"),), REPORT)
    assert record.closing_chars == len(NARRATION)
    assert record.duplication == 0.0
    assert record.result_chars == len(REPORT)


def test_a_child_that_finished_without_standing_prose_measures_zero() -> None:
    messages = (
        assistant(NARRATION, tool="bash"),
        Message(role="user", content=(ToolResultBlock(tool_use_id="call-1", content="ok"),)),
    )
    record = handoff_record(
        CONVERSATION, messages, "Sealed the journal mode. Patch: /workspace/fix.patch"
    )
    assert record.closing_chars == len(NARRATION)


def test_the_last_message_joins_its_own_text_blocks() -> None:
    record = handoff_record(CONVERSATION, (assistant("first half.", "second half."),), "")
    assert record.closing_chars == len("first half.\nsecond half.")
    assert record.duplication == 0.0


def test_overlap_ignores_a_fragment_too_short_to_shingle() -> None:
    assert shingle_overlap("done", "done") == 0.0
    assert shingle_overlap(REPORT, "") == 0.0
    assert shingle_overlap("", REPORT) == 0.0


def test_overlap_is_the_share_of_the_closing_that_reappears() -> None:
    half = REPORT[: REPORT.index(" ", len(REPORT) // 2)]
    assert shingle_overlap(half, REPORT) == 1.0
    assert 0.0 < shingle_overlap(REPORT, half) < 1.0


def wrote(path: str, content: str, call_id: str = "w1") -> Message:
    return Message(
        role="assistant",
        content=(
            ToolUseBlock(
                id=call_id,
                name="write",
                input={"file_path": path, "content": content, "user_description": "the report"},
            ),
        ),
    )


def read_back(path: str, call_id: str = "r1", is_error: bool = False) -> tuple[Message, Message]:
    return (
        Message(
            role="assistant",
            content=(ToolUseBlock(id=call_id, name="read", input={"file_path": path}),),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id=call_id, content="body", is_error=is_error),),
        ),
    )


def test_a_report_routed_through_a_file_is_still_measured() -> None:
    """The shape a terse-handoff contract produces instead of a final message: write the report to a
    file, read it back, summarize it into the payload. Nothing stands in a message, so the closing
    numbers read clean — the document numbers are what expose it."""
    messages = (
        wrote("/workspace/findings.md", REPORT),
        *read_back("/workspace/findings.md"),
        assistant("Wrote the findings.", tool="bash"),
    )
    record = handoff_record(CONVERSATION, messages, REPORT)
    assert record.closing_chars == len("Wrote the findings.")
    assert record.duplication == 0.0, "the old signal reads clean — this is why it was a false pass"
    assert record.document == "/workspace/findings.md"
    assert record.document_chars == len(REPORT)
    assert record.document_reads == 1
    assert record.document_duplication == 1.0


def test_an_edit_counts_the_text_it_inserted() -> None:
    messages = (
        Message(
            role="assistant",
            content=(
                ToolUseBlock(
                    id="e1",
                    name="edit",
                    input={
                        "file_path": "/workspace/notes.md",
                        "edits": [{"old_string": "x", "new_string": REPORT}],
                        "user_description": "extend",
                    },
                ),
            ),
        ),
        *read_back("/workspace/notes.md"),
    )
    record = handoff_record(CONVERSATION, messages, REPORT)
    assert record.document_chars == len(REPORT)
    assert record.document_reads == 1


def test_a_failed_write_is_counted() -> None:
    messages = (
        wrote("/workspace/big.md", REPORT),
        Message(
            role="user", content=(ToolResultBlock(tool_use_id="w1", content="EIO", is_error=True),)
        ),
    )
    record = handoff_record(CONVERSATION, messages, REPORT)
    assert record.document_errors == 1
    assert record.document_chars == len(REPORT)


def test_a_failed_read_back_is_counted_against_the_document() -> None:
    """`_documents` counts errors on any call touching the document, reads included: a read-back
    that fails is a round paid for and nothing returned."""
    messages = (
        wrote("/workspace/findings.md", REPORT),
        *read_back("/workspace/findings.md", is_error=True),
    )
    record = handoff_record(CONVERSATION, messages, REPORT)
    assert record.document_reads == 1
    assert record.document_errors == 1


def test_a_restatement_behind_a_long_preamble_is_seen() -> None:
    """A payload can restate the closing behind thousands of characters of unrelated prose, which
    is the regime the largest handoffs live in."""
    filler = "unrelated preamble words that share nothing with the report. " * 400
    record = handoff_record(CONVERSATION, (assistant(REPORT),), filler + REPORT)
    assert record.duplication == 1.0
