"""Client and server classes corresponding to protobuf-defined services."""
import grpc
import warnings

from ufo_ext_imessage.proto.photon.imessage.v1 import attachment_service_pb2 as photon_dot_imessage_dot_v1_dot_attachment__service__pb2

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
        + f' but the generated code in photon/imessage/v1/attachment_service_pb2_grpc.py depends on'
        + f' grpcio>={GRPC_GENERATED_VERSION}.'
        + f' Please upgrade your grpc module to grpcio>={GRPC_GENERATED_VERSION}'
        + f' or downgrade your generated code using grpcio-tools<={GRPC_VERSION}.'
    )


class AttachmentServiceStub(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Attachment metadata reads, uploads, and downloads. All transfers are
    end-to-end on this gRPC channel; no external blob store is involved.
    """

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.GetAttachmentInfo = channel.unary_unary(
                '/photon.imessage.v1.AttachmentService/GetAttachmentInfo',
                request_serializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.GetAttachmentInfoRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.GetAttachmentInfoResponse.FromString,
                _registered_method=True)
        self.UploadAttachment = channel.unary_unary(
                '/photon.imessage.v1.AttachmentService/UploadAttachment',
                request_serializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.UploadAttachmentRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.UploadAttachmentResponse.FromString,
                _registered_method=True)
        self.DownloadAttachment = channel.unary_stream(
                '/photon.imessage.v1.AttachmentService/DownloadAttachment',
                request_serializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.DownloadAttachmentRequest.SerializeToString,
                response_deserializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.DownloadAttachmentResponse.FromString,
                _registered_method=True)


class AttachmentServiceServicer(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Attachment metadata reads, uploads, and downloads. All transfers are
    end-to-end on this gRPC channel; no external blob store is involved.
    """

    def GetAttachmentInfo(self, request, context):
        """Metadata-only lookup. Cheap; never touches attachment bytes.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def UploadAttachment(self, request, context):
        """Atomic upload of one primary file plus an optional sidecar. The
        server persists both before responding; on failure neither is visible
        to subsequent reads.

        No HTTP mapping on purpose: attachment payloads don't belong
        base64-encoded in JSON. Served over HTTP by a dedicated raw-bytes
        route in the transcoding middleware (as is `DownloadAttachment`).
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def DownloadAttachment(self, request, context):
        """Server-streaming bytes. See `DownloadAttachmentResponse` for the
        frame contract.
        """
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_AttachmentServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'GetAttachmentInfo': grpc.unary_unary_rpc_method_handler(
                    servicer.GetAttachmentInfo,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.GetAttachmentInfoRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.GetAttachmentInfoResponse.SerializeToString,
            ),
            'UploadAttachment': grpc.unary_unary_rpc_method_handler(
                    servicer.UploadAttachment,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.UploadAttachmentRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.UploadAttachmentResponse.SerializeToString,
            ),
            'DownloadAttachment': grpc.unary_stream_rpc_method_handler(
                    servicer.DownloadAttachment,
                    request_deserializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.DownloadAttachmentRequest.FromString,
                    response_serializer=photon_dot_imessage_dot_v1_dot_attachment__service__pb2.DownloadAttachmentResponse.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'photon.imessage.v1.AttachmentService', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('photon.imessage.v1.AttachmentService', rpc_method_handlers)


class AttachmentService(object):
    """---------------------------------------------------------------------------
    Service
    ---------------------------------------------------------------------------

    Attachment metadata reads, uploads, and downloads. All transfers are
    end-to-end on this gRPC channel; no external blob store is involved.
    """

    @staticmethod
    def GetAttachmentInfo(request,
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
            '/photon.imessage.v1.AttachmentService/GetAttachmentInfo',
            photon_dot_imessage_dot_v1_dot_attachment__service__pb2.GetAttachmentInfoRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_attachment__service__pb2.GetAttachmentInfoResponse.FromString,
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
    def UploadAttachment(request,
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
            '/photon.imessage.v1.AttachmentService/UploadAttachment',
            photon_dot_imessage_dot_v1_dot_attachment__service__pb2.UploadAttachmentRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_attachment__service__pb2.UploadAttachmentResponse.FromString,
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
    def DownloadAttachment(request,
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
            '/photon.imessage.v1.AttachmentService/DownloadAttachment',
            photon_dot_imessage_dot_v1_dot_attachment__service__pb2.DownloadAttachmentRequest.SerializeToString,
            photon_dot_imessage_dot_v1_dot_attachment__service__pb2.DownloadAttachmentResponse.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)
