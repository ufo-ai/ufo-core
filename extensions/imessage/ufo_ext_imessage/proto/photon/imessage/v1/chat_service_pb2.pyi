from ufo_ext_imessage.proto.google.api import annotations_pb2 as _annotations_pb2
from google.protobuf import empty_pb2 as _empty_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import address_types_pb2 as _address_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import chat_types_pb2 as _chat_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import message_types_pb2 as _message_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import streaming_pb2 as _streaming_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CreateChatRequest(_message.Message):
    __slots__ = ("addresses", "service", "initial_message", "client_message_id")
    ADDRESSES_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    INITIAL_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    addresses: _containers.RepeatedScalarFieldContainer[str]
    service: _address_types_pb2.ChatServiceType
    initial_message: CreateChatInitialMessage
    client_message_id: str
    def __init__(self, addresses: _Optional[_Iterable[str]] = ..., service: _Optional[_Union[_address_types_pb2.ChatServiceType, str]] = ..., initial_message: _Optional[_Union[CreateChatInitialMessage, _Mapping]] = ..., client_message_id: _Optional[str] = ...) -> None: ...

class CreateChatInitialMessage(_message.Message):
    __slots__ = ("text", "attributed_body", "effect_id", "subject")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    ATTRIBUTED_BODY_FIELD_NUMBER: _ClassVar[int]
    EFFECT_ID_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    text: str
    attributed_body: bytes
    effect_id: str
    subject: str
    def __init__(self, text: _Optional[str] = ..., attributed_body: _Optional[bytes] = ..., effect_id: _Optional[str] = ..., subject: _Optional[str] = ...) -> None: ...

class CreateChatResponse(_message.Message):
    __slots__ = ("chat", "initial_message")
    CHAT_FIELD_NUMBER: _ClassVar[int]
    INITIAL_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    chat: _chat_types_pb2.Chat
    initial_message: _message_types_pb2.Message
    def __init__(self, chat: _Optional[_Union[_chat_types_pb2.Chat, _Mapping]] = ..., initial_message: _Optional[_Union[_message_types_pb2.Message, _Mapping]] = ...) -> None: ...

class MarkChatReadRequest(_message.Message):
    __slots__ = ("chat_guid",)
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    def __init__(self, chat_guid: _Optional[str] = ...) -> None: ...

class SetBackgroundRequest(_message.Message):
    __slots__ = ("chat_guid", "data")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    data: bytes
    def __init__(self, chat_guid: _Optional[str] = ..., data: _Optional[bytes] = ...) -> None: ...

class RemoveBackgroundRequest(_message.Message):
    __slots__ = ("chat_guid",)
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    def __init__(self, chat_guid: _Optional[str] = ...) -> None: ...

class ShareContactInfoRequest(_message.Message):
    __slots__ = ("chat_guid",)
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    def __init__(self, chat_guid: _Optional[str] = ...) -> None: ...

class SetTypingRequest(_message.Message):
    __slots__ = ("chat_guid", "is_typing")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    IS_TYPING_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    is_typing: bool
    def __init__(self, chat_guid: _Optional[str] = ..., is_typing: bool = ...) -> None: ...

class GetChatRequest(_message.Message):
    __slots__ = ("chat_guid",)
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    def __init__(self, chat_guid: _Optional[str] = ...) -> None: ...

class GetChatResponse(_message.Message):
    __slots__ = ("chat",)
    CHAT_FIELD_NUMBER: _ClassVar[int]
    chat: _chat_types_pb2.Chat
    def __init__(self, chat: _Optional[_Union[_chat_types_pb2.Chat, _Mapping]] = ...) -> None: ...

class GetChatCountRequest(_message.Message):
    __slots__ = ("include_archived",)
    INCLUDE_ARCHIVED_FIELD_NUMBER: _ClassVar[int]
    include_archived: bool
    def __init__(self, include_archived: bool = ...) -> None: ...

class GetChatCountResponse(_message.Message):
    __slots__ = ("count",)
    COUNT_FIELD_NUMBER: _ClassVar[int]
    count: int
    def __init__(self, count: _Optional[int] = ...) -> None: ...

class HasBackgroundRequest(_message.Message):
    __slots__ = ("chat_guid",)
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    def __init__(self, chat_guid: _Optional[str] = ...) -> None: ...

class HasBackgroundResponse(_message.Message):
    __slots__ = ("background_present",)
    BACKGROUND_PRESENT_FIELD_NUMBER: _ClassVar[int]
    background_present: bool
    def __init__(self, background_present: bool = ...) -> None: ...

class SubscribeChatEventsRequest(_message.Message):
    __slots__ = ("chat_guid",)
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    def __init__(self, chat_guid: _Optional[str] = ...) -> None: ...

class SubscribeChatEventsResponse(_message.Message):
    __slots__ = ("sequence", "chat_changed", "heartbeat")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    CHAT_CHANGED_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    chat_changed: _chat_types_pb2.ChatChangeEvent
    heartbeat: _streaming_pb2.Heartbeat
    def __init__(self, sequence: _Optional[int] = ..., chat_changed: _Optional[_Union[_chat_types_pb2.ChatChangeEvent, _Mapping]] = ..., heartbeat: _Optional[_Union[_streaming_pb2.Heartbeat, _Mapping]] = ...) -> None: ...
