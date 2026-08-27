# iMessage Provider Contract and gRPC Service Bindings  `stage-23.8`

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it defines the “plugs and sockets” that the rest of the system uses to talk to an iMessage provider, whether that provider is local code or a separate service on the network.

The provider.py file is the human-written contract. It says what an iMessage provider must be able to do, and it defines small data shapes for things like incoming messages, attachments, and events. These are like standard forms everyone agrees to fill out the same way.

The other files are generated gRPC bindings. gRPC is a way for one program to call functions in another program over the network as if they were local calls. The attachment, chat, message, and event binding files each provide client stubs for making those calls and server hooks for exposing them. Together, they let the system use the same provider ideas across process and network boundaries.

## Files in this stage

### Provider Contract
Defines the shared provider interface and data objects that the iMessage extension uses to describe messages, attachments, and events.

### `extensions/imessage/ufo_ext_imessage/provider.py`

`data_model` · `cross-cutting`

This file is like the plug shape for the iMessage extension. The rest of the system wants to send texts, receive messages, download attachments, and understand provider errors, but it should not need to know which concrete service or backend is doing that work. This file solves that by defining a shared interface, called MessageProvider, using a Protocol. A Protocol is a Python way to say: “any object with these methods can be used here.”

It also defines simple frozen data classes. MessageAttachment describes one attached file. InboundMessage describes a message that arrived, including who sent it, which conversation it belongs to, its text, attachments, and whether it was direct. ProviderEvent wraps changes coming from the provider, such as a message, a sequence number, or the current head position. The sequence numbers act like bookmarks in a long stream of events, so the system can catch up without rereading everything.

The file does not implement real iMessage behavior. Instead, it says what a real provider must be able to do: assign a phone line, catch up on missed events, subscribe to live events, send text and attachments, download attachment bytes, and classify errors. Without this contract, the surface layer would have to know the details of every provider implementation, making the extension harder to swap, test, or maintain.

#### Function details

##### `MessageProvider.installation_id`  (lines 37–37)

```
def installation_id(self) -> str
```

**Purpose**: This property gives the unique identity of the provider installation. The system can use it to tell one configured provider instance from another.

**Data flow**: The caller asks the provider for its installation identity. The provider reads its own stored identity and returns it as text. Nothing is changed.

**Call relations**: This is part of the provider contract. Even though no direct caller is shown in the supplied graph, any code that needs to label or distinguish the active iMessage provider would use this property.


##### `MessageProvider.assign_line`  (lines 39–39)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This method asks the provider to assign or connect a phone number for use by the extension. The idempotency key helps make retries safe, so the same request is not accidentally performed twice.

**Data flow**: The caller gives a phone number and a retry-safe idempotency key. The provider sends or records that assignment request in its own backend. It returns a text identifier for the assigned line or resulting operation.

**Call relations**: This is part of setup or provisioning for a concrete provider. No direct caller is shown here, but a higher-level setup flow would call it when the extension needs to claim or activate a phone number.


##### `MessageProvider.catch_up`  (lines 41–41)

```
def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This method returns past provider events that happened after a given sequence bookmark. It lets the system recover missed messages after downtime or a reconnect.

**Data flow**: The caller provides the last sequence number it has already processed, or nothing if it has no bookmark. The provider reads its event history after that point and yields ProviderEvent objects one by one. The caller receives a stream of older events until it is caught up.

**Call relations**: ImessageSurface._catch_up calls this when the surface needs to replay missed provider activity. The provider supplies events, and the surface consumes them to bring its local state back in line before relying on live updates.

*Call graph*: called by 1 (_catch_up).


##### `MessageProvider.subscribe`  (lines 43–43)

```
def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This method opens a live stream of new provider events. It is how the extension keeps receiving messages as they happen.

**Data flow**: The caller passes an asyncio.Event, which is a small signal object used to say “the subscription is ready.” The provider connects to its live event source, sets or uses that ready signal at the right time, and yields ProviderEvent objects as new activity arrives.

**Call relations**: ImessageSurface._pump_live calls this during live operation. The surface waits for the provider to start streaming, then consumes each event as part of the ongoing message pump.

*Call graph*: called by 1 (_pump_live).


##### `MessageProvider.send_text`  (lines 45–45)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This method sends a plain text message into a conversation. The idempotency key protects against duplicate sends if the caller has to retry after a failure.

**Data flow**: The caller supplies a conversation ID, the message text, and an idempotency key. The provider asks its backend to send the text to that conversation. It returns a text identifier for the sent message or send operation.

**Call relations**: ImessageSurface._prove calls this when it needs to send a text through the provider. The surface decides what should be sent, and the provider is responsible for carrying it to the external messaging system.

*Call graph*: called by 1 (_prove).


##### `MessageProvider.send_attachment`  (lines 47–53)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This method sends a file attachment to a conversation. It is used when the extension needs to share something more than plain text, such as a contact card.

**Data flow**: The caller provides the conversation ID, a filename, the file contents as bytes, and an idempotency key. The provider uploads or sends that file through its backend. It returns a text identifier for the sent attachment or send operation.

**Call relations**: ImessageSurface._send_contact_card calls this when it needs to send a contact card file. The surface prepares the file, then hands the bytes to the provider to deliver.

*Call graph*: called by 1 (_send_contact_card).


##### `MessageProvider.download_attachment`  (lines 55–55)

```
def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This method downloads the raw contents of an attachment by its provider attachment ID. It streams the file in chunks, which avoids needing to hold a large file in memory all at once.

**Data flow**: The caller gives an attachment ID. The provider fetches the matching attachment data and yields chunks of bytes over time. The caller receives those chunks and can write or process the completed file.

**Call relations**: ImessageSurface._downloaded_files calls this when it needs local access to files attached to incoming messages. The provider supplies the bytes, and the surface turns them into downloaded files.

*Call graph*: called by 1 (_downloaded_files).


##### `MessageProvider.invalidate`  (lines 57–57)

```
async def invalidate(self) -> None
```

**Purpose**: This method tells the provider that its current state or connection should no longer be trusted. A concrete provider can use it to close sessions, clear cached credentials, or force a fresh connection later.

**Data flow**: The caller sends no extra data. The provider updates its internal state so the current provider instance or connection is treated as invalid. It returns nothing once cleanup or marking is complete.

**Call relations**: This is part of the provider lifecycle contract. No direct caller is shown in the supplied graph, but it exists so orchestration code can deliberately retire a provider after a serious configuration or connection problem.


##### `MessageProvider.invalid_cursor`  (lines 59–59)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This method answers whether an error means the saved event bookmark is no longer usable. That lets the system decide whether a catch-up position must be reset.

**Data flow**: The caller passes an exception object. The provider inspects it using knowledge of its own backend’s error formats. It returns true if the error means the event cursor or sequence bookmark is invalid, otherwise false.

**Call relations**: This is an error-classification hook in the provider contract. No direct caller is shown in the supplied graph, but it would be used around catch-up or event-reading code to decide how to recover from a bad bookmark.


##### `MessageProvider.external_error`  (lines 61–61)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This method answers whether an exception came from the outside provider service rather than from local program logic. That distinction helps the surface decide how to report or recover from failures.

**Data flow**: The caller gives an exception. The provider checks whether it matches the provider backend’s known error types or error shapes. It returns true for outside-service errors and false for errors that should be treated as local or unexpected.

**Call relations**: ImessageSurface._consume_connected, ImessageSurface._downloaded_files, and ImessageSurface._send_contact_card call this when something goes wrong during connected event processing, attachment download, or contact-card sending. The provider helps the surface decide whether the failure belongs to the external messaging service.

*Call graph*: called by 3 (_consume_connected, _downloaded_files, _send_contact_card).


##### `MessageProvider.error_code`  (lines 63–63)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This method turns a provider-specific exception into a short text error code. That gives the rest of the extension a stable label to log, report, or return without exposing messy backend details.

**Data flow**: The caller passes an exception. The provider reads the exception details and maps them to a string code. The returned code can then be used by higher-level code for reporting or decisions.

**Call relations**: ImessageSurface._send_contact_card calls this when sending an attachment fails and it needs a clear provider error code. The surface uses the code to describe the failure in a more consistent way.

*Call graph*: called by 1 (_send_contact_card).


### gRPC Service Bindings
Provides the generated Python client and server wiring for iMessage attachment, chat, event, and message service RPCs.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2_grpc.py`

`generated` · `request handling`

This file is machine-generated from a service definition, so it is mostly plumbing rather than hand-written business logic. Its job is to make three attachment operations available over gRPC, which is a remote-call system where one program can call a function in another program as if it were local. The operations are: get attachment metadata, upload an attachment, and download an attachment as a stream of byte chunks.

Think of it like a set of matching electrical plugs and sockets. The client side, `AttachmentServiceStub`, creates callable methods that know the exact network names, message formats, and response formats for the service. The server side, `AttachmentServiceServicer`, defines the method names a real server must implement, but its default methods only say “not implemented.” A project-specific subclass is expected to provide the real storage and retrieval behavior.

The helper `add_AttachmentServiceServicer_to_server` connects a real servicer object to a gRPC server so incoming network requests are unpacked into request objects, sent to the right method, and packed back into response messages. The final `AttachmentService` class offers experimental one-off static call helpers for clients that do not want to create a stub first.

The file also checks that the installed `grpc` Python package is new enough for this generated code. Without this file, Python code would not know how to speak this attachment service protocol.

#### Function details

##### `AttachmentServiceStub.__init__`  (lines 37–57)

```
def __init__(self, channel)
```

**Purpose**: Creates a client-side object with ready-to-use methods for the attachment service. A caller gives it a gRPC channel, which is the network connection to the server, and it prepares the three remote calls.

**Data flow**: A gRPC channel goes in. The initializer attaches three callable attributes to the stub: one for reading attachment information, one for uploading an attachment, and one for downloading an attachment as a stream. Each callable knows how to turn request objects into bytes before sending them and how to turn response bytes back into Python message objects.

**Call relations**: Client code creates this stub when it wants to talk to an attachment server through an existing channel. After that, the rest of the program calls `GetAttachmentInfo`, `UploadAttachment`, or `DownloadAttachment` on the stub, and the gRPC library carries those requests across the network.


##### `AttachmentServiceServicer.GetAttachmentInfo`  (lines 69–74)

```
def GetAttachmentInfo(self, request, context)
```

**Purpose**: Defines the server-side shape of the metadata lookup operation. In this generated base class it is only a placeholder; real server code must override it to return attachment details.

**Data flow**: A request and a gRPC context come in. The default method marks the call as unimplemented, adds the message “Method not implemented!”, and raises an error. No attachment data is read and no useful response is produced.

**Call relations**: This method is called by gRPC when a server has registered a servicer and a client asks for attachment information. In this base class it deliberately fails, so a real attachment service must provide its own version before registration.


##### `AttachmentServiceServicer.UploadAttachment`  (lines 76–87)

```
def UploadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the upload operation. The comments describe the intended behavior: save one main file and possibly a sidecar file atomically, meaning either everything becomes visible or nothing does.

**Data flow**: An upload request and a gRPC context come in. The generated placeholder sets the call status to unimplemented and raises an error. It does not store any bytes or return an upload result.

**Call relations**: When registered with a gRPC server, this method is the target for client upload calls. The generated version is only a contract; project code is expected to override it with the real persistence logic before `add_AttachmentServiceServicer_to_server` wires it into the server.


##### `AttachmentServiceServicer.DownloadAttachment`  (lines 89–95)

```
def DownloadAttachment(self, request, context)
```

**Purpose**: Defines the server-side shape of the download operation. Downloads are server-streaming, meaning the server can send many response messages back for one request, usually chunks of the attachment bytes.

**Data flow**: A download request and a gRPC context come in. The placeholder marks the method as unimplemented and raises an error. It produces no stream of download frames.

**Call relations**: gRPC calls this method when a client requests an attachment download from a registered servicer. A real implementation must replace this placeholder so it can read stored bytes and yield response frames back to the client.


##### `add_AttachmentServiceServicer_to_server`  (lines 98–119)

```
def add_AttachmentServiceServicer_to_server(servicer, server)
```

**Purpose**: Registers an attachment service implementation with a gRPC server. This is the step that makes the server actually listen for the three attachment method names.

**Data flow**: A servicer object and a gRPC server go in. The function builds a table that maps each public service method name to the matching Python method on the servicer, along with the correct request parser and response serializer. It then adds that table to the server, changing the server so it can route incoming attachment calls.

**Call relations**: Server startup code calls this after creating a real `AttachmentServiceServicer` implementation. Inside, it asks gRPC to create method handlers for normal request/response calls and for the streaming download call, groups them under the full service name, and hands that group to the server.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `AttachmentService.GetAttachmentInfo`  (lines 133–157)

```
def GetAttachmentInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for making a one-off metadata lookup call without manually creating an `AttachmentServiceStub`. It sends one request and expects one response.

**Data flow**: A request, a target server address, and optional connection settings go in. The function serializes the request, asks gRPC’s experimental helper to call the `GetAttachmentInfo` network method, and deserializes the returned bytes into a response object.

**Call relations**: Client code may call this static helper directly when it wants a quick remote call. It hands the actual network work to `grpc.experimental.unary_unary`, which is gRPC’s helper for a single request followed by a single response.


##### `AttachmentService.UploadAttachment`  (lines 160–184)

```
def UploadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for uploading an attachment with a single request and a single response. It is an alternative to creating a stub first.

**Data flow**: An upload request, a target server address, and optional connection settings go in. The function converts the request into bytes, calls the remote `UploadAttachment` method through gRPC’s experimental API, and converts the response bytes back into an upload response object.

**Call relations**: Client code can use this helper for direct uploads. It delegates the actual remote procedure call to `grpc.experimental.unary_unary`, matching the service design where upload is one request followed by one final result.


##### `AttachmentService.DownloadAttachment`  (lines 187–211)

```
def DownloadAttachment(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for downloading an attachment as a stream. One request goes to the server, and the server can send back many response messages containing download frames.

**Data flow**: A download request, a target server address, and optional connection settings go in. The function serializes the request, starts the remote `DownloadAttachment` call using gRPC’s experimental streaming helper, and returns a stream of deserialized download response objects.

**Call relations**: Client code may call this static helper when it wants to receive attachment bytes directly from a server. It passes control to `grpc.experimental.unary_stream`, which is the gRPC pattern for one request followed by many server responses.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2_grpc.py`

`generated` · `client/server RPC setup and request handling`

This file is machine-made from a protobuf service definition, which is a language-neutral contract for messages and remote calls. Its job is not to decide how chats work. Instead, it is the phone jack and wiring that lets one process ask another process to create chats, mark chats read, set backgrounds, send typing indicators, read chat details, and subscribe to chat events. Without it, Python code would still know the message shapes from the protobuf files, but it would not know the exact gRPC method names, how to turn request objects into bytes, or how to turn response bytes back into Python objects.

There are three main pieces. `ChatServiceStub` is for clients: given a gRPC channel, it creates callable methods such as `CreateChat` and `GetChat`. `ChatServiceServicer` is a server-side base class: it names every method the server must provide, but the default version only says “not implemented.” Real server code is expected to subclass it and fill in the behavior. `add_ChatServiceServicer_to_server` connects that real implementation to a gRPC server. The final `ChatService` class offers an experimental shortcut style for making one-off calls directly to a target address.

#### Function details

##### `ChatServiceStub.__init__`  (lines 37–92)

```
def __init__(self, channel)
```

**Purpose**: Builds the client-side object used to call the remote chat service. After this runs, code can call attributes like `CreateChat` or `SubscribeChatEvents` as if they were local functions, while gRPC sends the request over the network.

**Data flow**: It receives a gRPC channel, which is an open communication path to a server. It attaches one callable per chat operation, telling each callable how to serialize its request object into bytes, what remote method path to use, and how to deserialize the response bytes back into the correct response object. The result is a ready-to-use stub object whose attributes perform remote calls.

**Call relations**: Application client code creates this stub when it has a channel to the iMessage service. Later, when the application invokes one of the stub methods, gRPC uses the method path and serializer choices set up here to talk to the matching server method.


##### `ChatServiceServicer.CreateChat`  (lines 103–108)

```
def CreateChat(self, request, context)
```

**Purpose**: Defines the server-side slot for creating a chat, but does not implement it. It exists so real server code has a clear method name and signature to override.

**Data flow**: It receives a create-chat request and a gRPC context object, which carries request status and metadata. The default implementation marks the call as unimplemented, stores a short error detail in the context, and raises an error instead of returning a chat response.

**Call relations**: When a server registers a `ChatServiceServicer`, gRPC may route incoming `CreateChat` calls to this method. In normal use, a project-specific subclass replaces this placeholder so the registered server can actually create the chat.


##### `ChatServiceServicer.MarkChatRead`  (lines 110–114)

```
def MarkChatRead(self, request, context)
```

**Purpose**: Defines the server-side slot for marking a chat as read. The generated base version is only a placeholder and deliberately fails if used directly.

**Data flow**: It receives a request saying which chat should be marked read and a gRPC context. It sets the response status to “unimplemented,” adds the detail message “Method not implemented!”, and raises an error instead of returning the expected empty success response.

**Call relations**: Incoming `MarkChatRead` RPCs reach this method through the server registration function. A real service implementation is expected to override it before the server is used.


##### `ChatServiceServicer.SetBackground`  (lines 116–122)

```
def SetBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for setting a chat background image or data. This base method does not perform the change; it only signals that no implementation was provided.

**Data flow**: It receives a set-background request, possibly containing byte data, plus the gRPC context. It changes the context to an unimplemented error state and raises an exception, so no background is stored and no success response is produced.

**Call relations**: The registration helper can connect this method to the `/SetBackground` RPC route. Real server code should subclass `ChatServiceServicer` and replace this method with code that validates and stores the background.


##### `ChatServiceServicer.RemoveBackground`  (lines 124–128)

```
def RemoveBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for removing a chat background. The generated version is a required placeholder, not working application logic.

**Data flow**: It receives a remove-background request and the gRPC context. It marks the call as unimplemented, records an explanatory detail, and raises an error rather than returning the expected empty response.

**Call relations**: After registration, gRPC would call this for incoming `RemoveBackground` requests unless a subclass overrides it. The placeholder helps enforce the service contract while leaving the real behavior to application code.


##### `ChatServiceServicer.ShareContactInfo`  (lines 130–137)

```
def ShareContactInfo(self, request, context)
```

**Purpose**: Defines the server-side slot for sharing the local user’s contact card into a chat. The generated base class does not actually send anything.

**Data flow**: It receives a request naming the target chat and a gRPC context. Instead of pushing contact information, it sets the status to unimplemented, adds a detail message, and raises an exception.

**Call relations**: The server registration can route `ShareContactInfo` requests here. A real implementation should override this method so the contact-sharing request is passed to the system that can send it.


##### `ChatServiceServicer.SetTyping`  (lines 139–144)

```
def SetTyping(self, request, context)
```

**Purpose**: Defines the server-side slot for sending a temporary typing indicator. The base version exists only to show the method that real code must provide.

**Data flow**: It receives a typing-indicator request and a gRPC context. It does not publish a typing state; it marks the call as unimplemented and raises an error.

**Call relations**: When the service is registered, incoming `SetTyping` calls are mapped to this method name. In a working server, a subclass overrides it and sends the transient typing signal to the chat system.


##### `ChatServiceServicer.GetChat`  (lines 146–152)

```
def GetChat(self, request, context)
```

**Purpose**: Defines the server-side slot for reading one chat’s details. The generated version does not look anything up; it reports that the method is missing.

**Data flow**: It receives a get-chat request and context. It changes the context status to unimplemented, records a detail message, and raises an exception instead of returning a chat response.

**Call relations**: The registration helper maps remote `GetChat` requests to this method. Real server code supplies the actual lookup by overriding it.


##### `ChatServiceServicer.GetChatCount`  (lines 154–158)

```
def GetChatCount(self, request, context)
```

**Purpose**: Defines the server-side slot for returning how many chats match a request. By itself, it is only a placeholder.

**Data flow**: It receives a count request and gRPC context. It marks the RPC as unimplemented and raises an error, so no count is calculated or returned.

**Call relations**: A registered service can receive `GetChatCount` calls through this method name. A project-specific subclass must replace it with the code that counts chats.


##### `ChatServiceServicer.HasBackground`  (lines 160–164)

```
def HasBackground(self, request, context)
```

**Purpose**: Defines the server-side slot for checking whether a chat has a background. The generated base method does not check storage or return a real answer.

**Data flow**: It receives a request identifying the chat and the gRPC context. It sets the status to unimplemented, adds an error detail, and raises an exception instead of returning a yes-or-no response.

**Call relations**: Server registration maps `HasBackground` network calls to this method. Working server code overrides it to inspect the chat background state.


##### `ChatServiceServicer.SubscribeChatEvents`  (lines 166–170)

```
def SubscribeChatEvents(self, request, context)
```

**Purpose**: Defines the server-side slot for streaming chat events to a caller. The base method does not open a stream; it fails to show that real streaming logic is required.

**Data flow**: It receives a subscription request and a gRPC context. Instead of yielding event responses over time, it marks the call as unimplemented and raises an error.

**Call relations**: This method is connected to the streaming `SubscribeChatEvents` route during server registration. A real implementation overrides it and yields event messages as chat changes happen.


##### `add_ChatServiceServicer_to_server`  (lines 173–229)

```
def add_ChatServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a server-side chat service implementation to a gRPC server. It is the adapter that tells gRPC which Python method should answer each named remote call.

**Data flow**: It receives a servicer object, usually a subclass with real chat behavior, and a gRPC server. It builds a table from RPC names such as `CreateChat` to handler objects, pairing each handler with the right request parser and response writer. It then adds that table to the server so incoming network calls can be dispatched correctly.

**Call relations**: Server startup code calls this after creating the real `ChatServiceServicer` implementation. Inside, it uses gRPC’s unary request/response handler builders for ordinary calls, a unary-to-stream handler builder for chat event subscriptions, and a generic service handler so the server knows the service name and method map.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `ChatService.CreateChat`  (lines 242–266)

```
def CreateChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-off client call to create a chat without first building a `ChatServiceStub`. It is a convenience wrapper around gRPC’s direct-call API.

**Data flow**: It receives a create-chat request, a target server address, and optional settings such as credentials, timeout, compression, and metadata. It serializes the request, sends it to the `CreateChat` RPC path, parses the response bytes as a create-chat response, and returns that result to the caller.

**Call relations**: Client code may use this static method instead of creating a stub. It hands the call to gRPC’s experimental unary request/response mechanism, which contacts the server method registered for `CreateChat`.


##### `ChatService.MarkChatRead`  (lines 269–293)

```
def MarkChatRead(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental one-off client call to mark a chat as read. It is useful when code wants a direct remote call rather than a reusable stub object.

**Data flow**: It receives a mark-read request, target address, and optional call settings. It turns the request into bytes, sends it to the `MarkChatRead` RPC path, parses the empty success response, and returns it.

**Call relations**: This is used on the client side. It delegates the actual network work to gRPC’s experimental unary request/response call path, which reaches the server’s registered `MarkChatRead` method.


##### `ChatService.SetBackground`  (lines 296–320)

```
def SetBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call to set a chat background. It packages the background request for the remote chat service.

**Data flow**: It receives a set-background request, destination target, and optional settings like credentials or timeout. It serializes the request, sends it to the `SetBackground` RPC path, parses the empty response that means success, and returns that response.

**Call relations**: Client code can call this static helper as a shortcut. gRPC carries the request to whatever server implementation was registered for `SetBackground`.


##### `ChatService.RemoveBackground`  (lines 323–347)

```
def RemoveBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call to remove a chat background. It hides the low-level method path and byte conversion from the caller.

**Data flow**: It receives a remove-background request, target server, and optional call controls. It serializes the request, sends it to the `RemoveBackground` RPC path, parses the empty success response, and returns it.

**Call relations**: This client-side helper hands off to gRPC’s experimental unary call machinery. On the server side, the request is delivered to the registered `RemoveBackground` handler.


##### `ChatService.ShareContactInfo`  (lines 350–374)

```
def ShareContactInfo(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call to share contact information into a chat. It provides a shortcut for invoking that remote operation.

**Data flow**: It receives a share-contact-info request, target address, and optional settings. It converts the request object into bytes, sends it to the `ShareContactInfo` RPC path, parses the empty response, and returns it.

**Call relations**: Client code may use this helper for a one-off call. gRPC routes the request over the network to the server method registered as `ShareContactInfo`.


##### `ChatService.SetTyping`  (lines 377–401)

```
def SetTyping(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call to send a typing indicator. This is for a short-lived signal rather than a stored chat change.

**Data flow**: It receives a typing request, target server, and optional settings such as timeout or metadata. It serializes the request, sends it to the `SetTyping` RPC path, parses the empty response, and returns it.

**Call relations**: This client helper passes the work to gRPC’s experimental unary call support. The server receives it through the registered `SetTyping` method.


##### `ChatService.GetChat`  (lines 404–428)

```
def GetChat(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call to fetch details for a chat. It returns the remote service’s chat response object.

**Data flow**: It receives a get-chat request, target address, and optional call settings. It serializes the request, sends it to the `GetChat` RPC path, converts the returned bytes into a get-chat response, and returns that response.

**Call relations**: Client code can use this as a shortcut when it does not need a reusable stub. gRPC carries the call to the server implementation registered for `GetChat`.


##### `ChatService.GetChatCount`  (lines 431–455)

```
def GetChatCount(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call to ask how many chats match the request. It wraps the network details for the count operation.

**Data flow**: It receives a chat-count request, a target server, and optional settings. It serializes the request, sends it to the `GetChatCount` RPC path, parses the returned bytes as a chat-count response, and returns that response.

**Call relations**: This helper is used by clients for one-off count requests. gRPC delivers the request to the server method registered under `GetChatCount`.


##### `ChatService.HasBackground`  (lines 458–482)

```
def HasBackground(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call to ask whether a chat has a background. It returns the remote service’s yes-or-no style response object.

**Data flow**: It receives a has-background request, target address, and optional call controls. It serializes the request, sends it to the `HasBackground` RPC path, parses the response bytes as a has-background response, and returns it.

**Call relations**: Client code can use this static method instead of a stub method. gRPC routes the request to the server handler registered for `HasBackground`.


##### `ChatService.SubscribeChatEvents`  (lines 485–509)

```
def SubscribeChatEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Makes an experimental direct client call that subscribes to a stream of chat events. Unlike the other helpers, it expects many responses over time instead of one immediate response.

**Data flow**: It receives a subscription request, target server, and optional settings. It serializes the request, opens the `SubscribeChatEvents` RPC path, and returns a stream-like result that yields event response objects as the server sends them.

**Call relations**: Client code uses this when it wants to watch chat changes. It hands off to gRPC’s experimental unary-to-stream support, which connects to the server’s registered streaming `SubscribeChatEvents` method.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2_grpc.py`

`generated` · `startup and request handling`

This file is machine-made from a protocol buffer service definition. Protocol buffers describe messages and services in a language-neutral way; gRPC is the network system that uses those definitions so one program can call another program as if it were calling a local function. Here, the service is about catching up on durable iMessage-related events, such as messages, group changes, polls, and chats.

The main problem it solves is consistency after a client has fallen behind. A client can ask for all events newer than a known sequence number, receive a stream of catch-up responses, and then switch to live event subscriptions without leaving a gap. Think of it like watching a recorded recap before joining a live broadcast.

The file provides three pieces. EventServiceStub is used by clients; it knows the exact remote method name and how to turn request and response objects into bytes and back. EventServiceServicer is the base class a server-side implementation inherits from; by default it says the method is not implemented. add_EventServiceServicer_to_server connects a real servicer object to a gRPC server. EventService offers an experimental one-shot helper for making the same call without manually building a stub. The version check at import time helps ensure the installed gRPC library is new enough for this generated code.

#### Function details

##### `EventServiceStub.__init__`  (lines 45–55)

```
def __init__(self, channel)
```

**Purpose**: Creates a client-side handle for calling the remote CatchUpEvents method. A caller uses this when it has a gRPC channel to a server and wants to request a stream of missed events.

**Data flow**: It receives a gRPC channel, which represents an open path to a server. It attaches a CatchUpEvents callable to the stub, telling gRPC the remote method name, how to serialize a CatchUpEventsRequest into bytes, and how to deserialize each CatchUpEventsResponse from bytes. After this, the stub object can start the remote catch-up stream.

**Call relations**: Client code creates this stub before asking the EventService server for events. The stub does not implement the event logic itself; it prepares the network call so gRPC can send the request to the server-side CatchUpEvents implementation.


##### `EventServiceServicer.CatchUpEvents`  (lines 75–79)

```
def CatchUpEvents(self, request, context)
```

**Purpose**: Defines the server-side method that should stream catch-up events to a client. In this generated base class it is only a placeholder, so real server code must override it.

**Data flow**: It receives a request and a gRPC context object, which carries status and call information. Because no real behavior is provided here, it marks the response as UNIMPLEMENTED, adds a message saying the method is not implemented, and raises an error. Nothing useful is streamed unless a project-specific subclass replaces this method.

**Call relations**: A real server implementation is expected to inherit from EventServiceServicer and provide its own CatchUpEvents behavior. add_EventServiceServicer_to_server later registers that implementation with the gRPC server so incoming network calls reach it.


##### `add_EventServiceServicer_to_server`  (lines 82–93)

```
def add_EventServiceServicer_to_server(servicer, server)
```

**Purpose**: Connects a server-side EventService implementation to a running gRPC server. Without this registration step, incoming CatchUpEvents requests would not know which Python method to call.

**Data flow**: It receives a servicer object and a gRPC server. It builds a method handler for CatchUpEvents, including the request parser and response serializer, then groups that handler under the full service name photon.imessage.v1.EventService. It adds those handlers to the server, changing the server so it can accept this service's requests.

**Call relations**: Server startup code calls this after creating a concrete servicer. Inside, it asks gRPC to build a unary-stream method handler, meaning one request comes in and many response messages can stream back, and then wraps that in a generic service handler before registering it on the server.

*Call graph*: 2 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler).


##### `EventService.CatchUpEvents`  (lines 115–139)

```
def CatchUpEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental shortcut for making a CatchUpEvents remote call without first creating an EventServiceStub object. It is useful for code that wants to call the service directly by target address and options.

**Data flow**: It receives a CatchUpEvents request, a target server address, and optional connection settings such as credentials, timeout, compression, and metadata. It passes these into gRPC's experimental unary-stream helper, along with the method path and the functions that convert requests and responses to and from bytes. The result is a stream of CatchUpEventsResponse messages from the remote server.

**Call relations**: This is an alternate client path to the same remote service method set up by EventServiceStub.__init__. It hands the actual network work to grpc.experimental.unary_stream, while the server side must still have registered an implementation through add_EventServiceServicer_to_server.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2_grpc.py`

`generated` · `request handling`

This file is like the phone switchboard for the message service. It does not decide how to send, edit, list, or subscribe to messages. Instead, it makes sure each named remote procedure call, or RPC (a function call made over the network), is connected to the correct network path and uses the correct protobuf message format. Protobuf messages are compact structured data objects shared by client and server.

On the client side, `MessageServiceStub` turns a gRPC channel into easy-to-call attributes such as `SendTextMessage` or `ListChatMessages`. Each one knows how to turn the request object into bytes before sending it and how to turn the response bytes back into a Python object.

On the server side, `MessageServiceServicer` is a base class with one method per service action. These methods intentionally return “not implemented” until real server code subclasses it and supplies the actual behavior. The helper `add_MessageServiceServicer_to_server` registers that real servicer with a gRPC server so incoming network calls are routed to the right Python method.

The final `MessageService` class offers experimental one-shot client helpers. Without this file, clients and servers could still have message definitions, but they would not have the shared network glue needed to call the service reliably.

#### Function details

##### `MessageServiceStub.__init__`  (lines 46–126)

```
def __init__(self, channel)
```

**Purpose**: Builds a client-side object for calling the remote message service. Someone uses it when they already have a gRPC channel and want Python methods for sending, editing, reading, and subscribing to messages.

**Data flow**: It receives a gRPC channel, which is the network connection to a server. It creates callable attributes for each service method, attaching the correct service path plus the functions that convert request objects to bytes and response bytes back to objects. After construction, the stub is ready for client code to call those remote methods.

**Call relations**: Client code creates this stub before making message-service calls. Each generated attribute hands requests to the gRPC channel, which carries them to the server and returns decoded responses.


##### `MessageServiceServicer.SendTextMessage`  (lines 146–159)

```
def SendTextMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a plain text message. Real server code is expected to override it with the actual send behavior.

**Data flow**: It receives a text-message request and a gRPC context object that can carry status information. Because this generated base method has no real implementation, it marks the call as unimplemented and raises an error. Nothing is sent and no useful response is produced.

**Call relations**: When a server registers a subclass through `add_MessageServiceServicer_to_server`, incoming `SendTextMessage` calls are routed to the subclass method instead of this placeholder.


##### `MessageServiceServicer.SendAttachmentMessage`  (lines 161–165)

```
def SendAttachmentMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a message with an attachment. It exists as a contract that real server code must fill in.

**Data flow**: It receives an attachment-message request and the gRPC call context. The base version records an unimplemented status in the context and raises an error, so the caller learns that no server behavior was supplied.

**Call relations**: The registration helper connects the service name to this method slot. A real servicer subclass supplies the working version used during incoming attachment-message calls.


##### `MessageServiceServicer.SendMultipartMessage`  (lines 167–171)

```
def SendMultipartMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a message made of multiple parts, such as text plus media. The generated method is only a required shape, not the real work.

**Data flow**: It takes a multipart-message request and a gRPC context. It sets the context status to unimplemented, raises an error, and returns no message response.

**Call relations**: Incoming multipart send requests reach this method position through the registered gRPC handler. Production code should override it in a subclass.


##### `MessageServiceServicer.SendCustomizedMiniAppMessage`  (lines 173–178)

```
def SendCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for sending a customized iMessage mini-app card. A mini-app card is an interactive message backed by an app extension.

**Data flow**: It receives the mini-app send request and call context. The generated base method only reports that the method is not implemented and raises an error.

**Call relations**: The gRPC server wiring points this RPC name at the servicer method. A real implementation replaces this placeholder when the application wants to support mini-app card sending.


##### `MessageServiceServicer.UpdateCustomizedMiniAppMessage`  (lines 180–185)

```
def UpdateCustomizedMiniAppMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for updating an existing customized mini-app card. Real code must override it to change the card contents.

**Data flow**: It receives an update request and the gRPC context. It marks the call as unimplemented and raises an error, so no message snapshot is returned.

**Call relations**: This method is the server-side slot for the update RPC. The registration helper exposes the slot, while application code supplies the actual behavior in a subclass.


##### `MessageServiceServicer.EditMessage`  (lines 187–191)

```
def EditMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for editing a previously sent message. It is generated so clients and servers agree that this operation exists.

**Data flow**: It receives an edit request and context. The base method sets an unimplemented status, raises an error, and changes no message.

**Call relations**: A registered server routes edit requests to this method name. A concrete servicer should override it to perform the edit and return the updated message.


##### `MessageServiceServicer.UnsendMessage`  (lines 193–198)

```
def UnsendMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for retracting, or unsending, an existing message. Real server code must provide the actual retraction behavior.

**Data flow**: It receives an unsend request and the gRPC context. Since the generated base class cannot perform the action, it reports unimplemented and raises an error instead of returning an empty success response.

**Call relations**: The server registration maps the `UnsendMessage` RPC to this method slot. A subclass is expected to replace it for real requests.


##### `MessageServiceServicer.SetReaction`  (lines 200–204)

```
def SetReaction(self, request, context)
```

**Purpose**: Defines the server-side placeholder for adding, changing, or removing a reaction on a message. The base version is only a template.

**Data flow**: It receives a reaction request and context. It marks the call as unimplemented, raises an error, and returns no updated message.

**Call relations**: Incoming reaction calls are routed to this method name by the generated server wiring. Real application logic should override it.


##### `MessageServiceServicer.PlaceSticker`  (lines 206–210)

```
def PlaceSticker(self, request, context)
```

**Purpose**: Defines the server-side placeholder for placing a sticker on a message. A sticker is a visual decoration attached to an existing message.

**Data flow**: It receives a sticker-placement request and context. The base method records an unimplemented status and raises an error, leaving all message state unchanged.

**Call relations**: The gRPC registration exposes this method as the target for sticker-placement calls. A concrete server implementation supplies the useful behavior.


##### `MessageServiceServicer.NotifySilencedMessage`  (lines 212–217)

```
def NotifySilencedMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for triggering Apple's per-message “Notify Anyway” action. This action attempts to alert someone even if notifications are silenced.

**Data flow**: It receives a notify request and the call context. The generated base method says the call is unimplemented and raises an error instead of returning an empty success result.

**Call relations**: The generated server mapping connects the notify RPC to this method slot. A real servicer overrides it when that feature is supported.


##### `MessageServiceServicer.GetMessage`  (lines 219–224)

```
def GetMessage(self, request, context)
```

**Purpose**: Defines the server-side placeholder for reading one message. It marks the expected method shape for real server code.

**Data flow**: It receives a get-message request and context. Because the base class does not know where messages are stored, it sets an unimplemented status, raises an error, and returns no message data.

**Call relations**: When the service is registered, incoming get-message calls are directed to this method name. The application’s subclass should fetch and return the requested message.


##### `MessageServiceServicer.ListRecentMessages`  (lines 226–233)

```
def ListRecentMessages(self, request, context)
```

**Purpose**: Defines the server-side placeholder for listing recent messages, optionally filtered by fields such as sender, read state, or time range. Real server code must implement the lookup.

**Data flow**: It receives a list request and context. The generated base method cannot query storage, so it marks the call as unimplemented, raises an error, and returns no list.

**Call relations**: The registration helper exposes this method as the server target for recent-message listing. A concrete servicer supplies the actual database or projection read.


##### `MessageServiceServicer.ListChatMessages`  (lines 235–240)

```
def ListChatMessages(self, request, context)
```

**Purpose**: Defines the server-side placeholder for listing messages in one chat. It exists so the network contract has a clear place for this read operation.

**Data flow**: It receives a chat-message list request and context. The base version reports unimplemented and raises an error, producing no list of messages.

**Call relations**: Registered servers route `ListChatMessages` RPC calls to this method slot. Real code overrides it to read messages for the requested chat.


##### `MessageServiceServicer.GetEmbeddedMedia`  (lines 242–250)

```
def GetEmbeddedMedia(self, request, context)
```

**Purpose**: Defines the server-side placeholder for fetching media embedded in a message. This is for raw media bytes, such as image or file data, rather than ordinary JSON-friendly text.

**Data flow**: It receives an embedded-media request and context. The base method marks the call as unimplemented and raises an error, returning no media bytes.

**Call relations**: The generated gRPC wiring can route media-fetch calls here. A real server implementation must override it, often alongside special HTTP handling for raw bytes.


##### `MessageServiceServicer.SubscribeMessageEvents`  (lines 252–258)

```
def SubscribeMessageEvents(self, request, context)
```

**Purpose**: Defines the server-side placeholder for subscribing to live message events. A subscription is a stream where the server can send many updates over time.

**Data flow**: It receives a subscription request and context. The base method reports that streaming message events is not implemented and raises an error, so no event stream starts.

**Call relations**: The registration helper uses a streaming RPC handler for this method. A concrete servicer overrides it to yield message-event updates to connected clients.


##### `add_MessageServiceServicer_to_server`  (lines 261–342)

```
def add_MessageServiceServicer_to_server(servicer, server)
```

**Purpose**: Registers a real message-service implementation with a gRPC server. Without this, the server would not know which Python methods should answer incoming message-service network calls.

**Data flow**: It takes a servicer object and a gRPC server. It builds a table that maps each RPC name to the matching servicer method, with the right request parser and response writer for each protobuf type. It then adds that table to the server, changing the server so it can receive and dispatch these RPCs.

**Call relations**: Server startup code calls this after creating a concrete servicer. Inside, it asks gRPC to build unary request-response handlers and one unary-to-stream handler, then wraps them with `grpc.method_handlers_generic_handler` so the server can route calls under the `photon.imessage.v1.MessageService` service name.

*Call graph*: 3 external calls (method_handlers_generic_handler, unary_stream_rpc_method_handler, unary_unary_rpc_method_handler).


##### `MessageService.SendTextMessage`  (lines 364–388)

```
def SendTextMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for sending a text message without first creating a stub object. It is useful when a caller wants to make this single remote call directly.

**Data flow**: It receives a request, a target server address, and optional connection settings such as credentials, timeout, metadata, and compression. It serializes the text-message request, sends it to the `SendTextMessage` RPC path, and decodes the returned message response.

**Call relations**: Client code may call this static helper directly. It hands the work to `grpc.experimental.unary_unary`, which performs the actual network request and response decoding.


##### `MessageService.SendAttachmentMessage`  (lines 391–415)

```
def SendAttachmentMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for sending a message with an attachment. It avoids the separate step of building a `MessageServiceStub`.

**Data flow**: It receives an attachment request, target address, and optional call settings. It turns the request into bytes, sends it to the attachment-send RPC path, and turns the response bytes into a message response object.

**Call relations**: Client code calls this when it wants the generated helper style. The method delegates the real network call to `grpc.experimental.unary_unary`.


##### `MessageService.SendMultipartMessage`  (lines 418–442)

```
def SendMultipartMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for sending a multi-part message. This covers messages made from several pieces rather than one simple body.

**Data flow**: It takes the multipart request plus target and optional connection details. It serializes the request, calls the `SendMultipartMessage` RPC, and deserializes the returned message snapshot.

**Call relations**: This helper sits on the client side. It passes all connection and serialization details to `grpc.experimental.unary_unary`.


##### `MessageService.SendCustomizedMiniAppMessage`  (lines 445–469)

```
def SendCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for sending a customized iMessage mini-app card. It is a direct-call alternative to using the stub.

**Data flow**: It receives the mini-app send request, the target server, and optional gRPC settings. It sends the serialized request to the matching RPC path and returns the decoded message response.

**Call relations**: Client code can use this static method for a single mini-app send call. The generated helper relies on `grpc.experimental.unary_unary` to contact the server.


##### `MessageService.UpdateCustomizedMiniAppMessage`  (lines 472–496)

```
def UpdateCustomizedMiniAppMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for updating a customized mini-app card message. It packages the network call in one static method.

**Data flow**: It receives an update request, target server, and optional settings. It serializes the update request, sends it to the update RPC path, and decodes the returned updated message response.

**Call relations**: A client may call this directly instead of creating a stub. The actual RPC is performed by `grpc.experimental.unary_unary`.


##### `MessageService.EditMessage`  (lines 499–523)

```
def EditMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for editing an existing message. It is a direct wrapper around the remote edit operation.

**Data flow**: It takes an edit request, target, and optional call options. It converts the request to bytes, sends it to the edit RPC path, and returns the decoded message response.

**Call relations**: Client code uses this when making a direct edit call. It delegates the network send and receive work to `grpc.experimental.unary_unary`.


##### `MessageService.UnsendMessage`  (lines 526–550)

```
def UnsendMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for retracting a message. A successful call returns an empty response object, meaning there is no extra payload beyond success.

**Data flow**: It receives an unsend request, target server, and optional settings. It serializes the request, calls the unsend RPC path, and decodes the server’s empty response.

**Call relations**: This is a client-side shortcut. It hands the remote call to `grpc.experimental.unary_unary`, using the empty protobuf response type for decoding.


##### `MessageService.SetReaction`  (lines 553–577)

```
def SetReaction(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for setting a reaction on a message. It wraps the reaction RPC in a direct static call.

**Data flow**: It receives a reaction request, target, and optional gRPC settings. It serializes the request, sends it to the reaction RPC path, and returns the decoded updated message response.

**Call relations**: Client code can call this helper instead of going through a stub instance. The method delegates transport details to `grpc.experimental.unary_unary`.


##### `MessageService.PlaceSticker`  (lines 580–604)

```
def PlaceSticker(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for placing a sticker on a message. It is a convenience wrapper for the remote sticker operation.

**Data flow**: It takes a sticker-placement request, target server, and optional call settings. It serializes the request, sends it to the sticker RPC path, and decodes the message response returned by the server.

**Call relations**: Client code uses this for direct sticker-placement calls. It relies on `grpc.experimental.unary_unary` for the network exchange.


##### `MessageService.NotifySilencedMessage`  (lines 607–631)

```
def NotifySilencedMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for triggering “Notify Anyway” on a silenced message. It reports success with an empty response when the server accepts the action.

**Data flow**: It receives a notify request, target server, and optional connection choices. It serializes the request, calls the notify RPC path, and decodes an empty protobuf response.

**Call relations**: This client-side helper sends the request through `grpc.experimental.unary_unary`. It is an alternative to using the same method on a `MessageServiceStub`.


##### `MessageService.GetMessage`  (lines 634–658)

```
def GetMessage(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for fetching one message. It wraps the remote read in a single static method.

**Data flow**: It receives a get-message request, target, and optional call settings. It serializes the request, sends it to the get-message RPC path, and decodes the get-message response.

**Call relations**: Client code may call this directly for a single read. The helper passes the request to `grpc.experimental.unary_unary`.


##### `MessageService.ListRecentMessages`  (lines 661–685)

```
def ListRecentMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for asking the server for recent messages. Optional fields in the request can narrow the list.

**Data flow**: It takes a recent-message list request, target server, and optional gRPC settings. It serializes the request, sends it to the recent-list RPC path, and decodes the list response.

**Call relations**: This is a client convenience method. It delegates the actual request-response exchange to `grpc.experimental.unary_unary`.


##### `MessageService.ListChatMessages`  (lines 688–712)

```
def ListChatMessages(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for listing messages in a particular chat. It is a direct wrapper around the chat-message listing RPC.

**Data flow**: It receives a chat-message list request, target, and optional settings. It converts the request to bytes, calls the chat-list RPC path, and returns the decoded list response.

**Call relations**: Client code can use this static helper instead of a stub. The network work is passed to `grpc.experimental.unary_unary`.


##### `MessageService.GetEmbeddedMedia`  (lines 715–739)

```
def GetEmbeddedMedia(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for fetching media embedded in a message. It is meant for data such as image or file bytes carried in the protobuf response.

**Data flow**: It takes an embedded-media request, target server, and optional call options. It serializes the request, sends it to the embedded-media RPC path, and decodes the media response.

**Call relations**: This helper is used by client code that wants a direct media-fetch RPC. It delegates the actual call to `grpc.experimental.unary_unary`.


##### `MessageService.SubscribeMessageEvents`  (lines 742–766)

```
def SubscribeMessageEvents(request, target, options=(), channel_credentials=None, call_credentials=None, insecure=False, compression=None, wait_for_ready=None, timeout=None, metadata=None)
```

**Purpose**: Provides an experimental one-shot client helper for subscribing to live message events. Unlike the ordinary helpers, this returns a stream of responses over time.

**Data flow**: It receives a subscription request, target, and optional connection settings. It serializes the request, opens the subscription RPC, and decodes each event response as the server sends it.

**Call relations**: Client code calls this to start a live event stream. It hands the work to `grpc.experimental.unary_stream`, which supports one request followed by many server responses.
