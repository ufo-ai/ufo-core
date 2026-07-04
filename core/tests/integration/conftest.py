"""Live end-to-end tests: each integrates against a REAL dependency — a live model, a real Docker
container — never a fake (CLAUDE.md testing doctrine). Everything under this directory is stamped
`integration` and `serial`: the DBOS executor is a process singleton and a live container/database
is shared, so these run one at a time, and a plain `uv run pytest` (no xdist) is already serial.

Gating mirrors metalcraft's `tests/integration` env-gating: a test skips with a clear reason when
its dependency is absent, so the suite stays collectable everywhere (CI runs `--collect-only -m
integration` as a rot-guard) and only executes where the dependency is present. Each test module
declares its own gate as a module-level `pytestmark` skipif — `ANTHROPIC_API_KEY` for the live-model
turn, a `docker` daemon for the sandbox turn — beside the `docker` marker where it needs one. The db
and DBOS fixtures (`db`, `dbos_launched`) come from the parent `core/tests/conftest.py`."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Stamp every test in this directory `integration` + `serial` so `-m integration` selects the
    whole live suite and `-m "not integration"` excludes it, without each module repeating the two
    markers — the per-module `pytestmark` then adds only the dependency gate it actually needs."""
    for item in items:
        if "tests/integration/" in item.nodeid or "tests\\integration\\" in item.nodeid:
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.serial)
