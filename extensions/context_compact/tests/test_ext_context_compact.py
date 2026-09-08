"""What this extension declares, resolved the way a deploy resolves it.

A deploy runs this boundary by naming it in the toml, so these select `compact` over the
installed entry points rather than over a fixture, and check the spec they get is this package's:
its boundary, its one tool, and prose that promises no reset the boundary cannot honor."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from ufo_ext_context_compact.compaction import Compaction
from ufo_ext_context_compact.manifest import (
    COMPACT_STRATEGY,
    GET_CONTEXT_REMAINING_TOOL,
    NAME,
    manifest,
)
from ufo_ext_context_compact.prompts import CONTEXT_WINDOW_BLOCK
from ufo_testsupport.models import serving_model

from ufo.blob import FilesystemBlobStore
from ufo.config import DEFAULT_CONTEXT_STRATEGY, Config
from ufo.harness.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.host.ext.loader import HookChain, load_manifests
from ufo.runtime.context_boundary import (
    BoundaryInputs,
    context_boundary_tools,
    select_context_boundary,
)

ROLLOVER_TOOLS = ("new_context", "search_history")


class _Model:
    async def complete(self, _request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="ok")


def _config() -> Config:
    return Config.model_validate(
        {
            "database": {"url": "sqlite+aiosqlite:///ufo.db"},
            "blob": {"backend": "filesystem", "root": "./blobs"},
            "context": {"strategy": COMPACT_STRATEGY},
        }
    )


def _inputs(tmp_path: Path) -> BoundaryInputs:
    return BoundaryInputs(
        serving=serving_model(_Model()),
        blob=FilesystemBlobStore(root=tmp_path),
        conversation_id=uuid4(),
        hooks=HookChain(),
    )


def test_the_named_strategy_resolves_to_this_extensions_boundary(tmp_path: Path) -> None:
    installed = load_manifests()
    assert NAME in {declared.name for declared in installed}

    spec = select_context_boundary(_config(), installed)

    assert spec.strategy == COMPACT_STRATEGY
    assert spec.prompt == CONTEXT_WINDOW_BLOCK
    assert spec.tools == (GET_CONTEXT_REMAINING_TOOL,)
    assert isinstance(spec.build(_inputs(tmp_path)), Compaction)


def test_the_stock_default_resolves_to_this_extensions_boundary() -> None:
    """`[context] strategy` defaults to `compact` — compaction stays what a stock deploy runs
    until the toml says otherwise — and nothing in core registers that name: the installed manifests
    must supply it, or a stock deploy boots without a boundary."""
    assert DEFAULT_CONTEXT_STRATEGY == COMPACT_STRATEGY
    installed = load_manifests()
    assert NAME in {declared.name for declared in installed}

    spec = select_context_boundary(
        Config.model_validate(
            {
                "database": {"url": "sqlite+aiosqlite:///ufo.db"},
                "blob": {"backend": "filesystem", "root": "./blobs"},
            }
        ),
        installed,
    )

    assert spec.strategy == COMPACT_STRATEGY
    assert spec.prompt == CONTEXT_WINDOW_BLOCK
    assert spec.tools == (GET_CONTEXT_REMAINING_TOOL,)


def test_a_compact_deploy_never_promises_the_rollover_tools() -> None:
    """This boundary offers neither `new_context` nor `search_history`, so its own note names
    neither and the turn's registry drops both: the prose and the tool set come from one
    strategy."""
    assert manifest().tools == ()

    installed = load_manifests()
    spec = select_context_boundary(_config(), installed)
    offers = context_boundary_tools(spec, installed)
    for tool in ROLLOVER_TOOLS:
        assert tool not in spec.tools
        assert tool not in spec.prompt
        assert not offers(tool)
    assert GET_CONTEXT_REMAINING_TOOL in spec.prompt
