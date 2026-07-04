"""Public re-export: the search seam an extension implements (a backend) or a tool calls.

An extension pairs a `SearchProviderSpec` (`selfhost.sdk.manifest`) with a backend name a deploy
selects through `config.research.search_provider`, and implements `SearchProvider` — answering a
`SearchQuery` with `SearchResults` and a `FetchRequest` with a `FetchedPage`, or raising
`SearchUnsupported` when it cannot fetch. The concrete seam lives in `selfhost.search`, reached only
here."""

from selfhost.search import (
    FetchedPage as FetchedPage,
)
from selfhost.search import (
    FetchRequest as FetchRequest,
)
from selfhost.search import (
    SearchHit as SearchHit,
)
from selfhost.search import (
    SearchProvider as SearchProvider,
)
from selfhost.search import (
    SearchQuery as SearchQuery,
)
from selfhost.search import (
    SearchResults as SearchResults,
)
from selfhost.search import (
    SearchUnsupported as SearchUnsupported,
)
