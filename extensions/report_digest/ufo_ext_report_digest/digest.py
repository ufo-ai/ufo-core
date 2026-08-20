"""The digest writer: one report and one reader in, one entry out.

A toolless typed writer, because the report is the whole of what it may read and a tool would let
it read further. The reader is carried in the payload rather than looked up, so the same entry is
reproducible from its inputs alone."""

import json
from pathlib import Path

from pydantic import BaseModel, field_validator

SKILL_NAME = "report-digest"
SKILL_DIR = Path(__file__).parent / "skills" / SKILL_NAME
PROMPTS = Path(__file__).parent / "prompts"

FINISH_TOOL = "finish"
FINISH_DESCRIPTION = "Deliver the digest entry."
MAX_OUTPUT_TOKENS = 1_024

"""How much of a report reaches the writer. A report opens with what it found and argues it
after; past this the entry is being written from the argument rather than the finding, and the
payload is one an external API is billed for."""
MAX_REPORT_CHARS = 24_000

FRONTMATTER_FENCE = "---"

MAX_POINTS = 3
MAX_TITLE_CHARS = 90
MAX_SUMMARY_CHARS = 130
MAX_POINT_CHARS = 80
MAX_ACTOR_CHARS = 60


def _clipped(value: str, ceiling: int) -> str:
    """A prose field held to its ceiling by cutting it, never by refusing it. The model is told its
    budget in words and mostly keeps it; a schema that rejects the overrun instead throws away the
    whole entry — every other field with it — over a clause the reader would never have seen,
    because the column cuts the line long before this does.

    The cut lands on a word boundary: a line ending mid-word reads as a rendering fault, which is a
    worse defect than the sentence being one clause shorter."""
    said = value.strip()
    if len(said) <= ceiling:
        return said
    return said[:ceiling].rsplit(" ", 1)[0].rstrip(",;:—- ")


class DigestPoint(BaseModel):
    """One finding as one line, and whoever the report says did it. There is no second half: a
    qualifying clause is the reader's reason to open the report, not the digest's to spend a line
    on, so the line carries its own number and stops."""

    text: str
    actor: str = ""

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        return _clipped(value, MAX_POINT_CHARS)

    @field_validator("actor")
    @classmethod
    def _actor(cls, value: str) -> str:
        return _clipped(value, MAX_ACTOR_CHARS)


class DigestEntry(BaseModel):
    title: str
    summary: str
    points: tuple[DigestPoint, ...] = ()

    @field_validator("points", mode="before")
    @classmethod
    def _decoded(cls, value: object) -> object:
        """A provider that hands back a nested array as its JSON text rather than as an array. The
        payload is the model's, so it is decoded here rather than trusted or refused."""
        if isinstance(value, str):
            decoded = json.loads(value)
            return decoded.get("points", decoded) if isinstance(decoded, dict) else decoded
        return value

    @field_validator("points")
    @classmethod
    def _kept(cls, value: tuple[DigestPoint, ...]) -> tuple[DigestPoint, ...]:
        return value[:MAX_POINTS]

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _clipped(value, MAX_TITLE_CHARS)

    @field_validator("summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        return _clipped(value, MAX_SUMMARY_CHARS)


def bounded(report: str) -> str:
    """The report as the writer receives it, cut at the ceiling the payload is billed against."""
    return report[:MAX_REPORT_CHARS]


def writing_standard() -> str:
    """What the model is told: the writer's own instruction, then the skill that holds the standard.
    The skill is the one copy — a member's agent loads exactly these words with `load_skill`, so the
    entry a job writes and the entry an agent writes answer to the same rules. The frontmatter is
    routing metadata, not instruction, and is dropped the way `load_skill` drops it."""
    document = (SKILL_DIR / "SKILL.md").read_text()
    instructions = document.split(FRONTMATTER_FENCE, 2)[2].strip()
    return (PROMPTS / "subagent_digest.md").read_text().strip() + "\n\n" + instructions
