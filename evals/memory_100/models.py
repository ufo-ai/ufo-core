from typing import Literal

from pydantic import BaseModel, Field

type Corpus = Literal["enterprise", "longmem", "ufo"]
type ItemClass = Literal["fact", "episodic", "semantic"]
type MemoryKind = Literal["fact", "preference", "decision", "event", "task"]


class UpstreamAsset(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    license: str = Field(min_length=1)


class SnapshotCase(BaseModel):
    id: str = Field(min_length=1)
    corpus: Corpus
    category: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    answer_facts: tuple[str, ...] = ()


class SnapshotPage(BaseModel):
    source_ref: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    body: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    origin: str = Field(min_length=1)


class SnapshotMemory(BaseModel):
    source_ref: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    body: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    item_class: ItemClass = "fact"
    memory_kind: MemoryKind = "fact"
    confidence: int = Field(default=5, ge=1, le=10)


class SnapshotFile(BaseModel):
    path: str = Field(pattern=r"^[a-z0-9_.-]+$")
    records: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SnapshotManifest(BaseModel):
    name: Literal["memory_100"] = "memory_100"
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    builder_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    upstreams: tuple[UpstreamAsset, ...]
    cases: SnapshotFile
    pages: SnapshotFile
    memories: SnapshotFile


class Snapshot(BaseModel):
    manifest: SnapshotManifest
    cases: tuple[SnapshotCase, ...]
    pages: tuple[SnapshotPage, ...]
    memories: tuple[SnapshotMemory, ...]
