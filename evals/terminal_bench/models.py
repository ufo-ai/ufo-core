from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
TaskName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReleaseAsset(BoundaryModel):
    url: str = Field(pattern=r"^https://")
    size_bytes: int = Field(gt=0)
    sha256: Digest


class SelectedTask(BoundaryModel):
    name: TaskName
    digest: Digest
    restrict_agent_network: bool


class UpstreamMetadata(BoundaryModel):
    repository: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$")
    release_tag: str = Field(min_length=1)
    asset: ReleaseAsset
    harbor_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    tasks: tuple[SelectedTask, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selected_tasks(self) -> "UpstreamMetadata":
        names = [task.name for task in self.tasks]
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        if duplicates:
            raise ValueError(f"duplicate selected task ids: {', '.join(duplicates)}")
        return self


class DatasetAuthor(BoundaryModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=1)


class DatasetMetadata(BoundaryModel):
    name: str = Field(min_length=1)
    description: str = ""
    keywords: tuple[str, ...] = ()
    authors: tuple[DatasetAuthor, ...] = ()


class DatasetTask(BoundaryModel):
    name: str = Field(pattern=r"^terminal-bench/[a-z0-9]+(?:-[a-z0-9]+)*$")
    digest: Digest

    @property
    def task_id(self) -> str:
        return self.name.removeprefix("terminal-bench/")


class DatasetManifest(BoundaryModel):
    dataset: DatasetMetadata
    tasks: tuple[DatasetTask, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_task_ids(self) -> "DatasetManifest":
        task_ids = [task.task_id for task in self.tasks]
        duplicates = sorted(task_id for task_id in set(task_ids) if task_ids.count(task_id) > 1)
        if duplicates:
            raise ValueError(f"duplicate task ids: {', '.join(duplicates)}")
        return self
