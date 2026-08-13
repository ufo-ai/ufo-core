"""Replay-safe serialization for everything DBOS persists.

DBOS writes workflow inputs, step outputs, and terminal errors to the system database and reads
them back on crash recovery — across deploys, so the build replaying a recording is not the build
that wrote it. A plain pickle restores a pydantic model's field dict verbatim: construction never
runs, so a field added between the two builds is missing from the restored object and the first
read of it raises AttributeError mid-replay, failing the recovered turn. `ReplaySafeSerializer`
keeps the pickle format but records every `BaseModel` as its class plus its field values, rebuilt
through `model_validate` on load — a missing field takes the class's current default, a dropped
one is ignored, and a missing field without a default fails by name at the read instead of as an
AttributeError deep in the loop. Rows recorded before this serializer carry the default
serializer's name and keep loading through it; `_rebuild` is referenced by name from every
recording made here, so it must keep this module and name for as long as those rows replay.
"""

import base64
import io
import pickle
from collections.abc import Callable

from dbos import DBOSClient, Serializer
from pydantic import BaseModel

SERIALIZATION_NAME = "ufo_pickle"


def replay_safe_client(system_database_url: str) -> DBOSClient:
    """The one way this repo constructs a DBOSClient: rows are recorded under this serializer's
    name, so a client built without it cannot decode what the engine wrote — DBOS degrades the
    read to the raw serialized string instead of raising."""
    return DBOSClient(system_database_url=system_database_url, serializer=ReplaySafeSerializer())


def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel:
    return model_class.model_validate(fields)


class _ModelPickler(pickle.Pickler):
    def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]:
        if isinstance(obj, BaseModel):
            return (_rebuild, (type(obj), dict(obj.__dict__)))
        return NotImplemented


class ReplaySafeSerializer(Serializer):
    """The serializer every DBOS launch and client in this repo is configured with."""

    def name(self) -> str:
        return SERIALIZATION_NAME

    def serialize(self, data: object) -> str:
        buffer = io.BytesIO()
        _ModelPickler(buffer).dump(data)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def deserialize(self, serialized_data: str) -> object:
        return pickle.loads(base64.b64decode(serialized_data))
