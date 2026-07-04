"""Public re-export: the vector-index seam an extension implements to contribute a backend.

An extension implements `IndexBackend` — lexical + vector retrieval and reindex over `Chunk`s,
returning `Hit`s under a subject + owner-kind filter, deleting by `IndexScope` — pairs it with a
backend name in an `IndexBackendSpec` (`selfhost.sdk.manifest`), and a deploy selects it by that
name through the `memory.index_backend` config knob. Core builds it at boot with the deploy
`EmbedClient` and the extension's workspace-scoped context. `EmbedClient` is the embedding seam an
extension implements to contribute a batched embed backend selected by `memory.embed_backend`. The
concrete shapes live in `selfhost.indexing`, reached only here."""

from selfhost.indexing import (
    OWNER_KIND_MEMORY_ITEM as OWNER_KIND_MEMORY_ITEM,
)
from selfhost.indexing import (
    OWNER_KIND_PAGE as OWNER_KIND_PAGE,
)
from selfhost.indexing import (
    Chunk as Chunk,
)
from selfhost.indexing import (
    EmbedClient as EmbedClient,
)
from selfhost.indexing import (
    Hit as Hit,
)
from selfhost.indexing import (
    IndexBackend as IndexBackend,
)
from selfhost.indexing import (
    IndexScope as IndexScope,
)
from selfhost.indexing import (
    TextChunker as TextChunker,
)
from selfhost.indexing import (
    chunk_embed_upsert as chunk_embed_upsert,
)
