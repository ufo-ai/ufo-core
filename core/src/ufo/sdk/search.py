"""Public re-export: the search seam an extension implements (a backend) or a tool calls.

An extension pairs a `SearchProviderSpec` (`ufo.sdk.manifest`) with a backend name a deploy
selects through `config.research.search_provider`, and implements `SearchProvider` — answering a
`SearchQuery` with `SearchResults` and, when `supports_fetch` is set, a `FetchRequest` with a
`FetchedPage`. The concrete seam lives in `ufo.runtime.search`, reached only here."""

from ufo.runtime.search import (
    FetchedPage as FetchedPage,
)
from ufo.runtime.search import (
    FetchRequest as FetchRequest,
)
from ufo.runtime.search import (
    SearchHit as SearchHit,
)
from ufo.runtime.search import (
    SearchProvider as SearchProvider,
)
from ufo.runtime.search import (
    SearchQuery as SearchQuery,
)
from ufo.runtime.search import (
    SearchResults as SearchResults,
)
