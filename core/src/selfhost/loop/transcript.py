"""The conversation's one durable transcript: whole-object codec, monotonic seq guard."""

import json
from dataclasses import dataclass
from uuid import UUID

import lz4.frame
from pydantic import BaseModel, ConfigDict, Field

from selfhost.blob import BlobNotFound, BlobStore
from selfhost.models import Message


class Conversation(BaseModel):
    model_config = ConfigDict(strict=True)
    seq: int = Field(ge=1)
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class Transcript:
    blob: BlobStore
    conversation_id: UUID

    async def read(self) -> Conversation | None:
        try:
            body = await self.blob.get(self._key)
        except BlobNotFound:
            return None
        return Conversation.model_validate_json(lz4.frame.decompress(body))

    async def write(self, conversation: Conversation) -> None:
        current = await self.read()
        if current is not None and current.seq >= conversation.seq:
            return
        encoded = json.dumps(
            conversation.model_dump(), separators=(",", ":"), sort_keys=True
        ).encode()
        await self.blob.put(self._key, lz4.frame.compress(encoded))

    @property
    def _key(self) -> str:
        return f"conversations/{self.conversation_id}/messages.json.lz4"
