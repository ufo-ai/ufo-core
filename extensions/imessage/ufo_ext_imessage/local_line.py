import asyncio
from collections.abc import AsyncGenerator, AsyncIterator

from ufo_ext_imessage.provider import ProviderEvent

LOCAL_LINE = "+15555550100"
LOCAL_LINE_INSTALLATION = "local-line"
LOCAL_LINE_ERROR_CODE = "local_line"
LOCAL_LINE_DELIVERS_NOTHING = "The local line delivers nothing."


class LocalLine:
    """The iMessage provider of a dev deploy that holds no Spectrum project: it assigns one fixed
    line so the connect flow runs to its QR and sms: link, keeps the listener healthy on a stream
    that never speaks, and refuses to send — a phone connected here is never messaged."""

    @property
    def installation_id(self) -> str:
        return LOCAL_LINE_INSTALLATION

    async def assign_line(self, phone_number: str, idempotency_key: str) -> str:
        return LOCAL_LINE

    async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]:
        return
        yield ProviderEvent()

    async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]:
        ready.set()
        await asyncio.Event().wait()
        yield ProviderEvent()

    async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str:
        raise RuntimeError(LOCAL_LINE_DELIVERS_NOTHING)

    async def send_attachment(
        self,
        conversation_id: str,
        filename: str,
        data: bytes,
        idempotency_key: str,
    ) -> str:
        raise RuntimeError(LOCAL_LINE_DELIVERS_NOTHING)

    async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]:
        raise RuntimeError(LOCAL_LINE_DELIVERS_NOTHING)
        yield

    async def invalidate(self) -> None:
        return

    def invalid_cursor(self, error: Exception) -> bool:
        return False

    def external_error(self, error: Exception) -> bool:
        return False

    def error_code(self, error: Exception) -> str:
        return LOCAL_LINE_ERROR_CODE
