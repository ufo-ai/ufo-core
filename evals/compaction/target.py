"""The compaction suite's live collaborator: the model client and blob store the artifact layer
compacts with, and the materializer that swaps a full-scale fixture window in as a conversation's
durable transcript so a probe turn loads it and crosses the boundary for real."""

from dataclasses import dataclass
from uuid import UUID

from ufo.blob import BlobStore
from ufo.loop.compaction import Compaction
from ufo.models.interface import Message, ModelClient
from ufo.transcript import Conversation, encode, transcript_key

WORKSPACE_PREFIX = "/workspace/"


@dataclass(frozen=True)
class CompactionTarget:
    client: ModelClient
    model: str
    blob: BlobStore

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
            key = f"conversations/{conversation_id}/workspace/{path.removeprefix(WORKSPACE_PREFIX)}"
            await self.blob.put(key, body.encode())
