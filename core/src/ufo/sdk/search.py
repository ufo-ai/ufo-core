"""Public re-export: the search seam an extension implements (a backend) or a tool calls.

An extension pairs a `SearchProviderSpec` (`ufo.sdk.manifest`) with a backend name a deploy
selects through `config.research.search_provider`, and implements `SearchProvider` — answering a
`SearchQuery` with `SearchResults` and a `FetchRequest` with a `FetchedPage`, or raising
`SearchUnsupported` when it cannot fetch. The concrete seam lives in `ufo.search`, reached only
here."""

from ufo.search import (
    FetchedPage as FetchedPage,
)
from ufo.search import (
    FetchRequest as FetchRequest,
)
from ufo.search import (
    SearchHit as SearchHit,
)
from ufo.search import (
    SearchProvider as SearchProvider,
)
from ufo.search import (
    SearchQuery as SearchQuery,
)
from ufo.search import (
    SearchResults as SearchResults,
)
from ufo.search import (
    SearchUnsupported as SearchUnsupported,
)
