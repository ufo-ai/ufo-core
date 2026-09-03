import asyncio
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

import grpc
import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ufo.sdk.credentials import deploy_env
from ufo.sdk.http import plain_local
from ufo_ext_imessage.local_line import LocalLine
from ufo_ext_imessage.proto.photon.imessage.v1 import (
    attachment_service_pb2,
    attachment_service_pb2_grpc,
    event_service_pb2,
    event_service_pb2_grpc,
    message_service_pb2,
    message_service_pb2_grpc,
    message_types_pb2,
)
from ufo_ext_imessage.provider import (
    InboundMessage,
    MessageAttachment,
    MessageProvider,
    ProviderEvent,
    ProviderNotConfigured,
)

SPECTRUM_PROJECT_ID_ENV = "SPECTRUM_PROJECT_ID"
SPECTRUM_PROJECT_SECRET_ENV = "SPECTRUM_PROJECT_SECRET"
SPECTRUM_CLOUD_URL = "https://spectrum.photon.codes"
SPECTRUM_IMESSAGE_ADDRESS = "imessage.spectrum.photon.codes:443"
HTTP_TIMEOUT_SECONDS = 20.0
RPC_TIMEOUT_SECONDS = 30.0
TOKEN_REFRESH_MARGIN_SECONDS = 60


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

    async def assign_line(self, phone_number: str, idempotency_key: str) -> str:
        """The shared line Spectrum sends this phone's messages from, registering the phone the
        first time. A shared line cannot open a conversation with a phone that has not texted it, so
        the member's own first message is what opens one."""
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
        if existing is not None:
            return existing.assigned_phone_number
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
        return created.data.assigned_phone_number

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
        return isinstance(error, (grpc.aio.AioRpcError, SpectrumCloudError, httpx.HTTPError))

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
                            _inbound_message(frame.message_changed)
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
                        _inbound_message(frame.message_changed)
                        if frame.WhichOneof("payload") == "message_changed"
                        else None
                    ),
                )

    async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str:
        line = await self.line()
        request = message_service_pb2.SendTextMessageRequest(
            chat_guid=conversation_id,
            text=text,
            client_message_id=idempotency_key,
        )
        async with self.channel() as channel:
            response = await message_service_pb2_grpc.MessageServiceStub(channel).SendTextMessage(
                request,
                metadata=rpc_metadata(line.token, idempotency_key),
                timeout=RPC_TIMEOUT_SECONDS,
            )
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


NOT_CONFIGURED = (
    f"Set UFO_{SPECTRUM_PROJECT_ID_ENV} and UFO_{SPECTRUM_PROJECT_SECRET_ENV} "
    f"(or {SPECTRUM_PROJECT_ID_ENV} and {SPECTRUM_PROJECT_SECRET_ENV})"
)


def _spectrum_pair() -> tuple[str, str] | None:
    project_id = deploy_env(SPECTRUM_PROJECT_ID_ENV)
    project_secret = deploy_env(SPECTRUM_PROJECT_SECRET_ENV)
    if not project_id or not project_secret:
        return None
    return project_id, project_secret


def spectrum_configured() -> bool:
    """Whether this deploy carries the Spectrum project pair the iMessage provider signs in with."""
    return _spectrum_pair() is not None


def imessage_offered(public_base_url: str | None) -> bool:
    """Whether this deploy connects an iMessage phone: it holds the Spectrum project pair, or it is
    a plain-local dev deploy, which runs the connect flow on the local line."""
    return spectrum_configured() or plain_local(public_base_url)


def line_provider(public_base_url: str | None) -> MessageProvider:
    """The deploy's iMessage provider: the Spectrum project where its pair is set, the local line on
    a plain-local dev deploy, and a refusal naming the pair anywhere else."""
    if spectrum_configured():
        return spectrum_project()
    if plain_local(public_base_url):
        return LocalLine()
    raise ProviderNotConfigured(NOT_CONFIGURED)


@cache
def spectrum_project() -> SpectrumProject:
    pair = _spectrum_pair()
    if pair is None:
        raise ProviderNotConfigured(NOT_CONFIGURED)
    project_id, project_secret = pair
    return SpectrumProject(
        project_id=project_id,
        project_secret=project_secret,
        client=httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS),
        lock=asyncio.Lock(),
        token_state={},
    )


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
