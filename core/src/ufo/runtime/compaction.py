"""Window-triggered transcript compaction: a deterministic compression pipeline, not one call.

When the loaded history crosses the model's token window, the head is compressed into a validated
structured summary and the recent tail is kept verbatim. The pipeline is deterministic around its
bounded external summarization attempts: group into API rounds, render the head (images become
markers, verbatim-repeated runs fold to one copy plus a count marker), summarize into a typed
`CompactionSummary` (retrying with fewer rounds if the summarize request itself overflows), harvest
the durable runtime references the head offloaded, reconstruct the window, verify the
reconstruction against the head it replaces, and persist. The whole pre-compaction window
(`before`), the window that replaces it (`after`), and the typed summary — carrying the
verification the swap passed — persist above the live transcript at
`conversations/<cid>/compactions/<n>/{before,after,summary}.json.lz4`, so a pre-compaction fact
survives verbatim and an eval reader gets the structured object, not just rendered text."""

import json
import re
from dataclasses import dataclass, field, replace
from itertools import chain
from typing import Literal
from uuid import UUID

import lz4.frame
from dbos import DBOS
from pydantic import ValidationError

from ufo.blob import BlobNotFound, WorkspaceBlobStore
from ufo.harness.context import CompactionHarness, ContextWindow
from ufo.harness.models.interface import (
    ImageBlock,
    Message,
    ModelClient,
    ModelRequest,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.harness.models.spec import ReasoningSupport
from ufo.harness.o11y import emit_metric, log, log_error, warn
from ufo.harness.sandbox.session import TOOL_OUTPUT_DIRNAME, UFO_HOME_ENV
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.ext.manifest import PostCompact, PreCompact
from ufo.runtime.prompts.render import COMPACTION_SYSTEM_PROMPT
from ufo.runtime.skills.runtime import LoadedSkills
from ufo.runtime.turns.transcript import (
    Anchor,
    AnchorKind,
    CompactionRecord,
    CompactionSummary,
    CompactionVerification,
    CompactionWindow,
    compaction_key,
    decode_compaction,
)
from ufo.schema.records import Agent, Turn, Usage

CHARS_PER_TOKEN = 2
IMAGE_TOKEN_ESTIMATE = 1_600
DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000
AUTOCOMPACT_BUFFER_TOKENS = 30_000
COMPACTION_KEEP_MESSAGES = 8
COMPACTION_SUMMARY_MAX_TOKENS = 20_000
MAX_PTL_RETRIES = 3
PTL_DROP_DENOMINATOR = 5
MAX_REFERENCE_PATHS = 5
COMPACTED_CONTEXT_PREFIX = "Compacted context:\n"
FILES_HEADING = "## Files and outputs"
REFERENCES_HEADING = "## Durable references (re-read with the file tools)"
ACTIVE_REQUESTS_HEADING = "## Active member requests"
MAX_ANCHORS_PER_KIND = 20
ERROR_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}(?:Error|Exception)\b")
MESSAGE_REF_RE = re.compile(r"message_ref: (\S+)")
EMPTY_SUMMARY = CompactionSummary(intent="", current_work="", next_step="")
"""The render with every model-authored section gone: what the replacement message costs whatever
the summary says, because the prefix, the durable references, and each active request are carried
verbatim. It is the floor the budget invariant holds a summary against."""
ANCHOR_RETRY_INSTRUCTION = (
    "\n\n---\nYour previous summary of this head dropped the facts below, and they are "
    "load-bearing. Carry each one verbatim in the field of the JSON object it belongs to.\n"
)
COMPACTION_FORMAT_RESTATEMENT = (
    "\n\n---\nEnd of transcript head. Respond now with the SINGLE JSON object described in "
    "your instructions — no prose, no markdown fences, nothing else."
)
IMAGE_MARKER = "[image]"
REDACTED_REASONING_MARKER = "[redacted reasoning]"
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
TOOL_OUTPUT_PATH_RE = re.compile(
    rf"(?:\${UFO_HOME_ENV})?/\S*{re.escape(TOOL_OUTPUT_DIRNAME)}/\S+\.txt"
)


def harvest_anchors(
    head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]
) -> tuple[Anchor, ...]:
    """The anchor set a replacement window is graded against: what the head demonstrably held and a
    later round cannot reconstruct from anywhere else — the runtime paths the engine
    offloaded large results to, the skill workflows the boundary drops, the refs of the member
    requests still open, and the error classes the head named. Each is harvested by pattern from the
    head's own text or from the pipeline's trackers, never from the summary, so the grade cannot be
    authored by the thing it grades. Bounded per kind to the most recent, because a miss rides a log
    line and a retry instruction, both of which are payloads.

    Lives here, the lower module, so the runtime gate and the `evals/compaction` bars read one
    anchor definition and cannot drift apart."""
    harvested: tuple[tuple[AnchorKind, list[str]], ...] = (
        ("tool_output", TOOL_OUTPUT_PATH_RE.findall(head_text)),
        ("error", ERROR_TOKEN_RE.findall(head_text)),
        ("skill", list(loaded_skills)),
        (
            "request",
            [ref for request in active_requests for ref in MESSAGE_REF_RE.findall(request)],
        ),
    )
    return tuple(
        Anchor(kind=kind, literal=literal)
        for kind, literals in harvested
        for literal in tuple(dict.fromkeys(literals))[-MAX_ANCHORS_PER_KIND:]
    )


def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]:
    """The anchors absent from the text the boundary carries forward — the rendered summary plus
    the tail kept verbatim. Containment, not similarity: an anchor is a literal, and a window
    carries it only by reproducing it."""
    return tuple(anchor for anchor in anchors if anchor.literal not in carried)


@dataclass(frozen=True)
class _Boundary:
    """What the pre-compaction window fixes about this boundary before any summary exists: the tail
    kept verbatim, the references harvested out of the head, the requests and skills the pipeline
    carries itself, the pre-compaction text a model-authored path is checked against, the anchors
    the replacement has to carry, and the token count it has to beat. Both summarize attempts are
    graded against one of these, so a retry is judged on identical terms."""

    index: int
    tail: tuple[Message, ...]
    references: tuple[str, ...]
    active_requests: tuple[str, ...]
    loaded_skills: tuple[str, ...]
    pre_text: str
    anchors: tuple[Anchor, ...]
    before_tokens: int


@dataclass(frozen=True)
class _Candidate:
    """One summarize attempt graded against the boundary: the summary as the pipeline would persist
    it, the message that would replace the head, and the verification that decides whether the swap
    happens, is retried, or fails the turn."""

    summary: CompactionSummary
    rendered: str
    after: tuple[Message, ...]
    verification: CompactionVerification


@dataclass(frozen=True, repr=False)
class _CompactionRequest:
    messages: tuple[Message, ...]
    reason: Literal["auto", "force"]
    active_requests: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"_CompactionRequest(messages={len(self.messages)}, reason={self.reason}, "
            f"active_requests={len(self.active_requests)})"
        )


class _InvalidSummary(RuntimeError):
    def __init__(self, error_class: str, message: str, usage: Usage) -> None:
        super().__init__(error_class, message, usage)
        self.error_class = error_class
        self.usage = usage

    def __str__(self) -> str:
        return str(self.args[1])


@dataclass
class _CompactionState:
    automatic_suppressed: bool = False


@dataclass(frozen=True, repr=False)
class Compaction:
    """The compaction workflow: decide on the window, run the compression pipeline over the head,
    persist before/after/summary, and hand back the window the turn should actually send to the
    model. When compaction actually occurs it fires the observe-only `pre_compact` after the model
    has produced a valid summary but before installing it, and `post_compact` after installation —
    fired here, not in the engine, because only this flow knows past every compactibility guard that
    compaction will truly happen. The trigger derives from the model's real window less the
    summary's own output reserve and a buffer; `trigger_tokens` overrides the derivation for a test
    or an operator.
    `loaded_skills` is the turn's skill-load tracker, shared with the tool context: the boundary
    drops the workflow bodies the head held, so this flow drains the tracker into the summary and
    leaves it empty — the one summary field the pipeline knows and the model does not."""

    client: ModelClient
    model: str
    blob: WorkspaceBlobStore
    conversation_id: UUID
    summary_max_tokens: int = COMPACTION_SUMMARY_MAX_TOKENS
    context_window: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    trigger_tokens: int | None = None
    keep_messages: int = COMPACTION_KEEP_MESSAGES
    max_ptl_retries: int = MAX_PTL_RETRIES
    hooks: HookChain = field(default_factory=HookChain)
    loaded_skills: LoadedSkills = field(default_factory=LoadedSkills)
    turn: Turn | None = None
    agent: Agent | None = None
    reasoning: ReasoningSupport = field(
        default_factory=lambda: ReasoningSupport(supported=True, tools_with_reasoning=True)
    )
    _state: _CompactionState = field(default_factory=_CompactionState, init=False, compare=False)
    window: ContextWindow[Message] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "window",
            ContextWindow(
                role=lambda message: message.role,
                text=self._text,
                opaque_chars=self._opaque_chars,
                image_count=self._image_count,
                context_tokens=self.context_window,
                summary_tokens=self.summary_max_tokens,
                buffer_tokens=AUTOCOMPACT_BUFFER_TOKENS,
                keep_messages=self.keep_messages,
                chars_per_token=CHARS_PER_TOKEN,
                image_tokens=IMAGE_TOKEN_ESTIMATE,
                trigger_tokens=self.trigger_tokens,
                head_drop_denominator=PTL_DROP_DENOMINATOR,
            ),
        )

    def __repr__(self) -> str:
        return f"Compaction(conversation_id={self.conversation_id}, model={self.model})"

    async def maybe_compact(
        self,
        messages: tuple[Message, ...],
        force: bool = False,
        active_requests: tuple[str, ...] = (),
    ) -> tuple[tuple[Message, ...], tuple[Usage, ...]]:
        """Compact when the window crosses the trigger, or unconditionally when `force` — the
        reactive path after a provider context-overflow. Either way the compactibility guards hold:
        a window at or under `keep_messages`, or one with no assistant boundary to split on, has
        nothing to summarize and is returned unchanged, so a forced call still no-ops safely. An
        unusable automatic summary suppresses further automatic attempts for this turn; a forced
        recovery still runs because the provider has proven the unchanged window cannot proceed."""
        if not self.window.should_compact(
            messages,
            force=force,
            automatic_suppressed=self._state.automatic_suppressed,
        ):
            return messages, ()
        compacted, usages = await self._compact(
            _CompactionRequest(
                messages=messages,
                reason="force" if force else "auto",
                active_requests=active_requests,
            )
        )
        if not force and usages and compacted == messages:
            self._state.automatic_suppressed = True
        return compacted, usages

    @DBOS.step()
    async def _compact(
        self, request: _CompactionRequest
    ) -> tuple[tuple[Message, ...], tuple[Usage, ...]]:
        """The compaction itself, memoized as a DBOS step: the summarize model call, the blob
        writes, the index selection, and the pre/post_compact hook fires all run once and replay
        from the recorded output on a crash-recovery re-run, so recovery neither re-summarizes (no
        tokens re-spent) nor duplicates a compaction record at a fresh index, and the observe-only
        compaction hooks do not double-fire. `maybe_compact`'s guards stay outside the step —
        deterministic reads of the window — so a round that does not compact records no step and the
        step sequence lines up on replay. The summary's `loaded_skills` is drained from the tracker
        here rather than asked of the model: the tracker knows which workflows the head actually
        held, and draining it is what tells the rest of the turn those bodies are gone.

        Nothing the model authored replaces the live window unverified. `_verify` grades the
        reconstruction against the boundary the head fixed; anchors the first summary dropped buy
        one re-summarize naming them. A valid retry replaces the first candidate; a failed retry
        leaves that verified candidate installable and records its remaining loss. The budget
        invariant is the one that fails loud — a boundary that did not shrink the window spent a
        summarize call to make the next round worse."""

        async def open_boundary(
            head: tuple[tuple[Message, ...], ...],
            tail: tuple[Message, ...],
            before_tokens: int,
        ) -> _Boundary:
            index = await self._next_index()
            await self.hooks.fire(
                "pre_compact",
                PreCompact(reason=request.reason, before_tokens=before_tokens),
                self.turn,
                self.agent,
                None,
            )
            drained = self.loaded_skills.drain()
            return _Boundary(
                index=index,
                tail=tail,
                references=self._references(head, tail),
                active_requests=request.active_requests,
                loaded_skills=drained,
                pre_text=self._window_text(request.messages),
                anchors=harvest_anchors(
                    self._window_text(tuple(chain.from_iterable(head))),
                    drained,
                    request.active_requests,
                ),
                before_tokens=before_tokens,
            )

        def retry_failed(candidate: _Candidate, error: Exception) -> _Candidate:
            verification = candidate.verification.model_copy(update={"retried": True})
            warn("compaction.anchor_retry_failed", error_class=type(error).__name__)
            return replace(
                candidate,
                summary=candidate.summary.model_copy(update={"verification": verification}),
                verification=verification,
            )

        async def checkpoint(candidate: _Candidate, boundary: _Boundary) -> None:
            await self._persist(
                boundary.index, request.messages, candidate.after, candidate.summary
            )
            self._record_verification(boundary.index, request.reason, candidate.verification)
            await self.hooks.fire(
                "post_compact",
                PostCompact(
                    summary=candidate.rendered,
                    before_tokens=boundary.before_tokens,
                    after_tokens=candidate.verification.after_tokens,
                ),
                self.turn,
                self.agent,
                None,
            )

        try:
            result = await CompactionHarness[
                Message, Anchor, Usage, CompactionSummary, _Candidate, _Boundary
            ](
                window=self.window,
                summarize=self._summarize_once,
                open_boundary=open_boundary,
                verify=self._verify,
                missing=lambda candidate: candidate.verification.missing,
                retry_failed=retry_failed,
                failed_usage=lambda error: (
                    (error.usage,) if isinstance(error, _InvalidSummary) else ()
                ),
                after=lambda candidate: candidate.after,
                fixed_replacement_tokens=lambda boundary: self.window.tokens(
                    (
                        Message(
                            role="user",
                            content=self._render(
                                EMPTY_SUMMARY,
                                boundary.references,
                                boundary.active_requests,
                            ),
                        ),
                    )
                ),
                checkpoint=checkpoint,
                max_context_retries=self.max_ptl_retries,
            ).run(request.messages)
        except _InvalidSummary as error:
            if request.reason == "force":
                raise
            log_error(
                "compaction.summary_invalid",
                conversation_id=str(self.conversation_id),
                reason=request.reason,
                error_class=error.error_class,
            )
            return request.messages, (error.usage,)
        return result.messages, result.usages

    async def _summarize_once(
        self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]
    ) -> tuple[CompactionSummary, Usage]:
        request = ModelRequest(
            model=self.model,
            system=COMPACTION_SYSTEM_PROMPT,
            messages=(Message(role="user", content=self._prepare(rounds, missed)),),
            max_tokens=self.summary_max_tokens,
            conversation_cache_ttl="5m",
            session_id=str(self.conversation_id),
            reasoning=self.reasoning.internal_effort(),
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
        try:
            return self._parse_summary("".join(parts)), usage
        except RuntimeError as error:
            cause = error.__cause__
            raise _InvalidSummary(
                type(cause if cause is not None else error).__name__, str(error), usage
            ) from error

    def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str:
        """Render the head rounds to the summarizer's input: one `role: content` block per message,
        preserving block structure. Inline images are already `[image]` markers in `_text` — they
        carry no text but must not vanish silently, so the summarizer knows one was there. The
        rendered head then folds verbatim repetition, so the summarize request carries the
        information, not the bulk. Anchors a previous attempt dropped follow the head, so the model
        reads them as a correction to what it just wrote rather than as part of the transcript. The
        format restatement closes the input because the model's response shape follows the nearest
        instruction: at a full-scale head the system prompt sits hundreds of thousands of tokens
        back and the transcript's own momentum otherwise captures the reply into continuing the
        conversation (measured 4/6 continuations on a captured window, 0/12 with the
        restatement)."""
        head = self._fold_repeated_runs(
            "\n\n".join(
                f"{message.role}: {self._text(message)}" for round_ in rounds for message in round_
            )
        )
        correction = (
            ANCHOR_RETRY_INSTRUCTION + self._bullets(tuple(anchor.literal for anchor in missed))
            if missed
            else ""
        )
        return head + correction + COMPACTION_FORMAT_RESTATEMENT

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

    def _parse_summary(self, text: str) -> CompactionSummary:
        """Validate the summarize call's output into the typed CompactionSummary. The model is asked
        for a single JSON object; the first balanced object is extracted (tolerating a stray fence,
        surrounding prose, and trailing characters) and validated. An empty, unparseable, or
        schema-invalid summary raises the module's own RuntimeError (never pydantic's). The caller
        decides whether the accepted source window is still a valid fallback, and every failure
        mode of this method shares one exception type an operator can filter on."""
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
        """The durable runtime references to carry past the boundary: the `tool-output/<id>.txt`
        files the engine offloaded large tool results to that fall in the summarized head. They are
        already durable, so the pipeline re-references the path — one line the model re-reads on
        demand — rather than re-inlining the content. A path still visible in
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

    def _render(
        self,
        summary: CompactionSummary,
        references: tuple[str, ...],
        active_requests: tuple[str, ...],
    ) -> str:
        """Render the validated CompactionSummary and the durable reference block into the one user
        message that replaces the head — deterministically, so the same summary always yields the
        same window. Each active member request survives verbatim outside the model-authored
        summary, preserving its authority ref and exact request together. Only sections with
        content appear; the prefix marks the message as compacted context. This same text is what
        `post_compact` observes as the summary."""
        blocks: list[str] = []
        if summary.intent.strip():
            blocks.append(f"## Primary request and intent\n{summary.intent}")
        if summary.concepts:
            blocks.append("## Key technical concepts\n" + self._bullets(summary.concepts))
        if summary.files:
            blocks.append(
                FILES_HEADING
                + "\n"
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
            blocks.append(REFERENCES_HEADING + "\n" + self._bullets(references))
        if active_requests:
            blocks.append(ACTIVE_REQUESTS_HEADING + "\n" + "\n\n".join(active_requests))
        return COMPACTED_CONTEXT_PREFIX + "\n".join(blocks)

    def _bullets(self, items: tuple[str, ...]) -> str:
        return "\n".join(f"- {item}" for item in items)

    def _window_text(self, messages: tuple[Message, ...]) -> str:
        return "\n".join(self._text(message) for message in messages)

    def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate:
        """Grade one summarize attempt against the boundary, before any of it can replace the live
        window: cut the model-authored paths the pre-compaction window never mentioned, render, and
        count the anchors that reached neither the render nor the retained tail.

        Reference integrity runs on the summary's own `files`, because a path the model invented
        renders under a heading the next round reads as fact. It does not run on the harvested
        references, which this pipeline pulled out of the head's own text by pattern and which
        therefore satisfy the check by construction. A path is checked against the pre-compaction
        text and nothing else: the paths name a sandbox filesystem this process holds no view of, so
        whether one resolves on disk is not a question answerable here.

        The check reads the whole pre-compaction window, not the head the summarizer saw — the
        prompt-too-long ladder can drop rounds from the input, and a path from a dropped round is a
        real path the model may still name off the retry instruction."""
        kept = tuple(ref for ref in summary.files if ref.path in boundary.pre_text)
        dropped = tuple(ref.path for ref in summary.files if ref.path not in boundary.pre_text)
        checked = summary.model_copy(
            update={"loaded_skills": boundary.loaded_skills, "files": kept}
        )
        rendered = self._render(checked, boundary.references, boundary.active_requests)
        after = (Message(role="user", content=rendered), *boundary.tail)
        verification = CompactionVerification(
            before_tokens=boundary.before_tokens,
            after_tokens=self.window.tokens(after),
            tail_tokens=self.window.tokens(boundary.tail),
            anchors=len(boundary.anchors),
            missing=missing_anchors(boundary.anchors, self._window_text(after)),
            dropped_paths=dropped,
            retried=retried,
        )
        return _Candidate(
            summary=checked.model_copy(update={"verification": verification}),
            rendered=rendered,
            after=after,
            verification=verification,
        )

    def _record_verification(
        self, index: int, reason: Literal["auto", "force"], verification: CompactionVerification
    ) -> None:
        """Every compaction reports its own grade, so the fleet's loss rate is a query rather than
        an eval-suite inference: one log record naming the anchors that died and the paths that were
        cut, and one count split by whether anything died and whether the retry was spent."""
        log(
            "compaction.verified",
            conversation_id=str(self.conversation_id),
            index=index,
            reason=reason,
            before_tokens=verification.before_tokens,
            after_tokens=verification.after_tokens,
            tail_tokens=verification.tail_tokens,
            anchors=verification.anchors,
            missing=[f"{anchor.kind}:{anchor.literal}" for anchor in verification.missing],
            dropped_paths=list(verification.dropped_paths),
            retried=verification.retried,
        )
        emit_metric(
            "compaction_verified_total",
            outcome="lossy" if verification.missing else "clean",
            retried="true" if verification.retried else "false",
        )

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

    def _opaque_chars(self, message: Message) -> int:
        """What a reasoning block costs the window beyond its rendered text: a thinking block's
        signature, a redacted block's encrypted body, and a reasoning item's encrypted body are
        re-sent on every request of the turn but are not prose a summarizer can use, so they are
        counted here and rendered nowhere. Counted because an uncounted block is the direction
        `_tokens` cannot afford — under `display: omitted`, or a reasoning item whose request asked
        for no summary, the opaque body is the whole block, so leaving it out estimates every
        reasoning round of the deploy's default model at zero."""
        if isinstance(message.content, str):
            return 0
        total = 0
        for block in message.content:
            match block:
                case ThinkingBlock(signature=signature):
                    total += len(signature)
                case RedactedThinkingBlock(data=data):
                    total += len(data)
                case ReasoningItemBlock(encrypted_content=encrypted):
                    total += len(encrypted)
        return total

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
                case ThinkingBlock(thinking=thinking):
                    rendered.append(thinking)
                case RedactedThinkingBlock():
                    rendered.append(REDACTED_REASONING_MARKER)
                case ReasoningItemBlock(summary=summary):
                    rendered.extend(summary)
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
