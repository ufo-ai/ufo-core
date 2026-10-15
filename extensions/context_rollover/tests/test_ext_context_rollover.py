"""What this extension declares, resolved the way a deploy resolves it.

Core registers no strategy, so the stock default is only real if it resolves through the installed
entry points: these load the manifests the wheel ships and select `rollover` off a config that names
nothing, then check the spec they get is this package's: its boundary, its tools, and its prose."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from ufo_ext_context_rollover.manifest import (
    GET_CONTEXT_REMAINING_TOOL,
    NAME,
    ROLLOVER_STRATEGY,
    manifest,
)
from ufo_ext_context_rollover.prompts import CONTEXT_WINDOW_BLOCK
from ufo_ext_context_rollover.rollover import ContextRollover
from ufo_ext_context_rollover.tools import NEW_CONTEXT_TOOL, SEARCH_HISTORY_TOOL
from ufo_testsupport.models import serving_model

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.harness.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, SandboxSession, SandboxSpec
from ufo.host.ext.loader import HookChain, load_manifests
from ufo.runtime.context_boundary import (
    BoundaryInputs,
    context_boundary_tools,
    select_context_boundary,
)


class _Model:
    async def complete(self, _request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="ok")


def _config(strategy: str | None = None) -> Config:
    return Config.model_validate(
        {
            "database": {"url": "sqlite+aiosqlite:///ufo.db"},
            "blob": {"backend": "filesystem", "root": "./blobs"},
            **({} if strategy is None else {"context": {"strategy": strategy}}),
        }
    )


def _inputs(tmp_path: Path, sandbox: SandboxSession | None = None) -> BoundaryInputs:
    return BoundaryInputs(
        serving=serving_model(_Model()),
        blob=FilesystemBlobStore(root=tmp_path),
        conversation_id=uuid4(),
        hooks=HookChain(),
        sandbox=sandbox,
    )


async def _session(workspace: Path) -> SandboxSession:
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=uuid4(),
            image_ref=SANDBOX_IMAGE_REF,
            workspace_host_path=str(workspace),
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            run_token="rollover-manifest-test",
        )
    )
    return SandboxSession(carrier=carrier, handle=handle)


def test_this_extension_resolves_when_the_deploy_names_it() -> None:
    """A deploy that names `rollover` in the toml resolves it through the installed manifests —
    nothing in core registers the name — and gets this package's boundary, prompt, and tools."""
    installed = load_manifests()
    assert NAME in {declared.name for declared in installed}

    spec = select_context_boundary(_config(ROLLOVER_STRATEGY), installed)

    assert spec.strategy == ROLLOVER_STRATEGY
    assert spec.prompt == CONTEXT_WINDOW_BLOCK
    assert spec.tools == (GET_CONTEXT_REMAINING_TOOL, NEW_CONTEXT_TOOL, SEARCH_HISTORY_TOOL)


def test_the_declared_tools_are_the_ones_the_boundary_offers() -> None:
    """The two tools ship on the same manifest as the boundary that honors them, so a deploy running
    this strategy keeps both in its registry."""
    declared = manifest()
    assert {tool.name for tool in declared.tools} == {NEW_CONTEXT_TOOL, SEARCH_HISTORY_TOOL}
    assert all(tool.subagent_default for tool in declared.tools)

    installed = load_manifests()
    spec = select_context_boundary(_config(ROLLOVER_STRATEGY), installed)
    offers = context_boundary_tools(spec, installed)
    assert offers(NEW_CONTEXT_TOOL)
    assert offers(SEARCH_HISTORY_TOOL)


def test_this_extension_declares_no_flag_of_its_own() -> None:
    """The key that selects this strategy is core's read, so core declares it."""
    assert manifest().flags == ()


async def test_the_selected_spec_builds_a_rollover_over_the_turns_sandbox(tmp_path: Path) -> None:
    spec = select_context_boundary(_config(ROLLOVER_STRATEGY), load_manifests())
    built = spec.build(_inputs(tmp_path, await _session(tmp_path / "workspace")))
    assert isinstance(built, ContextRollover)


def test_a_turn_with_no_sandbox_fails_loud_rather_than_losing_the_history(tmp_path: Path) -> None:
    """This boundary appends the outgoing window to the conversation's history file: with no sandbox
    there is nowhere to keep it, so the build refuses instead of resetting the window silently."""
    spec = select_context_boundary(_config(ROLLOVER_STRATEGY), load_manifests())
    with pytest.raises(RuntimeError, match="no sandbox"):
        spec.build(_inputs(tmp_path))
