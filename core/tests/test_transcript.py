from pathlib import Path
from uuid import uuid4

import lz4.frame
import pytest
from pydantic import ValidationError

from selfhost.blob import FilesystemBlobStore
from selfhost.loop.transcript import Conversation, Transcript
from selfhost.models.interface import Message


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
    await transcript.write(
        Conversation(seq=1, messages=(Message(role="user", content="hi"),))
    )
    raw = await blob.get(f"conversations/{conversation_id}/messages.json.lz4")
    decoded = lz4.frame.decompress(raw).decode()
    assert decoded == '{"messages":[{"content":"hi","role":"user"}],"seq":1}'
