from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParquetColumn(BoundaryModel):
    name: str = Field(min_length=1)
    arrow_type: str = Field(min_length=1)


class UpstreamParquet(BoundaryModel):
    repository: Literal["openai/gdpval"] = "openai/gdpval"
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    relative_path: Literal["data/train-00000-of-00001.parquet"] = (
        "data/train-00000-of-00001.parquet"
    )
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=DIGEST_PATTERN)
    rows: Literal[220] = 220
    columns: tuple[ParquetColumn, ...]
    license: Literal["NOASSERTION"] = "NOASSERTION"


class RubricItem(BoundaryModel):
    score: int = Field(ge=-100, le=100)
    criterion: str = Field(min_length=1)
    required: None = None
    rubric_item_id: str = Field(pattern=UUID_PATTERN)
    author_type: Literal["human"] = "human"
    tags: tuple[str, ...]
    read_only: None = None
    form_content: None = None

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: int) -> int:
        if value == 0:
            raise ValueError("rubric score must not be zero")
        return value


class SnapshotAsset(BoundaryModel):
    relative_path: str = Field(min_length=1)
    snapshot_path: str | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_materialization(self) -> "SnapshotAsset":
        values = (self.snapshot_path, self.size_bytes, self.sha256)
        if any(value is None for value in values) != all(value is None for value in values):
            raise ValueError("snapshot asset materialization fields must be complete or absent")
        return self


class SnapshotCase(BoundaryModel):
    task_id: str = Field(pattern=UUID_PATTERN)
    sector: str = Field(min_length=1)
    occupation: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    references: tuple[SnapshotAsset, ...]
    deliverables: tuple[SnapshotAsset, ...]
    rubric: tuple[RubricItem, ...] = Field(min_length=1)


class SnapshotFile(BoundaryModel):
    path: Literal["cases.jsonl.gz"] = "cases.jsonl.gz"
    records: Literal[220] = 220
    sha256: str = Field(pattern=DIGEST_PATTERN)


class SnapshotManifest(BoundaryModel):
    name: Literal["gdpval_100"] = "gdpval_100"
    digest: str = Field(pattern=DIGEST_PATTERN)
    upstream: UpstreamParquet
    cases: SnapshotFile
    materialized_case_ids: tuple[str, ...]


class Snapshot(BoundaryModel):
    root: str = Field(min_length=1)
    manifest: SnapshotManifest
    cases: tuple[SnapshotCase, ...]
