"""Typed extension registrations for conversation-scoped portal slots."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.audience import Audience
from ufo.ext.context import ExtensionContext
from ufo.models.interface import Message
from ufo.tools.file_changes import FILE_CHANGE_PATH_MAX_CHARS

CONVERSATION_CHANGE_PATCH_MAX_CHARS = 25_000
CONVERSATION_CHANGES_MAX = 100
PortalIcon = Literal["artifact", "diff", "file", "link"]
PORTAL_ICONS = frozenset(("artifact", "diff", "file", "link"))


class ConversationChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=FILE_CHANGE_PATH_MAX_CHARS)
    patch: str = Field(max_length=CONVERSATION_CHANGE_PATCH_MAX_CHARS)
    truncated: bool


class ChangesSlotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["changes"] = "changes"
    changes: tuple[ConversationChange, ...] = Field(max_length=CONVERSATION_CHANGES_MAX)
    truncated: bool


ConversationSlotPayload = ChangesSlotPayload
SUPPORTED_CONVERSATION_SLOT_PAYLOADS = (ChangesSlotPayload,)


@dataclass(frozen=True)
class ConversationSlotContext:
    ext: ExtensionContext
    conversation_id: UUID
    agent_id: UUID
    audience: Audience
    messages: tuple[Message, ...]
    compacted: bool


ConversationSlotSummary = Callable[[ConversationSlotContext], Awaitable[int | None]]
ConversationSlotRead = Callable[[ConversationSlotContext], Awaitable[ConversationSlotPayload]]


@dataclass(frozen=True)
class ConversationSlotProvider:
    id: str
    label: str
    icon: PortalIcon
    content: type[ConversationSlotPayload]
    summarize: ConversationSlotSummary
    read: ConversationSlotRead


@dataclass(frozen=True)
class BoundConversationSlot:
    extension: str
    provider: ConversationSlotProvider
    ext: ExtensionContext
