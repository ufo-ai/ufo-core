import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from ufo.sdk.audience import conversation_audience, room_audience
from ufo.sdk.context import ScopedStore
from ufo.sdk.o11y import log
from ufo.sdk.surfaces import (
    AmbientMessage,
    MidTurnReply,
    SurfaceContext,
    SurfaceListenerContext,
    TurnContext,
    Writeback,
    fence_member_message,
    inbox_name,
    mint_marker,
)
from ufo_ext_imessage.provider import (
    InboundMessage,
    MessageAttachment,
    MessageProvider,
    ProviderEvent,
    ProviderNotConfigured,
)

IMESSAGE_EXTENSION = "imessage"
SURFACE_IMESSAGE = "imessage"
IMESSAGE_INBOX_DIR = "inbox/imessage"
CURSOR_KEY = "stream:shared:cursor"
CLAIM_PREFIX = "opt-in-claim:"
CONFIRMATION_REPLY_PREFIX = "opt-in-receipt:"
RECONNECT_SECONDS = 2.0
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
LIVE_BUFFER_FRAMES = 1_000
CONNECTED_TEXT = "Connected. Send your request."
CONTACT_CARD_NAME = "ufo"
CONTACT_CARD_FILENAME = "ufo.vcf"
OPT_IN_TEXT = "UFO"
OPT_OUT_REPLIES = frozenset(
    {"cancel", "end", "optout", "quit", "revoke", "stop", "stopall", "unsubscribe"}
)


class PendingClaim(BaseModel):
    member_id: UUID
    phone_number: str
    conversation_id: str | None
    assigned_phone_number: str
    opt_in_code: str = Field(pattern=r"^[0-9A-F]{32}$")
    expires_at: datetime


@dataclass(frozen=True)
class MemberLink:
    member_id: UUID
    confirmation_receipt_key: str | None


@dataclass(frozen=True)
class LiveFrame:
    value: ProviderEvent


@dataclass(frozen=True)
class LiveFailure:
    error: Exception


class MessageStreamDisconnected(RuntimeError):
    def __init__(self, cursor: int | None, error: Exception) -> None:
        self.cursor = cursor
        self.error = error
        super().__init__(str(error))


class MessageProviderStreamEnded(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversationAddress:
    id: str
    direct: bool


def contact_card(assigned_phone_number: str) -> bytes:
    """A vCard for the assigned line. The member saves it as a known contact, which is what drops
    the Report Junk banner from the conversation."""
    lines = (
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{CONTACT_CARD_NAME};;;;",
        f"FN:{CONTACT_CARD_NAME}",
        f"TEL;TYPE=CELL:{assigned_phone_number}",
        "END:VCARD",
        "",
    )
    return "\r\n".join(lines).encode()


def phone_key(phone_number: str) -> str:
    return f"{CLAIM_PREFIX}{hashlib.sha256(phone_number.encode()).hexdigest()}"


def queue_key(conversation_id: str, *, direct: bool) -> str:
    return json.dumps(["direct" if direct else "group", conversation_id], separators=(",", ":"))


def conversation_from_queue(queue: str) -> ConversationAddress:
    value = json.loads(queue)
    match value:
        case ["direct", str() as conversation_id] if conversation_id:
            return ConversationAddress(id=conversation_id, direct=True)
        case ["group", str() as conversation_id] if conversation_id:
            return ConversationAddress(id=conversation_id, direct=False)
        case _:
            raise ValueError("Invalid iMessage queue key")


async def _attachment_content(data: bytes) -> AsyncIterator[bytes]:
    yield data


@dataclass(frozen=True)
class ImessageSurface:
    provider: Callable[[], MessageProvider]

    async def listen(self, context: SurfaceListenerContext) -> None:
        """Read the provider's durable event stream and admit each inbound iMessage once."""
        try:
            provider = self.provider()
        except ProviderNotConfigured as error:
            log("imessage.listener.inactive", reason=str(error))
            await asyncio.Event().wait()
            return
        cursor = await self._read_cursor(context, provider.installation_id)
        while True:
            try:
                await self._consume_connected(context, provider, provider.installation_id, cursor)
            except MessageStreamDisconnected as disconnected:
                stored = await self._read_cursor(context, provider.installation_id)
                cursor = stored if stored is not None else disconnected.cursor
                if provider.invalid_cursor(disconnected.error) and cursor is not None:
                    cursor = None
                    await self._clear_cursor(context, provider.installation_id)
                await provider.invalidate()
                log(
                    "imessage.stream.disconnected",
                    provider=type(provider).__name__,
                    code=provider.error_code(disconnected.error),
                )
                await asyncio.sleep(RECONNECT_SECONDS)

    async def _consume_connected(
        self,
        context: SurfaceListenerContext,
        provider: MessageProvider,
        installation_id: str,
        cursor: int | None,
    ) -> None:
        ready = asyncio.Event()
        frames: asyncio.Queue[LiveFrame | LiveFailure] = asyncio.Queue(LIVE_BUFFER_FRAMES)
        pump = asyncio.create_task(self._pump_live(provider, ready, frames))
        try:
            await ready.wait()
            if cursor is not None:
                try:
                    cursor = await self._catch_up(context, provider, installation_id, cursor)
                except Exception as catch_up_error:
                    if not provider.external_error(catch_up_error):
                        raise
                    raise MessageStreamDisconnected(cursor, catch_up_error) from catch_up_error
            while True:
                match await frames.get():
                    case LiveFailure(error=live_error):
                        if not isinstance(
                            live_error, MessageProviderStreamEnded
                        ) and not provider.external_error(live_error):
                            raise live_error
                        raise MessageStreamDisconnected(cursor, live_error) from live_error
                    case LiveFrame(value=frame):
                        if frame.sequence is None or (
                            cursor is not None and frame.sequence <= cursor
                        ):
                            continue
                        cursor = frame.sequence
                        try:
                            await self._process_event(
                                context, provider, installation_id, cursor, frame.message
                            )
                        except Exception as process_error:
                            if not provider.external_error(process_error):
                                raise
                            raise MessageStreamDisconnected(
                                cursor, process_error
                            ) from process_error
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def _pump_live(
        self,
        provider: MessageProvider,
        ready: asyncio.Event,
        frames: asyncio.Queue[LiveFrame | LiveFailure],
    ) -> None:
        try:
            async for frame in provider.subscribe(ready):
                await frames.put(LiveFrame(frame))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await frames.put(LiveFailure(error))
        else:
            await frames.put(
                LiveFailure(MessageProviderStreamEnded("Message provider stream ended"))
            )
        finally:
            ready.set()

    async def _catch_up(
        self,
        context: SurfaceListenerContext,
        provider: MessageProvider,
        installation_id: str,
        cursor: int | None,
    ) -> int:
        head = cursor or 0
        async for frame in provider.catch_up(cursor):
            if frame.head_sequence is not None:
                head = frame.head_sequence
                continue
            if frame.sequence is None:
                continue
            head = frame.sequence
            await self._process_event(
                context, provider, installation_id, frame.sequence, frame.message
            )
        await self._store_cursor(context, installation_id, head)
        return head

    async def _process_event(
        self,
        context: SurfaceListenerContext,
        provider: MessageProvider,
        installation_id: str,
        sequence: int,
        message: InboundMessage | None,
    ) -> None:
        async with context.workspace(installation_id) as ctx:
            if ctx is not None:
                confirmation_receipt_key = None
                if message is not None:
                    confirmation_receipt_key = await self._admit_message(ctx, provider, message)
                await ScopedStore(IMESSAGE_EXTENSION).put(CURSOR_KEY, sequence)
                if confirmation_receipt_key is not None:
                    await ScopedStore(IMESSAGE_EXTENSION).delete(confirmation_receipt_key)

    async def _admit_message(
        self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage
    ) -> str | None:
        link = await self._linked_member(ctx, provider, message)
        if link is None:
            return None
        if link.confirmation_receipt_key is not None:
            return link.confirmation_receipt_key
        ambient_text = message.text
        if message.attachments:
            names = ", ".join(attachment.filename for attachment in message.attachments)
            ambient_text = f"{ambient_text}\nAttachments: {names}".strip()
        if not message.direct and not await ctx.ambient_reply_wanted(
            AmbientMessage(speaker=message.sender, text=ambient_text), ()
        ):
            return None
        audience = (
            conversation_audience(link.member_id)
            if message.direct
            else room_audience(
                SURFACE_IMESSAGE,
                hashlib.sha256(message.conversation_id.encode()).hexdigest()[:32],
            )
        )
        conversation_id = await ctx.conversation_for(
            queue_key(message.conversation_id, direct=message.direct),
            audience,
            label="Direct message" if message.direct else "Group chat",
        )
        attached = (
            await self._downloaded_files(ctx, provider, conversation_id, message.attachments)
            if message.attachments
            else ""
        )
        body = fence_member_message(mint_marker(), "", message.text, attached)
        await ctx.admit(
            conversation_id,
            body,
            idempotency_key=message.id,
            context=TurnContext(sender=message.sender, source=f"iMessage from {message.sender}"),
            speaker_member_id=link.member_id,
        )
        return None

    async def _linked_member(
        self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage
    ) -> MemberLink | None:
        store = ScopedStore(IMESSAGE_EXTENSION)
        receipt_key = (
            f"{CONFIRMATION_REPLY_PREFIX}{hashlib.sha256(message.id.encode()).hexdigest()}"
        )
        receipt = await store.get(receipt_key)
        linked = await ctx.linked_member(message.sender)
        if receipt is None and linked is not None:
            await store.delete(phone_key(message.sender))
            return MemberLink(member_id=linked, confirmation_receipt_key=None)
        key = phone_key(message.sender)
        stored = receipt if receipt is not None else await store.get(key)
        if stored is None:
            return None
        claim = PendingClaim.model_validate(stored)
        if receipt is None and claim.expires_at <= datetime.now(UTC):
            await store.delete(key)
            return None
        if (
            not message.direct
            or claim.phone_number != message.sender
            or (
                claim.conversation_id is not None
                and claim.conversation_id != message.conversation_id
            )
        ):
            return None
        reply = message.text.strip().casefold()
        if receipt is None and reply in OPT_OUT_REPLIES:
            await store.delete(key)
            return None
        expected_reply = f"{OPT_IN_TEXT} {claim.opt_in_code}".casefold()
        if receipt is None and (reply != expected_reply or message.attachments):
            return None
        if receipt is None and not await store.put_if(receipt_key, stored, expected=None):
            return None
        if linked is None:
            linked = await ctx.link_member_id(message.sender, claim.member_id)
        if linked is None:
            await store.delete(key)
            await store.delete(receipt_key)
            return None
        await provider.send_text(
            message.conversation_id,
            CONNECTED_TEXT,
            f"imessage-connected:{message.id}",
        )
        if claim.assigned_phone_number:
            await provider.send_attachment(
                message.conversation_id,
                CONTACT_CARD_FILENAME,
                contact_card(claim.assigned_phone_number),
                f"imessage-contact-card:{message.id}",
            )
        await store.delete(key)
        return MemberLink(member_id=linked, confirmation_receipt_key=receipt_key)

    async def _downloaded_files(
        self,
        ctx: SurfaceContext,
        provider: MessageProvider,
        conversation_id: UUID,
        attachments: tuple[MessageAttachment, ...],
    ) -> str:
        used: set[str] = set()
        delivered: list[str] = []
        too_large: list[str] = []
        unavailable: list[str] = []
        for attachment in attachments:
            name = inbox_name(attachment.filename, used)
            if attachment.size_bytes > MAX_ATTACHMENT_BYTES:
                too_large.append(attachment.filename)
                continue
            data = bytearray()
            stream = provider.download_attachment(attachment.id)
            try:
                async for chunk in stream:
                    data += chunk
                    if len(data) > MAX_ATTACHMENT_BYTES:
                        break
            except Exception as error:
                if not provider.external_error(error):
                    raise
                unavailable.append(attachment.filename)
                continue
            finally:
                await stream.aclose()
            if len(data) > MAX_ATTACHMENT_BYTES:
                too_large.append(attachment.filename)
                continue
            await ctx.write_workspace_file(
                conversation_id,
                f"{IMESSAGE_INBOX_DIR}/{name}",
                _attachment_content(bytes(data)),
            )
            delivered.append(name)
        notes: list[str] = []
        if delivered:
            files = ", ".join(f"{IMESSAGE_INBOX_DIR}/{name}" for name in delivered)
            notes.append(f"Attached files, saved in the workspace: {files}")
        if too_large:
            files = ", ".join(too_large)
            notes.append(f"Skipped files, too large to download: {files}")
        if unavailable:
            files = ", ".join(unavailable)
            notes.append(f"Skipped files, unavailable to download: {files}")
        return "\n".join(notes)

    async def _store_cursor(
        self, context: SurfaceListenerContext, installation_id: str, cursor: int
    ) -> None:
        async with context.workspace(installation_id) as ctx:
            if ctx is not None:
                await ScopedStore(IMESSAGE_EXTENSION).put(CURSOR_KEY, cursor)

    async def _read_cursor(
        self, context: SurfaceListenerContext, installation_id: str
    ) -> int | None:
        async with context.workspace(installation_id) as ctx:
            if ctx is None:
                return None
            value = await ScopedStore(IMESSAGE_EXTENSION).get(CURSOR_KEY)
        return value if isinstance(value, int) and value >= 0 else None

    async def _clear_cursor(self, context: SurfaceListenerContext, installation_id: str) -> None:
        async with context.workspace(installation_id) as ctx:
            if ctx is not None:
                await ScopedStore(IMESSAGE_EXTENSION).delete(CURSOR_KEY)

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str:
        """Send one terminal turn reply to its iMessage chat."""
        conversation = conversation_from_queue(writeback.queue_key)
        return await self.provider().send_text(
            conversation.id,
            await self._terminal_text(ctx, writeback, direct=conversation.direct),
            str(writeback.turn_id),
        )

    async def speak(self, _ctx: SurfaceContext, reply: MidTurnReply) -> str:
        """Send one reply produced before its turn ends."""
        return await self.provider().send_text(
            conversation_from_queue(reply.queue_key).id, reply.text, str(reply.id)
        )

    async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None:
        """Upload each shared file that fits the provider's bounded attachment request."""
        provider = self.provider()
        conversation_id = conversation_from_queue(writeback.queue_key).id
        for index, artifact in enumerate(writeback.artifacts):
            if artifact.size_bytes > MAX_ATTACHMENT_BYTES:
                continue
            await provider.send_attachment(
                conversation_id,
                artifact.filename,
                await self._artifact_bytes(ctx, artifact.blob_key, artifact.size_bytes),
                f"{writeback.turn_id}:{index}",
            )

    async def _terminal_text(
        self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool
    ) -> str:
        parts = [writeback.terminal.text, self._question_text(writeback)]
        if writeback.terminal.connect_request is not None:
            if direct:
                parts.append(
                    await ctx.connect_url(
                        writeback.turn_id,
                        writeback.terminal.connect_request.requester_member_id,
                    )
                )
            else:
                parts.append("Continue in a direct message to connect the account.")
        if writeback.terminal.credential_request is not None:
            parts.append(ctx.home_url() or "Open the member portal to continue.")
        for artifact in writeback.artifacts:
            if artifact.size_bytes <= MAX_ATTACHMENT_BYTES:
                continue
            link = ctx.artifact_link(artifact)
            parts.append(f"{artifact.filename}: {link}" if link else artifact.filename)
        text = "\n\n".join(part for part in parts if part)
        return text or f"The turn ended with status: {writeback.terminal.status}."

    def _question_text(self, writeback: Writeback) -> str:
        question = writeback.terminal.question
        if question is None:
            return ""
        lines = [question.title]
        for item in question.questions:
            lines.append(item.question)
            if item.options:
                lines.append("Options: " + ", ".join(option.label for option in item.options))
            if item.chosen:
                lines.append("Current answer: " + item.chosen)
        return "\n".join(lines)

    async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes:
        if size_bytes > MAX_ATTACHMENT_BYTES:
            raise ValueError("iMessage attachment exceeds the upload limit")
        data = bytearray()
        async for chunk in ctx.blob.get_stream(blob_key):
            data += chunk
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise ValueError("iMessage attachment exceeds the upload limit")
        if len(data) != size_bytes:
            raise ValueError("iMessage attachment size changed before upload")
        return bytes(data)
