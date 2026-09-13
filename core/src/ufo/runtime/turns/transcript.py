"""The durable conversation blob contracts: the keys transcripts and context-boundary records land
under and the codecs that cross them. The write roles (`loop.transcript.Transcript`, and whichever
context-boundary strategy the deploy selected — the `context_rollover` extension's `ContextRollover`
or the `context_compact` extension's `Compaction`) and the read roles (the eval
`TrajectoryCorpus`, the debug surface) sit in packages that cannot import each other, so each
key + record + codec lives here once — a format change moves every role at the same time instead of
silently breaking one.

A rollover record names the lines of the sandbox history file that hold the window it closed, so
the fresh window and the file agree on where everything else is."""

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import lz4.frame
from pydantic import BaseModel, ConfigDict, Field

from ufo.blob import BlobNotFound, BlobStore
from ufo.harness.models.interface import Message
from ufo.schema.records import MEMBER_ADMISSION, TurnAdmissionSource


class ParkedRequester(BaseModel):
    """One active message whose admission, member identity, and content survive a turn park."""

    model_config = ConfigDict(strict=True)
    id: UUID
    member_id: UUID | None
    rendered: str
    admission_source: TurnAdmissionSource = MEMBER_ADMISSION


class ParkedTurn(BaseModel):
    """The arrivals and requester identities already represented in a parked message window."""

    model_config = ConfigDict(strict=True)
    absorbed: tuple[UUID, ...]
    requesters: tuple[ParkedRequester, ...]


class Conversation(BaseModel):
    """The durable transcript: the message window at `seq`, plus — on a completed turn's write —
    the exact `system` string that turn's model calls ran with and the `injected` context its
    user_prompt_submit hooks contributed after the submitted message in the model context, so the
    debug surface can show the full model input, not just the messages.

    `from_run` marks the write as the one the turn's own execution made, carrying what the turn
    did. A write without it knows only the member's messages — the repair fallback for a turn whose
    run may never have written, and every record stored before this field existed. `parked` names a
    resumable window, the arrivals already present in it, and their requester identities."""

    model_config = ConfigDict(strict=True)
    seq: int = Field(ge=1)
    messages: tuple[Message, ...]
    system: str | None = None
    injected: str | None = None
    from_run: bool = False
    parked: ParkedTurn | None = None


MEMBER_CONTEXT_OPENING = "<context>\n"
"""How a member inbound opens in the window: the engine renders a `<context>` tag — message ref,
admission time, sender — before every member message and never before its own prompts, so a
reader that wants the member's words and nothing else keys on this opening."""


class TranscriptDecodeError(ValueError):
    """A stored transcript blob could not be decoded to a Conversation — corrupt bytes or a schema
    that no longer matches the record."""


def transcript_key(conversation_id: UUID) -> str:
    return f"conversations/{conversation_id}/messages.json.lz4"


def encode(conversation: Conversation) -> bytes:
    body = json.dumps(conversation.model_dump(mode="json"), separators=(",", ":")).encode()
    return lz4.frame.compress(body)


def decode(body: bytes) -> Conversation:
    try:
        return Conversation.model_validate_json(lz4.frame.decompress(body))
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error


class FileRef(BaseModel):
    """A file the window touched — a workspace source or a runtime `tool-output/<id>.txt`
    offload — and one line on why it mattered, so the model can re-read it after the boundary."""

    path: str
    why: str


AnchorKind = Literal["tool_output", "skill", "request", "error"]


class Anchor(BaseModel):
    """One fact the window held that the window replacing it has to still carry, harvested from the
    window by pattern and from the pipeline's own trackers — never from anything the model wrote at
    the boundary, so the grade cannot be authored by the thing it grades. `literal` is the exact
    substring a carrying window must contain, so survival is containment and not a judgement."""

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
    """The structured compression of a summarized head — the recovery payload the compaction
    strategy installs at its boundary. It crosses a boundary — it re-enters the model context
    (rendered by the compaction pipeline's reconstruction) and persists in the `after` record — so
    it is a validated BaseModel, not a freeform blob: every field is a section the reconstruction
    renders deterministically, so the same summary always yields the same window. Sections adapted
    from Claude Code's compaction prompt, trimmed for a headless multi-surface agent. The
    summarizing model authors every field but the last two: `loaded_skills` the pipeline fills from
    the turn's skill-load tracker, which knows what the head actually held, and `verification` it
    fills with the grade the swap passed, which is the one field no reader should take the model's
    word for."""

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
    """Where the compaction boundary writes its records. The rollover index walk reads them too,
    rendered as a recovery record whose handoff is the summary, so a conversation that crossed the
    line under either boundary pages the same way and numbers on from where it was."""
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


class PendingResult(BaseModel):
    """A tool result the model has not read yet when the window rolls over: the call that produced
    it, its arguments, and a bounded head of the result text. `entry_id` is the line of the history
    file that holds the whole result, so the fresh model reads the rest there instead of the
    boundary carrying it."""

    entry_id: int
    call: str
    arguments: str
    text: str
    truncated: bool = False


class RolloverVerification(BaseModel):
    """What the deterministic check between the recovery record and the window swap found,
    persisted with the record and emitted through `o11y` so a boundary is readable in production
    and not only in the eval suite. `missing` names the anchors that reached neither the recovery
    record nor the window appended to the history file. `handoff_capped` and `checklist_capped` mark
    what the caps trimmed."""

    before_tokens: int
    after_tokens: int
    entries: int
    anchors: int
    missing: tuple[Anchor, ...] = ()
    handoff_chars: int = 0
    handoff_capped: bool = False
    checklist_chars: int = 0
    checklist_capped: bool = False


class RecoveryRecord(BaseModel):
    """The bounded state a fresh window opens with — never a summary. Nothing here is written by a
    model at the boundary: `user_inputs` are the member's own recent words, `pending_results` are
    the tool results the rolled-over window never showed the model, `checklist` is carried across
    verbatim, `checkpoint` is the last handoff the agent itself wrote and is labelled possibly
    stale, and `handoff` is the note an explicit `new_context` call persisted. `history_path` is the
    sandbox history file and `first_entry_id`..`last_entry_id` the lines of it that hold the window
    this boundary closed, so everything left out is readable by line rather than lost;
    `history_lost` marks a file that no longer reached back before this window. `window_digest`
    identifies the window, so a replayed boundary finds its own record. `objective` is a spawned
    turn's founding inbound, the task it exists for, read off the turn row."""

    objective: str | None = None
    user_inputs: tuple[str, ...] = ()
    pending_results: tuple[PendingResult, ...] = ()
    checklist: tuple[str, ...] = ()
    checkpoint: str | None = None
    handoff: str | None = None
    active_requests: tuple[str, ...] = ()
    loaded_skills: tuple[str, ...] = ()
    first_entry_id: int = 1
    last_entry_id: int = 0
    history_path: str = ""
    history_lost: bool = False
    window_digest: str = ""
    verification: RolloverVerification | None = None


class RolloverWindow(BaseModel):
    """The codec for a persisted message window — one for the before record, one for the after."""

    messages: tuple[Message, ...]


@dataclass(frozen=True)
class RolloverRecord:
    """One rollover's durable artifacts, read back from the blob store: the pre-rollover window,
    the fresh window that replaced it, and the typed recovery record it opens with."""

    index: int
    before: tuple[Message, ...]
    after: tuple[Message, ...]
    recovery: RecoveryRecord


RolloverHalf = Literal["before", "after", "recovery"]


def rollover_key(conversation_id: UUID, index: int, half: RolloverHalf) -> str:
    return f"conversations/{conversation_id}/rollovers/{index}/{half}.json.lz4"


def _summary_handoff(summary: bytes) -> str:
    fields = json.loads(lz4.frame.decompress(summary))
    lines = [
        f"{label}: {fields[key]}"
        for key, label in (
            ("intent", "intent"),
            ("current_work", "current work"),
            ("next_step", "next step"),
        )
        if fields.get(key)
    ]
    for key in ("concepts", "errors", "decisions", "pending", "loaded_skills"):
        if fields.get(key):
            lines.append(
                f"{key.replace('_', ' ')}: {' · '.join(str(value) for value in fields[key])}"
            )
    for file in fields.get("files") or ():
        if isinstance(file, dict):
            lines.append(f"file {file.get('path', '')} — {file.get('why', '')}")
    return "\n".join(lines)


async def count_summary_records(blob: BlobStore, conversation_id: UUID) -> int:
    """How many records the compaction boundary left, so the next boundary numbers on after
    them and the index walk stays one sequence."""
    index = 1
    while True:
        try:
            await blob.get(compaction_key(conversation_id, index, "summary"))
        except BlobNotFound:
            return index - 1
        index += 1


def decode_rollover(index: int, before: bytes, after: bytes, recovery: bytes) -> RolloverRecord:
    try:
        return RolloverRecord(
            index=index,
            before=RolloverWindow.model_validate_json(lz4.frame.decompress(before)).messages,
            after=RolloverWindow.model_validate_json(lz4.frame.decompress(after)).messages,
            recovery=RecoveryRecord.model_validate_json(lz4.frame.decompress(recovery)),
        )
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error


async def read_rollover_after(
    blob: BlobStore, conversation_id: UUID, index: int
) -> tuple[Message, ...] | None:
    """One rollover's `after` window alone — the fresh window the boundary installed. It is the
    small half of the record, so a reader checking whether a later window opens with it pays one
    light fetch instead of decoding the full pre-rollover history."""
    try:
        body = await blob.get(rollover_key(conversation_id, index, "after"))
    except BlobNotFound:
        try:
            body = await blob.get(compaction_key(conversation_id, index, "after"))
        except BlobNotFound:
            return None
    try:
        return RolloverWindow.model_validate_json(lz4.frame.decompress(body)).messages
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error


async def read_recovery_record(
    blob: BlobStore, conversation_id: UUID, index: int
) -> RecoveryRecord | None:
    """One rollover's recovery record alone — the light half that names the entry range the
    boundary closed and carries the checklist and handoff the next boundary inherits — or None
    when that index holds no record."""
    try:
        body = await blob.get(rollover_key(conversation_id, index, "recovery"))
    except BlobNotFound:
        return None
    try:
        return RecoveryRecord.model_validate_json(lz4.frame.decompress(body))
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error


async def read_rollover_record(
    blob: BlobStore, conversation_id: UUID, index: int
) -> RolloverRecord | None:
    """One rollover's durable record, or None when that index holds no record — the per-index fetch
    every read role (the debug surface, the eval harness) shares with the write role."""
    try:
        before = await blob.get(rollover_key(conversation_id, index, "before"))
        after = await blob.get(rollover_key(conversation_id, index, "after"))
        recovery = await blob.get(rollover_key(conversation_id, index, "recovery"))
    except BlobNotFound:
        try:
            before = await blob.get(compaction_key(conversation_id, index, "before"))
            after = await blob.get(compaction_key(conversation_id, index, "after"))
            summary = await blob.get(compaction_key(conversation_id, index, "summary"))
        except BlobNotFound:
            return None
        try:
            record = RecoveryRecord(
                handoff=_summary_handoff(summary),
                first_entry_id=1,
                last_entry_id=len(
                    RolloverWindow.model_validate_json(lz4.frame.decompress(before)).messages
                ),
            )
        except (ValueError, RuntimeError) as error:
            raise TranscriptDecodeError(str(error)) from error
        return decode_rollover(
            index, before, after, lz4.frame.compress(record.model_dump_json().encode())
        )
    return decode_rollover(index, before, after, recovery)


async def read_rollover_records(
    blob: BlobStore, conversation_id: UUID
) -> tuple[RolloverRecord, ...]:
    """Every rollover a conversation persisted, oldest first. Indices are written sequentially from
    one, so the first absent index ends the walk."""
    records: list[RolloverRecord] = []
    index = 1
    while (record := await read_rollover_record(blob, conversation_id, index)) is not None:
        records.append(record)
        index += 1
    return tuple(records)
