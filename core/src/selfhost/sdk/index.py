"""Public re-export: the vector-index seam an extension implements to contribute a backend.

An extension implements `IndexBackend` — lexical + vector retrieval and reindex over `Chunk`s,
returning `Hit`s under a subject + owner-kind filter, deleting by `IndexScope` — pairs it with a
backend name in an `IndexBackendSpec` (`selfhost.sdk.manifest`), and a deploy selects it by that
name through the `memory.index_backend` config knob. Core builds it at boot with the deploy
`EmbedClient` and a credential reader scoped to the extension's declared slots. The concrete shapes
live in `selfhost.memory.{index,chunk,embed}`, reached only here."""

from selfhost.memory.chunk import (
    Chunk as Chunk,
)
from selfhost.memory.chunk import (
    Hit as Hit,
)
from selfhost.memory.chunk import (
    IndexScope as IndexScope,
)
from selfhost.memory.embed import (
    EmbedClient as EmbedClient,
)
from selfhost.memory.index import (
    IndexBackend as IndexBackend,
)
