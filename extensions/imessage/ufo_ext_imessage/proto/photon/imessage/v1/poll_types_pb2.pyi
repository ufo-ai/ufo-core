import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import address_types_pb2 as _address_types_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PollOption(_message.Message):
    __slots__ = ("text", "option_identifier", "creator_handle")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    OPTION_IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    CREATOR_HANDLE_FIELD_NUMBER: _ClassVar[int]
    text: str
    option_identifier: str
    creator_handle: str
    def __init__(self, text: _Optional[str] = ..., option_identifier: _Optional[str] = ..., creator_handle: _Optional[str] = ...) -> None: ...

class PollParticipantVote(_message.Message):
    __slots__ = ("participant", "option_identifier")
    PARTICIPANT_FIELD_NUMBER: _ClassVar[int]
    OPTION_IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    participant: _address_types_pb2.SingleServiceAddressInfo
    option_identifier: str
    def __init__(self, participant: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., option_identifier: _Optional[str] = ...) -> None: ...

class PollInfo(_message.Message):
    __slots__ = ("poll_message_guid", "chat_guid", "title", "options", "votes")
    POLL_MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    VOTES_FIELD_NUMBER: _ClassVar[int]
    poll_message_guid: str
    chat_guid: str
    title: str
    options: _containers.RepeatedCompositeFieldContainer[PollOption]
    votes: _containers.RepeatedCompositeFieldContainer[PollParticipantVote]
    def __init__(self, poll_message_guid: _Optional[str] = ..., chat_guid: _Optional[str] = ..., title: _Optional[str] = ..., options: _Optional[_Iterable[_Union[PollOption, _Mapping]]] = ..., votes: _Optional[_Iterable[_Union[PollParticipantVote, _Mapping]]] = ...) -> None: ...

class PollCreated(_message.Message):
    __slots__ = ("title", "options")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    title: str
    options: _containers.RepeatedCompositeFieldContainer[PollOption]
    def __init__(self, title: _Optional[str] = ..., options: _Optional[_Iterable[_Union[PollOption, _Mapping]]] = ...) -> None: ...

class PollOptionAdded(_message.Message):
    __slots__ = ("title", "options")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    title: str
    options: _containers.RepeatedCompositeFieldContainer[PollOption]
    def __init__(self, title: _Optional[str] = ..., options: _Optional[_Iterable[_Union[PollOption, _Mapping]]] = ...) -> None: ...

class PollVoted(_message.Message):
    __slots__ = ("option_identifier",)
    OPTION_IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    option_identifier: str
    def __init__(self, option_identifier: _Optional[str] = ...) -> None: ...

class PollUnvoted(_message.Message):
    __slots__ = ("option_identifier",)
    OPTION_IDENTIFIER_FIELD_NUMBER: _ClassVar[int]
    option_identifier: str
    def __init__(self, option_identifier: _Optional[str] = ...) -> None: ...

class PollChangeEvent(_message.Message):
    __slots__ = ("chat_guid", "poll_message_guid", "occurred_at", "actor", "is_from_me", "created", "option_added", "voted", "unvoted")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    POLL_MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    CREATED_FIELD_NUMBER: _ClassVar[int]
    OPTION_ADDED_FIELD_NUMBER: _ClassVar[int]
    VOTED_FIELD_NUMBER: _ClassVar[int]
    UNVOTED_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    poll_message_guid: str
    occurred_at: _timestamp_pb2.Timestamp
    actor: _address_types_pb2.SingleServiceAddressInfo
    is_from_me: bool
    created: PollCreated
    option_added: PollOptionAdded
    voted: PollVoted
    unvoted: PollUnvoted
    def __init__(self, chat_guid: _Optional[str] = ..., poll_message_guid: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., actor: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., is_from_me: bool = ..., created: _Optional[_Union[PollCreated, _Mapping]] = ..., option_added: _Optional[_Union[PollOptionAdded, _Mapping]] = ..., voted: _Optional[_Union[PollVoted, _Mapping]] = ..., unvoted: _Optional[_Union[PollUnvoted, _Mapping]] = ...) -> None: ...
