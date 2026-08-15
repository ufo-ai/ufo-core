"""The conversation's one durable transcript writer: monotonic seq guard over the shared blob
contract in `ufo.transcript`. Only the run that ends a turn — or the repair flow republishing its
committed terminal — writes at that turn's seq, so the first write at a seq is authoritative."""

from dataclasses import dataclass
from uuid import UUID

from ufo.blob import BlobNotFound, WorkspaceBlobStore
from ufo.transcript import Conversation, decode, encode, transcript_key


@dataclass(frozen=True)
class Transcript:
    blob: WorkspaceBlobStore
    conversation_id: UUID

    async def read(self) -> Conversation | None:
        try:
            body = await self.blob.get(transcript_key(self.conversation_id))
        except BlobNotFound:
            return None
        return decode(body)

    async def write(self, conversation: Conversation) -> None:
        current = await self.read()
        if current is not None and current.seq >= conversation.seq:
            return
        await self.blob.put(transcript_key(self.conversation_id), encode(conversation))
