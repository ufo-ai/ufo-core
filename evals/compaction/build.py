"""Build the compaction snapshot: full-scale planted-fact windows synthesized from this repo.

`python -m evals.compaction.build --out DIR` harvests real material (docs and source of the
checkout it runs in), composes it into realistic tool-traffic windows sized to cross the
compaction trigger, and splices in generated facts whose value literals are guaranteed absent
from every filler byte — so any post-boundary appearance is survival, never coincidence. The
same machinery builds at any `--target-tokens`, which is how the unit tests exercise it cheaply."""

import argparse
import base64
import hashlib
import json
import re
import struct
import sys
import zlib
from collections.abc import Callable
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
from ufo.harness.models.interface import (
    ContentBlock,
    ImageBlock,
    ImageSource,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.runtime.compaction import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACTION_KEEP_MESSAGES,
    COMPACTION_SUMMARY_MAX_TOKENS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    IMAGE_MARKER,
)
from ufo.runtime.engine import OFFLOAD_NOTICE
from ufo.runtime.turns.transcript import decode

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

REAL_VALUE_OFFSETS = {
    "real-plan-reversal": 0,
    "real-version-churn": 100,
    "real-poisoned-recap": 200,
}
REAL_TRIM_FRACTION = 1.05
REAL_TAIL_GUARD_ROUNDS = 5
SKELETON_SUFFIX = ".messages.json.lz4"
REAL_SKELETONS: dict[str, tuple[str, ...]] = {
    "real-plan-reversal": (
        "6d58f714-1016-4a0d-ad4b-fdf7e8182f52",
        "b4dd15fe-d94f-4d06-8443-f4370de75eae",
    ),
    "real-version-churn": (
        "5445d6e3-ff52-4f43-8a65-707076359949",
        "0a4c8d40-6e01-4178-aef8-c684ed388055",
    ),
    "real-poisoned-recap": (
        "221e13bd-fc1c-4fb5-b2b1-0d51a2556776",
        "0eb0a497-46b9-45c6-8481-b87838b1425b",
        "08dd500d-b2a6-4d06-bbb6-90fb52c7152c",
    ),
}
SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "redacted@example.com"),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{8,}|xox[a-z]-[A-Za-z0-9-]{8,}"
            r"|ghp_[A-Za-z0-9]{8,}|AKIA[A-Z0-9]{12,})\b"
        ),
        "[redacted]",
    ),
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
    literal collision-free, and write the digest-pinned snapshot. With `transcripts` set, real
    agent transcripts compose the additional real-leaf cases and pin into the manifest."""

    repo: Path
    out: Path
    target_tokens: int = DEFAULT_TARGET_TOKENS
    transcripts: Path | None = None

    def build(self) -> str:
        corpus, slices = self._harvest()
        cases: list[CompactionCase] = []
        cursor = 0
        value_key = 0
        for spec in ROSTER:
            case, cursor, value_key = self._case(spec, slices, cursor, value_key)
            cases.append(case)
        skeletons: tuple[CorpusFile, ...] = ()
        if self.transcripts is not None:
            pinned, decoded = _load_skeletons(self.transcripts)
            skeletons = pinned
            cases.extend(RealCaseBuild(skeletons=decoded, target_tokens=self.target_tokens).build())
        manifest = write_snapshot(
            self.out,
            builder_digest=_builder_digest(),
            target_tokens=self.target_tokens,
            corpus=corpus,
            cases=tuple(cases),
            skeletons=skeletons,
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
        _verify_literals(case)
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
                    path = f"/workspace/tool-output/ref-{spec.id}-{ordinal}.txt"
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
        guard = (COMPACTION_KEEP_MESSAGES + 1) // 2
        pieces: list[Slice] = []
        while total < self.target_tokens or len(pieces) <= guard:
            piece = slices[cursor % len(slices)]
            cursor += 1
            pieces.append(piece)
            total += estimate_tokens(self._filler_unit(spec.id, len(pieces) - 1, piece, piece.text))
        span = len(pieces) - guard
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


def _verify_literals(case: CompactionCase) -> None:
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
            raise ValueError(f"case {case.id!r} fact {fact.id!r} literal leaks into an extension")
        if fact.kind == "reference" and fact.literal not in fact.body:
            raise ValueError(f"reference fact {fact.id!r} body does not carry its literal")
        if fact.stale_literal and window_text.count(fact.stale_literal) != 1:
            raise ValueError(f"case {case.id!r} fact {fact.id!r} stale literal is not unique")
    tail_text = "\n".join(message_text(message) for message in _tail(case.messages))
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


def _round_groups(messages: tuple[Message, ...]) -> list[tuple[Message, ...]]:
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
    return rounds


def _tail(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    rounds = _round_groups(messages)
    kept: list[Message] = []
    count = 0
    while rounds and count < COMPACTION_KEEP_MESSAGES:
        count += len(rounds[-1])
        kept = [*rounds[-1], *kept]
        rounds = rounds[:-1]
    return tuple(kept)


def _load_skeletons(
    transcripts: Path,
) -> tuple[tuple[CorpusFile, ...], dict[str, tuple[Message, ...]]]:
    pinned: list[CorpusFile] = []
    decoded: dict[str, tuple[Message, ...]] = {}
    for name in sorted({name for pair in REAL_SKELETONS.values() for name in pair}):
        path = transcripts / f"{name}{SKELETON_SUFFIX}"
        if not path.exists():
            raise ValueError(f"real-case skeleton missing: {path}")
        messages = tuple(_scrub_message(m) for m in decode(path.read_bytes()).messages)
        payload = "\n".join(message_text(m) for m in messages).encode()
        pinned.append(
            CorpusFile(path=path.name, sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}")
        )
        decoded[name] = messages
    return tuple(pinned), decoded


def _scrub(text: str) -> str:
    for pattern, replacement in SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _scrub_message(message: Message) -> Message:
    if isinstance(message.content, str):
        return Message(role=message.role, content=_scrub(message.content))
    blocks: list[ContentBlock] = []
    for block in message.content:
        match block:
            case TextBlock(text=text):
                blocks.append(TextBlock(text=_scrub(text)))
            case ImageBlock():
                blocks.append(TextBlock(text=IMAGE_MARKER))
            case ToolResultBlock(tool_use_id=tid, content=str(content)):
                blocks.append(ToolResultBlock(tool_use_id=tid, content=_scrub(content)))
            case ToolResultBlock(tool_use_id=tid, content=tuple(parts)):
                blocks.append(
                    ToolResultBlock(
                        tool_use_id=tid,
                        content=tuple(
                            TextBlock(text=_scrub(part.text))
                            if isinstance(part, TextBlock)
                            else TextBlock(text=IMAGE_MARKER)
                            for part in parts
                        ),
                    )
                )
            case ToolUseBlock(id=call_id, name=name, input=arguments):
                blocks.append(
                    ToolUseBlock(
                        id=call_id,
                        name=name,
                        input=json.loads(_scrub(json.dumps(arguments))),
                    )
                )
            case _:
                blocks.append(block)
    return Message(role=message.role, content=tuple(blocks))


@dataclass(frozen=True)
class RealCaseBuild:
    """The real-leaf cases: two pinned agent transcripts composed into one full-scale window, with
    the graded exchanges spliced in at round boundaries. Skeletons contribute the texture — real
    plans, tool traffic, file writes — and every graded fact stays a collision-checked literal, so
    grading is unchanged. The runner reports these cases without applying a pass bar."""

    skeletons: dict[str, tuple[Message, ...]]
    target_tokens: int

    def build(self) -> tuple[CompactionCase, ...]:
        cases = (self._plan_reversal(), self._version_churn(), self._poisoned_recap())
        for case in cases:
            if estimate_tokens(case.messages) < self.target_tokens:
                raise ValueError(f"real case {case.id!r} fell short of {self.target_tokens} tokens")
            _verify_literals(case)
        return cases

    def _plan_reversal(self) -> CompactionCase:
        rounds = self._base("real-plan-reversal")
        value = _value_source("real-plan-reversal", rounds)
        steps = (
            ("rollout batch size", "conversations"),
            ("canary hold period", "minutes"),
            ("writeback batch window", "milliseconds"),
            ("index refresh interval", "seconds"),
            ("canary sample rate", "checks per hour"),
            ("reconciliation table row cap", "rows"),
        )
        originals = [value() for _ in steps]
        revised_window = value()
        revised_rate = value()
        controls = [value(), value()]
        plan_lines = "\n".join(
            f"- step {index + 1}, {subject}: {literal} {unit}"
            for index, ((subject, unit), literal) in enumerate(zip(steps, originals, strict=True))
        )
        inserts = (
            (
                0.10,
                _exchange(
                    "Before you go further, here is the rollout plan we are executing. "
                    f"Keep every step's number straight:\n{plan_lines}",
                    "Plan recorded — six steps with their targets. Continuing.",
                ),
            ),
            (
                0.45,
                _exchange(
                    "Two changes after the sync: the writeback batch window moves to "
                    f"{revised_window} milliseconds, and the canary sample rate moves to "
                    f"{revised_rate} checks per hour. Also drop step 6 entirely — we are not "
                    "building the reconciliation table; fold that work into the export job.",
                    "Updated: new batch window and sample rate recorded, step 6 dropped.",
                ),
            ),
            (
                0.75,
                _exchange(
                    "On the writeback batch window — go back to what you first proposed in the "
                    "original plan; keep it simple. The step-6 decision from the sync stands.",
                    "Reverted the batch window to the original plan value; step 6 stays dropped.",
                ),
            ),
        )
        facts = (
            _real_fact("real-plan-reversal", "decision", 0, originals[0], weight=3),
            _real_fact("real-plan-reversal", "decision", 1, originals[1], weight=3),
            _real_fact("real-plan-reversal", "decision", 2, originals[3], weight=4),
            _real_fact(
                "real-plan-reversal",
                "superseded",
                0,
                originals[2],
                stale=revised_window,
                weight=5,
            ),
            _real_fact(
                "real-plan-reversal", "superseded", 1, revised_rate, stale=originals[4], weight=4
            ),
            _real_fact("real-plan-reversal", "distractor", 0, originals[5]),
            _real_fact("real-plan-reversal", "control", 0, controls[0], weight=3),
            _real_fact("real-plan-reversal", "control", 1, controls[1], weight=3),
        )
        probes = (
            CompactionProbe(
                id="recall",
                question=(
                    "What rollout batch size did the plan lock in? Answer with the exact number."
                ),
                expect_literals=(originals[0],),
            ),
            CompactionProbe(
                id="reversal",
                question=(
                    "What writeback batch window stands right now, after every revision? "
                    "Give only the value that stands."
                ),
                expect_literals=(originals[2],),
                forbid_literals=(revised_window,),
            ),
            CompactionProbe(
                id="revision",
                question=(
                    "What is the current canary sample rate? Give only the value that stands."
                ),
                expect_literals=(revised_rate,),
                forbid_literals=(originals[4],),
            ),
            CompactionProbe(
                id="deleted",
                question=(
                    "Are we still building the reconciliation table from the plan, and at what "
                    "row cap if so?"
                ),
                forbid_literals=(originals[5],),
            ),
            CompactionProbe(
                id="control",
                question=(
                    "What export retention floor did we just record? Answer with the exact number."
                ),
                expect_literals=(controls[0],),
            ),
        )
        return self._case("real-plan-reversal", rounds, inserts, facts, probes, controls)

    def _version_churn(self) -> CompactionCase:
        rounds = self._base("real-version-churn")
        value = _value_source("real-version-churn", rounds)
        figures = [value() for _ in range(7)]
        ticket_keeper = value()
        ticket_pretender = value()
        controls = [value(), value()]
        weights = (3, 5, 2, 4, 1, 1, 1)
        version_inserts = tuple(
            (
                0.12 + index * 0.09,
                _write_round("real-version-churn", index + 1, figures[index]),
            )
            for index in range(7)
        )
        inserts = (
            *version_inserts,
            (
                0.35,
                _exchange(
                    "For the record: briefing_v2.md is the copy legal approved, under change "
                    f"ticket {ticket_keeper}. Everything after v2 is exploratory until I say "
                    "otherwise.",
                    "Noted — v2 carries the standing approval; later drafts are exploratory.",
                ),
            ),
            (
                0.60,
                _exchange(
                    f"Scratch that — legal moved the sign-off to briefing_v4.md under ticket "
                    f"{ticket_pretender}. Treat v4 as the approved copy.",
                    "Updated — v4 now carries the approval.",
                ),
            ),
            (
                0.70,
                _exchange(
                    "Legal pulled the v4 approval this morning; the original v2 sign-off "
                    "stands. Do not cite the newer ticket anywhere.",
                    "Reverted — v2 is the approved briefing again, under its original ticket.",
                ),
            ),
        )
        facts = (
            *(
                _real_fact(
                    "real-version-churn",
                    "reference",
                    index,
                    figures[index],
                    weight=weights[index],
                    path=_briefing_path(index + 1),
                    body=(
                        f"# Executive briefing, draft v{index + 1}\n\n"
                        f"Headline figure for this revision: {figures[index]} committed "
                        "units across the program.\n"
                        "Remaining sections summarize the sourcing plan and the open "
                        "risks register.\n"
                    ),
                )
                for index in range(7)
            ),
            _real_fact(
                "real-version-churn",
                "superseded",
                0,
                ticket_keeper,
                stale=ticket_pretender,
                weight=5,
            ),
            _real_fact("real-version-churn", "control", 0, controls[0], weight=3),
            _real_fact("real-version-churn", "control", 1, controls[1], weight=3),
        )
        probes = (
            CompactionProbe(
                id="authority",
                question=(
                    "Which change ticket covers the briefing approval that stands right now? "
                    "Give only the ticket number that stands."
                ),
                expect_literals=(ticket_keeper,),
                forbid_literals=(ticket_pretender,),
            ),
            CompactionProbe(
                id="reread",
                question=(
                    "What headline figure does the approved briefing carry? "
                    "Read the approved copy if you need to."
                ),
                expect_literals=(figures[1],),
                expect_read_path=_briefing_path(2),
            ),
            CompactionProbe(
                id="recall",
                question=("What headline figure did briefing_v1.md carry? Read it if you need to."),
                expect_literals=(figures[0],),
                expect_read_path=_briefing_path(1),
            ),
            CompactionProbe(
                id="control",
                question=(
                    "What export retention floor did we just record? Answer with the exact number."
                ),
                expect_literals=(controls[0],),
            ),
        )
        return self._case("real-version-churn", rounds, inserts, facts, probes, controls)

    def _poisoned_recap(self) -> CompactionCase:
        rounds = self._base("real-poisoned-recap")
        value = _value_source("real-poisoned-recap", rounds)
        subjects = (
            ("bid item unit price floor", "dollars"),
            ("estimate contingency margin", "basis points"),
            ("quantity takeoff tolerance", "units"),
            ("submittal review window", "hours"),
            ("retainage release threshold", "dollars"),
            ("crew mobilization budget", "dollars"),
            ("materials escalation reserve", "dollars"),
            ("closeout punch-list cap", "items"),
        )
        stable = [value() for _ in subjects]
        recap_review = value()
        recap_reserve = value()
        corrected_review = value()
        corrected_reserve = value()
        controls = [value(), value()]
        statement_inserts = tuple(
            (
                0.12 + index * 0.05,
                _exchange(
                    DECISION_POOL[index % len(DECISION_POOL)].format(
                        subject=subject, value=f"{stable[index]} {unit}"
                    ),
                    ACK_POOL[index % len(ACK_POOL)],
                )
                if index not in (3, 6)
                else _exchange(
                    f"Set the {subject} to a working value for now; I will confirm the final "
                    "number before the recap.",
                    ACK_POOL[index % len(ACK_POOL)],
                ),
            )
            for index, (subject, unit) in enumerate(subjects)
        )
        inserts = (
            *statement_inserts,
            (
                0.55,
                _exchange(
                    "Where do we stand? Give me the rundown before we continue.",
                    "Here's where we stand on the recorded numbers. The floors and caps we "
                    "locked earlier all stand as stated. On the two open items: the submittal "
                    f"review window is set at {recap_review} hours, and the materials "
                    f"escalation reserve is set at {recap_reserve} dollars. Nothing else "
                    "changed. Continuing from here.",
                ),
            ),
            (
                0.65,
                _exchange(
                    f"Final call on the submittal review window: {corrected_review} hours — "
                    "the number from the rundown was the stale working figure.",
                    "Corrected — the review window from the rundown is superseded.",
                ),
            ),
            (
                0.80,
                _exchange(
                    f"And the materials escalation reserve settles at {corrected_reserve} "
                    "dollars, not what the rundown said.",
                    "Corrected — the reserve from the rundown is superseded.",
                ),
            ),
        )
        facts = (
            *(
                _real_fact(
                    "real-poisoned-recap", "decision", index, stable[index], weight=1 + index % 5
                )
                for index in (0, 1, 2, 4, 5, 7)
            ),
            _real_fact(
                "real-poisoned-recap",
                "superseded",
                0,
                corrected_review,
                stale=recap_review,
                weight=5,
            ),
            _real_fact(
                "real-poisoned-recap",
                "superseded",
                1,
                corrected_reserve,
                stale=recap_reserve,
                weight=4,
            ),
            _real_fact("real-poisoned-recap", "control", 0, controls[0], weight=3),
            _real_fact("real-poisoned-recap", "control", 1, controls[1], weight=3),
        )
        probes = (
            CompactionProbe(
                id="poisoned-review",
                question=(
                    "What submittal review window stands now? Give only the value that stands "
                    "after any corrections."
                ),
                expect_literals=(corrected_review,),
                forbid_literals=(recap_review,),
            ),
            CompactionProbe(
                id="poisoned-reserve",
                question=(
                    "What materials escalation reserve stands now? Give only the value that "
                    "stands after any corrections."
                ),
                expect_literals=(corrected_reserve,),
                forbid_literals=(recap_reserve,),
            ),
            CompactionProbe(
                id="recall",
                question=(
                    "What bid item unit price floor did we record? Answer with the exact number."
                ),
                expect_literals=(stable[0],),
            ),
            CompactionProbe(
                id="control",
                question=(
                    "What export retention floor did we just record? Answer with the exact number."
                ),
                expect_literals=(controls[0],),
            ),
        )
        return self._case("real-poisoned-recap", rounds, inserts, facts, probes, controls)

    def _base(self, case_id: str) -> list[tuple[Message, ...]]:
        rounds = [
            unit for name in REAL_SKELETONS[case_id] for unit in _round_groups(self.skeletons[name])
        ]
        ceiling = int(self.target_tokens * REAL_TRIM_FRACTION)
        total = sum(estimate_tokens(unit) for unit in rounds)
        while len(rounds) > 1 and total > ceiling:
            last = estimate_tokens(rounds[-1])
            if total - last < self.target_tokens:
                break
            rounds.pop()
            total -= last
        return rounds

    def _case(
        self,
        case_id: str,
        rounds: list[tuple[Message, ...]],
        inserts: tuple[tuple[float, tuple[Message, ...]], ...],
        facts: tuple[PlantedFact, ...],
        probes: tuple[CompactionProbe, ...],
        controls: list[str],
    ) -> CompactionCase:
        cap = max(1, len(rounds) - REAL_TAIL_GUARD_ROUNDS)
        for fraction, unit in sorted(inserts, key=lambda insert: insert[0], reverse=True):
            rounds.insert(min(int(len(rounds) * fraction), cap), unit)
        tail = (
            Message(role="assistant", content="Pausing the work to log two housekeeping values."),
            Message(
                role="user",
                content=(
                    f"Before you continue: put the export retention floor down as {controls[0]} "
                    f"days, and the audit sample count as {controls[1]} records."
                ),
            ),
            Message(role="assistant", content=CLOSER),
        )
        messages = (*(message for unit in rounds for message in unit), *tail)
        return CompactionCase(
            id=case_id, leaf="real", messages=messages, facts=facts, probes=probes
        )


def _value_source(case_id: str, rounds: list[tuple[Message, ...]]) -> Callable[[], str]:
    text = "\n".join(message_text(message) for unit in rounds for message in unit)
    taken: set[str] = set()
    key = REAL_VALUE_OFFSETS[case_id]

    def next_value() -> str:
        nonlocal key
        while True:
            value = str(VALUE_FLOOR + (key * VALUE_STEP) % VALUE_SPAN)
            key += 1
            if value not in taken and value not in text:
                taken.add(value)
                return value

    return next_value


def _real_fact(
    case_id: str,
    kind: FactKind,
    ordinal: int,
    literal: str,
    stale: str = "",
    weight: int = 3,
    path: str = "",
    body: str = "",
) -> PlantedFact:
    return PlantedFact(
        id=f"{case_id}-{kind}-{ordinal}",
        kind=kind,
        literal=literal,
        stale_literal=stale,
        weight=weight,
        path=path,
        body=body,
    )


def _exchange(user: str, assistant: str) -> tuple[Message, ...]:
    return (
        Message(role="user", content=user),
        Message(role="assistant", content=assistant),
    )


def _briefing_path(version: int) -> str:
    return f"/workspace/deliverables/briefing_v{version}.md"


def _write_round(case_id: str, version: int, figure: str) -> tuple[Message, ...]:
    path = _briefing_path(version)
    call_id = f"{case_id}-write-{version}"
    return (
        Message(
            role="assistant",
            content=(
                TextBlock(text=f"Regenerating the briefing as draft v{version}."),
                ToolUseBlock(
                    id=call_id,
                    name="bash",
                    input={"command": f"briefing-gen --rev {version} > {path}"},
                ),
            ),
        ),
        Message(
            role="user",
            content=(
                ToolResultBlock(
                    tool_use_id=call_id,
                    content=f"generated {path} ({version + 1} sections)",
                ),
            ),
        ),
    )


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
    parser.add_argument(
        "--transcripts",
        type=Path,
        help="directory of exported real transcripts; adds the real-leaf cases",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    digest = SnapshotBuild(
        repo=args.repo,
        out=args.out,
        target_tokens=args.target_tokens,
        transcripts=args.transcripts,
    ).build()
    print(digest)


if __name__ == "__main__":
    main()
