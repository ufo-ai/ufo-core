from ufo_ext_imessage.proto.google.api import annotations_pb2 as _annotations_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import attachment_types_pb2 as _attachment_types_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetAttachmentInfoRequest(_message.Message):
    __slots__ = ("attachment_guid",)
    ATTACHMENT_GUID_FIELD_NUMBER: _ClassVar[int]
    attachment_guid: str
    def __init__(self, attachment_guid: _Optional[str] = ...) -> None: ...

class GetAttachmentInfoResponse(_message.Message):
    __slots__ = ("attachment",)
    ATTACHMENT_FIELD_NUMBER: _ClassVar[int]
    attachment: _attachment_types_pb2.AttachmentInfo
    def __init__(self, attachment: _Optional[_Union[_attachment_types_pb2.AttachmentInfo, _Mapping]] = ...) -> None: ...

class UploadAttachmentRequest(_message.Message):
    __slots__ = ("file_name", "data", "companion")
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    COMPANION_FIELD_NUMBER: _ClassVar[int]
    file_name: str
    data: bytes
    companion: _attachment_types_pb2.Companion
    def __init__(self, file_name: _Optional[str] = ..., data: _Optional[bytes] = ..., companion: _Optional[_Union[_attachment_types_pb2.Companion, _Mapping]] = ...) -> None: ...

class UploadAttachmentResponse(_message.Message):
    __slots__ = ("attachment", "companion")
    ATTACHMENT_FIELD_NUMBER: _ClassVar[int]
    COMPANION_FIELD_NUMBER: _ClassVar[int]
    attachment: _attachment_types_pb2.AttachmentInfo
    companion: _attachment_types_pb2.CompanionInfo
    def __init__(self, attachment: _Optional[_Union[_attachment_types_pb2.AttachmentInfo, _Mapping]] = ..., companion: _Optional[_Union[_attachment_types_pb2.CompanionInfo, _Mapping]] = ...) -> None: ...

class DownloadAttachmentRequest(_message.Message):
    __slots__ = ("attachment_guid",)
    ATTACHMENT_GUID_FIELD_NUMBER: _ClassVar[int]
    attachment_guid: str
    def __init__(self, attachment_guid: _Optional[str] = ...) -> None: ...

class DownloadHeader(_message.Message):
    __slots__ = ("attachment", "companion")
    ATTACHMENT_FIELD_NUMBER: _ClassVar[int]
    COMPANION_FIELD_NUMBER: _ClassVar[int]
    attachment: _attachment_types_pb2.AttachmentInfo
    companion: _attachment_types_pb2.CompanionInfo
    def __init__(self, attachment: _Optional[_Union[_attachment_types_pb2.AttachmentInfo, _Mapping]] = ..., companion: _Optional[_Union[_attachment_types_pb2.CompanionInfo, _Mapping]] = ...) -> None: ...

class DownloadAttachmentResponse(_message.Message):
    __slots__ = ("header", "primary_chunk", "companion_chunk")
    HEADER_FIELD_NUMBER: _ClassVar[int]
    PRIMARY_CHUNK_FIELD_NUMBER: _ClassVar[int]
    COMPANION_CHUNK_FIELD_NUMBER: _ClassVar[int]
    header: DownloadHeader
    primary_chunk: bytes
    companion_chunk: bytes
    def __init__(self, header: _Optional[_Union[DownloadHeader, _Mapping]] = ..., primary_chunk: _Optional[bytes] = ..., companion_chunk: _Optional[bytes] = ...) -> None: ...
