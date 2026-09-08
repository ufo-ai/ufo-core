"""Client and server classes corresponding to protobuf-defined services."""
import grpc
import warnings

from ufo_ext_imessage.proto.photon.imessage.v1 import event_service_pb2 as photon_dot_imessage_dot_v1_dot_event__service__pb2

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
        + f' but the generated code in photon/imessage/v1/event_service_pb2_grpc.py depends on'
        + f' grpcio>={GRPC_GENERATED_VERSION}.'
        + f' Please upgrade your grpc module to grpcio>={GRPC_GENERATED_VERSION}'
        + f' or downgrade your generated code using grpcio-tools<={GRPC_VERSION}.'
    )


class EventServiceStub(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Unified finite replay across every durable event domain:
    messages, group state, polls, and chats.

    Drains everything newer than `after_sequence` and terminates with
    `CatchUpEventsComplete`, after which the caller hands off to the live
    `Subscribe*` streams without gaps.

    All `sequence` values returned here share the same global event log
    as `SubscribeMessageEvents`, `SubscribeGroupEvents`, `SubscribePollEvents`,
    and `SubscribeChatEvents`.
    """

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.CatchUpEvents = channel.unary_stream(
                '/photon.imessage.v1.EventService/CatchUpEvents',
                request_serializer=photon_dot_imessage_dot_v1_dot_event__service__pb2.CatchUpEventsRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_event__service__pb2.CatchUpEventsResponse.FromString,
                _registered_method=True)


class EventServiceServicer(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Unified finite replay across every durable event domain:
    messages, group state, polls, and chats.

    Drains everything newer than `after_sequence` and terminates with
    `CatchUpEventsComplete`, after which the caller hands off to the live
    `Subscribe*` streams without gaps.

    All `sequence` values returned here share the same global event log
    as `SubscribeMessageEvents`, `SubscribeGroupEvents`, `SubscribePollEvents`,
    and `SubscribeChatEvents`.
    """

    def CatchUpEvents(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_EventServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'CatchUpEvents': grpc.unary_stream_rpc_method_handler(
                    servicer.CatchUpEvents,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_event__service__pb2.CatchUpEventsRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_event__service__pb2.CatchUpEventsResponse.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'photon.imessage.v1.EventService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('photon.imessage.v1.EventService', rpc_method_handlers)


class EventService(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Unified finite replay across every durable event domain:
    messages, group state, polls, and chats.

    Drains everything newer than `after_sequence` and terminates with
    `CatchUpEventsComplete`, after which the caller hands off to the live
    `Subscribe*` streams without gaps.

    All `sequence` values returned here share the same global event log
    as `SubscribeMessageEvents`, `SubscribeGroupEvents`, `SubscribePollEvents`,
    and `SubscribeChatEvents`.
    """

    @staticmethod
    def CatchUpEvents(request,
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
            '/photon.imessage.v1.EventService/CatchUpEvents',
            photon_dot_imessage_dot_v1_dot_event__service__pb2.CatchUpEventsRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_event__service__pb2.CatchUpEventsResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)
