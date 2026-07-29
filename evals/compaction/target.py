"""The compaction suite's live collaborator: the model client and blob store the artifact layer
compacts with, and the materializer that swaps a full-scale fixture window in as a conversation's
durable transcript so a probe turn loads it and crosses the boundary for real."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ufo.blob import BlobStore
from ufo.loop.compaction import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACTION_SUMMARY_MAX_TOKENS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    Compaction,
)
from ufo.models.interface import Message, ModelClient
from ufo.transcript import Conversation, encode, transcript_key

WORKSPACE_PREFIX = "/workspace/"


@dataclass(frozen=True)
class CompactionTarget:
    """`context_window` is the declared window of the model the live leaves' probe turns run on —
    the same number `queue.py` hands the engine's `Compaction`. Artifact leaves override the trigger
    to the snapshot's own scale, so they never consult it; behavior and real leaves cannot, and
    their windows have to clear the model's real trigger to compact at all."""

    client: ModelClient
    model: str
    blob: BlobStore
    workspace_root: Path
    context_window: int = DEFAULT_CONTEXT_WINDOW_TOKENS

    @property
    def live_trigger_tokens(self) -> int:
        """The trigger a probe turn will actually be measured against — `Compaction`'s own
        derivation with `trigger_tokens` unset, mirrored so a mismatch is reportable before a turn
        is spent rather than surfacing as a compaction that never fired."""
        return self.context_window - COMPACTION_SUMMARY_MAX_TOKENS - AUTOCOMPACT_BUFFER_TOKENS

    def compactor(self, conversation_id: UUID, trigger_tokens: int | None = None) -> Compaction:
        return Compaction(
            client=self.client,
            model=self.model,
            blob=self.blob,
            conversation_id=conversation_id,
            trigger_tokens=trigger_tokens,
        )

    async def materialize(
        self, conversation_id: UUID, messages: tuple[Message, ...], files: dict[str, str]
    ) -> None:
        """Replace the conversation's transcript with the fixture window at the seed turn's seq, so
        the next admitted turn loads it as prior context, and land each offloaded body under the
        conversation workspace where the sandbox file tools can re-read it."""
        await self.blob.put(
            transcript_key(conversation_id), encode(Conversation(seq=1, messages=messages))
        )
        for path, body in files.items():
            if not path.startswith(WORKSPACE_PREFIX):
                raise ValueError(f"offloaded file {path!r} is outside the workspace")
            target = (
                self.workspace_root / str(conversation_id) / path.removeprefix(WORKSPACE_PREFIX)
            )
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, body)
