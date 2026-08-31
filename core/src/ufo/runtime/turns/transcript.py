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

from ufo.blob import BlobNotFound, BlobStore
from ufo.harness.models.interface import Message


class Conversation(BaseModel):
    """The durable transcript: the message window at `seq`, plus — on a completed turn's write —
    the exact `system` string that turn's model calls ran with and the `injected` context its
    user_prompt_submit hooks contributed after the submitted message in the model context, so the
    debug surface can show the full model input, not just the messages.

    `from_run` marks the write as the one the turn's own execution made, carrying what the turn
    did. A write without it knows only the member's messages — the repair fallback for a turn whose
    run may never have written, and every record stored before this field existed."""

    model_config = ConfigDict(strict=True)
    seq: int = Field(ge=1)
    messages: tuple[Message, ...]
    system: str | None = None
    injected: str | None = None
    from_run: bool = False


class TranscriptDecodeError(ValueError):
    """A stored transcript blob could not be decoded to a Conversation — corrupt bytes or a schema
    that no longer matches the record."""


def transcript_key(conversation_id: UUID) -> str:
    return f"conversations/{conversation_id}/messages.json.lz4"


def encode(conversation: Conversation) -> bytes:
    body = json.dumps(conversation.model_dump(), separators=(",", ":")).encode()
    return lz4.frame.compress(body)


def decode(body: bytes) -> Conversation:
    try:
        return Conversation.model_validate_json(lz4.frame.decompress(body))
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error


class FileRef(BaseModel):
    """A file the head touched — a workspace source or a runtime `tool-output/<id>.txt`
    offload — and one line on why it mattered, so the model can re-read it after the boundary."""

    path: str
    why: str


AnchorKind = Literal["tool_output", "skill", "request", "error"]


class Anchor(BaseModel):
    """One fact the summarized head held that the window replacing it has to still carry, harvested
    from the head by pattern and from the pipeline's own trackers — never from the model's summary,
    so the grade cannot be authored by the thing it grades. `literal` is the exact substring a
    carrying window must contain, so survival is containment and not a judgement."""

    kind: AnchorKind
    literal: str


class CompactionVerification(BaseModel):
    """What the deterministic check between the summary and the window swap found, persisted with
    the summary and emitted through `o11y` so a boundary's loss is readable in production and not
    only in the eval suite. `missing` names the anchors that reached neither the rendered summary
    nor the retained tail — a compaction carrying entries here was accepted lossy after its retry;
    `dropped_paths` names the model-authored file paths that appeared nowhere in the pre-compaction
    window and were cut from the render instead of read as fact on the next round. `tail_tokens` is
    what the verbatim tail cost on its own — the part of `after_tokens` no summary could have made
    smaller, and the reason an installed window may still sit over the trigger."""

    before_tokens: int
    after_tokens: int
    tail_tokens: int
    anchors: int
    missing: tuple[Anchor, ...] = ()
    dropped_paths: tuple[str, ...] = ()
    retried: bool = False


class CompactionSummary(BaseModel):
    """The structured compression of a summarized head. It crosses a boundary — it re-enters the
    model context (rendered by the compaction pipeline's reconstruction) and persists in the `after`
    record — so it is a validated BaseModel, not a freeform blob: every field is a section the
    reconstruction renders deterministically, so the same summary always yields the same window.
    Sections adapted from Claude Code's compaction prompt, trimmed for a headless multi-surface
    agent. The summarizing model authors every field but the last two: `loaded_skills` the pipeline
    fills from the turn's skill-load tracker, which knows what the head actually held, and
    `verification` it fills with the grade the swap passed, which is the one field no reader should
    take the model's word for."""

    intent: str
    current_work: str
    next_step: str
    concepts: tuple[str, ...] = ()
    files: tuple[FileRef, ...] = ()
    errors: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    loaded_skills: tuple[str, ...] = ()
    verification: CompactionVerification | None = None


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


async def read_compaction_after(
    blob: BlobStore, conversation_id: UUID, index: int
) -> tuple[Message, ...] | None:
    """One compaction's `after` window alone — the summary message plus the kept tail. It is the
    small half of the record, so a reader checking whether a later window opens with it pays one
    light fetch instead of decoding the full pre-compaction history."""
    try:
        body = await blob.get(compaction_key(conversation_id, index, "after"))
    except BlobNotFound:
        return None
    try:
        return CompactionWindow.model_validate_json(lz4.frame.decompress(body)).messages
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error


async def read_compaction_record(
    blob: BlobStore, conversation_id: UUID, index: int
) -> CompactionRecord | None:
    """One compaction's durable record, or None when that index holds no record — the per-index
    fetch every read role (the debug surface, the eval harness) shares with the write role."""
    try:
        before = await blob.get(compaction_key(conversation_id, index, "before"))
        after = await blob.get(compaction_key(conversation_id, index, "after"))
        summary = await blob.get(compaction_key(conversation_id, index, "summary"))
    except BlobNotFound:
        return None
    return decode_compaction(index, before, after, summary)


async def read_compaction_records(
    blob: BlobStore, conversation_id: UUID
) -> tuple[CompactionRecord, ...]:
    """Every compaction a conversation persisted, oldest first. Indices are written sequentially
    from one, so the first absent index ends the walk."""
    records: list[CompactionRecord] = []
    index = 1
    while (record := await read_compaction_record(blob, conversation_id, index)) is not None:
        records.append(record)
        index += 1
    return tuple(records)
