# iMessage v1 generated protobuf message contracts  `stage-19.7`

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it provides the agreed “forms” that other code fills in when it talks about iMessage data. These files are generated from Protocol Buffers, a format for describing structured data so different parts of a system can read the same information in the same way.

The address file defines people’s contact addresses and which service they use, such as iMessage, SMS, or RCS. The attachment service and attachment type files describe how attachments are named, uploaded, downloaded, and tracked. The chat service and chat type files describe chats, participants, typing, read state, backgrounds, and chat updates. The event service file defines the stream of changes the system can follow to stay caught up. Group, message, and poll type files describe group changes, messages, reactions, edits, receipts, stickers, and poll data. The streaming file adds a heartbeat message, like a pulse, to keep live connections alive.

## Files in this stage

### Addressing and attachments
Foundational contracts for iMessage addresses and attachment lookup, upload, download, and metadata exchange.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/address_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-generated from a Protocol Buffers schema. Protocol Buffers, often called protobuf, are a compact data format used to define messages that different programs can read and write in the same way. In everyday terms, this file is like a printed form template: it says what fields an address record can have, what choices are allowed for the service type, and how that information should be packed for sending or stored for later.

The main ideas defined here are address information records. One record, `SingleServiceAddressInfo`, describes one address, one chat service, and optionally a country. Another, `MultiServiceAddressInfo`, describes one address that may work with several services. The service choices come from `ChatServiceType`, an enum, which means a fixed list of allowed labels: unspecified, iMessage, SMS, and RCS.

The code also registers these message shapes with Python's protobuf runtime. That registration lets the rest of the application create these message objects, serialize them into bytes, and read them back safely. Because it is generated, people should not edit it directly; changes should be made in the original `.proto` file and regenerated. Without this file, Python code in this extension would not know the exact structure of these address messages.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2.py`

`generated` · `request handling and serialization`

This file is machine-made from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the shape of data they send to each other. In everyday terms, this file is like a set of preprinted forms for the attachment part of an iMessage service: one form asks for attachment information, another carries uploaded file bytes, and another streams downloaded file bytes back.

The file registers those forms with Google's protobuf runtime so the rest of the project can create, read, serialize, and deserialize them in Python. It also describes an AttachmentService with three remote operations: getting attachment metadata by attachment GUID, uploading an attachment, and downloading an attachment. A GUID is a unique identifier, like a tracking number for a specific attachment.

Several message types refer to attachment-related types defined in a separate generated file, such as AttachmentInfo and CompanionInfo. The download response is shaped so it can carry either a header, a chunk of the main attachment data, or a chunk of companion data. That matters because downloads may be streamed in pieces rather than returned all at once.

Because this is generated code, people should not edit it directly. Changes should be made in the original .proto file, then this Python file should be regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_types_pb2.py`

`generated` · `serialization and data exchange`

This file is machine-generated from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the shape of data they exchange. In this case, the shared data is about iMessage attachments: file names, MIME types, sizes, whether the attachment was sent or received, whether it is hidden or a sticker, and what state its transfer is in.

The file does not contain hand-written business logic. Instead, it registers a serialized description of the attachment schema with Google’s protobuf runtime. The runtime then builds Python classes from that description, such as `AttachmentInfo`, `CompanionInfo`, and `Companion`. Other code can create these objects, fill in their fields, serialize them into bytes, or parse bytes back into structured Python objects.

The companion-related types are used for extra files that travel with an attachment, such as the video part of a Live Photo. The enum values, such as `TransferState` and `CompanionKind`, give stable names to fixed categories so different systems do not have to guess what a number means.

Because this file is generated, developers normally should not edit it directly. If the attachment format needs to change, the source `.proto` file should be changed and this Python file regenerated.


### Chat coordination
Service and data contracts for creating, updating, reading, and subscribing to iMessage chat state.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2.py`

`generated` · `import time and message serialization`

This file is produced automatically from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a way for different programs to agree on the exact shape of data they send to each other. Think of it like a printed form: both sides know which boxes exist, what each box means, and what kind of value belongs in it.

The file registers message types such as requests and responses for chat operations. For example, there are message shapes for creating a chat, sending an initial message, marking a chat as read, setting or removing a chat background, sharing contact information, setting typing status, getting a chat, counting chats, checking whether a background exists, and subscribing to live chat events. It also describes the ChatService service itself: the set of remote calls a client can make, including their request and response types.

The code imports related generated protobuf files for shared types like chats, messages, addresses, streaming heartbeat events, and Google API annotations. It then loads one compact serialized description into protobuf's global registry, which lets Python create the right classes at import time.

Because this is generated code, people should not edit it by hand. If it were missing or out of date, client and server code could disagree about the chat API, causing messages to fail to serialize, deserialize, or route correctly.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_types_pb2.py`

`generated` · `cross-cutting`

This is machine-generated Protocol Buffers code. Protocol Buffers, often called protobuf, are a structured way to describe data so different parts of a system can agree on exactly what a message looks like. Think of it like a standardized form: every sender and receiver knows which boxes exist, which ones are optional, and what kind of value each box can hold.

The file describes chat-level iMessage records. A Chat can include an internal identifier, a visible chat identifier, a group id, a display name, whether it is a group chat, whether it is archived or filtered, an unread count, a list of participants, and the last message. It also defines small event marker messages for chat background changes, read status changes, archiving, and unarchiving.

The most important event type here is ChatChangeEvent. It records which chat changed, when it happened, who caused it if known, whether it came from the local user, and exactly one kind of change. That “exactly one” choice is represented by protobuf as a oneof field, meaning the event can be a background change, a marked-read event, an archive event, and so on, but not several at once.

Because this file is generated, humans normally should not edit it directly. If the chat data shape needs to change, the source .proto file should be changed and this Python file regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2.py`

`generated` · `import time and event streaming`

This file is machine-made from a Protocol Buffers definition file. Protocol Buffers, or protobuf, are a compact format for describing structured data so different programs can exchange it reliably. In plain terms, this file is the dictionary Python uses to know what an iMessage event request or response looks like.

The main idea is “catching up” on events. A client can ask for events after a known sequence number, and the service can stream back responses. Each response has a sequence number and exactly one kind of payload, such as a message change, group change, poll change, chat change, completion marker, or heartbeat. A heartbeat is a small “still alive” signal, like someone tapping the line during a long phone call so the other side knows the connection has not gone silent.

This file does not contain handwritten business logic. Instead, it registers message and service descriptions with Google’s protobuf runtime. Other code imports it so it can create, read, serialize, and deserialize these event messages. Without this file, Python code would not know the exact field names, field numbers, or allowed payload choices for the event service wire format.


### Conversation content
Contracts for group changes, message APIs, message records, reactions, edits, receipts, and poll data.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/group_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-made from a Protocol Buffers schema, which is a shared blueprint for structured data. In plain terms, it gives Python code a common set of “forms” for recording group chat events. Without it, different parts of the system might disagree about what a group rename or participant removal looks like, making stored or transmitted data hard to understand.

The file registers several message shapes with Google’s protobuf runtime. Some messages describe one specific kind of change: a display name change stores the new name, participant changes store the affected address, and icon changes mark that the group picture changed or was removed. The main message, `GroupChangeEvent`, wraps these details together with shared context: the chat identifier, when the change happened, who caused it if known, and whether it came from the local user.

An important detail is that `GroupChangeEvent` uses a protobuf “oneof,” meaning only one kind of change is meant to be set at a time. This is like a form with several possible checkboxes where the rules say exactly one event type should be filled in. The file also connects to other generated protobuf files for timestamps and iMessage address information.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2.py`

`generated` · `import time and request handling`

This file is machine-made from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the exact shape of data they exchange. Think of it like a set of official forms: a “send text message” form has fields for the chat, text, reply target, formatting, and optional client ID; a “list messages” form has fields for page size, filters, and time limits.

The file registers those forms with Google’s protobuf runtime so Python code can use classes such as SendTextMessageRequest, MessageResponse, EditMessageRequest, and SubscribeMessageEventsResponse. It also describes the MessageService itself: the operations available for sending text, sending attachments, updating mini-app cards, editing or unsending messages, adding reactions or stickers, fetching messages, listing messages, getting embedded media, and subscribing to live message changes.

It imports shared message types from nearby generated files, such as Message, AttachmentRef, ReplyTarget, and streaming event types. It also includes HTTP route annotations, which map service calls to REST-style paths like /v1/messages:sendText. Because this file is generated, people should not edit it by hand. If it were missing or out of date, client and server code could disagree about what fields exist or how message API calls are named.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_types_pb2.py`

`generated` · `cross-cutting serialization and message data exchange`

This file is machine-made from a Protocol Buffer schema, so people should not edit it by hand. A Protocol Buffer is like a standardized form: every field has a name and type, so different parts of the system agree on exactly what a “message,” “reaction,” or “sticker placement” means.

The file registers all of the iMessage message-related data shapes with Google’s protobuf runtime. These include small pieces such as a reaction kind, a mention, text formatting, an attachment reference, or sticker position. They also include larger records such as full message content, a complete message, and message change events like “message received,” “message edited,” “message read,” “message unsent,” “reaction added,” and “sticker placed.”

It also links to related generated files for addresses and attachments, and to Google’s timestamp type for dates. Without this file, Python code in this extension would not have concrete classes for these message records. That would make it hard to serialize, deserialize, validate, or exchange iMessage data consistently. The important thing to know is that this file is plumbing for a data contract: it does not decide business rules, but it defines the exact boxes that message data must fit into.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/poll_types_pb2.py`

`generated` · `cross-cutting`

This file is machine-made from a Protocol Buffers definition, which is a language-neutral recipe for structured data. In plain terms, it is like a standardized form for iMessage polls: everyone who uses it agrees where the poll title goes, where the options go, how votes are recorded, and how changes to a poll are described.

The file registers several message types with Google's Protocol Buffers runtime. These include poll options, participant votes, full poll state, and poll change events such as a poll being created, an option being added, a vote being cast, or a vote being removed. Some fields point to other shared message types, such as a timestamp for when something happened and an address object for who performed the action.

Most project code should not edit this file directly. Instead, it imports the generated message classes and fills them in, serializes them for storage or transport, or reads them back from serialized data. Without this file, Python code would not know the exact structure of iMessage poll messages, and different parts of the system could disagree about what a poll event means.


### Streaming liveness
The streaming heartbeat contract used to keep iMessage event streams alive.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/streaming_pb2.py`

`generated` · `runtime import and message serialization`

This file is not handwritten application logic. It is produced by the Protocol Buffers compiler from `photon/imessage/v1/streaming.proto`. Protocol Buffers, often called protobuf, are a compact way for programs to describe and exchange structured messages, especially across services or network connections.

The main job of this file is to register the protobuf schema with Python’s protobuf runtime. It says: there is a protobuf package named `photon.imessage.v1`, and inside it there is a message type called `Heartbeat`. A heartbeat is usually a tiny “I am still here” message, like a person periodically tapping the table during a phone call so the other side knows the connection has not gone silent.

When this module is imported, it first checks that the installed protobuf runtime is compatible with the version that generated this file. Then it loads a serialized description of the schema into protobuf’s shared descriptor pool. Finally, protobuf’s builder creates the Python message classes and metadata needed by the rest of the program to create, parse, serialize, and inspect `Heartbeat` messages.

Because this is generated code, people should not edit it directly. Changes should be made in the source `.proto` file and regenerated. Without this file, Python code in this project would not know how to represent or decode the streaming heartbeat message.
