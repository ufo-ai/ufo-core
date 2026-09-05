# iMessage generated gRPC service wiring  `stage-19.6.5`

This stage is shared behind-the-scenes support for the iMessage extension. It is not the code that decides what to do with messages or chats. Instead, it is the network “plumbing” that lets one part of the system call another part over gRPC, a system for making function calls across a network as if they were local calls.

The attachment service wiring provides the ready-made client calls and server connection points for attachment operations. The chat service wiring does the same for chat-related actions. The event service wiring focuses on fetching a stream of older events, so a client can catch up before listening for new live events. The message service wiring connects message-related remote calls to the real code that sends, reads, or manages messages.

These files are generated from service definitions, so developers normally do not edit them by hand. Like labeled sockets in a switchboard, they make sure clients and servers agree on which calls exist and how data moves between them.

## Files in this stage

### Attachment and chat wiring
Generated gRPC bindings for attachment operations and broader chat-related remote procedures.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2_grpc.py`

`generated` · `server startup and request handling`

This file is machine-generated from a protobuf service definition. Protobuf is a way to define network messages and services in a language-neutral format, and gRPC is the remote-call system that sends those messages between programs. The real-world problem here is moving iMessage attachment information and attachment bytes between a client and a server without inventing custom network plumbing by hand.

The file provides three main pieces. `AttachmentServiceStub` is the client-side helper: given a gRPC channel, it creates callable methods for getting attachment metadata, uploading an attachment, and downloading an attachment. `AttachmentServiceServicer` is the server-side base class: it names the methods a real server must implement, but by default each method says “not implemented.” `add_AttachmentServiceServicer_to_server` connects a real servicer object to a gRPC server so incoming network requests reach the right Python method.

There is also an experimental `AttachmentService` class with static convenience methods for making one-off calls without first creating a stub object. The important detail is that this file does not decide where attachments live or how they are stored. It is more like the standardized plug and socket: it makes sure both sides agree on method names, message conversion, and whether a call returns one response or a stream of responses.

#### Function details

##### `AttachmentServiceStub.__init__`  (lines 37–57)

```
def __init__(self, channel)
```

**Purpose**: Creates a client-side object with ready-to-use methods for calling the attachment service over a gRPC channel. A caller uses this when it wants to talk to a remote attachment server.

**Data flow**: It receives a gRPC channel, which is the network connection to a server. It attaches three callable attributes to the stub: one for metadata lookup, one for upload, and one for download. Each callable knows how to turn the request object into bytes before sending it and how to turn the reply bytes back into the right response object.

**Call relations**: Client code creates this stub before making attachment requests. After that, calls such as `GetAttachmentInfo`, `UploadAttachment`, and `DownloadAttachment` go through the gRPC channel using the service paths defined here.


##### `AttachmentServiceServicer.GetAttachmentInfo`  (lines 69–74)

```
def GetAttachmentInfo(self, request, context)
```

**Purpose**: Defines the server-side method name for looking up attachment metadata, but does not provide the real behavior. A real server is expected to override it.

**Data flow**: It receives a request and a gRPC context, which carries details about the current network call. Because this base version has no real implementation, it marks the call as unimplemented, adds a short explanation, and raises an error instead of returning attachment information.

**Call relations**: This method is the placeholder that `add_AttachmentServiceServicer_to_server` can connect to the gRPC server. In a working service, project code subclasses or replaces this method so incoming metadata requests produce real responses.


##### `AttachmentServiceServicer.UploadAttachment`  (lines 76–87)

```
def UploadAttachment(self, request, context)
```

**Purpose**: Defines the server-side method name for uploading an attachment, but leaves the actual saving work to a real implementation. The service contract says the upload should be all-or-nothing: either all parts are saved, or none become visible.

**Data flow**: It receives an upload request and the gRPC context for that call. In this generated base class, it does not inspect or store the attachment data; it marks the call as unimplemented and raises an error.

**Call relations**: This placeholder is wired into the server by `add_AttachmentServiceServicer_to_server`. A real attachment server supplies its own version so upload requests arriving over gRPC are persisted and answered.


##### `AttachmentServiceServicer.DownloadAttachment`  (lines 89–95)

```
def DownloadAttachment(self, request, context)
```

**Purpose**: Defines the server-side method name for downloading attachment bytes as a stream, but does not provide the actual bytes. A stream means the server can send the file in pieces instead of one giant response.

**Data flow**: It receives a download request and the call context. The base version immediately marks the operation as unimplemented and raises an error, so no download chunks are produced.

**Call relations**: This is the method that server registration connects to the gRPC download endpoint. In a real server, it is overridden so the gRPC layer can ask for an attachment and receive a sequence of response frames.


##### `add_AttachmentServiceServicer_to_server`  (lines 98–119)

```
def add_AttachmentServiceServicer_to_server(servicer, server)
```

**Purpose**: Registers a server-side attachment service implementation with a gRPC server. Without this, the server would not know which Python methods should answer the attachment service’s network calls.

**Data flow**: It receives a `servicer`, which is the object containing the actual service methods, and a `server`, which is the running gRPC server being configured. It builds method handlers for metadata lookup, upload, and download, including how to decode incoming request bytes and encode outgoing response objects. It then adds those handlers to the server under the attachment service name.

**Call relations**: Application startup code calls this when setting up the gRPC server. Inside, it uses gRPC helper functions such as `grpc.unary_unary_rpc_method_handler`, `grpc.unary_stream_rpc_method_handler`, and `grpc.method_handlers_generic_handler` to turn the servicer’s Python methods into network-call handlers.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `AttachmentService.GetAttachmentInfo`  (lines 133–157)

```
def GetAttachmentInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to fetch attachment metadata from a target server. It is an alternative to creating an `AttachmentServiceStub` first.

**Data flow**: It takes a metadata request, a target server address, and optional call settings such as credentials, timeout, compression, and metadata. It serializes the request, sends a single request expecting a single response, then deserializes the response into the proper metadata response object.

**Call relations**: Client code may call this static method directly when it wants a shortcut-style gRPC request. It hands the actual network work to `grpc.experimental.unary_unary`, using the same service path and message formats as the normal stub.


##### `AttachmentService.UploadAttachment`  (lines 160–184)

```
def UploadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to upload an attachment to a target server. It packages the upload request for gRPC and expects one final response.

**Data flow**: It receives an upload request, the server target, and optional settings for security, timing, compression, and metadata. It turns the request object into bytes, sends it as a single gRPC request, and converts the returned bytes into an upload response object.

**Call relations**: Client code can use this static method as a direct shortcut instead of building a stub. The method delegates the network call to `grpc.experimental.unary_unary` and uses the generated protobuf serializers so the server receives the expected upload message.


##### `AttachmentService.DownloadAttachment`  (lines 187–211)

```
def DownloadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to download an attachment from a target server as a stream of response pieces. This is useful for large files because they do not need to arrive all at once.

**Data flow**: It receives a download request, a target server address, and optional gRPC call settings. It serializes the request, starts a gRPC call that expects many response messages, and deserializes each incoming response frame into the generated download response type.

**Call relations**: Client code may call this directly for shortcut-style downloads. It hands off to `grpc.experimental.unary_stream`, matching the server-side streaming method registered by `add_AttachmentServiceServicer_to_server`.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2_grpc.py`

`generated` · `request handling`

This file is like a phonebook and switchboard for chat operations over gRPC, a system for calling functions across a network as if they were local. It is generated from a protocol definition, so humans normally should not edit it directly. Without it, Python clients would not know the exact network names, message formats, or streaming style for chat calls such as creating a chat, marking it read, setting a background, or subscribing to chat events.

The file has three main pieces. ChatServiceStub is for clients that already have a gRPC channel, which is the connection to a server. Its constructor creates callable attributes for each remote chat method and connects each one to the right request and response message type. ChatServiceServicer is the base class for servers. It lists the methods a real server must override; by default each method says “not implemented” so missing server code fails clearly. add_ChatServiceServicer_to_server attaches a real servicer to a gRPC server and tells gRPC how to turn incoming bytes into request objects and outgoing response objects back into bytes.

The final ChatService class offers an experimental shortcut API for making one-off calls without first building a stub object. A version check at import time also protects the program from using generated code with an older incompatible grpc package.

#### Function details

##### `ChatServiceStub.__init__`  (lines 37–92)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side helper for calling the remote ChatService methods. Someone uses this when they have a gRPC channel to a server and want normal Python callables for chat operations.

**Data flow**: It receives a channel, which is the active connection to a gRPC server. It asks that channel to create method call objects for each chat operation, pairing each network path with the right way to serialize the request and deserialize the response. After construction, the stub object has attributes such as CreateChat, GetChat, and SubscribeChatEvents that send correctly formatted remote calls.

**Call relations**: Client code creates this stub before making chat RPC calls. Each attribute it creates hands requests to the gRPC channel, which then sends them to a server registered under the matching ChatService method names.


##### `ChatServiceServicer.CreateChat`  (lines 103–108)

```
def CreateChat(self, request, context)
```

**Purpose**: Defines the server-side method slot for creating a chat. In this generated base class it is only a placeholder; a real server must override it.

**Data flow**: It receives a CreateChat request and a gRPC context object that carries call status information. Since no real behavior is supplied here, it marks the call as unimplemented and raises an error. Nothing useful is returned.

**Call relations**: When add_ChatServiceServicer_to_server registers a concrete servicer, gRPC may call this method for incoming CreateChat requests. If the application did not override it, the caller gets a clear “not implemented” failure.


##### `ChatServiceServicer.MarkChatRead`  (lines 110–114)

```
def MarkChatRead(self, request, context)
```

**Purpose**: Defines the server-side method slot for marking a chat as read. This generated version is a placeholder that reminds implementers to provide the actual behavior.

**Data flow**: It receives a MarkChatRead request and the call context. It sets the gRPC status to unimplemented, records a short error message, and raises NotImplementedError. It does not change any chat state by itself.

**Call relations**: The server registration function connects the MarkChatRead network method to this servicer method. A real service subclass is expected to replace it so client calls from ChatServiceStub or ChatService.MarkChatRead can succeed.


##### `ChatServiceServicer.SetBackground`  (lines 116–122)

```
def SetBackground(self, request, context)
```

**Purpose**: Defines the server-side method slot for setting a chat background image or data. The base implementation does not actually store anything; it only reports that the method is missing.

**Data flow**: It receives a SetBackground request and call context. Instead of using the request data, it marks the RPC as unimplemented and raises an error. No response value or persistent change is produced here.

**Call relations**: Incoming SetBackground calls are routed here by the server wiring unless an application subclass overrides it. Client-side helpers serialize the request bytes before gRPC delivers them to this server method.


##### `ChatServiceServicer.RemoveBackground`  (lines 124–128)

```
def RemoveBackground(self, request, context)
```

**Purpose**: Defines the server-side method slot for removing a chat background. This generated method is only a stub for real server code.

**Data flow**: It receives a RemoveBackground request and context. It ignores the request, sets the call status to unimplemented, and raises NotImplementedError. It does not remove any background on its own.

**Call relations**: add_ChatServiceServicer_to_server maps the RemoveBackground RPC name to this method. A real servicer must override it so callers using ChatServiceStub.RemoveBackground or the experimental shortcut get a successful empty response.


##### `ChatServiceServicer.ShareContactInfo`  (lines 130–137)

```
def ShareContactInfo(self, request, context)
```

**Purpose**: Defines the server-side method slot for sharing the local user's contact card into a chat. The generated base version is deliberately nonfunctional until real service code supplies the work.

**Data flow**: It receives a ShareContactInfo request and a gRPC context. It does not push any contact information; it sets an unimplemented status and raises an error. The only visible result is a failed RPC if not overridden.

**Call relations**: The registration function routes incoming ShareContactInfo calls to this method. Client helpers can call the remote method, but success depends on the server using a subclass that replaces this placeholder.


##### `ChatServiceServicer.SetTyping`  (lines 139–144)

```
def SetTyping(self, request, context)
```

**Purpose**: Defines the server-side method slot for sending a temporary typing indicator. This base method exists only to show the expected method shape.

**Data flow**: It receives a SetTyping request and context. It does not broadcast or save a typing state; instead it marks the RPC as unimplemented and raises NotImplementedError. There is no normal response from this placeholder.

**Call relations**: Server wiring connects the SetTyping RPC name to this method. A real implementation must override it so client calls can send transient typing updates successfully.


##### `ChatServiceServicer.GetChat`  (lines 146–152)

```
def GetChat(self, request, context)
```

**Purpose**: Defines the server-side method slot for reading one chat. The generated version is a placeholder and does not fetch chat data.

**Data flow**: It receives a GetChat request and context. It sets the context status to unimplemented, adds an error detail message, and raises NotImplementedError. No chat response is created here.

**Call relations**: Incoming GetChat calls are decoded and routed here by the registration setup. Client stubs expect a GetChatResponse, but they only receive one if a real servicer overrides this method.


##### `ChatServiceServicer.GetChatCount`  (lines 154–158)

```
def GetChatCount(self, request, context)
```

**Purpose**: Defines the server-side method slot for counting chats. The base generated method only signals that actual counting logic has not been provided.

**Data flow**: It receives a GetChatCount request and context. It ignores the request, marks the call as unimplemented, and raises an error. It returns no count.

**Call relations**: The server registration connects the GetChatCount RPC to this method. Application server code should override it so client calls receive a GetChatCountResponse.


##### `ChatServiceServicer.HasBackground`  (lines 160–164)

```
def HasBackground(self, request, context)
```

**Purpose**: Defines the server-side method slot for checking whether a chat has a background. This generated method is not the real checker.

**Data flow**: It receives a HasBackground request and context. It does not inspect stored chat information; it sets an unimplemented status and raises NotImplementedError. No yes-or-no response is produced.

**Call relations**: Incoming HasBackground requests reach this method through the gRPC server handlers. A concrete server implementation must replace it for the client-side HasBackground helpers to work.


##### `ChatServiceServicer.SubscribeChatEvents`  (lines 166–170)

```
def SubscribeChatEvents(self, request, context)
```

**Purpose**: Defines the server-side method slot for subscribing to a stream of chat events. In the base class it does not stream anything and only reports that the method is missing.

**Data flow**: It receives a SubscribeChatEvents request and context. Instead of yielding event responses over time, it marks the call as unimplemented and raises an error. No event stream is produced.

**Call relations**: The server registration treats this as a unary-to-stream RPC, meaning one request can lead to many responses. A real servicer must override this method so clients using SubscribeChatEvents can receive ongoing chat updates.


##### `add_ChatServiceServicer_to_server`  (lines 173–229)

```
def add_ChatServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a ChatService server implementation to a gRPC server. This is the bridge that tells gRPC which Python method should run for each incoming chat RPC.

**Data flow**: It receives a servicer object and a server object. It builds a table of method handlers, giving each handler the servicer method to call plus the correct request decoder and response encoder. It then registers that table with the gRPC server, so future network requests can be routed to the servicer.

**Call relations**: Server startup code calls this after creating a concrete ChatServiceServicer subclass. Inside, it relies on gRPC helper functions such as unary_unary_rpc_method_handler, unary_stream_rpc_method_handler, and method_handlers_generic_handler to package the routing rules for the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `ChatService.CreateChat`  (lines 242–266)

```
def CreateChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for the CreateChat RPC. It lets code send a create-chat request to a target server without first constructing a ChatServiceStub.

**Data flow**: It receives a CreateChat request, a target server address, and optional call settings such as credentials, timeout, metadata, and compression. It serializes the request, sends a unary request expecting one response, and turns the returned bytes into a CreateChatResponse. The result is the server's create-chat response.

**Call relations**: Client code may call this static method directly for a quick RPC. It hands the work to grpc.experimental.unary_unary, using the same network path and message formats that ChatServiceStub.__init__ sets up for the regular stub.


##### `ChatService.MarkChatRead`  (lines 269–293)

```
def MarkChatRead(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for marking a chat as read. It is useful for direct client calls when creating a full stub object is unnecessary.

**Data flow**: It receives a MarkChatRead request, a target server, and optional call settings. It serializes the request, sends it as a single request expecting a single response, and decodes the response as an empty success message. The meaningful outcome is whether the RPC succeeds or fails.

**Call relations**: This static method delegates the network work to grpc.experimental.unary_unary. On the server side, the request is routed to the MarkChatRead method registered by add_ChatServiceServicer_to_server.


##### `ChatService.SetBackground`  (lines 296–320)

```
def SetBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for setting a chat background. It wraps the network details so callers only provide the request and connection options.

**Data flow**: It receives a SetBackground request, target server information, and optional settings like credentials or timeout. It serializes the request data, sends one request to the SetBackground RPC path, and decodes an empty response if the server accepts it. Any actual background change happens on the server.

**Call relations**: This shortcut uses grpc.experimental.unary_unary. The matching server route is installed by add_ChatServiceServicer_to_server and should lead to a real override of ChatServiceServicer.SetBackground.


##### `ChatService.RemoveBackground`  (lines 323–347)

```
def RemoveBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for removing a chat background. It hides the gRPC path and message conversion details from the caller.

**Data flow**: It receives a RemoveBackground request, a target server, and optional call options. It turns the request into bytes, sends it as a single RPC, and converts the server's empty response back into an Empty message. Success or failure of the call tells the caller whether the server accepted the operation.

**Call relations**: It hands the actual communication to grpc.experimental.unary_unary. The server must have registered a servicer whose RemoveBackground method performs the real work.


##### `ChatService.ShareContactInfo`  (lines 350–374)

```
def ShareContactInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for sharing the user's contact information into a chat. It is a thin network wrapper around that remote action.

**Data flow**: It receives a ShareContactInfo request, target address, and optional gRPC settings. It serializes the request, sends it to the ShareContactInfo RPC, and decodes an empty success response. It does not change local state itself; the server decides what to do.

**Call relations**: This method calls grpc.experimental.unary_unary with the ShareContactInfo path and message converters. Server registration maps that path to the servicer's ShareContactInfo method.


##### `ChatService.SetTyping`  (lines 377–401)

```
def SetTyping(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for sending a typing indicator. It is meant for a short-lived signal rather than a saved chat change.

**Data flow**: It receives a SetTyping request, target server information, and optional call settings. It serializes the request, sends one RPC, and decodes an empty response if the server accepts the typing update. The function returns the RPC result, not a stored typing record.

**Call relations**: It delegates to grpc.experimental.unary_unary. The server side receives the call through the SetTyping route installed by add_ChatServiceServicer_to_server.


##### `ChatService.GetChat`  (lines 404–428)

```
def GetChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for fetching chat details. It lets a caller ask a remote server for one chat and receive a typed response object.

**Data flow**: It receives a GetChat request, target server, and optional settings. It serializes the request, sends it to the GetChat RPC path, and decodes the reply into a GetChatResponse. The returned object contains whatever chat information the server supplies.

**Call relations**: This shortcut sends the call through grpc.experimental.unary_unary. On the server, the registered ChatService handler routes it to the servicer's GetChat method.


##### `ChatService.GetChatCount`  (lines 431–455)

```
def GetChatCount(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for asking how many chats match a request. It avoids requiring the caller to manually set up a stub.

**Data flow**: It receives a GetChatCount request, a target server, and optional settings. It converts the request to bytes, sends one request, and decodes the returned bytes as a GetChatCountResponse. The output is the count information supplied by the server.

**Call relations**: It uses grpc.experimental.unary_unary with the same method path used by the normal stub. The server route is created by add_ChatServiceServicer_to_server and points to GetChatCount on the servicer.


##### `ChatService.HasBackground`  (lines 458–482)

```
def HasBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-call client shortcut for checking whether a chat has a background. It packages the request and response conversion for that remote check.

**Data flow**: It receives a HasBackground request, target server information, and optional call settings. It serializes the request, sends a single RPC, and decodes the server reply into a HasBackgroundResponse. The result tells the caller what the server reports.

**Call relations**: The method delegates network work to grpc.experimental.unary_unary. The matching server-side method is attached during add_ChatServiceServicer_to_server registration.


##### `ChatService.SubscribeChatEvents`  (lines 485–509)

```
def SubscribeChatEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental client shortcut for subscribing to chat events from a server. Unlike most methods here, one request can produce a stream of many event responses over time.

**Data flow**: It receives a SubscribeChatEvents request, target server, and optional call settings. It serializes the request, opens a unary-to-stream RPC, and decodes each incoming response as a SubscribeChatEventsResponse. The output is a stream-like result that the caller can read for ongoing chat events.

**Call relations**: It calls grpc.experimental.unary_stream because this RPC returns multiple responses. The server side must register and implement SubscribeChatEvents through add_ChatServiceServicer_to_server so event updates can flow back to the client.


### Event and message wiring
Generated gRPC bindings for event catch-up streaming and message-related client/server procedures.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2_grpc.py`

`generated` · `request handling`

This file is machine-generated from a protocol definition, so it is not where the real event replay rules are written. Instead, it is the wiring that lets Python code talk over gRPC, a remote-call system where one program can call a method on another program as if it were local.

The service here is about “catching up” on durable iMessage-related events: messages, group changes, polls, and chats. A caller can ask for everything newer than a saved sequence number, receive a finite stream of responses, and then move to live subscription streams without missing anything. Think of it like reading all unread letters from a mailbox before standing by the door for new deliveries.

The file provides three main shapes. EventServiceStub is for clients; it creates a callable CatchUpEvents method on a gRPC channel. EventServiceServicer is the server-side base class; real server code is expected to subclass it and replace the placeholder method. add_EventServiceServicer_to_server connects that implementation to a running gRPC server. EventService offers an experimental one-shot style helper for making the same remote call.

At import time, the file also checks that the installed grpc package is new enough for this generated code. Without this file, Python clients and servers would not know how to serialize requests, deserialize responses, or register the CatchUpEvents remote method.

#### Function details

##### `EventServiceStub.__init__`  (lines 45–55)

```
def __init__(self, channel)
```

**Purpose**: This sets up the client-side object used to call the remote CatchUpEvents service. A client uses it when it already has a gRPC channel connected to a server.

**Data flow**: It receives a gRPC channel as input. It attaches a CatchUpEvents callable to the stub, telling gRPC the remote method name, how to turn a CatchUpEventsRequest into bytes for sending, and how to turn response bytes back into CatchUpEventsResponse objects. The result is a stub object ready to start a server-streaming catch-up request.

**Call relations**: Client code creates this stub before asking the remote EventService for missed events. When the client later calls CatchUpEvents on the stub, the gRPC library uses the serializer and deserializer configured here to carry the request and response stream across the network.


##### `EventServiceServicer.CatchUpEvents`  (lines 75–79)

```
def CatchUpEvents(self, request, context)
```

**Purpose**: This is the server-side placeholder for the CatchUpEvents operation. It exists so real server code has a clear method to override with the actual replay behavior.

**Data flow**: It receives a request and a gRPC context, which is the object used to report status and details back to the caller. In this generated base version, it marks the call as unimplemented, adds the message 'Method not implemented!', and raises an error instead of returning events.

**Call relations**: The registration helper connects this method name to the gRPC server, but useful servers are expected to provide a subclass or replacement implementation. If nobody overrides it, any client calling CatchUpEvents will get an unimplemented error rather than an event stream.


##### `add_EventServiceServicer_to_server`  (lines 82–93)

```
def add_EventServiceServicer_to_server(servicer, server)
```

**Purpose**: This connects a server-side EventService implementation to a gRPC server so incoming CatchUpEvents calls can reach it. It is the bridge between the generated service definition and the running server process.

**Data flow**: It receives a servicer object and a gRPC server. It builds a method table for CatchUpEvents, including how to decode incoming request bytes, call the servicer method, and encode each response object back into bytes. It then registers that method table with the server, changing the server so it can accept EventService calls.

**Call relations**: Server startup code calls this after creating the real EventService servicer. Inside, it asks gRPC to build a unary-stream method handler, meaning one request comes in and a stream of responses goes out, then wraps that in a generic service handler and installs it on the server.

*Call graph*: 2 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler).


##### `EventService.CatchUpEvents`  (lines 115–139)

```
def CatchUpEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: This is an experimental convenience entry point for making a CatchUpEvents remote call without first manually creating the usual stub object. It sends one catch-up request to a target server and returns a stream of responses.

**Data flow**: It receives the request, the target server address, and optional call settings such as credentials, timeout, compression, metadata, and whether to use an insecure connection. It passes all of that to gRPC along with the method path and the request/response conversion functions. The output is the gRPC result stream for CatchUpEvents.

**Call relations**: This is an alternate client path to the same remote method configured by EventServiceStub.__init__. Rather than server code calling it, application client code may use it when it wants the generated experimental API to directly perform the unary-stream gRPC call.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2_grpc.py`

`generated` · `cross-cutting`

This file is like the phonebook and switchboard for the iMessage message API. The real message behavior lives elsewhere; this file defines how Python code talks to it over gRPC, which is a remote-call system that lets one program call functions in another program as if they were local.

At import time, it first checks that the installed grpc package is new enough for this generated code. If the versions do not match, it fails early with a clear upgrade/downgrade message rather than breaking later in a harder-to-understand way.

The MessageServiceStub is for clients. Given a gRPC channel, it creates callable methods such as SendTextMessage, GetMessage, and SubscribeMessageEvents. Each method knows the exact service path and how to turn request and response objects into bytes and back again.

The MessageServiceServicer is for servers. It lists the same methods, but each one is only a placeholder that returns “not implemented.” A real server subclasses or replaces this behavior with actual iMessage work.

The add_MessageServiceServicer_to_server function connects a real servicer to a gRPC server. Finally, the MessageService class offers an experimental shortcut style for making one-off calls without first building a stub object.

#### Function details

##### `MessageServiceStub.__init__`  (lines 46–126)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side MessageService object from a gRPC channel. After this runs, client code can call Python methods that send, edit, read, and subscribe to iMessage message data on a remote server.

**Data flow**: It receives a channel, which is the open communication path to a server. It attaches one callable attribute per remote operation, pairing each operation name with the right request serializer and response parser. The result is the same stub object, now populated with ready-to-use remote-call methods.

**Call relations**: Client code creates this stub when it wants to talk to the MessageService. Later, when the caller invokes one of the stub methods, gRPC uses the method path and serialization rules set up here to send the request to the matching server method.


##### `MessageServiceServicer.SendTextMessage`  (lines 146–159)

```
def SendTextMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending a plain text message. In this generated base class it deliberately does nothing except report that no real implementation has been supplied.

**Data flow**: It receives a SendTextMessage request and a gRPC context, which carries status information for the call. It marks the call as unimplemented, adds a short error detail, and raises NotImplementedError. No message is sent and no response object is produced.

**Call relations**: A real server is expected to override this method. If add_MessageServiceServicer_to_server registers a servicer that has not replaced it, incoming SendTextMessage calls reach this placeholder and fail clearly.


##### `MessageServiceServicer.SendAttachmentMessage`  (lines 161–165)

```
def SendAttachmentMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending a message with an attachment. The generated version is only a placeholder and must be replaced by real server code.

**Data flow**: It receives an attachment-message request and the call context. It sets the gRPC status to unimplemented, stores an explanatory detail, and raises NotImplementedError. Nothing is sent back except the error.

**Call relations**: The gRPC server calls this method when a client requests SendAttachmentMessage. A production servicer should override it before being registered with add_MessageServiceServicer_to_server.


##### `MessageServiceServicer.SendMultipartMessage`  (lines 167–171)

```
def SendMultipartMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending a message made from multiple parts, such as text plus media. Here it is only the generated “not implemented” fallback.

**Data flow**: It takes the multipart request and context, writes an unimplemented status into the context, and raises NotImplementedError. The request is not processed and no MessageResponse is created.

**Call relations**: This method is the target for incoming SendMultipartMessage RPCs unless a real servicer overrides it. Registration through add_MessageServiceServicer_to_server is what exposes that implementation to gRPC.


##### `MessageServiceServicer.SendCustomizedMiniAppMessage`  (lines 173–178)

```
def SendCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for sending an iMessage mini-app card backed by the caller’s extension. The generated base method exists only to document the service shape and fail if not replaced.

**Data flow**: It receives the mini-app send request and context. It changes the context status to unimplemented, adds the standard detail text, and raises NotImplementedError. No card is created.

**Call relations**: When a client calls SendCustomizedMiniAppMessage, gRPC dispatches to the registered servicer’s method. A real implementation must override this placeholder before registration.


##### `MessageServiceServicer.UpdateCustomizedMiniAppMessage`  (lines 180–185)

```
def UpdateCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for updating an existing customized iMessage mini-app card. The generated method is a required placeholder, not working business logic.

**Data flow**: It receives an update request and the call context. It records an unimplemented status and then raises NotImplementedError. The existing mini-app message is not read or changed.

**Call relations**: This is where the gRPC server routes UpdateCustomizedMiniAppMessage calls. The useful behavior comes from a project-specific servicer that replaces this base method.


##### `MessageServiceServicer.EditMessage`  (lines 187–191)

```
def EditMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for editing an existing message. In this file it is only a stub that signals missing implementation.

**Data flow**: It takes an edit request plus context, sets the call status to unimplemented, adds a detail message, and raises NotImplementedError. No edit is applied and no fresh message snapshot is returned.

**Call relations**: Incoming EditMessage calls arrive here through the gRPC server registration. Real server code should override it so clients receive actual edit behavior instead of the generated error.


##### `MessageServiceServicer.UnsendMessage`  (lines 193–198)

```
def UnsendMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for retracting an existing message. The generated version only reports that the operation has not been implemented.

**Data flow**: It receives an unsend request and context. It marks the call as unimplemented and raises NotImplementedError. It does not retract a message and does not return the expected empty success response.

**Call relations**: The registered servicer’s UnsendMessage method is called when clients ask to retract a message. This base version is meant to be replaced before the service is exposed.


##### `MessageServiceServicer.SetReaction`  (lines 200–204)

```
def SetReaction(self, request, context)
```

**Purpose**: Defines the server-side hook for adding, changing, or removing a reaction on a message. The generated method is just a failure placeholder.

**Data flow**: It receives a reaction request and context. It writes an unimplemented status and detail into the context, then raises NotImplementedError. No reaction change happens.

**Call relations**: gRPC dispatches SetReaction requests to the servicer registered for MessageService. A real servicer should override this method to return an updated MessageResponse.


##### `MessageServiceServicer.PlaceSticker`  (lines 206–210)

```
def PlaceSticker(self, request, context)
```

**Purpose**: Defines the server-side hook for placing a sticker on a message. In the generated base class, it only says the method is missing.

**Data flow**: It receives a sticker-placement request and context. It sets the call status to unimplemented and raises NotImplementedError. No sticker is placed and no updated message is returned.

**Call relations**: This placeholder is reached only if the registered server implementation has not supplied real PlaceSticker behavior. add_MessageServiceServicer_to_server wires whichever implementation is present into the gRPC server.


##### `MessageServiceServicer.NotifySilencedMessage`  (lines 212–217)

```
def NotifySilencedMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for Apple’s per-message “Notify Anyway” action. This generated method does not perform that action; it only marks the call as unimplemented.

**Data flow**: It receives a notify request and call context. It records an unimplemented status, adds the standard detail text, and raises NotImplementedError. No notification action is triggered.

**Call relations**: Clients reach this hook through the MessageService gRPC route. A server that supports the feature must override it before registering the servicer.


##### `MessageServiceServicer.GetMessage`  (lines 219–224)

```
def GetMessage(self, request, context)
```

**Purpose**: Defines the server-side hook for reading one message. The generated method is a placeholder that fails until real read logic is supplied.

**Data flow**: It receives a get-message request and context. It sets the context status to unimplemented and raises NotImplementedError. No message lookup occurs and no GetMessageResponse is returned.

**Call relations**: The gRPC server calls this method for GetMessage requests. Project code provides the actual lookup by overriding this base method and then registering the servicer.


##### `MessageServiceServicer.ListRecentMessages`  (lines 226–233)

```
def ListRecentMessages(self, request, context)
```

**Purpose**: Defines the server-side hook for listing recent messages, possibly filtered by fields such as sender, read state, or time. Here it is only the generated not-implemented method.

**Data flow**: It receives a list request and context. It marks the gRPC call as unimplemented, records a detail message, and raises NotImplementedError. No list is built or returned.

**Call relations**: When clients request recent messages, gRPC routes the call to this method on the registered servicer. A real implementation must replace it to turn the request filters into a response list.


##### `MessageServiceServicer.ListChatMessages`  (lines 235–240)

```
def ListChatMessages(self, request, context)
```

**Purpose**: Defines the server-side hook for listing messages inside one chat. The base generated method exists only so server code knows what to implement.

**Data flow**: It receives a chat-message list request and context. It sets an unimplemented status and raises NotImplementedError. No chat history is read.

**Call relations**: This hook is used by the gRPC dispatch table for ListChatMessages. The useful behavior comes from a real servicer method registered through add_MessageServiceServicer_to_server.


##### `MessageServiceServicer.GetEmbeddedMedia`  (lines 242–250)

```
def GetEmbeddedMedia(self, request, context)
```

**Purpose**: Defines the server-side hook for retrieving media bytes embedded in a message. The generated base method does not fetch the media; it only reports missing implementation.

**Data flow**: It receives a media request and context. It marks the call as unimplemented, adds the detail text, and raises NotImplementedError. No raw media bytes are returned.

**Call relations**: The service definition includes this RPC for gRPC use, while the comments note that HTTP access should use a dedicated raw-bytes route. A real server implementation overrides this hook before registration.


##### `MessageServiceServicer.SubscribeMessageEvents`  (lines 252–258)

```
def SubscribeMessageEvents(self, request, context)
```

**Purpose**: Defines the server-side hook for a live stream of durable message events. The generated method is a placeholder and does not stream anything.

**Data flow**: It receives a subscription request and context. It sets the call status to unimplemented and raises NotImplementedError. No event stream is opened.

**Call relations**: Clients that subscribe to message events are routed to this method on the registered servicer. A real implementation should yield event responses over time; this base method simply fails if left in place.


##### `add_MessageServiceServicer_to_server`  (lines 261–342)

```
def add_MessageServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a MessageService server implementation to a gRPC server. Without this step, the server could have the right methods in Python but gRPC would not know which network paths should call them.

**Data flow**: It receives a servicer object and a server object. It builds a table that maps each RPC name to the matching servicer method, along with the correct request parser and response serializer. It then creates a generic gRPC handler and adds both generic and registered method handlers to the server.

**Call relations**: Server startup code calls this after creating a concrete servicer. Inside, it uses gRPC helper functions such as unary_unary_rpc_method_handler, unary_stream_rpc_method_handler, and method_handlers_generic_handler to turn Python methods into network-call handlers.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `MessageService.SendTextMessage`  (lines 364–388)

```
def SendTextMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to send a text message without first creating a MessageServiceStub. It is a convenience wrapper around the same remote SendTextMessage endpoint.

**Data flow**: It receives a request object, a target server address, and optional call settings such as credentials, timeout, compression, and metadata. It serializes the request, sends it to the SendTextMessage path, parses the MessageResponse, and returns that response to the caller.

**Call relations**: Client code can use this static method as a shortcut. It talks directly through gRPC’s experimental unary-unary call path, matching the server method that was registered for SendTextMessage.


##### `MessageService.SendAttachmentMessage`  (lines 391–415)

```
def SendAttachmentMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to send an attachment message. It lets callers reach the remote method without constructing a stub object first.

**Data flow**: It takes an attachment-message request, the target server, and optional connection and call settings. It turns the request into bytes, sends it to the SendAttachmentMessage route, parses the returned MessageResponse, and gives that response back.

**Call relations**: This is a client-side shortcut to the same RPC exposed through MessageServiceStub. On the server side, the call must match a registered SendAttachmentMessage implementation.


##### `MessageService.SendMultipartMessage`  (lines 418–442)

```
def SendMultipartMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to send a multipart message. It is useful when code wants a direct call instead of creating a reusable stub.

**Data flow**: It receives the multipart request, target server, and optional gRPC settings. It serializes the request, sends it to the SendMultipartMessage endpoint, deserializes the MessageResponse, and returns it.

**Call relations**: This static method is a client convenience. It depends on the server having registered a SendMultipartMessage handler through the MessageService service wiring.


##### `MessageService.SendCustomizedMiniAppMessage`  (lines 445–469)

```
def SendCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to send a customized iMessage mini-app card. It wraps the remote call details for callers.

**Data flow**: It takes the mini-app send request, target server, and optional settings such as credentials and timeout. It sends the serialized request to the matching service path and turns the returned bytes into a MessageResponse.

**Call relations**: Client code uses this as an alternative to calling the same method on MessageServiceStub. The server must provide a registered SendCustomizedMiniAppMessage implementation.


##### `MessageService.UpdateCustomizedMiniAppMessage`  (lines 472–496)

```
def UpdateCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to update a customized mini-app message. It hides the low-level gRPC path and byte conversion from the caller.

**Data flow**: It receives an update request, a target server, and optional call settings. It serializes the request, sends it to the UpdateCustomizedMiniAppMessage endpoint, parses the MessageResponse, and returns it.

**Call relations**: This shortcut reaches the same remote operation as the stub method. It only succeeds when the server has registered an implementation for UpdateCustomizedMiniAppMessage.


##### `MessageService.EditMessage`  (lines 499–523)

```
def EditMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to edit an existing message. It provides direct access to the EditMessage RPC.

**Data flow**: It takes an edit request, target, and optional gRPC options. It converts the request object into bytes, sends it to the EditMessage path, converts the response bytes into a MessageResponse, and returns it.

**Call relations**: Client code can choose this static method instead of a stub. The call is delivered to the server’s registered EditMessage handler.


##### `MessageService.UnsendMessage`  (lines 526–550)

```
def UnsendMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to retract a message. On success, the remote operation returns an empty response, meaning there is no extra message body to read.

**Data flow**: It receives an unsend request, target server, and optional call settings. It serializes the request, sends it to the UnsendMessage path, parses the empty response type, and returns that empty response.

**Call relations**: This is the static-call version of the UnsendMessage stub method. It depends on a server-side UnsendMessage handler being registered for the MessageService.


##### `MessageService.SetReaction`  (lines 553–577)

```
def SetReaction(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to set a reaction on a message. It returns the server’s fresh MessageResponse when the call succeeds.

**Data flow**: It takes a reaction request, target server, and optional options. It serializes the request, sends it to the SetReaction route, deserializes the MessageResponse, and returns it.

**Call relations**: Client code uses this method to reach the same RPC that MessageServiceStub exposes. The request is handled by the server’s registered SetReaction implementation.


##### `MessageService.PlaceSticker`  (lines 580–604)

```
def PlaceSticker(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to place a sticker on a message. It packages the request for the remote PlaceSticker operation.

**Data flow**: It receives a sticker-placement request, target address, and optional call settings. It sends the serialized request to the PlaceSticker service path and parses the returned MessageResponse.

**Call relations**: This client shortcut maps directly to the server-side PlaceSticker RPC. It works when that method has been registered on the gRPC server.


##### `MessageService.NotifySilencedMessage`  (lines 607–631)

```
def NotifySilencedMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call for the “Notify Anyway” action on a silenced message. A successful call returns an empty response rather than a message snapshot.

**Data flow**: It takes a notify request, target server, and optional connection settings. It serializes the request, sends it to the NotifySilencedMessage route, parses the empty response, and returns it.

**Call relations**: This is the direct static-call form of the same operation available on the stub. The server must have registered NotifySilencedMessage handling for the request to do real work.


##### `MessageService.GetMessage`  (lines 634–658)

```
def GetMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to fetch one message. It hides the gRPC path and byte conversion from the caller.

**Data flow**: It receives a get-message request, target server, and optional settings. It serializes the request, sends it to the GetMessage endpoint, parses the GetMessageResponse, and returns it.

**Call relations**: Client code can call this static method instead of creating a stub. The call lands on the server’s registered GetMessage method.


##### `MessageService.ListRecentMessages`  (lines 661–685)

```
def ListRecentMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to list recent messages. The request can carry filters, and the response contains the matching list.

**Data flow**: It takes a list request, target server, and optional gRPC settings. It serializes the request, sends it to the ListRecentMessages path, parses the ListRecentMessagesResponse, and returns it.

**Call relations**: This is a convenience route to the same RPC set up on MessageServiceStub. The server-side ListRecentMessages implementation supplies the actual message list.


##### `MessageService.ListChatMessages`  (lines 688–712)

```
def ListChatMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to list messages from a specific chat. It is the direct-call companion to the stub method.

**Data flow**: It receives a chat-message list request, target server, and optional call settings. It serializes the request, sends it to the ListChatMessages endpoint, parses the ListChatMessagesResponse, and returns it.

**Call relations**: Client code uses this shortcut when it does not want to keep a stub object. The server must have registered a ListChatMessages handler for the call.


##### `MessageService.GetEmbeddedMedia`  (lines 715–739)

```
def GetEmbeddedMedia(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes a one-off experimental client call to fetch embedded media data from a message. It returns a response object that may contain raw media bytes.

**Data flow**: It takes a media request, target server, and optional gRPC settings. It serializes the request, sends it to the GetEmbeddedMedia path, parses the GetEmbeddedMediaResponse, and returns it.

**Call relations**: This static method reaches the gRPC version of embedded-media retrieval. The comments note that HTTP access is handled separately by a raw-bytes route, but this method talks to the registered gRPC handler.


##### `MessageService.SubscribeMessageEvents`  (lines 742–766)

```
def SubscribeMessageEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Opens a one-off experimental client subscription to live message events. Unlike most other methods here, it returns a stream, meaning the caller can receive many responses over time from one request.

**Data flow**: It receives a subscription request, target server, and optional call settings. It serializes the request and sends it to the SubscribeMessageEvents path. Instead of one response, it parses and yields a sequence of SubscribeMessageEventsResponse objects as the server sends them.

**Call relations**: This is the static shortcut for the streaming RPC also configured on MessageServiceStub. It relies on the server’s registered SubscribeMessageEvents method to keep producing event responses.
