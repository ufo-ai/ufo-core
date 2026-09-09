"""Selecting the one context boundary a turn crosses.

The strategy is config, not code: `[context] strategy` and `[context] flagged_strategy` name the
two, `enable-context-rollover` picks between them per turn, every strategy is registered by an
extension at the Manifest `context_boundaries` point, and the selection fails loud rather than
falling back. These pin the selection itself over registered manifests — which spec is chosen, what
the flag decides, which tools the turn then offers, and every way a name can be wrong. Core
registers no strategy, so a selection over no manifest at all resolves nothing; the strategies each
prove their own boundary in their own extension's tests.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
from pydantic import ValidationError
from ufo_ext_flags_open import FLAG_BACKEND as OPEN_FLAG_BACKEND
from ufo_testsupport.models import serving_model

from ufo.blob import FilesystemBlobStore
from ufo.config import (
    DEFAULT_CONTEXT_STRATEGY,
    DEFAULT_FLAGGED_CONTEXT_STRATEGY,
    Config,
    load_config,
)
from ufo.flags import SERVED_FALSE, SERVED_TRUE, init_flags
from ufo.harness.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.host.ext.loader import HookChain, load_manifests
from ufo.runtime.context_boundary import (
    CONTEXT_ROLLOVER_FLAG,
    BoundaryInputs,
    ContextBoundary,
    boundary_specs,
    context_boundary_tools,
    flagged_context_boundary,
    select_context_boundary,
    select_flagged_context_boundary,
)
from ufo.runtime.ext.manifest import ContextBoundarySpec, Manifest, NotRegisteredError
from ufo.runtime.workspace import ws
from ufo.serve import _select_flag_provider

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


def _config(strategy: str | None = None, flagged: str | None = None) -> Config:
    context = {
        **({} if strategy is None else {"strategy": strategy}),
        **({} if flagged is None else {"flagged_strategy": flagged}),
    }
    return Config.model_validate(
        {
            "database": {"url": "sqlite+aiosqlite:///ufo.db"},
            "blob": {"backend": "filesystem", "root": "./blobs"},
            **({} if not context else {"context": context}),
        }
    )


def _extension(strategy: str, tools: tuple[str, ...] = (), name: str | None = None) -> Manifest:
    def build(_inputs: BoundaryInputs) -> ContextBoundary:
        return _Boundary(strategy)  # type: ignore[return-value]

    return Manifest(
        name=name or f"ext_{strategy}",
        version="1",
        context_boundaries=(
            ContextBoundarySpec(
                strategy=strategy,
                build=build,
                tools=tools,
                prompt=f"<context_window>{strategy}</context_window>",
            ),
        ),
    )


ROLLOVER_STRATEGY = "rollover"
ROLLOVER_EXTENSION = _extension(
    ROLLOVER_STRATEGY, ("get_context_remaining", "new_context", "search_history")
)
COMPACT_EXTENSION = _extension(COMPACT_STRATEGY, ("get_context_remaining",))
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


def _flag_serving(value: str) -> InMemoryProvider:
    return InMemoryProvider(
        {CONTEXT_ROLLOVER_FLAG: InMemoryFlag(default_variant="set", variants={"set": value})}
    )


def test_the_flagged_name_defaults_to_rollover() -> None:
    spec = select_flagged_context_boundary(_config(), INSTALLED)
    assert spec.strategy == DEFAULT_FLAGGED_CONTEXT_STRATEGY
    assert spec.strategy != DEFAULT_CONTEXT_STRATEGY


async def test_the_flag_selects_which_of_the_two_names_a_turn_crosses() -> None:
    """One build, two mechanisms: production serves the flag off and crosses compaction, testing
    serves it on and crosses rollover. The tools and the prompt block ride the spec the flag
    selected, so a turn never crosses one boundary while offering the other's tools."""
    try:
        init_flags(_flag_serving(SERVED_TRUE))
        with ws(uuid4()):
            on = await flagged_context_boundary(_config(), INSTALLED)
        init_flags(_flag_serving(SERVED_FALSE))
        with ws(uuid4()):
            off = await flagged_context_boundary(_config(), INSTALLED)
    finally:
        init_flags(InMemoryProvider({}))

    assert on.strategy == ROLLOVER_STRATEGY
    assert off.strategy == COMPACT_STRATEGY
    assert on.prompt != off.prompt
    offered = context_boundary_tools(on, INSTALLED)
    assert offered("new_context") and offered("search_history")
    assert not context_boundary_tools(off, INSTALLED)("new_context")


async def test_a_flag_nothing_answers_crosses_the_strategy_the_toml_names() -> None:
    """The closed state, which is what a deploy with no flag service, an unseeded key, or an outage
    runs on: the mechanism moves only where the service says so."""
    try:
        init_flags(InMemoryProvider({}))
        with ws(uuid4()):
            unanswered = await flagged_context_boundary(_config(), INSTALLED)
    finally:
        init_flags(InMemoryProvider({}))

    assert unanswered.strategy == DEFAULT_CONTEXT_STRATEGY


async def test_a_pack_shipping_one_boundary_crosses_it_under_the_open_flag_backend() -> None:
    """The `open` backend a dev or eval stack selects answers every key it holds no declaration for
    as on, and the pack that ships compaction alone ships no rollover extension to declare this key.
    Core declares the key it reads, so such a stack crosses `[context] strategy` instead of failing
    every turn on a flagged name no provider registers."""
    manifests = load_manifests("assistant")
    assert sorted(boundary_specs(manifests)) == [DEFAULT_CONTEXT_STRATEGY]
    config = Config.model_validate(
        {
            "database": {"url": "sqlite+aiosqlite:///ufo.db"},
            "blob": {"backend": "filesystem", "root": "./blobs"},
            "flags": {"backend": OPEN_FLAG_BACKEND},
        }
    )
    try:
        init_flags(_select_flag_provider(config, manifests))
        with ws(uuid4()):
            crossed = await flagged_context_boundary(config, manifests)
    finally:
        init_flags(InMemoryProvider({}))

    assert crossed.strategy == DEFAULT_CONTEXT_STRATEGY


async def test_a_flagged_name_no_provider_registers_fails_loud() -> None:
    """A flag flip is what selects this name and no deploy follows the flip, so boot resolves it
    beside the other one rather than leaving the first filled window to find it missing."""
    with pytest.raises(NotRegisteredError, match="forgetting"):
        select_flagged_context_boundary(_config(flagged="forgetting"), INSTALLED)
    try:
        init_flags(_flag_serving(SERVED_TRUE))
        with ws(uuid4()), pytest.raises(NotRegisteredError, match="forgetting"):
            await flagged_context_boundary(_config(flagged="forgetting"), INSTALLED)
    finally:
        init_flags(InMemoryProvider({}))
