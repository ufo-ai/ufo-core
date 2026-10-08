"""The default index over the test database, scoped to whichever workspace the caller binds, and an
embedder that answers every text with one fixed vector."""

from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex

from ufo.db import workspace_tx
from ufo.runtime.workspace import ws_current


def default_index() -> DefaultIndex:
    return DefaultIndex(transaction=workspace_tx, workspace=lambda: ws_current().workspace_id)


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    """An `EMBED_DIM` vector holding each `(axis, value)` and zero elsewhere."""
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """An `EmbedClient` answering every text with `vector`."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)
