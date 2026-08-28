from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
TaskName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UpstreamMetadata(BoundaryModel):
    repository: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$")
    dataset: str = Field(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$")
    revision: int = Field(gt=0)
    digest: Digest
    harbor_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    tasks: tuple[TaskName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selected_tasks(self) -> "UpstreamMetadata":
        duplicates = sorted(name for name in set(self.tasks) if self.tasks.count(name) > 1)
        if duplicates:
            raise ValueError(f"duplicate selected task ids: {', '.join(duplicates)}")
        return self
