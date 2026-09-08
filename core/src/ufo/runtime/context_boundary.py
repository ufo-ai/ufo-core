"""The context boundary: the one extension point a turn crosses when its window fills.

Every round the loop asks the active boundary what window to send. The boundary reads the window it
was handed, the member requests still open, and the skill-load tracker it shares with the tool
context, and answers the window the round actually runs on — unchanged while the window is under
its line, replaced once it crosses. Nothing else in the loop knows what replacement means.

Core registers no strategy. Every one of them ships as an extension declaring a
`ContextBoundarySpec` at the Manifest `context_boundaries` seam, and `[context] strategy` in the
config toml names the one this deploy runs: `context_rollover` registers `rollover` (reset the
window to a recovery record no model authored and append the outgoing window to the sandbox history
file) and `context_compact` registers `compact` (spend one model call over the head and
install the verified summary in front of a verbatim tail). Both ship in the wheel, so the config
default resolves without core naming either implementation.

The strategies are mutually exclusive: `strategy` names one, so a deploy cannot run both over one
window, and selection fails loud on a name nothing registers or a name two providers claim.

Whatever the strategy, the contract at the boundary is the same, and it is what makes a boundary
durable: the replacement is written by one memoized DBOS step, so a crash-recovery replay re-reads
the recorded output instead of re-running the step, and every input the step reads is either the
recorded window or the last persisted record — never process memory. A replay therefore decides the
boundary exactly as the first run did."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ufo.blob import WorkspaceBlobStore
from ufo.config import Config
from ufo.harness.context import ContextRemaining
from ufo.harness.models.interface import Message
from ufo.harness.models.registry import ServingModel
from ufo.harness.sandbox.session import Sandbox
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.ext.manifest import ContextBoundarySpec, Manifest, NotRegisteredError
from ufo.runtime.skills.runtime import LoadedSkills
from ufo.schema.records import Agent, Turn, Usage


@dataclass(frozen=True)
class BoundaryOutcome:
    """What the boundary answers each round: the window the round should send, whether this call
    replaced it, and the model usage the replacement cost — empty for a strategy that calls no
    model."""

    messages: tuple[Message, ...]
    crossed: bool
    usage: tuple[Usage, ...] = ()


@dataclass(frozen=True)
class BoundaryInputs:
    """What a strategy is built from, per turn: the model serving the turn (whose spec declares the
    window and the line), the blob store the boundary record persists to, the conversation the
    record belongs to, the hook chain `pre_compact`/`post_compact` fire on, the turn and agent those
    hooks carry, and the sandbox — the history file a rollover appends to lives there, and a
    strategy that keeps no history ignores it."""

    serving: ServingModel
    blob: WorkspaceBlobStore
    conversation_id: UUID
    hooks: HookChain
    turn: Turn | None = None
    agent: Agent | None = None
    sandbox: Sandbox | None = None


class ContextBoundary(Protocol):
    """The boundary the loop dispatches to. `loaded_skills` is the turn's skill-load tracker,
    shared with the tool context: a boundary drops the workflow bodies the window held, so it
    drains the tracker into its replacement and leaves it empty. `remaining`, `handoff_cap` and
    `checklist_cap` answer the context tools; `maybe_cross` is the boundary itself."""

    @property
    def loaded_skills(self) -> LoadedSkills: ...

    def remaining(self, messages: tuple[Message, ...] | None = None) -> ContextRemaining: ...

    def handoff_cap(self) -> int: ...

    def checklist_cap(self) -> int: ...

    async def maybe_cross(
        self,
        messages: tuple[Message, ...],
        force: bool = False,
        active_requests: tuple[str, ...] = (),
        final: bool = False,
    ) -> BoundaryOutcome: ...


def boundary_specs(manifests: tuple[Manifest, ...]) -> dict[str, ContextBoundarySpec]:
    """Every registered strategy by name, one entry per active extension's spec. A name two
    providers claim fails loud here: with one boundary per deploy, an ambiguous name would decide
    silently which implementation every window crosses."""
    specs: dict[str, ContextBoundarySpec] = {}
    for manifest in manifests:
        for spec in manifest.context_boundaries:
            if spec.strategy in specs:
                raise RuntimeError(
                    f"extension {manifest.name!r} registers context strategy "
                    f"{spec.strategy!r}, which another extension already registers"
                )
            specs[spec.strategy] = spec
    return specs


def select_context_boundary(
    config: Config, manifests: tuple[Manifest, ...] = ()
) -> ContextBoundarySpec:
    """The one strategy this deploy runs, named by `[context] strategy`. Resolved rather than
    remembered, so boot and every turn read the same answer off the same config. A name no provider
    registers fails loud: a deploy that meant to run compaction must not fall back to rollover
    and lose a model call's worth of behavior without saying so."""
    specs = boundary_specs(manifests)
    found = specs.get(config.context.strategy)
    if found is None:
        raise NotRegisteredError(
            f"config selects context strategy {config.context.strategy!r} but no provider "
            f"registers it (have {sorted(specs)})"
        )
    return found


def context_boundary_tools(
    spec: ContextBoundarySpec, manifests: tuple[Manifest, ...] = ()
) -> Callable[[str], bool]:
    """Whether the active strategy offers a given tool. A tool some registered strategy claims and
    the selected one does not is dropped from the turn's registry, so a deploy never offers a reset
    or a history search whose behavior its boundary does not have — the extension that ships those
    tools stays installed either way. Every tool no strategy claims passes untouched."""
    claimed = frozenset(
        name for registered in boundary_specs(manifests).values() for name in registered.tools
    )
    offered = frozenset(spec.tools)
    return lambda name: name not in claimed or name in offered
