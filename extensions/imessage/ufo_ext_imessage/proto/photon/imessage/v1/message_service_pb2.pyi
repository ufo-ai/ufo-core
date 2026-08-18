import datetime

from ufo_ext_imessage.proto.google.api import annotations_pb2 as _annotations_pb2
from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import message_types_pb2 as _message_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import streaming_pb2 as _streaming_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class MessageTarget(_message.Message):
    __slots__ = ("chat_guid", "message_guid", "target_part_index")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    message_guid: str
    target_part_index: int
    def __init__(self, chat_guid: _Optional[str] = ..., message_guid: _Optional[str] = ..., target_part_index: _Optional[int] = ...) -> None: ...

class SendTextMessageRequest(_message.Message):
    __slots__ = ("chat_guid", "text", "reply_to", "subject", "effect_id", "enable_data_detection", "enable_link_preview", "formatting", "client_message_id")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    REPLY_TO_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    EFFECT_ID_FIELD_NUMBER: _ClassVar[int]
    ENABLE_DATA_DETECTION_FIELD_NUMBER: _ClassVar[int]
    ENABLE_LINK_PREVIEW_FIELD_NUMBER: _ClassVar[int]
    FORMATTING_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    text: str
    reply_to: _message_types_pb2.ReplyTarget
    subject: str
    effect_id: str
    enable_data_detection: bool
    enable_link_preview: bool
    formatting: _containers.RepeatedCompositeFieldContainer[_message_types_pb2.TextFormat]
    client_message_id: str
    def __init__(self, chat_guid: _Optional[str] = ..., text: _Optional[str] = ..., reply_to: _Optional[_Union[_message_types_pb2.ReplyTarget, _Mapping]] = ..., subject: _Optional[str] = ..., effect_id: _Optional[str] = ..., enable_data_detection: bool = ..., enable_link_preview: bool = ..., formatting: _Optional[_Iterable[_Union[_message_types_pb2.TextFormat, _Mapping]]] = ..., client_message_id: _Optional[str] = ...) -> None: ...

class SendAttachmentMessageRequest(_message.Message):
    __slots__ = ("chat_guid", "attachment", "reply_to", "effect_id", "is_audio_message", "client_message_id")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_FIELD_NUMBER: _ClassVar[int]
    REPLY_TO_FIELD_NUMBER: _ClassVar[int]
    EFFECT_ID_FIELD_NUMBER: _ClassVar[int]
    IS_AUDIO_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    attachment: _message_types_pb2.AttachmentRef
    reply_to: _message_types_pb2.ReplyTarget
    effect_id: str
    is_audio_message: bool
    client_message_id: str
    def __init__(self, chat_guid: _Optional[str] = ..., attachment: _Optional[_Union[_message_types_pb2.AttachmentRef, _Mapping]] = ..., reply_to: _Optional[_Union[_message_types_pb2.ReplyTarget, _Mapping]] = ..., effect_id: _Optional[str] = ..., is_audio_message: bool = ..., client_message_id: _Optional[str] = ...) -> None: ...

class SendMultipartMessageRequest(_message.Message):
    __slots__ = ("chat_guid", "parts", "reply_to", "subject", "effect_id", "enable_data_detection", "client_message_id")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    PARTS_FIELD_NUMBER: _ClassVar[int]
    REPLY_TO_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    EFFECT_ID_FIELD_NUMBER: _ClassVar[int]
    ENABLE_DATA_DETECTION_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    parts: _containers.RepeatedCompositeFieldContainer[_message_types_pb2.MessagePart]
    reply_to: _message_types_pb2.ReplyTarget
    subject: str
    effect_id: str
    enable_data_detection: bool
    client_message_id: str
    def __init__(self, chat_guid: _Optional[str] = ..., parts: _Optional[_Iterable[_Union[_message_types_pb2.MessagePart, _Mapping]]] = ..., reply_to: _Optional[_Union[_message_types_pb2.ReplyTarget, _Mapping]] = ..., subject: _Optional[str] = ..., effect_id: _Optional[str] = ..., enable_data_detection: bool = ..., client_message_id: _Optional[str] = ...) -> None: ...

class MiniAppLayout(_message.Message):
    __slots__ = ("caption", "subcaption", "trailing_caption", "trailing_subcaption", "image", "image_title", "image_subtitle", "summary")
    CAPTION_FIELD_NUMBER: _ClassVar[int]
    SUBCAPTION_FIELD_NUMBER: _ClassVar[int]
    TRAILING_CAPTION_FIELD_NUMBER: _ClassVar[int]
    TRAILING_SUBCAPTION_FIELD_NUMBER: _ClassVar[int]
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    IMAGE_TITLE_FIELD_NUMBER: _ClassVar[int]
    IMAGE_SUBTITLE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    caption: str
    subcaption: str
    trailing_caption: str
    trailing_subcaption: str
    image: bytes
    image_title: str
    image_subtitle: str
    summary: str
    def __init__(self, caption: _Optional[str] = ..., subcaption: _Optional[str] = ..., trailing_caption: _Optional[str] = ..., trailing_subcaption: _Optional[str] = ..., image: _Optional[bytes] = ..., image_title: _Optional[str] = ..., image_subtitle: _Optional[str] = ..., summary: _Optional[str] = ...) -> None: ...

class SendCustomizedMiniAppMessageRequest(_message.Message):
    __slots__ = ("chat_guid", "team_id", "extension_bundle_id", "app_name", "url", "layout", "app_store_id", "live", "client_message_id")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_BUNDLE_ID_FIELD_NUMBER: _ClassVar[int]
    APP_NAME_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    LAYOUT_FIELD_NUMBER: _ClassVar[int]
    APP_STORE_ID_FIELD_NUMBER: _ClassVar[int]
    LIVE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    team_id: str
    extension_bundle_id: str
    app_name: str
    url: str
    layout: MiniAppLayout
    app_store_id: int
    live: bool
    client_message_id: str
    def __init__(self, chat_guid: _Optional[str] = ..., team_id: _Optional[str] = ..., extension_bundle_id: _Optional[str] = ..., app_name: _Optional[str] = ..., url: _Optional[str] = ..., layout: _Optional[_Union[MiniAppLayout, _Mapping]] = ..., app_store_id: _Optional[int] = ..., live: bool = ..., client_message_id: _Optional[str] = ...) -> None: ...

class MiniAppCardSession(_message.Message):
    __slots__ = ("message_guid", "chat_guid", "session_id", "target_message_guid")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    chat_guid: str
    session_id: str
    target_message_guid: str
    def __init__(self, message_guid: _Optional[str] = ..., chat_guid: _Optional[str] = ..., session_id: _Optional[str] = ..., target_message_guid: _Optional[str] = ...) -> None: ...

class UpdateCustomizedMiniAppMessageRequest(_message.Message):
    __slots__ = ("session", "team_id", "extension_bundle_id", "app_name", "url", "layout", "app_store_id", "live", "client_message_id")
    SESSION_FIELD_NUMBER: _ClassVar[int]
    TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_BUNDLE_ID_FIELD_NUMBER: _ClassVar[int]
    APP_NAME_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    LAYOUT_FIELD_NUMBER: _ClassVar[int]
    APP_STORE_ID_FIELD_NUMBER: _ClassVar[int]
    LIVE_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session: MiniAppCardSession
    team_id: str
    extension_bundle_id: str
    app_name: str
    url: str
    layout: MiniAppLayout
    app_store_id: int
    live: bool
    client_message_id: str
    def __init__(self, session: _Optional[_Union[MiniAppCardSession, _Mapping]] = ..., team_id: _Optional[str] = ..., extension_bundle_id: _Optional[str] = ..., app_name: _Optional[str] = ..., url: _Optional[str] = ..., layout: _Optional[_Union[MiniAppLayout, _Mapping]] = ..., app_store_id: _Optional[int] = ..., live: bool = ..., client_message_id: _Optional[str] = ...) -> None: ...

class MessageResponse(_message.Message):
    __slots__ = ("message", "mini_app_card_session")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    MINI_APP_CARD_SESSION_FIELD_NUMBER: _ClassVar[int]
    message: _message_types_pb2.Message
    mini_app_card_session: MiniAppCardSession
    def __init__(self, message: _Optional[_Union[_message_types_pb2.Message, _Mapping]] = ..., mini_app_card_session: _Optional[_Union[MiniAppCardSession, _Mapping]] = ...) -> None: ...

class EditMessageRequest(_message.Message):
    __slots__ = ("target", "new_text", "backward_compat_text", "client_message_id")
    TARGET_FIELD_NUMBER: _ClassVar[int]
    NEW_TEXT_FIELD_NUMBER: _ClassVar[int]
    BACKWARD_COMPAT_TEXT_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    target: MessageTarget
    new_text: str
    backward_compat_text: str
    client_message_id: str
    def __init__(self, target: _Optional[_Union[MessageTarget, _Mapping]] = ..., new_text: _Optional[str] = ..., backward_compat_text: _Optional[str] = ..., client_message_id: _Optional[str] = ...) -> None: ...

class UnsendMessageRequest(_message.Message):
    __slots__ = ("target", "client_message_id")
    TARGET_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    target: MessageTarget
    client_message_id: str
    def __init__(self, target: _Optional[_Union[MessageTarget, _Mapping]] = ..., client_message_id: _Optional[str] = ...) -> None: ...

class SetReactionRequest(_message.Message):
    __slots__ = ("target", "reaction", "is_set", "client_message_id")
    TARGET_FIELD_NUMBER: _ClassVar[int]
    REACTION_FIELD_NUMBER: _ClassVar[int]
    IS_SET_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    target: MessageTarget
    reaction: _message_types_pb2.MessageReaction
    is_set: bool
    client_message_id: str
    def __init__(self, target: _Optional[_Union[MessageTarget, _Mapping]] = ..., reaction: _Optional[_Union[_message_types_pb2.MessageReaction, _Mapping]] = ..., is_set: bool = ..., client_message_id: _Optional[str] = ...) -> None: ...

class PlaceStickerRequest(_message.Message):
    __slots__ = ("target", "sticker", "placement", "client_message_id")
    TARGET_FIELD_NUMBER: _ClassVar[int]
    STICKER_FIELD_NUMBER: _ClassVar[int]
    PLACEMENT_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    target: MessageTarget
    sticker: _message_types_pb2.AttachmentRef
    placement: _message_types_pb2.StickerPlacement
    client_message_id: str
    def __init__(self, target: _Optional[_Union[MessageTarget, _Mapping]] = ..., sticker: _Optional[_Union[_message_types_pb2.AttachmentRef, _Mapping]] = ..., placement: _Optional[_Union[_message_types_pb2.StickerPlacement, _Mapping]] = ..., client_message_id: _Optional[str] = ...) -> None: ...

class NotifySilencedMessageRequest(_message.Message):
    __slots__ = ("chat_guid", "message_guid", "client_message_id")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    message_guid: str
    client_message_id: str
    def __init__(self, chat_guid: _Optional[str] = ..., message_guid: _Optional[str] = ..., client_message_id: _Optional[str] = ...) -> None: ...

class GetMessageRequest(_message.Message):
    __slots__ = ("message_guid",)
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    def __init__(self, message_guid: _Optional[str] = ...) -> None: ...

class GetMessageResponse(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: _message_types_pb2.Message
    def __init__(self, message: _Optional[_Union[_message_types_pb2.Message, _Mapping]] = ...) -> None: ...

class ListRecentMessagesRequest(_message.Message):
    __slots__ = ("page_size", "page_token", "is_from_me", "is_read", "before", "after")
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    IS_READ_FIELD_NUMBER: _ClassVar[int]
    BEFORE_FIELD_NUMBER: _ClassVar[int]
    AFTER_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    page_token: str
    is_from_me: bool
    is_read: bool
    before: _timestamp_pb2.Timestamp
    after: _timestamp_pb2.Timestamp
    def __init__(self, page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., is_from_me: bool = ..., is_read: bool = ..., before: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., after: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListRecentMessagesResponse(_message.Message):
    __slots__ = ("messages", "next_page_token")
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[_message_types_pb2.Message]
    next_page_token: str
    def __init__(self, messages: _Optional[_Iterable[_Union[_message_types_pb2.Message, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class ListChatMessagesRequest(_message.Message):
    __slots__ = ("chat_guid", "page_size", "page_token", "is_from_me", "is_read", "before", "after")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    IS_READ_FIELD_NUMBER: _ClassVar[int]
    BEFORE_FIELD_NUMBER: _ClassVar[int]
    AFTER_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    page_size: int
    page_token: str
    is_from_me: bool
    is_read: bool
    before: _timestamp_pb2.Timestamp
    after: _timestamp_pb2.Timestamp
    def __init__(self, chat_guid: _Optional[str] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., is_from_me: bool = ..., is_read: bool = ..., before: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., after: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListChatMessagesResponse(_message.Message):
    __slots__ = ("messages", "next_page_token")
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[_message_types_pb2.Message]
    next_page_token: str
    def __init__(self, messages: _Optional[_Iterable[_Union[_message_types_pb2.Message, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class GetEmbeddedMediaRequest(_message.Message):
    __slots__ = ("chat_guid", "message_guid")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    message_guid: str
    def __init__(self, chat_guid: _Optional[str] = ..., message_guid: _Optional[str] = ...) -> None: ...

class GetEmbeddedMediaResponse(_message.Message):
    __slots__ = ("media",)
    MEDIA_FIELD_NUMBER: _ClassVar[int]
    media: _message_types_pb2.EmbeddedMedia
    def __init__(self, media: _Optional[_Union[_message_types_pb2.EmbeddedMedia, _Mapping]] = ...) -> None: ...

class SubscribeMessageEventsRequest(_message.Message):
    __slots__ = ("chat_guid",)
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    def __init__(self, chat_guid: _Optional[str] = ...) -> None: ...

class SubscribeMessageEventsResponse(_message.Message):
    __slots__ = ("sequence", "message_changed", "heartbeat")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_CHANGED_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    message_changed: _message_types_pb2.MessageChangeEvent
    heartbeat: _streaming_pb2.Heartbeat
    def __init__(self, sequence: _Optional[int] = ..., message_changed: _Optional[_Union[_message_types_pb2.MessageChangeEvent, _Mapping]] = ..., heartbeat: _Optional[_Union[_streaming_pb2.Heartbeat, _Mapping]] = ...) -> None: ...
