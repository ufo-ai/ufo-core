"""The rollover strategy's declaration: the `rollover` boundary, its two tools, and its prompt.

Core owns the seam and the selection, never a strategy: `[context] strategy` names one of the
strategies registered at this Manifest point, and this extension is what registers `rollover`. It
ships in the wheel and is discovered like every other first-party extension, so the config default
resolves to this boundary with no core import of it.

`enable-context-rollover` is the flag core reads per turn to cross `[context] flagged_strategy`
instead of `[context] strategy`. Core declares it beside that read rather than here: a pack that
ships one boundary ships no rollover extension, and the flag has to read closed on that pack too.

The build fails loud on a turn with no sandbox: this boundary appends the outgoing window to the
conversation's history file, and a deploy that selects it must not silently reset the window with
nowhere to keep what left it."""

from ufo.sdk.context import BoundaryInputs, ContextBoundary
from ufo.sdk.manifest import ContextBoundarySpec, Manifest
from ufo_ext_context_rollover.prompts import CONTEXT_WINDOW_BLOCK
from ufo_ext_context_rollover.rollover import ContextRollover, SandboxJournal
from ufo_ext_context_rollover.tools import (
    NEW_CONTEXT_TOOL,
    ROLLOVER_TOOLS,
    SEARCH_HISTORY_TOOL,
)

NAME = "context_rollover"
VERSION = "0.1.0"
ROLLOVER_STRATEGY = "rollover"
GET_CONTEXT_REMAINING_TOOL = "get_context_remaining"


def build_rollover(inputs: BoundaryInputs) -> ContextBoundary:
    if inputs.sandbox is None:
        raise RuntimeError(
            f"context strategy {ROLLOVER_STRATEGY!r} appends the outgoing window to the "
            "conversation's sandbox history file, but this turn has no sandbox"
        )
    return ContextRollover(
        serving=inputs.serving,
        blob=inputs.blob,
        conversation_id=inputs.conversation_id,
        hooks=inputs.hooks,
        turn=inputs.turn,
        agent=inputs.agent,
        journal=SandboxJournal(inputs.sandbox, inputs.conversation_id),
    )


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=ROLLOVER_TOOLS,
        context_boundaries=(
            ContextBoundarySpec(
                strategy=ROLLOVER_STRATEGY,
                build=build_rollover,
                tools=(GET_CONTEXT_REMAINING_TOOL, NEW_CONTEXT_TOOL, SEARCH_HISTORY_TOOL),
                prompt=CONTEXT_WINDOW_BLOCK,
            ),
        ),
    )
