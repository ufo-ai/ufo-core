"""The compact strategy's declaration: the `compact` boundary and its prompt.

A deploy runs this boundary with `[context] strategy = "compact"`. It offers no reset tool and
keeps no history file, so the spec names `get_context_remaining` alone and the turn's registry drops
the tools the other strategy ships.

The extension carries no tools of its own: everything this boundary does happens at the boundary,
inside the memoized step the loop dispatches through the seam."""

from ufo.sdk.context import BoundaryInputs, ContextBoundary
from ufo.sdk.manifest import ContextBoundarySpec, Manifest
from ufo_ext_context_compact.compaction import Compaction
from ufo_ext_context_compact.prompts import CONTEXT_WINDOW_BLOCK

NAME = "context_compact"
VERSION = "0.1.0"
COMPACT_STRATEGY = "compact"
GET_CONTEXT_REMAINING_TOOL = "get_context_remaining"


def build_compact(inputs: BoundaryInputs) -> ContextBoundary:
    return Compaction(
        serving=inputs.serving,
        blob=inputs.blob,
        conversation_id=inputs.conversation_id,
        hooks=inputs.hooks,
        turn=inputs.turn,
        agent=inputs.agent,
    )


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        context_boundaries=(
            ContextBoundarySpec(
                strategy=COMPACT_STRATEGY,
                build=build_compact,
                tools=(GET_CONTEXT_REMAINING_TOOL,),
                prompt=CONTEXT_WINDOW_BLOCK,
            ),
        ),
    )
