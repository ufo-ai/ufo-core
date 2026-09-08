"""Client and server classes corresponding to protobuf-defined services."""
import grpc
import warnings

from google.protobuf import empty_pb2 as google_dot_protobuf_dot_empty__pb2
from ufo_ext_imessage.proto.photon.imessage.v1 import message_service_pb2 as photon_dot_imessage_dot_v1_dot_message__service__pb2

GRPC_GENERATED_VERSION = '1.74.0'
GRPC_VERSION = grpc.__version__
_version_not_supported = False

try:
    from grpc._utilities import first_version_is_lower
    _version_not_supported = first_version_is_lower(GRPC_VERSION, GRPC_GENERATED_VERSION)
except ImportError:
    _version_not_supported = True

if _version_not_supported:
    raise RuntimeError(
        f'The grpc package installed is at version {GRPC_VERSION},'
        + f' but the generated code in photon/imessage/v1/message_service_pb2_grpc.py depends on'
        + f' grpcio>={GRPC_GENERATED_VERSION}.'
        + f' Please upgrade your grpc module to grpcio>={GRPC_GENERATED_VERSION}'
        + f' or downgrade your generated code using grpcio-tools<={GRPC_VERSION}.'
    )


class MessageServiceStub(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Sends, mutates, reads, and observes messages.

    Writes are synchronous to the command path. Methods that return
    `MessageResponse` also perform the reads needed to return a fresh message
    snapshot; methods that return `google.protobuf.Empty` do not add a projection
    read unless their individual contract says so.

    Durable message changes flow through `SubscribeMessageEvents`. For gap-free
    reconnect, drain history with `EventService.CatchUpEvents` before joining
    the live subscription.
    """

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.SendTextMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/SendTextMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendTextMessageRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.SendAttachmentMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/SendAttachmentMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendAttachmentMessageRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.SendMultipartMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/SendMultipartMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendMultipartMessageRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.SendCustomizedMiniAppMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/SendCustomizedMiniAppMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendCustomizedMiniAppMessageRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.UpdateCustomizedMiniAppMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/UpdateCustomizedMiniAppMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.UpdateCustomizedMiniAppMessageRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.EditMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/EditMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.EditMessageRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.UnsendMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/UnsendMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.UnsendMessageRequest.SerializeToString,
                response_deserializer=google_dot_protobuf_dot_empty__pb2.Empty.FromString,
                _registered_method=True)
        self.SetReaction = channel.unary_unary(
                '/photon.imessage.v1.MessageService/SetReaction',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SetReactionRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.PlaceSticker = channel.unary_unary(
                '/photon.imessage.v1.MessageService/PlaceSticker',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.PlaceStickerRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
                _registered_method=True)
        self.NotifySilencedMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/NotifySilencedMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.NotifySilencedMessageRequest.SerializeToString,
                response_deserializer=google_dot_protobuf_dot_empty__pb2.Empty.FromString,
                _registered_method=True)
        self.GetMessage = channel.unary_unary(
                '/photon.imessage.v1.MessageService/GetMessage',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetMessageRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetMessageResponse.FromString,
                _registered_method=True)
        self.ListRecentMessages = channel.unary_unary(
                '/photon.imessage.v1.MessageService/ListRecentMessages',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListRecentMessagesRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListRecentMessagesResponse.FromString,
                _registered_method=True)
        self.ListChatMessages = channel.unary_unary(
                '/photon.imessage.v1.MessageService/ListChatMessages',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListChatMessagesRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListChatMessagesResponse.FromString,
                _registered_method=True)
        self.GetEmbeddedMedia = channel.unary_unary(
                '/photon.imessage.v1.MessageService/GetEmbeddedMedia',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetEmbeddedMediaRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetEmbeddedMediaResponse.FromString,
                _registered_method=True)
        self.SubscribeMessageEvents = channel.unary_stream(
                '/photon.imessage.v1.MessageService/SubscribeMessageEvents',
                request_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SubscribeMessageEventsRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SubscribeMessageEventsResponse.FromString,
                _registered_method=True)


class MessageServiceServicer(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Sends, mutates, reads, and observes messages.

    Writes are synchronous to the command path. Methods that return
    `MessageResponse` also perform the reads needed to return a fresh message
    snapshot; methods that return `google.protobuf.Empty` do not add a projection
    read unless their individual contract says so.

    Durable message changes flow through `SubscribeMessageEvents`. For gap-free
    reconnect, drain history with `EventService.CatchUpEvents` before joining
    the live subscription.
    """

    def SendTextMessage(self, request, context):
        """Writes

        HTTP mapping convention (governs all annotations in this package):
        - `chat_guid` values (e.g. `iMessage;-;+15551234567`) are hostile to URL
        paths and never appear in one — they ride in the request body, or in
        query parameters for reads.
        - `message_guid` values (UUID-shaped, or proxy-virtual `spc-*`) are
        path-safe and map to resource paths.
        - Non-CRUD mutations use the `:customVerb` suffix style (AIP-136).
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def SendAttachmentMessage(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def SendMultipartMessage(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def SendCustomizedMiniAppMessage(self, request, context):
        """Sends an iMessage mini-app card backed by the caller's own extension.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def UpdateCustomizedMiniAppMessage(self, request, context):
        """Updates an iMessage mini-app card backed by the caller's own extension.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def EditMessage(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def UnsendMessage(self, request, context):
        """Retracts an existing message and returns Empty after helper success.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def SetReaction(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def PlaceSticker(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def NotifySilencedMessage(self, request, context):
        """Triggers Apple's per-message "Notify Anyway" action.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def GetMessage(self, request, context):
        """Reads
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def ListRecentMessages(self, request, context):
        """Query parameters over HTTP: `pageSize` (int), `isFromMe`/`isRead`
        (true|false), `before`/`after` (RFC 3339 timestamps, the proto3-JSON
        form of `google.protobuf.Timestamp`).
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def ListChatMessages(self, request, context):
        """Same query-parameter shapes as `ListRecentMessages`, plus `chatGuid`.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def GetEmbeddedMedia(self, request, context):
        """No HTTP mapping on purpose: the response carries raw media bytes
        (`EmbeddedMedia.data`), which don't belong base64-encoded in JSON.
        Served over HTTP by a dedicated raw-bytes route in the transcoding
        middleware alongside Upload/DownloadAttachment.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def SubscribeMessageEvents(self, request, context):
        """Live durable-event subscription. Pair with `EventService.CatchUpEvents`
        for gap-free history-then-live consumption.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_MessageServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'SendTextMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.SendTextMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendTextMessageRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'SendAttachmentMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.SendAttachmentMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendAttachmentMessageRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'SendMultipartMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.SendMultipartMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendMultipartMessageRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'SendCustomizedMiniAppMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.SendCustomizedMiniAppMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SendCustomizedMiniAppMessageRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'UpdateCustomizedMiniAppMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.UpdateCustomizedMiniAppMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.UpdateCustomizedMiniAppMessageRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'EditMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.EditMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.EditMessageRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'UnsendMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.UnsendMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.UnsendMessageRequest.FromString,
                    response_serializer=google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
            ),
            'SetReaction': grpc.unary_unary_rpc_method_handler(
                    servicer.SetReaction,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SetReactionRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'PlaceSticker': grpc.unary_unary_rpc_method_handler(
                    servicer.PlaceSticker,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.PlaceStickerRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.SerializeToString,
            ),
            'NotifySilencedMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.NotifySilencedMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.NotifySilencedMessageRequest.FromString,
                    response_serializer=google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
            ),
            'GetMessage': grpc.unary_unary_rpc_method_handler(
                    servicer.GetMessage,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetMessageRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetMessageResponse.SerializeToString,
            ),
            'ListRecentMessages': grpc.unary_unary_rpc_method_handler(
                    servicer.ListRecentMessages,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListRecentMessagesRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListRecentMessagesResponse.SerializeToString,
            ),
            'ListChatMessages': grpc.unary_unary_rpc_method_handler(
                    servicer.ListChatMessages,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListChatMessagesRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.ListChatMessagesResponse.SerializeToString,
            ),
            'GetEmbeddedMedia': grpc.unary_unary_rpc_method_handler(
                    servicer.GetEmbeddedMedia,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetEmbeddedMediaRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.GetEmbeddedMediaResponse.SerializeToString,
            ),
            'SubscribeMessageEvents': grpc.unary_stream_rpc_method_handler(
                    servicer.SubscribeMessageEvents,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SubscribeMessageEventsRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_message__service__pb2.SubscribeMessageEventsResponse.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'photon.imessage.v1.MessageService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('photon.imessage.v1.MessageService', rpc_method_handlers)


class MessageService(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Sends, mutates, reads, and observes messages.

    Writes are synchronous to the command path. Methods that return
    `MessageResponse` also perform the reads needed to return a fresh message
    snapshot; methods that return `google.protobuf.Empty` do not add a projection
    read unless their individual contract says so.

    Durable message changes flow through `SubscribeMessageEvents`. For gap-free
    reconnect, drain history with `EventService.CatchUpEvents` before joining
    the live subscription.
    """

    @staticmethod
    def SendTextMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/SendTextMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.SendTextMessageRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def SendAttachmentMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/SendAttachmentMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.SendAttachmentMessageRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def SendMultipartMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/SendMultipartMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.SendMultipartMessageRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def SendCustomizedMiniAppMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/SendCustomizedMiniAppMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.SendCustomizedMiniAppMessageRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def UpdateCustomizedMiniAppMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/UpdateCustomizedMiniAppMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.UpdateCustomizedMiniAppMessageRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def EditMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/EditMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.EditMessageRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def UnsendMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/UnsendMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.UnsendMessageRequest.SerializeToString,
            google_dot_protobuf_dot_empty__pb2.Empty.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def SetReaction(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/SetReaction',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.SetReactionRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def PlaceSticker(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/PlaceSticker',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.PlaceStickerRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.MessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def NotifySilencedMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/NotifySilencedMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.NotifySilencedMessageRequest.SerializeToString,
            google_dot_protobuf_dot_empty__pb2.Empty.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def GetMessage(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/GetMessage',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.GetMessageRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.GetMessageResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def ListRecentMessages(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/ListRecentMessages',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.ListRecentMessagesRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.ListRecentMessagesResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def ListChatMessages(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/ListChatMessages',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.ListChatMessagesRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.ListChatMessagesResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def GetEmbeddedMedia(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/photon.imessage.v1.MessageService/GetEmbeddedMedia',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.GetEmbeddedMediaRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.GetEmbeddedMediaResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)

    @staticmethod
    def SubscribeMessageEvents(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_stream(
            request,
            target,
            '/photon.imessage.v1.MessageService/SubscribeMessageEvents',
            photon_dot_imessage_dot_v1_dot_message__service__pb2.SubscribeMessageEventsRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_message__service__pb2.SubscribeMessageEventsResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)
