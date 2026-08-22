"""The digest writer: one report and one reader in, one entry out.

A toolless typed writer, because the report is the whole of what it may read and a tool would let
it read further. The reader is carried in the payload rather than looked up, so the same entry is
reproducible from its inputs alone."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator

from ufo.sdk.delivery_register import DELIVERY_REGISTER_BLOCK

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

"""Two lines under the title. Position is the rank the standard asks for, so the line dropped here
is the weakest finding the report held."""
MAX_POINTS = 2

"""One finding. A title is read in a band of 40 to 60 characters — a reader scanning a column
spends about three words on it — and one finding fits that band, so the ceiling stands just over
it."""
MAX_TITLE_CHARS = 65

"""What a title uses to carry a second finding. The standard gives the title the first-ranked
finding alone, so the mark is where the title ends: the writer ranked what stands before it, and
the ceiling is left to bound one finding rather than to choose between two. A title cut at the
ceiling instead loses whichever words fall past it, which reads as neither finding."""
TITLE_JOIN = ";"

"""One clause. The longest single clause a report has needed is 87 characters and the shortest
two-clause summary written under a wider ceiling was 101, so this ceiling admits every clause the
summary is read for and refuses the second one."""
MAX_SUMMARY_CHARS = 96

MAX_POINT_CHARS = 80
MAX_ACTOR_CHARS = 60

STOPWORDS = frozenset(
    """a an the and or but of to in on at for with by from as is are was were be been being
    this that these those it its their his her our your not no nor so than then while when where
    which who whom what into over under across per each every all any some more most less least
    up down out off still also just only very much many few both either neither yet""".split()
)

WORD_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")

SUFFIXES = (
    "ations",
    "ation",
    "ings",
    "ing",
    "ions",
    "ion",
    "ments",
    "ment",
    "ances",
    "ance",
    "ences",
    "ence",
    "ives",
    "ive",
    "edly",
    "ed",
    "es",
    "s",
    "ly",
    "al",
)

STEM_CHARS = 5
MIN_STEM_CHARS = 4

"""How much of a line has to be new for the line to be worth its row. Measured over a feed written
without the rule: the least novel line that still carried a number the reader needed was a third
new, and the most novel line that carried nothing the reader had not already read was under a
third. A line at or under this share says what an earlier line of its own entry said — in the same
words, or in wider ones — and reading it costs the reader a row and returns nothing."""
MIN_NOVEL_SHARE = 0.30


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


def _stem(word: str) -> str:
    root = word.rsplit("-", 1)[-1]
    if len(root) < MIN_STEM_CHARS:
        root = word
    for suffix in SUFFIXES:
        if root.endswith(suffix) and len(root) - len(suffix) >= MIN_STEM_CHARS:
            return root[: -len(suffix)][:STEM_CHARS]
    return root[:STEM_CHARS]


def _content(text: str) -> tuple[str, ...]:
    return tuple(_stem(word) for word in WORD_RE.findall(text.lower()) if word not in STOPWORDS)


def _adds_to(text: str, said: set[str]) -> bool:
    words = _content(text)
    return bool(words) and sum(word not in said for word in words) / len(words) > MIN_NOVEL_SHARE


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
    """One report as the shortest thing that makes a reader open it. `holds_a_change` is asked
    first and answered from the report alone — a state that changed, a number that moved, a name
    that is new, a date that is coming — so a report that finds what yesterday's found is answered
    with the judgement and nothing else, and costs the reader no row."""

    holds_a_change: bool
    title: str = ""
    summary: str = ""
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

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _clipped(value.split(TITLE_JOIN, 1)[0], MAX_TITLE_CHARS)

    @field_validator("summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        return _clipped(value, MAX_SUMMARY_CHARS)

    @model_validator(mode="after")
    def _titled(self) -> "DigestEntry":
        if self.holds_a_change and not self.title:
            raise ValueError("an entry holding a change carries a title")
        return self

    @model_validator(mode="after")
    def _said_once(self) -> "DigestEntry":
        """The entry read against itself, in the order the reader reads it. A field survives on
        what it carries that no field above it carried; one that clears the bar on nothing is the
        row above in other words, and the reader spends a line to learn what they already know.

        The lines are ranked, so the surviving lines are cut from the end: an entry keeps its two
        best distinct findings rather than its first two written."""
        said = set(_content(self.title))
        if not _adds_to(self.summary, said):
            self.summary = ""
        said.update(_content(self.summary))
        kept: list[DigestPoint] = []
        for point in self.points:
            if _adds_to(point.text, said):
                said.update(_content(point.text))
                kept.append(point)
                if len(kept) == MAX_POINTS:
                    break
        self.points = tuple(kept)
        return self


def bounded(report: str) -> str:
    """The report as the writer receives it, cut at the ceiling the payload is billed against."""
    return report[:MAX_REPORT_CHARS]


def writing_standard() -> str:
    """What the model is told: the writer's own instruction, the house delivery register, then the
    skill that holds the standard. The skill is the one copy — a member's agent loads exactly these
    words with `load_skill`, and carries the register already in its own shell, so the entry a job
    writes and the entry an agent writes answer to identical rules. The frontmatter is routing
    metadata, not instruction, and is dropped the way `load_skill` drops it."""
    document = (SKILL_DIR / "SKILL.md").read_text()
    instructions = document.split(FRONTMATTER_FENCE, 2)[2].strip()
    return "\n\n".join(
        (
            (PROMPTS / "subagent_digest.md").read_text().strip(),
            DELIVERY_REGISTER_BLOCK,
            instructions,
        )
    )
