"""Selecting the one context boundary a deploy runs.

The strategy is config, not code: `[context] strategy` names it, every strategy is registered by an
extension at the Manifest `context_boundaries` point, and the selection fails loud rather than
falling back. These pin the selection itself over registered manifests — which spec is chosen, which
tools the turn then offers, and every way the name can be wrong. Core registers no strategy, so a
selection over no manifest at all resolves nothing; the strategies each prove their own boundary in
their own extension's tests.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from ufo_testsupport.models import serving_model

from ufo.blob import FilesystemBlobStore
from ufo.config import DEFAULT_CONTEXT_STRATEGY, Config, load_config
from ufo.harness.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.host.ext.loader import HookChain
from ufo.runtime.context_boundary import (
    BoundaryInputs,
    ContextBoundary,
    boundary_specs,
    context_boundary_tools,
    select_context_boundary,
)
from ufo.runtime.ext.manifest import ContextBoundarySpec, Manifest, NotRegisteredError

CONFIG_BODY = """
[database]
url = "sqlite+aiosqlite:///ufo.db"

[blob]
backend = "filesystem"
root = "./blobs"
"""
COMPACT_STRATEGY = "compact"


class _Boundary:
    """A boundary built by a registered spec, standing in for whatever the strategy's own package
    constructs. The selection is what these pin, so the object only has to be the one the chosen
    spec returned."""

    def __init__(self, strategy: str) -> None:
        self.strategy = strategy


class _Model:
    async def complete(self, _request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="ok")


def _inputs(tmp_path: Path) -> BoundaryInputs:
    return BoundaryInputs(
        serving=serving_model(_Model()),
        blob=FilesystemBlobStore(root=tmp_path),
        conversation_id=uuid4(),
        hooks=HookChain(),
    )


def _config(strategy: str | None = None) -> Config:
    return Config.model_validate(
        {
            "database": {"url": "sqlite+aiosqlite:///ufo.db"},
            "blob": {"backend": "filesystem", "root": "./blobs"},
            **({} if strategy is None else {"context": {"strategy": strategy}}),
        }
    )


def _extension(strategy: str, tools: tuple[str, ...] = (), name: str | None = None) -> Manifest:
    def build(_inputs: BoundaryInputs) -> ContextBoundary:
        return _Boundary(strategy)  # type: ignore[return-value]

    return Manifest(
        name=name or f"ext_{strategy}",
        version="1",
        context_boundaries=(ContextBoundarySpec(strategy=strategy, build=build, tools=tools),),
    )


ROLLOVER_STRATEGY = "rollover"
ROLLOVER_EXTENSION = _extension(
    ROLLOVER_STRATEGY, ("get_context_remaining", "new_context", "search_history")
)
COMPACT_EXTENSION = _extension(COMPACT_STRATEGY, ("get_context_remaining",))
# One boundary per deploy: a pack bundles one strategy's extension, so the stock set carries only
# the default. INSTALLED lists both only to let each test select either name over one registry.
INSTALLED = (ROLLOVER_EXTENSION, COMPACT_EXTENSION)


def test_a_deploy_that_names_no_strategy_runs_the_default_name() -> None:
    spec = select_context_boundary(_config(), INSTALLED)
    assert spec.strategy == DEFAULT_CONTEXT_STRATEGY


def test_the_selected_spec_is_the_one_that_builds_the_boundary(tmp_path: Path) -> None:
    spec = select_context_boundary(_config(COMPACT_STRATEGY), INSTALLED)
    built = spec.build(_inputs(tmp_path))
    assert built.strategy == COMPACT_STRATEGY  # type: ignore[attr-defined]


def test_core_registers_no_strategy_of_its_own() -> None:
    assert boundary_specs(()) == {}
    with pytest.raises(NotRegisteredError, match=DEFAULT_CONTEXT_STRATEGY):
        select_context_boundary(_config())


def test_a_strategy_no_provider_registers_fails_loud() -> None:
    with pytest.raises(NotRegisteredError, match="forgetting"):
        select_context_boundary(_config("forgetting"), INSTALLED)


def test_every_registered_strategy_is_selectable_by_name() -> None:
    manifests = (*INSTALLED, _extension("recall"))
    assert select_context_boundary(_config("recall"), manifests).strategy == "recall"
    assert sorted(boundary_specs(manifests)) == ["compact", "recall", "rollover"]


def test_two_extensions_claiming_one_name_are_refused() -> None:
    with pytest.raises(RuntimeError, match="already registers"):
        boundary_specs((*INSTALLED, _extension(DEFAULT_CONTEXT_STRATEGY, name="shadow")))


@pytest.mark.parametrize("value", ["strategy = 3", 'strategy = ["rollover"]'])
def test_a_strategy_that_is_not_a_name_fails_validation(tmp_path: Path, value: str) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(f"{CONFIG_BODY}\n[context]\n{value}\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_an_unknown_context_key_fails_validation(tmp_path: Path) -> None:
    path = tmp_path / "ufo.toml"
    path.write_text(f"{CONFIG_BODY}\n[context]\nstrategy = 'rollover'\nkeep = 3\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_a_turn_offers_only_the_selected_strategys_tools() -> None:
    rollover = context_boundary_tools(
        select_context_boundary(_config(ROLLOVER_STRATEGY), INSTALLED), INSTALLED
    )
    compact = context_boundary_tools(
        select_context_boundary(_config(COMPACT_STRATEGY), INSTALLED), INSTALLED
    )
    claimed = ("get_context_remaining", "new_context", "search_history")
    assert [name for name in claimed if rollover(name)] == list(claimed)
    assert [name for name in claimed if compact(name)] == ["get_context_remaining"]
    assert rollover("bash") and compact("bash")
