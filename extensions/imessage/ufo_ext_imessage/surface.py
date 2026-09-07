import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.audience import conversation_audience, room_audience
from ufo.sdk.context import ScopedStore
from ufo.sdk.o11y import log
from ufo.sdk.surfaces import (
    AddressClaim,
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
CLAIM_PREFIX = "phone-claim:"
RECONNECT_SECONDS = 2.0
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
DETAILS_LINK_TEXT = "Open detailed report"
LIVE_BUFFER_FRAMES = 1_000
CONNECTED_TEXT = "Connected. Send your request."
CODE_UNKNOWN_TEXT = "That code does not match. Check the chat and send the code again."
CODE_EXPIRED_TEXT = "That code expired. Ask in chat for a new one."
CONTACT_CARD_NAME = "ufo"
CONTACT_CARD_FILENAME = "ufo.vcf"
OPT_IN_TEXT = "UFO"
OPT_IN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
OPT_IN_CODE_LENGTH = 6
OPT_OUT_REPLIES = frozenset(
    {"cancel", "end", "optout", "quit", "revoke", "stop", "stopall", "unsubscribe"}
)


class PendingClaim(BaseModel):
    """The line and the code one member was given for one phone. Who holds the claim and until when
    is the fleet's row, since only that row can be unique across every workspace the shared line
    serves."""

    assigned_phone_number: str
    opt_in_code: str = Field(pattern=rf"^[{OPT_IN_CODE_ALPHABET}]{{{OPT_IN_CODE_LENGTH}}}$")


def read_claim(stored: object) -> PendingClaim | None:
    """The claim one stored row carries, or None when the row holds no readable claim. A claim is
    worth no more than the next `imessage_connect` call, so a row this model rejects is discarded by
    its caller: raising instead would stop every inbound message the surface admits."""
    if stored is None:
        return None
    try:
        return PendingClaim.model_validate(stored)
    except ValidationError:
        log("imessage.claim.unreadable")
        return None


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


def claim_key(member_id: UUID, phone_number: str) -> str:
    return f"{CLAIM_PREFIX}{hashlib.sha256(f'{member_id}:{phone_number}'.encode()).hexdigest()}"


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
    provider: Callable[[str | None], MessageProvider]

    async def listen(self, context: SurfaceListenerContext) -> None:
        """Read the provider's durable event stream and admit each inbound iMessage once."""
        try:
            provider = self.provider(context.public_base_url)
        except ProviderNotConfigured as error:
            log("imessage.listener.inactive", reason=str(error))
            await asyncio.Event().wait()
            return
        cursor = await context.cursor(provider.installation_id)
        while True:
            try:
                await self._consume_connected(context, provider, provider.installation_id, cursor)
            except MessageStreamDisconnected as disconnected:
                stored = await context.cursor(provider.installation_id)
                cursor = stored if stored is not None else disconnected.cursor
                if provider.invalid_cursor(disconnected.error) and cursor is not None:
                    cursor = None
                    await context.clear_cursor()
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
                        try:
                            await self._process_event(
                                context, provider, installation_id, frame.sequence, frame.message
                            )
                        except Exception as process_error:
                            if not provider.external_error(process_error):
                                raise
                            raise MessageStreamDisconnected(
                                cursor, process_error
                            ) from process_error
                        cursor = frame.sequence
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
        await context.store_cursor(installation_id, head)
        return head

    async def _process_event(
        self,
        context: SurfaceListenerContext,
        provider: MessageProvider,
        installation_id: str,
        sequence: int,
        message: InboundMessage | None,
    ) -> None:
        """Deliver one event to the workspace its sender reaches, then record the stream position.
        One line serves every workspace, so a message from a phone no workspace claims advances the
        stream and reaches nobody."""
        if message is not None:
            async with context.addressed(message.sender) as ctx:
                if ctx is not None:
                    await self._admit_message(ctx, provider, message)
        await context.store_cursor(installation_id, sequence)

    async def _admit_message(
        self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage
    ) -> None:
        claim = await ctx.address_claim(message.sender)
        if claim is None:
            return
        if claim.claim_expires_at is not None:
            await self._prove(ctx, provider, message, claim)
            return
        if claim.proved_by == message.id:
            return
        ambient_text = message.text
        if message.attachments:
            names = ", ".join(attachment.filename for attachment in message.attachments)
            ambient_text = f"{ambient_text}\nAttachments: {names}".strip()
        if not message.direct and not await ctx.ambient_reply_wanted(
            AmbientMessage(speaker=message.sender, text=ambient_text), ()
        ):
            return
        audience = (
            conversation_audience(claim.member_id)
            if message.direct
            else room_audience(
                SURFACE_IMESSAGE,
                hashlib.sha256(message.conversation_id.encode()).hexdigest()[:32],
            )
        )
        conversation_id = await ctx.conversation_for(
            queue_key(message.conversation_id, direct=message.direct),
            audience,
            label="DM" if message.direct else "Group chat",
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
            speaker_member_id=claim.member_id,
        )

    async def _prove(
        self,
        ctx: SurfaceContext,
        provider: MessageProvider,
        message: InboundMessage,
        claim: AddressClaim,
    ) -> None:
        """Read one message against the claim its sender is proving. The proving message links the
        phone and founds no turn, so every path here returns without admitting: a message carrying
        no live code is answered with what to do next."""
        if not message.direct or claim.claim_expires_at is None:
            return
        store = ScopedStore(IMESSAGE_EXTENSION)
        key = claim_key(claim.member_id, message.sender)
        if claim.claim_expires_at <= datetime.now(UTC):
            await ctx.release_address(message.sender)
            await store.delete(key)
            await provider.send_text(
                message.conversation_id, CODE_EXPIRED_TEXT, f"imessage-code-expired:{message.id}"
            )
            return
        if message.text.strip().casefold() in OPT_OUT_REPLIES:
            await ctx.release_address(message.sender)
            await store.delete(key)
            return
        pending = read_claim(await store.get(key))
        if pending is None:
            return
        typed = "".join(character for character in message.text.upper() if character.isalnum())
        if pending.opt_in_code not in typed:
            await provider.send_text(
                message.conversation_id, CODE_UNKNOWN_TEXT, f"imessage-code-unknown:{message.id}"
            )
            return
        await provider.send_text(
            message.conversation_id, CONNECTED_TEXT, f"imessage-connected:{message.id}"
        )
        await self._send_contact_card(provider, message, pending.assigned_phone_number)
        await ctx.confirm_address(message.sender, message.id)
        await store.delete(key)

    async def _send_contact_card(
        self, provider: MessageProvider, message: InboundMessage, assigned_phone_number: str
    ) -> None:
        """Send the vCard for the assigned line. A provider that refuses the card leaves the phone
        connected: the card is what drops the Report Junk banner, not what carries a member's
        traffic, so its own failure is logged under its own name and never reported as the stream
        dropping."""
        try:
            await provider.send_attachment(
                message.conversation_id,
                CONTACT_CARD_FILENAME,
                contact_card(assigned_phone_number),
                f"imessage-contact-card:{message.id}",
            )
        except Exception as error:
            if not provider.external_error(error):
                raise
            log(
                "imessage.contact_card.refused",
                provider=type(provider).__name__,
                code=provider.error_code(error),
            )

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

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str:
        """Send one terminal turn reply to its iMessage chat."""
        conversation = conversation_from_queue(writeback.queue_key)
        return await self.provider(ctx.public_base_url).send_text(
            conversation.id,
            await self._terminal_text(ctx, writeback, direct=conversation.direct),
            str(writeback.turn_id),
        )

    async def speak(self, ctx: SurfaceContext, reply: MidTurnReply) -> str:
        """Send one reply produced before its turn ends."""
        return await self.provider(ctx.public_base_url).send_text(
            conversation_from_queue(reply.queue_key).id, reply.text, str(reply.id)
        )

    async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None:
        """Upload each shared file that fits the provider's bounded attachment request. A file the
        closing reply carried is a link in that reply, never an attachment."""
        provider = self.provider(ctx.public_base_url)
        conversation_id = conversation_from_queue(writeback.queue_key).id
        for index, artifact in enumerate(writeback.artifacts):
            if artifact.role != "file" or artifact.size_bytes > MAX_ATTACHMENT_BYTES:
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
            if artifact.role == "file" and artifact.size_bytes <= MAX_ATTACHMENT_BYTES:
                continue
            link = None
            if artifact.role == "details":
                link = await ctx.report_url(writeback.conversation_id, artifact)
            link = link or ctx.artifact_link(artifact)
            label = DETAILS_LINK_TEXT if artifact.role == "details" else artifact.filename
            parts.append(f"{label}: {link}" if link else artifact.filename)
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
