# iMessage provider and gRPC service boundary  `stage-21.1`

This stage is the border between the iMessage extension and the rest of the system. It is shared support code, used whenever the system needs to talk about iMessage data or call iMessage services over the network. The __init__.py file is only a package marker, like a label on a folder, so Python can import the extension.

The hand-written provider.py file defines the contract that real iMessage providers must follow. It also defines small shared data shapes for phone numbers, incoming messages, attachments, and events, so the rest of the extension can pass information around consistently.

The many *_pb2.py files are generated from Protocol Buffers, a schema format for structured data. They define the agreed shapes for addresses, chats, messages, attachments, groups, polls, events, and stream heartbeats. The *_pb2_grpc.py files add gRPC wiring, which means standard client and server code for calling those services across a network. Together, these files act like adapters and plugs: provider.py says what the extension can do, while the generated files make sure both sides speak the same language.

## Files in this stage

### Package and address vocabulary
This group establishes the importable iMessage extension package and its shared address/service-type vocabulary.

### `extensions/imessage/ufo_ext_imessage/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find it by name. Here, the drawer is the `ufo_ext_imessage` extension package. Even though the file has no code, removing it could change how imports work, especially in tools or environments that expect traditional Python packages. Its main job is structural: it makes the iMessage extension visible as a package and gives the project a stable place to add package-level setup later if needed.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/address_types_pb2.py`

`generated` · `cross-cutting data serialization`

This file is machine-generated glue code for Protocol Buffers, often called protobuf: a format for describing data so different programs can exchange it without guessing what each field means. In plain terms, it defines a small “address card” format for chat contact information.

The file registers two message shapes. `SingleServiceAddressInfo` represents one address, such as a phone number or email address, tied to one chat service. `MultiServiceAddressInfo` represents one address that may work with several services. Both can also include an optional country value, which is useful when a phone number or address needs regional context.

It also defines `ChatServiceType`, a fixed list of possible services: unspecified, iMessage, SMS, and RCS. Using a fixed list helps prevent spelling mistakes or mismatched names across systems.

Most of the code is not handwritten business logic. It loads a compact serialized description of the protobuf schema into Google’s protobuf runtime, which then creates the usable Python message classes. Without this file, Python code in this extension would not know how to construct, parse, or serialize these address records in the exact format expected by other components.


### Attachment service boundary
This group defines the generated attachment API contract, its gRPC client/server wiring, and the attachment data shapes used across that API.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2.py`

`generated` · `request handling and serialization`

This is machine-generated Protocol Buffers code. Protocol Buffers are a way to describe messages once, then generate code in many languages so different parts of a system agree on exactly what data looks like. In this case, the shared topic is iMessage attachments: files being uploaded, downloaded, or queried by their attachment ID.

The file loads a compact serialized description of the attachment service and asks Google’s protobuf runtime to turn it into usable Python classes. Those classes represent requests and responses such as “get attachment info,” “upload attachment,” and “download attachment.” They include fields like an attachment GUID, file name, raw file bytes, attachment metadata, and optional companion-device information.

It also describes an AttachmentService with three remote operations: getting attachment information, uploading an attachment, and downloading an attachment. One of those operations includes an HTTP mapping for a REST-style path, so gateway tools can translate an HTTP request into the protobuf service call.

A useful way to think about this file is as a printed customs form template: it does not ship the package itself, but it makes sure every sender and receiver labels the package the same way. Without this generated file, Python code in this project could not reliably build, read, or serialize these attachment-service messages.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2_grpc.py`

`generated` · `request handling`

This file is machine-made from a protobuf service definition. A protobuf file describes network messages and services in a language-neutral way, and gRPC is the system that uses those descriptions to make remote calls feel like normal method calls. Here, the service is about attachments: reading metadata, uploading an attachment, and downloading attachment bytes as a stream.

The file does not contain the real business logic for storing or finding attachments. Instead, it is like a set of matching electrical sockets and plugs. On the client side, AttachmentServiceStub turns Python request objects into bytes, sends them to the named remote service method, and turns the byte response back into Python objects. On the server side, AttachmentServiceServicer defines the method names a real server must implement. Its default methods deliberately fail with “not implemented,” so a developer must subclass or replace them with real behavior. The add_AttachmentServiceServicer_to_server function connects those server methods to a running gRPC server.

There is also an experimental AttachmentService helper with static methods for making one-off calls without first creating a stub object. At import time, the file checks that the installed grpc package is new enough for this generated code; otherwise it fails early with a clear upgrade message.

#### Function details

##### `AttachmentServiceStub.__init__`  (lines 37–57)

```
def __init__(self, channel)
```

**Purpose**: Creates a client-side object that knows how to call the remote attachment service. Someone uses this when they already have a gRPC channel, which is the connection path to a server.

**Data flow**: It receives a gRPC channel. It attaches three callable methods to the stub: one for getting attachment metadata, one for uploading an attachment, and one for downloading attachment bytes as a stream. Each method knows how to turn the correct request object into bytes before sending it, and how to turn the server’s bytes back into the correct response object.

**Call relations**: Client code creates this stub before making attachment calls. After that, the rest of the program calls the stub’s GetAttachmentInfo, UploadAttachment, or DownloadAttachment attributes as the network entry points to the server.


##### `AttachmentServiceServicer.GetAttachmentInfo`  (lines 69–74)

```
def GetAttachmentInfo(self, request, context)
```

**Purpose**: Defines the server-side shape of the metadata lookup operation. In this generated base class, it is only a placeholder and must be replaced by real code in a service implementation.

**Data flow**: It receives a request and a gRPC context, which is the call’s control object for status and details. Because no real lookup is implemented here, it marks the call as unimplemented, adds an explanatory message, and raises an error instead of returning attachment metadata.

**Call relations**: A real server implementation is expected to override this method. The registration function connects whatever implementation is present to incoming GetAttachmentInfo network calls.


##### `AttachmentServiceServicer.UploadAttachment`  (lines 76–87)

```
def UploadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the upload operation for an attachment. The generated version is a placeholder, so it prevents accidental silent success when no real upload logic has been written.

**Data flow**: It receives an upload request and the gRPC context. Instead of saving any files, it sets the call status to unimplemented, records that the method is not implemented, and raises an error.

**Call relations**: Server code should override this with logic that stores the primary attachment file and any optional sidecar file. The registration function exposes the overriding method to incoming UploadAttachment calls.


##### `AttachmentServiceServicer.DownloadAttachment`  (lines 89–95)

```
def DownloadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the download operation, where attachment bytes are sent back in multiple pieces. The generated base method is only a placeholder.

**Data flow**: It receives a download request and the gRPC context. It does not read or stream any attachment data; it marks the call as unimplemented and raises an error.

**Call relations**: A real attachment server should override this with streaming download logic. Once registered, gRPC will call that implementation when a client asks to download an attachment.


##### `add_AttachmentServiceServicer_to_server`  (lines 98–119)

```
def add_AttachmentServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a concrete attachment service implementation to a gRPC server so remote clients can call it. Without this step, the server might exist, but these attachment method names would not be routed anywhere.

**Data flow**: It receives a servicer object and a gRPC server. It builds a table that maps each network method name to the matching Python method, along with the correct request parser and response writer. It then creates a generic gRPC service handler and adds those handlers to the server.

**Call relations**: Server startup code calls this after creating the real servicer. Inside, it asks gRPC to build unary-unary handlers for request-and-single-response methods, a unary-stream handler for the download method, and a generic handler for the whole AttachmentService before registering everything on the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `AttachmentService.GetAttachmentInfo`  (lines 133–157)

```
def GetAttachmentInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off client call to fetch attachment metadata using gRPC’s experimental convenience API. It is useful when code wants to call the remote method directly without manually creating a stub object first.

**Data flow**: It receives a metadata request, the server target address, and optional connection settings such as credentials, timeout, compression, and metadata headers. It serializes the request, sends it to the GetAttachmentInfo remote method, deserializes the response, and returns the result from gRPC.

**Call relations**: Client code may call this static helper directly. It hands the actual network work to grpc.experimental.unary_unary, because this method sends one request and expects one response.


##### `AttachmentService.UploadAttachment`  (lines 160–184)

```
def UploadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off client call to upload an attachment through the experimental gRPC convenience API. It wraps the network details so the caller can focus on the upload request object.

**Data flow**: It receives an upload request, a target server, and optional call settings. It turns the upload request into bytes, sends those bytes to the UploadAttachment remote method, converts the byte response back into an upload response object, and returns the gRPC call result.

**Call relations**: Client code can use this instead of constructing AttachmentServiceStub. It delegates the network operation to grpc.experimental.unary_unary, matching the service design of one upload request followed by one upload response.


##### `AttachmentService.DownloadAttachment`  (lines 187–211)

```
def DownloadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off client call to download an attachment as a stream of response messages. This is suited to large attachment data because the server can send chunks instead of one huge response.

**Data flow**: It receives a download request, a target server, and optional call settings. It serializes the request, sends it to the DownloadAttachment remote method, and returns a stream-like result that yields deserialized download response messages as they arrive.

**Call relations**: Client code may call this static helper when it wants to download without creating a stub. It hands the call to grpc.experimental.unary_stream, because the flow is one request followed by many response frames.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_types_pb2.py`

`generated` · `cross-cutting serialization/deserialization`

This file is machine-generated from a Protocol Buffer definition, which is a compact, shared way to describe structured data so different parts of a system can agree on what a message looks like. In everyday terms, it is like a printed form template: it says which boxes exist for an iMessage attachment, what type of value belongs in each box, and which fixed choices are allowed.

The main message shape is AttachmentInfo. It records facts about an attachment, including its unique id, original id if present, file name, MIME type (the file’s content type, such as image/jpeg), Apple UTI (Uniform Type Identifier), size in bytes, whether it was sent by the local user, transfer status, and flags such as hidden or sticker. It also supports companion information, used for related attachment pieces such as the video part of a Live Photo.

The file also defines CompanionInfo and Companion, plus two enums, which are fixed lists of named values: TransferState describes whether a file is pending, transferring, failed, finished, unavailable, or unknown; CompanionKind describes what kind of companion data is attached.

Because this is generated code, people should not edit it directly. If it were missing or out of date, code expecting these attachment message classes could not correctly serialize, deserialize, or inspect iMessage attachment metadata.


### Chat and event services
This group covers generated chat and event service contracts, their gRPC bindings, and the supporting chat and group-change record types.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2.py`

`generated` · `import time and API request/response serialization`

This file is machine-made from `chat_service.proto`, so humans are not meant to edit it directly. Its job is to turn the chat API contract into Python objects the program can use. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the shape of messages they send each other, like a shared form with named fields.

The file describes requests and responses for common chat actions: creating a chat, marking a chat as read, setting or removing a chat background, sharing contact info, showing typing status, fetching a chat, counting chats, checking for a background, and subscribing to chat change events. It also records the `ChatService` service definition, including HTTP paths such as `/v1/chats:get` and `/v1/chats:count`, so API layers can map network calls to the right operation.

Most of the work happens at import time. The serialized protobuf description is registered with Google’s protobuf runtime, and helper code builds Python classes such as `CreateChatRequest`, `GetChatResponse`, and `SubscribeChatEventsResponse`. Other code can then instantiate these classes, fill in fields, and convert them to or from bytes for network communication. Without this file, Python code in this extension would not know the exact wire format or field names for the chat service, and clients and servers could easily disagree about what a valid chat request looks like.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is machine-made from a protobuf service definition. A protobuf file describes messages and remote procedures; gRPC is the network system that turns those descriptions into real client and server calls. Without this file, Python code would still know what the chat messages look like, but it would not know how to send a CreateChat request over the wire, receive a GetChat response, or register a server method under the correct service name.

The file does three main jobs. First, it checks that the installed grpc Python package is new enough for the generated code. Second, ChatServiceStub builds a client-side object: each attribute is a callable remote method that knows the exact network path and how to turn request and response objects into bytes and back. Third, ChatServiceServicer provides placeholder server methods. These placeholders deliberately return “not implemented” until real application code subclasses or replaces them.

The add_ChatServiceServicer_to_server function connects a real servicer to a gRPC server, like plugging labeled phone lines into a switchboard. Finally, the ChatService class offers an experimental shortcut API for making one-off calls without first creating a stub object.

#### Function details

##### `ChatServiceStub.__init__`  (lines 37–92)

```
def __init__(self, channel)
```

**Purpose**: Builds the client-side handle for calling the remote chat service. Someone uses this when they already have a gRPC channel, which is the connection-like object used to talk to a server.

**Data flow**: It receives a channel. It attaches callable methods such as CreateChat, GetChat, and SubscribeChatEvents to the stub, each configured with the right service path plus the right request serializer and response parser. The result is the same stub object, now ready for client code to call remote chat operations.

**Call relations**: Client code creates this stub before making normal gRPC calls. Each callable it creates hands requests to the gRPC library, which sends them to the server method registered under the matching path.


##### `ChatServiceServicer.CreateChat`  (lines 103–108)

```
def CreateChat(self, request, context)
```

**Purpose**: Defines the server-side slot for creating a chat, but only as a placeholder. Real server code must override it with the actual behavior.

**Data flow**: It receives a CreateChat request and the gRPC context, which carries call status and metadata. Instead of creating anything, it marks the call as unimplemented and raises an error. Nothing useful is returned.

**Call relations**: When a server registers a servicer without overriding this method, incoming CreateChat calls end here and fail clearly. The registration function connects this method name to the gRPC server machinery.


##### `ChatServiceServicer.MarkChatRead`  (lines 110–114)

```
def MarkChatRead(self, request, context)
```

**Purpose**: Defines the server-side slot for marking a chat as read, but does not implement it. It exists so application code has the correct method shape to override.

**Data flow**: It receives a MarkChatRead request and call context. It sets the gRPC status to unimplemented, records a short explanation, and raises NotImplementedError. No read state is changed.

**Call relations**: If registered as-is, this is what the server calls for MarkChatRead requests. A real chat service would replace this placeholder with code that updates read status.


##### `ChatServiceServicer.SetBackground`  (lines 116–122)

```
def SetBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for setting a chat background image or data, but leaves the real work to an override. The comment notes that HTTP JSON forms carry raw bytes as base64 text.

**Data flow**: It receives a SetBackground request and context. It does not inspect or store the background data; it reports that the method is unimplemented and raises an error. No response body is produced.

**Call relations**: The gRPC server can route SetBackground calls to this method after registration. In a working service, a subclass would supply the actual background-saving behavior.


##### `ChatServiceServicer.RemoveBackground`  (lines 124–128)

```
def RemoveBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for removing a chat background. In this generated base class, it is only a reminder that real code must implement the action.

**Data flow**: It receives a RemoveBackground request and context. It changes only the call status, setting it to unimplemented, then raises NotImplementedError. No chat background is removed.

**Call relations**: The server registration maps the RemoveBackground RPC to this method. Production code is expected to override it before serving real users.


##### `ChatServiceServicer.ShareContactInfo`  (lines 130–137)

```
def ShareContactInfo(self, request, context)
```

**Purpose**: Defines the server-side slot for sharing the local user's contact card into a chat. The generated version does not perform the share; it only marks the method as missing.

**Data flow**: It receives a ShareContactInfo request and context. It sets an unimplemented status and raises an error, so no contact information is sent and no local state changes.

**Call relations**: Incoming ShareContactInfo calls reach this method if the base servicer is registered directly. A real servicer supplies the code that talks to the underlying iMessage system.


##### `ChatServiceServicer.SetTyping`  (lines 139–144)

```
def SetTyping(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a temporary typing indicator. The base generated method does not send anything.

**Data flow**: It receives a SetTyping request and context. It reports “method not implemented” through the gRPC context and raises NotImplementedError. No typing signal is persisted or transmitted.

**Call relations**: The registration helper can wire this method into a server. In normal use, application code overrides it so clients can tell a chat that the user is typing.


##### `ChatServiceServicer.GetChat`  (lines 146–152)

```
def GetChat(self, request, context)
```

**Purpose**: Defines the server-side slot for reading one chat. It is a placeholder that must be replaced by real lookup logic.

**Data flow**: It receives a GetChat request and context. It does not fetch a chat; it marks the call as unimplemented and raises an error. No GetChat response is returned.

**Call relations**: After registration, this is the fallback destination for GetChat requests. Real server code overrides it to read from the chat source and return chat details.


##### `ChatServiceServicer.GetChatCount`  (lines 154–158)

```
def GetChatCount(self, request, context)
```

**Purpose**: Defines the server-side slot for counting chats. The generated base version exists only to provide the expected method signature.

**Data flow**: It receives a GetChatCount request and context. It sets an unimplemented status and raises NotImplementedError, so no count is calculated or returned.

**Call relations**: The server wiring can route GetChatCount calls here. A working implementation replaces this method with code that counts matching chats.


##### `ChatServiceServicer.HasBackground`  (lines 160–164)

```
def HasBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for checking whether a chat has a background. The generated method itself cannot answer the question.

**Data flow**: It receives a HasBackground request and context. It marks the call as unimplemented and raises an error. No true-or-false background result is returned.

**Call relations**: The registration helper maps the HasBackground RPC to this method. Application code overrides it to inspect the chat's stored background state.


##### `ChatServiceServicer.SubscribeChatEvents`  (lines 166–170)

```
def SubscribeChatEvents(self, request, context)
```

**Purpose**: Defines the server-side slot for subscribing to chat events, such as changes or updates over time. The base version does not stream any events.

**Data flow**: It receives a SubscribeChatEvents request and context. It sets the call status to unimplemented and raises NotImplementedError, so no event stream is opened.

**Call relations**: This method is intended for a server-streaming RPC, meaning one request can produce many responses over time. The registration helper wires it as a stream method, but real event-producing code must override it.


##### `add_ChatServiceServicer_to_server`  (lines 173–229)

```
def add_ChatServiceServicer_to_server(servicer, server)
```

**Purpose**: Registers a chat service implementation with a gRPC server. This is the bridge between a Python object with methods and the network names that clients call.

**Data flow**: It receives a servicer object and a server. It builds a table mapping each chat RPC name to the matching servicer method, along with the correct request parser and response serializer. It then adds that table to the server so incoming network calls can be decoded, dispatched, and encoded back into responses.

**Call relations**: Server startup code calls this after creating a real ChatServiceServicer implementation. Inside, it asks gRPC to create unary request-response handlers and one unary-to-stream handler, then wraps them in a generic service handler and adds them to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `ChatService.CreateChat`  (lines 242–266)

```
def CreateChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to create a chat without first building a ChatServiceStub. It is a convenience wrapper around gRPC's direct call API.

**Data flow**: It receives a CreateChat request, a target server address, and optional call settings such as credentials, timeout, metadata, and compression. It serializes the request, sends it to the CreateChat service path, parses the CreateChat response, and returns the gRPC call result.

**Call relations**: Client code may use this instead of creating a stub. It hands the actual network work to grpc.experimental.unary_unary, which performs a single request and waits for a single response.


##### `ChatService.MarkChatRead`  (lines 269–293)

```
def MarkChatRead(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to mark a chat as read. It is for callers that want the direct API rather than a reusable stub.

**Data flow**: It receives a MarkChatRead request, target, and optional call settings. It sends the serialized request to the MarkChatRead path and expects an Empty response, which means success has no extra data attached.

**Call relations**: Client code calls this when it wants to update read status through the generated experimental API. The function delegates the network exchange to grpc.experimental.unary_unary.


##### `ChatService.SetBackground`  (lines 296–320)

```
def SetBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to set a chat background. It packages the request for the correct remote method and expects only an empty success response.

**Data flow**: It receives a SetBackground request, the target server, and optional settings like credentials and timeout. It serializes the request bytes, sends them to the SetBackground RPC path, and parses an Empty response if the call succeeds.

**Call relations**: Client code can call this directly when changing a chat background. It relies on grpc.experimental.unary_unary to do the actual sending and receiving.


##### `ChatService.RemoveBackground`  (lines 323–347)

```
def RemoveBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to remove a chat background. It hides the exact gRPC path and serialization details from the caller.

**Data flow**: It receives a RemoveBackground request, target, and optional call controls. It converts the request to bytes, calls the RemoveBackground service path, and converts the empty response back into an Empty object.

**Call relations**: This is an alternative to using ChatServiceStub.RemoveBackground. It passes the remote call to grpc.experimental.unary_unary.


##### `ChatService.ShareContactInfo`  (lines 350–374)

```
def ShareContactInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to share contact information into a chat. It is useful when a caller wants a single direct request rather than a long-lived client object.

**Data flow**: It receives a ShareContactInfo request, server target, and optional settings. It serializes the request, sends it to the ShareContactInfo RPC path, and parses an Empty response indicating there is no response payload.

**Call relations**: Client code calls this direct wrapper for the contact-sharing RPC. The function hands off to grpc.experimental.unary_unary for the actual network call.


##### `ChatService.SetTyping`  (lines 377–401)

```
def SetTyping(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to send a temporary typing indicator. It is a small wrapper that knows the exact remote method name and message formats.

**Data flow**: It receives a SetTyping request, target, and optional connection and call settings. It serializes the request, sends it to the SetTyping path, and parses an Empty response on success.

**Call relations**: Client code can use this when it needs to announce typing without creating a stub first. It delegates the single request-response exchange to grpc.experimental.unary_unary.


##### `ChatService.GetChat`  (lines 404–428)

```
def GetChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to fetch a chat. It turns a local request object into a remote GetChat call and returns the parsed response.

**Data flow**: It receives a GetChat request, target, and optional settings. It serializes the request, sends it to the GetChat service path, parses the GetChat response bytes, and returns the result from gRPC.

**Call relations**: Client code may use this direct method instead of ChatServiceStub.GetChat. The actual network operation is performed by grpc.experimental.unary_unary.


##### `ChatService.GetChatCount`  (lines 431–455)

```
def GetChatCount(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to count chats. It provides the correct gRPC path and message conversion for that request.

**Data flow**: It receives a GetChatCount request, target, and optional call settings. It sends the serialized request to the GetChatCount RPC and parses the GetChatCount response from the server.

**Call relations**: This is the direct-call counterpart to the stub's GetChatCount method. It passes work to grpc.experimental.unary_unary, which sends one request and receives one response.


##### `ChatService.HasBackground`  (lines 458–482)

```
def HasBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to ask whether a chat has a background. It returns the server's parsed answer rather than making the caller deal with wire-format bytes.

**Data flow**: It receives a HasBackground request, target, and optional settings. It serializes the request, sends it to the HasBackground RPC path, and parses the HasBackground response.

**Call relations**: Client code can use this helper for a direct background-check call. The helper delegates the network exchange to grpc.experimental.unary_unary.


##### `ChatService.SubscribeChatEvents`  (lines 485–509)

```
def SubscribeChatEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to subscribe to chat events. Unlike the simple request-response methods, this call can receive a stream of responses over time.

**Data flow**: It receives a SubscribeChatEvents request, target, and optional call settings. It serializes the request, opens the SubscribeChatEvents RPC, and parses each streamed SubscribeChatEvents response as it arrives.

**Call relations**: Client code calls this when it wants ongoing chat updates without creating a stub. It hands off to grpc.experimental.unary_stream, the gRPC helper for one request followed by many server responses.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_types_pb2.py`

`generated` · `import time and data serialization/deserialization`

This file is like a pre-printed form for chat data. It tells Python exactly what fields an iMessage chat can have, such as its unique ID, display name, whether it is a group chat, who participates, and what the last message was. It also defines small event shapes for things that can happen to a chat, such as being archived, unarchived, marked read, or having its background changed.

The format used here is Protocol Buffers, often called protobuf: a compact data format that lets different programs exchange structured information without guessing what each field means. The source of truth is the original `chat_types.proto` file; this Python file is generated from it by the protobuf compiler.

At import time, the file checks that the installed protobuf runtime is compatible, imports related message definitions for timestamps, addresses, and messages, then registers the serialized chat schema with protobuf’s descriptor system. After that, protobuf’s builder creates the usable Python message classes, such as `Chat` and `ChatChangeEvent`, from the schema description.

Without this file, Python code in this extension would not be able to create, read, serialize, or deserialize these chat records in the agreed format. If the file were edited manually, it could drift away from the schema and break compatibility with other services or languages using the same protobuf definitions.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2.py`

`generated` · `import time and request/stream serialization`

This file is not handwritten application logic. It is generated from a Protocol Buffers definition, which is a language-neutral contract for messages sent between systems. In plain terms, it is like a printed form template: every program that uses it knows which boxes exist, what can go in them, and how to pack or unpack the form for transport.

The event service described here supports a “catch up” flow. A client can ask for events after a known sequence number, and the service can stream back responses. Each response may contain one kind of event payload, such as a message change, group change, poll change, chat change, a completion marker, or a heartbeat. The sequence number helps both sides agree where they are in the event timeline, so a client can resume without missing updates.

The file also imports the generated message definitions for chats, groups, messages, polls, and streaming heartbeats, because this event service refers to those types. At import time, it registers the message and service descriptors with the protobuf runtime. Other code can then create, serialize, deserialize, and inspect these event messages without manually parsing bytes.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is produced from a Protocol Buffers service definition, so it is not meant to be edited by hand. Protocol Buffers define structured messages, and gRPC is the network system that sends those messages between programs. Here, the service is about “catching up” on durable iMessage-related events: messages, group changes, polls, and chats. A client can ask for everything newer than a known sequence number, receive a finite stream of events, and then move to live subscription streams without missing anything.

The file provides three main pieces. EventServiceStub is the client-side handle: it turns a Python request object into bytes, sends it to the remote service, and turns the streamed byte responses back into Python objects. EventServiceServicer is the server-side base class: real server code subclasses it and fills in the actual CatchUpEvents behavior. add_EventServiceServicer_to_server connects that implementation to a running gRPC server, like registering a phone extension so calls reach the right desk. EventService is an experimental shortcut API for making the same call without first building a stub object.

At import time, the file also checks that the installed grpc package is new enough for the generated code. Without this file, Python clients and servers would not have the standard gRPC glue needed to call or publish this service.

#### Function details

##### `EventServiceStub.__init__`  (lines 45–55)

```
def __init__(self, channel)
```

**Purpose**: This builds the client-side object used to call CatchUpEvents on a remote EventService server. Someone uses it when their code already has a gRPC channel, which is the network connection path to the server.

**Data flow**: It receives a channel as input. It asks that channel to create a unary-to-stream call: one request goes in, many responses can come back. It attaches the right converters so CatchUpEventsRequest objects become bytes before sending, and response bytes become CatchUpEventsResponse objects after receiving. The result is a stub instance with a CatchUpEvents callable ready to use.

**Call relations**: Client code creates EventServiceStub when it wants to talk to the service through a normal gRPC channel. Later, when the client calls stub.CatchUpEvents, the callable set up here sends the request to the server method registered under the EventService/CatchUpEvents path.


##### `EventServiceServicer.CatchUpEvents`  (lines 75–79)

```
def CatchUpEvents(self, request, context)
```

**Purpose**: This is the placeholder server method for CatchUpEvents. It exists so real server code has a clear method to override with the actual event replay logic.

**Data flow**: It receives a request and a gRPC context, which carries information about the current network call and can be used to set an error status. Because this base version has no real behavior, it marks the call as UNIMPLEMENTED, adds the message 'Method not implemented!', and raises NotImplementedError. Nothing useful is streamed back unless a subclass replaces this method.

**Call relations**: The registration helper connects a servicer’s CatchUpEvents method to the gRPC server. If the project registers this base class directly, callers get an unimplemented error. In normal use, application code provides a subclass whose CatchUpEvents method is called instead.


##### `add_EventServiceServicer_to_server`  (lines 82–93)

```
def add_EventServiceServicer_to_server(servicer, server)
```

**Purpose**: This registers an EventService server implementation with a gRPC server so remote clients can call it. Without this step, the server might be running, but it would not know which Python method should answer CatchUpEvents requests.

**Data flow**: It receives a servicer object and a server object. It builds a routing table that says the CatchUpEvents RPC should call servicer.CatchUpEvents, while using the generated message code to decode incoming request bytes and encode outgoing response objects. It then adds that routing table to the gRPC server, changing the server so it can accept EventService calls.

**Call relations**: Server setup code calls this during startup after creating the real servicer. Inside, it asks gRPC to make a unary-stream method handler and then wraps that in a generic service handler for photon.imessage.v1.EventService. Once registered, incoming CatchUpEvents network requests are handed to the servicer method.

*Call graph*: 2 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler).


##### `EventService.CatchUpEvents`  (lines 115–139)

```
def CatchUpEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This is an experimental convenience function for calling CatchUpEvents directly. It is useful when code wants to make the RPC with a target address and call options instead of first creating an EventServiceStub.

**Data flow**: It receives a request, a target server address, and optional settings such as credentials, timeout, compression, metadata, and whether to use an insecure connection. It passes all of that to gRPC’s experimental unary-stream helper, along with the service path and the generated request and response converters. The result is a stream-like RPC response that yields CatchUpEventsResponse objects from the remote server.

**Call relations**: Client code may call this static method as a shortcut. It hands the actual network work to grpc.experimental.unary_stream, using the same service path and message serialization rules as the regular stub, so it reaches the same server method registered by add_EventServiceServicer_to_server.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/group_types_pb2.py`

`generated` · `cross-cutting data serialization`

This file is machine-made from a Protocol Buffers definition file, so humans are not meant to edit it directly. Its job is to give the Python codebase a shared vocabulary for iMessage group events. Without it, different parts of the system would not have a reliable, agreed-on way to represent events like “Alice was added to this chat” or “the group display name changed.”

The file starts by checking that the installed Protocol Buffers library is the expected version. It then imports two pieces it depends on: Google’s timestamp type for event times, and an address type used to identify iMessage participants. After that, it loads a serialized schema into Protocol Buffers’ descriptor system. A descriptor is like a blueprint: it tells Python what fields each message has and how they fit together.

From that blueprint, Protocol Buffers builds message classes such as `GroupDisplayNameChanged`, `GroupParticipantAdded`, and `GroupChangeEvent`. The central message is `GroupChangeEvent`, which records the chat identifier, when the change happened, who caused it if known, whether it came from the local user, and exactly one specific kind of change. The “exactly one” part is implemented as a Protocol Buffers `oneof`, meaning the event is one of several possible change types, not all of them at once.


### Message streaming APIs
This group defines the generated message service surface, its gRPC wiring, and the message, poll, and heartbeat payload types used by live message flows.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2.py`

`generated` · `import time and during API request/response serialization`

This file is machine-made from a Protocol Buffers definition, which is a language-neutral way to describe data and remote API calls. Think of it like a printed form template: every caller and server agrees on the same boxes, names, and allowed contents, so messages do not get misread in transit.

The file registers many data types used by the iMessage extension. These include requests for sending plain text, attachments, multipart messages, mini-app cards, edits, unsends, reactions, stickers, silenced-message notifications, message lookup, message listing, embedded media lookup, and event subscriptions. It also defines response shapes, such as a returned Message, pagination tokens for list results, and streaming events with either message changes or heartbeat keep-alive signals.

It also describes the MessageService remote procedure call interface. A remote procedure call, or RPC, is a function call that crosses a process or network boundary. The service includes operations such as SendTextMessage, GetMessage, ListRecentMessages, and SubscribeMessageEvents. Some methods are annotated with HTTP paths like `/v1/messages:sendText`, so the same contract can guide HTTP/JSON gateways as well as protobuf-based clients.

Because this is generated code, developers normally should not edit it by hand. If it were missing or out of date, Python clients and servers would disagree about what message fields exist or how to encode them, causing API calls to fail or data to be interpreted incorrectly.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is like the phone book and switchboard for the message service. The real message rules live elsewhere, but this file defines how Python code talks to that service over gRPC, a remote procedure call system that lets one program call another program over the network as if it were calling a local function. At import time it first checks that the installed grpc package is new enough for the generated code. If the versions do not match, it stops early with a clear error instead of failing later in confusing ways.

There are three main pieces. MessageServiceStub is for clients: given a network channel, it creates methods such as SendTextMessage and GetMessage that know the exact service path and how to turn request and response objects into bytes and back. MessageServiceServicer is for servers: it is a base class with every service method present, but each one returns “not implemented” until a real server subclass supplies the actual behavior. add_MessageServiceServicer_to_server connects such a servicer to a running gRPC server. Finally, MessageService offers an experimental shortcut style for making one-off calls without manually creating a stub. Without this file, client and server code would not agree on the method names, paths, or message formats.

#### Function details

##### `MessageServiceStub.__init__`  (lines 46–126)

```
def __init__(self, channel)
```

**Purpose**: Builds the client-side object used to call the remote message service. After this runs, the stub has one callable attribute for each service operation, such as sending a text message, listing messages, or subscribing to live events.

**Data flow**: It takes a gRPC channel, which is the network connection to a server. It attaches methods to the stub, and each method knows the remote path, how to serialize the request object into bytes, and how to deserialize the response bytes back into the right protobuf object. The result is the same stub object, now ready for client calls.

**Call relations**: Client code creates this stub when it has a channel to a message-service server. Later, when the client calls one of the attached methods, gRPC uses the paths and serializers registered here to send the request to the matching server method.


##### `MessageServiceServicer.SendTextMessage`  (lines 146–159)

```
def SendTextMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a plain text message. In this generated base class it is only a placeholder; real servers must override it.

**Data flow**: It receives a send-text request and a gRPC context object. Instead of sending anything, it marks the response as UNIMPLEMENTED, adds a short error detail, and raises NotImplementedError.

**Call relations**: When a server registers a servicer, the registration function points the SendTextMessage RPC at this method. A production servicer subclass is expected to replace this placeholder so incoming client calls do real work.


##### `MessageServiceServicer.SendAttachmentMessage`  (lines 161–165)

```
def SendAttachmentMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a message that contains an attachment. Here it exists only to state the expected method shape for real implementations.

**Data flow**: It receives an attachment-message request and the call context. It changes the context to say the method is not implemented, then raises an error instead of returning a message response.

**Call relations**: The server registration wiring can route SendAttachmentMessage calls here. Real application code should override it before exposing the service.


##### `MessageServiceServicer.SendMultipartMessage`  (lines 167–171)

```
def SendMultipartMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a message made of multiple parts. The generated version is a placeholder.

**Data flow**: It accepts the multipart send request and context, sets an UNIMPLEMENTED status on the context, and raises NotImplementedError. No message is created.

**Call relations**: Incoming multipart-send RPCs are wired to this method unless a subclass supplies a real implementation.


##### `MessageServiceServicer.SendCustomizedMiniAppMessage`  (lines 173–178)

```
def SendCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a custom iMessage mini-app card. In this base class, it deliberately does not implement the action.

**Data flow**: It receives the mini-app send request and call context. It records that the method is unimplemented and raises an error, producing no successful response.

**Call relations**: Server setup can register this method as the receiver for the matching RPC. A concrete servicer must override it to actually send mini-app messages.


##### `MessageServiceServicer.UpdateCustomizedMiniAppMessage`  (lines 180–185)

```
def UpdateCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for updating an existing custom iMessage mini-app card. The generated method is only a stub for real server code.

**Data flow**: It receives an update request and the call context. It sets the gRPC status to UNIMPLEMENTED and raises NotImplementedError, so nothing is updated.

**Call relations**: The registration function can connect the network method to this Python method. In a working server, a subclass provides the real update behavior.


##### `MessageServiceServicer.EditMessage`  (lines 187–191)

```
def EditMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for editing an existing message. This base implementation only reports that the feature has not been supplied.

**Data flow**: It takes an edit request and context, writes an UNIMPLEMENTED status and detail into the context, then raises NotImplementedError. No edited message snapshot is returned.

**Call relations**: Registered servers route EditMessage calls to the servicer method. Actual message-edit logic belongs in an overriding subclass.


##### `MessageServiceServicer.UnsendMessage`  (lines 193–198)

```
def UnsendMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for retracting, or unsending, a message. The base class does not perform the retraction.

**Data flow**: It receives an unsend request and context. It marks the call as unimplemented and raises an error rather than returning an empty success response.

**Call relations**: The server wiring maps the UnsendMessage RPC to this method. A real servicer overrides it to perform the unsend action and return success.


##### `MessageServiceServicer.SetReaction`  (lines 200–204)

```
def SetReaction(self, request, context)
```

**Purpose**: Defines the server-side slot for adding, changing, or removing a reaction on a message. The generated version is a placeholder.

**Data flow**: It receives a reaction request and context. It sets the call status to UNIMPLEMENTED and raises NotImplementedError, leaving the message unchanged.

**Call relations**: The registration function can expose this method to clients. Real reaction behavior must come from a subclass.


##### `MessageServiceServicer.PlaceSticker`  (lines 206–210)

```
def PlaceSticker(self, request, context)
```

**Purpose**: Defines the server-side slot for placing a sticker on a message. This base method only signals that no implementation is present.

**Data flow**: It takes a sticker-placement request and context, marks the call as unimplemented, and raises an error. It returns no updated message response.

**Call relations**: Incoming PlaceSticker calls are routed to the registered servicer. A concrete servicer overrides this method to apply stickers.


##### `MessageServiceServicer.NotifySilencedMessage`  (lines 212–217)

```
def NotifySilencedMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for triggering Apple's per-message “Notify Anyway” action. The generated base class does not trigger anything itself.

**Data flow**: It receives a notify request and context. It records an UNIMPLEMENTED status, adds a detail message, and raises NotImplementedError instead of returning empty success.

**Call relations**: The RPC server can route NotifySilencedMessage calls here. A real implementation must replace it to perform the notification action.


##### `MessageServiceServicer.GetMessage`  (lines 219–224)

```
def GetMessage(self, request, context)
```

**Purpose**: Defines the server-side slot for reading one message. In this base file it only provides the method signature required by gRPC.

**Data flow**: It receives a get-message request and context. It sets the context to UNIMPLEMENTED and raises NotImplementedError, so no message data is returned.

**Call relations**: Server registration connects the GetMessage RPC to this method. Production code overrides it to look up and return the requested message.


##### `MessageServiceServicer.ListRecentMessages`  (lines 226–233)

```
def ListRecentMessages(self, request, context)
```

**Purpose**: Defines the server-side slot for listing recent messages, optionally filtered or paged. The generated implementation is only a placeholder.

**Data flow**: It receives a list request and context. It marks the call as unimplemented and raises NotImplementedError, returning no list.

**Call relations**: The registration function can route ListRecentMessages requests to this method. A concrete server supplies the actual query behavior.


##### `MessageServiceServicer.ListChatMessages`  (lines 235–240)

```
def ListChatMessages(self, request, context)
```

**Purpose**: Defines the server-side slot for listing messages in one chat. This base method does not read any chat history.

**Data flow**: It accepts a chat-message list request and context, sets UNIMPLEMENTED on the context, and raises NotImplementedError. No messages are returned.

**Call relations**: Registered servers expose this method under the ListChatMessages RPC name. Real chat-history lookup must be implemented by a subclass.


##### `MessageServiceServicer.GetEmbeddedMedia`  (lines 242–250)

```
def GetEmbeddedMedia(self, request, context)
```

**Purpose**: Defines the server-side slot for fetching embedded media bytes from a message. The generated base class does not fetch the media.

**Data flow**: It receives a media request and context. It marks the call as unimplemented and raises NotImplementedError, returning no media response.

**Call relations**: The server wiring can connect GetEmbeddedMedia network calls to this method. A real servicer overrides it to retrieve and return the requested bytes.


##### `MessageServiceServicer.SubscribeMessageEvents`  (lines 252–258)

```
def SubscribeMessageEvents(self, request, context)
```

**Purpose**: Defines the server-side slot for a live stream of message events. The base method does not produce any events.

**Data flow**: It receives a subscription request and context. Instead of opening a stream, it sets the call status to UNIMPLEMENTED and raises NotImplementedError.

**Call relations**: The registration function treats this as a server-streaming RPC, meaning one request can produce many responses. A real servicer must override it to yield live message events.


##### `add_MessageServiceServicer_to_server`  (lines 261–342)

```
def add_MessageServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a MessageServiceServicer implementation to a gRPC server so clients can call it over the network. This is the server-side switchboard setup.

**Data flow**: It takes a servicer object and a gRPC server. It builds a table that maps each public RPC name to the matching Python method, along with the correct request parser and response serializer. It then creates a generic gRPC handler and adds both generic and registered method handlers to the server.

**Call relations**: Server startup code calls this after creating a real servicer. Inside, it uses gRPC helper functions for ordinary request-response calls and for the SubscribeMessageEvents streaming call, then hands the completed routing table to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `MessageService.SendTextMessage`  (lines 364–388)

```
def SendTextMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to send a text message without first building a stub object. It is a convenience wrapper around the gRPC call machinery.

**Data flow**: It receives a send-text request, a target server address, and optional connection settings such as credentials, timeout, and metadata. It serializes the request, sends it to the SendTextMessage RPC path, and deserializes the returned bytes into a MessageResponse.

**Call relations**: Client code may call this static method directly. It hands the actual network work to gRPC’s experimental unary-unary helper, meaning one request is expected to produce one response.


##### `MessageService.SendAttachmentMessage`  (lines 391–415)

```
def SendAttachmentMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to send an attachment message. It saves callers from manually creating a channel and stub for this single operation.

**Data flow**: It takes an attachment-message request, the target server, and optional call settings. It turns the request into bytes, calls the matching remote path, and turns the response bytes into a MessageResponse.

**Call relations**: This is used from client-side code that wants the shortcut API. It delegates the network exchange to gRPC’s experimental unary-unary helper.


##### `MessageService.SendMultipartMessage`  (lines 418–442)

```
def SendMultipartMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to send a multipart message. It is a shortcut for a single request-response RPC.

**Data flow**: It receives the multipart request plus target and optional connection options. It serializes the request, sends it to the SendMultipartMessage service path, and returns the decoded MessageResponse.

**Call relations**: Client code can use this instead of MessageServiceStub. The method passes the work to the gRPC experimental unary-unary call helper.


##### `MessageService.SendCustomizedMiniAppMessage`  (lines 445–469)

```
def SendCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to send a custom mini-app iMessage card. It wraps the exact gRPC path and message formats for that operation.

**Data flow**: It takes a mini-app send request, a target server, and optional call settings. It converts the request to bytes, sends it over gRPC, and converts the reply into a MessageResponse.

**Call relations**: This client-side shortcut hands the actual remote call to gRPC’s experimental unary-unary helper.


##### `MessageService.UpdateCustomizedMiniAppMessage`  (lines 472–496)

```
def UpdateCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to update a custom mini-app iMessage card. It packages the update request for the correct remote method.

**Data flow**: It receives an update request, server target, and optional settings. It serializes the request, sends it to the UpdateCustomizedMiniAppMessage path, and returns a decoded MessageResponse.

**Call relations**: Client code may use this static method for a one-off call. It relies on gRPC’s experimental unary-unary helper for the network communication.


##### `MessageService.EditMessage`  (lines 499–523)

```
def EditMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to edit an existing message. It is a convenience path for one request and one response.

**Data flow**: It takes an edit request, target server, and optional call options. It sends the serialized request to the EditMessage RPC and returns the decoded MessageResponse.

**Call relations**: This method is part of the experimental shortcut client API. It hands off the real sending and receiving to gRPC’s unary-unary helper.


##### `MessageService.UnsendMessage`  (lines 526–550)

```
def UnsendMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to retract a message. On success, the expected reply is an empty protobuf message, which means “done” without extra data.

**Data flow**: It receives an unsend request, target, and optional settings. It serializes the request, calls the UnsendMessage RPC path, and deserializes the response as google.protobuf.Empty.

**Call relations**: Client code can call this shortcut directly. It delegates the single request-response exchange to gRPC’s experimental unary-unary helper.


##### `MessageService.SetReaction`  (lines 553–577)

```
def SetReaction(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to set a reaction on a message. It returns the refreshed message response expected from that operation.

**Data flow**: It takes a reaction request, target server, and optional call settings. It serializes the request, sends it to the SetReaction RPC, and decodes the response into a MessageResponse.

**Call relations**: This is a client-side convenience method. It passes the remote-call details to gRPC’s experimental unary-unary helper.


##### `MessageService.PlaceSticker`  (lines 580–604)

```
def PlaceSticker(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to place a sticker on a message. It wraps the method path and protobuf conversion details.

**Data flow**: It receives a sticker-placement request, target, and optional settings. It turns the request into bytes, calls the PlaceSticker RPC path, and returns a decoded MessageResponse.

**Call relations**: Client code can use this static method as a shortcut. It relies on gRPC’s experimental unary-unary helper to perform the network call.


##### `MessageService.NotifySilencedMessage`  (lines 607–631)

```
def NotifySilencedMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to trigger “Notify Anyway” for a silenced message. A successful response carries no extra data.

**Data flow**: It takes a notify request, a target server, and optional call options. It serializes the request, sends it to the NotifySilencedMessage path, and decodes the reply as an empty protobuf message.

**Call relations**: This shortcut method is called by client code that wants to invoke the operation directly. It hands the one-request, one-response exchange to gRPC’s experimental helper.


##### `MessageService.GetMessage`  (lines 634–658)

```
def GetMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to fetch one message. It is useful when a caller wants a quick lookup without constructing a stub.

**Data flow**: It receives a get-message request, target server, and optional settings. It serializes the request, calls the GetMessage RPC path, and returns a decoded GetMessageResponse.

**Call relations**: Client-side code may call this static shortcut. It delegates to gRPC’s experimental unary-unary helper.


##### `MessageService.ListRecentMessages`  (lines 661–685)

```
def ListRecentMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to list recent messages. It wraps the remote method name and response type for callers.

**Data flow**: It takes a recent-messages list request, target, and optional connection settings. It sends the serialized request to the ListRecentMessages RPC and decodes the response into a ListRecentMessagesResponse.

**Call relations**: This is part of the experimental direct-call API for clients. It hands the network request to gRPC’s unary-unary helper.


##### `MessageService.ListChatMessages`  (lines 688–712)

```
def ListChatMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to list messages from a specific chat. It is a shortcut for the matching gRPC request-response method.

**Data flow**: It receives a chat-message list request, target server, and optional call options. It serializes the request, calls the ListChatMessages RPC path, and returns a decoded ListChatMessagesResponse.

**Call relations**: Client code may choose this instead of using a stub. It delegates the call to gRPC’s experimental unary-unary helper.


##### `MessageService.GetEmbeddedMedia`  (lines 715–739)

```
def GetEmbeddedMedia(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a direct experimental client call to fetch embedded media data related to a message. It wraps the protobuf request and response conversion for that lookup.

**Data flow**: It takes a media request, target server, and optional settings. It serializes the request, sends it to the GetEmbeddedMedia RPC path, and decodes the response into a GetEmbeddedMediaResponse.

**Call relations**: This client-side shortcut uses gRPC’s experimental unary-unary helper to perform the one request and one response exchange.


##### `MessageService.SubscribeMessageEvents`  (lines 742–766)

```
def SubscribeMessageEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Starts a direct experimental client subscription to live message events. Unlike the other shortcut methods, one request can produce a stream of many event responses.

**Data flow**: It receives a subscription request, target server, and optional connection settings. It serializes the request, opens the SubscribeMessageEvents RPC, and deserializes each incoming response as a SubscribeMessageEventsResponse.

**Call relations**: Client code can use this method to join the live event stream. It hands the work to gRPC’s experimental unary-stream helper, which is built for one request followed by many server responses.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-generated from a Protocol Buffers schema, which is a language-neutral blueprint for structured data. In plain terms, it is like a set of standardized forms: every iMessage message, reaction, sticker placement, edit event, or read receipt has named fields in a known format, so different parts of the system can exchange the same information without guessing what each piece means.

The file registers message definitions with Google’s protobuf runtime. Once imported, it creates Python classes such as Message, MessageContent, MessageReaction, MessageChangeEvent, and related event types. These classes are used to build, read, serialize, and deserialize iMessage data. Serialization means turning an object into compact bytes for storage or transport; deserialization means turning those bytes back into usable Python objects.

It also connects to other generated protobuf files for shared types, such as timestamps, addresses, and attachment details. The many optional fields reflect the real variety of iMessage behavior: a message might have text, attachments, formatting, mentions, mini-app content, replies, reactions, stickers, delivery status, or edit and deletion times.

Because this file is generated, developers should not edit it by hand. If the message format needs to change, the source .proto schema should change and this file should be regenerated. Without it, Python code in this extension would not know the official shape of iMessage message data.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/poll_types_pb2.py`

`generated` · `serialization and message exchange`

This file is machine-created from a Protocol Buffer schema, which is a compact way to describe data so different parts of a system can exchange it safely. In everyday terms, it is like a set of blank forms for iMessage polls: one form for a poll option, one for a participant vote, one for the full poll state, and one for events such as creating a poll, adding an option, voting, or removing a vote.

The file does not contain hand-written business logic. Instead, it registers these message definitions with Google’s protobuf runtime so other Python code can create, serialize, deserialize, and inspect poll-related records. It imports timestamp support for event times and address types so votes and actors can be tied to a specific iMessage participant.

The most important generated message types are PollInfo, which represents the current state of a poll, and PollChangeEvent, which represents something that happened to a poll at a specific time. PollChangeEvent uses a “one of these choices” pattern: a single event is either a creation, an option addition, a vote, or an unvote, not all of them at once.

Without this file, Python code in this extension would not know the exact wire format for poll messages. That would make it unable to reliably exchange poll data with other services or stored protobuf records.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/streaming_pb2.py`

`generated` · `import time and message serialization/deserialization`

This file is not handwritten application logic. It was produced by the Protocol Buffers compiler from `photon/imessage/v1/streaming.proto`. Protocol Buffers, often called protobuf, are a way to define structured messages once and then generate code for reading and writing those messages in different programming languages.

The useful thing this file gives the rest of the project is a Python class for the `Heartbeat` message in the `photon.imessage.v1` package. A heartbeat is like a quick “I’m still here” tap on the shoulder during a long-running connection. Even though the message has no fields, its presence can still matter: sending or receiving it can tell the other side that the stream is open and responsive.

At import time, the file checks that the installed protobuf runtime is compatible with the version that generated this code. It then registers a serialized description of the message with protobuf’s global descriptor pool. A descriptor is metadata that tells protobuf what messages exist and how they should be encoded. Finally, protobuf’s internal builder creates the actual Python message class and related module-level objects.

Because this is generated code, people should not edit it directly. Changes should be made in the original `.proto` file and regenerated, otherwise edits may be overwritten or may drift away from the official message definition.


### Provider contract
This group contains the hand-written provider interface and local data models that extension implementations use to send, receive, and report iMessage-style activity.

### `extensions/imessage/ufo_ext_imessage/provider.py`

`data_model` · `cross-cutting`

This file is the boundary between the iMessage extension and the outside messaging service it talks to. It does not contain a real provider implementation. Instead, it says, in one place, what a provider must be able to do: register a phone number, replay missed events, listen for live messages, send text and files, download attachments, and explain certain errors.

The small frozen data classes act like labeled envelopes. RegisteredPhone records the assigned phone line and the conversation it belongs to. MessageAttachment describes a received file. InboundMessage describes one incoming message, including who sent it, its text, any files, and whether it was direct. ProviderEvent wraps the stream of updates from the provider; it can carry a message, a sequence number, or the provider’s current “head” position. A sequence number is a bookmark in the message stream, used so the system can resume without rereading everything.

The MessageProvider protocol is the main piece. A protocol is a Python way of saying “any object with these methods counts,” like requiring that any delivery company used by a store must support pickup, tracking, and delivery. Other parts of the extension depend on this shape instead of one specific provider. That keeps the surface layer independent from the details of a cloud service, test double, or future provider. The custom errors mark important provider states, especially when a target phone must text the assigned line first before outbound messages are allowed.

#### Function details

##### `TargetNotOptedIn.__init__`  (lines 46–48)

```
def __init__(self, assigned_phone_number: str='') -> None
```

**Purpose**: Creates an error that means the provider refused to send a message because the destination phone has not opted in yet. It can carry the assigned phone number that the person must text first.

**Data flow**: It receives an optional assigned phone number. It stores that number on the error object, then passes the same value to the base RuntimeError so normal error reporting can include it.

**Call relations**: The cloud provider raises this kind of error during phone registration when it learns that the target has not started the required conversation. Higher layers can then show or use the assigned phone number instead of treating the failure as a generic crash.

*Call graph*: called by 1 (register_phone).


##### `MessageProvider.installation_id`  (lines 53–53)

```
def installation_id(self) -> str
```

**Purpose**: Identifies the particular provider installation being used. This gives the rest of the system a stable name or id for the connected messaging backend.

**Data flow**: A provider implementation supplies this read-only text value. Callers read it when they need to know which installed provider instance they are talking to; nothing is changed by reading it.

**Call relations**: This is part of the provider contract. Any concrete provider must expose it so the extension can distinguish one provider installation from another when needed.


##### `MessageProvider.register_phone`  (lines 55–55)

```
async def register_phone(self, phone_number: str, idempotency_key: str) -> RegisteredPhone
```

**Purpose**: Asks the provider to register a member’s phone number for messaging. The idempotency key is a safety label that lets the same request be retried without accidentally creating duplicates.

**Data flow**: It takes a phone number and an idempotency key. The provider sends that request to its backend and returns a RegisteredPhone containing the assigned line and conversation id, or raises an error if registration cannot happen.

**Call relations**: Concrete providers implement this method when onboarding or linking a phone number. The rest of the extension can call it without knowing the provider’s private registration steps.


##### `MessageProvider.catch_up`  (lines 57–57)

```
def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Returns old provider events that happened after a saved sequence bookmark. This is how the extension catches up on missed messages after downtime or a restart.

**Data flow**: It receives the last sequence number the system has already processed, or no number if there is no bookmark. It produces an asynchronous stream of ProviderEvent objects, one at a time, until the backlog is covered.

**Call relations**: The iMessage surface calls this during its catch-up phase. The provider supplies past events, and the surface consumes them so local state can be brought up to date before or alongside live listening.

*Call graph*: called by 1 (_catch_up).


##### `MessageProvider.subscribe`  (lines 59–59)

```
def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Opens a live stream of new provider events. The ready event is a signal used to tell the caller when the subscription is actually listening.

**Data flow**: It receives an asyncio.Event, which is a small shared signal for asynchronous code. The provider starts listening for new messages, sets the signal when it is ready, and yields ProviderEvent objects as they arrive.

**Call relations**: The iMessage surface calls this in its live message pump. Once subscribed, the provider feeds new events to the surface so incoming messages can be processed without polling manually.

*Call graph*: called by 1 (_pump_live).


##### `MessageProvider.send_text`  (lines 61–61)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: Sends a text message into an existing conversation. The idempotency key protects against duplicate sends if the caller retries after a timeout or network problem.

**Data flow**: It receives a conversation id, the message text, and an idempotency key. The provider sends the text through its backend and returns the provider’s id for the sent message.

**Call relations**: The iMessage surface calls this when a linked member sends a plain text reply. The surface chooses what should be sent, and the provider handles the actual delivery details.

*Call graph*: called by 1 (_linked_member).


##### `MessageProvider.send_attachment`  (lines 63–69)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: Sends a file attachment into an existing conversation. It is the file-sending counterpart to sending a text message.

**Data flow**: It receives a conversation id, a filename, the file bytes, and an idempotency key. The provider uploads or sends the file and returns the provider’s id for the sent attachment message.

**Call relations**: The iMessage surface calls this when a linked member sends a file. The surface provides the file contents, while the provider performs the provider-specific upload and delivery work.

*Call graph*: called by 1 (_linked_member).


##### `MessageProvider.download_attachment`  (lines 71–71)

```
def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: Downloads a received attachment from the provider. It yields the file in chunks so large files do not need to be loaded into memory all at once.

**Data flow**: It receives an attachment id. The provider fetches that attachment and produces an asynchronous stream of byte chunks until the file is fully downloaded.

**Call relations**: The iMessage surface calls this while preparing downloaded files from inbound messages. The provider supplies the raw bytes, and the surface can then store or forward the file.

*Call graph*: called by 1 (_downloaded_files).


##### `MessageProvider.invalidate`  (lines 73–73)

```
async def invalidate(self) -> None
```

**Purpose**: Tells the provider that its current connection or cached state should no longer be trusted. This gives an implementation a chance to close sessions, clear tokens, or force a clean reconnect.

**Data flow**: It takes no extra data. The provider performs whatever cleanup or invalidation its backend needs, and the async call completes when that work is done.

**Call relations**: This is part of the shared provider contract, so orchestration code can ask any provider implementation to reset itself without knowing its internal connection details.


##### `MessageProvider.invalid_cursor`  (lines 75–75)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: Checks whether an error means the saved sequence bookmark is no longer accepted by the provider. A cursor is a position marker in a stream, like a bookmark in a book.

**Data flow**: It receives an exception object. The provider inspects that error and returns true if it means the caller’s stream position is invalid, otherwise false.

**Call relations**: This lets higher-level code react differently to a bad stream bookmark than to a normal network or service failure. Each provider knows its own error shapes, so the check lives behind this contract.


##### `MessageProvider.external_error`  (lines 77–77)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: Checks whether an error came from the outside provider service rather than from the extension’s own code. This helps the caller decide how to report or recover from failures.

**Data flow**: It receives an exception. The provider examines it and returns true when it represents a provider-side or network-side problem, otherwise false.

**Call relations**: The iMessage surface uses this while consuming provider events and while downloading files. That lets the surface treat provider outages or remote errors as expected operational problems instead of confusing them with local bugs.

*Call graph*: called by 2 (_consume_connected, _downloaded_files).


##### `MessageProvider.error_code`  (lines 79–79)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: Turns a provider error into a short text code. This gives logs, metrics, or user-facing handling a consistent label for different failure types.

**Data flow**: It receives an exception. The provider translates that exception into a provider-specific code string and returns it; the exception itself is not changed.

**Call relations**: This is part of the provider contract so higher layers can ask for a simple error label without understanding every provider’s private exception classes.
