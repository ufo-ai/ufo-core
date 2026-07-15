from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type AnswerType = Literal["Single Answer", "Set Answer"]
type PressureBand = Literal["lower", "higher"]
SOURCE_ROWS = 900
SUBSET_SIZE = 100
type Category = Literal[
    "Arts",
    "Arts & Entertainment",
    "Biology",
    "Current Events",
    "Education",
    "Finance & Economics",
    "Geography",
    "Health",
    "History",
    "Linguistics",
    "Media & Entertainment",
    "Other",
    "Politics & Government",
    "Science",
    "Sports",
    "Technology",
    "Travel",
]


class UpstreamAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["deepsearchqa_kaggle_v4"] = "deepsearchqa_kaggle_v4"
    url: str = Field(min_length=1)
    revision: Literal["kaggle-v4"] = "kaggle-v4"
    archive_size_bytes: int = Field(gt=0)
    archive_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    csv_size_bytes: int = Field(gt=0)
    csv_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    license: Literal["Apache-2.0"] = "Apache-2.0"


class PromptSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_anchor_count: int = Field(ge=0)
    stage_marker_count: int = Field(ge=0)
    temporal_anchor_count: int = Field(ge=0)
    numeric_signal_count: int = Field(ge=0)
    exclusion_signal_count: int = Field(ge=0)
    ranking_signal_count: int = Field(ge=0)
    enumeration_signal_count: int = Field(ge=0)
    format_signal_count: int = Field(ge=0)


class SelectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    example_id: int = Field(ge=0, lt=SOURCE_ROWS)
    pressure_band: PressureBand
    category: Category
    problem_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tool_pressure_score: int = Field(ge=0)
    prompt_word_count: int = Field(gt=0)
    signals: PromptSignals


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: Literal["dsqa_prompt_visible_contrast"] = "dsqa_prompt_visible_contrast"
    items: tuple[SelectionItem, ...]


class SnapshotCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    example_id: int = Field(ge=0, lt=SOURCE_ROWS)
    problem: str = Field(min_length=1)
    problem_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    category: Category
    answer: str = Field(min_length=1)
    answer_type: AnswerType
    pressure_band: PressureBand
    tool_pressure_score: int = Field(ge=0)
    prompt_word_count: int = Field(gt=0)
    signals: PromptSignals


class SnapshotFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Literal["cases.jsonl.gz"] = "cases.jsonl.gz"
    records: int = Field(default=SUBSET_SIZE, ge=SUBSET_SIZE, le=SUBSET_SIZE)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["dsqa_100"] = "dsqa_100"
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    builder_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    upstream: UpstreamAsset
    cases: SnapshotFile


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: SnapshotManifest
    cases: tuple[SnapshotCase, ...]
