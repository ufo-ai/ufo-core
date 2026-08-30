from pathlib import Path
from uuid import UUID, uuid4

import lz4.frame
import pytest
from pydantic import ValidationError

from ufo.blob import FilesystemBlobStore
from ufo.loop.transcript import Transcript
from ufo.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.turns.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    compaction_key,
    decode,
    encode,
    read_compaction_after,
    read_compaction_records,
    transcript_key,
)


def _transcript(tmp_path: Path) -> Transcript:
    return Transcript(blob=FilesystemBlobStore(root=tmp_path), conversation_id=uuid4())


async def test_round_trip(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    conversation = Conversation(
        seq=1,
        messages=(Message(role="user", content="hi"), Message(role="assistant", content="yo")),
    )
    await transcript.write(conversation)
    assert await transcript.read() == conversation


async def test_round_trip_preserves_image_blocks(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    conversation = Conversation(
        seq=1,
        messages=(
            Message(
                role="user",
                content=(
                    TextBlock(text="see this"),
                    ImageBlock(source=ImageSource(media_type="image/png", data="AAAA")),
                ),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(
                        tool_use_id="t1",
                        content=(
                            TextBlock(text="chart.png"),
                            ImageBlock(source=ImageSource(media_type="image/jpeg", data="BBBB")),
                        ),
                    ),
                ),
            ),
        ),
    )
    await transcript.write(conversation)
    assert await transcript.read() == conversation


async def test_round_trip_preserves_system_and_injected(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    conversation = Conversation(
        seq=1,
        messages=(Message(role="user", content="hi"),),
        system="you are the agent",
        injected="<system-reminder>recalled fact</system-reminder>",
    )
    await transcript.write(conversation)
    assert await transcript.read() == conversation


def test_decode_accepts_a_blob_without_system_or_injected() -> None:
    conversation = decode(
        lz4.frame.compress(b'{"messages":[{"content":"hi","role":"user"}],"seq":1}')
    )
    assert conversation.system is None
    assert conversation.injected is None


def test_decode_accepts_a_tool_call_with_the_removed_description_arg() -> None:
    conversation = decode(
        lz4.frame.compress(
            b'{"seq":1,"messages":['
            b'{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"bash",'
            b'"input":{"command":"ls","user_description":"Listing files"}}]},'
            b'{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1",'
            b'"content":"README.md"}]}]}'
        )
    )
    assistant = conversation.messages[0].content
    result = conversation.messages[1].content
    assert isinstance(assistant, tuple)
    assert isinstance(result, tuple)
    call = assistant[0]
    outcome = result[0]
    assert isinstance(call, ToolUseBlock)
    assert call.input == {"command": "ls", "user_description": "Listing files"}
    assert isinstance(outcome, ToolResultBlock)
    assert (outcome.activity, outcome.activity_text) == (False, "")


async def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert await _transcript(tmp_path).read() is None


async def test_monotonic_guard_never_goes_backward(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    newer = Conversation(seq=2, messages=(Message(role="user", content="two"),))
    older = Conversation(seq=1, messages=(Message(role="user", content="one"),))
    await transcript.write(newer)
    await transcript.write(older)
    assert await transcript.read() == newer


async def test_replayed_same_seq_write_is_skipped(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    first = Conversation(seq=1, messages=(Message(role="user", content="first"),))
    replay = Conversation(seq=1, messages=(Message(role="user", content="replay"),))
    await transcript.write(first)
    await transcript.write(replay)
    assert await transcript.read() == first


def test_decode_rejects_boolean_seq() -> None:
    with pytest.raises(ValidationError):
        Conversation.model_validate_json('{"seq": true, "messages": []}')


def test_decode_rejects_zero_seq() -> None:
    with pytest.raises(ValidationError):
        Conversation.model_validate_json('{"seq": 0, "messages": []}')


def test_decode_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        Conversation.model_validate_json(
            '{"seq": 1, "messages": [{"role": "tool", "content": "x"}]}'
        )


async def test_stored_bytes_are_lz4_compact_json(tmp_path: Path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    conversation_id = uuid4()
    transcript = Transcript(blob=blob, conversation_id=conversation_id)
    await transcript.write(Conversation(seq=1, messages=(Message(role="user", content="hi"),)))
    raw = await blob.get(transcript_key(conversation_id))
    decoded = lz4.frame.decompress(raw).decode()
    assert decoded == (
        '{"seq":1,"messages":[{"role":"user","content":"hi"}],"system":null,"injected":null,"from_run":false}'
    )


def test_stored_tool_input_keeps_provider_key_order() -> None:
    conversation = Conversation(
        seq=1,
        messages=(
            Message(
                role="assistant",
                content=(ToolUseBlock(id="t1", name="probe", input={"zeta": 1, "alpha": 2}),),
            ),
        ),
    )
    raw = lz4.frame.decompress(encode(conversation)).decode()
    assert raw.index('"zeta"') < raw.index('"alpha"')
    block = decode(encode(conversation)).messages[0].content[0]
    assert isinstance(block, ToolUseBlock)
    assert tuple(block.input) == ("zeta", "alpha")


async def test_writer_bytes_decode_through_the_shared_contract(tmp_path: Path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    conversation_id = uuid4()
    conversation = Conversation(seq=1, messages=(Message(role="user", content="hi"),))
    await Transcript(blob=blob, conversation_id=conversation_id).write(conversation)
    assert decode(await blob.get(transcript_key(conversation_id))) == conversation


async def _write_compaction(blob: FilesystemBlobStore, conversation_id: UUID, index: int) -> None:
    for half, messages in (
        ("before", (Message(role="user", content=f"before {index}"),)),
        ("after", (Message(role="user", content=f"after {index}"),)),
    ):
        await blob.put(
            compaction_key(conversation_id, index, half),
            lz4.frame.compress(CompactionWindow(messages=messages).model_dump_json().encode()),
        )
    summary = CompactionSummary(intent=f"intent {index}", current_work="", next_step="")
    await blob.put(
        compaction_key(conversation_id, index, "summary"),
        lz4.frame.compress(summary.model_dump_json().encode()),
    )


async def test_read_compaction_records_walks_every_index(tmp_path: Path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    conversation_id = uuid4()
    await _write_compaction(blob, conversation_id, 1)
    await _write_compaction(blob, conversation_id, 2)
    records = await read_compaction_records(blob, conversation_id)
    assert tuple(record.index for record in records) == (1, 2)
    assert records[0].summary.intent == "intent 1"


async def test_read_compaction_records_is_empty_without_compactions(tmp_path: Path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    assert await read_compaction_records(blob, uuid4()) == ()


async def test_read_compaction_after_fetches_the_light_half_alone(tmp_path: Path) -> None:
    blob = FilesystemBlobStore(root=tmp_path)
    conversation_id = uuid4()
    await _write_compaction(blob, conversation_id, 1)
    assert await read_compaction_after(blob, conversation_id, 1) == (
        Message(role="user", content="after 1"),
    )
    assert await read_compaction_after(blob, conversation_id, 2) is None


async def test_a_shorter_run_record_never_clobbers_a_fuller_fallback_at_the_same_seq(
    tmp_path: Path,
) -> None:
    """Defect from review: a turn that persists after its commit (an intent turn, or the
    denied-founding path) rebuilds its record from a read that self-excludes the fallback's own
    same-seq write, so it comes back holding only the founding and the answer. A redelivery lands
    the full-history fallback first; the run's shorter record must not stand over it, or every
    message before this turn is deleted and the next turn opens with no history."""
    blob = FilesystemBlobStore(root=tmp_path)
    conversation_id = uuid4()
    transcript = Transcript(blob=blob, conversation_id=conversation_id)

    # The repair fallback lands first at seq 3, carrying the whole conversation before this turn.
    fallback = Conversation(
        seq=3,
        messages=(
            Message(role="user", content="turn one"),
            Message(role="assistant", content="answer one"),
            Message(role="user", content="turn two"),
            Message(role="assistant", content="answer two"),
            Message(role="user", content="turn three founding"),
        ),
        from_run=False,
    )
    await transcript.write(fallback)

    # The run's own record, rebuilt from a self-excluding read, holds only founding + answer.
    truncated_run = Conversation(
        seq=3,
        messages=(
            Message(role="user", content="turn three founding"),
            Message(role="assistant", content="answer three"),
        ),
        from_run=True,
    )
    await transcript.write(truncated_run)

    stored = await transcript.read()
    assert stored is not None
    assert len(stored.messages) == 5
    assert stored.messages[0].content == "turn one"


async def test_a_fuller_run_record_still_supersedes_the_thin_fallback(tmp_path: Path) -> None:
    """The guard preserves the interrupt fix: a run whose record carries at least what the fallback
    held still replaces it when the fallback landed first."""
    blob = FilesystemBlobStore(root=tmp_path)
    conversation_id = uuid4()
    transcript = Transcript(blob=blob, conversation_id=conversation_id)
    await transcript.write(
        Conversation(
            seq=2,
            messages=(Message(role="user", content="founding"),),
            from_run=False,
        )
    )
    await transcript.write(
        Conversation(
            seq=2,
            messages=(
                Message(role="user", content="founding"),
                Message(role="assistant", content="a tool call"),
                Message(role="user", content="a tool result"),
            ),
            from_run=True,
        )
    )
    stored = await transcript.read()
    assert stored is not None and stored.from_run and len(stored.messages) == 3
