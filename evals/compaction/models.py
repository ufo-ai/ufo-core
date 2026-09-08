"""The compaction snapshot's records: full-scale message windows with a planted-fact manifest.

Every planted fact carries one distinctive value literal, so grading is a deterministic substring
check against the rendered compaction summary or a probe answer — no judge, no variance. The
builder guarantees each literal appears nowhere in the window's filler, so presence after the
boundary can only mean survival."""

import json
from typing import Literal

from pydantic import BaseModel, Field
from ufo_ext_context_compact.compaction import (
    CHARS_PER_TOKEN,
    IMAGE_MARKER,
    IMAGE_TOKEN_ESTIMATE,
    REDACTED_REASONING_MARKER,
)

from ufo.harness.models.interface import (
    ImageBlock,
    Message,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

type CompactionLeaf = Literal[
    "overload", "buried", "supersession", "chain", "reference", "image", "behavior", "real"
]
type FactKind = Literal[
    "decision", "buried", "superseded", "reference", "image", "distractor", "control"
]


class PlantedFact(BaseModel):
    """One fact spliced into a window. `literal` is the checkable value token; a `superseded` fact
    also carries the earlier `stale_literal` its correction replaced; a `reference` fact's literal
    lives only in the offloaded `body` behind `path`, never in the window text."""

    id: str = Field(min_length=1)
    kind: FactKind
    literal: str = Field(min_length=1)
    stale_literal: str = ""
    weight: int = Field(default=3, ge=1, le=5)
    path: str = ""
    body: str = ""


class CompactionProbe(BaseModel):
    """One post-compaction member turn and its deterministic answer criteria. A non-empty
    `expect_read_path` additionally requires the turn's tool trajectory to touch that path."""

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expect_literals: tuple[str, ...] = ()
    forbid_literals: tuple[str, ...] = ()
    expect_read_path: str = ""


class WindowExtension(BaseModel):
    """The fresh rounds appended after one compaction so the next generation refills the window —
    a chain case ships one extension per generation past the first."""

    messages: tuple[Message, ...]


class CompactionCase(BaseModel):
    id: str = Field(min_length=1)
    leaf: CompactionLeaf
    messages: tuple[Message, ...]
    facts: tuple[PlantedFact, ...]
    extensions: tuple[WindowExtension, ...] = ()
    probes: tuple[CompactionProbe, ...] = ()

    @property
    def generations(self) -> int:
        return 1 + len(self.extensions)


class CorpusFile(BaseModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SnapshotFile(BaseModel):
    path: str = Field(pattern=r"^[a-z0-9_.-]+$")
    records: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CompactionManifest(BaseModel):
    """`skeletons` pins the real transcripts the real-leaf cases were composed from — provenance
    for the report, folded into the digest like the corpus."""

    name: Literal["compaction"] = "compaction"
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    builder_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    target_tokens: int = Field(gt=0)
    corpus: tuple[CorpusFile, ...]
    skeletons: tuple[CorpusFile, ...] = ()
    cases: SnapshotFile


class CompactionSnapshot(BaseModel):
    manifest: CompactionManifest
    cases: tuple[CompactionCase, ...]


def estimate_tokens(messages: tuple[Message, ...]) -> int:
    """The window estimate the compaction trigger uses — mirrored exactly, so a built window
    provably crosses the trigger and a report's compression ratios match the pipeline's own."""
    return sum(
        (
            len(message.role)
            + len(message_text(message))
            + _opaque_chars(message)
            + CHARS_PER_TOKEN
            - 1
        )
        // CHARS_PER_TOKEN
        + IMAGE_TOKEN_ESTIMATE * _image_count(message)
        for message in messages
    )


def message_text(message: Message) -> str:
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


def _opaque_chars(message: Message) -> int:
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


def _image_count(message: Message) -> int:
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
