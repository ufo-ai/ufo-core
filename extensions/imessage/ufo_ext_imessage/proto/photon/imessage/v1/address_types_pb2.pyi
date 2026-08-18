from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ChatServiceType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHAT_SERVICE_TYPE_UNSPECIFIED: _ClassVar[ChatServiceType]
    CHAT_SERVICE_TYPE_IMESSAGE: _ClassVar[ChatServiceType]
    CHAT_SERVICE_TYPE_SMS: _ClassVar[ChatServiceType]
    CHAT_SERVICE_TYPE_RCS: _ClassVar[ChatServiceType]
CHAT_SERVICE_TYPE_UNSPECIFIED: ChatServiceType
CHAT_SERVICE_TYPE_IMESSAGE: ChatServiceType
CHAT_SERVICE_TYPE_SMS: ChatServiceType
CHAT_SERVICE_TYPE_RCS: ChatServiceType

class SingleServiceAddressInfo(_message.Message):
    __slots__ = ("address", "service", "country")
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    address: str
    service: ChatServiceType
    country: str
    def __init__(self, address: _Optional[str] = ..., service: _Optional[_Union[ChatServiceType, str]] = ..., country: _Optional[str] = ...) -> None: ...

class MultiServiceAddressInfo(_message.Message):
    __slots__ = ("address", "services", "country")
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    SERVICES_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    address: str
    services: _containers.RepeatedScalarFieldContainer[ChatServiceType]
    country: str
    def __init__(self, address: _Optional[str] = ..., services: _Optional[_Iterable[_Union[ChatServiceType, str]]] = ..., country: _Optional[str] = ...) -> None: ...
