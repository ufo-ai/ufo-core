"""The durable conversation transcript's blob contract: the key it lands under and the codec that
crosses it. The write role (`loop.transcript.Transcript`) and the read role (the eval
`TrajectoryCorpus`) sit in packages that cannot import each other, so the key + record + codec live
here once — a format change moves both roles at the same time instead of silently breaking one."""

import json
from uuid import UUID

import lz4.frame
from pydantic import BaseModel, ConfigDict, Field

from ufo.models.interface import Message


class Conversation(BaseModel):
    model_config = ConfigDict(strict=True)
    seq: int = Field(ge=1)
    messages: tuple[Message, ...]


class TranscriptDecodeError(ValueError):
    """A stored transcript blob could not be decoded to a Conversation — corrupt bytes or a schema
    that no longer matches the record."""


def transcript_key(conversation_id: UUID) -> str:
    return f"conversations/{conversation_id}/messages.json.lz4"


def encode(conversation: Conversation) -> bytes:
    body = json.dumps(conversation.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    return lz4.frame.compress(body)


def decode(body: bytes) -> Conversation:
    try:
        return Conversation.model_validate_json(lz4.frame.decompress(body))
    except (ValueError, RuntimeError) as error:
        raise TranscriptDecodeError(str(error)) from error
