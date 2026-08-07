"""Typed extension registrations for conversation-scoped portal slots."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from unicodedata import category
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ufo.audience import Audience
from ufo.ext.context import ExtensionContext
from ufo.image_previews import RasterImageMediaType
from ufo.models.interface import Message
from ufo.tools.file_changes import FILE_CHANGE_PATH_MAX_CHARS

CONVERSATION_CHANGE_PATCH_MAX_CHARS = 25_000
CONVERSATION_CHANGES_MAX = 100
CONVERSATION_ARTIFACT_FILENAME_MAX_CHARS = 500
CONVERSATION_ARTIFACT_SUBJECT_MAX_CHARS = 2_000
CONVERSATION_ARTIFACT_MEDIA_TYPE_MAX_CHARS = 200
CONVERSATION_ARTIFACT_URL_MAX_CHARS = 8_192
CONVERSATION_ARTIFACTS_MAX = 100
CONVERSATION_FILES_MAX = 100
CONVERSATION_SOURCE_URL_MAX_CHARS = 4_096
CONVERSATION_SOURCE_TITLE_MAX_CHARS = 500
CONVERSATION_SOURCE_SNIPPET_MAX_CHARS = 2_000
CONVERSATION_SOURCE_DATE_MAX_CHARS = 40
CONVERSATION_SOURCES_MAX = 100
PortalIcon = Literal["artifact", "diff", "file", "link"]
PORTAL_ICONS = frozenset(("artifact", "diff", "file", "link"))


class ImagePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["image"] = "image"
    media_type: RasterImageMediaType
    url: str = Field(min_length=1, max_length=CONVERSATION_ARTIFACT_URL_MAX_CHARS)

    @field_validator("url")
    @classmethod
    def same_origin_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        decoded = unquote(value)
        if (
            not value.startswith("/")
            or value.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or "\\" in decoded
            or any(category(char) == "Cc" for char in decoded)
        ):
            raise ValueError("image preview URL must be same-origin and root-relative")
        return value


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


class ConversationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str = Field(min_length=1, max_length=CONVERSATION_ARTIFACT_FILENAME_MAX_CHARS)
    subject: str | None = Field(default=None, max_length=CONVERSATION_ARTIFACT_SUBJECT_MAX_CHARS)
    media_type: str = Field(min_length=1, max_length=CONVERSATION_ARTIFACT_MEDIA_TYPE_MAX_CHARS)
    size_bytes: int = Field(ge=0)
    created_at: datetime
    url: str | None = Field(default=None, max_length=CONVERSATION_ARTIFACT_URL_MAX_CHARS)
    preview: ImagePreview | None = None

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("artifact URL must be HTTP(S) without embedded credentials")
        return value


class ArtifactsSlotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["artifacts"] = "artifacts"
    artifacts: tuple[ConversationArtifact, ...] = Field(max_length=CONVERSATION_ARTIFACTS_MAX)
    truncated: bool


class ConversationFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=FILE_CHANGE_PATH_MAX_CHARS)
    size_bytes: int = Field(ge=0)
    modified_at: datetime
    preview: ImagePreview | None = None


class FilesSlotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["files"] = "files"
    files: tuple[ConversationFile, ...] = Field(max_length=CONVERSATION_FILES_MAX)
    truncated: bool


class ConversationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=CONVERSATION_SOURCE_URL_MAX_CHARS)
    title: str = Field(max_length=CONVERSATION_SOURCE_TITLE_MAX_CHARS)
    snippet: str = Field(max_length=CONVERSATION_SOURCE_SNIPPET_MAX_CHARS)
    published_date: str | None = Field(default=None, max_length=CONVERSATION_SOURCE_DATE_MAX_CHARS)

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("source URL must be HTTP(S) without embedded credentials")
        return value


class SourcesSlotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["sources"] = "sources"
    sources: tuple[ConversationSource, ...] = Field(max_length=CONVERSATION_SOURCES_MAX)
    truncated: bool


ConversationSlotPayload = (
    ArtifactsSlotPayload | ChangesSlotPayload | FilesSlotPayload | SourcesSlotPayload
)
SUPPORTED_CONVERSATION_SLOT_PAYLOADS = (
    ArtifactsSlotPayload,
    ChangesSlotPayload,
    FilesSlotPayload,
    SourcesSlotPayload,
)


@dataclass(frozen=True)
class ConversationSlotContext:
    ext: ExtensionContext
    conversation_id: UUID
    agent_id: UUID
    audience: Audience
    messages: tuple[Message, ...]
    compacted: bool
    projection: ConversationSlotPayload | None = None


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
