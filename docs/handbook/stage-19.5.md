# Generated iMessage protobuf message contracts  `stage-19.5`

This stage is shared behind-the-scenes support. It does not start the app or run the main message loop by itself. Instead, it provides the “forms” that other code fills in when talking about iMessage data. Protocol Buffers are a compact data format; these generated Python files define the exact shapes of those data records so different parts of the system agree.

The Google annotation files describe how service calls can be linked to web-style HTTP methods and paths. The address file names the kinds of contact addresses, such as iMessage, SMS, or RCS. Attachment files define both attachment data and the service calls for uploading, downloading, creating, and reading it. Chat files define chat records and chat service requests, including creating chats, typing status, backgrounds, and chat event subscriptions. Group files describe group-change events. Message files define message records, reactions, edits, reads, stickers, and the service calls for sending, editing, listing, and subscribing. Poll files describe poll data. Event and streaming files define catch-up event requests and simple heartbeat messages used to keep a live connection recognizable.

## Files in this stage

### HTTP annotation support
Generated Google API annotation modules provide the shared HTTP mapping metadata used by service protobuf contracts.

### `extensions/imessage/ufo_ext_imessage/proto/google/api/annotations_pb2.py`

`generated` · `import time / protobuf setup`

This file exists so Python can understand a small piece of Google API metadata. Protocol Buffers, often called protobuf, are a compact way to describe structured data and service definitions. The original human-written file is `annotations.proto`; this Python file was produced automatically from it, so people should not edit it by hand.

Its main job is to register one protobuf extension named `http`. That extension attaches an `HttpRule` to a service method’s options. In plain terms, it lets an API say, “this internal service method can also be reached as an HTTP endpoint, such as GET `/v1/messages`.” Without this generated file, code that loads these API definitions would not know what that `http` annotation means, so HTTP-to-service mappings could not be read correctly.

The file imports protobuf runtime helpers, checks that the installed protobuf library is new enough, imports the related `http_pb2` definition that describes `HttpRule`, and then registers a serialized description of the annotation with protobuf’s shared descriptor pool. A descriptor is like a machine-readable blueprint for a message or option. Other generated protobuf files and runtime tools can then look up this blueprint when parsing or inspecting API definitions.


### `extensions/imessage/ufo_ext_imessage/proto/google/api/http_pb2.py`

`generated` · `import time, whenever protobuf API definitions are loaded`

This file is not handwritten project logic. It was produced by the Protocol Buffers compiler from `google/api/http.proto`. Protocol Buffers, often called protobuf, are a compact format for defining data shapes that can be shared across languages. Here, the data shapes describe HTTP rules: for example, which service method should answer a `GET /v1/messages/{id}` request, what part of the request becomes the body, and whether there are extra route patterns.

When Python imports this file, it first checks that the installed protobuf runtime is new enough for the generated code. It then registers a serialized description of the proto file with protobuf's global descriptor pool. A descriptor is like a blueprint: it says what messages exist, what fields they have, and how they fit together. The protobuf builder uses that blueprint to create Python message classes such as `Http`, `HttpRule`, and `CustomHttpPattern`.

Other generated API code can then import this module and refer to these message types without having to parse the original `.proto` file. Without this file, code that depends on Google-style HTTP annotations would fail to import or would not understand those annotation message definitions.


### Address and attachment contracts
Shared address labels and attachment service/type messages define how contacts and media payloads are represented.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/address_types_pb2.py`

`generated` · `cross-cutting data serialization`

This file is machine-made from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a compact way for different parts of a system to agree on the exact shape of data they send to each other. Think of it like a pre-printed form: every sender and receiver knows which boxes exist and what kind of value belongs in each box.

Here, the forms describe address information for chat services. One message, `SingleServiceAddressInfo`, represents one address tied to one service, such as an email or phone number used for iMessage or SMS. Another message, `MultiServiceAddressInfo`, represents one address that may work with several services. Both can also include an optional country value, which matters because phone numbers and messaging behavior can depend on country rules.

The file also defines `ChatServiceType`, a fixed list of allowed service names: unspecified, iMessage, SMS, and RCS. That fixed list prevents different parts of the program from inventing slightly different spellings for the same idea.

No human-written business logic lives here. At import time, the protobuf runtime reads the encoded schema and builds Python classes from it. Other code can then create, read, serialize, and deserialize these message objects safely and consistently.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2.py`

`generated` · `loaded at import time, then used during attachment request and response handling`

This file is machine-made from a Protocol Buffers definition, which is a shared schema for structured messages. In plain terms, it is like a printed form template: every part of the system agrees where the attachment ID, file name, file bytes, and attachment details belong, so messages can be passed around without guesswork.

It describes request and response message types for three attachment operations: getting information about an attachment, uploading an attachment, and downloading an attachment. Upload messages can include a file name, raw file data, and optional companion-device information. Download responses can carry either a header with attachment details or chunks of file data, separating metadata from the actual bytes being transferred.

The file also registers an `AttachmentService` service definition. That service includes methods named `GetAttachmentInfo`, `UploadAttachment`, and `DownloadAttachment`. One method also carries an HTTP mapping for `/v1/attachments/{attachment_guid}`, which lets compatible tooling expose the same operation over a web-style endpoint.

Because this is generated code, humans should not edit it directly. If the attachment API needs to change, the source `.proto` file should be changed and this file regenerated. Without this file, Python code would not know the exact message classes or service descriptors needed to speak this attachment API correctly.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_types_pb2.py`

`generated` · `cross-cutting data serialization`

This file is machine-made from a Protocol Buffers schema, which is a compact, language-neutral way to describe structured data. In plain terms, it is like a preprinted form for iMessage attachments: every part of the system agrees on the same fields, names, and allowed values.

The main message type is AttachmentInfo. It describes one attachment with details such as its unique id, file name, MIME type (the standard label for file content such as image/jpeg), file size, whether it was sent or received, whether it is hidden or a sticker, and its transfer state. TransferState is an enum, meaning a fixed set of allowed labels, such as pending, transferring, failed, or finished.

The file also defines companion attachment information. This matters for cases such as Live Photos, where an image may have an associated video companion. CompanionInfo stores metadata about that companion, while Companion stores the actual bytes plus its kind.

Because this file is generated, humans should not edit it directly. If the attachment schema needs to change, the source .proto file should be changed and this Python file regenerated. Without this file, Python code in the iMessage extension would not have the agreed-upon classes and constants needed to serialize, deserialize, and interpret attachment records safely.


### Chat and group contracts
Chat service, chat data, and group-change message shapes describe conversation state and membership updates.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2.py`

`generated` · `cross-cutting request and streaming message serialization`

This file is produced from a Protocol Buffers definition, often called a “proto” file. Protocol Buffers are a compact way to describe structured data and service calls so different parts of a system can exchange information without guessing what fields mean. In plain terms, this file is like a printed form library for chat actions: it defines what blanks exist on each form, such as a chat ID, a list of addresses, message text, or whether someone is typing.

The file registers those forms with Google’s protobuf runtime. Other code can then create objects such as CreateChatRequest, GetChatResponse, or SubscribeChatEventsResponse, fill them in, and serialize them for network use. It also describes the ChatService API itself: operations such as CreateChat, MarkChatRead, SetBackground, GetChat, GetChatCount, HasBackground, and SubscribeChatEvents. Some methods include HTTP path annotations, which tell gateway code how these service calls map onto web-style endpoints like /v1/chats:get.

Because this file is generated, people should not edit it by hand. The source of truth is the matching chat_service.proto file. If this generated module were missing or out of date, Python code using this chat API could fail to import, send fields in the wrong shape, or disagree with the server about what each chat operation expects.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is not handwritten application logic. It is generated from a Protocol Buffers definition, which is a schema that says exactly what fields a piece of data may contain. In plain terms, it is like a standardized form for iMessage chat information: chat ID, display name, whether it is a group chat, participants, unread count, last message, and so on.

It also defines small event-shaped messages for things that can happen to a chat, such as the chat being archived, unarchived, marked read, or having its background changed. The larger ChatChangeEvent message wraps those possibilities and records shared details like which chat changed, when it happened, who caused it, and whether it came from the current user.

The file imports related generated definitions for timestamps, addresses, and messages, then registers the chat schema with Google’s Protocol Buffers runtime. After that, other Python code can create, read, serialize, and deserialize these chat records without manually parsing raw bytes or dictionaries.

Because this is generated code, developers normally should not edit it directly. If the shape of chat data needs to change, the source .proto file should be changed and this file regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/group_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is produced automatically from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a compact way to describe data so different parts of a system can agree on exactly what fields exist and how they are encoded. You can think of this file like a printed form template: it does not decide what happened in a chat, but it defines the boxes that must be filled in when recording a group-chat event.

The messages described here cover common iMessage group changes. There are small message types for specific changes, such as a new display name, a participant being added or removed, someone leaving, and the group icon changing or being removed. The central message is `GroupChangeEvent`, which wraps one of those specific changes together with shared details: the chat identifier, when the event occurred, who caused it if known, and whether it came from the current user.

The file also connects to other protobuf definitions. It uses Google’s timestamp type for dates and times, and an address type from the iMessage protobuf package to describe participants and actors. Because this is generated code, humans should not edit it directly. If it were missing or out of date, code that expects these group-event message classes would fail to import them or could encode data in a way other components do not understand.


### Message and poll contracts
Message service, message data, and poll structures define the core send/edit/list interactions and rich message content.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2.py`

`generated` · `cross-cutting`

This file is produced automatically from a Protocol Buffers definition file. Protocol Buffers, often called protobuf, are a way to describe data messages once and then generate code that can read and write those messages in many languages. In everyday terms, this file is like a printed set of standardized forms for the messaging system: one form for sending text, another for sending an attachment, another for editing a message, and so on.

The file registers those forms with Google’s protobuf runtime. It defines request and response message types such as SendTextMessageRequest, MessageResponse, EditMessageRequest, ListChatMessagesRequest, and SubscribeMessageEventsResponse. It also describes the MessageService itself, including operations for sending text, sending attachments, sending multipart messages, updating mini app cards, adding reactions, placing stickers, fetching messages, listing messages, getting embedded media, and streaming message change events.

Some service methods also carry HTTP path information, such as /v1/messages:sendText or /v1/messages/{message_guid}. That lets the same API description support both remote procedure calls and HTTP-style access.

Because this is generated code, humans normally should not edit it directly. If it were missing or out of date, the rest of the iMessage extension could disagree about the exact fields in each API message, causing clients and servers to fail when they try to communicate.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_types_pb2.py`

`generated` · `cross-cutting`

This file is machine-made from a protocol buffer definition file, which is a language-neutral recipe for structured data. In plain terms, it is the project's shared form for describing iMessage activity. Without it, different parts of the system could disagree about what a message, reaction, attachment, or read receipt should look like, and data sent between components might not be understood.

The file registers a serialized schema with Google's Protocol Buffers library. Protocol Buffers are a compact way to turn structured objects into bytes and back again, like packing a labeled form into a small envelope and later unpacking it with all the labels intact. From that schema, the protobuf runtime builds Python classes such as Message, MessageContent, MessageReaction, MessageChangeEvent, and several smaller helper types.

The schema covers the main pieces of iMessage history and live changes: message text, attachments, mentions, formatting, mini-app content, timestamps, sender information, delivery and read status, edits, unsends, reactions, and stickers. It also defines enums, which are fixed sets of named choices, for message item types and reaction kinds.

Because this is generated code, humans should not edit it directly. Changes should be made in the original .proto file, then this Python file should be regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/poll_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-written from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a way to describe structured data once and then generate code for many languages. Here, the data is about iMessage polls: poll options, votes, full poll state, and events such as creating a poll, adding an option, voting, or removing a vote.

The file does not contain hand-written business logic. Instead, it registers a packed description of the poll message shapes with Google’s protobuf runtime. The runtime then builds usable Python classes such as PollOption, PollInfo, and PollChangeEvent. Other code can create these objects, fill in fields like the poll title or selected option identifier, and serialize them for storage or transport.

It also links to two outside message types: Google’s Timestamp for event times, and SingleServiceAddressInfo from the address types protobuf file for identifying a poll participant or actor. A useful way to think about this file is as a set of standardized forms. Everyone in the system fills out the same form fields, so poll data can move between components without each part inventing its own format.

Because this is generated code, it should not be edited directly. Changes should be made in the source .proto file and regenerated.


### Event streaming contracts
Event catch-up and streaming support messages define how clients receive ongoing or missed iMessage updates.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2.py`

`generated` · `import time and request/stream serialization`

This file is not handwritten application logic. It is produced from a Protocol Buffers definition file, which is a language-neutral contract for structured messages. In plain terms, it is like a printed form template: it says which boxes exist on the form, what kind of value each box can hold, and how the form is packed for sending between systems.

The messages here support an event catch-up service. A client can ask for events after a known sequence number, and the service can stream back changes such as message updates, group updates, poll updates, chat updates, a completion marker, or heartbeat messages that keep the stream alive. The response uses a “one of these” style payload, meaning each response item carries one main event type at a time.

At import time, the file registers these message definitions with Google’s protobuf runtime. It also imports related generated files for chat, group, message, poll, and streaming message types, because this event service refers to those shared event shapes. Other code should normally use the generated classes from this module, not edit this file directly. If this file were missing or out of date, Python clients and servers would not agree on how to encode or decode this event service’s messages.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/streaming_pb2.py`

`generated` · `import time and streaming message serialization`

This file is not hand-written application logic. It was produced by the Protocol Buffers compiler from `photon/imessage/v1/streaming.proto`. Protocol Buffers, often called protobuf, are a compact way for programs to describe and exchange structured messages, like a shared form both sides know how to read.

The practical job of this file is to register the streaming message schema with Google’s protobuf runtime. In this case, the schema is very small: it defines a `Heartbeat` message in the `photon.imessage.v1` package. A heartbeat is usually a tiny “I am still here” signal sent over a long-running connection so the other side knows the stream is alive.

When Python imports this module, it checks that the installed protobuf runtime is compatible with the version used to generate the file. It then loads a serialized description of the `.proto` file into protobuf’s descriptor pool, which is like a catalog of known message shapes. Finally, protobuf’s builder creates the Python message classes and related metadata from that catalog.

Without this generated file, Python code in this project would not have the concrete `Heartbeat` protobuf type available. Anything that needs to send, receive, serialize, or parse that streaming heartbeat message would fail or have to work with raw bytes by hand.
