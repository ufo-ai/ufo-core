from collections.abc import Iterator

import pytest
from openfeature.provider.in_memory_provider import InMemoryProvider
from ufo_ext_flags_open import build

from ufo.flags import init_flags


@pytest.fixture(autouse=True)
def flags_open() -> Iterator[None]:
    """Every flag on, as a dev or eval stack reads them, so the gated paths run; a test that needs
    the flag off binds its own provider inside."""
    init_flags(build(0.0, {}))
    yield
    init_flags(InMemoryProvider({}))
