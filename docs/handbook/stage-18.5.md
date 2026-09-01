# iMessage Provider and Generated Service APIs  `stage-18.5`

This stage is shared behind-the-scenes support for the iMessage extension. It defines the “language” that the rest of the system uses when it talks about iMessage data or calls iMessage services over the network. The hand-written provider.py file is the local contract: it says what an iMessage provider must offer, and it defines the simple shapes for messages and attachments that other code can rely on.

The other files are generated from Protocol Buffers, a format for describing structured data so different programs agree on what each message contains. The attachment, chat, event, and message *_pb2.py files define the request and response shapes for those four service areas. The matching *_pb2_grpc.py files add the gRPC wiring, which is the network calling layer. They let a client call remote methods and let a server attach real implementations. Together, these files act like standard plugs and sockets: provider.py defines what this project expects, while the generated modules make sure network data fits correctly.

## Files in this stage

### Attachment Service API
Generated protobuf message definitions and gRPC bindings for attachment-related iMessage operations.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2.py`

`generated` · `request handling and serialization`

This file is machine-made from a Protocol Buffers definition file, which is a language-neutral way to describe messages and services. In plain terms, it is a shared dictionary for the attachment part of the iMessage extension: it defines the names and fields for asking about an attachment, uploading one, and downloading one. Without this file, Python code would not know how to build or read these attachment messages in the exact format expected by the rest of the system.

The file first checks that the installed Protocol Buffers runtime is the version the generated code expects. It then imports related message definitions, such as attachment details and companion-device information. After that, it loads a serialized description of the service into Protobuf’s descriptor pool. A descriptor is metadata that says, for example, “GetAttachmentInfoRequest has an attachment_guid text field” or “DownloadAttachmentResponse can carry either a header, a primary data chunk, or a companion data chunk.”

The important service described here is AttachmentService. It includes methods for getting attachment metadata, uploading attachment bytes, and streaming a download in pieces. One method also carries an HTTP mapping, meaning a REST-style request to `/v1/attachments/{attachment_guid}` can correspond to the Protobuf service call. Because this is generated code, people should not edit it directly; changes should be made in the original `.proto` file and regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2_grpc.py`

`generated` · `service setup and request handling`

This file is like the phonebook and switchboard for attachment transfer over gRPC, which is a system for calling functions on another process or machine as if they were local. The actual attachment rules are not written here. Instead, this file defines the standard client and server shapes that both sides must use so they can talk to each other correctly.

At import time, it first checks that the installed grpc package is new enough for the generated code. If the version is too old, it stops immediately with a clear error, because mismatched generated code and runtime libraries can fail in confusing ways later.

The client side is AttachmentServiceStub. Given a gRPC channel, it creates callable methods for getting attachment metadata, uploading an attachment, and downloading an attachment. It knows which network path to call and how to turn request and response messages into bytes and back.

The server side is AttachmentServiceServicer. It is a base class with placeholder methods that raise “not implemented.” A real server subclasses it and fills in the behavior. The helper add_AttachmentServiceServicer_to_server connects that real implementation to a gRPC server.

There is also an experimental AttachmentService class with static convenience methods for making one-off calls without manually creating a stub.

#### Function details

##### `AttachmentServiceStub.__init__`  (lines 37–57)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object that knows how to call the remote attachment service. A program uses this when it already has a gRPC channel to the server and wants simple Python callables for each attachment operation.

**Data flow**: It receives a gRPC channel. It asks that channel to create three remote-call functions: one for attachment metadata, one for upload, and one for download. After construction, the stub has GetAttachmentInfo, UploadAttachment, and DownloadAttachment attributes that serialize outgoing request messages, send them to the right service path, and deserialize incoming responses.

**Call relations**: Client code creates this stub before making attachment requests. The stub does not decide what attachments mean; it only packages calls so they match the service contract defined by the protobuf messages.


##### `AttachmentServiceServicer.GetAttachmentInfo`  (lines 69–74)

```
def GetAttachmentInfo(self, request, context)
```

**Purpose**: Acts as the server-side placeholder for looking up attachment metadata without reading the attachment bytes. Real servers are expected to override this method with actual lookup logic.

**Data flow**: It receives a request and a gRPC context, which carries details about the current remote call. In this generated base version, it marks the call as unimplemented, adds the message “Method not implemented!”, and raises an error. Nothing useful is returned until a subclass replaces it.

**Call relations**: The server registration helper wires this method into the gRPC server. When a client calls GetAttachmentInfo, gRPC will reach the registered servicer method; in production, that should be an overridden implementation rather than this placeholder.


##### `AttachmentServiceServicer.UploadAttachment`  (lines 76–87)

```
def UploadAttachment(self, request, context)
```

**Purpose**: Acts as the server-side placeholder for uploading an attachment, including the main file and possibly an extra sidecar file. Real servers override it so the upload can be saved atomically, meaning either all parts are saved or none are visible.

**Data flow**: It receives an upload request and a gRPC context. In the generated base class, it does not inspect or save the payload. It sets the response status to unimplemented, records a short explanation, and raises an error instead of returning an upload response.

**Call relations**: The registration helper can expose this method as the UploadAttachment RPC endpoint. A real attachment server supplies a subclass implementation so client upload calls do useful work instead of hitting this default failure.


##### `AttachmentServiceServicer.DownloadAttachment`  (lines 89–95)

```
def DownloadAttachment(self, request, context)
```

**Purpose**: Acts as the server-side placeholder for downloading attachment bytes as a stream. Streaming means the server can send the file in pieces instead of one giant response.

**Data flow**: It receives a download request and a gRPC context. The generated base version sends no file chunks. It marks the call as unimplemented, adds an explanatory detail, and raises an error.

**Call relations**: The server registration helper connects this method to the DownloadAttachment RPC endpoint as a server-streaming call. In a working server, a subclass replaces it with code that yields download response frames.


##### `add_AttachmentServiceServicer_to_server`  (lines 98–119)

```
def add_AttachmentServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a concrete attachment service implementation to a gRPC server. Without this step, the server may have working Python methods, but incoming network requests would not know where to go.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table that maps each public RPC name to the matching servicer method, along with the correct request parser and response writer. It then adds that table to the server so future remote calls are routed to the supplied servicer.

**Call relations**: Server startup code calls this after creating a real AttachmentServiceServicer subclass. Inside, it uses grpc.unary_unary_rpc_method_handler for request-and-single-response methods, grpc.unary_stream_rpc_method_handler for the download stream, and grpc.method_handlers_generic_handler to package the whole service before adding it to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `AttachmentService.GetAttachmentInfo`  (lines 133–157)

```
def GetAttachmentInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for making a one-off remote call to fetch attachment metadata. It is useful when code wants to call the service directly by target address rather than first building an AttachmentServiceStub.

**Data flow**: It receives a GetAttachmentInfo request, a target server address, and optional call settings such as credentials, timeout, metadata, compression, and whether insecure transport is allowed. It serializes the request, sends a unary gRPC call, meaning one request and one response, and converts the response bytes back into a GetAttachmentInfoResponse.

**Call relations**: This convenience method hands the actual network work to grpc.experimental.unary_unary. It follows the same service path and message formats as the stub’s GetAttachmentInfo method, just through gRPC’s experimental direct-call API.


##### `AttachmentService.UploadAttachment`  (lines 160–184)

```
def UploadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for uploading an attachment with a single request and receiving a single response. It avoids manually creating a stub, while still using the same gRPC service contract.

**Data flow**: It receives an UploadAttachment request, a target server address, and optional call settings such as credentials, timeout, metadata, compression, and readiness behavior. It turns the request message into bytes, sends it to the UploadAttachment RPC path, then turns the response bytes into an UploadAttachmentResponse.

**Call relations**: This method delegates the remote call to grpc.experimental.unary_unary. It mirrors the stub-based UploadAttachment call, so it is another entry point into the same server-side UploadAttachment implementation.


##### `AttachmentService.DownloadAttachment`  (lines 187–211)

```
def DownloadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for downloading an attachment as a stream of response messages. This is meant for file data that may arrive in multiple chunks instead of one complete response.

**Data flow**: It receives a DownloadAttachment request, a target server address, and optional call settings such as credentials, timeout, metadata, compression, and readiness behavior. It serializes the request, starts a unary-to-stream gRPC call, meaning one request followed by many possible response frames, and deserializes each incoming frame into a DownloadAttachmentResponse.

**Call relations**: This method hands the work to grpc.experimental.unary_stream. It uses the same RPC path and message formats as the stub’s DownloadAttachment method, so both client styles reach the same server-side download endpoint.


### Chat Service API
Generated protobuf message definitions and gRPC bindings for chat-related iMessage operations.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2.py`

`generated` · `import time and API request/response serialization`

This file is like a shared form book for the chat part of the iMessage extension. It does not contain the real behavior for creating chats or marking them read. Instead, it defines the exact data formats and service contract that the rest of the system uses when talking over Protocol Buffers, a compact data format often used for APIs.

At import time, it checks that the installed Protocol Buffers Python runtime is the expected version. Then it loads definitions from related files, such as chat types, message types, address types, and streaming event types. After that, it registers a serialized description of the chat service with Python’s Protocol Buffers machinery. That description includes message types such as CreateChatRequest, GetChatResponse, SetTypingRequest, and SubscribeChatEventsResponse.

The service definition named ChatService describes operations such as creating a chat, getting a chat, counting chats, setting typing status, changing chat backgrounds, and subscribing to chat events. Some methods also carry HTTP route annotations, such as /v1/chats or /v1/chats:get, so the same contract can be exposed through HTTP-style gateways.

Because this is generated code, people should not edit it directly. If it were missing or out of sync with the original .proto file, Python code using this API could fail to import, serialize the wrong fields, or disagree with other services about what a chat request or response means.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2_grpc.py`

`generated` · `client/server setup and request handling`

This file is like a phone directory and switchboard for chat operations over gRPC, a system for calling functions on another process or machine as if they were local methods. The actual chat behavior is not written here. Instead, this file translates between Python objects and network messages, and connects method names such as CreateChat or GetChat to the right request and response message formats.

There are three main pieces. ChatServiceStub is for clients: given a gRPC channel, it creates ready-to-use call objects for creating chats, marking chats read, setting chat backgrounds, sending typing indicators, reading chat details, and subscribing to chat event streams. ChatServiceServicer is for server authors: it lists the methods the server must implement, but the default versions only report “not implemented.” add_ChatServiceServicer_to_server connects a real servicer implementation to a gRPC server, telling gRPC how to decode incoming bytes into request objects and encode responses back into bytes.

The ChatService class offers an alternate experimental one-call style API, where each static method sends a request directly to a target address. The file also checks that the installed grpc package is new enough for the generated code. Without this file, the protobuf message definitions would exist, but Python clients and servers would not have the standard gRPC hooks needed to talk to each other.

#### Function details

##### `ChatServiceStub.__init__`  (lines 37–92)

```
def __init__(self, channel)
```

**Purpose**: Creates a client-side object with one callable attribute for each remote chat operation. A caller uses this when it already has a gRPC channel and wants convenient methods such as CreateChat, GetChat, or SubscribeChatEvents.

**Data flow**: It receives a gRPC channel, which represents a connection path to a server. It asks that channel to build remote-call helpers for each chat method, pairing each helper with the correct request-to-bytes converter and bytes-to-response converter. After construction, the stub object holds these helpers as attributes that client code can call.

**Call relations**: Client code creates this stub before making chat-service calls. The stub does not implement chat behavior itself; it hands each request to the gRPC channel, which sends it to the server endpoint named in the generated method path.


##### `ChatServiceServicer.CreateChat`  (lines 103–108)

```
def CreateChat(self, request, context)
```

**Purpose**: Defines the server-side method slot for creating a chat. In this generated base class it is only a placeholder, so real server code must override it.

**Data flow**: It receives a CreateChat request and a gRPC context object, which carries status information for the request. The placeholder marks the call as unimplemented, adds a short error detail, and raises an error instead of returning a chat response.

**Call relations**: When add_ChatServiceServicer_to_server registers a servicer, gRPC can call this method for incoming CreateChat requests. A working service is expected to subclass or replace this method so the request reaches real chat-creation logic.


##### `ChatServiceServicer.MarkChatRead`  (lines 110–114)

```
def MarkChatRead(self, request, context)
```

**Purpose**: Defines the server-side method slot for marking a chat as read. The generated version is a placeholder that tells callers the method has not been implemented.

**Data flow**: It receives a MarkChatRead request and the request context. It sets the context status to unimplemented, records an explanatory message, and raises an error instead of returning the empty success response expected by the API.

**Call relations**: gRPC calls this method when a registered servicer receives a MarkChatRead request. Real server code must override it so the request can update read state in the chat system.


##### `ChatServiceServicer.SetBackground`  (lines 116–122)

```
def SetBackground(self, request, context)
```

**Purpose**: Defines the server-side method slot for setting a chat background image or data. In this base class it deliberately does nothing except report that no implementation exists.

**Data flow**: It receives a SetBackground request, which may include byte data for the background, and a gRPC context. It changes the context to an unimplemented error state and raises an exception rather than returning the empty success message.

**Call relations**: Incoming SetBackground calls are routed here through the server registration created by add_ChatServiceServicer_to_server. A concrete server implementation should override it and perform the actual background change.


##### `ChatServiceServicer.RemoveBackground`  (lines 124–128)

```
def RemoveBackground(self, request, context)
```

**Purpose**: Defines the server-side method slot for removing a chat background. The generated base method is only a reminder that the real behavior must be supplied elsewhere.

**Data flow**: It receives a RemoveBackground request and context. It marks the gRPC call as unimplemented, stores the detail message, and raises an error instead of returning an empty success response.

**Call relations**: After the service is registered, gRPC may call this method for RemoveBackground requests. Production code must provide an overriding method to actually remove the background.


##### `ChatServiceServicer.ShareContactInfo`  (lines 130–137)

```
def ShareContactInfo(self, request, context)
```

**Purpose**: Defines the server-side method slot for sharing the local user's contact card into a chat. The generated version is not functional and only reports that it must be implemented.

**Data flow**: It receives a ShareContactInfo request and a context object. It sets the status to unimplemented, adds an error detail, and raises an exception instead of returning the expected empty response.

**Call relations**: The server registration maps incoming ShareContactInfo network calls to this method. A real servicer should override it to push the user's name-and-photo contact information through the underlying iMessage system.


##### `ChatServiceServicer.SetTyping`  (lines 139–144)

```
def SetTyping(self, request, context)
```

**Purpose**: Defines the server-side method slot for sending a temporary typing indicator. The generated base method is a placeholder and does not send any typing state.

**Data flow**: It receives a SetTyping request and request context. It marks the request as unimplemented and raises an error, so no success response is produced.

**Call relations**: gRPC routes incoming SetTyping requests here once a servicer is registered. A concrete implementation should override it to send the transient typing notification.


##### `ChatServiceServicer.GetChat`  (lines 146–152)

```
def GetChat(self, request, context)
```

**Purpose**: Defines the server-side method slot for reading details about one chat. In this generated base class, it only reports that the method has not been implemented.

**Data flow**: It receives a GetChat request and context. It sets an unimplemented status and raises an error instead of returning a GetChat response with chat information.

**Call relations**: The registration helper connects the GetChat RPC name to this method. Real server code overrides it so incoming read requests can be answered from the chat data source.


##### `ChatServiceServicer.GetChatCount`  (lines 154–158)

```
def GetChatCount(self, request, context)
```

**Purpose**: Defines the server-side method slot for asking how many chats match a request. The base method is not usable on its own.

**Data flow**: It receives a GetChatCount request and context. It changes the call status to unimplemented, records a detail message, and raises an exception rather than returning a count response.

**Call relations**: gRPC calls this method for GetChatCount requests after server registration. A working server must override it to count chats and return the result.


##### `ChatServiceServicer.HasBackground`  (lines 160–164)

```
def HasBackground(self, request, context)
```

**Purpose**: Defines the server-side method slot for checking whether a chat has a background. The generated method is only a placeholder.

**Data flow**: It receives a HasBackground request and context. It marks the call as unimplemented and raises an error instead of returning a true-or-false style response object.

**Call relations**: The server registration maps HasBackground network calls to this method. A real implementation should override it to inspect chat background state and return the answer.


##### `ChatServiceServicer.SubscribeChatEvents`  (lines 166–170)

```
def SubscribeChatEvents(self, request, context)
```

**Purpose**: Defines the server-side method slot for subscribing to a stream of chat events. The base method does not produce any event stream.

**Data flow**: It receives a SubscribeChatEvents request and context. It sets the status to unimplemented and raises an error instead of yielding or returning chat event responses over time.

**Call relations**: This is the server-side target for the SubscribeChatEvents streaming RPC. add_ChatServiceServicer_to_server registers it as a unary-to-stream method, meaning one request can lead to many responses, but a real servicer must override it to supply those events.


##### `add_ChatServiceServicer_to_server`  (lines 173–229)

```
def add_ChatServiceServicer_to_server(servicer, server)
```

**Purpose**: Registers a chat service implementation with a gRPC server. This is the bridge that lets incoming network calls reach the Python methods on a servicer object.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table that maps each public RPC name to the matching servicer method, along with the correct request decoder and response encoder. It then gives that table to the server so future incoming requests can be routed and translated correctly.

**Call relations**: Server setup code calls this after creating a concrete ChatServiceServicer. Inside, it asks gRPC to create unary-unary handlers for normal one-request, one-response calls, a unary-stream handler for event subscriptions, and a generic service handler for the full ChatService name; then it attaches those handlers to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `ChatService.CreateChat`  (lines 242–266)

```
def CreateChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Sends a CreateChat request using gRPC's experimental direct-call API. It is an alternate client helper for creating a chat without first building a ChatServiceStub object.

**Data flow**: It receives a request object, a target server address, and optional connection settings such as credentials, timeout, metadata, and compression. It serializes the request, sends it to the CreateChat RPC path, decodes the returned bytes as a CreateChat response, and returns that response to the caller.

**Call relations**: Client code may call this static method directly. It hands the actual network work to grpc.experimental.unary_unary, using the generated method path and message converters.


##### `ChatService.MarkChatRead`  (lines 269–293)

```
def MarkChatRead(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Sends a request to mark a chat as read through the experimental direct-call API. It is useful for one-off client calls where creating a stub is not desired.

**Data flow**: It takes a MarkChatRead request, a target server, and optional call settings. It converts the request to bytes, calls the MarkChatRead RPC path, decodes the server reply as an empty protobuf message, and returns that empty success marker.

**Call relations**: This static helper is called by client-side code. It delegates the transport work to grpc.experimental.unary_unary, which contacts the server method registered under the MarkChatRead path.


##### `ChatService.SetBackground`  (lines 296–320)

```
def SetBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Sends a request to set a chat background using the experimental direct-call API. It packages the background-setting request for the remote chat service.

**Data flow**: It receives a SetBackground request, target address, and optional gRPC settings. It serializes the request, sends it to the SetBackground RPC path, reads the response as an empty protobuf message, and returns that success marker.

**Call relations**: Client code can use this instead of ChatServiceStub.SetBackground. It hands off to grpc.experimental.unary_unary with the generated path and the correct message serialization functions.


##### `ChatService.RemoveBackground`  (lines 323–347)

```
def RemoveBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Sends a request to remove a chat background through the experimental direct-call API. It provides a direct client-side entry to the RemoveBackground remote method.

**Data flow**: It takes a RemoveBackground request, a target, and optional call options. It turns the request into bytes, sends it to the RemoveBackground RPC path, decodes the server's empty response, and returns it.

**Call relations**: Client code calls this when it wants to invoke the server without constructing a stub. The function delegates to grpc.experimental.unary_unary, which performs the remote call.


##### `ChatService.ShareContactInfo`  (lines 350–374)

```
def ShareContactInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Sends a request to share the local user's contact information into a chat. It is the experimental direct-call version of the ShareContactInfo client method.

**Data flow**: It receives a ShareContactInfo request, the target server, and optional connection details. It serializes the request, sends it over the ShareContactInfo RPC path, decodes the empty response, and returns that response as the sign of success.

**Call relations**: Client code may call this static method directly. It relies on grpc.experimental.unary_unary to contact the server-side ShareContactInfo handler.


##### `ChatService.SetTyping`  (lines 377–401)

```
def SetTyping(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Sends a temporary typing-indicator request to the chat service. This is the experimental direct-call helper for the SetTyping RPC.

**Data flow**: It receives a SetTyping request, target server information, and optional call settings. It serializes the request, sends it to the SetTyping RPC path, decodes the empty response, and returns it.

**Call relations**: This is used by client code that wants to call SetTyping directly. The method passes control to grpc.experimental.unary_unary, which does the network call and response decoding.


##### `ChatService.GetChat`  (lines 404–428)

```
def GetChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Requests details for a chat using the experimental direct-call API. It is a direct client helper for reading one chat from the remote service.

**Data flow**: It takes a GetChat request, a target server, and optional settings. It converts the request to bytes, sends it to the GetChat RPC path, decodes the reply as a GetChat response, and returns the chat information to the caller.

**Call relations**: Client code can use this method instead of creating ChatServiceStub and calling GetChat there. It delegates the remote procedure call to grpc.experimental.unary_unary.


##### `ChatService.GetChatCount`  (lines 431–455)

```
def GetChatCount(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Requests a count of chats from the remote service. It is the experimental direct-call helper for the GetChatCount RPC.

**Data flow**: It receives a GetChatCount request, a target server address, and optional connection choices. It serializes the request, sends it to the GetChatCount RPC path, decodes the reply as a GetChatCount response, and returns that count response.

**Call relations**: Client code calls this for a direct one-off count request. It hands the network call to grpc.experimental.unary_unary with the generated request and response converters.


##### `ChatService.HasBackground`  (lines 458–482)

```
def HasBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Asks the remote service whether a chat has a background. It is the experimental direct-call client helper for this background-check operation.

**Data flow**: It receives a HasBackground request, target server, and optional call settings. It serializes the request, sends it to the HasBackground RPC path, decodes the returned bytes as a HasBackground response, and returns that answer.

**Call relations**: Client code may call this static method directly. It delegates to grpc.experimental.unary_unary, which contacts the server-side HasBackground RPC handler.


##### `ChatService.SubscribeChatEvents`  (lines 485–509)

```
def SubscribeChatEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Starts a subscription to chat events using the experimental direct-call API. Unlike most methods here, one request can lead to a stream of many event responses over time.

**Data flow**: It receives a SubscribeChatEvents request, target server, and optional connection settings. It serializes the request and sends it to the SubscribeChatEvents RPC path, then returns a stream-like result that decodes each incoming response as a SubscribeChatEvents response.

**Call relations**: Client code uses this when it wants ongoing chat updates rather than a single answer. It hands off to grpc.experimental.unary_stream, matching the server registration that treats SubscribeChatEvents as a one-request, many-responses method.


### Event Service API
Generated protobuf message definitions and gRPC bindings for catching up on iMessage event data.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2.py`

`generated` · `import time and request/stream message serialization`

This file is machine-made glue between a Protocol Buffers definition and Python. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the exact shape of messages they send to each other. Here, the agreed messages are for an iMessage “event service,” mainly a stream where a client asks, “What changed after this sequence number?” and receives changes for messages, groups, polls, chats, completion notices, or heartbeat pings.

The file first checks that the installed protobuf runtime is the expected version. That is like checking that the reader has the right dictionary before trying to decode a message. It then imports other generated protobuf files that define the event payloads this service can carry, such as message changes and group changes.

The central piece is `DESCRIPTOR`, which registers the serialized schema with protobuf’s global descriptor pool. From that schema, protobuf’s builder creates Python message classes such as `CatchUpEventsRequest`, `CatchUpEventsResponse`, and `CatchUpEventsComplete`, plus the `EventService` service description. Application code normally imports those generated classes rather than editing this file. If this file were missing or out of date, Python code would not know how to build, parse, or recognize the event-service messages used on the wire.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2_grpc.py`

`generated` · `startup, service registration, and request handling`

This file is machine-generated glue code for gRPC, a system that lets one program call a function running in another program over the network. The service here is about “catching up” on durable iMessage-related events: messages, group changes, polls, and chats. A client can ask for everything newer than a known sequence number, then switch to live event streams without missing anything.

The file has three main pieces. EventServiceStub is the client-side doorway: it creates a callable CatchUpEvents method that sends one request and receives a stream of responses. EventServiceServicer is the server-side base class: real server code is expected to subclass it and replace the placeholder CatchUpEvents method with actual replay logic. add_EventServiceServicer_to_server is the registration step that tells a gRPC server, “when a network request for CatchUpEvents arrives, send it to this servicer method.” EventService is an experimental convenience API for making the same call without manually building a stub.

At import time, the file also checks that the installed grpc Python package is new enough for this generated code. Without this file, the project could still define event messages, but Python clients and servers would not know how to send or receive the CatchUpEvents remote call.

#### Function details

##### `EventServiceStub.__init__`  (lines 45–55)

```
def __init__(self, channel)
```

**Purpose**: This builds the client-side object used to call the remote CatchUpEvents service. Someone uses it when they already have a gRPC channel, which is like an open phone line to a server.

**Data flow**: It receives a gRPC channel. It attaches a CatchUpEvents callable to the stub, teaching it how to turn a CatchUpEventsRequest into bytes before sending it and how to turn each returned byte message back into a CatchUpEventsResponse. After this runs, the stub object can make the network call.

**Call relations**: Client code creates EventServiceStub when it wants to talk to an EventService server. The callable it sets up matches the server method registered by add_EventServiceServicer_to_server, so both sides agree on the method name and message formats.


##### `EventServiceServicer.CatchUpEvents`  (lines 75–79)

```
def CatchUpEvents(self, request, context)
```

**Purpose**: This is the default server-side placeholder for the CatchUpEvents operation. It exists so generated server classes have the right shape, but real application code must override it.

**Data flow**: It receives a request and a gRPC context, which carries details about the current network call. Instead of producing catch-up events, it marks the call as unimplemented, adds a human-readable explanation, and raises NotImplementedError.

**Call relations**: When a servicer is registered with add_EventServiceServicer_to_server, incoming CatchUpEvents requests are directed to this method name. In a working server, a project-specific subclass replaces this placeholder so the request can be answered with real event replay data.


##### `add_EventServiceServicer_to_server`  (lines 82–93)

```
def add_EventServiceServicer_to_server(servicer, server)
```

**Purpose**: This connects a server implementation of EventService to a running gRPC server. Without this registration step, network requests for CatchUpEvents would arrive with no route to the Python method that should answer them.

**Data flow**: It receives a servicer object and a gRPC server. It builds a handler for the CatchUpEvents remote call, including how to decode incoming request bytes and encode outgoing response objects. It then adds those handlers to the server so future network calls can be dispatched correctly.

**Call relations**: Server startup code calls this after creating a concrete EventServiceServicer. Inside, it asks gRPC to create a unary-stream handler, meaning one request leads to many streamed responses, and then wraps that handler under the full service name so the server can match incoming calls to servicer.CatchUpEvents.

*Call graph*: 2 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler).


##### `EventService.CatchUpEvents`  (lines 115–139)

```
def CatchUpEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This is an experimental shortcut for making the CatchUpEvents remote call directly. It lets caller code send one catch-up request to a target server and receive a stream of event responses.

**Data flow**: It receives the request, the target server address, and optional connection settings such as credentials, timeout, metadata, and compression. It serializes the request, sends it through gRPC’s experimental unary-stream helper, and yields deserialized CatchUpEventsResponse messages from the server.

**Call relations**: This is an alternative to creating EventServiceStub and then calling its CatchUpEvents method. It uses the same service path and message converters, so it talks to servers registered through add_EventServiceServicer_to_server in the same protocol-compatible way.


### Message Service API
Generated protobuf message definitions and gRPC bindings for message-related iMessage operations.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2.py`

`generated` · `cross-cutting: imported whenever message-service protobuf types are needed`

This file is machine-made from `message_service.proto`, so people should not edit it by hand. Its job is to give Python code a shared vocabulary for talking about iMessage actions: sending text, sending attachments, editing or unsending messages, reacting, placing stickers, listing messages, getting embedded media, and subscribing to message-change events. Protocol Buffers, often called protobuf, are a compact data format and schema system: like a pre-printed form, they say exactly which fields a message can contain and how those fields are packed for travel over a network or saved data stream.

At import time, the file checks that the installed protobuf runtime is compatible, loads other message definitions it depends on, and registers one large serialized description of the service. The protobuf library then builds Python classes such as request and response message types from that description. It also records service method names and their HTTP-style paths, for example routes for sending text or listing recent messages.

Without this file, Python code would not know the exact field names, nested message types, optional fields, or service definitions expected by the iMessage API. Clients and servers could easily disagree about what a “send message” request looks like.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is produced from a Protocol Buffers service definition, so it is more like a machine-made adapter than hand-written application logic. gRPC is a remote-call system: it lets one program call a function in another program, often over the network, as if it were local. This file defines both sides of that connection for sending, changing, reading, and watching iMessage messages.

On the client side, MessageServiceStub turns friendly Python method names such as SendTextMessage or GetMessage into network calls with the exact service path gRPC expects. It also knows how to turn request objects into bytes before sending them, and how to turn response bytes back into Python message objects afterward.

On the server side, MessageServiceServicer is a base class. Its methods are placeholders that deliberately return “not implemented.” A real server subclasses it and fills in the actual behavior. The helper add_MessageServiceServicer_to_server connects those real methods to a running gRPC server.

The final MessageService class offers an experimental shortcut style for making one-off calls without first building a stub. Without this file, clients and servers would not agree on the method names, wire paths, or message encodings for this service.

#### Function details

##### `MessageServiceStub.__init__`  (lines 46–126)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object with one callable Python attribute for each remote message-service operation. A caller uses this stub when it already has a gRPC channel, which is the connection path to a server.

**Data flow**: It receives a gRPC channel. It asks that channel to create call helpers for each service method, pairing each helper with the correct remote path, request-to-bytes converter, and bytes-to-response converter. After construction, the stub object contains ready-to-use methods such as SendTextMessage, ListChatMessages, and SubscribeMessageEvents.

**Call relations**: Client code creates this stub before making message-service calls. Each generated attribute later hands the request to the gRPC channel, which sends it to the server method registered under the matching service path.


##### `MessageServiceServicer.SendTextMessage`  (lines 146–159)

```
def SendTextMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a plain text iMessage. In this generated base class it is only a placeholder; real servers must override it.

**Data flow**: It receives a text-message request and a gRPC context object, which carries request status and metadata. The placeholder marks the call as unimplemented, adds an explanatory detail, and raises an error instead of returning a message response.

**Call relations**: When a server registers a servicer, the registration helper points incoming SendTextMessage calls at this method. In normal use, that method is supplied by a subclass that performs the real send and returns the fresh message snapshot.


##### `MessageServiceServicer.SendAttachmentMessage`  (lines 161–165)

```
def SendAttachmentMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending an iMessage attachment. The generated version exists only to show the expected method shape.

**Data flow**: It receives an attachment-message request and the gRPC context. It changes the context to say the operation is not implemented, then raises NotImplementedError, so no successful response is produced.

**Call relations**: The server registration helper connects the SendAttachmentMessage RPC name to this method. A real implementation overrides it so clients using the stub can send attachment messages.


##### `MessageServiceServicer.SendMultipartMessage`  (lines 167–171)

```
def SendMultipartMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a message made of multiple parts. The base method is a placeholder to be replaced by service code.

**Data flow**: It receives a multipart-message request and call context. It records an unimplemented status in the context and raises an exception rather than returning a message response.

**Call relations**: Incoming multipart send requests reach this method through the gRPC method table created during server setup. Production code supplies the real behavior by subclassing this servicer.


##### `MessageServiceServicer.SendCustomizedMiniAppMessage`  (lines 173–178)

```
def SendCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending an iMessage mini-app card owned by the caller's extension. The generated method does not actually send anything.

**Data flow**: It takes the mini-app send request and context, sets the response status to unimplemented, stores a short detail message, and raises NotImplementedError.

**Call relations**: The registration helper maps the SendCustomizedMiniAppMessage remote call to this method. A real server replaces the placeholder to create and return the resulting message.


##### `MessageServiceServicer.UpdateCustomizedMiniAppMessage`  (lines 180–185)

```
def UpdateCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for updating an existing customized mini-app message. It is a required interface point, not the real update logic.

**Data flow**: It receives the update request and the context for the RPC call. The placeholder writes an unimplemented status into the context and raises an error, leaving the message unchanged.

**Call relations**: When a client calls the matching remote method, gRPC dispatches to the registered servicer method. Application code is expected to override this base method with actual update behavior.


##### `MessageServiceServicer.EditMessage`  (lines 187–191)

```
def EditMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for editing a previously sent message. The generated base method only reports that no implementation has been provided.

**Data flow**: It receives an edit request and call context. It sets the context status and details to unimplemented, then raises an exception instead of returning the edited message snapshot.

**Call relations**: The server setup helper connects incoming EditMessage calls to this method. Real service code overrides it so the client stub can edit messages through the same RPC path.


##### `MessageServiceServicer.UnsendMessage`  (lines 193–198)

```
def UnsendMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for retracting, or unsending, an existing message. The base class does not perform the retraction.

**Data flow**: It receives an unsend request and a gRPC context. It marks the call as unimplemented and raises NotImplementedError, so it returns no Empty success response.

**Call relations**: Registered servers expose this method name to clients. A real servicer must override it to do the unsend work and return an empty success result when finished.


##### `MessageServiceServicer.SetReaction`  (lines 200–204)

```
def SetReaction(self, request, context)
```

**Purpose**: Defines the server-side slot for adding, changing, or removing a reaction on a message. The generated version is only a placeholder.

**Data flow**: It receives a reaction request and context. It updates the context with an unimplemented status and raises an exception, producing no message response.

**Call relations**: The gRPC registration table routes SetReaction calls here. In a working server, a subclass supplies the reaction logic and returns the updated message snapshot.


##### `MessageServiceServicer.PlaceSticker`  (lines 206–210)

```
def PlaceSticker(self, request, context)
```

**Purpose**: Defines the server-side slot for placing a sticker on a message. The base method communicates that the operation has not been implemented.

**Data flow**: It receives a sticker-placement request and the call context. It sets an unimplemented error on the context and raises NotImplementedError instead of returning a response.

**Call relations**: During server setup, the generated helper binds the PlaceSticker RPC name to this method. Real code overrides it so sticker placement can be requested by clients.


##### `MessageServiceServicer.NotifySilencedMessage`  (lines 212–217)

```
def NotifySilencedMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for triggering Apple's “Notify Anyway” action on a silenced message. The generated method itself only rejects the call as unimplemented.

**Data flow**: It receives a notify request plus the gRPC context. It marks the context as unimplemented and raises an error, so no Empty success response is returned.

**Call relations**: The server registration helper makes this method reachable under the NotifySilencedMessage RPC name. A concrete servicer supplies the Apple-specific action behind that entry point.


##### `MessageServiceServicer.GetMessage`  (lines 219–224)

```
def GetMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for reading one message. The base method is a placeholder and does not look up anything.

**Data flow**: It receives a get-message request and the RPC context. It sets the call status to unimplemented and raises NotImplementedError, so no get-message response is produced.

**Call relations**: When clients call GetMessage through the stub or shortcut API, the registered server method is invoked. A real implementation replaces this placeholder with lookup logic.


##### `MessageServiceServicer.ListRecentMessages`  (lines 226–233)

```
def ListRecentMessages(self, request, context)
```

**Purpose**: Defines the server-side slot for listing recent messages, optionally filtered or paged. The generated base method does not query message history.

**Data flow**: It receives a list request and context. It writes an unimplemented status and details to the context and raises an exception instead of returning a list response.

**Call relations**: The generated server registration maps the ListRecentMessages RPC to this method. A working service overrides it to read recent messages and return the requested page.


##### `MessageServiceServicer.ListChatMessages`  (lines 235–240)

```
def ListChatMessages(self, request, context)
```

**Purpose**: Defines the server-side slot for listing messages within one chat. In this base class it is only the expected method signature.

**Data flow**: It receives a chat-message listing request and context. It marks the call as unimplemented and raises NotImplementedError, producing no list response.

**Call relations**: Incoming ListChatMessages calls are routed here by the generated method table. Application code overrides the method to fetch messages for the requested chat.


##### `MessageServiceServicer.GetEmbeddedMedia`  (lines 242–250)

```
def GetEmbeddedMedia(self, request, context)
```

**Purpose**: Defines the server-side slot for retrieving media embedded in a message. The generated placeholder does not fetch or return any media bytes.

**Data flow**: It receives an embedded-media request and the gRPC context. It sets the status to unimplemented, records a detail message, and raises an error rather than returning media data.

**Call relations**: The registration helper connects the GetEmbeddedMedia RPC to this method. A real server supplies the raw media retrieval behavior behind that same RPC shape.


##### `MessageServiceServicer.SubscribeMessageEvents`  (lines 252–258)

```
def SubscribeMessageEvents(self, request, context)
```

**Purpose**: Defines the server-side slot for subscribing to live message events. This is meant to stream many event updates back over time, but the base method does not stream anything.

**Data flow**: It receives a subscription request and context. The placeholder marks the call as unimplemented and raises an error, so no stream of event responses is produced.

**Call relations**: The server registration uses a streaming handler for this method, because one request can produce many responses. A concrete servicer overrides it to feed live message changes to connected clients.


##### `add_MessageServiceServicer_to_server`  (lines 261–342)

```
def add_MessageServiceServicer_to_server(servicer, server)
```

**Purpose**: Attaches a concrete MessageServiceServicer implementation to a gRPC server. This is the server-side switchboard that tells gRPC which Python method should answer each remote method name.

**Data flow**: It receives a servicer object and a server. It builds a dictionary of method handlers, each with the right request parser, response writer, and servicer method. It then adds those handlers to the server so future network calls can be dispatched correctly.

**Call relations**: Server startup code calls this after creating the real servicer. Inside, it asks gRPC to make unary request/response handlers for ordinary calls, a unary-to-stream handler for SubscribeMessageEvents, and a generic service handler for the whole MessageService before registering them with the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `MessageService.SendTextMessage`  (lines 364–388)

```
def SendTextMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call shortcut for sending a text message to a target server. It is useful when code wants to make the remote call directly without first constructing a stub object.

**Data flow**: It receives a SendTextMessage request, the server target address, and optional connection settings such as credentials, timeout, compression, and metadata. It serializes the request, performs a unary gRPC call, and converts the response bytes into a MessageResponse.

**Call relations**: Client code can call this static method directly. It hands the work to gRPC's experimental unary-unary helper, which contacts the server method registered for SendTextMessage.


##### `MessageService.SendAttachmentMessage`  (lines 391–415)

```
def SendAttachmentMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending an attachment message. It wraps the network details for this one remote method.

**Data flow**: It takes an attachment-send request, a target server, and optional call settings. It turns the request object into bytes, sends it as a single request expecting a single response, and turns the returned bytes into a MessageResponse.

**Call relations**: This shortcut bypasses a prebuilt stub but reaches the same server endpoint. gRPC's experimental unary-unary helper carries the request to the registered SendAttachmentMessage handler.


##### `MessageService.SendMultipartMessage`  (lines 418–442)

```
def SendMultipartMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending a multipart message. It hides the exact gRPC path and encoding rules from the caller.

**Data flow**: It receives a multipart request, server target, and optional settings. It serializes the request, sends one remote call, and deserializes the single MessageResponse returned by the server.

**Call relations**: Client code uses this when it wants a direct static call. The method delegates the actual network exchange to gRPC's experimental unary-unary helper.


##### `MessageService.SendCustomizedMiniAppMessage`  (lines 445–469)

```
def SendCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending a customized iMessage mini-app card. It packages the request for the correct remote service method.

**Data flow**: It receives the mini-app send request, destination target, and optional call controls. It converts the request to bytes, sends it to the named RPC path, and converts the server's bytes back into a MessageResponse.

**Call relations**: This static method is a client-side convenience. It reaches the same SendCustomizedMiniAppMessage server method that a MessageServiceStub would call.


##### `MessageService.UpdateCustomizedMiniAppMessage`  (lines 472–496)

```
def UpdateCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for updating a customized mini-app message. It is a thin wrapper around the matching remote procedure.

**Data flow**: It takes an update request, a server target, and optional connection details. It serializes the request, makes a single request/single response gRPC call, and returns the decoded MessageResponse.

**Call relations**: Client code may call this instead of creating a stub. It delegates to gRPC's experimental unary-unary helper, which routes the call to the server's registered update method.


##### `MessageService.EditMessage`  (lines 499–523)

```
def EditMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for editing a message. It prepares and sends the edit request to the correct remote endpoint.

**Data flow**: It receives an edit request, target server address, and optional call settings. It serializes the request, sends it over gRPC, and deserializes the returned MessageResponse.

**Call relations**: This method is a shortcut for client callers. The real edit work happens on the server method registered under EditMessage.


##### `MessageService.UnsendMessage`  (lines 526–550)

```
def UnsendMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for unsending a message. A successful call returns an empty response object, meaning there is no extra data beyond success.

**Data flow**: It receives an unsend request, target, and optional settings. It serializes the request, sends one gRPC call, and converts the returned bytes into a google.protobuf.Empty object.

**Call relations**: Client code can use this direct wrapper to reach the same UnsendMessage server endpoint used by the stub. gRPC's experimental unary-unary helper performs the transport work.


##### `MessageService.SetReaction`  (lines 553–577)

```
def SetReaction(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for setting a reaction on a message. It returns the server's fresh view of the affected message.

**Data flow**: It takes a reaction request, server target, and optional call options. It turns the request into bytes, sends it as one remote call, and decodes the reply as a MessageResponse.

**Call relations**: This shortcut method sends the request through gRPC to the registered SetReaction server method. It offers the same operation as the stub attribute with less setup.


##### `MessageService.PlaceSticker`  (lines 580–604)

```
def PlaceSticker(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for placing a sticker on a message. It wraps the matching gRPC service path and message encoding.

**Data flow**: It receives a sticker-placement request, target, and optional connection settings. It serializes the request, sends it to the server, and returns the decoded MessageResponse.

**Call relations**: Client code calls this static method for a one-off sticker operation. The actual request is carried by gRPC to the server handler registered for PlaceSticker.


##### `MessageService.NotifySilencedMessage`  (lines 607–631)

```
def NotifySilencedMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for the “Notify Anyway” action on a silenced message. On success it returns an empty response, because the action does not return a message snapshot here.

**Data flow**: It receives a notify request, server target, and optional call controls. It serializes the request, sends one gRPC call, and decodes the server response as an Empty object.

**Call relations**: This is a client convenience for reaching the NotifySilencedMessage server method. It relies on gRPC's experimental unary-unary call helper for the network exchange.


##### `MessageService.GetMessage`  (lines 634–658)

```
def GetMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for reading one message. It is useful for a simple lookup without manually creating a stub.

**Data flow**: It receives a get-message request, target server, and optional settings. It serializes the request, sends it over gRPC, and turns the response bytes into a GetMessageResponse.

**Call relations**: The method calls into gRPC's experimental unary-unary helper. That helper contacts the server method registered as GetMessage.


##### `MessageService.ListRecentMessages`  (lines 661–685)

```
def ListRecentMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for listing recent messages. It packages paging and filtering information inside the request object and returns the server's list response.

**Data flow**: It receives a recent-messages request, target, and optional call settings. It serializes the request, sends one remote call, and deserializes the returned ListRecentMessagesResponse.

**Call relations**: Client code can call this static method instead of going through MessageServiceStub. The server-side ListRecentMessages implementation supplies the actual message list.


##### `MessageService.ListChatMessages`  (lines 688–712)

```
def ListChatMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for listing messages in a particular chat. It hides the remote path and conversion between Python objects and network bytes.

**Data flow**: It receives a chat-message list request, target server, and optional settings. It sends the serialized request through gRPC and decodes the reply as a ListChatMessagesResponse.

**Call relations**: This direct wrapper reaches the same ListChatMessages endpoint as the regular stub. gRPC carries the call to the server method registered during setup.


##### `MessageService.GetEmbeddedMedia`  (lines 715–739)

```
def GetEmbeddedMedia(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for retrieving media embedded in a message. It returns the response object that may contain the media data.

**Data flow**: It receives an embedded-media request, a target server, and optional call controls. It serializes the request, sends one request over gRPC, and decodes the response as GetEmbeddedMediaResponse.

**Call relations**: Client code uses this as a shortcut to the GetEmbeddedMedia RPC. The real media lookup occurs in the server implementation registered for that method.


##### `MessageService.SubscribeMessageEvents`  (lines 742–766)

```
def SubscribeMessageEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for subscribing to live message events. Unlike the simple request/response calls, this sends one request and then receives a stream of event responses over time.

**Data flow**: It receives a subscription request, target server, and optional call settings. It serializes the request, opens a unary-to-stream gRPC call, and yields or returns decoded SubscribeMessageEventsResponse items as the server sends them.

**Call relations**: This shortcut delegates to gRPC's experimental unary-stream helper. It connects to the server's SubscribeMessageEvents streaming handler, which is expected to keep sending message-event updates.


### Provider Contract
The hand-written provider interface and shared message and attachment data shapes used by the iMessage extension.

### `extensions/imessage/ufo_ext_imessage/provider.py`

`data_model` · `cross-cutting`

This file is like a menu and order form for the iMessage side of the system. It does not send messages itself. Instead, it says, in one clear place, what an iMessage provider must be able to do: assign a phone line, read old messages, listen for new ones, send text, send attachments, download files, and classify errors.

The small frozen data classes describe the pieces of information that move across this boundary. A MessageAttachment records an attachment’s identifier, name, and size. An InboundMessage records who sent a message, which conversation it belongs to, its text, any attachments, and whether it was direct. A ProviderEvent wraps message stream progress, such as a sequence number, a message, or the current head position. These sequence values act like page numbers or mile markers, helping the system know where it left off.

The MessageProvider protocol is the important contract. A protocol is a promise: any real provider object can be used here if it offers these same methods and properties. That lets the iMessage surface code work with different provider implementations without caring how they talk to Apple Messages, a relay service, or a test fake. Without this file, the rest of the extension would not have a stable vocabulary for messages or a reliable list of provider abilities.

#### Function details

##### `MessageProvider.installation_id`  (lines 37–37)

```
def installation_id(self) -> str
```

**Purpose**: This property identifies the specific provider installation being used. The system can use it to distinguish one configured iMessage backend from another.

**Data flow**: The caller asks the provider for its installation identity. The provider reads whatever internal identifier it uses and returns it as text, without changing anything.

**Call relations**: This is part of the provider contract. Other code can rely on every MessageProvider having a stable installation_id, even though this file does not show a direct caller in the provided graph.


##### `MessageProvider.assign_line`  (lines 39–39)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This asks the provider to attach or reserve a phone number for use by this installation. The idempotency key is a safety label that lets repeated requests avoid doing the same assignment twice.

**Data flow**: A phone number and an idempotency key go in. The provider attempts the line assignment, using the key to recognize retries, and returns a text identifier or result for the assigned line.

**Call relations**: This method is part of the setup-facing provider contract. Code that needs to prepare an iMessage line can call it without knowing the provider’s internal assignment process.


##### `MessageProvider.catch_up`  (lines 41–41)

```
def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This asks the provider for past events after a known sequence point. It is how the system fills in anything it missed while it was offline or disconnected.

**Data flow**: The caller gives the last sequence number it has seen, or nothing if it has no saved position. The provider produces an asynchronous stream of ProviderEvent objects, each representing message data or progress, until it has caught up.

**Call relations**: ImessageSurface._catch_up calls this when the surface needs to replay older provider events. The provider hands events back one by one so the surface can process them in order.

*Call graph*: called by 1 (_catch_up).


##### `MessageProvider.subscribe`  (lines 43–43)

```
def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This opens a live stream of new provider events. It is used when the system wants to stay connected and react as new iMessages arrive.

**Data flow**: The caller passes an asyncio.Event, which is a small signal object used to say when the subscription is ready. The provider starts listening, sets or uses that ready signal as appropriate, and yields ProviderEvent objects over time.

**Call relations**: ImessageSurface._pump_live calls this during live message pumping. The provider supplies the ongoing event stream, and the surface consumes those events as they arrive.

*Call graph*: called by 1 (_pump_live).


##### `MessageProvider.send_text`  (lines 45–45)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This sends a plain text message into an existing conversation. The idempotency key protects against accidental duplicate sends if the request is retried.

**Data flow**: A conversation identifier, message text, and idempotency key go in. The provider sends or records the outbound text message and returns a provider-side message identifier as text.

**Call relations**: ImessageSurface._prove calls this when it needs to send a text message through the provider. The surface supplies the conversation and text, while the provider performs the actual delivery work.

*Call graph*: called by 1 (_prove).


##### `MessageProvider.send_attachment`  (lines 47–53)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This sends a file attachment into an existing conversation. It carries both the visible filename and the raw file bytes that should be delivered.

**Data flow**: The caller gives a conversation identifier, filename, file data as bytes, and an idempotency key. The provider sends the attachment, using the key to avoid duplicate sends on retry, and returns a text identifier for the sent item.

**Call relations**: ImessageSurface._send_contact_card calls this when it needs to send a contact card or similar attachment. The surface prepares the file content, and the provider takes responsibility for delivering it.

*Call graph*: called by 1 (_send_contact_card).


##### `MessageProvider.download_attachment`  (lines 55–55)

```
def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This retrieves the contents of an attachment by its provider attachment identifier. It returns the file as chunks, which is useful for large files because the whole file does not need to be held in memory at once.

**Data flow**: An attachment identifier goes in. The provider finds the attachment and yields pieces of its bytes through an asynchronous generator until the download is complete.

**Call relations**: ImessageSurface._downloaded_files calls this when it needs to turn message attachment references into actual downloaded file data. The provider streams the bytes back to the surface.

*Call graph*: called by 1 (_downloaded_files).


##### `MessageProvider.invalidate`  (lines 57–57)

```
async def invalidate(self) -> None
```

**Purpose**: This tells the provider that its current state or connection should no longer be trusted. It gives an implementation a chance to clean up, reset, or mark itself unusable.

**Data flow**: No data is required from the caller. The provider updates its own internal state, such as closing sessions or clearing cached credentials, and returns when that work is done.

**Call relations**: This is part of the common provider contract for teardown or recovery. Although no direct caller is shown in the provided graph, any orchestration code can use it to force the provider out of a bad state.


##### `MessageProvider.invalid_cursor`  (lines 59–59)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This checks whether an error means the saved stream position is no longer valid. A cursor is a remembered place in an event stream, like a bookmark.

**Data flow**: An exception object goes in. The provider inspects it using provider-specific rules and returns true or false to say whether the stored position should be treated as bad.

**Call relations**: This method lets higher-level code react correctly to provider-specific failures without knowing each provider’s error details. No direct caller is shown in the provided graph, but it belongs to the recovery path around event streaming.


##### `MessageProvider.external_error`  (lines 61–61)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This checks whether an error came from the outside provider rather than from local code. That distinction helps the surface decide whether to report, retry, or classify the failure differently.

**Data flow**: An exception object goes in. The provider examines it and returns a yes-or-no answer about whether it represents an external provider failure.

**Call relations**: ImessageSurface._consume_connected, ImessageSurface._downloaded_files, and ImessageSurface._send_contact_card call this when something goes wrong during streaming, downloading, or sending. The provider supplies the provider-specific judgment, and the surface uses that judgment to choose its next step.

*Call graph*: called by 3 (_consume_connected, _downloaded_files, _send_contact_card).


##### `MessageProvider.error_code`  (lines 63–63)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This turns a provider-specific error into a short code the rest of the system can understand or report. It gives errors a stable label instead of exposing raw exception details everywhere.

**Data flow**: An exception object goes in. The provider translates it into a text error code and returns that code, without necessarily changing any state.

**Call relations**: ImessageSurface._send_contact_card calls this after a send failure so it can report or respond with a meaningful provider error label. The surface does not need to know how each provider names its failures.

*Call graph*: called by 1 (_send_contact_card).
