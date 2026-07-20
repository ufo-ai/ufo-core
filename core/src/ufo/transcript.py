"""The durable conversation blob contracts: the keys transcripts and compaction records land under
and the codecs that cross them. The write roles (`loop.transcript.Transcript`,
`loop.compaction.Compaction`) and the read roles (the eval `TrajectoryCorpus`, the debug surface)
sit in packages that cannot import each other, so each key + record + codec lives here once — a
format change moves every role at the same time instead of silently breaking one."""

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import lz4.frame
from pydantic import BaseModel, ConfigDict, Field

from ufo.models.interface import Message


class Conversation(BaseModel):
    model_config = ConfigDict(strict=True)
    seq: int = Field(ge=1)
    messages: tuple[Message, ...]


class TranscriptDecodeError(ValueError):
    """A stored transcript blob could not be decoded to a Conversation — corrupt bytes or a schema
    that no longer matches the record."""


def transcript_key(conversation_id: UUID) -> str:
    return f"conversations/{conversation_id}/messages.json.lz4"


def encode(conversation: Conversation) -> bytes:
    body = json.dumps(conversation.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    return lz4.frame.compress(body)


def decode(body: bytes) -> Conversation:
    try:
        return Conversation.model_validate_json(lz4.frame.decompress(body))
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error


class FileRef(BaseModel):
    """A workspace file the head touched — a read source or a `.tool-output/<id>.txt` offload — and
    one line on why it mattered, so the model can re-read it by path after the boundary."""

    path: str
    why: str


class CompactionSummary(BaseModel):
    """The structured compression of a summarized head. It crosses a boundary — it re-enters the
    model context (rendered by the compaction pipeline's reconstruction) and persists in the `after`
    record — so it is a validated BaseModel, not a freeform blob: every field is a section the
    reconstruction renders deterministically, so the same summary always yields the same window.
    Sections adapted from Claude Code's compaction prompt, trimmed for a headless multi-surface
    agent."""

    intent: str
    current_work: str
    next_step: str
    concepts: tuple[str, ...] = ()
    files: tuple[FileRef, ...] = ()
    errors: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    loaded_skills: tuple[str, ...] = ()


class CompactionWindow(BaseModel):
    """The codec for a persisted message window — one for the before record, one for the after."""

    messages: tuple[Message, ...]


@dataclass(frozen=True)
class CompactionRecord:
    """One compaction's durable artifacts, read back from the blob store: the pre-compaction window,
    the window that replaced it, and the typed summary the head was compressed into."""

    index: int
    before: tuple[Message, ...]
    after: tuple[Message, ...]
    summary: CompactionSummary


CompactionHalf = Literal["before", "after", "summary"]


def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str:
    return f"conversations/{conversation_id}/compactions/{index}/{half}.json.lz4"


def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord:
    try:
        return CompactionRecord(
            index=index,
            before=CompactionWindow.model_validate_json(lz4.frame.decompress(before)).messages,
            after=CompactionWindow.model_validate_json(lz4.frame.decompress(after)).messages,
            summary=CompactionSummary.model_validate_json(lz4.frame.decompress(summary)),
        )
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error
