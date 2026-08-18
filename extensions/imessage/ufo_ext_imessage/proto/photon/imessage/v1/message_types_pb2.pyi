import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import address_types_pb2 as _address_types_pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import attachment_types_pb2 as _attachment_types_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class MessageItemType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MESSAGE_ITEM_TYPE_NORMAL: _ClassVar[MessageItemType]
    MESSAGE_ITEM_TYPE_PARTICIPANT_CHANGE: _ClassVar[MessageItemType]
    MESSAGE_ITEM_TYPE_GROUP_NAME_CHANGE: _ClassVar[MessageItemType]
    MESSAGE_ITEM_TYPE_CHAT_ACTION: _ClassVar[MessageItemType]

class MessageReactionKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MESSAGE_REACTION_KIND_LOVE: _ClassVar[MessageReactionKind]
    MESSAGE_REACTION_KIND_LIKE: _ClassVar[MessageReactionKind]
    MESSAGE_REACTION_KIND_DISLIKE: _ClassVar[MessageReactionKind]
    MESSAGE_REACTION_KIND_LAUGH: _ClassVar[MessageReactionKind]
    MESSAGE_REACTION_KIND_EMPHASIZE: _ClassVar[MessageReactionKind]
    MESSAGE_REACTION_KIND_QUESTION: _ClassVar[MessageReactionKind]
    MESSAGE_REACTION_KIND_EMOJI: _ClassVar[MessageReactionKind]
    MESSAGE_REACTION_KIND_STICKER: _ClassVar[MessageReactionKind]
MESSAGE_ITEM_TYPE_NORMAL: MessageItemType
MESSAGE_ITEM_TYPE_PARTICIPANT_CHANGE: MessageItemType
MESSAGE_ITEM_TYPE_GROUP_NAME_CHANGE: MessageItemType
MESSAGE_ITEM_TYPE_CHAT_ACTION: MessageItemType
MESSAGE_REACTION_KIND_LOVE: MessageReactionKind
MESSAGE_REACTION_KIND_LIKE: MessageReactionKind
MESSAGE_REACTION_KIND_DISLIKE: MessageReactionKind
MESSAGE_REACTION_KIND_LAUGH: MessageReactionKind
MESSAGE_REACTION_KIND_EMPHASIZE: MessageReactionKind
MESSAGE_REACTION_KIND_QUESTION: MessageReactionKind
MESSAGE_REACTION_KIND_EMOJI: MessageReactionKind
MESSAGE_REACTION_KIND_STICKER: MessageReactionKind

class MessageReaction(_message.Message):
    __slots__ = ("kind", "emoji")
    KIND_FIELD_NUMBER: _ClassVar[int]
    EMOJI_FIELD_NUMBER: _ClassVar[int]
    kind: MessageReactionKind
    emoji: str
    def __init__(self, kind: _Optional[_Union[MessageReactionKind, str]] = ..., emoji: _Optional[str] = ...) -> None: ...

class MessageMention(_message.Message):
    __slots__ = ("address", "start", "length")
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    LENGTH_FIELD_NUMBER: _ClassVar[int]
    address: str
    start: int
    length: int
    def __init__(self, address: _Optional[str] = ..., start: _Optional[int] = ..., length: _Optional[int] = ...) -> None: ...

class TextFormat(_message.Message):
    __slots__ = ("type", "start", "length", "effect_name")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    LENGTH_FIELD_NUMBER: _ClassVar[int]
    EFFECT_NAME_FIELD_NUMBER: _ClassVar[int]
    type: str
    start: int
    length: int
    effect_name: str
    def __init__(self, type: _Optional[str] = ..., start: _Optional[int] = ..., length: _Optional[int] = ..., effect_name: _Optional[str] = ...) -> None: ...

class AttachmentRef(_message.Message):
    __slots__ = ("attachment_path", "attachment_guid", "attachment_name")
    ATTACHMENT_PATH_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_GUID_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_NAME_FIELD_NUMBER: _ClassVar[int]
    attachment_path: str
    attachment_guid: str
    attachment_name: str
    def __init__(self, attachment_path: _Optional[str] = ..., attachment_guid: _Optional[str] = ..., attachment_name: _Optional[str] = ...) -> None: ...

class ReplyTarget(_message.Message):
    __slots__ = ("message_guid", "target_part_index")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    target_part_index: int
    def __init__(self, message_guid: _Optional[str] = ..., target_part_index: _Optional[int] = ...) -> None: ...

class StickerPlacement(_message.Message):
    __slots__ = ("x", "y", "scale", "rotation", "width")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    SCALE_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    scale: float
    rotation: float
    width: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., scale: _Optional[float] = ..., rotation: _Optional[float] = ..., width: _Optional[float] = ...) -> None: ...

class MessagePart(_message.Message):
    __slots__ = ("text", "attachment", "mentioned_address", "bubble_index", "formatting")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_FIELD_NUMBER: _ClassVar[int]
    MENTIONED_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_INDEX_FIELD_NUMBER: _ClassVar[int]
    FORMATTING_FIELD_NUMBER: _ClassVar[int]
    text: str
    attachment: AttachmentRef
    mentioned_address: str
    bubble_index: int
    formatting: _containers.RepeatedCompositeFieldContainer[TextFormat]
    def __init__(self, text: _Optional[str] = ..., attachment: _Optional[_Union[AttachmentRef, _Mapping]] = ..., mentioned_address: _Optional[str] = ..., bubble_index: _Optional[int] = ..., formatting: _Optional[_Iterable[_Union[TextFormat, _Mapping]]] = ...) -> None: ...

class MiniAppLayoutInfo(_message.Message):
    __slots__ = ("caption", "subcaption", "trailing_caption", "trailing_subcaption", "image_title", "image_subtitle", "summary")
    CAPTION_FIELD_NUMBER: _ClassVar[int]
    SUBCAPTION_FIELD_NUMBER: _ClassVar[int]
    TRAILING_CAPTION_FIELD_NUMBER: _ClassVar[int]
    TRAILING_SUBCAPTION_FIELD_NUMBER: _ClassVar[int]
    IMAGE_TITLE_FIELD_NUMBER: _ClassVar[int]
    IMAGE_SUBTITLE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    caption: str
    subcaption: str
    trailing_caption: str
    trailing_subcaption: str
    image_title: str
    image_subtitle: str
    summary: str
    def __init__(self, caption: _Optional[str] = ..., subcaption: _Optional[str] = ..., trailing_caption: _Optional[str] = ..., trailing_subcaption: _Optional[str] = ..., image_title: _Optional[str] = ..., image_subtitle: _Optional[str] = ..., summary: _Optional[str] = ...) -> None: ...

class MiniAppContent(_message.Message):
    __slots__ = ("team_id", "extension_bundle_id", "app_name", "url", "session_id", "app_store_id", "live", "layout")
    TEAM_ID_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_BUNDLE_ID_FIELD_NUMBER: _ClassVar[int]
    APP_NAME_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    APP_STORE_ID_FIELD_NUMBER: _ClassVar[int]
    LIVE_FIELD_NUMBER: _ClassVar[int]
    LAYOUT_FIELD_NUMBER: _ClassVar[int]
    team_id: str
    extension_bundle_id: str
    app_name: str
    url: str
    session_id: str
    app_store_id: int
    live: bool
    layout: MiniAppLayoutInfo
    def __init__(self, team_id: _Optional[str] = ..., extension_bundle_id: _Optional[str] = ..., app_name: _Optional[str] = ..., url: _Optional[str] = ..., session_id: _Optional[str] = ..., app_store_id: _Optional[int] = ..., live: bool = ..., layout: _Optional[_Union[MiniAppLayoutInfo, _Mapping]] = ...) -> None: ...

class MessageContent(_message.Message):
    __slots__ = ("text", "attachments", "formatting", "mentions", "balloon_bundle_id", "expressive_send_style_id", "mini_app")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENTS_FIELD_NUMBER: _ClassVar[int]
    FORMATTING_FIELD_NUMBER: _ClassVar[int]
    MENTIONS_FIELD_NUMBER: _ClassVar[int]
    BALLOON_BUNDLE_ID_FIELD_NUMBER: _ClassVar[int]
    EXPRESSIVE_SEND_STYLE_ID_FIELD_NUMBER: _ClassVar[int]
    MINI_APP_FIELD_NUMBER: _ClassVar[int]
    text: str
    attachments: _containers.RepeatedCompositeFieldContainer[_attachment_types_pb2.AttachmentInfo]
    formatting: _containers.RepeatedCompositeFieldContainer[TextFormat]
    mentions: _containers.RepeatedCompositeFieldContainer[MessageMention]
    balloon_bundle_id: str
    expressive_send_style_id: str
    mini_app: MiniAppContent
    def __init__(self, text: _Optional[str] = ..., attachments: _Optional[_Iterable[_Union[_attachment_types_pb2.AttachmentInfo, _Mapping]]] = ..., formatting: _Optional[_Iterable[_Union[TextFormat, _Mapping]]] = ..., mentions: _Optional[_Iterable[_Union[MessageMention, _Mapping]]] = ..., balloon_bundle_id: _Optional[str] = ..., expressive_send_style_id: _Optional[str] = ..., mini_app: _Optional[_Union[MiniAppContent, _Mapping]] = ...) -> None: ...

class MessageAppliedReaction(_message.Message):
    __slots__ = ("message_guid", "target_part_index", "reaction", "sender", "is_from_me", "date_created")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    REACTION_FIELD_NUMBER: _ClassVar[int]
    SENDER_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    DATE_CREATED_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    target_part_index: int
    reaction: MessageReaction
    sender: _address_types_pb2.SingleServiceAddressInfo
    is_from_me: bool
    date_created: _timestamp_pb2.Timestamp
    def __init__(self, message_guid: _Optional[str] = ..., target_part_index: _Optional[int] = ..., reaction: _Optional[_Union[MessageReaction, _Mapping]] = ..., sender: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., is_from_me: bool = ..., date_created: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class MessagePlacedSticker(_message.Message):
    __slots__ = ("message_guid", "target_part_index", "sender", "is_from_me", "date_created", "sticker", "placement")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    SENDER_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    DATE_CREATED_FIELD_NUMBER: _ClassVar[int]
    STICKER_FIELD_NUMBER: _ClassVar[int]
    PLACEMENT_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    target_part_index: int
    sender: _address_types_pb2.SingleServiceAddressInfo
    is_from_me: bool
    date_created: _timestamp_pb2.Timestamp
    sticker: _attachment_types_pb2.AttachmentInfo
    placement: StickerPlacement
    def __init__(self, message_guid: _Optional[str] = ..., target_part_index: _Optional[int] = ..., sender: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., is_from_me: bool = ..., date_created: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., sticker: _Optional[_Union[_attachment_types_pb2.AttachmentInfo, _Mapping]] = ..., placement: _Optional[_Union[StickerPlacement, _Mapping]] = ...) -> None: ...

class Message(_message.Message):
    __slots__ = ("guid", "content", "subject", "date_created", "date_read", "date_delivered", "date_edited", "date_retracted", "date_played", "date_expressive_send_played", "sender", "is_from_me", "is_sent", "is_delivered", "is_delivered_quietly", "did_notify_recipient", "send_error_code", "is_audio_message", "is_auto_reply", "is_system_message", "is_forward", "is_delayed", "is_spam", "data_detector_results_present", "is_archived", "is_service_message", "is_corrupt", "is_expirable", "share_status", "share_direction", "item_type", "group_title", "chat_action_type", "thread_originator_guid", "thread_originator_part", "reply_to_guid", "reply_target_guid", "destination_caller_id", "part_count", "cached_room_names", "reaction_target_guid", "reaction_target_part_index", "reaction", "reaction_selected", "applied_reactions", "placed_stickers", "chat_guids")
    GUID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    DATE_CREATED_FIELD_NUMBER: _ClassVar[int]
    DATE_READ_FIELD_NUMBER: _ClassVar[int]
    DATE_DELIVERED_FIELD_NUMBER: _ClassVar[int]
    DATE_EDITED_FIELD_NUMBER: _ClassVar[int]
    DATE_RETRACTED_FIELD_NUMBER: _ClassVar[int]
    DATE_PLAYED_FIELD_NUMBER: _ClassVar[int]
    DATE_EXPRESSIVE_SEND_PLAYED_FIELD_NUMBER: _ClassVar[int]
    SENDER_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    IS_SENT_FIELD_NUMBER: _ClassVar[int]
    IS_DELIVERED_FIELD_NUMBER: _ClassVar[int]
    IS_DELIVERED_QUIETLY_FIELD_NUMBER: _ClassVar[int]
    DID_NOTIFY_RECIPIENT_FIELD_NUMBER: _ClassVar[int]
    SEND_ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    IS_AUDIO_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    IS_AUTO_REPLY_FIELD_NUMBER: _ClassVar[int]
    IS_SYSTEM_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    IS_FORWARD_FIELD_NUMBER: _ClassVar[int]
    IS_DELAYED_FIELD_NUMBER: _ClassVar[int]
    IS_SPAM_FIELD_NUMBER: _ClassVar[int]
    DATA_DETECTOR_RESULTS_PRESENT_FIELD_NUMBER: _ClassVar[int]
    IS_ARCHIVED_FIELD_NUMBER: _ClassVar[int]
    IS_SERVICE_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    IS_CORRUPT_FIELD_NUMBER: _ClassVar[int]
    IS_EXPIRABLE_FIELD_NUMBER: _ClassVar[int]
    SHARE_STATUS_FIELD_NUMBER: _ClassVar[int]
    SHARE_DIRECTION_FIELD_NUMBER: _ClassVar[int]
    ITEM_TYPE_FIELD_NUMBER: _ClassVar[int]
    GROUP_TITLE_FIELD_NUMBER: _ClassVar[int]
    CHAT_ACTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    THREAD_ORIGINATOR_GUID_FIELD_NUMBER: _ClassVar[int]
    THREAD_ORIGINATOR_PART_FIELD_NUMBER: _ClassVar[int]
    REPLY_TO_GUID_FIELD_NUMBER: _ClassVar[int]
    REPLY_TARGET_GUID_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_CALLER_ID_FIELD_NUMBER: _ClassVar[int]
    PART_COUNT_FIELD_NUMBER: _ClassVar[int]
    CACHED_ROOM_NAMES_FIELD_NUMBER: _ClassVar[int]
    REACTION_TARGET_GUID_FIELD_NUMBER: _ClassVar[int]
    REACTION_TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    REACTION_FIELD_NUMBER: _ClassVar[int]
    REACTION_SELECTED_FIELD_NUMBER: _ClassVar[int]
    APPLIED_REACTIONS_FIELD_NUMBER: _ClassVar[int]
    PLACED_STICKERS_FIELD_NUMBER: _ClassVar[int]
    CHAT_GUIDS_FIELD_NUMBER: _ClassVar[int]
    guid: str
    content: MessageContent
    subject: str
    date_created: _timestamp_pb2.Timestamp
    date_read: _timestamp_pb2.Timestamp
    date_delivered: _timestamp_pb2.Timestamp
    date_edited: _timestamp_pb2.Timestamp
    date_retracted: _timestamp_pb2.Timestamp
    date_played: _timestamp_pb2.Timestamp
    date_expressive_send_played: _timestamp_pb2.Timestamp
    sender: _address_types_pb2.SingleServiceAddressInfo
    is_from_me: bool
    is_sent: bool
    is_delivered: bool
    is_delivered_quietly: bool
    did_notify_recipient: bool
    send_error_code: int
    is_audio_message: bool
    is_auto_reply: bool
    is_system_message: bool
    is_forward: bool
    is_delayed: bool
    is_spam: bool
    data_detector_results_present: bool
    is_archived: bool
    is_service_message: bool
    is_corrupt: bool
    is_expirable: bool
    share_status: int
    share_direction: int
    item_type: MessageItemType
    group_title: str
    chat_action_type: int
    thread_originator_guid: str
    thread_originator_part: str
    reply_to_guid: str
    reply_target_guid: str
    destination_caller_id: str
    part_count: int
    cached_room_names: str
    reaction_target_guid: str
    reaction_target_part_index: int
    reaction: MessageReaction
    reaction_selected: bool
    applied_reactions: _containers.RepeatedCompositeFieldContainer[MessageAppliedReaction]
    placed_stickers: _containers.RepeatedCompositeFieldContainer[MessagePlacedSticker]
    chat_guids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, guid: _Optional[str] = ..., content: _Optional[_Union[MessageContent, _Mapping]] = ..., subject: _Optional[str] = ..., date_created: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., date_read: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., date_delivered: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., date_edited: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., date_retracted: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., date_played: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., date_expressive_send_played: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., sender: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., is_from_me: bool = ..., is_sent: bool = ..., is_delivered: bool = ..., is_delivered_quietly: bool = ..., did_notify_recipient: bool = ..., send_error_code: _Optional[int] = ..., is_audio_message: bool = ..., is_auto_reply: bool = ..., is_system_message: bool = ..., is_forward: bool = ..., is_delayed: bool = ..., is_spam: bool = ..., data_detector_results_present: bool = ..., is_archived: bool = ..., is_service_message: bool = ..., is_corrupt: bool = ..., is_expirable: bool = ..., share_status: _Optional[int] = ..., share_direction: _Optional[int] = ..., item_type: _Optional[_Union[MessageItemType, str]] = ..., group_title: _Optional[str] = ..., chat_action_type: _Optional[int] = ..., thread_originator_guid: _Optional[str] = ..., thread_originator_part: _Optional[str] = ..., reply_to_guid: _Optional[str] = ..., reply_target_guid: _Optional[str] = ..., destination_caller_id: _Optional[str] = ..., part_count: _Optional[int] = ..., cached_room_names: _Optional[str] = ..., reaction_target_guid: _Optional[str] = ..., reaction_target_part_index: _Optional[int] = ..., reaction: _Optional[_Union[MessageReaction, _Mapping]] = ..., reaction_selected: bool = ..., applied_reactions: _Optional[_Iterable[_Union[MessageAppliedReaction, _Mapping]]] = ..., placed_stickers: _Optional[_Iterable[_Union[MessagePlacedSticker, _Mapping]]] = ..., chat_guids: _Optional[_Iterable[str]] = ...) -> None: ...

class EmbeddedMedia(_message.Message):
    __slots__ = ("data", "mime_type")
    DATA_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    mime_type: str
    def __init__(self, data: _Optional[bytes] = ..., mime_type: _Optional[str] = ...) -> None: ...

class MessageReceived(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: Message
    def __init__(self, message: _Optional[_Union[Message, _Mapping]] = ...) -> None: ...

class MessageEdited(_message.Message):
    __slots__ = ("message_guid", "content", "edited_at")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    EDITED_AT_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    content: MessageContent
    edited_at: _timestamp_pb2.Timestamp
    def __init__(self, message_guid: _Optional[str] = ..., content: _Optional[_Union[MessageContent, _Mapping]] = ..., edited_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class MessageRead(_message.Message):
    __slots__ = ("message_guid", "read_at")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    READ_AT_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    read_at: _timestamp_pb2.Timestamp
    def __init__(self, message_guid: _Optional[str] = ..., read_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class MessageUnsent(_message.Message):
    __slots__ = ("message_guid", "retracted_at")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    RETRACTED_AT_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    retracted_at: _timestamp_pb2.Timestamp
    def __init__(self, message_guid: _Optional[str] = ..., retracted_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class MessageReactionAdded(_message.Message):
    __slots__ = ("message_guid", "target_part_index", "reaction")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    REACTION_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    target_part_index: int
    reaction: MessageReaction
    def __init__(self, message_guid: _Optional[str] = ..., target_part_index: _Optional[int] = ..., reaction: _Optional[_Union[MessageReaction, _Mapping]] = ...) -> None: ...

class MessageReactionRemoved(_message.Message):
    __slots__ = ("message_guid", "target_part_index", "reaction")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    REACTION_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    target_part_index: int
    reaction: MessageReaction
    def __init__(self, message_guid: _Optional[str] = ..., target_part_index: _Optional[int] = ..., reaction: _Optional[_Union[MessageReaction, _Mapping]] = ...) -> None: ...

class StickerPlaced(_message.Message):
    __slots__ = ("message_guid", "target_part_index", "sticker", "placement")
    MESSAGE_GUID_FIELD_NUMBER: _ClassVar[int]
    TARGET_PART_INDEX_FIELD_NUMBER: _ClassVar[int]
    STICKER_FIELD_NUMBER: _ClassVar[int]
    PLACEMENT_FIELD_NUMBER: _ClassVar[int]
    message_guid: str
    target_part_index: int
    sticker: _attachment_types_pb2.AttachmentInfo
    placement: StickerPlacement
    def __init__(self, message_guid: _Optional[str] = ..., target_part_index: _Optional[int] = ..., sticker: _Optional[_Union[_attachment_types_pb2.AttachmentInfo, _Mapping]] = ..., placement: _Optional[_Union[StickerPlacement, _Mapping]] = ...) -> None: ...

class MessageChangeEvent(_message.Message):
    __slots__ = ("chat_guid", "occurred_at", "actor", "is_from_me", "message_received", "message_edited", "message_read", "message_unsent", "reaction_added", "reaction_removed", "sticker_placed")
    CHAT_GUID_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_AT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    IS_FROM_ME_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_RECEIVED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_EDITED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_READ_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_UNSENT_FIELD_NUMBER: _ClassVar[int]
    REACTION_ADDED_FIELD_NUMBER: _ClassVar[int]
    REACTION_REMOVED_FIELD_NUMBER: _ClassVar[int]
    STICKER_PLACED_FIELD_NUMBER: _ClassVar[int]
    chat_guid: str
    occurred_at: _timestamp_pb2.Timestamp
    actor: _address_types_pb2.SingleServiceAddressInfo
    is_from_me: bool
    message_received: MessageReceived
    message_edited: MessageEdited
    message_read: MessageRead
    message_unsent: MessageUnsent
    reaction_added: MessageReactionAdded
    reaction_removed: MessageReactionRemoved
    sticker_placed: StickerPlaced
    def __init__(self, chat_guid: _Optional[str] = ..., occurred_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., actor: _Optional[_Union[_address_types_pb2.SingleServiceAddressInfo, _Mapping]] = ..., is_from_me: bool = ..., message_received: _Optional[_Union[MessageReceived, _Mapping]] = ..., message_edited: _Optional[_Union[MessageEdited, _Mapping]] = ..., message_read: _Optional[_Union[MessageRead, _Mapping]] = ..., message_unsent: _Optional[_Union[MessageUnsent, _Mapping]] = ..., reaction_added: _Optional[_Union[MessageReactionAdded, _Mapping]] = ..., reaction_removed: _Optional[_Union[MessageReactionRemoved, _Mapping]] = ..., sticker_placed: _Optional[_Union[StickerPlaced, _Mapping]] = ...) -> None: ...
