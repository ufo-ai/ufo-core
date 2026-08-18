from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TransferState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TRANSFER_STATE_PENDING: _ClassVar[TransferState]
    TRANSFER_STATE_UNAVAILABLE: _ClassVar[TransferState]
    TRANSFER_STATE_TRANSFERRING: _ClassVar[TransferState]
    TRANSFER_STATE_FAILED: _ClassVar[TransferState]
    TRANSFER_STATE_FINISHED: _ClassVar[TransferState]
    TRANSFER_STATE_UNKNOWN: _ClassVar[TransferState]

class CompanionKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    COMPANION_KIND_UNSPECIFIED: _ClassVar[CompanionKind]
    COMPANION_KIND_LIVE_PHOTO_VIDEO: _ClassVar[CompanionKind]
TRANSFER_STATE_PENDING: TransferState
TRANSFER_STATE_UNAVAILABLE: TransferState
TRANSFER_STATE_TRANSFERRING: TransferState
TRANSFER_STATE_FAILED: TransferState
TRANSFER_STATE_FINISHED: TransferState
TRANSFER_STATE_UNKNOWN: TransferState
COMPANION_KIND_UNSPECIFIED: CompanionKind
COMPANION_KIND_LIVE_PHOTO_VIDEO: CompanionKind

class AttachmentInfo(_message.Message):
    __slots__ = ("guid", "original_guid", "file_name", "mime_type", "uti", "total_bytes", "is_outgoing", "transfer_state", "is_hidden", "is_sticker", "companion_kind")
    GUID_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_GUID_FIELD_NUMBER: _ClassVar[int]
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    UTI_FIELD_NUMBER: _ClassVar[int]
    TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    IS_OUTGOING_FIELD_NUMBER: _ClassVar[int]
    TRANSFER_STATE_FIELD_NUMBER: _ClassVar[int]
    IS_HIDDEN_FIELD_NUMBER: _ClassVar[int]
    IS_STICKER_FIELD_NUMBER: _ClassVar[int]
    COMPANION_KIND_FIELD_NUMBER: _ClassVar[int]
    guid: str
    original_guid: str
    file_name: str
    mime_type: str
    uti: str
    total_bytes: int
    is_outgoing: bool
    transfer_state: TransferState
    is_hidden: bool
    is_sticker: bool
    companion_kind: CompanionKind
    def __init__(self, guid: _Optional[str] = ..., original_guid: _Optional[str] = ..., file_name: _Optional[str] = ..., mime_type: _Optional[str] = ..., uti: _Optional[str] = ..., total_bytes: _Optional[int] = ..., is_outgoing: bool = ..., transfer_state: _Optional[_Union[TransferState, str]] = ..., is_hidden: bool = ..., is_sticker: bool = ..., companion_kind: _Optional[_Union[CompanionKind, str]] = ...) -> None: ...

class CompanionInfo(_message.Message):
    __slots__ = ("file_name", "mime_type", "total_bytes", "kind")
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    file_name: str
    mime_type: str
    total_bytes: int
    kind: CompanionKind
    def __init__(self, file_name: _Optional[str] = ..., mime_type: _Optional[str] = ..., total_bytes: _Optional[int] = ..., kind: _Optional[_Union[CompanionKind, str]] = ...) -> None: ...

class Companion(_message.Message):
    __slots__ = ("data", "kind")
    DATA_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    kind: CompanionKind
    def __init__(self, data: _Optional[bytes] = ..., kind: _Optional[_Union[CompanionKind, str]] = ...) -> None: ...
