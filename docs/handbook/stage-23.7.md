# iMessage v1 Generated Protobuf Message and Service Types  `stage-23.7`

This stage is shared behind-the-scenes support for the iMessage extension. It is not the code that starts the app or performs the user action itself. Instead, it provides the common “forms” that other code fills in and reads. These files are generated from Protocol Buffers, a compact data format used so different parts of a system can agree on the exact shape of a message.

The address types describe contact addresses and which chat service they use. Attachment types describe photos, files, stickers, and related media, while the attachment service file defines the remote calls for working with them. Chat types describe chats and chat events, and the chat service file defines actions such as creating chats, marking them read, and watching for updates. Message types cover texts, edits, reactions, stickers, and read events, while the message service file defines sending and changing messages. Event service types support catching up on streams of changes. Group types record joins, leaves, and renames. Poll types describe options, votes, and poll changes. Streaming adds a heartbeat message to keep live connections alive.

## Files in this stage

### Addresses and Attachments
Shared address records and attachment API/type definitions establish how iMessage contacts and media payloads are represented.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/address_types_pb2.py`

`generated` · `cross-cutting`

This file is machine-made glue code for Protocol Buffers, often called protobuf: a format for defining data messages once and using them safely across different programs and languages. Here, the data is about messaging addresses, such as a phone number or email address, and the services connected to that address, such as iMessage, SMS, or RCS.

The file registers the schema named `photon/imessage/v1/address_types.proto` with Python's protobuf runtime. Once loaded, it creates Python message classes for two shapes of data: one address tied to a single service, and one address tied to multiple services. Both can also include an optional country field. It also defines the allowed service values, so code uses clear fixed choices instead of loose strings.

Think of this file like a pre-printed form template. Other code fills in the blanks, but this file defines what blanks exist and what kinds of answers are allowed. Because it is generated, people should not edit it by hand; changes should be made in the original `.proto` schema and regenerated. Without this file, Python code in this extension would not know how to create, parse, or validate these address messages in the shared protobuf format.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2.py`

`generated` · `import time and request handling`

This file is machine-made from a Protocol Buffers definition file. Protocol Buffers, often called protobuf, are a compact way for programs to agree on the shape of data they send to each other. In everyday terms, this file is like a printed set of blank forms and mailing rules for attachment operations: asking for attachment details, uploading an attachment, and downloading one.

It defines the Python message classes for requests and responses such as getting attachment information, uploading attachment bytes, and streaming downloaded attachment data. It also records the service named AttachmentService, including the available calls. One call has an HTTP path attached to it, so tools can understand that a request for attachment information maps to a URL like /v1/attachments/{attachment_guid}.

The file does not contain hand-written business logic. Instead, it registers a serialized description of the service with Google's protobuf runtime, which then builds the usable Python classes at import time. Other parts of the project import this module when they need reliable, shared data containers for iMessage attachment traffic. Without it, the client and server code would not have the generated Python types needed to encode, decode, or describe attachment service messages correctly.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_types_pb2.py`

`generated` · `cross-cutting serialization/deserialization`

This file is machine-made from a Protocol Buffers schema, which is a language-neutral way to describe structured data. In plain terms, it is a shared form for attachment records: every part of the system can agree that an attachment has fields like a unique id, file name, MIME type, size, transfer state, and whether it is hidden or a sticker.

The file does not contain hand-written business logic. Instead, it registers the attachment schema with Google’s Protocol Buffers runtime and asks that runtime to build Python classes from it. Those classes include messages such as AttachmentInfo, CompanionInfo, and Companion. A “message” here is like a labeled container for data. It also defines enums, which are fixed sets of named values, such as TransferState for whether an attachment is pending, transferring, failed, finished, unavailable, or unknown.

This matters because attachment data often has to cross boundaries: between processes, over storage, or through APIs. Without this generated file, Python code in this extension would not know how to create, parse, or validate these attachment-shaped protobuf messages. The important caution is that this file should not be edited by hand; changes should be made in the original .proto schema and regenerated.


### Chats and Conversation Events
Chat service and type definitions lead into event-stream and group-change records for conversation-level activity.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2.py`

`generated` · `cross-cutting: imported whenever chat API messages or service descriptors are needed`

This file is produced from a Protocol Buffers definition, not written by hand. Protocol Buffers are a compact, structured way for programs to describe data they send to each other. Think of this file as the official set of forms for the chat service: every request and response has named boxes, expected types, and rules about what can be present.

The file registers message types such as CreateChatRequest, CreateChatResponse, GetChatRequest, SetTypingRequest, and SubscribeChatEventsResponse. These messages describe the data needed for common chat operations: who to start a chat with, what the first message contains, which chat should be marked read, whether someone is typing, and what kind of chat event was received.

It also describes the ChatService service itself. That service lists remote operations such as CreateChat, MarkChatRead, GetChat, GetChatCount, HasBackground, and SubscribeChatEvents. Some of these operations include HTTP path annotations, which are hints for exposing the same service over web-style endpoints like /v1/chats:get.

Nothing here contains the real business logic for iMessage. It does not create chats or read from a database. Instead, it gives clients and servers a shared language. Without it, different parts of the system could disagree about the exact fields, names, and formats used to call the chat API.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_types_pb2.py`

`generated` · `import time and data serialization/deserialization`

This file is not handwritten application logic. It is produced by the Protocol Buffers compiler from a source definition file called `chat_types.proto`. Protocol Buffers, often called protobuf, are like a shared form template: every system that uses the template agrees on the same fields, names, and data shapes, so data can move safely between programs.

The file defines Python classes for chat records and chat change events. A `Chat` can include identifiers, display name, whether it is a group chat, service type, archive/filter state, unread count, participants, and the last message. A `ChatChangeEvent` records something that happened to a chat, such as the background changing, the chat being marked read, archived, or unarchived. Some fields are optional, meaning the data can say both “this value is absent” and “this value is present but empty.”

It also links to other generated protobuf files for timestamps, addresses, and messages, so chat data can include people and messages using the same shared format. Without this file, Python code in this extension would not have the concrete classes needed to serialize and deserialize chat information consistently.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2.py`

`generated` · `import time and event streaming`

This file is produced by the Protocol Buffers compiler, not written by hand. Protocol Buffers, often called “protobuf,” are a compact way for different programs to agree on the shape of messages they send to each other. Think of the `.proto` file as a form template, and this Python file as the stack of ready-to-use forms Python can fill in, send, receive, and inspect.

The file defines the Python-side message classes for an event service. That service lets a client ask, “What changed after this sequence number?” and then receive a stream of responses. Those responses may describe a changed message, group, poll, or chat; mark the catch-up as complete; or carry a heartbeat, which is a small “still alive” signal used during long-running streams.

It also registers an `EventService` description with one streaming-style operation, `CatchUpEvents`. The actual networking code is not here; this file only describes the message shapes and service contract so other generated or runtime code can serialize and deserialize the data correctly.

Because it is generated, editing it directly would be risky. Changes should be made in the source `.proto` file and regenerated. Without this file, Python code in this extension would not know the exact wire format for event catch-up requests and responses.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/group_types_pb2.py`

`generated` · `cross-cutting serialization and deserialization`

This file is produced by the Protocol Buffers compiler. Protocol Buffers, often called protobuf, are a compact data format used to describe structured messages in a way that different programs can share safely. In plain terms, this file is like a set of pre-made forms for group chat events: one form for a changed display name, one for a participant being added, one for a participant leaving, and so on.

The most important message described here is `GroupChangeEvent`. It records which chat changed, when it happened, who caused it if known, whether it came from the current user, and exactly what kind of change occurred. Only one specific change type is meant to be filled in at a time, such as `participant_added` or `icon_removed`; this is protobuf’s “oneof” idea, meaning “choose one box from this group.”

The file also connects these group events to shared message types from other generated files, such as a timestamp and an iMessage address identity. At import time, it registers the message shapes with protobuf’s runtime library so the rest of the extension can serialize them to bytes or rebuild them from bytes. Because it is generated code, people should not edit it directly; changes should be made in the original `.proto` schema instead.


### Messages and Polls
Message service and payload definitions cover sending, editing, reactions, read events, and poll-related message data.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2.py`

`generated` · `import time and API request/response serialization`

This file is machine-written from a Protocol Buffers definition. Protocol Buffers, often called “protobuf,” are a compact way for programs to agree on the exact shape of data they exchange, like a shared order form where every box has a name and expected type. Without this file, Python code in this project would not know how to create or understand the request and response objects used by the iMessage message service.

The file registers a large serialized service definition with Google’s protobuf runtime. From that definition, it creates Python classes for many message-related data containers: targets that identify a chat or message, requests to send text or attachments, mini app card layouts, edit and unsend requests, reaction and sticker requests, list and lookup requests, and streaming event responses. It also records the service methods, such as sending a text message, listing recent messages, and subscribing to message events.

Some service methods also include HTTP path annotations, which describe how these remote procedure calls can map to web-style endpoints such as `/v1/messages:sendText`. The file does not contain hand-written business logic. Its job is to be the trusted translation layer between Python objects and the wire format used when this API talks to other parts of the system.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_types_pb2.py`

`generated` · `cross-cutting`

This file is produced by the Protocol Buffers compiler, not written by hand. Protocol Buffers, often called protobuf, are a way to describe data once and then generate code that can read and write that data consistently across different programming languages. Think of it like a shared form: every system fills in the same boxes, so everyone understands what the message means.

Here, the shared forms are for iMessage data. They cover the main message body, attachments, text formatting, mentions, replies, stickers, reactions, mini-app content, and message lifecycle events such as received, edited, read, unsent, reaction added, reaction removed, and sticker placed. The file also defines fixed lists of allowed values, such as message item types and reaction kinds, so code does not have to guess whether a reaction means “love,” “like,” “laugh,” or something else.

At import time, the file registers a serialized description of all these message types with Google’s protobuf runtime. That runtime then builds Python classes from the description. Other parts of the project can import this module and create, inspect, serialize, or parse these message objects. Without this file, Python code in this extension would not know the exact structure of iMessage protobuf records and could not safely exchange them with the rest of the system.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/poll_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-generated from a Protocol Buffers schema named `poll_types.proto`. Protocol Buffers, often called protobuf, are a way for different parts of a system to agree on the exact shape of structured data, much like a standardized form everyone fills out the same way. Here, the form is about iMessage polls: what the poll is called, what options it has, who voted, and what changed over time.

The file does not contain hand-written business logic. Instead, it registers message definitions with Google's protobuf runtime so Python code can create objects such as `PollOption`, `PollInfo`, and `PollChangeEvent`. Other code can then serialize those objects into bytes for storage or transport, or deserialize bytes back into readable Python objects.

It also imports shared message types. For example, poll events use a timestamp type for when something happened, and an address type for the person who acted. The most important behavior to know is that this file should not be edited directly. If the poll data shape needs to change, the source `.proto` file should be changed and this Python file regenerated.


### Streaming Heartbeats
Streaming protocol definitions provide the heartbeat message used to keep iMessage streaming connections alive.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/streaming_pb2.py`

`generated` · `import time and message serialization/deserialization`

This file is not handwritten application logic. It was produced by the Protocol Buffers compiler from `photon/imessage/v1/streaming.proto`. Protocol Buffers, often called “protobuf,” are a compact way for services to describe and exchange structured messages. Think of the `.proto` file as a form template, and this generated Python file as the ready-to-use Python version of that form.

The main thing this file contributes is the `Heartbeat` message type. A heartbeat message usually carries little or no content; its purpose is to say “I am still here” over a long-running connection. Without a generated file like this, Python code in the project would not have the correct class definitions and metadata needed to create, parse, serialize, or recognize this message in the same way as other services using the same protobuf definition.

At import time, the file checks that the installed protobuf runtime is compatible with the version used to generate it. It then registers the serialized message description with protobuf’s global descriptor pool, which is the lookup table protobuf uses to understand message shapes. Finally, protobuf’s builder utilities create the usable Python message classes and attach them to this module. Because this is generated code, developers should update the original `.proto` file and regenerate this file rather than editing it directly.
