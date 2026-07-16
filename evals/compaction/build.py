"""Build the compaction snapshot: full-scale planted-fact windows synthesized from this repo.

`python -m evals.compaction.build --out DIR` harvests real material (docs and source of the
checkout it runs in), composes it into realistic tool-traffic windows sized to cross the
compaction trigger, and splices in generated facts whose value literals are guaranteed absent
from every filler byte — so any post-boundary appearance is survival, never coincidence. The
same machinery builds at any `--target-tokens`, which is how the unit tests exercise it cheaply."""

import argparse
import base64
import hashlib
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

from evals.compaction.models import (
    CompactionCase,
    CompactionLeaf,
    CompactionProbe,
    CorpusFile,
    FactKind,
    PlantedFact,
    WindowExtension,
    estimate_tokens,
    message_text,
)
from evals.compaction.snapshot import write_snapshot
from ufo.loop.compaction import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACTION_KEEP_MESSAGES,
    COMPACTION_SUMMARY_MAX_TOKENS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
)
from ufo.loop.engine import OFFLOAD_NOTICE
from ufo.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

TRIGGER_MARGIN_TOKENS = 2_000
DEFAULT_TARGET_TOKENS = (
    DEFAULT_CONTEXT_WINDOW_TOKENS
    - COMPACTION_SUMMARY_MAX_TOKENS
    - AUTOCOMPACT_BUFFER_TOKENS
    + TRIGGER_MARGIN_TOKENS
)
EXTENSION_TARGET_FRACTION = 0.95
CORPUS_GLOBS = ("spec.md", "README.md", "docs/**/*.md", "core/src/ufo/**/*.py")
SLICE_CHARS = 4_000
VALUE_FLOOR = 3_000
VALUE_STEP = 137
VALUE_SPAN = 6_999
IMAGE_FACT_WORD = "CANARY"

DOMAINS = (
    "export",
    "webhook",
    "billing",
    "sandbox",
    "queue",
    "search",
    "memory",
    "browser",
    "slack",
    "email",
    "deploy",
    "backup",
    "audit",
    "metrics",
    "auth",
)
PROPERTIES = (
    ("throttle ceiling", "requests/minute"),
    ("retry backoff cap", "seconds"),
    ("batch window", "milliseconds"),
    ("payload limit", "kilobytes"),
    ("timeout floor", "seconds"),
    ("partition count", "partitions"),
    ("retention horizon", "days"),
    ("sampling rate", "per-mille"),
    ("concurrency cap", "workers"),
    ("shard budget", "shards"),
)

OPENER = (
    "We are doing the quarterly platform configuration review. Walk the docs and source, and as "
    "you go I will call out the settings we are locking in. Keep every recorded decision straight."
)
CLOSER = "Progress noted; the review continues from the recorded decisions above."
EXTENSION_OPENER = "Keep going through the remaining material; the decisions above still stand."

DECISION_POOL = (
    "For the record: we set the {subject} to {value}. Keep this decision in mind.",
    "Decision on the {subject}: it goes to {value}.",
    "We agreed just now to move the {subject} to {value}.",
    "Lock in {value} for the {subject}.",
    "After that discussion, the {subject} lands at {value}.",
    "Put the {subject} down as {value} in the review notes.",
    "The {subject} is now {value} — that one is settled.",
    "Final call on the {subject}: {value}.",
    "Let's go with {value} for the {subject} and move on.",
    "We are standardizing the {subject} at {value}.",
)
STALE_POOL = (
    "Note for the review record: the {subject} is {value}.",
    "Current state before any changes: the {subject} sits at {value}.",
    "As of this morning the {subject} runs at {value}.",
    "For context, the {subject} has been {value} all quarter.",
    "The dashboard currently shows the {subject} at {value}.",
    "Baseline going in: {value} on the {subject}.",
)
CORRECTION_POOL = (
    "Correction to an earlier note: the {subject} is {value} going forward — use only this value.",
    "Update on the {subject}: it changes to {value}, effective immediately.",
    "Scratch what I said about the {subject} earlier — make it {value}.",
    "Revising the {subject}: the final number is {value}.",
    "The {subject} got re-decided after the sync; it is {value} now.",
    "Overriding the earlier {subject} figure — go with {value} from here on.",
)
BURIED_POOL = (
    "\n\nConfig audit note: the {subject} is pinned at {value}; this review confirms it as the "
    "operative setting.",
    "\n\nOperative value per the runbook: {subject} = {value}.",
    "\n\nThe deployment manifest sets the {subject} to {value}; treat that as the source of truth.",
    "\n\nPer the incident retro, the {subject} was locked at {value} and must stay there.",
    "\n\nCurrent production value confirmed by the on-call: {subject} at {value}.",
    "\n\nChange-log entry from last sprint: {subject} raised to {value}, still in effect.",
)
DISTRACTOR_POOL = (
    "\n\nIncidentally, a vendor benchmark quoted {value} for their own {subject}; irrelevant to "
    "this review.",
    "\n\nAside: a blog post we skimmed claimed {value} for a similar {subject}, which does not "
    "apply to our setup.",
    "\n\nA competitor changelog mentions {value} on their {subject}; noted only as trivia.",
    "\n\nSomeone in the channel pasted {value} as their {subject} — different stack, ignore it.",
    "\n\nAn abandoned runbook draft once floated {value} for the {subject}.",
    "\n\nThe sales deck cites {value} for a customer's {subject}, which has no bearing here.",
)
ACK_POOL = (
    "Noted. Continuing with the next chunk.",
    "Got it — recorded. Back to the material.",
    "Logged that. Picking up where I left off.",
    "Understood, that is captured.",
    "Recorded. Resuming the walk-through.",
    "Captured — carrying on.",
)
CHATTER_POOL = (
    "How far through the material are we at this point?",
    "This stretch looks like boilerplate — keep going.",
    "Nothing to add on that section, continue.",
    "Skim the next part unless something stands out.",
    "Still with you. Go on.",
    "That part matches what I remembered; proceed.",
    "No changes coming out of that section.",
    "Fine to keep moving here.",
)
PROGRESS_POOL = (
    "Working through the next stretch of the corpus.",
    "Moving on to the following file.",
    "Continuing the walk-through.",
    "Proceeding through the remaining material.",
    "On to the next section.",
    "Resuming the read-through.",
)

FONT = {
    "0": (".XXX.", "X...X", "X..XX", "X.X.X", "XX..X", "X...X", ".XXX."),
    "1": ("..X..", ".XX..", "..X..", "..X..", "..X..", "..X..", "XXXXX"),
    "2": (".XXX.", "X...X", "....X", "...X.", "..X..", ".X...", "XXXXX"),
    "3": ("XXXX.", "....X", "....X", ".XXX.", "....X", "....X", "XXXX."),
    "4": ("...X.", "..XX.", ".X.X.", "X..X.", "XXXXX", "...X.", "...X."),
    "5": ("XXXXX", "X....", "XXXX.", "....X", "....X", "X...X", ".XXX."),
    "6": (".XXX.", "X....", "X....", "XXXX.", "X...X", "X...X", ".XXX."),
    "7": ("XXXXX", "....X", "...X.", "..X..", ".X...", ".X...", ".X..."),
    "8": (".XXX.", "X...X", "X...X", ".XXX.", "X...X", "X...X", ".XXX."),
    "9": (".XXX.", "X...X", "X...X", ".XXXX", "....X", "....X", ".XXX."),
    "A": (".XXX.", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"),
    "C": (".XXX.", "X...X", "X....", "X....", "X....", "X...X", ".XXX."),
    "N": ("X...X", "XX..X", "X.X.X", "X..XX", "X...X", "X...X", "X...X"),
    "R": ("XXXX.", "X...X", "X...X", "XXXX.", "X.X..", "X..X.", "X...X"),
    "Y": ("X...X", "X...X", ".X.X.", "..X..", "..X..", "..X..", "..X.."),
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
}
GLYPH_SCALE = 4
GLYPH_MARGIN = 8


@dataclass(frozen=True)
class CaseSpec:
    id: str
    leaf: CompactionLeaf
    decisions: int = 0
    buried: int = 0
    superseded: int = 0
    distractors: int = 0
    references: int = 0
    controls: int = 0
    image: bool = False
    extensions: int = 0
    probed: bool = False


ROSTER = (
    CaseSpec("overload-1", "overload", decisions=120, distractors=25),
    CaseSpec("overload-2", "overload", decisions=120, distractors=25),
    CaseSpec("buried-1", "buried", decisions=40, buried=40, distractors=15),
    CaseSpec("supersession-1", "supersession", decisions=20, superseded=18, distractors=15),
    CaseSpec("reference-1", "reference", decisions=10, references=9),
    CaseSpec("chain-1", "chain", decisions=40, extensions=2),
    CaseSpec("image-1", "image", decisions=20, image=True),
    CaseSpec(
        "behavior-1",
        "behavior",
        decisions=24,
        superseded=4,
        references=2,
        controls=2,
        probed=True,
    ),
    CaseSpec(
        "behavior-2",
        "behavior",
        decisions=24,
        superseded=4,
        references=2,
        controls=2,
        probed=True,
    ),
)


@dataclass(frozen=True)
class Slice:
    path: str
    chunk: int
    text: str


@dataclass(frozen=True)
class FactSeed:
    fact: PlantedFact
    subject: str
    unit: str
    key: int


@dataclass(frozen=True)
class SnapshotBuild:
    """The build workflow: harvest the corpus, synthesize one window per roster case, prove every
    literal collision-free, and write the digest-pinned snapshot."""

    repo: Path
    out: Path
    target_tokens: int = DEFAULT_TARGET_TOKENS

    def build(self) -> str:
        corpus, slices = self._harvest()
        cases: list[CompactionCase] = []
        cursor = 0
        value_key = 0
        for spec in ROSTER:
            case, cursor, value_key = self._case(spec, slices, cursor, value_key)
            cases.append(case)
        manifest = write_snapshot(
            self.out,
            builder_digest=_builder_digest(),
            target_tokens=self.target_tokens,
            corpus=corpus,
            cases=tuple(cases),
        )
        return manifest.digest

    def _harvest(self) -> tuple[tuple[CorpusFile, ...], tuple[Slice, ...]]:
        paths = sorted(
            {
                candidate.relative_to(self.repo)
                for pattern in CORPUS_GLOBS
                for candidate in self.repo.glob(pattern)
                if candidate.is_file()
            }
        )
        corpus: list[CorpusFile] = []
        slices: list[Slice] = []
        for path in paths:
            text = (self.repo / path).read_text(encoding="utf-8")
            if not text.strip():
                continue
            corpus.append(
                CorpusFile(
                    path=str(path),
                    sha256=f"sha256:{hashlib.sha256(text.encode()).hexdigest()}",
                )
            )
            slices.extend(
                Slice(path=str(path), chunk=index, text=text[start : start + SLICE_CHARS])
                for index, start in enumerate(range(0, len(text), SLICE_CHARS))
            )
        if not slices:
            raise ValueError(f"no corpus material found under {self.repo}")
        return tuple(corpus), tuple(slices)

    def _case(
        self, spec: CaseSpec, slices: tuple[Slice, ...], cursor: int, value_key: int
    ) -> tuple[CompactionCase, int, int]:
        seeds, value_key = self._seeds(spec, slices, value_key)
        window, cursor = self._window(spec, seeds, slices, cursor)
        extensions: list[WindowExtension] = []
        for _ in range(spec.extensions):
            extension, cursor = self._extension(slices, cursor)
            extensions.append(extension)
        case = CompactionCase(
            id=spec.id,
            leaf=spec.leaf,
            messages=window,
            facts=tuple(seed.fact for seed in seeds),
            extensions=tuple(extensions),
            probes=self._probes(seeds) if spec.probed else (),
        )
        self._verify_literals(case)
        return case, cursor, value_key

    def _seeds(
        self, spec: CaseSpec, slices: tuple[Slice, ...], value_key: int
    ) -> tuple[tuple[FactSeed, ...], int]:
        filler_text = "\n".join(item.text for item in slices)
        taken: set[str] = set()

        def next_value() -> str:
            nonlocal value_key
            while True:
                value = str(VALUE_FLOOR + (value_key * VALUE_STEP) % VALUE_SPAN)
                value_key += 1
                if value not in taken and value not in filler_text:
                    taken.add(value)
                    return value

        counted: tuple[tuple[FactKind, int], ...] = (
            ("decision", spec.decisions),
            ("buried", spec.buried),
            ("superseded", spec.superseded),
            ("distractor", spec.distractors),
            ("reference", spec.references),
            ("control", spec.controls),
        )
        seeds: list[FactSeed] = []
        index = 0
        for kind, count in counted:
            for ordinal in range(count):
                domain = DOMAINS[index % len(DOMAINS)]
                prop, unit = PROPERTIES[(index // len(DOMAINS)) % len(PROPERTIES)]
                subject = f"{domain} {prop}"
                literal = next_value()
                stale = next_value() if kind == "superseded" else ""
                path = ""
                body = ""
                weight = 1 + (ordinal % 5)
                if kind == "reference":
                    path = f"/workspace/.tool-output/ref-{spec.id}-{ordinal}.txt"
                    body = (
                        f"Load test report for the {subject}.\n"
                        f"The measured {subject} peaked at {literal} {unit} during the soak "
                        "window.\n"
                    )
                    weight = max(1, 5 - (ordinal * 5) // max(count, 1))
                seeds.append(
                    FactSeed(
                        fact=PlantedFact(
                            id=f"{spec.id}-{kind}-{ordinal}",
                            kind=kind,
                            literal=literal,
                            stale_literal=stale,
                            weight=weight,
                            path=path,
                            body=body,
                        ),
                        subject=subject,
                        unit=unit,
                        key=index,
                    )
                )
                index += 1
        if spec.image:
            seeds.append(
                FactSeed(
                    fact=PlantedFact(
                        id=f"{spec.id}-image-0",
                        kind="image",
                        literal=next_value(),
                        weight=5,
                    ),
                    subject="canary dashboard reading",
                    unit="",
                    key=index,
                )
            )
        return tuple(seeds), value_key

    def _window(
        self,
        spec: CaseSpec,
        seeds: tuple[FactSeed, ...],
        slices: tuple[Slice, ...],
        cursor: int,
    ) -> tuple[tuple[Message, ...], int]:
        early_units: list[tuple[Message, ...]] = []
        mid_units: list[tuple[Message, ...]] = []
        late_units: list[tuple[Message, ...]] = []
        tail_units: list[tuple[Message, ...]] = []
        for seed in seeds:
            if seed.fact.kind == "superseded":
                early_units.append(
                    _statement_unit(_phrase(STALE_POOL, seed, seed.fact.stale_literal), seed.key)
                )
                late_units.append(
                    _statement_unit(_phrase(CORRECTION_POOL, seed, seed.fact.literal), seed.key + 1)
                )
                continue
            unit = self._fact_unit(seed)
            if unit is None:
                continue
            (tail_units if seed.fact.kind == "control" else mid_units).append(unit)
        head_units = [*early_units, *mid_units, *late_units]
        head_units = _spread(
            head_units,
            [
                (
                    Message(role="assistant", content=PROGRESS_POOL[ordinal % len(PROGRESS_POOL)]),
                    Message(role="user", content=CHATTER_POOL[ordinal % len(CHATTER_POOL)]),
                )
                for ordinal in range(max(3, len(head_units) // 5))
            ],
        )
        opener = Message(role="user", content=OPENER)
        closer = Message(role="assistant", content=CLOSER)
        fixed = (
            opener,
            *(message for unit in (*head_units, *tail_units) for message in unit),
            closer,
        )
        total = estimate_tokens(fixed)
        pieces: list[Slice] = []
        while total < self.target_tokens:
            piece = slices[cursor % len(slices)]
            cursor += 1
            pieces.append(piece)
            total += estimate_tokens(self._filler_unit(spec.id, len(pieces) - 1, piece, piece.text))
        guard = (COMPACTION_KEEP_MESSAGES + 1) // 2
        span = len(pieces) - guard
        if span < 1:
            raise ValueError(
                f"case {spec.id!r} filler is too small to keep planted facts out of the tail"
            )
        injected: tuple[tuple[tuple[str, ...], list[FactSeed]], ...] = (
            (DISTRACTOR_POOL, [seed for seed in seeds if seed.fact.kind == "distractor"]),
            (BURIED_POOL, [seed for seed in seeds if seed.fact.kind == "buried"]),
        )
        texts = {index: piece.text for index, piece in enumerate(pieces)}
        for pool, group in injected:
            for ordinal, seed in enumerate(group):
                index = min((ordinal + 1) * span // (len(group) + 1), span - 1)
                texts[index] = _inject(texts[index], _phrase(pool, seed, seed.fact.literal))
        filler = [
            self._filler_unit(spec.id, index, piece, texts[index])
            for index, piece in enumerate(pieces)
        ]
        messages = (
            opener,
            *(message for unit in _spread(filler[:span], head_units) for message in unit),
            *(message for unit in filler[span:] for message in unit),
            *(message for unit in tail_units for message in unit),
            closer,
        )
        if estimate_tokens(messages) < self.target_tokens:
            raise ValueError(f"case {spec.id!r} window fell short of {self.target_tokens} tokens")
        return messages, cursor

    def _extension(self, slices: tuple[Slice, ...], cursor: int) -> tuple[WindowExtension, int]:
        target = max(1, int(self.target_tokens * EXTENSION_TARGET_FRACTION))
        opener = Message(role="user", content=EXTENSION_OPENER)
        closer = Message(role="assistant", content=CLOSER)
        units: list[tuple[Message, ...]] = []
        total = estimate_tokens((opener, closer))
        while total < target:
            piece = slices[cursor % len(slices)]
            cursor += 1
            unit = self._filler_unit("ext", len(units), piece, piece.text)
            units.append(unit)
            total += estimate_tokens(unit)
        messages = (opener, *(message for unit in units for message in unit), closer)
        return WindowExtension(messages=messages), cursor

    def _filler_unit(
        self, case_id: str, call_id: int, piece: Slice, text: str
    ) -> tuple[Message, ...]:
        tool_use_id = f"{case_id}-t{call_id}"
        return (
            Message(
                role="assistant",
                content=(
                    TextBlock(text=f"Reading {piece.path} chunk {piece.chunk}."),
                    ToolUseBlock(
                        id=tool_use_id,
                        name="read_file",
                        input={"path": piece.path, "chunk": piece.chunk},
                    ),
                ),
            ),
            Message(
                role="user",
                content=(ToolResultBlock(tool_use_id=tool_use_id, content=text),),
            ),
        )

    def _fact_unit(self, seed: FactSeed) -> tuple[Message, ...] | None:
        fact = seed.fact
        match fact.kind:
            case "decision" | "control":
                return _statement_unit(_phrase(DECISION_POOL, seed, fact.literal), seed.key)
            case "reference":
                notice = OFFLOAD_NOTICE.format(total=len(fact.body), path=fact.path)
                return (
                    Message(
                        role="assistant",
                        content=(
                            TextBlock(text=f"Pulling the soak report for the {seed.subject}."),
                            ToolUseBlock(
                                id=f"{fact.id}-call",
                                name="bash",
                                input={"command": f"soak-report --for '{seed.subject}'"},
                            ),
                        ),
                    ),
                    Message(
                        role="user",
                        content=(
                            ToolResultBlock(
                                tool_use_id=f"{fact.id}-call",
                                content=f"Report generated for the {seed.subject}.{notice}",
                            ),
                        ),
                    ),
                )
            case "image":
                png = _text_png(f"{IMAGE_FACT_WORD} {fact.literal}")
                return (
                    Message(role="assistant", content="Pulling up the canary dashboard."),
                    Message(
                        role="user",
                        content=(
                            TextBlock(
                                text="Screenshot of the canary dashboard with the current reading:"
                            ),
                            ImageBlock(
                                source=ImageSource(
                                    media_type="image/png",
                                    data=base64.standard_b64encode(png).decode(),
                                )
                            ),
                        ),
                    ),
                )
            case _:
                return None

    def _probes(self, seeds: tuple[FactSeed, ...]) -> tuple[CompactionProbe, ...]:
        by_kind = {
            kind: [seed for seed in seeds if seed.fact.kind == kind]
            for kind in ("decision", "superseded", "reference", "control")
        }
        decision = max(by_kind["decision"], key=lambda seed: seed.fact.weight)
        superseded = by_kind["superseded"][0]
        reference = by_kind["reference"][0]
        control = by_kind["control"][0]
        return (
            CompactionProbe(
                id="recall",
                question=(
                    f"What did we set the {decision.subject} to during this review? "
                    "Answer with the exact number."
                ),
                expect_literals=(decision.fact.literal,),
            ),
            CompactionProbe(
                id="supersession",
                question=(
                    f"What is the current {superseded.subject}? "
                    "Give only the value that stands after any corrections."
                ),
                expect_literals=(superseded.fact.literal,),
                forbid_literals=(superseded.fact.stale_literal,),
            ),
            CompactionProbe(
                id="reference",
                question=(
                    f"What peak value did the offloaded soak report at {reference.fact.path} "
                    "record? Read the file if you need to."
                ),
                expect_literals=(reference.fact.literal,),
                expect_read_path=reference.fact.path,
            ),
            CompactionProbe(
                id="control",
                question=(
                    f"What did we set the {control.subject} to during this review? "
                    "Answer with the exact number."
                ),
                expect_literals=(control.fact.literal,),
            ),
        )

    def _verify_literals(self, case: CompactionCase) -> None:
        window_text = "\n".join(message_text(message) for message in case.messages)
        extension_text = "\n".join(
            message_text(message) for extension in case.extensions for message in extension.messages
        )
        expected_by_kind = {
            "decision": 1,
            "buried": 1,
            "control": 1,
            "distractor": 1,
            "superseded": 1,
            "reference": 0,
            "image": 0,
        }
        for fact in case.facts:
            found = window_text.count(fact.literal)
            if found != expected_by_kind[fact.kind]:
                raise ValueError(
                    f"case {case.id!r} fact {fact.id!r} literal appears {found} times, "
                    f"expected {expected_by_kind[fact.kind]}"
                )
            if fact.literal in extension_text:
                raise ValueError(
                    f"case {case.id!r} fact {fact.id!r} literal leaks into an extension"
                )
            if fact.kind == "reference" and fact.literal not in fact.body:
                raise ValueError(f"reference fact {fact.id!r} body does not carry its literal")
            if fact.stale_literal and window_text.count(fact.stale_literal) != 1:
                raise ValueError(f"case {case.id!r} fact {fact.id!r} stale literal is not unique")
        tail_text = "\n".join(message_text(message) for message in self._tail(case.messages))
        for fact in case.facts:
            if fact.kind == "control":
                if fact.literal not in tail_text:
                    raise ValueError(f"control fact {fact.id!r} fell out of the verbatim tail")
                continue
            marks = (fact.literal, fact.stale_literal, fact.path)
            leaked = next((mark for mark in marks if mark and mark in tail_text), None)
            if leaked is not None:
                raise ValueError(
                    f"case {case.id!r} fact {fact.id!r} leaks into the verbatim tail "
                    f"({leaked!r}) — it would survive compaction without being summarized"
                )

    def _tail(self, messages: tuple[Message, ...]) -> tuple[Message, ...]:
        rounds: list[list[Message]] = []
        current: list[Message] = []
        for message in messages:
            if message.role == "assistant" and current:
                rounds.append(current)
                current = [message]
            else:
                current.append(message)
        if current:
            rounds.append(current)
        kept: list[Message] = []
        count = 0
        while rounds and count < COMPACTION_KEEP_MESSAGES:
            count += len(rounds[-1])
            kept = [*rounds[-1], *kept]
            rounds = rounds[:-1]
        return tuple(kept)


def _inject(text: str, note: str) -> str:
    middle = text.find("\n", len(text) // 2)
    if middle == -1:
        return text + note
    return text[:middle] + note + text[middle:]


def _phrase(pool: tuple[str, ...], seed: FactSeed, literal: str) -> str:
    return pool[seed.key % len(pool)].format(
        subject=seed.subject, value=f"{literal} {seed.unit}".strip()
    )


def _statement_unit(statement: str, key: int) -> tuple[Message, ...]:
    return (
        Message(role="assistant", content=ACK_POOL[key % len(ACK_POOL)]),
        Message(role="user", content=statement),
    )


def _spread(
    base: list[tuple[Message, ...]], extras: list[tuple[Message, ...]]
) -> list[tuple[Message, ...]]:
    merged: list[tuple[Message, ...]] = []
    base_taken = extras_taken = 0
    while base_taken < len(base) or extras_taken < len(extras):
        take_extra = extras_taken < len(extras) and (
            base_taken >= len(base) or extras_taken * len(base) <= base_taken * len(extras)
        )
        if take_extra:
            merged.append(extras[extras_taken])
            extras_taken += 1
        else:
            merged.append(base[base_taken])
            base_taken += 1
    return merged


def _builder_digest() -> str:
    source = Path(__file__).read_text(encoding="utf-8")
    return f"sha256:{hashlib.sha256(source.encode()).hexdigest()}"


def _text_png(text: str) -> bytes:
    unknown = sorted({char for char in text if char not in FONT})
    if unknown:
        raise ValueError(f"image fact text uses unrenderable characters: {unknown}")
    glyph_width = 6 * GLYPH_SCALE
    width = len(text) * glyph_width - GLYPH_SCALE + 2 * GLYPH_MARGIN
    height = 7 * GLYPH_SCALE + 2 * GLYPH_MARGIN
    rows = [bytearray([255] * width) for _ in range(height)]
    for position, char in enumerate(text):
        for glyph_y, pattern in enumerate(FONT[char]):
            for glyph_x, cell in enumerate(pattern):
                if cell != "X":
                    continue
                for dy in range(GLYPH_SCALE):
                    for dx in range(GLYPH_SCALE):
                        x = GLYPH_MARGIN + position * glyph_width + glyph_x * GLYPH_SCALE + dx
                        y = GLYPH_MARGIN + glyph_y * GLYPH_SCALE + dy
                        rows[y][x] = 0
    body = zlib.compress(b"".join(b"\x00" + bytes(row) for row in rows))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", body)
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(tag: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.compaction.build")
    parser.add_argument("--out", type=Path, required=True, help="snapshot output directory")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repo checkout to harvest")
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    digest = SnapshotBuild(repo=args.repo, out=args.out, target_tokens=args.target_tokens).build()
    print(digest)


if __name__ == "__main__":
    main()
