import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MessageAttachment:
    id: str
    filename: str
    size_bytes: int


@dataclass(frozen=True)
class InboundMessage:
    id: str
    conversation_id: str
    sender: str
    text: str
    attachments: tuple[MessageAttachment, ...]
    direct: bool


@dataclass(frozen=True)
class ProviderEvent:
    sequence: int | None = None
    message: InboundMessage | None = None
    head_sequence: int | None = None


class ProviderNotConfigured(RuntimeError):
    pass


class MessageProvider(Protocol):
    @property
    def installation_id(self) -> str: ...

    async def assign_line(self, phone_number: str, idempotency_key: str) -> str: ...

    def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]: ...

    def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]: ...

    async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str: ...

    async def send_attachment(
        self,
        conversation_id: str,
        filename: str,
        data: bytes,
        idempotency_key: str,
    ) -> str: ...

    def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]: ...

    async def invalidate(self) -> None: ...

    def invalid_cursor(self, error: Exception) -> bool: ...

    def external_error(self, error: Exception) -> bool: ...

    def error_code(self, error: Exception) -> str: ...
