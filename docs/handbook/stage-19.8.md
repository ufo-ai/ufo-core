# iMessage provider contract and generated gRPC stubs  `stage-19.8`

This stage defines the boundary between the rest of the app and an iMessage-like messaging source. It is shared behind-the-scenes support: other code can ask for chats, messages, attachments, or past events without needing to know whether the data comes from a local database, a phone bridge, or a network service.

The provider.py file is the main contract. It describes what a message provider must be able to do, and it defines the simple data shapes for messages and attachments that the rest of the extension expects. Think of it as the agreed plug shape for any iMessage adapter.

The four generated gRPC files are the network wiring. gRPC is a system for calling functions on another process or machine as if they were local. The attachment, chat, message, and event service stubs give clients ready-made methods to call those remote services. They also give servers matching hooks where real implementation code can be plugged in. Together, the contract and generated stubs let the extension talk to messaging backends in a consistent way.

## Files in this stage

### Generated service wiring
Generated Python gRPC modules expose the attachment, chat, event, and message service APIs to clients and server implementations.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is like the phone book and switchboard for an attachment service. The service supports three network calls: looking up attachment metadata, uploading an attachment, and downloading an attachment as a stream of byte chunks. It does not contain the real storage logic. Instead, it defines the standard client and server shapes that the rest of the project must use so both sides speak the same protocol.

At import time, it first checks that the installed gRPC library is new enough for the generated code. gRPC is a remote procedure call system: it lets one program call a function in another program as if it were local, while quietly handling network messages underneath.

For clients, `AttachmentServiceStub` turns ordinary Python method calls into correctly named gRPC requests, using protobuf messages for conversion to and from bytes. For servers, `AttachmentServiceServicer` provides placeholder methods that deliberately fail until a real service subclass overrides them. The helper `add_AttachmentServiceServicer_to_server` connects a real servicer to a gRPC server, telling gRPC how to decode incoming requests and encode outgoing responses. The final `AttachmentService` class offers an experimental shortcut API for making one-off calls without first building a stub object.

Without this file, clients and servers could still have attachment data, but they would not have the shared network wiring needed to exchange it safely and consistently.

#### Function details

##### `AttachmentServiceStub.__init__`  (lines 37–57)

```
def __init__(self, channel)
```

**Purpose**: This sets up a client-side object that knows how to call the attachment service over an existing gRPC channel. Someone uses it when they want to ask a remote server for attachment info, upload an attachment, or download attachment bytes.

**Data flow**: It receives a gRPC channel, which is the already-open communication path to a server. It attaches three callable fields to the stub, each tied to a specific remote method name and each given the right protobuf message converters. After this runs, the stub can turn Python request objects into network bytes and turn response bytes back into Python response objects.

**Call relations**: Client code creates this stub before making attachment calls. The stub does not do the attachment work itself; it prepares the three remote-call entry points that gRPC will use when the client later invokes them.


##### `AttachmentServiceServicer.GetAttachmentInfo`  (lines 69–74)

```
def GetAttachmentInfo(self, request, context)
```

**Purpose**: This is the server-side placeholder for looking up attachment metadata without reading the attachment file bytes. A real server implementation is expected to override it.

**Data flow**: It receives a request and a gRPC context, which is the object used to report status back to the caller. Because this generated base method has no real implementation, it marks the call as unimplemented, adds a short explanation, and raises an error instead of returning attachment information.

**Call relations**: When a server subclass provides the actual behavior, gRPC calls that override through the registration created by `add_AttachmentServiceServicer_to_server`. If no override is provided, this placeholder is what runs, making the missing implementation obvious to the client.


##### `AttachmentServiceServicer.UploadAttachment`  (lines 76–87)

```
def UploadAttachment(self, request, context)
```

**Purpose**: This is the server-side placeholder for uploading one attachment, including an optional related sidecar file. A real implementation should store the upload atomically, meaning either everything becomes visible or nothing does.

**Data flow**: It receives an upload request and the gRPC context. The generated placeholder does not inspect or save the payload. It sets the response status to unimplemented, records that detail, and raises an error.

**Call relations**: The registration helper can connect this method name to a gRPC server, but useful behavior only appears when application code subclasses `AttachmentServiceServicer` and replaces this method. Until then, upload calls reach this placeholder and fail clearly.


##### `AttachmentServiceServicer.DownloadAttachment`  (lines 89–95)

```
def DownloadAttachment(self, request, context)
```

**Purpose**: This is the server-side placeholder for downloading attachment bytes as a stream. Streaming means the server can send the file in pieces instead of one huge response.

**Data flow**: It receives a download request and the gRPC context. Since this base version has no real download logic, it marks the call as unimplemented, adds an explanatory detail, and raises an error rather than yielding byte frames.

**Call relations**: A real server subclass overrides this method to produce the stream of download responses. The registration helper tells gRPC to treat this method as a server-streaming call, so the client can receive multiple response messages from one request.


##### `add_AttachmentServiceServicer_to_server`  (lines 98–119)

```
def add_AttachmentServiceServicer_to_server(servicer, server)
```

**Purpose**: This connects a concrete attachment service implementation to a gRPC server. It tells the server which Python methods should answer each network method name, and how to translate messages to and from bytes.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table mapping the three attachment RPC names to handlers, pairing each handler with the correct request decoder and response encoder. It then adds that table to the server, changing the server so incoming attachment-service calls can be routed to the servicer.

**Call relations**: Server startup code calls this after creating the real servicer. Inside, it asks gRPC to build unary request/response handlers for metadata lookup and upload, a streaming-response handler for download, and a generic service handler that the server can register.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `AttachmentService.GetAttachmentInfo`  (lines 133–157)

```
def GetAttachmentInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This experimental static helper makes a one-off remote call to get attachment metadata. It is an alternative to first creating an `AttachmentServiceStub`.

**Data flow**: It receives a request, a target server address, and optional call settings such as credentials, timeout, compression, and metadata. It passes those details to gRPC along with the exact service path and protobuf converters. The result is the response from the remote metadata lookup call, decoded into the expected response message type.

**Call relations**: Client code may call this directly when it wants a shortcut API. It hands the actual network work to gRPC's experimental unary-unary call helper, meaning one request goes out and one response comes back.


##### `AttachmentService.UploadAttachment`  (lines 160–184)

```
def UploadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This experimental static helper makes a one-off remote upload call for an attachment. It is useful when client code wants to send an upload request without creating a long-lived stub object.

**Data flow**: It receives an upload request, a target server, and optional settings such as credentials, timeout, compression, and metadata. It supplies gRPC with the upload method path and the protobuf functions needed to serialize the request and parse the response. It returns the decoded upload response from the server.

**Call relations**: Client code can use this as a direct route into the remote upload method. It delegates to gRPC's experimental unary-unary helper, because upload is modeled here as one request followed by one response.


##### `AttachmentService.DownloadAttachment`  (lines 187–211)

```
def DownloadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This experimental static helper starts a one-off remote download call for an attachment. It is designed for downloads where the server sends multiple response messages, usually chunks of the file.

**Data flow**: It receives a download request, a target server, and optional call settings. It gives gRPC the download method path plus the protobuf serializer and deserializer. The result is a stream-like response from gRPC that the caller can read piece by piece.

**Call relations**: Client code may call this directly instead of using `AttachmentServiceStub`. It hands the work to gRPC's experimental unary-stream helper, because one request can produce many download response frames.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is machine-generated from a Protocol Buffers service definition. Protocol Buffers are a way to define messages and services in a language-neutral format, and gRPC is the remote-call system that sends those messages over the network. In plain terms, this file is the phonebook and switchboard for chat operations such as creating a chat, marking it read, setting a background, checking chat counts, and subscribing to chat events.

There are three main pieces. ChatServiceStub is for clients: given a network channel, it creates callable Python attributes like CreateChat and GetChat. Each one knows the exact remote method name and how to turn request objects into bytes and response bytes back into Python objects. ChatServiceServicer is the server-side base class. Its methods are only placeholders; real server code must subclass it and implement the actual behavior. add_ChatServiceServicer_to_server connects that real implementation to a gRPC server, like plugging labeled wires into the right sockets. Finally, ChatService offers experimental one-shot static helpers for making calls without first building a stub object.

The file also checks that the installed grpc package is new enough for this generated code. Without this file, Python code would not have the standard client and server bindings for this chat API.

#### Function details

##### `ChatServiceStub.__init__`  (lines 37–92)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object whose attributes are ready-to-use remote calls for the chat service. A caller uses this when it already has a gRPC channel, which is the network connection to the server.

**Data flow**: It receives a channel. It attaches methods such as CreateChat, MarkChatRead, and SubscribeChatEvents to the stub, each with the correct remote path plus the rules for turning request objects into bytes and response bytes back into objects. It returns no separate value; the stub object itself is changed so callers can use those attributes.

**Call relations**: Client code creates this stub before making chat-service calls. After that, calling one of the stub attributes sends work through the channel to the matching server method.


##### `ChatServiceServicer.CreateChat`  (lines 103–108)

```
def CreateChat(self, request, context)
```

**Purpose**: Defines the server-side shape of the CreateChat operation, but does not implement it. Real server code is expected to override this method to create a chat.

**Data flow**: It receives a request and a gRPC context, which carries response status information. In this base version, it marks the call as unimplemented, adds the message 'Method not implemented!', and raises an error instead of returning a chat response.

**Call relations**: When add_ChatServiceServicer_to_server registers a servicer, gRPC will route incoming CreateChat requests to the servicer's CreateChat method. If the application has not overridden this placeholder, the client receives an unimplemented error.


##### `ChatServiceServicer.MarkChatRead`  (lines 110–114)

```
def MarkChatRead(self, request, context)
```

**Purpose**: Defines the server-side shape of marking a chat as read, but leaves the real work to an override. It exists so generated server registration knows this method is part of the service.

**Data flow**: It receives a mark-read request and a gRPC context. This placeholder writes an unimplemented status into the context and raises an error, so no chat is changed and no successful empty response is produced.

**Call relations**: Registered server code is expected to replace this method with real behavior. If not replaced, incoming MarkChatRead calls routed by gRPC end here and fail as unimplemented.


##### `ChatServiceServicer.SetBackground`  (lines 116–122)

```
def SetBackground(self, request, context)
```

**Purpose**: Defines the server-side shape of setting a chat background, but does not actually save any background. The comment notes that when used through HTTP JSON, raw bytes are represented as base64 text.

**Data flow**: It receives a background-setting request and a gRPC context. The base method sets an unimplemented status and raises an error, so the supplied background data is not used.

**Call relations**: gRPC can route SetBackground requests to this method after the servicer is registered. A real service subclass must override it; otherwise callers get an unimplemented response.


##### `ChatServiceServicer.RemoveBackground`  (lines 124–128)

```
def RemoveBackground(self, request, context)
```

**Purpose**: Defines the server-side shape of removing a chat background, but provides no real removal behavior. It is a placeholder for application code.

**Data flow**: It receives a remove-background request and context. It changes the context to say the method is unimplemented, then raises an error, leaving any stored background untouched.

**Call relations**: After registration, the gRPC server calls this method for RemoveBackground requests unless a subclass overrides it. The generated registration code supplies the routing, while real business logic must come from elsewhere.


##### `ChatServiceServicer.ShareContactInfo`  (lines 130–137)

```
def ShareContactInfo(self, request, context)
```

**Purpose**: Defines the server-side shape of sending the local user's contact card into a target chat. The base method does not perform the sharing; it only signals that the method still needs an implementation.

**Data flow**: It receives a share-contact request and context. Instead of sending any contact information, it marks the RPC as unimplemented and raises an error.

**Call relations**: gRPC routes ShareContactInfo calls to the registered servicer method. A project-specific subclass is responsible for replacing this placeholder with the code that talks to the underlying chat system.


##### `ChatServiceServicer.SetTyping`  (lines 139–144)

```
def SetTyping(self, request, context)
```

**Purpose**: Defines the server-side shape of sending a temporary typing indicator. The base method does not send the indicator.

**Data flow**: It receives a typing-status request and context. It records an unimplemented status in the context and raises an error, so no typing state is sent or saved.

**Call relations**: Incoming SetTyping calls reach this method through the gRPC server registration. In a working server, a subclass overrides it with the actual transient typing-indicator behavior.


##### `ChatServiceServicer.GetChat`  (lines 146–152)

```
def GetChat(self, request, context)
```

**Purpose**: Defines the server-side shape of reading a chat by request details such as a chat identifier. The base method does not look anything up.

**Data flow**: It receives a get-chat request and context. It marks the request as unimplemented and raises an error, producing no chat response.

**Call relations**: Once the servicer is registered, gRPC uses this method name for GetChat requests. Real server code must override it so clients can actually retrieve chat data.


##### `ChatServiceServicer.GetChatCount`  (lines 154–158)

```
def GetChatCount(self, request, context)
```

**Purpose**: Defines the server-side shape of asking how many chats match some request. This generated base method is only a placeholder.

**Data flow**: It receives a count request and context. It sets the context to an unimplemented error and raises, so no count is calculated or returned.

**Call relations**: The registration function maps GetChatCount traffic to this method. A subclass supplies the real counting logic if the service is meant to work.


##### `ChatServiceServicer.HasBackground`  (lines 160–164)

```
def HasBackground(self, request, context)
```

**Purpose**: Defines the server-side shape of checking whether a chat has a background. The base version does not check anything.

**Data flow**: It receives a has-background request and context. It writes an unimplemented status and raises an error, returning no true-or-false style response.

**Call relations**: After server registration, incoming HasBackground calls are directed here unless application code overrides the method. The generated layer provides the route; the real server supplies the answer.


##### `ChatServiceServicer.SubscribeChatEvents`  (lines 166–170)

```
def SubscribeChatEvents(self, request, context)
```

**Purpose**: Defines the server-side shape of subscribing to a stream of chat events. The base method does not produce any event stream.

**Data flow**: It receives a subscription request and context. It marks the method as unimplemented and raises an error, so no sequence of chat event responses is sent.

**Call relations**: The registration function treats this as a server-streaming RPC, meaning one request can lead to many responses. A real subclass must override it to yield or return chat event updates over time.


##### `add_ChatServiceServicer_to_server`  (lines 173–229)

```
def add_ChatServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a concrete chat-service implementation to a gRPC server. This is the server setup step that tells gRPC which Python method should answer each named remote call.

**Data flow**: It receives a servicer object and a server. It builds a dictionary of method handlers, each one pairing a service method with the correct request parser and response writer. It then creates a generic gRPC handler and adds both generic and registered method handlers to the server.

**Call relations**: Server startup code calls this after creating a servicer subclass with real behavior. Inside, it uses gRPC helper functions such as grpc.unary_unary_rpc_method_handler for normal request-response calls, grpc.unary_stream_rpc_method_handler for event streaming, and grpc.method_handlers_generic_handler to bundle the methods under the ChatService name.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `ChatService.CreateChat`  (lines 242–266)

```
def CreateChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to create a chat. It is an alternative to creating a ChatServiceStub first.

**Data flow**: It receives a CreateChat request, a target server address, and optional connection settings such as credentials, timeout, metadata, and compression. It serializes the request, sends it to the CreateChat remote path, parses the response bytes as a CreateChatResponse, and returns that result from gRPC.

**Call relations**: Client code may call this static helper directly. It hands the actual network work to grpc.experimental.unary_unary, which performs a single request and expects a single response.


##### `ChatService.MarkChatRead`  (lines 269–293)

```
def MarkChatRead(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to mark a chat as read. It is useful for clients that want a direct call without building a stub object.

**Data flow**: It receives a MarkChatRead request, target server information, and optional call settings. It turns the request into bytes, sends it to the MarkChatRead remote path, and parses the reply as an empty protobuf message, meaning success carries no extra data.

**Call relations**: Client code calls this helper when it wants to perform MarkChatRead directly. The helper delegates to grpc.experimental.unary_unary for the actual request-response exchange.


##### `ChatService.SetBackground`  (lines 296–320)

```
def SetBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to set a chat background. The successful response is empty because the operation is treated as a write with no returned data.

**Data flow**: It receives a SetBackground request, a target, and optional connection and call settings. It serializes the request, sends it to the SetBackground remote path, parses an empty response, and returns the gRPC call result.

**Call relations**: Client code can use this instead of a stub method. It passes the request and serialization rules to grpc.experimental.unary_unary, which talks to the server.


##### `ChatService.RemoveBackground`  (lines 323–347)

```
def RemoveBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to remove a chat background. It represents a write action where success is shown by an empty response.

**Data flow**: It receives a RemoveBackground request, target server details, and optional settings. It serializes the request, sends it to the RemoveBackground remote path, deserializes the empty response, and returns the result.

**Call relations**: This static helper is used from client-side code that wants a direct call. The actual network exchange is performed by grpc.experimental.unary_unary.


##### `ChatService.ShareContactInfo`  (lines 350–374)

```
def ShareContactInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to share the local user's contact information into a chat. It expects no response body beyond an empty success message.

**Data flow**: It receives a ShareContactInfo request, a target, and optional call configuration. It converts the request to bytes, sends it to the ShareContactInfo remote path, reads the empty response, and returns the gRPC result.

**Call relations**: Client code may call this helper directly. It delegates the single request and single response pattern to grpc.experimental.unary_unary.


##### `ChatService.SetTyping`  (lines 377–401)

```
def SetTyping(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to send a temporary typing indicator. It is for signaling activity rather than saving lasting chat data.

**Data flow**: It receives a SetTyping request, target server information, and optional settings. It serializes the request, sends it to the SetTyping remote path, parses the empty response, and returns the result.

**Call relations**: This helper is available to client code that does not want to create a stub first. It uses grpc.experimental.unary_unary to do the actual remote call.


##### `ChatService.GetChat`  (lines 404–428)

```
def GetChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to fetch chat information. It returns a structured GetChatResponse from the server.

**Data flow**: It receives a GetChat request, target address, and optional call settings. It serializes the request, sends it to the GetChat remote path, parses the response bytes as a GetChatResponse, and returns that result.

**Call relations**: Client code can call this static method for a direct read. It hands the communication details to grpc.experimental.unary_unary, which sends one request and waits for one response.


##### `ChatService.GetChatCount`  (lines 431–455)

```
def GetChatCount(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to ask for a count of chats. It returns a GetChatCountResponse with the server's answer.

**Data flow**: It receives a GetChatCount request, a target server, and optional settings. It serializes the request, sends it to the GetChatCount remote path, deserializes the response as a GetChatCountResponse, and returns it.

**Call relations**: Client code may use this as a direct helper. The method relies on grpc.experimental.unary_unary for the actual network request and response.


##### `ChatService.HasBackground`  (lines 458–482)

```
def HasBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-shot remote call to check whether a chat has a background. It returns a HasBackgroundResponse containing the server's answer.

**Data flow**: It receives a HasBackground request, target information, and optional settings. It serializes the request, sends it to the HasBackground remote path, parses the response bytes as a HasBackgroundResponse, and returns it.

**Call relations**: This is a client-side convenience helper. It delegates to grpc.experimental.unary_unary because the call has one request and one response.


##### `ChatService.SubscribeChatEvents`  (lines 485–509)

```
def SubscribeChatEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental remote call that subscribes to chat events and receives a stream of responses. Unlike the simple calls, one request can lead to many event messages over time.

**Data flow**: It receives a SubscribeChatEvents request, a target server, and optional settings. It serializes the request, opens the SubscribeChatEvents remote call, and deserializes each incoming response as a SubscribeChatEventsResponse as the stream produces them.

**Call relations**: Client code uses this helper when it wants live chat updates without first making a stub. It calls grpc.experimental.unary_stream, the gRPC pattern for sending one request and receiving a sequence of responses.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2_grpc.py`

`generated` · `request handling`

This file is machine-generated plumbing for gRPC, which is a system that lets one program call a function in another program over the network as if it were local. The service here is EventService, and its main job is to let a client ask for all durable events after a given sequence number, then receive a stream of responses until the catch-up is complete. In everyday terms, it is like asking a logbook clerk, “Tell me everything that happened after entry 500,” and the clerk reads entries back one by one until there are no more.

The file does not contain the real business logic for finding or producing events. Instead, it defines the standard shapes that client and server code use to talk to each other. EventServiceStub is used by clients: it turns a Python request object into bytes, sends it to the remote service, and turns response bytes back into Python objects. EventServiceServicer is the base class for servers: real server code is expected to subclass it and replace the default “not implemented” method. add_EventServiceServicer_to_server wires that server implementation into a gRPC server. The EventService class offers an experimental shortcut API for making the same call without manually building a stub.

The version check at the top matters because generated gRPC code must match the installed grpcio library closely enough to work safely.

#### Function details

##### `EventServiceStub.__init__`  (lines 45–55)

```
def __init__(self, channel)
```

**Purpose**: This sets up the client-side object used to call the remote CatchUpEvents service. A caller gives it a gRPC channel, which is the network connection to the server, and it prepares the specific remote call for later use.

**Data flow**: It receives a channel object. It asks that channel to create a unary-stream call, meaning the client sends one request and receives many responses. It attaches the correct path for the remote method, plus the functions that convert request objects into bytes and response bytes back into response objects. After this, the stub has a CatchUpEvents attribute ready for client code to call.

**Call relations**: Client code creates this stub when it wants to talk to an EventService server. Later, when the client calls stub.CatchUpEvents, gRPC uses the serializer and deserializer configured here to carry the request and streamed responses across the network.


##### `EventServiceServicer.CatchUpEvents`  (lines 75–79)

```
def CatchUpEvents(self, request, context)
```

**Purpose**: This is the server-side placeholder for the CatchUpEvents operation. It exists so real server implementations have a clear method to override with the actual event replay logic.

**Data flow**: It receives a request and a gRPC context, which carries information about the current network call. In this generated base version, it does not read events or return useful responses. Instead, it marks the call as unimplemented, adds the message “Method not implemented!”, and raises a NotImplementedError.

**Call relations**: A real service class is expected to inherit from EventServiceServicer and provide its own CatchUpEvents method. If the generated base method is used directly, gRPC will report to the client that the server has not implemented this operation.


##### `add_EventServiceServicer_to_server`  (lines 82–93)

```
def add_EventServiceServicer_to_server(servicer, server)
```

**Purpose**: This registers a concrete EventService server implementation with a gRPC server. Without this step, incoming network requests for CatchUpEvents would not know which Python method should answer them.

**Data flow**: It receives a servicer object, which should contain the actual CatchUpEvents method, and a gRPC server object. It builds a method table that maps the network name “CatchUpEvents” to the servicer’s Python method, along with the request parser and response writer. It then adds that mapping to the server so future calls can be routed correctly.

**Call relations**: Server startup code calls this after creating its EventService implementation. Inside, it asks gRPC to build a unary-stream method handler for CatchUpEvents and then wraps those handlers under the full service name. Once registered, the gRPC server can dispatch client CatchUpEvents requests to the servicer method.

*Call graph*: 2 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler).


##### `EventService.CatchUpEvents`  (lines 115–139)

```
def CatchUpEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This is an experimental convenience way to make the CatchUpEvents remote call directly. It lets code send one catch-up request to a target server and receive a stream of catch-up responses without explicitly creating an EventServiceStub.

**Data flow**: It receives the request object, the target server address, and optional connection settings such as credentials, timeout, compression, and metadata. It passes these to gRPC’s experimental unary-stream helper, along with the method path and the request/response conversion functions. The result is a streamed remote call that yields CatchUpEventsResponse objects from the server.

**Call relations**: Caller code may use this as a shortcut client API. It hands the work off to gRPC’s experimental unary-stream machinery, which performs the same basic network operation that the stub’s CatchUpEvents setup supports.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is machine-written from a Protocol Buffers service definition. Protocol Buffers describe structured messages, and gRPC is the network system that sends those messages between a client and a server. Without this file, Python code would know the shapes of the request and response messages, but it would not know how to call MessageService methods over the wire or how to attach a server implementation to gRPC.

Think of it like a phone directory plus socket adapters. The client stub lists the exact remote method names, such as sending a text message, editing a message, getting recent messages, or subscribing to message events. It also says how to turn each request object into bytes before sending it, and how to turn the returned bytes back into a response object.

On the server side, MessageServiceServicer is a base class with placeholder methods. Real server code is expected to subclass it and replace those placeholders. If a method is not replaced, it returns “not implemented.” The add_MessageServiceServicer_to_server function registers all these method names with a gRPC server so incoming network calls reach the right Python method. The final MessageService class offers an experimental shortcut style for making one-off calls without manually creating a stub.

#### Function details

##### `MessageServiceStub.__init__`  (lines 46–126)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object with one callable attribute for each remote MessageService operation. Client code uses this stub when it wants to send, edit, read, or subscribe to messages through a gRPC channel.

**Data flow**: It receives a gRPC channel, which is the open communication path to a server. It attaches methods to the stub that know the remote method name, how to serialize the request into bytes, and how to deserialize the response back into a Python message object. The result is the same stub object, now populated with ready-to-use remote calls.

**Call relations**: Client code creates this stub after opening a channel to the service. Later, when code calls attributes such as SendTextMessage or ListChatMessages, gRPC uses the wiring created here to contact the server.


##### `MessageServiceServicer.SendTextMessage`  (lines 146–159)

```
def SendTextMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending a text message. In this generated base class it is only a placeholder; real service code must override it.

**Data flow**: It receives a text-message request and a gRPC context object, which carries response status information. Because no real behavior is implemented here, it marks the call as unimplemented, adds an explanatory detail, and raises an error instead of returning a message response.

**Call relations**: The gRPC server calls this method when a client invokes SendTextMessage, but only if a concrete servicer has not overridden it. It is registered for routing by add_MessageServiceServicer_to_server.


##### `MessageServiceServicer.SendAttachmentMessage`  (lines 161–165)

```
def SendAttachmentMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending a message with an attachment. This generated version is a placeholder that must be replaced by real server logic.

**Data flow**: It receives an attachment-message request and the call context. It changes the context status to “unimplemented,” records that the method is not implemented, and raises an error rather than producing a message response.

**Call relations**: When registered on a server, gRPC would call this for SendAttachmentMessage requests unless a subclass supplies the actual attachment-sending behavior.


##### `MessageServiceServicer.SendMultipartMessage`  (lines 167–171)

```
def SendMultipartMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending a message made of multiple parts, such as text plus media. The base implementation intentionally does nothing useful.

**Data flow**: It receives a multipart-message request and a context object. It sets the outgoing gRPC status to unimplemented and raises NotImplementedError, so no successful response is created.

**Call relations**: This method is the target gRPC routes to for SendMultipartMessage after registration. Production code is expected to override it before the service can actually send multipart messages.


##### `MessageServiceServicer.SendCustomizedMiniAppMessage`  (lines 173–178)

```
def SendCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending a customized iMessage mini-app card. The generated base class only reports that no implementation exists.

**Data flow**: It receives a mini-app message request and the gRPC context. It writes an unimplemented status into the context and raises an error instead of returning the fresh message snapshot promised by the API.

**Call relations**: gRPC calls this method for SendCustomizedMiniAppMessage requests once the servicer is registered. A real servicer subclass must take over to create and send the mini-app card.


##### `MessageServiceServicer.UpdateCustomizedMiniAppMessage`  (lines 180–185)

```
def UpdateCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for updating an existing customized mini-app message. Here it is only a generated placeholder.

**Data flow**: It receives an update request and a context. It marks the call as unimplemented, stores a short failure message in the context, and raises NotImplementedError rather than returning an updated message response.

**Call relations**: The registration function connects this hook to the UpdateCustomizedMiniAppMessage RPC route. Real update behavior belongs in an overriding subclass.


##### `MessageServiceServicer.EditMessage`  (lines 187–191)

```
def EditMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for editing an already-sent message. This base method exists so the server has a known method name to override.

**Data flow**: It takes an edit request and call context. Since the generated class has no editing logic, it sets the gRPC status to unimplemented and raises an error with no successful response.

**Call relations**: Incoming EditMessage calls are routed here by gRPC after server registration unless application code overrides the method with real edit behavior.


##### `MessageServiceServicer.UnsendMessage`  (lines 193–198)

```
def UnsendMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for retracting a message. In this generated class it is not implemented and must be supplied by the actual service.

**Data flow**: It receives an unsend request and the gRPC context. It records an unimplemented status and raises NotImplementedError, so it does not return the expected empty success response.

**Call relations**: The server registration maps the UnsendMessage RPC to this method. A real servicer overrides it to perform the retraction and then return an empty response on success.


##### `MessageServiceServicer.SetReaction`  (lines 200–204)

```
def SetReaction(self, request, context)
```

**Purpose**: Defines the server-side hook for adding, changing, or clearing a reaction on a message. The generated version only signals that real logic is missing.

**Data flow**: It receives a reaction request and context. It sets the call status to unimplemented and raises an error instead of returning the affected message snapshot.

**Call relations**: After registration, gRPC can route SetReaction calls to this method. Application code must override it so reactions are actually applied.


##### `MessageServiceServicer.PlaceSticker`  (lines 206–210)

```
def PlaceSticker(self, request, context)
```

**Purpose**: Defines the server-side hook for placing a sticker on a message. This base implementation is a placeholder.

**Data flow**: It takes a sticker placement request and context. It marks the call as unimplemented and raises NotImplementedError, producing no message response.

**Call relations**: The gRPC routing table created by add_MessageServiceServicer_to_server points PlaceSticker calls here unless a real servicer subclass replaces the method.


##### `MessageServiceServicer.NotifySilencedMessage`  (lines 212–217)

```
def NotifySilencedMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for Apple's “Notify Anyway” action on a silenced message. The generated method only reports that it has not been implemented.

**Data flow**: It receives a notify request and the call context. It sets the status to unimplemented, adds a detail string, and raises an error rather than returning the expected empty success result.

**Call relations**: gRPC calls this hook for NotifySilencedMessage requests once registered. A concrete service must override it to trigger the actual notification behavior.


##### `MessageServiceServicer.GetMessage`  (lines 219–224)

```
def GetMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for reading one message. This generated method is a placeholder, not the real lookup.

**Data flow**: It receives a get-message request and context. It changes the context to an unimplemented failure and raises an error instead of returning message data.

**Call relations**: The registration function connects the GetMessage RPC name to this hook. Real server code overrides it to fetch and return the requested message.


##### `MessageServiceServicer.ListRecentMessages`  (lines 226–233)

```
def ListRecentMessages(self, request, context)
```

**Purpose**: Defines the server-side hook for listing recent messages, optionally filtered by fields such as sender, read state, or time range. The base method has no listing logic.

**Data flow**: It receives a recent-messages request and context. It marks the RPC as unimplemented and raises NotImplementedError, so no list response is returned.

**Call relations**: Incoming ListRecentMessages calls are routed here by gRPC after registration. A real implementation must override it to query and return the message list.


##### `MessageServiceServicer.ListChatMessages`  (lines 235–240)

```
def ListChatMessages(self, request, context)
```

**Purpose**: Defines the server-side hook for listing messages in a specific chat. This generated method exists only as an override point.

**Data flow**: It receives a chat-message listing request and context. It sets the call status to unimplemented and raises an error instead of returning the chat's messages.

**Call relations**: The gRPC server uses this hook for ListChatMessages requests if no subclass has provided the actual chat lookup behavior.


##### `MessageServiceServicer.GetEmbeddedMedia`  (lines 242–250)

```
def GetEmbeddedMedia(self, request, context)
```

**Purpose**: Defines the server-side hook for retrieving media embedded in a message. The base version does not return media bytes; it only reports missing implementation.

**Data flow**: It receives a media request and the call context. It marks the call as unimplemented, adds a detail message, and raises NotImplementedError rather than returning media data.

**Call relations**: Registration maps GetEmbeddedMedia network calls to this method. A real server implementation must override it, especially because media bytes are served outside normal JSON-style HTTP mapping.


##### `MessageServiceServicer.SubscribeMessageEvents`  (lines 252–258)

```
def SubscribeMessageEvents(self, request, context)
```

**Purpose**: Defines the server-side hook for a live stream of durable message events. The generated placeholder does not stream anything.

**Data flow**: It receives a subscription request and context. Instead of yielding event responses over time, it marks the call as unimplemented and raises an error.

**Call relations**: This method is registered as a server-streaming RPC, meaning one request can produce many responses. Real service code overrides it so clients can watch message changes live.


##### `add_MessageServiceServicer_to_server`  (lines 261–342)

```
def add_MessageServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a MessageService server implementation to a gRPC server. Without this registration step, incoming network calls would not know which Python methods to run.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table from each public RPC name to the matching servicer method, along with the correct request parser and response serializer. It then adds that table to the server, changing the server so it can accept MessageService calls.

**Call relations**: Server startup code calls this after creating a concrete servicer. Inside, it asks gRPC to create unary-unary handlers for ordinary request-and-response methods, a unary-stream handler for event subscriptions, and a generic service handler that the server installs for routing.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `MessageService.SendTextMessage`  (lines 364–388)

```
def SendTextMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to send a text message without first building a stub object. It is a convenience wrapper around gRPC's direct call helper.

**Data flow**: It receives a text-message request, a target server address, and optional connection settings such as credentials, timeout, metadata, and compression. It serializes the request, sends it to the SendTextMessage RPC path, deserializes the returned bytes into a MessageResponse, and returns that response.

**Call relations**: Client code can call this static method directly. It hands the actual network work to grpc.experimental.unary_unary, which performs a single request and receives a single response.


##### `MessageService.SendAttachmentMessage`  (lines 391–415)

```
def SendAttachmentMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to send an attachment message. It avoids the need to manually create a MessageServiceStub.

**Data flow**: It takes an attachment-message request, the target server, and optional call settings. It turns the request into bytes, sends it to the SendAttachmentMessage RPC path, converts the reply into a MessageResponse, and returns it.

**Call relations**: Client code uses this as a shortcut. The method delegates the actual remote call to grpc.experimental.unary_unary.


##### `MessageService.SendMultipartMessage`  (lines 418–442)

```
def SendMultipartMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to send a multipart message. It packages the request for the correct remote method and returns the server's message snapshot.

**Data flow**: It receives a multipart request, destination target, and optional gRPC settings. It serializes the request, sends it over gRPC to the SendMultipartMessage path, deserializes the response as a MessageResponse, and returns it to the caller.

**Call relations**: This static helper is used by clients that prefer direct calls. It relies on grpc.experimental.unary_unary to do the network exchange.


##### `MessageService.SendCustomizedMiniAppMessage`  (lines 445–469)

```
def SendCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to send a customized mini-app message. It is a convenience entry point for this specific RPC.

**Data flow**: It receives the mini-app send request, server target, and optional call options. It serializes the request, calls the SendCustomizedMiniAppMessage remote path, deserializes the returned bytes into a MessageResponse, and returns that response.

**Call relations**: Client code calls this shortcut when it does not want to create a stub. The gRPC experimental unary-unary helper performs the actual single request and single response.


##### `MessageService.UpdateCustomizedMiniAppMessage`  (lines 472–496)

```
def UpdateCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to update a customized mini-app message. It wraps the gRPC details for that specific remote method.

**Data flow**: It takes an update request, target server, and optional settings. It converts the request to bytes, sends it to the UpdateCustomizedMiniAppMessage RPC path, converts the reply into a MessageResponse, and returns it.

**Call relations**: This is a client-side shortcut. It passes all network work to grpc.experimental.unary_unary.


##### `MessageService.EditMessage`  (lines 499–523)

```
def EditMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to edit a message. It lets callers invoke the edit RPC in one function call.

**Data flow**: It receives an edit request, a target, and optional connection or call options. It serializes the request, sends it to the EditMessage path, reads the reply as a MessageResponse, and returns it.

**Call relations**: Client code may use this instead of a stub method. The function delegates to grpc.experimental.unary_unary for the remote request-response exchange.


##### `MessageService.UnsendMessage`  (lines 526–550)

```
def UnsendMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to retract an existing message. A successful call returns an empty response, meaning there is no extra data beyond success.

**Data flow**: It receives an unsend request, target server, and optional call settings. It serializes the request, sends it to the UnsendMessage RPC path, deserializes the reply as google.protobuf.Empty, and returns that empty result.

**Call relations**: Clients can use this static shortcut for unsend operations. It hands off to grpc.experimental.unary_unary for the actual network call.


##### `MessageService.SetReaction`  (lines 553–577)

```
def SetReaction(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to set a reaction on a message. It hides the low-level gRPC path and conversion details.

**Data flow**: It takes a reaction request, destination target, and optional call options. It serializes the request, sends it to the SetReaction remote method, converts the response bytes into a MessageResponse, and returns it.

**Call relations**: Client code calls this helper for a single reaction update. The underlying send-and-receive work is done by grpc.experimental.unary_unary.


##### `MessageService.PlaceSticker`  (lines 580–604)

```
def PlaceSticker(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to place a sticker on a message. It returns the server's updated message response.

**Data flow**: It receives a sticker placement request, target server, and optional gRPC settings. It turns the request into bytes, calls the PlaceSticker RPC path, turns the response bytes into a MessageResponse, and returns it.

**Call relations**: This static method is a client-side convenience. It delegates the network exchange to grpc.experimental.unary_unary.


##### `MessageService.NotifySilencedMessage`  (lines 607–631)

```
def NotifySilencedMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call for the “Notify Anyway” action on a silenced message. It reports success with an empty response.

**Data flow**: It receives a notify request, target server, and optional call settings. It serializes the request, sends it to the NotifySilencedMessage path, deserializes the response as an Empty message, and returns it.

**Call relations**: Clients may call this shortcut directly. It uses grpc.experimental.unary_unary for the single request and single response.


##### `MessageService.GetMessage`  (lines 634–658)

```
def GetMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to fetch one message. It is a shortcut for the GetMessage RPC.

**Data flow**: It receives a get-message request, a target server, and optional settings. It serializes the request, sends it to the GetMessage path, deserializes the result into a GetMessageResponse, and returns it.

**Call relations**: Client code can use this instead of creating a stub. The actual remote communication is performed by grpc.experimental.unary_unary.


##### `MessageService.ListRecentMessages`  (lines 661–685)

```
def ListRecentMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to list recent messages. It wraps the request serialization and response parsing for this listing RPC.

**Data flow**: It receives a recent-messages request, target server, and optional call settings. It sends the serialized request to the ListRecentMessages path and returns the deserialized ListRecentMessagesResponse.

**Call relations**: This is a client-side convenience method. It delegates the one-request, one-response network call to grpc.experimental.unary_unary.


##### `MessageService.ListChatMessages`  (lines 688–712)

```
def ListChatMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to list messages in a chat. It packages the chat listing request for the proper remote service path.

**Data flow**: It receives a chat-messages request, destination target, and optional gRPC options. It serializes the request, calls the ListChatMessages RPC path, deserializes the response into a ListChatMessagesResponse, and returns it.

**Call relations**: Client code may use this as a shortcut instead of a stub. The gRPC experimental unary-unary helper performs the actual network call.


##### `MessageService.GetEmbeddedMedia`  (lines 715–739)

```
def GetEmbeddedMedia(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to retrieve embedded media information or bytes through the gRPC service. It wraps the GetEmbeddedMedia RPC details.

**Data flow**: It receives a media request, target server, and optional call settings. It serializes the request, sends it to the GetEmbeddedMedia path, deserializes the reply into a GetEmbeddedMediaResponse, and returns it.

**Call relations**: Clients can call this static method directly. It relies on grpc.experimental.unary_unary for the single remote request and response.


##### `MessageService.SubscribeMessageEvents`  (lines 742–766)

```
def SubscribeMessageEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to subscribe to live message events. Unlike the other direct helpers, this returns a stream of responses over time.

**Data flow**: It receives a subscription request, target server, and optional connection settings. It serializes the request, opens the SubscribeMessageEvents RPC path, and deserializes each incoming event response as it arrives. The output is a stream-like result rather than just one response.

**Call relations**: Client code uses this when it wants live updates. It delegates to grpc.experimental.unary_stream, which sends one request and then receives many event messages from the server.


### Provider contract
The provider abstraction defines the common message-source interface and the message and attachment shapes consumed by the extension.

### `extensions/imessage/ufo_ext_imessage/provider.py`

`data_model` · `cross-cutting`

This file is like the plug shape for the iMessage extension. The rest of the system does not need to know whether messages come from Apple Messages, a bridge service, a mock test provider, or another backend. It only needs a provider that follows this contract.

The small frozen data classes describe the information that moves through the system: an attachment has an id, filename, and size; an inbound message has a sender, text, conversation, attachments, and whether it was direct; a provider event can carry either a message or information about the current message stream position. “Frozen” means these objects are meant to be read-only once created, which helps avoid accidental changes while events move between tasks.

The `MessageProvider` protocol is an interface: it says what methods a real provider must offer, but it does not implement them here. Those methods cover the full message lifecycle: assign a phone line, catch up on missed messages, subscribe to live messages, send text, send files, download attachments, and classify provider-specific errors. Without this file, the iMessage surface would have no stable vocabulary for asking a backend to send, receive, or explain messages.

#### Function details

##### `MessageProvider.installation_id`  (lines 37–37)

```
def installation_id(self) -> str
```

**Purpose**: This property identifies the provider installation being used. It gives the rest of the system a stable name or id for the connected messaging backend.

**Data flow**: A caller asks the provider for its installation id → the concrete provider reads whatever identifier it uses internally → the caller receives a string that can be logged, stored, or compared.

**Call relations**: This is part of the provider contract. No direct caller is shown in the provided call facts, but implementations must expose it so the extension can identify which provider instance it is talking to.


##### `MessageProvider.assign_line`  (lines 39–39)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This asks the provider to attach or reserve a phone number for use by the system. The idempotency key is a safety label that lets repeated requests avoid creating duplicate work.

**Data flow**: A phone number and idempotency key go in → the provider tries to assign that number in its own backend → it returns a string result, usually an id or confirmation for the assigned line.

**Call relations**: This is defined as part of the provider contract. No direct caller is shown in the provided call facts, but a concrete provider must implement it for setup flows that need to claim a messaging line.


##### `MessageProvider.catch_up`  (lines 41–41)

```
def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This returns past provider events after a known position in the message stream. It is used to recover messages that arrived while the system was offline or not listening.

**Data flow**: The caller supplies the last sequence number it has already seen, or nothing if it has no saved position → the provider walks forward through older stored events → it yields provider events one at a time.

**Call relations**: `ImessageSurface._catch_up` calls this when the surface needs to fill gaps before live processing begins. The provider hands back an asynchronous stream, meaning events can arrive over time without blocking the whole program.

*Call graph*: called by 1 (_catch_up).


##### `MessageProvider.subscribe`  (lines 43–43)

```
def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This opens a live stream of new provider events. It is the real-time listening path for incoming messages.

**Data flow**: The caller gives an `asyncio.Event`, which is a signal object used to say when the subscription is ready → the provider connects to its live event source and sets or uses that signal at the right moment → it yields new provider events as they happen.

**Call relations**: `ImessageSurface._pump_live` calls this during live message pumping. The surface relies on the provider to turn backend-specific notifications into the shared `ProviderEvent` shape.

*Call graph*: called by 1 (_pump_live).


##### `MessageProvider.send_text`  (lines 45–45)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This sends a plain text message into an existing conversation. The idempotency key helps prevent duplicate sends if the same request is retried.

**Data flow**: A conversation id, text body, and idempotency key go in → the provider sends the message through its backend → it returns a string, typically the sent message id or another confirmation.

**Call relations**: `ImessageSurface._prove` calls this when it needs to send a text message as part of its flow. The surface decides what should be sent, and the provider performs the actual delivery.

*Call graph*: called by 1 (_prove).


##### `MessageProvider.send_attachment`  (lines 47–53)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This sends a file attachment to a conversation. It carries both the file name and the raw bytes of the file.

**Data flow**: A conversation id, filename, file data, and idempotency key go in → the provider uploads or sends the attachment using its backend → it returns a string confirmation, such as an attachment or message id.

**Call relations**: `ImessageSurface._send_contact_card` calls this when it needs to send a contact card file. The surface prepares the file content, then hands it to the provider for delivery.

*Call graph*: called by 1 (_send_contact_card).


##### `MessageProvider.download_attachment`  (lines 55–55)

```
def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This retrieves the contents of an attachment from the provider. It streams the bytes instead of requiring the whole file to be loaded at once.

**Data flow**: An attachment id goes in → the provider finds that attachment in its backend and reads it in pieces → it yields chunks of bytes until the download is complete.

**Call relations**: `ImessageSurface._downloaded_files` calls this when it needs local access to message attachments. The provider hides where the file really lives and exposes a simple byte stream.

*Call graph*: called by 1 (_downloaded_files).


##### `MessageProvider.invalidate`  (lines 57–57)

```
async def invalidate(self) -> None
```

**Purpose**: This tells the provider that its current connection, session, or cached state should no longer be trusted. A concrete provider can use it to clean up or force a reconnect.

**Data flow**: The caller sends no extra data → the provider marks its current state as invalid or clears resources → the operation completes without returning a value.

**Call relations**: This is part of the provider contract. No direct caller is shown in the provided call facts, but it exists so orchestration code can ask a provider to reset itself when its state is no longer safe to use.


##### `MessageProvider.invalid_cursor`  (lines 59–59)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This checks whether an error means the saved stream position is no longer usable. A cursor is like a bookmark in the event stream; if it is invalid, catch-up may need to restart differently.

**Data flow**: An exception object goes in → the provider inspects it using backend-specific rules → it returns true if the error means the message-stream bookmark is bad, otherwise false.

**Call relations**: This is part of the provider contract. No direct caller is shown in the provided call facts, but it gives higher-level code a provider-neutral way to recognize a broken event position.


##### `MessageProvider.external_error`  (lines 61–61)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This checks whether an error came from the outside messaging provider rather than from the extension’s own logic. That lets the surface decide how to report or recover from backend failures.

**Data flow**: An exception object goes in → the provider compares it with the kinds of errors its backend can produce → it returns true if the error should be treated as an external provider problem.

**Call relations**: `ImessageSurface._consume_connected`, `ImessageSurface._downloaded_files`, and `ImessageSurface._send_contact_card` call this when something goes wrong. The surface asks the provider to classify the error because only the provider understands its backend’s error types.

*Call graph*: called by 3 (_consume_connected, _downloaded_files, _send_contact_card).


##### `MessageProvider.error_code`  (lines 63–63)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This turns a provider-specific error into a short code that the rest of the system can use in logs, responses, or status messages. It keeps backend-specific details from leaking everywhere.

**Data flow**: An exception object goes in → the provider extracts or chooses a stable error code → it returns that code as a string.

**Call relations**: `ImessageSurface._send_contact_card` calls this after a provider-related send failure. The surface uses the returned code to describe what went wrong without needing to know the provider’s private error format.

*Call graph*: called by 1 (_send_contact_card).
