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
)
from ufo.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    compaction_key,
    decode,
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
    assert decoded == '{"messages":[{"content":"hi","role":"user"}],"seq":1}'


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
