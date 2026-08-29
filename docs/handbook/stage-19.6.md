# iMessage service stubs and provider contract  `stage-19.6`

This stage is shared plumbing for the iMessage extension. It does not do the real work of reading or sending messages by itself. Instead, it defines the “sockets” where other code plugs in, and the network wrappers that let different parts of the system talk to those sockets.

The four generated gRPC files are the network wiring. gRPC is a system for calling code on another process or machine as if it were a local function. The attachment service wiring covers attachment calls, the chat service covers chat operations, the event service lets clients ask for missed event history, and the message service covers message-related calls. Each file gives client code a ready-made caller, and server code a place to attach the real implementation.

The provider.py file is the provider contract. It defines what a message record looks like, what errors a provider may return, and what actions the system can request. Together, these files form the public interface between iMessage-specific code and the rest of the application.

## Files in this stage

### Generated gRPC service stubs
Python gRPC wiring exposes the iMessage attachment, chat, event, and message services for clients and server implementations.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2_grpc.py`

`generated` · `startup and RPC request handling`

This file is machine-generated from a protocol definition, so it is mostly glue code rather than hand-written project logic. Its job is to make attachment operations usable over gRPC, which is a system for calling functions on another process or machine as if they were local functions. The service covers three things: reading attachment metadata, uploading an attachment, and downloading attachment bytes as a stream.

Think of it like the phonebook and switchboard for this service. On the client side, AttachmentServiceStub creates callable entries for each remote operation. A client gives it a gRPC channel, and the stub knows the exact remote method names and how to turn request and response messages into bytes and back again.

On the server side, AttachmentServiceServicer is a base class with placeholder methods. Real server code is expected to subclass it and replace those placeholders with actual storage and retrieval behavior. The add_AttachmentServiceServicer_to_server function connects such a real implementation to a running gRPC server.

The file also includes an experimental convenience class, AttachmentService, which can make one-off calls without first building a stub object. At import time, it checks that the installed grpc package is new enough for this generated code; otherwise it fails early with a clear error.

#### Function details

##### `AttachmentServiceStub.__init__`  (lines 37–57)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object with three ready-to-use remote calls: get attachment info, upload an attachment, and download an attachment. Someone uses this when they already have a gRPC channel to the server and want a simple way to call the attachment service.

**Data flow**: It receives a gRPC channel, which is the connection path to the remote server. It attaches three call helpers to the stub, each with the correct service path plus rules for converting request objects into bytes and response bytes back into Python message objects. After construction, the stub exposes those helpers as attributes that client code can call.

**Call relations**: Client code creates this stub before making attachment-service requests. The stub does not do the real attachment work itself; it prepares calls that travel through the gRPC channel to whichever server registered the matching service methods.


##### `AttachmentServiceServicer.GetAttachmentInfo`  (lines 69–74)

```
def GetAttachmentInfo(self, request, context)
```

**Purpose**: Defines the server-side shape of the metadata lookup method, but does not implement it. It exists so real server code can override it with logic that returns attachment details without reading the attachment bytes.

**Data flow**: It receives a request and a gRPC context, which carries response status information. In this base version, it marks the call as unimplemented, adds the message "Method not implemented!", and raises an error instead of returning attachment information.

**Call relations**: The gRPC server calls this method when a client asks for attachment metadata, but only if a real subclass has not replaced it. In normal use, project-specific server code overrides this placeholder before the servicer is registered with the server.


##### `AttachmentServiceServicer.UploadAttachment`  (lines 76–87)

```
def UploadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the upload method, but leaves the real upload behavior to another class. The intended behavior is an all-or-nothing upload, where the main file and optional sidecar are both saved before success is reported.

**Data flow**: It receives an upload request and a gRPC context. In this base version, it does not inspect or save the attachment data; it sets the response status to unimplemented, records a short explanation, and raises an error.

**Call relations**: The gRPC server would route upload requests here through the registered service handler. Real attachment-storage code is expected to override this method so clients can upload bytes through the gRPC channel.


##### `AttachmentServiceServicer.DownloadAttachment`  (lines 89–95)

```
def DownloadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the download method, which is meant to send attachment bytes back in multiple pieces. This base method is only a placeholder and must be replaced for downloads to work.

**Data flow**: It receives a download request and a gRPC context. Instead of producing a stream of download response messages, it marks the call as unimplemented and raises an error.

**Call relations**: The server-side registration points download RPCs at this method. A real servicer subclass should override it so that, when a client asks for an attachment, the server can stream back the file data piece by piece.


##### `add_AttachmentServiceServicer_to_server`  (lines 98–119)

```
def add_AttachmentServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a server-side attachment service implementation to a gRPC server. Without this step, incoming network calls would not know which Python methods should answer them.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table that maps each public RPC name to the matching Python method, along with the request parser and response writer for that method. It then registers that table with the server so future remote calls are routed correctly.

**Call relations**: Server startup code calls this after creating a concrete AttachmentServiceServicer. Inside, it asks gRPC to create method handlers for the two single-response calls and the one streaming download call, then gives those handlers to the server under the full AttachmentService name.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `AttachmentService.GetAttachmentInfo`  (lines 133–157)

```
def GetAttachmentInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for making a one-off GetAttachmentInfo remote call. It is useful when code wants to call the service directly by target address rather than first creating a stub.

**Data flow**: It receives a request, a target server address, and optional connection settings such as credentials, timeout, compression, and metadata. It serializes the request, sends it to the GetAttachmentInfo RPC path using gRPC's experimental helper, then converts the response bytes back into a GetAttachmentInfoResponse object.

**Call relations**: This is an alternative client path to the stub-based call. It hands the actual network work to gRPC's experimental unary-unary helper, meaning one request goes out and one response comes back.


##### `AttachmentService.UploadAttachment`  (lines 160–184)

```
def UploadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for making a one-off UploadAttachment remote call. It sends an upload request to a target server and waits for one upload response.

**Data flow**: It takes an upload request, the target server, and optional call settings. It turns the request into bytes, sends those bytes to the UploadAttachment RPC path, and turns the returned bytes into an UploadAttachmentResponse object.

**Call relations**: Client code can use this instead of constructing AttachmentServiceStub. It delegates the actual remote procedure call to gRPC's experimental unary-unary helper, matching the upload method's pattern of one request followed by one response.


##### `AttachmentService.DownloadAttachment`  (lines 187–211)

```
def DownloadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for starting a DownloadAttachment remote call. Unlike the metadata and upload calls, this one expects the server to send back a stream of response messages.

**Data flow**: It receives a download request, a target server, and optional connection settings. It serializes the request and sends it to the DownloadAttachment RPC path, then returns a gRPC stream that yields DownloadAttachmentResponse objects as chunks arrive from the server.

**Call relations**: This is the one-off client-side path for downloads. It hands off to gRPC's experimental unary-stream helper, where one request from the client can produce many response messages from the server.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is machine-made from a protocol definition, so it is mostly plumbing rather than project-specific decision-making. Its job is to make remote chat actions feel like normal Python method calls. gRPC is a remote procedure call system: one program asks another program to run a named operation, and both sides agree on how requests and replies are encoded.

At import time, the file first checks that the installed grpc package is new enough for the generated code. Without that check, the program might fail later in confusing ways because the generated helpers expect features that are not present.

The file then provides three main pieces. ChatServiceStub is for clients. Given a network channel, it creates callable methods such as CreateChat, GetChat, SetTyping, and SubscribeChatEvents. Each method knows the exact remote path and how to turn request and response message objects into bytes and back again.

ChatServiceServicer is the server-side base class. It lists the same chat methods, but each default method simply reports “not implemented.” A real server is expected to subclass it and replace those methods with actual behavior.

Finally, add_ChatServiceServicer_to_server connects a servicer object to a gRPC server, like putting labeled mail slots on a front desk so incoming requests go to the right method. The ChatService class also offers experimental one-shot static call helpers for clients that do not want to build a stub object first.

#### Function details

##### `ChatServiceStub.__init__`  (lines 37–92)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object whose attributes are ready-to-use remote chat calls. Someone uses this when they have a gRPC channel to the chat service and want to call methods like GetChat or SetTyping from Python.

**Data flow**: It receives a channel, which represents the network connection to a gRPC server. It asks that channel to create call helpers for each chat operation, giving each helper the remote method name plus the functions that encode requests and decode replies. After it finishes, the stub object has callable attributes for all supported chat service methods.

**Call relations**: Client code creates this stub near the point where it wants to talk to the chat service. Later, the client calls the attributes prepared here; those calls send serialized protobuf messages over the channel and turn the server replies back into Python message objects.


##### `ChatServiceServicer.CreateChat`  (lines 103–108)

```
def CreateChat(self, request, context)
```

**Purpose**: Defines the server-side placeholder for creating a chat. In this generated base class it deliberately does not create anything; real server code must override it.

**Data flow**: It receives a CreateChat request and a gRPC context, which is the object used to report status back to the caller. The placeholder marks the request as unimplemented, adds a short explanation, and raises an error instead of returning a response.

**Call relations**: When a real server registers a servicer, gRPC may route CreateChat requests to this method. If the project has not replaced it with a real implementation, the caller receives an unimplemented error.


##### `ChatServiceServicer.MarkChatRead`  (lines 110–114)

```
def MarkChatRead(self, request, context)
```

**Purpose**: Defines the server-side placeholder for marking a chat as read. It exists so generated routing knows the method name, but it must be overridden to do useful work.

**Data flow**: It receives a MarkChatRead request and the gRPC context. The base method changes the context status to unimplemented, records that the method is missing, and raises an error rather than returning the expected empty success message.

**Call relations**: Registered server routing can send MarkChatRead calls here. A real chat service subclass is expected to replace this method so clients can mark conversations as read.


##### `ChatServiceServicer.SetBackground`  (lines 116–122)

```
def SetBackground(self, request, context)
```

**Purpose**: Defines the server-side placeholder for setting a chat background image or data. The note from the protocol says that when exposed over HTTP, byte data is carried as base64 text in JSON.

**Data flow**: It receives a SetBackground request and a gRPC context. Instead of storing or applying a background, the generated base method reports that the operation is not implemented and raises an error.

**Call relations**: Incoming SetBackground requests reach this method only if no subclass override supplies the real behavior. The registration helper connects this method name to the gRPC server.


##### `ChatServiceServicer.RemoveBackground`  (lines 124–128)

```
def RemoveBackground(self, request, context)
```

**Purpose**: Defines the server-side placeholder for removing a chat background. It is a required shape for the service, not the actual removal logic.

**Data flow**: It receives a RemoveBackground request plus the gRPC context. The method sets an unimplemented status and raises an error, so no chat background is changed by this base version.

**Call relations**: Server code should override this method before registering the servicer. Otherwise, requests routed to RemoveBackground will fail with the standard generated unimplemented response.


##### `ChatServiceServicer.ShareContactInfo`  (lines 130–137)

```
def ShareContactInfo(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sharing the local user’s contact card into a chat. The intended operation is to push name-and-photo information to the target chat, but this base version does not do it.

**Data flow**: It receives a ShareContactInfo request and the gRPC context. It records that the method is unimplemented and raises an error, returning no successful result.

**Call relations**: This method is part of the server interface that add_ChatServiceServicer_to_server can expose. A real server implementation must override it so clients can actually trigger contact sharing.


##### `ChatServiceServicer.SetTyping`  (lines 139–144)

```
def SetTyping(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a temporary typing indicator. The intended action is transient, meaning it is not stored as lasting chat history.

**Data flow**: It receives a SetTyping request and a gRPC context. The generated method does not send any typing signal; it marks the operation as unimplemented and raises an error.

**Call relations**: When a client asks the server to show typing state, gRPC can route that request to this method. In a working service, a subclass supplies the real temporary typing behavior.


##### `ChatServiceServicer.GetChat`  (lines 146–152)

```
def GetChat(self, request, context)
```

**Purpose**: Defines the server-side placeholder for reading one chat. The protocol note says chat identifiers are awkward to put directly into URL paths, so related HTTP mappings use query-style parameters instead.

**Data flow**: It receives a GetChat request and the gRPC context. Rather than looking up a chat and returning a GetChat response, it marks the method unimplemented and raises an error.

**Call relations**: Registered server routing can dispatch GetChat requests here. The generated base method protects against silently doing nothing; real chat lookup code must override it.


##### `ChatServiceServicer.GetChatCount`  (lines 154–158)

```
def GetChatCount(self, request, context)
```

**Purpose**: Defines the server-side placeholder for returning a count of chats. It provides the expected method slot but no counting behavior.

**Data flow**: It receives a GetChatCount request and context. The placeholder sets the gRPC status to unimplemented, adds a detail message, and raises an error instead of returning a count response.

**Call relations**: A gRPC server can expose this method through the registration helper. To make it useful, project code must implement it in a subclass.


##### `ChatServiceServicer.HasBackground`  (lines 160–164)

```
def HasBackground(self, request, context)
```

**Purpose**: Defines the server-side placeholder for checking whether a chat has a background set. The base class only says that the operation is missing.

**Data flow**: It receives a HasBackground request and context. It does not inspect any chat data; it marks the call as unimplemented and raises an error instead of returning a yes-or-no response.

**Call relations**: This method is one of the handlers wired into the gRPC server by the registration function. A real servicer replaces it to answer client checks.


##### `ChatServiceServicer.SubscribeChatEvents`  (lines 166–170)

```
def SubscribeChatEvents(self, request, context)
```

**Purpose**: Defines the server-side placeholder for subscribing to a stream of chat events. A stream means one request can produce many response messages over time, but this base method produces none.

**Data flow**: It receives a SubscribeChatEvents request and context. Instead of yielding event updates, it marks the call as unimplemented and raises an error.

**Call relations**: This is the server-side method that should back long-running event subscriptions. The registration helper wires it as a streaming response method, but a real subclass must provide the event stream.


##### `add_ChatServiceServicer_to_server`  (lines 173–229)

```
def add_ChatServiceServicer_to_server(servicer, server)
```

**Purpose**: Attaches a chat service implementation to a gRPC server so incoming network calls know which Python method to run. Without this step, the server might exist, but chat service requests would have no registered destination.

**Data flow**: It receives a servicer object, which should contain the real chat methods, and a gRPC server. It builds a table mapping each public RPC name to the matching servicer method, along with the request decoder and response encoder for that method. It then registers that table with the server, changing the server so it can accept ChatService calls.

**Call relations**: Server startup code calls this after creating a concrete servicer. Inside, it uses grpc.unary_unary_rpc_method_handler for ordinary one-request, one-reply methods, grpc.unary_stream_rpc_method_handler for the event subscription method that can return many replies, and grpc.method_handlers_generic_handler to package the method table under the ChatService service name before adding it to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `ChatService.CreateChat`  (lines 242–266)

```
def CreateChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for making a CreateChat remote call without first creating a ChatServiceStub object. It is useful for direct, one-off client calls.

**Data flow**: It receives a CreateChat request, a target server address, and optional connection settings such as credentials, timeout, compression, and metadata. It serializes the request, sends it to the CreateChat remote method through gRPC’s experimental helper, then deserializes the reply into a CreateChat response.

**Call relations**: Client code may call this static method instead of using ChatServiceStub. It hands the actual network work to grpc.experimental.unary_unary because CreateChat is a single request followed by a single response.


##### `ChatService.MarkChatRead`  (lines 269–293)

```
def MarkChatRead(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for asking the remote service to mark a chat as read. It avoids the need to create a stub first.

**Data flow**: It receives a MarkChatRead request, target address, and optional call settings. It turns the request into bytes, calls the MarkChatRead endpoint, and turns the empty success reply back into an Empty protobuf object.

**Call relations**: This is a client-side helper. It delegates to grpc.experimental.unary_unary, matching the shape of the remote method: one request goes out, one reply comes back.


##### `ChatService.SetBackground`  (lines 296–320)

```
def SetBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for setting a chat background on a remote service. It is a client convenience wrapper around the generated gRPC method name and message encoders.

**Data flow**: It receives a SetBackground request, the target server, and optional connection details. It serializes the request, sends it to the SetBackground endpoint, and deserializes the empty success response.

**Call relations**: Client code can use this static method for a direct call. It passes the request and all connection options to grpc.experimental.unary_unary, which performs the network request.


##### `ChatService.RemoveBackground`  (lines 323–347)

```
def RemoveBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for removing a chat background through the remote service. It wraps the gRPC call details so the caller only needs a request and target.

**Data flow**: It receives a RemoveBackground request plus target and optional call settings. It encodes the request, sends it to the remote RemoveBackground method, and decodes the empty response that signals success.

**Call relations**: This static client helper hands off to grpc.experimental.unary_unary. It is an alternative to calling the RemoveBackground method prepared on a ChatServiceStub.


##### `ChatService.ShareContactInfo`  (lines 350–374)

```
def ShareContactInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for telling the remote service to share the local user’s contact information into a chat. It is meant for client code that wants a direct call helper.

**Data flow**: It receives a ShareContactInfo request, a target server address, and optional gRPC settings. It serializes the request, sends it to the ShareContactInfo endpoint, and decodes the empty success reply.

**Call relations**: Client code may call this instead of building a stub. The method delegates to grpc.experimental.unary_unary because the remote operation returns one final response, not a stream.


##### `ChatService.SetTyping`  (lines 377–401)

```
def SetTyping(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for sending a typing-indicator update to the remote chat service. This is for temporary presence-style updates rather than lasting chat data.

**Data flow**: It receives a SetTyping request, the server target, and optional call settings. It encodes the request, sends it to the SetTyping endpoint, and decodes the empty response.

**Call relations**: This is a direct client-side wrapper over grpc.experimental.unary_unary. It follows the same service path that the normal stub uses for SetTyping.


##### `ChatService.GetChat`  (lines 404–428)

```
def GetChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for fetching chat information from a remote service. It lets client code make the read call directly.

**Data flow**: It receives a GetChat request, target server, and optional settings such as timeout or credentials. It serializes the request, calls the GetChat endpoint, and deserializes the reply into a GetChat response object.

**Call relations**: Client code can use this static helper instead of ChatServiceStub.GetChat. It relies on grpc.experimental.unary_unary because the call has one request and one response.


##### `ChatService.GetChatCount`  (lines 431–455)

```
def GetChatCount(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for asking the remote service how many chats match the request. It wraps the network method and message conversion details.

**Data flow**: It receives a GetChatCount request, target address, and optional gRPC call options. It converts the request to bytes, sends it to the GetChatCount method, and converts the response bytes into a GetChatCount response.

**Call relations**: This client-side helper delegates to grpc.experimental.unary_unary. It is a convenience alternative to creating a stub and calling its GetChatCount attribute.


##### `ChatService.HasBackground`  (lines 458–482)

```
def HasBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for checking whether a remote chat has a background set. It hides the exact gRPC method path and encoding functions from the caller.

**Data flow**: It receives a HasBackground request, target server, and optional call settings. It serializes the request, sends it to the HasBackground endpoint, and deserializes the reply into a HasBackground response.

**Call relations**: Client code may use this static method for a one-off check. The network call is performed by grpc.experimental.unary_unary, matching the one-request, one-response pattern.


##### `ChatService.SubscribeChatEvents`  (lines 485–509)

```
def SubscribeChatEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for subscribing to chat events from a remote service. Unlike the other shortcuts, this one can receive many event messages after sending one request.

**Data flow**: It receives a SubscribeChatEvents request, the server target, and optional connection settings. It serializes the request and starts a unary-to-stream gRPC call, meaning one request goes out and a stream of response messages may come back over time. Each received response is decoded into a SubscribeChatEvents response object.

**Call relations**: Client code uses this when it wants ongoing chat updates without first building a stub. It hands the work to grpc.experimental.unary_stream because the server side can keep sending event responses after the initial request.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2_grpc.py`

`generated` · `request handling`

This file is machine-written glue code produced from a Protocol Buffers service definition. Protocol Buffers, or protobuf, describe messages and services in a language-neutral way; gRPC uses those descriptions to make remote function calls over the network. Here, the service is about catching up on durable iMessage-related events: messages, group changes, polls, and chats. The key idea is that a client can say, “give me everything after this sequence number,” receive a finite stream of older missed events, and then switch to live subscription streams without leaving a gap. The file provides three main shapes. EventServiceStub is for clients: it turns a Python method call into a network request and turns streamed network replies back into Python response objects. EventServiceServicer is the server-side base class: real server code is expected to subclass it and provide the actual CatchUpEvents behavior. add_EventServiceServicer_to_server plugs that server implementation into a gRPC server so incoming requests reach the right method. EventService is an experimental shortcut API for making the same call without manually building a stub. The file also checks that the installed grpc Python package is new enough for the generated code; without that check, subtle runtime failures could happen later.

#### Function details

##### `EventServiceStub.__init__`  (lines 45–55)

```
def __init__(self, channel)
```

**Purpose**: This sets up the client-side handle for calling CatchUpEvents on a remote EventService. A caller uses it when they have a gRPC channel, which is the network connection to the server.

**Data flow**: It receives a channel object as input. It registers a unary-to-stream remote call on that channel: one request message goes out, and many response messages may come back. It also attaches the correct protobuf converters, so request objects become bytes on the wire and response bytes become Python objects again. After construction, the stub has a CatchUpEvents attribute ready to call.

**Call relations**: Client code creates EventServiceStub when it wants to talk to the EventService over an existing gRPC channel. The method it prepares corresponds to the server method registered by add_EventServiceServicer_to_server, so both sides agree on the service path and message formats.


##### `EventServiceServicer.CatchUpEvents`  (lines 75–79)

```
def CatchUpEvents(self, request, context)
```

**Purpose**: This is the default server-side placeholder for CatchUpEvents. It exists so real server code can inherit from EventServiceServicer and replace this method with the actual event replay logic.

**Data flow**: It receives a request and a request context from gRPC. Instead of producing event responses, it marks the call as unimplemented and raises an error. Nothing useful is returned unless another class overrides this method.

**Call relations**: When add_EventServiceServicer_to_server registers a servicer, incoming CatchUpEvents requests are directed to that servicer’s CatchUpEvents method. If the application forgot to override this placeholder, clients will receive an 'unimplemented' error rather than silently getting bad data.


##### `add_EventServiceServicer_to_server`  (lines 82–93)

```
def add_EventServiceServicer_to_server(servicer, server)
```

**Purpose**: This connects a server’s EventService implementation to a running gRPC server. Without this step, the server might exist, but network requests for CatchUpEvents would have nowhere to go.

**Data flow**: It takes a servicer object and a gRPC server. It builds a routing table that says the CatchUpEvents RPC should call servicer.CatchUpEvents, and it supplies the protobuf converters for incoming requests and outgoing responses. It then adds that routing table to the server. Afterward, the server can receive EventService calls from clients.

**Call relations**: Server startup code calls this after creating an object that implements EventServiceServicer. Inside, it asks gRPC to create a unary-to-stream method handler and a generic service handler, then hands those to the server so future client calls made through EventServiceStub or EventService.CatchUpEvents reach the implementation.

*Call graph*: 2 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler).


##### `EventService.CatchUpEvents`  (lines 115–139)

```
def CatchUpEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This is an experimental convenience call for invoking CatchUpEvents directly. It lets code send one catch-up request to a target server and receive a stream of responses without first creating an EventServiceStub by hand.

**Data flow**: It receives the request, the target server address, and optional call settings such as credentials, timeout, compression, and metadata. It serializes the request into bytes, asks gRPC to perform a unary-to-stream network call to the CatchUpEvents service path, and deserializes each response back into a Python response object. The result is a stream-like gRPC call object that yields catch-up responses.

**Call relations**: This method is an alternate client entry point to the same remote operation prepared by EventServiceStub.__init__. On the server side, the request is expected to be routed through the handler installed by add_EventServiceServicer_to_server.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2_grpc.py`

`generated` · `cross-cutting`

This file is like the phonebook and switchboard for the iMessage message API. The actual message shapes live in the matching protobuf file, and the real business behavior lives in server code elsewhere. This file connects those pieces to gRPC, which is a system for calling functions on another machine as if they were local functions.

At import time, it first checks that the installed grpc package is new enough for the generated code. If the version is too old, it stops immediately with a clear error, because client and server calls might not work correctly.

The `MessageServiceStub` class is for clients. Given a gRPC channel, it creates callable methods such as sending text, editing a message, listing recent messages, or subscribing to message events. Each method knows how to turn a request object into bytes for the network and how to turn the returned bytes back into a response object.

The `MessageServiceServicer` class is a server-side template. Its methods all return “not implemented” until project code subclasses or replaces them with real behavior. The `add_MessageServiceServicer_to_server` function registers those methods with a gRPC server.

Finally, the experimental `MessageService` class offers one-shot static helper calls for clients that want to call an endpoint directly without first building a stub object.

#### Function details

##### `MessageServiceStub.__init__`  (lines 46–126)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object whose methods call the remote iMessage message service. A caller uses it after opening a gRPC channel to the server.

**Data flow**: It receives a gRPC channel, which is the network connection to the server. It attaches one callable attribute for each service operation, pairing the correct request serializer with the correct response parser. After construction, the stub object can send typed request objects over the channel and return typed response objects.

**Call relations**: Client code creates this stub when it wants to talk to the message service repeatedly. Each generated stub method points at a named remote endpoint, and the gRPC library uses those definitions to send requests to the matching server method.


##### `MessageServiceServicer.SendTextMessage`  (lines 146–159)

```
def SendTextMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a plain text message. In this generated base class it is only a placeholder, so real server code must provide the actual sending behavior.

**Data flow**: It receives a send-text request and a gRPC context object for reporting status. Instead of sending anything, it marks the call as unimplemented, adds an explanatory detail message, and raises an error. Nothing useful is returned.

**Call relations**: When the server registration connects this service to gRPC, incoming SendTextMessage calls are routed to this method unless an application-specific servicer overrides it. It is the contract point between the network layer and the real message-sending code.


##### `MessageServiceServicer.SendAttachmentMessage`  (lines 161–165)

```
def SendAttachmentMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a message with an attachment. The generated version is a placeholder that tells callers no implementation has been supplied yet.

**Data flow**: It receives an attachment-message request and the call context. It sets the gRPC status to unimplemented, records a detail string, and raises a NotImplementedError. No attachment is sent and no message response is produced.

**Call relations**: gRPC calls this method for the SendAttachmentMessage endpoint after the servicer is registered. A real server must override it to hand the request to attachment-sending logic.


##### `MessageServiceServicer.SendMultipartMessage`  (lines 167–171)

```
def SendMultipartMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a message made of multiple parts, such as text plus media. The base implementation exists only to declare the API shape.

**Data flow**: It receives a multipart-message request and context. It reports that the method is unimplemented and raises an error, leaving the request untouched. The caller receives a gRPC unimplemented failure rather than a normal response.

**Call relations**: Incoming network calls for SendMultipartMessage land here unless the project supplies a real implementation. This keeps the generated service interface complete while leaving the feature work to application code.


##### `MessageServiceServicer.SendCustomizedMiniAppMessage`  (lines 173–178)

```
def SendCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending an iMessage mini-app card backed by the caller's own extension. The generated method is only a placeholder.

**Data flow**: It receives a customized mini-app send request and context. It sets the call status to unimplemented, gives a short explanation, and raises an error. No card is created or returned.

**Call relations**: The gRPC server dispatches matching incoming calls to this method after registration. Application code is expected to replace this placeholder with the logic that actually creates the mini-app message.


##### `MessageServiceServicer.UpdateCustomizedMiniAppMessage`  (lines 180–185)

```
def UpdateCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for updating an existing customized iMessage mini-app card. The generated version simply says the behavior is not implemented.

**Data flow**: It receives an update request and the gRPC context. It writes an unimplemented status into the context and raises NotImplementedError. No message is changed and no fresh snapshot is returned.

**Call relations**: This method is the server endpoint that gRPC would call for mini-app card updates. Real project code must override it to connect the network request to the update operation.


##### `MessageServiceServicer.EditMessage`  (lines 187–191)

```
def EditMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for editing an existing message. In this base class, it only declares the endpoint and rejects calls as unimplemented.

**Data flow**: It receives an edit request and context. It sets the status to unimplemented, records a message saying the method is not implemented, and raises an error. The original message is not edited.

**Call relations**: After service registration, EditMessage network calls are routed here unless a concrete servicer supplies real edit behavior. This generated method is the required API hook.


##### `MessageServiceServicer.UnsendMessage`  (lines 193–198)

```
def UnsendMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for retracting, or unsending, an existing message. The generated base method does not perform the retraction.

**Data flow**: It receives an unsend request and context. It marks the call as unimplemented and raises NotImplementedError. No empty success response is returned because no work was done.

**Call relations**: The registration function maps the UnsendMessage RPC endpoint to this method. A real server implementation should replace it with code that retracts the message and then returns an empty success result.


##### `MessageServiceServicer.SetReaction`  (lines 200–204)

```
def SetReaction(self, request, context)
```

**Purpose**: Defines the server-side slot for adding, changing, or clearing a reaction on a message. Here it is only a generated placeholder.

**Data flow**: It receives a reaction request and the gRPC context. It sets an unimplemented status and raises an error. No reaction is changed and no updated message response is produced.

**Call relations**: When a client calls SetReaction, gRPC routes that call to this method on the registered servicer. Application code must override it to connect the call to real reaction-changing behavior.


##### `MessageServiceServicer.PlaceSticker`  (lines 206–210)

```
def PlaceSticker(self, request, context)
```

**Purpose**: Defines the server-side slot for placing a sticker on a message. The generated method only reports that no implementation exists.

**Data flow**: It receives a sticker-placement request and context. It sets the gRPC status to unimplemented, adds a detail message, and raises an error. No sticker is placed and no message response is returned.

**Call relations**: This is the method the gRPC server invokes for the PlaceSticker endpoint. A concrete servicer must provide the actual sticker placement logic.


##### `MessageServiceServicer.NotifySilencedMessage`  (lines 212–217)

```
def NotifySilencedMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for triggering Apple's per-message “Notify Anyway” action. The base class does not actually notify anyone.

**Data flow**: It receives a notify request and context. It marks the call as unimplemented and raises NotImplementedError. No notification action happens and no empty success response is returned.

**Call relations**: The generated server registration maps NotifySilencedMessage calls to this method. Project code needs to override it to perform the Apple-specific notification action.


##### `MessageServiceServicer.GetMessage`  (lines 219–224)

```
def GetMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for reading one message. In the generated base class, it is a placeholder and returns an unimplemented error.

**Data flow**: It receives a get-message request and context. It sets an unimplemented status, records a detail message, and raises an error. No message data is looked up or returned.

**Call relations**: When a registered server receives a GetMessage call, gRPC dispatches to this method unless a real servicer overrides it. It is the network-facing hook for message lookup.


##### `MessageServiceServicer.ListRecentMessages`  (lines 226–233)

```
def ListRecentMessages(self, request, context)
```

**Purpose**: Defines the server-side slot for listing recent messages, optionally filtered by fields such as sender, read status, or time. The generated method itself does not query anything.

**Data flow**: It receives a list request and context. It reports the method as unimplemented and raises an error. No recent-message list is produced.

**Call relations**: This method is where gRPC sends ListRecentMessages requests after server registration. Real application code should override it to read from the message store and return a list response.


##### `MessageServiceServicer.ListChatMessages`  (lines 235–240)

```
def ListChatMessages(self, request, context)
```

**Purpose**: Defines the server-side slot for listing messages within a particular chat. The base generated version only rejects the call as unimplemented.

**Data flow**: It receives a chat-message list request and context. It sets the gRPC status to unimplemented and raises NotImplementedError. No chat history is read or returned.

**Call relations**: ListChatMessages client calls arrive here through the gRPC server machinery. A concrete implementation should use the request details, such as the chat identifier and filters, to fetch the right messages.


##### `MessageServiceServicer.GetEmbeddedMedia`  (lines 242–250)

```
def GetEmbeddedMedia(self, request, context)
```

**Purpose**: Defines the server-side slot for retrieving embedded media bytes from a message. The generated placeholder does not fetch any media.

**Data flow**: It receives an embedded-media request and context. It marks the call as unimplemented, adds a detail string, and raises an error. No raw media data is returned.

**Call relations**: The service registration routes GetEmbeddedMedia RPC calls to this method. The comments note that raw bytes are intentionally not mapped to normal JSON HTTP output, so real code must provide a suitable media-serving path.


##### `MessageServiceServicer.SubscribeMessageEvents`  (lines 252–258)

```
def SubscribeMessageEvents(self, request, context)
```

**Purpose**: Defines the server-side slot for subscribing to live message change events. The generated base version does not stream any events.

**Data flow**: It receives a subscription request and context. It marks the method as unimplemented and raises an error before yielding any event responses. The client receives a failure rather than a stream.

**Call relations**: This is the server hook for the SubscribeMessageEvents streaming endpoint. A real implementation should produce a stream of durable message events, often after clients have caught up on missed history elsewhere.


##### `add_MessageServiceServicer_to_server`  (lines 261–342)

```
def add_MessageServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a message-service servicer object to a gRPC server so incoming network calls know which Python methods to run. Without this registration, the server could have implementation methods but gRPC would not route requests to them.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table mapping each RPC method name to a gRPC method handler, including the correct request parser and response serializer for that method. It then adds that table to the server, so the server can accept calls for `photon.imessage.v1.MessageService`.

**Call relations**: Server setup code calls this when starting the gRPC service. Inside it, the generated code asks gRPC to build unary-unary handlers for normal request-and-response calls, a unary-stream handler for the live event subscription, and a generic service handler that is registered on the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `MessageService.SendTextMessage`  (lines 364–388)

```
def SendTextMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for sending a text message to a target server. It is useful when code wants to make a direct call without first creating a reusable stub.

**Data flow**: It receives a text-message request, a target server address, and optional call settings such as credentials, timeout, metadata, and compression. It serializes the request, sends it to the SendTextMessage endpoint using gRPC, and parses the returned bytes into a message response.

**Call relations**: Client code can call this static helper directly. It hands the actual network work to `grpc.experimental.unary_unary`, which performs a single request and waits for one response.


##### `MessageService.SendAttachmentMessage`  (lines 391–415)

```
def SendAttachmentMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending a message that includes an attachment. It wraps the gRPC details so callers pass typed objects rather than raw bytes.

**Data flow**: It receives an attachment-message request, target address, and optional call settings. It converts the request to bytes, sends it to the SendAttachmentMessage RPC path, and converts the response bytes into a message response.

**Call relations**: This helper is used by client-side code that wants a one-off attachment send. It delegates the actual network exchange to `grpc.experimental.unary_unary`.


##### `MessageService.SendMultipartMessage`  (lines 418–442)

```
def SendMultipartMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending a multipart message. Multipart means the message may contain several pieces rather than just one simple text body.

**Data flow**: It receives a multipart-message request, target server, and optional gRPC settings. It serializes the request, calls the SendMultipartMessage endpoint, and parses the single returned message response.

**Call relations**: Client code uses this as a shortcut around creating a stub. The method hands off to `grpc.experimental.unary_unary` because the RPC has one request and one response.


##### `MessageService.SendCustomizedMiniAppMessage`  (lines 445–469)

```
def SendCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending a customized iMessage mini-app card. It packages the request for the correct remote endpoint.

**Data flow**: It receives the mini-app message request, a target address, and optional settings such as credentials and timeout. It turns the request into network bytes, calls the SendCustomizedMiniAppMessage endpoint, and parses the returned message response.

**Call relations**: Client code may call this static method for a single send operation. The method relies on `grpc.experimental.unary_unary` to perform the network request and response handling.


##### `MessageService.UpdateCustomizedMiniAppMessage`  (lines 472–496)

```
def UpdateCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for updating a customized iMessage mini-app card. It hides the low-level gRPC serialization details from callers.

**Data flow**: It receives an update request, target server, and optional call options. It serializes the request, sends it to the UpdateCustomizedMiniAppMessage endpoint, and parses the returned message response.

**Call relations**: This is a client-side convenience method for one update call. It delegates the actual remote procedure call to `grpc.experimental.unary_unary`.


##### `MessageService.EditMessage`  (lines 499–523)

```
def EditMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for editing an existing message. Callers use it to send an edit request to a remote message service.

**Data flow**: It receives an edit request, target address, and optional gRPC settings. It serializes the request, sends it to the EditMessage endpoint, and deserializes the single response into a message response object.

**Call relations**: Client code can use this instead of constructing `MessageServiceStub`. The network call itself is performed by `grpc.experimental.unary_unary`.


##### `MessageService.UnsendMessage`  (lines 526–550)

```
def UnsendMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for retracting an existing message. A successful call returns an empty response, meaning there is no extra data beyond success.

**Data flow**: It receives an unsend request, target server, and optional settings. It serializes the request, sends it to the UnsendMessage endpoint, and parses the response as an empty protobuf message.

**Call relations**: This helper is called by client code that wants a one-off unsend operation. It uses `grpc.experimental.unary_unary` because the call sends one request and expects one final response.


##### `MessageService.SetReaction`  (lines 553–577)

```
def SetReaction(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for setting a reaction on a message. It returns the updated message snapshot when the server supports the operation.

**Data flow**: It receives a reaction request, a target address, and optional call settings. It serializes the request, sends it to the SetReaction endpoint, and parses the returned bytes into a message response.

**Call relations**: Client code can use this static shortcut to avoid creating a reusable stub. The helper delegates to `grpc.experimental.unary_unary` for the actual network exchange.


##### `MessageService.PlaceSticker`  (lines 580–604)

```
def PlaceSticker(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for placing a sticker on a message. It turns a typed sticker-placement request into the correct remote gRPC call.

**Data flow**: It receives a sticker-placement request, target server, and optional options. It serializes the request, calls the PlaceSticker endpoint, and parses the single response into a message response.

**Call relations**: This function is a client convenience wrapper. It relies on `grpc.experimental.unary_unary` to send the request and receive the response.


##### `MessageService.NotifySilencedMessage`  (lines 607–631)

```
def NotifySilencedMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for triggering the “Notify Anyway” action on a silenced message. A successful call returns an empty response.

**Data flow**: It receives a notify request, target address, and optional gRPC settings. It serializes the request, sends it to the NotifySilencedMessage endpoint, and parses the response as an empty protobuf message.

**Call relations**: Client code uses this helper for a single notification action. The method hands the call to `grpc.experimental.unary_unary`, which performs one request and waits for one response.


##### `MessageService.GetMessage`  (lines 634–658)

```
def GetMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for reading one message from the remote service. It returns a typed get-message response.

**Data flow**: It receives a get-message request, target server, and optional settings. It serializes the request, sends it to the GetMessage endpoint, and deserializes the returned bytes into a get-message response.

**Call relations**: Client code can call this directly for a one-off read. It delegates the network request and response to `grpc.experimental.unary_unary`.


##### `MessageService.ListRecentMessages`  (lines 661–685)

```
def ListRecentMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for retrieving recent messages. The request can carry filters such as time range, sender direction, or read status.

**Data flow**: It receives a recent-message list request, target address, and optional call settings. It serializes the request, calls the ListRecentMessages endpoint, and parses the returned bytes into a list response.

**Call relations**: This is a client-side shortcut for one listing operation. The actual gRPC work is done by `grpc.experimental.unary_unary`.


##### `MessageService.ListChatMessages`  (lines 688–712)

```
def ListChatMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for retrieving messages from a specific chat. It packages the request for the chat-history endpoint.

**Data flow**: It receives a chat-message list request, target server, and optional settings. It serializes the request, sends it to the ListChatMessages endpoint, and parses the returned bytes into a list response.

**Call relations**: Client code may use this static helper instead of a stub when making a single chat-history request. It delegates to `grpc.experimental.unary_unary`.


##### `MessageService.GetEmbeddedMedia`  (lines 715–739)

```
def GetEmbeddedMedia(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for retrieving embedded media attached to or contained in a message. It returns a typed response that can include raw media bytes.

**Data flow**: It receives an embedded-media request, target server, and optional call settings. It serializes the request, calls the GetEmbeddedMedia endpoint, and parses the returned bytes into an embedded-media response.

**Call relations**: This helper is used by clients that need one media fetch over gRPC. It hands the request to `grpc.experimental.unary_unary`, matching the one-request, one-response shape of the endpoint.


##### `MessageService.SubscribeMessageEvents`  (lines 742–766)

```
def SubscribeMessageEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for subscribing to live message events. Unlike the other helpers, it expects a stream of responses over time rather than one final response.

**Data flow**: It receives a subscription request, target address, and optional gRPC settings. It serializes the request, opens the SubscribeMessageEvents endpoint, and parses each incoming response in the event stream as it arrives.

**Call relations**: Client code calls this when it wants ongoing updates about message changes. The method delegates to `grpc.experimental.unary_stream`, which is the gRPC helper for one request followed by many streamed responses.


### Provider contract
The provider interface defines the message records, errors, and actions that concrete iMessage providers must support.

### `extensions/imessage/ufo_ext_imessage/provider.py`

`data_model` · `cross-cutting`

This file is the “plug shape” for the iMessage extension. The rest of the app wants to send texts, receive new messages, download attachments, and recover after reconnecting, but it should not need to know which concrete iMessage backend is being used. This file solves that by defining small data containers and a provider interface.

The data classes are plain, frozen records. A MessageAttachment describes one attached file. An InboundMessage describes a received message, including who sent it, which conversation it belongs to, its text, its attachments, and whether it was direct. A ProviderEvent is the wrapper used when reading from the provider; it can carry a message, a sequence number, or information about the latest known position in the stream. A “sequence” is like a page number in a logbook: it lets the system know where it left off.

MessageProvider is a Protocol, meaning “any object with these methods counts.” It describes what a provider must offer: assign a phone line, catch up on missed messages, subscribe to live messages, send text or files, download attachments, and translate provider-specific errors into meanings the surface layer can understand. Without this file, the iMessage surface would be tied to one backend instead of being able to work with any compatible provider.

#### Function details

##### `MessageProvider.installation_id`  (lines 37–37)

```
def installation_id(self) -> str
```

**Purpose**: This property gives the unique identifier for the provider installation. The rest of the system can use it to tell one configured provider instance from another.

**Data flow**: The caller asks the provider for its installation identity. The provider reads whatever identity it was configured with and returns it as a string. Nothing is changed.

**Call relations**: This is part of the provider contract. A concrete provider supplies the value when the system needs to identify which installation is in use.


##### `MessageProvider.assign_line`  (lines 39–39)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This asks the provider to attach or reserve a phone number for use by this iMessage extension. The idempotency key is a repeat-safe token, so retrying the same request should not accidentally create duplicate work.

**Data flow**: The caller provides a phone number and an idempotency key. The provider sends or records that assignment request in its own backend. It returns a string identifying the assigned line or resulting provider-side resource.

**Call relations**: This is part of setup or provisioning. Code that configures the iMessage surface can call it on whatever concrete provider implements this protocol.


##### `MessageProvider.catch_up`  (lines 41–41)

```
def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This reads older provider events that happened after a saved sequence number. It lets the system recover messages that arrived while it was offline or not listening.

**Data flow**: The caller gives the last sequence number it already processed, or nothing if it has no saved position. The provider produces an asynchronous stream of ProviderEvent objects after that point. The caller consumes those events one by one and updates its own state.

**Call relations**: ImessageSurface._catch_up calls this when it needs to fill in missed history before or while returning to normal operation. The provider hands events back to the surface, which decides how to store or act on them.

*Call graph*: called by 1 (_catch_up).


##### `MessageProvider.subscribe`  (lines 43–43)

```
def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This opens a live stream of new provider events. It is how the iMessage surface keeps listening for messages as they arrive.

**Data flow**: The caller passes an asyncio.Event, which is a small signal object used by asynchronous code. The provider starts connecting or listening, sets the signal when it is ready, and then yields ProviderEvent objects as new activity appears.

**Call relations**: ImessageSurface._pump_live calls this for the live message pump. Once the stream is active, the provider feeds events to the surface so the rest of the app can react to incoming messages.

*Call graph*: called by 1 (_pump_live).


##### `MessageProvider.send_text`  (lines 45–45)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This sends a text message into a conversation through the provider. The idempotency key helps make retries safe if the network or provider fails halfway through.

**Data flow**: The caller supplies a conversation ID, the text to send, and an idempotency key. The provider sends the message through its backend and returns a string, usually the provider-side ID of the sent message.

**Call relations**: ImessageSurface._prove calls this when it needs to send a text as part of its flow. The surface prepares the conversation and message content, then relies on the provider to actually deliver it.

*Call graph*: called by 1 (_prove).


##### `MessageProvider.send_attachment`  (lines 47–53)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This sends a file attachment into a conversation. It is used when the surface needs to send something richer than plain text, such as a contact card.

**Data flow**: The caller provides the target conversation, the filename, the file bytes, and an idempotency key. The provider uploads or sends that file through its backend. It returns a string identifying the sent attachment or message.

**Call relations**: ImessageSurface._send_contact_card calls this when it has prepared contact-card data and needs the provider to deliver it. If something goes wrong, related provider error helpers can be used by the surface to interpret the failure.

*Call graph*: called by 1 (_send_contact_card).


##### `MessageProvider.download_attachment`  (lines 55–55)

```
def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This retrieves the contents of an attachment from the provider. It streams the file in pieces instead of requiring the whole file to be loaded at once.

**Data flow**: The caller gives an attachment ID. The provider reads that attachment from its backend and yields chunks of bytes asynchronously. The caller collects or writes those chunks wherever it needs them.

**Call relations**: ImessageSurface._downloaded_files calls this when it needs the actual file data for attachments mentioned in an incoming message. The provider supplies the raw bytes; the surface decides what to do with the downloaded files.

*Call graph*: called by 1 (_downloaded_files).


##### `MessageProvider.invalidate`  (lines 57–57)

```
async def invalidate(self) -> None
```

**Purpose**: This tells the provider that its current state or connection should no longer be trusted. It gives a concrete provider a chance to clean up, reset, or mark itself unusable.

**Data flow**: The caller invokes the method with no extra data. The provider updates its own internal state, such as closing connections or clearing cached credentials. It returns nothing when the invalidation is complete.

**Call relations**: This is part of the shared provider contract. It is available to orchestration code that needs to tear down or reset a provider after configuration changes or serious failures.


##### `MessageProvider.invalid_cursor`  (lines 59–59)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This checks whether an error means the saved stream position is no longer valid. A cursor is like a bookmark in the provider’s event log; this method answers whether that bookmark can still be used.

**Data flow**: The caller passes an exception. The provider inspects it using its own backend-specific rules and returns true if the error means the sequence or cursor is invalid, otherwise false. The error itself is not changed.

**Call relations**: This is a diagnostic helper in the provider contract. It lets higher-level code decide whether it should reset its saved position and perform a broader catch-up.


##### `MessageProvider.external_error`  (lines 61–61)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This checks whether an exception came from the outside provider system rather than from local application logic. That helps the surface decide how to report or recover from the failure.

**Data flow**: The caller passes an exception. The provider examines the exception and returns true if it represents a provider-side or network-side problem, otherwise false. No state is changed.

**Call relations**: ImessageSurface._consume_connected, ImessageSurface._downloaded_files, and ImessageSurface._send_contact_card call this when operations fail. The surface uses the answer to choose the right error path instead of treating every exception the same way.

*Call graph*: called by 3 (_consume_connected, _downloaded_files, _send_contact_card).


##### `MessageProvider.error_code`  (lines 63–63)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This turns a provider-specific exception into a stable text code the rest of the system can use. It gives higher layers a simple label instead of forcing them to understand every backend’s error format.

**Data flow**: The caller provides an exception. The provider reads the details it understands and returns a string code describing the failure. The exception is not modified.

**Call relations**: ImessageSurface._send_contact_card calls this when sending an attachment fails and it needs a clear provider error code. The provider translates the raw failure into a form the surface can report or act on.

*Call graph*: called by 1 (_send_contact_card).
