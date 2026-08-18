import asyncio
import os
import re
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

import grpc
import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ufo_ext_imessage.proto.photon.imessage.v1 import (
    address_types_pb2,
    attachment_service_pb2,
    attachment_service_pb2_grpc,
    chat_service_pb2,
    chat_service_pb2_grpc,
    event_service_pb2,
    event_service_pb2_grpc,
    message_service_pb2,
    message_service_pb2_grpc,
    message_types_pb2,
)
from ufo_ext_imessage.provider import (
    InboundMessage,
    MessageAttachment,
    PhoneNotAllowed,
    ProviderEvent,
    ProviderNotConfigured,
    RegisteredPhone,
    TargetNotOptedIn,
)

SPECTRUM_PROJECT_ID_ENV = "SPECTRUM_PROJECT_ID"
SPECTRUM_PROJECT_SECRET_ENV = "SPECTRUM_PROJECT_SECRET"
SPECTRUM_PHONE_SCOPE_ENV = "SPECTRUM_PHONE_SCOPE"
SPECTRUM_CLOUD_URL = "https://spectrum.photon.codes"
SPECTRUM_IMESSAGE_ADDRESS = "imessage.spectrum.photon.codes:443"
HTTP_TIMEOUT_SECONDS = 20.0
RPC_TIMEOUT_SECONDS = 30.0
TOKEN_REFRESH_MARGIN_SECONDS = 60
E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")


class SpectrumCloudError(RuntimeError):
    pass


class SharedToken(BaseModel):
    type: Literal["shared"]
    token: str = Field(min_length=1)
    expires_in: int = Field(alias="expiresIn", gt=0)


class SpectrumUser(BaseModel):
    id: str
    phone_number: str = Field(alias="phoneNumber")
    assigned_phone_number: str = Field(alias="assignedPhoneNumber")


class UserPage(BaseModel):
    users: tuple[SpectrumUser, ...]
    total: int


class CloudEnvelope[PayloadT](BaseModel):
    succeed: bool
    data: PayloadT


@dataclass(frozen=True)
class SpectrumLine:
    id: str
    token: str


@dataclass(frozen=True)
class SpectrumLoop:
    client: httpx.AsyncClient
    lock: asyncio.Lock
    token_state: dict[str, object]


@dataclass(frozen=True)
class SpectrumProject:
    project_id: str
    project_secret: str
    client: httpx.AsyncClient
    lock: asyncio.Lock
    token_state: dict[str, object]
    allowed_phone_numbers: frozenset[str] | None = None
    blocked_phone_numbers: frozenset[str] = frozenset()
    _loops: dict[asyncio.AbstractEventLoop, SpectrumLoop] = field(
        default_factory=dict, compare=False
    )
    _loops_lock: threading.Lock = field(default_factory=threading.Lock, compare=False)

    @property
    def installation_id(self) -> str:
        return f"project:{self.project_id}"

    async def line(self) -> SpectrumLine:
        state = self._loop()
        async with state.lock:
            expires_at = state.token_state.get("expires_at")
            line = state.token_state.get("line")
            if (
                isinstance(expires_at, float)
                and isinstance(line, SpectrumLine)
                and expires_at - TOKEN_REFRESH_MARGIN_SECONDS > time.monotonic()
            ):
                return line
            payload = await self._request("POST", f"/projects/{self.project_id}/imessage/tokens")
            try:
                token = TypeAdapter(CloudEnvelope[SharedToken]).validate_python(payload)
            except ValidationError as error:
                raise SpectrumCloudError("Spectrum returned an invalid shared token") from error
            if not token.succeed:
                raise SpectrumCloudError("This iMessage extension requires a shared Spectrum line")
            line = SpectrumLine(id="shared", token=token.data.token)
            state.token_state["line"] = line
            state.token_state["expires_at"] = time.monotonic() + token.data.expires_in
            return line

    def _loop(self) -> SpectrumLoop:
        loop = asyncio.get_running_loop()
        with self._loops_lock:
            state = self._loops.get(loop)
            if state is not None:
                return state
            state = (
                SpectrumLoop(client=self.client, lock=self.lock, token_state=self.token_state)
                if not self._loops
                else SpectrumLoop(
                    client=httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS),
                    lock=asyncio.Lock(),
                    token_state={},
                )
            )
            self._loops[loop] = state
            return state

    async def register_phone(self, phone_number: str, idempotency_key: str) -> RegisteredPhone:
        if not self._phone_allowed(phone_number):
            raise PhoneNotAllowed(phone_number)
        try:
            page = TypeAdapter(CloudEnvelope[UserPage]).validate_python(
                await self._request("GET", f"/projects/{self.project_id}/users/")
            )
        except ValidationError as error:
            raise SpectrumCloudError("Spectrum returned an invalid user list") from error
        if not page.succeed:
            raise SpectrumCloudError("Spectrum rejected the user list request")
        existing = next(
            (user for user in page.data.users if user.phone_number == phone_number), None
        )
        if existing is None:
            try:
                created = TypeAdapter(CloudEnvelope[SpectrumUser]).validate_python(
                    await self._request(
                        "POST",
                        f"/projects/{self.project_id}/users/",
                        json={"type": "shared", "phoneNumber": phone_number},
                        idempotency_key=f"{idempotency_key}:user",
                    )
                )
            except ValidationError as error:
                raise SpectrumCloudError("Spectrum returned an invalid registered user") from error
            if not created.succeed:
                raise SpectrumCloudError("Spectrum rejected the user registration request")
            existing = created.data
        line = await self.line()
        request = chat_service_pb2.CreateChatRequest(
            addresses=(phone_number,),
            service=address_types_pb2.CHAT_SERVICE_TYPE_IMESSAGE,
            client_message_id=f"{idempotency_key}:conversation",
        )
        async with self.channel() as channel:
            try:
                response = await chat_service_pb2_grpc.ChatServiceStub(channel).CreateChat(
                    request,
                    metadata=rpc_metadata(line.token, f"{idempotency_key}:conversation"),
                    timeout=RPC_TIMEOUT_SECONDS,
                )
            except grpc.aio.AioRpcError as error:
                if error.code() != grpc.StatusCode.PERMISSION_DENIED:
                    raise
                raise TargetNotOptedIn(existing.assigned_phone_number) from error
        if not response.chat.guid:
            raise SpectrumCloudError("Spectrum returned a direct chat without an id")
        return RegisteredPhone(
            assigned_phone_number=existing.assigned_phone_number,
            conversation_id=response.chat.guid,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        headers = {} if idempotency_key is None else {"x-idempotency-key": idempotency_key}
        response = await self._loop().client.request(
            method,
            f"{SPECTRUM_CLOUD_URL}{path}",
            auth=httpx.BasicAuth(self.project_id, self.project_secret),
            headers=headers,
            json=json,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise SpectrumCloudError(
                f"Spectrum Cloud returned HTTP {response.status_code}"
            ) from error
        return response.json()

    def channel(self) -> grpc.aio.Channel:
        return grpc.aio.secure_channel(SPECTRUM_IMESSAGE_ADDRESS, grpc.ssl_channel_credentials())

    async def invalidate(self) -> None:
        state = self._loop()
        async with state.lock:
            state.token_state.clear()

    def invalid_cursor(self, error: Exception) -> bool:
        return (
            isinstance(error, grpc.aio.AioRpcError)
            and error.code() == grpc.StatusCode.INVALID_ARGUMENT
        )

    def external_error(self, error: Exception) -> bool:
        return isinstance(
            error, (grpc.aio.AioRpcError, SpectrumCloudError, TargetNotOptedIn, httpx.HTTPError)
        )

    def error_code(self, error: Exception) -> str:
        if isinstance(error, grpc.aio.AioRpcError):
            return error.code().name
        return type(error).__name__

    async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]:
        line = await self.line()
        request = event_service_pb2.CatchUpEventsRequest()
        if after_sequence is not None:
            request.after_sequence = after_sequence
        async with self.channel() as channel:
            stream = event_service_pb2_grpc.EventServiceStub(channel).CatchUpEvents(
                request, metadata=rpc_metadata(line.token)
            )
            async for frame in stream:
                payload = frame.WhichOneof("payload")
                if payload == "complete":
                    yield ProviderEvent(head_sequence=frame.complete.head_sequence)
                elif frame.HasField("sequence"):
                    yield ProviderEvent(
                        sequence=frame.sequence,
                        message=(
                            self._scoped_message(frame.message_changed)
                            if payload == "message_changed"
                            else None
                        ),
                    )

    async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]:
        line = await self.line()
        request = message_service_pb2.SubscribeMessageEventsRequest()
        async with self.channel() as channel:
            stream = message_service_pb2_grpc.MessageServiceStub(channel).SubscribeMessageEvents(
                request, metadata=rpc_metadata(line.token)
            )
            ready.set()
            async for frame in stream:
                yield ProviderEvent(
                    sequence=frame.sequence if frame.HasField("sequence") else None,
                    message=(
                        self._scoped_message(frame.message_changed)
                        if frame.WhichOneof("payload") == "message_changed"
                        else None
                    ),
                )

    def _scoped_message(self, event: object) -> InboundMessage | None:
        message = _inbound_message(event)
        if message is None or not self._phone_allowed(message.sender):
            return None
        return message

    def _phone_allowed(self, phone_number: str) -> bool:
        return phone_number not in self.blocked_phone_numbers and (
            self.allowed_phone_numbers is None or phone_number in self.allowed_phone_numbers
        )

    async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str:
        line = await self.line()
        request = message_service_pb2.SendTextMessageRequest(
            chat_guid=conversation_id,
            text=text,
            client_message_id=idempotency_key,
        )
        async with self.channel() as channel:
            try:
                response = await message_service_pb2_grpc.MessageServiceStub(
                    channel
                ).SendTextMessage(
                    request,
                    metadata=rpc_metadata(line.token, idempotency_key),
                    timeout=RPC_TIMEOUT_SECONDS,
                )
            except grpc.aio.AioRpcError as error:
                if error.code() != grpc.StatusCode.PERMISSION_DENIED:
                    raise
                raise TargetNotOptedIn from error
        return response.message.guid

    async def send_attachment(
        self,
        conversation_id: str,
        filename: str,
        data: bytes,
        idempotency_key: str,
    ) -> str:
        line = await self.line()
        async with self.channel() as channel:
            attachment = await attachment_service_pb2_grpc.AttachmentServiceStub(
                channel
            ).UploadAttachment(
                attachment_service_pb2.UploadAttachmentRequest(file_name=filename, data=data),
                metadata=rpc_metadata(line.token, f"{idempotency_key}:upload"),
                timeout=RPC_TIMEOUT_SECONDS,
            )
            response = await message_service_pb2_grpc.MessageServiceStub(
                channel
            ).SendAttachmentMessage(
                message_service_pb2.SendAttachmentMessageRequest(
                    chat_guid=conversation_id,
                    attachment={"attachment_guid": attachment.attachment.guid},
                    client_message_id=idempotency_key,
                ),
                metadata=rpc_metadata(line.token, idempotency_key),
                timeout=RPC_TIMEOUT_SECONDS,
            )
        return response.message.guid

    async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]:
        line = await self.line()
        async with self.channel() as channel:
            stream = attachment_service_pb2_grpc.AttachmentServiceStub(channel).DownloadAttachment(
                attachment_service_pb2.DownloadAttachmentRequest(attachment_guid=attachment_id),
                metadata=rpc_metadata(line.token),
                timeout=RPC_TIMEOUT_SECONDS,
            )
            async for frame in stream:
                if frame.WhichOneof("payload") == "primary_chunk":
                    yield frame.primary_chunk


@cache
def spectrum_project() -> SpectrumProject:
    project_id = os.environ.get(SPECTRUM_PROJECT_ID_ENV)
    project_secret = os.environ.get(SPECTRUM_PROJECT_SECRET_ENV)
    if not project_id or not project_secret:
        raise ProviderNotConfigured(
            f"Set {SPECTRUM_PROJECT_ID_ENV} and {SPECTRUM_PROJECT_SECRET_ENV}"
        )
    allowed_phone_numbers, blocked_phone_numbers = _phone_scope(
        os.environ.get(SPECTRUM_PHONE_SCOPE_ENV)
    )
    return SpectrumProject(
        project_id=project_id,
        project_secret=project_secret,
        client=httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS),
        lock=asyncio.Lock(),
        token_state={},
        allowed_phone_numbers=allowed_phone_numbers,
        blocked_phone_numbers=blocked_phone_numbers,
    )


def _phone_scope(value: str | None) -> tuple[frozenset[str] | None, frozenset[str]]:
    if value is None or not value.strip():
        return None, frozenset()
    try:
        mode, serialized = value.split(":", maxsplit=1)
    except ValueError as error:
        message = f"{SPECTRUM_PHONE_SCOPE_ENV} must start with allow: or block:"
        raise RuntimeError(message) from error
    phone_numbers = frozenset(phone.strip() for phone in serialized.split(",") if phone.strip())
    if not phone_numbers or any(E164_PATTERN.fullmatch(phone) is None for phone in phone_numbers):
        raise RuntimeError(f"{SPECTRUM_PHONE_SCOPE_ENV} must contain E.164 phone numbers")
    match mode:
        case "allow":
            return phone_numbers, frozenset()
        case "block":
            return None, phone_numbers
        case _:
            raise RuntimeError(f"{SPECTRUM_PHONE_SCOPE_ENV} must start with allow: or block:")


def rpc_metadata(token: str, idempotency_key: str | None = None) -> tuple[tuple[str, str], ...]:
    values = [("authorization", f"Bearer {token}")]
    if idempotency_key is not None:
        values.append(("x-idempotency-key", idempotency_key))
    return tuple(values)


def _inbound_message(event: object) -> InboundMessage | None:
    if not isinstance(event, message_types_pb2.MessageChangeEvent):
        return None
    if event.WhichOneof("change") != "message_received" or event.is_from_me:
        return None
    message = event.message_received.message
    if (
        message.is_from_me
        or message.is_system_message
        or message.is_spam
        or message.is_service_message
        or message.is_corrupt
    ):
        return None
    sender = event.actor.address if event.HasField("actor") else ""
    if not sender and message.HasField("sender"):
        sender = message.sender.address
    if not sender:
        return None
    attachments = tuple(
        MessageAttachment(
            id=attachment.guid,
            filename=attachment.file_name,
            size_bytes=attachment.total_bytes,
        )
        for attachment in message.content.attachments
        if not attachment.is_hidden and not attachment.is_sticker
    )
    text = message.content.text if message.content.HasField("text") else ""
    if not text and not attachments:
        return None
    return InboundMessage(
        id=message.guid,
        conversation_id=event.chat_guid,
        sender=sender,
        text=text,
        attachments=attachments,
        direct=";-;" in event.chat_guid,
    )
