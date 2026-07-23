"""Window-triggered transcript compaction: a deterministic compression pipeline, not one call.

When the loaded history crosses the model's token window, the head is compressed into a validated
structured summary and the recent tail is kept verbatim. The pipeline is deterministic around a
single external model call: group into API rounds, render the head (images become markers,
verbatim-repeated runs fold to one copy plus a count marker), summarize into a typed
`CompactionSummary` (retrying with fewer rounds if the summarize request itself overflows), harvest
the durable `.tool-output` references the head offloaded, reconstruct the window, and persist. The
whole pre-compaction window (`before`), the window that replaces it
(`after`), and the typed summary persist above the live transcript at
`conversations/<cid>/compactions/<n>/{before,after,summary}.json.lz4`, so a pre-compaction fact
survives verbatim and an eval reader gets the structured object, not just rendered text."""

import json
import re
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import lz4.frame
from dbos import DBOS
from pydantic import ValidationError

from ufo.blob import BlobNotFound, BlobStore
from ufo.ext.loader import HookChain
from ufo.ext.manifest import PostCompact, PreCompact
from ufo.loop.prompts.render import COMPACTION_SYSTEM_PROMPT
from ufo.models.interface import (
    ImageBlock,
    Message,
    ModelClient,
    ModelRequest,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.sandbox.session import TOOL_OUTPUT_DIRNAME
from ufo.schema.records import Agent, Turn, Usage
from ufo.transcript import (
    CompactionRecord,
    CompactionSummary,
    CompactionWindow,
    compaction_key,
    decode_compaction,
)

CHARS_PER_TOKEN = 4
IMAGE_TOKEN_ESTIMATE = 1_600
DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000
AUTOCOMPACT_BUFFER_TOKENS = 30_000
COMPACTION_KEEP_MESSAGES = 8
COMPACTION_SUMMARY_MAX_TOKENS = 8_192
MAX_PTL_RETRIES = 3
PTL_DROP_DENOMINATOR = 5
MAX_REFERENCE_PATHS = 5
COMPACTED_CONTEXT_PREFIX = "Compacted context:\n"
COMPACTION_FORMAT_RESTATEMENT = (
    "\n\n---\nEnd of transcript head. Respond now with the SINGLE JSON object described in "
    "your instructions — no prose, no markdown fences, nothing else."
)
IMAGE_MARKER = "[image]"
REPEATED_RUN_MIN_OCCURRENCES = 10
REPEATED_RUN_UNIT_MAX_WORDS = 32
REPEATED_RUN_WORD_MAX_CHARS = 80
REPEATED_RUN_RE = re.compile(
    rf"(?:^|(?<=\s))"
    rf"(\S{{1,{REPEATED_RUN_WORD_MAX_CHARS}}}"
    rf"(?:\s\S{{1,{REPEATED_RUN_WORD_MAX_CHARS}}}){{0,{REPEATED_RUN_UNIT_MAX_WORDS - 1}}}?)"
    rf"(?:\s\1){{{REPEATED_RUN_MIN_OCCURRENCES - 1},}}"
)
REPEATED_RUN_MARKER = "[repeated {count} times]"
TOOL_OUTPUT_PATH_RE = re.compile(rf"/\S*{re.escape(TOOL_OUTPUT_DIRNAME)}/\S+\.txt")
CONTEXT_OVERFLOW_MARKERS = ("too long", "context length", "maximum context", "prompt is too large")

MODEL_CONTEXT_WINDOW: dict[str, int] = {
    "gpt-5.5": 272_000,
    "gpt-5.4": 272_000,
    "gpt-5.4-mini": 272_000,
    "gpt-5.4-nano": 272_000,
}


def is_context_overflow(error: Exception) -> bool:
    """A provider rejected the request because the context is too large — matched against the error
    class and message so the turn (or the summarize call itself) can recover by shrinking and
    retrying rather than fail. Lives here, the lower module, so the compaction pipeline's own
    prompt-too-long recovery and the engine's round recovery share one detector."""
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in CONTEXT_OVERFLOW_MARKERS)


@dataclass(frozen=True)
class Compaction:
    """The compaction workflow: decide on the window, run the compression pipeline over the head,
    persist before/after/summary, and hand back the window the turn should actually send to the
    model. When compaction actually occurs it fires the observe-only `pre_compact` (before the
    summarize call) and `post_compact` (after) turn hooks off the turn's chain — fired here, not in
    the engine, because only this flow knows past every compactibility guard that compaction will
    truly happen. The trigger derives from the model's real window less the summary's own output
    reserve and a buffer; `trigger_tokens` overrides the derivation for a test or an operator."""

    client: ModelClient
    model: str
    blob: BlobStore
    conversation_id: UUID
    summary_max_tokens: int = COMPACTION_SUMMARY_MAX_TOKENS
    trigger_tokens: int | None = None
    keep_messages: int = COMPACTION_KEEP_MESSAGES
    max_ptl_retries: int = MAX_PTL_RETRIES
    hooks: HookChain = field(default_factory=HookChain)
    turn: Turn | None = None
    agent: Agent | None = None
    audience_member_id: UUID | None = None
    speaker_member_id: UUID | None = None

    async def maybe_compact(
        self, messages: tuple[Message, ...], force: bool = False
    ) -> tuple[tuple[Message, ...], tuple[Usage, ...]]:
        """Compact when the window crosses the trigger, or unconditionally when `force` — the
        reactive path after a provider context-overflow. Either way the compactibility guards hold:
        a window at or under `keep_messages`, or one with no assistant boundary to split on, has
        nothing to summarize and is returned unchanged, so a forced call still no-ops safely."""
        if len(messages) <= self.keep_messages:
            return messages, ()
        window = MODEL_CONTEXT_WINDOW.get(self.model, DEFAULT_CONTEXT_WINDOW_TOKENS)
        trigger = (
            self.trigger_tokens
            if self.trigger_tokens is not None
            else window - self.summary_max_tokens - AUTOCOMPACT_BUFFER_TOKENS
        )
        if not force and self._tokens(messages) <= trigger:
            return messages, ()
        return await self._compact(messages, "force" if force else "auto")

    @DBOS.step()
    async def _compact(
        self, messages: tuple[Message, ...], reason: Literal["auto", "force"]
    ) -> tuple[tuple[Message, ...], tuple[Usage, ...]]:
        """The compaction itself, memoized as a DBOS step: the summarize model call, the blob
        writes, the index selection, and the pre/post_compact hook fires all run once and replay
        from the recorded output on a crash-recovery re-run, so recovery neither re-summarizes (no
        tokens re-spent) nor duplicates a compaction record at a fresh index, and the observe-only
        compaction hooks do not double-fire. `maybe_compact`'s guards stay outside the step —
        deterministic reads of the window — so a round that does not compact records no step and the
        step sequence lines up on replay."""
        selection = self._select(messages)
        if selection is None:
            return messages, ()
        head_rounds, tail = selection
        before_tokens = self._tokens(messages)
        await self.hooks.fire(
            "pre_compact",
            PreCompact(reason=reason, before_tokens=before_tokens),
            self.turn,
            self.agent,
            self.audience_member_id,
            self.speaker_member_id,
        )
        index = await self._next_index()
        summary, usages = await self._summarize(head_rounds)
        references = self._references(head_rounds, tail)
        rendered = self._render(summary, references)
        after = (Message(role="user", content=rendered), *tail)
        await self._persist(index, messages, after, summary)
        await self.hooks.fire(
            "post_compact",
            PostCompact(
                summary=rendered, before_tokens=before_tokens, after_tokens=self._tokens(after)
            ),
            self.turn,
            self.agent,
            self.audience_member_id,
            self.speaker_member_id,
        )
        return after, usages

    def _select(
        self, messages: tuple[Message, ...]
    ) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None:
        """Group the window into API rounds and split into the summarized head rounds and the kept
        tail. Keep the minimal trailing whole rounds that cover at least `keep_messages` messages,
        so recency is preserved and a tool_use is never severed from its tool_result. Returns None
        when there is no head left to summarize — the force no-op guard."""
        rounds = self._rounds(messages)
        kept: list[tuple[Message, ...]] = []
        kept_count = 0
        while rounds and kept_count < self.keep_messages:
            kept.insert(0, rounds[-1])
            kept_count += len(rounds[-1])
            rounds = rounds[:-1]
        if not rounds:
            return None
        return rounds, tuple(message for round_ in kept for message in round_)

    def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]:
        """Group messages into API rounds: a round begins at each assistant message and runs
        through the user (tool-result) messages that answer it, so a tool_use and its tool_result
        always fall in the same round. Leading user messages before the first assistant open one."""
        rounds: list[tuple[Message, ...]] = []
        current: list[Message] = []
        for message in messages:
            if message.role == "assistant" and current:
                rounds.append(tuple(current))
                current = [message]
            else:
                current.append(message)
        if current:
            rounds.append(tuple(current))
        return tuple(rounds)

    async def _summarize(
        self, head_rounds: tuple[tuple[Message, ...], ...]
    ) -> tuple[CompactionSummary, tuple[Usage, ...]]:
        """One metered model call turning the head rounds into a validated CompactionSummary. When
        the summarize request itself overflows the provider context, drop the oldest head rounds and
        retry, up to `max_ptl_retries` — every successful attempt's usage is returned to meter.
        Exhausting the retries (or an empty/unparseable summary) raises: a compaction that cannot
        shrink fails the turn loud rather than looping, the circuit breaker in this system's shape.
        The retry is legitimate — it is against a model call's proven external uncertainty."""
        rounds = head_rounds
        for attempt in range(self.max_ptl_retries + 1):
            try:
                summary, usage = await self._summarize_once(rounds)
                return summary, (usage,)
            except Exception as error:
                if (
                    not is_context_overflow(error)
                    or attempt == self.max_ptl_retries
                    or len(rounds) <= 1
                ):
                    raise
                rounds = self._drop_oldest(rounds)
        raise RuntimeError("compaction prompt-too-long recovery exhausted")

    async def _summarize_once(
        self, rounds: tuple[tuple[Message, ...], ...]
    ) -> tuple[CompactionSummary, Usage]:
        request = ModelRequest(
            model=self.model,
            system=COMPACTION_SYSTEM_PROMPT,
            messages=(Message(role="user", content=self._prepare(rounds)),),
            max_tokens=self.summary_max_tokens,
            reasoning="off",
        )
        parts: list[str] = []
        usage: Usage | None = None
        async for event in self.client.complete(request):
            match event:
                case TextDelta(text=chunk):
                    parts.append(chunk)
                case Usage():
                    usage = event
        if usage is None:
            raise RuntimeError("compaction produced no usage")
        return self._parse_summary("".join(parts)), usage

    def _prepare(self, rounds: tuple[tuple[Message, ...], ...]) -> str:
        """Render the head rounds to the summarizer's input: one `role: content` block per message,
        preserving block structure. Inline images are already `[image]` markers in `_text` — they
        carry no text but must not vanish silently, so the summarizer knows one was there. The
        rendered head then folds verbatim repetition, so the summarize request carries the
        information, not the bulk. The format restatement closes the input because the model's
        response shape follows the nearest instruction: at a full-scale head the system prompt sits
        hundreds of thousands of tokens back and the transcript's own momentum otherwise captures
        the reply into continuing the conversation (measured 4/6 continuations on a captured
        window, 0/12 with the restatement)."""
        return (
            self._fold_repeated_runs(
                "\n\n".join(
                    f"{message.role}: {self._text(message)}"
                    for round_ in rounds
                    for message in round_
                )
            )
            + COMPACTION_FORMAT_RESTATEMENT
        )

    def _fold_repeated_runs(self, text: str) -> str:
        """Fold a short word-sequence repeated verbatim `REPEATED_RUN_MIN_OCCURRENCES`+ times in a
        row into one copy plus a count marker. A head can be dominated by such runs — a wedged tool
        loop, pasted spam — and rendering them verbatim ships hundreds of thousands of characters
        that carry no more information than one copy and the count; at that scale Anthropic
        deterministically refuses the summarize request (stop_reason=refusal), so the fold is what
        lets a repetition-heavy window compact at all."""

        def fold(match: re.Match[str]) -> str:
            unit = match.group(1)
            count = (len(match.group(0)) + 1) // (len(unit) + 1)
            return f"{unit} {REPEATED_RUN_MARKER.format(count=count)}"

        return REPEATED_RUN_RE.sub(fold, text)

    def _drop_oldest(
        self, rounds: tuple[tuple[Message, ...], ...]
    ) -> tuple[tuple[Message, ...], ...]:
        """Drop the oldest fifth of the head rounds (at least one) — the bounded head-shrink a
        prompt-too-long retry applies before trying the summarize call again."""
        return rounds[max(1, len(rounds) // PTL_DROP_DENOMINATOR) :]

    def _parse_summary(self, text: str) -> CompactionSummary:
        """Validate the summarize call's output into the typed CompactionSummary. The model is asked
        for a single JSON object; the first balanced object is extracted (tolerating a stray fence,
        surrounding prose, and trailing characters) and validated. An empty, unparseable, or
        schema-invalid summary raises the module's own RuntimeError (never pydantic's) — a
        compaction that cannot produce a usable summary fails the turn loud rather than swapping in
        a degraded window, and every failure mode of this method shares one exception type an
        operator can filter on."""
        start = text.find("{")
        if start == -1:
            raise RuntimeError("compaction produced no JSON summary")
        end = -1
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end == -1:
            raise RuntimeError("compaction produced no JSON summary")
        try:
            summary = CompactionSummary.model_validate_json(text[start : end + 1])
        except ValidationError as error:
            raise RuntimeError(f"compaction produced an invalid summary: {error}") from error
        if not summary.intent.strip():
            raise RuntimeError("compaction produced an empty summary")
        return summary

    def _references(
        self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]
    ) -> tuple[str, ...]:
        """The durable workspace references to carry past the boundary: the `.tool-output/<id>.txt`
        files the engine offloaded large tool results to that fall in the summarized head. They are
        already durable, so the pipeline re-references the path — one line the model re-reads on
        demand — rather than re-inlining the content (workspace-is-truth). A path still visible in
        the kept tail is skipped, and the block is bounded to the most recent paths."""
        visible = {
            path for message in tail for path in TOOL_OUTPUT_PATH_RE.findall(self._text(message))
        }
        found: list[str] = []
        for round_ in head_rounds:
            for message in round_:
                for path in TOOL_OUTPUT_PATH_RE.findall(self._text(message)):
                    if path not in visible and path not in found:
                        found.append(path)
        return tuple(found[-MAX_REFERENCE_PATHS:])

    def _render(self, summary: CompactionSummary, references: tuple[str, ...]) -> str:
        """Render the validated CompactionSummary and the durable reference block into the one user
        message that replaces the head — deterministically, so the same summary always yields the
        same window. Only sections with content appear; the prefix marks the message as compacted
        context. This same text is what `post_compact` observes as the summary."""
        blocks: list[str] = []
        if summary.intent.strip():
            blocks.append(f"## Primary request and intent\n{summary.intent}")
        if summary.concepts:
            blocks.append("## Key technical concepts\n" + self._bullets(summary.concepts))
        if summary.files:
            blocks.append(
                "## Files and outputs\n"
                + "\n".join(f"- {ref.path} — {ref.why}" for ref in summary.files)
            )
        if summary.errors:
            blocks.append("## Errors and fixes\n" + self._bullets(summary.errors))
        if summary.decisions:
            blocks.append("## Decisions and problems solved\n" + self._bullets(summary.decisions))
        if summary.pending:
            blocks.append("## Pending tasks\n" + self._bullets(summary.pending))
        if summary.current_work.strip():
            blocks.append(f"## Current work\n{summary.current_work}")
        if summary.next_step.strip():
            blocks.append(f"## Next step\n{summary.next_step}")
        if summary.loaded_skills:
            blocks.append("## Loaded skills\n" + self._bullets(summary.loaded_skills))
        if references:
            blocks.append(
                "## Durable references (re-read with the file tools)\n" + self._bullets(references)
            )
        return COMPACTED_CONTEXT_PREFIX + "\n".join(blocks)

    def _bullets(self, items: tuple[str, ...]) -> str:
        return "\n".join(f"- {item}" for item in items)

    async def _persist(
        self,
        index: int,
        before: tuple[Message, ...],
        after: tuple[Message, ...],
        summary: CompactionSummary,
    ) -> None:
        await self._write(index, "before", before)
        await self._write(index, "after", after)
        await self.blob.put(
            self._key(index, "summary"), lz4.frame.compress(summary.model_dump_json().encode())
        )

    async def _next_index(self) -> int:
        index = 1
        while await self.blob.exists(self._key(index, "after")):
            index += 1
        return index

    async def read_record(self, index: int) -> CompactionRecord | None:
        try:
            before = await self.blob.get(self._key(index, "before"))
            after = await self.blob.get(self._key(index, "after"))
            summary = await self.blob.get(self._key(index, "summary"))
        except BlobNotFound:
            return None
        return decode_compaction(index, before, after, summary)

    async def _write(
        self, index: int, half: Literal["before", "after"], messages: tuple[Message, ...]
    ) -> None:
        encoded = json.dumps(
            CompactionWindow(messages=messages).model_dump(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        await self.blob.put(self._key(index, half), lz4.frame.compress(encoded))

    def _key(self, index: int, half: Literal["before", "after", "summary"]) -> str:
        return compaction_key(self.conversation_id, index, half)

    def _tokens(self, messages: tuple[Message, ...]) -> int:
        """Estimate the window's token cost: text length plus a flat cost per inline image. Images
        carry no text, so without IMAGE_TOKEN_ESTIMATE an image-heavy window counts as ~0 tokens and
        never trips the compaction trigger, ballooning the stored conversation."""
        return sum(
            (len(message.role) + len(self._text(message)) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
            + IMAGE_TOKEN_ESTIMATE * self._image_count(message)
            for message in messages
        )

    def _image_count(self, message: Message) -> int:
        if isinstance(message.content, str):
            return 0
        total = 0
        for block in message.content:
            match block:
                case ImageBlock():
                    total += 1
                case ToolResultBlock(content=tuple(parts)):
                    total += sum(1 for part in parts if isinstance(part, ImageBlock))
        return total

    def _text(self, message: Message) -> str:
        if isinstance(message.content, str):
            return message.content
        rendered: list[str] = []
        for block in message.content:
            match block:
                case TextBlock(text=text):
                    rendered.append(text)
                case ImageBlock():
                    rendered.append(IMAGE_MARKER)
                case ToolResultBlock(content=str(content)):
                    rendered.append(content)
                case ToolResultBlock(content=tuple(parts)):
                    rendered.extend(
                        part.text if isinstance(part, TextBlock) else IMAGE_MARKER for part in parts
                    )
                case ToolUseBlock(name=name, input=arguments):
                    rendered.append(f"{name}({json.dumps(arguments, sort_keys=True)})")
        return "\n".join(rendered)
