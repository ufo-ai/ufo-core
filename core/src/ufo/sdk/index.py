"""Public re-export: the vector-index seam an extension implements to contribute a backend.

An extension implements `IndexBackend` — lexical + vector retrieval over `Chunk`s, returning
`Hit`s under a subject + owner-kind filter, deleting by `IndexScope` — pairs it with a
backend name in an `IndexBackendSpec` (`ufo.sdk.manifest`), and a deploy selects it by that
name through the `memory.index_backend` config knob. Core builds it at boot with the extension's
workspace-scoped context. `EmbedClient` is the embedding seam an extension implements to contribute
a batched embed backend selected by `memory.embed_backend`. The concrete shapes live in
`ufo.indexing`, reached only here."""

from ufo.indexing import (
    OWNER_KIND_MEMORY_ITEM as OWNER_KIND_MEMORY_ITEM,
)
from ufo.indexing import (
    OWNER_KIND_PAGE as OWNER_KIND_PAGE,
)
from ufo.indexing import (
    Chunk as Chunk,
)
from ufo.indexing import (
    EmbedClient as EmbedClient,
)
from ufo.indexing import (
    Hit as Hit,
)
from ufo.indexing import (
    IndexBackend as IndexBackend,
)
from ufo.indexing import (
    IndexScope as IndexScope,
)
from ufo.indexing import (
    TextChunker as TextChunker,
)
from ufo.indexing import (
    chunk_embed_upsert as chunk_embed_upsert,
)
