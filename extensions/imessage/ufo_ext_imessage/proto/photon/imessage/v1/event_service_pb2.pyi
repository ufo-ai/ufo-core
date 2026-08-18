from ufo_ext_imessage.proto.photon.imessage.v1 import chat_types_pb2 as _chat_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import group_types_pb2 as _group_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import message_types_pb2 as _message_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import poll_types_pb2 as _poll_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import streaming_pb2 as _streaming_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CatchUpEventsRequest(_message.Message):
    __slots__ = ("after_sequence",)
    AFTER_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    after_sequence: int
    def __init__(self, after_sequence: _Optional[int] = ...) -> None: ...

class CatchUpEventsComplete(_message.Message):
    __slots__ = ("head_sequence",)
    HEAD_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    head_sequence: int
    def __init__(self, head_sequence: _Optional[int] = ...) -> None: ...

class CatchUpEventsResponse(_message.Message):
    __slots__ = ("sequence", "message_changed", "group_changed", "poll_changed", "chat_changed", "complete", "heartbeat")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_CHANGED_FIELD_NUMBER: _ClassVar[int]
    GROUP_CHANGED_FIELD_NUMBER: _ClassVar[int]
    POLL_CHANGED_FIELD_NUMBER: _ClassVar[int]
    CHAT_CHANGED_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    message_changed: _message_types_pb2.MessageChangeEvent
    group_changed: _group_types_pb2.GroupChangeEvent
    poll_changed: _poll_types_pb2.PollChangeEvent
    chat_changed: _chat_types_pb2.ChatChangeEvent
    complete: CatchUpEventsComplete
    heartbeat: _streaming_pb2.Heartbeat
    def __init__(self, sequence: _Optional[int] = ..., message_changed: _Optional[_Union[_message_types_pb2.MessageChangeEvent, _Mapping]] = ..., group_changed: _Optional[_Union[_group_types_pb2.GroupChangeEvent, _Mapping]] = ..., poll_changed: _Optional[_Union[_poll_types_pb2.PollChangeEvent, _Mapping]] = ..., chat_changed: _Optional[_Union[_chat_types_pb2.ChatChangeEvent, _Mapping]] = ..., complete: _Optional[_Union[CatchUpEventsComplete, _Mapping]] = ..., heartbeat: _Optional[_Union[_streaming_pb2.Heartbeat, _Mapping]] = ...) -> None: ...
