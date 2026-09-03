# iMessage generated service APIs and gRPC bindings  `stage-20.2.2`

This stage is shared behind-the-scenes support for the iMessage extension. It is not the main business logic itself. Instead, it provides the “contract” that different parts of the system use when they talk over the network. The files are generated from Protocol Buffers, a format that defines data shapes clearly so both sides agree on what a request or response looks like. gRPC is the calling system that uses those shapes to make remote method calls, like calling a local function that actually runs on another process.

Each service has a pair of files. The pb2 file defines the vocabulary: attachment, chat, event, or message requests and replies, plus the service description. The matching pb2_grpc file provides the wiring: client stubs for making calls and server hooks for implementing them. Together, the attachment files cover upload and download work, chat files cover conversations, event files cover catching up on missed activity, and message files cover sending, editing, fetching, reacting to, and subscribing to message changes.

## Files in this stage

### Attachment service bindings
Generated protobuf descriptors and gRPC wiring define the attachment API vocabulary and client/server methods for attachment lookup, upload, and download.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2.py`

`generated` · `request handling`

This file is produced automatically from a Protocol Buffers definition file. Protocol Buffers, often called protobuf, are a way for different programs to agree on the exact shape of messages they send to each other. In everyday terms, this file is like a printed form template: it says which boxes exist, what kind of information goes in each box, and which service actions use those forms.

The attachment service described here covers three main tasks. A caller can request information about an attachment using its attachment GUID, upload a new attachment with a file name and raw byte data, or download an attachment. Downloading is modeled as a stream: the first response can carry header information, and later responses can carry chunks of the primary attachment data or companion attachment data.

The file also connects these messages to related attachment types from `attachment_types_pb2`, such as `AttachmentInfo`, `Companion`, and `CompanionInfo`. It records a service named `AttachmentService` with methods for getting metadata, uploading, and downloading. One method is also linked to an HTTP path, `/v1/attachments/{attachment_guid}`, through Google API annotations.

Because this is generated code, humans are not expected to edit it directly. Other code imports it so that requests and responses are encoded and decoded consistently. Without it, Python code would not know the agreed message formats for this attachment API.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is like the telephone switchboard for attachment operations. The real attachment rules live elsewhere, but this file defines how a Python client and server speak over gRPC, which is a system for calling functions across a network as if they were local methods. The service covers three actions: read attachment metadata, upload an attachment, and download an attachment as a stream of byte chunks.

At import time, the file first checks that the installed grpc Python package is new enough for the code that generated this file. If not, it stops immediately with a clear error, because mismatched generated code and runtime libraries can fail in confusing ways.

For clients, `AttachmentServiceStub` turns a gRPC channel into three callable methods. It knows the exact remote method names and how to turn request and response messages into bytes and back again. For servers, `AttachmentServiceServicer` provides placeholder methods that deliberately fail until an application overrides them with real behavior. The helper `add_AttachmentServiceServicer_to_server` registers those methods with a gRPC server. The final `AttachmentService` class offers an experimental shortcut style for making one-off calls without first creating a stub.

#### Function details

##### `AttachmentServiceStub.__init__`  (lines 37–57)

```
def __init__(self, channel)
```

**Purpose**: Creates a client-side object for talking to the remote attachment service. Someone uses this when they already have a gRPC channel and want simple Python callables for getting attachment info, uploading an attachment, or downloading one.

**Data flow**: It receives a gRPC channel, which is the network connection to a server. It attaches three methods to the stub, each with the remote service path and the correct message converters: requests are turned into bytes before sending, and response bytes are turned back into Python message objects. After construction, the stub object has ready-to-call methods for all three attachment operations.

**Call relations**: Client code creates this stub before making remote calls. Once created, the stub hands each method call to the gRPC channel, which sends the request to the server-side handlers registered for the same service paths.


##### `AttachmentServiceServicer.GetAttachmentInfo`  (lines 69–74)

```
def GetAttachmentInfo(self, request, context)
```

**Purpose**: Defines the server-side shape of the metadata lookup method, but does not implement it here. A real server is expected to subclass or replace this method so it can return information about an attachment without reading the attachment bytes.

**Data flow**: It receives a request and a gRPC context object, which carries response status information. In this base generated version, it marks the call as unimplemented, adds the detail message 'Method not implemented!', and raises an error instead of returning attachment metadata.

**Call relations**: This placeholder is called only if a server registers an `AttachmentServiceServicer` without overriding the method. In normal use, `add_AttachmentServiceServicer_to_server` connects the server's real version of this method to incoming `GetAttachmentInfo` requests.


##### `AttachmentServiceServicer.UploadAttachment`  (lines 76–87)

```
def UploadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the attachment upload method, but leaves the actual saving work to application code. A real implementation should store the primary file and optional sidecar together so that a partial upload is not visible later.

**Data flow**: It receives an upload request and a gRPC context. In this generated base class, it does not inspect or save the attachment data. It sets the response status to unimplemented, records a short explanation, and raises an error.

**Call relations**: This method is the target that server registration points to for upload calls. Real server code is expected to override it; otherwise, any client calling `UploadAttachment` through the stub or experimental API receives an unimplemented-method failure.


##### `AttachmentServiceServicer.DownloadAttachment`  (lines 89–95)

```
def DownloadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the attachment download method, which is meant to send the file back in multiple pieces. The generated base version is only a placeholder.

**Data flow**: It receives a download request and a gRPC context. Instead of producing a stream of response chunks, it sets the call status to unimplemented, adds a detail message, and raises an error.

**Call relations**: When the service is registered, incoming download requests are routed to this method name. In a working server, application code replaces it with a method that yields download response messages one after another.


##### `add_AttachmentServiceServicer_to_server`  (lines 98–119)

```
def add_AttachmentServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a server's attachment-service implementation to a gRPC server so outside clients can call it. Without this registration step, the server might have the Python methods, but gRPC would not know which network requests should reach them.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table that maps each public RPC name to the matching servicer method, along with the correct request parser and response writer. It then creates a generic gRPC handler for the attachment service and adds that handler table to the server.

**Call relations**: Server startup code calls this after creating its real servicer. Inside, it asks gRPC to build unary request/response handlers for metadata lookup and upload, and a unary-to-stream handler for download. It then hands the complete routing table to the gRPC server so future client calls can be dispatched correctly.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `AttachmentService.GetAttachmentInfo`  (lines 133–157)

```
def GetAttachmentInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for making a single remote metadata lookup call without first creating an `AttachmentServiceStub`. It is useful for code that wants to call the service directly by target address.

**Data flow**: It receives a request message, a target server address, and optional call settings such as credentials, timeout, compression, and metadata. It serializes the request, sends it through gRPC to the `GetAttachmentInfo` service path, deserializes the response bytes into a response message, and returns the result from the gRPC call.

**Call relations**: Client code may call this static method instead of using a stub. The method delegates the actual network work to gRPC's experimental unary-unary call helper, meaning one request is sent and one response is expected.


##### `AttachmentService.UploadAttachment`  (lines 160–184)

```
def UploadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for uploading one attachment through a direct gRPC call. It avoids creating a stub first, while still using the same service path and message formats.

**Data flow**: It takes an upload request, the target server, and optional settings such as credentials and timeout. It turns the request into bytes, sends it to the remote `UploadAttachment` method, converts the returned bytes into an upload response message, and gives that response back to the caller.

**Call relations**: Client code can use this method as a one-off call path. It hands the work to gRPC's experimental unary-unary helper, matching the server registration that expects one upload request and one upload response.


##### `AttachmentService.DownloadAttachment`  (lines 187–211)

```
def DownloadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for downloading an attachment as a stream of response messages. This is used when the server may need to send the attachment back in chunks rather than all at once.

**Data flow**: It receives a download request, target server information, and optional call settings. It serializes the request and sends it to the remote `DownloadAttachment` method. Instead of one response, it returns a gRPC stream that yields deserialized download response messages as the server sends them.

**Call relations**: Client code may call this static method when it wants direct access to a download stream without creating a stub. It delegates to gRPC's experimental unary-stream helper, which matches the server-side registration for a single request followed by many response frames.


### Chat service bindings
Generated protobuf descriptors and gRPC wiring define the chat API request and response shapes plus client/server access to chat operations.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2.py`

`generated` · `import time and request/response serialization`

This file is like a printed order form for the chat part of the iMessage extension. It does not contain the business logic that creates chats or marks them as read. Instead, it defines the exact message formats and service contract that other code must follow when talking about chats.

The file is generated by the Protocol Buffers compiler. Protocol Buffers, often called protobuf, are a way to describe structured data so different programs can read and write it consistently. Here, the generated code registers message types such as requests to create a chat, mark a chat as read, set a background, send typing status, fetch a chat, count chats, and subscribe to chat change events.

It also describes the `ChatService` remote API. A remote API is a set of calls one program can make to another, like asking, “create this chat” or “tell me when this chat changes.” Some service methods include HTTP route information, such as `/v1/chats:get`, so the same service definition can be used over web-style requests.

Because this is generated code, humans normally should not edit it directly. If it were missing or out of date, Python code using these chat messages could fail to import, reject valid data, or misunderstand what fields are available.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is produced from a Protocol Buffers service definition, so people normally should not edit it by hand. Protocol Buffers define messages and services in a language-neutral way; gRPC uses that definition to make remote calls feel like ordinary method calls. In practical terms, this file is the phone switchboard for chat operations such as creating a chat, marking it read, setting a background, sending typing status, reading chat details, and subscribing to chat events.

There are three main pieces. `ChatServiceStub` is for clients. Given a gRPC channel, it creates callable methods that know the exact network path to use and how to turn request and response objects into bytes and back again. `ChatServiceServicer` is a server-side base class. Its methods are only placeholders: each returns “not implemented” until real server code subclasses it and fills in the behavior. `add_ChatServiceServicer_to_server` connects such a real servicer to a gRPC server so incoming network calls are routed to the right Python method.

At the bottom, `ChatService` offers an experimental shortcut API for making single remote calls without first creating a stub object. The file also checks that the installed `grpcio` package is new enough for the generated code. Without this file, the project would still have chat message types, but Python clients and servers would not have the ready-made gRPC plumbing needed to communicate.

#### Function details

##### `ChatServiceStub.__init__`  (lines 37–92)

```
def __init__(self, channel)
```

**Purpose**: Builds the client-side object used to call the remote chat service. After this runs, code can call methods like `CreateChat` or `GetChat` as Python attributes, while gRPC sends the actual request over the network.

**Data flow**: It receives a gRPC channel, which is the open route to a remote server. It attaches one callable per chat operation to the stub, each with the correct service path plus the functions that convert request objects into bytes and response bytes back into objects. The result is the same stub object, now ready to make remote chat calls.

**Call relations**: Client code creates this stub when it has a channel to the iMessage service. The callables it installs are then used during normal request handling to send requests to the server endpoint generated in this same service definition.


##### `ChatServiceServicer.CreateChat`  (lines 103–108)

```
def CreateChat(self, request, context)
```

**Purpose**: Defines the server-side slot for creating a chat, but does not implement it here. Real server code is expected to override this method with the actual chat creation behavior.

**Data flow**: It receives a parsed create-chat request and a gRPC context object for reporting status. In this generated base version, it marks the response as “unimplemented,” adds a detail message, and raises an error instead of returning a chat response.

**Call relations**: When a server registers a subclass through `add_ChatServiceServicer_to_server`, incoming `CreateChat` calls are routed to this method name. If the subclass does not replace it, clients receive an unimplemented-method error.


##### `ChatServiceServicer.MarkChatRead`  (lines 110–114)

```
def MarkChatRead(self, request, context)
```

**Purpose**: Defines the server-side slot for marking a chat as read. This generated version is only a placeholder and must be overridden by the real service.

**Data flow**: It receives a mark-read request and the call context. Instead of changing chat state, it sets the call status to “unimplemented,” records a short explanation, and raises an error.

**Call relations**: The registration helper maps the remote `MarkChatRead` RPC to this method. A real servicer subclass supplies the actual work; otherwise this placeholder is what the server runs.


##### `ChatServiceServicer.SetBackground`  (lines 116–122)

```
def SetBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for setting a chat background image or data. It is not implemented in this generated file.

**Data flow**: It receives a set-background request, which may include byte data for the background, and the gRPC context. The placeholder does not store anything; it reports that the method is unimplemented and raises an error.

**Call relations**: The server registration helper connects incoming `SetBackground` calls to this method name. Real service code should override it so clients using the stub or experimental API can actually change a chat background.


##### `ChatServiceServicer.RemoveBackground`  (lines 124–128)

```
def RemoveBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for removing a chat background. The generated base method exists so the service shape is known, but it performs no real removal.

**Data flow**: It receives a remove-background request and the call context. It changes only the context status, setting it to “unimplemented,” then raises an error rather than returning success.

**Call relations**: Registered servers route `RemoveBackground` network calls here unless a subclass provides the real implementation. This keeps the service contract complete while leaving project-specific behavior elsewhere.


##### `ChatServiceServicer.ShareContactInfo`  (lines 130–137)

```
def ShareContactInfo(self, request, context)
```

**Purpose**: Defines the server-side slot for sharing the local user’s contact card into a chat. This base version is a placeholder and does not actually send anything.

**Data flow**: It receives a share-contact-info request and the gRPC context. The method sets an unimplemented status and raises an error, so no contact information is pushed and no success response is produced.

**Call relations**: The registration function maps the remote `ShareContactInfo` call to this method. A real implementation in a subclass is needed for clients to successfully trigger contact sharing.


##### `ChatServiceServicer.SetTyping`  (lines 139–144)

```
def SetTyping(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a temporary typing indicator. This generated method only says the feature is not implemented here.

**Data flow**: It receives a typing-status request and the call context. It does not publish or persist a typing state; it marks the call unimplemented and raises an error.

**Call relations**: Incoming `SetTyping` calls reach this method through the server registration table. Real server code overrides it to send the transient typing signal.


##### `ChatServiceServicer.GetChat`  (lines 146–152)

```
def GetChat(self, request, context)
```

**Purpose**: Defines the server-side slot for reading details about one chat. The generated base method does not look up any chat data.

**Data flow**: It receives a get-chat request and the gRPC context. Instead of fetching a chat and returning a response, it sets the call status to unimplemented and raises an error.

**Call relations**: The registration helper routes the remote `GetChat` method to this slot. A subclass must replace it before clients can use `GetChat` successfully.


##### `ChatServiceServicer.GetChatCount`  (lines 154–158)

```
def GetChatCount(self, request, context)
```

**Purpose**: Defines the server-side slot for returning how many chats match a request. This generated method is only a placeholder.

**Data flow**: It receives a chat-count request and the context for the RPC call. It does not count anything; it marks the method as unimplemented and raises an error.

**Call relations**: Servers registered with `add_ChatServiceServicer_to_server` expose this method name to the network. Real counting behavior belongs in a subclass.


##### `ChatServiceServicer.HasBackground`  (lines 160–164)

```
def HasBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for checking whether a chat has a background set. The base generated method does not perform the check.

**Data flow**: It receives a has-background request and the call context. It returns no yes-or-no answer; instead, it sets an unimplemented status and raises an error.

**Call relations**: The generated server registration connects incoming `HasBackground` requests to this method. A real implementation must override it to answer clients.


##### `ChatServiceServicer.SubscribeChatEvents`  (lines 166–170)

```
def SubscribeChatEvents(self, request, context)
```

**Purpose**: Defines the server-side slot for subscribing to a stream of chat events. This generated placeholder does not open or send any event stream.

**Data flow**: It receives a subscription request and the gRPC context. Rather than yielding event responses over time, it marks the method as unimplemented and raises an error.

**Call relations**: The registration helper treats this as a streaming response method, meaning a real implementation would send many responses for one request. If no subclass overrides it, subscribers receive an unimplemented error.


##### `add_ChatServiceServicer_to_server`  (lines 173–229)

```
def add_ChatServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a chat service implementation to a gRPC server. It is the server-side switchboard that says which Python method should run for each incoming chat RPC.

**Data flow**: It receives a servicer object, usually a subclass with real chat behavior, and a gRPC server. It builds a table of method handlers, each one describing how to decode incoming bytes into request objects, call the matching servicer method, and encode the response back into bytes. It then adds that table to the server so the service becomes reachable over the network.

**Call relations**: Server startup code calls this after creating the real servicer. Inside, it uses gRPC helper functions such as `grpc.unary_unary_rpc_method_handler`, `grpc.unary_stream_rpc_method_handler`, and `grpc.method_handlers_generic_handler` to package the routing rules before handing them to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `ChatService.CreateChat`  (lines 242–266)

```
def CreateChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for creating a chat. It sends a `CreateChat` request directly to a target server without requiring the caller to first build a `ChatServiceStub`.

**Data flow**: It receives a create-chat request, a target address, and optional connection settings such as credentials, timeout, compression, and metadata. It serializes the request, sends it to the `CreateChat` service path, deserializes the returned bytes into a create-chat response, and returns that result to the caller.

**Call relations**: This is an alternative to using `ChatServiceStub.__init__` and then calling the stub’s `CreateChat`. It hands the actual network work to `grpc.experimental.unary_unary`, meaning one request produces one response.


##### `ChatService.MarkChatRead`  (lines 269–293)

```
def MarkChatRead(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for marking a chat as read. It is useful when code wants to make just this remote call directly.

**Data flow**: It receives a mark-read request, target server information, and optional call settings. It turns the request into bytes, sends it to the `MarkChatRead` endpoint, reads an empty success response if the server accepts it, and returns that empty response object.

**Call relations**: This bypasses the longer-lived stub style and delegates the network exchange to `grpc.experimental.unary_unary`. On the server side, the call is routed to the registered servicer’s `MarkChatRead` method.


##### `ChatService.SetBackground`  (lines 296–320)

```
def SetBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for setting a chat background. It packages the request and sends it to the remote chat service.

**Data flow**: It receives a set-background request, the target server, and optional settings for the remote call. It serializes the request, calls the `SetBackground` endpoint, deserializes the empty success response, and returns it.

**Call relations**: This method is the direct-call counterpart to the `SetBackground` callable installed by `ChatServiceStub.__init__`. The remote server must have registered a servicer whose `SetBackground` method implements the action.


##### `ChatService.RemoveBackground`  (lines 323–347)

```
def RemoveBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for removing a chat background. It lets client code send this request directly to a target server.

**Data flow**: It receives a remove-background request plus target and optional call settings. It converts the request to bytes, sends it to the `RemoveBackground` endpoint, converts the empty response bytes back into an object, and returns it.

**Call relations**: It uses `grpc.experimental.unary_unary` for a simple one-request, one-response exchange. The server-side registration table connects the matching incoming RPC to `RemoveBackground` on the servicer.


##### `ChatService.ShareContactInfo`  (lines 350–374)

```
def ShareContactInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for asking the service to share the local user’s contact information in a chat. It is a direct remote call helper.

**Data flow**: It receives a share-contact-info request, target server details, and optional settings. It serializes the request, sends it to the `ShareContactInfo` endpoint, expects an empty success response, deserializes that response, and returns it.

**Call relations**: This direct helper mirrors the stub method of the same name. The remote call eventually lands on the server’s registered `ShareContactInfo` implementation.


##### `ChatService.SetTyping`  (lines 377–401)

```
def SetTyping(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for sending a typing indicator. This is for a temporary signal, not a stored chat record.

**Data flow**: It receives a typing request, a server target, and optional call settings. It serializes the request, sends it to the `SetTyping` endpoint, reads the empty response, and returns that response object.

**Call relations**: It delegates the network exchange to `grpc.experimental.unary_unary`. On the server, the registered servicer’s `SetTyping` method is responsible for doing the real typing-indicator work.


##### `ChatService.GetChat`  (lines 404–428)

```
def GetChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for fetching information about a chat. It sends a read request and returns the server’s chat response.

**Data flow**: It receives a get-chat request, a target server, and optional connection or call settings. It turns the request into bytes, sends it to the `GetChat` endpoint, turns the response bytes into a get-chat response object, and returns it.

**Call relations**: This is the direct-call version of the stub’s `GetChat` method. The server registration table maps the incoming request to the servicer’s `GetChat` method.


##### `ChatService.GetChatCount`  (lines 431–455)

```
def GetChatCount(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for asking how many chats match a request. It returns a response object containing the count information.

**Data flow**: It receives a chat-count request, the target server, and optional settings such as timeout or credentials. It serializes the request, sends it to the `GetChatCount` endpoint, deserializes the count response, and returns it.

**Call relations**: It uses the same one-request, one-response gRPC pattern as most methods in this file. On the server side, the registered `GetChatCount` servicer method handles the request.


##### `ChatService.HasBackground`  (lines 458–482)

```
def HasBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-call client shortcut for checking whether a chat has a background. It asks the remote service and returns the service’s yes-or-no style response.

**Data flow**: It receives a has-background request, target server details, and optional call settings. It serializes the request, sends it to the `HasBackground` endpoint, deserializes the returned response object, and gives that back to the caller.

**Call relations**: This direct helper mirrors the `HasBackground` callable on `ChatServiceStub`. The server must have a registered servicer implementation for `HasBackground` to produce a meaningful answer.


##### `ChatService.SubscribeChatEvents`  (lines 485–509)

```
def SubscribeChatEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental client shortcut for subscribing to chat events from a remote server. Unlike most methods here, one request can produce a stream of many responses over time.

**Data flow**: It receives a subscription request, target server information, and optional call settings. It serializes the request, opens the `SubscribeChatEvents` streaming call, and deserializes each event response as the server sends it. The caller receives a stream-like result rather than a single finished response.

**Call relations**: It delegates to `grpc.experimental.unary_stream`, which means one outgoing request leads to multiple incoming messages. On the server side, `add_ChatServiceServicer_to_server` registers `SubscribeChatEvents` with a streaming handler so a real servicer can yield chat events.


### Event service bindings
Generated protobuf descriptors and gRPC wiring define the event catch-up API and its client/server integration points.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2.py`

`generated` · `request handling and serialization`

This file is machine-made by the Protocol Buffers compiler. Protocol Buffers, or protobuf, are a compact way for different programs to agree on the shape of messages they send to each other. In this case, the messages are about catching up on iMessage events: message changes, group changes, poll changes, chat changes, completion notices, and heartbeats.

Think of it like a printed form template shared by two offices. One office fills in a “catch up from this event number” request, and the other sends back a stream of event forms in a known format. Without this file, the Python side would not know how to create, read, or validate those forms.

The file imports related generated protobuf modules for chats, groups, messages, polls, and streaming. It then registers a serialized description of the event service with protobuf’s global descriptor pool. From that description, protobuf builds Python classes such as `CatchUpEventsRequest`, `CatchUpEventsResponse`, and `CatchUpEventsComplete`, plus the `EventService` service definition. The response uses a “one-of” style payload, meaning each response item carries one kind of event at a time, such as a message change or a heartbeat.

Because this is generated code, developers normally should not edit it directly. Changes should be made in the original `.proto` file and regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is machine-written from a protocol buffer service definition. Its job is to connect ordinary Python code to gRPC, which is a remote call system that lets one program call a function in another program, often over the network. The service here is about catching up on durable iMessage-related events: messages, group changes, polls, and chats. A client can request everything after a known sequence number, then switch to live event streams without missing anything.

The file provides three main pieces. EventServiceStub is the client-side helper. Given a gRPC channel, it creates a callable named CatchUpEvents that sends one request and receives a stream of responses. EventServiceServicer is the server-side base class. By default it only says “not implemented,” so real server code must subclass it or provide a method with the same shape. add_EventServiceServicer_to_server connects that server implementation to a running gRPC server, like registering a phone extension so calls reach the right desk. EventService is an experimental shortcut API for making the same call without explicitly building a stub.

At import time, the file also checks that the installed grpc package is new enough for the generated code. Without this file, the protobuf message types would exist, but clients and servers would not have the standard Python gRPC glue needed to call the service.

#### Function details

##### `EventServiceStub.__init__`  (lines 45–55)

```
def __init__(self, channel)
```

**Purpose**: Creates the client-side access point for the EventService. Code that wants to call CatchUpEvents uses this stub so it does not have to manually describe the network method, message conversion, or response stream.

**Data flow**: It receives a gRPC channel, which is the connection path to a server. It attaches a CatchUpEvents callable to the stub, telling gRPC the remote method name, how to turn a CatchUpEventsRequest into bytes, and how to turn each response byte message back into a CatchUpEventsResponse. After construction, client code can call stub.CatchUpEvents with a request and read the returned stream of events.

**Call relations**: Client code creates this stub when it is ready to talk to an EventService server. The method it prepares must match the server registration done by add_EventServiceServicer_to_server, so the client and server agree on the same service path and message formats.


##### `EventServiceServicer.CatchUpEvents`  (lines 75–79)

```
def CatchUpEvents(self, request, context)
```

**Purpose**: Defines the server-side method that real EventService implementations are expected to provide. In this generated base class, it deliberately fails to make clear that no actual catch-up behavior has been written here.

**Data flow**: It receives a request and a gRPC context, which is the per-call object used to report status and details back to the caller. It marks the call as unimplemented, adds a short explanation, and raises a NotImplementedError. Nothing is streamed back unless a real implementation replaces this method.

**Call relations**: Server code normally subclasses EventServiceServicer or supplies an object with a working CatchUpEvents method. add_EventServiceServicer_to_server registers that method with gRPC, so when a client calls CatchUpEvents, gRPC routes the request here or to the overriding implementation.


##### `add_EventServiceServicer_to_server`  (lines 82–93)

```
def add_EventServiceServicer_to_server(servicer, server)
```

**Purpose**: Registers an EventService implementation with a gRPC server. This is the step that makes incoming CatchUpEvents network calls reach the Python method that knows how to answer them.

**Data flow**: It receives a servicer object and a gRPC server. It builds a method table for CatchUpEvents, including how to decode incoming request bytes and encode outgoing response messages. It then gives that table to the server under the EventService name, changing the server so it can accept this service’s calls.

**Call relations**: Server startup code calls this after creating the real servicer and before serving requests. Inside, it asks gRPC to build a unary-stream method handler, meaning one request comes in and many responses may go out, and then wraps that handler in a generic service registration so the server can route matching client calls to servicer.CatchUpEvents.

*Call graph*: 2 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler).


##### `EventService.CatchUpEvents`  (lines 115–139)

```
def CatchUpEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Offers an experimental one-shot way to call the remote CatchUpEvents method. It is useful for code that wants to make the request directly by giving a target address and call options instead of first creating a stub object.

**Data flow**: It receives a CatchUpEvents request, the target server address, and optional connection settings such as credentials, timeout, compression, and metadata. It passes those details to gRPC along with the service path and the request and response conversion functions. The result is a response stream from the remote server.

**Call relations**: This is an alternate client path to the same remote method prepared by EventServiceStub.__init__. On the server side, the call still lands on whatever CatchUpEvents implementation was registered through add_EventServiceServicer_to_server.


### Message service bindings
Generated protobuf descriptors and gRPC wiring define the message API vocabulary and client/server methods for sending, editing, fetching, listing, reacting, and subscribing.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2.py`

`generated` · `cross-cutting`

This file is produced automatically from a Protocol Buffers definition file, not written by hand. Protocol Buffers are a compact, strict way for different programs to agree on what data looks like when they talk to each other. Think of it like a set of standardized forms: one form for sending text, another for attaching a file, another for listing recent messages, and so on.

The file registers those forms with Google’s protobuf runtime so Python code can create, read, and validate them. It also describes the MessageService itself: operations such as SendTextMessage, SendAttachmentMessage, EditMessage, UnsendMessage, SetReaction, PlaceSticker, GetMessage, ListRecentMessages, ListChatMessages, GetEmbeddedMedia, and SubscribeMessageEvents. Several service methods also include HTTP path mappings, such as /v1/messages:sendText, so the same service contract can be exposed over web-style routes.

Most of the actual names and fields come from other generated protobuf files, such as message types and streaming event types. This file ties them together into the message-service contract. Without it, Python clients and servers would not have the generated classes and descriptors needed to serialize and deserialize message API traffic consistently.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2_grpc.py`

`generated` · `cross-cutting`

This file is like a phone book and switchboard for the message service. The real message behavior lives elsewhere; this file defines how callers and servers agree on names, request types, response types, and streaming shape. It was generated from a Protocol Buffers service definition. Protocol Buffers are a compact, typed message format, and gRPC is a remote-call system that lets one program call another over the network as if it were calling a local method.

At import time, the file first checks that the installed grpc package is new enough for the generated code. Without that check, a version mismatch could fail later in confusing ways.

There are three main pieces. MessageServiceStub is for clients: given a gRPC channel, it creates callable attributes such as SendTextMessage and GetMessage that serialize request objects, send them to the server, and turn the byte response back into Python message objects. MessageServiceServicer is the server-side base class: it lists every method the server must implement, but each default method simply returns “not implemented.” Real servers subclass it. add_MessageServiceServicer_to_server connects a real servicer to a gRPC server. Finally, MessageService offers experimental one-shot static helpers for making calls without manually creating a stub.

#### Function details

##### `MessageServiceStub.__init__`  (lines 46–126)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object with one callable attribute for each remote message operation. A caller uses these attributes to send requests such as sending text, listing messages, or subscribing to message events.

**Data flow**: It receives a gRPC channel, which is the network connection to a server. It attaches methods to the stub; each method knows the remote path to call, how to turn the request object into bytes, and how to turn the server’s bytes back into the right response object. The result is a ready-to-use client stub.

**Call relations**: Client code creates this stub when it already has a channel to the iMessage service. Later, each generated callable hands the request to gRPC, which performs the network call and returns the decoded response.


##### `MessageServiceServicer.SendTextMessage`  (lines 146–159)

```
def SendTextMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a plain text message. It exists so real server code can override it with the actual send behavior.

**Data flow**: It receives a request and a gRPC context, marks the call as unimplemented, adds a human-readable detail, and raises NotImplementedError. Nothing is sent or changed by this base version.

**Call relations**: When a server is registered without overriding this method, gRPC will route SendTextMessage calls here and the caller will receive an unimplemented error. A real servicer subclass is expected to replace it.


##### `MessageServiceServicer.SendAttachmentMessage`  (lines 161–165)

```
def SendAttachmentMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a message with an attachment. It is a required shape for real implementations to fill in.

**Data flow**: It takes the incoming attachment-message request and call context, sets the context status to unimplemented, and raises an error. It produces no successful response.

**Call relations**: gRPC can route SendAttachmentMessage requests to this method through the server registration helper. In production, a subclass should override it before registration.


##### `MessageServiceServicer.SendMultipartMessage`  (lines 167–171)

```
def SendMultipartMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a message made of multiple parts, such as text plus media. Real server code should override it.

**Data flow**: It receives the multipart request and context, changes the context to report an unimplemented method, and raises NotImplementedError. No message is created by this default method.

**Call relations**: This method is the endpoint target for registered SendMultipartMessage calls unless a subclass supplies real logic.


##### `MessageServiceServicer.SendCustomizedMiniAppMessage`  (lines 173–178)

```
def SendCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending an iMessage mini-app card backed by the caller’s own extension. It documents the expected server method but does not implement it.

**Data flow**: It receives the mini-app send request and context, sets an unimplemented gRPC status, and raises an exception. The base method returns no message snapshot.

**Call relations**: The registration helper can connect this method to incoming gRPC calls. A real service subclass is expected to perform the mini-app send instead.


##### `MessageServiceServicer.UpdateCustomizedMiniAppMessage`  (lines 180–185)

```
def UpdateCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for updating an existing customized mini-app message. It provides the method slot that real server code must fill.

**Data flow**: It takes the update request and context, reports that the method is not implemented, and raises NotImplementedError. It does not update any stored message.

**Call relations**: Incoming UpdateCustomizedMiniAppMessage calls are routed here only if the registered servicer has not overridden the method.


##### `MessageServiceServicer.EditMessage`  (lines 187–191)

```
def EditMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for editing an existing message. It is a template method for actual edit behavior.

**Data flow**: It receives an edit request and context, marks the gRPC call as unimplemented, and raises an exception. No edited message response is produced.

**Call relations**: The server registration maps the EditMessage RPC name to this method on the servicer object. Real implementations override it to do the work.


##### `MessageServiceServicer.UnsendMessage`  (lines 193–198)

```
def UnsendMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for retracting, or unsending, an existing message. The base version only signals that no implementation is present.

**Data flow**: It receives an unsend request and context, sets the status to unimplemented, and raises NotImplementedError. It does not retract anything or return the expected empty success value.

**Call relations**: If a server registers this base method as-is, UnsendMessage calls fail. A real servicer should replace it with code that performs the retraction.


##### `MessageServiceServicer.SetReaction`  (lines 200–204)

```
def SetReaction(self, request, context)
```

**Purpose**: Defines the server-side placeholder for adding, changing, or removing a reaction on a message. It establishes the method signature for real service code.

**Data flow**: It takes the reaction request and context, records an unimplemented status on the context, and raises an error. No reaction is saved and no message response is returned.

**Call relations**: The gRPC server can call this method after registration, but only an overridden version should be used for a working service.


##### `MessageServiceServicer.PlaceSticker`  (lines 206–210)

```
def PlaceSticker(self, request, context)
```

**Purpose**: Defines the server-side placeholder for placing a sticker on or near a message. Actual sticker placement must be supplied in a subclass.

**Data flow**: It receives the sticker request and context, marks the call as unimplemented, and raises NotImplementedError. It makes no message changes.

**Call relations**: This is the default target for PlaceSticker RPC calls when the servicer has not provided real behavior.


##### `MessageServiceServicer.NotifySilencedMessage`  (lines 212–217)

```
def NotifySilencedMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for Apple’s “Notify Anyway” action on a silenced message. It is only a contract in this generated base class.

**Data flow**: It receives the notification request and context, sets an unimplemented status and detail, then raises an exception. No notification action happens.

**Call relations**: After server registration, gRPC can route NotifySilencedMessage calls here. A production servicer should override it to trigger the actual action.


##### `MessageServiceServicer.GetMessage`  (lines 219–224)

```
def GetMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for reading one message. It tells implementers what method must exist but performs no lookup itself.

**Data flow**: It receives a get-message request and context, marks the call as unimplemented, and raises NotImplementedError. It returns no message data.

**Call relations**: The registration helper maps GetMessage to this servicer method. A real implementation replaces it with code that fetches and returns the requested message.


##### `MessageServiceServicer.ListRecentMessages`  (lines 226–233)

```
def ListRecentMessages(self, request, context)
```

**Purpose**: Defines the server-side placeholder for listing recent messages, optionally filtered by fields such as sender or read state. The base method does not perform the query.

**Data flow**: It takes the list request and context, sets the call status to unimplemented, and raises an exception. It produces no list response.

**Call relations**: Incoming ListRecentMessages calls reach this method through gRPC registration unless the servicer subclass overrides it.


##### `MessageServiceServicer.ListChatMessages`  (lines 235–240)

```
def ListChatMessages(self, request, context)
```

**Purpose**: Defines the server-side placeholder for listing messages from a particular chat. Real code must override it to read the chat history.

**Data flow**: It receives a chat-message list request and context, marks the call as unimplemented, and raises NotImplementedError. No messages are read or returned.

**Call relations**: The generated registration connects the ListChatMessages RPC name to this method on the servicer object. A working server supplies an overriding method.


##### `MessageServiceServicer.GetEmbeddedMedia`  (lines 242–250)

```
def GetEmbeddedMedia(self, request, context)
```

**Purpose**: Defines the server-side placeholder for fetching raw media embedded in a message. It exists because media bytes need a different response shape than ordinary JSON-style data.

**Data flow**: It receives the media request and context, reports the method as unimplemented, and raises an exception. It returns no media bytes.

**Call relations**: gRPC routes GetEmbeddedMedia calls to this method after registration. Actual servers override it or provide a companion raw-bytes route as described by the service contract.


##### `MessageServiceServicer.SubscribeMessageEvents`  (lines 252–258)

```
def SubscribeMessageEvents(self, request, context)
```

**Purpose**: Defines the server-side placeholder for a live stream of message change events. A real implementation would keep sending event responses over time.

**Data flow**: It receives a subscription request and context, sets the status to unimplemented, and raises NotImplementedError. No event stream is opened by the base method.

**Call relations**: The registration helper treats this as a unary-to-stream RPC: one request starts a stream of responses. A real servicer overrides it to feed live durable message events to callers.


##### `add_MessageServiceServicer_to_server`  (lines 261–342)

```
def add_MessageServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a concrete MessageServiceServicer object to a gRPC server so network requests can reach the right Python methods. Without this, the server would not know which method should answer each MessageService RPC name.

**Data flow**: It receives a servicer instance and a gRPC server. It builds a table from service method names to gRPC handlers, each with the right request decoder and response encoder, then registers that table on the server. The server is changed in place and becomes able to accept MessageService calls.

**Call relations**: Server setup code calls this after creating a servicer with real implementations. Inside, it asks gRPC to create ordinary request-response handlers and one request-to-stream handler, wraps them in a generic service handler, and adds those handlers to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `MessageService.SendTextMessage`  (lines 364–388)

```
def SendTextMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for sending a text message to a target server. It is useful when code wants to make the call directly instead of first building a stub.

**Data flow**: It receives a text-message request, a target address, and optional connection settings such as credentials, timeout, and metadata. It serializes the request, sends one gRPC request, decodes one MessageResponse, and returns it.

**Call relations**: Client code may call this static helper directly. The helper hands the actual network work to grpc.experimental.unary_unary with the SendTextMessage service path and matching serializers.


##### `MessageService.SendAttachmentMessage`  (lines 391–415)

```
def SendAttachmentMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending an attachment message. It wraps the gRPC details so the caller only supplies the request, destination, and options.

**Data flow**: It takes an attachment-message request plus target and call options. It turns the request into bytes, sends it as a single request, decodes the single MessageResponse, and returns that response.

**Call relations**: This helper is used from client-side code that chooses the experimental API. It delegates the network call to grpc.experimental.unary_unary.


##### `MessageService.SendMultipartMessage`  (lines 418–442)

```
def SendMultipartMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending a multipart message. Multipart means the request can contain several pieces that belong to one outgoing message.

**Data flow**: It receives the multipart request, server target, and optional gRPC settings. It serializes the request, sends one remote call, deserializes the returned MessageResponse, and gives it back to the caller.

**Call relations**: Client code calls this helper when it wants a one-shot call. The helper passes the prepared call information to grpc.experimental.unary_unary.


##### `MessageService.SendCustomizedMiniAppMessage`  (lines 445–469)

```
def SendCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for sending a customized mini-app message. It hides the low-level gRPC path and conversion functions.

**Data flow**: It takes the mini-app send request, target server, and optional call settings. It converts the request to bytes, performs one remote call, converts the bytes back into a MessageResponse, and returns it.

**Call relations**: This static helper is a client-side shortcut. It relies on grpc.experimental.unary_unary to contact the SendCustomizedMiniAppMessage endpoint.


##### `MessageService.UpdateCustomizedMiniAppMessage`  (lines 472–496)

```
def UpdateCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for updating a customized mini-app message. Callers use it to request the update and receive a fresh message response.

**Data flow**: It receives an update request and connection options, serializes the request, sends it to the UpdateCustomizedMiniAppMessage RPC path, decodes the returned MessageResponse, and returns it.

**Call relations**: Client-side code can call this instead of creating MessageServiceStub. The helper delegates the actual request-response exchange to grpc.experimental.unary_unary.


##### `MessageService.EditMessage`  (lines 499–523)

```
def EditMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for editing a message. It packages the request with the correct gRPC service path and response type.

**Data flow**: It takes an edit request, target, and optional call controls such as timeout or metadata. It serializes the request, sends one remote call, deserializes the MessageResponse, and returns that response.

**Call relations**: Client code invokes this helper for a one-off edit operation. The helper hands the call to grpc.experimental.unary_unary.


##### `MessageService.UnsendMessage`  (lines 526–550)

```
def UnsendMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for retracting an existing message. A successful call returns an empty response, meaning there is no extra payload beyond success.

**Data flow**: It receives an unsend request, target address, and optional settings. It serializes the request, sends it to the UnsendMessage RPC, decodes the empty response type, and returns it.

**Call relations**: This client helper uses grpc.experimental.unary_unary for a single request and single response. It is an alternative to calling the same RPC through MessageServiceStub.


##### `MessageService.SetReaction`  (lines 553–577)

```
def SetReaction(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for setting a reaction on a message. It returns the resulting message snapshot when the server succeeds.

**Data flow**: It takes a reaction request and connection options, serializes the request, sends one call to the SetReaction endpoint, decodes the MessageResponse, and returns it.

**Call relations**: Client code may use this static method as a shortcut. It delegates the real network exchange to grpc.experimental.unary_unary.


##### `MessageService.PlaceSticker`  (lines 580–604)

```
def PlaceSticker(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for placing a sticker. It wraps the gRPC call details for this specific message mutation.

**Data flow**: It receives a sticker-placement request, a server target, and optional call settings. It serializes the request, performs one remote request-response call, decodes the MessageResponse, and returns it.

**Call relations**: This helper is called by clients using the experimental API. It forwards the call to grpc.experimental.unary_unary with the PlaceSticker RPC path.


##### `MessageService.NotifySilencedMessage`  (lines 607–631)

```
def NotifySilencedMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for triggering “Notify Anyway” on a silenced message. It expects only an empty success response.

**Data flow**: It receives the notification request, target, and optional gRPC controls. It serializes the request, sends one remote call, decodes the empty response, and returns it.

**Call relations**: Client code can use this helper instead of a stub method. The helper delegates the single request and response to grpc.experimental.unary_unary.


##### `MessageService.GetMessage`  (lines 634–658)

```
def GetMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for reading one message. It returns the server’s get-message response.

**Data flow**: It receives a get-message request, target server, and optional settings. It serializes the request, sends it to the GetMessage RPC, decodes a GetMessageResponse, and returns it.

**Call relations**: This is a client-side shortcut for the GetMessage endpoint. It uses grpc.experimental.unary_unary to do the network call.


##### `MessageService.ListRecentMessages`  (lines 661–685)

```
def ListRecentMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for listing recent messages. It can be used with request fields that filter or page through messages.

**Data flow**: It takes a list request, target, and optional call settings. It serializes the request, sends a single remote call, decodes a ListRecentMessagesResponse, and returns the list response.

**Call relations**: Client code using the experimental API calls this directly. The helper passes the call to grpc.experimental.unary_unary with the ListRecentMessages path.


##### `MessageService.ListChatMessages`  (lines 688–712)

```
def ListChatMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for listing messages in a specific chat. It returns a response containing that chat’s messages according to the request parameters.

**Data flow**: It receives a chat-message list request, target, and optional gRPC options. It serializes the request, sends one call to the ListChatMessages endpoint, decodes the response, and returns it.

**Call relations**: This static helper is a client-side alternative to a stub call. It relies on grpc.experimental.unary_unary for transport.


##### `MessageService.GetEmbeddedMedia`  (lines 715–739)

```
def GetEmbeddedMedia(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for fetching embedded media from a message. It returns a response type that can carry media data.

**Data flow**: It takes the embedded-media request, target server, and optional settings. It serializes the request, sends a single gRPC call, decodes a GetEmbeddedMediaResponse, and returns it.

**Call relations**: Client code can call this helper when using the experimental API. It delegates the request-response exchange to grpc.experimental.unary_unary.


##### `MessageService.SubscribeMessageEvents`  (lines 742–766)

```
def SubscribeMessageEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental direct client call for subscribing to live message events. Unlike the other helpers, one request can produce many responses over time.

**Data flow**: It receives a subscription request, target, and optional connection settings. It serializes the one request, opens a unary-to-stream gRPC call, and yields or returns a stream of decoded SubscribeMessageEventsResponse objects.

**Call relations**: Client code calls this to join the live event feed. The helper hands the streaming work to grpc.experimental.unary_stream, using the generated request and response converters.
