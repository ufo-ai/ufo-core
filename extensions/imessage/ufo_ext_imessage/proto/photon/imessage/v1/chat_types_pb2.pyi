import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import address_types_pb2 as _address_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import message_types_pb2 as _message_types_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Chat(_message.Message):
    __slots__ = ("guid", "chat_identifier", "group_id", "display_name", "is_group", "service", "is_archived", "is_filtered", "unread_count", "participants", "last_message")
    GUID_FIELD_NUMBER: _ClassVar[int]
    CHAT_IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    IS_GROUP_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    IS_ARCHIVED_FIELD_NUMBER: _ClassVar[int]
    IS_FILTERED_FIELD_NUMBER: _ClassVar[int]
    UNREAD_COUNT_FIELD_NUMBER: _ClassVar[int]
    PARTICIPANTS_FIELD_NUMBER: _ClassVar[int]
    LAST_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    guid: str
    chat_identifier: str
    group_id: str
    display_name: str
    is_group: bool
    service: _address_types_pb2.ChatServiceType
    is_archived: bool
    is_filtered: bool
    unread_count: int
    participants: _containers.RepeatedCompositeFieldContainer[_address_types_pb2.SingleServiceAddressInfo]
    last_message: _message_types_pb2.Message
    def __init__(self, guid: _Optional[str] = ..., chat_identifier: _Optional[str] = ..., group_id: _Optional[str] = ..., display_name: _Optional[str] = ..., is_group: bool = ..., service: _Optional[_Union[_address_types_pb2.ChatServiceType, str]] = ..., is_archived: bool = ..., is_filtered: bool = ..., unread_count: _Optional[int] = ..., participants: _Optional[_Iterable[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]]] = ..., last_message: _Optional[_Union[_message_types_pb2.Message, _Mapping]] = ...) -> None: ...

class ChatBackgroundChanged(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ChatBackgroundRemoved(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ChatMarkedRead(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ChatArchived(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ChatUnarchived(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ChatChangeEvent(_message.Message):
    __slots__ = ("chat_guid", "occurred_at", "actor", "is_from_me", "background_changed", "background_removed", "marked_read", "archived", "unarchived")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    BACKGROUND_CHANGED_FIELD_NUMBER: _ClassVar[int]
    BACKGROUND_REMOVED_FIELD_NUMBER: _ClassVar[int]
    MARKED_READ_FIELD_NUMBER: _ClassVar[int]
    ARCHIVED_FIELD_NUMBER: _ClassVar[int]
    UNARCHIVED_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    occurred_at: _timestamp_pb2.Timestamp
    actor: _address_types_pb2.SingleServiceAddressInfo
    is_from_me: bool
    background_changed: ChatBackgroundChanged
    background_removed: ChatBackgroundRemoved
    marked_read: ChatMarkedRead
    archived: ChatArchived
    unarchived: ChatUnarchived
    def __init__(self, chat_guid: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., actor: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., is_from_me: bool = ..., background_changed: _Optional[_Union[ChatBackgroundChanged, _Mapping]] = ..., background_removed: _Optional[_Union[ChatBackgroundRemoved, _Mapping]] = ..., marked_read: _Optional[_Union[ChatMarkedRead, _Mapping]] = ..., archived: _Optional[_Union[ChatArchived, _Mapping]] = ..., unarchived: _Optional[_Union[ChatUnarchived, _Mapping]] = ...) -> None: ...
