"""Window rollover: reset the context at the line, never summarize it.

One of the two context-boundary strategies `runtime.context_boundary` selects between — the one a
deploy runs with `[context] strategy = "rollover"`, which is the default. The other summarizes the
head instead of resetting it (the `context_compact` extension). Exactly one is active.

A long turn crosses its model's window. The old answer was to summarize the head and swap the
summary in — one lossy model-authored paragraph standing in for everything that happened. This
module replaces that with a rollover: at the line the window is reset to a bounded recovery record
and the conversation continues in a fresh window, while the full history stays in an append-only
history file in the conversation's sandbox, where `search_history` and the agent's own shell reach
it.

Nothing at the boundary is authored by a model. The recovery record carries the member's own recent
words, the tool results the rolled-over window never showed the model, a checklist carried across
verbatim, the last handoff the agent itself wrote (labelled possibly stale), the history file and
the lines that hold the window it replaced, and an instruction to verify live state before any
stateful or external action.

The agent is told once, in its system prompt, how the window behaves — a stable note, not a
churning meter — and reads the numbers on demand with `get_context_remaining`. It can also reset
deliberately with `new_context`, which takes effect after the current tool batch commits so no
result is stranded mid-batch.

Every input to a boundary is durable. The reset request is read off the recorded window — the
`new_context` call and its result — and the carried checklist and the stale checkpoint off the last
persisted record, never off process memory: a crash-recovery replay re-reads recorded results and
re-runs no handler, so it decides the boundary exactly as the first run did, and a later turn
inherits what the boundary before it carried.

The history is written only at a boundary, inside the memoized reset step: the outgoing window is
appended to the file as one JSON line per message, so nothing before the first model call of a
round touches the sandbox. A boundary is identified by a digest of the window it closed: a rollover
interrupted after its append and before its record write finds its own record, cuts the file back to
where the record before it ended, and appends the same lines again, so a replay never doubles them.
The file lives and dies with the sandbox, like the workspace and the offloaded tool outputs beside
it; a record names that plainly when the file no longer reaches back."""

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

import lz4.frame
from dbos import DBOS

from ufo.sdk.context import (
    Agent,
    BoundaryOutcome,
    ContextRemaining,
    ContextWindow,
    Turn,
    WorkspaceBlobStore,
)
from ufo.sdk.manifest import HookChain, PostCompact, PreCompact
from ufo.sdk.models import (
    ImageBlock,
    Message,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    ServingModel,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.sdk.o11y import emit_metric, log
from ufo.sdk.sandbox import Sandbox, SandboxProviderUnavailable, SandboxUnreachable
from ufo.sdk.skills import LoadedSkills
from ufo.sdk.terminal import TerminalGone
from ufo.sdk.transcript import (
    MEMBER_CONTEXT_OPENING,
    Anchor,
    AnchorKind,
    PendingResult,
    RecoveryRecord,
    RolloverRecord,
    RolloverVerification,
    RolloverWindow,
    count_summary_records,
    read_recovery_record,
    read_rollover_record,
    rollover_key,
)
from ufo.sdk.untrusted import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from ufo_ext_context_rollover.tools import NEW_CONTEXT_TOOL, cap_checklist, history_filename

ROLLOVER_METRIC = "rollover_total"
CHARS_PER_TOKEN = 2
IMAGE_TOKEN_ESTIMATE = 1_600
DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000
ROLLOVER_BUFFER_TOKENS = 30_000
RECOVERY_RESERVE_TOKENS = 20_000
"""What the fresh window's own first message may cost: the reserve the rollover line is set back
from the model's real window, and the ceiling the recovery record is bounded to."""
HANDOFF_MAX_CHARS = 20_000
CHECKPOINT_MAX_CHARS = 8_000
CHECKLIST_MAX_CHARS = 8_000
MAX_USER_INPUTS = 5
USER_INPUT_MAX_CHARS = 4_000
MAX_PENDING_RESULTS = 20
MAX_ACTIVE_REQUESTS = 10
PENDING_RESULT_MAX_CHARS = 2_000
PENDING_ARGUMENTS_MAX_CHARS = 500
MAX_ANCHORS_PER_KIND = 20
CHECKPOINT_REMINDER_FRACTION = 90
"""The percent of the rollover line past which the turn gets its one checkpoint reminder."""

ROLLOVER_PREFIX = "Context rollover — the window was reset. Nothing below is a summary.\n"
HANDOFF_HEADING = "## Handoff you wrote before this reset"
OBJECTIVE_HEADING = "## Objective you were spawned with, verbatim"
USER_INPUTS_HEADING = "## Recent member messages, verbatim"
PENDING_HEADING = "## Tool results you had not read yet"
CHECKLIST_HEADING = "## Checklist, carried verbatim"
CHECKPOINT_HEADING = "## Earlier checkpoint — POSSIBLY STALE"
ACTIVE_REQUESTS_HEADING = "## Active member requests"
LOADED_SKILLS_HEADING = "## Skills whose workflow text the reset dropped"
CONTINUE_HEADING = "## How to continue"
HISTORY_LINE = (
    "History: {path} — lines {first}-{last} hold this window in full; earlier lines hold the "
    "windows before it."
)
HISTORY_LOST_NOTE = (
    "The history file on this sandbox does not reach back before this window; the windows before "
    "it went with the sandbox that held them."
)
HISTORY_UNWRITTEN_NOTE = (
    "History: no sandbox was reachable at this reset, so this window was not written to the "
    "history file. Only the record above carries it."
)
HISTORY_INSTRUCTION = (
    "- The whole conversation is in that file, one JSON line per message, append-only. "
    "search_history finds lines by phrase, newest first; read a line with bash (sed -n 'Np'); "
    "the line numbers above address the rest of any result trimmed here.\n"
)
VERIFY_INSTRUCTION = (
    "- Verify live state before any stateful or external action. Nothing above proves what is "
    "true right now — it states what was said and done before the reset."
)
CHECKPOINT_REMINDER = (
    "Context checkpoint reminder: this window is close to its rollover line. Write your current "
    "state to your notes file now, and call new_context with a handoff when the work in flight is "
    "at a clean point. This reminder is sent once."
)
TRUNCATION_MARKER = "\n[trimmed — line {entry_id} of the history file has the rest]"

IMAGE_MARKER = "[image]"
REDACTED_REASONING_MARKER = "[redacted reasoning]"
ERROR_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}(?:Error|Exception)\b")
MESSAGE_REF_RE = re.compile(r"message_ref: (\S+)")
JOURNAL_APPEND_SCRIPT = (
    'set -e; mkdir -p "$(dirname "$1")"; '
    'if [ -f "$1" ]; then held=$(wc -l < "$1"); head -n "$3" "$1" > "$1.tmp"; '
    'else held=0; : > "$1.tmp"; fi; '
    'cat "$2" >> "$1.tmp"; mv "$1.tmp" "$1"; rm -f "$2"; '
    'printf "%s %s\\n" "$held" "$(wc -l < "$1")"'
)
"""Keep the first `$3` lines of the history file `$1`, add the lines in `$2` after them, and print
how many lines the file held before and holds after. The bytes arrive as a runtime file write; the
shell only splices, so nothing the model wrote ever rides a command line."""


def harvest_anchors(
    window_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]
) -> tuple[Anchor, ...]:
    """The anchor set a boundary is graded against: what the window demonstrably held — the skill
    workflows the reset drops, the refs of the member requests still open, and the error classes
    the window named. Each is harvested by pattern from the window's own text or from the
    pipeline's trackers, never from anything written at the boundary, so the grade cannot be
    authored by the thing it grades."""
    harvested: tuple[tuple[AnchorKind, list[str]], ...] = (
        ("error", ERROR_TOKEN_RE.findall(window_text)),
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
    """The anchors absent from the text a boundary carries forward — the recovery record plus the
    window it appended to the history file. Containment, not similarity: an anchor is a literal, and
    a boundary carries it only by reproducing it or by leaving it readable at a line."""
    return tuple(anchor for anchor in anchors if anchor.literal not in carried)


class Journal(Protocol):
    """Where a rolled-over window goes: one JSON line per message, addressed by line number, where
    `search_history` and the agent's own shell reach it."""

    async def display_path(self) -> str: ...

    async def append(self, lines: tuple[str, ...], after: int) -> tuple[int, int]: ...


@dataclass(frozen=True)
class SandboxJournal:
    """The history file in the conversation's sandbox, beside its offloaded tool outputs. `append`
    keeps the first `after` lines and adds the new ones behind them, answering how many lines the
    file held before and holds after — so a replayed boundary rewrites the same lines, and a file a
    fresh sandbox no longer holds is reported rather than assumed."""

    sandbox: Sandbox
    conversation_id: UUID

    async def display_path(self) -> str:
        return await self.sandbox.runtime_display_path(history_filename(self.conversation_id))

    async def append(self, lines: tuple[str, ...], after: int) -> tuple[int, int]:
        part = f"history-{self.conversation_id.hex}.part"
        await self.sandbox.write_runtime_file(part, "".join(f"{line}\n" for line in lines).encode())
        result = await self.sandbox.sh(
            JOURNAL_APPEND_SCRIPT,
            await self.sandbox.runtime_path(history_filename(self.conversation_id)),
            await self.sandbox.runtime_path(part),
            str(after),
        )
        counts = result.stdout.split()
        if result.exit_code != 0 or len(counts) != 2 or not all(c.isdigit() for c in counts):
            raise RuntimeError(
                f"history append failed (exit {result.exit_code}): "
                f"{(result.stderr or result.stdout).strip()[:200]}"
            )
        return int(counts[0]), int(counts[1])


@dataclass(frozen=True)
class ResetRequest:
    """What the model's last `new_context` call asked for, read off the recorded window: the
    handoff already trimmed to the cap, the checklist to carry, and whether the cap trimmed."""

    handoff: str
    checklist: tuple[str, ...]
    trimmed: bool


@dataclass(frozen=True, repr=False)
class _RolloverRequest:
    """A cancelled DBOS step logs its arguments, so the repr renders none of the window."""

    messages: tuple[Message, ...]
    force: bool
    reset: ResetRequest | None
    active_requests: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"_RolloverRequest(messages={len(self.messages)}, force={self.force}, "
            f"reset={self.reset is not None}, active_requests={len(self.active_requests)})"
        )


@dataclass
class _RolloverState:
    window: tuple[Message, ...] = ()
    reminded: bool = False


@dataclass(frozen=True, repr=False)
class ContextRollover:
    """The rollover workflow: journal the window, answer where the window stands, and — at the
    line, on overflow, or on a `new_context` call the last round landed — reset the window to a
    recovery record and persist the boundary. It fires the observe-only `pre_compact` before
    installing the fresh window and `post_compact` after, fired here rather than in the engine
    because only this flow knows past every guard that the reset will truly happen.

    `loaded_skills` is the turn's skill-load tracker, shared with the tool context: the reset drops
    the workflow bodies the window held, so this flow drains the tracker into the recovery record
    and leaves it empty.

    `journal` is the history file the outgoing window is appended to at the boundary — the
    conversation's sandbox in production, so the agent reaches it with the file tools it already
    has. Nothing is written to it on a round that does not roll over."""

    serving: ServingModel
    blob: WorkspaceBlobStore
    conversation_id: UUID
    recovery_reserve_tokens: int = RECOVERY_RESERVE_TOKENS
    trigger_tokens: int | None = None
    hooks: HookChain = field(default_factory=HookChain)
    loaded_skills: LoadedSkills = field(default_factory=LoadedSkills)
    turn: Turn | None = None
    agent: Agent | None = None
    journal: Journal = field(kw_only=True)
    _state: _RolloverState = field(default_factory=_RolloverState, init=False, compare=False)

    def __repr__(self) -> str:
        return (
            f"ContextRollover(conversation_id={self.conversation_id}, model={self.serving.model})"
        )

    @property
    def window(self) -> ContextWindow[Message]:
        """The window of the model serving the turn right now — read off `serving` at each use, so
        a turn that moved onto another account rolls over against that model's real window."""
        return ContextWindow(
            role=lambda message: message.role,
            text=self._text,
            opaque_chars=self._opaque_chars,
            image_count=self._image_count,
            context_tokens=self.serving.spec.context_window,
            reserve_tokens=self.recovery_reserve_tokens,
            buffer_tokens=ROLLOVER_BUFFER_TOKENS,
            chars_per_token=CHARS_PER_TOKEN,
            image_tokens=IMAGE_TOKEN_ESTIMATE,
            trigger_tokens=(
                self.serving.spec.rollover_trigger_tokens
                if self.trigger_tokens is None
                else self.trigger_tokens
            ),
        )

    def remaining(self, messages: tuple[Message, ...] | None = None) -> ContextRemaining:
        """Where the window stands: tokens used, the rollover line, and the model's hard limit.
        Reported on demand rather than injected each round, so the number the agent reads never
        churns the prompt it reads it from."""
        window = self.window
        used = window.tokens(self._state.window if messages is None else messages)
        return ContextRemaining(
            used_tokens=used,
            rollover_at_tokens=window.trigger,
            tokens_until_rollover=max(window.trigger - used, 0),
            hard_limit_tokens=window.context_tokens,
            tokens_until_hard_limit=max(window.context_tokens - used, 0),
        )

    def handoff_cap(self) -> int:
        """A handoff may take at most half the fresh window's own capacity, and never more than the
        flat cap: a handoff that fills the window it opens has moved the problem, not solved it."""
        return min(HANDOFF_MAX_CHARS, self.recovery_reserve_tokens * CHARS_PER_TOKEN // 2)

    def checklist_cap(self) -> int:
        """A checklist may take a quarter of the fresh window's own capacity, never more than the
        flat cap: it is carried into every later window, so what it costs it costs forever."""
        return min(CHECKLIST_MAX_CHARS, self.recovery_reserve_tokens * CHARS_PER_TOKEN // 4)

    def _reset_request(self, messages: tuple[Message, ...]) -> ResetRequest | None:
        """Read off the window: a crash-recovery replay re-reads recorded results and re-runs no
        handler."""
        last_assistant = self._last_assistant(messages)
        if last_assistant is None:
            return None
        calls = {
            block.id: block
            for block in self._blocks(messages[last_assistant])
            if isinstance(block, ToolUseBlock) and block.name == NEW_CONTEXT_TOOL
        }
        landed = [
            calls[block.tool_use_id]
            for message in messages[last_assistant + 1 :]
            for block in self._blocks(message)
            if isinstance(block, ToolResultBlock)
            and block.tool_use_id in calls
            and not block.is_error
        ]
        if not landed:
            return None
        handoff = landed[-1].input.get("handoff")
        checklist = landed[-1].input.get("checklist")
        stripped = handoff.strip() if isinstance(handoff, str) else ""
        cap = self.handoff_cap()
        return ResetRequest(
            handoff=self._reclosed(stripped[:cap], stripped),
            checklist=(
                tuple(line for line in checklist if isinstance(line, str))
                if isinstance(checklist, list | tuple)
                else ()
            ),
            trimmed=len(stripped) > cap,
        )

    def _repeated_tool_trigger(self, messages: tuple[Message, ...]) -> int | None:
        policy = self.serving.spec.repeated_tool_rollover
        if policy is None:
            return None
        repeated: set[tuple[str, str]] | None = None
        current: set[tuple[str, str]] = set()
        turns = 0
        latest = True
        for message in reversed(messages):
            if message.role == "user" and not self._is_tool_results(message):
                if latest:
                    latest = False
                    if not current:
                        continue
                turns += 1
                repeated = current if repeated is None else repeated & current
                if not repeated:
                    return None
                if turns == policy.consecutive_turns:
                    return max(1, self.window.trigger * policy.trigger_percent // 100)
                current = set()
                continue
            if message.role != "assistant":
                continue
            current.update(
                (block.name, json.dumps(block.input, sort_keys=True, separators=(",", ":")))
                for block in self._blocks(message)
                if isinstance(block, ToolUseBlock)
            )
        return None

    async def maybe_cross(
        self,
        messages: tuple[Message, ...],
        force: bool = False,
        active_requests: tuple[str, ...] = (),
        final: bool = False,
    ) -> BoundaryOutcome:
        """Reset this window when it crossed the line, when the provider refused it as too large
        (`force`), or when the last round landed a `new_context` call. A window of one message has
        nothing to roll over and is returned unchanged, so a forced call still no-ops safely. A
        window still under the line gets at most one checkpoint reminder per turn.

        `final` is the round budget's last word: the round that follows answers with no tools, so
        it could not read the history the record points at. A requested reset is not honoured —
        nothing continues past the answer — and the window is reset only when it cannot fit at
        all, so the answer is written from the work itself whenever the work still fits."""
        self._state.window = messages
        window = self.window
        before_tokens = window.tokens(messages)
        reset = None if final else self._reset_request(messages)
        repeated = None if final else self._repeated_tool_trigger(messages)
        line = window.trigger if repeated is None else min(repeated, window.trigger)
        if final:
            line = window.trigger + window.reserve_tokens
        if len(messages) <= 1 or not (force or reset is not None or before_tokens > line):
            return BoundaryOutcome(
                messages if final else self._remind(messages, before_tokens), False
            )
        rolled = await self._roll_over(
            _RolloverRequest(
                messages=messages, force=force, reset=reset, active_requests=active_requests
            )
        )
        self._state.window = rolled
        return BoundaryOutcome(rolled, True)

    def _remind(self, messages: tuple[Message, ...], before_tokens: int) -> tuple[Message, ...]:
        """One best-effort checkpoint reminder, in the last band of usable context. Once per turn:
        a reminder repeated every round is the churn the stable prompt note exists to avoid."""
        band = self.window.trigger * CHECKPOINT_REMINDER_FRACTION // 100
        if self._state.reminded or before_tokens < band:
            return messages
        self._state.reminded = True
        log(
            "rollover.checkpoint_reminder",
            conversation_id=str(self.conversation_id),
            tokens=before_tokens,
            band=band,
        )
        return (*messages, Message(role="user", content=CHECKPOINT_REMINDER))

    @DBOS.step()
    async def _roll_over(self, request: _RolloverRequest) -> tuple[Message, ...]:
        """The guards stay outside the DBOS step, so a round that does not roll over records no step
        and the step sequence lines up on replay."""
        messages, force, reset, active_requests = (
            request.messages,
            request.force,
            request.reset,
            request.active_requests,
        )
        window = self.window
        before_tokens = window.tokens(messages)
        drained = self.loaded_skills.drain()
        window_text = self._window_text(messages)
        window_digest = sha256(window_text.encode()).hexdigest()
        index, previous = await self._boundary(window_digest)
        expected = 0 if previous is None else previous.last_entry_id
        journaled = await self._journal(messages, expected)
        held, total, history_path = (expected, expected, "") if journaled is None else journaled
        first_entry_id = min(held, expected) + 1
        carried = (
            reset.checklist
            if reset is not None and reset.checklist
            else (previous.checklist if previous is not None else ())
        )
        checklist = cap_checklist(carried, self.checklist_cap())
        record = RecoveryRecord(
            objective=self._objective(),
            user_inputs=self._user_inputs(messages),
            pending_results=self._pending_results(messages, first_entry_id),
            checklist=checklist,
            checkpoint=(
                None
                if previous is None
                else self._cap_checkpoint(previous.handoff or previous.checkpoint)
            ),
            handoff=reset.handoff or None if reset is not None else None,
            active_requests=self._active_requests(active_requests),
            loaded_skills=drained,
            first_entry_id=first_entry_id,
            last_entry_id=total,
            history_path=history_path,
            history_lost=held < expected,
            window_digest=window_digest,
        )
        rendered = self.render(record)
        after = (Message(role="user", content=rendered),)
        after_tokens = window.tokens(after)
        room = (
            window.tokens((Message(role="user", content=self.render(RecoveryRecord())),))
            + self._carried_ceiling_chars() // CHARS_PER_TOKEN
            + self.recovery_reserve_tokens
        )
        if before_tokens > room and after_tokens >= before_tokens:
            raise RuntimeError(
                f"rollover did not shrink the window: {after_tokens} >= {before_tokens} tokens"
            )
        verification = RolloverVerification(
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            entries=len(messages),
            anchors=len(anchors := harvest_anchors(window_text, drained, active_requests)),
            missing=missing_anchors(anchors, rendered + "\n" + window_text),
            handoff_chars=len(record.handoff or ""),
            handoff_capped=reset is not None and reset.trimmed,
            checklist_chars=sum(len(line) for line in checklist),
            checklist_capped=checklist != carried,
        )
        record = record.model_copy(update={"verification": verification})
        reason: Literal["force", "requested", "window"] = (
            "force" if force else ("requested" if reset is not None else "window")
        )
        await self.hooks.fire(
            "pre_compact",
            PreCompact(reason="force" if force else "auto", before_tokens=before_tokens),
            self.turn,
            self.agent,
            None,
        )
        await self._persist(index, messages, after, record)
        self._record_verification(index, reason, verification)
        await self.hooks.fire(
            "post_compact",
            PostCompact(record=rendered, before_tokens=before_tokens, after_tokens=after_tokens),
            self.turn,
            self.agent,
            None,
        )
        return after

    async def _journal(
        self, messages: tuple[Message, ...], expected: int
    ) -> tuple[int, int, str] | None:
        """None when no sandbox is reachable: the reset needs none, and the record keeps the
        previous end line so the next boundary appends where this one would have."""
        try:
            held, total = await self.journal.append(
                tuple(
                    json.dumps(
                        {"role": message.role, "text": self._text(message)}, ensure_ascii=False
                    )
                    for message in messages
                ),
                after=expected,
            )
            return held, total, await self.journal.display_path()
        except (TerminalGone, SandboxUnreachable, SandboxProviderUnavailable) as error:
            log(
                "rollover.history_unwritten",
                conversation_id=str(self.conversation_id),
                error_class=type(error).__name__,
            )
            return None

    def render(self, record: RecoveryRecord) -> str:
        """Render the recovery record into the one user message the fresh window opens with —
        deterministically, so the same record always yields the same window."""
        blocks: list[str] = []
        if record.objective:
            blocks.append(f"{OBJECTIVE_HEADING}\n{record.objective}")
        if record.handoff:
            blocks.append(f"{HANDOFF_HEADING}\n{record.handoff}")
        if record.user_inputs:
            blocks.append(USER_INPUTS_HEADING + "\n" + self._bullets(record.user_inputs))
        if record.pending_results:
            blocks.append(
                PENDING_HEADING
                + "\n"
                + "\n\n".join(
                    f"- {result.call}({result.arguments}) [line {result.entry_id}]\n{result.text}"
                    + (
                        TRUNCATION_MARKER.format(entry_id=result.entry_id)
                        if result.truncated
                        else ""
                    )
                    for result in record.pending_results
                )
            )
        if record.checklist:
            blocks.append(CHECKLIST_HEADING + "\n" + self._bullets(record.checklist))
        if record.checkpoint:
            blocks.append(f"{CHECKPOINT_HEADING}\n{record.checkpoint}")
        if record.active_requests:
            blocks.append(ACTIVE_REQUESTS_HEADING + "\n" + "\n\n".join(record.active_requests))
        if record.loaded_skills:
            blocks.append(LOADED_SKILLS_HEADING + "\n" + self._bullets(record.loaded_skills))
        blocks.append(
            f"{CONTINUE_HEADING}\n"
            + (
                HISTORY_LINE.format(
                    path=record.history_path,
                    first=record.first_entry_id,
                    last=record.last_entry_id,
                )
                + (f"\n{HISTORY_LOST_NOTE}" if record.history_lost else "")
                + f"\n{HISTORY_INSTRUCTION}"
                if record.history_path
                else f"{HISTORY_UNWRITTEN_NOTE}\n"
            )
            + VERIFY_INSTRUCTION
        )
        return ROLLOVER_PREFIX + "\n".join(blocks)

    def _bullets(self, items: tuple[str, ...]) -> str:
        return "\n".join(f"- {item}" for item in items)

    def _cap_checkpoint(self, checkpoint: str | None) -> str | None:
        if checkpoint is None:
            return None
        return self._reclosed(checkpoint[:CHECKPOINT_MAX_CHARS], checkpoint)

    def _carried_ceiling_chars(self) -> int:
        return (
            USER_INPUT_MAX_CHARS * (MAX_USER_INPUTS + MAX_ACTIVE_REQUESTS + 1)
            + MAX_PENDING_RESULTS
            * (PENDING_RESULT_MAX_CHARS + PENDING_ARGUMENTS_MAX_CHARS + len(TRUNCATION_MARKER))
            + self.handoff_cap()
            + self.checklist_cap()
            + CHECKPOINT_MAX_CHARS
        )

    def _active_requests(self, requests: tuple[str, ...]) -> tuple[str, ...]:
        """The open member requests, newest last, each bounded like a member message and re-closed
        if the cut opened a wall; the whole request stays readable in the history file."""
        return tuple(
            self._reclosed(request[:USER_INPUT_MAX_CHARS], request)
            for request in requests[-MAX_ACTIVE_REQUESTS:]
        )

    def _objective(self) -> str | None:
        """A spawned turn's founding inbound carries no envelope, so the member-words filter never
        keeps it."""
        if self.turn is None or not self.turn.spawned:
            return None
        return self.turn.inbound[:USER_INPUT_MAX_CHARS]

    def _user_inputs(self, messages: tuple[Message, ...]) -> tuple[str, ...]:
        """Only member inbounds open with the envelope the engine renders; runtime prompts and tool
        results carry none."""
        found = [
            self._reclosed(self._text(message)[:USER_INPUT_MAX_CHARS], self._text(message))
            for message in messages
            if message.role == "user"
            and not any(isinstance(block, ToolResultBlock) for block in self._blocks(message))
            and self._text(message).startswith(MEMBER_CONTEXT_OPENING)
        ]
        return tuple(found[-MAX_USER_INPUTS:])

    def _pending_results(
        self, messages: tuple[Message, ...], first_entry_id: int
    ) -> tuple[PendingResult, ...]:
        last_assistant = self._last_assistant(messages)
        if last_assistant is None:
            return ()
        calls = {
            block.id: block
            for block in self._blocks(messages[last_assistant])
            if isinstance(block, ToolUseBlock)
        }
        pending: list[PendingResult] = []
        for index in range(last_assistant + 1, len(messages)):
            entry_id = first_entry_id + index
            for block in self._blocks(messages[index]):
                if not isinstance(block, ToolResultBlock):
                    continue
                call = calls.get(block.tool_use_id)
                if call is not None and call.name == NEW_CONTEXT_TOOL:
                    continue
                text = self._block_text(block)
                kept = self._reclosed(text[:PENDING_RESULT_MAX_CHARS], text)
                pending.append(
                    PendingResult(
                        entry_id=entry_id,
                        call=call.name if call is not None else "unknown",
                        arguments=(
                            json.dumps(call.input, sort_keys=True)[:PENDING_ARGUMENTS_MAX_CHARS]
                            if call is not None
                            else ""
                        ),
                        text=kept,
                        truncated=len(text) > PENDING_RESULT_MAX_CHARS,
                    )
                )
        return tuple(pending[-MAX_PENDING_RESULTS:])

    def _reclosed(self, kept: str, whole: str) -> str:
        """The body escapes every literal close tag, so the one unescaped close is the wall's."""
        opened = UNTRUSTED_OPEN.split("{", 1)[0]
        if opened in whole and UNTRUSTED_CLOSE not in kept:
            return kept + "\n" + UNTRUSTED_CLOSE
        return kept

    def _last_assistant(self, messages: tuple[Message, ...]) -> int | None:
        return max(
            (index for index, message in enumerate(messages) if message.role == "assistant"),
            default=None,
        )

    async def _boundary(self, window_digest: str) -> tuple[int, RecoveryRecord | None]:
        previous: RecoveryRecord | None = None
        index = await count_summary_records(self.blob, self.conversation_id) + 1
        while (
            record := await read_recovery_record(self.blob, self.conversation_id, index)
        ) is not None:
            if record.window_digest == window_digest:
                return index, previous
            previous = record
            index += 1
        return index, previous

    async def read_record(self, index: int) -> RolloverRecord | None:
        return await read_rollover_record(self.blob, self.conversation_id, index)

    async def _persist(
        self,
        index: int,
        before: tuple[Message, ...],
        after: tuple[Message, ...],
        record: RecoveryRecord,
    ) -> None:
        await self._write(index, "before", before)
        await self._write(index, "after", after)
        await self.blob.put(
            rollover_key(self.conversation_id, index, "recovery"),
            lz4.frame.compress(record.model_dump_json().encode()),
        )

    async def _write(
        self, index: int, half: Literal["before", "after"], messages: tuple[Message, ...]
    ) -> None:
        encoded = json.dumps(
            RolloverWindow(messages=messages).model_dump(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        await self.blob.put(
            rollover_key(self.conversation_id, index, half), lz4.frame.compress(encoded)
        )

    def _record_verification(
        self,
        index: int,
        reason: Literal["force", "requested", "window"],
        verification: RolloverVerification,
    ) -> None:
        log(
            "rollover.recorded",
            conversation_id=str(self.conversation_id),
            index=index,
            reason=reason,
            before_tokens=verification.before_tokens,
            after_tokens=verification.after_tokens,
            entries=verification.entries,
            anchors=verification.anchors,
            missing=[f"{anchor.kind}:{anchor.literal}" for anchor in verification.missing],
            handoff_chars=verification.handoff_chars,
            handoff_capped=verification.handoff_capped,
            checklist_capped=verification.checklist_capped,
        )
        emit_metric(
            ROLLOVER_METRIC,
            model=self.serving.model,
            outcome="unrecoverable" if verification.missing else "recoverable",
            provider=self.serving.spec.provider,
            reason=reason,
        )

    def _is_tool_results(self, message: Message) -> bool:
        blocks = self._blocks(message)
        return bool(blocks) and all(isinstance(block, ToolResultBlock) for block in blocks)

    def _blocks(self, message: Message) -> tuple[object, ...]:
        return () if isinstance(message.content, str) else tuple(message.content)

    def _window_text(self, messages: tuple[Message, ...]) -> str:
        return "\n".join(self._text(message) for message in messages)

    def _opaque_chars(self, message: Message) -> int:
        """Thinking signatures and redacted or encrypted reasoning bodies are re-sent on every
        request of the turn but are not prose."""
        total = 0
        for block in self._blocks(message):
            match block:
                case ThinkingBlock(signature=signature):
                    total += len(signature)
                case RedactedThinkingBlock(data=data):
                    total += len(data)
                case ReasoningItemBlock(encrypted_content=encrypted):
                    total += len(encrypted)
        return total

    def _image_count(self, message: Message) -> int:
        total = 0
        for block in self._blocks(message):
            match block:
                case ImageBlock():
                    total += 1
                case ToolResultBlock(content=tuple(parts)):
                    total += sum(1 for part in parts if isinstance(part, ImageBlock))
        return total

    def _block_text(self, block: ToolResultBlock) -> str:
        if isinstance(block.content, str):
            return block.content
        return "\n".join(
            part.text if isinstance(part, TextBlock) else IMAGE_MARKER for part in block.content
        )

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
                case ToolResultBlock():
                    rendered.append(self._block_text(block))
                case ToolUseBlock(name=name, input=arguments):
                    rendered.append(f"{name}({json.dumps(arguments, sort_keys=True)})")
        return "\n".join(rendered)
