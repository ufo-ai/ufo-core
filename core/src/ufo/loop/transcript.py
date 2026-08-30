"""The conversation's one durable transcript writer: a guard monotonic in seq, then in what the
write knows, over the shared blob contract in `ufo.turns.transcript`. Only the run that ends a turn
— or the repair flow republishing its committed terminal — writes at that turn's seq, and the run's
own record outranks the repair fallback whichever lands first."""

from dataclasses import dataclass
from uuid import UUID

from ufo.blob import BlobNotFound, WorkspaceBlobStore
from ufo.turns.transcript import Conversation, decode, encode, transcript_key


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
        if current is not None and not _supersedes(conversation, current):
            return
        await self.blob.put(transcript_key(self.conversation_id), encode(conversation))


def _supersedes(incoming: Conversation, stored: Conversation) -> bool:
    """Whether `incoming` may replace what is stored. A later seq always may. At the same seq only
    the run's own record may replace the repair fallback, which knows the member's messages and
    nothing the turn did — the terminal row is committed before the blob is written, so a
    redelivery can reach the fallback while the run that owns the seq is still writing, and
    first-write-wins would strand the record of what the turn actually ran.

    And only when it does not shrink the record. The run builds its window from the conversation
    before it, so its record holds everything the fallback did and more — except a record rebuilt
    from a read that self-excludes the fallback's own same-seq write (a turn that persists after its
    commit, so a redelivery can land the fallback in between), which comes back shorter and must
    not stand over the fuller history the fallback preserved."""
    if incoming.seq != stored.seq:
        return incoming.seq > stored.seq
    return (
        incoming.from_run and not stored.from_run and len(incoming.messages) >= len(stored.messages)
    )
