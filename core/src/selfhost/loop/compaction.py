"""Window-triggered transcript compaction: summarize the head, keep the tail verbatim.

When the loaded history crosses the token window, one model call summarizes the older messages and
the recent tail is kept unchanged. The whole pre-compaction window (`before`) and the summarized
window that replaces it (`after`) persist above the live transcript at
`conversations/<cid>/compactions/<n>/{before,after}.json.lz4`, so a pre-compaction fact survives
verbatim even after the live window is swapped for the summary."""

import json
from dataclasses import dataclass
from uuid import UUID

import lz4.frame
from pydantic import BaseModel

from selfhost.blob import BlobNotFound, BlobStore
from selfhost.models.interface import (
    Message,
    ModelClient,
    ModelRequest,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
)
from selfhost.schema.records import Usage

CHARS_PER_TOKEN = 4
COMPACTION_TRIGGER_TOKENS = 120_000
COMPACTION_KEEP_MESSAGES = 8
COMPACTION_SUMMARY_MAX_TOKENS = 8_192
COMPACTED_CONTEXT_PREFIX = "Compacted context:\n"
COMPACTION_SYSTEM_PROMPT = (
    "Compress the conversation for continuation. Keep user requirements, decisions, tool results, "
    "open tasks, and unresolved errors verbatim where they matter. Drop repetition and transient "
    "wording. Return only the compacted context."
)


class CompactionWindow(BaseModel):
    """The codec for a persisted message window — one for the before record, one for the after."""

    messages: tuple[Message, ...]


@dataclass(frozen=True)
class CompactionRecord:
    """One compaction's durable pair, read back from the blob store."""

    index: int
    before: tuple[Message, ...]
    after: tuple[Message, ...]


@dataclass(frozen=True)
class Compaction:
    """The compaction workflow: decide on the window, summarize the head, persist before/after,
    and hand back the window the turn should actually send to the model."""

    client: ModelClient
    model: str
    blob: BlobStore
    conversation_id: UUID
    trigger_tokens: int = COMPACTION_TRIGGER_TOKENS
    keep_messages: int = COMPACTION_KEEP_MESSAGES

    async def maybe_compact(
        self, messages: tuple[Message, ...]
    ) -> tuple[tuple[Message, ...], tuple[Usage, ...]]:
        if len(messages) <= self.keep_messages:
            return messages, ()
        if self._tokens(messages) <= self.trigger_tokens:
            return messages, ()
        return await self._compact(messages)

    async def _compact(
        self, messages: tuple[Message, ...]
    ) -> tuple[tuple[Message, ...], tuple[Usage, ...]]:
        split = len(messages) - self.keep_messages
        while split > 0 and messages[split].role != "assistant":
            split -= 1
        if split == 0:
            return messages, ()
        index = await self._next_index()
        head = messages[:split]
        tail = messages[split:]
        summary, usage = await self._summarize(head)
        after = (Message(role="user", content=f"{COMPACTED_CONTEXT_PREFIX}{summary}"), *tail)
        await self._write(index, "before", messages)
        await self._write(index, "after", after)
        return after, (usage,)

    async def _summarize(self, head: tuple[Message, ...]) -> tuple[str, Usage]:
        rendered = "\n\n".join(f"{message.role}: {self._text(message)}" for message in head)
        request = ModelRequest(
            model=self.model,
            system=COMPACTION_SYSTEM_PROMPT,
            messages=(Message(role="user", content=rendered),),
            max_tokens=COMPACTION_SUMMARY_MAX_TOKENS,
        )
        parts: list[str] = []
        usage: Usage | None = None
        async for event in self.client.complete(request):
            match event:
                case TextDelta(text=chunk):
                    parts.append(chunk)
                case Usage():
                    usage = event
        summary = "".join(parts)
        if not summary.strip():
            raise RuntimeError("compaction produced no summary")
        if usage is None:
            raise RuntimeError("compaction produced no usage")
        return summary, usage

    async def _next_index(self) -> int:
        index = 1
        while await self.blob.exists(self._key(index, "after")):
            index += 1
        return index

    async def read_record(self, index: int) -> CompactionRecord | None:
        try:
            before = await self.blob.get(self._key(index, "before"))
            after = await self.blob.get(self._key(index, "after"))
        except BlobNotFound:
            return None
        return CompactionRecord(
            index=index,
            before=CompactionWindow.model_validate_json(lz4.frame.decompress(before)).messages,
            after=CompactionWindow.model_validate_json(lz4.frame.decompress(after)).messages,
        )

    async def _write(self, index: int, half: str, messages: tuple[Message, ...]) -> None:
        encoded = json.dumps(
            CompactionWindow(messages=messages).model_dump(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        await self.blob.put(self._key(index, half), lz4.frame.compress(encoded))

    def _key(self, index: int, half: str) -> str:
        return f"conversations/{self.conversation_id}/compactions/{index}/{half}.json.lz4"

    def _tokens(self, messages: tuple[Message, ...]) -> int:
        return sum(
            (len(message.role) + len(self._text(message)) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
            for message in messages
        )

    def _text(self, message: Message) -> str:
        if isinstance(message.content, str):
            return message.content
        rendered: list[str] = []
        for block in message.content:
            match block:
                case TextBlock(text=text):
                    rendered.append(text)
                case ToolResultBlock(content=content):
                    rendered.append(content)
                case ToolUseBlock(name=name, input=arguments):
                    rendered.append(f"{name}({json.dumps(arguments, sort_keys=True)})")
        return "\n".join(rendered)
