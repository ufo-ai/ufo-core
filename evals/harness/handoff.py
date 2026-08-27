"""What a delegated child actually handed back.

A subagent's turn ends with a `finish` call whose payload is the only thing its parent reads. The
engine returns the moment that call validates, so the finishing round's own text never reaches the
transcript — what stays durable is the last message the child wrote before it, and the payload
itself. That pair is exactly where the duplication lives: a child that writes a complete prose
report, is then told to finish, and regenerates substantially the same report as its payload has
paid for the report twice and handed over one copy.

The same waste moves rather than disappears when a contract forbids the final message: a child can
write the report into a file, read it back, and summarize that into the payload. So a handoff is
also read through every document the child wrote — the name, the bytes, the re-reads, the errors,
and how much of it reappears in the payload. Whether a document was legitimately asked for is the
suite's question, not this module's: a case knows its own deliverables, so every document is
reported and the policy applied where the case is known. Reporting one document would let an
asked-for deliverable hide a report routed through a second file.

Overlap is counted over word shingles, which is what makes a regenerated report legible as one —
ordinary working narration ("running the tests now") shares almost nothing with a result, while a
rewritten report shares most of itself."""

from __future__ import annotations

import json
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from evals.harness.harness import JsonObject
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock

WRITE_TOOLS = ("write", "edit")
READ_TOOL = "read"
SHINGLE_WORDS = 8
WORD_PATTERN = re.compile(r"[a-z0-9]+")


class HandoffDocument(BaseModel):
    """One file the child wrote: the bytes it wrote, how often it read the file back, how many calls
    touching it errored, and the share of it that reappears in the payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    chars: int = Field(ge=0)
    reads: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    duplication: float = Field(default=0.0, ge=0.0, le=1.0)


class SubagentHandoff(BaseModel):
    """One delegated conversation's closing shape. `closing_chars` is the prose the child left
    standing as its last durable message; `result_chars` is the payload that reached the parent;
    `result_json_object` says the complete payload parses as one JSON object; `duplication` is the
    share of the closing's word shingles that reappear in the payload; and `documents` is every
    file it wrote, largest first. The prose is counted, never stored: a transcript carries private
    handoffs every other archive path redacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    closing_chars: int = Field(ge=0)
    result_chars: int = Field(ge=0)
    result_json_object: bool = False
    duplication: float = Field(ge=0.0, le=1.0)
    documents: tuple[HandoffDocument, ...] = ()


def shingle_overlap(before: str, after: str) -> float:
    """The share of `before`'s word shingles that also appear in `after`. Zero when either side has
    less than one shingle's worth of words, so a one-line narration is never reported as duplicated
    on the strength of a couple of shared words."""
    first = _shingles(before)
    if not first:
        return 0.0
    second = _shingles(after)
    if not second:
        return 0.0
    return len(first & second) / len(first)


def _shingles(text: str) -> frozenset[tuple[str, ...]]:
    words = WORD_PATTERN.findall(text.casefold())
    if len(words) < SHINGLE_WORDS:
        return frozenset()
    return frozenset(
        tuple(words[index : index + SHINGLE_WORDS])
        for index in range(len(words) - SHINGLE_WORDS + 1)
    )


def handoff_record(
    conversation_id: UUID,
    messages: tuple[Message, ...],
    result: str,
    *,
    terminal_texts: tuple[str, ...] = (),
) -> SubagentHandoff:
    """One child conversation's handoff: its whole transcript against the payload its last terminal
    turn carried, so a conversation a `message_spawn` follow-up extended is counted once and
    whole rather than attributed to its final turn."""
    closing = _last_assistant_text(messages, terminal_texts)
    return SubagentHandoff(
        conversation_id=conversation_id,
        closing_chars=len(closing),
        result_chars=len(result),
        result_json_object=_is_json_object(result),
        duplication=shingle_overlap(closing, result),
        documents=_documents(messages, result),
    )


def _is_json_object(result: str) -> bool:
    try:
        return isinstance(json.loads(result), dict)
    except json.JSONDecodeError:
        return False


def _documents(messages: tuple[Message, ...], result: str) -> tuple[HandoffDocument, ...]:
    """Every file the child wrote, largest first, with how often it read the file back, how many
    calls touching it errored, and how much of it the payload restates. `edit` counts the text it
    inserted, since an edit that grows a report by a section paid for that section."""
    bodies: dict[str, list[str]] = {}
    reads: dict[str, int] = {}
    errors: dict[str, int] = {}
    failed: dict[str, str] = {}
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            match block:
                case ToolUseBlock(name=name, input=args) if name in WRITE_TOOLS:
                    path = str(args.get("file_path", ""))
                    if not path:
                        continue
                    failed[block.id] = path
                    bodies.setdefault(path, []).append(_written_text(args))
                case ToolUseBlock(name=name, input=args) if name == READ_TOOL:
                    path = str(args.get("file_path", ""))
                    if path:
                        failed[block.id] = path
                        reads[path] = reads.get(path, 0) + 1
                case ToolResultBlock(tool_use_id=call_id, is_error=True):
                    path = failed.get(call_id, "")
                    if path:
                        errors[path] = errors.get(path, 0) + 1
                case _:
                    continue
    return tuple(
        sorted(
            (
                HandoffDocument(
                    path=path,
                    chars=sum(len(part) for part in parts),
                    reads=reads.get(path, 0),
                    errors=errors.get(path, 0),
                    duplication=shingle_overlap("\n".join(parts), result),
                )
                for path, parts in bodies.items()
            ),
            key=lambda document: document.chars,
            reverse=True,
        )
    )


def _written_text(args: JsonObject) -> str:
    """What a write or edit put into the file: the content, or every inserted string of an edit."""
    match args.get("content"):
        case str() as content:
            return content
        case _:
            pass
    inserted: list[str] = []
    match args.get("edits"):
        case list() as edits:
            for edit in edits:
                if isinstance(edit, dict):
                    match edit.get("new_string"):
                        case str() as text:
                            inserted.append(text)
                        case _:
                            continue
        case _:
            pass
    return "\n".join(inserted)


def _last_assistant_text(messages: tuple[Message, ...], terminal_texts: tuple[str, ...]) -> str:
    """The text of the last assistant message that carried any — the child's final standing prose.
    Text blocks within that one message join, so a message split across blocks is measured whole.
    Every terminal turn persists its own payload as an assistant message, so each payload is
    skipped once: a child a `message_spawn` follow-up extended, whose last turn ends on a lone
    `finish` call, would otherwise stand on the payload of the turn before it."""
    omit = [text.strip() for text in terminal_texts if text.strip()]
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        if isinstance(message.content, str):
            text = message.content.strip()
        else:
            text = "\n".join(
                block.text
                for block in message.content
                if isinstance(block, TextBlock) and block.text
            ).strip()
        if text in omit:
            omit.remove(text)
            continue
        if text:
            return text
    return ""
