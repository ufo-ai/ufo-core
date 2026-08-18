import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import address_types_pb2 as _address_types_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GroupDisplayNameChanged(_message.Message):
    __slots__ = ("display_name",)
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    display_name: str
    def __init__(self, display_name: _Optional[str] = ...) -> None: ...

class GroupParticipantAdded(_message.Message):
    __slots__ = ("participant",)
    PARTICIPANT_FIELD_NUMBER: _ClassVar[int]
    participant: _address_types_pb2.SingleServiceAddressInfo
    def __init__(self, participant: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ...) -> None: ...

class GroupParticipantRemoved(_message.Message):
    __slots__ = ("participant",)
    PARTICIPANT_FIELD_NUMBER: _ClassVar[int]
    participant: _address_types_pb2.SingleServiceAddressInfo
    def __init__(self, participant: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ...) -> None: ...

class GroupParticipantLeft(_message.Message):
    __slots__ = ("participant",)
    PARTICIPANT_FIELD_NUMBER: _ClassVar[int]
    participant: _address_types_pb2.SingleServiceAddressInfo
    def __init__(self, participant: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ...) -> None: ...

class GroupIconChanged(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GroupIconRemoved(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GroupChangeEvent(_message.Message):
    __slots__ = ("chat_guid", "occurred_at", "actor", "is_from_me", "display_name_changed", "participant_added", "participant_removed", "participant_left", "icon_changed", "icon_removed")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_CHANGED_FIELD_NUMBER: _ClassVar[int]
    PARTICIPANT_ADDED_FIELD_NUMBER: _ClassVar[int]
    PARTICIPANT_REMOVED_FIELD_NUMBER: _ClassVar[int]
    PARTICIPANT_LEFT_FIELD_NUMBER: _ClassVar[int]
    ICON_CHANGED_FIELD_NUMBER: _ClassVar[int]
    ICON_REMOVED_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    occurred_at: _timestamp_pb2.Timestamp
    actor: _address_types_pb2.SingleServiceAddressInfo
    is_from_me: bool
    display_name_changed: GroupDisplayNameChanged
    participant_added: GroupParticipantAdded
    participant_removed: GroupParticipantRemoved
    participant_left: GroupParticipantLeft
    icon_changed: GroupIconChanged
    icon_removed: GroupIconRemoved
    def __init__(self, chat_guid: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., actor: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., is_from_me: bool = ..., display_name_changed: _Optional[_Union[GroupDisplayNameChanged, _Mapping]] = ..., participant_added: _Optional[_Union[GroupParticipantAdded, _Mapping]] = ..., participant_removed: _Optional[_Union[GroupParticipantRemoved, _Mapping]] = ..., participant_left: _Optional[_Union[GroupParticipantLeft, _Mapping]] = ..., icon_changed: _Optional[_Union[GroupIconChanged, _Mapping]] = ..., icon_removed: _Optional[_Union[GroupIconRemoved, _Mapping]] = ...) -> None: ...
